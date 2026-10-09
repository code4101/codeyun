"""One resource policy, independent of CLI/chat/desktop transports.

`ds` is a logical Flash resource; Go and official are backing providers.
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
    """Only a currently exhausted quota window allows official fallback."""
    resource = config["resources"]["ds"]
    if session_provider and session_provider not in {
        resource["primary"]["provider"], resource["fallback"]["provider"]
    }:
        raise ValueError("DS 会话 provider 不匹配")
    exhausted = usage.get("available") and any(
        window.get("remaining_percent") == 0 for window in usage.get("windows", [])
    )
    if exhausted and resource.get("fallback_on") == "disabled":
        return resource["primary"], "official_fallback_disabled"
    return (resource["fallback"], "quota_exhausted") if exhausted else (resource["primary"], "primary")


def select_ds_route(*, session_provider: str = "") -> tuple[dict, dict]:
    """Read the public quota endpoint, then choose a route for one execution."""
    from backend.core.opencode_usage import read_opencode_go_usage
    usage = read_opencode_go_usage()
    route, reason = ds_route(load_config(), usage, session_provider)
    return route, {"available": usage.get("available", False),
                   "windows": usage.get("windows", []), "reason": reason}


def read_official_ds_key() -> str:
    """Reuse deployment environment or the existing public credential reader."""
    import os
    import sys
    root = Path(load_config()["codeyun_root"])
    sys.path.insert(0, str(root.parent / "xlproject" / "src"))
    import xlproject.loadenv  # noqa: F401
    from backend.core.codex.official_setup import read_existing_deepseek_token
    return os.environ.get("DEEPSEEK_API_KEY") or read_existing_deepseek_token()
