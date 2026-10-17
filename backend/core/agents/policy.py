"""One resource policy, independent of CLI/chat/desktop transports.

`ds` is the official DeepSeek Flash resource.
Business profiles define main + optional aux. A provider outage or unknown
quota is not evidence of exhaustion and must never silently change billing.
"""
import json
from pathlib import Path

CONFIG = Path(__file__).with_name("config.json")


def load_config() -> dict:
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def resolve(config: dict | None = None, *, business: str = "", pair: str = "") -> dict:
    """Resolve a task-local override without modifying the business defaults."""
    config = config or load_config()
    if pair:
        parts = [part.strip().lower() for part in pair.split("+")]
        if len(parts) not in (1, 2) or any(p not in config["resources"] for p in parts):
            raise ValueError("组合须为 codex/cc/ds 单资源或 A+B")
        profile = {"main": parts[0], "aux": parts[1] if len(parts) == 2 else None}
    else:
        if business and business not in config["businesses"]:
            raise ValueError("未知业务，请省略 business 使用默认，或明确 pair")
        profile = dict(config["businesses"].get(business, config["default"]))
    return {"business": business or None, **profile,
            "resources": {name: config["resources"][name] for name in profile.values() if name}}


def ds_route(config: dict, usage: dict, session_provider: str = "") -> tuple[dict, str]:
    """DS always uses the official provider; usage is a legacy compatibility argument.

    Existing Go sessions may resume with the official provider, but no new
    request or quota probe is ever sent to Go.
    """
    route = config["resources"]["ds"]["primary"]
    if route["provider"] != "deepseek-official" or route["base_url"] != "https://api.deepseek.com/v1":
        raise ValueError("DS requires the official DeepSeek endpoint")
    if session_provider not in {"", "deepseek-official", "opencode-go"}:
        raise ValueError("DS session provider mismatch")
    return route, "official_only"


def select_ds_route(*, session_provider: str = "") -> tuple[dict, dict]:
    """Select the official route without querying third-party quotas."""
    route, reason = ds_route(load_config(), {}, session_provider)
    return route, {"available": False, "windows": [], "reason": reason}


def read_official_ds_key() -> str:
    """Reuse deployment environment or the existing public credential reader."""
    import os
    import sys
    root = Path(load_config()["codeyun_root"])
    sys.path.insert(0, str(root.parent / "xlproject" / "src"))
    import xlproject.loadenv  # noqa: F401
    from backend.core.codex.official_setup import read_existing_deepseek_token
    return os.environ.get("DEEPSEEK_API_KEY") or read_existing_deepseek_token()
