from __future__ import annotations

import csv
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from backend.core.fanxiu_resources import FanxiuResourceError, resolve_fanxiu_export_root


DEFAULT_LUA_PACKET_DIR = Path("by_source/lscripts/gamesystem/game")
_PACKAGE_RE = re.compile(r"package\.loaded\[[\"']([^\"']+)[\"']\]")
_CLASS_RE = re.compile(r"_M\s*=\s*class\(([^,\)]+)")
_GET_ID_RE = re.compile(r"function\s+_M\.getId\s*\([^)]*\).*?return\s+([0-9]+)", re.S)
_GET_NAME_RE = re.compile(r"function\s+_M\.getName\s*\([^)]*\).*?return\s*[\"']([^\"']+)[\"']", re.S)
_READ_ASSIGN_RE = re.compile(r"self\.([A-Za-z0-9_]+)\s*=.*?self:read([A-Za-z0-9_]+)\(")
_READ_INTO_RE = re.compile(r"self:read([A-Za-z0-9_]+)\(\s*self\.([A-Za-z0-9_]+)")
_TYPEOF_RE = re.compile(r"typeof\(([A-Za-z0-9_]+)\)")


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _resolve_export_dir(path: str | Path | None, default: Path, *, export_root: str | Path | None = None) -> Path:
    root = resolve_fanxiu_export_root(export_root)
    raw_path = Path(path) if path else default
    resolved = raw_path.expanduser().resolve() if raw_path.is_absolute() else (root / raw_path).resolve()
    if not _is_relative_to(resolved, root):
        raise FanxiuResourceError(f"目录必须位于导出根目录内：{root}")
    if not resolved.is_dir():
        raise FanxiuResourceError(f"目录不存在：{resolved}")
    return resolved


def _write_tsv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, delimiter="\t", extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _direction_for_name(name: str) -> str:
    if name.startswith("CM_"):
        return "client_to_server"
    if name.startswith("SM_"):
        return "server_to_client"
    if name.endswith("VO") or name.endswith("DTO"):
        return "value_object"
    return "other"


def _module_for_package(package_name: str) -> str:
    marker = ".module."
    if marker not in package_name:
        return ""
    tail = package_name.split(marker, 1)[1]
    return tail.rsplit(".packet.", 1)[0] if ".packet." in tail else tail


def _extract_packet_fields(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for line_no, line in enumerate(text.splitlines(), start=1):
        field_name = ""
        read_method = ""
        match = _READ_ASSIGN_RE.search(line)
        if match:
            field_name, read_method = match.group(1), match.group(2)
        else:
            match = _READ_INTO_RE.search(line)
            if match:
                read_method, field_name = match.group(1), match.group(2)
        if not field_name or read_method == "ing":
            continue
        type_match = _TYPEOF_RE.search(line)
        type_hint = type_match.group(1) if type_match else ""
        key = (field_name, read_method, type_hint)
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            {
                "field_index": len(rows) + 1,
                "field_name": field_name,
                "read_method": read_method,
                "type_hint": type_hint,
                "line": line_no,
            }
        )
    return rows


def _field_signature(rows: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for row in rows:
        read_method = str(row.get("read_method") or "")
        type_hint = str(row.get("type_hint") or "")
        suffix = f"<{type_hint}>" if type_hint else ""
        parts.append(f"{row.get('field_name')}:{read_method}{suffix}")
    return ", ".join(parts)


def _parse_packet_file(path: Path, root: Path) -> dict[str, Any] | None:
    text = path.read_text(encoding="utf-8", errors="replace")
    package_name = next((match.group(1) for match in _PACKAGE_RE.finditer(text)), "")
    id_match = _GET_ID_RE.search(text)
    name_match = _GET_NAME_RE.search(text)
    message_id = int(id_match.group(1)) if id_match else None
    message_name = name_match.group(1) if name_match else path.stem
    if message_id is None and ".packet." not in package_name and not re.match(r"^(CM|SM)_.+|.+VO$", path.stem):
        return None
    class_match = _CLASS_RE.search(text)
    base_class = class_match.group(1).strip() if class_match else ""
    bundle = path.parent.parent.name if path.parent.name == "text_assets" else path.parent.name
    relative_path = path.relative_to(root).as_posix()
    fields = _extract_packet_fields(text)
    return {
        "bundle": bundle,
        "file": path.name,
        "relative_path": relative_path,
        "package": package_name,
        "module": _module_for_package(package_name),
        "name": message_name,
        "id": message_id,
        "direction": _direction_for_name(message_name),
        "base_class": base_class,
        "field_count": len(fields),
        "fields": fields,
    }


def build_fanxiu_lua_packet_index(
    *,
    source_dir: str | Path | None = None,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_export_root(export_root)
    resolved_source_dir = _resolve_export_dir(source_dir, DEFAULT_LUA_PACKET_DIR, export_root=export_root)
    out_dir = root / "parsed_configs" / "lua_packet_index"
    out_dir.mkdir(parents=True, exist_ok=True)

    packet_rows: list[dict[str, Any]] = []
    field_rows: list[dict[str, Any]] = []
    for path in sorted(resolved_source_dir.glob("*/text_assets/*.lua")):
        item = _parse_packet_file(path, root)
        if not item:
            continue
        packet_rows.append({key: value for key, value in item.items() if key != "fields"})
        for field in item["fields"]:
            field_rows.append(
                {
                    "packet_name": item["name"],
                    "packet_id": item["id"],
                    "direction": item["direction"],
                    "module": item["module"],
                    "bundle": item["bundle"],
                    "file": item["file"],
                    "field_index": field["field_index"],
                    "field_name": field["field_name"],
                    "read_method": field["read_method"],
                    "type_hint": field["type_hint"],
                    "line": field["line"],
                }
            )

    packet_rows.sort(key=lambda row: (row["id"] is None, row["id"] or 0, str(row["name"])))
    field_rows.sort(key=lambda row: (row["packet_id"] is None, row["packet_id"] or 0, int(row["field_index"])))
    direction_counts = Counter(str(row["direction"]) for row in packet_rows)
    module_counts = Counter(str(row["module"] or "<unknown>") for row in packet_rows)
    duplicate_ids = [
        {"id": packet_id, "names": sorted(names)}
        for packet_id, names in _group_packet_ids(packet_rows).items()
        if packet_id is not None and len(names) > 1
    ]
    stats = {
        "packet_count": len(packet_rows),
        "message_id_count": len({row["id"] for row in packet_rows if row["id"] is not None}),
        "field_count": len(field_rows),
        "faze_packet_count": module_counts.get("player.faze", 0),
        "direction_counts": dict(direction_counts),
        "top_modules": dict(module_counts.most_common(30)),
        "duplicate_id_count": len(duplicate_ids),
    }

    packets_path = out_dir / "packets.tsv"
    fields_path = out_dir / "packet_fields.tsv"
    faze_packets_path = out_dir / "faze_packets.tsv"
    json_path = out_dir / "lua_packet_index.json"
    report_path = out_dir / "lua_packet_index_report.md"
    _write_tsv(
        packets_path,
        packet_rows,
        [
            "id",
            "name",
            "direction",
            "module",
            "field_count",
            "base_class",
            "bundle",
            "file",
            "relative_path",
            "package",
        ],
    )
    _write_tsv(
        fields_path,
        field_rows,
        [
            "packet_id",
            "packet_name",
            "field_index",
            "field_name",
            "read_method",
            "type_hint",
            "direction",
            "module",
            "bundle",
            "file",
            "line",
        ],
    )
    fields_by_id: dict[Any, list[dict[str, Any]]] = {}
    for row in field_rows:
        fields_by_id.setdefault(row.get("packet_id"), []).append(row)
    faze_rows = [
        {
            "id": row["id"],
            "name": row["name"],
            "direction": row["direction"],
            "field_count": row["field_count"],
            "fields": _field_signature(fields_by_id.get(row["id"], [])),
            "file": row["file"],
        }
        for row in packet_rows
        if row.get("module") == "player.faze"
    ]
    _write_tsv(faze_packets_path, faze_rows, ["id", "name", "direction", "field_count", "fields", "file"])
    json_path.write_text(
        json.dumps(
            {
                "source_dir": str(resolved_source_dir),
                "stats": stats,
                "duplicate_ids": duplicate_ids,
                "packets": packet_rows,
                "fields": field_rows,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    report_path.write_text(
        "\n".join(
            [
                "# 凡修 Lua Packet 静态索引",
                "",
                f"- Packet/VO 文件：{stats['packet_count']}",
                f"- 消息 id：{stats['message_id_count']}",
                f"- 字段：{stats['field_count']}",
                f"- player.faze：{stats['faze_packet_count']}",
                f"- 重复消息 id：{stats['duplicate_id_count']}",
                "",
                "## 方向统计",
                "",
                *[f"- `{key}`：{value}" for key, value in direction_counts.most_common()],
                "",
                "## 高频模块",
                "",
                *[f"- `{key}`：{value}" for key, value in module_counts.most_common(20)],
            ]
        ),
        encoding="utf-8",
    )
    return {
        "output_dir": str(out_dir),
        "source_dir": str(resolved_source_dir),
        "stats": stats,
        "files": {
            "index_json": str(json_path),
            "packets_tsv": str(packets_path),
            "packet_fields_tsv": str(fields_path),
            "faze_packets_tsv": str(faze_packets_path),
            "report": str(report_path),
        },
    }


def _group_packet_ids(rows: list[dict[str, Any]]) -> dict[int | None, set[str]]:
    grouped: dict[int | None, set[str]] = {}
    for row in rows:
        packet_id = row.get("id")
        grouped.setdefault(packet_id, set()).add(str(row.get("name") or ""))
    return grouped
