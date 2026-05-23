from __future__ import annotations

import csv
import re
from collections import Counter
from functools import lru_cache
from pathlib import Path
from threading import RLock
from typing import Any

from backend.core.fanxiu_resources import FanxiuResourceError, resolve_fanxiu_export_root


_LUA_NUMERIC_ENTRY_RE = re.compile(r"^\[(?P<key>\d+)\]='(?P<text>.*)',\s*$")
_LUA_STRING_ENTRY_RE = re.compile(r"^\['(?P<key>(?:\\'|[^'])+)'\]='(?P<text>.*)',\s*$")
_RICH_TAG_RE = re.compile(r"<[^>]+>")
_BRACKET_TERM_RE = re.compile(r"【([^】]{1,30})】")
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
_TEXT_ENTRIES_LOCK = RLock()


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _indexes_dir(export_root: str | Path | None = None) -> Path:
    return resolve_fanxiu_export_root(export_root) / "indexes"


def _read_tsv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return [dict(row) for row in csv.DictReader(f, delimiter="\t")]


def _unescape_lua_string(value: str) -> str:
    result: list[str] = []
    i = 0
    while i < len(value):
        ch = value[i]
        if ch != "\\" or i + 1 >= len(value):
            result.append(ch)
            i += 1
            continue
        nxt = value[i + 1]
        if nxt == "n":
            result.append("\n")
        elif nxt == "r":
            result.append("\r")
        elif nxt == "t":
            result.append("\t")
        elif nxt in {"\\", "'", '"'}:
            result.append(nxt)
        else:
            result.append("\\")
            result.append(nxt)
        i += 2
    return "".join(result)


def strip_fanxiu_rich_text(value: str) -> str:
    return _RICH_TAG_RE.sub("", value or "")


def _preview_text(value: str, limit: int = 220) -> str:
    text = re.sub(r"\s+", " ", value or "").strip()
    return text if len(text) <= limit else f"{text[:limit].rstrip()}..."


def _first_terms(text: str, *, limit: int = 6) -> list[str]:
    seen: set[str] = set()
    terms: list[str] = []
    for match in _BRACKET_TERM_RE.finditer(text):
        term = match.group(1).strip()
        if not term or term in seen:
            continue
        seen.add(term)
        terms.append(term)
        if len(terms) >= limit:
            break
    return terms


def _guess_title(key: str, text: str) -> str:
    clean = strip_fanxiu_rich_text(text).strip()
    first_line = next((line.strip() for line in clean.splitlines() if line.strip()), "")
    if first_line and len(first_line) <= 28:
        return first_line
    terms = _first_terms(clean, limit=1)
    if terms:
        return f"【{terms[0]}】"
    return _preview_text(first_line or clean or key, 32)


def _classify_text(asset: str, text: str) -> str:
    clean = strip_fanxiu_rich_text(text)
    if asset == "localization.lua":
        return "界面文案"
    if "\n" not in clean and len(clean) <= 24:
        return "名称"
    if "效果" in clean or "属性" in clean or "加成" in clean:
        return "效果"
    if "活动" in clean or "倒计时" in clean:
        return "活动"
    if "获得" in clean or "概率" in clean or "奖励" in clean:
        return "规则"
    return "文本"


def _is_config_like_text(value: str) -> bool:
    text = value.strip()
    return text.startswith("BD:") or text.startswith("BD：") or ("|" in text and len(text) > 60)


def _display_text_kind(entry: dict[str, Any]) -> str:
    category = str(entry.get("category") or "")
    title = str(entry.get("title") or "")
    plain_text = str(entry.get("plain_text") or "")
    if category == "名称":
        return "名字"
    if "效果" in title or "效果" in plain_text:
        return "效果"
    if _is_config_like_text(plain_text):
        return "配置"
    return category or "文本"


@lru_cache(maxsize=16)
def _load_lua_text_entries_cached(
    path_text: str,
    mtime_ns: int,
    size: int,
    source: str,
    asset: str,
) -> tuple[dict[str, Any], ...]:
    path = Path(path_text)
    entries: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig", errors="replace") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.rstrip("\n\r")
            match = _LUA_NUMERIC_ENTRY_RE.match(line) or _LUA_STRING_ENTRY_RE.match(line)
            if not match:
                continue
            key = _unescape_lua_string(match.group("key"))
            rich_text = _unescape_lua_string(match.group("text"))
            plain_text = strip_fanxiu_rich_text(rich_text)
            terms = _first_terms(plain_text)
            entries.append(
                {
                    "id": f"{asset}:{key}",
                    "source": source,
                    "asset": asset,
                    "key": key,
                    "title": _guess_title(key, rich_text),
                    "category": _classify_text(asset, rich_text),
                    "terms": terms,
                    "plain_text": plain_text,
                    "rich_text": rich_text,
                    "line_no": line_no,
                }
            )
    return tuple(entries)


def _load_lua_text_entries(path: Path, source: str, asset: str) -> list[dict[str, Any]]:
    stat = path.stat()
    return list(_load_lua_text_entries_cached(str(path), stat.st_mtime_ns, stat.st_size, source, asset))


def _text_asset_rows(export_root: str | Path | None = None) -> list[dict[str, str]]:
    rows = _read_tsv(_indexes_dir(export_root) / "text_assets.tsv")
    root = resolve_fanxiu_export_root(export_root)
    valid_rows: list[dict[str, str]] = []
    for row in rows:
        path = Path(row.get("path") or "").expanduser().resolve()
        if path.is_file() and _is_relative_to(path, root):
            valid_rows.append(row)
    return valid_rows


TextAssetSignature = tuple[tuple[str, int, int, str, str], ...]


def _text_asset_signature(export_root: str | Path | None = None) -> TextAssetSignature:
    signature: list[tuple[str, int, int, str, str]] = []
    for row in _text_asset_rows(export_root):
        path = Path(row["path"]).expanduser().resolve()
        asset = row.get("asset") or path.name
        if asset not in {"lang.lua", "localization.lua"}:
            continue
        stat = path.stat()
        signature.append((
            str(path),
            stat.st_mtime_ns,
            stat.st_size,
            row.get("source") or "",
            asset,
        ))
    return tuple(signature)


@lru_cache(maxsize=8)
def _load_text_entries_by_signature(signature: TextAssetSignature) -> tuple[dict[str, Any], ...]:
    entries: list[dict[str, Any]] = []
    for path_text, mtime_ns, size, source, asset in signature:
        entries.extend(_load_lua_text_entries_cached(path_text, mtime_ns, size, source, asset))
    return tuple(entries)


@lru_cache(maxsize=8)
def _text_entry_index_by_signature(signature: TextAssetSignature) -> dict[tuple[str, str], dict[str, Any]]:
    return {
        (entry["asset"], str(entry["key"])): entry
        for entry in _load_text_entries_by_signature(signature)
    }


@lru_cache(maxsize=8)
def _text_catalog_counts_by_signature(signature: TextAssetSignature) -> dict[str, Any]:
    entries = _load_text_entries_by_signature(signature)
    by_asset = Counter(item["asset"] for item in entries)
    by_category = Counter(item["category"] for item in entries)
    by_display_kind = Counter(_display_text_kind(item) for item in entries)
    return {
        "text_count": len(entries),
        "text_assets": dict(by_asset.most_common()),
        "text_categories": dict(by_category.most_common()),
        "text_display_kinds": dict(by_display_kind.most_common()),
    }


@lru_cache(maxsize=8)
def _text_search_docs_by_signature(signature: TextAssetSignature) -> tuple[dict[str, Any], ...]:
    docs: list[dict[str, Any]] = []
    for entry in _load_text_entries_by_signature(signature):
        haystacks = {
            "key": str(entry.get("key", "")).lower(),
            "title": str(entry.get("title", "")).lower(),
            "plain": str(entry.get("plain_text", "")).lower(),
            "source": str(entry.get("source", "")).lower(),
        }
        docs.append(
            {
                "entry": entry,
                "haystacks": haystacks,
                "combined": " ".join(haystacks.values()),
                "dedupe_key": _dedupe_text_entry_key(entry),
                "display_kind": _display_text_kind(entry),
            }
        )
    return tuple(docs)


def load_fanxiu_wiki_text_entries(export_root: str | Path | None = None) -> list[dict[str, Any]]:
    signature = _text_asset_signature(export_root)
    with _TEXT_ENTRIES_LOCK:
        return list(_load_text_entries_by_signature(signature))


def build_fanxiu_wiki_catalog(export_root: str | Path | None = None) -> dict[str, Any]:
    root = resolve_fanxiu_export_root(export_root)
    signature = _text_asset_signature(export_root)
    with _TEXT_ENTRIES_LOCK:
        text_counts = _text_catalog_counts_by_signature(signature)
    galleries = {
        "meaningful_textures": len(_read_tsv(_indexes_dir(export_root) / "meaningful_textures.tsv")),
        "sprites": len(_read_tsv(_indexes_dir(export_root) / "sprites.tsv")),
        "model_textures": len(_read_tsv(_indexes_dir(export_root) / "model_textures.tsv")),
    }
    return {
        "export_root": str(root),
        "exists": root.exists(),
        **text_counts,
        "galleries": galleries,
    }


def _score_text_doc(doc: dict[str, Any], terms: tuple[str, ...]) -> int:
    entry = doc["entry"]
    if not terms:
        return 1
    haystacks = doc["haystacks"]
    if not all(term in doc["combined"] for term in terms):
        return 0
    score = 0
    for term in terms:
        if haystacks["key"] == term:
            score += 120
        if term in haystacks["title"]:
            score += 50
        if term in haystacks["plain"]:
            score += 12 + min(haystacks["plain"].count(term), 8)
        if term in haystacks["source"]:
            score += 4
    plain_text = haystacks["plain"]
    rich_text = str(entry.get("rich_text", ""))
    if len(plain_text) <= 80:
        score += 8
    if entry.get("category") == "效果":
        score += 40
    if "<color=" in rich_text:
        score += 8
    if any(marker in plain_text for marker in ("十星效果", "十阶效果", "一阶效果", "满阶效果")):
        score += 24
    if entry.get("category") == "名称" and re.search(r"\d+重$", str(entry.get("title", ""))):
        score -= 18
    if plain_text.startswith("BD:") or plain_text.startswith("BD："):
        score -= 40
    if "|" in plain_text and len(plain_text) > 80:
        score -= 70
    return score


def _score_text_entry(entry: dict[str, Any], terms: list[str]) -> int:
    lowered_terms = tuple(term.lower() for term in terms)
    haystacks = {
        "key": str(entry.get("key", "")).lower(),
        "title": str(entry.get("title", "")).lower(),
        "plain": str(entry.get("plain_text", "")).lower(),
        "source": str(entry.get("source", "")).lower(),
    }
    return _score_text_doc({
        "entry": entry,
        "haystacks": haystacks,
        "combined": " ".join(haystacks.values()),
    }, lowered_terms)


def _dedupe_text_entry_key(entry: dict[str, Any]) -> str:
    """Collapse rows whose visible text is exactly identical in search lists."""
    plain_text = str(entry.get("plain_text", ""))
    return "\n".join(line.strip() for line in plain_text.replace("\r\n", "\n").replace("\r", "\n").splitlines()).strip()


def _first_difference_preview(reference: dict[str, Any], entry: dict[str, Any]) -> str:
    if reference["id"] == entry["id"]:
        return ""
    reference_lines = [line.strip() for line in str(reference.get("plain_text", "")).splitlines() if line.strip()]
    entry_lines = [line.strip() for line in str(entry.get("plain_text", "")).splitlines() if line.strip()]
    for index, line in enumerate(entry_lines):
        if index >= len(reference_lines) or line != reference_lines[index]:
            return _preview_text(line, 90)
    if len(reference_lines) != len(entry_lines):
        return "末尾内容不同"
    return ""


def _format_text_search_item(
    score: int,
    entry: dict[str, Any],
    variants: list[dict[str, Any]],
) -> dict[str, Any]:
    variant_items = [_format_text_variant(item, reference=entry) for item in variants]
    variant_keys = [item["locator"] for item in variant_items]
    return {
        "id": entry["id"],
        "source": entry["source"],
        "asset": entry["asset"],
        "key": entry["key"],
        "title": entry["title"],
        "category": entry["category"],
        "display_kind": _display_text_kind(entry),
        "terms": entry["terms"],
        "plain_preview": _preview_text(entry["plain_text"]),
        "rich_preview": _preview_text(entry["rich_text"], 320),
        "line_no": entry["line_no"],
        "score": score,
        "duplicate_count": len(variants),
        "duplicate_keys": variant_keys,
        "same_title_count": len(variants),
        "variant_preview": "",
        "variants": variant_items,
    }


def _format_text_variant(entry: dict[str, Any], *, reference: dict[str, Any]) -> dict[str, Any]:
    variant_preview = _first_difference_preview(reference, entry)
    if not variant_preview and reference["id"] != entry["id"] and reference.get("rich_text") != entry.get("rich_text"):
        variant_preview = "可见文案相同，富文本标签不同"
    return {
        "id": entry["id"],
        "source": entry["source"],
        "asset": entry["asset"],
        "key": entry["key"],
        "locator": f"{entry['asset']}:{entry['key']}",
        "title": entry["title"],
        "category": entry["category"],
        "display_kind": _display_text_kind(entry),
        "terms": entry["terms"],
        "plain_preview": _preview_text(entry["plain_text"]),
        "rich_preview": _preview_text(entry["rich_text"], 180),
        "line_no": entry["line_no"],
        "variant_preview": variant_preview,
    }


@lru_cache(maxsize=64)
def _search_text_groups_by_signature(
    signature: TextAssetSignature,
    query: str,
    asset: str,
    category: str,
    display_kind: str,
) -> tuple[tuple[int, dict[str, Any], tuple[dict[str, Any], ...]], ...]:
    terms = tuple(item.strip().lower() for item in re.split(r"\s+", query or "") if item.strip())
    rows: list[tuple[int, dict[str, Any], str]] = []
    for doc in _text_search_docs_by_signature(signature):
        entry = doc["entry"]
        if asset != "all" and entry["asset"] != asset:
            continue
        if category != "all" and entry["category"] != category:
            continue
        if display_kind != "all" and doc["display_kind"] != display_kind:
            continue
        score = _score_text_doc(doc, terms)
        if score <= 0:
            continue
        group_key = str(entry.get("title") or doc["dedupe_key"] or entry["id"]).strip()
        rows.append((score, entry, group_key))

    rows.sort(key=lambda item: (-item[0], item[1]["asset"], str(item[1]["key"])))
    grouped_rows: list[tuple[int, dict[str, Any], list[dict[str, Any]]]] = []
    grouped_index: dict[str, int] = {}
    for score, entry, group_key in rows:
        if group_key in grouped_index:
            grouped_rows[grouped_index[group_key]][2].append(entry)
            continue
        grouped_index[group_key] = len(grouped_rows)
        grouped_rows.append((score, entry, [entry]))

    return tuple((score, entry, tuple(variants)) for score, entry, variants in grouped_rows)


def search_fanxiu_wiki_texts(
    *,
    query: str = "",
    asset: str = "all",
    category: str = "all",
    display_kind: str = "all",
    limit: int = 50,
    offset: int = 0,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    signature = _text_asset_signature(export_root)
    with _TEXT_ENTRIES_LOCK:
        grouped_rows = _search_text_groups_by_signature(signature, query, asset, category, display_kind)

    page_rows = grouped_rows[offset : offset + limit]
    return {
        "query": query,
        "asset": asset,
        "category": category,
        "display_kind": display_kind,
        "limit": limit,
        "offset": offset,
        "total": len(grouped_rows),
        "raw_total": sum(len(variants) for _score, _entry, variants in grouped_rows),
        "items": [
            _format_text_search_item(score, entry, list(variants))
            for score, entry, variants in page_rows
        ],
    }


def get_fanxiu_wiki_text_entry(
    *,
    asset: str,
    key: str,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    signature = _text_asset_signature(export_root)
    with _TEXT_ENTRIES_LOCK:
        entry = _text_entry_index_by_signature(signature).get((asset, str(key)))
    if entry:
        return entry
    raise FanxiuResourceError(f"没有找到图鉴文本：{asset}:{key}")


def _gallery_rows(kind: str, export_root: str | Path | None = None) -> list[dict[str, str]]:
    index_name = {
        "texture": "meaningful_textures.tsv",
        "sprite": "sprites.tsv",
        "model_texture": "model_textures.tsv",
    }.get(kind)
    if not index_name:
        return []
    return _read_tsv(_indexes_dir(export_root) / index_name)


def search_fanxiu_wiki_gallery(
    *,
    query: str = "",
    kind: str = "all",
    limit: int = 60,
    offset: int = 0,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_export_root(export_root)
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    kinds = ["texture", "sprite", "model_texture"] if kind == "all" else [kind]
    terms = [item.strip().lower() for item in re.split(r"\s+", query or "") if item.strip()]
    rows: list[dict[str, Any]] = []

    for row_kind in kinds:
        for row in _gallery_rows(row_kind, export_root):
            path = Path(row.get("path") or "").expanduser().resolve()
            if not path.is_file() or path.suffix.lower() not in _IMAGE_SUFFIXES or not _is_relative_to(path, root):
                continue
            haystack = " ".join(
                str(row.get(field, ""))
                for field in ("group", "source", "name", "width", "height")
            ).lower()
            if terms and not all(term in haystack for term in terms):
                continue
            rows.append(
                {
                    "kind": row_kind,
                    "group": row.get("group", ""),
                    "source": row.get("source", ""),
                    "name": row.get("name", path.stem),
                    "width": int(row.get("width") or 0),
                    "height": int(row.get("height") or 0),
                    "path": str(path),
                }
            )

    rows.sort(key=lambda item: (item["kind"], item["group"], item["name"], item["path"]))
    return {
        "query": query,
        "kind": kind,
        "limit": limit,
        "offset": offset,
        "total": len(rows),
        "items": rows[offset : offset + limit],
    }


def resolve_fanxiu_wiki_media_path(path: str, export_root: str | Path | None = None) -> Path:
    root = resolve_fanxiu_export_root(export_root)
    media_path = Path(path).expanduser().resolve()
    if not _is_relative_to(media_path, root):
        raise FanxiuResourceError(f"媒体路径必须位于导出目录内：{root}")
    if not media_path.is_file() or media_path.suffix.lower() not in _IMAGE_SUFFIXES:
        raise FanxiuResourceError(f"媒体文件不存在或格式不支持：{media_path}")
    return media_path
