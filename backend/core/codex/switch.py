"""Two-level Codex provider/model switching.

The user picks a provider first (OpenAI / DeepSeek / opencode) and then a model
within that provider.  DeepSeek (official API) and the OpenAI default are handled
by the official DeepSeek setup script; opencode Go is written directly because it
uses a different provider block.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tomllib
from pathlib import Path
from typing import Any

from backend.core.codex.official_setup import (
    FLASH_MODE,
    GPT_MODE,
    PRO_MODE,
    CodexSetupError,
    resolve_codex_home,
    switch_codex_mode,
)
from backend.core.opencode_usage import read_opencode_go_key


OPENCODE_GO_PROVIDER_ID = "opencode_go"
OPENCODE_GO_BASE_URL = "https://opencode.ai/zen/go/v1"
OPENCODE_GO_SESSION = "codeyun-codex"

DEEPSEEK_PROVIDER_ID = "deepseek"
OPENAI_PROVIDER_ID = "openai"

_HEADER_RE = re.compile(r"^\s*\[([^\]]+)\]")
_TOP_LEVEL_DROP = {"model", "model_provider", "model_catalog_json"}

PROVIDERS: tuple[dict[str, Any], ...] = (
    {"id": OPENAI_PROVIDER_ID, "label": "OpenAI", "models": []},
    {
        "id": DEEPSEEK_PROVIDER_ID,
        "label": "DeepSeek",
        "models": [
            {"id": FLASH_MODE, "label": "DeepSeek Flash"},
            {"id": PRO_MODE, "label": "DeepSeek V4 Pro"},
        ],
    },
    {
        "id": "opencode",
        "label": "opencode Go",
        "models": [
            {"id": "deepseek-v4-pro", "label": "DeepSeek V4 Pro"},
            {"id": "deepseek-v4-flash", "label": "DeepSeek V4 Flash"},
            {"id": "deepseek-v4.1-flash", "label": "DeepSeek V4.1 Flash"},
            {"id": "grok-4.6", "label": "Grok 4.6"},
        ],
    },
)

_OPENCODE_MODELS = {item["id"] for item in PROVIDERS[2]["models"]}


class CodexSwitchError(CodexSetupError):
    """Raised when a provider/model switch cannot be applied."""


def detect_provider(model_provider: str) -> str:
    normalized = str(model_provider or "").strip().lower()
    if normalized == OPENCODE_GO_PROVIDER_ID:
        return "opencode"
    if normalized == DEEPSEEK_PROVIDER_ID:
        return DEEPSEEK_PROVIDER_ID
    return OPENAI_PROVIDER_ID


def _read_config_lines(config_path: Path) -> list[str]:
    if not config_path.is_file():
        return []
    text = config_path.read_text(encoding="utf-8", errors="replace")
    return text.replace("\r\n", "\n").rstrip("\n").split("\n")


def _write_config_lines(config_path: Path, lines: list[str]) -> None:
    payload = "\n".join(lines).rstrip("\n") + "\n"
    tomllib.loads(payload)  # validate before touching the real file
    temporary = config_path.with_name(f"{config_path.name}.codeyun-tmp")
    temporary.write_text(payload, encoding="utf-8", newline="\n")
    os.replace(temporary, config_path)


def _strip_for_opencode(lines: list[str]) -> list[str]:
    out: list[str] = []
    seen_table = False
    skip_section = False
    for line in lines:
        header = _HEADER_RE.match(line)
        if header:
            seen_table = True
            name = header.group(1).strip().strip('"').strip("'")
            skip_section = name == f"model_providers.{OPENCODE_GO_PROVIDER_ID}" or name.startswith(
                f"model_providers.{OPENCODE_GO_PROVIDER_ID}."
            )
            if not skip_section:
                out.append(line)
            continue
        if skip_section:
            continue
        if not seen_table:
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                key = stripped.split("=", 1)[0].strip().strip('"').strip("'")
                if key in _TOP_LEVEL_DROP:
                    continue
        out.append(line)
    # drop leading blank lines left by removed keys
    while out and not out[0].strip():
        out.pop(0)
    return out


def _set_top_level_key(lines: list[str], key: str, value: str) -> None:
    pattern = re.compile(rf'^\s*{re.escape(key)}\s*=')
    for index, line in enumerate(lines):
        if pattern.match(line):
            lines[index] = f"{key} = {value}"
            return
    lines.insert(0, f"{key} = {value}")


CODEX_BACKUP_DIRNAME = "backup-codeyun"


def _snapshot_before_opencode(codex_home: Path, config_path: Path) -> None:
    """Keep the pre-opencode config so OpenAI can be restored without the DeepSeek backup."""

    if (codex_home / "backup-deepseek").is_dir():
        return
    backup_config = codex_home / CODEX_BACKUP_DIRNAME / "config.toml"
    if backup_config.is_file() or not config_path.is_file():
        return
    backup_config.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(config_path, backup_config)


def restore_codeyun_backup(codex_home: Path) -> bool:
    backup_config = codex_home / CODEX_BACKUP_DIRNAME / "config.toml"
    if not backup_config.is_file():
        return False
    shutil.copyfile(backup_config, codex_home / "config.toml")
    shutil.rmtree(codex_home / CODEX_BACKUP_DIRNAME, ignore_errors=True)
    return True


def switch_to_opencode(config_path: Path, model: str) -> None:
    if model not in _OPENCODE_MODELS:
        raise CodexSwitchError(f"opencode Go 不支持该模型：{model}")

    key = read_opencode_go_key()
    if not key:
        raise CodexSwitchError("未找到 opencode-go 凭证，无法切换")

    _snapshot_before_opencode(config_path.parent, config_path)
    lines = _strip_for_opencode(_read_config_lines(config_path))
    lines.insert(0, f"model = {json.dumps(model)}")
    lines.insert(1, f'model_provider = "{OPENCODE_GO_PROVIDER_ID}"')
    lines.extend(
        [
            "",
            f"[model_providers.{OPENCODE_GO_PROVIDER_ID}]",
            'name = "opencode Go"',
            f'base_url = "{OPENCODE_GO_BASE_URL}"',
            'wire_api = "responses"',
            f"experimental_bearer_token = {json.dumps(key)}",
            f'http_headers = {{ "x-opencode-session" = "{OPENCODE_GO_SESSION}" }}',
        ]
    )
    _write_config_lines(config_path, lines)


def ensure_deepseek_provider(config_path: Path) -> None:
    """Make sure the official script's fast path lands back on the DeepSeek provider."""

    if not config_path.is_file():
        return
    lines = _read_config_lines(config_path)
    _set_top_level_key(lines, "model_provider", f'"{DEEPSEEK_PROVIDER_ID}"')
    models_json = (config_path.parent / "models.json").as_posix()
    _set_top_level_key(lines, "model_catalog_json", json.dumps(models_json))
    _write_config_lines(config_path, lines)


def switch_codex(
    provider: str,
    model: str | None = None,
    *,
    api_key: str | None = None,
) -> dict[str, Any]:
    normalized = str(provider or "").strip().lower()
    chosen_model = str(model or "").strip()

    if normalized == OPENAI_PROVIDER_ID:
        return switch_codex_mode(GPT_MODE)

    if normalized == DEEPSEEK_PROVIDER_ID:
        if chosen_model not in {FLASH_MODE, PRO_MODE}:
            raise CodexSwitchError(f"DeepSeek 不支持该模型：{chosen_model}")
        ensure_deepseek_provider(resolve_codex_home() / "config.toml")
        return switch_codex_mode(
            PRO_MODE if chosen_model == PRO_MODE else FLASH_MODE,
            api_key=api_key,
        )

    if normalized == "opencode":
        config_path = resolve_codex_home() / "config.toml"
        switch_to_opencode(config_path, chosen_model)
        from backend.core.codex.official_setup import read_codex_status

        return {"mode": chosen_model, "output": "", "status": read_codex_status()}

    raise CodexSwitchError(f"未知的供应商：{provider}")
