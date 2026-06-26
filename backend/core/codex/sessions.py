from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
from collections import defaultdict
from contextlib import nullcontext
from datetime import date as calendar_date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from sqlmodel import Session, delete, select

from backend.core.ai.chat import chat_with_provider
from backend.core.ai.app_config import AI_APP_CODEX_DAILY_SUMMARY, AiAppConfigError, resolve_ai_app_runtime_config
from backend.db import engine
from backend.core.notes.semantics import (
    NOTE_CATEGORY_BUILTIN_KEYS,
    NOTE_CATEGORY_DEFAULT,
    NOTE_TYPE_BUILTIN_PALETTE,
    build_note_category_palette_setting_key,
    build_note_type_palette_setting_key,
    is_note_auto_classification_blocked_category,
)
from backend.models import (
    AppSetting,
    CodexDailySummaryRun,
    CodexTextCacheMessage,
    CodexTextCacheRoot,
    CodexTextCacheThread,
    CodexTextCacheTurn,
    User,
)


_CODEX_CACHE_LOCK = threading.Lock()
_CODEX_DAILY_SUMMARY_TIMEZONE = "Asia/Shanghai"
_CODEX_DAILY_SUMMARY_PROVIDER_ID = "deepseek"
_CODEX_DAILY_SUMMARY_GENERATED_BY = "deepseek"
_CODEX_DAILY_SUMMARY_DEFAULT_MODEL = "deepseek-v4-pro"
_CODEX_DAILY_SUMMARY_TIMEOUT_SECONDS = 900.0
_CODEX_DAILY_SUMMARY_PROMPT_VERSION = "2026-04-23.hierarchical-note-types-v1"
_CODEX_DAILY_SUMMARY_HEARTBEAT_INTERVAL_SECONDS = 2.0
_CODEX_DAILY_SUMMARY_USER_TEXT_LIMIT = 320
_CODEX_DAILY_SUMMARY_ASSISTANT_TEXT_LIMIT = 320
_CODEX_DAILY_SUMMARY_PROCESS_TEXT_LIMIT = 180
_CODEX_DAILY_SUMMARY_IGNORED_THREAD_PATTERNS = (
    re.compile(r"\bmemory\s+writing\s+agent\b", re.IGNORECASE),
    re.compile(r"\bcodex-cli-workspace\b", re.IGNORECASE),
    re.compile(r"通过\s+CodeYun\s+调用本机\s+Codex\s+CLI", re.IGNORECASE),
)


def _default_codex_root_dir() -> Path:
    return (Path.home() / ".codex").resolve(strict=False)


def _clean_path_text(value: str | None) -> str:
    text = str(value or "").strip().strip('"')
    if text.startswith("\\\\?\\"):
        text = text[4:]
    return text


def _resolve_display_path(value: str | None) -> str:
    text = _clean_path_text(value)
    if not text:
        return ""
    try:
        return str(Path(text).expanduser().resolve(strict=False))
    except OSError:
        return text


def _path_match_key(value: str | None) -> str:
    text = _resolve_display_path(value)
    if not text:
        return ""
    return os.path.normcase(os.path.normpath(text))


def _remote_path_match_key(value: str | None) -> str:
    text = _clean_path_text(value)
    if not text:
        return ""
    return re.sub(r"[\\/]+", "/", text.rstrip("\\/")).lower()


def build_remote_codex_cache_root_key(device_entry_id: str, root_dir: str | None) -> str:
    entry_key = str(device_entry_id or "").strip()
    if not entry_key:
        raise ValueError("device_entry_id 不能为空")
    root_key = _remote_path_match_key(root_dir) or "default"
    return f"device-entry:{entry_key}:{root_key}"


def _path_name(value: str | None) -> str:
    text = _resolve_display_path(value)
    if not text:
        return ""
    normalized = text.rstrip("\\/")
    if not normalized:
        return text
    return os.path.basename(normalized) or normalized


def _load_json_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    return payload if isinstance(payload, dict) else {}


def _parse_timestamp_seconds(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        timestamp = float(value)
        return timestamp / 1000 if abs(timestamp) >= 1_000_000_000_000 else timestamp

    text = str(value).strip()
    if not text:
        return None

    try:
        numeric = float(text)
    except ValueError:
        numeric = None
    if numeric is not None:
        return numeric / 1000 if abs(numeric) >= 1_000_000_000_000 else numeric

    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        return datetime.fromisoformat(normalized).timestamp()
    except ValueError:
        return None


def _resolve_codex_root_dir(root_dir: str | None = None) -> tuple[Path, Path]:
    requested = _clean_path_text(root_dir)
    candidate = Path(requested).expanduser() if requested else _default_codex_root_dir()
    resolved = candidate.resolve(strict=False)
    if not resolved.exists():
        raise FileNotFoundError(f"Codex 根目录不存在：{resolved}")
    if not resolved.is_dir():
        raise NotADirectoryError(f"Codex 根目录不是目录：{resolved}")
    return resolved, _default_codex_root_dir()


def _load_workspace_roots(root_path: Path) -> list[dict[str, Any]]:
    payload = _load_json_file(root_path / ".codex-global-state.json")
    raw_paths = []
    for key in ("electron-saved-workspace-roots", "active-workspace-roots"):
        values = payload.get(key)
        if isinstance(values, list):
            raw_paths.extend(item for item in values if isinstance(item, str) and item.strip())

    label_map = payload.get("electron-workspace-root-labels")
    if not isinstance(label_map, dict):
        label_map = {}

    roots: list[dict[str, Any]] = []
    seen: set[str] = set()
    for order, raw_path in enumerate(raw_paths):
        display_path = _resolve_display_path(raw_path)
        match_key = _path_match_key(display_path)
        if not match_key or match_key in seen:
            continue
        seen.add(match_key)
        label = str(label_map.get(raw_path) or label_map.get(display_path) or "").strip() or _path_name(display_path)
        roots.append(
            {
                "path": display_path,
                "match_key": match_key,
                "label": label,
                "order": order,
            }
        )
    return roots


def _load_session_index_titles(root_path: Path) -> dict[str, str]:
    titles: dict[str, str] = {}
    index_path = root_path / "session_index.jsonl"
    if not index_path.exists():
        return titles

    with index_path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            thread_id = str(payload.get("id") or "").strip()
            title = str(payload.get("thread_name") or "").strip()
            if thread_id and title:
                titles[thread_id] = title
    return titles


def _load_thread_rows(root_path: Path) -> list[dict[str, Any]]:
    state_db_path = root_path / "state_5.sqlite"
    if not state_db_path.exists():
        raise FileNotFoundError(f"未找到 Codex 状态库：{state_db_path}")

    title_map = _load_session_index_titles(root_path)
    conn = sqlite3.connect(state_db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT
                id,
                rollout_path,
                created_at,
                updated_at,
                cwd,
                title,
                archived,
                first_user_message
            FROM threads
            ORDER BY updated_at DESC, created_at DESC, id DESC
            """
        ).fetchall()
    finally:
        conn.close()

    thread_rows: list[dict[str, Any]] = []
    for row in rows:
        original_cwd = _clean_path_text(row["cwd"])
        cwd = _resolve_display_path(original_cwd)
        rollout_path = _resolve_display_path(row["rollout_path"])
        first_user_message = str(row["first_user_message"] or "").strip()
        title = str(row["title"] or "").strip() or title_map.get(str(row["id"]), "").strip() or first_user_message or "未命名会话"
        thread_rows.append(
            {
                "id": str(row["id"]),
                "title": title,
                "preview": first_user_message or None,
                "cwd": cwd or None,
                "original_cwd": original_cwd or None,
                "rollout_path": rollout_path or None,
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "archived": bool(row["archived"]),
            }
        )
    return thread_rows


def _find_workspace_root(cwd: str | None, workspace_roots: list[dict[str, Any]]) -> dict[str, Any] | None:
    cwd_key = _path_match_key(cwd)
    if not cwd_key:
        return None

    matched: dict[str, Any] | None = None
    matched_length = -1
    for root in workspace_roots:
        root_key = root["match_key"]
        if cwd_key == root_key or cwd_key.startswith(root_key + os.sep):
            if len(root_key) > matched_length:
                matched = root
                matched_length = len(root_key)
    return matched


def _serialize_thread_summary(thread: dict[str, Any], workspace_root: dict[str, Any] | None) -> dict[str, Any]:
    cwd = thread["cwd"]
    label = _path_name(cwd) or thread["title"]
    secondary_label = None
    workspace_root_path = None
    if workspace_root is not None:
        workspace_root_path = workspace_root["path"]
        if _path_match_key(cwd) != workspace_root["match_key"]:
            secondary_label = workspace_root["label"] or _path_name(workspace_root_path)
            if secondary_label == label:
                secondary_label = None

    return {
        "id": thread["id"],
        "title": thread["title"],
        "preview": thread["preview"],
        "cwd": cwd,
        "original_cwd": thread["original_cwd"],
        "rollout_path": thread["rollout_path"],
        "created_at": thread["created_at"],
        "updated_at": thread["updated_at"],
        "archived": thread["archived"],
        "project_label": label,
        "project_secondary_label": secondary_label,
        "workspace_root": workspace_root_path,
    }


def _extract_message_text(content: Any) -> str:
    if not isinstance(content, list):
        return ""

    parts: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        if isinstance(text, str) and text.strip():
            parts.append(text)
    return "\n\n".join(parts).strip()


def _extract_message_image_url(item: dict[str, Any]) -> str | None:
    image_url = item.get("image_url")
    if isinstance(image_url, str) and image_url.strip():
        return image_url
    if isinstance(image_url, dict):
        url_value = image_url.get("url")
        if isinstance(url_value, str) and url_value.strip():
            return url_value
    return None


def _extract_message_images(content: Any) -> list[dict[str, Any]]:
    if not isinstance(content, list):
        return []

    images: list[dict[str, Any]] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        image_url = _extract_message_image_url(item)
        if not image_url:
            continue
        image_type = str(item.get("type") or "image").strip() or "image"
        if not image_type.endswith("image") and image_type != "image":
            continue
        images.append(
            {
                "index": len(images) + 1,
                "type": image_type,
                "image_url": image_url,
            }
        )
    return images


def _should_skip_user_message(text: str) -> bool:
    normalized = text.strip()
    return normalized.startswith("<environment_context>") and normalized.endswith("</environment_context>")


def _iter_rollout_message_entries(rollout_path: Path):
    if not rollout_path.exists():
        raise FileNotFoundError(f"未找到会话 JSONL：{rollout_path}")

    with rollout_path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            if payload.get("type") != "response_item":
                continue
            response_item = payload.get("payload")
            if not isinstance(response_item, dict):
                continue
            if response_item.get("type") != "message":
                continue

            role = str(response_item.get("role") or "").strip()
            if role not in {"user", "assistant"}:
                continue

            yield {
                "timestamp": payload.get("timestamp"),
                "role": role,
                "phase": response_item.get("phase"),
                "content": response_item.get("content"),
            }


def _load_rollout_messages(rollout_path: Path) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    for entry in _iter_rollout_message_entries(rollout_path):
        text = _extract_message_text(entry.get("content"))
        if not text:
            continue
        if entry["role"] == "user" and _should_skip_user_message(text):
            continue

        messages.append(
            {
                "seq": len(messages) + 1,
                "timestamp": entry.get("timestamp"),
                "role": entry["role"],
                "phase": entry.get("phase"),
                "text": text,
            }
        )
    return messages


def _build_thread_turns(
    thread: dict[str, Any],
    group: dict[str, Any],
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    turns: list[dict[str, Any]] = []
    thread_created_at = _parse_timestamp_seconds(thread.get("created_at"))
    thread_updated_at = _parse_timestamp_seconds(thread.get("updated_at"))
    index = 0
    turn_index = 0

    while index < len(messages):
        current_message = messages[index]
        if current_message["role"] != "user":
            index += 1
            continue

        turn_index += 1
        assistant_messages: list[dict[str, Any]] = []
        index += 1
        while index < len(messages) and messages[index]["role"] == "assistant":
            assistant_messages.append(messages[index])
            index += 1

        start_at = _parse_timestamp_seconds(current_message.get("timestamp")) or thread_created_at
        end_message = assistant_messages[-1] if assistant_messages else current_message
        end_at = _parse_timestamp_seconds(end_message.get("timestamp"))
        has_explicit_result = any(item.get("phase") == "final_answer" for item in assistant_messages)
        is_last_turn = index >= len(messages)

        if assistant_messages and not has_explicit_result and is_last_turn and thread_updated_at is not None:
            end_at = max(end_at or thread_updated_at, thread_updated_at)
        elif end_at is None:
            end_at = thread_updated_at or start_at

        if start_at is None and end_at is not None:
            start_at = min(end_at, thread_created_at or end_at)
        if end_at is None and start_at is not None:
            end_at = start_at
        if start_at is None or end_at is None:
            continue
        if end_at < start_at:
            end_at = start_at

        turns.append(
            {
                "id": f"{thread['id']}:{turn_index}",
                "thread_id": thread["id"],
                "turn_index": turn_index,
                "thread_title": thread["title"],
                "project_label": thread["project_label"],
                "project_secondary_label": thread.get("project_secondary_label"),
                "workspace_root": thread.get("workspace_root"),
                "group_key": group["key"],
                "group_label": group["label"],
                "user_seq": current_message["seq"],
                "assistant_seq": assistant_messages[-1]["seq"] if assistant_messages else None,
                "start_at": start_at,
                "end_at": end_at,
                "duration_seconds": max(end_at - start_at, 0.0),
                "completed": bool(assistant_messages) and has_explicit_result,
                "preview": current_message["text"],
            }
        )

    return turns


def _build_workload_segments(turns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not turns:
        return []

    start_counts: dict[float, int] = defaultdict(int)
    end_counts: dict[float, int] = defaultdict(int)
    time_points: set[float] = set()

    for turn in turns:
        start_at = float(turn["start_at"])
        end_at = float(turn["end_at"])
        start_counts[start_at] += 1
        end_counts[end_at] += 1
        time_points.add(start_at)
        time_points.add(end_at)

    segments: list[dict[str, Any]] = []
    sorted_points = sorted(time_points)
    concurrency = 0
    for index, point in enumerate(sorted_points[:-1]):
        concurrency = max(0, concurrency - end_counts.get(point, 0) + start_counts.get(point, 0))
        next_point = sorted_points[index + 1]
        if next_point <= point or concurrency <= 0:
            continue
        segments.append(
            {
                "start_at": point,
                "end_at": next_point,
                "duration_seconds": next_point - point,
                "concurrency": concurrency,
            }
        )
    return segments


def _aggregate_workload_turn_by_local_day(
    day_seconds: dict[str, float],
    *,
    start_at: float,
    end_at: float,
) -> None:
    if end_at <= start_at:
        return

    current = datetime.fromtimestamp(start_at)
    while True:
        day_start = datetime(current.year, current.month, current.day)
        next_day = day_start + timedelta(days=1)
        chunk_start = max(start_at, day_start.timestamp())
        chunk_end = min(end_at, next_day.timestamp())
        if chunk_end > chunk_start:
            day_key = day_start.date().isoformat()
            day_seconds[day_key] = day_seconds.get(day_key, 0.0) + (chunk_end - chunk_start)
        if next_day.timestamp() >= end_at:
            break
        current = next_day


def _file_signature(path: Path) -> tuple[int | None, int | None]:
    try:
        stat = path.stat()
    except OSError:
        return None, None
    modified_ns = int(getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1_000_000_000)))
    return int(stat.st_size), modified_ns


def _session_scope(session: Session | None):
    return nullcontext(session) if session is not None else Session(engine)


def _serialize_cached_thread_row(row: CodexTextCacheThread) -> dict[str, Any]:
    return {
        "id": row.thread_id,
        "title": row.title,
        "preview": row.preview,
        "cwd": row.cwd,
        "original_cwd": row.original_cwd,
        "rollout_path": row.rollout_path,
        "created_at": row.created_at_source,
        "updated_at": row.updated_at_source,
        "archived": bool(row.archived),
        "project_label": row.project_label,
        "project_secondary_label": row.project_secondary_label,
        "workspace_root": row.workspace_root,
    }


def _build_group_stub(thread: dict[str, Any]) -> dict[str, Any]:
    return {
        "key": thread["cwd"] or thread["id"],
        "label": thread["project_label"],
        "secondary_label": thread["project_secondary_label"],
        "cwd": thread["cwd"],
        "workspace_root": thread["workspace_root"],
    }


def _build_groups_from_threads(thread_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups_by_key: dict[str, dict[str, Any]] = {}
    group_threads: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for thread in thread_rows:
        group = _build_group_stub(thread)
        group_threads[group["key"]].append(thread)
        if group["key"] not in groups_by_key:
            groups_by_key[group["key"]] = group

    groups: list[dict[str, Any]] = []
    for group_key, threads in group_threads.items():
        threads.sort(
            key=lambda item: (
                float(item["updated_at"] or 0),
                float(item["created_at"] or 0),
                item["id"],
            ),
            reverse=True,
        )
        latest_updated_at = threads[0]["updated_at"] if threads else None
        archived_thread_count = sum(1 for item in threads if item["archived"])
        group_meta = groups_by_key[group_key]
        groups.append(
            {
                "key": group_meta["key"],
                "label": group_meta["label"],
                "secondary_label": group_meta["secondary_label"],
                "cwd": group_meta["cwd"],
                "workspace_root": group_meta["workspace_root"],
                "thread_count": len(threads),
                "archived_thread_count": archived_thread_count,
                "latest_updated_at": latest_updated_at,
                "threads": threads,
            }
        )

    groups.sort(
        key=lambda item: (
            float(item["latest_updated_at"] or 0),
            item["label"],
            item["key"],
        ),
        reverse=True,
    )
    return groups


def _sort_codex_threads(thread_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        thread_rows,
        key=lambda item: (
            float(item["updated_at"] or 0),
            float(item["created_at"] or 0),
            item["id"],
        ),
        reverse=True,
    )


def _paginate_codex_threads(
    thread_rows: list[dict[str, Any]],
    *,
    thread_offset: int = 0,
    thread_limit: int | None = None,
) -> tuple[list[dict[str, Any]], int, int | None, bool]:
    offset = max(0, int(thread_offset or 0))
    limit = int(thread_limit) if thread_limit is not None else None
    if limit is not None and limit <= 0:
        limit = None

    sorted_threads = _sort_codex_threads(thread_rows)
    if limit is None:
        page_threads = sorted_threads[offset:]
    else:
        page_threads = sorted_threads[offset : offset + limit]
    has_more = offset + len(page_threads) < len(sorted_threads)
    return page_threads, offset, limit, has_more


def paginate_codex_overview_payload(
    payload: dict[str, Any],
    *,
    thread_offset: int = 0,
    thread_limit: int | None = None,
) -> dict[str, Any]:
    """Slice an overview payload by global thread order while preserving total counts."""
    if not isinstance(payload, dict):
        raise ValueError("Codex overview 必须是对象")

    threads = _iter_remote_overview_threads(payload)
    page_threads, offset, limit, has_more = _paginate_codex_threads(
        threads,
        thread_offset=thread_offset,
        thread_limit=thread_limit,
    )
    total_threads = int(payload.get("total_threads") or len(threads))
    archived_threads = int(payload.get("archived_threads") or sum(1 for item in threads if item["archived"]))
    all_groups = _build_groups_from_threads(threads)

    return {
        **payload,
        "total_groups": int(payload.get("total_groups") or len(all_groups)),
        "total_threads": total_threads,
        "archived_threads": archived_threads,
        "groups": _build_groups_from_threads(page_threads),
        "thread_offset": offset,
        "thread_limit": limit,
        "returned_threads": len(page_threads),
        "has_more": has_more,
    }


def _assign_thread_summary_to_cache_row(
    row: CodexTextCacheThread,
    summary: dict[str, Any],
    *,
    now: float,
) -> bool:
    changed = False
    field_pairs = {
        "title": summary["title"],
        "preview": summary["preview"],
        "cwd": summary["cwd"],
        "original_cwd": summary["original_cwd"],
        "rollout_path": summary["rollout_path"],
        "created_at_source": summary["created_at"],
        "updated_at_source": summary["updated_at"],
        "archived": summary["archived"],
        "project_label": summary["project_label"],
        "project_secondary_label": summary["project_secondary_label"],
        "workspace_root": summary["workspace_root"],
    }
    for field_name, value in field_pairs.items():
        if getattr(row, field_name) != value:
            setattr(row, field_name, value)
            changed = True
    row.refreshed_at = now
    row.updated_at = now
    return changed


def _replace_thread_text_cache(
    session: Session,
    *,
    root_key: str,
    thread_row: CodexTextCacheThread,
    rollout_size: int | None,
    rollout_mtime_ns: int | None,
    messages: list[dict[str, Any]],
    now: float,
) -> None:
    session.exec(
        delete(CodexTextCacheMessage).where(
            CodexTextCacheMessage.root_key == root_key,
            CodexTextCacheMessage.thread_id == thread_row.thread_id,
        )
    )
    session.exec(
        delete(CodexTextCacheTurn).where(
            CodexTextCacheTurn.root_key == root_key,
            CodexTextCacheTurn.thread_id == thread_row.thread_id,
        )
    )

    for message in messages:
        session.add(
            CodexTextCacheMessage(
                root_key=root_key,
                thread_id=thread_row.thread_id,
                seq=int(message["seq"]),
                timestamp=str(message.get("timestamp") or "") or None,
                role=str(message["role"]),
                phase=str(message.get("phase") or "") or None,
                text=str(message["text"]),
                created_at=now,
                updated_at=now,
            )
        )

    thread_summary = _serialize_cached_thread_row(thread_row)
    group = _build_group_stub(thread_summary)
    turns = _build_thread_turns(thread_summary, group, messages)
    for turn in turns:
        session.add(
            CodexTextCacheTurn(
                root_key=root_key,
                thread_id=thread_row.thread_id,
                turn_index=int(turn["turn_index"]),
                user_seq=int(turn["user_seq"]),
                assistant_seq=turn["assistant_seq"],
                start_at=float(turn["start_at"]),
                end_at=float(turn["end_at"]),
                duration_seconds=float(turn["duration_seconds"]),
                completed=bool(turn["completed"]),
                preview=turn["preview"],
                created_at=now,
                updated_at=now,
            )
        )

    thread_row.rollout_size = rollout_size
    thread_row.rollout_mtime_ns = rollout_mtime_ns
    thread_row.message_count = len(messages)
    thread_row.user_message_count = sum(1 for item in messages if item["role"] == "user")
    thread_row.assistant_message_count = sum(1 for item in messages if item["role"] == "assistant")
    thread_row.refreshed_at = now
    thread_row.updated_at = now
    session.add(thread_row)


def _ensure_remote_codex_cache_root(
    session: Session,
    *,
    device_entry_id: str,
    payload: dict[str, Any],
    now: float,
) -> tuple[str, CodexTextCacheRoot]:
    root_dir = _clean_path_text(payload.get("root_dir")) or _clean_path_text(payload.get("default_root_dir"))
    if not root_dir:
        raise ValueError("远端 Codex 结果缺少 root_dir")

    root_key = build_remote_codex_cache_root_key(device_entry_id, root_dir)
    root_row = session.get(CodexTextCacheRoot, root_key)
    if root_row is None:
        root_row = CodexTextCacheRoot(root_key=root_key, created_at=now)

    root_row.root_dir = root_dir
    root_row.default_root_dir = _clean_path_text(payload.get("default_root_dir")) or root_dir
    root_row.state_db_path = _clean_path_text(payload.get("state_db_path"))
    root_row.session_index_path = _clean_path_text(payload.get("session_index_path"))
    root_row.global_state_path = _clean_path_text(payload.get("global_state_path"))
    root_row.workspace_roots = list(root_row.workspace_roots or [])
    root_row.state_db_size = None
    root_row.state_db_mtime_ns = None
    root_row.session_index_size = None
    root_row.session_index_mtime_ns = None
    root_row.global_state_size = None
    root_row.global_state_mtime_ns = None
    root_row.refreshed_at = now
    root_row.updated_at = now
    session.add(root_row)
    return root_key, root_row


def _normalize_remote_thread_summary(raw_thread: dict[str, Any]) -> dict[str, Any]:
    thread_id = str(raw_thread.get("id") or raw_thread.get("thread_id") or "").strip()
    if not thread_id:
        raise ValueError("远端 Codex 会话缺少 id")

    title = str(raw_thread.get("title") or raw_thread.get("thread_title") or "").strip() or "未命名会话"
    cwd = _clean_path_text(raw_thread.get("cwd") or raw_thread.get("group_key")) or None
    project_label = str(raw_thread.get("project_label") or raw_thread.get("group_label") or "").strip()
    return {
        "id": thread_id,
        "title": title,
        "preview": raw_thread.get("preview"),
        "cwd": cwd,
        "original_cwd": _clean_path_text(raw_thread.get("original_cwd")) or cwd,
        "rollout_path": _clean_path_text(raw_thread.get("rollout_path")) or None,
        "created_at": raw_thread.get("created_at"),
        "updated_at": raw_thread.get("updated_at"),
        "archived": bool(raw_thread.get("archived")),
        "project_label": project_label or _path_name(cwd) or title,
        "project_secondary_label": raw_thread.get("project_secondary_label"),
        "workspace_root": _clean_path_text(raw_thread.get("workspace_root")) or None,
    }


def _iter_remote_overview_threads(payload: dict[str, Any]) -> list[dict[str, Any]]:
    threads: list[dict[str, Any]] = []
    for group in payload.get("groups") or []:
        if not isinstance(group, dict):
            continue
        for raw_thread in group.get("threads") or []:
            if not isinstance(raw_thread, dict):
                continue
            thread = dict(raw_thread)
            thread.setdefault("project_label", group.get("label"))
            thread.setdefault("project_secondary_label", group.get("secondary_label"))
            thread.setdefault("cwd", group.get("cwd") or group.get("key"))
            thread.setdefault("workspace_root", group.get("workspace_root"))
            threads.append(_normalize_remote_thread_summary(thread))
    return threads


def cache_remote_codex_overview(
    device_entry_id: str,
    payload: dict[str, Any],
    *,
    session: Session | None = None,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("远端 Codex overview 必须是对象")

    now = time.time()
    with _CODEX_CACHE_LOCK:
        with _session_scope(session) as session:
            root_key, root_row = _ensure_remote_codex_cache_root(
                session,
                device_entry_id=device_entry_id,
                payload=payload,
                now=now,
            )
            remote_threads = _iter_remote_overview_threads(payload)
            existing_rows = {
                row.thread_id: row
                for row in session.exec(
                    select(CodexTextCacheThread).where(CodexTextCacheThread.root_key == root_key)
                ).all()
            }
            seen_thread_ids: set[str] = set()
            for summary in remote_threads:
                thread_row = existing_rows.get(summary["id"])
                if thread_row is None:
                    thread_row = CodexTextCacheThread(
                        root_key=root_key,
                        thread_id=summary["id"],
                        created_at=now,
                    )
                _assign_thread_summary_to_cache_row(thread_row, summary, now=now)
                session.add(thread_row)
                seen_thread_ids.add(summary["id"])

            is_complete_overview = (
                "thread_offset" not in payload
                or (
                    int(payload.get("thread_offset") or 0) == 0
                    and not bool(payload.get("has_more"))
                    and int(payload.get("returned_threads") or len(remote_threads)) >= int(payload.get("total_threads") or len(remote_threads))
                )
            )
            if is_complete_overview:
                removed_thread_ids = set(existing_rows) - seen_thread_ids
                for removed_thread_id in removed_thread_ids:
                    session.exec(
                        delete(CodexTextCacheMessage).where(
                            CodexTextCacheMessage.root_key == root_key,
                            CodexTextCacheMessage.thread_id == removed_thread_id,
                        )
                    )
                    session.exec(
                        delete(CodexTextCacheTurn).where(
                            CodexTextCacheTurn.root_key == root_key,
                            CodexTextCacheTurn.thread_id == removed_thread_id,
                        )
                    )
                    session.exec(
                        delete(CodexTextCacheThread).where(
                            CodexTextCacheThread.root_key == root_key,
                            CodexTextCacheThread.thread_id == removed_thread_id,
                        )
                    )

            root_row.refreshed_at = now
            root_row.updated_at = now
            session.add(root_row)
            session.commit()

    return {"root_key": root_key, "thread_count": len(remote_threads)}


def cache_remote_codex_thread_detail(
    device_entry_id: str,
    payload: dict[str, Any],
    *,
    session: Session | None = None,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("远端 Codex thread detail 必须是对象")
    raw_thread = payload.get("thread")
    if not isinstance(raw_thread, dict):
        raise ValueError("远端 Codex thread detail 缺少 thread")

    now = time.time()
    with _CODEX_CACHE_LOCK:
        with _session_scope(session) as session:
            root_key, root_row = _ensure_remote_codex_cache_root(
                session,
                device_entry_id=device_entry_id,
                payload=payload,
                now=now,
            )
            summary = _normalize_remote_thread_summary(raw_thread)
            thread_row = session.exec(
                select(CodexTextCacheThread).where(
                    CodexTextCacheThread.root_key == root_key,
                    CodexTextCacheThread.thread_id == summary["id"],
                )
            ).first()
            if thread_row is None:
                thread_row = CodexTextCacheThread(
                    root_key=root_key,
                    thread_id=summary["id"],
                    created_at=now,
                )
            _assign_thread_summary_to_cache_row(thread_row, summary, now=now)
            messages = [
                {
                    "seq": int(message.get("seq") or index + 1),
                    "timestamp": message.get("timestamp"),
                    "role": str(message.get("role") or ""),
                    "phase": message.get("phase"),
                    "text": str(message.get("text") or ""),
                }
                for index, message in enumerate(payload.get("messages") or [])
                if isinstance(message, dict)
                and str(message.get("role") or "") in {"user", "assistant"}
                and str(message.get("text") or "").strip()
            ]
            _replace_thread_text_cache(
                session,
                root_key=root_key,
                thread_row=thread_row,
                rollout_size=None,
                rollout_mtime_ns=None,
                messages=messages,
                now=now,
            )
            root_row.refreshed_at = now
            root_row.updated_at = now
            session.add(root_row)
            session.commit()

    return {"root_key": root_key, "thread_id": summary["id"], "message_count": len(messages)}


def cache_remote_codex_workload(
    device_entry_id: str,
    payload: dict[str, Any],
    *,
    session: Session | None = None,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("远端 Codex workload 必须是对象")

    now = time.time()
    with _CODEX_CACHE_LOCK:
        with _session_scope(session) as session:
            root_key, root_row = _ensure_remote_codex_cache_root(
                session,
                device_entry_id=device_entry_id,
                payload=payload,
                now=now,
            )
            session.exec(delete(CodexTextCacheTurn).where(CodexTextCacheTurn.root_key == root_key))

            existing_threads = {
                row.thread_id: row
                for row in session.exec(
                    select(CodexTextCacheThread).where(CodexTextCacheThread.root_key == root_key)
                ).all()
            }
            for raw_turn in payload.get("turns") or []:
                if not isinstance(raw_turn, dict):
                    continue
                thread_id = str(raw_turn.get("thread_id") or "").strip()
                if not thread_id:
                    continue
                thread_row = existing_threads.get(thread_id)
                if thread_row is None:
                    summary = _normalize_remote_thread_summary(
                        {
                            "id": thread_id,
                            "title": raw_turn.get("thread_title"),
                            "preview": raw_turn.get("preview"),
                            "cwd": raw_turn.get("group_key"),
                            "project_label": raw_turn.get("project_label") or raw_turn.get("group_label"),
                            "project_secondary_label": raw_turn.get("project_secondary_label"),
                            "workspace_root": raw_turn.get("workspace_root"),
                            "created_at": raw_turn.get("start_at"),
                            "updated_at": raw_turn.get("end_at"),
                        }
                    )
                    thread_row = CodexTextCacheThread(root_key=root_key, thread_id=thread_id, created_at=now)
                    _assign_thread_summary_to_cache_row(thread_row, summary, now=now)
                    existing_threads[thread_id] = thread_row
                    session.add(thread_row)

                session.add(
                    CodexTextCacheTurn(
                        root_key=root_key,
                        thread_id=thread_id,
                        turn_index=int(raw_turn.get("turn_index") or 0),
                        user_seq=int(raw_turn.get("user_seq") or 0),
                        assistant_seq=raw_turn.get("assistant_seq"),
                        start_at=float(raw_turn.get("start_at") or 0),
                        end_at=float(raw_turn.get("end_at") or raw_turn.get("start_at") or 0),
                        duration_seconds=float(raw_turn.get("duration_seconds") or 0),
                        completed=bool(raw_turn.get("completed")),
                        preview=raw_turn.get("preview"),
                        created_at=now,
                        updated_at=now,
                    )
                )

            root_row.refreshed_at = now
            root_row.updated_at = now
            session.add(root_row)
            session.commit()

    return {"root_key": root_key, "turn_count": len(payload.get("turns") or [])}


def _ensure_codex_text_cache(
    root_dir: str | None = None,
    session: Session | None = None,
    *,
    refresh_rollouts: bool = True,
) -> dict[str, Any]:
    root_path, default_root_dir = _resolve_codex_root_dir(root_dir)
    root_key = _path_match_key(str(root_path)) or str(root_path)
    state_db_path = root_path / "state_5.sqlite"
    session_index_path = root_path / "session_index.jsonl"
    global_state_path = root_path / ".codex-global-state.json"
    state_db_size, state_db_mtime_ns = _file_signature(state_db_path)
    session_index_size, session_index_mtime_ns = _file_signature(session_index_path)
    global_state_size, global_state_mtime_ns = _file_signature(global_state_path)

    with _CODEX_CACHE_LOCK:
        with _session_scope(session) as session:
            root_row = session.get(CodexTextCacheRoot, root_key)
            metadata_needs_refresh = root_row is None or any(
                [
                    root_row.root_dir != str(root_path),
                    root_row.default_root_dir != str(default_root_dir),
                    root_row.state_db_path != str(state_db_path),
                    root_row.session_index_path != str(session_index_path),
                    root_row.global_state_path != str(global_state_path),
                    root_row.state_db_size != state_db_size,
                    root_row.state_db_mtime_ns != state_db_mtime_ns,
                    root_row.session_index_size != session_index_size,
                    root_row.session_index_mtime_ns != session_index_mtime_ns,
                    root_row.global_state_size != global_state_size,
                    root_row.global_state_mtime_ns != global_state_mtime_ns,
                ]
            )

            dirty_thread_ids: set[str] = set()
            if metadata_needs_refresh:
                workspace_roots = _load_workspace_roots(root_path)
                source_threads = _load_thread_rows(root_path)
                summarized_threads = [
                    _serialize_thread_summary(thread, _find_workspace_root(thread["cwd"], workspace_roots))
                    for thread in source_threads
                ]
                existing_rows = {
                    row.thread_id: row
                    for row in session.exec(
                        select(CodexTextCacheThread).where(CodexTextCacheThread.root_key == root_key)
                    ).all()
                }
                seen_thread_ids: set[str] = set()
                now = time.time()
                for summary in summarized_threads:
                    thread_row = existing_rows.get(summary["id"])
                    if thread_row is None:
                        thread_row = CodexTextCacheThread(
                            root_key=root_key,
                            thread_id=summary["id"],
                            created_at=now,
                        )
                        dirty_thread_ids.add(summary["id"])
                        _assign_thread_summary_to_cache_row(thread_row, summary, now=now)
                    elif _assign_thread_summary_to_cache_row(thread_row, summary, now=now):
                        dirty_thread_ids.add(summary["id"])
                    else:
                        thread_row.refreshed_at = now
                        thread_row.updated_at = now
                    session.add(thread_row)
                    seen_thread_ids.add(summary["id"])

                removed_thread_ids = set(existing_rows) - seen_thread_ids
                for removed_thread_id in removed_thread_ids:
                    session.exec(
                        delete(CodexTextCacheMessage).where(
                            CodexTextCacheMessage.root_key == root_key,
                            CodexTextCacheMessage.thread_id == removed_thread_id,
                        )
                    )
                    session.exec(
                        delete(CodexTextCacheTurn).where(
                            CodexTextCacheTurn.root_key == root_key,
                            CodexTextCacheTurn.thread_id == removed_thread_id,
                        )
                    )
                    session.exec(
                        delete(CodexTextCacheThread).where(
                            CodexTextCacheThread.root_key == root_key,
                            CodexTextCacheThread.thread_id == removed_thread_id,
                        )
                    )

                if root_row is None:
                    root_row = CodexTextCacheRoot(
                        root_key=root_key,
                        created_at=now,
                    )
                root_row.root_dir = str(root_path)
                root_row.default_root_dir = str(default_root_dir)
                root_row.state_db_path = str(state_db_path)
                root_row.session_index_path = str(session_index_path)
                root_row.global_state_path = str(global_state_path)
                root_row.workspace_roots = workspace_roots
                root_row.state_db_size = state_db_size
                root_row.state_db_mtime_ns = state_db_mtime_ns
                root_row.session_index_size = session_index_size
                root_row.session_index_mtime_ns = session_index_mtime_ns
                root_row.global_state_size = global_state_size
                root_row.global_state_mtime_ns = global_state_mtime_ns
                root_row.refreshed_at = now
                root_row.updated_at = now
                session.add(root_row)
                session.commit()

            rollout_refreshed = False
            now = time.time()
            if refresh_rollouts:
                thread_rows = session.exec(
                    select(CodexTextCacheThread).where(CodexTextCacheThread.root_key == root_key)
                ).all()
                for thread_row in thread_rows:
                    rollout_path_text = thread_row.rollout_path
                    rollout_size = None
                    rollout_mtime_ns = None
                    if rollout_path_text:
                        rollout_size, rollout_mtime_ns = _file_signature(Path(rollout_path_text))
                    needs_rollout_refresh = thread_row.thread_id in dirty_thread_ids or any(
                        [
                            thread_row.rollout_path is None,
                            thread_row.rollout_size != rollout_size,
                            thread_row.rollout_mtime_ns != rollout_mtime_ns,
                        ]
                    )
                    if not needs_rollout_refresh:
                        continue

                    if rollout_path_text and rollout_size is not None and rollout_mtime_ns is not None:
                        messages = _load_rollout_messages(Path(rollout_path_text))
                    else:
                        messages = []
                    _replace_thread_text_cache(
                        session,
                        root_key=root_key,
                        thread_row=thread_row,
                        rollout_size=rollout_size,
                        rollout_mtime_ns=rollout_mtime_ns,
                        messages=messages,
                        now=now,
                    )
                    rollout_refreshed = True

            if root_row is None:
                root_row = session.get(CodexTextCacheRoot, root_key)
            if root_row is not None and (metadata_needs_refresh or rollout_refreshed):
                root_row.refreshed_at = now
                root_row.updated_at = now
                session.add(root_row)
            session.commit()

    return {
        "root_key": root_key,
        "root_dir": str(root_path),
        "default_root_dir": str(default_root_dir),
        "state_db_path": str(state_db_path),
        "session_index_path": str(session_index_path),
        "global_state_path": str(global_state_path),
    }


def build_codex_overview(
    root_dir: str | None = None,
    session: Session | None = None,
    *,
    thread_offset: int = 0,
    thread_limit: int | None = None,
) -> dict[str, Any]:
    context = _ensure_codex_text_cache(root_dir, session=session, refresh_rollouts=False)
    with _session_scope(session) as session:
        thread_rows = session.exec(
            select(CodexTextCacheThread).where(CodexTextCacheThread.root_key == context["root_key"])
        ).all()
        threads = [_serialize_cached_thread_row(row) for row in thread_rows]
        page_threads, offset, limit, has_more = _paginate_codex_threads(
            threads,
            thread_offset=thread_offset,
            thread_limit=thread_limit,
        )
        all_groups = _build_groups_from_threads(threads)
        groups = _build_groups_from_threads(page_threads)

    return {
        "root_dir": context["root_dir"],
        "default_root_dir": context["default_root_dir"],
        "state_db_path": context["state_db_path"],
        "session_index_path": context["session_index_path"],
        "global_state_path": context["global_state_path"],
        "total_groups": len(all_groups),
        "total_threads": len(threads),
        "archived_threads": sum(1 for item in threads if item["archived"]),
        "groups": groups,
        "thread_offset": offset,
        "thread_limit": limit,
        "returned_threads": len(page_threads),
        "has_more": has_more,
    }


def _resolve_thread_record(root_dir: str | None, thread_id: str, session: Session | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    context = _ensure_codex_text_cache(root_dir, session=session)
    with _session_scope(session) as session:
        thread_row = session.exec(
            select(CodexTextCacheThread).where(
                CodexTextCacheThread.root_key == context["root_key"],
                CodexTextCacheThread.thread_id == thread_id,
            )
        ).first()
    if thread_row is None:
        raise KeyError(f"未找到 Codex 会话：{thread_id}")

    thread = _serialize_cached_thread_row(thread_row)
    return context, thread


def build_codex_thread_detail(root_dir: str | None, thread_id: str, session: Session | None = None) -> dict[str, Any]:
    context, thread = _resolve_thread_record(root_dir, thread_id, session=session)
    rollout_path_text = thread.get("rollout_path")
    if not rollout_path_text:
        raise FileNotFoundError(f"会话 {thread_id} 缺少 rollout_path")
    rollout_path = Path(rollout_path_text)
    if not rollout_path.exists():
        raise FileNotFoundError(f"未找到会话 JSONL：{rollout_path}")

    with _session_scope(session) as session:
        message_rows = session.exec(
            select(CodexTextCacheMessage).where(
                CodexTextCacheMessage.root_key == context["root_key"],
                CodexTextCacheMessage.thread_id == thread_id,
            ).order_by(CodexTextCacheMessage.seq)
        ).all()

    messages = [
        {
            "seq": row.seq,
            "timestamp": row.timestamp,
            "role": row.role,
            "phase": row.phase,
            "text": row.text,
        }
        for row in message_rows
    ]
    group = _build_group_stub(thread)

    return {
        "root_dir": context["root_dir"],
        "thread": {
            **thread,
            "group_key": group["key"],
            "group_label": group["label"],
            "group_secondary_label": group["secondary_label"],
        },
        "message_count": len(messages),
        "user_message_count": sum(1 for item in messages if item["role"] == "user"),
        "assistant_message_count": sum(1 for item in messages if item["role"] == "assistant"),
        "messages": messages,
    }


def build_codex_thread_message_images(
    root_dir: str | None,
    thread_id: str,
    message_seq: int,
    session: Session | None = None,
) -> dict[str, Any]:
    if message_seq <= 0:
        raise ValueError("message_seq 必须大于 0")

    context, thread = _resolve_thread_record(root_dir, thread_id, session=session)
    rollout_path_text = thread.get("rollout_path")
    if not rollout_path_text:
        raise FileNotFoundError(f"会话 {thread_id} 缺少 rollout_path")

    current_seq = 0
    for entry in _iter_rollout_message_entries(Path(rollout_path_text)):
        text = _extract_message_text(entry.get("content"))
        if not text:
            continue
        if entry["role"] == "user" and _should_skip_user_message(text):
            continue

        current_seq += 1
        if current_seq != message_seq:
            continue

        return {
            "root_dir": context["root_dir"],
            "thread_id": thread_id,
            "message_seq": message_seq,
            "images": _extract_message_images(entry.get("content")),
        }

    raise KeyError(f"未找到会话消息：{thread_id}#{message_seq}")


def build_codex_workload(
    root_dir: str | None = None,
    session: Session | None = None,
    *,
    start_at: float | None = None,
    end_at: float | None = None,
    compact: bool = False,
    include_segments: bool = True,
    historical_day_summary_before: float | None = None,
) -> dict[str, Any]:
    context = _ensure_codex_text_cache(root_dir, session=session)
    with _session_scope(session) as session:
        thread_rows = session.exec(
            select(CodexTextCacheThread).where(CodexTextCacheThread.root_key == context["root_key"])
        ).all()
        thread_map = {
            row.thread_id: _serialize_cached_thread_row(row)
            for row in thread_rows
        }
        turn_filters = [CodexTextCacheTurn.root_key == context["root_key"]]
        if start_at is not None:
            turn_filters.append(CodexTextCacheTurn.end_at > float(start_at))
        if end_at is not None:
            turn_filters.append(CodexTextCacheTurn.start_at < float(end_at))
        turn_rows = session.exec(
            select(CodexTextCacheTurn)
            .where(*turn_filters)
            .order_by(CodexTextCacheTurn.start_at, CodexTextCacheTurn.thread_id, CodexTextCacheTurn.turn_index)
        ).all()

    skipped_threads = 0
    for thread in thread_map.values():
        rollout_path_text = thread.get("rollout_path")
        if not rollout_path_text or not Path(rollout_path_text).exists():
            skipped_threads += 1

    turns: list[dict[str, Any]] = []
    day_seconds: dict[str, float] = {}
    summarized_turns = 0
    for row in turn_rows:
        thread = thread_map.get(row.thread_id)
        if thread is None:
            continue
        if historical_day_summary_before is not None and float(row.end_at) <= float(historical_day_summary_before):
            _aggregate_workload_turn_by_local_day(
                day_seconds,
                start_at=float(row.start_at),
                end_at=float(row.end_at),
            )
            summarized_turns += 1
            continue
        turn_payload = {
            "id": f"{row.thread_id}:{row.turn_index}",
            "start_at": float(row.start_at),
            "end_at": float(row.end_at),
            "duration_seconds": float(row.duration_seconds),
            "completed": bool(row.completed),
        }
        if not compact:
            group = _build_group_stub(thread)
            turn_payload.update(
                {
                    "thread_id": row.thread_id,
                    "turn_index": row.turn_index,
                    "thread_title": thread["title"],
                    "project_label": thread["project_label"],
                    "project_secondary_label": thread.get("project_secondary_label"),
                    "workspace_root": thread.get("workspace_root"),
                    "group_key": group["key"],
                    "group_label": group["label"],
                    "user_seq": row.user_seq,
                    "assistant_seq": row.assistant_seq,
                    "preview": row.preview,
                }
            )
        turns.append(turn_payload)

    segments = _build_workload_segments(turns) if include_segments else []
    time_range_start = turns[0]["start_at"] if turns else None
    time_range_end = max((float(item["end_at"]) for item in turns), default=None)
    max_concurrency = max((int(item["concurrency"]) for item in segments), default=0)

    is_filtered = start_at is not None or end_at is not None
    total_threads = len({row.thread_id for row in turn_rows}) if is_filtered else len(thread_map)

    return {
        "root_dir": context["root_dir"],
        "total_threads": total_threads,
        "total_turns": len(turn_rows),
        "returned_turns": len(turns),
        "summarized_turns": summarized_turns,
        "skipped_threads": skipped_threads,
        "max_concurrency": max_concurrency,
        "time_range_start": time_range_start,
        "time_range_end": time_range_end,
        "day_seconds": day_seconds,
        "turns": turns,
        "segments": segments,
    }


def _resolve_codex_daily_summary_model(model: str | None = None) -> str:
    return (model or "").strip() or _CODEX_DAILY_SUMMARY_DEFAULT_MODEL


def _resolve_codex_daily_summary_runtime_config(
    session: Session | None,
    user_id: int | None,
) -> dict[str, Any]:
    runtime: dict[str, Any] = {
        "provider_id": _CODEX_DAILY_SUMMARY_PROVIDER_ID,
        "base_url": None,
        "api_key": None,
        "model": _CODEX_DAILY_SUMMARY_DEFAULT_MODEL,
        "extra_providers": (),
    }
    if session is None or user_id is None:
        return runtime

    try:
        app_runtime = resolve_ai_app_runtime_config(
            session=session,
            current_user=session.get(User, int(user_id)),
            app_id=AI_APP_CODEX_DAILY_SUMMARY,
        )
        runtime["provider_id"] = str(app_runtime.get("provider") or _CODEX_DAILY_SUMMARY_PROVIDER_ID)
        runtime["base_url"] = app_runtime.get("base_url")
        runtime["api_key"] = app_runtime.get("api_key")
        runtime["model"] = str(app_runtime.get("model") or _CODEX_DAILY_SUMMARY_DEFAULT_MODEL)
        runtime["extra_providers"] = tuple(app_runtime.get("extra_providers") or ())
        return runtime
    except AiAppConfigError as exc:
        raise ValueError(str(exc)) from exc


def _truncate_text(text: str, limit: int) -> str:
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    if limit <= 3:
        return text[:limit]
    return f"{text[:limit - 3].rstrip()}..."


def _clean_daily_summary_text(text: str | None, *, limit: int | None = None) -> str:
    normalized_text = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized_text:
        return ""

    lines = normalized_text.split("\n")
    request_heading_pattern = re.compile(r"^#+\s*My request for Codex:\s*(.*)$", re.IGNORECASE)
    request_line_index = next(
        (index for index, line in enumerate(lines) if request_heading_pattern.match(line.strip())),
        -1,
    )
    preferred_text = normalized_text
    if request_line_index >= 0:
        request_match = request_heading_pattern.match(lines[request_line_index].strip())
        inline_text = request_match.group(1).strip() if request_match else ""
        remaining_text = "\n".join(lines[request_line_index + 1 :]).strip()
        preferred_text = "\n".join(part for part in (inline_text, remaining_text) if part).strip() or normalized_text

    cleaned_text = re.sub(r"<image>[\s\S]*?</image>", " ", preferred_text, flags=re.IGNORECASE)
    cleaned_text = re.sub(r"</?image>", " ", cleaned_text, flags=re.IGNORECASE)
    cleaned_text = re.sub(r"\s+", " ", cleaned_text).strip()
    if limit is not None:
        cleaned_text = _truncate_text(cleaned_text, limit)
    return cleaned_text


def _is_ignored_codex_daily_summary_thread(thread: dict[str, Any]) -> bool:
    searchable_text = "\n".join(
        str(thread.get(field) or "")
        for field in (
            "title",
            "preview",
            "project_label",
            "project_secondary_label",
            "cwd",
            "original_cwd",
            "rollout_path",
        )
    )
    return any(pattern.search(searchable_text) for pattern in _CODEX_DAILY_SUMMARY_IGNORED_THREAD_PATTERNS)


def _format_codex_project_label(project_label: str, secondary_label: str | None = None) -> str:
    return " · ".join(part for part in (project_label, secondary_label) if part)


def _format_chinese_calendar_date(target_date: calendar_date) -> str:
    return f"{target_date.year}年{target_date.month}月{target_date.day}日"


def _resolve_codex_daily_summary_range(target_date_text: str) -> tuple[str, calendar_date, ZoneInfo, float, float]:
    normalized_date_text = str(target_date_text or "").strip()
    try:
        target_date = datetime.strptime(normalized_date_text, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError("date 必须是 YYYY-MM-DD 格式") from exc

    timezone = ZoneInfo(_CODEX_DAILY_SUMMARY_TIMEZONE)
    start_datetime = datetime(target_date.year, target_date.month, target_date.day, tzinfo=timezone)
    end_datetime = start_datetime + timedelta(days=1)
    return normalized_date_text, target_date, timezone, start_datetime.timestamp(), end_datetime.timestamp()


def _format_local_summary_datetime(timestamp: float | int | None, timezone: ZoneInfo) -> str:
    if timestamp is None:
        return "未记录"
    return datetime.fromtimestamp(float(timestamp), tz=timezone).strftime("%Y-%m-%d %H:%M")


def _normalize_codex_daily_summary_type_item(value: Any, fallback_order: int = 0) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None

    raw_key = value.get("key")
    if not isinstance(raw_key, str) or not raw_key.strip():
        return None

    key = raw_key.strip()
    if key == "note":
        key = "general"
    elif key in {"doc", "memo"}:
        return None

    builtin_entry = next((item for item in NOTE_TYPE_BUILTIN_PALETTE if item["key"] == key), None)
    label = str(value.get("label") or "").strip()
    if not label:
        label = str(builtin_entry["label"] if builtin_entry else key)
    elif key == "general" and label == "笔记":
        label = "综合"

    try:
        order = int(value.get("order", fallback_order))
    except (TypeError, ValueError):
        order = fallback_order

    color = str(value.get("color") or (builtin_entry["color"] if builtin_entry else "")).strip() or None
    return {
        "key": key,
        "label": label,
        "color": color,
        "order": order,
        "builtin": bool(value.get("builtin")) or key in NOTE_CATEGORY_BUILTIN_KEYS,
    }


def _load_codex_daily_summary_type_palette(user_id: int | None, session: Session) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if user_id is not None:
        for setting_key in (
            build_note_category_palette_setting_key(user_id),
            build_note_type_palette_setting_key(user_id),
        ):
            row = session.get(AppSetting, setting_key)
            raw_items = row.value.get("items") if row and isinstance(row.value, dict) else None
            if not isinstance(raw_items, list):
                continue

            seen: set[str] = set()
            normalized: list[dict[str, Any]] = []
            for index, raw_item in enumerate(raw_items):
                normalized_item = _normalize_codex_daily_summary_type_item(raw_item, fallback_order=index * 10)
                if not normalized_item:
                    continue
                key = str(normalized_item["key"])
                if key in seen:
                    continue
                seen.add(key)
                normalized.append(normalized_item)
            if normalized:
                items = normalized
                break

    if not items:
        items = [
            {
                "key": item["key"],
                "label": item["label"],
                "color": item["color"],
                "order": int(item["order"]),
                "builtin": True,
            }
            for item in NOTE_TYPE_BUILTIN_PALETTE
        ]

    filtered = [
        item
        for item in items
        if not is_note_auto_classification_blocked_category(item.get("key"), item.get("label"))
    ]
    if not filtered:
        filtered = [
            {
                "key": item["key"],
                "label": item["label"],
                "color": item["color"],
                "order": int(item["order"]),
                "builtin": True,
            }
            for item in NOTE_TYPE_BUILTIN_PALETTE
            if item["key"] == NOTE_CATEGORY_DEFAULT
        ]

    return sorted(filtered, key=lambda item: (int(item.get("order", 0)), str(item.get("label") or ""), str(item.get("key") or "")))


def _build_codex_daily_summary_system_prompt(
    target_date: calendar_date,
    type_items: list[dict[str, Any]],
) -> str:
    chinese_date = _format_chinese_calendar_date(target_date)
    blocked_labels = "、".join(("任务", "重点", "项目", "模块"))
    type_labels = " / ".join(str(item["label"]) for item in type_items) or "综合 / 缺陷"
    return "\n".join(
        [
            "你在整理同一位用户的 Codex 工作日报。",
            "你会收到某一天的真实聊天记录提要。",
            "只允许依据输入材料总结，不要补充未出现的信息。",
            f"优先参考这些工作类型来组织一级分类：{type_labels}。",
            f"一级分类不得使用这些名称：{blocked_labels}。",
            "输出要求：只输出中文层次编号列表，不要加标题、代码块、Markdown 解释或客套话。",
            "一级编号使用“1. 2. 3.”，二级编号使用“1.1 1.2 1.3”。",
            "一级条目只保留当天确实涉及的类别，空类别不要输出。",
            "二级条目要写清楚做了什么、达成了什么结果，必要时补一句后续方向，避免空泛表述。",
            f"如果需要在文中点名日期，统一写“{chinese_date}”。",
        ]
    )


def _build_codex_daily_summary_prompt(
    *,
    target_date: calendar_date,
    timezone: ZoneInfo,
    thread_count: int,
    turn_count: int,
    user_message_count: int,
    assistant_message_count: int,
    type_items: list[dict[str, Any]],
    turn_records: list[dict[str, Any]],
) -> str:
    lines = [
        f"日期：{target_date.isoformat()}",
        f"时区：{timezone.key}",
        f"会话数：{thread_count}",
        f"对话轮次：{turn_count}",
        f"用户消息：{user_message_count}",
        f"助手消息：{assistant_message_count}",
        "",
        "优先参考的工作类型预设：",
    ]

    for index, item in enumerate(type_items, start=1):
        lines.append(f"{index}. {item['label']}（key={item['key']}）")

    lines.extend(["", "按时间排序的真实工作记录："])
    for index, turn in enumerate(turn_records, start=1):
        record_lines = [f"{index}. 时间：{turn['time_range']}"]
        if turn.get("source_device_name"):
            record_lines.append(f"设备：{turn['source_device_name']}")
        record_lines.extend(
            [
                f"项目：{turn['project_label']}",
                f"会话：{turn['thread_title']}",
                f"用户诉求：{turn['user_request']}",
                f"结果：{turn['assistant_result'] or '当轮还没有明确结果'}",
            ]
        )
        lines.extend(record_lines)
        if turn["assistant_process"]:
            lines.append(f"过程：{turn['assistant_process']}")
        lines.append("")

    lines.extend(
        [
            "请根据这些真实记录，输出一份中文层次编号日报。",
            "请优先按上面的工作类型做一级归类，如果某类没有内容就省略。",
            "如果某些事项跨越多类，优先放到最贴近的一类；无法自然归类的公共推进可放在“综合”。",
        ]
    )
    return "\n".join(lines).strip()


def _collect_codex_daily_summary_source(
    root_dir: str | None,
    target_date_text: str,
    *,
    user_id: int | None = None,
    session: Session | None = None,
) -> dict[str, Any]:
    context = _ensure_codex_text_cache(root_dir, session=session)
    return _collect_codex_daily_summary_source_from_context(
        context,
        target_date_text,
        user_id=user_id,
        session=session,
    )


def collect_codex_daily_summary_source(
    root_dir: str | None,
    target_date_text: str,
    *,
    user_id: int | None = None,
    session: Session | None = None,
) -> dict[str, Any]:
    return _collect_codex_daily_summary_source(
        root_dir,
        target_date_text,
        user_id=user_id,
        session=session,
    )


def _collect_codex_daily_summary_source_from_context(
    context: dict[str, Any],
    target_date_text: str,
    *,
    user_id: int | None = None,
    session: Session | None = None,
) -> dict[str, Any]:
    normalized_date_text, target_date, timezone, day_start_at, day_end_at = _resolve_codex_daily_summary_range(
        target_date_text
    )

    with _session_scope(session) as session:
        type_items = _load_codex_daily_summary_type_palette(user_id, session)
        turn_rows = session.exec(
            select(CodexTextCacheTurn)
            .where(
                CodexTextCacheTurn.root_key == context["root_key"],
                CodexTextCacheTurn.end_at > day_start_at,
                CodexTextCacheTurn.start_at < day_end_at,
            )
            .order_by(CodexTextCacheTurn.start_at, CodexTextCacheTurn.thread_id, CodexTextCacheTurn.turn_index)
        ).all()

        if not turn_rows:
            return {
                "context": context,
                "date": normalized_date_text,
                "target_date": target_date,
                "timezone": timezone,
                "type_items": type_items,
                "turn_records": [],
                "threads": [],
                "thread_count": 0,
                "turn_count": 0,
                "user_message_count": 0,
                "assistant_message_count": 0,
            }

        thread_ids = sorted({row.thread_id for row in turn_rows})
        thread_rows = session.exec(
            select(CodexTextCacheThread).where(
                CodexTextCacheThread.root_key == context["root_key"],
                CodexTextCacheThread.thread_id.in_(thread_ids),
            )
        ).all()
        thread_map = {row.thread_id: row for row in thread_rows}
        ignored_thread_ids = {
            thread_id
            for thread_id, thread_row in thread_map.items()
            if _is_ignored_codex_daily_summary_thread(_serialize_cached_thread_row(thread_row))
        }
        if ignored_thread_ids:
            turn_rows = [row for row in turn_rows if row.thread_id not in ignored_thread_ids]
            thread_map = {
                thread_id: thread_row
                for thread_id, thread_row in thread_map.items()
                if thread_id not in ignored_thread_ids
            }
            thread_ids = sorted({row.thread_id for row in turn_rows})

        if not turn_rows:
            return {
                "context": context,
                "date": normalized_date_text,
                "target_date": target_date,
                "timezone": timezone,
                "type_items": type_items,
                "turn_records": [],
                "threads": [],
                "thread_count": 0,
                "turn_count": 0,
                "user_message_count": 0,
                "assistant_message_count": 0,
            }

        message_rows = session.exec(
            select(CodexTextCacheMessage)
            .where(
                CodexTextCacheMessage.root_key == context["root_key"],
                CodexTextCacheMessage.thread_id.in_(thread_ids),
            )
            .order_by(CodexTextCacheMessage.thread_id, CodexTextCacheMessage.seq)
        ).all()
        messages_by_thread: dict[str, list[CodexTextCacheMessage]] = defaultdict(list)
        for row in message_rows:
            messages_by_thread[row.thread_id].append(row)

    turn_records: list[dict[str, Any]] = []
    thread_aggregates: dict[str, dict[str, Any]] = {}
    user_message_count = 0
    assistant_message_count = 0

    for turn_row in turn_rows:
        thread_row = thread_map.get(turn_row.thread_id)
        if thread_row is None:
            continue

        thread = _serialize_cached_thread_row(thread_row)
        thread_messages = messages_by_thread.get(turn_row.thread_id, [])
        user_message = next((row for row in thread_messages if row.seq == turn_row.user_seq), None)
        assistant_messages = (
            [
                row
                for row in thread_messages
                if row.seq > turn_row.user_seq and row.seq <= int(turn_row.assistant_seq)
            ]
            if turn_row.assistant_seq is not None
            else []
        )

        user_request = _clean_daily_summary_text(
            user_message.text if user_message is not None else turn_row.preview,
            limit=_CODEX_DAILY_SUMMARY_USER_TEXT_LIMIT,
        )
        thread_title = _clean_daily_summary_text(thread["title"], limit=96) or user_request or thread["project_label"]
        final_assistant_message = next(
            (row for row in reversed(assistant_messages) if row.phase == "final_answer"),
            assistant_messages[-1] if assistant_messages else None,
        )
        assistant_result = _clean_daily_summary_text(
            final_assistant_message.text if final_assistant_message is not None else "",
            limit=_CODEX_DAILY_SUMMARY_ASSISTANT_TEXT_LIMIT,
        )
        assistant_process_parts = [
            _clean_daily_summary_text(row.text)
            for row in assistant_messages
            if row.phase != "final_answer" and row.text.strip()
        ]
        assistant_process = _truncate_text(
            "；".join(dict.fromkeys(part for part in assistant_process_parts if part)),
            _CODEX_DAILY_SUMMARY_PROCESS_TEXT_LIMIT,
        )
        if assistant_process == assistant_result:
            assistant_process = ""

        assistant_message_count += len(assistant_messages)
        if user_request:
            user_message_count += 1

        project_label = _format_codex_project_label(
            thread["project_label"],
            thread.get("project_secondary_label"),
        )
        turn_records.append(
            {
                "thread_id": thread["id"],
                "thread_title": thread_title,
                "project_label": project_label,
                "time_range": (
                    f"{_format_local_summary_datetime(turn_row.start_at, timezone)}"
                    f" ~ {_format_local_summary_datetime(turn_row.end_at, timezone)}"
                ),
                "user_request": user_request or "未记录",
                "assistant_result": assistant_result,
                "assistant_process": assistant_process,
                "start_at": float(turn_row.start_at),
                "end_at": float(turn_row.end_at),
            }
        )

        aggregate = thread_aggregates.get(turn_row.thread_id)
        if aggregate is None:
            aggregate = {
                "thread_id": thread["id"],
                "title": thread_title,
                "project_label": thread["project_label"],
                "project_secondary_label": thread.get("project_secondary_label"),
                "workspace_root": thread.get("workspace_root"),
                "start_at": float(turn_row.start_at),
                "end_at": float(turn_row.end_at),
                "turn_count": 0,
                "user_message_count": 0,
                "assistant_message_count": 0,
                "preview": user_request or thread.get("preview"),
            }
            thread_aggregates[turn_row.thread_id] = aggregate

        aggregate["start_at"] = min(float(aggregate["start_at"]), float(turn_row.start_at))
        aggregate["end_at"] = max(float(aggregate["end_at"]), float(turn_row.end_at))
        aggregate["turn_count"] += 1
        aggregate["user_message_count"] += 1 if user_request else 0
        aggregate["assistant_message_count"] += len(assistant_messages)
        if not aggregate.get("preview") and user_request:
            aggregate["preview"] = user_request

    threads = sorted(
        thread_aggregates.values(),
        key=lambda item: (
            float(item["start_at"]),
            float(item["end_at"]),
            str(item["thread_id"]),
        ),
    )
    return {
        "context": context,
        "date": normalized_date_text,
        "target_date": target_date,
        "timezone": timezone,
        "type_items": type_items,
        "turn_records": turn_records,
        "threads": threads,
        "thread_count": len(threads),
        "turn_count": len(turn_records),
        "user_message_count": user_message_count,
        "assistant_message_count": assistant_message_count,
    }


def collect_cached_codex_daily_summary_source(
    root_key: str,
    target_date_text: str,
    *,
    user_id: int | None = None,
    session: Session | None = None,
) -> dict[str, Any]:
    with _session_scope(session) as active_session:
        root_row = active_session.get(CodexTextCacheRoot, root_key)
        if root_row is None:
            raise KeyError(f"未找到 Codex 缓存根：{root_key}")
        context = {
            "root_key": root_row.root_key,
            "root_dir": root_row.root_dir,
            "default_root_dir": root_row.default_root_dir,
            "state_db_path": root_row.state_db_path,
            "session_index_path": root_row.session_index_path,
            "global_state_path": root_row.global_state_path,
        }
        return _collect_codex_daily_summary_source_from_context(
            context,
            target_date_text,
            user_id=user_id,
            session=active_session,
        )


def annotate_codex_daily_summary_source(
    source: dict[str, Any],
    *,
    source_entry_id: str,
    source_device_name: str,
    source_root_dir: str | None = None,
) -> dict[str, Any]:
    annotated = {
        **source,
        "turn_records": [],
        "threads": [],
    }
    for turn in source.get("turn_records") or []:
        annotated["turn_records"].append(
            {
                **turn,
                "thread_id": f"{source_entry_id}:{turn.get('thread_id')}",
                "source_entry_id": source_entry_id,
                "source_device_name": source_device_name,
                "source_root_dir": source_root_dir or source.get("context", {}).get("root_dir"),
            }
        )
    for thread in source.get("threads") or []:
        annotated["threads"].append(
            {
                **thread,
                "thread_id": f"{source_entry_id}:{thread.get('thread_id')}",
                "source_entry_id": source_entry_id,
                "source_device_name": source_device_name,
                "source_root_dir": source_root_dir or source.get("context", {}).get("root_dir"),
            }
        )
    return annotated


def merge_codex_daily_summary_sources(
    sources: list[dict[str, Any]],
    *,
    root_key: str,
    root_dir: str,
    target_date_text: str,
    user_id: int | None = None,
    session: Session | None = None,
) -> dict[str, Any]:
    normalized_date_text, target_date, timezone, _, _ = _resolve_codex_daily_summary_range(target_date_text)
    with _session_scope(session) as active_session:
        type_items = _load_codex_daily_summary_type_palette(user_id, active_session)

    turn_records = sorted(
        [
            turn
            for source in sources
            for turn in (source.get("turn_records") or [])
        ],
        key=lambda item: (
            float(item.get("start_at") or 0),
            float(item.get("end_at") or 0),
            str(item.get("source_entry_id") or ""),
            str(item.get("thread_id") or ""),
        ),
    )
    threads = sorted(
        [
            thread
            for source in sources
            for thread in (source.get("threads") or [])
        ],
        key=lambda item: (
            float(item.get("start_at") or 0),
            float(item.get("end_at") or 0),
            str(item.get("source_entry_id") or ""),
            str(item.get("thread_id") or ""),
        ),
    )

    return {
        "context": {
            "root_key": root_key,
            "root_dir": root_dir,
            "default_root_dir": "",
            "state_db_path": "",
            "session_index_path": "",
            "global_state_path": "",
        },
        "date": normalized_date_text,
        "target_date": target_date,
        "timezone": timezone,
        "type_items": type_items,
        "turn_records": turn_records,
        "threads": threads,
        "thread_count": len(threads),
        "turn_count": len(turn_records),
        "user_message_count": sum(int(source.get("user_message_count") or 0) for source in sources),
        "assistant_message_count": sum(int(source.get("assistant_message_count") or 0) for source in sources),
    }


def _build_codex_daily_summary_result_from_source(
    source: dict[str, Any],
    *,
    model: str | None = None,
    before_codex_call: Any = None,
    runtime_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    context = dict(source["context"])
    timezone = source["timezone"]
    result_base = {
        "root_dir": context["root_dir"],
        "date": source["date"],
        "timezone": timezone.key,
        "prompt_version": _CODEX_DAILY_SUMMARY_PROMPT_VERSION,
        "thread_count": int(source["thread_count"]),
        "turn_count": int(source["turn_count"]),
        "user_message_count": int(source["user_message_count"]),
        "assistant_message_count": int(source["assistant_message_count"]),
        "threads": list(source["threads"]),
        "type_items": list(source["type_items"]),
    }
    if not source["turn_records"]:
        return {
            **result_base,
            "generated_at": None,
            "generated_by": "empty",
            "model": None,
            "summary_text": "",
        }

    prompt = _build_codex_daily_summary_prompt(
        target_date=source["target_date"],
        timezone=timezone,
        thread_count=int(source["thread_count"]),
        turn_count=int(source["turn_count"]),
        user_message_count=int(source["user_message_count"]),
        assistant_message_count=int(source["assistant_message_count"]),
        type_items=list(source["type_items"]),
        turn_records=list(source["turn_records"]),
    )
    resolved_model = _resolve_codex_daily_summary_model(model or str(runtime_config.get("model") or ""))
    runtime_config = runtime_config or _resolve_codex_daily_summary_runtime_config(None, None)
    if callable(before_codex_call):
        before_codex_call()
    response = chat_with_provider(
        provider_id=str(runtime_config.get("provider_id") or _CODEX_DAILY_SUMMARY_PROVIDER_ID),
        base_url=runtime_config.get("base_url"),
        api_key=runtime_config.get("api_key"),
        model=resolved_model,
        system_prompt=_build_codex_daily_summary_system_prompt(source["target_date"], list(source["type_items"])),
        messages=[{"role": "user", "content": prompt}],
        timeout_seconds=_CODEX_DAILY_SUMMARY_TIMEOUT_SECONDS,
        extra_providers=tuple(runtime_config.get("extra_providers") or ()),
    )
    summary_text = str(response.get("content") or "").strip()
    if not summary_text:
        raise ValueError("DeepSeek 没有返回有效的日报总结")

    return {
        **result_base,
        "generated_at": response.get("created_at"),
        "generated_by": _CODEX_DAILY_SUMMARY_GENERATED_BY,
        "model": response.get("model") or resolved_model,
        "summary_text": summary_text,
    }


def build_codex_daily_summary_result_from_source(
    source: dict[str, Any],
    *,
    model: str | None = None,
    before_codex_call: Any = None,
    runtime_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _build_codex_daily_summary_result_from_source(
        source,
        model=model,
        before_codex_call=before_codex_call,
        runtime_config=runtime_config,
    )


def build_codex_daily_summary(
    root_dir: str | None,
    target_date_text: str,
    *,
    model: str | None = None,
    user_id: int | None = None,
    session: Session | None = None,
) -> dict[str, Any]:
    with _session_scope(session) as active_session:
        source = _collect_codex_daily_summary_source(
            root_dir,
            target_date_text,
            user_id=user_id,
            session=active_session,
        )
        runtime_config = _resolve_codex_daily_summary_runtime_config(active_session, user_id)
    return _build_codex_daily_summary_result_from_source(
        source,
        model=model,
        runtime_config=runtime_config,
    )


def resolve_codex_daily_summary_epoch_range(target_date_text: str) -> tuple[str, float, float]:
    normalized_date_text, _, _, day_start_at, day_end_at = _resolve_codex_daily_summary_range(target_date_text)
    return normalized_date_text, day_start_at, day_end_at


def _resolve_codex_daily_summary_root_identity(
    root_dir: str | None,
    *,
    require_existing: bool = False,
) -> dict[str, str]:
    requested = _clean_path_text(root_dir)
    default_root_dir = _default_codex_root_dir().resolve(strict=False)
    root_path = Path(requested).expanduser() if requested else default_root_dir
    root_path = root_path.resolve(strict=False)
    if require_existing:
        if not root_path.exists():
            raise FileNotFoundError(f"Codex 根目录不存在：{root_path}")
        if not root_path.is_dir():
            raise NotADirectoryError(f"Codex 根目录不是目录：{root_path}")
    root_dir_text = str(root_path)
    return {
        "root_key": _path_match_key(root_dir_text) or root_dir_text,
        "root_dir": root_dir_text,
        "default_root_dir": str(default_root_dir),
    }


def _serialize_codex_daily_summary_run(
    run: CodexDailySummaryRun,
    *,
    reused_existing_run: bool = False,
) -> dict[str, Any]:
    result_payload = dict(run.result_json or {})
    return {
        "id": run.id,
        "root_dir": run.root_dir,
        "date": run.summary_date,
        "timezone": run.timezone,
        "provider": run.provider,
        "generated_by": run.generated_by,
        "model": run.model or None,
        "prompt_version": run.prompt_version,
        "force_requested": bool(run.force_requested),
        "reused_existing_run": reused_existing_run,
        "status": run.status,
        "stage": run.stage,
        "stage_label": run.stage_label,
        "thread_count": int(run.thread_count or 0),
        "turn_count": int(run.turn_count or 0),
        "user_message_count": int(run.user_message_count or 0),
        "assistant_message_count": int(run.assistant_message_count or 0),
        "summary_text": run.summary_text,
        "error_message": run.error_message,
        "heartbeat_at": run.heartbeat_at,
        "result": result_payload or None,
        "created_at": run.created_at,
        "finished_at": run.finished_at,
        "updated_at": run.updated_at,
    }


def serialize_codex_daily_summary_run(
    run: CodexDailySummaryRun,
    *,
    reused_existing_run: bool = False,
) -> dict[str, Any]:
    return _serialize_codex_daily_summary_run(run, reused_existing_run=reused_existing_run)


def _get_codex_daily_summary_latest_row(
    session: Session,
    *,
    scope_key: str,
    root_key: str,
    summary_date: str,
) -> CodexDailySummaryRun | None:
    return session.exec(
        select(CodexDailySummaryRun)
        .where(
            CodexDailySummaryRun.scope_key == scope_key,
            CodexDailySummaryRun.root_key == root_key,
            CodexDailySummaryRun.summary_date == summary_date,
        )
        .order_by(CodexDailySummaryRun.created_at.desc(), CodexDailySummaryRun.id.desc())
    ).first()


def _get_codex_daily_summary_run_row(
    session: Session,
    *,
    scope_key: str,
    run_id: str,
) -> CodexDailySummaryRun:
    run = session.get(CodexDailySummaryRun, run_id)
    if run is None or str(run.scope_key or "") != str(scope_key):
        raise KeyError(f"未找到 Codex 日报任务：{run_id}")
    return run


def _run_codex_daily_summary_worker(
    *,
    db_engine: Any,
    run_id: str,
    scope_key: str,
    root_dir: str,
    target_date_text: str,
    user_id: int | None,
    model: str | None,
    source_loader: Any = None,
) -> None:
    heartbeat_stop = threading.Event()
    heartbeat_thread: threading.Thread | None = None

    def mutate_run(mutator: Any) -> None:
        with Session(db_engine) as session:
            run = session.get(CodexDailySummaryRun, run_id)
            if run is None or str(run.scope_key or "") != str(scope_key):
                return
            mutator(run)
            session.add(run)
            session.commit()

    def apply_stage(stage: str, stage_label: str, *, source: dict[str, Any] | None = None) -> None:
        now = time.time()

        def _mutate(run: CodexDailySummaryRun) -> None:
            run.status = "running"
            run.stage = stage
            run.stage_label = stage_label
            run.updated_at = now
            run.heartbeat_at = now
            if source is not None:
                run.root_dir = str(source["context"]["root_dir"])
                run.timezone = str(source["timezone"].key)
                run.thread_count = int(source["thread_count"])
                run.turn_count = int(source["turn_count"])
                run.user_message_count = int(source["user_message_count"])
                run.assistant_message_count = int(source["assistant_message_count"])
            if model:
                run.model = model

        mutate_run(_mutate)

    def start_heartbeat() -> None:
        nonlocal heartbeat_thread
        if heartbeat_thread is not None and heartbeat_thread.is_alive():
            return

        def _heartbeat_loop() -> None:
            while not heartbeat_stop.wait(_CODEX_DAILY_SUMMARY_HEARTBEAT_INTERVAL_SECONDS):
                now = time.time()

                def _mutate(run: CodexDailySummaryRun) -> None:
                    if run.status != "running":
                        return
                    run.heartbeat_at = now
                    run.updated_at = now

                mutate_run(_mutate)

        heartbeat_thread = threading.Thread(target=_heartbeat_loop, daemon=True)
        heartbeat_thread.start()

    def stop_heartbeat() -> None:
        heartbeat_stop.set()
        if heartbeat_thread is not None:
            heartbeat_thread.join(timeout=1.0)

    try:
        apply_stage("loading_cache", "读取 Codex 会话缓存")
        with Session(db_engine) as session:
            if callable(source_loader):
                source = source_loader(session)
            else:
                source = _collect_codex_daily_summary_source(
                    root_dir,
                    target_date_text,
                    user_id=user_id,
                    session=session,
                )
            runtime_config = _resolve_codex_daily_summary_runtime_config(session, user_id)
        apply_stage("building_prompt", "整理类型归类与层次提纲", source=source)

        if not source["turn_records"]:
            result = _build_codex_daily_summary_result_from_source(
                source,
                model=model,
                runtime_config=runtime_config,
            )
            completed_at = time.time()

            def _mutate_empty(run: CodexDailySummaryRun) -> None:
                run.root_dir = str(result["root_dir"])
                run.summary_date = str(result["date"])
                run.timezone = str(result["timezone"])
                run.generated_by = str(result["generated_by"])
                run.model = str(result.get("model") or "")
                run.prompt_version = str(result["prompt_version"])
                run.status = "completed"
                run.stage = "completed"
                run.stage_label = "已完成"
                run.thread_count = int(result["thread_count"])
                run.turn_count = int(result["turn_count"])
                run.user_message_count = int(result["user_message_count"])
                run.assistant_message_count = int(result["assistant_message_count"])
                run.summary_text = str(result["summary_text"])
                run.error_message = None
                run.result_json = dict(result)
                run.heartbeat_at = completed_at
                run.finished_at = completed_at
                run.updated_at = completed_at

            mutate_run(_mutate_empty)
            return

        def _before_codex_call() -> None:
            apply_stage("running_deepseek", "调用 DeepSeek 生成日报", source=source)
            start_heartbeat()

        result = _build_codex_daily_summary_result_from_source(
            source,
            model=model,
            before_codex_call=_before_codex_call,
            runtime_config=runtime_config,
        )
        stop_heartbeat()
        completed_at = time.time()

        def _mutate_completed(run: CodexDailySummaryRun) -> None:
            run.root_dir = str(result["root_dir"])
            run.summary_date = str(result["date"])
            run.timezone = str(result["timezone"])
            run.generated_by = str(result["generated_by"])
            run.model = str(result.get("model") or "")
            run.prompt_version = str(result["prompt_version"])
            run.status = "completed"
            run.stage = "completed"
            run.stage_label = "已完成"
            run.thread_count = int(result["thread_count"])
            run.turn_count = int(result["turn_count"])
            run.user_message_count = int(result["user_message_count"])
            run.assistant_message_count = int(result["assistant_message_count"])
            run.summary_text = str(result["summary_text"])
            run.error_message = None
            run.result_json = dict(result)
            run.heartbeat_at = completed_at
            run.finished_at = completed_at
            run.updated_at = completed_at

        mutate_run(_mutate_completed)
    except Exception as exc:
        stop_heartbeat()
        failed_at = time.time()
        error_message = str(getattr(exc, "detail", None) or exc)
        failed_stage = ""

        def _mutate_failed(run: CodexDailySummaryRun) -> None:
            nonlocal failed_stage
            failed_stage = run.stage
            run.status = "failed"
            run.stage = "failed"
            run.stage_label = "生成失败"
            run.error_message = error_message
            run.heartbeat_at = failed_at
            run.finished_at = failed_at
            run.updated_at = failed_at

        mutate_run(_mutate_failed)
        try:
            from backend.core.notes.metadata_feedback import record_codex_maintenance_feedback

            with Session(db_engine) as session:
                failed_run = session.get(CodexDailySummaryRun, run_id)
                if failed_run is not None:
                    record_codex_maintenance_feedback(
                        session,
                        source_kind="codex_daily_summary",
                        source_ref_id=failed_run.id,
                        user_id=failed_run.user_id,
                        source_date=failed_run.summary_date or target_date_text,
                        stage=failed_stage or failed_run.stage,
                        error_message=error_message,
                        context={
                            "scope_key": failed_run.scope_key,
                            "root_key": failed_run.root_key,
                            "root_dir": failed_run.root_dir or root_dir,
                            "provider": failed_run.provider,
                            "generated_by": failed_run.generated_by,
                            "model": failed_run.model or model or "",
                            "prompt_version": failed_run.prompt_version,
                            "thread_count": failed_run.thread_count,
                            "turn_count": failed_run.turn_count,
                            "force_requested": failed_run.force_requested,
                        },
                    )
                    session.commit()
        except Exception:
            pass


def get_codex_daily_summary_latest_run(
    scope_key: str,
    root_dir: str | None,
    target_date_text: str,
    *,
    session: Session | None = None,
) -> dict[str, Any]:
    root_identity = _resolve_codex_daily_summary_root_identity(root_dir)
    normalized_date_text, _, timezone, _, _ = _resolve_codex_daily_summary_range(target_date_text)
    with _session_scope(session) as session:
        run = _get_codex_daily_summary_latest_row(
            session,
            scope_key=scope_key,
            root_key=root_identity["root_key"],
            summary_date=normalized_date_text,
        )
    if run is None:
        raise KeyError(
            f"未找到 {normalized_date_text} 的 Codex 日报：{root_identity['root_dir']}"
        )
    if not run.timezone:
        run.timezone = timezone.key
    return _serialize_codex_daily_summary_run(run)


def get_codex_daily_summary_latest_run_by_root_key(
    scope_key: str,
    root_key: str,
    target_date_text: str,
    *,
    session: Session | None = None,
) -> dict[str, Any]:
    normalized_date_text, _, timezone, _, _ = _resolve_codex_daily_summary_range(target_date_text)
    with _session_scope(session) as session:
        run = _get_codex_daily_summary_latest_row(
            session,
            scope_key=scope_key,
            root_key=root_key,
            summary_date=normalized_date_text,
        )
    if run is None:
        raise KeyError(f"未找到 {normalized_date_text} 的 Codex 日报：{root_key}")
    if not run.timezone:
        run.timezone = timezone.key
    return _serialize_codex_daily_summary_run(run)


def get_codex_daily_summary_run(
    scope_key: str,
    run_id: str,
    *,
    session: Session | None = None,
) -> dict[str, Any]:
    with _session_scope(session) as session:
        run = _get_codex_daily_summary_run_row(session, scope_key=scope_key, run_id=run_id)
        return _serialize_codex_daily_summary_run(run)


def start_codex_daily_summary_run(
    scope_key: str,
    root_dir: str | None,
    target_date_text: str,
    *,
    model: str | None = None,
    user_id: int | None = None,
    force: bool = False,
    session: Session | None = None,
    root_identity: dict[str, str] | None = None,
    source_loader: Any = None,
) -> dict[str, Any]:
    root_identity = root_identity or _resolve_codex_daily_summary_root_identity(root_dir, require_existing=True)
    normalized_date_text, _, timezone, _, _ = _resolve_codex_daily_summary_range(target_date_text)
    db_engine = engine
    resolved_model = _resolve_codex_daily_summary_model(model)

    with _session_scope(session) as session:
        try:
            db_engine = session.get_bind()
        except Exception:
            db_engine = engine
        latest_run = _get_codex_daily_summary_latest_row(
            session,
            scope_key=scope_key,
            root_key=root_identity["root_key"],
            summary_date=normalized_date_text,
        )
        if latest_run is not None:
            latest_status = str(latest_run.status or "")
            if latest_status in {"pending", "running"}:
                return _serialize_codex_daily_summary_run(latest_run, reused_existing_run=True)
            if latest_status == "completed" and not force:
                return _serialize_codex_daily_summary_run(latest_run, reused_existing_run=True)

        now = time.time()
        run = CodexDailySummaryRun(
            scope_key=scope_key,
            user_id=user_id,
            root_key=root_identity["root_key"],
            root_dir=root_identity["root_dir"],
            summary_date=normalized_date_text,
            timezone=timezone.key,
            provider=_CODEX_DAILY_SUMMARY_PROVIDER_ID,
            generated_by=_CODEX_DAILY_SUMMARY_GENERATED_BY,
            model=resolved_model,
            prompt_version=_CODEX_DAILY_SUMMARY_PROMPT_VERSION,
            force_requested=bool(force),
            status="running",
            stage="queued",
            stage_label="已进入队列",
            created_at=now,
            heartbeat_at=now,
            updated_at=now,
        )
        session.add(run)
        session.commit()
        session.refresh(run)
        serialized = _serialize_codex_daily_summary_run(run)

    worker = threading.Thread(
        target=_run_codex_daily_summary_worker,
        kwargs={
            "db_engine": db_engine,
            "run_id": run.id,
            "scope_key": scope_key,
            "root_dir": root_identity["root_dir"],
            "target_date_text": normalized_date_text,
            "user_id": user_id,
            "model": resolved_model,
            "source_loader": source_loader,
        },
        daemon=True,
    )
    worker.start()
    return serialized
