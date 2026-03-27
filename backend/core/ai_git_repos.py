from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any

from sqlmodel import Session

from backend.models import AppSetting


AI_GIT_REPOS_SETTING_KEY_PREFIX = "ai_git_commit.saved_repos.user"


def build_ai_git_repos_setting_key(user_id: int) -> str:
    return f"{AI_GIT_REPOS_SETTING_KEY_PREFIX}.{int(user_id)}"


def _normalize_repo_id(value: Any) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return f"repo-{uuid.uuid4().hex[:12]}"


def _normalize_repo_timestamp(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _normalize_repo_order_index(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, float):
        return max(0, int(value))
    return None


def _normalize_repo_entry_id(value: Any) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return ""


def _normalize_repo_cwd(value: Any) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return ""


def _build_repo_fallback_name(cwd: str, fallback_index: int) -> str:
    trimmed = cwd.strip()
    if trimmed:
        name = Path(trimmed.rstrip("/\\")).name.strip()
        if name:
            return name
        return trimmed
    return f"项目 {fallback_index}"


def _normalize_repo_name(value: Any, cwd: str, fallback_index: int) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return _build_repo_fallback_name(cwd, fallback_index)


def _normalize_repo_path_key(entry_id: str, cwd: str) -> tuple[str, str]:
    normalized_cwd = cwd.replace("\\", "/").rstrip("/").casefold()
    return entry_id.casefold(), normalized_cwd


def _normalize_repo_item(
    raw_item: Any,
    *,
    fallback_index: int,
    fallback_order_index: int,
    existing_item: dict[str, Any] | None = None,
    now: float | None = None,
) -> dict[str, Any] | None:
    if not isinstance(raw_item, dict):
        return None

    entry_id = _normalize_repo_entry_id(raw_item.get("entry_id"))
    cwd = _normalize_repo_cwd(raw_item.get("cwd") or raw_item.get("path"))
    if not entry_id or not cwd:
        return None

    repo_id = _normalize_repo_id(raw_item.get("id"))
    existing_item = existing_item or {}
    created_at = (
        _normalize_repo_timestamp(raw_item.get("created_at"))
        or _normalize_repo_timestamp(existing_item.get("created_at"))
        or now
    )
    updated_at = (
        now
        if now is not None
        else _normalize_repo_timestamp(raw_item.get("updated_at"))
        or _normalize_repo_timestamp(existing_item.get("updated_at"))
        or created_at
    )
    last_used_at = (
        _normalize_repo_timestamp(raw_item.get("last_used_at"))
        or _normalize_repo_timestamp(existing_item.get("last_used_at"))
    )

    return {
        "id": repo_id,
        "name": _normalize_repo_name(raw_item.get("name"), cwd, fallback_index),
        "entry_id": entry_id,
        "cwd": cwd,
        "pinned": bool(raw_item.get("pinned", existing_item.get("pinned", False))),
        "order_index": (
            _normalize_repo_order_index(raw_item.get("order_index"))
            or _normalize_repo_order_index(existing_item.get("order_index"))
            or fallback_order_index
        ),
        "created_at": created_at,
        "updated_at": updated_at,
        "last_used_at": last_used_at,
    }


def _normalize_repo_payload(
    value: Any,
    *,
    existing_items: dict[str, dict[str, Any]] | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    payload = value if isinstance(value, dict) else {}
    raw_items = payload.get("items")
    existing_items = existing_items or {}

    items: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_paths: set[tuple[str, str]] = set()

    for raw_item in raw_items if isinstance(raw_items, list) else []:
        raw_id = raw_item.get("id") if isinstance(raw_item, dict) else None
        existing_item = existing_items.get(str(raw_id).strip()) if raw_id is not None else None
        item = _normalize_repo_item(
            raw_item,
            fallback_index=len(items) + 1,
            fallback_order_index=len(items),
            existing_item=existing_item,
            now=now,
        )
        if item is None:
            continue

        if item["id"] in seen_ids:
            continue

        path_key = _normalize_repo_path_key(item["entry_id"], item["cwd"])
        if path_key in seen_paths:
            continue

        seen_ids.add(item["id"])
        seen_paths.add(path_key)
        item["order_index"] = len(items)
        items.append(item)

    return {"items": items}


def list_user_ai_git_repos(session: Session, user_id: int) -> dict[str, Any]:
    row = session.get(AppSetting, build_ai_git_repos_setting_key(user_id))
    if row is None:
        return {"items": []}
    return _normalize_repo_payload(row.value)


def save_user_ai_git_repos(
    session: Session,
    user_id: int,
    *,
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    setting_key = build_ai_git_repos_setting_key(user_id)
    row = session.get(AppSetting, setting_key)
    existing_payload = _normalize_repo_payload(row.value) if row and isinstance(row.value, dict) else {"items": []}
    existing_items = {
        item["id"]: item
        for item in existing_payload["items"]
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }

    now = time.time()
    normalized = _normalize_repo_payload(
        {"items": items},
        existing_items=existing_items,
        now=now,
    )

    if not normalized["items"]:
        if row is not None:
            session.delete(row)
            session.commit()
        return normalized

    if row is None:
        row = AppSetting(key=setting_key)
    row.value = normalized
    row.updated_at = now
    session.add(row)
    session.commit()
    return normalized


def touch_user_ai_git_repo(session: Session, user_id: int, repo_id: str) -> dict[str, Any] | None:
    setting_key = build_ai_git_repos_setting_key(user_id)
    row = session.get(AppSetting, setting_key)
    if row is None or not isinstance(row.value, dict):
        return None

    payload = _normalize_repo_payload(row.value)
    target: dict[str, Any] | None = None
    now = time.time()
    for item in payload["items"]:
        if item["id"] != repo_id:
            continue
        item["last_used_at"] = now
        item["updated_at"] = now
        target = item
        break

    if target is None:
        return None

    row.value = payload
    row.updated_at = now
    session.add(row)
    session.commit()
    return target
