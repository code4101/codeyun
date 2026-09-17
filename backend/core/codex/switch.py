"""Two-level Codex provider/model switching.

The user picks a provider first (OpenAI / DeepSeek / opencode) and then a model
within that provider.  DeepSeek (official API) and the OpenAI default are handled
by the official DeepSeek setup script; OpenCode Go is written directly because it
uses a different provider block.
"""

from __future__ import annotations

import copy
import json
import os
import re
import shutil
import time
import tomllib
from pathlib import Path
from typing import Any

import requests

from backend.core.codex.official_setup import (
    FLASH_MODE,
    GPT_MODE,
    PRO_MODE,
    CodexSetupError,
    resolve_codex_home,
    switch_codex_mode,
)
from backend.core.opencode_usage import read_opencode_go_key
from backend.core.runtime.opencode_proxy_runtime import get_opencode_proxy_base_url


OPENCODE_GO_PROVIDER_ID = "opencode_go"
OPENCODE_GO_SESSION = "codeyun-opencode-proxy"

# Codex auto-compacts at ``context_window * effective_context_window_percent``.
# Codex's token estimate can drift from the upstream tokenizer, and a busy
# tool-using turn can append a lot before the next compaction check, so keep more
# headroom than the Codex default of 95% to avoid overrunning the hard cap.
OPENCODE_CONTEXT_WINDOW_PERCENT = 80

# Fallback model list when the local proxy is not reachable.
_FALLBACK_OPENCODE_MODELS = (
    "gpt-5.6-luna", "grok-4.6", "grok-4.5", "muse-spark-1.3-contributor", "muse-spark-1.2-contributor",
    "deepseek-v4-pro", "deepseek-v4-flash", "deepseek-flash", "deepseek-v4.1-flash", "deepseek-v4-flash-vision-exp",
    "glm-5.3", "glm-5.3-flash", "glm-5.2", "glm-5.1", "glm-5",
    "kimi-k3", "kimi-k2.7-code", "kimi-k2.6", "kimi-k2.5",
    "qwen3.8-max", "qwen3.8-flash", "qwen3.7-max", "qwen3.7-plus", "qwen3.6-plus", "qwen3.5-plus",
    "minimax-m3", "minimax-m2.7", "minimax-m2.5",
    "longcat-2.0", "mimo-v2.5", "mimo-v2.5-pro", "hy3", "hy4-preview", "hy3-preview", "omen-alpha",
)
_OPENCODE_MODELS_CACHE: tuple[float, list[dict[str, str]]] | None = None
_OPENCODE_MODELS_CACHE_TTL_SECONDS = 120.0

DEEPSEEK_PROVIDER_ID = "deepseek"
OPENAI_PROVIDER_ID = "openai"

# The page picks a provider only, so these slugs are what a switch writes into
# config.toml when the caller does not name a model.  Both channels default to the
# cheap/fast DeepSeek Flash; the picker in Codex can still move a thread to pro.
DEFAULT_DEEPSEEK_MODEL = FLASH_MODE
DEFAULT_OPENCODE_MODEL = FLASH_MODE

_HEADER_RE = re.compile(r"^\s*\[([^\]]+)\]")
_TOP_LEVEL_DROP = {"model", "model_provider", "model_catalog_json", "model_reasoning_effort"}

PROVIDERS: tuple[dict[str, Any], ...] = (
    {"id": OPENAI_PROVIDER_ID, "label": "OpenAI", "default_model": "", "models": []},
    {
        "id": "opencode",
        "label": "OpenCode Go",
        "default_model": DEFAULT_OPENCODE_MODEL,
        "models": [{"id": model_id, "label": model_id} for model_id in _FALLBACK_OPENCODE_MODELS],
    },
    {
        "id": DEEPSEEK_PROVIDER_ID,
        "label": "DeepSeek",
        "default_model": DEFAULT_DEEPSEEK_MODEL,
        "models": [
            {"id": FLASH_MODE, "label": "DeepSeek Flash"},
            {"id": PRO_MODE, "label": "DeepSeek V4 Pro"},
        ],
    },
)


class CodexSwitchError(CodexSetupError):
    """Raised when a provider/model switch cannot be applied."""


def discover_opencode_models(*, timeout: float = 10.0) -> list[dict[str, str]]:
    """List OpenCode Go models through the local proxy, with a static fallback."""

    global _OPENCODE_MODELS_CACHE
    now = time.monotonic()
    if _OPENCODE_MODELS_CACHE is not None and now - _OPENCODE_MODELS_CACHE[0] < _OPENCODE_MODELS_CACHE_TTL_SECONDS:
        return _OPENCODE_MODELS_CACHE[1]

    models: list[dict[str, str]] = []
    try:
        response = requests.get(f"{get_opencode_proxy_base_url()}/v1/models", timeout=timeout)
        if response.status_code == 200:
            data = response.json()
            items = data.get("data") if isinstance(data, dict) else None
            for item in items or []:
                model_id = str(item.get("id") or "").strip() if isinstance(item, dict) else ""
                if model_id:
                    models.append({"id": model_id, "label": model_id})
    except (requests.RequestException, ValueError):
        models = []

    if not models:
        models = [{"id": model_id, "label": model_id} for model_id in _FALLBACK_OPENCODE_MODELS]
    models.sort(key=lambda item: item["id"])
    _OPENCODE_MODELS_CACHE = (now, models)
    return models


def detect_provider(model_provider: str) -> str:
    normalized = str(model_provider or "").strip().lower()
    if normalized == OPENCODE_GO_PROVIDER_ID:
        return "opencode"
    if normalized == DEEPSEEK_PROVIDER_ID:
        return DEEPSEEK_PROVIDER_ID
    return OPENAI_PROVIDER_ID


_CATALOG_TEMPLATE_PREFERRED = ("gpt-5.3-codex", "gpt-5-codex", "gpt-5.6-luna", "gpt-6-astra")
_OFFICIAL_CATALOG_CACHE: dict[str, dict[str, Any]] | None = None


def _official_deepseek_catalog() -> dict[str, dict[str, Any]]:
    """extract the official DeepSeek models.json so DeepSeek entries keep its tuning."""

    global _OFFICIAL_CATALOG_CACHE
    if _OFFICIAL_CATALOG_CACHE is not None:
        return _OFFICIAL_CATALOG_CACHE

    entries: dict[str, dict[str, Any]] = {}
    try:
        from backend.core.codex import official_setup

        payload: dict[str, Any] | None = None
        local = resolve_codex_home() / "models.json"
        if local.is_file():
            payload = json.loads(local.read_text(encoding="utf-8", errors="replace"))
        else:
            script_path = official_setup._download_setup_script(official_setup._platform_key())
            text = script_path.read_text(encoding="utf-8", errors="replace")
            match = re.search(r"@'\r?\n(.*?)\r?\n'@", text, re.S)
            if match is None:
                match = re.search(r"<<'CODEX_MODELS_JSON'\r?\n(.*?)\r?\nCODEX_MODELS_JSON", text, re.S)
            if match is not None:
                payload = json.loads(match.group(1))
        if isinstance(payload, dict):
            for item in payload.get("models") or []:
                if isinstance(item, dict) and item.get("slug"):
                    entries[str(item["slug"])] = item
    except Exception:  # noqa: BLE001 - best effort, fall back to the Codex template
        entries = {}

    _OFFICIAL_CATALOG_CACHE = entries
    return entries


def _load_catalog_template(codex_home: Path) -> dict[str, Any] | None:
    cache_path = codex_home / "models_cache.json"
    if not cache_path.is_file():
        return None
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    models = payload.get("models") if isinstance(payload, dict) else None
    if not isinstance(models, list) or not models:
        return None
    by_slug = {item.get("slug"): item for item in models if isinstance(item, dict)}
    for slug in _CATALOG_TEMPLATE_PREFERRED:
        if slug in by_slug:
            return by_slug[slug]
    return models[0]


def _opencode_models_metadata() -> dict[str, dict[str, Any]]:
    base = (os.environ.get("XDG_CACHE_HOME") or "").strip()
    root = Path(base) / "opencode" if base else Path.home() / ".cache" / "opencode"
    cache_path = root / "models.json"
    if not cache_path.is_file():
        return {}
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    provider = payload.get("opencode-go") if isinstance(payload, dict) else None
    if not isinstance(provider, dict):
        return {}
    models = provider.get("models")
    return models if isinstance(models, dict) else {}


def _reasoning_efforts(meta: dict[str, Any]) -> list[str]:
    for option in meta.get("reasoning_options") or []:
        if isinstance(option, dict) and option.get("type") == "effort":
            values = [str(value) for value in (option.get("values") or []) if str(value).strip()]
            if values:
                return values
    return []


def _default_effort(values: list[str]) -> str:
    for preferred in ("medium", "high", "low", "max", "xhigh", "none"):
        if preferred in values:
            return preferred
    return values[0]


def build_opencode_catalog(codex_home: Path, models: list[dict[str, str]]) -> Path | None:
    """Write a Codex model catalog for OpenCode Go so its TUI lists the models.

    A Codex template entry provides the base message/instructions fields; the
    per-model reasoning levels and context window come from opencode's own model
    metadata so Codex offers the right reasoning options for each model.
    """

    template = _load_catalog_template(codex_home)
    if template is None or not models:
        return None
    metadata = _opencode_models_metadata()
    official = _official_deepseek_catalog()
    entries: list[dict[str, Any]] = []
    for index, item in enumerate(models):
        model_id = str(item.get("id") or "").strip()
        if not model_id:
            continue
        # Prefer the official DeepSeek catalog entry when the slug matches, so the
        # DeepSeek models keep DeepSeek's tuned instructions/tool settings.
        entry = copy.deepcopy(official.get(model_id) or template)
        entry["slug"] = model_id
        entry["display_name"] = item.get("label") or model_id
        entry["description"] = f"OpenCode Go · {model_id}"
        entry["priority"] = 1000 + index
        entry["visibility"] = "list"
        entry["supported_in_api"] = True
        # Match the known-good DeepSeek catalog: chat-style function tools rather
        # than Responses-only tool types, so upstream parses calls instead of the
        # model leaking its native DSML markup.
        entry["apply_patch_tool_type"] = "freeform"
        entry["shell_type"] = "shell_command"
        entry["experimental_supported_tools"] = []
        entry["tool_mode"] = None
        entry["web_search_tool_type"] = "text"
        entry["use_responses_lite"] = False

        meta = metadata.get(model_id) if isinstance(metadata.get(model_id), dict) else {}
        efforts = _reasoning_efforts(meta)
        if efforts:
            entry["supported_reasoning_levels"] = [
                {"effort": effort, "description": effort} for effort in efforts
            ]
            entry["default_reasoning_level"] = _default_effort(efforts)
        limit = meta.get("limit") if isinstance(meta.get("limit"), dict) else {}
        context = limit.get("context")
        if isinstance(context, int) and context > 0:
            entry["context_window"] = context
            entry["max_context_window"] = context
        entry["effective_context_window_percent"] = OPENCODE_CONTEXT_WINDOW_PERCENT
        entries.append(entry)
    if not entries:
        return None
    path = codex_home / "opencode_models.json"
    path.write_text(json.dumps({"models": entries}, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


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


# Codex Desktop only renders a model's reasoning levels that are also listed in
# ``[desktop] enabled-reasoning-efforts``; levels such as ``max`` silently vanish
# otherwise.  Keep a known ordering so the merged array stays readable.
_EFFORT_ORDER = ("minimal", "low", "medium", "high", "xhigh", "max", "ultra")


def _order_efforts(values: list[str]) -> list[str]:
    unique: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in unique:
            unique.append(text)
    known = [effort for effort in _EFFORT_ORDER if effort in unique]
    extra = [effort for effort in unique if effort not in _EFFORT_ORDER]
    return known + extra


def _find_section(lines: list[str], section: str) -> tuple[int, int] | None:
    start: int | None = None
    for index, line in enumerate(lines):
        header = _HEADER_RE.match(line)
        if header is None:
            continue
        name = header.group(1).strip().strip('"').strip("'")
        if start is None:
            if name == section:
                start = index
        else:
            return start, index
    if start is not None:
        return start, len(lines)
    return None


def _parse_array(text: str) -> list[str] | None:
    try:
        value = tomllib.loads(f"value = {text.strip()}").get("value")
    except tomllib.TOMLDecodeError:
        return None
    if not isinstance(value, list):
        return None
    return [str(item) for item in value]


def ensure_enabled_reasoning_efforts(
    lines: list[str],
    efforts: list[str],
    *,
    section: str = "desktop",
    key: str = "enabled-reasoning-efforts",
) -> None:
    """Merge the catalog's reasoning levels into the Desktop enabled list."""

    wanted = _order_efforts(efforts)
    if not wanted:
        return

    bounds = _find_section(lines, section)
    if bounds is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(f"[{section}]")
        lines.append(f"{key} = {json.dumps(wanted)}")
        return

    start, end = bounds
    for index in range(start + 1, end):
        stripped = lines[index].strip()
        if stripped.startswith("#") or "=" not in stripped:
            continue
        name = stripped.split("=", 1)[0].strip().strip('"').strip("'")
        if name != key:
            continue
        existing = _parse_array(stripped.split("=", 1)[1])
        if existing is None:
            return
        lines[index] = f"{key} = {json.dumps(_order_efforts(existing + wanted))}"
        return

    lines.insert(start + 1, f"{key} = {json.dumps(wanted)}")


def _catalog_reasoning_efforts(catalog_path: Path | None) -> list[str]:
    if catalog_path is None or not catalog_path.is_file():
        return []
    try:
        payload = json.loads(catalog_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    efforts: list[str] = []
    for item in payload.get("models") or []:
        if not isinstance(item, dict):
            continue
        for level in item.get("supported_reasoning_levels") or []:
            if isinstance(level, dict):
                efforts.append(str(level.get("effort") or ""))
    return _order_efforts(efforts)


CODEX_BACKUP_DIRNAME = "backup-codeyun"
CODEX_BASELINE_FILENAME = "base-config.toml"
_LEGACY_BACKUP_FILENAME = "config.toml"
_DEEPSEEK_BACKUP_DIRNAME = "backup-deepseek"
OPENCODE_CATALOG_FILENAME = "opencode_models.json"


def _baseline_path(codex_home: Path) -> Path | None:
    backup_dir = codex_home / CODEX_BACKUP_DIRNAME
    for name in (CODEX_BASELINE_FILENAME, _LEGACY_BACKUP_FILENAME):
        candidate = backup_dir / name
        if candidate.is_file():
            return candidate
    return None


def ensure_baseline_snapshot(codex_home: Path, config_path: Path) -> bool:
    """Persist the pristine pre-CodeYun config once, before any provider change.

    Switching back to OpenAI must land on the original config, never on one a
    DeepSeek or opencode switch left behind, so this snapshot is written only when
    it does not exist yet and is never overwritten afterwards.
    """

    backup_dir = codex_home / CODEX_BACKUP_DIRNAME
    baseline = backup_dir / CODEX_BASELINE_FILENAME
    if baseline.is_file():
        return False
    # Migrate a snapshot kept by the previous (pre-opencode only) mechanism.
    source = _baseline_path(codex_home) or (config_path if config_path.is_file() else None)
    if source is None:
        return False
    backup_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, baseline)
    return True


def _collect_tables(lines: list[str], prefix: str) -> dict[str, list[str]]:
    """Collect ``[prefix*]`` table blocks (header + body), trailing blanks trimmed."""

    tables: dict[str, list[str]] = {}
    name: str | None = None
    body: list[str] = []
    for line in lines:
        header = _HEADER_RE.match(line)
        if header:
            if name is not None:
                tables[name] = body
            table = header.group(1).strip().strip('"').strip("'")
            name = table if table.startswith(prefix) else None
            body = [line] if name is not None else []
            continue
        if name is not None:
            body.append(line)
    if name is not None:
        tables[name] = body
    for value in tables.values():
        while value and not value[-1].strip():
            value.pop()
    return tables


def _opencode_provider_table() -> list[str]:
    return [
        f"[model_providers.{OPENCODE_GO_PROVIDER_ID}]",
        'name = "OpenCode Go (proxy)"',
        f'base_url = "{get_opencode_proxy_base_url()}/v1"',
        'wire_api = "responses"',
        'experimental_bearer_token = "opencode-proxy"',
    ]


def restore_codeyun_backup(codex_home: Path) -> bool:
    baseline = _baseline_path(codex_home)
    if baseline is None:
        return False
    config_path = codex_home / "config.toml"
    lines = _read_config_lines(baseline)
    if config_path.is_file():
        # Existing threads store the provider they were created with; Codex refuses
        # to open a thread whose provider is missing ("Model provider ... not
        # found").  Keep such provider blocks so old opencode/deepseek threads
        # still load, while the active model/provider comes from the baseline.
        baseline_tables = {name.lower() for name in _collect_tables(lines, "model_providers.")}
        extra = [
            body
            for name, body in _collect_tables(_read_config_lines(config_path), "model_providers.").items()
            if name.lower() not in baseline_tables
        ]
        if extra:
            lines = [*lines, ""]
            for body in extra:
                lines.extend(body)
    # The proxy provider is CodeYun-managed and carries no secret, so keep it
    # defined even when the live config no longer has it; without it Codex cannot
    # open any thread that was created on the opencode provider.
    if not any(name.lower() == f"model_providers.{OPENCODE_GO_PROVIDER_ID}" for name in _collect_tables(lines, "model_providers.")):
        lines = [*lines, "", *_opencode_provider_table()]
    _write_config_lines(config_path, lines)
    # Drop the generated opencode catalog so no stale model list is left behind.
    (codex_home / OPENCODE_CATALOG_FILENAME).unlink(missing_ok=True)
    return True


def ensure_opencode_provider_defined(config_path: Path) -> bool:
    """Re-add the CodeYun-managed opencode provider block if it went missing.

    Threads created on the opencode provider carry ``model_provider = "opencode_go"``
    and Codex refuses to open them once the block is gone.
    """

    lines = _read_config_lines(config_path)
    if not lines:
        return False
    tables = {name.lower() for name in _collect_tables(lines, "model_providers.")}
    if f"model_providers.{OPENCODE_GO_PROVIDER_ID}" in tables:
        return False
    _write_config_lines(config_path, [*lines, "", *_opencode_provider_table()])
    return True


def switch_to_opencode(config_path: Path, model: str) -> None:
    allowed = {item["id"] for item in discover_opencode_models()}
    if model not in allowed:
        raise CodexSwitchError(f"OpenCode Go 不支持该模型：{model}")

    if not read_opencode_go_key():
        raise CodexSwitchError("未找到 OpenCode Go 凭证，无法切换")

    ensure_baseline_snapshot(config_path.parent, config_path)
    catalog_path = build_opencode_catalog(config_path.parent, discover_opencode_models())
    lines = _strip_for_opencode(_read_config_lines(config_path))
    lines.insert(0, f"model = {json.dumps(model)}")
    lines.insert(1, f'model_provider = "{OPENCODE_GO_PROVIDER_ID}"')
    if catalog_path is not None:
        lines.insert(2, f"model_catalog_json = {json.dumps(catalog_path.as_posix())}")
    lines.extend(
        [
            "",
            f"[model_providers.{OPENCODE_GO_PROVIDER_ID}]",
            'name = "OpenCode Go (proxy)"',
            f'base_url = "{get_opencode_proxy_base_url()}/v1"',
            'wire_api = "responses"',
            'experimental_bearer_token = "opencode-proxy"',
        ]
    )
    ensure_enabled_reasoning_efforts(lines, _catalog_reasoning_efforts(catalog_path))
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
        if not chosen_model:
            chosen_model = DEFAULT_DEEPSEEK_MODEL
        if chosen_model not in {FLASH_MODE, PRO_MODE}:
            raise CodexSwitchError(f"DeepSeek 不支持该模型：{chosen_model}")
        codex_home = resolve_codex_home()
        config_path = codex_home / "config.toml"
        # Snapshot before ``ensure_deepseek_provider`` rewrites the provider, so
        # the pristine config survives and OpenAI can be restored from it.
        ensure_baseline_snapshot(codex_home, config_path)
        ensure_deepseek_provider(config_path)
        return switch_codex_mode(
            PRO_MODE if chosen_model == PRO_MODE else FLASH_MODE,
            api_key=api_key,
        )

    if normalized == "opencode":
        config_path = resolve_codex_home() / "config.toml"
        switch_to_opencode(config_path, chosen_model or DEFAULT_OPENCODE_MODEL)
        from backend.core.codex.official_setup import read_codex_status

        return {"mode": chosen_model or DEFAULT_OPENCODE_MODEL, "output": "", "status": read_codex_status()}

    raise CodexSwitchError(f"未知的供应商：{provider}")
