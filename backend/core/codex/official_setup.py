"""Drive the official DeepSeek Codex setup script non-interactively.

The DeepSeek documentation ships a configuration manager that rewrites
``~/.codex/config.toml`` and ``~/.codex/models.json`` so Codex CLI (and the
ChatGPT desktop app / IDE extension) can talk to DeepSeek models.  The script is
menu driven; this module downloads it, answers the menu through stdin and the
``DEEPSEEK_API_KEY`` environment variable, so CodeYun can switch the local
machine between ``deepseek-flash``, ``deepseek-v4-pro`` and the default ``gpt``
configuration without any user interaction.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any

import requests

from backend.core.temp_paths import codeyun_temp_root


SETUP_SCRIPT_URLS: dict[str, str] = {
    "windows": "https://cdn.deepseek.com/api-docs/codex-deepseek-setup.ps1",
    "posix": "https://cdn.deepseek.com/api-docs/codex-deepseek-setup.sh",
}
SETUP_SCRIPT_SUFFIXES: dict[str, str] = {"windows": ".ps1", "posix": ".sh"}

FLASH_MODE = "deepseek-flash"
PRO_MODE = "deepseek-v4-pro"
GPT_MODE = "gpt"
SUPPORTED_MODES = (FLASH_MODE, PRO_MODE, GPT_MODE)

_MODE_CHOICES: dict[str, str] = {FLASH_MODE: "1", PRO_MODE: "2", GPT_MODE: "9"}

PROVIDER_ID = "deepseek"
BACKUP_DIRNAME = "backup-deepseek"

_SETUP_SCRIPT_ENV = "CODEYUN_CODEX_SETUP_SCRIPT"
_ENV_PREFIX = "env:CODEYUN_CODEX_SETUP_SCRIPT"

_DOWNLOAD_TIMEOUT_SECONDS = 30
_RUN_TIMEOUT_SECONDS = 180
_OUTPUT_LIMIT = 20000

_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_switch_lock = threading.Lock()


class CodexSetupError(RuntimeError):
    """Raised when the official setup script cannot complete a switch."""


def resolve_codex_home() -> Path:
    """Return the Codex home directory used on this machine."""

    explicit = (os.environ.get("CODEX_HOME") or "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve(strict=False)
    return (Path.home() / ".codex").resolve(strict=False)


def _platform_key() -> str:
    return "windows" if os.name == "nt" else "posix"


def read_codex_status() -> dict[str, Any]:
    """Describe the current Codex provider configuration on this machine."""

    codex_home = resolve_codex_home()
    config_path = codex_home / "config.toml"
    models_path = codex_home / "models.json"
    backup_dir = codex_home / BACKUP_DIRNAME

    status: dict[str, Any] = {
        "mode": "unconfigured",
        "model": "",
        "model_provider": "",
        "codex_home": str(codex_home),
        "config_path": str(config_path),
        "config_exists": config_path.is_file(),
        "models_json_exists": models_path.is_file(),
        "backup_exists": backup_dir.is_dir(),
        "deepseek_configured": False,
        "deepseek_api_key_present": False,
    }

    if not config_path.is_file():
        return status

    try:
        text = config_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return status

    parsed = _parse_toml(text)
    model = str(parsed.get("model") or "").strip()
    provider = str(parsed.get("model_provider") or "").strip()
    providers = parsed.get("model_providers")
    deepseek_provider = providers.get(PROVIDER_ID) if isinstance(providers, dict) else None
    deepseek_key = ""
    if isinstance(deepseek_provider, dict):
        deepseek_key = str(deepseek_provider.get("experimental_bearer_token") or "").strip()

    status["model"] = model
    status["model_provider"] = provider
    status["deepseek_configured"] = isinstance(deepseek_provider, dict) or f"[model_providers.{PROVIDER_ID}]" in text
    status["deepseek_api_key_present"] = bool(deepseek_key)

    if provider == PROVIDER_ID and model == FLASH_MODE:
        status["mode"] = FLASH_MODE
    elif provider == PROVIDER_ID and model == PRO_MODE:
        status["mode"] = PRO_MODE
    elif provider == PROVIDER_ID:
        status["mode"] = "custom-deepseek"
    elif provider:
        status["mode"] = "custom"
    else:
        status["mode"] = GPT_MODE

    return status


def read_existing_deepseek_token() -> str:
    """Return the DeepSeek API key already stored in config.toml, if any."""

    config_path = resolve_codex_home() / "config.toml"
    if not config_path.is_file():
        return ""
    try:
        text = config_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    parsed = _parse_toml(text)
    providers = parsed.get("model_providers")
    if isinstance(providers, dict):
        provider = providers.get(PROVIDER_ID)
        if isinstance(provider, dict):
            return str(provider.get("experimental_bearer_token") or "").strip()
    return ""


def switch_codex_mode(mode: str, *, api_key: str | None = None) -> dict[str, Any]:
    """Run the official setup script for ``mode`` and return the captured output."""

    normalized_mode = str(mode or "").strip().lower()
    if normalized_mode not in SUPPORTED_MODES:
        raise CodexSetupError(f"不支持的切换目标：{mode}")

    with _switch_lock:
        platform = _platform_key()
        script_path = _download_setup_script(platform)
        codex_home = resolve_codex_home()
        codex_home.mkdir(parents=True, exist_ok=True)

        env = os.environ.copy()
        env["CODEX_HOME"] = str(codex_home)
        env[_SETUP_SCRIPT_ENV] = str(script_path)
        key = str(api_key or "").strip()
        if key:
            env["DEEPSEEK_API_KEY"] = key

        command = _build_command(platform, script_path)
        answers = _build_answers(normalized_mode)

        try:
            completed = subprocess.run(
                command,
                input=answers,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                cwd=str(script_path.parent),
                timeout=_RUN_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            raise CodexSetupError(f"官方脚本执行超时（{_RUN_TIMEOUT_SECONDS} 秒）") from exc
        except OSError as exc:
            raise CodexSetupError(f"无法执行官方脚本：{exc}") from exc

        output = _clean_output(f"{completed.stdout or ''}{completed.stderr or ''}")
        if completed.returncode != 0:
            raise CodexSetupError(_failure_message(completed.returncode, output))

        return {
            "mode": normalized_mode,
            "output": output[-_OUTPUT_LIMIT:],
            "status": read_codex_status(),
        }


def _download_setup_script(platform: str) -> Path:
    url = SETUP_SCRIPT_URLS[platform]
    suffix = SETUP_SCRIPT_SUFFIXES[platform]
    cache_dir = codeyun_temp_root("codex-setup")
    target = cache_dir / f"codex-deepseek-setup{suffix}"
    temporary = target.with_name(f"{target.name}.tmp")

    try:
        response = requests.get(
            url,
            timeout=_DOWNLOAD_TIMEOUT_SECONDS,
            headers={"User-Agent": "CodeYun codex-switch"},
        )
        response.raise_for_status()
        text = response.text
        if not text.strip():
            raise CodexSetupError("官方脚本内容为空")
        temporary.write_text(text, encoding="utf-8", newline="\n")
        temporary.replace(target)
        return target
    except Exception as exc:  # noqa: BLE001 - fall back to a cached copy
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        if target.is_file() and target.stat().st_size > 0:
            return target
        raise CodexSetupError(f"下载官方脚本失败：{exc}") from exc


def _build_command(platform: str, script_path: Path) -> list[str]:
    if platform == "windows":
        executable = shutil.which("pwsh") or shutil.which("powershell")
        if not executable:
            raise CodexSetupError("未找到 PowerShell，无法执行官方脚本")
        # PowerShell writes redirected output using the machine's ANSI code page,
        # which mangles the script's Chinese report.  Force UTF-8 output first,
        # then invoke the script through the ``CODEYUN_CODEX_SETUP_SCRIPT`` env var
        # so the path never has to be quoted into the command string.
        prelude = (
            "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false); "
            f"& ${_ENV_PREFIX}"
        )
        return [
            executable,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            prelude,
        ]

    executable = shutil.which("bash") or "/bin/bash"
    return [executable, str(script_path)]


def _build_answers(mode: str) -> str:
    choice = _MODE_CHOICES[mode]
    if mode == GPT_MODE:
        # Menu choice followed by the restore confirmation prompt.
        return f"{choice}\ny\n"
    return f"{choice}\n"


def _clean_output(text: str) -> str:
    return _ANSI_ESCAPE_RE.sub("", text).replace("\r\n", "\n").strip()


def _failure_message(returncode: int, output: str) -> str:
    tail = "\n".join(line for line in output.splitlines() if line.strip())[-1500:]
    if tail:
        return f"官方脚本执行失败（退出码 {returncode}）：\n{tail}"
    return f"官方脚本执行失败（退出码 {returncode}）"


def _parse_toml(text: str) -> dict[str, Any]:
    try:
        import tomllib
    except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
        return _parse_toml_fallback(text)
    try:
        data = tomllib.loads(text)
    except Exception:  # noqa: BLE001 - malformed config should not break status
        return {}
    return data if isinstance(data, dict) else {}


def _parse_toml_fallback(text: str) -> dict[str, Any]:  # pragma: no cover - Python < 3.11
    data: dict[str, Any] = {}
    section = data
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            name = line.strip("[]").strip()
            if name == f"model_providers.{PROVIDER_ID}":
                provider = data.setdefault("model_providers", {}).setdefault(PROVIDER_ID, {})
                section = provider if isinstance(provider, dict) else {}
            else:
                section = {}
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().strip('"').strip("'")
        value = value.strip().strip('"').strip("'")
        if key in {"model", "model_provider"} and section is data:
            data[key] = value
        elif key == "experimental_bearer_token":
            section[key] = value
    return data
