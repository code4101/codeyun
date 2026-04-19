"""Bridge shared WJX data sync helpers into CodeYun."""

from __future__ import annotations

from typing import Any

import pandas as pd

from kq5034.wjx_automation import 获取问卷星增量记录


class WjxDataSyncError(RuntimeError):
    """Raised when questionary data sync cannot complete safely."""


def execute_wjx_data_sync(
    *,
    login_username: str,
    password: str,
    activity_id: str,
    exist_max_id: int = 0,
) -> dict[str, Any]:
    try:
        result = 获取问卷星增量记录(
            exist_max_id=exist_max_id,
            activity_id=activity_id,
            login_username=login_username,
            password=password,
        )
    except Exception as exc:
        raise WjxDataSyncError(str(exc)) from exc

    df = result["df"]
    rows = df.astype(object).where(df.notna(), None).to_dict(orient="records")
    return {
        "activity_id": str(activity_id),
        "exist_max_id": int(result.get("exist_max_id") or 0),
        "latest_max_id": int(result.get("latest_max_id") or 0),
        "recent_count": int(result.get("recent_count") or 0),
        "fetched_count": int(result.get("fetched_count") or 0),
        "incremental_count": int(result.get("incremental_count") or 0),
        "used_all_pages": bool(result.get("used_all_pages")),
        "rows": rows,
    }

__all__ = [
    "WjxDataSyncError",
    "execute_wjx_data_sync",
]
