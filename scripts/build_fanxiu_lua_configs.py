from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.core.fanxiu.catalog.lua_config import _find_default_lang_path, build_fanxiu_lua_config_report
from backend.core.fanxiu.catalog.resources import (
    export_fanxiu_unity_text_assets,
    resolve_fanxiu_export_root,
    resolve_fanxiu_resource_root,
)


def _write_tsv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, delimiter="\t", lineterminator="\n", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _iter_cfg_bundles(resource_root: Path, table_filter: set[str]) -> list[Path]:
    cfg_dir = resource_root / "lscripts" / "generate" / "cfg"
    bundles = sorted(cfg_dir.glob("*.bytes"), key=lambda item: item.name.lower())
    if not table_filter:
        return bundles
    filtered: list[Path] = []
    for path in bundles:
        logical_name = path.name.split("_", 1)[0].lower()
        if logical_name in table_filter or path.stem.lower() in table_filter:
            filtered.append(path)
    return filtered


def build_fanxiu_lua_configs(
    *,
    resource_root: str | Path | None = None,
    export_root: str | Path | None = None,
    table_filter: set[str] | None = None,
    skip_export: bool = False,
    max_preview_rows: int = 5000,
) -> dict[str, Any]:
    resource_base = resolve_fanxiu_resource_root(resource_root)
    export_base = resolve_fanxiu_export_root(export_root)
    table_filter = {item.lower() for item in (table_filter or set()) if item.strip()}
    bundles = _iter_cfg_bundles(resource_base, table_filter)
    if not bundles:
        raise FileNotFoundError(f"no cfg bundles found under {resource_base / 'lscripts' / 'generate' / 'cfg'}")

    export_rows: list[dict[str, Any]] = []
    parse_rows: list[dict[str, Any]] = []
    lua_paths: list[Path] = []

    if not skip_export:
        lang_bundle = next((p for p in (resource_base / "lscripts" / "generate" / "localization" / "chinese").glob("lang*.bytes")), None)
        if lang_bundle:
            export_fanxiu_unity_text_assets(lang_bundle.relative_to(resource_base), resource_root=resource_base, export_root=export_base)
        for bundle in bundles:
            rel = bundle.relative_to(resource_base)
            try:
                result = export_fanxiu_unity_text_assets(rel, resource_root=resource_base, export_root=export_base)
                exported = [Path(item.get("path") or "") for item in result.get("items", []) if item.get("path")]
                lua_paths.extend(path for path in exported if path.suffix.lower() == ".lua")
                export_rows.append(
                    {
                        "bundle": rel.as_posix(),
                        "text_asset_count": len(result.get("items", [])),
                        "lua_count": sum(1 for path in exported if path.suffix.lower() == ".lua"),
                        "output_dir": result.get("output_dir", ""),
                        "error": "",
                    }
                )
            except Exception as exc:
                export_rows.append(
                    {
                        "bundle": rel.as_posix(),
                        "text_asset_count": 0,
                        "lua_count": 0,
                        "output_dir": "",
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
    else:
        by_source = export_base / "by_source" / "lscripts" / "generate" / "cfg"
        lua_paths = sorted(by_source.glob("*/text_assets/*.lua"), key=lambda item: str(item).lower())

    if not lua_paths:
        by_source = export_base / "by_source" / "lscripts" / "generate" / "cfg"
        lua_paths = sorted(by_source.glob("*/text_assets/*.lua"), key=lambda item: str(item).lower())

    lang_path = _find_default_lang_path(export_base)
    seen_lua: set[Path] = set()
    for lua_path in sorted(lua_paths, key=lambda item: str(item).lower()):
        lua_path = lua_path.resolve()
        if lua_path in seen_lua:
            continue
        seen_lua.add(lua_path)
        try:
            result = build_fanxiu_lua_config_report(
                lua_path,
                lang_path=lang_path,
                export_root=export_base,
                max_preview_rows=max_preview_rows,
            )
            parse_rows.append(
                {
                    "asset": lua_path.name,
                    "row_count": result["row_count"],
                    "field_count": result["field_count"],
                    "output_dir": result["output_dir"],
                    "source_path": result["source_path"],
                    "error": "",
                }
            )
        except Exception as exc:
            parse_rows.append(
                {
                    "asset": lua_path.name,
                    "row_count": 0,
                    "field_count": 0,
                    "output_dir": "",
                    "source_path": str(lua_path),
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

    index_dir = export_base / "parsed_configs" / "lua_config_index"
    index_dir.mkdir(parents=True, exist_ok=True)
    _write_tsv(index_dir / "cfg_text_asset_exports.tsv", export_rows, ["bundle", "text_asset_count", "lua_count", "output_dir", "error"])
    _write_tsv(index_dir / "lua_config_tables.tsv", parse_rows, ["asset", "row_count", "field_count", "output_dir", "source_path", "error"])
    payload = {
        "resource_root": str(resource_base),
        "export_root": str(export_base),
        "cfg_bundle_count": len(bundles),
        "exported_bundle_count": sum(1 for row in export_rows if not row["error"]) if export_rows else 0,
        "lua_count": len(seen_lua),
        "parsed_count": sum(1 for row in parse_rows if not row["error"]),
        "parse_error_count": sum(1 for row in parse_rows if row["error"]),
        "lang_path": str(lang_path or ""),
        "files": {
            "exports_tsv": str(index_dir / "cfg_text_asset_exports.tsv"),
            "tables_tsv": str(index_dir / "lua_config_tables.tsv"),
            "summary_json": str(index_dir / "lua_config_tables.json"),
        },
        "tables": parse_rows,
    }
    (index_dir / "lua_config_tables.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Export and parse Fanxiu generated Lua config bundles.")
    parser.add_argument("--resource-root", default=None)
    parser.add_argument("--export-root", default=None)
    parser.add_argument("--table", action="append", default=[], help="Optional logical table/bundle prefix filter; repeatable")
    parser.add_argument("--skip-export", action="store_true", help="Parse existing exported Lua files only")
    parser.add_argument("--max-preview-rows", type=int, default=5000)
    args = parser.parse_args()
    result = build_fanxiu_lua_configs(
        resource_root=args.resource_root,
        export_root=args.export_root,
        table_filter=set(args.table or []),
        skip_export=args.skip_export,
        max_preview_rows=args.max_preview_rows,
    )
    print(json.dumps({key: value for key, value in result.items() if key != "tables"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
