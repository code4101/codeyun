from __future__ import annotations

from collections import defaultdict
import csv
import hashlib
import json
import os
from pathlib import Path
import re
import time
from typing import Any
import uuid

import jieba
from pypinyin import Style, lazy_pinyin


SNAPSHOT_FILE = "context_prediction_snapshot.tsv"
COUNTS_FILE = "context_prediction_model_counts.tsv"
SEED_FILE = "context_prediction.tsv"
PENDING_FILE = "context_prediction_pending.tsv"
HISTORY_FILE = "context_prediction_history.log"
HISTORY_ARTICLE_FILE = "context_prediction_history_article.txt"
HISTORY_ARTICLE_META_FILE = "context_prediction_history_article.json"
HTML_REPORT_FILE = "docs/context_prediction_tree.html"
ARTICLE_MANIFEST_FILE = "context_prediction_articles.json"
ARTICLE_CONTENT_DIR = "context_prediction_articles"
ARTICLE_CONTRIBUTIONS_FILE = "context_prediction_article_counts.tsv"
DELETED_CANDIDATES_FILE = "context_prediction_deleted_candidates.tsv"
ARCHIVE_DIR = "context_prediction_archives"

ARTICLE_EXTRACTOR_VERSION = 1
MAX_CONTEXT_TOKENS = 4
MAX_ARTICLE_CHARS = 1_000_000
DEFAULT_TOPK_PER_KEY = 20
DEFAULT_HISTORY_ARTICLE_LIMIT = 20000
DEFAULT_HISTORY_ARTICLE_PAGE_SIZE = 100
HISTORY_PARAGRAPH_GAP_SECONDS = 5 * 60

_CJK_RE = re.compile(r"[\u3400-\u9fff]+")
_SENTENCE_SPLIT_RE = re.compile(r"[\r\n。！？!?；;]+")
_HISTORY_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")


jieba.setLogLevel(50)


class RimeContextPredictionError(ValueError):
    pass


def _resolve_rime_dir() -> Path | None:
    configured = os.environ.get("CODEYUN_RIME_USER_DIR")
    if configured:
        return Path(configured).expanduser()

    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "Rime"

    if os.name == "nt":
        return Path.home() / "AppData" / "Roaming" / "Rime"

    return None


def _file_info(rime_dir: Path | None, relative_path: str) -> dict[str, Any]:
    path = (rime_dir / relative_path) if rime_dir else None
    exists = bool(path and path.exists())
    stat = path.stat() if exists and path else None
    return {
        "key": relative_path,
        "path": str(path) if path else None,
        "exists": exists,
        "size": stat.st_size if stat else 0,
        "modified_at": stat.st_mtime if stat else None,
    }


def _tracked_files(rime_dir: Path | None) -> list[dict[str, Any]]:
    return [
        _file_info(rime_dir, item)
        for item in [
            SNAPSHOT_FILE,
            COUNTS_FILE,
            SEED_FILE,
            PENDING_FILE,
            HISTORY_FILE,
            HISTORY_ARTICLE_FILE,
            HISTORY_ARTICLE_META_FILE,
            ARTICLE_MANIFEST_FILE,
            ARTICLE_CONTRIBUTIONS_FILE,
            DELETED_CANDIDATES_FILE,
            HTML_REPORT_FILE,
        ]
    ]


def _clean_tsv_field(value: Any) -> str:
    return str(value or "").replace("\t", " ").replace("\r", " ").replace("\n", " ").strip()


def _format_weight(value: float) -> str:
    return f"{float(value):g}"


def _read_prediction_rows(path: Path, limit: int | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.reader(fh, delimiter="\t")
        for fields in reader:
            if not fields:
                continue
            first = (fields[0] or "").strip()
            if not first or first.startswith("#"):
                continue
            if len(fields) < 4:
                continue

            context = first
            prefix = (fields[1] or "").strip()
            candidate = (fields[2] or "").strip()
            if not prefix or not candidate:
                continue
            try:
                weight = float((fields[3] or "0").strip())
            except ValueError:
                weight = 0.0
            comment = (fields[4] or "").strip() if len(fields) >= 5 else ""
            rows.append(
                {
                    "context": context,
                    "prefix": prefix,
                    "candidate": candidate,
                    "weight": weight,
                    "comment": comment,
                }
            )
            if limit and len(rows) >= limit:
                break
    return rows


def _write_prediction_rows_file(path: Path, rows: list[dict[str, Any]]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write("# context_key\tpinyin_prefix\tcandidate\tweight\tcomment\n")
        for row in rows:
            fh.write(
                "\t".join(
                    [
                        _clean_tsv_field(row.get("context") or ""),
                        _clean_tsv_field(row.get("prefix") or ""),
                        _clean_tsv_field(row.get("candidate") or ""),
                        _format_weight(float(row.get("weight") or 0)),
                        _clean_tsv_field(row.get("comment") or ""),
                    ]
                )
                + "\n"
            )
    os.replace(tmp, path)


def _read_count_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.reader(fh, delimiter="\t")
        for fields in reader:
            if not fields or (fields[0] or "").lstrip().startswith("#") or len(fields) < 4:
                continue
            try:
                weight = float((fields[3] or "0").strip())
            except ValueError:
                continue
            rows.append(
                {
                    "context": (fields[0] or "").strip(),
                    "prefix": (fields[1] or "").strip(),
                    "candidate": (fields[2] or "").strip(),
                    "weight": weight,
                    "comment": (fields[5] or "输入历史").strip() if len(fields) >= 6 else "输入历史",
                }
            )
    return rows


def _read_count_entries(path: Path) -> dict[tuple[str, str, str], dict[str, Any]]:
    entries: dict[tuple[str, str, str], dict[str, Any]] = {}
    if not path.exists():
        return entries
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.reader(fh, delimiter="\t")
        for fields in reader:
            if not fields or (fields[0] or "").lstrip().startswith("#") or len(fields) < 4:
                continue
            context, prefix, candidate = _candidate_key(fields[0], fields[1], fields[2])
            if not context or not prefix or not candidate:
                continue
            try:
                count = float((fields[3] or "0").strip())
            except ValueError:
                continue
            entries[(context, prefix, candidate)] = {
                "count": count,
                "last_seen": (fields[4] or "").strip() if len(fields) >= 5 else "",
                "comment": (fields[5] or "输入历史").strip() if len(fields) >= 6 else "输入历史",
            }
    return entries


def _write_count_entries(rime_dir: Path, entries: dict[tuple[str, str, str], dict[str, Any]]) -> None:
    path = rime_dir / COUNTS_FILE
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write("# context_key\tpinyin_prefix\tcandidate\tcount\tlast_seen\tcomment\n")
        for (context, prefix, candidate), entry in sorted(entries.items()):
            fh.write(
                "\t".join(
                    [
                        context,
                        prefix,
                        candidate,
                        _format_weight(float(entry.get("count") or 0)),
                        _clean_tsv_field(entry.get("last_seen") or ""),
                        _clean_tsv_field(entry.get("comment") or "输入历史"),
                    ]
                )
                + "\n"
            )
    os.replace(tmp, path)


def _default_article_manifest() -> dict[str, Any]:
    return {"version": 1, "articles": []}


def _read_article_manifest(rime_dir: Path) -> dict[str, Any]:
    path = rime_dir / ARTICLE_MANIFEST_FILE
    if not path.exists():
        return _default_article_manifest()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _default_article_manifest()
    if not isinstance(payload, dict):
        return _default_article_manifest()
    articles = payload.get("articles")
    if not isinstance(articles, list):
        payload["articles"] = []
    payload.setdefault("version", 1)
    return payload


def _write_article_manifest(rime_dir: Path, manifest: dict[str, Any]) -> None:
    path = rime_dir / ARTICLE_MANIFEST_FILE
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _article_content_path(rime_dir: Path, article: dict[str, Any]) -> Path:
    relative = str(article.get("content_path") or "")
    if relative:
        return rime_dir / relative
    return rime_dir / ARTICLE_CONTENT_DIR / f"{article['id']}.txt"


def _read_article_contributions(rime_dir: Path) -> list[dict[str, Any]]:
    path = rime_dir / ARTICLE_CONTRIBUTIONS_FILE
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.reader(fh, delimiter="\t")
        for fields in reader:
            if not fields or (fields[0] or "").lstrip().startswith("#") or len(fields) < 5:
                continue
            try:
                weight = float((fields[4] or "0").strip())
            except ValueError:
                continue
            rows.append(
                {
                    "source_id": (fields[0] or "").strip(),
                    "context": (fields[1] or "").strip(),
                    "prefix": (fields[2] or "").strip(),
                    "candidate": (fields[3] or "").strip(),
                    "weight": weight,
                }
            )
    return rows


def _write_article_contributions(rime_dir: Path, rows: list[dict[str, Any]]) -> None:
    path = rime_dir / ARTICLE_CONTRIBUTIONS_FILE
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write("# source_id\tcontext_key\tpinyin_prefix\tcandidate\tcount\n")
        for row in sorted(rows, key=lambda item: (item["source_id"], item["context"], item["prefix"], item["candidate"])):
            fh.write(
                "\t".join(
                    [
                        _clean_tsv_field(row["source_id"]),
                        _clean_tsv_field(row["context"]),
                        _clean_tsv_field(row["prefix"]),
                        _clean_tsv_field(row["candidate"]),
                        _format_weight(float(row["weight"])),
                    ]
                )
                + "\n"
            )
    os.replace(tmp, path)


def _candidate_key(context: Any, prefix: Any, candidate: Any) -> tuple[str, str, str]:
    return (
        _clean_tsv_field(context),
        _clean_tsv_field(prefix),
        _clean_tsv_field(candidate),
    )


def _normalize_candidate_key(context: Any, prefix: Any, candidate: Any) -> tuple[str, str, str]:
    key = _candidate_key(context, prefix, candidate)
    if not all(key):
        raise RimeContextPredictionError("前文片段、当前拼音和候选词都不能为空。")
    return key


def _read_deleted_candidate_rows(rime_dir: Path) -> list[dict[str, Any]]:
    path = rime_dir / DELETED_CANDIDATES_FILE
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.reader(fh, delimiter="\t")
        for fields in reader:
            if not fields or (fields[0] or "").lstrip().startswith("#") or len(fields) < 3:
                continue
            context, prefix, candidate = _candidate_key(fields[0], fields[1], fields[2])
            if not context or not prefix or not candidate:
                continue
            deleted_at = 0.0
            if len(fields) >= 4:
                try:
                    deleted_at = float((fields[3] or "0").strip())
                except ValueError:
                    deleted_at = 0.0
            rows.append(
                {
                    "context": context,
                    "prefix": prefix,
                    "candidate": candidate,
                    "deleted_at": deleted_at,
                }
            )
    return rows


def _read_deleted_candidate_keys(rime_dir: Path) -> set[tuple[str, str, str]]:
    return {
        (row["context"], row["prefix"], row["candidate"])
        for row in _read_deleted_candidate_rows(rime_dir)
    }


def _is_manual_rule_comment(comment: Any) -> bool:
    return _clean_tsv_field(comment) == "手动规则"


def _write_deleted_candidate_rows(rime_dir: Path, rows: list[dict[str, Any]]) -> None:
    path = rime_dir / DELETED_CANDIDATES_FILE
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write("# context_key\tpinyin_prefix\tcandidate\tdeleted_at\n")
        for row in sorted(rows, key=lambda item: (item["context"], item["prefix"], item["candidate"])):
            fh.write(
                "\t".join(
                    [
                        _clean_tsv_field(row["context"]),
                        _clean_tsv_field(row["prefix"]),
                        _clean_tsv_field(row["candidate"]),
                        _format_weight(float(row.get("deleted_at") or 0)),
                    ]
                )
                + "\n"
            )
    os.replace(tmp, path)


def _rotate_pending_events(rime_dir: Path) -> Path | None:
    pending_path = rime_dir / PENDING_FILE
    if not pending_path.exists() or pending_path.stat().st_size == 0:
        return None
    archive_dir = rime_dir / ARCHIVE_DIR
    archive_dir.mkdir(parents=True, exist_ok=True)
    processing_path = archive_dir / f"context_prediction_pending.{int(time.time())}.processing.tsv"
    os.replace(pending_path, processing_path)
    return processing_path


def _fold_pending_events(rime_dir: Path) -> dict[str, Any]:
    processing_path = _rotate_pending_events(rime_dir)
    if not processing_path:
        return {"pending_rows": 0}

    entries = _read_count_entries(rime_dir / COUNTS_FILE)
    seen_at = time.strftime("%Y-%m-%d %H:%M:%S")
    folded_rows = 0
    try:
        with processing_path.open("r", encoding="utf-8-sig", newline="") as fh:
            reader = csv.reader(fh, delimiter="\t")
            for fields in reader:
                if not fields or (fields[0] or "").lstrip().startswith("#") or len(fields) < 4:
                    continue
                context, prefix, candidate = _candidate_key(fields[0], fields[1], fields[2])
                if not context or not prefix or not candidate:
                    continue
                try:
                    weight = float((fields[3] or "1").strip())
                except ValueError:
                    weight = 1.0
                comment = (fields[4] or "自学习").strip() if len(fields) >= 5 else "自学习"
                entry = entries.setdefault(
                    (context, prefix, candidate),
                    {"count": 0.0, "last_seen": "", "comment": comment or "自学习"},
                )
                entry["count"] = float(entry.get("count") or 0) + weight
                entry["last_seen"] = seen_at
                if comment:
                    entry["comment"] = comment
                folded_rows += 1
    finally:
        processing_path.unlink(missing_ok=True)

    _write_count_entries(rime_dir, entries)
    return {"pending_rows": folded_rows, "count_entries": len(entries)}


def _discard_pending_events(rime_dir: Path) -> int:
    processing_path = _rotate_pending_events(rime_dir)
    if not processing_path:
        return 0
    pending_rows = _count_data_rows(processing_path)
    processing_path.unlink(missing_ok=True)
    return pending_rows


def _parse_history_timestamp(value: str) -> float | None:
    text = (value or "").strip()
    if not _HISTORY_TIMESTAMP_RE.match(text):
        return None
    try:
        return time.mktime(time.strptime(text, "%Y-%m-%d %H:%M:%S"))
    except ValueError:
        return None


def _parse_history_event_fields(fields: list[str]) -> dict[str, Any] | None:
    if not fields:
        return None
    first = (fields[0] or "").strip()
    if not first or first.startswith("#"):
        return None
    timestamp = first if _HISTORY_TIMESTAMP_RE.match(first) else ""
    text = ""
    if len(fields) >= 2:
        text = fields[-1] or ""
    elif not timestamp:
        text = fields[0] or ""
    if not text:
        return None
    return {
        "timestamp": timestamp,
        "time": _parse_history_timestamp(timestamp) if timestamp else None,
        "text": text,
    }


def _iter_history_events(path: Path):
    if not path.exists():
        return
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.reader(fh, delimiter="\t")
        for fields in reader:
            event = _parse_history_event_fields(fields)
            if event:
                yield event


def _read_history_events(path: Path) -> list[dict[str, Any]]:
    return list(_iter_history_events(path))


def _read_history_event_page(path: Path, *, page: int, page_size: int) -> dict[str, Any]:
    normalized_page_size = max(1, min(int(page_size or DEFAULT_HISTORY_ARTICLE_PAGE_SIZE), 1000))
    total = sum(1 for _ in _iter_history_events(path))
    total_pages = max(1, (total + normalized_page_size - 1) // normalized_page_size)
    normalized_page = max(1, min(int(page or 1), total_pages))

    end_index = max(0, total - (normalized_page - 1) * normalized_page_size)
    start_index = max(1, end_index - normalized_page_size + 1) if end_index else 0

    events: list[dict[str, Any]] = []
    if total:
        for index, event in enumerate(_iter_history_events(path), start=1):
            if index < start_index:
                continue
            if index > end_index:
                break
            events.append(event)

    return {
        "events": events,
        "pagination": {
            "page": normalized_page,
            "page_size": normalized_page_size,
            "total": total,
            "total_pages": total_pages if total else 0,
            "start_index": start_index if events else 0,
            "end_index": end_index if events else 0,
            "has_prev": normalized_page > 1 and total > 0,
            "has_next": normalized_page < total_pages and total > 0,
        },
    }


def _count_data_rows(path: Path) -> int:
    if not path.exists():
        return 0
    count = 0
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.reader(fh, delimiter="\t")
        for fields in reader:
            if not fields:
                continue
            first = (fields[0] or "").strip()
            if first and not first.startswith("#"):
                count += 1
    return count


def _history_events_to_article(events: list[dict[str, Any]]) -> str:
    paragraphs: list[str] = []
    current: list[str] = []
    last_time: float | None = None
    last_date = ""

    for event in events:
        text = str(event.get("text") or "")
        if not text:
            continue
        timestamp = str(event.get("timestamp") or "")
        current_time = event.get("time") if isinstance(event.get("time"), (int, float)) else None
        current_date = timestamp[:10] if timestamp else ""
        should_break = bool(
            current
            and (
                (last_time is not None and current_time is not None and current_time - last_time >= HISTORY_PARAGRAPH_GAP_SECONDS)
                or (last_date and current_date and current_date != last_date)
            )
        )
        if should_break:
            paragraphs.append("".join(current))
            current = []
        current.append(text)
        if current_time is not None:
            last_time = float(current_time)
        if current_date:
            last_date = current_date

    if current:
        paragraphs.append("".join(current))
    return "\n\n".join(paragraph for paragraph in paragraphs if paragraph)


def _history_article_path(rime_dir: Path) -> Path:
    return rime_dir / HISTORY_ARTICLE_FILE


def _history_article_meta_path(rime_dir: Path) -> Path:
    return rime_dir / HISTORY_ARTICLE_META_FILE


def _read_history_article_meta(rime_dir: Path) -> dict[str, Any]:
    path = _history_article_meta_path(rime_dir)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_history_article_meta(rime_dir: Path, meta: dict[str, Any]) -> None:
    path = _history_article_meta_path(rime_dir)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _history_article_content_from_events(
    events: list[dict[str, Any]],
    *,
    saved_event_count: int = 0,
    saved_content: str = "",
) -> tuple[str, int]:
    content = saved_content
    appended_event_count = 0
    if saved_event_count < 0:
        saved_event_count = 0
    if saved_event_count < len(events):
        appended_events = events[saved_event_count:]
        suffix = _history_events_to_article(appended_events)
        if suffix:
            content = f"{content.rstrip()}\n\n{suffix}" if content.strip() else suffix
            appended_event_count = len(appended_events)
    return content, appended_event_count


def _resolve_history_article_content(
    rime_dir: Path,
    events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    history_events = events if events is not None else _read_history_events(rime_dir / HISTORY_FILE)
    article_path = _history_article_path(rime_dir)
    meta = _read_history_article_meta(rime_dir)
    edited = article_path.exists()

    if edited:
        saved_content = article_path.read_text(encoding="utf-8")
        saved_event_count = int(meta.get("history_event_count") or 0)
        content, appended_event_count = _history_article_content_from_events(
            history_events,
            saved_event_count=saved_event_count,
            saved_content=saved_content,
        )
    else:
        content = _history_events_to_article(history_events)
        appended_event_count = 0

    return {
        "content": content,
        "events": history_events,
        "edited": edited,
        "saved_at": float(meta.get("saved_at") or 0),
        "base_event_count": int(meta.get("history_event_count") or 0),
        "appended_event_count": appended_event_count,
    }


def save_rime_context_prediction_history_article(content: str) -> dict[str, Any]:
    rime_dir = _ensure_writable_rime_dir()
    text = _normalize_article_text(content)
    events = _read_history_events(rime_dir / HISTORY_FILE)
    now = time.time()
    article_path = _history_article_path(rime_dir)
    tmp = article_path.with_suffix(article_path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, article_path)
    history_path = rime_dir / HISTORY_FILE
    stat = history_path.stat() if history_path.exists() else None
    _write_history_article_meta(
        rime_dir,
        {
            "version": 1,
            "saved_at": now,
            "history_event_count": len(events),
            "history_size": stat.st_size if stat else 0,
            "history_modified_at": stat.st_mtime if stat else None,
            "content_hash": _content_hash(text),
        },
    )
    return collect_rime_context_prediction_history_article()


def _rebuild_count_entries_from_history(rime_dir: Path) -> dict[str, Any]:
    history_article = _resolve_history_article_content(rime_dir)
    events = history_article["events"]
    content = str(history_article["content"] or "")
    entries: dict[tuple[str, str, str], dict[str, Any]] = {}
    if not events or not _CJK_RE.search(content):
        _write_count_entries(rime_dir, entries)
        return {
            "history_events": len(events),
            "history_chars": len(content),
            "history_contributions": 0,
            "count_entries": 0,
        }

    last_seen = str(events[-1].get("timestamp") or "") or time.strftime("%Y-%m-%d %H:%M:%S")
    for row in _extract_article_contributions("input_history", content):
        context, prefix, candidate = _candidate_key(row["context"], row["prefix"], row["candidate"])
        if not context or not prefix or not candidate:
            continue
        entry = entries.setdefault(
            (context, prefix, candidate),
            {"count": 0.0, "last_seen": last_seen, "comment": "输入历史"},
        )
        entry["count"] = float(entry.get("count") or 0) + float(row.get("weight") or 0)
        entry["last_seen"] = last_seen

    _write_count_entries(rime_dir, entries)
    return {
        "history_events": len(events),
        "history_chars": len(content),
        "history_contributions": sum(float(item.get("count") or 0) for item in entries.values()),
        "count_entries": len(entries),
        "history_article_edited": bool(history_article["edited"]),
    }


def _can_rebuild_from_history(rime_dir: Path) -> bool:
    path = rime_dir / HISTORY_FILE
    return path.exists() and path.stat().st_size > 0


def rebuild_rime_context_prediction_from_history(rime_dir: Path | None = None) -> dict[str, Any]:
    target_dir = rime_dir or _ensure_writable_rime_dir()
    history_result = _rebuild_count_entries_from_history(target_dir)
    pending_rows = _discard_pending_events(target_dir)
    snapshot_result = rebuild_rime_context_prediction_snapshot(target_dir)
    return {
        **history_result,
        **snapshot_result,
        "pending_rows": pending_rows,
        "source": HISTORY_FILE,
    }


def make_rime_context_prediction_history_unavailable(
    *,
    status: str,
    message: str,
    rime_dir: str | None = None,
    files: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "available": False,
        "status": status,
        "message": message,
        "rime_dir": rime_dir,
        "source": None,
        "source_path": None,
        "updated_at": None,
        "files": files or [],
        "summary": {
            "entry_count": 0,
            "char_count": 0,
            "paragraph_count": 0,
            "first_seen": "",
            "last_seen": "",
            "pending_row_count": 0,
            "model_count_row_count": 0,
            "truncated": False,
            "limit": 0,
            "edited": False,
            "saved_at": 0,
            "base_event_count": 0,
            "appended_event_count": 0,
        },
        "pagination": None,
        "content": "",
    }


def collect_rime_context_prediction_history_article(
    limit: int | None = DEFAULT_HISTORY_ARTICLE_LIMIT,
    *,
    page: int | None = None,
    page_size: int | None = None,
) -> dict[str, Any]:
    rime_dir = _resolve_rime_dir()
    files = _tracked_files(rime_dir)

    if not rime_dir:
        return make_rime_context_prediction_history_unavailable(
            status="unsupported_platform",
            message="当前系统没有可识别的 Rime 用户目录位置。",
            files=files,
        )

    if not rime_dir.exists():
        return make_rime_context_prediction_history_unavailable(
            status="rime_missing",
            message="该设备未发现 Rime 用户目录，可能没有安装小狼毫或尚未启动过 Rime。",
            rime_dir=str(rime_dir),
            files=files,
        )

    history_path = rime_dir / HISTORY_FILE
    if not history_path.exists():
        return make_rime_context_prediction_history_unavailable(
            status="history_missing",
            message="没有发现输入历史日志。当前预测索引可能只有聚合计数，不能无损还原成文章。",
            rime_dir=str(rime_dir),
            files=files,
        )

    try:
        if page is not None or page_size is not None:
            page_payload = _read_history_event_page(
                history_path,
                page=int(page or 1),
                page_size=int(page_size or DEFAULT_HISTORY_ARTICLE_PAGE_SIZE),
            )
            events = page_payload["events"]
            pagination = page_payload["pagination"]
            meta = _read_history_article_meta(rime_dir)
            history_article = {
                "content": _history_events_to_article(events),
                "events": events,
                "edited": _history_article_path(rime_dir).exists(),
                "saved_at": float(meta.get("saved_at") or 0),
                "base_event_count": int(meta.get("history_event_count") or 0),
                "appended_event_count": 0,
            }
            all_event_count = int(pagination["total"])
            truncated = bool(all_event_count > len(events))
        else:
            all_events = _read_history_events(history_path)
            normalized_limit = int(limit or 0)
            truncated = bool(normalized_limit > 0 and len(all_events) > normalized_limit)
            events = all_events[-normalized_limit:] if truncated else all_events
            history_article = _resolve_history_article_content(rime_dir, events)
            all_event_count = len(events)
            pagination = None
    except OSError as exc:
        return make_rime_context_prediction_history_unavailable(
            status="read_error",
            message=f"读取输入历史日志失败：{exc}",
            rime_dir=str(rime_dir),
            files=files,
        )

    normalized_limit = int(pagination["page_size"]) if pagination else int(limit or 0)
    content = str(history_article["content"] or "")
    stat = history_path.stat()
    status = "ready" if events else "empty"
    message = (
        "已读取输入历史修订稿。"
        if history_article["edited"]
        else "已读取输入历史并还原为文章。"
    ) if events else "输入历史日志存在，但暂时没有可展示记录。"
    return {
        "available": bool(events),
        "status": status,
        "message": message,
        "rime_dir": str(rime_dir),
        "source": HISTORY_FILE,
        "source_path": str(history_path),
        "updated_at": stat.st_mtime,
        "files": files,
        "summary": {
            "entry_count": all_event_count,
            "char_count": len(content),
            "paragraph_count": len([item for item in content.split("\n\n") if item]),
            "first_seen": str(events[0].get("timestamp") or "") if events else "",
            "last_seen": str(events[-1].get("timestamp") or "") if events else "",
            "pending_row_count": _count_data_rows(rime_dir / PENDING_FILE),
            "model_count_row_count": _count_data_rows(rime_dir / COUNTS_FILE),
            "truncated": truncated,
            "limit": normalized_limit,
            "edited": bool(history_article["edited"]),
            "saved_at": float(history_article["saved_at"] or 0),
            "base_event_count": int(history_article["base_event_count"] or 0),
            "appended_event_count": int(history_article["appended_event_count"] or 0),
        },
        "pagination": pagination,
        "content": content,
    }


def _normalize_article_text(content: str) -> str:
    text = (content or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        raise RimeContextPredictionError("文章内容不能为空。")
    if len(text) > MAX_ARTICLE_CHARS:
        raise RimeContextPredictionError(f"文章内容过长，当前上限是 {MAX_ARTICLE_CHARS} 个字符。")
    if not _CJK_RE.search(text):
        raise RimeContextPredictionError("文章内容里没有可提炼的中文。")
    return text


def _normalize_article_title(title: str | None, content: str) -> str:
    value = (title or "").strip()
    if not value:
        first_line = next((line.strip() for line in content.splitlines() if line.strip()), "")
        value = first_line[:30].strip()
    return value or "未命名文章"


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _token_to_pinyin(token: str) -> str:
    parts = lazy_pinyin(token, style=Style.NORMAL, strict=False, errors="ignore")
    return "".join(parts).replace("ü", "v").replace("u:", "v").strip().lower()


def _article_sentence_tokens(text: str) -> list[list[str]]:
    sentences: list[list[str]] = []
    for chunk in _SENTENCE_SPLIT_RE.split(text):
        chunk = chunk.strip()
        if not chunk:
            continue
        tokens: list[str] = []
        for word in jieba.cut(chunk, cut_all=False):
            for match in _CJK_RE.findall(word):
                token = match.strip()
                if token:
                    tokens.append(token[:16])
        if tokens:
            sentences.append(tokens)
    return sentences


def _extract_article_contributions(article_id: str, text: str) -> list[dict[str, Any]]:
    counts: dict[tuple[str, str, str], float] = defaultdict(float)
    for tokens in _article_sentence_tokens(text):
        for index, candidate in enumerate(tokens):
            prefix = _token_to_pinyin(candidate)
            if not prefix:
                continue
            counts[("__global", prefix, candidate)] += 1.0
            max_len = min(MAX_CONTEXT_TOKENS, index)
            for length in range(1, max_len + 1):
                context = " ".join(tokens[index - length : index])
                counts[(context, prefix, candidate)] += 1.0

    return [
        {
            "source_id": article_id,
            "context": context,
            "prefix": prefix,
            "candidate": candidate,
            "weight": weight,
        }
        for (context, prefix, candidate), weight in counts.items()
    ]


def _article_to_payload(article: dict[str, Any]) -> dict[str, Any]:
    source_type = str(article.get("source_type") or "imported_article")
    source_label = str(article.get("source_label") or "")
    if not source_label:
        source_label = "输入历史" if source_type == "device_history" else "导入文章"
    return {
        "id": str(article.get("id") or ""),
        "title": str(article.get("title") or "未命名文章"),
        "enabled": bool(article.get("enabled", True)),
        "source_type": source_type,
        "source_key": str(article.get("source_key") or ""),
        "source_label": source_label,
        "status": str(article.get("status") or "ready"),
        "row_count": int(article.get("row_count") or 0),
        "char_count": int(article.get("char_count") or 0),
        "content_hash": str(article.get("content_hash") or ""),
        "extractor_version": int(article.get("extractor_version") or ARTICLE_EXTRACTOR_VERSION),
        "created_at": float(article.get("created_at") or 0),
        "updated_at": float(article.get("updated_at") or 0),
        "processed_at": float(article.get("processed_at") or 0),
    }


def make_rime_context_prediction_articles_unavailable(
    *,
    status: str,
    message: str,
    rime_dir: str | None = None,
    files: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "available": False,
        "status": status,
        "message": message,
        "rime_dir": rime_dir,
        "files": files or [],
        "summary": {
            "article_count": 0,
            "enabled_count": 0,
            "contribution_count": 0,
        },
        "articles": [],
    }


def _article_sources_response(rime_dir: Path, manifest: dict[str, Any], files: list[dict[str, Any]]) -> dict[str, Any]:
    articles = [_article_to_payload(article) for article in manifest.get("articles", []) if isinstance(article, dict)]
    articles.sort(key=lambda item: item["updated_at"], reverse=True)
    return {
        "available": True,
        "status": "ready",
        "message": "已读取导入文章清单。",
        "rime_dir": str(rime_dir),
        "files": files,
        "summary": {
            "article_count": len(articles),
            "enabled_count": sum(1 for article in articles if article["enabled"]),
            "contribution_count": sum(article["row_count"] for article in articles if article["enabled"]),
        },
        "articles": articles,
    }


def _ensure_writable_rime_dir() -> Path:
    rime_dir = _resolve_rime_dir()
    if not rime_dir:
        raise RimeContextPredictionError("当前系统没有可识别的 Rime 用户目录位置。")
    if not rime_dir.exists():
        raise RimeContextPredictionError("该设备未发现 Rime 用户目录，可能没有安装小狼毫或尚未启动过 Rime。")
    return rime_dir


def _merge_snapshot_row(
    rows_by_key: dict[tuple[str, str], dict[str, dict[str, Any]]],
    context: str,
    prefix: str,
    candidate: str,
    weight: float,
    comment: str,
    deleted_keys: set[tuple[str, str, str]] | None = None,
    *,
    replace_existing: bool = False,
    lock_entry: bool = False,
) -> None:
    context = _clean_tsv_field(context)
    prefix = _clean_tsv_field(prefix)
    candidate = _clean_tsv_field(candidate)
    if not context or not prefix or not candidate:
        return
    if deleted_keys and (context, prefix, candidate) in deleted_keys:
        return
    key = (context, prefix)
    candidate_map = rows_by_key[key]
    existing = candidate_map.get(candidate)
    if replace_existing:
        candidate_map[candidate] = {
            "weight": float(weight),
            "comment": comment or (existing or {}).get("comment") or "",
            "locked": lock_entry or bool((existing or {}).get("locked")),
        }
        return
    if existing and existing.get("locked"):
        return
    if existing:
        existing["weight"] = float(existing.get("weight") or 0) + float(weight)
        if comment:
            existing["comment"] = comment
        return
    candidate_map[candidate] = {
        "weight": float(weight),
        "comment": comment or "",
        "locked": lock_entry,
    }


def _write_prediction_snapshot(rime_dir: Path, rows: list[tuple[str, str, str, float, str]]) -> None:
    path = rime_dir / SNAPSHOT_FILE
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write("# context_key\tpinyin_prefix\tcandidate\tweight\tcomment\n")
        for context, prefix, candidate, weight, comment in rows:
            fh.write(
                "\t".join(
                    [
                        _clean_tsv_field(context),
                        _clean_tsv_field(prefix),
                        _clean_tsv_field(candidate),
                        _format_weight(weight),
                        _clean_tsv_field(comment),
                    ]
                )
                + "\n"
            )
    os.replace(tmp, path)


def rebuild_rime_context_prediction_snapshot(
    rime_dir: Path | None = None,
    *,
    allow_snapshot_fallback: bool = False,
) -> dict[str, Any]:
    target_dir = rime_dir or _ensure_writable_rime_dir()
    manifest = _read_article_manifest(target_dir)
    deleted_keys = _read_deleted_candidate_keys(target_dir)
    enabled_ids = {
        str(article.get("id"))
        for article in manifest.get("articles", [])
        if isinstance(article, dict) and article.get("enabled", True)
    }
    article_rows = _read_article_contributions(target_dir)

    rows_by_key: dict[tuple[str, str], dict[str, dict[str, Any]]] = defaultdict(dict)
    merged_source_rows = 0
    for row in _read_prediction_rows(target_dir / SEED_FILE):
        merged_source_rows += 1
        _merge_snapshot_row(
            rows_by_key,
            row["context"],
            row["prefix"],
            row["candidate"],
            row["weight"],
            row.get("comment") or "手动规则",
            deleted_keys,
            replace_existing=_is_manual_rule_comment(row.get("comment")),
            lock_entry=_is_manual_rule_comment(row.get("comment")),
        )
    for row in _read_count_rows(target_dir / COUNTS_FILE):
        merged_source_rows += 1
        _merge_snapshot_row(
            rows_by_key,
            row["context"],
            row["prefix"],
            row["candidate"],
            row["weight"],
            row.get("comment") or "输入历史",
            deleted_keys,
        )
    for row in article_rows:
        if row["source_id"] in enabled_ids:
            merged_source_rows += 1
            _merge_snapshot_row(
                rows_by_key,
                row["context"],
                row["prefix"],
                row["candidate"],
                row["weight"],
                "导入文章",
                deleted_keys,
            )

    if allow_snapshot_fallback and not merged_source_rows:
        for row in _read_prediction_rows(target_dir / SNAPSHOT_FILE):
            _merge_snapshot_row(
                rows_by_key,
                row["context"],
                row["prefix"],
                row["candidate"],
                row["weight"],
                row.get("comment") or "",
                deleted_keys,
            )

    output: list[tuple[str, str, str, float, str]] = []
    for (context, prefix), candidate_map in rows_by_key.items():
        ranked = sorted(candidate_map.items(), key=lambda item: (-float(item[1].get("weight") or 0), item[0]))
        for candidate, entry in ranked[:DEFAULT_TOPK_PER_KEY]:
            output.append((context, prefix, candidate, float(entry.get("weight") or 0), str(entry.get("comment") or "")))
    output.sort(key=lambda row: (row[0], row[1], -row[3], row[2]))
    _write_prediction_snapshot(target_dir, output)
    return {"snapshot_rows": len(output), "enabled_article_count": len(enabled_ids)}


def delete_rime_context_prediction_candidate(
    *,
    context: str,
    prefix: str,
    candidate: str,
) -> dict[str, Any]:
    rime_dir = _ensure_writable_rime_dir()
    key = _normalize_candidate_key(context, prefix, candidate)
    rows = _read_deleted_candidate_rows(rime_dir)
    if not any((row["context"], row["prefix"], row["candidate"]) == key for row in rows):
        rows.append(
            {
                "context": key[0],
                "prefix": key[1],
                "candidate": key[2],
                "deleted_at": time.time(),
            }
        )
        _write_deleted_candidate_rows(rime_dir, rows)
    rebuild_rime_context_prediction_snapshot(rime_dir, allow_snapshot_fallback=True)
    return collect_rime_context_prediction_tree()


def update_rime_context_prediction_candidate(
    *,
    original_context: str | None,
    original_prefix: str | None,
    original_candidate: str | None,
    context: str,
    prefix: str,
    candidate: str,
    weight: float,
) -> dict[str, Any]:
    if float(weight) <= 0:
        raise RimeContextPredictionError("权重必须大于 0。")

    rime_dir = _ensure_writable_rime_dir()
    target_key = _normalize_candidate_key(context, prefix, candidate)
    original_key = None
    if original_context and original_prefix and original_candidate:
        original_key = _normalize_candidate_key(original_context, original_prefix, original_candidate)

    seed_path = rime_dir / SEED_FILE
    seed_rows = _read_prediction_rows(seed_path)
    next_seed_rows: list[dict[str, Any]] = []
    for row in seed_rows:
        row_key = _candidate_key(row.get("context"), row.get("prefix"), row.get("candidate"))
        is_manual_row = _is_manual_rule_comment(row.get("comment"))
        if is_manual_row and row_key in {key for key in [original_key, target_key] if key is not None}:
            continue
        next_seed_rows.append(row)
    next_seed_rows.append(
        {
            "context": target_key[0],
            "prefix": target_key[1],
            "candidate": target_key[2],
            "weight": float(weight),
            "comment": "手动规则",
        }
    )
    _write_prediction_rows_file(seed_path, next_seed_rows)

    deleted_rows = _read_deleted_candidate_rows(rime_dir)
    next_deleted_rows = [
        row for row in deleted_rows
        if (row["context"], row["prefix"], row["candidate"]) != target_key
    ]
    if original_key and original_key != target_key:
        if not any((row["context"], row["prefix"], row["candidate"]) == original_key for row in next_deleted_rows):
            next_deleted_rows.append(
                {
                    "context": original_key[0],
                    "prefix": original_key[1],
                    "candidate": original_key[2],
                    "deleted_at": time.time(),
                }
            )
    if len(next_deleted_rows) != len(deleted_rows) or (original_key and original_key != target_key):
        _write_deleted_candidate_rows(rime_dir, next_deleted_rows)

    rebuild_rime_context_prediction_snapshot(rime_dir, allow_snapshot_fallback=True)
    payload = collect_rime_context_prediction_tree()
    payload["message"] = "已更新候选词手动规则。"
    return payload


def refresh_rime_context_prediction_tree() -> dict[str, Any]:
    rime_dir = _ensure_writable_rime_dir()
    if _can_rebuild_from_history(rime_dir):
        refresh_result = rebuild_rime_context_prediction_from_history(rime_dir)
    else:
        refresh_result = _fold_pending_events(rime_dir)
        refresh_result.update(rebuild_rime_context_prediction_snapshot(rime_dir))
    payload = collect_rime_context_prediction_tree()
    if refresh_result.get("source") == HISTORY_FILE:
        source_label = "输入历史修订稿" if refresh_result.get("history_article_edited") else "输入历史"
        payload["message"] = (
            f"已从{source_label}重建预测索引："
            f"{int(refresh_result.get('history_events') or 0)} 条输入事件，"
            f"{int(refresh_result.get('count_entries') or 0)} 条索引记录。"
        )
    else:
        payload["message"] = (
            f"已合并增量输入并重建预测索引："
            f"{int(refresh_result.get('pending_rows') or 0)} 条待合并记录。"
        )
    return payload


def collect_rime_context_prediction_articles() -> dict[str, Any]:
    rime_dir = _resolve_rime_dir()
    files = _tracked_files(rime_dir)

    if not rime_dir:
        return make_rime_context_prediction_articles_unavailable(
            status="unsupported_platform",
            message="当前系统没有可识别的 Rime 用户目录位置。",
            files=files,
        )

    if not rime_dir.exists():
        return make_rime_context_prediction_articles_unavailable(
            status="rime_missing",
            message="该设备未发现 Rime 用户目录，可能没有安装小狼毫或尚未启动过 Rime。",
            rime_dir=str(rime_dir),
            files=files,
        )

    manifest = _read_article_manifest(rime_dir)
    return _article_sources_response(rime_dir, manifest, files)


def _upsert_article_contributions(rime_dir: Path, article: dict[str, Any], content: str) -> None:
    existing = [row for row in _read_article_contributions(rime_dir) if row["source_id"] != article["id"]]
    rows = _extract_article_contributions(str(article["id"]), content)
    article["row_count"] = len(rows)
    article["status"] = "ready"
    article["processed_at"] = time.time()
    article["extractor_version"] = ARTICLE_EXTRACTOR_VERSION
    _write_article_contributions(rime_dir, existing + rows)


def import_rime_context_prediction_article(
    *,
    title: str | None,
    content: str,
    enabled: bool = True,
    source_type: str = "imported_article",
    source_key: str | None = None,
    source_label: str | None = None,
) -> dict[str, Any]:
    rime_dir = _ensure_writable_rime_dir()
    text = _normalize_article_text(content)
    now = time.time()
    digest = _content_hash(text)
    normalized_source_type = _clean_tsv_field(source_type or "imported_article") or "imported_article"
    normalized_source_key = _clean_tsv_field(source_key or "")
    normalized_source_label = _clean_tsv_field(source_label or "")
    manifest = _read_article_manifest(rime_dir)
    articles = manifest.setdefault("articles", [])

    target_article = None
    if normalized_source_key:
        for article in articles:
            if (
                isinstance(article, dict)
                and str(article.get("source_type") or "imported_article") == normalized_source_type
                and str(article.get("source_key") or "") == normalized_source_key
            ):
                target_article = article
                break
    else:
        for article in articles:
            if (
                isinstance(article, dict)
                and str(article.get("source_type") or "imported_article") == "imported_article"
                and article.get("content_hash") == digest
            ):
                target_article = article
                break

    if target_article is None:
        article_id = uuid.uuid4().hex[:16]
        content_dir = rime_dir / ARTICLE_CONTENT_DIR
        content_dir.mkdir(parents=True, exist_ok=True)
        content_path = content_dir / f"{article_id}.txt"
        content_path.write_text(text, encoding="utf-8")
        target_article = {
            "id": article_id,
            "title": _normalize_article_title(title, text),
            "enabled": bool(enabled),
            "source_type": normalized_source_type,
            "source_key": normalized_source_key,
            "source_label": normalized_source_label,
            "content_hash": digest,
            "content_path": f"{ARTICLE_CONTENT_DIR}/{article_id}.txt",
            "char_count": len(text),
            "row_count": 0,
            "status": "pending",
            "created_at": now,
            "updated_at": now,
            "processed_at": 0,
            "extractor_version": ARTICLE_EXTRACTOR_VERSION,
        }
        articles.append(target_article)
        _upsert_article_contributions(rime_dir, target_article, text)
    else:
        old_digest = str(target_article.get("content_hash") or "")
        target_article["title"] = _normalize_article_title(title, text)
        target_article["enabled"] = bool(enabled)
        target_article["source_type"] = normalized_source_type
        target_article["source_key"] = normalized_source_key
        target_article["source_label"] = normalized_source_label
        target_article["content_hash"] = digest
        target_article["updated_at"] = now
        target_article["char_count"] = len(text)
        content_path = _article_content_path(rime_dir, target_article)
        content_path.parent.mkdir(parents=True, exist_ok=True)
        content_path.write_text(text, encoding="utf-8")
        if (
            old_digest != digest
            or target_article.get("extractor_version") != ARTICLE_EXTRACTOR_VERSION
            or not int(target_article.get("row_count") or 0)
        ):
            _upsert_article_contributions(rime_dir, target_article, text)

    _write_article_manifest(rime_dir, manifest)
    rebuild_rime_context_prediction_snapshot(rime_dir)
    return _article_sources_response(rime_dir, manifest, _tracked_files(rime_dir))


def update_rime_context_prediction_article(
    article_id: str,
    *,
    enabled: bool | None = None,
    title: str | None = None,
) -> dict[str, Any]:
    rime_dir = _ensure_writable_rime_dir()
    manifest = _read_article_manifest(rime_dir)
    article = next(
        (
            item
            for item in manifest.get("articles", [])
            if isinstance(item, dict) and str(item.get("id") or "") == article_id
        ),
        None,
    )
    if not article:
        raise RimeContextPredictionError("未找到这篇导入文章。")

    if enabled is not None:
        article["enabled"] = bool(enabled)
    if title is not None and title.strip():
        article["title"] = title.strip()
    article["updated_at"] = time.time()
    _write_article_manifest(rime_dir, manifest)
    rebuild_rime_context_prediction_snapshot(rime_dir)
    return _article_sources_response(rime_dir, manifest, _tracked_files(rime_dir))


def delete_rime_context_prediction_article(article_id: str) -> dict[str, Any]:
    rime_dir = _ensure_writable_rime_dir()
    manifest = _read_article_manifest(rime_dir)
    articles = manifest.get("articles", [])
    kept_articles = []
    removed: dict[str, Any] | None = None
    for article in articles:
        if isinstance(article, dict) and str(article.get("id") or "") == article_id:
            removed = article
        else:
            kept_articles.append(article)
    if removed is None:
        raise RimeContextPredictionError("未找到这篇导入文章。")

    manifest["articles"] = kept_articles
    _write_article_manifest(rime_dir, manifest)
    _write_article_contributions(
        rime_dir,
        [row for row in _read_article_contributions(rime_dir) if row["source_id"] != article_id],
    )
    content_path = _article_content_path(rime_dir, removed)
    content_path.unlink(missing_ok=True)
    rebuild_rime_context_prediction_snapshot(rime_dir)
    return _article_sources_response(rime_dir, manifest, _tracked_files(rime_dir))


def _summarize_rows(rows: list[dict[str, Any]]) -> dict[str, int]:
    contexts = {str(row.get("context") or "") for row in rows}
    prefixes = {(str(row.get("context") or ""), str(row.get("prefix") or "")) for row in rows}
    candidates = {
        (
            str(row.get("context") or ""),
            str(row.get("prefix") or ""),
            str(row.get("candidate") or ""),
        )
        for row in rows
    }
    return {
        "row_count": len(rows),
        "context_count": len(contexts),
        "prefix_count": len(prefixes),
        "candidate_count": len(candidates),
    }


def make_rime_context_prediction_unavailable(
    *,
    status: str,
    message: str,
    rime_dir: str | None = None,
    files: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "available": False,
        "status": status,
        "message": message,
        "rime_dir": rime_dir,
        "source": None,
        "source_path": None,
        "updated_at": None,
        "files": files or [],
        "summary": {
            "row_count": 0,
            "context_count": 0,
            "prefix_count": 0,
            "candidate_count": 0,
        },
        "rows": [],
    }


def collect_rime_context_prediction_tree(limit: int | None = 5000) -> dict[str, Any]:
    rime_dir = _resolve_rime_dir()
    files = _tracked_files(rime_dir)

    if not rime_dir:
        return make_rime_context_prediction_unavailable(
            status="unsupported_platform",
            message="当前系统没有可识别的 Rime 用户目录位置。",
            files=files,
        )

    if not rime_dir.exists():
        return make_rime_context_prediction_unavailable(
            status="rime_missing",
            message="该设备未发现 Rime 用户目录，可能没有安装小狼毫或尚未启动过 Rime。",
            rime_dir=str(rime_dir),
            files=files,
        )

    source_candidates = [
        (SNAPSHOT_FILE, rime_dir / SNAPSHOT_FILE),
        (COUNTS_FILE, rime_dir / COUNTS_FILE),
        (SEED_FILE, rime_dir / SEED_FILE),
    ]
    source_name = None
    source_path = None
    rows: list[dict[str, Any]] = []
    read_error = None

    for name, path in source_candidates:
        if not path.exists():
            continue
        source_name = name
        source_path = path
        try:
            rows = _read_prediction_rows(path, limit=limit)
        except OSError as exc:
            read_error = str(exc)
            rows = []
        break

    if not source_path:
        return make_rime_context_prediction_unavailable(
            status="extension_missing",
            message="该设备已发现 Rime 用户目录，但没有上下文预测扩展的数据文件。",
            rime_dir=str(rime_dir),
            files=files,
        )

    if read_error:
        return make_rime_context_prediction_unavailable(
            status="read_error",
            message=f"读取上下文预测索引失败：{read_error}",
            rime_dir=str(rime_dir),
            files=files,
        )

    summary = _summarize_rows(rows)
    status = "ready" if rows else "empty"
    message = "已读取上下文预测索引。" if rows else "上下文预测索引文件存在，但暂时没有可展示记录。"
    stat = source_path.stat()
    return {
        "available": bool(rows),
        "status": status,
        "message": message,
        "rime_dir": str(rime_dir),
        "source": source_name,
        "source_path": str(source_path),
        "updated_at": stat.st_mtime,
        "files": files,
        "summary": summary,
        "rows": rows,
    }
