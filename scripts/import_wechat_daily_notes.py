from __future__ import annotations

import argparse
import hashlib
import html
from html.parser import HTMLParser
import json
import re
import sqlite3
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from sqlmodel import Session, select

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.api.wechat_archive import _settings_wechat_db_storage_path
from backend.core.ai_app_config import AI_APP_WECHAT_DAILY_SUMMARY, resolve_ai_app_runtime_config
from backend.core.ai_chat import OllamaClientError, chat_with_provider
from backend.core.note_identity import allocate_new_note_identity
from backend.core.note_progress import (
    NOTE_COMPLETION_PROGRESS_EXPR_FIELD,
    get_custom_field_value,
)
from backend.core.note_semantics import (
    NOTE_CATEGORY_DEFAULT,
    NOTE_FORM_DOCUMENT,
    NOTE_SCENE_DEFAULT,
    derive_legacy_semantics_from_taxonomy,
)
from backend.db import engine
from backend.models import NoteNode, User


CN_TZ = timezone(timedelta(hours=8))
FIELD_SOURCE = "wechat_daily_source"
FIELD_DATE = "wechat_daily_date"
FIELD_CHAT_USERNAME = "wechat_daily_chat_username"
FIELD_CHAT_NAME = "wechat_daily_chat_name"
FIELD_CHAT_TYPE = "wechat_daily_chat_type"
FIELD_MESSAGE_COUNT = "wechat_daily_message_count"
FIELD_TEXT_COUNT = "wechat_daily_text_count"
FIELD_NON_TEXT_COUNT = "wechat_daily_non_text_count"
FIELD_TEXT_CHARS = "wechat_daily_text_chars"
FIELD_WEIGHTED_CONTENT_UNITS = "wechat_daily_weighted_content_units"
FIELD_START_TIME = "wechat_daily_start_time"
FIELD_END_TIME = "wechat_daily_end_time"
FIELD_SUMMARY_SOURCE = "wechat_daily_summary_source"
FIELD_SUMMARY_MODEL = "wechat_daily_summary_model"
FIELD_RESOURCE_COUNT = "wechat_daily_resource_count"
FIELD_IMAGE_OCR_COUNT = "wechat_daily_image_ocr_count"

CHAT_PREFIX_RE = re.compile(r"^(?:wxid_[A-Za-z0-9_-]+|gh_[A-Za-z0-9_-]+|[^:\n]{1,80}):\n")
WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_+.#/-]{1,}|[\u4e00-\u9fff]{2,12}")
XMLISH_RE = re.compile(r"^\s*<\?xml|^\s*<msg\b|^\s*<appmsg\b", re.I)
HEX32_RE = re.compile(r"[0-9a-fA-F]{32}")
STOP_WORDS = {
    "这个",
    "那个",
    "不是",
    "还是",
    "就是",
    "可以",
    "已经",
    "然后",
    "现在",
    "一下",
    "还有",
    "因为",
    "所以",
    "如果",
    "没有",
    "感觉",
    "应该",
    "可能",
    "需要",
    "比较",
    "直接",
    "今天",
    "昨天",
    "明天",
    "哈哈",
    "好像",
    "这里",
    "那边",
}


@dataclass
class DayChatSummary:
    username: str
    name: str
    table_name: str
    chat_type: str
    message_count: int
    text_count: int
    non_text_count: int
    text_chars: int
    weighted_content_units: float
    first_ts: int
    last_ts: int
    keywords: list[str]
    snippets: list[tuple[int, str, str]]
    transcript: list[tuple[int, str, str]]
    resource_count: int = 0
    image_ocr_count: int = 0


@dataclass
class AiSummaryDraft:
    summary: list[str]
    notes: list[str]
    source: str
    model: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import one day of WeChat chats as Star Map note documents.")
    parser.add_argument("--date", required=True, help="Date in YYYY-MM-DD, interpreted in Asia/Shanghai.")
    parser.add_argument("--user", default="code4101", help="CodeYun username to own created notes.")
    parser.add_argument("--db-storage", default="", help="Override decrypted WeChat db_storage path.")
    parser.add_argument("--min-messages", type=int, default=1, help="Skip chats with fewer messages.")
    parser.add_argument("--limit", type=int, default=0, help="Import at most N chats after filtering.")
    parser.add_argument("--chat", default="", help="Only import chats whose name, username or table contains this text.")
    parser.add_argument("--ai", dest="use_ai", action="store_true", default=True, help="Use AI to write summaries.")
    parser.add_argument("--no-ai", dest="use_ai", action="store_false", help="Use deterministic fallback only.")
    parser.add_argument("--ai-provider", default="", help="Override AI provider, defaults to app config.")
    parser.add_argument("--ai-model", default="", help="Override AI model, defaults to app config.")
    parser.add_argument("--ai-timeout", type=float, default=900.0, help="AI call timeout seconds per chat.")
    parser.add_argument("--ai-limit-chars", type=int, default=30000, help="Max transcript characters sent per chat.")
    parser.add_argument("--include-resource-context", action="store_true", default=True, help="Attach file/image resource evidence.")
    parser.add_argument("--no-resource-context", dest="include_resource_context", action="store_false")
    parser.add_argument("--ocr-images", action="store_true", default=True, help="OCR exported images and include OCR text as evidence.")
    parser.add_argument("--no-ocr-images", dest="ocr_images", action="store_false")
    parser.add_argument("--dry-run", action="store_true", help="Only print planned note summaries.")
    return parser.parse_args()


def parse_day_range(day: str) -> tuple[int, int, datetime]:
    date_value = datetime.strptime(day, "%Y-%m-%d").date()
    start_dt = datetime.combine(date_value, datetime.min.time(), tzinfo=CN_TZ)
    end_dt = start_dt + timedelta(days=1)
    return int(start_dt.timestamp()), int(end_dt.timestamp()), start_dt


def format_ts(ts: int, fmt: str = "%H:%M") -> str:
    return datetime.fromtimestamp(int(ts), CN_TZ).strftime(fmt)


def quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def strip_sender_prefix(text: str) -> str:
    return CHAT_PREFIX_RE.sub("", text, count=1).strip()


def normalize_message_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = strip_sender_prefix(value).replace("\r\n", "\n").strip()
    if not text or XMLISH_RE.match(text):
        return None
    return text


def get_sender_weight(status: Any) -> float:
    try:
        status_value = int(status)
    except (TypeError, ValueError):
        return 0.5
    return 1.0 if status_value == 2 else 0.5


def get_sender_label(status: Any) -> str:
    try:
        status_value = int(status)
    except (TypeError, ValueError):
        return "对方"
    return "我" if status_value == 2 else "对方"


def brief_text(text: str, limit: int = 90) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "..."


def iter_message_dbs(db_storage: Path) -> Iterable[Path]:
    message_dir = db_storage / "message"
    for path in sorted(message_dir.glob("message_*.db")):
        if path.name == "message_fts.db":
            continue
        yield path


def load_chats(db_storage: Path) -> list[dict[str, Any]]:
    from pyxllib.autogui.wechat_db import WeChatDbStorage

    storage = WeChatDbStorage(db_storage)
    return storage.list_chats(limit=20000, include_folded_entry=True)


def build_storage(db_storage: Path):
    from pyxllib.autogui.wechat_db import WeChatDbStorage

    return WeChatDbStorage(db_storage)


def table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (table_name,),
    ).fetchone()
    return row is not None


def select_representative_snippets(texts: list[tuple[int, str, str]], limit: int = 8) -> list[tuple[int, str, str]]:
    if len(texts) <= limit:
        return [(ts, sender, brief_text(text)) for ts, sender, text in texts]

    score_items: list[tuple[float, int]] = []
    total = len(texts)
    for index, (_ts, sender, text) in enumerate(texts):
        time_bucket = index * limit // total
        bucket_center = (time_bucket + 0.5) * total / limit
        time_score = 1 - min(1, abs(index - bucket_center) / max(1, total / limit))
        sender_score = 0.45 if sender == "我" else 0
        length_score = min(0.25, len(text) / 240)
        score_items.append((time_score + sender_score + length_score, index))

    selected = sorted(index for _score, index in sorted(score_items, reverse=True)[:limit])
    return [(texts[index][0], texts[index][1], brief_text(texts[index][2])) for index in selected]


def extract_keywords(texts: list[str], limit: int = 6) -> list[str]:
    counts: dict[str, int] = {}
    for text in texts:
        for match in WORD_RE.finditer(text):
            word = match.group(0).strip().lower()
            if len(word) < 2 or word in STOP_WORDS:
                continue
            if "\u4e00" <= word[0] <= "\u9fff" and len(word) > 8:
                continue
            counts[word] = counts.get(word, 0) + 1
    ranked = sorted(counts.items(), key=lambda item: (-item[1], len(item[0]), item[0]))
    return [word for word, _ in ranked[:limit]]


def _resource_cache_dir(db_storage: Path) -> Path:
    path = db_storage.parent / "analysis_cache" / "image_ocr"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _file_sha1(path: Path) -> str:
    h = hashlib.sha1()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _ocr_result_text(result: Any) -> str:
    payload = result.json.get("res") if hasattr(result, "json") else result
    if isinstance(payload, dict):
        texts = payload.get("rec_texts") or payload.get("texts") or []
    else:
        texts = []
    if not isinstance(texts, list):
        return ""
    return " ".join(str(item).strip() for item in texts if str(item).strip())


def ocr_image_cached(image_path: Path, cache_dir: Path) -> str:
    if not image_path.exists() or image_path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp", ".bmp"}:
        return ""
    try:
        key = _file_sha1(image_path)
    except OSError:
        return ""
    cache_path = cache_dir / f"{key}.json"
    if cache_path.exists():
        try:
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
            return str(payload.get("text") or "").strip()
        except (OSError, json.JSONDecodeError):
            pass
    try:
        from pyxllib.ai.ocr import ocr_text

        text = _ocr_result_text(ocr_text(image_path))
    except Exception as exc:  # OCR is optional evidence; do not block note import.
        text = ""
        error = str(exc)
    else:
        error = ""
    payload = {
        "image_path": os_fspath(image_path),
        "text": text,
        "error": error,
        "updated_at": time.time(),
    }
    try:
        cache_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass
    return text


def os_fspath(path: Path) -> str:
    return str(path)


def _packed_ascii(value: Any) -> str:
    if isinstance(value, bytes):
        return "".join(chr(b) if 32 <= b < 127 else " " for b in value)
    return str(value or "")


def _resource_kind(message_local_type: Any, detail_type: Any, item: dict[str, Any] | None) -> str:
    if item and item.get("kind"):
        return str(item["kind"])
    try:
        local_type = int(message_local_type)
    except (TypeError, ValueError):
        local_type = 0
    if local_type == 3:
        return "image"
    if local_type == 43:
        return "video"
    if local_type == 25769803825:
        return "file"
    try:
        rtype = int(detail_type)
    except (TypeError, ValueError):
        rtype = 0
    if rtype in {65539, 1507331}:
        return "file"
    if rtype in {65537, 131073, 262145}:
        return "image"
    if rtype in {65538, 131074, 196610}:
        return "video"
    return "resource"


def _resource_display_name(row: sqlite3.Row, item: dict[str, Any] | None) -> str:
    if item:
        return str(item.get("original_file_name") or item.get("file_name") or "").strip()
    ascii_text = _packed_ascii(row["packed_info"])
    names = [part.strip() for part in re.split(r"\s{2,}|\x00", ascii_text) if part.strip()]
    return names[0] if names else ""


def _resolve_exported_resource(row: sqlite3.Row, exported: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    ascii_text = _packed_ascii(row["packed_info"])
    for md5 in HEX32_RE.findall(ascii_text):
        item = exported.get(md5.lower()) or exported.get(md5)
        if item:
            return item
    name = _resource_display_name(row, None)
    if name:
        item = exported.get(name)
        if item:
            return item
    try:
        size = int(row["size"] or 0)
    except (TypeError, ValueError):
        size = 0
    if size:
        return exported.get(f"size:{size}") or exported.get(f"size:{size + 31}") or exported.get(f"size:{size - 31}")
    return None


def load_resource_contexts(
    db_storage: Path,
    chats: list[dict[str, Any]],
    start_ts: int,
    end_ts: int,
    *,
    include_resource_context: bool,
    ocr_images: bool,
) -> tuple[dict[tuple[str, int], list[str]], dict[tuple[str, int], dict[str, int]]]:
    if not include_resource_context:
        return {}, {}
    resource_db = db_storage / "message" / "message_resource.db"
    if not resource_db.exists():
        return {}, {}

    storage = build_storage(db_storage)
    exported = storage._export_resource_files(decode_missing=ocr_images)
    ocr_cache_dir = _resource_cache_dir(db_storage)
    chat_by_username = {str(chat.get("username") or ""): chat for chat in chats}
    contexts: dict[tuple[str, int], list[str]] = {}
    stats: dict[tuple[str, int], dict[str, int]] = {}

    with sqlite3.connect(resource_db) as conn:
        conn.row_factory = sqlite3.Row
        chat_rows = conn.execute("SELECT rowid, user_name FROM ChatName2Id").fetchall()
        id_to_username = {
            int(row["rowid"]): str(row["user_name"] or "")
            for row in chat_rows
            if str(row["user_name"] or "") in chat_by_username
        }
        if not id_to_username:
            return {}, {}
        placeholders = ",".join("?" for _ in id_to_username)
        rows = conn.execute(
            f"""
            SELECT i.chat_id, i.message_local_id, i.message_local_type, i.message_create_time,
                   d.type, d.size, d.packed_info
            FROM MessageResourceInfo i
            LEFT JOIN MessageResourceDetail d ON d.message_id = i.message_id
            WHERE i.chat_id IN ({placeholders})
              AND i.message_create_time >= ?
              AND i.message_create_time < ?
            ORDER BY i.message_create_time, i.message_local_id
            """,
            (*id_to_username.keys(), start_ts, end_ts),
        ).fetchall()

    seen_resource: set[tuple[str, int, str, int, str]] = set()
    for row in rows:
        username = id_to_username.get(int(row["chat_id"]))
        if not username:
            continue
        local_id = int(row["message_local_id"])
        key = (username, local_id)
        item = _resolve_exported_resource(row, exported)
        kind = _resource_kind(row["message_local_type"], row["type"], item)
        name = _resource_display_name(row, item)
        size = int(row["size"] or (item or {}).get("size") or 0)
        stored_path = Path(str(item.get("stored_path") or "")) if item else None
        identity = (username, local_id, kind, size, name)
        if identity in seen_resource:
            continue
        seen_resource.add(identity)
        stats.setdefault(key, {"resource_count": 0, "image_ocr_count": 0})
        stats[key]["resource_count"] += 1

        pieces = [f"{kind}"]
        if name:
            pieces.append(f"name={name}")
        if size:
            pieces.append(f"size={size}")
        if item and item.get("download_name"):
            pieces.append(f"path={item['download_name']}")

        if kind == "image" and ocr_images and stored_path and stored_path.exists():
            ocr_text_value = ocr_image_cached(stored_path, ocr_cache_dir)
            if ocr_text_value:
                stats[key]["image_ocr_count"] += 1
                pieces.append(f"OCR={brief_text(ocr_text_value, 240)}")
        contexts.setdefault(key, []).append("[资源证据] " + "; ".join(pieces))
    return contexts, stats


def collect_day_summaries(
    db_storage: Path,
    day: str,
    min_messages: int,
    *,
    include_resource_context: bool = True,
    ocr_images: bool = True,
) -> list[DayChatSummary]:
    start_ts, end_ts, _ = parse_day_range(day)
    chats = load_chats(db_storage)
    resource_contexts, resource_stats = load_resource_contexts(
        db_storage,
        chats,
        start_ts,
        end_ts,
        include_resource_context=include_resource_context,
        ocr_images=ocr_images,
    )
    summaries: list[DayChatSummary] = []

    for db_path in iter_message_dbs(db_storage):
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            for chat in chats:
                table_name = str(chat.get("table_name") or "").strip()
                if not table_name or not table_exists(conn, table_name):
                    continue
                rows = conn.execute(
                    f"""
                    SELECT local_id, create_time, status, local_type, message_content
                    FROM {quote_ident(table_name)}
                    WHERE create_time >= ? AND create_time < ?
                    ORDER BY create_time ASC, local_id ASC
                    """,
                    (start_ts, end_ts),
                ).fetchall()
                if len(rows) < min_messages:
                    continue

                texts_with_time: list[tuple[int, str, str]] = []
                transcript: list[tuple[int, str, str]] = []
                text_values: list[str] = []
                non_text_count = 0
                resource_count = 0
                image_ocr_count = 0
                weighted_content_units = 0.0
                first_ts = int(rows[0]["create_time"])
                last_ts = int(rows[-1]["create_time"])

                for row in rows:
                    sender_weight = get_sender_weight(row["status"])
                    text = normalize_message_text(row["message_content"])
                    resource_key = (str(chat.get("username") or table_name), int(row["local_id"]))
                    row_resource_contexts = resource_contexts.get(resource_key) or []
                    row_resource_stats = resource_stats.get(resource_key) or {}
                    resource_count += int(row_resource_stats.get("resource_count") or 0)
                    image_ocr_count += int(row_resource_stats.get("image_ocr_count") or 0)
                    if text is None:
                        non_text_count += 1
                        weighted_content_units += 24 * sender_weight
                        resource_text = "；".join(row_resource_contexts)
                        transcript.append(
                            (
                                int(row["create_time"]),
                                get_sender_label(row["status"]),
                                resource_text or f"[资源/非文本消息 local_type={row['local_type']}]",
                            )
                        )
                        continue
                    ts = int(row["create_time"])
                    sender_label = get_sender_label(row["status"])
                    texts_with_time.append((ts, sender_label, text))
                    transcript.append((ts, sender_label, text))
                    for resource_text in row_resource_contexts:
                        transcript.append((ts, sender_label, resource_text))
                    text_values.append(text)
                    weighted_content_units += max(len(text), 4) * sender_weight

                summaries.append(
                    DayChatSummary(
                        username=str(chat.get("username") or table_name),
                        name=str(chat.get("name") or chat.get("username") or table_name),
                        table_name=table_name,
                        chat_type=str(chat.get("chat_type") or ""),
                        message_count=len(rows),
                        text_count=len(texts_with_time),
                        non_text_count=non_text_count,
                        text_chars=sum(len(text) for text in text_values),
                        weighted_content_units=round(weighted_content_units, 2),
                        first_ts=first_ts,
                        last_ts=last_ts,
                        keywords=extract_keywords(text_values),
                        snippets=select_representative_snippets(texts_with_time),
                        transcript=transcript,
                        resource_count=resource_count,
                        image_ocr_count=image_ocr_count,
                    )
                )

    summaries.sort(key=lambda item: (item.first_ts, -item.message_count, item.name))
    return summaries


def custom_fields(
    summary: DayChatSummary,
    day: str,
    progress_expr: str,
    summary_draft: AiSummaryDraft | None = None,
) -> list[list[Any]]:
    summary_source = summary_draft.source if summary_draft else "rules"
    summary_model = summary_draft.model if summary_draft else ""
    return [
        [FIELD_SOURCE, "string", "mf:v4_db_storage"],
        [FIELD_DATE, "string", day],
        [FIELD_CHAT_USERNAME, "string", summary.username],
        [FIELD_CHAT_NAME, "string", summary.name],
        [FIELD_CHAT_TYPE, "string", summary.chat_type],
        [FIELD_MESSAGE_COUNT, "number", summary.message_count],
        [FIELD_TEXT_COUNT, "number", summary.text_count],
        [FIELD_NON_TEXT_COUNT, "number", summary.non_text_count],
        [FIELD_TEXT_CHARS, "number", summary.text_chars],
        [FIELD_WEIGHTED_CONTENT_UNITS, "number", summary.weighted_content_units],
        [FIELD_RESOURCE_COUNT, "number", summary.resource_count],
        [FIELD_IMAGE_OCR_COUNT, "number", summary.image_ocr_count],
        [FIELD_START_TIME, "string", format_ts(summary.first_ts, "%Y-%m-%d %H:%M:%S")],
        [FIELD_END_TIME, "string", format_ts(summary.last_ts, "%Y-%m-%d %H:%M:%S")],
        [FIELD_SUMMARY_SOURCE, "string", summary_source],
        [FIELD_SUMMARY_MODEL, "string", summary_model],
        [NOTE_COMPLETION_PROGRESS_EXPR_FIELD, "string", progress_expr],
    ]


def build_note_content(summary: DayChatSummary, day: str, summary_draft: AiSummaryDraft | None = None) -> str:
    if summary_draft:
        summary_html = "\n".join(f"  <li>{html.escape(item)}</li>" for item in summary_draft.summary)
        notes_html = "\n".join(format_note_html(item) for item in summary_draft.notes)
    else:
        keyword_text = "、".join(summary.keywords) if summary.keywords else "未提取到稳定关键词"
        summary_html = "\n".join(
            [
                f"  <li>主要话题：{html.escape(keyword_text)}</li>",
                "  <li>聊天强度：按消息量和文本长度映射到节点进度，便于在日历上看当天沟通重心。</li>",
            ]
        )
        notes_html = "\n".join(
            f"<p>{html.escape(format_ts(ts))} {html.escape(sender)}：{html.escape(text)}</p>"
            for ts, sender, text in summary.snippets
        )
    return f"""
<h2>聊天日总结：{html.escape(summary.name)}</h2>
<p>{html.escape(day)} {html.escape(format_ts(summary.first_ts))} - {html.escape(format_ts(summary.last_ts))}，
共 {summary.message_count} 条消息，其中文本 {summary.text_count} 条、资源/非文本 {summary.non_text_count} 条。</p>
<h3>摘要</h3>
<ul>
{summary_html}
</ul>
<h3>笔记</h3>
{notes_html}
""".strip()


def build_ai_transcript(summary: DayChatSummary, char_limit: int) -> str:
    lines: list[str] = []
    total_chars = 0
    limit = max(2000, int(char_limit or 30000))
    for ts, sender, text in summary.transcript:
        cleaned = re.sub(r"\s+", " ", text).strip()
        line = f"{format_ts(ts)} {sender}：{cleaned}"
        if total_chars + len(line) > limit:
            remaining = len(summary.transcript) - len(lines)
            lines.append(f"...（后续还有 {remaining} 条消息，因长度限制省略）")
            break
        lines.append(line)
        total_chars += len(line)
    return "\n".join(lines)


def build_focus_hints(summary: DayChatSummary) -> str:
    transcript_text = "\n".join(text for _ts, _sender, text in summary.transcript).lower()
    groups = [
        ("代理/节点/订阅", ["clash", "verge", "directaccess", "mojie", "魔戒", "代理", "节点", "订阅", "梯子", "gpt"]),
        ("远程控制", ["向日葵", "远程", "控制", "连不上"]),
        ("网络环境", ["网络", "wifi", "热点", "路由器", "宽带", "运营商", "屏蔽", "学校", "实训室"]),
    ]
    lines: list[str] = []
    for label, words in groups:
        hits = {word: transcript_text.count(word.lower()) for word in words}
        hits = {word: count for word, count in hits.items() if count > 0}
        if hits:
            joined = "、".join(f"{word}={count}" for word, count in sorted(hits.items(), key=lambda item: (-item[1], item[0])))
            lines.append(f"- {label}证据：{joined}")
    if "clash" in transcript_text and "向日葵" in transcript_text:
        lines.append(
            "- 判别提醒：聊天同时出现 Clash/节点/订阅 和 向日葵时，优先判断哪一个是被安装、切换、订阅和反复测试的对象；向日葵可能只是远程协助工具。"
        )
    if not lines:
        return "无额外自动提示。"
    return "\n".join(lines)


def build_fact_hints(summary: DayChatSummary) -> str:
    groups = [
        ("mojie/魔戒购买与测试", ["mojie", "魔戒", "1gb", "1g", "1元", "20元", "30元", "套餐", "可以用"]),
        ("客户端安装/重装", ["clash.verge", "setup.zip", "重装", "卸载", "解压", "快捷方式"]),
        ("网络切换", ["网线", "宽带", "wifi", "热点", "运营商", "路由器", "独立流量"]),
    ]
    lines: list[str] = []
    for label, words in groups:
        matched: list[str] = []
        for ts, sender, text in summary.transcript:
            lower_text = text.lower()
            if any(word in lower_text for word in words):
                matched.append(f"{format_ts(ts)} {sender}：{brief_text(re.sub(r'\\s+', ' ', text).strip(), 120)}")
            if len(matched) >= 8:
                break
        if matched:
            lines.append(f"- {label}：")
            lines.extend(f"  - {item}" for item in matched)
    return "\n".join(lines) if lines else "无额外事实候选。"


def build_ai_system_prompt() -> str:
    return """
你是中文私人知识库的聊天记录整理助手。请根据一段微信单日聊天记录，为“我”的星图笔记生成简洁但有语义的日总结。

要求：
1. 输出分成“摘要”和“笔记”两块，不要输出“概览”或“代表片段”。
2. “摘要”要短而顺：固定 3-4 条，每条优先控制在 55 个汉字以内。第 1 条必须用一句话写清“发生了什么事”：聊天对象 + 关键工具/对象 + 核心异常 + 找我做什么。示例：“紫丹那边 Clash 突然连不上，于是找我帮忙判断是本机、节点还是网络问题。”
3. “摘要”不是证据摘抄。不要连续引用聊天原话，不要把“他说了 A、又说了 B、我又说 C”堆成流水账；除非原词本身是关键术语，否则用自己的话概括。
4. “摘要”按这个顺序写：①背景触发；②尝试过的排查/沟通事项；③我的判断；④后续安排。不要只写判断，不能漏掉背景。
5. “摘要”使用自然口语，不要写“我端/对端/链路/故障对象/证据链”这类工程报告词；除非它们是聊天原文里的产品或技术名词。
6. “笔记”必须同时保留关键事实和可复用信息：当天实际发生/已验证/已购买/已失败/已发送的内容 + 对应结论、流程、网址、注意事项、后续待办。
7. “笔记”不是流水账，不要按聊天时间线解释整件事。每条只记录一个可沉淀事项，句式为“事实性结论 + 必要证据/状态 + 可复用动作”。不要写“11:50 对方说...、12:01 又说...”这类时间线。
8. “笔记”优先按这些类型组织：主线结论、已执行排查、客户端/工具处理、资源或入口、替代方案、后续待办。每条都必须有实际内容，不能只写标题。
9. “笔记”格式优先为“<strong>小标题</strong>：事实性结论 + 沉淀操作”。例如“mojie 已用 1 元/1GB 小包测通；后续可按稳定性再决定是否买 20/30 元等更高流量套餐。”
10. “笔记”不要变成纯抽象知识卡。涉及明确发生过的动作时，必须写清事实完成态，例如“已测试可用”“已发送安装包”“重装后仍不稳定”“对方截图显示...”。不要把“已买/已测/已发/已失败”改写成“可买/可测/可发/可尝试”。
11. 对事实和知识的组合写法：先写事实，再写沉淀。例如“我花 1 元买了 1GB 小流量包测试，mojie 已可用；后续可按稳定性再决定是否买 20/30 元等更高流量套餐。”
12. “笔记”尽量不写冗余人物关系，不要每条都写“我建议/我提醒/对方反馈”；但关键事实需要主体时可以保留“我已... / 对方已...”，以免丢失事实归属。
13. 遇到排查流程、配置流程或购买/验证步骤时，必须优先使用嵌套列表，写成可复用步骤；父级列表项必须有正文，不能只有标题。适合步骤、排查流程、注意事项时，用 <ol><li>...</li></ol> 或 <ul><li>...</li></ul> 展开；不需要展开时保持单行条目。
14. “笔记”可以使用少量 HTML 富文本：<strong>小标题</strong>、<ol>/<ul>/<li> 子列表、<code>文件名</code>、<a href="https://...">链接</a>、<br>。不要使用表格、图片、样式或脚本。
15. 必须使用第一人称视角写“摘要”，用“我”描述我的判断、处理、决定和待跟进；聊天对象统一称“对方”，不要猜测性别写“他/她”。
16. 每条摘要和笔记必须有聊天证据支撑，但证据主要用于判断，不要把证据堆进正文。必要时只用短括号补充，不输出大段原话或精确时间线。
17. 严格区分“正在排查的故障对象”和“用于远程协助/顺带提到的工具”。不要把只出现一两次的辅助工具误判成核心主题。
18. 遇到软件名、产品名、故障对象时，优先保留原词，例如 Clash、Clash Verge、DirectACCESS、mojie、向日葵。
19. 如果聊天中同时出现截图/文件资源证据和普通文本，资源证据可帮助判断上下文，但不要编造截图里没有 OCR 出来的内容。
20. 先在心里判断候选故障对象的证据强弱：被安装/重装/下载、被订阅/切换、被反复测试的对象，通常比远程协助工具更像主线。
21. 摘要里不要使用“用户”“双方”“对话核心”“故障对象的证据”等外部分析口吻；可以说“我判断”“我帮对方”“我后面要看”。
22. 不要编造聊天中没有的信息；不确定就写得保守。
23. 只输出 JSON 对象。
""".strip()


def build_ai_user_prompt(summary: DayChatSummary, day: str, char_limit: int) -> str:
    return f"""
日期：{day}
聊天对象：{summary.name}
聊天类型：{summary.chat_type or "unknown"}
统计：共 {summary.message_count} 条，文本 {summary.text_count} 条，资源/非文本 {summary.non_text_count} 条，已关联资源证据 {summary.resource_count} 条，图片 OCR {summary.image_ocr_count} 条。

自动证据提示（只作为辅助，不可替代原文；如果与原文冲突，以原文为准）：
{build_focus_hints(summary)}

重要事实候选（优先用于避免把已发生事实改写成泛化建议；如果与完整聊天记录冲突，以完整记录为准）：
{build_fact_hints(summary)}

请输出 JSON：
{{
  "summary": ["短摘要1", "短摘要2"],
  "notes": ["<strong>笔记小标题</strong>：结构化要点或步骤", "可复用笔记2"]
}}

聊天记录：
{build_ai_transcript(summary, char_limit)}
""".strip()


def extract_json_object(text: Any) -> dict[str, Any]:
    raw = str(text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.I).strip()
        raw = re.sub(r"\s*```$", "", raw).strip()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            raise
        payload = json.loads(raw[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("AI summary payload is not an object")
    return payload


def normalize_ai_summary_payload(payload: dict[str, Any], *, source: str, model: str) -> AiSummaryDraft:
    summary_items = payload.get("summary")
    if summary_items is None:
        summary_items = payload.get("overview")
    note_items = payload.get("notes")
    if note_items is None:
        note_items = payload.get("snippets")

    summary: list[str] = []
    for item in summary_items or []:
        text = re.sub(r"\s+", " ", str(item or "")).strip()
        text = normalize_summary_style(text)
        if text:
            summary.append(text)
    notes: list[str] = []
    for item in note_items or []:
        if isinstance(item, dict):
            time_text = re.sub(r"\s+", " ", str(item.get("time") or "")).strip()
            sender = re.sub(r"\s+", " ", str(item.get("sender") or "")).strip()
            text = re.sub(r"\s+", " ", str(item.get("text") or "")).strip()
            text = " ".join(part for part in [time_text, sender, text] if part)
        else:
            text = re.sub(r"\s+", " ", str(item or "")).strip()
        text = normalize_summary_style(text)
        if text:
            notes.append(text)
    if not summary:
        raise ValueError("AI summary has no summary")
    if not notes:
        raise ValueError("AI summary has no notes")
    return AiSummaryDraft(
        summary=summary[:5],
        notes=notes[:12],
        source=source,
        model=model,
    )


def normalize_summary_style(text: str) -> str:
    replacements = {
        "我端": "我这边",
        "对端": "对方那边",
        "故障对象": "问题对象",
        "证据链": "依据",
        "她": "对方",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    text = re.sub(r"(?<!其)他(?!人|们|处|方|项|端|类)", "对方", text)
    return text


class SafeNoteHtmlParser(HTMLParser):
    allowed_tags = {"strong", "b", "em", "i", "code", "a", "ul", "ol", "li", "br", "p"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag not in self.allowed_tags:
            self.parts.append(html.escape(self.get_starttag_text() or ""))
            return
        if tag == "a":
            href = ""
            for key, value in attrs:
                if key.lower() == "href" and value:
                    href = value.strip()
                    break
            if re.match(r"^https?://", href, flags=re.I):
                self.parts.append(f'<a href="{html.escape(href, quote=True)}">')
            else:
                self.parts.append("<a>")
            return
        self.parts.append(f"<{tag}>")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self.allowed_tags and tag != "br":
            self.parts.append(f"</{tag}>")
        elif tag not in self.allowed_tags:
            self.parts.append(html.escape(f"</{tag}>"))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag == "br":
            self.parts.append("<br>")
        elif tag in self.allowed_tags:
            self.handle_starttag(tag, attrs)
            self.handle_endtag(tag)
        else:
            self.parts.append(html.escape(self.get_starttag_text() or ""))

    def handle_data(self, data: str) -> None:
        self.parts.append(html.escape(data))

    def handle_entityref(self, name: str) -> None:
        self.parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        self.parts.append(f"&#{name};")


def sanitize_note_html(text: str) -> str:
    parser = SafeNoteHtmlParser()
    parser.feed(text or "")
    parser.close()
    return "".join(parser.parts)


def format_note_html(text: str) -> str:
    sanitized = sanitize_note_html(convert_inline_numbered_steps(text or "")).strip()
    sanitized = wrap_text_before_list(sanitized)
    if re.search(r"</?(?:p|ol|ul|li)\b", sanitized, flags=re.I):
        return sanitized
    return f"<p>{sanitized}</p>"


def convert_inline_numbered_steps(text: str) -> str:
    if re.search(r"</?(?:ol|ul|li)\b", text, flags=re.I):
        return text
    matches = list(re.finditer(r"(?<!\d)([1-9]\d*)[）)]", text))
    if len(matches) < 2:
        return text

    prefix = text[: matches[0].start()].rstrip("；;，, ")
    steps: list[str] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        step = text[match.end() : end].strip("；;，, ")
        if step:
            steps.append(step)
    if len(steps) < 2:
        return text

    items = "".join(f"<li>{step}</li>" for step in steps)
    return f"<p>{prefix}</p><ol>{items}</ol>"


def wrap_text_before_list(html_text: str) -> str:
    match = re.search(r"<(?:ol|ul)\b", html_text, flags=re.I)
    if not match:
        return html_text
    prefix = html_text[: match.start()].strip()
    if not prefix or prefix.lower().startswith("<p"):
        return html_text
    suffix = html_text[match.start() :].strip()
    return f"<p>{prefix}</p>{suffix}"


def build_ai_summary_draft(
    session: Session,
    user: User,
    summary: DayChatSummary,
    day: str,
    *,
    provider: str = "",
    model: str = "",
    timeout_seconds: float = 900.0,
    char_limit: int = 30000,
) -> AiSummaryDraft:
    runtime = resolve_ai_app_runtime_config(
        session=session,
        current_user=user,
        app_id=AI_APP_WECHAT_DAILY_SUMMARY,
        provider=provider or None,
        model=model or None,
    )
    resolved_provider = str(runtime.get("provider") or "").strip()
    resolved_model = str(runtime.get("model") or "").strip()
    response = chat_with_provider(
        provider_id=resolved_provider,
        base_url=runtime.get("base_url"),
        api_key=runtime.get("api_key"),
        model=resolved_model,
        system_prompt=build_ai_system_prompt(),
        messages=[{"role": "user", "content": build_ai_user_prompt(summary, day, char_limit)}],
        response_format="json",
        temperature=0.2,
        timeout_seconds=timeout_seconds,
        extra_providers=tuple(runtime.get("extra_providers") or ()),
    )
    response_model = str(response.get("model") or resolved_model or "").strip()
    return normalize_ai_summary_payload(
        extract_json_object(response.get("content")),
        source=f"ai:{resolved_provider}",
        model=response_model,
    )


def compute_progress(summary: DayChatSummary) -> float:
    if summary.weighted_content_units <= 0:
        return 0.05
    return max(0.05, min(1.0, summary.weighted_content_units / 8000))


def compute_weight(summary: DayChatSummary) -> int:
    return 0


def resolve_user(session: Session, username: str) -> User:
    user = session.exec(select(User).where(User.username == username)).first()
    if user is not None:
        return user
    user = session.exec(select(User).where(User.is_active == True).order_by(User.id)).first()  # noqa: E712
    if user is None or user.id is None:
        raise RuntimeError("No active CodeYun user found.")
    return user


def find_existing_note(
    session: Session,
    user_id: int,
    day: str,
    username: str,
    start_ts: int,
    end_ts: int,
) -> NoteNode | None:
    notes = session.exec(
        select(NoteNode)
        .where(NoteNode.user_id == user_id)
        .where(NoteNode.start_at >= start_ts)
        .where(NoteNode.start_at < end_ts)
    ).all()
    for note in notes:
        if note.deleted_at:
            continue
        if (
            get_custom_field_value(note.custom_fields, FIELD_DATE) == day
            and get_custom_field_value(note.custom_fields, FIELD_CHAT_USERNAME) == username
        ):
            return note
    return None


def upsert_note(
    session: Session,
    user: User,
    summary: DayChatSummary,
    day: str,
    summary_draft: AiSummaryDraft | None = None,
) -> str:
    day_start_ts, day_end_ts, _ = parse_day_range(day)
    progress = compute_progress(summary)
    progress_expr = f"{progress:.4f}"
    taxonomy = derive_legacy_semantics_from_taxonomy(
        [{"key": NOTE_CATEGORY_DEFAULT, "weight": 100}],
        primary_category=NOTE_CATEGORY_DEFAULT,
        note_form=NOTE_FORM_DOCUMENT,
        note_scene=NOTE_SCENE_DEFAULT,
        lifecycle_stage="done",
    )
    title = summary.name
    data = {
        "title": title,
        "content": build_note_content(summary, day, summary_draft),
        "weight": compute_weight(summary),
        "weight_mode": None,
        "private_level": 0,
        "custom_fields": custom_fields(summary, day, progress_expr, summary_draft),
        "start_at": float(summary.first_ts),
        "updated_at": time.time(),
        **taxonomy,
    }

    existing = find_existing_note(session, int(user.id), day, summary.username, day_start_ts, day_end_ts)
    if existing is not None:
        for key, value in data.items():
            setattr(existing, key, value)
        session.add(existing)
        return "updated"

    note_identity = allocate_new_note_identity(session)
    note = NoteNode(
        id=note_identity.primary_id,
        numeric_id=note_identity.numeric_id,
        legacy_id=note_identity.legacy_id,
        user_id=int(user.id),
        created_at=time.time(),
        history=[],
        color=None,
        **data,
    )
    session.add(note)
    return "created"


def main() -> None:
    args = parse_args()
    db_storage = Path(args.db_storage).expanduser() if args.db_storage else _settings_wechat_db_storage_path()
    if not db_storage.exists():
        raise SystemExit(f"WeChat db_storage not found: {db_storage}")

    summaries = collect_day_summaries(
        db_storage,
        args.date,
        args.min_messages,
        include_resource_context=args.include_resource_context,
        ocr_images=args.ocr_images,
    )
    if args.chat.strip():
        keyword = args.chat.strip().lower()
        summaries = [
            item
            for item in summaries
            if keyword in item.name.lower()
            or keyword in item.username.lower()
            or keyword in item.table_name.lower()
        ]
    if args.limit > 0:
        summaries = summaries[: args.limit]

    if args.dry_run:
        print(f"db_storage={db_storage}")
        print(f"date={args.date}, chats={len(summaries)}")
        for item in summaries:
            print(
                f"{format_ts(item.first_ts)} {item.name} "
                f"messages={item.message_count}, text={item.text_count}, non_text={item.non_text_count}, "
                f"resources={item.resource_count}, image_ocr={item.image_ocr_count}, "
                f"progress={compute_progress(item):.2f}, keywords={'/'.join(item.keywords)}"
            )
        return

    created = 0
    updated = 0
    ai_generated = 0
    ai_failed = 0
    with Session(engine) as session:
        user = resolve_user(session, args.user)
        for item in summaries:
            summary_draft: AiSummaryDraft | None = None
            if args.use_ai:
                try:
                    summary_draft = build_ai_summary_draft(
                        session,
                        user,
                        item,
                        args.date,
                        provider=args.ai_provider,
                        model=args.ai_model,
                        timeout_seconds=args.ai_timeout,
                        char_limit=args.ai_limit_chars,
                    )
                    ai_generated += 1
                    print(f"AI summary ok: {format_ts(item.first_ts)} {item.name} ({summary_draft.model})")
                except (OllamaClientError, ValueError, json.JSONDecodeError) as exc:
                    ai_failed += 1
                    print(f"AI summary fallback: {format_ts(item.first_ts)} {item.name}: {exc}", file=sys.stderr)
            result = upsert_note(session, user, item, args.date, summary_draft)
            if result == "created":
                created += 1
            else:
                updated += 1
        session.commit()

    print(
        f"Imported WeChat daily notes for {args.date}: "
        f"created={created}, updated={updated}, total={len(summaries)}, "
        f"ai_generated={ai_generated}, ai_failed={ai_failed}"
    )


if __name__ == "__main__":
    main()
