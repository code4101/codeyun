from __future__ import annotations

import os
from pathlib import Path
import sqlite3
import time
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from backend.core.background_task_queue import background_task_queue
from backend.core.feature_access_guard import require_feature_access_dependency
from backend.core.settings import get_settings


FEATURE_KEY = "notes.wechat"
DEFAULT_PAGE_SIZE = 80
MAX_PAGE_SIZE = 500
WECHAT_ARCHIVE_SYNC_TASK_NAME = "wechat_archive_incremental_sync"
_WECHAT_ARCHIVE_LAST_SYNC_RESULT: dict[str, Any] | None = None

router = APIRouter(
    dependencies=[Depends(require_feature_access_dependency(FEATURE_KEY))],
)


class WeChatArchiveImportRequest(BaseModel):
    chat_name: str = Field(min_length=1, max_length=120)
    mode: Literal["loaded", "scroll", "full"] = "loaded"
    max_scrolls: int | None = Field(default=0, ge=0, le=10000)
    exact: bool = True
    save_media: bool = False


class WeChatArchiveSyncStartRequest(BaseModel):
    mode: Literal["incremental", "latest", "history", "history_clearance", "full"] = "incremental"
    chat_name: str | None = Field(default=None, max_length=120)
    chat_names: list[str] | None = None
    max_runtime: int = Field(default=90, ge=5, le=3600)
    max_chats: int = Field(default=6, ge=1, le=50)
    max_scrolls_total: int = Field(default=8, ge=0, le=10000)
    max_scrolls_per_chat: int = Field(default=1, ge=0, le=1000)
    exact: bool = True
    save_media: bool = False


def _settings_wechat_db_storage_path() -> Path:
    env_path = (os.environ.get("CODEYUN_WECHAT_DB_STORAGE") or "").strip()
    if env_path:
        return Path(env_path).expanduser()
    return Path(r"D:\home\chenkunze\data\d2605微信逆向\decrypted\db_storage")


def _settings_archive_db_path() -> Path:
    settings = get_settings()
    env_path = (os.environ.get("CODEYUN_WECHAT_ARCHIVE_DB") or "").strip()
    if env_path:
        return Path(env_path).expanduser()

    default_path = settings.data_dir / "wechat_archive" / "archive.sqlite"
    legacy_probe = Path(r"C:\home\chenkunze\data\wechat_archive\archive.sqlite")
    if legacy_probe.exists():
        return legacy_probe
    return default_path


def _connect_archive(readonly: bool = True):
    db_path = _settings_archive_db_path()
    if readonly:
        if not db_path.exists():
            raise FileNotFoundError(db_path)
        uri = "{}?mode=ro".format(db_path.resolve().as_uri())
        conn = sqlite3.connect(uri, uri=True)
    else:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _chat_names_from_payload(payload: WeChatArchiveSyncStartRequest) -> list[str] | None:
    names: list[str] = []
    if payload.chat_name and payload.chat_name.strip():
        names.append(payload.chat_name.strip())
    if payload.chat_names:
        names.extend(name.strip() for name in payload.chat_names if name and name.strip())
    deduped = list(dict.fromkeys(names))
    return deduped or None


def _is_wechat_sync_task(snapshot: dict[str, Any] | None) -> bool:
    return bool(snapshot and snapshot.get("name") == WECHAT_ARCHIVE_SYNC_TASK_NAME)


def _queue_has_wechat_sync_task() -> bool:
    queue = background_task_queue.snapshot()
    if _is_wechat_sync_task(queue.get("running")):
        return True
    return any(_is_wechat_sync_task(item) for item in queue.get("pending") or [])


def _latest_wechat_sync_queue_run(queue: dict[str, Any]) -> dict[str, Any] | None:
    for item in queue.get("recent") or []:
        if _is_wechat_sync_task(item):
            return item
    if _is_wechat_sync_task(queue.get("running")):
        return queue.get("running")
    return None


def _run_wechat_archive_sync_job(payload: dict[str, Any]) -> dict[str, Any]:
    global _WECHAT_ARCHIVE_LAST_SYNC_RESULT

    from pyxllib.autogui.wechat_archive import WeChatArchive

    started_at = time.time()
    db_path = _settings_archive_db_path()
    archive = WeChatArchive(db_path)
    mode = payload.get("mode") or "incremental"
    chat_names = payload.get("chat_names")

    if mode == "full":
        if not chat_names:
            raise ValueError("full sync requires chat_name")
        result = archive.full_chat(
            chat_names[0],
            exact=bool(payload.get("exact", True)),
            save_media=bool(payload.get("save_media", False)),
        )
    elif mode == "history_clearance":
        result = archive.sync_history_clearance(
            chat_name=chat_names[0] if chat_names else None,
            max_runtime=payload.get("max_runtime", 1800),
            max_scrolls=payload.get("max_scrolls_total", 200),
            exact=bool(payload.get("exact", True)),
            save_media=bool(payload.get("save_media", False)),
        )
    elif mode == "history":
        result = archive.sync_incremental(
            chat_names=chat_names,
            max_runtime=payload.get("max_runtime", 90),
            max_chats=payload.get("max_chats", 6),
            max_scrolls_total=payload.get("max_scrolls_total", 8),
            max_scrolls_per_chat=payload.get("max_scrolls_per_chat", 1),
            sync_latest=False,
            backfill_history=True,
            exact=bool(payload.get("exact", True)),
            save_media=bool(payload.get("save_media", False)),
        )
    elif mode == "latest":
        result = archive.sync_incremental(
            chat_names=chat_names,
            max_runtime=payload.get("max_runtime", 90),
            max_chats=payload.get("max_chats", 6),
            max_scrolls_total=0,
            max_scrolls_per_chat=0,
            sync_latest=True,
            backfill_history=False,
            exact=bool(payload.get("exact", True)),
            save_media=bool(payload.get("save_media", False)),
        )
    else:
        result = archive.sync_incremental(
            chat_names=chat_names,
            max_runtime=payload.get("max_runtime", 90),
            max_chats=payload.get("max_chats", 6),
            max_scrolls_total=payload.get("max_scrolls_total", 8),
            max_scrolls_per_chat=payload.get("max_scrolls_per_chat", 1),
            exact=bool(payload.get("exact", True)),
            save_media=bool(payload.get("save_media", False)),
        )

    _WECHAT_ARCHIVE_LAST_SYNC_RESULT = {
        "mode": mode,
        "started_at": started_at,
        "finished_at": time.time(),
        "payload": payload,
        "result": result,
    }
    return _WECHAT_ARCHIVE_LAST_SYNC_RESULT


def _enqueue_wechat_archive_sync(payload: dict[str, Any]) -> str:
    if _queue_has_wechat_sync_task():
        raise HTTPException(status_code=409, detail="微信归档同步任务已在队列中")
    return background_task_queue.enqueue(
        WECHAT_ARCHIVE_SYNC_TASK_NAME,
        _run_wechat_archive_sync_job,
        payload,
        metadata={
            "mode": payload.get("mode"),
            "chat_names": payload.get("chat_names"),
            "max_runtime": payload.get("max_runtime"),
            "max_scrolls_total": payload.get("max_scrolls_total"),
        },
    )


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def _parse_json(value: str | None) -> Any:
    if not value:
        return None
    try:
        import json

        return json.loads(value)
    except Exception:
        return value


def _archive_status_payload() -> dict[str, Any]:
    db_path = _settings_archive_db_path()
    payload: dict[str, Any] = {
        "db_path": os.fspath(db_path),
        "exists": db_path.exists(),
        "accounts": 0,
        "chats": 0,
        "messages": 0,
        "latest_collected_at": None,
    }
    if not db_path.exists():
        return payload

    conn = _connect_archive(readonly=True)
    try:
        payload["accounts"] = conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
        payload["chats"] = conn.execute("SELECT COUNT(*) FROM chats").fetchone()[0]
        payload["messages"] = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        payload["latest_collected_at"] = conn.execute("SELECT MAX(collected_at) FROM messages").fetchone()[0]
        return payload
    finally:
        conn.close()


def _ensure_archive_schema_if_exists() -> None:
    db_path = _settings_archive_db_path()
    if not db_path.exists():
        return
    from pyxllib.autogui.wechat_archive import WeChatArchive

    WeChatArchive(db_path)


def _open_wechat_db_storage():
    from pyxllib.autogui.wechat_db import WeChatDbStorage

    return WeChatDbStorage(_settings_wechat_db_storage_path())


@router.get("/status")
def get_wechat_archive_status():
    return _archive_status_payload()


@router.get("/db-status")
def get_wechat_db_status():
    storage = _open_wechat_db_storage()
    try:
        return storage.status()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"微信数据库读取失败：{exc}") from exc


@router.post("/db-sync-live")
def sync_wechat_db_from_live():
    storage = _open_wechat_db_storage()
    try:
        return storage.sync_from_live(export_media=True)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"微信数据库同步失败：{exc}") from exc


@router.get("/db-schema")
def get_wechat_db_schema():
    storage = _open_wechat_db_storage()
    try:
        return {"items": storage.schema_overview(), "db_storage_path": os.fspath(storage.root)}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"微信数据库 schema 读取失败：{exc}") from exc


@router.get("/db-chats")
def list_wechat_db_chats(
    q: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 500,
    offset: Annotated[int, Query(ge=0)] = 0,
    scope: Annotated[Literal["main", "folded", "all"], Query()] = "main",
):
    storage = _open_wechat_db_storage()
    try:
        folded = True if scope == "folded" else None
        include_folded_entry = scope == "main"
        return {
            "items": storage.list_chats(
                limit=limit,
                offset=offset,
                q=q,
                folded=folded,
                include_folded_entry=include_folded_entry,
            ),
            "total": storage.count_chats(q=q, folded=folded, include_folded_entry=include_folded_entry),
            "db_storage_path": os.fspath(storage.root),
        }
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"微信数据库会话读取失败：{exc}") from exc


@router.get("/db-messages")
def list_wechat_db_messages(
    chat_username: Annotated[str, Query(min_length=1, max_length=200)],
    q: Annotated[str | None, Query(max_length=200)] = None,
    message_type: Annotated[str | None, Query(max_length=40)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    offset: Annotated[int, Query(ge=0)] = 0,
    order: Annotated[Literal["asc", "desc"], Query()] = "desc",
    include_resources: bool = True,
):
    storage = _open_wechat_db_storage()
    try:
        payload = storage.list_messages(
            chat_username=chat_username,
            q=q,
            message_type=message_type,
            limit=limit,
            offset=offset,
            order=order,
            include_resources=include_resources,
        )
        return {**payload, "db_storage_path": os.fspath(storage.root)}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"微信数据库消息读取失败：{exc}") from exc


@router.get("/db-message-count")
def count_wechat_db_messages(
    chat_username: Annotated[str, Query(min_length=1, max_length=200)],
    q: Annotated[str | None, Query(max_length=200)] = None,
    message_type: Annotated[str | None, Query(max_length=40)] = None,
):
    storage = _open_wechat_db_storage()
    try:
        payload = storage.count_messages(
            chat_username=chat_username,
            q=q,
            message_type=message_type,
        )
        return {**payload, "db_storage_path": os.fspath(storage.root)}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"微信数据库消息计数失败：{exc}") from exc


@router.get("/db-message-types")
def list_wechat_db_message_types(chat_username: Annotated[str | None, Query(max_length=200)] = None):
    storage = _open_wechat_db_storage()
    try:
        return {"items": storage.message_types(chat_username=chat_username)}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"微信数据库消息类型读取失败：{exc}") from exc


@router.get("/db-media/{kind}/{file_name}")
def get_wechat_db_media_file(kind: Literal["image", "video", "file"], file_name: str):
    storage = _open_wechat_db_storage()
    root = (storage.root.parent / "exported_media" / kind).resolve()
    path = (root / file_name).resolve()
    if not str(path).startswith(str(root)) or not path.exists():
        raise HTTPException(status_code=404, detail="资源文件不存在或尚未导出")
    return FileResponse(path, filename=file_name)


@router.get("/db-tables")
def list_wechat_db_tables(database: Annotated[str, Query(min_length=1, max_length=40)]):
    storage = _open_wechat_db_storage()
    try:
        return {"items": storage.list_tables(database)}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"微信数据库表读取失败：{exc}") from exc


@router.get("/db-table-rows")
def list_wechat_db_table_rows(
    database: Annotated[str, Query(min_length=1, max_length=40)],
    table: Annotated[str, Query(min_length=1, max_length=120)],
    q: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    storage = _open_wechat_db_storage()
    try:
        return storage.browse_table(database=database, table=table, q=q, limit=limit, offset=offset)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"微信数据库表数据读取失败：{exc}") from exc


@router.get("/chats")
def list_wechat_archive_chats():
    db_path = _settings_archive_db_path()
    if not db_path.exists():
        return {"items": [], "db_path": os.fspath(db_path)}
    _ensure_archive_schema_if_exists()

    conn = _connect_archive(readonly=True)
    try:
        rows = conn.execute(
            """
            SELECT
                c.id,
                c.name,
                c.chat_type,
                c.remark,
                c.group_member_count,
                c.status,
                c.last_error,
                cfg.enabled AS sync_enabled,
                cfg.priority AS sync_priority,
                cfg.sync_latest,
                cfg.backfill_history,
                ss.loaded_count,
                ss.scroll_count,
                ss.reached_top,
                ss.last_incremental_at,
                ss.last_history_at,
                ss.last_success_at,
                ss.consecutive_failures,
                ss.next_due_at,
                ss.updated_at AS sync_updated_at,
                COUNT(m.id) AS message_count,
                MAX(m.collected_at) AS latest_collected_at,
                MIN(m.normalized_time) AS first_message_time,
                MAX(m.normalized_time) AS last_message_time
            FROM chats c
            LEFT JOIN messages m ON m.chat_id = c.id
            LEFT JOIN sync_state ss ON ss.chat_id = c.id
            LEFT JOIN chat_sync_config cfg ON cfg.chat_id = c.id
            GROUP BY c.id
            ORDER BY latest_collected_at DESC, c.updated_at DESC, c.id DESC
            """
        ).fetchall()
        return {"items": [_row_to_dict(row) for row in rows], "db_path": os.fspath(db_path)}
    finally:
        conn.close()


@router.get("/messages")
def list_wechat_archive_messages(
    chat_id: Annotated[int | None, Query(ge=1)] = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    direction: Annotated[str | None, Query(max_length=20)] = None,
    message_type: Annotated[str | None, Query(max_length=40)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    db_path = _settings_archive_db_path()
    if not db_path.exists():
        return {"total": 0, "items": [], "db_path": os.fspath(db_path)}

    clauses = []
    params: list[Any] = []
    if chat_id:
        clauses.append("m.chat_id = ?")
        params.append(chat_id)
    if q:
        clauses.append("(m.content LIKE ? OR m.sender LIKE ? OR m.sender_remark LIKE ?)")
        needle = "%{}%".format(q.strip())
        params.extend([needle, needle, needle])
    if direction:
        clauses.append("m.direction = ?")
        params.append(direction)
    if message_type:
        clauses.append("m.message_type = ?")
        params.append(message_type)

    where_sql = " WHERE " + " AND ".join(clauses) if clauses else ""
    conn = _connect_archive(readonly=True)
    try:
        total = conn.execute(
            "SELECT COUNT(*) FROM messages m{}".format(where_sql),
            params,
        ).fetchone()[0]
        rows = conn.execute(
            """
            SELECT
                m.id,
                m.chat_id,
                c.name AS chat_name,
                m.direction,
                m.sender,
                m.sender_remark,
                m.message_type,
                m.content,
                m.media_path,
                m.normalized_time,
                m.raw_time_label,
                m.raw_id,
                m.raw_json,
                m.fingerprint,
                m.collected_at
            FROM messages m
            JOIN chats c ON c.id = m.chat_id
            {where_sql}
            ORDER BY
                COALESCE(m.normalized_time, '') DESC,
                m.id DESC
            LIMIT ? OFFSET ?
            """.format(where_sql=where_sql),
            [*params, limit, offset],
        ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item["raw"] = _parse_json(item.pop("raw_json", None))
            items.append(item)
        return {"total": total, "items": items, "db_path": os.fspath(db_path)}
    finally:
        conn.close()


@router.get("/message-types")
def list_wechat_archive_message_types(chat_id: Annotated[int | None, Query(ge=1)] = None):
    db_path = _settings_archive_db_path()
    if not db_path.exists():
        return {"items": []}

    params: list[Any] = []
    where_sql = ""
    if chat_id:
        where_sql = " WHERE chat_id = ?"
        params.append(chat_id)

    conn = _connect_archive(readonly=True)
    try:
        rows = conn.execute(
            "SELECT message_type, COUNT(*) AS count FROM messages{} GROUP BY message_type ORDER BY count DESC".format(
                where_sql
            ),
            params,
        ).fetchall()
        return {"items": [_row_to_dict(row) for row in rows]}
    finally:
        conn.close()


@router.get("/sync-plan")
def get_wechat_archive_sync_plan(
    max_chats: Annotated[int, Query(ge=1, le=50)] = 12,
    chat_name: Annotated[str | None, Query(max_length=120)] = None,
    kind: Annotated[Literal["incremental", "history"], Query()] = "incremental",
):
    db_path = _settings_archive_db_path()
    if not db_path.exists():
        return {"items": [], "db_path": os.fspath(db_path)}

    try:
        from pyxllib.autogui.wechat_archive import WeChatArchive

        archive = WeChatArchive(db_path)
        if kind == "history":
            items = archive.plan_history_chats(
                manual_chat_names=[chat_name] if chat_name else None,
                limit=max_chats,
            )
        else:
            items = archive.plan_sync_chats(
                manual_chat_names=[chat_name] if chat_name else None,
                limit=max_chats,
            )
        return {"items": items, "db_path": os.fspath(db_path)}
    except Exception as exc:
        raise HTTPException(status_code=502, detail="微信归档同步计划生成失败：{}".format(exc)) from exc


@router.get("/sync-status")
def get_wechat_archive_sync_status():
    queue = background_task_queue.snapshot()
    return {
        "active": _queue_has_wechat_sync_task(),
        "queue": queue,
        "latest_queue_run": _latest_wechat_sync_queue_run(queue),
        "latest_result": _WECHAT_ARCHIVE_LAST_SYNC_RESULT,
        "status": _archive_status_payload(),
    }


@router.post("/sync/start")
def start_wechat_archive_sync(payload: WeChatArchiveSyncStartRequest):
    chat_names = _chat_names_from_payload(payload)
    if payload.mode == "full" and not chat_names:
        raise HTTPException(status_code=400, detail="全量同步需要指定会话名")
    if payload.mode in ("latest", "history") and payload.chat_name and not chat_names:
        raise HTTPException(status_code=400, detail="会话名不能为空")

    task_payload = payload.model_dump()
    task_payload["chat_names"] = chat_names
    task_id = _enqueue_wechat_archive_sync(task_payload)
    return {
        "queued": True,
        "queue_task_id": task_id,
        "task_name": WECHAT_ARCHIVE_SYNC_TASK_NAME,
        "sync_status": get_wechat_archive_sync_status(),
    }


@router.post("/import")
def import_wechat_archive(payload: WeChatArchiveImportRequest):
    db_path = _settings_archive_db_path()
    try:
        from pyxllib.autogui.wechat_archive import WeChatArchive

        archive = WeChatArchive(db_path)
        if payload.mode == "full":
            result = archive.full_chat(
                payload.chat_name,
                exact=payload.exact,
                save_media=payload.save_media,
            )
        else:
            max_scrolls = payload.max_scrolls or 0
            result = archive.pull_chat(
                payload.chat_name,
                until="top" if payload.mode == "scroll" or max_scrolls else "loaded",
                max_scrolls=max_scrolls,
                exact=payload.exact,
                save_media=payload.save_media,
            )
        return {
            **result,
            "status": _archive_status_payload(),
        }
    except Exception as exc:
        raise HTTPException(status_code=502, detail="微信归档导入失败：{}".format(exc)) from exc
