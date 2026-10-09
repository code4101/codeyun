"""Public local CC/DS execution adapter; resolve plans without starting a model.

Configuration is the single source for roles and resource routes. Runtime
credentials come from xlproject.loadenv / CodeYun public readers. Provider
overrides exist only in the child environment; no global model switch occurs.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from backend.core.agents.policy import load_config, resolve, ds_route, select_ds_route, read_official_ds_key


def bootstrap(config: dict) -> None:
    root = Path(config["codeyun_root"])
    sys.path.insert(0, str(root))
    sys.path.insert(0, str(root.parent / "xlproject" / "src"))
    import xlproject.loadenv  # noqa: F401


def official_environment(config: dict, env: dict, key: str) -> dict:
    """Add one isolated OpenCode provider, preserving existing inline options."""
    route = config["resources"]["ds"]["fallback"]
    inline = json.loads(env.get("OPENCODE_CONFIG_CONTENT") or "{}")
    inline.setdefault("provider", {})[route["provider"]] = {
        "npm": "@ai-sdk/openai-compatible", "name": "DeepSeek Official Flash",
        "options": {"baseURL": route["base_url"], "apiKey": "{env:DEEPSEEK_API_KEY}"},
        "models": {route["model"]: {"name": "DeepSeek Flash"}}
    }
    return {**env, "DEEPSEEK_API_KEY": key,
            "OPENCODE_CONFIG_CONTENT": json.dumps(inline, ensure_ascii=False)}


def cli_command(driver: str, args: list[str], env: dict) -> tuple[list[str], dict]:
    """Windows npm shims are invoked with an argument array, never shell text."""
    executable = shutil.which(driver)
    if not executable:
        raise FileNotFoundError(driver)
    if os.name != "nt" or Path(executable).suffix.lower() == ".exe":
        return [executable, *args], env
    # PowerShell resolves npm's .ps1 wrapper. JSON is transported in the child
    # environment, so prompts containing $, backticks or newlines remain data.
    source = """[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$ProgressPreference = 'SilentlyContinue'
$cliArgs = ConvertFrom-Json $env:CODEYUN_AGENT_ARGS
$cli = (Get-Command $env:CODEYUN_AGENT_DRIVER -ErrorAction Stop).Source
& $cli @cliArgs
exit $LASTEXITCODE
"""
    encoded = base64.b64encode(source.encode("utf-16-le")).decode("ascii")
    return ["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded], {
        **env, "CODEYUN_AGENT_DRIVER": driver,
        "CODEYUN_AGENT_ARGS": json.dumps(args, ensure_ascii=False),
    }


def run_process(command: list[str], *, env: dict, directory: Path, timeout: int) -> subprocess.CompletedProcess:
    """Capture one child; timeout terminates only this invocation's tree."""
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
    process = subprocess.Popen(command, cwd=directory, env=env, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                               encoding="utf-8", errors="replace", **options)
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                           capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        else:
            import signal
            os.killpg(process.pid, signal.SIGKILL)
        process.kill()
        process.communicate()
        raise
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def parse_result(driver: str, stdout: str, exit_code: int) -> dict:
    """CLI success requires final text and no error/permission denial."""
    text_parts, session, error = [], "", ""
    if driver == "claude":
        try:
            payload = json.loads(stdout)
        except ValueError:
            payload = {}
            error = "invalid_cli_json"
        if isinstance(payload, dict):
            session = payload.get("session_id") or ""
            if payload.get("is_error"):
                error = "agent_error"
            if payload.get("permission_denials"):
                error = "permission_denied"
            if payload.get("result"):
                text_parts.append(str(payload["result"]))
        else:
            error = "invalid_cli_json"
    else:
        for line in stdout.splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            part = event.get("part") or {}
            if not isinstance(part, dict):
                part = {}
            session = event.get("sessionID") or part.get("sessionID") or session
            if event.get("type") == "error":
                error = "agent_error"
            if part.get("type") == "text" and part.get("text"):
                text_parts.append(str(part["text"]))
    text = "\n".join(text_parts).strip()
    if exit_code and not error:
        error = "cli_failed"
    if not text and not error:
        error = "missing_final_text"
    return {"ok": not bool(error), "status": "completed" if not error else error,
            "session_id": session, "text": text, "error": error, "exit_code": exit_code}


def execute(args, config: dict, plan: dict, *, environment: dict | None = None) -> dict:
    resource = plan[args.role] if args.role else (plan["aux"] or plan["main"])
    result = {"ok": False, "status": "not_started", "resource": resource, "provider": "",
              "model": "inherit", "session_id": args.session or "", "text": "",
              "error": "", "exit_code": None}
    if not resource:
        return {**result, "status": "no_aux", "error": "组合未配置辅 agent"}
    if resource == "codex":
        return {**result, "status": "current_session_required", "error": "由当前 Codex 会话或公共 Codex 入口执行"}
    if bool(args.session) != bool(args.session_provider):
        raise ValueError("续接须同时提供 --session 与 --session-provider")
    directory = Path(args.directory).resolve(strict=True)
    if not directory.is_dir():
        raise ValueError("工作目录必须是目录")
    prompt = Path(args.prompt_file).read_text(encoding="utf-8-sig").strip()
    if not prompt:
        raise ValueError("任务包不能为空")
    bootstrap(config)
    env = os.environ.copy()
    if environment:
        env.update(environment)
    driver = config["resources"][resource]["driver"]
    if resource == "cc":
        if args.session_provider and args.session_provider != "claude":
            raise ValueError("CC 会话 provider 必须是 claude")
        result["provider"] = "claude"
        cli_args = ["-p", prompt, "--output-format", "json"]
        if args.allowed_tools:
            cli_args.extend(["--allowedTools", *args.allowed_tools])
        if args.session:
            cli_args.extend(["--resume", args.session])
    else:
        route, probe = select_ds_route(session_provider=args.session_provider)
        result.update(provider=route["provider"], model=route["model"], quota_probe=probe)
        if route["provider"] == config["resources"]["ds"]["fallback"]["provider"]:
            key = read_official_ds_key()
            if not key:
                return {**result, "status": "missing_credentials", "error": "缺少官方 DeepSeek 凭据"}
            env = official_environment(config, env, key)
        cli_args = ["run", "--format", "json", "--model", f"{route['provider']}/{route['model']}",
                    "--dir", str(directory)]
        if args.session:
            cli_args.extend(["--session", args.session])
        if args.thinking:
            cli_args.append("--thinking")
        cli_args.append(prompt)
    try:
        command, env = cli_command(driver, cli_args, env)
        completed = run_process(command, env=env, directory=directory, timeout=args.timeout)
    except FileNotFoundError:
        return {**result, "status": "missing_cli", "error": f"缺少 {driver} CLI"}
    except subprocess.TimeoutExpired:
        return {**result, "status": "timeout", "error": "本次进程树已停止；检查业务现场后再续接"}
    parsed = parse_result(driver, completed.stdout, completed.returncode)
    return {**result, **parsed, "session_id": parsed["session_id"] or result["session_id"]}


def run_agent(*, resource: str, prompt: str, directory: str | Path,
              session_id: str = "", session_provider: str = "", timeout: int = 900,
              environment: dict | None = None, allowed_tools: list[str] | None = None) -> dict:
    """Bounded public execution API; permission overrides remain child-local.

    This runs one agent role, not a second orchestration tree. The caller owns
    the business task and acceptance, and must pass the exact returned session.
    """
    from types import SimpleNamespace
    import tempfile
    from backend.core.temp_paths import codeyun_temp_root
    with tempfile.TemporaryDirectory(dir=codeyun_temp_root("agents")) as temporary:
        prompt_path = Path(temporary) / "prompt.txt"
        prompt_path.write_text(prompt, encoding="utf-8")
        args = SimpleNamespace(role="main", directory=str(directory), prompt_file=str(prompt_path),
                               session=session_id, session_provider=session_provider,
                               timeout=timeout, allowed_tools=allowed_tools, thinking=False)
        config = load_config()
        return execute(args, config, resolve(config, pair=resource), environment=environment)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("resolve", "run"):
        command = commands.add_parser(name)
        command.add_argument("--business", default="")
        command.add_argument("--pair", default="")
        if name == "run":
            command.add_argument("--directory", required=True)
            command.add_argument("--prompt-file", required=True)
            command.add_argument("--role", choices=["main", "aux"])
            command.add_argument("--session", default="")
            command.add_argument("--session-provider", default="")
            command.add_argument("--allowed-tools", nargs="*")
            command.add_argument("--timeout", type=int, default=900)
            command.add_argument("--thinking", action="store_true")
    args = parser.parse_args()
    try:
        config = load_config()
        plan = resolve(config, business=args.business, pair=args.pair)
        result = plan if args.command == "resolve" else execute(args, config, plan)
    except (ValueError, OSError, ImportError) as exc:
        # Avoid emitting credential-bearing exception or raw subprocess output.
        result = {"ok": False, "status": "configuration_error", "error": type(exc).__name__}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok", args.command == "resolve") else 1


if __name__ == "__main__":
    raise SystemExit(main())
