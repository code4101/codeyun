from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.core.fanxiu.catalog.resources import (
    export_fanxiu_unity_text_assets,
    resolve_fanxiu_export_root,
    resolve_fanxiu_resource_root,
)


SECTION_DIRS = {
    "cfg": Path("lscripts/generate/cfg"),
    "localization": Path("lscripts/generate/localization"),
    "game": Path("lscripts/gamesystem/game"),
    "common": Path("lscripts/gamesystem"),
}


def _write_tsv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, delimiter="\t", lineterminator="\n", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _iter_bundles(resource_root: Path, sections: set[str], name_filter: set[str]) -> list[Path]:
    selected_sections = set(SECTION_DIRS) if "all" in sections else sections
    bundles: list[Path] = []
    for section in selected_sections:
        base = resource_root / SECTION_DIRS[section]
        if not base.is_dir():
            continue
        section_bundles = sorted(base.rglob("*.bytes"), key=lambda item: item.relative_to(resource_root).as_posix().lower())
        for bundle in section_bundles:
            logical_name = bundle.name.split("_", 1)[0].lower()
            if name_filter and logical_name not in name_filter and bundle.stem.lower() not in name_filter:
                continue
            bundles.append(bundle)
    return bundles


def export_fanxiu_lua_text_assets(
    *,
    resource_root: str | Path | None = None,
    export_root: str | Path | None = None,
    sections: set[str] | None = None,
    name_filter: set[str] | None = None,
    max_bundles: int | None = None,
    max_assets_per_bundle: int | None = None,
) -> dict[str, Any]:
    resource_base = resolve_fanxiu_resource_root(resource_root)
    export_base = resolve_fanxiu_export_root(export_root)
    selected_sections = {item.strip().lower() for item in (sections or {"cfg"}) if item.strip()}
    invalid_sections = sorted(selected_sections - set(SECTION_DIRS) - {"all"})
    if invalid_sections:
        raise ValueError(f"unknown section(s): {', '.join(invalid_sections)}")
    filters = {item.strip().lower() for item in (name_filter or set()) if item.strip()}
    bundles = _iter_bundles(resource_base, selected_sections, filters)
    if max_bundles is not None:
        bundles = bundles[: max(0, int(max_bundles))]

    rows: list[dict[str, Any]] = []
    started = time.perf_counter()
    for index, bundle in enumerate(bundles, start=1):
        relative_bundle = bundle.relative_to(resource_base).as_posix()
        section = next(
            (key for key, section_dir in SECTION_DIRS.items() if bundle.is_relative_to(resource_base / section_dir)),
            "",
        )
        item: dict[str, Any] = {
            "section": section,
            "bundle": relative_bundle,
            "text_asset_count": 0,
            "lua_count": 0,
            "output_dir": "",
            "elapsed_seconds": "",
            "error": "",
        }
        row_started = time.perf_counter()
        try:
            result = export_fanxiu_unity_text_assets(
                bundle.relative_to(resource_base),
                resource_root=resource_base,
                export_root=export_base,
                max_assets=max_assets_per_bundle,
            )
            exported = [
                Path(row.get("path") or row.get("output_path") or "")
                for row in result.get("items", [])
                if row.get("path") or row.get("output_path")
            ]
            item.update(
                {
                    "text_asset_count": len(result.get("items", [])),
                    "lua_count": sum(1 for path in exported if path.suffix.lower() == ".lua"),
                    "output_dir": result.get("output_dir", ""),
                }
            )
        except Exception as exc:
            item["error"] = f"{type(exc).__name__}: {exc}"
        item["elapsed_seconds"] = f"{time.perf_counter() - row_started:.3f}"
        rows.append(item)
        if index == 1 or index % 50 == 0 or index == len(bundles):
            print(f"exported {index}/{len(bundles)} {relative_bundle}", flush=True)

    index_dir = export_base / "parsed_configs" / "lua_text_asset_index"
    section_label = "all" if "all" in selected_sections else "_".join(sorted(selected_sections))
    suffix = f"{section_label}_latest"
    tsv_path = index_dir / f"text_asset_exports_{suffix}.tsv"
    json_path = index_dir / f"text_asset_exports_{suffix}.json"
    fields = ["section", "bundle", "text_asset_count", "lua_count", "output_dir", "elapsed_seconds", "error"]
    _write_tsv(tsv_path, rows, fields)
    payload = {
        "resource_root": str(resource_base),
        "export_root": str(export_base),
        "sections": sorted(selected_sections),
        "name_filter": sorted(filters),
        "bundle_count": len(bundles),
        "exported_bundle_count": sum(1 for row in rows if not row["error"]),
        "error_count": sum(1 for row in rows if row["error"]),
        "text_asset_count": sum(int(row["text_asset_count"] or 0) for row in rows),
        "lua_count": sum(int(row["lua_count"] or 0) for row in rows),
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "files": {
            "tsv": str(tsv_path),
            "json": str(json_path),
        },
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Export Fanxiu Unity TextAsset Lua files from lscripts bundles.")
    parser.add_argument("--resource-root", default=None)
    parser.add_argument("--export-root", default=None)
    parser.add_argument("--section", action="append", default=[], help="cfg, localization, game, common, or all; repeatable")
    parser.add_argument("--name", action="append", default=[], help="Optional bundle logical prefix filter; repeatable")
    parser.add_argument("--max-bundles", type=int, default=None)
    parser.add_argument("--max-assets-per-bundle", type=int, default=None)
    args = parser.parse_args()
    result = export_fanxiu_lua_text_assets(
        resource_root=args.resource_root,
        export_root=args.export_root,
        sections=set(args.section or ["cfg"]),
        name_filter=set(args.name or []),
        max_bundles=args.max_bundles,
        max_assets_per_bundle=args.max_assets_per_bundle,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
