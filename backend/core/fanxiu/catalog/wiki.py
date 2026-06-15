from __future__ import annotations

import csv
import re
from collections import Counter
from functools import lru_cache
from pathlib import Path
from threading import RLock
from typing import Any

from backend.core.fanxiu.catalog.resources import FanxiuResourceError, resolve_fanxiu_export_root


_LUA_NUMERIC_ENTRY_RE = re.compile(r"^\[(?P<key>\d+)\]='(?P<text>.*)',\s*$")
_LUA_STRING_ENTRY_RE = re.compile(r"^\['(?P<key>(?:\\'|[^'])+)'\]='(?P<text>.*)',\s*$")
_RICH_TAG_RE = re.compile(r"<[^>]+>")
_BRACKET_TERM_RE = re.compile(r"【([^】]{1,30})】")
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
_TEXT_ENTRIES_LOCK = RLock()
_BUNDLE_HASH_SUFFIX_RE = re.compile(r"_[0-9a-f]{32}$", re.IGNORECASE)


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


def _iter_tsv(path: Path):
    if not path.exists():
        return
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        yield from csv.DictReader(f, delimiter="\t")


def _coerce_export_media_path(path_text: str, root: Path) -> Path | None:
    if not path_text:
        return None
    path = Path(path_text).expanduser()
    if not path.is_absolute():
        path = root / path
    try:
        path = path.resolve()
    except OSError:
        return None
    if path.suffix.lower() not in _IMAGE_SUFFIXES or not _is_relative_to(path, root):
        return None
    return path


def _coerce_export_media_path_text(path_text: str, root: Path) -> str:
    if not path_text:
        return ""
    path = Path(path_text).expanduser()
    if not path.is_absolute():
        path = root / path
    try:
        path_text = str(path.resolve())
    except OSError:
        return ""
    if Path(path_text).suffix.lower() not in _IMAGE_SUFFIXES:
        return ""
    root_text = str(root.resolve()).rstrip("\\/") + "\\"
    normalized = path_text.replace("/", "\\")
    if not normalized.startswith(root_text):
        return ""
    return path_text


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


def _iter_lua_text_entries_from_path(path: Path, source: str, asset: str):
    with path.open("r", encoding="utf-8-sig", errors="replace") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.rstrip("\n\r")
            match = _LUA_NUMERIC_ENTRY_RE.match(line) or _LUA_STRING_ENTRY_RE.match(line)
            if not match:
                continue
            key = _unescape_lua_string(match.group("key"))
            rich_text = _unescape_lua_string(match.group("text"))
            plain_text = strip_fanxiu_rich_text(rich_text)
            yield {
                "id": f"{asset}:{key}",
                "source": source,
                "asset": asset,
                "key": key,
                "title": _guess_title(key, rich_text),
                "category": _classify_text(asset, rich_text),
                "terms": _first_terms(plain_text),
                "plain_text": plain_text,
                "rich_text": rich_text,
                "line_no": line_no,
            }


def _text_asset_rows(export_root: str | Path | None = None) -> list[dict[str, str]]:
    rows = _read_tsv(_indexes_dir(export_root) / "text_assets.tsv")
    root = resolve_fanxiu_export_root(export_root)
    valid_rows: list[dict[str, str]] = []
    for row in rows:
        path = Path(row.get("path") or "").expanduser().resolve()
        if path.is_file() and _is_relative_to(path, root):
            valid_rows.append(row)
    if valid_rows:
        return valid_rows

    candidates: dict[tuple[str, str], tuple[int, dict[str, str]]] = {}
    for path in sorted(root.glob("by_source/**/text_assets/*.lua"), key=lambda item: str(item).lower()):
        asset = path.name
        if asset not in {"lang.lua", "localization.lua"}:
            continue
        try:
            rel_source = path.relative_to(root / "by_source")
        except ValueError:
            rel_source = path.relative_to(root)
        source_dir = rel_source.parent.parent
        source_key = _BUNDLE_HASH_SUFFIX_RE.sub("", str(source_dir).replace("\\", "/"))
        priority = 1 if _BUNDLE_HASH_SUFFIX_RE.search(source_dir.name) else 0
        row = {
            "source": str(source_dir),
            "asset": asset,
            "entries": "",
            "path": str(path.resolve()),
        }
        key = (source_key, asset)
        existing = candidates.get(key)
        if existing is None or priority > existing[0]:
            candidates[key] = (priority, row)
    return [
        row
        for _priority, row in sorted(
            candidates.values(),
            key=lambda item: (str(item[1].get("source", "")).lower(), str(item[1].get("asset", "")).lower()),
        )
    ]


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
    if not signature:
        return {
            "text_count": 0,
            "text_assets": {},
            "text_categories": {},
            "text_display_kinds": {},
        }
    entries: list[dict[str, Any]] = []
    for path_text, mtime_ns, size, source, asset in signature:
        entries.extend(_sample_lua_text_entries_cached(path_text, mtime_ns, size, source, asset, 5000))
    by_asset = Counter(item["asset"] for item in entries)
    by_category = Counter(item["category"] for item in entries)
    by_display_kind = Counter(_display_text_kind(item) for item in entries)
    return {
        "text_count": _count_lua_text_entries_by_signature(signature),
        "text_assets": dict(by_asset.most_common()),
        "text_categories": dict(by_category.most_common()),
        "text_display_kinds": dict(by_display_kind.most_common()),
    }


@lru_cache(maxsize=16)
def _count_lua_text_entries_cached(path_text: str, mtime_ns: int, size: int) -> int:
    del mtime_ns, size
    count = 0
    with Path(path_text).open("r", encoding="utf-8-sig", errors="replace") as f:
        for line in f:
            if _LUA_NUMERIC_ENTRY_RE.match(line.rstrip("\n\r")) or _LUA_STRING_ENTRY_RE.match(line.rstrip("\n\r")):
                count += 1
    return count


def _count_lua_text_entries_by_signature(signature: TextAssetSignature) -> int:
    return sum(
        _count_lua_text_entries_cached(path_text, mtime_ns, size)
        for path_text, mtime_ns, size, _source, _asset in signature
    )


@lru_cache(maxsize=16)
def _sample_lua_text_entries_cached(
    path_text: str,
    mtime_ns: int,
    size: int,
    source: str,
    asset: str,
    limit: int,
) -> tuple[dict[str, Any], ...]:
    del mtime_ns, size
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
            entries.append(
                {
                    "id": f"{asset}:{key}",
                    "source": source,
                    "asset": asset,
                    "key": key,
                    "title": _guess_title(key, rich_text),
                    "category": _classify_text(asset, rich_text),
                    "terms": _first_terms(plain_text),
                    "plain_text": plain_text,
                    "rich_text": rich_text,
                    "line_no": line_no,
                }
            )
            if len(entries) >= limit:
                break
    return tuple(entries)


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
        "meaningful_textures": _gallery_count("texture", export_root),
        "sprites": _gallery_count("sprite", export_root),
        "model_textures": _gallery_count("model_texture", export_root),
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


def _search_empty_text_page_by_signature(
    signature: TextAssetSignature,
    *,
    asset: str,
    category: str,
    display_kind: str,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    if category == "all" and display_kind == "all":
        matched_signature = tuple(
            item for item in signature if asset == "all" or item[4] == asset
        )
        total = _count_lua_text_entries_by_signature(matched_signature)
        page_entries: list[dict[str, Any]] = []
        seen = 0
        for path_text, _mtime_ns, _size, source, row_asset in matched_signature:
            for entry in _iter_lua_text_entries_from_path(Path(path_text), source, row_asset):
                if seen >= offset and len(page_entries) < limit:
                    page_entries.append(entry)
                    if len(page_entries) >= limit:
                        break
                seen += 1
            if len(page_entries) >= limit:
                break
        return {
            "total": total,
            "raw_total": total,
            "items": [
                _format_text_search_item(1, entry, [entry])
                for entry in page_entries
            ],
        }

    total = 0
    page_entries: list[dict[str, Any]] = []
    for path_text, _mtime_ns, _size, source, row_asset in signature:
        if asset != "all" and row_asset != asset:
            continue
        for entry in _iter_lua_text_entries_from_path(Path(path_text), source, row_asset):
            if category != "all" and entry["category"] != category:
                continue
            row_display_kind = _display_text_kind(entry)
            if display_kind != "all" and row_display_kind != display_kind:
                continue
            if total >= offset and len(page_entries) < limit:
                page_entries.append(entry)
            total += 1
    return {
        "total": total,
        "raw_total": total,
        "items": [
            _format_text_search_item(1, entry, [entry])
            for entry in page_entries
        ],
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
    if not str(query or "").strip():
        page = _search_empty_text_page_by_signature(
            signature,
            asset=asset,
            category=category,
            display_kind=display_kind,
            limit=limit,
            offset=offset,
        )
        return {
            "query": query,
            "asset": asset,
            "category": category,
            "display_kind": display_kind,
            "limit": limit,
            "offset": offset,
            **page,
        }

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


GallerySignature = tuple[str, str, int, int]


def _gallery_signature(kind: str, export_root: str | Path | None = None) -> GallerySignature | None:
    index_name = {
        "texture": "meaningful_textures.tsv",
        "sprite": "sprites.tsv",
        "model_texture": "model_textures.tsv",
    }.get(kind)
    if not index_name:
        return None
    root = resolve_fanxiu_export_root(export_root)
    legacy_path = _indexes_dir(export_root) / index_name
    if legacy_path.exists():
        stat = legacy_path.stat()
        return (kind, str(legacy_path.resolve()), stat.st_mtime_ns, stat.st_size)

    visual_dir = root / "parsed_configs" / "visual_catalog"
    modern_index = {
        "texture": "visual_asset_catalog.tsv",
        "sprite": "atlas_sprite_catalog.tsv",
        "model_texture": "apk_visual_assets.tsv",
    }[kind]
    modern_path = visual_dir / modern_index
    if not modern_path.exists():
        return None
    stat = modern_path.stat()
    return (kind, str(modern_path.resolve()), stat.st_mtime_ns, stat.st_size)


def _is_legacy_gallery_signature(signature: GallerySignature) -> bool:
    return Path(signature[1]).name in {"meaningful_textures.tsv", "sprites.tsv", "model_textures.tsv"}


def _gallery_row_from_modern_tsv(row: dict[str, str], root: Path) -> dict[str, str] | None:
    path_text = row.get("image_path") or row.get("path") or ""
    media_path_text = _coerce_export_media_path_text(path_text, root)
    if not media_path_text:
        return None
    return {
        "group": row.get("atlas_key") or row.get("asset_group") or row.get("category") or "",
        "source": row.get("relative_source_path") or row.get("source") or "",
        "name": row.get("name") or Path(media_path_text).stem,
        "width": row.get("width") or "0",
        "height": row.get("height") or "0",
        "path": media_path_text,
    }


def _gallery_row_from_legacy_tsv(row: dict[str, str], root: Path) -> dict[str, str] | None:
    media_path_text = _coerce_export_media_path_text(row.get("path") or "", root)
    if not media_path_text:
        return None
    normalized = dict(row)
    normalized["path"] = media_path_text
    normalized.setdefault("name", Path(media_path_text).stem)
    return normalized


def _gallery_row_metadata_from_tsv(signature: GallerySignature, row: dict[str, str]) -> dict[str, str]:
    if _is_legacy_gallery_signature(signature):
        path_text = row.get("path") or ""
        name = row.get("name") or Path(path_text).stem
        return {
            "group": row.get("group", ""),
            "source": row.get("source", ""),
            "name": name,
            "width": row.get("width") or "0",
            "height": row.get("height") or "0",
            "path": path_text,
        }
    path_text = row.get("image_path") or row.get("path") or ""
    return {
        "group": row.get("atlas_key") or row.get("asset_group") or row.get("category") or "",
        "source": row.get("relative_source_path") or row.get("source") or "",
        "name": row.get("name") or Path(path_text).stem,
        "width": row.get("width") or "0",
        "height": row.get("height") or "0",
        "path": path_text,
    }


def _iter_gallery_rows_by_signature(signature: GallerySignature, root: Path):
    _kind, path_text, _mtime_ns, _size = signature
    is_legacy = _is_legacy_gallery_signature(signature)
    for row in _iter_tsv(Path(path_text)) or ():
        normalized = _gallery_row_from_legacy_tsv(row, root) if is_legacy else _gallery_row_from_modern_tsv(row, root)
        if normalized is not None:
            yield normalized


@lru_cache(maxsize=16)
def _gallery_count_by_signature(signature: GallerySignature, root_text: str) -> int:
    del root_text
    _kind, path_text, _mtime_ns, _size = signature
    count = 0
    if _is_legacy_gallery_signature(signature):
        path_field = "path"
    else:
        path_field = "image_path"
    for row in _iter_tsv(Path(path_text)) or ():
        media_name = row.get(path_field) or row.get("path") or ""
        if Path(media_name).suffix.lower() in _IMAGE_SUFFIXES:
            count += 1
    return count


def _gallery_count(kind: str, export_root: str | Path | None = None) -> int:
    signature = _gallery_signature(kind, export_root)
    if signature is None:
        return 0
    root = resolve_fanxiu_export_root(export_root)
    return _gallery_count_by_signature(signature, str(root.resolve()))


@lru_cache(maxsize=16)
def _gallery_rows_by_signature(signature: GallerySignature, root_text: str) -> tuple[dict[str, str], ...]:
    root = Path(root_text)
    return tuple(_iter_gallery_rows_by_signature(signature, root))


def _gallery_rows(kind: str, export_root: str | Path | None = None) -> list[dict[str, str]]:
    signature = _gallery_signature(kind, export_root)
    if signature is None:
        return []
    root = resolve_fanxiu_export_root(export_root)
    return list(_gallery_rows_by_signature(signature, str(root.resolve())))


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
    original_offset = offset
    kinds = ["texture", "sprite", "model_texture"] if kind == "all" else [kind]
    terms = [item.strip().lower() for item in re.split(r"\s+", query or "") if item.strip()]
    rows: list[dict[str, Any]] = []
    total = 0
    remaining_skip = offset

    for row_kind in kinds:
        signature = _gallery_signature(row_kind, export_root)
        if signature is None:
            continue
        if not terms:
            kind_total = _gallery_count_by_signature(signature, str(root.resolve()))
            total += kind_total
            if remaining_skip >= kind_total:
                remaining_skip -= kind_total
                continue
            for row in _iter_gallery_rows_by_signature(signature, root):
                if remaining_skip > 0:
                    remaining_skip -= 1
                    continue
                if len(rows) < limit:
                    rows.append(_format_gallery_row(row_kind, row))
                if len(rows) >= limit:
                    break
            continue
        for raw_row in _iter_tsv(Path(signature[1])) or ():
            metadata = _gallery_row_metadata_from_tsv(signature, raw_row)
            haystack = " ".join(
                str(metadata.get(field, ""))
                for field in ("group", "source", "name", "width", "height")
            ).lower()
            if terms and not all(term in haystack for term in terms):
                continue
            if total >= offset and len(rows) < limit:
                path = _coerce_export_media_path(metadata.get("path") or "", root)
                if path is None:
                    continue
                rows.append(_format_gallery_row(row_kind, metadata, path=path))
            total += 1

    return {
        "query": query,
        "kind": kind,
        "limit": limit,
        "offset": original_offset,
        "total": total,
        "items": rows,
    }


def _format_gallery_row(row_kind: str, row: dict[str, str], *, path: Path | None = None) -> dict[str, Any]:
    media_path = path or Path(row.get("path") or "")
    return {
        "kind": row_kind,
        "group": row.get("group", ""),
        "source": row.get("source", ""),
        "name": row.get("name", media_path.stem),
        "width": int(row.get("width") or 0),
        "height": int(row.get("height") or 0),
        "path": str(media_path),
    }


def resolve_fanxiu_wiki_media_path(path: str, export_root: str | Path | None = None) -> Path:
    root = resolve_fanxiu_export_root(export_root)
    media_path = Path(path).expanduser().resolve()
    if not _is_relative_to(media_path, root):
        raise FanxiuResourceError(f"媒体路径必须位于导出目录内：{root}")
    if not media_path.is_file() or media_path.suffix.lower() not in _IMAGE_SUFFIXES:
        raise FanxiuResourceError(f"媒体文件不存在或格式不支持：{media_path}")
    return media_path
