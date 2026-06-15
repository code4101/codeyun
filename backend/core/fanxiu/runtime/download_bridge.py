from __future__ import annotations

import csv
import json
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from backend.core.fanxiu.catalog.resources import FanxiuResourceError, resolve_fanxiu_export_root


_LUA_BRIDGE_MODULE = "Core.Engine.CommonSystem.Asset.LuaGameResDownloadBridge"
_BRIDGE_FUNC_RE = re.compile(r"^function\s+_M\.([A-Za-z0-9_]+)\(([^)]*)\)")
_BRIDGE_METHOD_RE = re.compile(r"GameResDownloadBridge\.([A-Za-z0-9_]+)\s*\(")
_LUA_CALL_RE = re.compile(r"LuaGameResDownloadBridge\.([A-Za-z0-9_]+)\s*\(")
_CONFIG_REF_RE = re.compile(r"ConfigName\.([A-Za-z0-9_]+)")
_TYPE_REF_RE = re.compile(r"type#(-?\d+)")
_IL2CPP_DOWNLOAD_TYPE_TERMS = (
    "MU.GameLogic.GameResDownLoad",
    "LuaBridge.Load.GameResDownloadBridge",
    "LuaBridge_Load_GameResDownloadBridgeWrap",
    "AssetBundleEncryptStream",
)
_IL2CPP_DOWNLOAD_STRING_TERMS = (
    "GameResDownloadBridge",
    "filelistVersion",
    "ResDownLoadURL",
    "BundleVersion",
    "DownloadPackage",
    "F_CheckIsResUpdate",
    "Get remote",
    "force filelist",
)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _clean_cell(value: object, *, limit: int = 1200) -> str:
    text = "" if value is None else str(value)
    text = text.replace("\r", "\\r").replace("\n", "\\n").replace("\t", " ").replace("\x00", "")
    if len(text) > limit:
        text = f"{text[:limit]}..."
    return text


def _write_tsv(path: Path, fields: list[str], rows: Iterable[dict[str, object]]) -> int:
    count = 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: _clean_cell(row.get(field, "")) for field in fields})
            count += 1
    return count


def _read_tsv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8-sig", errors="ignore", newline="") as f:
        return [dict(row) for row in csv.DictReader(f, delimiter="\t")]


def _markdown_cell(value: object, *, limit: int = 260) -> str:
    return _clean_cell(value, limit=limit).replace("|", "\\|")


def _find_bridge_lua_files(export_root: Path) -> list[Path]:
    by_source = export_root / "by_source"
    if not by_source.is_dir():
        return []
    return sorted(by_source.rglob("LuaGameResDownloadBridge.lua"), key=lambda item: item.as_posix().lower())


def _iter_lua_files(export_root: Path) -> Iterable[Path]:
    by_source = export_root / "by_source"
    if not by_source.is_dir():
        return []
    return sorted(by_source.rglob("*.lua"), key=lambda item: item.as_posix().lower())


def _parse_bridge_functions(path: Path, export_root: Path) -> list[dict[str, object]]:
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    starts: list[tuple[int, re.Match[str]]] = []
    for index, line in enumerate(lines):
        match = _BRIDGE_FUNC_RE.match(line.strip())
        if match:
            starts.append((index, match))

    rows: list[dict[str, object]] = []
    rel = path.relative_to(export_root).as_posix()
    for pos, (start_index, match) in enumerate(starts):
        end_index = starts[pos + 1][0] if pos + 1 < len(starts) else len(lines)
        body = "\n".join(lines[start_index:end_index])
        bridge_methods = sorted(dict.fromkeys(_BRIDGE_METHOD_RE.findall(body)))
        config_refs = sorted(dict.fromkeys(_CONFIG_REF_RE.findall(body)))
        rows.append(
            {
                "source": rel,
                "function": match.group(1),
                "args": match.group(2),
                "bridge_methods": ", ".join(bridge_methods),
                "config_refs": ", ".join(config_refs),
                "line_start": start_index + 1,
                "line_end": end_index,
                "snippet": " ".join(line.strip() for line in lines[start_index : min(end_index, start_index + 6)]),
            }
        )
    return rows


def _collect_bridge_call_sites(export_root: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for path in _iter_lua_files(export_root):
        rel = path.relative_to(export_root).as_posix()
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        for index, line in enumerate(lines, start=1):
            stripped = line.strip()
            if _LUA_BRIDGE_MODULE in stripped:
                rows.append(
                    {
                        "source": rel,
                        "line": index,
                        "kind": "require",
                        "method": "",
                        "snippet": stripped,
                    }
                )
            for method in _LUA_CALL_RE.findall(stripped):
                rows.append(
                    {
                        "source": rel,
                        "line": index,
                        "kind": "call",
                        "method": method,
                        "snippet": stripped,
                    }
                )
    return rows


def _write_bridge_markdown(
    path: Path,
    *,
    export_root: Path,
    bridge_rows: list[dict[str, object]],
    call_rows: list[dict[str, object]],
) -> None:
    calls_by_method = Counter(str(row["method"]) for row in call_rows if row.get("method"))
    calls_by_source: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in call_rows:
        calls_by_source[str(row["source"])].append(row)

    lines = [
        "# 凡修 Lua 资源下载桥报告",
        "",
        f"- 导出目录：`{export_root}`",
        f"- 桥函数：{len(bridge_rows)}",
        f"- Lua 调用点：{len(call_rows)}",
        "- 说明：Lua 这里只是桥接层，实际下载、包状态和资源存在性判断落在 `LuaBridge.Load.GameResDownloadBridge` 的 C#/IL2CPP 实现。",
        "",
        "## 桥函数",
        "",
        "| 函数 | C#/IL2CPP 方法 | 配置引用 | 行号 |",
        "| --- | --- | --- | --- |",
    ]
    for row in bridge_rows:
        lines.append(
            "| "
            f"{_markdown_cell(row.get('function'))} | "
            f"{_markdown_cell(row.get('bridge_methods'))} | "
            f"{_markdown_cell(row.get('config_refs'))} | "
            f"{row.get('line_start')}-{row.get('line_end')} |"
        )

    lines.extend(["", "## 调用频次", "", "| 方法 | 次数 |", "| --- | --- |"])
    for method, count in calls_by_method.most_common():
        lines.append(f"| {_markdown_cell(method)} | {count} |")

    lines.extend(["", "## 代表调用点", ""])
    for source, source_rows in sorted(calls_by_source.items(), key=lambda item: (-len(item[1]), item[0]))[:20]:
        lines.extend([f"### {source}", "", "| 行号 | 类型 | 方法 | 代码 |", "| --- | --- | --- | --- |"])
        for row in source_rows[:20]:
            lines.append(
                "| "
                f"{row.get('line')} | "
                f"{_markdown_cell(row.get('kind'))} | "
                f"{_markdown_cell(row.get('method'))} | "
                f"{_markdown_cell(row.get('snippet'), limit=360)} |"
            )
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


def build_fanxiu_lua_download_bridge_report(
    *,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    resolved_export_root = resolve_fanxiu_export_root(export_root)
    output_dir = (resolved_export_root / "apk_static_index").resolve()
    if not _is_relative_to(output_dir, resolved_export_root):
        raise FanxiuResourceError(f"导出目录必须位于导出根目录内：{resolved_export_root}")
    output_dir.mkdir(parents=True, exist_ok=True)

    bridge_paths = _find_bridge_lua_files(resolved_export_root)
    bridge_rows: list[dict[str, object]] = []
    for path in bridge_paths:
        bridge_rows.extend(_parse_bridge_functions(path, resolved_export_root))
    call_rows = _collect_bridge_call_sites(resolved_export_root)

    bridge_function_count = _write_tsv(
        output_dir / "lua_download_bridge_functions.tsv",
        ["source", "function", "args", "bridge_methods", "config_refs", "line_start", "line_end", "snippet"],
        bridge_rows,
    )
    call_site_count = _write_tsv(
        output_dir / "lua_download_bridge_call_sites.tsv",
        ["source", "line", "kind", "method", "snippet"],
        call_rows,
    )
    _write_bridge_markdown(
        output_dir / "lua_download_bridge_report.md",
        export_root=resolved_export_root,
        bridge_rows=bridge_rows,
        call_rows=call_rows,
    )

    result = {
        "export_root": str(resolved_export_root),
        "output_dir": str(output_dir),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "counts": {
            "bridge_files": len(bridge_paths),
            "bridge_functions": bridge_function_count,
            "call_sites": call_site_count,
            "calls_by_method": dict(Counter(str(row["method"]) for row in call_rows if row.get("method")).most_common()),
        },
        "outputs": {
            "summary": str(output_dir / "lua_download_bridge_report.json"),
            "markdown": str(output_dir / "lua_download_bridge_report.md"),
            "functions": str(output_dir / "lua_download_bridge_functions.tsv"),
            "call_sites": str(output_dir / "lua_download_bridge_call_sites.tsv"),
        },
    }
    (output_dir / "lua_download_bridge_report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


def _il2cpp_download_match(value: str) -> bool:
    return any(term.lower() in value.lower() for term in _IL2CPP_DOWNLOAD_TYPE_TERMS)


def _il2cpp_download_string_match(value: str) -> bool:
    return any(term.lower() in value.lower() for term in _IL2CPP_DOWNLOAD_STRING_TERMS)


def _safe_int(value: object, default: int = -1) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return default


def _build_il2cpp_type_name_map(type_rows: list[dict[str, str]]) -> dict[str, str]:
    result: dict[str, str] = {}
    # Method/field signatures point at Il2CppType indices. For type definitions,
    # byval_type_index is the reliable bridge from a type index to the definition.
    # The row index is only a fallback for hand-authored/minimal TSV fixtures.
    for row in type_rows:
        full_name = row.get("full_name", "")
        if not full_name:
            continue
        index = row.get("index", "")
        if index:
            result.setdefault(str(index), full_name)
    for row in type_rows:
        full_name = row.get("full_name", "")
        if not full_name:
            continue
        for key in ("byval_type_index", "byref_type_index"):
            type_index = row.get(key, "")
            if type_index and type_index != "-1":
                result[str(type_index)] = full_name
    return result


def _resolve_type_ref(value: object, type_name_by_index: dict[str, str]) -> str:
    text = str(value)
    if not text or text == "-1":
        return ""
    return type_name_by_index.get(text, f"type#{text}")


def _resolve_parameter_signature(parameters: str, type_name_by_index: dict[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        return _resolve_type_ref(match.group(1), type_name_by_index)

    return _TYPE_REF_RE.sub(replace, parameters)


def _enrich_il2cpp_download_rows(
    *,
    type_name_by_index: dict[str, str],
    method_rows: list[dict[str, str]],
    field_rows: list[dict[str, str]],
) -> None:
    for row in method_rows:
        row["parameters_resolved"] = _resolve_parameter_signature(row.get("parameters", ""), type_name_by_index)
        row["return_type_name"] = _resolve_type_ref(row.get("return_type", ""), type_name_by_index)
    for row in field_rows:
        row["type_name"] = _resolve_type_ref(row.get("type_index", ""), type_name_by_index)


def _write_il2cpp_download_inventory_markdown(
    path: Path,
    *,
    export_root: Path,
    type_rows: list[dict[str, str]],
    method_rows: list[dict[str, str]],
    field_rows: list[dict[str, str]],
    string_rows: list[dict[str, str]],
) -> None:
    methods_by_owner = Counter(row.get("owner", "") for row in method_rows)
    fields_by_owner = Counter(row.get("owner", "") for row in field_rows)
    lines = [
        "# 凡修 IL2CPP 下载类清单",
        "",
        f"- 导出目录：`{export_root}`",
        f"- 类型：{len(type_rows)}",
        f"- 方法：{len(method_rows)}",
        f"- 字段：{len(field_rows)}",
        f"- 字符串：{len(string_rows)}",
        "- 说明：本报告来自 `global-metadata.dat` 解析，只能看到类名、方法名、字段名和字符串字面量；方法体和真实调用图仍需要 IL2CPP 反编译/反汇编工具。",
        "",
        "## 类型",
        "",
        "| full_name | method_count | field_count |",
        "| --- | --- | --- |",
    ]
    for row in type_rows:
        lines.append(
            "| "
            f"{_markdown_cell(row.get('full_name'))} | "
            f"{_markdown_cell(row.get('method_count'))} | "
            f"{_markdown_cell(row.get('field_count'))} |"
        )

    lines.extend(["", "## 方法所有者", "", "| owner | 方法数 | 字段数 |", "| --- | --- | --- |"])
    for owner, count in methods_by_owner.most_common():
        lines.append(f"| {_markdown_cell(owner)} | {count} | {fields_by_owner.get(owner, 0)} |")

    lines.extend(["", "## 代表方法", "", "| owner | name | parameters | return_type |", "| --- | --- | --- | --- |"])
    for row in method_rows[:160]:
        lines.append(
            "| "
            f"{_markdown_cell(row.get('owner'))} | "
            f"{_markdown_cell(row.get('name'))} | "
            f"{_markdown_cell(row.get('parameters_resolved') or row.get('parameters'))} | "
            f"{_markdown_cell(row.get('return_type_name') or row.get('return_type'))} |"
        )

    lines.extend(["", "## 代表字段", "", "| owner | name | type |", "| --- | --- | --- |"])
    for row in field_rows[:160]:
        lines.append(
            "| "
            f"{_markdown_cell(row.get('owner'))} | "
            f"{_markdown_cell(row.get('name'))} | "
            f"{_markdown_cell(row.get('type_name') or row.get('type_index'))} |"
        )

    lines.extend(["", "## 字符串线索", "", "| index | value |", "| --- | --- |"])
    for row in string_rows[:120]:
        lines.append(f"| {_markdown_cell(row.get('index'))} | {_markdown_cell(row.get('value'), limit=360)} |")
    path.write_text("\n".join(lines), encoding="utf-8")


def build_fanxiu_il2cpp_download_inventory(
    *,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    resolved_export_root = resolve_fanxiu_export_root(export_root)
    output_dir = (resolved_export_root / "apk_static_index").resolve()
    if not _is_relative_to(output_dir, resolved_export_root):
        raise FanxiuResourceError(f"导出目录必须位于导出根目录内：{resolved_export_root}")
    output_dir.mkdir(parents=True, exist_ok=True)

    all_types = _read_tsv(output_dir / "il2cpp_types.tsv")
    all_methods = _read_tsv(output_dir / "il2cpp_methods.tsv")
    all_fields = _read_tsv(output_dir / "il2cpp_fields.tsv")
    all_strings = _read_tsv(output_dir / "il2cpp_string_literals.tsv")
    type_name_by_index = _build_il2cpp_type_name_map(all_types)

    type_rows = [row for row in all_types if _il2cpp_download_match(row.get("full_name", ""))]
    type_names = {row.get("full_name", "") for row in type_rows}
    method_rows = [
        row
        for row in all_methods
        if row.get("owner", "") in type_names or _il2cpp_download_match(row.get("owner", "")) or _il2cpp_download_match(row.get("qualified_name", ""))
    ]
    field_rows = [
        row
        for row in all_fields
        if row.get("owner", "") in type_names or _il2cpp_download_match(row.get("owner", "")) or _il2cpp_download_match(row.get("qualified_name", ""))
    ]
    string_rows = [row for row in all_strings if _il2cpp_download_string_match(row.get("value", ""))]
    _enrich_il2cpp_download_rows(type_name_by_index=type_name_by_index, method_rows=method_rows, field_rows=field_rows)

    method_rows.sort(key=lambda row: (row.get("owner", ""), _safe_int(row.get("index", ""))))
    field_rows.sort(key=lambda row: (row.get("owner", ""), _safe_int(row.get("index", ""))))
    string_rows.sort(key=lambda row: int(row.get("index", "0") or 0))

    type_count = _write_tsv(
        output_dir / "il2cpp_download_types.tsv",
        [
            "index",
            "namespace",
            "name",
            "full_name",
            "field_start",
            "field_count",
            "method_start",
            "method_count",
            "parent_index",
            "token",
        ],
        type_rows,
    )
    method_count = _write_tsv(
        output_dir / "il2cpp_download_methods.tsv",
        [
            "index",
            "owner",
            "name",
            "qualified_name",
            "parameters",
            "parameters_resolved",
            "declaring_type",
            "return_type",
            "return_type_name",
            "token",
            "flags",
            "slot",
        ],
        method_rows,
    )
    field_count = _write_tsv(
        output_dir / "il2cpp_download_fields.tsv",
        ["index", "owner", "name", "qualified_name", "type_index", "type_name", "token"],
        field_rows,
    )
    string_count = _write_tsv(
        output_dir / "il2cpp_download_strings.tsv",
        ["index", "length", "data_index", "value"],
        string_rows,
    )
    _write_il2cpp_download_inventory_markdown(
        output_dir / "il2cpp_download_inventory.md",
        export_root=resolved_export_root,
        type_rows=type_rows,
        method_rows=method_rows,
        field_rows=field_rows,
        string_rows=string_rows,
    )

    result = {
        "export_root": str(resolved_export_root),
        "output_dir": str(output_dir),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "counts": {
            "types": type_count,
            "methods": method_count,
            "fields": field_count,
            "strings": string_count,
            "methods_by_owner": dict(Counter(row.get("owner", "") for row in method_rows).most_common()),
        },
        "outputs": {
            "summary": str(output_dir / "il2cpp_download_inventory.json"),
            "markdown": str(output_dir / "il2cpp_download_inventory.md"),
            "types": str(output_dir / "il2cpp_download_types.tsv"),
            "methods": str(output_dir / "il2cpp_download_methods.tsv"),
            "fields": str(output_dir / "il2cpp_download_fields.tsv"),
            "strings": str(output_dir / "il2cpp_download_strings.tsv"),
        },
    }
    (output_dir / "il2cpp_download_inventory.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result
