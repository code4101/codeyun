from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import sqlite3
import time
from typing import Any
from urllib.parse import urlparse
import xml.etree.ElementTree as ET

import requests
from sqlalchemy.engine import Engine
from sqlmodel import SQLModel, Session, select

from backend.core.devices.device import get_device_id
from backend.db import engine
from backend.models import WeChatMoment


WECHAT_MOMENTS_LOCAL_JOB_TYPE = "archive.wechat-moments"
DEFAULT_MEDIA_PREVIEW_LIMIT = 100
MAX_MEDIA_PREVIEW_BYTES = 25 * 1024 * 1024
MEDIA_PREVIEW_RETRY_SECONDS = 24 * 60 * 60
_ALLOWED_MEDIA_DOMAIN_SUFFIXES = (
    ".qq.com",
    ".qpic.cn",
    ".gtimg.com",
    ".wechat.com",
    ".weixin.qq.com",
)


class WeChatMomentsError(RuntimeError):
    pass


@dataclass
class _MediaBudget:
    remaining: int
    archived: int = 0
    failed: int = 0


def _text(node: ET.Element | None, path: str, default: str = "") -> str:
    if node is None:
        return default
    return str(node.findtext(path) or default).strip()


def _int_text(node: ET.Element | None, path: str, default: int = 0) -> int:
    try:
        return int(_text(node, path, str(default)))
    except (TypeError, ValueError):
        return default


def _unsigned_tid(value: Any) -> str:
    tid = int(value or 0)
    return str(tid if tid >= 0 else tid + (1 << 64))


def _xml_payload(node: ET.Element | None) -> dict[str, Any]:
    if node is None:
        return {}
    payload: dict[str, Any] = {}
    if node.attrib:
        payload["@attributes"] = dict(node.attrib)
    node_text = str(node.text or "").strip()
    if node_text:
        payload["#text"] = node_text
    for child in node:
        value: Any = _xml_payload(child) if list(child) or child.attrib else str(child.text or "")
        existing = payload.get(child.tag)
        if existing is None:
            payload[child.tag] = value
        elif isinstance(existing, list):
            existing.append(value)
        else:
            payload[child.tag] = [existing, value]
    return payload


def _comment_payload(node: ET.Element) -> dict[str, Any]:
    keys = (
        "comment_id", "comment_64id", "username", "nickname", "content",
        "create_time", "ref_comment_id", "ref_comment_64id", "ref_username",
        "b_deleted", "type",
    )
    return {key: _text(node, key) for key in keys if _text(node, key)}


def _merge_items(old: list[dict[str, Any]], new: list[dict[str, Any]], keys: tuple[str, ...]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for item in [*old, *new]:
        if not isinstance(item, dict):
            continue
        identity = next((str(item.get(key) or "") for key in keys if item.get(key)), "")
        if not identity:
            identity = hashlib.sha256(repr(sorted(item.items())).encode("utf-8")).hexdigest()
        merged[identity] = {**merged.get(identity, {}), **item}
    return list(merged.values())


def _parse_timeline_row(tid: int, user_name: str, raw_xml: str) -> dict[str, Any]:
    root = ET.fromstring(raw_xml)
    timeline = root.find("./TimelineObject")
    if timeline is None:
        raise WeChatMomentsError("SnsTimeLine 缺少 TimelineObject")
    local = root.find("./LocalExtraInfo")
    content_object = timeline.find("./ContentObject")
    moment_id = _text(timeline, "id") or _unsigned_tid(tid)
    return {
        "moment_id": moment_id,
        "source_tid": str(tid),
        "author_username": _text(timeline, "username") or str(user_name or ""),
        "author_nickname": _text(local, "nickname"),
        "published_at": _int_text(timeline, "createTime"),
        "content_available": True,
        "content_type": _int_text(content_object, "type"),
        "content_text": _text(timeline, "contentDesc"),
        "title": _text(content_object, "title"),
        "description": _text(content_object, "description"),
        "content_url": _text(content_object, "contentUrl"),
        "location_json": _xml_payload(timeline.find("./location")),
        "media_json": [_xml_payload(node) for node in timeline.findall("./ContentObject/mediaList/media")],
        "likes_json": [_comment_payload(node) for node in root.findall("./LocalExtraInfo/like_user_list/user_comment")],
        "comments_json": [_comment_payload(node) for node in root.findall("./LocalExtraInfo/comment_user_list/user_comment")],
        "raw_xml": raw_xml,
        "source_content_hash": hashlib.sha256(raw_xml.encode("utf-8")).hexdigest(),
    }


def _load_source_records(sns_db_path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    if not sns_db_path.exists():
        raise WeChatMomentsError(f"微信朋友圈数据库不存在：{sns_db_path}")
    conn = sqlite3.connect(f"file:{sns_db_path.resolve().as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    records: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    try:
        table_names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "SnsTopItem_1" in table_names:
            for row in conn.execute(
                "SELECT tid, username, summary, create_time, last_read_time, is_read FROM SnsTopItem_1"
            ):
                moment_id = _unsigned_tid(row["tid"])
                candidate = {
                    "moment_id": moment_id, "source_tid": str(row["tid"]),
                    "author_username": str(row["username"] or ""), "author_nickname": "",
                    "published_at": int(row["create_time"] or 0), "content_available": False,
                    "content_type": 0, "content_text": str(row["summary"] or ""),
                    "title": "", "description": "", "content_url": "", "location_json": {},
                    "media_json": [], "likes_json": [], "comments_json": [], "raw_xml": "",
                    "source_content_hash": "",
                    "source_is_read": bool(row["is_read"]) if row["is_read"] is not None else None,
                    "source_last_read_at": int(row["last_read_time"] or 0) or None,
                }
                previous = records.get(moment_id)
                if previous is None or candidate["published_at"] >= previous["published_at"]:
                    records[moment_id] = candidate
        if "SnsTimeLine" not in table_names:
            raise WeChatMomentsError("sns.db 缺少 SnsTimeLine")
        for row in conn.execute("SELECT tid, user_name, content FROM SnsTimeLine WHERE content IS NOT NULL"):
            try:
                detailed = _parse_timeline_row(row["tid"], row["user_name"], row["content"])
            except Exception as exc:
                errors.append(f"tid={row['tid']}: {type(exc).__name__}: {exc}")
                continue
            previous = records.get(detailed["moment_id"]) or {}
            detailed["source_is_read"] = previous.get("source_is_read")
            detailed["source_last_read_at"] = previous.get("source_last_read_at")
            records[detailed["moment_id"]] = detailed
    finally:
        conn.close()
    return sorted(records.values(), key=lambda item: (int(item["published_at"]), item["moment_id"]), reverse=True), errors


def _archive_xml(archive_root: Path, record: dict[str, Any]) -> str:
    raw_xml = str(record.get("raw_xml") or "")
    digest = str(record.get("source_content_hash") or "")
    if not raw_xml or not digest:
        return ""
    relative = Path("moments") / str(record["moment_id"]) / f"{digest}.xml"
    target = archive_root / relative
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".xml.writing")
        temporary.write_text(raw_xml, encoding="utf-8")
        temporary.replace(target)
    return relative.as_posix()


def _allowed_media_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
    except ValueError:
        return False
    host = str(parsed.hostname or "").lower()
    return parsed.scheme in {"http", "https"} and any(
        host == suffix[1:] or host.endswith(suffix) for suffix in _ALLOWED_MEDIA_DOMAIN_SUFFIXES
    )


def _media_urls(media: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    for key in ("thumb", "thumbUrl", "coverUrl", "fullCoverUrl", "url"):
        value = media.get(key)
        values = value if isinstance(value, list) else [value]
        for item in values:
            if isinstance(item, dict):
                item = item.get("#text") or item.get("text")
            text = str(item or "").strip()
            if text and _allowed_media_url(text) and text not in urls:
                urls.append(text)
    return urls


def _image_extension(content_type: str, prefix: bytes) -> str | None:
    normalized = content_type.split(";", 1)[0].strip().lower()
    if prefix.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if prefix.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if prefix.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if prefix.startswith(b"RIFF") and prefix[8:12] == b"WEBP":
        return ".webp"
    return {"image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif", "image/webp": ".webp"}.get(normalized)


def _download_media_preview(
    http: requests.Session, archive_root: Path, moment_id: str, index: int, media: dict[str, Any]
) -> dict[str, Any] | None:
    for url in _media_urls(media):
        try:
            response = http.get(url, stream=True, timeout=(5, 20), allow_redirects=False)
        except requests.RequestException:
            continue
        try:
            if response.status_code != 200:
                continue
            declared = int(response.headers.get("content-length") or 0)
            if declared > MAX_MEDIA_PREVIEW_BYTES:
                continue
            chunks: list[bytes] = []
            size = 0
            prefix = b""
            for chunk in response.iter_content(64 * 1024):
                if not chunk:
                    continue
                if not prefix:
                    prefix = chunk[:16]
                size += len(chunk)
                if size > MAX_MEDIA_PREVIEW_BYTES:
                    chunks = []
                    break
                chunks.append(chunk)
            if not chunks:
                continue
            extension = _image_extension(str(response.headers.get("content-type") or ""), prefix)
            if extension is None:
                continue
            data = b"".join(chunks)
            digest = hashlib.sha256(data).hexdigest()
            relative = Path("media") / moment_id / f"{index:02d}-{digest[:16]}{extension}"
            target = archive_root / relative
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary = target.with_suffix(target.suffix + ".writing")
                temporary.write_bytes(data)
                temporary.replace(target)
            return {
                "path": relative.as_posix(), "sha256": digest, "size": len(data),
                "content_type": str(response.headers.get("content-type") or ""), "source_url": url,
            }
        finally:
            response.close()
    return None


def _archive_media(
    archive_root: Path, moment_id: str, media_items: list[dict[str, Any]],
    old_items: list[dict[str, Any]], budget: _MediaBudget, http: requests.Session,
) -> list[dict[str, Any]]:
    old_by_id: dict[str, dict[str, Any]] = {}
    for item in old_items:
        if isinstance(item, dict):
            identity = str(item.get("id") or "") or next(iter(_media_urls(item)), "")
            if identity:
                old_by_id[identity] = item
    result: list[dict[str, Any]] = []
    for index, media in enumerate(media_items):
        item = dict(media)
        identity = str(item.get("id") or "") or next(iter(_media_urls(item)), "")
        previous = old_by_id.get(identity) or {}
        archived = previous.get("archived_preview") if isinstance(previous, dict) else None
        previous_attempt = previous.get("archive_attempt") if isinstance(previous, dict) else None
        archived_path = archive_root / str((archived or {}).get("path") or "") if archived else None
        if archived and archived_path and archived_path.is_file():
            item["archived_preview"] = archived
        elif (
            isinstance(previous_attempt, dict)
            and previous_attempt.get("status") == "failed"
            and float(previous_attempt.get("attempted_at") or 0) > time.time() - MEDIA_PREVIEW_RETRY_SECONDS
        ):
            item["archive_attempt"] = previous_attempt
        elif budget.remaining > 0:
            budget.remaining -= 1
            downloaded = _download_media_preview(http, archive_root, moment_id, index, item)
            if downloaded:
                item["archived_preview"] = downloaded
                budget.archived += 1
            else:
                budget.failed += 1
                item["archive_attempt"] = {
                    "status": "failed",
                    "attempted_at": time.time(),
                }
        result.append(item)
    return result


def ensure_wechat_moments_schema(db_engine: Engine = engine) -> None:
    SQLModel.metadata.create_all(db_engine, tables=[WeChatMoment.__table__])


def ingest_wechat_moments(
    *, db_storage_root: str | os.PathLike[str], account_key: str,
    db_engine: Engine = engine, device_id: str | None = None,
    download_media: bool = True, media_preview_limit: int = DEFAULT_MEDIA_PREVIEW_LIMIT,
) -> dict[str, Any]:
    root = Path(db_storage_root)
    sns_db_path = root / "sns" / "sns.db"
    archive_root = root.parent / "sns_archive"
    records, parse_errors = _load_source_records(sns_db_path)
    ensure_wechat_moments_schema(db_engine)
    now = time.time()
    resolved_device_id = str(device_id or get_device_id())
    normalized_account = str(account_key or "").strip()
    if not normalized_account:
        raise WeChatMomentsError("朋友圈归档缺少微信账号标识")
    inserted = updated = unchanged = detailed = 0
    budget = _MediaBudget(max(0, int(media_preview_limit if download_media else 0)))
    http = requests.Session()
    http.headers.update({"User-Agent": "Mozilla/5.0 CodeYun-WeChatMoments/1.0"})
    try:
        with Session(db_engine) as session:
            for source in records:
                row_id = hashlib.sha256(f"{normalized_account}\0{source['moment_id']}".encode("utf-8")).hexdigest()
                row = session.get(WeChatMoment, row_id)
                is_new = row is None
                if row is None:
                    row = WeChatMoment(id=row_id, account_key=normalized_account, moment_id=source["moment_id"])
                old_hash = row.source_content_hash
                old_media = list(row.media_json or [])
                if source["content_available"]:
                    detailed += 1
                    row.content_available = True
                    row.content_type = int(source["content_type"])
                    row.content_text = source["content_text"]
                    row.title = source["title"]
                    row.description = source["description"]
                    row.content_url = source["content_url"]
                    row.location_json = source["location_json"]
                    row.media_json = _archive_media(
                        archive_root, source["moment_id"], list(source["media_json"]), old_media, budget, http
                    )
                    row.likes_json = _merge_items(
                        list(row.likes_json or []), source["likes_json"], ("comment_64id", "comment_id", "username")
                    )
                    row.comments_json = _merge_items(
                        list(row.comments_json or []), source["comments_json"], ("comment_64id", "comment_id")
                    )
                    row.source_archive_path = _archive_xml(archive_root, source)
                    row.source_content_hash = source["source_content_hash"]
                elif not row.content_available:
                    row.content_text = source["content_text"]
                row.device_id = resolved_device_id
                row.source_tid = source["source_tid"]
                row.author_username = source["author_username"] or row.author_username
                row.author_nickname = source["author_nickname"] or row.author_nickname
                row.published_at = int(source["published_at"] or row.published_at)
                row.source_is_read = source.get("source_is_read")
                row.source_last_read_at = source.get("source_last_read_at")
                row.last_seen_at = now
                if is_new:
                    row.first_seen_at = now
                    row.created_at = now
                    inserted += 1
                elif old_hash != row.source_content_hash or row.media_json != old_media:
                    updated += 1
                else:
                    unchanged += 1
                row.updated_at = now
                session.add(row)
            session.commit()
            total = len(session.exec(select(WeChatMoment).where(WeChatMoment.account_key == normalized_account)).all())
    finally:
        http.close()
    return {
        "source_db": os.fspath(sns_db_path), "archive_root": os.fspath(archive_root),
        "source_records": len(records), "detailed_records": detailed,
        "inserted": inserted, "updated": updated, "unchanged": unchanged,
        "total_records": total, "media_previews_archived": budget.archived,
        "media_preview_failures": budget.failed, "parse_error_count": len(parse_errors),
        "parse_errors": parse_errors[:20],
    }


def run_wechat_moments_sync(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    from backend.api.wechat_archive import _open_wechat_db_storage

    options = dict(payload or {})
    storage = _open_wechat_db_storage()
    source_sync = storage.sync_from_live(export_media=False)
    account_root = Path(str(source_sync.get("live_account_root") or ""))
    account_key = str(options.get("account_key") or account_root.name).strip()
    result = ingest_wechat_moments(
        db_storage_root=storage.root, account_key=account_key,
        download_media=bool(options.get("download_media", True)),
        media_preview_limit=int(options.get("media_preview_limit", DEFAULT_MEDIA_PREVIEW_LIMIT)),
    )
    return {"source_sync": source_sync, "moments": result}
