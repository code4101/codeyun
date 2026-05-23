from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any

from backend.core.fanxiu_resources import FanxiuResourceError, resolve_fanxiu_export_root
from backend.core.fanxiu_wiki import _unescape_lua_string, strip_fanxiu_rich_text


_SAFE_NAME_RE = re.compile(r"[^0-9A-Za-z_.-]+")
_KEY_INDEX_RE = re.compile(r"([A-Za-z_][0-9A-Za-z_]*)=(\d+)")
_INDEX_NUMBER_RE = re.compile(r"\[(\d+)\]=(-?\d+)")
_INDEX_STRING_RE = re.compile(r"^\[(?P<index>\d+)\]='(?P<value>(?:\\'|[^'])*)',?\s*$")
_INDEX_VALUE_RE = re.compile(r"^\[(?P<index>\d+)\]=(?P<value>.*),?\s*$")
_ROW_RE = re.compile(
    r"^(?:\[(?P<num_id>-?\d+)\]|\['(?P<str_id>(?:\\'|[^'])+)'\])=setmetatable\(\{(?P<body>.*)\},_[A-Za-z][0-9A-Za-z]*\),?\s*$"
)
_ROW_FIELD_RE = re.compile(r"\[(?P<index>\d+)\]=(?P<value>[^,}]+)")
_BRACKET_KEY_RE = re.compile(r"^\[(?P<key>-?\d+|'(?:\\'|[^'])*')\]=(.*)$")
_POOL_REF_RE = re.compile(r"^_(?P<pool>[A-Z])\[(?P<index>\d+)\]$")
_SETMETATABLE_RE = re.compile(r"^setmetatable\((?P<body>\{.*\}),_T\[\d+\]\)$")
_LANG_ENTRY_RE = re.compile(r"^\[(?P<key>\d+)\]='(?P<text>.*)',\s*$")
_INLINE_LANG_RE = re.compile(r"_(?:I|L)\((\d+)\)")


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _safe_name(value: str, fallback: str = "config") -> str:
    text = _SAFE_NAME_RE.sub("_", str(value or "").strip()).strip("._")
    return text[:80] if text else fallback


def _resolve_export_file(path: str | Path, *, export_root: str | Path | None = None) -> Path:
    root = resolve_fanxiu_export_root(export_root)
    raw_path = Path(path)
    resolved = raw_path.expanduser().resolve() if raw_path.is_absolute() else (root / raw_path).resolve()
    if not _is_relative_to(resolved, root):
        raise FanxiuResourceError(f"文件必须位于导出根目录内：{root}")
    if not resolved.is_file():
        raise FanxiuResourceError(f"文件不存在：{resolved}")
    return resolved


def _resolve_export_dir(path: str | Path, *, export_root: str | Path | None = None) -> Path:
    root = resolve_fanxiu_export_root(export_root)
    raw_path = Path(path)
    resolved = raw_path.expanduser().resolve() if raw_path.is_absolute() else (root / raw_path).resolve()
    if not _is_relative_to(resolved, root):
        raise FanxiuResourceError(f"目录必须位于导出根目录内：{root}")
    if not resolved.is_dir():
        raise FanxiuResourceError(f"目录不存在：{resolved}")
    return resolved


def _find_default_lang_path(export_root: str | Path | None = None) -> Path | None:
    root = resolve_fanxiu_export_root(export_root)
    candidates = [path for path in root.glob("by_source/**/text_assets/lang.lua") if path.is_file()]
    if not candidates:
        return None
    return max(candidates, key=lambda item: item.stat().st_size)


def _find_default_gongfa_config_dir(export_root: str | Path | None = None) -> Path:
    root = resolve_fanxiu_export_root(export_root)
    candidates = [
        path
        for path in root.glob("by_source/lscripts/generate/cfg/gongfa_*/text_assets")
        if path.is_dir()
    ]
    if not candidates:
        raise FanxiuResourceError("未找到 gongfa TextAsset 导出目录，请先导出 gongfa_*.bytes 的 TextAsset")
    return max(candidates, key=lambda item: item.stat().st_mtime_ns)


def _extract_lua_table_body(text: str, name: str) -> str:
    marker = f"local {name}={{"
    start = text.find(marker)
    if start < 0:
        return ""
    cursor = start + len(marker)
    depth = 1
    quote = ""
    escaped = False
    while cursor < len(text):
        ch = text[cursor]
        if quote:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                quote = ""
        elif ch in {"'", '"'}:
            quote = ch
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start + len(marker):cursor]
        cursor += 1
    return ""


def _parse_index_name_map(body: str) -> dict[int, str]:
    return {int(index): name for name, index in _KEY_INDEX_RE.findall(body)}


def _parse_index_number_map(body: str) -> dict[int, int]:
    return {int(index): int(value) for index, value in _INDEX_NUMBER_RE.findall(body)}


def _parse_string_pool(body: str) -> dict[int, str]:
    values: dict[int, str] = {}
    for line in body.splitlines():
        match = _INDEX_STRING_RE.match(line.strip())
        if match:
            values[int(match.group("index"))] = _unescape_lua_string(match.group("value"))
    return values


def _parse_index_value_map(body: str) -> dict[int, str]:
    values: dict[int, str] = {}
    for line in body.splitlines():
        line = line.strip()
        match = _INDEX_VALUE_RE.match(line)
        if not match:
            continue
        value = match.group("value").strip()
        if value.endswith(","):
            value = value[:-1].rstrip()
        values[int(match.group("index"))] = value
    return values


def load_fanxiu_lang_map(path: str | Path) -> dict[int, str]:
    lang_path = Path(path)
    values: dict[int, str] = {}
    with lang_path.open("r", encoding="utf-8-sig", errors="replace") as f:
        for line in f:
            match = _LANG_ENTRY_RE.match(line.rstrip("\r\n"))
            if not match:
                continue
            values[int(match.group("key"))] = _unescape_lua_string(match.group("text"))
    return values


def _split_top_level(value: str) -> list[str]:
    items: list[str] = []
    start = 0
    depth = 0
    quote = ""
    escaped = False
    for index, ch in enumerate(value):
        if quote:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                quote = ""
            continue
        if ch in {"'", '"'}:
            quote = ch
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        elif ch == "," and depth == 0:
            item = value[start:index].strip()
            if item:
                items.append(item)
            start = index + 1
    tail = value[start:].strip()
    if tail:
        items.append(tail)
    return items


def _parse_scalar(value: str) -> Any:
    text = value.strip()
    if text == "nil":
        return None
    if text == "true":
        return True
    if text == "false":
        return False
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    if re.fullmatch(r"-?\d+\.\d+", text):
        return float(text)
    if len(text) >= 2 and text[0] == "'" and text[-1] == "'":
        return _unescape_lua_string(text[1:-1])
    if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        return _unescape_lua_string(text[1:-1])
    return text


def _format_lua_lang(value: str, args: list[Any]) -> str:
    text = str(value)
    if not args:
        return text
    try:
        return text % tuple(args)
    except (TypeError, ValueError):
        result = text
        for arg in args:
            result = result.replace("%s", str(arg), 1)
        return result


def _resolve_inline_lang_calls(value: Any, lang_map: dict[int, str]) -> Any:
    if isinstance(value, str):
        def replace(match: re.Match[str]) -> str:
            lang_id = int(match.group(1))
            return lang_map.get(lang_id, match.group(0))

        return _INLINE_LANG_RE.sub(replace, value)
    if isinstance(value, list):
        return [_resolve_inline_lang_calls(item, lang_map) for item in value]
    if isinstance(value, dict):
        return {key: _resolve_inline_lang_calls(item, lang_map) for key, item in value.items()}
    return value


def _parse_lua_value(value: str, resolve_ref) -> Any:
    text = value.strip()
    setmetatable_match = _SETMETATABLE_RE.match(text)
    if setmetatable_match:
        text = setmetatable_match.group("body")
    if text.startswith("{") and text.endswith("}"):
        body = text[1:-1].strip()
        if not body:
            return []
        parts = _split_top_level(body)
        keyed: dict[int | str, Any] = {}
        sequential: list[Any] = []
        has_keyed = False
        for part in parts:
            key_match = re.match(r"^([A-Za-z_][0-9A-Za-z_]*)=(.*)$", part)
            if key_match:
                has_keyed = True
                keyed[key_match.group(1)] = _parse_lua_value(key_match.group(2), resolve_ref)
                continue
            bracket_key_match = _BRACKET_KEY_RE.match(part)
            if bracket_key_match:
                has_keyed = True
                raw_key = bracket_key_match.group("key")
                key: int | str
                if re.fullmatch(r"-?\d+", raw_key):
                    key = int(raw_key)
                else:
                    key = _unescape_lua_string(raw_key[1:-1])
                keyed[key] = _parse_lua_value(bracket_key_match.group(2), resolve_ref)
                continue
            sequential.append(_parse_lua_value(part, resolve_ref))
        if has_keyed and not sequential:
            int_keys = [key for key in keyed if isinstance(key, int)]
            if len(int_keys) == len(keyed) and sorted(int_keys) == list(range(1, len(int_keys) + 1)):
                return [keyed[index] for index in range(1, len(int_keys) + 1)]
            return keyed
        if has_keyed:
            keyed["_items"] = sequential
            return keyed
        return sequential
    ref = _POOL_REF_RE.match(text)
    if ref:
        return resolve_ref(ref.group("pool"), int(ref.group("index")))[0]
    return _parse_scalar(text)


def _parse_row_body_values(body: str) -> list[tuple[int, str]]:
    values: list[tuple[int, str]] = []
    next_index = 1
    for part in _split_top_level(body):
        match = _BRACKET_KEY_RE.match(part)
        if match and re.fullmatch(r"-?\d+", match.group("key")):
            values.append((int(match.group("key")), match.group(2).strip()))
            continue
        values.append((next_index, part.strip()))
        next_index += 1
    return values


def _resolve_formatted_lang(parsed_value: Any, lang_map: dict[int, str]) -> tuple[Any, int | None]:
    if isinstance(parsed_value, int) and parsed_value in lang_map:
        return lang_map[parsed_value], parsed_value
    if isinstance(parsed_value, list) and parsed_value and isinstance(parsed_value[0], int) and parsed_value[0] in lang_map:
        lang_id = parsed_value[0]
        return _format_lua_lang(lang_map[lang_id], parsed_value[1:]), lang_id
    return parsed_value, None


def _resolve_row_value(
    value: str,
    *,
    string_pool: dict[int, str],
    raw_pools: dict[str, dict[int, str]],
    lang_map: dict[int, str],
) -> tuple[Any, int | None]:
    active_refs: set[tuple[str, int]] = set()

    def resolve_ref(pool: str, index: int) -> tuple[Any, int | None]:
        key = (pool, index)
        if pool == "A":
            return _resolve_inline_lang_calls(string_pool.get(index, text), lang_map), None
        if key in active_refs:
            return f"_{pool}[{index}]", None
        if pool == "C":
            raw = raw_pools.get("C", {}).get(index)
            if raw is None:
                return f"_C[{index}]", None
            active_refs.add(key)
            try:
                parsed = _parse_lua_value(raw, resolve_ref)
                resolved, lang_id = _resolve_formatted_lang(parsed, lang_map)
                return _resolve_inline_lang_calls(resolved, lang_map), lang_id
            finally:
                active_refs.discard(key)
        if pool == "B":
            raw = raw_pools.get("B", {}).get(index)
            if raw is None:
                return f"_B[{index}]", None
            active_refs.add(key)
            try:
                return _resolve_inline_lang_calls(_parse_lua_value(raw, resolve_ref), lang_map), None
            finally:
                active_refs.discard(key)
        return f"_{pool}[{index}]", None

    text = value.strip()
    ref = _POOL_REF_RE.match(text)
    if ref:
        return resolve_ref(ref.group("pool"), int(ref.group("index")))
    return _resolve_inline_lang_calls(_parse_lua_value(text, resolve_ref), lang_map), None


def parse_fanxiu_generated_lua_config(
    config_path: str | Path,
    *,
    lang_path: str | Path | None = None,
) -> dict[str, Any]:
    path = Path(config_path)
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    key2index = _parse_index_name_map(_extract_lua_table_body(text, "_key2index"))
    key2type = _parse_index_number_map(_extract_lua_table_body(text, "_key2type"))
    string_pool = _parse_string_pool(_extract_lua_table_body(text, "_A"))
    raw_pools = {
        "B": _parse_index_value_map(_extract_lua_table_body(text, "_B")),
        "C": _parse_index_value_map(_extract_lua_table_body(text, "_C")),
    }
    lang_map = load_fanxiu_lang_map(lang_path) if lang_path else {}
    rows = []
    for line in _extract_lua_table_body(text, "_M").splitlines():
        match = _ROW_RE.match(line.strip())
        if not match:
            continue
        if match.group("num_id") is not None:
            row_key: int | str = int(match.group("num_id"))
        else:
            row_key = _unescape_lua_string(match.group("str_id") or "")
        row: dict[str, Any] = {"_row_key": row_key}
        raw_values: dict[str, str] = {}
        for index, raw_value in _parse_row_body_values(match.group("body")):
            field = key2index.get(index, f"field_{index}")
            resolved, lang_id = _resolve_row_value(raw_value, string_pool=string_pool, raw_pools=raw_pools, lang_map=lang_map)
            raw_values[field] = raw_value
            row[field] = resolved
            if lang_id is not None:
                row[f"{field}_lang_id"] = lang_id
                row[f"{field}_plain"] = strip_fanxiu_rich_text(str(resolved))
        rows.append(row)
    return {
        "source_path": str(path),
        "lang_path": str(lang_path) if lang_path else "",
        "fields": [key2index[index] for index in sorted(key2index)],
        "key2index": {name: index for index, name in key2index.items()},
        "key2type": key2type,
        "string_pool_count": len(string_pool),
        "b_pool_count": len(raw_pools["B"]),
        "c_pool_count": len(raw_pools["C"]),
        "row_count": len(rows),
        "rows": rows,
    }


def _write_tsv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, delimiter="\t", extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def build_fanxiu_lua_config_report(
    config_path: str | Path,
    *,
    lang_path: str | Path | None = None,
    export_root: str | Path | None = None,
    max_preview_rows: int = 5000,
) -> dict[str, Any]:
    root = resolve_fanxiu_export_root(export_root)
    resolved_config_path = _resolve_export_file(config_path, export_root=export_root)
    resolved_lang_path = _resolve_export_file(lang_path, export_root=export_root) if lang_path else _find_default_lang_path(export_root)
    parsed = parse_fanxiu_generated_lua_config(resolved_config_path, lang_path=resolved_lang_path)
    out_dir = root / "parsed_configs" / _safe_name(resolved_config_path.stem)
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = list(parsed["rows"])
    all_fields = sorted({key for row in rows for key in row})
    preferred = [
        "_row_key",
        "id",
        "name_plain",
        "name",
        "skillName_plain",
        "skillName",
        "quality",
        "pin",
        "level",
        "icon",
        "descript_plain",
        "descript",
        "describe_plain",
        "describe",
        "effectDescribe_plain",
        "effectDescribe",
    ]
    tsv_fields = [field for field in preferred if field in all_fields] + [field for field in all_fields if field not in preferred]
    preview_fields = [field for field in preferred if field in all_fields]
    preview_rows = rows[: max(1, min(int(max_preview_rows), 50000))]

    (out_dir / "schema.json").write_text(
        json.dumps({key: value for key, value in parsed.items() if key != "rows"}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out_dir / "rows.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_tsv(out_dir / "rows.tsv", rows, tsv_fields)
    _write_tsv(out_dir / "preview.tsv", preview_rows, preview_fields or tsv_fields[:20])

    return {
        "output_dir": str(out_dir),
        "source_path": str(resolved_config_path),
        "lang_path": str(resolved_lang_path or ""),
        "row_count": len(rows),
        "field_count": len(parsed["fields"]),
        "fields": parsed["fields"],
        "files": {
            "schema": str(out_dir / "schema.json"),
            "rows_json": str(out_dir / "rows.json"),
            "rows_tsv": str(out_dir / "rows.tsv"),
            "preview_tsv": str(out_dir / "preview.tsv"),
        },
    }


def build_fanxiu_lua_config_batch_report(
    *,
    config_dir: str | Path | None = None,
    lang_path: str | Path | None = None,
    export_root: str | Path | None = None,
    max_preview_rows: int = 5000,
) -> dict[str, Any]:
    root = resolve_fanxiu_export_root(export_root)
    resolved_config_dir = _resolve_export_dir(config_dir, export_root=export_root) if config_dir else _find_default_gongfa_config_dir(export_root)
    resolved_lang_path = _resolve_export_file(lang_path, export_root=export_root) if lang_path else _find_default_lang_path(export_root)
    out_dir = root / "parsed_configs" / "lua_config_index"
    out_dir.mkdir(parents=True, exist_ok=True)

    table_rows: list[dict[str, Any]] = []
    for config_path in sorted(resolved_config_dir.glob("*.lua"), key=lambda item: item.name.lower()):
        try:
            result = build_fanxiu_lua_config_report(
                config_path,
                lang_path=resolved_lang_path,
                export_root=export_root,
                max_preview_rows=max_preview_rows,
            )
            table_rows.append(
                {
                    "asset": config_path.name,
                    "row_count": result["row_count"],
                    "field_count": result["field_count"],
                    "fields": ", ".join(result["fields"]),
                    "output_dir": result["output_dir"],
                    "error": "",
                }
            )
        except Exception as exc:  # pragma: no cover - exercised by real-world malformed assets.
            table_rows.append(
                {
                    "asset": config_path.name,
                    "row_count": 0,
                    "field_count": 0,
                    "fields": "",
                    "output_dir": "",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

    json_path = out_dir / "lua_config_tables.json"
    tsv_path = out_dir / "lua_config_tables.tsv"
    json_path.write_text(
        json.dumps(
            {
                "config_dir": str(resolved_config_dir),
                "lang_path": str(resolved_lang_path or ""),
                "table_count": len(table_rows),
                "parsed_count": sum(1 for row in table_rows if not row["error"]),
                "tables": table_rows,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    _write_tsv(tsv_path, table_rows, ["asset", "row_count", "field_count", "fields", "output_dir", "error"])

    return {
        "output_dir": str(out_dir),
        "config_dir": str(resolved_config_dir),
        "lang_path": str(resolved_lang_path or ""),
        "table_count": len(table_rows),
        "parsed_count": sum(1 for row in table_rows if not row["error"]),
        "files": {
            "tables_json": str(json_path),
            "tables_tsv": str(tsv_path),
        },
    }
