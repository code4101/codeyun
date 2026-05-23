from __future__ import annotations

import csv
import json
import os
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from backend.core.fanxiu_resources import (
    FanxiuResourceError,
    export_fanxiu_unity_text_assets,
    resolve_fanxiu_export_root,
    resolve_fanxiu_resource_root,
)
from backend.core.fanxiu_lua_config import parse_fanxiu_generated_lua_config
from backend.core.fanxiu_wiki import strip_fanxiu_rich_text


_HOT_UPDATE_TERMS = (
    "blld",
    "bluestarsea",
    "gongfa",
    "faze",
    "skill",
    "item",
    "shop",
    "activity",
    "autotask",
    "doublecharge",
    "title",
    "server",
    "download",
    "http",
    "reward",
    "rank",
)

_REQUIRE_RE = re.compile(r"""require\s*(?:\(\s*)?["']([^"']+)["']""")
_FUNCTION_RE = re.compile(r"""(?:^|\n)\s*(?:local\s+)?function\s+([A-Za-z_][\w.:]*)""")
_METHOD_FUNCTION_RE = re.compile(r"""(?m)^function\s+_M[.:]([A-Za-z_]\w*)\s*\(([^)]*)\)""")
_PACKET_REQUIRE_RE = re.compile(
    r"""local\s+(_?[A-Za-z_][\w]*)\s*=\s*require\s*["']GameSystem\.Game\.Message\.module\.world\.blld\.packet\.([^"']+)["']"""
)
_GENERIC_PACKET_REQUIRE_RE = re.compile(
    r"""local\s+(_?[A-Za-z_][\w]*)\s*=\s*require\s*["']GameSystem\.Game\.Message\.module\.([A-Za-z0-9_.]+)\.packet\.([^"']+)["']"""
)
_MESSAGE_POOL_ASSIGN_RE = re.compile(
    r"""local\s+([A-Za-z_]\w*)\s*=\s*SocketManager\.Inst_get\(\):GetMessageFromPools\(_?([A-Za-z_]\w*)\)"""
)
_MESSAGE_POOL_RE = re.compile(r"""GetMessageFromPools\(_([A-Za-z_]\w*)\)""")
_FIELD_ASSIGN_RE = re.compile(r"""(?m)^\s*([A-Za-z_]\w*)\.([A-Za-z_]\w*)\s*=\s*(.+?)\s*$""")
_FIELD_METHOD_USE_RE = re.compile(r"""(?m)^\s*([A-Za-z_]\w*)\.([A-Za-z_]\w*):([A-Za-z_]\w*)\(""")
_MSG_FIELD_RE = re.compile(r"""msg\.([A-Za-z_]\w*)""")
_MODEL_CALL_RE = re.compile(r"""BLLDMgr\.Inst_get\(\)\.Model:([A-Za-z_]\w*)\(([^)]*)\)""")
_MGR_CALL_RE = re.compile(r"""BLLDMgr\.Inst_get\(\):([A-Za-z_]\w*)\(([^)]*)\)""")
_NETLOGIC_CALL_RE = re.compile(r"""(?:self|BLLDMgr\.Inst_get\(\))\.NetLogic[:.]([A-Za-z_]\w*)\(([^)]*)\)""")


def _clean_tsv_cell(value: object, *, limit: int = 1200) -> str:
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
            writer.writerow({field: _clean_tsv_cell(row.get(field, "")) for field in fields})
            count += 1
    return count


def _read_tsv_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8-sig", errors="ignore", newline="") as f:
        return [dict(row) for row in csv.DictReader(f, delimiter="\t")]


def _line_no(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _lua_method_blocks(text: str) -> list[dict[str, object]]:
    matches = list(_METHOD_FUNCTION_RE.finditer(text))
    blocks: list[dict[str, object]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[match.end() : end]
        blocks.append(
            {
                "name": match.group(1),
                "args": match.group(2),
                "body": body,
                "start_line": _line_no(text, match.start()),
                "end_line": _line_no(text, end),
            }
        )
    return blocks


def _unique_join(values: Iterable[object], *, limit: int = 20, sep: str = " | ") -> str:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
        if len(out) >= limit:
            break
    return sep.join(out)


def _find_lua_lines(root: Path, pattern: re.Pattern[str]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    if not root.is_dir():
        return rows
    for path in sorted(root.glob("*.lua"), key=lambda item: item.name.lower()):
        if "__" in path.stem:
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        for line_no, line in enumerate(lines, start=1):
            for match in pattern.finditer(line):
                rows.append(
                    {
                        "file": path.name,
                        "line": line_no,
                        "call": match.group(1),
                        "args": match.group(2).strip(),
                        "text": line.strip(),
                        "path": str(path),
                    }
                )
    return rows


def _markdown_table_cell(value: object, *, limit: int = 260) -> str:
    return _clean_tsv_cell(value, limit=limit).replace("|", "\\|")


def _path_module(rel_path: str) -> str:
    path = Path(rel_path)
    return path.stem.lower()


def _hot_update_group(rel_path: str) -> str:
    parts = rel_path.replace("\\", "/").split("/")
    if len(parts) >= 3 and parts[0] == "lscripts" and parts[1] == "generate" and parts[2] == "cfg":
        return "config"
    if len(parts) >= 4 and parts[0] == "lscripts" and parts[1] == "gamesystem" and parts[2] == "game":
        return "game"
    if len(parts) >= 2:
        return "/".join(parts[:2])
    return parts[0] if parts else ""


def _scan_text_asset(path: Path) -> dict[str, object]:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return {
            "line_count": 0,
            "function_count": 0,
            "requires": "",
            "terms": "",
        }
    lower = text.lower()
    requires = list(dict.fromkeys(_REQUIRE_RE.findall(text)))[:8]
    functions = list(dict.fromkeys(_FUNCTION_RE.findall(text)))[:8]
    terms = [term for term in _HOT_UPDATE_TERMS if term in lower]
    return {
        "line_count": text.count("\n") + (1 if text else 0),
        "function_count": len(functions),
        "requires": " | ".join(requires),
        "functions": " | ".join(functions),
        "terms": " | ".join(terms),
    }


def _write_hot_update_lscripts_markdown(
    path: Path,
    *,
    export_base: Path,
    resource_root: Path,
    bundle_rows: list[dict[str, object]],
    asset_rows: list[dict[str, object]],
    errors: list[dict[str, object]],
) -> None:
    by_status = Counter(str(row["status"]) for row in bundle_rows)
    by_group = Counter(str(row["group"]) for row in bundle_rows)
    by_module_term = Counter()
    for row in bundle_rows:
        for term in str(row.get("terms") or "").split(" | "):
            if term:
                by_module_term[term] += 1

    lines = [
        "# 凡修热更新 Lua 脚本包索引",
        "",
        f"- 资源目录：`{resource_root}`",
        f"- 导出目录：`{export_base}`",
        f"- 脚本包：{len(bundle_rows)}；TextAsset：{len(asset_rows)}；错误：{len(errors)}",
        f"- 状态：{', '.join(f'{name}:{count}' for name, count in by_status.most_common())}",
        f"- 分组：{', '.join(f'{name}:{count}' for name, count in by_group.most_common())}",
        "",
        "## 新增脚本包",
        "",
        "| 模块 | 路径 | TextAsset | Lua | 大小 | 关键词 |",
        "| --- | --- | ---: | ---: | ---: | --- |",
    ]
    for row in [item for item in bundle_rows if item["status"] == "added"][:80]:
        lines.append(
            "| "
            f"{_markdown_table_cell(row['module'])} | "
            f"{_markdown_table_cell(row['logical_path'], limit=180)} | "
            f"{row['text_asset_count']} | "
            f"{row['lua_asset_count']} | "
            f"{row['resource_size']} | "
            f"{_markdown_table_cell(row['terms'], limit=120)} |"
        )

    lines.extend(
        [
            "",
            "## 变更脚本包 Top",
            "",
            "| 模块 | 路径 | TextAsset | Lua | 大小变化 | 关键词 |",
            "| --- | --- | ---: | ---: | ---: | --- |",
        ]
    )
    changed_rows = [row for row in bundle_rows if row["status"] == "changed"]
    changed_rows.sort(key=lambda row: abs(int(row.get("size_delta") or 0)), reverse=True)
    for row in changed_rows[:80]:
        lines.append(
            "| "
            f"{_markdown_table_cell(row['module'])} | "
            f"{_markdown_table_cell(row['logical_path'], limit=180)} | "
            f"{row['text_asset_count']} | "
            f"{row['lua_asset_count']} | "
            f"{row['size_delta']} | "
            f"{_markdown_table_cell(row['terms'], limit=120)} |"
        )

    lines.extend(
        [
            "",
            "## 关键词分布",
            "",
            "| 关键词 | 脚本包数 |",
            "| --- | ---: |",
        ]
    )
    for term, count in by_module_term.most_common(40):
        lines.append(f"| {_markdown_table_cell(term)} | {count} |")

    lines.extend(
        [
            "",
            "## TextAsset 样例",
            "",
            "| 状态 | 模块 | TextAsset | 行数 | 函数数 | require |",
            "| --- | --- | --- | ---: | ---: | --- |",
        ]
    )
    for row in asset_rows[:120]:
        lines.append(
            "| "
            f"{row['status']} | "
            f"{_markdown_table_cell(row['module'])} | "
            f"{_markdown_table_cell(row['asset_name'], limit=160)} | "
            f"{row['line_count']} | "
            f"{row['function_count']} | "
            f"{_markdown_table_cell(row['requires'], limit=180)} |"
        )

    if errors:
        lines.extend(["", "## 错误", "", "| 路径 | 错误 |", "| --- | --- |"])
        for row in errors[:40]:
            lines.append(f"| {_markdown_table_cell(row['logical_path'], limit=180)} | {_markdown_table_cell(row['error'], limit=220)} |")

    path.write_text("\n".join(lines), encoding="utf-8")


def build_fanxiu_hot_update_lscripts_report(
    *,
    resource_root: str | os.PathLike[str] | None = None,
    export_root: str | os.PathLike[str] | None = None,
    statuses: Iterable[str] = ("added", "changed"),
    max_bundles: int | None = None,
) -> dict[str, Any]:
    resolved_resource_root = resolve_fanxiu_resource_root(resource_root)
    export_base = resolve_fanxiu_export_root(export_root)
    output_dir = export_base / "apk_static_index"
    diff_path = output_dir / "resource_manifest_diff.tsv"
    diff_rows = _read_tsv_rows(diff_path)
    if not diff_rows:
        raise FanxiuResourceError(f"资源清单差异文件不存在或为空：{diff_path}")

    status_set = {str(item) for item in statuses}
    candidate_rows = [
        row
        for row in diff_rows
        if row.get("status") in status_set and str(row.get("path") or "").startswith("lscripts/")
    ]
    if max_bundles is not None:
        candidate_rows = candidate_rows[: max(0, int(max_bundles))]

    bundle_rows: list[dict[str, object]] = []
    asset_rows: list[dict[str, object]] = []
    errors: list[dict[str, object]] = []

    for row in candidate_rows:
        logical_path = str(row.get("path") or "")
        actual_path = str(row.get("resource_actual_path") or logical_path)
        module = _path_module(logical_path)
        group = _hot_update_group(logical_path)
        try:
            export_result = export_fanxiu_unity_text_assets(
                actual_path,
                resource_root=resolved_resource_root,
                export_root=export_base,
            )
        except Exception as exc:  # noqa: BLE001 - report all per-bundle extraction failures.
            errors.append(
                {
                    "status": row.get("status", ""),
                    "logical_path": logical_path,
                    "actual_path": actual_path,
                    "error": str(exc),
                }
            )
            continue

        items = export_result.get("items", [])
        bundle_terms = [term for term in _HOT_UPDATE_TERMS if term in logical_path.lower()]
        lua_asset_count = 0
        total_text_bytes = 0
        for item in items:
            output_path = Path(str(item.get("output_path") or ""))
            asset_name = str(item.get("name") or output_path.name)
            scan = _scan_text_asset(output_path)
            total_text_bytes += int(item.get("byte_size") or 0)
            if asset_name.lower().endswith(".lua"):
                lua_asset_count += 1
            for term in str(scan.get("terms") or "").split(" | "):
                if term and term not in bundle_terms:
                    bundle_terms.append(term)
            asset_rows.append(
                {
                    "status": row.get("status", ""),
                    "module": module,
                    "group": group,
                    "logical_path": logical_path,
                    "actual_path": actual_path,
                    "asset_name": asset_name,
                    "path_id": item.get("path_id", ""),
                    "byte_size": item.get("byte_size", ""),
                    "line_count": scan.get("line_count", 0),
                    "function_count": scan.get("function_count", 0),
                    "requires": scan.get("requires", ""),
                    "functions": scan.get("functions", ""),
                    "terms": scan.get("terms", ""),
                    "output_path": str(output_path),
                }
            )

        bundle_rows.append(
            {
                "status": row.get("status", ""),
                "group": group,
                "module": module,
                "logical_path": logical_path,
                "actual_path": actual_path,
                "resource_size": row.get("resource_size", ""),
                "size_delta": row.get("size_delta", ""),
                "resource_md5": row.get("resource_md5", ""),
                "text_asset_count": len(items),
                "lua_asset_count": lua_asset_count,
                "total_text_bytes": total_text_bytes,
                "terms": " | ".join(bundle_terms),
                "output_dir": export_result.get("output_dir", ""),
            }
        )

    bundle_rows.sort(key=lambda item: (str(item["status"]) != "added", str(item["group"]), str(item["module"])))
    asset_rows.sort(key=lambda item: (str(item["status"]) != "added", str(item["module"]), str(item["asset_name"]).lower()))
    errors.sort(key=lambda item: str(item["logical_path"]).lower())

    bundle_count = _write_tsv(
        output_dir / "hot_update_lscripts_bundles.tsv",
        [
            "status",
            "group",
            "module",
            "logical_path",
            "actual_path",
            "resource_size",
            "size_delta",
            "resource_md5",
            "text_asset_count",
            "lua_asset_count",
            "total_text_bytes",
            "terms",
            "output_dir",
        ],
        bundle_rows,
    )
    asset_count = _write_tsv(
        output_dir / "hot_update_lscripts_text_assets.tsv",
        [
            "status",
            "module",
            "group",
            "logical_path",
            "actual_path",
            "asset_name",
            "path_id",
            "byte_size",
            "line_count",
            "function_count",
            "requires",
            "functions",
            "terms",
            "output_path",
        ],
        asset_rows,
    )
    error_count = _write_tsv(
        output_dir / "hot_update_lscripts_errors.tsv",
        ["status", "logical_path", "actual_path", "error"],
        errors,
    )

    result = {
        "resource_root": str(resolved_resource_root),
        "export_root": str(export_base),
        "output_dir": str(output_dir),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "counts": {
            "candidate_bundles": len(candidate_rows),
            "bundles": bundle_count,
            "text_assets": asset_count,
            "errors": error_count,
            "by_status": dict(Counter(str(row["status"]) for row in bundle_rows).most_common()),
            "by_group": dict(Counter(str(row["group"]) for row in bundle_rows).most_common()),
        },
        "outputs": {
            "summary": str(output_dir / "hot_update_lscripts_report.json"),
            "markdown": str(output_dir / "hot_update_lscripts_report.md"),
            "bundles": str(output_dir / "hot_update_lscripts_bundles.tsv"),
            "text_assets": str(output_dir / "hot_update_lscripts_text_assets.tsv"),
            "errors": str(output_dir / "hot_update_lscripts_errors.tsv"),
        },
    }
    _write_hot_update_lscripts_markdown(
        output_dir / "hot_update_lscripts_report.md",
        export_base=export_base,
        resource_root=resolved_resource_root,
        bundle_rows=bundle_rows,
        asset_rows=asset_rows,
        errors=errors,
    )
    (output_dir / "hot_update_lscripts_report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def _find_text_asset_dir(export_base: Path, *parts: str, module: str) -> Path | None:
    root = export_base / "by_source" / Path(*parts)
    candidates = sorted(root.glob(f"{module}_*/text_assets"))
    return candidates[-1] if candidates else None


def _find_lang_path(export_base: Path) -> Path | None:
    candidates = sorted((export_base / "by_source").glob("lscripts/generate/localization/chinese/lang_*/text_assets/*.lua"))
    preferred = [path for path in candidates if path.name.lower().startswith("lang")]
    if preferred:
        return preferred[-1]
    return candidates[-1] if candidates else None


def _parse_config(path: Path | None, lang_path: Path | None) -> dict[str, Any]:
    if path is None or not path.is_file():
        return {"source_path": str(path or ""), "fields": [], "row_count": 0, "rows": []}
    return parse_fanxiu_generated_lua_config(path, lang_path=lang_path)


def _plain(row: dict[str, Any], field: str) -> str:
    value = row.get(f"{field}_plain", row.get(field, ""))
    if isinstance(value, list):
        return ",".join(str(item) for item in value)
    return strip_fanxiu_rich_text(str(value))


def _table_summary_rows(module: str, directory: Path | None, lang_path: Path | None) -> list[dict[str, object]]:
    if directory is None:
        return []
    rows: list[dict[str, object]] = []
    for path in sorted(directory.glob("*.lua"), key=lambda item: item.name.lower()):
        if "__" in path.stem:
            continue
        try:
            data = _parse_config(path, lang_path)
            rows.append(
                {
                    "module": module,
                    "table": path.stem,
                    "row_count": data["row_count"],
                    "field_count": len(data["fields"]),
                    "fields": " | ".join(str(item) for item in data["fields"][:16]),
                    "path": str(path),
                }
            )
        except Exception as exc:  # noqa: BLE001 - keep per-table parsing evidence.
            rows.append(
                {
                    "module": module,
                    "table": path.stem,
                    "row_count": 0,
                    "field_count": 0,
                    "fields": "",
                    "path": str(path),
                    "error": str(exc),
                }
            )
    return rows


def _map_by_id(rows: list[dict[str, Any]], field: str = "id") -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    for row in rows:
        try:
            result[int(row[field])] = row
        except (KeyError, TypeError, ValueError):
            continue
    return result


def _aggregate_bluestarsea_tree(rows: list[dict[str, Any]]) -> list[dict[str, object]]:
    stats: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in rows:
        key = (str(row.get("faqiId", "")), str(row.get("group", "")), _plain(row, "name"))
        stat = stats.setdefault(
            key,
            {
                "faqi_id": key[0],
                "group": key[1],
                "name": key[2],
                "row_count": 0,
                "max_level": 0,
                "costs": Counter(),
                "skills": Counter(),
                "fazes": Counter(),
                "des_samples": [],
            },
        )
        stat["row_count"] += 1
        try:
            stat["max_level"] = max(int(stat["max_level"]), int(row.get("level") or 0))
        except ValueError:
            pass
        if row.get("cost"):
            stat["costs"][str(row["cost"])] += 1
        if row.get("skill"):
            stat["skills"][str(row["skill"])] += 1
        if row.get("faze"):
            stat["fazes"][str(row["faze"])] += 1
        if len(stat["des_samples"]) < 2 and row.get("des"):
            stat["des_samples"].append(_plain(row, "des"))
    out: list[dict[str, object]] = []
    for stat in stats.values():
        out.append(
            {
                "faqi_id": stat["faqi_id"],
                "group": stat["group"],
                "name": stat["name"],
                "row_count": stat["row_count"],
                "max_level": stat["max_level"],
                "costs": " | ".join(name for name, _count in stat["costs"].most_common(6)),
                "skills": " | ".join(name for name, _count in stat["skills"].most_common(6)),
                "fazes": " | ".join(name for name, _count in stat["fazes"].most_common(6)),
                "des_samples": " || ".join(stat["des_samples"]),
            }
        )
    out.sort(key=lambda item: (str(item["faqi_id"]), str(item["group"]), str(item["name"])))
    return out


def _write_hot_update_feature_probe_markdown(
    path: Path,
    *,
    export_base: Path,
    table_rows: list[dict[str, object]],
    blld_activity_rows: list[dict[str, object]],
    bluestarsea_base_rows: list[dict[str, object]],
    bluestarsea_tree_rows: list[dict[str, object]],
    blld_level_rows: list[dict[str, object]],
) -> None:
    activity_names = sorted({str(row.get("activity_name") or "") for row in blld_activity_rows if row.get("activity_name")})
    recommend_tips = sorted({str(row.get("recommend_tips") or "") for row in blld_level_rows if row.get("recommend_tips")})
    lines = [
        "# 凡修新增玩法配置探针",
        "",
        f"- 导出目录：`{export_base}`",
        f"- BLLD 活动名：{', '.join(activity_names) or '未解析'}",
        f"- BLLD 关卡数：{len(blld_level_rows)}；蓝色星海节点组：{len(bluestarsea_tree_rows)}",
        "",
        "## 表汇总",
        "",
        "| 模块 | 表 | 行数 | 字段数 | 字段样例 |",
        "| --- | --- | ---: | ---: | --- |",
    ]
    for row in table_rows:
        lines.append(
            "| "
            f"{_markdown_table_cell(row['module'])} | "
            f"{_markdown_table_cell(row['table'])} | "
            f"{row['row_count']} | "
            f"{row['field_count']} | "
            f"{_markdown_table_cell(row['fields'], limit=180)} |"
        )

    lines.extend(["", "## BLLD 活动入口", "", "| id | 活动名 | activityId | 时间 | 条件 | 红点 |", "| ---: | --- | ---: | --- | --- | --- |"])
    for row in blld_activity_rows:
        time_desc = f"{row.get('start_time', '')} -> {row.get('end_time', '')}"
        lines.append(
            "| "
            f"{row.get('id', '')} | "
            f"{_markdown_table_cell(row.get('activity_name', ''))} | "
            f"{row.get('activity_id', '')} | "
            f"{_markdown_table_cell(time_desc, limit=160)} | "
            f"{_markdown_table_cell(row.get('join_condition_describe', ''), limit=120)} | "
            f"{_markdown_table_cell(row.get('red_dot', ''))} |"
        )

    lines.extend(
        [
            "",
            "## 蓝色星海分区",
            "",
            "| id | 名称 | interface | 开启条件 | 未开启文案 |",
            "| ---: | --- | ---: | --- | --- |",
        ]
    )
    for row in bluestarsea_base_rows:
        lines.append(
            "| "
            f"{row.get('id', '')} | "
            f"{_markdown_table_cell(row.get('name', ''))} | "
            f"{row.get('interface', '')} | "
            f"{_markdown_table_cell(row.get('open_condition', ''), limit=160)} | "
            f"{_markdown_table_cell(row.get('open_lan', ''), limit=120)} |"
        )

    lines.extend(
        [
            "",
            "## 蓝色星海节点组",
            "",
            "| faqiId | group | 名称 | 行数 | 最高等级 | 消耗样例 | 效果样例 |",
            "| --- | --- | --- | ---: | ---: | --- | --- |",
        ]
    )
    for row in bluestarsea_tree_rows[:80]:
        lines.append(
            "| "
            f"{row.get('faqi_id', '')} | "
            f"{row.get('group', '')} | "
            f"{_markdown_table_cell(row.get('name', ''))} | "
            f"{row.get('row_count', '')} | "
            f"{row.get('max_level', '')} | "
            f"{_markdown_table_cell(row.get('costs', ''), limit=120)} | "
            f"{_markdown_table_cell(row.get('des_samples', ''), limit=220)} |"
        )

    lines.extend(
        [
            "",
            "## BLLD 关卡推荐",
            "",
            "| 关卡 | 名称 | 推荐 | 奖励标题 |",
            "| ---: | --- | --- | --- |",
        ]
    )
    for row in blld_level_rows[:40]:
        lines.append(
            "| "
            f"{row.get('id', '')} | "
            f"{_markdown_table_cell(row.get('name', ''))} | "
            f"{_markdown_table_cell(row.get('recommend_tips', ''), limit=180)} | "
            f"{_markdown_table_cell(row.get('reward_show_title', ''), limit=180)} |"
        )
    if recommend_tips:
        lines.extend(["", "## 推荐词条", ""])
        for tip in recommend_tips[:40]:
            lines.append(f"- {tip}")
    path.write_text("\n".join(lines), encoding="utf-8")


def build_fanxiu_hot_update_feature_probe(
    *,
    export_root: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    export_base = resolve_fanxiu_export_root(export_root)
    output_dir = export_base / "apk_static_index"
    output_dir.mkdir(parents=True, exist_ok=True)
    lang_path = _find_lang_path(export_base)
    blld_dir = _find_text_asset_dir(export_base, "lscripts", "generate", "cfg", module="blld")
    bluestarsea_dir = _find_text_asset_dir(export_base, "lscripts", "generate", "cfg", module="bluestarsea")
    activity_dir = _find_text_asset_dir(export_base, "lscripts", "generate", "cfg", module="activity")
    if blld_dir is None:
        raise FanxiuResourceError("未找到已导出的 BLLD 配置 TextAsset，请先运行热更新 lscripts 报告。")
    if bluestarsea_dir is None:
        raise FanxiuResourceError("未找到已导出的 BlueStarSea 配置 TextAsset，请先运行热更新 lscripts 报告。")

    table_rows = _table_summary_rows("BLLD", blld_dir, lang_path) + _table_summary_rows("BlueStarSea", bluestarsea_dir, lang_path)
    blld_activity = _parse_config(blld_dir / "ActivityBase.lua", lang_path)
    blld_level = _parse_config(blld_dir / "Level.lua", lang_path)
    blld_config_value = _parse_config(blld_dir / "ConfigValue.lua", lang_path)
    bluestarsea_base = _parse_config(bluestarsea_dir / "Base.lua", lang_path)
    bluestarsea_tree = _parse_config(bluestarsea_dir / "Tree.lua", lang_path)
    activity = _parse_config(activity_dir / "Activity.lua" if activity_dir else None, lang_path)
    activity_by_id = _map_by_id(activity["rows"])

    blld_activity_rows: list[dict[str, object]] = []
    for row in blld_activity["rows"]:
        activity_id = int(row.get("activityId") or 0)
        activity_row = activity_by_id.get(activity_id, {})
        blld_activity_rows.append(
            {
                "id": row.get("id", ""),
                "activity_id": activity_id,
                "activity_name": _plain(activity_row, "name") if activity_row else "",
                "model": row.get("model", ""),
                "level_group": row.get("levelGroup", ""),
                "faqi": row.get("faqi", ""),
                "blue_star_sea": row.get("blueStarSea", ""),
                "blood_moon_group": row.get("bloodMoonGroup", ""),
                "rule_id": row.get("ruleId", ""),
                "start_time": activity_row.get("startTime", ""),
                "end_time": activity_row.get("endTime", ""),
                "join_condition_describe": _plain(activity_row, "joinConditionDescribe") if activity_row else "",
                "red_dot": activity_row.get("redDot", ""),
                "open_condition": activity_row.get("openCondition", ""),
            }
        )

    blld_level_rows = [
        {
            "id": row.get("id", ""),
            "group": row.get("group", ""),
            "layer": row.get("layer", ""),
            "stage": row.get("stage", ""),
            "sub_layer": row.get("subLayer", ""),
            "name": _plain(row, "name"),
            "recommend_tips": _plain(row, "recommendTips"),
            "reward_show_title": _plain(row, "rewardShowTitle"),
            "show_img": row.get("showImg", ""),
            "scene_id": row.get("sceneId", ""),
            "push_reward": row.get("pushReward", ""),
            "find_reward": row.get("findReward", ""),
        }
        for row in blld_level["rows"]
    ]

    blld_config_rows = [
        {"id": row.get("id", ""), "value": row.get("value", "")}
        for row in blld_config_value["rows"]
    ]
    bluestarsea_base_rows = [
        {
            "id": row.get("id", ""),
            "sort": row.get("sort", ""),
            "name": _plain(row, "name"),
            "interface": row.get("interface", ""),
            "show_condition": row.get("showcondition", ""),
            "open_condition": row.get("opencondition", ""),
            "open_lan": _plain(row, "openlan"),
            "name_img": row.get("nameImg", ""),
        }
        for row in bluestarsea_base["rows"]
    ]
    bluestarsea_tree_rows = _aggregate_bluestarsea_tree(bluestarsea_tree["rows"])

    table_count = _write_tsv(
        output_dir / "hot_update_feature_tables.tsv",
        ["module", "table", "row_count", "field_count", "fields", "path", "error"],
        table_rows,
    )
    activity_count = _write_tsv(
        output_dir / "hot_update_blld_activities.tsv",
        [
            "id",
            "activity_id",
            "activity_name",
            "model",
            "level_group",
            "faqi",
            "blue_star_sea",
            "blood_moon_group",
            "rule_id",
            "start_time",
            "end_time",
            "join_condition_describe",
            "red_dot",
            "open_condition",
        ],
        blld_activity_rows,
    )
    level_count = _write_tsv(
        output_dir / "hot_update_blld_levels.tsv",
        [
            "id",
            "group",
            "layer",
            "stage",
            "sub_layer",
            "name",
            "recommend_tips",
            "reward_show_title",
            "show_img",
            "scene_id",
            "push_reward",
            "find_reward",
        ],
        blld_level_rows,
    )
    config_value_count = _write_tsv(output_dir / "hot_update_blld_config_values.tsv", ["id", "value"], blld_config_rows)
    base_count = _write_tsv(
        output_dir / "hot_update_bluestarsea_base.tsv",
        ["id", "sort", "name", "interface", "show_condition", "open_condition", "open_lan", "name_img"],
        bluestarsea_base_rows,
    )
    tree_count = _write_tsv(
        output_dir / "hot_update_bluestarsea_tree_summary.tsv",
        ["faqi_id", "group", "name", "row_count", "max_level", "costs", "skills", "fazes", "des_samples"],
        bluestarsea_tree_rows,
    )

    result = {
        "export_root": str(export_base),
        "output_dir": str(output_dir),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "inputs": {
            "lang_path": str(lang_path or ""),
            "blld_dir": str(blld_dir),
            "bluestarsea_dir": str(bluestarsea_dir),
            "activity_dir": str(activity_dir or ""),
        },
        "counts": {
            "tables": table_count,
            "blld_activities": activity_count,
            "blld_levels": level_count,
            "blld_config_values": config_value_count,
            "bluestarsea_base": base_count,
            "bluestarsea_tree_groups": tree_count,
        },
        "outputs": {
            "summary": str(output_dir / "hot_update_feature_probe_report.json"),
            "markdown": str(output_dir / "hot_update_feature_probe_report.md"),
            "tables": str(output_dir / "hot_update_feature_tables.tsv"),
            "blld_activities": str(output_dir / "hot_update_blld_activities.tsv"),
            "blld_levels": str(output_dir / "hot_update_blld_levels.tsv"),
            "blld_config_values": str(output_dir / "hot_update_blld_config_values.tsv"),
            "bluestarsea_base": str(output_dir / "hot_update_bluestarsea_base.tsv"),
            "bluestarsea_tree": str(output_dir / "hot_update_bluestarsea_tree_summary.tsv"),
        },
    }
    _write_hot_update_feature_probe_markdown(
        output_dir / "hot_update_feature_probe_report.md",
        export_base=export_base,
        table_rows=table_rows,
        blld_activity_rows=blld_activity_rows,
        bluestarsea_base_rows=bluestarsea_base_rows,
        bluestarsea_tree_rows=bluestarsea_tree_rows,
        blld_level_rows=blld_level_rows,
    )
    (output_dir / "hot_update_feature_probe_report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def _read_blld_packet_index(export_base: Path) -> tuple[list[dict[str, str]], dict[str, list[dict[str, str]]]]:
    packet_dir = export_base / "parsed_configs" / "lua_packet_index"
    packets = [
        row
        for row in _read_tsv_rows(packet_dir / "packets.tsv")
        if row.get("module") == "world.blld" or "Blld" in str(row.get("name") or "")
    ]
    fields_by_packet: dict[str, list[dict[str, str]]] = {}
    for row in _read_tsv_rows(packet_dir / "packet_fields.tsv"):
        name = str(row.get("packet_name") or "")
        if row.get("module") == "world.blld" or "Blld" in name:
            fields_by_packet.setdefault(name, []).append(row)
    return packets, fields_by_packet


def _read_packet_index_by_module(
    export_base: Path,
    *,
    module_name: str,
    name_keyword: str,
) -> tuple[list[dict[str, str]], dict[str, list[dict[str, str]]]]:
    packet_dir = export_base / "parsed_configs" / "lua_packet_index"
    packets = [
        row
        for row in _read_tsv_rows(packet_dir / "packets.tsv")
        if row.get("module") == module_name or name_keyword in str(row.get("name") or "")
    ]
    fields_by_packet: dict[str, list[dict[str, str]]] = {}
    for row in _read_tsv_rows(packet_dir / "packet_fields.tsv"):
        name = str(row.get("packet_name") or "")
        if row.get("module") == module_name or name_keyword in name:
            fields_by_packet.setdefault(name, []).append(row)
    return packets, fields_by_packet


def _parse_registered_packets(text: str) -> list[dict[str, object]]:
    lines = text.splitlines()
    rows: list[dict[str, object]] = []
    for index, line in enumerate(lines):
        match = re.search(r"""F_Register\(_?([A-Za-z_]\w*):getId\(\),typeof\(_?([A-Za-z_]\w*)\)""", line)
        if not match:
            continue
        packet_name = match.group(1)
        callback = ""
        if "function(msg" in line:
            for next_line in lines[index + 1 : index + 5]:
                callback_match = re.search(r"""(?:self|_M)\.([A-Za-z_]\w*)\(msg\)""", next_line)
                if callback_match:
                    callback = callback_match.group(1)
                    break
        rows.append(
            {
                "packet": packet_name,
                "direction": "client_to_server" if packet_name.startswith("CM_") else "server_to_client",
                "callback": callback,
                "line": index + 1,
            }
        )
    return rows


def _manager_model_call_re(manager_name: str) -> re.Pattern[str]:
    return re.compile(rf"""{re.escape(manager_name)}\.Inst_get\(\)\.Model:([A-Za-z_]\w*)\(([^)]*)\)""")


def _manager_call_re(manager_name: str) -> re.Pattern[str]:
    return re.compile(rf"""{re.escape(manager_name)}\.Inst_get\(\):([A-Za-z_]\w*)\(([^)]*)\)""")


def _netlogic_call_re(manager_name: str) -> re.Pattern[str]:
    return re.compile(rf"""(?:self|{re.escape(manager_name)}\.Inst_get\(\))\.NetLogic[:.]([A-Za-z_]\w*)\(([^)]*)\)""")


def _parse_net_flows(
    net_logic_path: Path,
    *,
    packet_module: str,
    manager_name: str,
    init_names: set[str],
) -> tuple[list[dict[str, object]], list[dict[str, object]], dict[str, str]]:
    text = net_logic_path.read_text(encoding="utf-8", errors="ignore")
    local_packet_names = {
        local_name.lstrip("_"): packet_name.split(".")[-1]
        for local_name, module_name, packet_name in _GENERIC_PACKET_REQUIRE_RE.findall(text)
        if module_name == packet_module
    }
    model_call_re = _manager_model_call_re(manager_name)
    mgr_call_re = _manager_call_re(manager_name)
    registered_rows = _parse_registered_packets(text)
    flow_rows: list[dict[str, object]] = []
    for block in _lua_method_blocks(text):
        name = str(block["name"])
        if name in init_names:
            continue
        body = str(block["body"])
        pool_assign = _MESSAGE_POOL_ASSIGN_RE.search(body)
        message_var = pool_assign.group(1) if pool_assign else ""
        message_name = pool_assign.group(2) if pool_assign else ""
        if not message_name:
            message_match = _MESSAGE_POOL_RE.search(body)
            message_name = message_match.group(1) if message_match else ""
        packet_name = local_packet_names.get(message_name.lstrip("_"), message_name.lstrip("_"))
        if not packet_name and (name.startswith("SM_") or name.startswith("CM_")):
            packet_name = name[:-3] if name.endswith("Fun") else name
        field_targets = {item for item in {message_var, message_name, message_name.lstrip("_"), packet_name, packet_name.lstrip("_")} if item}

        direct_fields: list[str] = []
        vo_fields: list[str] = []
        for assign_match in _FIELD_ASSIGN_RE.finditer(body):
            target, field, expr = assign_match.groups()
            item = f"{field}={expr.strip()}"
            if target in field_targets:
                direct_fields.append(item)
            elif target == "vo":
                vo_fields.append(item)
        used_fields = [
            f"{field}:{method}()"
            for target, field, method in _FIELD_METHOD_USE_RE.findall(body)
            if target in field_targets
        ]

        model_calls = [f"{call}({args.strip()})" for call, args in model_call_re.findall(body)]
        mgr_calls = [f"{call}({args.strip()})" for call, args in mgr_call_re.findall(body)]
        manager_calls = [
            f"{mgr}:{call}()"
            for mgr, call in re.findall(r"""([A-Za-z_]\w*Mgr)\.Inst_get\(\).*?:([A-Za-z_]\w*)\(""", body)
            if mgr != manager_name
        ]
        code_guards = []
        if re.search(r"""msg\.code\s*==\s*0""", body):
            code_guards.append("msg.code==0")
        if re.search(r"""msg\.code\s*~=\s*0""", body):
            code_guards.append("msg.code~=0")

        flow_rows.append(
            {
                "flow_kind": "send" if name.startswith("CM_") else "receive" if name.startswith("SM_") else "other",
                "function": name,
                "packet": packet_name,
                "args": block["args"],
                "assigned_fields": _unique_join(direct_fields + used_fields),
                "vo_fields": _unique_join(vo_fields),
                "msg_fields": _unique_join(_MSG_FIELD_RE.findall(body)),
                "model_calls": _unique_join(model_calls),
                "mgr_calls": _unique_join(mgr_calls + manager_calls),
                "code_guard": _unique_join(code_guards),
                "send_message": "yes" if "F_SendMsg(" in body else "",
                "call_site_count": 0,
                "start_line": block["start_line"],
                "end_line": block["end_line"],
                "path": str(net_logic_path),
            }
        )
    return registered_rows, flow_rows, local_packet_names


def _parse_blld_registered_packets(text: str) -> list[dict[str, object]]:
    lines = text.splitlines()
    rows: list[dict[str, object]] = []
    for index, line in enumerate(lines):
        match = re.search(r"""F_Register\(_([A-Za-z_]\w*):getId\(\),typeof\(_?([A-Za-z_]\w*)\)""", line)
        if not match:
            continue
        packet_name = match.group(1)
        callback = ""
        if "function(msg" in line:
            for next_line in lines[index + 1 : index + 5]:
                callback_match = re.search(r"""self\.([A-Za-z_]\w*)\(msg\)""", next_line)
                if callback_match:
                    callback = callback_match.group(1)
                    break
        rows.append(
            {
                "packet": packet_name,
                "direction": "client_to_server" if packet_name.startswith("CM_") else "server_to_client",
                "callback": callback,
                "line": index + 1,
            }
        )
    return rows


def _parse_blld_net_flows(net_logic_path: Path) -> tuple[list[dict[str, object]], list[dict[str, object]], dict[str, str]]:
    text = net_logic_path.read_text(encoding="utf-8", errors="ignore")
    local_packet_names = {
        local_name.lstrip("_"): packet_name.split(".")[-1]
        for local_name, packet_name in _PACKET_REQUIRE_RE.findall(text)
    }
    registered_rows = _parse_blld_registered_packets(text)
    flow_rows: list[dict[str, object]] = []
    for block in _lua_method_blocks(text):
        name = str(block["name"])
        if name in {"_init_", "LuaBLLDNetLogic", "Destroy"}:
            continue
        body = str(block["body"])
        message_match = _MESSAGE_POOL_RE.search(body)
        message_name = message_match.group(1) if message_match else ""
        packet_name = local_packet_names.get(message_name, message_name)
        if not packet_name and (name.startswith("SM_") or name.startswith("CM_")):
            packet_name = name[:-3] if name.endswith("Fun") else name

        direct_fields: list[str] = []
        vo_fields: list[str] = []
        for assign_match in _FIELD_ASSIGN_RE.finditer(body):
            target, field, expr = assign_match.groups()
            item = f"{field}={expr.strip()}"
            if target == packet_name:
                direct_fields.append(item)
            elif target == "vo":
                vo_fields.append(item)
        used_fields = [
            f"{field}:{method}()"
            for target, field, method in _FIELD_METHOD_USE_RE.findall(body)
            if target == packet_name
        ]

        model_calls = [f"{call}({args.strip()})" for call, args in _MODEL_CALL_RE.findall(body)]
        mgr_calls = [f"{call}({args.strip()})" for call, args in _MGR_CALL_RE.findall(body)]
        manager_calls = [
            f"{mgr}:{call}()"
            for mgr, call in re.findall(r"""([A-Za-z_]\w*Mgr)\.Inst_get\(\).*?:([A-Za-z_]\w*)\(""", body)
            if mgr not in {"BLLDMgr"}
        ]
        code_guards = []
        if "msg.code==0" in body:
            code_guards.append("msg.code==0")
        if "msg.code~=0" in body:
            code_guards.append("msg.code~=0")

        flow_rows.append(
            {
                "flow_kind": "send" if name.startswith("CM_") else "receive" if name.startswith("SM_") else "other",
                "function": name,
                "packet": packet_name,
                "args": block["args"],
                "assigned_fields": _unique_join(direct_fields + used_fields),
                "vo_fields": _unique_join(vo_fields),
                "msg_fields": _unique_join(_MSG_FIELD_RE.findall(body)),
                "model_calls": _unique_join(model_calls),
                "mgr_calls": _unique_join(mgr_calls + manager_calls),
                "code_guard": _unique_join(code_guards),
                "send_message": "yes" if "F_SendMsg(" in body else "",
                "call_site_count": 0,
                "start_line": block["start_line"],
                "end_line": block["end_line"],
                "path": str(net_logic_path),
            }
        )
    return registered_rows, flow_rows, local_packet_names


def _fields_summary(fields: list[dict[str, str]]) -> str:
    return _unique_join(
        (
            f"{row.get('field_index', '')}:{row.get('field_name', '')}:{row.get('type_hint') or row.get('read_method', '')}"
            for row in sorted(fields, key=lambda item: int(item.get("field_index") or 0))
        ),
        limit=60,
    )


def _write_blld_runtime_probe_markdown(
    path: Path,
    *,
    export_base: Path,
    blld_dir: Path,
    packet_rows: list[dict[str, object]],
    flow_rows: list[dict[str, object]],
    call_rows: list[dict[str, object]],
    anomaly_rows: list[dict[str, object]],
) -> None:
    send_rows = [row for row in flow_rows if row["flow_kind"] == "send"]
    receive_rows = [row for row in flow_rows if row["flow_kind"] == "receive"]
    by_direction = Counter(str(row.get("direction") or "") for row in packet_rows)
    lines = [
        "# BLLD 客户端运行与网络探针",
        "",
        f"- 导出目录：`{export_base}`",
        f"- BLLD Lua：`{blld_dir}`",
        f"- 协议项：{len(packet_rows)}；发送入口：{len(send_rows)}；回包入口：{len(receive_rows)}；NetLogic 调用点：{len(call_rows)}；异常点：{len(anomaly_rows)}",
        f"- 协议方向：{', '.join(f'{name}:{count}' for name, count in by_direction.most_common())}",
        "- 说明：这是客户端静态结构报告，只能证明客户端代码如何组织字段和处理回包，不能证明服务端是否信任这些字段。",
        "",
        "## 协议字段",
        "",
        "| id | 协议 | 方向 | 字段 | NetLogic | 回调 |",
        "| ---: | --- | --- | --- | --- | --- |",
    ]
    for row in packet_rows:
        lines.append(
            "| "
            f"{row.get('id', '')} | "
            f"{_markdown_table_cell(row.get('name', ''), limit=120)} | "
            f"{_markdown_table_cell(row.get('direction', ''), limit=80)} | "
            f"{_markdown_table_cell(row.get('fields', ''), limit=260)} | "
            f"{_markdown_table_cell(row.get('net_function', ''), limit=100)} | "
            f"{_markdown_table_cell(row.get('registered_callback', ''), limit=100)} |"
        )

    lines.extend(
        [
            "",
            "## 客户端发送入口",
            "",
            "| 函数 | 协议 | 参数 | 发送字段 | VO字段 | 调用点 |",
            "| --- | --- | --- | --- | --- | ---: |",
        ]
    )
    for row in send_rows:
        lines.append(
            "| "
            f"{_markdown_table_cell(row.get('function', ''), limit=120)} | "
            f"{_markdown_table_cell(row.get('packet', ''), limit=120)} | "
            f"{_markdown_table_cell(row.get('args', ''), limit=160)} | "
            f"{_markdown_table_cell(row.get('assigned_fields', ''), limit=260)} | "
            f"{_markdown_table_cell(row.get('vo_fields', ''), limit=180)} | "
            f"{row.get('call_site_count', '')} |"
        )

    lines.extend(
        [
            "",
            "## 服务端回包入口",
            "",
            "| 函数 | 协议 | msg字段 | 落点 | 其他调用 | code判断 |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in receive_rows:
        lines.append(
            "| "
            f"{_markdown_table_cell(row.get('function', ''), limit=120)} | "
            f"{_markdown_table_cell(row.get('packet', ''), limit=120)} | "
            f"{_markdown_table_cell(row.get('msg_fields', ''), limit=220)} | "
            f"{_markdown_table_cell(row.get('model_calls', ''), limit=240)} | "
            f"{_markdown_table_cell(row.get('mgr_calls', ''), limit=220)} | "
            f"{_markdown_table_cell(row.get('code_guard', ''), limit=120)} |"
        )

    lines.extend(["", "## NetLogic 调用点", "", "| 文件 | 行 | 调用 | 参数 |", "| --- | ---: | --- | --- |"])
    for row in call_rows[:120]:
        lines.append(
            "| "
            f"{_markdown_table_cell(row.get('file', ''), limit=120)} | "
            f"{row.get('line', '')} | "
            f"{_markdown_table_cell(row.get('call', ''), limit=120)} | "
            f"{_markdown_table_cell(row.get('args', ''), limit=220)} |"
        )

    if anomaly_rows:
        lines.extend(["", "## 静态异常点", "", "| 类型 | 对象 | 证据 |", "| --- | --- | --- |"])
        for row in anomaly_rows:
            lines.append(
                "| "
                f"{_markdown_table_cell(row.get('type', ''), limit=120)} | "
                f"{_markdown_table_cell(row.get('subject', ''), limit=140)} | "
                f"{_markdown_table_cell(row.get('evidence', ''), limit=300)} |"
            )

    lines.extend(
        [
            "",
            "## 初步结论",
            "",
            "- `CM_BlldFinishAndReward` 会从客户端带上 `levelId/findReward/success/passRate`，但客户端随后等待 `SM_BlldFinishAndReward` 且成功后再次 `CM_BlldSync`，所以静态上更像“客户端上报战斗结果，服务端回写最终奖励”。",
            "- 协议索引里存在 `CM_BlldFind/SM_BlldFind`，当前 `BLLDNetLogic.lua` 没有注册和调用，可能是旧流程残留、备用流程，或由别的脚本版本接入。",
            "- `CM_BlldFaqiLevelUp` 协议字段有 `faqiId`，当前发送函数没有填字段，后续值得沿 UI 按钮调用链继续确认。",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def build_fanxiu_blld_runtime_probe(
    *,
    export_root: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    export_base = resolve_fanxiu_export_root(export_root)
    output_dir = export_base / "apk_static_index"
    output_dir.mkdir(parents=True, exist_ok=True)
    blld_dir = _find_text_asset_dir(export_base, "lscripts", "gamesystem", "game", module="blld")
    if blld_dir is None:
        raise FanxiuResourceError("未找到已导出的 BLLD 游戏逻辑 TextAsset，请先运行热更新 lscripts 报告。")
    net_logic_path = blld_dir / "BLLDNetLogic.lua"
    if not net_logic_path.is_file():
        raise FanxiuResourceError(f"未找到 BLLDNetLogic.lua：{net_logic_path}")

    packet_index_rows, fields_by_packet = _read_blld_packet_index(export_base)
    registered_rows, flow_rows, _local_packet_names = _parse_blld_net_flows(net_logic_path)
    registered_by_packet = {str(row["packet"]): row for row in registered_rows}
    flow_by_packet = {str(row["packet"]): row for row in flow_rows if row.get("packet")}
    call_rows = _find_lua_lines(blld_dir, _NETLOGIC_CALL_RE)
    call_count_by_name = Counter(str(row["call"]) for row in call_rows)
    for row in flow_rows:
        row["call_site_count"] = call_count_by_name.get(str(row["function"]), 0)

    packet_names = set(fields_by_packet) | {str(row.get("name") or "") for row in packet_index_rows} | set(registered_by_packet) | {
        str(row.get("packet") or "") for row in flow_rows if row.get("packet")
    }
    packet_rows: list[dict[str, object]] = []
    packet_index_by_name = {str(row.get("name") or ""): row for row in packet_index_rows}
    for packet_name in sorted(name for name in packet_names if name):
        index_row = packet_index_by_name.get(packet_name, {})
        fields = fields_by_packet.get(packet_name, [])
        flow = flow_by_packet.get(packet_name, {})
        registration = registered_by_packet.get(packet_name, {})
        packet_rows.append(
            {
                "id": index_row.get("id", ""),
                "name": packet_name,
                "direction": index_row.get("direction") or registration.get("direction") or ("client_to_server" if packet_name.startswith("CM_") else "server_to_client" if packet_name.startswith("SM_") else "value_object"),
                "field_count": index_row.get("field_count", len(fields)),
                "fields": _fields_summary(fields),
                "registered_callback": registration.get("callback", ""),
                "net_function": flow.get("function", ""),
                "client_send_fields": flow.get("assigned_fields", ""),
                "msg_read_fields": flow.get("msg_fields", ""),
                "source_file": index_row.get("file", ""),
                "package": index_row.get("package", ""),
                "path": index_row.get("relative_path", ""),
            }
        )

    anomaly_rows: list[dict[str, object]] = []
    registered_packets = set(registered_by_packet)
    indexed_packet_names = {str(row.get("name") or "") for row in packet_index_rows}
    for packet_name in sorted(indexed_packet_names - registered_packets):
        if packet_name.startswith(("CM_", "SM_")):
            anomaly_rows.append(
                {
                    "type": "packet_not_registered_in_netlogic",
                    "subject": packet_name,
                    "evidence": "packet index 中存在，但 BLLDNetLogic.lua 未注册",
                    "path": str(net_logic_path),
                    "line": "",
                }
            )
    known_net_funcs = {str(row.get("function") or "") for row in flow_rows} | {"Destroy"}
    for call in call_rows:
        if str(call.get("call") or "") not in known_net_funcs:
            anomaly_rows.append(
                {
                    "type": "netlogic_call_without_function",
                    "subject": call.get("call", ""),
                    "evidence": f"{call.get('file')}:{call.get('line')} {call.get('text')}",
                    "path": call.get("path", ""),
                    "line": call.get("line", ""),
                }
            )
    for row in flow_rows:
        if row.get("flow_kind") != "send" or not row.get("packet"):
            continue
        field_names = {str(field.get("field_name") or "") for field in fields_by_packet.get(str(row["packet"]), [])}
        used_names = {
            item.split("=", 1)[0].split(":", 1)[0]
            for item in str(row.get("assigned_fields") or "").split(" | ")
            if item
        }
        missing = sorted(field_names - used_names)
        if missing:
            anomaly_rows.append(
                {
                    "type": "client_send_field_not_filled_in_function",
                    "subject": row["packet"],
                    "evidence": f"{row['function']} 未直接填充字段：{', '.join(missing)}",
                    "path": row["path"],
                    "line": row["start_line"],
                }
            )

    packet_count = _write_tsv(
        output_dir / "hot_update_blld_net_packets.tsv",
        [
            "id",
            "name",
            "direction",
            "field_count",
            "fields",
            "registered_callback",
            "net_function",
            "client_send_fields",
            "msg_read_fields",
            "source_file",
            "package",
            "path",
        ],
        packet_rows,
    )
    flow_count = _write_tsv(
        output_dir / "hot_update_blld_net_flows.tsv",
        [
            "flow_kind",
            "function",
            "packet",
            "args",
            "assigned_fields",
            "vo_fields",
            "msg_fields",
            "model_calls",
            "mgr_calls",
            "code_guard",
            "send_message",
            "call_site_count",
            "start_line",
            "end_line",
            "path",
        ],
        flow_rows,
    )
    call_count = _write_tsv(
        output_dir / "hot_update_blld_netlogic_call_sites.tsv",
        ["file", "line", "call", "args", "text", "path"],
        call_rows,
    )
    anomaly_count = _write_tsv(
        output_dir / "hot_update_blld_runtime_anomalies.tsv",
        ["type", "subject", "evidence", "path", "line"],
        anomaly_rows,
    )

    result = {
        "export_root": str(export_base),
        "output_dir": str(output_dir),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "inputs": {
            "blld_dir": str(blld_dir),
            "net_logic_path": str(net_logic_path),
            "packet_index_dir": str(export_base / "parsed_configs" / "lua_packet_index"),
        },
        "counts": {
            "packets": packet_count,
            "flows": flow_count,
            "call_sites": call_count,
            "anomalies": anomaly_count,
            "by_direction": dict(Counter(str(row.get("direction") or "") for row in packet_rows).most_common()),
            "by_flow_kind": dict(Counter(str(row.get("flow_kind") or "") for row in flow_rows).most_common()),
        },
        "outputs": {
            "summary": str(output_dir / "hot_update_blld_runtime_probe_report.json"),
            "markdown": str(output_dir / "hot_update_blld_runtime_probe_report.md"),
            "packets": str(output_dir / "hot_update_blld_net_packets.tsv"),
            "flows": str(output_dir / "hot_update_blld_net_flows.tsv"),
            "call_sites": str(output_dir / "hot_update_blld_netlogic_call_sites.tsv"),
            "anomalies": str(output_dir / "hot_update_blld_runtime_anomalies.tsv"),
        },
    }
    _write_blld_runtime_probe_markdown(
        output_dir / "hot_update_blld_runtime_probe_report.md",
        export_base=export_base,
        blld_dir=blld_dir,
        packet_rows=packet_rows,
        flow_rows=flow_rows,
        call_rows=call_rows,
        anomaly_rows=anomaly_rows,
    )
    (output_dir / "hot_update_blld_runtime_probe_report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


def _write_bluestarsea_runtime_probe_markdown(
    path: Path,
    *,
    export_base: Path,
    blue_dir: Path,
    packet_rows: list[dict[str, object]],
    flow_rows: list[dict[str, object]],
    call_rows: list[dict[str, object]],
    anomaly_rows: list[dict[str, object]],
) -> None:
    send_rows = [row for row in flow_rows if row["flow_kind"] == "send"]
    receive_rows = [row for row in flow_rows if row["flow_kind"] == "receive"]
    by_direction = Counter(str(row.get("direction") or "") for row in packet_rows)
    lines = [
        "# BlueStarSea 客户端运行与网络探针",
        "",
        f"- 导出目录：`{export_base}`",
        f"- BlueStarSea Lua：`{blue_dir}`",
        f"- 协议项：{len(packet_rows)}；发送入口：{len(send_rows)}；回包入口：{len(receive_rows)}；NetLogic 调用点：{len(call_rows)}；异常点：{len(anomaly_rows)}",
        f"- 协议方向：{', '.join(f'{name}:{count}' for name, count in by_direction.most_common())}",
        "- 说明：这是客户端静态结构报告，只整理 Lua 里可见的字段、回包落点和 UI 调用点；是否通过、扣除、奖励结算仍以服务端回包为准。",
        "",
        "## 协议字段",
        "",
        "| id | 协议 | 方向 | 字段 | NetLogic | 回调 |",
        "| ---: | --- | --- | --- | --- | --- |",
    ]
    for row in packet_rows:
        lines.append(
            "| "
            f"{row.get('id', '')} | "
            f"{_markdown_table_cell(row.get('name', ''), limit=120)} | "
            f"{_markdown_table_cell(row.get('direction', ''), limit=80)} | "
            f"{_markdown_table_cell(row.get('fields', ''), limit=280)} | "
            f"{_markdown_table_cell(row.get('net_function', ''), limit=120)} | "
            f"{_markdown_table_cell(row.get('registered_callback', ''), limit=120)} |"
        )

    lines.extend(
        [
            "",
            "## 客户端发送入口",
            "",
            "| 函数 | 协议 | 参数 | 发送字段 | 调用点 |",
            "| --- | --- | --- | --- | ---: |",
        ]
    )
    for row in send_rows:
        lines.append(
            "| "
            f"{_markdown_table_cell(row.get('function', ''), limit=140)} | "
            f"{_markdown_table_cell(row.get('packet', ''), limit=140)} | "
            f"{_markdown_table_cell(row.get('args', ''), limit=180)} | "
            f"{_markdown_table_cell(row.get('assigned_fields', ''), limit=280)} | "
            f"{row.get('call_site_count', '')} |"
        )

    lines.extend(
        [
            "",
            "## 服务端回包入口",
            "",
            "| 函数 | 协议 | msg字段 | Model落点 | 其他调用 | code判断 |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in receive_rows:
        lines.append(
            "| "
            f"{_markdown_table_cell(row.get('function', ''), limit=140)} | "
            f"{_markdown_table_cell(row.get('packet', ''), limit=140)} | "
            f"{_markdown_table_cell(row.get('msg_fields', ''), limit=240)} | "
            f"{_markdown_table_cell(row.get('model_calls', ''), limit=260)} | "
            f"{_markdown_table_cell(row.get('mgr_calls', ''), limit=260)} | "
            f"{_markdown_table_cell(row.get('code_guard', ''), limit=120)} |"
        )

    lines.extend(["", "## NetLogic 调用点", "", "| 文件 | 行 | 调用 | 参数 |", "| --- | ---: | --- | --- |"])
    for row in call_rows[:160]:
        lines.append(
            "| "
            f"{_markdown_table_cell(row.get('file', ''), limit=140)} | "
            f"{row.get('line', '')} | "
            f"{_markdown_table_cell(row.get('call', ''), limit=140)} | "
            f"{_markdown_table_cell(row.get('args', ''), limit=240)} |"
        )

    if anomaly_rows:
        lines.extend(["", "## 静态异常点", "", "| 类型 | 对象 | 证据 |", "| --- | --- | --- |"])
        for row in anomaly_rows:
            lines.append(
                "| "
                f"{_markdown_table_cell(row.get('type', ''), limit=120)} | "
                f"{_markdown_table_cell(row.get('subject', ''), limit=160)} | "
                f"{_markdown_table_cell(row.get('evidence', ''), limit=320)} |"
            )

    lines.extend(
        [
            "",
            "## 初步结论",
            "",
            "- 蓝色星海客户端发送的是操作意图：充能次数、分解物品列表、法器 id、树节点 id、方案 id/名称/优先级、星图领取 id 等。",
            "- 关键状态由服务端回包写回 Model：能量、充能次数、法器等级/星级/觉醒、悟道树激活、方案保存/套用/删除、星图奖励。",
            "- `CM_BlueStarSeaMerge/SM_BlueStarSeaMerge` 和 `SM_BlueStarSeaEnergyChange` 在 packet index 中存在，但当前 `BlueStarSeaNetLogic.lua` 没注册；静态上应先视为备用、旧版本或由其他脚本版本接入的协议。",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def build_fanxiu_bluestarsea_runtime_probe(
    *,
    export_root: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    export_base = resolve_fanxiu_export_root(export_root)
    output_dir = export_base / "apk_static_index"
    output_dir.mkdir(parents=True, exist_ok=True)
    blue_dir = _find_text_asset_dir(export_base, "lscripts", "gamesystem", "game", module="bluestarsea")
    if blue_dir is None:
        raise FanxiuResourceError("未找到已导出的 BlueStarSea 游戏逻辑 TextAsset，请先运行热更新 lscripts 报告。")
    net_logic_path = blue_dir / "BlueStarSeaNetLogic.lua"
    if not net_logic_path.is_file():
        raise FanxiuResourceError(f"未找到 BlueStarSeaNetLogic.lua：{net_logic_path}")

    packet_index_rows, fields_by_packet = _read_packet_index_by_module(
        export_base,
        module_name="player.bluestarsea",
        name_keyword="BlueStarSea",
    )
    registered_rows, flow_rows, _local_packet_names = _parse_net_flows(
        net_logic_path,
        packet_module="player.bluestarsea",
        manager_name="BlueStarSeaMgr",
        init_names={"_init_", "LuaBlueStarSeaNetLogic", "Destroy"},
    )
    registered_by_packet = {str(row["packet"]): row for row in registered_rows}
    flow_by_packet = {str(row["packet"]): row for row in flow_rows if row.get("packet")}
    call_rows = _find_lua_lines(blue_dir, _netlogic_call_re("BlueStarSeaMgr"))
    call_count_by_name = Counter(str(row["call"]) for row in call_rows)
    for row in flow_rows:
        row["call_site_count"] = call_count_by_name.get(str(row["function"]), 0)

    packet_names = set(fields_by_packet) | {str(row.get("name") or "") for row in packet_index_rows} | set(registered_by_packet) | {
        str(row.get("packet") or "") for row in flow_rows if row.get("packet")
    }
    packet_rows: list[dict[str, object]] = []
    packet_index_by_name = {str(row.get("name") or ""): row for row in packet_index_rows}
    for packet_name in sorted(name for name in packet_names if name):
        index_row = packet_index_by_name.get(packet_name, {})
        fields = fields_by_packet.get(packet_name, [])
        flow = flow_by_packet.get(packet_name, {})
        registration = registered_by_packet.get(packet_name, {})
        packet_rows.append(
            {
                "id": index_row.get("id", ""),
                "name": packet_name,
                "direction": index_row.get("direction") or registration.get("direction") or ("client_to_server" if packet_name.startswith("CM_") else "server_to_client" if packet_name.startswith("SM_") else "value_object"),
                "field_count": index_row.get("field_count", len(fields)),
                "fields": _fields_summary(fields),
                "registered_callback": registration.get("callback", ""),
                "net_function": flow.get("function", ""),
                "client_send_fields": flow.get("assigned_fields", ""),
                "msg_read_fields": flow.get("msg_fields", ""),
                "source_file": index_row.get("file", ""),
                "package": index_row.get("package", ""),
                "path": index_row.get("relative_path", ""),
            }
        )

    anomaly_rows: list[dict[str, object]] = []
    registered_packets = set(registered_by_packet)
    indexed_packet_names = {str(row.get("name") or "") for row in packet_index_rows}
    for packet_name in sorted(indexed_packet_names - registered_packets):
        if packet_name.startswith(("CM_", "SM_")):
            anomaly_rows.append(
                {
                    "type": "packet_not_registered_in_netlogic",
                    "subject": packet_name,
                    "evidence": "packet index 中存在，但 BlueStarSeaNetLogic.lua 未注册",
                    "path": str(net_logic_path),
                    "line": "",
                }
            )
    known_net_funcs = {str(row.get("function") or "") for row in flow_rows} | {"Destroy"}
    for call in call_rows:
        if str(call.get("call") or "") not in known_net_funcs:
            anomaly_rows.append(
                {
                    "type": "netlogic_call_without_function",
                    "subject": call.get("call", ""),
                    "evidence": f"{call.get('file')}:{call.get('line')} {call.get('text')}",
                    "path": call.get("path", ""),
                    "line": call.get("line", ""),
                }
            )
    for row in flow_rows:
        if row.get("flow_kind") != "send" or not row.get("packet"):
            continue
        field_names = {str(field.get("field_name") or "") for field in fields_by_packet.get(str(row["packet"]), [])}
        used_names = {
            item.split("=", 1)[0].split(":", 1)[0]
            for item in str(row.get("assigned_fields") or "").split(" | ")
            if item
        }
        missing = sorted(field_names - used_names)
        if missing:
            anomaly_rows.append(
                {
                    "type": "client_send_field_not_filled_in_function",
                    "subject": row["packet"],
                    "evidence": f"{row['function']} 未直接填充字段：{', '.join(missing)}",
                    "path": row["path"],
                    "line": row["start_line"],
                }
            )

    packet_count = _write_tsv(
        output_dir / "hot_update_bluestarsea_net_packets.tsv",
        [
            "id",
            "name",
            "direction",
            "field_count",
            "fields",
            "registered_callback",
            "net_function",
            "client_send_fields",
            "msg_read_fields",
            "source_file",
            "package",
            "path",
        ],
        packet_rows,
    )
    flow_count = _write_tsv(
        output_dir / "hot_update_bluestarsea_net_flows.tsv",
        [
            "flow_kind",
            "function",
            "packet",
            "args",
            "assigned_fields",
            "vo_fields",
            "msg_fields",
            "model_calls",
            "mgr_calls",
            "code_guard",
            "send_message",
            "call_site_count",
            "start_line",
            "end_line",
            "path",
        ],
        flow_rows,
    )
    call_count = _write_tsv(
        output_dir / "hot_update_bluestarsea_netlogic_call_sites.tsv",
        ["file", "line", "call", "args", "text", "path"],
        call_rows,
    )
    anomaly_count = _write_tsv(
        output_dir / "hot_update_bluestarsea_runtime_anomalies.tsv",
        ["type", "subject", "evidence", "path", "line"],
        anomaly_rows,
    )

    result = {
        "export_root": str(export_base),
        "output_dir": str(output_dir),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "inputs": {
            "blue_dir": str(blue_dir),
            "net_logic_path": str(net_logic_path),
            "packet_index_dir": str(export_base / "parsed_configs" / "lua_packet_index"),
        },
        "counts": {
            "packets": packet_count,
            "flows": flow_count,
            "call_sites": call_count,
            "anomalies": anomaly_count,
            "by_direction": dict(Counter(str(row.get("direction") or "") for row in packet_rows).most_common()),
            "by_flow_kind": dict(Counter(str(row.get("flow_kind") or "") for row in flow_rows).most_common()),
        },
        "outputs": {
            "summary": str(output_dir / "hot_update_bluestarsea_runtime_probe_report.json"),
            "markdown": str(output_dir / "hot_update_bluestarsea_runtime_probe_report.md"),
            "packets": str(output_dir / "hot_update_bluestarsea_net_packets.tsv"),
            "flows": str(output_dir / "hot_update_bluestarsea_net_flows.tsv"),
            "call_sites": str(output_dir / "hot_update_bluestarsea_netlogic_call_sites.tsv"),
            "anomalies": str(output_dir / "hot_update_bluestarsea_runtime_anomalies.tsv"),
        },
    }
    _write_bluestarsea_runtime_probe_markdown(
        output_dir / "hot_update_bluestarsea_runtime_probe_report.md",
        export_base=export_base,
        blue_dir=blue_dir,
        packet_rows=packet_rows,
        flow_rows=flow_rows,
        call_rows=call_rows,
        anomaly_rows=anomaly_rows,
    )
    (output_dir / "hot_update_bluestarsea_runtime_probe_report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


def _collect_bluestarsea_model_events(model_path: Path) -> list[dict[str, object]]:
    if not model_path.is_file():
        return []
    text = model_path.read_text(encoding="utf-8", errors="ignore")
    rows: list[dict[str, object]] = []
    for block in _lua_method_blocks(text):
        function_name = str(block["name"])
        body = str(block["body"])
        data_calls = [f"{name}({args.strip()})" for name, args in re.findall(r"""self\.BlueStarSeaData:([A-Za-z_]\w*)\(([^)]*)\)""", body)]
        events = [name for name in re.findall(r"""RaiseEvent\(BlueStarSeaType\.EventType\.([A-Za-z_]\w*)""", body)]
        red_dots = re.findall(r"""RaiseRedDotEvent\(RedDotID\.([A-Za-z_]\w*)""", body)
        reward_calls = re.findall(r"""([A-Za-z_]\w*Mgr)\.Inst_get\(\):AddRewardResults\(([^)]*)\)""", body)
        if not function_name.startswith("On") and not events and not red_dots and not reward_calls:
            continue
        rows.append(
            {
                "function": function_name,
                "data_calls": _unique_join(data_calls, limit=12),
                "events": _unique_join(events, limit=8),
                "red_dots": _unique_join(red_dots, limit=8),
                "reward_calls": _unique_join((f"{mgr}:AddRewardResults({args.strip()})" for mgr, args in reward_calls), limit=4),
                "msg_fields": _unique_join(_MSG_FIELD_RE.findall(body), limit=20),
                "start_line": block["start_line"],
                "end_line": block["end_line"],
                "path": str(model_path),
            }
        )
    return rows


_BLUE_STATE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("sync_snapshot", re.compile(r"""self\._SyncInfo\s*=\s*msg""")),
    ("sync_field_write", re.compile(r"""self\._SyncInfo\.vo\.([A-Za-z_]\w*)\s*=\s*(.+)""")),
    ("faqi_state_update", re.compile(r"""faqi\.([A-Za-z_]\w*)\s*=\s*msg\.faqi\.([A-Za-z_]\w*)""")),
    ("talent_tree_list_add", re.compile(r"""self\._SyncInfo\.vo\.activatedTreeIds:Add\(msg\.treeId\)""")),
    ("talent_tree_cache_add", re.compile(r"""self\._ActivatedTalentTreeIds\[msg\.treeId\]\s*=\s*true""")),
    ("star_tree_cache_add", re.compile(r"""self\._ActivatedStarTreeIdList:Add\(msg\.starTreeId\)""")),
    ("purify_rewards", re.compile(r"""self\._PurifyRewardResults\s*=\s*msg\.rewardResults""")),
    ("plan_field_update", re.compile(r"""plan\.([A-Za-z_]\w*)\s*=\s*msg\.plan\.([A-Za-z_]\w*)""")),
    ("plan_add", re.compile(r"""planList:Add\(msg\.plan\)""")),
    ("plan_delete", re.compile(r"""planList:RemoveAt\(i\)""")),
    ("plan_apply", re.compile(r"""plan\.applied\s*=\s*\(plan\.planId==msg\.appliedPlanId\)""")),
    ("config_read", re.compile(r"""GetConfigTable\(ConfigName\.([A-Za-z_]\w*)\)""")),
    ("cache_write", re.compile(r"""self\.V_([A-Za-z_]\w*)\s*=""")),
    ("purify_auto_select", re.compile(r"""(GetCurrentEnergyValue|GetAppliedPlan|BlueStarSeaPurifyItemVO\.new|remaining=remaining-count\*costPer)""")),
)


def _collect_bluestarsea_data_state_rows(data_path: Path) -> list[dict[str, object]]:
    if not data_path.is_file():
        return []
    text = data_path.read_text(encoding="utf-8", errors="ignore")
    rows: list[dict[str, object]] = []
    for block in _lua_method_blocks(text):
        body_lines = str(block["body"]).splitlines()
        for offset, line in enumerate(body_lines):
            stripped = line.strip()
            if not stripped:
                continue
            for category, pattern in _BLUE_STATE_PATTERNS:
                match = pattern.search(stripped)
                if not match:
                    continue
                target = ""
                source = ""
                if category == "sync_field_write":
                    target, source = match.group(1), match.group(2).strip()
                elif category in {"faqi_state_update", "plan_field_update"}:
                    target, source = match.group(1), match.group(2)
                elif category in {"config_read", "cache_write", "purify_auto_select"}:
                    target = match.group(1)
                rows.append(
                    {
                        "function": block["name"],
                        "category": category,
                        "target": target,
                        "source": source,
                        "line": int(block["start_line"]) + offset,
                        "text": stripped,
                        "path": str(data_path),
                    }
                )
                break
    return rows


def _collect_bluestarsea_model_getters(model_path: Path, ui_rows: list[dict[str, object]]) -> list[dict[str, object]]:
    if not model_path.is_file():
        return []
    text = model_path.read_text(encoding="utf-8", errors="ignore")
    call_count = Counter(str(row.get("signal") or "") for row in ui_rows if row.get("kind") == "model_call")
    rows: list[dict[str, object]] = []
    for block in _lua_method_blocks(text):
        body = str(block["body"]).strip()
        match = re.search(r"""return\s+self\.BlueStarSeaData:([A-Za-z_]\w*)\(([^)]*)\)""", body)
        if not match:
            continue
        model_function = str(block["name"])
        data_function = match.group(1)
        category = "getter"
        if model_function.startswith("Check"):
            category = "check"
        elif model_function.startswith("Build") or model_function.startswith("Calc") or model_function.startswith("Unwrap"):
            category = "compute"
        rows.append(
            {
                "model_function": model_function,
                "data_function": data_function,
                "args": match.group(2).strip(),
                "category": category,
                "ui_call_sites": call_count.get(model_function, 0),
                "line": block["start_line"],
                "path": str(model_path),
            }
        )
    return rows


def _collect_bluestarsea_ui_state_rows(blue_dir: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    if not blue_dir.is_dir():
        return rows
    event_re = re.compile(r"""BinderEvent\(BlueStarSeaMgr\.Inst_get\(\)\.Model,BlueStarSeaType\.EventType\.([A-Za-z_]\w*)\s*,([^)]+)\)""")
    model_call_re = re.compile(r"""BlueStarSeaMgr\.Inst_get\(\)\.Model:([A-Za-z_]\w*)\(([^)]*)\)""")
    sync_vo_re = re.compile(r"""syncInfo\.vo\.([A-Za-z_]\w*)""")
    ritual_vo_re = re.compile(r"""V_RitualImplementVO\.([A-Za-z_]\w*)""")
    skip_files = {"BlueStarSeaData.lua", "BlueStarSeaModel.lua", "BlueStarSeaNetLogic.lua", "BlueStarSeaType.lua"}
    for path in sorted(blue_dir.glob("*.lua"), key=lambda item: item.name.lower()):
        if path.name in skip_files:
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        for line_no, line in enumerate(lines, start=1):
            stripped = line.strip()
            for match in event_re.finditer(stripped):
                rows.append(
                    {
                        "kind": "event_binding",
                        "file": path.name,
                        "line": line_no,
                        "signal": match.group(1),
                        "args": match.group(2).strip(),
                        "text": stripped,
                        "path": str(path),
                    }
                )
            for match in model_call_re.finditer(stripped):
                rows.append(
                    {
                        "kind": "model_call",
                        "file": path.name,
                        "line": line_no,
                        "signal": match.group(1),
                        "args": match.group(2).strip(),
                        "text": stripped,
                        "path": str(path),
                    }
                )
            for match in sync_vo_re.finditer(stripped):
                rows.append(
                    {
                        "kind": "sync_vo_field",
                        "file": path.name,
                        "line": line_no,
                        "signal": match.group(1),
                        "args": "",
                        "text": stripped,
                        "path": str(path),
                    }
                )
            for match in ritual_vo_re.finditer(stripped):
                rows.append(
                    {
                        "kind": "ritual_vo_field",
                        "file": path.name,
                        "line": line_no,
                        "signal": match.group(1),
                        "args": "",
                        "text": stripped,
                        "path": str(path),
                    }
                )
    return rows


def _write_bluestarsea_model_state_markdown(
    path: Path,
    *,
    export_base: Path,
    blue_dir: Path,
    event_rows: list[dict[str, object]],
    state_rows: list[dict[str, object]],
    getter_rows: list[dict[str, object]],
    ui_rows: list[dict[str, object]],
) -> None:
    by_state = Counter(str(row.get("category") or "") for row in state_rows)
    by_ui = Counter(str(row.get("kind") or "") for row in ui_rows)
    lines = [
        "# BlueStarSea Model/Data 状态探针",
        "",
        f"- 导出目录：`{export_base}`",
        f"- BlueStarSea Lua：`{blue_dir}`",
        f"- 回包事件：{len(event_rows)}；Data 状态证据：{len(state_rows)}；Model getter：{len(getter_rows)}；UI 读取/绑定：{len(ui_rows)}",
        f"- 状态证据分组：{', '.join(f'{name}:{count}' for name, count in by_state.most_common())}",
        f"- UI 证据分组：{', '.join(f'{name}:{count}' for name, count in by_ui.most_common())}",
        "",
        "## 回包到事件",
        "",
        "| Model函数 | Data调用 | 事件 | 红点 | 奖励弹窗 | msg字段 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in event_rows:
        lines.append(
            "| "
            f"{_markdown_table_cell(row.get('function', ''), limit=140)} | "
            f"{_markdown_table_cell(row.get('data_calls', ''), limit=260)} | "
            f"{_markdown_table_cell(row.get('events', ''), limit=160)} | "
            f"{_markdown_table_cell(row.get('red_dots', ''), limit=180)} | "
            f"{_markdown_table_cell(row.get('reward_calls', ''), limit=220)} | "
            f"{_markdown_table_cell(row.get('msg_fields', ''), limit=180)} |"
        )

    lines.extend(["", "## Data 状态写入", "", "| 函数 | 分组 | 目标 | 来源 | 行 | 代码 |", "| --- | --- | --- | --- | ---: | --- |"])
    for row in state_rows[:180]:
        lines.append(
            "| "
            f"{_markdown_table_cell(row.get('function', ''), limit=120)} | "
            f"{_markdown_table_cell(row.get('category', ''), limit=120)} | "
            f"{_markdown_table_cell(row.get('target', ''), limit=120)} | "
            f"{_markdown_table_cell(row.get('source', ''), limit=160)} | "
            f"{row.get('line', '')} | "
            f"{_markdown_table_cell(row.get('text', ''), limit=300)} |"
        )

    lines.extend(["", "## Model Getter", "", "| Model函数 | Data函数 | 分组 | UI调用点 |", "| --- | --- | --- | ---: |"])
    for row in getter_rows:
        lines.append(
            "| "
            f"{_markdown_table_cell(row.get('model_function', ''), limit=140)} | "
            f"{_markdown_table_cell(row.get('data_function', ''), limit=140)} | "
            f"{_markdown_table_cell(row.get('category', ''), limit=80)} | "
            f"{row.get('ui_call_sites', '')} |"
        )

    lines.extend(["", "## UI 读取与事件绑定样例", "", "| 类型 | 文件 | 行 | 信号 | 参数/字段 |", "| --- | --- | ---: | --- | --- |"])
    for row in ui_rows[:180]:
        lines.append(
            "| "
            f"{_markdown_table_cell(row.get('kind', ''), limit=120)} | "
            f"{_markdown_table_cell(row.get('file', ''), limit=140)} | "
            f"{row.get('line', '')} | "
            f"{_markdown_table_cell(row.get('signal', ''), limit=160)} | "
            f"{_markdown_table_cell(row.get('args', ''), limit=220)} |"
        )

    lines.extend(
        [
            "",
            "## 初步结论",
            "",
            "- `BlueStarSeaData._SyncInfo` 是蓝色星海本地状态快照，完整内容来自 `SM_BlueStarSeaSync.vo`。",
            "- `OnCharge` 只回写 `energy/todayChargingTimes/lastRecoverTime`；`OnPurify` 回写 `energy` 并保存 `rewardResults` 供奖励展示。",
            "- `OnLevelUp/OnStarUp/OnWakeUp` 都按 `faqiId` 找到本地 `faqiList` 中的法器，再替换 `level/star/wake` 三个字段。",
            "- 悟道树、星图领取和方案保存/套用/删除都是在服务端成功回包后更新本地缓存；客户端 UI 读取这些缓存来刷新红点、列表和详情。",
            "- `BuildPurifyItems` 会按当前能量和已套用方案的 `itemPriority` 生成分解列表，但能量扣除和奖励结果仍以后续 `SM_BlueStarSeaPurify` 为准。",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def build_fanxiu_bluestarsea_model_state_probe(
    *,
    export_root: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    export_base = resolve_fanxiu_export_root(export_root)
    output_dir = export_base / "apk_static_index"
    output_dir.mkdir(parents=True, exist_ok=True)
    blue_dir = _find_text_asset_dir(export_base, "lscripts", "gamesystem", "game", module="bluestarsea")
    if blue_dir is None:
        raise FanxiuResourceError("未找到已导出的 BlueStarSea 游戏逻辑 TextAsset，请先运行热更新 lscripts 报告。")
    model_path = blue_dir / "BlueStarSeaModel.lua"
    data_path = blue_dir / "BlueStarSeaData.lua"
    if not model_path.is_file():
        raise FanxiuResourceError(f"未找到 BlueStarSeaModel.lua：{model_path}")
    if not data_path.is_file():
        raise FanxiuResourceError(f"未找到 BlueStarSeaData.lua：{data_path}")

    ui_rows = _collect_bluestarsea_ui_state_rows(blue_dir)
    event_rows = _collect_bluestarsea_model_events(model_path)
    state_rows = _collect_bluestarsea_data_state_rows(data_path)
    getter_rows = _collect_bluestarsea_model_getters(model_path, ui_rows)

    event_count = _write_tsv(
        output_dir / "hot_update_bluestarsea_model_events.tsv",
        ["function", "data_calls", "events", "red_dots", "reward_calls", "msg_fields", "start_line", "end_line", "path"],
        event_rows,
    )
    state_count = _write_tsv(
        output_dir / "hot_update_bluestarsea_data_state_updates.tsv",
        ["function", "category", "target", "source", "line", "text", "path"],
        state_rows,
    )
    getter_count = _write_tsv(
        output_dir / "hot_update_bluestarsea_model_getters.tsv",
        ["model_function", "data_function", "args", "category", "ui_call_sites", "line", "path"],
        getter_rows,
    )
    ui_count = _write_tsv(
        output_dir / "hot_update_bluestarsea_ui_state_bindings.tsv",
        ["kind", "file", "line", "signal", "args", "text", "path"],
        ui_rows,
    )
    result = {
        "export_root": str(export_base),
        "output_dir": str(output_dir),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "inputs": {
            "blue_dir": str(blue_dir),
            "model_path": str(model_path),
            "data_path": str(data_path),
        },
        "counts": {
            "events": event_count,
            "state_updates": state_count,
            "getters": getter_count,
            "ui_bindings": ui_count,
            "by_state_category": dict(Counter(str(row.get("category") or "") for row in state_rows).most_common()),
            "by_ui_kind": dict(Counter(str(row.get("kind") or "") for row in ui_rows).most_common()),
        },
        "outputs": {
            "summary": str(output_dir / "hot_update_bluestarsea_model_state_report.json"),
            "markdown": str(output_dir / "hot_update_bluestarsea_model_state_report.md"),
            "events": str(output_dir / "hot_update_bluestarsea_model_events.tsv"),
            "state_updates": str(output_dir / "hot_update_bluestarsea_data_state_updates.tsv"),
            "getters": str(output_dir / "hot_update_bluestarsea_model_getters.tsv"),
            "ui_bindings": str(output_dir / "hot_update_bluestarsea_ui_state_bindings.tsv"),
        },
    }
    _write_bluestarsea_model_state_markdown(
        output_dir / "hot_update_bluestarsea_model_state_report.md",
        export_base=export_base,
        blue_dir=blue_dir,
        event_rows=event_rows,
        state_rows=state_rows,
        getter_rows=getter_rows,
        ui_rows=ui_rows,
    )
    (output_dir / "hot_update_bluestarsea_model_state_report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


def _write_bluestarsea_support_config_markdown(
    path: Path,
    *,
    export_base: Path,
    blue_dir: Path,
    table_rows: list[dict[str, object]],
    config_rows: list[dict[str, object]],
    function_rows: list[dict[str, object]],
    filter_rows: list[dict[str, object]],
    skill_rows: list[dict[str, object]],
    store_rows: list[dict[str, object]],
) -> None:
    missing = [str(row["table"]) for row in table_rows if row.get("status") == "missing"]
    lines = [
        "# BlueStarSea 支撑配置探针",
        "",
        f"- 导出目录：`{export_base}`",
        f"- BlueStarSea 配置：`{blue_dir}`",
        f"- ConfigValue：{len(config_rows)}；Function：{len(function_rows)}；Filter：{len(filter_rows)}；Skill：{len(skill_rows)}；Store：{len(store_rows)}",
        f"- 缺失表：{', '.join(missing) if missing else '无'}",
        "",
        "## ConfigValue",
        "",
        "| id | value |",
        "| --- | --- |",
    ]
    for row in config_rows:
        lines.append(
            "| "
            f"{_markdown_table_cell(row.get('id', ''), limit=120)} | "
            f"{_markdown_table_cell(row.get('value', ''), limit=260)} |"
        )

    lines.extend(["", "## Function", "", "| id | 名称 | 图标路径 | 图标 | 界面 |", "| ---: | --- | --- | --- | ---: |"])
    for row in function_rows:
        lines.append(
            "| "
            f"{row.get('id', '')} | "
            f"{_markdown_table_cell(row.get('func_name', ''), limit=120)} | "
            f"{_markdown_table_cell(row.get('icon_patch', ''), limit=140)} | "
            f"{_markdown_table_cell(row.get('icon', ''), limit=120)} | "
            f"{row.get('interface', '')} |"
        )

    lines.extend(["", "## Filter", "", "| id | filterid | 一级 | 二级 |", "| ---: | ---: | --- | --- |"])
    for row in filter_rows:
        lines.append(
            "| "
            f"{row.get('id', '')} | "
            f"{row.get('filterid', '')} | "
            f"{_markdown_table_cell(row.get('first_filter', ''), limit=120)} | "
            f"{_markdown_table_cell(row.get('second_filter', ''), limit=120)} |"
        )

    lines.extend(["", "## Skill", "", "| id | 名称 | 前置 | 描述 | 关联技能 |", "| ---: | --- | --- | --- | ---: |"])
    for row in skill_rows:
        lines.append(
            "| "
            f"{row.get('id', '')} | "
            f"{_markdown_table_cell(row.get('name', ''), limit=120)} | "
            f"{_markdown_table_cell(row.get('pre', ''), limit=180)} | "
            f"{_markdown_table_cell(row.get('des', ''), limit=260)} | "
            f"{row.get('skill', '')} |"
        )

    if store_rows:
        lines.extend(["", "## Store", "", "| id | group | item | cost | 条件 |", "| ---: | --- | --- | --- | --- |"])
        for row in store_rows:
            lines.append(
                "| "
                f"{row.get('id', '')} | "
                f"{row.get('group', '')} | "
                f"{_markdown_table_cell(row.get('item', ''), limit=120)} | "
                f"{_markdown_table_cell(row.get('cost', ''), limit=120)} | "
                f"{_markdown_table_cell(row.get('condition', ''), limit=180)} |"
            )

    lines.extend(["", "## 表状态", "", "| 表 | 状态 | 行数 | 字段 |", "| --- | --- | ---: | --- |"])
    for row in table_rows:
        lines.append(
            "| "
            f"{_markdown_table_cell(row.get('table', ''), limit=100)} | "
            f"{_markdown_table_cell(row.get('status', ''), limit=80)} | "
            f"{row.get('row_count', '')} | "
            f"{_markdown_table_cell(row.get('fields', ''), limit=220)} |"
        )

    lines.extend(
        [
            "",
            "## 初步结论",
            "",
            "- `ConfigValue` 给出当前蓝色星海的本地常量：能量上限、恢复间隔、方案数量、规则 id、奖励 faze id、二阶段开启条件和初始能量。",
            "- 当前导出中 `Function` 是空表，`Store.lua` 不存在；客户端代码仍保留读取入口，说明这些可能是预留功能或被其他版本资源裁剪。",
            "- `Filter` 和 `Skill` 是图鉴/筛选/预览层配置，可继续接前端做“淬灵域筛选”和技能说明页。",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def build_fanxiu_bluestarsea_support_config_probe(
    *,
    export_root: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    export_base = resolve_fanxiu_export_root(export_root)
    output_dir = export_base / "apk_static_index"
    output_dir.mkdir(parents=True, exist_ok=True)
    lang_path = _find_lang_path(export_base)
    blue_dir = _find_cfg_text_asset_dir(export_base, "bluestarsea")
    if blue_dir is None:
        raise FanxiuResourceError("未找到已导出的 BlueStarSea 配置 TextAsset，请先运行热更新 lscripts 报告。")

    table_names = ["ConfigValue", "Function", "Filter", "Skill", "Store"]
    parsed = {name: _parse_config(blue_dir / f"{name}.lua", lang_path) for name in table_names}
    table_rows = [
        {
            "table": name,
            "status": "ok" if (blue_dir / f"{name}.lua").is_file() else "missing",
            "row_count": parsed[name]["row_count"],
            "fields": _unique_join(parsed[name]["fields"], limit=40),
            "path": parsed[name]["source_path"],
        }
        for name in table_names
    ]
    config_rows = [{"id": row.get("id", ""), "value": row.get("value", "")} for row in parsed["ConfigValue"]["rows"]]
    function_rows = [
        {
            "id": row.get("id", ""),
            "func_name": _plain(row, "funcName"),
            "icon_patch": row.get("iconPatch", ""),
            "icon": row.get("icon", ""),
            "interface": row.get("interface", ""),
        }
        for row in parsed["Function"]["rows"]
    ]
    filter_rows = [
        {
            "id": row.get("id", ""),
            "filterid": row.get("filterid", ""),
            "first_filter": _plain(row, "firstFilter"),
            "second_filter": _plain(row, "secondFilter"),
        }
        for row in parsed["Filter"]["rows"]
    ]
    skill_rows = [
        {
            "id": row.get("id", ""),
            "name": _plain(row, "name"),
            "pre": _plain(row, "pre"),
            "des": _plain(row, "des"),
            "skill": row.get("skill", ""),
        }
        for row in parsed["Skill"]["rows"]
    ]
    store_rows = [
        {
            "id": row.get("id", ""),
            "group": row.get("group", ""),
            "item": row.get("item", ""),
            "cost": row.get("cost", ""),
            "condition": row.get("condition", ""),
        }
        for row in parsed["Store"]["rows"]
    ]

    table_count = _write_tsv(output_dir / "hot_update_bluestarsea_support_config_tables.tsv", ["table", "status", "row_count", "fields", "path"], table_rows)
    config_count = _write_tsv(output_dir / "hot_update_bluestarsea_config_values.tsv", ["id", "value"], config_rows)
    function_count = _write_tsv(output_dir / "hot_update_bluestarsea_function_buttons.tsv", ["id", "func_name", "icon_patch", "icon", "interface"], function_rows)
    filter_count = _write_tsv(output_dir / "hot_update_bluestarsea_filters.tsv", ["id", "filterid", "first_filter", "second_filter"], filter_rows)
    skill_count = _write_tsv(output_dir / "hot_update_bluestarsea_skills.tsv", ["id", "name", "pre", "des", "skill"], skill_rows)
    store_count = _write_tsv(output_dir / "hot_update_bluestarsea_stores.tsv", ["id", "group", "item", "cost", "condition"], store_rows)
    result = {
        "export_root": str(export_base),
        "output_dir": str(output_dir),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "inputs": {"blue_dir": str(blue_dir), "lang_path": str(lang_path or "")},
        "counts": {
            "tables": table_count,
            "config_values": config_count,
            "functions": function_count,
            "filters": filter_count,
            "skills": skill_count,
            "stores": store_count,
            "missing_tables": [str(row["table"]) for row in table_rows if row["status"] == "missing"],
        },
        "outputs": {
            "summary": str(output_dir / "hot_update_bluestarsea_support_config_report.json"),
            "markdown": str(output_dir / "hot_update_bluestarsea_support_config_report.md"),
            "tables": str(output_dir / "hot_update_bluestarsea_support_config_tables.tsv"),
            "config_values": str(output_dir / "hot_update_bluestarsea_config_values.tsv"),
            "functions": str(output_dir / "hot_update_bluestarsea_function_buttons.tsv"),
            "filters": str(output_dir / "hot_update_bluestarsea_filters.tsv"),
            "skills": str(output_dir / "hot_update_bluestarsea_skills.tsv"),
            "stores": str(output_dir / "hot_update_bluestarsea_stores.tsv"),
        },
    }
    _write_bluestarsea_support_config_markdown(
        output_dir / "hot_update_bluestarsea_support_config_report.md",
        export_base=export_base,
        blue_dir=blue_dir,
        table_rows=table_rows,
        config_rows=config_rows,
        function_rows=function_rows,
        filter_rows=filter_rows,
        skill_rows=skill_rows,
        store_rows=store_rows,
    )
    (output_dir / "hot_update_bluestarsea_support_config_report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


def _collect_bluestarsea_open_entries(base_rows: list[dict[str, Any]]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for row in sorted(base_rows, key=lambda item: (int(item.get("sort") or 0), int(item.get("id") or 0))):
        rows.append(
            {
                "id": row.get("id", ""),
                "sort": row.get("sort", ""),
                "name": _plain(row, "name"),
                "showcondition": row.get("showcondition", ""),
                "opencondition": row.get("opencondition", ""),
                "openlan": _plain(row, "openlan"),
                "interface": row.get("interface", ""),
                "pre": row.get("pre", ""),
                "lockpre": row.get("lockpre", ""),
            }
        )
    return rows


def _categorize_bluestarsea_red_dot_rule(name: str, body: str) -> str:
    if name == "OnBackpackItemsChanged":
        return "backpack_delta"
    if name == "RaiseAllRedDotEvents":
        return "dispatcher"
    if "UpLevel" in name or "LevelCfg" in body:
        return "upgrade_level"
    if "UpStage" in name or "StarCfg" in body:
        return "upgrade_stage"
    if "Wake" in name:
        return "wake"
    if "Display" in name or "StarTree" in body:
        return "display_claim"
    if "TreeActive" in name or "TreeCfg" in body:
        return "tree_active"
    return "red_dot"


def _collect_bluestarsea_config_reads(body: str) -> list[str]:
    reads = re.findall(r"ConfigValue\.([A-Z0-9_]+)", body)
    reads.extend(re.findall(r'GetConfigTableById\(ConfigName\.BlueStarSea_ConfigValue,\s*"([A-Z0-9_]+)"\s*\)', body))
    for dyn in re.findall(r'GetConfigTableById\(ConfigName\.BlueStarSea_ConfigValue,\s*"OPENCONDITION"\s*\.\.\s*tostring\(([^)]+)\)', body):
        reads.append(f"OPENCONDITION{{{dyn.strip()}}}")
    for prefix in re.findall(r'GetConfigTableById\(ConfigName\.BlueStarSea_ConfigValue,\s*"([A-Z0-9_]+)"\s*\.\.', body):
        if prefix != "OPENCONDITION":
            reads.append(f"{prefix}{{dynamic}}")
    return reads


def _collect_bluestarsea_red_dot_rules(model_path: Path) -> list[dict[str, object]]:
    if not model_path.is_file():
        return []
    text = model_path.read_text(encoding="utf-8", errors="ignore")
    rows: list[dict[str, object]] = []
    for block in _lua_method_blocks(text):
        name = str(block["name"])
        if "RedDot" not in name and name not in {"OnBackpackItemsChanged", "RaiseAllRedDotEvents"}:
            continue
        body = str(block["body"])
        config_reads = _collect_bluestarsea_config_reads(body)
        condition_calls = re.findall(r"GameUtil\.CheckCondition\(([^)\n]+)\)", body)
        condition_gates = [item for item in condition_calls if "condition and condition.value" not in item and "conditionCfg and conditionCfg.value" not in item]
        gates = [item for item in config_reads if item.startswith("OPENCONDITION")] + condition_gates
        item_checks: list[str] = []
        for signal in ("GetBackpackNumByItem", "GetItemIcon", "GetItemByCfgConsume", "GetItemsCount"):
            if signal in body:
                item_checks.append(signal)
        cache_writes: list[str] = []
        for signal in ("_displayRedDotCache", "_treeActiveRedDotCache"):
            if signal in body:
                cache_writes.append(signal)
        raises = re.findall(r"RaiseRedDotEvent\(RedDotID\.([A-Za-z0-9_]+)\)", body)
        calls = re.findall(r"(?:self|BlueStarSeaData|BlueStarSeaMgr\.Inst_get\(\)\.Model)[:.]([A-Za-z_]\w*)\(", body)
        returns = re.findall(r"\breturn\s+([A-Za-z_][A-Za-z0-9_]*|true|false|nil)", body)
        rows.append(
            {
                "function": name,
                "category": _categorize_bluestarsea_red_dot_rule(name, body),
                "open_gate": _unique_join(gates, limit=12),
                "config_reads": _unique_join(config_reads, limit=20),
                "item_checks": _unique_join(item_checks, limit=12),
                "cache_writes": _unique_join(cache_writes, limit=12),
                "raises": _unique_join(raises, limit=20),
                "calls": _unique_join(calls, limit=30),
                "returns": _unique_join(returns, limit=20),
                "line_start": block["start_line"],
                "line_end": block["end_line"],
                "path": str(model_path),
            }
        )
    return rows


def _collect_bluestarsea_red_dot_bindings(blue_dir: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    if not blue_dir.is_dir():
        return rows
    for path in sorted(blue_dir.glob("*.lua"), key=lambda item: item.name.lower()):
        if "__" in path.stem:
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        for line_no, line in enumerate(lines, start=1):
            compact = line.strip()
            if not compact:
                continue
            bind_match = re.search(r"BindRedDot\(RedDotID\.([A-Za-z0-9_]+)", compact)
            if bind_match:
                lookahead = "\n".join(lines[line_no - 1 : line_no + 6])
                placeholder = "return false" in lookahead
                rows.append(
                    {
                        "kind": "bind_red_dot_placeholder" if placeholder else "bind_red_dot",
                        "file": path.name,
                        "line": line_no,
                        "signal": bind_match.group(1),
                        "detail": "callback_returns_false" if placeholder else "",
                        "text": compact,
                        "path": str(path),
                    }
                )
            for match in re.finditer(r"RaiseRedDotEvent\(RedDotID\.([A-Za-z0-9_]+)\)", compact):
                rows.append(
                    {
                        "kind": "raise_red_dot",
                        "file": path.name,
                        "line": line_no,
                        "signal": match.group(1),
                        "detail": "",
                        "text": compact,
                        "path": str(path),
                    }
                )
            update_match = re.search(r"UpdateShow\(([^)]*)\)", compact)
            if update_match and "RedDot" in compact:
                rows.append(
                    {
                        "kind": "red_dot_component_update",
                        "file": path.name,
                        "line": line_no,
                        "signal": update_match.group(1),
                        "detail": "",
                        "text": compact,
                        "path": str(path),
                    }
                )
            if "GameUtil.CheckCondition" in compact:
                rows.append(
                    {
                        "kind": "condition_check",
                        "file": path.name,
                        "line": line_no,
                        "signal": _unique_join(re.findall(r"GameUtil\.CheckCondition\(([^)\n]+)\)", compact), limit=4),
                        "detail": "",
                        "text": compact,
                        "path": str(path),
                    }
                )
            for signal in ("CheckDisplayRedDotByFaqiId", "CheckTreeActiveRedDotByFaqiId", "CheckUpLevelRedDotByFaqiId", "CheckUpStageRedDotByFaqiId", "CheckWakeRedDotByFaqiId"):
                if signal in compact:
                    rows.append(
                        {
                            "kind": "model_red_dot_call",
                            "file": path.name,
                            "line": line_no,
                            "signal": signal,
                            "detail": "",
                            "text": compact,
                            "path": str(path),
                        }
                    )
            if "OnBackpackItemsChanged" in compact:
                rows.append(
                    {
                        "kind": "backpack_handler_reference",
                        "file": path.name,
                        "line": line_no,
                        "signal": "OnBackpackItemsChanged",
                        "detail": "definition" if re.search(r"function\s+_M\.OnBackpackItemsChanged", compact) else "call_or_reference",
                        "text": compact,
                        "path": str(path),
                    }
                )
    return rows


def _collect_bluestarsea_red_dot_lifecycle(blue_dir: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    if not blue_dir.is_dir():
        return rows
    for path in sorted(blue_dir.glob("*.lua"), key=lambda item: item.name.lower()):
        if "__" in path.stem:
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        current_function = ""
        for line_no, line in enumerate(lines, start=1):
            compact = line.strip()
            if not compact:
                continue
            fn_match = _METHOD_FUNCTION_RE.match(compact)
            if fn_match:
                current_function = fn_match.group(1)
            patterns = [
                ("manager_event_add", r"((?:BackpackMgr|WalletMgr|LuaEventMgr)[^:]*:AddEventHandler\(([^)]*)\))"),
                ("manager_event_remove", r"((?:BackpackMgr|WalletMgr|LuaEventMgr)[^:]*:RemoveEventHandler\(([^)]*)\))"),
                ("red_dot_bind", r"(BindRedDot\(RedDotID\.([A-Za-z0-9_]+))"),
                ("ui_red_dot_bind", r"(BindRedDotEventNew\(([^)]*)\))"),
                ("ui_function_type", r"(SetFunctionType\(([^)]*)\))"),
                ("red_dot_raise", r"(RaiseRedDotEvent\(RedDotID\.([A-Za-z0-9_]+))"),
                ("model_red_dot_init", r"((InitDisplayRedDot|InitTreeActiveRedDot|RaiseAllRedDotEvents|OnBackpackItemsChanged)\()"),
            ]
            for kind, pattern in patterns:
                for match in re.finditer(pattern, compact):
                    rows.append(
                        {
                            "kind": kind,
                            "file": path.name,
                            "line": line_no,
                            "function": current_function,
                            "signal": match.group(2) if match.lastindex and match.lastindex >= 2 else "",
                            "text": compact,
                            "path": str(path),
                        }
                    )
    return rows


def _collect_bluestarsea_red_dot_config_rows(
    red_dot_rows: list[dict[str, Any]],
    open_function_rows: list[dict[str, Any]],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for row in red_dot_rows:
        row_id = str(row.get("id", ""))
        parent = str(row.get("parent", ""))
        if row_id == "BLUESEA" or "BlueStarSea" in row_id or "BLUESEA" in parent or "BlueStarSea" in parent:
            rows.append(
                {
                    "source": "RedDotId",
                    "id": row_id,
                    "name": row_id,
                    "parent": parent,
                    "type": row.get("type", ""),
                    "condition": row.get("condition", ""),
                    "red_dot": "",
                    "show_condition": "",
                    "lua_path": "",
                }
            )
    for row in open_function_rows:
        red_dot = str(row.get("redDot", ""))
        name = _plain(row, "name")
        if red_dot == "BLUESEA" or "BlueStarSea" in red_dot or "蓝色星海" in name or str(row.get("luaPath", "")).find("BlueStarSea") >= 0:
            rows.append(
                {
                    "source": "OpenFunction",
                    "id": row.get("id", ""),
                    "name": name,
                    "parent": "",
                    "type": row.get("type", ""),
                    "condition": row.get("condition", ""),
                    "red_dot": red_dot,
                    "show_condition": row.get("showCondition", ""),
                    "lua_path": row.get("luaPath", ""),
                }
            )
    return rows


def _collect_bluestarsea_open_red_dot_anomalies(
    *,
    function_rows: list[dict[str, Any]],
    rule_rows: list[dict[str, object]],
    binding_rows: list[dict[str, object]],
) -> list[dict[str, object]]:
    anomalies: list[dict[str, object]] = []
    placeholder_binds = [row for row in binding_rows if row.get("kind") == "bind_red_dot_placeholder"]
    if placeholder_binds:
        anomalies.append(
            {
                "category": "manager_red_dot_placeholder",
                "severity": "medium",
                "summary": f"BlueStarSeaMgr 中有 {len(placeholder_binds)} 个 BindRedDot 回调直接 return false",
                "evidence": _unique_join((f"{row.get('file')}:{row.get('line')}:{row.get('signal')}" for row in placeholder_binds), limit=12),
            }
        )
    handler_rules = [row for row in rule_rows if row.get("function") == "OnBackpackItemsChanged"]
    handler_refs = [
        row
        for row in binding_rows
        if row.get("signal") == "OnBackpackItemsChanged" and row.get("detail") != "definition"
    ]
    if handler_rules and not handler_refs:
        anomalies.append(
            {
                "category": "backpack_handler_without_visible_call_site",
                "severity": "low",
                "summary": "Model 定义了 OnBackpackItemsChanged，但当前 BlueStarSea 文件内没有看到直接调用点",
                "evidence": _unique_join((f"{row.get('function')}:{row.get('line_start')}-{row.get('line_end')}" for row in handler_rules), limit=4),
            }
        )
    if not function_rows:
        anomalies.append(
            {
                "category": "empty_function_table",
                "severity": "low",
                "summary": "配置中 Function 表为空，但 BlueStarSeaMainView 仍保留功能按钮读取逻辑",
                "evidence": "Function.lua row_count=0",
            }
        )
    return anomalies


def _write_bluestarsea_open_red_dot_markdown(
    path: Path,
    *,
    export_base: Path,
    blue_cfg_dir: Path,
    blue_game_dir: Path,
    open_rows: list[dict[str, object]],
    gate_rows: list[dict[str, object]],
    rule_rows: list[dict[str, object]],
    binding_rows: list[dict[str, object]],
    config_rows: list[dict[str, object]],
    lifecycle_rows: list[dict[str, object]],
    anomaly_rows: list[dict[str, object]],
) -> None:
    lines = [
        "# BlueStarSea 开放条件与红点探针",
        "",
        f"- 导出根：`{export_base}`",
        f"- 配置目录：`{blue_cfg_dir}`",
        f"- 逻辑目录：`{blue_game_dir}`",
        f"- 入口：{len(open_rows)}；门槛常量：{len(gate_rows)}；红点规则：{len(rule_rows)}；UI/事件证据：{len(binding_rows)}；红点配置：{len(config_rows)}；生命周期证据：{len(lifecycle_rows)}；异常：{len(anomaly_rows)}",
        "",
        "## 入口开放配置",
        "",
        "| id | sort | 名称 | showcondition | opencondition | openlan | interface |",
        "| ---: | ---: | --- | --- | --- | --- | ---: |",
    ]
    for row in open_rows:
        lines.append(
            "| "
            f"{row.get('id', '')} | "
            f"{row.get('sort', '')} | "
            f"{_markdown_table_cell(row.get('name', ''), limit=80)} | "
            f"{_markdown_table_cell(row.get('showcondition', ''), limit=140)} | "
            f"{_markdown_table_cell(row.get('opencondition', ''), limit=180)} | "
            f"{_markdown_table_cell(row.get('openlan', ''), limit=100)} | "
            f"{row.get('interface', '')} |"
        )

    lines.extend(["", "## 门槛常量", "", "| id | value |", "| --- | --- |"])
    for row in gate_rows:
        lines.append(
            "| "
            f"{_markdown_table_cell(row.get('id', ''), limit=100)} | "
            f"{_markdown_table_cell(row.get('value', ''), limit=260)} |"
        )

    lines.extend(
        [
            "",
            "## 红点规则",
            "",
            "| 函数 | 分类 | 开放门槛 | 背包/道具检查 | 缓存 | 事件 | 行号 |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in rule_rows:
        lines.append(
            "| "
            f"{_markdown_table_cell(row.get('function', ''), limit=120)} | "
            f"{_markdown_table_cell(row.get('category', ''), limit=80)} | "
            f"{_markdown_table_cell(row.get('open_gate', ''), limit=160)} | "
            f"{_markdown_table_cell(row.get('item_checks', ''), limit=120)} | "
            f"{_markdown_table_cell(row.get('cache_writes', ''), limit=120)} | "
            f"{_markdown_table_cell(row.get('raises', ''), limit=140)} | "
            f"{row.get('line_start', '')}-{row.get('line_end', '')} |"
        )

    lines.extend(
        [
            "",
            "## 红点配置与功能入口",
            "",
            "| 来源 | id | 名称 | parent | type | redDot | condition | showCondition | luaPath |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in config_rows:
        lines.append(
            "| "
            f"{_markdown_table_cell(row.get('source', ''), limit=80)} | "
            f"{_markdown_table_cell(row.get('id', ''), limit=100)} | "
            f"{_markdown_table_cell(row.get('name', ''), limit=120)} | "
            f"{_markdown_table_cell(row.get('parent', ''), limit=100)} | "
            f"{_markdown_table_cell(row.get('type', ''), limit=60)} | "
            f"{_markdown_table_cell(row.get('red_dot', ''), limit=120)} | "
            f"{_markdown_table_cell(row.get('condition', ''), limit=160)} | "
            f"{_markdown_table_cell(row.get('show_condition', ''), limit=120)} | "
            f"{_markdown_table_cell(row.get('lua_path', ''), limit=180)} |"
        )

    lines.extend(
        [
            "",
            "## 红点生命周期证据",
            "",
            "| 类型 | 文件 | 行 | 函数 | 信号 | 代码 |",
            "| --- | --- | ---: | --- | --- | --- |",
        ]
    )
    for row in lifecycle_rows[:100]:
        lines.append(
            "| "
            f"{_markdown_table_cell(row.get('kind', ''), limit=90)} | "
            f"{_markdown_table_cell(row.get('file', ''), limit=90)} | "
            f"{row.get('line', '')} | "
            f"{_markdown_table_cell(row.get('function', ''), limit=100)} | "
            f"{_markdown_table_cell(row.get('signal', ''), limit=140)} | "
            f"{_markdown_table_cell(row.get('text', ''), limit=260)} |"
        )

    lines.extend(
        [
            "",
            "## UI/事件证据",
            "",
            "| 类型 | 文件 | 行 | 信号 | 细节 | 代码 |",
            "| --- | --- | ---: | --- | --- | --- |",
        ]
    )
    for row in binding_rows[:120]:
        lines.append(
            "| "
            f"{_markdown_table_cell(row.get('kind', ''), limit=80)} | "
            f"{_markdown_table_cell(row.get('file', ''), limit=80)} | "
            f"{row.get('line', '')} | "
            f"{_markdown_table_cell(row.get('signal', ''), limit=140)} | "
            f"{_markdown_table_cell(row.get('detail', ''), limit=120)} | "
            f"{_markdown_table_cell(row.get('text', ''), limit=240)} |"
        )

    lines.extend(["", "## 静态异常", "", "| 分类 | 级别 | 摘要 | 证据 |", "| --- | --- | --- | --- |"])
    for row in anomaly_rows:
        lines.append(
            "| "
            f"{_markdown_table_cell(row.get('category', ''), limit=100)} | "
            f"{_markdown_table_cell(row.get('severity', ''), limit=60)} | "
            f"{_markdown_table_cell(row.get('summary', ''), limit=240)} | "
            f"{_markdown_table_cell(row.get('evidence', ''), limit=220)} |"
        )

    lines.extend(
        [
            "",
            "## 初步结论",
            "",
            "- `Base.showcondition` 更像入口展示/点击前置；`Base.opencondition` 控制对应入口是否解锁，并配合 `openlan` 显示锁定说明。",
            "- `ConfigValue.OPENCONDITION2` 是当前淬灵域相关红点和领取/激活入口的全局二阶段门槛。",
            "- 升级、升星、觉醒红点主要读取下一阶配置消耗，并用背包数量判断是否足够。",
            "- 图鉴/来源类红点以 `StarTree` 条件是否满足为核心；树节点激活红点以未激活节点的消耗道具是否足够为核心。",
            "- `OpenFunction` 把蓝色星海入口挂到 `BLUESEA` 总红点，三个养成页签分别挂 `BlueStarSea_UpLevel / UpStage / Wake`；`RedDotId` 中 `BlueStarSea_Display` 和 `BlueStarSea_TreeActive` 是额外子红点。",
            "- `BlueStarSeaMgr` 当前静态绑定的若干红点回调返回 `false`，所以实际红点可能由组件局部刷新或其他事件链路驱动；这需要后续继续追 UI 事件注册。",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def build_fanxiu_bluestarsea_open_red_dot_probe(
    *,
    export_root: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    export_base = resolve_fanxiu_export_root(export_root)
    output_dir = export_base / "apk_static_index"
    output_dir.mkdir(parents=True, exist_ok=True)
    lang_path = _find_lang_path(export_base)
    blue_cfg_dir = _find_cfg_text_asset_dir(export_base, "bluestarsea")
    blue_game_dir = _find_text_asset_dir(export_base, "lscripts", "gamesystem", "game", module="bluestarsea")
    if blue_cfg_dir is None:
        raise FanxiuResourceError("未找到已导出的 BlueStarSea 配置 TextAsset，请先运行热更新 lscripts 报告。")
    if blue_game_dir is None:
        raise FanxiuResourceError("未找到已导出的 BlueStarSea 逻辑 TextAsset，请先运行热更新 lscripts 报告。")

    base = _parse_config(blue_cfg_dir / "Base.lua", lang_path)
    config_value = _parse_config(blue_cfg_dir / "ConfigValue.lua", lang_path)
    function_config = _parse_config(blue_cfg_dir / "Function.lua", lang_path)
    red_dot_dir = _find_cfg_text_asset_dir(export_base, "reddot")
    open_function_dir = _find_cfg_text_asset_dir(export_base, "open_function")
    red_dot_config = _parse_config(red_dot_dir / "RedDotId.lua" if red_dot_dir else None, lang_path)
    open_function_config = _parse_config(open_function_dir / "OpenFunction.lua" if open_function_dir else None, lang_path)
    open_rows = _collect_bluestarsea_open_entries(base["rows"])
    gate_ids = {"LIMIT", "TIMERECOVER", "SCHEME_LIMIT", "STARTENERGY", "RULE", "REWARD_FAZEID"}
    gate_rows = [
        {"id": row.get("id", ""), "value": row.get("value", "")}
        for row in config_value["rows"]
        if str(row.get("id", "")).startswith("OPENCONDITION") or row.get("id") in gate_ids
    ]
    rule_rows = _collect_bluestarsea_red_dot_rules(blue_game_dir / "BlueStarSeaModel.lua")
    binding_rows = _collect_bluestarsea_red_dot_bindings(blue_game_dir)
    lifecycle_rows = _collect_bluestarsea_red_dot_lifecycle(blue_game_dir)
    config_rows = _collect_bluestarsea_red_dot_config_rows(red_dot_config["rows"], open_function_config["rows"])
    anomaly_rows = _collect_bluestarsea_open_red_dot_anomalies(
        function_rows=function_config["rows"],
        rule_rows=rule_rows,
        binding_rows=binding_rows,
    )

    open_count = _write_tsv(
        output_dir / "hot_update_bluestarsea_open_entries.tsv",
        ["id", "sort", "name", "showcondition", "opencondition", "openlan", "interface", "pre", "lockpre"],
        open_rows,
    )
    gate_count = _write_tsv(output_dir / "hot_update_bluestarsea_open_gate_values.tsv", ["id", "value"], gate_rows)
    rule_count = _write_tsv(
        output_dir / "hot_update_bluestarsea_red_dot_rules.tsv",
        ["function", "category", "open_gate", "config_reads", "item_checks", "cache_writes", "raises", "calls", "returns", "line_start", "line_end", "path"],
        rule_rows,
    )
    binding_count = _write_tsv(
        output_dir / "hot_update_bluestarsea_red_dot_bindings.tsv",
        ["kind", "file", "line", "signal", "detail", "text", "path"],
        binding_rows,
    )
    config_count = _write_tsv(
        output_dir / "hot_update_bluestarsea_red_dot_configs.tsv",
        ["source", "id", "name", "parent", "type", "condition", "red_dot", "show_condition", "lua_path"],
        config_rows,
    )
    lifecycle_count = _write_tsv(
        output_dir / "hot_update_bluestarsea_red_dot_lifecycle.tsv",
        ["kind", "file", "line", "function", "signal", "text", "path"],
        lifecycle_rows,
    )
    anomaly_count = _write_tsv(
        output_dir / "hot_update_bluestarsea_open_red_dot_anomalies.tsv",
        ["category", "severity", "summary", "evidence"],
        anomaly_rows,
    )
    result = {
        "export_root": str(export_base),
        "output_dir": str(output_dir),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "inputs": {
            "blue_cfg_dir": str(blue_cfg_dir),
            "blue_game_dir": str(blue_game_dir),
            "lang_path": str(lang_path or ""),
        },
        "counts": {
            "open_entries": open_count,
            "gate_values": gate_count,
            "red_dot_rules": rule_count,
            "bindings": binding_count,
            "red_dot_configs": config_count,
            "lifecycle": lifecycle_count,
            "anomalies": anomaly_count,
        },
        "outputs": {
            "summary": str(output_dir / "hot_update_bluestarsea_open_red_dot_report.json"),
            "markdown": str(output_dir / "hot_update_bluestarsea_open_red_dot_report.md"),
            "open_entries": str(output_dir / "hot_update_bluestarsea_open_entries.tsv"),
            "gate_values": str(output_dir / "hot_update_bluestarsea_open_gate_values.tsv"),
            "red_dot_rules": str(output_dir / "hot_update_bluestarsea_red_dot_rules.tsv"),
            "bindings": str(output_dir / "hot_update_bluestarsea_red_dot_bindings.tsv"),
            "red_dot_configs": str(output_dir / "hot_update_bluestarsea_red_dot_configs.tsv"),
            "lifecycle": str(output_dir / "hot_update_bluestarsea_red_dot_lifecycle.tsv"),
            "anomalies": str(output_dir / "hot_update_bluestarsea_open_red_dot_anomalies.tsv"),
        },
    }
    _write_bluestarsea_open_red_dot_markdown(
        output_dir / "hot_update_bluestarsea_open_red_dot_report.md",
        export_base=export_base,
        blue_cfg_dir=blue_cfg_dir,
        blue_game_dir=blue_game_dir,
        open_rows=open_rows,
        gate_rows=gate_rows,
        rule_rows=rule_rows,
        binding_rows=binding_rows,
        config_rows=config_rows,
        lifecycle_rows=lifecycle_rows,
        anomaly_rows=anomaly_rows,
    )
    (output_dir / "hot_update_bluestarsea_open_red_dot_report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


def _split_fanxiu_item_tokens(token: object) -> list[str]:
    text = str(token or "").strip()
    if not text:
        return []
    return [part.strip() for part in text.split(",") if part.strip()]


def _fanxiu_item_token_detail(token: object, item_by_id: dict[int, dict[str, Any]]) -> dict[str, object]:
    item_id, count, raw = _parse_item_reward_token(token)
    return {
        "item_id": item_id or "",
        "item_name": _item_name(item_by_id, item_id) if item_id is not None else "",
        "count": count or "",
        "raw": raw,
        "text": _fanxiu_item_token_text(token, item_by_id),
    }


def _collect_bluestarsea_purify_break_rows(
    break_rows: list[dict[str, Any]],
    *,
    item_by_id: dict[int, dict[str, Any]],
    start_energy: int,
    energy_limit: int,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for row in sorted(break_rows, key=lambda item: (int(item.get("sort") or 0), int(item.get("id") or 0))):
        item_id = int(row.get("item") or 0)
        energy_consume = int(row.get("energyConsume") or 0)
        obtain_tokens = _split_fanxiu_item_tokens(row.get("breakObtain"))
        obtain_details = [_fanxiu_item_token_detail(token, item_by_id) for token in obtain_tokens]
        rows.append(
            {
                "id": row.get("id", ""),
                "sort": row.get("sort", ""),
                "filter": row.get("filter", ""),
                "item_id": item_id,
                "item_name": _item_name(item_by_id, item_id),
                "energy_consume": energy_consume,
                "break_obtain": _unique_join((detail["text"] for detail in obtain_details), limit=12),
                "obtain_item_ids": _unique_join((detail["item_id"] for detail in obtain_details), limit=12),
                "obtain_raw": row.get("breakObtain", ""),
                "max_count_start_energy": start_energy // energy_consume if energy_consume > 0 else "",
                "max_count_energy_limit": energy_limit // energy_consume if energy_consume > 0 else "",
            }
        )
    return rows


def _collect_bluestarsea_charge_rows(
    charging_rows: list[dict[str, Any]],
    *,
    item_by_id: dict[int, dict[str, Any]],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    cumulative_cost_by_item: dict[int, int] = {}
    cumulative_energy = 0
    for row in sorted(charging_rows, key=lambda item: int(item.get("times") or item.get("id") or 0)):
        detail = _fanxiu_item_token_detail(row.get("consume"), item_by_id)
        item_id = int(detail["item_id"] or 0)
        count = int(detail["count"] or 0)
        if item_id:
            cumulative_cost_by_item[item_id] = cumulative_cost_by_item.get(item_id, 0) + count
        cumulative_energy += int(row.get("energy") or 0)
        rows.append(
            {
                "id": row.get("id", ""),
                "times": row.get("times", ""),
                "condition": row.get("condition", ""),
                "consume_item_id": detail["item_id"],
                "consume_item_name": detail["item_name"],
                "consume_count": detail["count"],
                "consume_text": detail["text"],
                "energy": row.get("energy", ""),
                "cumulative_consume": _unique_join(
                    (f"{_item_name(item_by_id, item_id) or item_id}x{amount}" for item_id, amount in cumulative_cost_by_item.items()),
                    limit=8,
                ),
                "cumulative_energy": cumulative_energy,
                "faze_id": _compact_json(row.get("fazeId"), limit=220),
            }
        )
    return rows


def _categorize_bluestarsea_purify_block(file_name: str, function_name: str, body: str) -> str:
    if "Charge" in file_name or "Charge" in function_name:
        if function_name.startswith("CM_"):
            return "charge_send"
        if function_name.startswith("SM_") or function_name == "OnCharge":
            return "charge_receive"
        return "charge_ui"
    if function_name == "BuildPurifyItems":
        return "purify_auto_select"
    if "PurifyComp" in file_name and function_name == "OnOneKeyClick":
        return "purify_one_key"
    if "MutiPurifyView" in file_name:
        return "purify_multi_ui"
    if file_name == "BlueStarSeaPurifyView.lua":
        return "purify_single_ui"
    if function_name.startswith("CM_BlueStarSeaPurify"):
        return "purify_send"
    if function_name.startswith("SM_BlueStarSeaPurify") or function_name == "OnPurify":
        return "purify_receive"
    if "Reward" in function_name or "Obtain" in function_name or "breakObtain" in body:
        return "reward_preview"
    if "BreakItem" in function_name or "BreakItem" in body:
        return "break_item_inventory"
    if "Energy" in function_name or "energy" in body:
        return "energy_state"
    return "purify_misc"


def _collect_bluestarsea_purify_runtime_rows(blue_dir: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    if not blue_dir.is_dir():
        return rows
    files = [
        "BlueStarSeaChargeTipsView.lua",
        "BlueStarSeaData.lua",
        "BlueStarSeaModel.lua",
        "BlueStarSeaMutiPurifyComp.lua",
        "BlueStarSeaMutiPurifyView.lua",
        "BlueStarSeaNetLogic.lua",
        "BlueStarSeaPurifyView.lua",
    ]
    signals = (
        "Purify",
        "Charge",
        "Energy",
        "energy",
        "BreakItem",
        "breakObtain",
        "rewardResults",
        "BuildPurifyItems",
        "BlueStarSeaPurifyItemVO",
        "GetCurrentEnergyValue",
        "GetChargeCfgByTimes",
        "CheckCanUseCharge",
        "CM_BlueStarSeaPurifyFun",
        "CM_BlueStarSeaChargeFun",
    )
    for file_name in files:
        path = blue_dir / file_name
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for block in _lua_method_blocks(text):
            name = str(block["name"])
            body = str(block["body"])
            if not any(signal in name or signal in body for signal in signals):
                continue
            packets = re.findall(r"""(CM_BlueStarSea(?:Purify|Charge)Fun|SM_BlueStarSea(?:Purify|Charge)Fun)""", body)
            packets.extend([name] if re.match(r"""[CS]M_BlueStarSea(?:Purify|Charge)Fun""", name) else [])
            vo_fields = [f"{field}={expr.strip()}" for field, expr in re.findall(r"""vo\.([A-Za-z_]\w*)\s*=\s*([^\n]+)""", body)]
            state_writes = [f"energy={expr.strip()}" for expr in re.findall(r"""self\._SyncInfo\.vo\.energy\s*=\s*([^\n]+)""", body)]
            state_writes.extend(f"todayChargingTimes={expr.strip()}" for expr in re.findall(r"""self\._SyncInfo\.vo\.todayChargingTimes\s*=\s*([^\n]+)""", body))
            state_writes.extend(f"lastRecoverTime={expr.strip()}" for expr in re.findall(r"""self\._SyncInfo\.vo\.lastRecoverTime\s*=\s*([^\n]+)""", body))
            if "_PurifyRewardResults" in body:
                state_writes.append("_PurifyRewardResults=msg.rewardResults")
            guards = []
            if re.search(r"""msg\.code\s*==\s*0""", body):
                guards.append("msg.code==0")
            if "not syncInfo" in body:
                guards.append("syncInfo exists")
            if "_chooseCount<=0" in body:
                guards.append("_chooseCount>0")
            if "ownedCount<=0" in body:
                guards.append("ownedCount>0")
            energy_reads = []
            for signal in ("GetCurrentEnergyValue", "syncInfo.vo.energy", "currentEnergy", "energyConsume", "GetEnergyLimit", "LIMIT", "STARTENERGY"):
                if signal in body:
                    energy_reads.append(signal)
            item_reads = []
            for signal in ("GetItemNumById", "GetBackpackNumByItem", "GetAllBreakItemList", "GetBreakItemDisplayList", "breakObtain", "SpliteItemStr", "UnwrapChestItems"):
                if signal in body:
                    item_reads.append(signal)
            reward_calls = re.findall(r"""([A-Za-z_]\w*Mgr)\.Inst_get\(\):AddRewardResults\(([^)]*)\)""", body)
            events = re.findall(r"""BlueStarSeaType\.EventType\.([A-Za-z_]\w*)""", body)
            model_calls = re.findall(r"""BlueStarSeaMgr\.Inst_get\(\)\.Model:([A-Za-z_]\w*)\(([^)]*)\)""", body)
            data_calls = re.findall(r"""self\.BlueStarSeaData:([A-Za-z_]\w*)\(([^)]*)\)""", body)
            config_reads = re.findall(r"""ConfigName\.([A-Za-z_]\w*)""", body)
            rows.append(
                {
                    "stage": _categorize_bluestarsea_purify_block(file_name, name, body),
                    "file": file_name,
                    "function": name,
                    "line_start": block["start_line"],
                    "line_end": block["end_line"],
                    "packets": _unique_join(packets, limit=12),
                    "vo_fields": _unique_join(vo_fields, limit=12),
                    "events": _unique_join(events, limit=12),
                    "state_writes": _unique_join(state_writes, limit=12),
                    "guards": _unique_join(guards, limit=12),
                    "energy_reads": _unique_join(energy_reads, limit=12),
                    "item_reads": _unique_join(item_reads, limit=12),
                    "reward_calls": _unique_join((f"{mgr}:AddRewardResults({args.strip()})" for mgr, args in reward_calls), limit=6),
                    "model_calls": _unique_join((f"{call}({args.strip()})" for call, args in model_calls), limit=18),
                    "data_calls": _unique_join((f"{call}({args.strip()})" for call, args in data_calls), limit=18),
                    "config_reads": _unique_join(config_reads, limit=18),
                    "path": str(path),
                }
            )
    return rows


def _collect_bluestarsea_purify_packet_rows(export_base: Path) -> list[dict[str, object]]:
    packets, fields_by_packet = _read_packet_index_by_module(
        export_base,
        module_name="player.bluestarsea",
        name_keyword="BlueStarSea",
    )
    names = {"CM_BlueStarSeaCharge", "SM_BlueStarSeaCharge", "CM_BlueStarSeaPurify", "SM_BlueStarSeaPurify", "BlueStarSeaPurifyItemVO"}
    packet_by_name = {row.get("name", ""): row for row in packets}
    rows: list[dict[str, object]] = []
    for name in sorted(names):
        packet = packet_by_name.get(name, {})
        packet_fields = fields_by_packet.get(name, [])
        if not packet_fields:
            rows.append(
                {
                    "packet": name,
                    "direction": packet.get("direction", "vo" if name.endswith("VO") else ""),
                    "packet_id": packet.get("id", ""),
                    "field_index": "",
                    "field_name": "",
                    "read_method": "",
                    "type_hint": "",
                    "file": packet.get("file", ""),
                    "line": "",
                }
            )
            continue
        for field in packet_fields:
            rows.append(
                {
                    "packet": name,
                    "direction": field.get("direction") or packet.get("direction", ""),
                    "packet_id": field.get("packet_id") or packet.get("id", ""),
                    "field_index": field.get("field_index", ""),
                    "field_name": field.get("field_name", ""),
                    "read_method": field.get("read_method", ""),
                    "type_hint": field.get("type_hint", ""),
                    "file": field.get("file") or packet.get("file", ""),
                    "line": field.get("line", ""),
                }
            )
    return rows


def _write_bluestarsea_purify_energy_markdown(
    path: Path,
    *,
    export_base: Path,
    blue_cfg_dir: Path,
    blue_game_dir: Path,
    break_rows: list[dict[str, object]],
    charge_rows: list[dict[str, object]],
    runtime_rows: list[dict[str, object]],
    packet_rows: list[dict[str, object]],
    config_values: dict[str, str],
) -> None:
    lines = [
        "# BlueStarSea 提纯能量链路探针",
        "",
        f"- 导出根：`{export_base}`",
        f"- 配置目录：`{blue_cfg_dir}`",
        f"- 逻辑目录：`{blue_game_dir}`",
        f"- 分解项：{len(break_rows)}；充能项：{len(charge_rows)}；运行证据：{len(runtime_rows)}；协议字段：{len(packet_rows)}",
        f"- 常量：`LIMIT={config_values.get('LIMIT', '')}`，`STARTENERGY={config_values.get('STARTENERGY', '')}`，`TIMERECOVER={config_values.get('TIMERECOVER', '')}`，`SCHEME_LIMIT={config_values.get('SCHEME_LIMIT', '')}`",
        "",
        "## 分解项",
        "",
        "| id | sort | 道具 | filter | 能量消耗 | 产物 | 初始能量可分解 | 能量上限可分解 |",
        "| ---: | ---: | --- | ---: | ---: | --- | ---: | ---: |",
    ]
    for row in break_rows:
        lines.append(
            "| "
            f"{row.get('id', '')} | "
            f"{row.get('sort', '')} | "
            f"{_markdown_table_cell(str(row.get('item_name') or row.get('item_id') or ''), limit=120)} | "
            f"{row.get('filter', '')} | "
            f"{row.get('energy_consume', '')} | "
            f"{_markdown_table_cell(row.get('break_obtain', ''), limit=180)} | "
            f"{row.get('max_count_start_energy', '')} | "
            f"{row.get('max_count_energy_limit', '')} |"
        )

    lines.extend(["", "## 充能项", "", "| 次数 | 条件 | 消耗 | 增加能量 | 累计消耗 | 累计能量 | fazeId |", "| ---: | --- | --- | ---: | --- | ---: | --- |"])
    for row in charge_rows:
        lines.append(
            "| "
            f"{row.get('times', '')} | "
            f"{_markdown_table_cell(row.get('condition', ''), limit=160)} | "
            f"{_markdown_table_cell(row.get('consume_text', ''), limit=120)} | "
            f"{row.get('energy', '')} | "
            f"{_markdown_table_cell(row.get('cumulative_consume', ''), limit=160)} | "
            f"{row.get('cumulative_energy', '')} | "
            f"{_markdown_table_cell(row.get('faze_id', ''), limit=140)} |"
        )

    lines.extend(
        [
            "",
            "## 协议字段",
            "",
            "| packet/VO | 方向 | 字段 | 读取方法 | 类型提示 | 文件 |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in packet_rows:
        lines.append(
            "| "
            f"{_markdown_table_cell(row.get('packet', ''), limit=120)} | "
            f"{_markdown_table_cell(row.get('direction', ''), limit=80)} | "
            f"{_markdown_table_cell(row.get('field_name', ''), limit=120)} | "
            f"{_markdown_table_cell(row.get('read_method', ''), limit=100)} | "
            f"{_markdown_table_cell(row.get('type_hint', ''), limit=120)} | "
            f"{_markdown_table_cell(row.get('file', ''), limit=120)} |"
        )

    lines.extend(
        [
            "",
            "## 运行链路",
            "",
            "| 阶段 | 文件 | 函数 | 协议/事件 | VO字段 | 状态写入 | 能量读取 | 道具读取 | 行号 |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in runtime_rows:
        lines.append(
            "| "
            f"{_markdown_table_cell(row.get('stage', ''), limit=90)} | "
            f"{_markdown_table_cell(row.get('file', ''), limit=90)} | "
            f"{_markdown_table_cell(row.get('function', ''), limit=100)} | "
            f"{_markdown_table_cell(_unique_join([row.get('packets', ''), row.get('events', '')], limit=4), limit=180)} | "
            f"{_markdown_table_cell(row.get('vo_fields', ''), limit=160)} | "
            f"{_markdown_table_cell(row.get('state_writes', ''), limit=180)} | "
            f"{_markdown_table_cell(row.get('energy_reads', ''), limit=160)} | "
            f"{_markdown_table_cell(row.get('item_reads', ''), limit=160)} | "
            f"{row.get('line_start', '')}-{row.get('line_end', '')} |"
        )

    lines.extend(
        [
            "",
            "## 初步结论",
            "",
            "- 单选和批量提纯都只向服务端发送 `BlueStarSeaPurifyItemVO.itemId/count` 列表；本地根据当前能量和背包数量限制选择数量，并根据 `BreakItem.breakObtain` 做奖励预览。",
            "- 一键提纯由 `BuildPurifyItems` 生成列表：优先使用已套用方案的 `itemPriority`，没有方案时按 `BreakItem.sort` 排序，并用 `currentEnergy / energyConsume` 截断数量。",
            "- 提纯最终结果以后端 `SM_BlueStarSeaPurify` 为准：客户端只回写 `energy`，保存 `rewardResults`，并把 `rewardResults` 交给奖励弹窗。",
            "- 充能客户端只发送 `times`；本地根据 `Charging` 表预览累计消耗和能量，服务端回包再写入 `energy/todayChargingTimes/lastRecoverTime`。",
            "- 真实配置中每次分解消耗 150 能量；初始能量 4500 理论上最多 30 次，能量上限 9000 理论上最多 60 次，实际还受道具拥有数量限制。",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def build_fanxiu_bluestarsea_purify_energy_probe(
    *,
    export_root: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    export_base = resolve_fanxiu_export_root(export_root)
    output_dir = export_base / "apk_static_index"
    output_dir.mkdir(parents=True, exist_ok=True)
    lang_path = _find_lang_path(export_base)
    blue_cfg_dir = _find_cfg_text_asset_dir(export_base, "bluestarsea")
    item_dir = _find_cfg_text_asset_dir(export_base, "item")
    blue_game_dir = _find_text_asset_dir(export_base, "lscripts", "gamesystem", "game", module="bluestarsea")
    if blue_cfg_dir is None:
        raise FanxiuResourceError("未找到已导出的 BlueStarSea 配置 TextAsset，请先运行热更新 lscripts 报告。")
    if blue_game_dir is None:
        raise FanxiuResourceError("未找到已导出的 BlueStarSea 逻辑 TextAsset，请先运行热更新 lscripts 报告。")

    item = _parse_config((item_dir / "Item.lua") if item_dir else None, lang_path)
    item_by_id = _map_by_id(item["rows"])
    break_item = _parse_config(blue_cfg_dir / "BreakItem.lua", lang_path)
    charging = _parse_config(blue_cfg_dir / "Charging.lua", lang_path)
    config_value = _parse_config(blue_cfg_dir / "ConfigValue.lua", lang_path)
    config_values = {str(row.get("id", "")): str(row.get("value", "")) for row in config_value["rows"]}
    start_energy = int(config_values.get("STARTENERGY") or 0)
    energy_limit = int(config_values.get("LIMIT") or 0)

    break_rows = _collect_bluestarsea_purify_break_rows(
        break_item["rows"],
        item_by_id=item_by_id,
        start_energy=start_energy,
        energy_limit=energy_limit,
    )
    charge_rows = _collect_bluestarsea_charge_rows(charging["rows"], item_by_id=item_by_id)
    runtime_rows = _collect_bluestarsea_purify_runtime_rows(blue_game_dir)
    packet_rows = _collect_bluestarsea_purify_packet_rows(export_base)

    break_count = _write_tsv(
        output_dir / "hot_update_bluestarsea_purify_break_items.tsv",
        ["id", "sort", "filter", "item_id", "item_name", "energy_consume", "break_obtain", "obtain_item_ids", "obtain_raw", "max_count_start_energy", "max_count_energy_limit"],
        break_rows,
    )
    charge_count = _write_tsv(
        output_dir / "hot_update_bluestarsea_purify_charging.tsv",
        ["id", "times", "condition", "consume_item_id", "consume_item_name", "consume_count", "consume_text", "energy", "cumulative_consume", "cumulative_energy", "faze_id"],
        charge_rows,
    )
    runtime_count = _write_tsv(
        output_dir / "hot_update_bluestarsea_purify_runtime_flows.tsv",
        ["stage", "file", "function", "line_start", "line_end", "packets", "vo_fields", "events", "state_writes", "guards", "energy_reads", "item_reads", "reward_calls", "model_calls", "data_calls", "config_reads", "path"],
        runtime_rows,
    )
    packet_count = _write_tsv(
        output_dir / "hot_update_bluestarsea_purify_packet_fields.tsv",
        ["packet", "direction", "packet_id", "field_index", "field_name", "read_method", "type_hint", "file", "line"],
        packet_rows,
    )
    result = {
        "export_root": str(export_base),
        "output_dir": str(output_dir),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "inputs": {
            "blue_cfg_dir": str(blue_cfg_dir),
            "blue_game_dir": str(blue_game_dir),
            "item_dir": str(item_dir or ""),
            "lang_path": str(lang_path or ""),
        },
        "counts": {
            "break_items": break_count,
            "charging": charge_count,
            "runtime_flows": runtime_count,
            "packet_fields": packet_count,
            "by_runtime_stage": dict(Counter(str(row["stage"]) for row in runtime_rows).most_common()),
        },
        "outputs": {
            "summary": str(output_dir / "hot_update_bluestarsea_purify_energy_report.json"),
            "markdown": str(output_dir / "hot_update_bluestarsea_purify_energy_report.md"),
            "break_items": str(output_dir / "hot_update_bluestarsea_purify_break_items.tsv"),
            "charging": str(output_dir / "hot_update_bluestarsea_purify_charging.tsv"),
            "runtime_flows": str(output_dir / "hot_update_bluestarsea_purify_runtime_flows.tsv"),
            "packet_fields": str(output_dir / "hot_update_bluestarsea_purify_packet_fields.tsv"),
        },
    }
    _write_bluestarsea_purify_energy_markdown(
        output_dir / "hot_update_bluestarsea_purify_energy_report.md",
        export_base=export_base,
        blue_cfg_dir=blue_cfg_dir,
        blue_game_dir=blue_game_dir,
        break_rows=break_rows,
        charge_rows=charge_rows,
        runtime_rows=runtime_rows,
        packet_rows=packet_rows,
        config_values=config_values,
    )
    (output_dir / "hot_update_bluestarsea_purify_energy_report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


def _collect_blld_keyword_rows(blld_dir: Path, categories: dict[str, list[str]]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for path in sorted(blld_dir.glob("*.lua"), key=lambda item: item.name.lower()):
        if "__" in path.stem:
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        for line_no, line in enumerate(lines, start=1):
            compact = line.strip()
            if not compact:
                continue
            lower = compact.lower()
            for category, terms in categories.items():
                for term in terms:
                    if term.lower() in lower:
                        rows.append(
                            {
                                "category": category,
                                "file": path.name,
                                "line": line_no,
                                "signal": term,
                                "text": compact,
                                "path": str(path),
                            }
                        )
                        break
    rows.sort(key=lambda item: (str(item["category"]), str(item["file"]), int(item["line"])))
    return rows


def _write_blld_finish_flow_markdown(
    path: Path,
    *,
    export_base: Path,
    blld_dir: Path,
    evidence_rows: list[dict[str, object]],
) -> None:
    by_category = Counter(str(row["category"]) for row in evidence_rows)

    def rows_for(category: str) -> list[dict[str, object]]:
        return [row for row in evidence_rows if row["category"] == category]

    lines = [
        "# BLLD 结算链路探针",
        "",
        f"- 导出目录：`{export_base}`",
        f"- BLLD Lua：`{blld_dir}`",
        f"- 证据行：{len(evidence_rows)}；分组：{', '.join(f'{name}:{count}' for name, count in by_category.most_common())}",
        "- 说明：本报告只描述客户端 Lua 的结算链路。服务端是否接受、校验或重算这些值，需要继续看服务端或实际抓包回包。",
        "",
        "## 结算链路",
        "",
        "1. `BLLDStartGame` 注册 `BLLDType.EventType.GameOver`，回调进入 `BLLDMgr:OnGameOver(isWin)`。",
        "2. 失败路径可见于玩家血量归零，触发 `GameOver,false`。",
        "3. 成功路径可见于撤离坚持时间结束，或撤离 boss 被击杀，触发 `GameOver,true`。",
        "4. `OnGameOver` 从 `InGameData:GetBagPlacementList()` 汇总 `rewardGroupId -> dropCount`，连同 `curLevelId/isWin` 发给 `CM_BlldFinishAndReward`。",
        "5. `CM_BlldFinishAndRewardFun` 同时带 `passRate`，失败时由 `Model:GetProgressVal()*100` 截断到 10000。",
        "6. 客户端最终展示依赖 `SM_BlldFinishAndReward` 回包落到 `Model:SetFinishAndReward(msg)`，成功后再 `CM_BlldSync`。",
        "",
    ]
    category_titles = [
        ("game_over", "GameOver 触发"),
        ("finish_send", "结算发送"),
        ("reward_find", "探索奖励"),
        ("progress", "进度与 passRate"),
        ("server_return", "服务端回包展示"),
    ]
    for category, title in category_titles:
        lines.extend([f"## {title}", "", "| 文件 | 行 | 信号 | 代码 |", "| --- | ---: | --- | --- |"])
        for row in rows_for(category)[:120]:
            lines.append(
                "| "
                f"{_markdown_table_cell(row.get('file', ''), limit=120)} | "
                f"{row.get('line', '')} | "
                f"{_markdown_table_cell(row.get('signal', ''), limit=120)} | "
                f"{_markdown_table_cell(row.get('text', ''), limit=260)} |"
            )
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def build_fanxiu_blld_finish_flow_probe(
    *,
    export_root: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    export_base = resolve_fanxiu_export_root(export_root)
    output_dir = export_base / "apk_static_index"
    output_dir.mkdir(parents=True, exist_ok=True)
    blld_dir = _find_text_asset_dir(export_base, "lscripts", "gamesystem", "game", module="blld")
    if blld_dir is None:
        raise FanxiuResourceError("未找到已导出的 BLLD 游戏逻辑 TextAsset，请先运行热更新 lscripts 报告。")

    categories = {
        "game_over": [
            "EventType.GameOver",
            "RaiseEvent(BLLDType.EventType.GameOver",
            "OnGameOver",
            "BLLDStartGame",
        ],
        "finish_send": [
            "CM_BlldFinishAndRewardFun",
            "CM_BlldFinishAndReward.",
            "GetLevelId()",
            "GetBagPlacementList()",
        ],
        "reward_find": [
            "rewardFindInfo",
            "RewardFind",
            "rewardGroupId",
            "AutoPlaceBagItem",
            "GetBagPlacementList",
        ],
        "progress": [
            "PICKUP_ADD_PROGRESS",
            "LASTSURVIVE_PROGRESS",
            "AddProgressVal",
            "GetProgressVal",
            "passRate",
        ],
        "server_return": [
            "SM_BlldFinishAndReward",
            "SetFinishAndReward",
            "GetFinishAndReward",
            "FinishAndReward",
        ],
    }
    evidence_rows = _collect_blld_keyword_rows(blld_dir, categories)
    by_category = {category: [row for row in evidence_rows if row["category"] == category] for category in categories}

    evidence_count = _write_tsv(
        output_dir / "hot_update_blld_finish_flow_evidence.tsv",
        ["category", "file", "line", "signal", "text", "path"],
        evidence_rows,
    )
    summary_rows = [
        {
            "category": category,
            "evidence_count": len(rows),
            "files": _unique_join((row["file"] for row in rows), limit=30),
            "signals": _unique_join((row["signal"] for row in rows), limit=30),
        }
        for category, rows in by_category.items()
    ]
    summary_count = _write_tsv(
        output_dir / "hot_update_blld_finish_flow_summary.tsv",
        ["category", "evidence_count", "files", "signals"],
        summary_rows,
    )

    result = {
        "export_root": str(export_base),
        "output_dir": str(output_dir),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "inputs": {"blld_dir": str(blld_dir)},
        "counts": {
            "evidence": evidence_count,
            "summary": summary_count,
            "by_category": dict(Counter(str(row["category"]) for row in evidence_rows).most_common()),
        },
        "outputs": {
            "summary": str(output_dir / "hot_update_blld_finish_flow_report.json"),
            "markdown": str(output_dir / "hot_update_blld_finish_flow_report.md"),
            "evidence": str(output_dir / "hot_update_blld_finish_flow_evidence.tsv"),
            "table": str(output_dir / "hot_update_blld_finish_flow_summary.tsv"),
        },
    }
    _write_blld_finish_flow_markdown(
        output_dir / "hot_update_blld_finish_flow_report.md",
        export_base=export_base,
        blld_dir=blld_dir,
        evidence_rows=evidence_rows,
    )
    (output_dir / "hot_update_blld_finish_flow_report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


def _find_cfg_text_asset_dir(export_base: Path, module: str) -> Path | None:
    return _find_text_asset_dir(export_base, "lscripts", "generate", "cfg", module=module)


def _parse_item_reward_token(token: object) -> tuple[int | None, int | None, str]:
    text = str(token or "")
    match = re.match(r"""(?i)item\|(\d+)(?:_(\d+))?""", text)
    if not match:
        return None, None, text
    item_id = int(match.group(1))
    count = int(match.group(2) or 1)
    return item_id, count, text


def _item_name(item_by_id: dict[int, dict[str, Any]], item_id: int | None) -> str:
    if item_id is None:
        return ""
    row = item_by_id.get(item_id, {})
    return _plain(row, "name") if row else ""


def _write_blld_reward_catalog_markdown(
    path: Path,
    *,
    export_base: Path,
    reward_group_rows: list[dict[str, object]],
    level_reward_rows: list[dict[str, object]],
) -> None:
    lines = [
        "# BLLD 奖励配置探针",
        "",
        f"- 导出目录：`{export_base}`",
        f"- 探索奖励组：{len(reward_group_rows)}；关卡奖励展开：{len(level_reward_rows)}",
        "- 说明：本报告还原客户端配置中的奖励展示和探索奖励池，不代表服务端最终发放结果。",
        "",
        "## 探索奖励组",
        "",
        "| 组 | 道具 | 名称 | 数量 | 限制 | 品质 | 权重 | 背包 |",
        "| ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in reward_group_rows:
        lines.append(
            "| "
            f"{row.get('reward_group_id', '')} | "
            f"{row.get('item_id', '')} | "
            f"{_markdown_table_cell(row.get('item_name', ''), limit=120)} | "
            f"{row.get('num', '')} | "
            f"{row.get('limit', '')} | "
            f"{row.get('quality', '')} | "
            f"{row.get('weight', '')} | "
            f"{row.get('bag', '')} |"
        )

    lines.extend(
        [
            "",
            "## 关卡探索奖励样例",
            "",
            "| 关卡 | 名称 | 奖励组 | 探索奖励 | 通关奖励标题 | 推送奖励样例 |",
            "| ---: | --- | --- | --- | --- | --- |",
        ]
    )
    level_rows = [row for row in level_reward_rows if row.get("reward_kind") == "find"]
    for row in level_rows[:80]:
        lines.append(
            "| "
            f"{row.get('level_id', '')} | "
            f"{_markdown_table_cell(row.get('level_name', ''), limit=100)} | "
            f"{row.get('reward_group_id', '')} | "
            f"{_markdown_table_cell(row.get('reward_text', ''), limit=180)} | "
            f"{_markdown_table_cell(row.get('reward_show_title', ''), limit=160)} | "
            f"{_markdown_table_cell(row.get('push_reward_preview', ''), limit=180)} |"
        )

    path.write_text("\n".join(lines), encoding="utf-8")


def build_fanxiu_blld_reward_catalog_probe(
    *,
    export_root: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    export_base = resolve_fanxiu_export_root(export_root)
    output_dir = export_base / "apk_static_index"
    output_dir.mkdir(parents=True, exist_ok=True)
    lang_path = _find_lang_path(export_base)
    blld_dir = _find_cfg_text_asset_dir(export_base, "blld")
    item_dir = _find_cfg_text_asset_dir(export_base, "item")
    if blld_dir is None:
        raise FanxiuResourceError("未找到已导出的 BLLD 配置 TextAsset，请先运行热更新 lscripts 报告。")
    if item_dir is None:
        raise FanxiuResourceError("未找到已导出的 Item 配置 TextAsset，请先运行热更新 lscripts 报告。")

    reward_group = _parse_config(blld_dir / "RewardGroup.lua", lang_path)
    level = _parse_config(blld_dir / "Level.lua", lang_path)
    item = _parse_config(item_dir / "Item.lua", lang_path)
    item_by_id = _map_by_id(item["rows"])
    reward_group_by_id = _map_by_id(reward_group["rows"])

    reward_group_rows: list[dict[str, object]] = []
    for row in reward_group["rows"]:
        item_id = int(row.get("item") or 0)
        item_row = item_by_id.get(item_id, {})
        reward_group_rows.append(
            {
                "reward_group_id": row.get("id", ""),
                "item_id": item_id,
                "item_name": _item_name(item_by_id, item_id),
                "num": row.get("num", ""),
                "limit": row.get("limit", ""),
                "quality": row.get("quality", ""),
                "weight": row.get("weight", ""),
                "bag": row.get("bag", ""),
                "item_quality": item_row.get("quality", ""),
                "icon": item_row.get("icon", ""),
                "descript": _plain(item_row, "descript") if item_row else "",
            }
        )

    level_reward_rows: list[dict[str, object]] = []
    for row in level["rows"]:
        push_names: list[str] = []
        for token in row.get("pushReward") or []:
            item_id, count, raw = _parse_item_reward_token(token)
            name = _item_name(item_by_id, item_id)
            push_names.append(f"{name or raw}x{count or ''}".rstrip("x"))
        push_preview = " | ".join(push_names[:8])
        for group_token in row.get("findReward") or []:
            try:
                group_id = int(group_token)
            except (TypeError, ValueError):
                group_id = 0
            group_row = reward_group_by_id.get(group_id, {})
            item_id = int(group_row.get("item") or 0) if group_row else None
            item_name = _item_name(item_by_id, item_id)
            level_reward_rows.append(
                {
                    "level_id": row.get("id", ""),
                    "level_name": _plain(row, "name"),
                    "stage": row.get("stage", ""),
                    "layer": row.get("layer", ""),
                    "reward_kind": "find",
                    "reward_group_id": group_id,
                    "item_id": item_id or "",
                    "item_name": item_name,
                    "num": group_row.get("num", "") if group_row else "",
                    "limit": group_row.get("limit", "") if group_row else "",
                    "quality": group_row.get("quality", "") if group_row else "",
                    "weight": group_row.get("weight", "") if group_row else "",
                    "reward_text": f"{item_name or '未解析'} x{group_row.get('num', '')}" if group_row else f"未解析奖励组 {group_id}",
                    "reward_show_title": _plain(row, "rewardShowTitle"),
                    "push_reward_preview": push_preview,
                }
            )

    reward_group_count = _write_tsv(
        output_dir / "hot_update_blld_reward_groups.tsv",
        [
            "reward_group_id",
            "item_id",
            "item_name",
            "num",
            "limit",
            "quality",
            "weight",
            "bag",
            "item_quality",
            "icon",
            "descript",
        ],
        reward_group_rows,
    )
    level_reward_count = _write_tsv(
        output_dir / "hot_update_blld_level_rewards.tsv",
        [
            "level_id",
            "level_name",
            "stage",
            "layer",
            "reward_kind",
            "reward_group_id",
            "item_id",
            "item_name",
            "num",
            "limit",
            "quality",
            "weight",
            "reward_text",
            "reward_show_title",
            "push_reward_preview",
        ],
        level_reward_rows,
    )

    result = {
        "export_root": str(export_base),
        "output_dir": str(output_dir),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "inputs": {
            "lang_path": str(lang_path or ""),
            "blld_dir": str(blld_dir),
            "item_dir": str(item_dir),
        },
        "counts": {
            "reward_groups": reward_group_count,
            "level_rewards": level_reward_count,
            "levels": level["row_count"],
        },
        "outputs": {
            "summary": str(output_dir / "hot_update_blld_reward_catalog_report.json"),
            "markdown": str(output_dir / "hot_update_blld_reward_catalog_report.md"),
            "reward_groups": str(output_dir / "hot_update_blld_reward_groups.tsv"),
            "level_rewards": str(output_dir / "hot_update_blld_level_rewards.tsv"),
        },
    }
    _write_blld_reward_catalog_markdown(
        output_dir / "hot_update_blld_reward_catalog_report.md",
        export_base=export_base,
        reward_group_rows=reward_group_rows,
        level_reward_rows=level_reward_rows,
    )
    (output_dir / "hot_update_blld_reward_catalog_report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


def _compact_json(value: object, *, limit: int = 220) -> str:
    if value in (None, "", [], {}):
        return ""
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    else:
        text = str(value)
    return text if len(text) <= limit else f"{text[:limit]}..."


def _numeric_values(rows: Iterable[dict[str, Any]], field: str) -> list[float]:
    values: list[float] = []
    for row in rows:
        try:
            values.append(float(row.get(field)))
        except (TypeError, ValueError):
            continue
    return values


def _range_text(values: Iterable[float]) -> str:
    items = list(values)
    if not items:
        return ""
    low = min(items)
    high = max(items)
    if low == high:
        return str(int(low)) if low.is_integer() else str(low)
    low_text = str(int(low)) if low.is_integer() else str(low)
    high_text = str(int(high)) if high.is_integer() else str(high)
    return f"{low_text}..{high_text}"


def _blld_effect_summary(effect_row: dict[str, Any]) -> str:
    fields = [
        "skill",
        "buffId",
        "targetBuffId",
        "extCd",
        "extDamage",
        "extReleaseCount",
        "criticalRate",
        "replaceSkill",
        "extDuration",
        "extBoomRange",
        "extSeekRange",
        "buffDamageRate",
        "addAttr",
    ]
    parts: list[str] = []
    for field in fields:
        value = effect_row.get(field)
        if value in (None, "", [], {}, 0):
            continue
        parts.append(f"{field}={_compact_json(value, limit=80)}")
    return "; ".join(parts)


_BLLD_ATTR_LABELS = {
    "FIRE_INTERVAL_RATE": "射出间隔缩短",
    "SKILL_CD_RATE": "技能冷却缩短",
    "SKILL_DAMAGE_RATE": "技能伤害增加",
    "EXT_RELEASE_COUNT": "额外释放数",
    "CRI_ADDDAMAGE": "暴击伤害增加",
    "CRI_RATE": "暴击率增加",
    "ATTACH_TRIGGER_COUNT_ADD": "附加触发次数增加",
    "SIDE_COUNT_ADD": "并排数量增加",
    "EXT_DURATION": "持续时间增加",
    "EXT_BOOM_RANGE": "范围增加",
    "EXT_SEEK_RANGE": "索敌范围增加",
    "EXT_BURN_CHANCE": "燃烧概率增加",
    "TARGET_BUFF_DURATION_ADD": "目标 Buff 持续增加",
    "CLONE_FAQI": "复制法器",
    "EXT_PARALYSIS_CHANCE": "麻痹概率增加",
    "EXT_REPEAT_CHANCE": "重复触发概率增加",
    "DAMAGE_STACK_RATE": "伤害叠加比例",
    "EXT_REFRACT_COUNT": "折射次数增加",
    "CHANGE_MOVE_SPEED": "移动速度改变",
    "POISON_HURT_PERCENT": "中毒伤害比例",
    "HIT_BACK_ADD_DAMAGE_RATE": "击退伤害比例",
    "EXT_DURATION_STOP_EFF_MOVE": "停留特效持续增加",
}


_BLLD_PERCENT_ATTRS = {
    "FIRE_INTERVAL_RATE",
    "SKILL_CD_RATE",
    "SKILL_DAMAGE_RATE",
    "CRI_ADDDAMAGE",
    "CRI_RATE",
    "EXT_BOOM_RANGE",
    "EXT_SEEK_RANGE",
    "EXT_BURN_CHANCE",
    "EXT_PARALYSIS_CHANCE",
    "EXT_REPEAT_CHANCE",
    "DAMAGE_STACK_RATE",
    "CHANGE_MOVE_SPEED",
    "POISON_HURT_PERCENT",
    "HIT_BACK_ADD_DAMAGE_RATE",
}


_BLLD_STORED_VALUE_ATTRS = {
    "EXT_RELEASE_COUNT",
    "ATTACH_TRIGGER_COUNT_ADD",
    "SIDE_COUNT_ADD",
    "EXT_REFRACT_COUNT",
    "EXT_DURATION",
    "TARGET_BUFF_DURATION_ADD",
    "EXT_DURATION_STOP_EFF_MOVE",
}


def _format_blld_add_attr(add_attr: object) -> str:
    text = str(add_attr or "")
    match = re.match(r"""([^:]+):(-?\d+(?:\.\d+)?)(?:_(\d+))?""", text)
    if not match:
        return text
    key = match.group(1)
    raw_text = match.group(2)
    buff_id = match.group(3)
    try:
        raw_value = float(raw_text)
    except ValueError:
        return text
    stored = raw_value * 0.0001
    label = _BLLD_ATTR_LABELS.get(key, key)
    if key in _BLLD_PERCENT_ATTRS:
        value_text = f"{raw_value / 100:g}%"
    elif key in _BLLD_STORED_VALUE_ATTRS:
        value_text = f"{stored:g}"
    elif raw_value.is_integer():
        value_text = str(int(raw_value))
    else:
        value_text = str(raw_value)
    suffix = f"，关联 Buff {buff_id}" if buff_id else ""
    return f"{label} {value_text}（{key}={raw_text}，stored={stored:g}{suffix}）"


def _blld_buff_effect_summary(buff_row: dict[str, Any]) -> str:
    if not buff_row:
        return ""
    parts: list[str] = []
    if buff_row.get("addAttr"):
        parts.append(_format_blld_add_attr(buff_row.get("addAttr")))
    for field in [
        "buffDamageRate",
        "slowDown",
        "injuryedValue",
        "shield",
        "decreaseCd",
        "triggerBuffId",
        "buffAmplify",
        "ignoreReduce",
        "boomRange",
        "killAddBuffId",
        "buffEndSkillId",
        "targetCount",
    ]:
        value = buff_row.get(field)
        if value not in (None, "", [], {}, 0):
            parts.append(f"{field}={_compact_json(value, limit=80)}")
    if buff_row.get("duration") not in (None, "", -1):
        parts.append(f"duration={buff_row.get('duration')}")
    if buff_row.get("interval") not in (None, "", 0):
        parts.append(f"interval={buff_row.get('interval')}")
    return "; ".join(parts)


def _write_blld_combat_mechanics_markdown(
    path: Path,
    *,
    export_base: Path,
    blld_cfg_dir: Path,
    blld_game_dir: Path,
    faqi_rows: list[dict[str, object]],
    monster_rows: list[dict[str, object]],
    enhance_rows: list[dict[str, object]],
    evidence_rows: list[dict[str, object]],
) -> None:
    by_category = Counter(str(row["category"]) for row in evidence_rows)

    lines = [
        "# BLLD 战斗机制探针",
        "",
        f"- 导出目录：`{export_base}`",
        f"- BLLD 配置：`{blld_cfg_dir}`",
        f"- BLLD Lua：`{blld_game_dir}`",
        f"- 法器技能：{len(faqi_rows)}；怪物波次聚合：{len(monster_rows)}；强化项：{len(enhance_rows)}；证据行：{len(evidence_rows)}",
        "- 说明：本报告只做静态还原，用于理解客户端本地小玩法逻辑；最终结算仍要以服务端回包为准。",
        "",
        "## 静态结论",
        "",
        "- 法器基础技能来自 `FaQI.defaultSkill -> CharacterSkillInfo.id`，技能冷却和攻击系数会被 `FaQiLevel.attr.SKILL_CD / FAQI_ATTACK_RATE` 覆盖。",
        "- 人物基础属性来自 `CharacterLevel.attr`，常见字段包括 `ATTACK / MAXHP / CRI_RATE / CRI_ADDDAMAGE`。",
        "- 怪物刷新由 `Level.layer` 或关卡组关联到 `MonsterRefreshPoint.group`，怪物血量和攻击来自刷新点配置，血月系数会放大 `Attack/MAXHP`。",
        "- `SkillEnhance -> SkillEnhanceEffect -> BuffEffect/BLLDBuffAddAttr` 会继续修改技能数据，例如冷却、伤害、暴击、弹体数量、持续时间和范围。",
        "- `BLLDFightComponent:AddDamageResult` 里能看到本地伤害计算：基础攻击、法器攻击系数、伤害类型抗性、增伤、技能加成、暴击和随机浮动会共同进入最终伤害。",
        "",
        "## 法器与默认技能",
        "",
        "| 法器 | 名称 | 伤害类型 | 默认技能 | 技能组 | CD | 范围 | 间隔 | 弹体 | 1级属性 | 最高级属性 | 描述 |",
        "| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | --- |",
    ]
    for row in faqi_rows[:80]:
        lines.append(
            "| "
            f"{row.get('faqi_id', '')} | "
            f"{_markdown_table_cell(row.get('faqi_name', ''), limit=120)} | "
            f"{row.get('damage_type', '')} | "
            f"{row.get('default_skill', '')} | "
            f"{row.get('skill_group', '')} | "
            f"{row.get('cd', '')} | "
            f"{row.get('range', '')} | "
            f"{row.get('interval', '')} | "
            f"{row.get('bullet_count', '')} | "
            f"{_markdown_table_cell(row.get('level1_attr', ''), limit=160)} | "
            f"{_markdown_table_cell(row.get('max_attr', ''), limit=160)} | "
            f"{_markdown_table_cell(row.get('skill_des', ''), limit=180)} |"
        )

    lines.extend(
        [
            "",
            "## 怪物波次聚合",
            "",
            "| 组 | 类型 | 行数 | 刷新总数 | 波次时间 | 刷新间隔 | 怪物 | 攻击 | 终伤 | 血量 | 关卡样例 |",
            "| ---: | ---: | ---: | ---: | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in monster_rows[:120]:
        lines.append(
            "| "
            f"{row.get('group', '')} | "
            f"{row.get('type', '')} | "
            f"{row.get('row_count', '')} | "
            f"{row.get('refresh_total_num', '')} | "
            f"{row.get('wave_time_range', '')} | "
            f"{row.get('refresh_time_range', '')} | "
            f"{_markdown_table_cell(row.get('monster_names', ''), limit=160)} | "
            f"{row.get('attack_range', '')} | "
            f"{row.get('final_attack_range', '')} | "
            f"{row.get('maxhp_range', '')} | "
            f"{_markdown_table_cell(row.get('levels', ''), limit=160)} |"
        )

    lines.extend(
        [
            "",
            "## 技能强化样例",
            "",
        "| ID | 法器 | 名称 | 品质 | 类型 | 描述 | 效果 | Buff 实际属性 |",
        "| ---: | ---: | --- | ---: | ---: | --- | --- | --- |",
        ]
    )
    for row in enhance_rows[:120]:
        lines.append(
            "| "
            f"{row.get('enhance_id', '')} | "
            f"{row.get('faqi_id', '')} | "
            f"{_markdown_table_cell(row.get('name', ''), limit=140)} | "
            f"{row.get('quality', '')} | "
            f"{row.get('type', '')} | "
            f"{_markdown_table_cell(row.get('des', ''), limit=180)} | "
            f"{_markdown_table_cell(row.get('effect_summary', ''), limit=220)} | "
            f"{_markdown_table_cell(row.get('buff_summary', ''), limit=260)} |"
        )

    lines.extend(
        [
            "",
            "## Lua 证据",
            "",
            f"- 分组：{', '.join(f'{name}:{count}' for name, count in by_category.most_common())}",
            "",
            "| 分组 | 文件 | 行 | 信号 | 代码 |",
            "| --- | --- | ---: | --- | --- |",
        ]
    )
    for row in evidence_rows[:180]:
        lines.append(
            "| "
            f"{_markdown_table_cell(row.get('category', ''), limit=100)} | "
            f"{_markdown_table_cell(row.get('file', ''), limit=120)} | "
            f"{row.get('line', '')} | "
            f"{_markdown_table_cell(row.get('signal', ''), limit=120)} | "
            f"{_markdown_table_cell(row.get('text', ''), limit=300)} |"
        )

    path.write_text("\n".join(lines), encoding="utf-8")


def build_fanxiu_blld_combat_mechanics_probe(
    *,
    export_root: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    export_base = resolve_fanxiu_export_root(export_root)
    output_dir = export_base / "apk_static_index"
    output_dir.mkdir(parents=True, exist_ok=True)
    lang_path = _find_lang_path(export_base)
    blld_cfg_dir = _find_cfg_text_asset_dir(export_base, "blld")
    blld_game_dir = _find_text_asset_dir(export_base, "lscripts", "gamesystem", "game", module="blld")
    if blld_cfg_dir is None:
        raise FanxiuResourceError("未找到已导出的 BLLD 配置 TextAsset，请先运行热更新 lscripts 报告。")
    if blld_game_dir is None:
        raise FanxiuResourceError("未找到已导出的 BLLD 游戏逻辑 TextAsset，请先运行热更新 lscripts 报告。")

    tables = {
        "FaQI": _parse_config(blld_cfg_dir / "FaQI.lua", lang_path),
        "CharacterSkillInfo": _parse_config(blld_cfg_dir / "CharacterSkillInfo.lua", lang_path),
        "FaQiLevel": _parse_config(blld_cfg_dir / "FaQiLevel.lua", lang_path),
        "CharacterLevel": _parse_config(blld_cfg_dir / "CharacterLevel.lua", lang_path),
        "MonsterInfo": _parse_config(blld_cfg_dir / "MonsterInfo.lua", lang_path),
        "MonsterRefreshPoint": _parse_config(blld_cfg_dir / "MonsterRefreshPoint.lua", lang_path),
        "Level": _parse_config(blld_cfg_dir / "Level.lua", lang_path),
        "SkillEnhance": _parse_config(blld_cfg_dir / "SkillEnhance.lua", lang_path),
        "SkillEnhanceEffect": _parse_config(blld_cfg_dir / "SkillEnhanceEffect.lua", lang_path),
        "BuffEffect": _parse_config(blld_cfg_dir / "BuffEffect.lua", lang_path),
        "BloodMoon": _parse_config(blld_cfg_dir / "BloodMoon.lua", lang_path),
    }

    table_rows = [
        {
            "table": name,
            "row_count": data["row_count"],
            "field_count": len(data["fields"]),
            "fields": " | ".join(str(field) for field in data["fields"][:30]),
            "path": data.get("source_path", ""),
        }
        for name, data in tables.items()
    ]
    table_count = _write_tsv(
        output_dir / "hot_update_blld_combat_config_tables.tsv",
        ["table", "row_count", "field_count", "fields", "path"],
        table_rows,
    )

    skills_by_id = _map_by_id(tables["CharacterSkillInfo"]["rows"])
    levels_by_faqi: dict[int, list[dict[str, Any]]] = {}
    for row in tables["FaQiLevel"]["rows"]:
        try:
            levels_by_faqi.setdefault(int(row.get("faqiId")), []).append(row)
        except (TypeError, ValueError):
            continue
    for rows in levels_by_faqi.values():
        rows.sort(key=lambda item: int(item.get("level") or 0))

    faqi_rows: list[dict[str, object]] = []
    for faqi in tables["FaQI"]["rows"]:
        try:
            faqi_id = int(faqi.get("id"))
        except (TypeError, ValueError):
            continue
        default_skill = faqi.get("defaultSkill", "")
        try:
            skill = skills_by_id.get(int(default_skill), {})
        except (TypeError, ValueError):
            skill = {}
        level_rows = levels_by_faqi.get(faqi_id, [])
        level1 = level_rows[0] if level_rows else {}
        max_level = level_rows[-1] if level_rows else {}
        faqi_rows.append(
            {
                "faqi_id": faqi_id,
                "faqi_name": _plain(faqi, "name"),
                "damage_type": faqi.get("damageType", ""),
                "default_skill": default_skill,
                "skill_group": skill.get("skillGroup", ""),
                "skill_type": skill.get("skillType", ""),
                "cd": skill.get("cd", ""),
                "range": skill.get("range", ""),
                "interval": skill.get("interval", ""),
                "bullet_count": skill.get("bulletCount", ""),
                "fire_interval": skill.get("fireInterval", ""),
                "target_buff_id": _compact_json(skill.get("targetBuffId"), limit=160),
                "level1_attr": _compact_json(level1.get("attr"), limit=220),
                "max_level": max_level.get("level", ""),
                "max_attr": _compact_json(max_level.get("attr"), limit=220),
                "skill_des": _plain(faqi, "skillDes"),
                "unlock_desc": _plain(faqi, "unLockDesc"),
            }
        )
    faqi_count = _write_tsv(
        output_dir / "hot_update_blld_faqi_skills.tsv",
        [
            "faqi_id",
            "faqi_name",
            "damage_type",
            "default_skill",
            "skill_group",
            "skill_type",
            "cd",
            "range",
            "interval",
            "bullet_count",
            "fire_interval",
            "target_buff_id",
            "level1_attr",
            "max_level",
            "max_attr",
            "skill_des",
            "unlock_desc",
        ],
        faqi_rows,
    )

    monster_by_id = _map_by_id(tables["MonsterInfo"]["rows"])
    levels_by_monster_group: dict[str, list[str]] = {}
    for row in tables["Level"]["rows"]:
        group = str(row.get("monsterGroup") or row.get("layer") or row.get("group") or "")
        if not group:
            continue
        levels_by_monster_group.setdefault(group, []).append(f"{row.get('id', '')}:{_plain(row, 'name')}")

    grouped_refresh: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in tables["MonsterRefreshPoint"]["rows"]:
        key = (str(row.get("group", "")), str(row.get("type", "")))
        grouped_refresh.setdefault(key, []).append(row)

    monster_rows: list[dict[str, object]] = []
    for (group, refresh_type), rows in sorted(grouped_refresh.items(), key=lambda item: (int(item[0][0] or 0), int(item[0][1] or 0))):
        monster_names = []
        monster_ids = []
        for row in rows:
            monster_id = row.get("monsterId", "")
            monster_ids.append(monster_id)
            try:
                monster = monster_by_id.get(int(monster_id), {})
            except (TypeError, ValueError):
                monster = {}
            if monster:
                monster_names.append(f"{monster_id}:{_plain(monster, 'name')}")
            elif monster_id:
                monster_names.append(str(monster_id))
        refresh_total = sum(int(value) for value in _numeric_values(rows, "refreshTotalNum"))
        kill_gold_total = sum(int(value) for value in _numeric_values(rows, "killGold"))
        monster_rows.append(
            {
                "group": group,
                "type": refresh_type,
                "row_count": len(rows),
                "refresh_total_num": refresh_total,
                "kill_gold_total": kill_gold_total,
                "wave_time_range": _range_text(_numeric_values(rows, "waveTime")),
                "refresh_time_range": _range_text(_numeric_values(rows, "refreshTime")),
                "refresh_num_range": _range_text(_numeric_values(rows, "refreshNum")),
                "monster_ids": _unique_join(monster_ids, limit=30),
                "monster_names": _unique_join(monster_names, limit=30),
                "attack_range": _range_text(_numeric_values(rows, "Attack")),
                "final_attack_range": _range_text(_numeric_values(rows, "finalAttack")),
                "maxhp_range": _range_text(_numeric_values(rows, "MAXHP")),
                "plus_lv_range": _range_text(_numeric_values(rows, "plusLv")),
                "levels": _unique_join(levels_by_monster_group.get(group, []), limit=16),
            }
        )
    monster_count = _write_tsv(
        output_dir / "hot_update_blld_monster_waves.tsv",
        [
            "group",
            "type",
            "row_count",
            "refresh_total_num",
            "kill_gold_total",
            "wave_time_range",
            "refresh_time_range",
            "refresh_num_range",
            "monster_ids",
            "monster_names",
            "attack_range",
            "final_attack_range",
            "maxhp_range",
            "plus_lv_range",
            "levels",
        ],
        monster_rows,
    )

    effect_by_id = _map_by_id(tables["SkillEnhanceEffect"]["rows"])
    buff_by_id = _map_by_id(tables["BuffEffect"]["rows"])
    enhance_rows: list[dict[str, object]] = []
    for row in tables["SkillEnhance"]["rows"]:
        effect_tokens = row.get("effectId")
        if not isinstance(effect_tokens, list):
            effect_tokens = [effect_tokens] if effect_tokens not in (None, "") else []
        effect_ids: list[int] = []
        effect_summaries: list[str] = []
        buff_summaries: list[str] = []
        for token in effect_tokens:
            try:
                effect_id = int(token)
            except (TypeError, ValueError):
                continue
            effect_ids.append(effect_id)
            effect_row = effect_by_id.get(effect_id, {})
            summary = _blld_effect_summary(effect_row)
            if summary:
                effect_summaries.append(f"{effect_id}:{summary}")
            buff_tokens = effect_row.get("buffId")
            if not isinstance(buff_tokens, list):
                buff_tokens = [buff_tokens] if buff_tokens not in (None, "") else []
            for buff_token in buff_tokens:
                try:
                    buff_id = int(buff_token)
                except (TypeError, ValueError):
                    continue
                buff_summary = _blld_buff_effect_summary(buff_by_id.get(buff_id, {}))
                if buff_summary:
                    buff_summaries.append(f"{buff_id}:{buff_summary}")
        enhance_rows.append(
            {
                "enhance_id": row.get("id", ""),
                "faqi_id": row.get("faqiId", ""),
                "name": _plain(row, "name"),
                "type": row.get("type", ""),
                "quality": row.get("quality", ""),
                "time": row.get("time", ""),
                "condition": row.get("condition", ""),
                "limit": row.get("limit", ""),
                "weight": row.get("weight", ""),
                "des": _plain(row, "des"),
                "effect_ids": _unique_join(effect_ids, limit=20),
                "effect_summary": _unique_join(effect_summaries, limit=8),
                "buff_summary": _unique_join(buff_summaries, limit=8),
            }
        )
    enhance_count = _write_tsv(
        output_dir / "hot_update_blld_enhance_effects.tsv",
        [
            "enhance_id",
            "faqi_id",
            "name",
            "type",
            "quality",
            "time",
            "condition",
            "limit",
            "weight",
            "des",
            "effect_ids",
            "effect_summary",
            "buff_summary",
        ],
        enhance_rows,
    )

    character_attr_rows = [
        {
            "group": row.get("group", ""),
            "level": row.get("level", ""),
            "cost": _compact_json(row.get("cost"), limit=180),
            "attr": _compact_json(row.get("attr"), limit=240),
        }
        for row in tables["CharacterLevel"]["rows"]
    ]
    character_attr_count = _write_tsv(
        output_dir / "hot_update_blld_character_attrs.tsv",
        ["group", "level", "cost", "attr"],
        character_attr_rows,
    )

    buff_effect_rows = [
        {
            "id": row.get("id", ""),
            "type": row.get("type", ""),
            "trigger_type": row.get("triggerType", ""),
            "duration": row.get("duration", ""),
            "interval": row.get("interval", ""),
            "summary": _blld_buff_effect_summary(row),
        }
        for row in tables["BuffEffect"]["rows"]
    ]
    buff_effect_count = _write_tsv(
        output_dir / "hot_update_blld_buff_effects.tsv",
        ["id", "type", "trigger_type", "duration", "interval", "summary"],
        buff_effect_rows,
    )

    categories = {
        "damage_formula": [
            "finalAttack=",
            "finalDamage=",
            "GetMonsterReduceDamage",
            "GetIncreaseDamage",
            "GetCriAddDamage",
            "BLLDHurtDataExecute",
            "Faqi_Damage",
        ],
        "skill_data": [
            "FAQI_ATTACK_RATE",
            "SKILL_CD",
            "ModifyData",
            "GetDamageRate",
            "GetCriRate",
            "GetFireInterval",
        ],
        "monster_spawn": [
            "InitMonsterRefresh",
            "UpdateType1Monster",
            "UpdateType2Monster",
            "SpawnMonster",
            "MAXHP=cfg.MAXHP*coeff",
            "refreshTime",
            "waveTime",
        ],
        "player_hp": [
            "InitPlayerAttr",
            "SetCurHp",
            "GetCurHp",
            "AddPlayerDamage",
            "GameOver,false",
        ],
        "buff_modify": [
            "GetAddExtAttr",
            "targetSkill.skillData:ModifyData",
            "addAttr",
            "BLLDBuffAddAttr",
        ],
        "blood_moon": [
            "GetBloodMoonCoeff",
            "AddBloodMoonLevel",
            "BloodMoon",
        ],
    }
    evidence_rows = _collect_blld_keyword_rows(blld_game_dir, categories)
    evidence_count = _write_tsv(
        output_dir / "hot_update_blld_combat_formula_evidence.tsv",
        ["category", "file", "line", "signal", "text", "path"],
        evidence_rows,
    )

    result = {
        "export_root": str(export_base),
        "output_dir": str(output_dir),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "inputs": {
            "lang_path": str(lang_path or ""),
            "blld_cfg_dir": str(blld_cfg_dir),
            "blld_game_dir": str(blld_game_dir),
        },
        "counts": {
            "config_tables": table_count,
            "faqi_skills": faqi_count,
            "monster_wave_groups": monster_count,
            "enhance_effects": enhance_count,
            "character_attrs": character_attr_count,
            "buff_effects": buff_effect_count,
            "formula_evidence": evidence_count,
            "by_evidence_category": dict(Counter(str(row["category"]) for row in evidence_rows).most_common()),
        },
        "outputs": {
            "summary": str(output_dir / "hot_update_blld_combat_mechanics_report.json"),
            "markdown": str(output_dir / "hot_update_blld_combat_mechanics_report.md"),
            "config_tables": str(output_dir / "hot_update_blld_combat_config_tables.tsv"),
            "faqi_skills": str(output_dir / "hot_update_blld_faqi_skills.tsv"),
            "monster_waves": str(output_dir / "hot_update_blld_monster_waves.tsv"),
            "enhance_effects": str(output_dir / "hot_update_blld_enhance_effects.tsv"),
            "character_attrs": str(output_dir / "hot_update_blld_character_attrs.tsv"),
            "buff_effects": str(output_dir / "hot_update_blld_buff_effects.tsv"),
            "formula_evidence": str(output_dir / "hot_update_blld_combat_formula_evidence.tsv"),
        },
    }
    _write_blld_combat_mechanics_markdown(
        output_dir / "hot_update_blld_combat_mechanics_report.md",
        export_base=export_base,
        blld_cfg_dir=blld_cfg_dir,
        blld_game_dir=blld_game_dir,
        faqi_rows=faqi_rows,
        monster_rows=monster_rows,
        enhance_rows=enhance_rows,
        evidence_rows=evidence_rows,
    )
    (output_dir / "hot_update_blld_combat_mechanics_report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


def _blld_push_reward_preview(tokens: Iterable[object], item_by_id: dict[int, dict[str, Any]], *, limit: int = 8) -> str:
    parts: list[str] = []
    for token in tokens:
        item_id, count, raw = _parse_item_reward_token(token)
        name = _item_name(item_by_id, item_id)
        if name:
            parts.append(f"{name}x{count or 1}")
        else:
            parts.append(raw)
        if len(parts) >= limit:
            break
    return " | ".join(parts)


def _blld_find_reward_preview(
    group_tokens: Iterable[object],
    reward_group_by_id: dict[int, dict[str, Any]],
    item_by_id: dict[int, dict[str, Any]],
    *,
    limit: int = 10,
) -> str:
    parts: list[str] = []
    for token in group_tokens:
        try:
            group_id = int(token)
        except (TypeError, ValueError):
            parts.append(str(token))
            continue
        group_row = reward_group_by_id.get(group_id, {})
        if not group_row:
            parts.append(f"奖励组{group_id}")
        else:
            item_id = int(group_row.get("item") or 0)
            name = _item_name(item_by_id, item_id)
            parts.append(f"{group_id}:{name or item_id}x{group_row.get('num', '')}")
        if len(parts) >= limit:
            break
    return " | ".join(parts)


def _blld_monster_group_summary(
    refresh_rows: list[dict[str, Any]],
    monster_by_id: dict[int, dict[str, Any]],
) -> dict[str, object]:
    monster_names: list[str] = []
    boss_names: list[str] = []
    refresh_total = 0
    kill_gold_total = 0
    types = Counter()
    for row in refresh_rows:
        types[str(row.get("type", ""))] += 1
        refresh_total += int(row.get("refreshTotalNum") or 0)
        kill_gold_total += int(row.get("killGold") or 0)
        monster_id = row.get("monsterId", "")
        try:
            monster = monster_by_id.get(int(monster_id), {})
        except (TypeError, ValueError):
            monster = {}
        name = f"{monster_id}:{_plain(monster, 'name')}" if monster else str(monster_id)
        monster_names.append(name)
        if str(row.get("type", "")) in {"3", "4"}:
            boss_names.append(name)
    return {
        "monster_refresh_rows": len(refresh_rows),
        "monster_types": ", ".join(f"{name}:{count}" for name, count in sorted(types.items())),
        "refresh_total_num": refresh_total,
        "kill_gold_total": kill_gold_total,
        "wave_time_range": _range_text(_numeric_values(refresh_rows, "waveTime")),
        "refresh_time_range": _range_text(_numeric_values(refresh_rows, "refreshTime")),
        "attack_range": _range_text(_numeric_values(refresh_rows, "Attack")),
        "final_attack_range": _range_text(_numeric_values(refresh_rows, "finalAttack")),
        "maxhp_range": _range_text(_numeric_values(refresh_rows, "MAXHP")),
        "plus_lv_range": _range_text(_numeric_values(refresh_rows, "plusLv")),
        "monster_names": _unique_join(monster_names, limit=20),
        "boss_names": _unique_join(boss_names, limit=12),
    }


def _write_blld_level_catalog_markdown(
    path: Path,
    *,
    export_base: Path,
    level_rows: list[dict[str, object]],
    stage_rows: list[dict[str, object]],
) -> None:
    lines = [
        "# BLLD 关卡图谱探针",
        "",
        f"- 导出目录：`{export_base}`",
        f"- 关卡：{len(level_rows)}；阶段聚合：{len(stage_rows)}",
        "- 说明：本报告把 `Level / RewardGroup / Item / MonsterRefreshPoint / MonsterInfo` 按关卡合并，便于查某关的奖励和怪物配置。",
        "",
        "## 阶段汇总",
        "",
        "| 阶段 | 关卡数 | 关卡范围 | 攻击 | 血量 | 固定奖励标题样例 | 探索奖励样例 |",
        "| ---: | ---: | --- | --- | --- | --- | --- |",
    ]
    for row in stage_rows:
        lines.append(
            "| "
            f"{row.get('stage', '')} | "
            f"{row.get('level_count', '')} | "
            f"{row.get('level_range', '')} | "
            f"{row.get('attack_range', '')} | "
            f"{row.get('maxhp_range', '')} | "
            f"{_markdown_table_cell(row.get('reward_titles', ''), limit=180)} | "
            f"{_markdown_table_cell(row.get('find_rewards', ''), limit=220)} |"
        )

    lines.extend(
        [
            "",
            "## 关卡样例",
            "",
            "| 关卡 | 名称 | 阶段 | 推荐 | 跳过 | 固定奖励 | 探索奖励 | 怪物 | Boss/事件 | 血量 |",
            "| ---: | --- | ---: | --- | ---: | --- | --- | --- | --- | --- |",
        ]
    )
    for row in level_rows[:120]:
        lines.append(
            "| "
            f"{row.get('level_id', '')} | "
            f"{_markdown_table_cell(row.get('name', ''), limit=80)} | "
            f"{row.get('stage', '')} | "
            f"{_markdown_table_cell(row.get('recommend_tips', ''), limit=140)} | "
            f"{row.get('allow_skip_level', '')} | "
            f"{_markdown_table_cell(row.get('push_reward_preview', ''), limit=200)} | "
            f"{_markdown_table_cell(row.get('find_reward_preview', ''), limit=240)} | "
            f"{_markdown_table_cell(row.get('monster_names', ''), limit=180)} | "
            f"{_markdown_table_cell(row.get('boss_names', ''), limit=160)} | "
            f"{row.get('maxhp_range', '')} |"
        )

    path.write_text("\n".join(lines), encoding="utf-8")


def build_fanxiu_blld_level_catalog_probe(
    *,
    export_root: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    export_base = resolve_fanxiu_export_root(export_root)
    output_dir = export_base / "apk_static_index"
    output_dir.mkdir(parents=True, exist_ok=True)
    lang_path = _find_lang_path(export_base)
    blld_dir = _find_cfg_text_asset_dir(export_base, "blld")
    item_dir = _find_cfg_text_asset_dir(export_base, "item")
    if blld_dir is None:
        raise FanxiuResourceError("未找到已导出的 BLLD 配置 TextAsset，请先运行热更新 lscripts 报告。")

    level = _parse_config(blld_dir / "Level.lua", lang_path)
    reward_group = _parse_config(blld_dir / "RewardGroup.lua", lang_path)
    monster_info = _parse_config(blld_dir / "MonsterInfo.lua", lang_path)
    monster_refresh = _parse_config(blld_dir / "MonsterRefreshPoint.lua", lang_path)
    activity_base = _parse_config(blld_dir / "ActivityBase.lua", lang_path)
    item = _parse_config((item_dir / "Item.lua") if item_dir else None, lang_path)

    item_by_id = _map_by_id(item["rows"])
    reward_group_by_id = _map_by_id(reward_group["rows"])
    monster_by_id = _map_by_id(monster_info["rows"])

    refresh_by_group: dict[str, list[dict[str, Any]]] = {}
    for row in monster_refresh["rows"]:
        refresh_by_group.setdefault(str(row.get("group", "")), []).append(row)

    activity_by_level_group: dict[str, list[str]] = {}
    for row in activity_base["rows"]:
        group = str(row.get("levelGroup", ""))
        activity_by_level_group.setdefault(group, []).append(str(row.get("activityId", "")))

    level_rows: list[dict[str, object]] = []
    reward_detail_rows: list[dict[str, object]] = []
    for row in level["rows"]:
        level_id = row.get("id", "")
        monster_group = str(row.get("monsterGroup") or row.get("layer") or row.get("group") or "")
        monster_summary = _blld_monster_group_summary(refresh_by_group.get(monster_group, []), monster_by_id)
        push_tokens = row.get("pushReward") or []
        find_tokens = row.get("findReward") or []
        push_preview = _blld_push_reward_preview(push_tokens, item_by_id)
        find_preview = _blld_find_reward_preview(find_tokens, reward_group_by_id, item_by_id)

        for token in push_tokens:
            item_id, count, raw = _parse_item_reward_token(token)
            reward_detail_rows.append(
                {
                    "level_id": level_id,
                    "reward_kind": "push",
                    "source": raw,
                    "reward_group_id": "",
                    "item_id": item_id or "",
                    "item_name": _item_name(item_by_id, item_id),
                    "count": count or "",
                    "quality": "",
                    "limit": "",
                    "weight": "",
                }
            )
        for token in find_tokens:
            try:
                reward_group_id = int(token)
            except (TypeError, ValueError):
                reward_group_id = 0
            group_row = reward_group_by_id.get(reward_group_id, {})
            item_id = int(group_row.get("item") or 0) if group_row else None
            reward_detail_rows.append(
                {
                    "level_id": level_id,
                    "reward_kind": "find",
                    "source": token,
                    "reward_group_id": reward_group_id or "",
                    "item_id": item_id or "",
                    "item_name": _item_name(item_by_id, item_id),
                    "count": group_row.get("num", "") if group_row else "",
                    "quality": group_row.get("quality", "") if group_row else "",
                    "limit": group_row.get("limit", "") if group_row else "",
                    "weight": group_row.get("weight", "") if group_row else "",
                }
            )

        level_rows.append(
            {
                "level_id": level_id,
                "name": _plain(row, "name"),
                "group": row.get("group", ""),
                "stage": row.get("stage", ""),
                "layer": row.get("layer", ""),
                "sub_layer": row.get("subLayer", ""),
                "rogue_group": row.get("rogueGroup", ""),
                "monster_group": monster_group,
                "scene_group": row.get("sceneGroup", ""),
                "scene_id": row.get("sceneId", ""),
                "minimum_level": row.get("minimumLevel", ""),
                "allow_skip_level": row.get("allowSkipLevel", ""),
                "activity_ids": _unique_join(activity_by_level_group.get(str(row.get("group", "")), []), limit=10),
                "recommend_tips": _plain(row, "recommendTips"),
                "reward_show_title": _plain(row, "rewardShowTitle"),
                "push_reward_preview": push_preview,
                "find_reward_preview": find_preview,
                **monster_summary,
            }
        )

    stage_groups: dict[str, list[dict[str, object]]] = {}
    for row in level_rows:
        stage_groups.setdefault(str(row.get("stage", "")), []).append(row)
    stage_rows: list[dict[str, object]] = []
    for stage, rows in sorted(stage_groups.items(), key=lambda item: int(item[0] or 0)):
        level_ids = [int(row["level_id"]) for row in rows if str(row.get("level_id", "")).isdigit()]
        attack_values: list[float] = []
        hp_values: list[float] = []
        find_reward_parts: list[str] = []
        for row in rows:
            for field, target in [("attack_range", attack_values), ("maxhp_range", hp_values)]:
                text = str(row.get(field, ""))
                if ".." in text:
                    left, right = text.split("..", 1)
                    for part in [left, right]:
                        try:
                            target.append(float(part))
                        except ValueError:
                            pass
                else:
                    try:
                        target.append(float(text))
                    except ValueError:
                        pass
            find_reward_parts.extend(part.strip() for part in str(row.get("find_reward_preview", "")).split(" | ") if part.strip())
        stage_rows.append(
            {
                "stage": stage,
                "level_count": len(rows),
                "level_range": f"{min(level_ids)}..{max(level_ids)}" if level_ids else "",
                "attack_range": _range_text(attack_values),
                "maxhp_range": _range_text(hp_values),
                "reward_titles": _unique_join((row.get("reward_show_title", "") for row in rows), limit=6),
                "find_rewards": _unique_join(find_reward_parts, limit=14),
            }
        )

    level_count = _write_tsv(
        output_dir / "hot_update_blld_levels.tsv",
        [
            "level_id",
            "name",
            "group",
            "stage",
            "layer",
            "sub_layer",
            "rogue_group",
            "monster_group",
            "scene_group",
            "scene_id",
            "minimum_level",
            "allow_skip_level",
            "activity_ids",
            "recommend_tips",
            "reward_show_title",
            "push_reward_preview",
            "find_reward_preview",
            "monster_refresh_rows",
            "monster_types",
            "refresh_total_num",
            "kill_gold_total",
            "wave_time_range",
            "refresh_time_range",
            "attack_range",
            "final_attack_range",
            "maxhp_range",
            "plus_lv_range",
            "monster_names",
            "boss_names",
        ],
        level_rows,
    )
    reward_detail_count = _write_tsv(
        output_dir / "hot_update_blld_level_reward_items.tsv",
        ["level_id", "reward_kind", "source", "reward_group_id", "item_id", "item_name", "count", "quality", "limit", "weight"],
        reward_detail_rows,
    )
    stage_count = _write_tsv(
        output_dir / "hot_update_blld_stage_summary.tsv",
        ["stage", "level_count", "level_range", "attack_range", "maxhp_range", "reward_titles", "find_rewards"],
        stage_rows,
    )

    result = {
        "export_root": str(export_base),
        "output_dir": str(output_dir),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "inputs": {
            "lang_path": str(lang_path or ""),
            "blld_dir": str(blld_dir),
            "item_dir": str(item_dir or ""),
        },
        "counts": {
            "levels": level_count,
            "reward_items": reward_detail_count,
            "stages": stage_count,
            "monster_refresh_points": monster_refresh["row_count"],
            "reward_groups": reward_group["row_count"],
        },
        "outputs": {
            "summary": str(output_dir / "hot_update_blld_level_catalog_report.json"),
            "markdown": str(output_dir / "hot_update_blld_level_catalog_report.md"),
            "levels": str(output_dir / "hot_update_blld_levels.tsv"),
            "reward_items": str(output_dir / "hot_update_blld_level_reward_items.tsv"),
            "stage_summary": str(output_dir / "hot_update_blld_stage_summary.tsv"),
        },
    }
    _write_blld_level_catalog_markdown(
        output_dir / "hot_update_blld_level_catalog_report.md",
        export_base=export_base,
        level_rows=level_rows,
        stage_rows=stage_rows,
    )
    (output_dir / "hot_update_blld_level_catalog_report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


def _fanxiu_item_token_text(token: object, item_by_id: dict[int, dict[str, Any]]) -> str:
    item_id, count, raw = _parse_item_reward_token(token)
    if item_id is None:
        return str(raw)
    name = _item_name(item_by_id, item_id)
    return f"{name or item_id}x{count or 1}"


def _aggregate_bluestarsea_nodes(rows: list[dict[str, Any]], item_by_id: dict[int, dict[str, Any]]) -> list[dict[str, object]]:
    groups: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in rows:
        key = (str(row.get("faqiId", "")), str(row.get("group", "")), _plain(row, "name"))
        stat = groups.setdefault(
            key,
            {
                "faqi_id": key[0],
                "group": key[1],
                "name": key[2],
                "level_count": 0,
                "min_level": None,
                "max_level": None,
                "costs": [],
                "des_samples": [],
                "attrs": [],
                "fazes": [],
                "skills": [],
                "partners": [],
            },
        )
        stat["level_count"] += 1
        try:
            level = int(row.get("level") or 0)
        except (TypeError, ValueError):
            level = 0
        stat["min_level"] = level if stat["min_level"] is None else min(stat["min_level"], level)
        stat["max_level"] = level if stat["max_level"] is None else max(stat["max_level"], level)
        if row.get("cost"):
            stat["costs"].append(_fanxiu_item_token_text(row.get("cost"), item_by_id))
        if row.get("des") or row.get("des_plain"):
            stat["des_samples"].append(_plain(row, "des").replace("\n", " / "))
        if row.get("attr") not in (None, "", [], {}):
            stat["attrs"].append(_compact_json(row.get("attr"), limit=180))
        for field, target in [("faze", "fazes"), ("skill", "skills"), ("parterner", "partners")]:
            if row.get(field):
                stat[target].append(row.get(field))

    output: list[dict[str, object]] = []
    for stat in groups.values():
        output.append(
            {
                "faqi_id": stat["faqi_id"],
                "group": stat["group"],
                "name": stat["name"],
                "level_count": stat["level_count"],
                "level_range": f"{stat['min_level']}..{stat['max_level']}",
                "costs": _unique_join(stat["costs"], limit=8),
                "des_samples": _unique_join(stat["des_samples"], limit=4),
                "attr_samples": _unique_join(stat["attrs"], limit=4),
                "fazes": _unique_join(stat["fazes"], limit=8),
                "skills": _unique_join(stat["skills"], limit=8),
                "partners": _unique_join(stat["partners"], limit=8),
            }
        )
    output.sort(key=lambda item: (int(str(item["faqi_id"]) or 0), int(str(item["group"]) or 0), str(item["name"])))
    return output


def _write_bluestarsea_catalog_markdown(
    path: Path,
    *,
    export_base: Path,
    base_rows: list[dict[str, object]],
    tree_rows: list[dict[str, object]],
    star_rows: list[dict[str, object]],
    startree_rows: list[dict[str, object]],
    wake_rows: list[dict[str, object]],
    evidence_rows: list[dict[str, object]],
) -> None:
    by_category = Counter(str(row["category"]) for row in evidence_rows)
    lines = [
        "# BlueStarSea 蓝色星海图鉴探针",
        "",
        f"- 导出目录：`{export_base}`",
        f"- 分区：{len(base_rows)}；悟道树节点组：{len(tree_rows)}；吞噬进化：{len(star_rows)}；星图来源：{len(startree_rows)}；觉醒：{len(wake_rows)}；Lua 证据：{len(evidence_rows)}",
        "- 说明：本报告聚焦配置和客户端 UI/Model 链路，用于把蓝色星海内容整理成可检索图鉴。",
        "",
        "## 分区",
        "",
        "| ID | 名称 | 排序 | 界面 | 开启条件 | 未开启文案 |",
        "| ---: | --- | ---: | ---: | --- | --- |",
    ]
    for row in base_rows:
        lines.append(
            "| "
            f"{row.get('id', '')} | "
            f"{_markdown_table_cell(row.get('name', ''), limit=100)} | "
            f"{row.get('sort', '')} | "
            f"{row.get('interface', '')} | "
            f"{_markdown_table_cell(row.get('opencondition', ''), limit=180)} | "
            f"{_markdown_table_cell(row.get('openlan', ''), limit=120)} |"
        )

    lines.extend(
        [
            "",
            "## 悟道树节点组",
            "",
            "| 法器 | 组 | 名称 | 等级 | 消耗 | 属性/文案样例 | 关联 |",
            "| ---: | ---: | --- | --- | --- | --- | --- |",
        ]
    )
    for row in tree_rows[:120]:
        lines.append(
            "| "
            f"{row.get('faqi_id', '')} | "
            f"{row.get('group', '')} | "
            f"{_markdown_table_cell(row.get('name', ''), limit=120)} | "
            f"{row.get('level_range', '')} | "
            f"{_markdown_table_cell(row.get('costs', ''), limit=160)} | "
            f"{_markdown_table_cell(row.get('des_samples', '') or row.get('attr_samples', ''), limit=260)} | "
            f"{_markdown_table_cell(row.get('fazes', '') or row.get('skills', '') or row.get('partners', ''), limit=160)} |"
        )

    lines.extend(
        [
            "",
            "## 吞噬进化样例",
            "",
            "| ID | 法器 | 组 | 阶 | 星 | 名称 | 消耗 | 属性 | 文案摘录 |",
            "| ---: | ---: | ---: | ---: | ---: | --- | --- | --- | --- |",
        ]
    )
    for row in star_rows[:80]:
        lines.append(
            "| "
            f"{row.get('id', '')} | "
            f"{row.get('faqi_id', '')} | "
            f"{row.get('group', '')} | "
            f"{row.get('jie', '')} | "
            f"{row.get('star', '')} | "
            f"{_markdown_table_cell(row.get('name', ''), limit=100)} | "
            f"{_markdown_table_cell(row.get('cost', ''), limit=120)} | "
            f"{_markdown_table_cell(row.get('attr', ''), limit=150)} | "
            f"{_markdown_table_cell(row.get('des_preview', ''), limit=260)} |"
        )

    lines.extend(
        [
            "",
            "## 星图来源样例",
            "",
            "| ID | 法器 | 组 | 来源 | 条件 | 奖励 | 品质 |",
            "| ---: | ---: | --- | --- | --- | --- | ---: |",
        ]
    )
    for row in startree_rows[:80]:
        lines.append(
            "| "
            f"{row.get('id', '')} | "
            f"{row.get('faqi_id', '')} | "
            f"{row.get('group', '')} | "
            f"{_markdown_table_cell(row.get('item_name', ''), limit=120)} | "
            f"{_markdown_table_cell(row.get('condition_des', ''), limit=140)} | "
            f"{_markdown_table_cell(row.get('reward', ''), limit=120)} | "
            f"{row.get('quality', '')} |"
        )

    lines.extend(["", "## 觉醒", "", "| ID | 法器 | 消耗 | 文案摘录 | 关联 |", "| ---: | ---: | --- | --- | --- |"])
    for row in wake_rows:
        lines.append(
            "| "
            f"{row.get('id', '')} | "
            f"{row.get('faqi_id', '')} | "
            f"{_markdown_table_cell(row.get('cost', ''), limit=120)} | "
            f"{_markdown_table_cell(row.get('des_preview', ''), limit=260)} | "
            f"{_markdown_table_cell(row.get('faze', '') or row.get('skill', ''), limit=160)} |"
        )

    lines.extend(
        [
            "",
            "## Lua 证据",
            "",
            f"- 分组：{', '.join(f'{name}:{count}' for name, count in by_category.most_common())}",
            "",
            "| 分组 | 文件 | 行 | 信号 | 代码 |",
            "| --- | --- | ---: | --- | --- |",
        ]
    )
    for row in evidence_rows[:160]:
        lines.append(
            "| "
            f"{_markdown_table_cell(row.get('category', ''), limit=100)} | "
            f"{_markdown_table_cell(row.get('file', ''), limit=120)} | "
            f"{row.get('line', '')} | "
            f"{_markdown_table_cell(row.get('signal', ''), limit=120)} | "
            f"{_markdown_table_cell(row.get('text', ''), limit=280)} |"
        )

    path.write_text("\n".join(lines), encoding="utf-8")


def build_fanxiu_bluestarsea_catalog_probe(
    *,
    export_root: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    export_base = resolve_fanxiu_export_root(export_root)
    output_dir = export_base / "apk_static_index"
    output_dir.mkdir(parents=True, exist_ok=True)
    lang_path = _find_lang_path(export_base)
    blue_dir = _find_cfg_text_asset_dir(export_base, "bluestarsea")
    item_dir = _find_cfg_text_asset_dir(export_base, "item")
    blue_game_dir = _find_text_asset_dir(export_base, "lscripts", "gamesystem", "game", module="bluestarsea")
    if blue_dir is None:
        raise FanxiuResourceError("未找到已导出的 BlueStarSea 配置 TextAsset，请先运行热更新 lscripts 报告。")

    item = _parse_config((item_dir / "Item.lua") if item_dir else None, lang_path)
    item_by_id = _map_by_id(item["rows"])
    base = _parse_config(blue_dir / "Base.lua", lang_path)
    tree = _parse_config(blue_dir / "Tree.lua", lang_path)
    star = _parse_config(blue_dir / "Star.lua", lang_path)
    star_tree = _parse_config(blue_dir / "StarTree.lua", lang_path)
    level = _parse_config(blue_dir / "Level.lua", lang_path)
    wake = _parse_config(blue_dir / "Wake.lua", lang_path)
    break_item = _parse_config(blue_dir / "BreakItem.lua", lang_path)
    charging = _parse_config(blue_dir / "Charging.lua", lang_path)

    base_rows = [
        {
            "id": row.get("id", ""),
            "sort": row.get("sort", ""),
            "name": _plain(row, "name"),
            "interface": row.get("interface", ""),
            "opencondition": row.get("opencondition", ""),
            "openlan": _plain(row, "openlan"),
            "showcondition": row.get("showcondition", ""),
        }
        for row in base["rows"]
    ]
    tree_rows = _aggregate_bluestarsea_nodes(tree["rows"], item_by_id)
    star_rows = [
        {
            "id": row.get("id", ""),
            "faqi_id": row.get("faqiId", ""),
            "group": row.get("group", ""),
            "jie": row.get("jie", ""),
            "star": row.get("star", ""),
            "name": _plain(row, "name"),
            "cost": _fanxiu_item_token_text(row.get("cost"), item_by_id) if row.get("cost") else "",
            "faze": row.get("faze", ""),
            "attr": _compact_json(row.get("attr"), limit=220),
            "des_preview": _plain(row, "des").replace("\n", " / ")[:360],
        }
        for row in star["rows"]
    ]
    startree_rows = []
    for row in star_tree["rows"]:
        item_id = int(row.get("item") or 0)
        startree_rows.append(
            {
                "id": row.get("id", ""),
                "faqi_id": row.get("faqiId", ""),
                "group": row.get("group", ""),
                "item_id": item_id,
                "item_name": _item_name(item_by_id, item_id),
                "condition": row.get("condition", ""),
                "condition_des": _plain(row, "conditionDes"),
                "reward": _fanxiu_item_token_text(row.get("reward"), item_by_id),
                "quality": row.get("quality", ""),
                "point": row.get("point", ""),
            }
        )
    wake_rows = [
        {
            "id": row.get("id", ""),
            "faqi_id": row.get("faqiId", ""),
            "wake": row.get("Wake", ""),
            "cost": _fanxiu_item_token_text(row.get("cost"), item_by_id) if row.get("cost") else "",
            "des_preview": _plain(row, "des").replace("\n", " / ")[:500],
            "faze": row.get("faze", ""),
            "skill": row.get("skill", ""),
        }
        for row in wake["rows"]
    ]
    level_rows = [
        {
            "id": row.get("id", ""),
            "faqi_id": row.get("faqiId", ""),
            "level": row.get("level", ""),
            "cost": _fanxiu_item_token_text(row.get("cost"), item_by_id) if row.get("cost") else "",
            "reward": _fanxiu_item_token_text(row.get("reward"), item_by_id) if row.get("reward") else "",
            "des": _plain(row, "des"),
            "attr": _compact_json(row.get("attr"), limit=220),
            "partner_attr": _compact_json(row.get("partnerAttr"), limit=220),
        }
        for row in level["rows"]
    ]
    break_rows = [
        {
            "id": row.get("id", ""),
            "item_id": row.get("item", ""),
            "item_name": _item_name(item_by_id, int(row.get("item") or 0)),
            "filter": row.get("filter", ""),
            "sort": row.get("sort", ""),
            "energy_consume": row.get("energyConsume", ""),
            "break_obtain": _fanxiu_item_token_text(row.get("breakObtain"), item_by_id),
        }
        for row in break_item["rows"]
    ]
    charging_rows = [
        {
            "id": row.get("id", ""),
            "condition": row.get("condition", ""),
            "consume": _fanxiu_item_token_text(row.get("consume"), item_by_id) if row.get("consume") else "",
            "times": row.get("times", ""),
            "energy": row.get("energy", ""),
            "faze_id": _compact_json(row.get("fazeId"), limit=220),
        }
        for row in charging["rows"]
    ]

    categories = {
        "net": ["CM_BlueStarSea", "SM_BlueStarSea", "F_SendMsg", "F_Register"],
        "model": ["SetBlueStarSea", "GetBlueStarSea", "UpdateBlueStarSea", "redDot"],
        "tree": ["Tree", "GetTree", "StarTree", "OpenBlueStarSeaTree"],
        "purify": ["Purify", "BreakItem", "Charging", "energy"],
        "wake": ["Wake", "RitualImplement", "LevelUp"],
    }
    evidence_rows = _collect_blld_keyword_rows(blue_game_dir, categories) if blue_game_dir else []

    base_count = _write_tsv(output_dir / "hot_update_bluestarsea_bases.tsv", ["id", "sort", "name", "interface", "opencondition", "openlan", "showcondition"], base_rows)
    tree_count = _write_tsv(
        output_dir / "hot_update_bluestarsea_tree_nodes.tsv",
        ["faqi_id", "group", "name", "level_count", "level_range", "costs", "des_samples", "attr_samples", "fazes", "skills", "partners"],
        tree_rows,
    )
    star_count = _write_tsv(output_dir / "hot_update_bluestarsea_star_nodes.tsv", ["id", "faqi_id", "group", "jie", "star", "name", "cost", "faze", "attr", "des_preview"], star_rows)
    startree_count = _write_tsv(output_dir / "hot_update_bluestarsea_startree_sources.tsv", ["id", "faqi_id", "group", "item_id", "item_name", "condition", "condition_des", "reward", "quality", "point"], startree_rows)
    wake_count = _write_tsv(output_dir / "hot_update_bluestarsea_wake.tsv", ["id", "faqi_id", "wake", "cost", "des_preview", "faze", "skill"], wake_rows)
    level_count = _write_tsv(output_dir / "hot_update_bluestarsea_levels.tsv", ["id", "faqi_id", "level", "cost", "reward", "des", "attr", "partner_attr"], level_rows)
    break_count = _write_tsv(output_dir / "hot_update_bluestarsea_break_items.tsv", ["id", "item_id", "item_name", "filter", "sort", "energy_consume", "break_obtain"], break_rows)
    charging_count = _write_tsv(output_dir / "hot_update_bluestarsea_charging.tsv", ["id", "condition", "consume", "times", "energy", "faze_id"], charging_rows)
    evidence_count = _write_tsv(output_dir / "hot_update_bluestarsea_runtime_evidence.tsv", ["category", "file", "line", "signal", "text", "path"], evidence_rows)

    result = {
        "export_root": str(export_base),
        "output_dir": str(output_dir),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "inputs": {
            "lang_path": str(lang_path or ""),
            "blue_dir": str(blue_dir),
            "blue_game_dir": str(blue_game_dir or ""),
            "item_dir": str(item_dir or ""),
        },
        "counts": {
            "bases": base_count,
            "tree_nodes": tree_count,
            "star_nodes": star_count,
            "startree_sources": startree_count,
            "wake": wake_count,
            "levels": level_count,
            "break_items": break_count,
            "charging": charging_count,
            "runtime_evidence": evidence_count,
            "by_evidence_category": dict(Counter(str(row["category"]) for row in evidence_rows).most_common()),
        },
        "outputs": {
            "summary": str(output_dir / "hot_update_bluestarsea_catalog_report.json"),
            "markdown": str(output_dir / "hot_update_bluestarsea_catalog_report.md"),
            "bases": str(output_dir / "hot_update_bluestarsea_bases.tsv"),
            "tree_nodes": str(output_dir / "hot_update_bluestarsea_tree_nodes.tsv"),
            "star_nodes": str(output_dir / "hot_update_bluestarsea_star_nodes.tsv"),
            "startree_sources": str(output_dir / "hot_update_bluestarsea_startree_sources.tsv"),
            "wake": str(output_dir / "hot_update_bluestarsea_wake.tsv"),
            "levels": str(output_dir / "hot_update_bluestarsea_levels.tsv"),
            "break_items": str(output_dir / "hot_update_bluestarsea_break_items.tsv"),
            "charging": str(output_dir / "hot_update_bluestarsea_charging.tsv"),
            "runtime_evidence": str(output_dir / "hot_update_bluestarsea_runtime_evidence.tsv"),
        },
    }
    _write_bluestarsea_catalog_markdown(
        output_dir / "hot_update_bluestarsea_catalog_report.md",
        export_base=export_base,
        base_rows=base_rows,
        tree_rows=tree_rows,
        star_rows=star_rows,
        startree_rows=startree_rows,
        wake_rows=wake_rows,
        evidence_rows=evidence_rows,
    )
    (output_dir / "hot_update_bluestarsea_catalog_report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result
