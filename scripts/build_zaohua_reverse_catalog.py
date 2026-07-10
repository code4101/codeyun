from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path, PurePosixPath
from typing import Any

import UnityPy
import dnfile


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


DEFAULT_ROOT = Path(r"D:\home\chenkunze\data\m2607造化仙缘")
BUILD_ID = "24123658"
CONFIG_BUNDLE = "bundle_466deec76ecdf5fc.unity3d"
XML_BUNDLE = "bundle_0f635d0e0f3874ff.unity3d"
LOCAL_BUNDLE = "bundle_190aeea761628805.unity3d"
ELEMENT_LABELS = {
    "gold": "金",
    "wood": "木",
    "water": "水",
    "fire": "火",
    "soil": "土",
    "earth": "土",
    "ice": "冰",
    "wind": "风",
    "thunder": "雷",
}
HERB_TYPE_ID = 342
ITEM_ATTRIBUTE_DEFINITIONS = {
    1: {"key": "gold", "name": "金", "order": 1},
    2: {"key": "water", "name": "水", "order": 2},
    3: {"key": "wood", "name": "木", "order": 3},
    4: {"key": "fire", "name": "火", "order": 4},
    5: {"key": "soil", "name": "土", "order": 5},
    6: {"key": "ice", "name": "冰", "order": 6},
    7: {"key": "wind", "name": "风", "order": 7},
    8: {"key": "thunder", "name": "雷", "order": 8},
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _text_assets(bundle_path: Path) -> dict[str, str]:
    environment = UnityPy.load(str(bundle_path))
    assets: dict[str, str] = {}
    for obj in environment.objects:
        if obj.type.name != "TextAsset":
            continue
        data = obj.read()
        content = data.m_Script
        if isinstance(content, bytes):
            content = content.decode("utf-8-sig", errors="replace")
        assets[str(data.m_Name)] = str(content)
    return assets


def _bundle_unity_version(bundle_path: Path) -> str:
    environment = UnityPy.load(str(bundle_path))
    for file in environment.files.values():
        version = str(getattr(file, "version_engine", "") or "")
        if version:
            return version
    return ""


def _xml_rows(content: str) -> list[dict[str, str]]:
    root = ET.fromstring(content.lstrip("\ufeff"))
    return [
        {child.tag: child.text or "" for child in row}
        for row in root
    ]


def _local_map(rows: list[dict[str, str]]) -> dict[str, str]:
    return {
        str(row.get("id") or ""): str(row.get("Chinese") or row.get("TraditionalChinese") or "")
        for row in rows
        if row.get("id")
    }


def _pairs(value: Any) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for part in re.split(r"[;,]", str(value or "")):
        key, separator, raw_count = part.strip().partition(":")
        if not key:
            continue
        try:
            count: int | float | str = float(raw_count) if "." in raw_count else int(raw_count or "0")
        except ValueError:
            count = raw_count
        results.append({"key": key, "count": count if separator else 0})
    return results


def _ids(value: Any) -> list[int]:
    result: list[int] = []
    for item in re.split(r"[^0-9]+", str(value or "")):
        if item:
            result.append(int(item))
    return result


def _normalized_icon_key(value: Any) -> str:
    path = str(value or "").strip().replace("\\", "/").strip("/").lower()
    if path.endswith(".png"):
        path = path[:-4]
    parts = PurePosixPath(path).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        return ""
    return "/".join(parts)


def _export_item_icons(others: Path, media_root: Path, icon_paths: set[str]) -> dict[str, dict[str, Any]]:
    wanted = {_normalized_icon_key(path) for path in icon_paths}
    wanted.discard("")
    asset_to_bundle: dict[str, Path] = {}
    for manifest_path in others.glob("*.unity3d.manifest"):
        bundle_path = manifest_path.with_suffix("")
        if not bundle_path.is_file():
            continue
        for line in manifest_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            asset_path = line.strip().removeprefix("- ").replace("\\", "/").lower()
            prefix = "assets/resources/"
            if not asset_path.startswith(prefix) or not asset_path.endswith(".png"):
                continue
            icon_key = asset_path[len(prefix):-4]
            if icon_key in wanted:
                asset_to_bundle[asset_path] = bundle_path

    assets_by_bundle: dict[Path, set[str]] = defaultdict(set)
    for icon_key in wanted:
        asset_path = f"assets/resources/{icon_key}.png"
        bundle_path = asset_to_bundle.get(asset_path)
        if bundle_path is not None:
            assets_by_bundle[bundle_path].add(asset_path)

    exports: dict[str, dict[str, Any]] = {}
    for bundle_path, asset_paths in assets_by_bundle.items():
        environment = UnityPy.load(str(bundle_path))
        candidates: dict[str, list[Any]] = defaultdict(list)
        for asset_path, obj in environment.container.items():
            normalized_asset_path = str(asset_path).replace("\\", "/").lower()
            if normalized_asset_path in asset_paths and obj.type.name in {"Sprite", "Texture2D"}:
                candidates[normalized_asset_path].append(obj)
        for asset_path, objects in candidates.items():
            icon_key = asset_path[len("assets/resources/"):-4]
            output_path = media_root / "icons" / Path(*PurePosixPath(icon_key).parts).with_suffix(".png")
            output_path.parent.mkdir(parents=True, exist_ok=True)
            obj = next((item for item in objects if item.type.name == "Sprite"), objects[0])
            obj.read().image.save(output_path)
            exports[icon_key] = {
                "media_path": output_path.relative_to(media_root).as_posix(),
                "sha256": _sha256(output_path),
                "source_bundle": bundle_path.name,
                "source_asset": asset_path,
            }
    return exports


def _item_view(item: dict[str, Any] | None, local: dict[str, str], grades: dict[int, dict[str, Any]], grade_local: dict[str, str]) -> dict[str, Any]:
    item = item or {}
    item_id = int(item.get("id") or 0)
    grade_id = int(item.get("gradeId") or 0)
    grade = grades.get(grade_id, {})
    return {
        "item_id": item_id,
        "name": local.get(str(item.get("name") or ""), str(item.get("name") or f"物品 #{item_id}")),
        "description": local.get(str(item.get("des") or ""), ""),
        "effect_description": local.get(str(item.get("effDes") or ""), ""),
        "use_effect": str(item.get("useEff") or ""),
        "icon_path": str(item.get("iconPath") or ""),
        "type_id": int(item.get("typeId") or 0),
        "grade_id": grade_id,
        "grade_name": grade_local.get(str(grade.get("name") or ""), ""),
        "price": item.get("price") or 0,
        "drug_quality": item.get("drugQuality") or 0,
        "attribute": item.get("attribute") or 0,
    }


def _assembly_report(assembly_path: Path) -> dict[str, Any]:
    pe = dnfile.dnPE(str(assembly_path))
    types: list[dict[str, Any]] = []
    method_count = 0
    keywords = ("drug", "recipe", "liandan", "crafting")
    for row in pe.net.mdtables.TypeDef.rows:
        name = str(row.TypeName)
        namespace = str(row.TypeNamespace)
        methods = [str(index.row.Name) for index in row.MethodList]
        method_count += len(methods)
        full_name = f"{namespace}.{name}".strip(".")
        if any(keyword in full_name.lower() for keyword in keywords):
            types.append({"type": full_name, "methods": methods})
    return {
        "assembly": str(assembly_path),
        "sha256": _sha256(assembly_path),
        "type_count": len(pe.net.mdtables.TypeDef.rows),
        "method_count": method_count,
        "alchemy_type_count": len(types),
        "alchemy_types": types,
    }


def build(root: Path) -> dict[str, Any]:
    snapshot = root / "raw_inputs" / f"steam_build_{BUILD_ID}"
    game_root = snapshot / "GodWorld"
    others = game_root / "Zaohua_Data" / "StreamingAssets" / "Others"
    parsed_dir = root / "parsed_configs" / "alchemy"
    herb_parsed_dir = root / "parsed_configs" / "herbs"
    raw_export_dir = root / "reverse_exports" / "text_assets" / "alchemy"
    report_dir = root / "reverse_exports" / "reports"
    media_dir = root / "media"
    for directory in (parsed_dir, herb_parsed_dir, raw_export_dir, report_dir):
        directory.mkdir(parents=True, exist_ok=True)

    config_assets = _text_assets(others / CONFIG_BUNDLE)
    xml_assets = _text_assets(others / XML_BUNDLE)
    local_assets = _text_assets(others / LOCAL_BUNDLE)
    selected_assets = {
        "TbDrugRecipeCfg.json": config_assets["TbDrugRecipeCfg"],
        "TbItemCfg.json": config_assets["TbItemCfg"],
        "TbDrugRecipeStateCfg.xml": xml_assets["TbDrugRecipeStateCfg"],
        "TbGradeCfg.xml": xml_assets["TbGradeCfg"],
        "TbTypeCfg.xml": xml_assets["TbTypeCfg"],
        "TbDrugRecipeCfgLocal.xml": local_assets["TbDrugRecipeCfgLocal"],
        "TbDrugRecipeStateCfgLocal.xml": local_assets["TbDrugRecipeStateCfgLocal"],
        "TbItemCfgLocal.xml": local_assets["TbItemCfgLocal"],
        "TbGradeCfgLocal.xml": local_assets["TbGradeCfgLocal"],
        "TbTypeCfgLocal.xml": local_assets["TbTypeCfgLocal"],
    }
    for filename, content in selected_assets.items():
        (raw_export_dir / filename).write_text(content, encoding="utf-8")

    recipe_rows = json.loads(config_assets["TbDrugRecipeCfg"])
    item_rows = json.loads(config_assets["TbItemCfg"])
    state_rows = _xml_rows(xml_assets["TbDrugRecipeStateCfg"])
    grade_rows = _xml_rows(xml_assets["TbGradeCfg"])
    type_rows = _xml_rows(xml_assets["TbTypeCfg"])
    recipe_local = _local_map(_xml_rows(local_assets["TbDrugRecipeCfgLocal"]))
    state_local = _local_map(_xml_rows(local_assets["TbDrugRecipeStateCfgLocal"]))
    item_local = _local_map(_xml_rows(local_assets["TbItemCfgLocal"]))
    grade_local = _local_map(_xml_rows(local_assets["TbGradeCfgLocal"]))
    type_local = _local_map(_xml_rows(local_assets["TbTypeCfgLocal"]))

    items = {int(row.get("id") or 0): row for row in item_rows}
    states = {int(row.get("id") or 0): row for row in state_rows}
    grades = {int(row.get("id") or 0): row for row in grade_rows}
    recipes: list[dict[str, Any]] = []
    for row in recipe_rows:
        recipe_id = int(row.get("id") or 0)
        output = _item_view(items.get(int(row.get("itemId") or 0)), item_local, grades, grade_local)
        examples: list[dict[str, Any]] = []
        for pair in _pairs(row.get("exampleItemStr")):
            item_id = int(pair["key"])
            item = _item_view(items.get(item_id), item_local, grades, grade_local)
            item["count"] = pair["count"]
            examples.append(item)
        state_rules: list[dict[str, Any]] = []
        for state_id in _ids(row.get("stateIdStr")):
            state = dict(states.get(state_id, {}))
            state_rules.append({
                "state_id": state_id,
                "name": state_local.get(str(state.get("name") or ""), str(state.get("name") or "")),
                "pool_type": state.get("poolType", ""),
                "state_type": state.get("stateType", ""),
                "area": state.get("area", ""),
                "calculate_type": state.get("calculateType", ""),
                "target1": state.get("target1", ""),
                "target2": state.get("target2", ""),
                "relation": state.get("relation", ""),
                "base_effect": state.get("baseEff", ""),
            })
        attr_limits = [
            {"element": pair["key"], "label": ELEMENT_LABELS.get(str(pair["key"]).lower(), str(pair["key"])), "value": pair["count"]}
            for pair in _pairs(row.get("attrLimiteStr"))
        ]
        name = recipe_local.get(str(row.get("name") or ""), str(row.get("name") or f"丹方 #{recipe_id}"))
        technique = recipe_local.get(str(row.get("techniqueDes") or ""), "")
        search_text = " ".join(
            [name, technique, output.get("name", ""), output.get("description", "")]
            + [str(item.get("name") or "") for item in examples]
            + [str(rule.get("name") or "") for rule in state_rules]
        ).lower()
        recipe = {
            "recipe_id": recipe_id,
            "name": name,
            "technique": technique,
            "output_count": int(row.get("count") or 0),
            "output": output,
            "attr_limits": attr_limits,
            "example_items": examples,
            "state_rules": state_rules,
            "search_text": search_text,
            "source_evidence": {
                "config_bundle": CONFIG_BUNDLE,
                "config_asset": "Assets/Resources/Json/TbDrugRecipeCfg.json",
                "state_bundle": XML_BUNDLE,
                "state_asset": "Assets/Resources/Xml/TbDrugRecipeStateCfg.xml",
                "local_bundle": LOCAL_BUNDLE,
                "local_asset": "Assets/Resources/LocalXml/TbDrugRecipeCfgLocal.xml",
                "assembly_type": "TbDrugRecipeCfg",
                "ui_type": "CraftingDrugCell",
            },
            "raw": row,
        }
        recipes.append(recipe)

    herb_recipe_refs: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for recipe in recipes:
        for item in recipe["example_items"]:
            herb_recipe_refs[int(item["item_id"])].append({
                "recipe_id": recipe["recipe_id"],
                "output_item_id": recipe["output"]["item_id"],
                "output_name": recipe["output"]["name"],
                "required_count": item.get("count", 0),
            })

    herb_rows: list[dict[str, Any]] = []
    for raw_index, row in enumerate(item_rows):
        if int(row.get("typeId") or 0) != HERB_TYPE_ID:
            continue
        herb = _item_view(row, item_local, grades, grade_local)
        element_id = int(row.get("attribute") or 0)
        element = ITEM_ATTRIBUTE_DEFINITIONS.get(
            element_id,
            {"key": "none", "name": "无", "order": 99},
        )
        recipes_using = herb_recipe_refs.get(herb["item_id"], [])
        herb.update({
            "display_order": raw_index,
            "type_name": type_local.get(str(next((item.get("name") for item in type_rows if int(item.get("id") or 0) == HERB_TYPE_ID), "")), "灵草"),
            "element_id": element_id,
            "element_key": element["key"],
            "element_name": element["name"],
            "element_order": element["order"],
            "lingqi": int(row.get("lingqi") or 0),
            "recipe_count": len(recipes_using),
            "recipes": recipes_using,
            "source_evidence": {
                "config_bundle": CONFIG_BUNDLE,
                "config_asset": "Assets/Resources/Json/TbItemCfg.json",
                "type_bundle": XML_BUNDLE,
                "type_asset": "Assets/Resources/Xml/TbTypeCfg.xml",
                "local_bundle": LOCAL_BUNDLE,
                "local_asset": "Assets/Resources/LocalXml/TbItemCfgLocal.xml",
                "assembly_type": "TbItemCfg",
                "type_id": str(HERB_TYPE_ID),
            },
            "raw": row,
        })
        herb["search_text"] = " ".join([
            str(herb.get("name") or ""),
            str(herb.get("description") or ""),
            str(herb.get("effect_description") or ""),
            str(herb.get("element_name") or ""),
            str(herb.get("grade_name") or ""),
            *[str(item.get("output_name") or "") for item in recipes_using],
        ]).lower()
        herb_rows.append(herb)

    herb_rows.sort(key=lambda item: (
        int(item.get("grade_id") or 10_000),
        int(item.get("element_order") or 99),
        int(item.get("display_order") or 0),
    ))
    for display_order, herb in enumerate(herb_rows, start=1):
        herb["display_order"] = display_order

    catalog_items = [
        *[item for recipe in recipes for item in [recipe["output"], *recipe["example_items"]]],
        *herb_rows,
    ]
    icon_exports = _export_item_icons(
        others,
        media_dir,
        {str(item.get("icon_path") or "") for item in catalog_items},
    )
    for item in catalog_items:
        icon_export = icon_exports.get(_normalized_icon_key(item.get("icon_path")))
        if icon_export:
            item["icon_media_path"] = icon_export["media_path"]
            item["icon_sha256"] = icon_export["sha256"]
    for recipe in recipes:
        recipe["content_hash"] = hashlib.sha256(
            json.dumps(recipe, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
    for herb in herb_rows:
        herb["content_hash"] = hashlib.sha256(
            json.dumps(herb, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()

    assembly_path = game_root / "Zaohua_Data" / "Managed" / "Assembly-CSharp.dll"
    assembly = _assembly_report(assembly_path)
    bundles = list(others.glob("*.unity3d"))
    manifests = list(others.glob("*.manifest"))
    snapshot_files = [path for path in snapshot.rglob("*") if path.is_file()]
    source_paths = [
        snapshot / "appmanifest_2377930.acf",
        assembly_path,
        others / CONFIG_BUNDLE,
        others / XML_BUNDLE,
        others / LOCAL_BUNDLE,
    ]
    source_files = [
        {
            "path": str(path.relative_to(snapshot)),
            "size": path.stat().st_size,
            "sha256": _sha256(path),
        }
        for path in source_paths
    ]
    config_manifest_entries = 0
    for manifest in manifests:
        text = manifest.read_text(encoding="utf-8", errors="ignore")
        config_manifest_entries += len(re.findall(r"Assets/Resources/(?:Json|Xml|LocalXml)/", text, flags=re.I))

    catalog = {
        "schema_version": 1,
        "source": {
            "steam_app_id": "2377930",
            "steam_build_id": BUILD_ID,
            "game_name": "Destiny of Immortal / 造化仙缘",
            "snapshot_root": str(snapshot),
            "unity_backend": "Mono",
            "unity_version": _bundle_unity_version(others / CONFIG_BUNDLE),
            "assembly_sha256": assembly["sha256"],
            "source_files": source_files,
        },
        "stats": {
            "recipe_count": len(recipes),
            "state_rule_count": len(state_rows),
            "item_count": len(item_rows),
            "herb_count": len(herb_rows),
            "recipe_herb_count": len(herb_recipe_refs),
            "output_item_count": len({row["output"]["item_id"] for row in recipes}),
            "example_item_count": len({item["item_id"] for row in recipes for item in row["example_items"]}),
            "alchemy_icon_count": len(icon_exports),
            "alchemy_icon_missing_count": len({
                _normalized_icon_key(item.get("icon_path"))
                for item in catalog_items
                if _normalized_icon_key(item.get("icon_path"))
            } - set(icon_exports)),
            "bundle_count": len(bundles),
            "bundle_size_bytes": sum(path.stat().st_size for path in bundles),
            "manifest_count": len(manifests),
            "snapshot_file_count": len(snapshot_files),
            "snapshot_size_bytes": sum(path.stat().st_size for path in snapshot_files),
            "config_manifest_entry_count": config_manifest_entries,
            "assembly_type_count": assembly["type_count"],
            "assembly_method_count": assembly["method_count"],
            "alchemy_type_count": assembly["alchemy_type_count"],
        },
        "recipes": sorted(recipes, key=lambda item: item["recipe_id"]),
    }
    catalog_path = parsed_dir / "alchemy_catalog.json"
    catalog_path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
    herb_catalog = {
        "schema_version": 1,
        "source": catalog["source"],
        "stats": {
            "herb_count": len(herb_rows),
            "recipe_herb_count": len(herb_recipe_refs),
            "unused_herb_count": len(herb_rows) - len(herb_recipe_refs),
            "herb_icon_count": len({
                _normalized_icon_key(item.get("icon_path"))
                for item in herb_rows
                if item.get("icon_media_path")
            }),
        },
        "herbs": herb_rows,
    }
    herb_catalog_path = herb_parsed_dir / "herb_catalog.json"
    herb_catalog_path.write_text(
        json.dumps(herb_catalog, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (report_dir / "assembly_framework.json").write_text(
        json.dumps(assembly, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    grade_counts = Counter(str(row["output"].get("grade_name") or "未命名") for row in recipes)
    report = [
        "# 造化仙缘整体框架与炼丹逆向报告",
        "",
        f"- Steam App：2377930",
        f"- Steam Build：{BUILD_ID}",
        f"- Unity 脚本后端：Mono",
        f"- Unity 版本：{catalog['source']['unity_version']}",
        f"- 来源快照：{len(snapshot_files)} 个文件 / {catalog['stats']['snapshot_size_bytes']} bytes",
        f"- Assembly-CSharp 类型：{assembly['type_count']}",
        f"- Assembly-CSharp 方法：{assembly['method_count']}",
        f"- 炼丹相关类型：{assembly['alchemy_type_count']}",
        f"- Unity Bundle：{len(bundles)}",
        f"- 配置资源清单条目：{config_manifest_entries}",
        "",
        "## 炼丹数据链",
        "",
        "`TbDrugRecipeCfg → TbItemCfg → TbDrugRecipeStateCfg`，中文文本分别由对应 `*Local` 表解析。",
        "",
        f"- 丹方：{len(recipes)}",
        f"- 丹方状态规则：{len(state_rows)}",
        f"- 丹方产出物：{len({row['output']['item_id'] for row in recipes})}",
        f"- 示例药材：{len({item['item_id'] for row in recipes for item in row['example_items']})}",
        f"- 完整药材图鉴：{len(herb_rows)}",
        f"- 炼丹图标：{len(icon_exports)}",
        "",
        "## 产出品阶分布",
        "",
        *[f"- {name}：{count}" for name, count in grade_counts.most_common()],
        "",
        "## 关键程序集类型",
        "",
        *[f"- `{item['type']}`：{', '.join(item['methods'][:16])}" for item in assembly["alchemy_types"]],
        "",
        "## 证据边界",
        "",
        "当前丹方、物品、状态条件与本地化文字均来自 build 24123658 的静态资源；尚未通过真实炼丹操作校准状态规则的运行时结算顺序。",
    ]
    (report_dir / "造化仙缘整体框架与炼丹逆向报告.md").write_text("\n".join(report), encoding="utf-8")
    return catalog


def main() -> int:
    parser = argparse.ArgumentParser(description="构建造化仙缘整体框架与炼丹逆向 catalog")
    parser.add_argument("--root", type=Path, default=Path(os.getenv("ZAOHUA_REVERSE_ROOT", "")) if os.getenv("ZAOHUA_REVERSE_ROOT") else DEFAULT_ROOT)
    parser.add_argument("--sync-db", action="store_true", help="生成后同步到 CodeYun 数据库")
    args = parser.parse_args()
    catalog = build(args.root)
    summary: dict[str, Any] = {
        "catalog": str(args.root / "parsed_configs" / "alchemy" / "alchemy_catalog.json"),
        "herb_catalog": str(args.root / "parsed_configs" / "herbs" / "herb_catalog.json"),
        "stats": catalog["stats"],
    }
    if args.sync_db:
        from backend.db import engine
        from backend.migrations.manager import run_migrations
        from backend.core.zaohua.catalog import (
            sync_zaohua_catalog_to_database,
            sync_zaohua_herb_catalog_to_database,
        )
        from sqlmodel import Session

        run_migrations(engine)
        with Session(engine) as session:
            summary["database"] = sync_zaohua_catalog_to_database(session, catalog)
            herb_catalog = json.loads(
                (args.root / "parsed_configs" / "herbs" / "herb_catalog.json").read_text(encoding="utf-8")
            )
            summary["herb_database"] = sync_zaohua_herb_catalog_to_database(session, herb_catalog)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
