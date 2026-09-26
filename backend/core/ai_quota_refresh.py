"""Hourly quota collection shared by the runtime job and the quota page."""
from __future__ import annotations

from sqlmodel import Session

from backend.core.codex.official_setup import PROVIDER_ID, read_existing_deepseek_token
from backend.core.codex.weekly_quota import collect_codex_quota_snapshot
from backend.core.deepseek_balance import collect_deepseek_balance_snapshot
from backend.core.opencode_usage import collect_opencode_usage_snapshot
from backend.core.system_ai_resources import SystemAiResourceError, resolve_system_ai_resource

AI_QUOTA_REFRESH_TASK_KEY = "ai_quota_refresh"


def resolve_deepseek_key(session: Session) -> str:
    try:
        runtime = resolve_system_ai_resource(session=session, provider_id=PROVIDER_ID)
        key = str(runtime.get("api_key") or "").strip()
    except SystemAiResourceError:
        key = ""
    if key:
        return key
    return read_existing_deepseek_token()


def collect_ai_quota_snapshots() -> dict:
    """Collect all providers; any failed reading fails the job for scheduler retry.

    Successful providers still persist their snapshots when another provider fails.
    Collectors preserve the last successful snapshot on a failed probe.
    """
    from backend.db import engine

    def collect_deepseek():
        with Session(engine) as session:
            key = resolve_deepseek_key(session)
        return collect_deepseek_balance_snapshot(key)

    results = {}
    errors = []
    for provider, collect in (
        ("Codex", collect_codex_quota_snapshot),
        ("OpenCode Go", collect_opencode_usage_snapshot),
        ("DeepSeek", collect_deepseek),
    ):
        try:
            snapshot = collect()
            payload = snapshot.get("payload", snapshot)
            if payload.get("error") or payload.get("available") is False:
                raise RuntimeError(payload.get("error") or "额度采集不可用")
            results[provider] = snapshot.get("observed_at", "")
        except Exception as exc:
            errors.append(f"{provider}: {exc}")
    if errors:
        raise RuntimeError("；".join(errors))
    return {"observed_at": results}
