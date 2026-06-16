from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import inspect
import ipaddress
import os
from pathlib import Path
import re
import sqlite3
import time
from urllib.parse import quote, unquote, urlparse
from typing import Annotated, Any, Literal

import psutil
import requests
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import object_session
from sqlmodel import Session, select
from starlette.background import BackgroundTask

from backend.core.runtime.background_task_queue import background_task_queue
from backend.core.access.auth import extract_api_token, get_optional_current_user_from_token, validate_api_token_value
from backend.core.access.feature_access_guard import ensure_feature_access
from backend.core.settings import get_settings
from backend.db import get_session
from backend.models import User, UserDevice


FEATURE_KEY = "notes.wechat"
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 500
WECHAT_ARCHIVE_SYNC_TASK_NAME = "wechat_archive_incremental_sync"
_WECHAT_ARCHIVE_LAST_SYNC_RESULT: dict[str, Any] | None = None
REMOTE_DEVICE_DIRECT_PROXIES = {"http": "", "https": "", "all": "", "no_proxy": "*"}
REMOTE_WECHAT_DEVICE_PREFIX = "entry:"


async def require_wechat_archive_access(
    session: Session = Depends(get_session),
    current_user: User | None = Depends(get_optional_current_user_from_token),
    authorization: str | None = Header(None),
    x_device_token: str | None = Header(None),
    token: str | None = Query(None),
    sec_websocket_protocol: str | None = Header(None),
) -> User | None:
    api_token = extract_api_token(
        authorization=authorization,
        x_device_token=x_device_token,
        token=token,
        sec_websocket_protocol=sec_websocket_protocol,
    )
    if api_token:
        try:
            validate_api_token_value(api_token)
            return current_user
        except HTTPException:
            pass
    return ensure_feature_access(session, feature_key=FEATURE_KEY, current_user=current_user)


router = APIRouter(dependencies=[Depends(require_wechat_archive_access)])


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
    legacy_codepc_mf_path = Path(r"D:\home\chenkunze\data\d2605微信逆向\decrypted\db_storage")
    if legacy_codepc_mf_path.exists():
        return legacy_codepc_mf_path
    return get_settings().data_dir / "wechat_db" / "decrypted" / "db_storage"


def _settings_wechat_legacy_storage_path() -> Path:
    env_path = (os.environ.get("CODEYUN_WECHAT_LEGACY_STORAGE") or "").strip()
    if env_path:
        return Path(env_path).expanduser()
    return get_settings().data_dir / "wechat_legacy" / "decrypted"


def _settings_tim_legacy_storage_path() -> Path:
    env_path = (os.environ.get("CODEYUN_TIM_LEGACY_STORAGE") or "").strip()
    if env_path:
        return Path(env_path).expanduser()
    return get_settings().data_dir / "tim_legacy" / "decrypted"


def _settings_wechat_db_storage_path_for_device(device_root: Path) -> Path:
    default_path = _settings_wechat_db_storage_path()
    device_root_path = device_root / "wechat_db" / "decrypted" / "db_storage"
    if device_root_path.exists():
        return device_root_path
    if (device_root / "wechat_legacy").exists() or (device_root / "tim_legacy").exists() or _is_tim_account_root(device_root):
        return device_root / "wechat_db" / "decrypted" / "db_storage"
    if device_root.resolve() == get_settings().data_dir.resolve():
        return default_path
    if not default_path.exists():
        return device_root_path
    return default_path


def _settings_wechat_legacy_storage_path_for_device(device_root: Path) -> Path:
    default_path = _settings_wechat_legacy_storage_path()
    device_root_path = device_root / "wechat_legacy" / "decrypted"
    if device_root_path.exists():
        return device_root_path
    if _is_tim_account_root(device_root):
        return device_root / "wechat_legacy" / "decrypted"
    if device_root.resolve() == get_settings().data_dir.resolve():
        return default_path
    if not default_path.exists():
        return device_root_path
    return default_path


def _is_tim_account_root(device_root: Path) -> bool:
    return any((device_root / name).exists() for name in ("Msg3.0.db", "Msg2.0.db"))


def _settings_tim_legacy_storage_path_for_device(device_root: Path) -> Path:
    default_path = _settings_tim_legacy_storage_path()
    if _is_tim_account_root(device_root):
        return device_root
    device_root_path = device_root / "tim_legacy" / "decrypted"
    if device_root_path.exists():
        return device_root_path
    if device_root.resolve() == get_settings().data_dir.resolve():
        return default_path
    if not default_path.exists():
        return device_root_path
    return default_path


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


def _run_wechat_db_live_sync_job(payload: dict[str, Any]) -> dict[str, Any]:
    global _WECHAT_ARCHIVE_LAST_SYNC_RESULT

    started_at = time.time()
    storage = _open_wechat_db_storage()
    result = storage.sync_from_live(export_media=bool(payload.get("save_media", True)))
    _WECHAT_ARCHIVE_LAST_SYNC_RESULT = {
        "mode": payload.get("mode") or "db_storage_live",
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


def _enqueue_wechat_db_live_sync(payload: dict[str, Any] | None = None) -> str:
    task_payload = {
        "mode": "db_storage_live",
        "save_media": True,
        **(payload or {}),
    }
    if _queue_has_wechat_sync_task():
        raise HTTPException(status_code=409, detail="微信数据库同步任务已在队列中")
    return background_task_queue.enqueue(
        WECHAT_ARCHIVE_SYNC_TASK_NAME,
        _run_wechat_db_live_sync_job,
        task_payload,
        metadata={
            "mode": task_payload.get("mode"),
            "save_media": task_payload.get("save_media"),
            "source": "db_storage",
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


@dataclass(frozen=True)
class RemoteWeChatDeviceRef:
    entry: UserDevice
    remote_device_id: str
    public_device_id: str


def _has_user_device_context(session: Any, current_user: Any) -> bool:
    return hasattr(session, "exec") and getattr(current_user, "id", None) is not None


def _release_user_device_session(entry: UserDevice) -> None:
    entry_session = object_session(entry)
    if entry_session is not None:
        entry_session.expunge(entry)
        entry_session.close()


def _remote_wechat_device_public_id(entry_id: str, remote_device_id: str) -> str:
    return f"{REMOTE_WECHAT_DEVICE_PREFIX}{entry_id}:{quote(remote_device_id, safe='')}"


def _parse_remote_wechat_device_id(device_id: str | None) -> tuple[str, str] | None:
    if not device_id or not device_id.startswith(REMOTE_WECHAT_DEVICE_PREFIX):
        return None
    rest = device_id[len(REMOTE_WECHAT_DEVICE_PREFIX) :]
    entry_id, sep, encoded_remote_id = rest.partition(":")
    if not entry_id or not sep:
        return None
    return entry_id, unquote(encoded_remote_id)


def _remote_wechat_headers(entry: UserDevice) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {entry.token}",
        "X-Device-Token": entry.token,
    }


def _remote_wechat_base_url(entry: UserDevice) -> str:
    if entry.mode != "remote":
        raise HTTPException(status_code=400, detail="该设备不是远程设备")
    if not entry.server_url:
        raise HTTPException(status_code=400, detail="远程设备缺少后端地址")
    return entry.server_url.rstrip("/")


def _extract_remote_wechat_error(response: requests.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        detail = payload.get("detail") or payload.get("message") or payload.get("error")
        if isinstance(detail, str) and detail.strip():
            return detail.strip()
    return response.text.strip() or f"远程设备返回 HTTP {response.status_code}"


def _remote_wechat_json(
    ref: RemoteWeChatDeviceRef,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    timeout: int = 20,
) -> Any:
    query = {key: value for key, value in (params or {}).items() if value is not None}
    if ref.remote_device_id:
        query["device_id"] = ref.remote_device_id
    entry = ref.entry
    url = f"{_remote_wechat_base_url(entry)}/api{path}"
    headers = _remote_wechat_headers(entry)
    _release_user_device_session(entry)
    try:
        response = requests.get(
            url,
            headers=headers,
            params=query,
            proxies=REMOTE_DEVICE_DIRECT_PROXIES.copy(),
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"远程微信数据设备不可达：{exc}") from exc
    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=_extract_remote_wechat_error(response))
    try:
        return response.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="远程微信数据接口返回了无效 JSON") from exc


def _remote_wechat_stream(
    ref: RemoteWeChatDeviceRef,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    timeout: int = 60,
) -> StreamingResponse:
    query = {key: value for key, value in (params or {}).items() if value is not None}
    if ref.remote_device_id:
        query["device_id"] = ref.remote_device_id
    entry = ref.entry
    url = f"{_remote_wechat_base_url(entry)}/api{path}"
    headers = _remote_wechat_headers(entry)
    _release_user_device_session(entry)
    try:
        response = requests.get(
            url,
            headers=headers,
            params=query,
            proxies=REMOTE_DEVICE_DIRECT_PROXIES.copy(),
            timeout=timeout,
            stream=True,
        )
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"远程微信资源不可达：{exc}") from exc
    if response.status_code >= 400:
        detail = _extract_remote_wechat_error(response)
        response.close()
        raise HTTPException(status_code=response.status_code, detail=detail)
    headers = {
        key: value
        for key, value in response.headers.items()
        if key.lower() in {"cache-control", "content-disposition", "content-length", "etag", "last-modified"}
    }
    return StreamingResponse(
        response.iter_content(chunk_size=64 * 1024),
        status_code=response.status_code,
        media_type=response.headers.get("content-type") or None,
        headers=headers,
        background=BackgroundTask(response.close),
    )


def _remote_user_device_entries(session: Any, current_user: Any) -> list[UserDevice]:
    if not _has_user_device_context(session, current_user):
        return []
    entries = list(
        session.exec(
            select(UserDevice)
            .where(
                UserDevice.user_id == current_user.id,
                UserDevice.is_active == True,  # noqa: E712
                UserDevice.mode == "remote",
            )
            .order_by(UserDevice.order_index, UserDevice.created_at)
        )
    )
    return [entry for entry in entries if not _is_loopback_server_url(entry.server_url)]


def _is_loopback_server_url(value: str | None) -> bool:
    if not value:
        return False
    host = (urlparse(value).hostname or "").strip().lower()
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _resolve_remote_wechat_device(
    device_id: str | None,
    session: Any,
    current_user: Any,
) -> RemoteWeChatDeviceRef | None:
    parsed = _parse_remote_wechat_device_id(device_id)
    if not parsed:
        return None
    if not _has_user_device_context(session, current_user):
        raise HTTPException(status_code=404, detail=f"微信数据设备不存在：{device_id}")
    entry_id, remote_device_id = parsed
    entry = session.get(UserDevice, entry_id)
    if not entry or entry.user_id != current_user.id or not entry.is_active:
        raise HTTPException(status_code=404, detail=f"微信数据设备不存在：{device_id}")
    if entry.mode != "remote":
        raise HTTPException(status_code=400, detail="该微信数据设备不是远程设备")
    return RemoteWeChatDeviceRef(entry=entry, remote_device_id=remote_device_id, public_device_id=device_id or "")


def _remote_wechat_device_payloads(session: Any, current_user: Any) -> list[dict[str, Any]]:
    devices: list[dict[str, Any]] = []
    for entry in _remote_user_device_entries(session, current_user):
        try:
            payload = _remote_wechat_json(
                RemoteWeChatDeviceRef(entry=entry, remote_device_id="", public_device_id=""),
                "/wechat-archive/db-devices",
                timeout=8,
            )
            remote_items = payload.get("items") if isinstance(payload, dict) else []
            if not isinstance(remote_items, list):
                remote_items = []
            for item in remote_items:
                if not isinstance(item, dict):
                    continue
                remote_device_id = str(item.get("id") or entry.device_id or entry.entry_id)
                public_id = _remote_wechat_device_public_id(entry.entry_id, remote_device_id)
                entry_label = str(entry.name or entry.device_id or entry.entry_id).strip()
                remote_label = str(item.get("label") or remote_device_id).replace("（本机）", "").strip() or remote_device_id
                label = entry_label if entry_label in {remote_label, remote_device_id} else f"{entry_label} · {remote_label}"
                devices.append(
                    {
                        **item,
                        "id": public_id,
                        "device_id": remote_device_id,
                        "entry_id": entry.entry_id,
                        "remote": True,
                        "label": label,
                        "current": False,
                        "can_sync_live": False,
                        "server_url": entry.server_url,
                    }
                )
        except Exception as exc:
            public_id = _remote_wechat_device_public_id(entry.entry_id, entry.device_id or entry.entry_id)
            devices.append(
                {
                    "id": public_id,
                    "device_id": entry.device_id,
                    "entry_id": entry.entry_id,
                    "remote": True,
                    "label": f"{entry.name} · 微信数据",
                    "current": False,
                    "ready": False,
                    "exists": False,
                    "source_format": "unknown",
                    "db_storage_path": "",
                    "self_username": None,
                    "can_sync_live": False,
                    "server_url": entry.server_url,
                    "error": str(exc),
                }
            )
    return devices


def _extra_wechat_device_roots() -> list[Path]:
    raw = (os.environ.get("CODEYUN_WECHAT_DEVICE_ROOTS") or "").strip()
    if not raw:
        return []

    values: list[str] = []
    try:
        import json

        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            values.extend(str(value) for value in parsed.values())
        elif isinstance(parsed, list):
            values.extend(str(value) for value in parsed)
    except Exception:
        for item in raw.replace("\n", ";").split(";"):
            value = item.strip()
            if not value:
                continue
            if "=" in value:
                value = value.split("=", 1)[1].strip()
            values.append(value)

    return [Path(value).expanduser() for value in values if value]


def _wechat_official_device_roots() -> list[Path]:
    home = Path.home()
    candidates = [
        *_wechat_live_process_device_roots(),
        home / "Documents" / "xwechat_files",
        home / "Documents" / "WeChat Files",
        home / "AppData" / "Roaming" / "Tencent" / "xwechat",
        home / "AppData" / "Roaming" / "Tencent" / "WeChat",
    ]
    resolved: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        if not candidate.exists() or not candidate.is_dir():
            continue
        key = os.fspath(candidate.resolve()).lower()
        if key in seen:
            continue
        seen.add(key)
        resolved.append(candidate)
    return resolved


def _tim_official_device_roots() -> list[Path]:
    try:
        from backend.core.messaging.tim_legacy_db import tim_account_roots

        return tim_account_roots()
    except Exception:
        return []


@lru_cache(maxsize=1)
def _wechat_live_process_device_roots() -> tuple[Path, ...]:
    if os.name != "nt":
        return ()
    command_lines: list[str] = []
    process_names = {"weixin.exe", "wechat.exe", "wechatappex.exe"}
    for proc in psutil.process_iter(["name", "cmdline"]):
        try:
            name = str(proc.info.get("name") or "").lower()
            if name not in process_names:
                continue
            cmdline = proc.info.get("cmdline") or []
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            continue
        if isinstance(cmdline, list):
            command_lines.append(" ".join(str(part) for part in cmdline))
        elif cmdline:
            command_lines.append(str(cmdline))
    paths: list[Path] = []
    for command_line in command_lines:
        for match in re.finditer(r'--wechat-files-path=(?:"([^"]+)"|(\S+))', command_line, re.IGNORECASE):
            raw = (match.group(1) or match.group(2) or "").strip()
            if raw:
                paths.append(Path(raw).expanduser())
    return tuple(paths)


def _wechat_fallback_device_roots() -> list[Path]:
    settings = get_settings()
    roots: list[Path] = [settings.data_dir]
    parent = settings.data_dir.parent
    if parent.exists():
        roots.extend(path for path in parent.iterdir() if path.is_dir() and path.name.startswith("codepc_"))
    return roots


def _dedupe_existing_paths(paths: list[Path]) -> list[Path]:
    deduped: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        if not path.exists() or not path.is_dir():
            continue
        key = os.fspath(path.resolve()).lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(path)
    return deduped


def _wechat_reverse_data_roots() -> list[Path]:
    env_path = (os.environ.get("CODEYUN_WECHAT_REVERSE_ROOT") or "").strip()
    candidates = [Path(env_path).expanduser()] if env_path else []
    legacy_root = Path(r"D:\home\chenkunze\data\d2605微信逆向")
    candidates.append(legacy_root)
    db_storage_root = _settings_wechat_db_storage_path()
    candidates.append(db_storage_root.parents[1] if len(db_storage_root.parents) > 1 else db_storage_root.parent)
    return _dedupe_existing_paths(candidates)


def _wechat_codeyun_data_roots() -> list[Path]:
    settings = get_settings()
    candidates = [
        settings.data_dir / "wechat-ilink",
        settings.data_dir / "wechat_archive",
        settings.data_dir / "wechat_db",
        settings.data_dir / "wechat_legacy",
    ]
    return _dedupe_existing_paths(candidates)


def _wechat_storage_scan_roots() -> list[tuple[str, str, Path, bool]]:
    roots: list[tuple[str, str, Path, bool]] = []
    for root in _wechat_official_device_roots():
        roots.append((f"official:{root.name}", f"官方微信：{root.name}", root, _is_current_device_root(root)))
    for root in _tim_official_device_roots():
        roots.append((f"tim:{root.name}", f"TIM：{root.name}", root, False))
    for root in _wechat_reverse_data_roots():
        roots.append((f"reverse:{root.name}", f"逆向数据：{root.name}", root, False))
    for root in _wechat_codeyun_data_roots():
        roots.append((f"codeyun:{root.name}", f"CodeYun微信：{root.name}", root, False))

    deduped: list[tuple[str, str, Path, bool]] = []
    seen_paths: set[str] = set()
    seen_ids: set[str] = set()
    for root_id, label, path, current in roots:
        key = os.fspath(path.resolve()).lower()
        if key in seen_paths:
            continue
        next_id = root_id
        index = 2
        while next_id in seen_ids:
            next_id = f"{root_id}-{index}"
            index += 1
        seen_paths.add(key)
        seen_ids.add(next_id)
        deduped.append((next_id, label, path, current))
    return deduped


def _wechat_default_current_device_root(roots: list[Path]) -> Path | None:
    if not roots:
        return None
    settings = get_settings()
    try:
        if settings.data_dir.exists() and any(
            (
                settings.data_dir / rel
            ).exists()
            for rel in (
                Path("wechat_db/decrypted/db_storage"),
                Path("wechat_legacy/decrypted"),
                Path("tim_legacy/decrypted"),
            )
        ):
            return settings.data_dir
    except OSError:
        pass
    official_roots = _wechat_official_device_roots()
    if official_roots:
        official_resolved: set[str] = set(
            os.fspath(candidate.resolve()) for candidate in official_roots if candidate.exists()
        )
        for root in roots:
            if os.fspath(root.resolve() if root.exists() else root) in official_resolved:
                return root
        return official_roots[0]
    for root in roots:
        if root.resolve() == settings.data_dir.resolve():
            return root
    return roots[0]


def _wechat_device_roots() -> list[Path]:
    official_roots = _wechat_official_device_roots()
    roots = list(_extra_wechat_device_roots())
    roots.extend(_wechat_fallback_device_roots())
    roots.extend(official_roots)
    roots.extend(_tim_official_device_roots())

    current_root = _wechat_default_current_device_root(roots)
    deduped: list[Path] = []
    seen: set[str] = set()
    current_root_key = os.fspath(current_root.resolve() if current_root and current_root.exists() else Path("")).lower()
    for root in roots:
        key = os.fspath(root.resolve() if root.exists() else root).lower()
        if key not in seen:
            seen.add(key)
            deduped.append(root)

    def _sort_key(item: Path) -> tuple[int, str]:
        item_key = os.fspath(item.resolve() if item.exists() else item).lower()
        is_current = int(item_key == current_root_key)
        return (-is_current, item.name.lower())

    return sorted(deduped, key=_sort_key)


def _device_id_for_root(root: Path) -> str:
    return root.name


def _is_current_device_root(root: Path) -> bool:
    try:
        current_root = _wechat_default_current_device_root(_wechat_device_roots())
        if current_root is None:
            return False
        return root.resolve() == current_root.resolve()
    except OSError:
        return False


def _choose_wechat_storage_for_device(device_root: Path):
    from pyxllib.autogui.wechat_db import WeChatDbStorage
    from backend.core.messaging.wechat_legacy_db import WeChatLegacyDbStorage, has_legacy_wechat_live_source
    from backend.core.messaging.tim_legacy_db import TimLegacyDbStorage, has_tim_live_source

    is_current = _is_current_device_root(device_root)
    source = (os.environ.get("CODEYUN_WECHAT_DB_SOURCE") or "auto").strip().lower() if is_current else "auto"
    v4_path = _settings_wechat_db_storage_path_for_device(device_root)
    legacy_path = _settings_wechat_legacy_storage_path_for_device(device_root)
    tim_path = _settings_tim_legacy_storage_path_for_device(device_root)
    if source in {"legacy", "v3", "wechat3", "wechat_3"}:
        return WeChatLegacyDbStorage(legacy_path)
    if source in {"v4", "wechat4", "wechat_4"}:
        return WeChatDbStorage(v4_path)
    if source in {"tim", "tim_legacy", "qq", "qq_legacy"}:
        return TimLegacyDbStorage(tim_path)
    if _is_tim_account_root(device_root):
        return TimLegacyDbStorage(tim_path)

    v4_storage = WeChatDbStorage(v4_path)
    try:
        if v4_storage.status().get("ready"):
            return v4_storage
    except Exception:
        pass

    legacy_storage = WeChatLegacyDbStorage(legacy_path)
    try:
        if legacy_storage.status().get("ready") or (is_current and has_legacy_wechat_live_source()):
            return legacy_storage
    except Exception:
        pass
    tim_storage = TimLegacyDbStorage(tim_path)
    try:
        if tim_storage.status().get("ready") or (is_current and has_tim_live_source()):
            return tim_storage
    except Exception:
        pass
    return v4_storage


def _wechat_storage_status(storage) -> dict[str, Any]:
    status = storage.status()
    if "source_format" not in status:
        status["source_format"] = "wechat_4"
    return status


def _wechat_db_device_payload(device_root: Path) -> dict[str, Any]:
    storage = _choose_wechat_storage_for_device(device_root)
    try:
        status = _wechat_storage_status(storage)
    except Exception as exc:
        status = {
            "db_storage_path": os.fspath(getattr(storage, "root", "")),
            "source_format": "unknown",
            "exists": False,
            "ready": False,
            "databases": {},
            "error": str(exc),
        }
    device_id = _device_id_for_root(device_root)
    is_current = _is_current_device_root(device_root)
    label = f"{device_id}（本机）" if is_current else device_id
    return {
        "id": device_id,
        "label": label,
        "current": is_current,
        "ready": bool(status.get("ready")),
        "exists": bool(status.get("exists")),
        "source_format": status.get("source_format") or "unknown",
        "db_storage_path": status.get("db_storage_path") or os.fspath(getattr(storage, "root", "")),
        "self_username": status.get("self_username"),
        "can_sync_live": is_current or status.get("source_format") == "tim_legacy",
        "error": status.get("error"),
    }


def _wechat_db_devices_payload(session: Any = None, current_user: Any = None) -> list[dict[str, Any]]:
    items = [_wechat_db_device_payload(root) for root in _wechat_device_roots()]
    visible_items = [item for item in items if item["current"] or item["ready"] or item["exists"]]
    visible_items.extend(_remote_wechat_device_payloads(session, current_user))
    return visible_items


def _wechat_storage_roots_payload() -> list[dict[str, Any]]:
    items = []
    for device_id, label, root, current in _wechat_storage_scan_roots():
        storage = _choose_wechat_storage_for_device(root)
        try:
            status = _wechat_storage_status(storage)
        except Exception as exc:
            status = {
                "db_storage_path": os.fspath(getattr(storage, "root", "")),
                "source_format": "unknown",
                "exists": False,
                "ready": False,
                "error": str(exc),
            }
        item = {
            "device_id": device_id,
            "label": label,
            "device_root": os.fspath(root),
            "db_storage_path": status.get("db_storage_path") or os.fspath(getattr(storage, "root", "")),
            "current": current,
            "source_format": status.get("source_format") or "unknown",
            "ready": bool(status.get("ready")),
            "exists": bool(status.get("exists")),
        }
        items.append(item)
    return items


def _wechat_storage_root_from_request(device_id: str | None) -> Path:
    if device_id:
        for root_id, _label, root, _current in _wechat_storage_scan_roots():
            if root_id == device_id:
                return root
        raise HTTPException(status_code=400, detail=f"未知微信数据设备：{device_id}")
    return _resolve_wechat_device_root(None)


def _normalize_wechat_storage_absolute_path(
    root: Path,
    path: str,
    absolute_path: str,
) -> Path:
    if absolute_path.strip():
        target = Path(absolute_path.strip())
    elif path.strip():
        target = root / path.strip()
    else:
        target = root

    normalized_target = target.resolve(strict=False)
    scope_roots = [root]
    if absolute_path.strip():
        scope_roots = [item[2] for item in _wechat_storage_scan_roots()]

    normalized_scope_roots = [scope_root.resolve(strict=False) for scope_root in scope_roots]
    target_key = os.path.normcase(os.fspath(normalized_target))
    matched = False
    for scope_root in normalized_scope_roots:
        scope_key = os.path.normcase(os.fspath(scope_root))
        try:
            if os.path.commonpath([target_key, scope_key]) == scope_key:
                matched = True
                break
        except ValueError:
            continue

    if not matched:
        raise HTTPException(status_code=400, detail="微信路径越界")

    return target


def _merge_wechat_directory_usage_stats(target: Path, payload: dict, session: Session | None = None) -> None:
    items = payload.get("items") or []
    directory_items = [item for item in items if item.get("is_dir")]
    if not directory_items:
        return
    if all(item.get("recursive_total_bytes") is not None for item in directory_items):
        return

    try:
        from backend.core.resources.storage_usage import collect_directory_usage

        summary = collect_directory_usage(
            target,
            top_limit=max(len(items), 1000),
            session=session,
        )
    except Exception:
        return

    usage_by_name = {entry.name: entry for entry in summary.top_entries}
    for item in directory_items:
        entry = usage_by_name.get(str(item.get("name") or ""))
        if entry is None:
            continue
        item["recursive_total_bytes"] = entry.logical_size_bytes
        item["recursive_file_count"] = entry.file_count
        item["latest_descendant_modified_at"] = (
            int(entry.modified_at * 1000) if entry.modified_at is not None else item.get("modified_at")
        )
        item["direct_file_count"] = item.get("direct_file_count")
        item["direct_file_bytes"] = item.get("direct_file_bytes")


def _resolve_wechat_device_root(device_id: str | None = None) -> Path:
    roots = _wechat_device_roots()
    if device_id:
        for root in roots:
            if _device_id_for_root(root) == device_id:
                return root
        raise HTTPException(status_code=404, detail=f"微信数据设备不存在：{device_id}")
    for root in roots:
        if _is_current_device_root(root):
            return root
    return roots[0]


def _open_wechat_db_storage(device_id: str | None = None):
    return _choose_wechat_storage_for_device(_resolve_wechat_device_root(device_id))


def _local_wechat_media_roots(device_id: str | None, kind: str) -> list[Path]:
    device_root = _resolve_wechat_device_root(device_id)
    candidates = [
        _settings_wechat_legacy_storage_path_for_device(device_root).parent / "exported_media" / kind,
        _settings_wechat_db_storage_path_for_device(device_root).parent / "exported_media" / kind,
        _settings_tim_legacy_storage_path_for_device(device_root).parent / "exported_media" / kind,
    ]
    roots: list[Path] = []
    seen: set[str] = set()
    for root in candidates:
        key = os.fspath(root.resolve() if root.exists() else root).lower()
        if key not in seen:
            seen.add(key)
            roots.append(root)
    return roots


@router.get("/storage-roots")
def list_wechat_storage_roots():
    return {"items": _wechat_storage_roots_payload()}


@router.get("/storage-directory")
def list_wechat_storage_directory(
    device_id: Annotated[str | None, Query(max_length=200)] = None,
    path: str = "",
    absolute_path: str = "",
    session: Session = Depends(get_session),
):
    root = _wechat_storage_root_from_request(device_id)
    target = _normalize_wechat_storage_absolute_path(root, path, absolute_path)
    if not target.exists():
        raise HTTPException(status_code=404, detail="路径不存在")
    if not target.is_dir():
        raise HTTPException(status_code=400, detail="路径不是目录")

    from backend.api.filesystem import list_directory_items

    payload = list_directory_items(
        absolute_path=os.fspath(target.resolve(strict=False)),
        session=session,
    )
    _merge_wechat_directory_usage_stats(target, payload, session)
    return {
        "device_id": _device_id_for_root(root),
        "device_root": os.fspath(root),
        "items": payload.get("items", []),
        "current_path": payload.get("current_path", ""),
        "absolute_path": payload.get("absolute_path", ""),
    }


@router.get("/db-devices")
def list_wechat_db_devices(
    session: Session = Depends(get_session),
    current_user: User | None = Depends(get_optional_current_user_from_token),
):
    return {"items": _wechat_db_devices_payload(session, current_user)}


@router.get("/status")
def get_wechat_archive_status():
    return _archive_status_payload()


@router.get("/db-status")
def get_wechat_db_status(
    device_id: Annotated[str | None, Query(max_length=200)] = None,
    session: Session = Depends(get_session),
    current_user: User | None = Depends(get_optional_current_user_from_token),
):
    remote = _resolve_remote_wechat_device(device_id, session, current_user)
    if remote:
        status = _remote_wechat_json(remote, "/wechat-archive/db-status")
        if isinstance(status, dict):
            status["device_id"] = remote.public_device_id
            status["remote_device_id"] = remote.remote_device_id
            status["entry_id"] = remote.entry.entry_id
            status["remote"] = True
        return status
    storage = _open_wechat_db_storage(device_id)
    try:
        status = _wechat_storage_status(storage)
        status["device_id"] = _device_id_for_root(_resolve_wechat_device_root(device_id))
        return status
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"微信数据库读取失败：{exc}") from exc


@router.post("/db-sync-live")
def sync_wechat_db_from_live(
    device_id: Annotated[str | None, Query(max_length=200)] = None,
    session: Session = Depends(get_session),
    current_user: User | None = Depends(get_optional_current_user_from_token),
):
    if _resolve_remote_wechat_device(device_id, session, current_user):
        raise HTTPException(status_code=400, detail="只能同步当前节点正在运行的微信数据")
    device_root = _resolve_wechat_device_root(device_id)
    if not _is_current_device_root(device_root) and not _is_tim_account_root(device_root):
        raise HTTPException(status_code=400, detail="只能同步本机正在运行的微信数据")
    storage = _choose_wechat_storage_for_device(device_root)
    try:
        return storage.sync_from_live(export_media=True)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"微信数据库同步失败：{exc}") from exc


@router.get("/db-schema")
def get_wechat_db_schema(
    device_id: Annotated[str | None, Query(max_length=200)] = None,
    session: Session = Depends(get_session),
    current_user: User | None = Depends(get_optional_current_user_from_token),
):
    remote = _resolve_remote_wechat_device(device_id, session, current_user)
    if remote:
        payload = _remote_wechat_json(remote, "/wechat-archive/db-schema")
        if isinstance(payload, dict):
            payload["device_id"] = remote.public_device_id
            payload["remote_device_id"] = remote.remote_device_id
            payload["entry_id"] = remote.entry.entry_id
            payload["remote"] = True
        return payload
    storage = _open_wechat_db_storage(device_id)
    try:
        return {
            "items": storage.schema_overview(),
            "db_storage_path": os.fspath(storage.root),
            "device_id": _device_id_for_root(_resolve_wechat_device_root(device_id)),
        }
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"微信数据库 schema 读取失败：{exc}") from exc


@router.get("/db-chats")
def list_wechat_db_chats(
    device_id: Annotated[str | None, Query(max_length=200)] = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 500,
    offset: Annotated[int, Query(ge=0)] = 0,
    scope: Annotated[Literal["main", "folded", "all"], Query()] = "main",
    session: Session = Depends(get_session),
    current_user: User | None = Depends(get_optional_current_user_from_token),
):
    remote = _resolve_remote_wechat_device(device_id, session, current_user)
    if remote:
        payload = _remote_wechat_json(
            remote,
            "/wechat-archive/db-chats",
            params={"q": q, "limit": limit, "offset": offset, "scope": scope},
        )
        if isinstance(payload, dict):
            payload["device_id"] = remote.public_device_id
            payload["remote_device_id"] = remote.remote_device_id
            payload["entry_id"] = remote.entry.entry_id
            payload["remote"] = True
        return payload
    storage = _open_wechat_db_storage(device_id)
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
            "device_id": _device_id_for_root(_resolve_wechat_device_root(device_id)),
        }
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"微信数据库会话读取失败：{exc}") from exc


@router.get("/db-messages")
def list_wechat_db_messages(
    chat_username: Annotated[str, Query(min_length=1, max_length=200)],
    device_id: Annotated[str | None, Query(max_length=200)] = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    message_type: Annotated[str | None, Query(max_length=40)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    offset: Annotated[int, Query(ge=0)] = 0,
    order: Annotated[Literal["asc", "desc"], Query()] = "desc",
    include_resources: bool = True,
    known_total: Annotated[int | None, Query(ge=0)] = None,
    session: Session = Depends(get_session),
    current_user: User | None = Depends(get_optional_current_user_from_token),
):
    remote = _resolve_remote_wechat_device(device_id, session, current_user)
    if remote:
        payload = _remote_wechat_json(
            remote,
            "/wechat-archive/db-messages",
            params={
                "chat_username": chat_username,
                "q": q,
                "message_type": message_type,
                "limit": limit,
                "offset": offset,
                "order": order,
                "include_resources": include_resources,
                "known_total": known_total,
            },
            timeout=30,
        )
        if isinstance(payload, dict):
            payload["device_id"] = remote.public_device_id
            payload["remote_device_id"] = remote.remote_device_id
            payload["entry_id"] = remote.entry.entry_id
            payload["remote"] = True
        return payload
    storage = _open_wechat_db_storage(device_id)
    try:
        kwargs = {
            "chat_username": chat_username,
            "q": q,
            "message_type": message_type,
            "limit": limit,
            "offset": offset,
            "order": order,
            "include_resources": include_resources,
        }
        if known_total is not None and "known_total" in inspect.signature(storage.list_messages).parameters:
            kwargs["known_total"] = known_total
        payload = storage.list_messages(**kwargs)
        return {
            **payload,
            "db_storage_path": os.fspath(storage.root),
            "device_id": _device_id_for_root(_resolve_wechat_device_root(device_id)),
        }
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"微信数据库消息读取失败：{exc}") from exc


@router.get("/db-message-count")
def count_wechat_db_messages(
    chat_username: Annotated[str, Query(min_length=1, max_length=200)],
    device_id: Annotated[str | None, Query(max_length=200)] = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    message_type: Annotated[str | None, Query(max_length=40)] = None,
    session: Session = Depends(get_session),
    current_user: User | None = Depends(get_optional_current_user_from_token),
):
    remote = _resolve_remote_wechat_device(device_id, session, current_user)
    if remote:
        payload = _remote_wechat_json(
            remote,
            "/wechat-archive/db-message-count",
            params={"chat_username": chat_username, "q": q, "message_type": message_type},
        )
        if isinstance(payload, dict):
            payload["device_id"] = remote.public_device_id
            payload["remote_device_id"] = remote.remote_device_id
            payload["entry_id"] = remote.entry.entry_id
            payload["remote"] = True
        return payload
    storage = _open_wechat_db_storage(device_id)
    try:
        payload = storage.count_messages(
            chat_username=chat_username,
            q=q,
            message_type=message_type,
        )
        return {
            **payload,
            "db_storage_path": os.fspath(storage.root),
            "device_id": _device_id_for_root(_resolve_wechat_device_root(device_id)),
        }
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"微信数据库消息计数失败：{exc}") from exc


@router.get("/db-message-types")
def list_wechat_db_message_types(
    device_id: Annotated[str | None, Query(max_length=200)] = None,
    chat_username: Annotated[str | None, Query(max_length=200)] = None,
    session: Session = Depends(get_session),
    current_user: User | None = Depends(get_optional_current_user_from_token),
):
    remote = _resolve_remote_wechat_device(device_id, session, current_user)
    if remote:
        return _remote_wechat_json(
            remote,
            "/wechat-archive/db-message-types",
            params={"chat_username": chat_username},
        )
    storage = _open_wechat_db_storage(device_id)
    try:
        return {"items": storage.message_types(chat_username=chat_username)}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"微信数据库消息类型读取失败：{exc}") from exc


@router.get("/db-media/{kind}/{file_name}")
def get_wechat_db_media_file(
    kind: Literal["image", "video", "file"],
    file_name: str,
    device_id: Annotated[str | None, Query(max_length=200)] = None,
    session: Session = Depends(get_session),
    current_user: User | None = Depends(get_optional_current_user_from_token),
):
    remote = _resolve_remote_wechat_device(device_id, session, current_user)
    if remote:
        return _remote_wechat_stream(remote, f"/wechat-archive/db-media/{kind}/{file_name}")
    for root_candidate in _local_wechat_media_roots(device_id, kind):
        root = root_candidate.resolve()
        path = (root / file_name).resolve()
        if str(path).startswith(str(root)) and path.exists():
            return FileResponse(path, filename=file_name)
    raise HTTPException(status_code=404, detail="资源文件不存在或尚未导出")


@router.get("/db-tables")
def list_wechat_db_tables(
    database: Annotated[str, Query(min_length=1, max_length=40)],
    device_id: Annotated[str | None, Query(max_length=200)] = None,
    session: Session = Depends(get_session),
    current_user: User | None = Depends(get_optional_current_user_from_token),
):
    remote = _resolve_remote_wechat_device(device_id, session, current_user)
    if remote:
        return _remote_wechat_json(remote, "/wechat-archive/db-tables", params={"database": database})
    storage = _open_wechat_db_storage(device_id)
    try:
        return {"items": storage.list_tables(database)}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"微信数据库表读取失败：{exc}") from exc


@router.get("/db-table-rows")
def list_wechat_db_table_rows(
    database: Annotated[str, Query(min_length=1, max_length=40)],
    table: Annotated[str, Query(min_length=1, max_length=120)],
    device_id: Annotated[str | None, Query(max_length=200)] = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    offset: Annotated[int, Query(ge=0)] = 0,
    session: Session = Depends(get_session),
    current_user: User | None = Depends(get_optional_current_user_from_token),
):
    remote = _resolve_remote_wechat_device(device_id, session, current_user)
    if remote:
        return _remote_wechat_json(
            remote,
            "/wechat-archive/db-table-rows",
            params={"database": database, "table": table, "q": q, "limit": limit, "offset": offset},
            timeout=30,
        )
    storage = _open_wechat_db_storage(device_id)
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
    if payload.mode in {"history", "history_clearance", "full"}:
        raise HTTPException(status_code=410, detail="旧版微信 GUI 采集同步已停用，请使用纯数据库同步。")
    chat_names = _chat_names_from_payload(payload)
    if payload.mode == "latest" and payload.chat_name and not chat_names:
        raise HTTPException(status_code=400, detail="会话名不能为空")

    task_payload = payload.model_dump()
    task_payload["chat_names"] = chat_names
    task_id = _enqueue_wechat_db_live_sync({
        "mode": "db_storage_live",
        "requested_mode": payload.mode,
        "chat_names": chat_names,
        "save_media": payload.save_media,
    })
    return {
        "queued": True,
        "queue_task_id": task_id,
        "task_name": WECHAT_ARCHIVE_SYNC_TASK_NAME,
        "sync_status": get_wechat_archive_sync_status(),
    }


@router.post("/import")
def import_wechat_archive(payload: WeChatArchiveImportRequest):
    raise HTTPException(status_code=410, detail="旧版微信 GUI 导入已停用，请使用纯数据库同步。")
