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
import tempfile

from backend.core.agents.policy import load_config, resolve, set_business_agent, ds_route, select_ds_route, read_official_ds_key

# Public tool set for owner-authorized unattended tasks, shared by providers.
DEFAULT_TASK_TOOLS = ['Bash', 'Read', 'Edit', 'Write', 'Glob', 'Grep', 'WebFetch', 'WebSearch',
                      'NotebookEdit', 'Skill', 'Task']


def bootstrap(config: dict) -> None:
    root = Path(config["codeyun_root"])
    sys.path.insert(0, str(root))
    sys.path.insert(0, str(root.parent / "xlproject" / "src"))
    import xlproject.loadenv  # noqa: F401


def official_environment(config: dict, env: dict, key: str) -> dict:
    """Add one isolated OpenCode provider, preserving existing inline options."""
    route = config["resources"]["ds"]["primary"]
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


def _read_capture(handle) -> str:
    handle.seek(0)
    text = handle.read().decode("utf-8", errors="replace")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def run_process(command: list[str], *, env: dict, directory: Path, timeout: int,
                input_text: str | None = None) -> subprocess.CompletedProcess:
    """Capture one child via temp files; timeout terminates only this invocation's tree.

    Pipes are not used: a descendant that outlives the root process (MCP server,
    background task) would keep a pipe open and block communicate() forever.
    Files are unnamed delete-on-close temp files, so no path is left behind.
    """
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err, tempfile.TemporaryFile() as incoming:
        if input_text is not None:
            incoming.write(input_text.encode("utf-8"))
            incoming.seek(0)
        process = subprocess.Popen(command, cwd=directory, env=env,
                                   stdin=incoming if input_text is not None else subprocess.DEVNULL,
                                   stdout=out, stderr=err, **options)
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                   capture_output=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
                else:
                    import signal
                    os.killpg(process.pid, signal.SIGKILL)
            except (subprocess.SubprocessError, OSError):
                pass  # tree kill is best effort; the root kill below still runs
            try:
                process.kill()
                process.wait(timeout=10)
            except (subprocess.SubprocessError, OSError):
                pass
            raise
        return subprocess.CompletedProcess(command, process.returncode, _read_capture(out), _read_capture(err))


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
    private_task = getattr(args, "privacy", False) or getattr(args, "privacy_session", "")
    if resource == "codex" and not private_task:
        return {**result, "status": "current_session_required", "error": "由当前 Codex 会话或公共 Codex 入口执行"}
    if bool(args.session) != bool(args.session_provider):
        raise ValueError("续接须同时提供 --session 与 --session-provider")
    directory = Path(args.directory).resolve(strict=True)
    if not directory.is_dir():
        raise ValueError("工作目录必须是目录")
    prompt = Path(args.prompt_file).read_text(encoding="utf-8-sig").strip()
    if not prompt:
        raise ValueError("任务包不能为空")
    privacy_session = ""
    if private_task:
        from backend.core.ai.privacy import prepare
        packet = prepare(prompt, session_id=getattr(args, "privacy_session", ""))
        privacy_session = packet["session_id"]
        prompt = packet["handoff_prompt"]
        result["privacy_session"] = privacy_session
    if resource == "codex":
        return {**result, "status": "current_session_required", "handoff": packet,
                "error": "由当前 Codex 会话接收脱敏交接包后执行"}
    bootstrap(config)
    env = os.environ.copy()
    if environment:
        env.update(environment)
    driver = config["resources"][resource]["driver"]
    if resource == "cc":
        if args.session_provider and args.session_provider != "claude":
            raise ValueError("CC 会话 provider 必须是 claude")
        result["provider"] = "claude"
        # stdin preserves multiline JSON/Chinese and avoids Windows npm shim
        # argument splitting and the command-line length limit for task packs.
        cli_args = ["-p", "--output-format", "json"]
        if getattr(args, 'disallowed_tools', None):
            cli_args.extend(['--disallowedTools', *args.disallowed_tools])
        if args.allowed_tools:
            cli_args.extend(["--allowedTools", *args.allowed_tools])
        if args.session:
            cli_args.extend(["--resume", args.session])
    else:
        route, probe = select_ds_route(session_provider=args.session_provider)
        result.update(provider=route["provider"], model=route["model"], quota_probe=probe)
        if route["provider"] == "deepseek-official":
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
        process_options = {"input_text": prompt} if resource == "cc" else {}
        completed = run_process(command, env=env, directory=directory, timeout=args.timeout, **process_options)
    except FileNotFoundError:
        return {**result, "status": "missing_cli", "error": f"缺少 {driver} CLI"}
    except subprocess.TimeoutExpired:
        return {**result, "status": "timeout", "error": "本次进程树已停止；检查业务现场后再续接"}
    parsed = parse_result(driver, completed.stdout, completed.returncode)
    if privacy_session:
        from backend.core.ai.privacy import redact_result
        parsed = redact_result(privacy_session, parsed)
    return {**result, **parsed, "session_id": parsed["session_id"] or result["session_id"]}


def run_agent(*, resource: str, prompt: str, directory: str | Path,
              session_id: str = "", session_provider: str = "", timeout: int = 900,
              environment: dict | None = None, allowed_tools: list[str] | None = None,
              disallowed_tools: list[str] | None = None,
              privacy: bool = False, privacy_session: str = "") -> dict:
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
                               timeout=timeout, allowed_tools=allowed_tools, thinking=False,
                               disallowed_tools=disallowed_tools, privacy=privacy, privacy_session=privacy_session)
        config = load_config()
        return execute(args, config, resolve(config, pair=resource), environment=environment)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    switch = commands.add_parser('switch', help='切换业务主 agent；只影响新任务，不迁移现有 owner')
    switch.add_argument('--business', required=True)
    switch.add_argument('--agent', required=True, choices=['codex', 'cc'])
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
            command.add_argument('--disallowed-tools', nargs='*')
            command.add_argument("--timeout", type=int, default=900)
            command.add_argument("--thinking", action="store_true")
            command.add_argument("--privacy", action="store_true", help="Official DS translates the task before delegation")
            command.add_argument("--privacy-session", default="", help="Reuse a privacy variable mapping")
    args = parser.parse_args()
    try:
        if args.command == 'switch':
            print(json.dumps(set_business_agent(args.business, args.agent), ensure_ascii=False))
            return 0
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
