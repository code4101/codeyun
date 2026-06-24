from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import sys
from pathlib import Path
from typing import Any

import UnityPy

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.core.mystia.catalog import CATALOG_VERSION, DEFAULT_ANALYSIS_ROOT

DEFAULT_GAME_ROOT = Path(r"D:\SteamLibrary\steamapps\common\Touhou Mystia Izakaya")
AA_DIR = Path(r"Touhou Mystia Izakaya_Data\StreamingAssets\aa\StandaloneWindows64")
CORE_PROFILE_BUNDLE = "core_07e01badce0c3466a71d003dd46efa15.bundle"
SIMPLIFIED_LANG_BUNDLE = "core_d588e1cad1b8b9b47f46af2be495e6c3.bundle"
XOR_KEY = 0x53
ASSET_IMAGE_DIR = "assets/images"
ASSET_AUDIO_DIR = "assets/audio"


def _safe_asset_stem(value: str) -> str:
    sanitized = re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", value, flags=re.UNICODE).strip("._")
    return sanitized or hashlib.sha1(value.encode("utf-8")).hexdigest()[:12]


def _decode_bundle_bytes(path: Path) -> bytes:
    raw = path.read_bytes()
    if raw.startswith(b"UnityFS"):
        return raw
    decoded = bytes(byte ^ XOR_KEY for byte in raw)
    if not decoded.startswith(b"UnityFS"):
        raise RuntimeError(f"bundle 解混淆后不是 UnityFS：{path}")
    return decoded


def _load_bundle(path: Path):
    return UnityPy.load(io.BytesIO(_decode_bundle_bytes(path)))


def _load_bundles(paths: list[Path]):
    streams = [io.BytesIO(_decode_bundle_bytes(path)) for path in paths]
    return UnityPy.load(*streams)


def _read_named_text_assets(bundle_path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for obj in _load_bundle(bundle_path).objects:
        if obj.type.name != "TextAsset":
            continue
        data = obj.read()
        result[str(data.m_Name)] = str(data.m_Script)
    return result


def _parse_tsv(text: str) -> list[dict[str, str]]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
    rows = list(csv.DictReader(io.StringIO(normalized), delimiter="\t"))
    return [
        {str(key or "").strip(): str(value or "").strip() for key, value in row.items()}
        for row in rows
    ]


def _id_map(rows: list[dict[str, str]], id_key: str = "id") -> dict[int, dict[str, str]]:
    result: dict[int, dict[str, str]] = {}
    for row in rows:
        raw = row.get(id_key) or row.get(id_key.upper()) or row.get("ID")
        if raw is None:
            continue
        try:
            result[int(raw)] = row
        except ValueError:
            continue
    return result


def _extract_profiles(bundle_path: Path) -> dict[str, dict[str, Any]]:
    names = {
        "RecipeProfile",
        "IngredientProfile",
        "FoodProfile",
        "BeverageProfile",
        "CookerProfile",
        "FoodTagProfile",
        "BeverageTagProfile",
        "NormalGuestProfile",
        "SpecialGuestProfile",
    }
    profiles: dict[str, dict[str, Any]] = {}
    for obj in _load_bundle(bundle_path).objects:
        if obj.type.name != "MonoBehaviour":
            continue
        try:
            tree = obj.read_typetree()
        except Exception:
            continue
        name = str(tree.get("m_Name") or "")
        if name in names:
            profiles[name] = tree
    missing = sorted(names - profiles.keys())
    if missing:
        raise RuntimeError(f"缺少核心 profile：{missing}")
    return profiles


def _tag_names(ids: list[int], tag_map: dict[int, dict[str, str]]) -> list[str]:
    result: list[str] = []
    for tag_id in ids:
        row = tag_map.get(int(tag_id))
        if row:
            result.append(row.get("tag") or row.get("Tag") or str(tag_id))
        else:
            result.append(str(tag_id))
    return result


def _weighted_tag_names(items: list[dict[str, Any]], tag_map: dict[int, dict[str, str]]) -> list[str]:
    result: list[str] = []
    for item in items:
        tag_id = int(item.get("tagId", 0))
        row = tag_map.get(tag_id)
        name = row.get("tag") if row else str(tag_id)
        weight = item.get("weight", 1)
        result.append(f"{name} x{weight}" if weight != 1 else name)
    return result


def _asset_url(relative_path: str) -> str:
    normalized = relative_path.replace("\\", "/")
    return f"/api/mystia/asset/{normalized}"


def _image_record(name: str, kind: str, bundle: Path, relative_path: str, size: tuple[int, int]) -> dict[str, Any]:
    return {
        "name": name,
        "kind": kind,
        "bundle": bundle.name,
        "path": relative_path.replace("\\", "/"),
        "url": _asset_url(relative_path),
        "width": size[0],
        "height": size[1],
    }


def _audio_group(name: str) -> str:
    lowered = name.lower()
    if "loop" in lowered or "intro" in lowered or lowered in {"op_intro", "op_loop"}:
        return "bgm"
    if lowered.startswith("spell_"):
        return "spell"
    if lowered.startswith("ui_"):
        return "ui"
    if lowered.startswith("sfx_") or lowered.startswith("common_"):
        return "sfx"
    if "_" in name and name.split("_", 1)[0][:1].isupper():
        return "character"
    return "other"


def _parse_named_id(name: str, prefix: str) -> int | None:
    match = re.fullmatch(rf"{re.escape(prefix)}_(\d+)", name)
    if not match:
        return None
    return int(match.group(1))


def _scan_asset_bundles(bundle_root: Path, output_root: Path) -> dict[str, Any]:
    bundle_paths = sorted(bundle_root.glob("*.bundle"))
    sprite_bundles: list[Path] = []
    audio_bundles: list[Path] = []
    image_candidates: dict[str, list[tuple[Path, str]]] = {
        "food_icons": [],
        "food_plates": [],
        "ingredient_icons": [],
        "beverage_icons": [],
        "character_sprites": [],
    }

    for bundle_path in bundle_paths:
        has_audio = False
        has_target_sprite = False
        try:
            env = _load_bundle(bundle_path)
        except Exception:
            continue
        for obj in env.objects:
            if obj.type.name == "AudioClip":
                has_audio = True
                continue
            if obj.type.name != "Sprite":
                continue
            try:
                sprite_name = str(obj.read().m_Name)
            except Exception:
                continue
            if _parse_named_id(sprite_name, "Foods") is not None:
                image_candidates["food_icons"].append((bundle_path, sprite_name))
                has_target_sprite = True
            elif _parse_named_id(sprite_name, "FoodPlates") is not None:
                image_candidates["food_plates"].append((bundle_path, sprite_name))
                has_target_sprite = True
            elif _parse_named_id(sprite_name, "Ingredients") is not None:
                image_candidates["ingredient_icons"].append((bundle_path, sprite_name))
                has_target_sprite = True
            elif _parse_named_id(sprite_name, "Beverages") is not None:
                image_candidates["beverage_icons"].append((bundle_path, sprite_name))
                has_target_sprite = True
            elif any(token in sprite_name for token in ["Mystia", "Wriggle", "Rumia", "Keine", "Cirno", "Reisen"]):
                image_candidates["character_sprites"].append((bundle_path, sprite_name))
                has_target_sprite = True
        if has_target_sprite:
            sprite_bundles.append(bundle_path)
        if has_audio:
            audio_bundles.append(bundle_path)

    related_sprite_paths = sorted(set(sprite_bundles + [
        path for path, _name in image_candidates["food_icons"]
        + image_candidates["food_plates"]
        + image_candidates["ingredient_icons"]
        + image_candidates["beverage_icons"]
        + image_candidates["character_sprites"]
    ]))
    image_root = output_root / ASSET_IMAGE_DIR
    image_root.mkdir(parents=True, exist_ok=True)

    images: list[dict[str, Any]] = []
    image_by_name: dict[str, dict[str, Any]] = {}
    exported_image_names: set[str] = set()
    if related_sprite_paths:
        env = _load_bundles(related_sprite_paths)
        wanted = {
            name: kind
            for kind, pairs in image_candidates.items()
            for _path, name in pairs
        }
        for obj in env.objects:
            if obj.type.name != "Sprite":
                continue
            try:
                sprite = obj.read()
                name = str(sprite.m_Name)
            except Exception:
                continue
            kind = wanted.get(name)
            if not kind:
                continue
            image_key = f"{kind}:{name}"
            if image_key in exported_image_names:
                continue
            try:
                image = sprite.image
            except Exception:
                continue
            if image is None:
                continue
            filename = f"{kind}__{_safe_asset_stem(name)}.png"
            relative_path = f"{ASSET_IMAGE_DIR}/{filename}"
            target = output_root / relative_path
            image.save(target)
            record = _image_record(name, kind, Path(getattr(obj.assets_file, "path", "")), relative_path, image.size)
            images.append(record)
            image_by_name[name] = record
            exported_image_names.add(image_key)

    audio_root = output_root / ASSET_AUDIO_DIR
    audio_root.mkdir(parents=True, exist_ok=True)
    audio: list[dict[str, Any]] = []
    for bundle_path in audio_bundles:
        try:
            env = _load_bundle(bundle_path)
        except Exception:
            continue
        for obj in env.objects:
            if obj.type.name != "AudioClip":
                continue
            try:
                clip = obj.read()
                clip_name = str(clip.m_Name)
                samples = dict(clip.samples)
            except Exception:
                continue
            for sample_name, sample_bytes in samples.items():
                suffix = Path(sample_name).suffix or ".wav"
                filename = f"{_safe_asset_stem(clip_name)}{suffix}"
                relative_path = f"{ASSET_AUDIO_DIR}/{filename}"
                target = output_root / relative_path
                target.write_bytes(sample_bytes)
                audio.append({
                    "name": clip_name,
                    "group": _audio_group(clip_name),
                    "bundle": bundle_path.name,
                    "path": relative_path,
                    "url": _asset_url(relative_path),
                    "bytes": len(sample_bytes),
                    "format": suffix.lstrip(".").lower(),
                })

    return {
        "images": sorted(images, key=lambda item: (item["kind"], item["name"])),
        "audio": sorted(audio, key=lambda item: (item["group"], item["name"])),
        "image_by_name": image_by_name,
        "asset_stats": {
            "image_count": len(images),
            "audio_count": len(audio),
            "sprite_bundle_count": len(sprite_bundles),
            "audio_bundle_count": len(audio_bundles),
        },
    }


def build_catalog(game_root: Path, output_root: Path) -> dict[str, Any]:
    bundle_root = game_root / AA_DIR
    profile_bundle = bundle_root / CORE_PROFILE_BUNDLE
    lang_bundle = bundle_root / SIMPLIFIED_LANG_BUNDLE
    if not profile_bundle.exists():
        raise FileNotFoundError(profile_bundle)
    if not lang_bundle.exists():
        raise FileNotFoundError(lang_bundle)

    profiles = _extract_profiles(profile_bundle)
    texts = _read_named_text_assets(lang_bundle)
    asset_catalog = _scan_asset_bundles(bundle_root, output_root)
    image_by_name = asset_catalog["image_by_name"]

    foods_lang = _id_map(_parse_tsv(texts["FoodsLang"]))
    ingredients_lang = _id_map(_parse_tsv(texts["IngredientsLang"]))
    beverages_lang = _id_map(_parse_tsv(texts["BeveragesLang"]))
    food_tags = _id_map(_parse_tsv(texts["FoodTagsLang"]))
    beverage_tags = _id_map(_parse_tsv(texts["BeverageTagsLang"]))
    cookers_lang = _id_map(_parse_tsv(texts["CookersLang"]), "ID")
    normal_guest_lang = _id_map(_parse_tsv(texts["NormGuestLang"]), "ID")
    special_guest_lang = _id_map(_parse_tsv(texts["SpecGuestLang"]), "ID")

    foods: list[dict[str, Any]] = []
    for row in profiles["FoodProfile"]["sellables"]:
        food_id = int(row["id"])
        lang = foods_lang.get(food_id, {})
        tags = [int(item) for item in row.get("tags", [])]
        foods.append({
            "id": food_id,
            "name": lang.get("name", str(food_id)),
            "description": lang.get("Description", ""),
            "level": row.get("level"),
            "base_value": row.get("baseValue"),
            "tag_ids": tags,
            "tags": _tag_names(tags, food_tags),
            "ban_tag_ids": row.get("banTags", []),
            "ban_tags": _tag_names([int(item) for item in row.get("banTags", [])], food_tags),
            "is_collab": bool(row.get("isCollab")),
            "assets": {
                "icon": image_by_name.get(f"Foods_{food_id}"),
                "plate": image_by_name.get(f"FoodPlates_{food_id}"),
            },
        })

    ingredients: list[dict[str, Any]] = []
    for row in profiles["IngredientProfile"]["ingredients"]:
        ingredient_id = int(row["id"])
        lang = ingredients_lang.get(ingredient_id, {})
        tags = [int(item) for item in row.get("tags", [])]
        ingredients.append({
            "id": ingredient_id,
            "name": lang.get("name", str(ingredient_id)),
            "description": lang.get("Description", ""),
            "level": row.get("level"),
            "base_value": row.get("baseValue"),
            "tag_ids": tags,
            "tags": _tag_names(tags, food_tags),
            "prefix": row.get("prefix"),
            "assets": {
                "icon": image_by_name.get(f"Ingredients_{ingredient_id}"),
            },
        })

    beverages: list[dict[str, Any]] = []
    for row in profiles["BeverageProfile"]["sellables"]:
        beverage_id = int(row["id"])
        lang = beverages_lang.get(beverage_id, {})
        tags = [int(item) for item in row.get("tags", [])]
        beverages.append({
            "id": beverage_id,
            "name": lang.get("name", str(beverage_id)),
            "description": lang.get("Description", ""),
            "level": row.get("level"),
            "base_value": row.get("baseValue"),
            "tag_ids": tags,
            "tags": _tag_names(tags, beverage_tags),
            "ban_tag_ids": row.get("banTags", []),
            "ban_tags": _tag_names([int(item) for item in row.get("banTags", [])], beverage_tags),
            "is_collab": bool(row.get("isCollab")),
            "assets": {
                "icon": image_by_name.get(f"Beverages_{beverage_id}"),
            },
        })

    ingredient_by_id = {item["id"]: item for item in ingredients}
    food_by_id = {item["id"]: item for item in foods}
    cooker_by_type = {
        int(row["type"]): row
        for row in profiles["CookerProfile"]["cookers"]
        if int(row.get("id", -999)) >= 0
    }
    recipes: list[dict[str, Any]] = []
    for row in profiles["RecipeProfile"]["recipes"]:
        food = food_by_id.get(int(row["foodID"]), {})
        cooker_type = int(row["cookerType"])
        cooker_id = int(cooker_by_type.get(cooker_type, {}).get("id", cooker_type))
        cooker_lang = cookers_lang.get(cooker_id, {})
        recipe_ingredients = [
            ingredient_by_id.get(int(ingredient_id), {"id": int(ingredient_id), "name": str(ingredient_id)})
            for ingredient_id in row.get("ingredients", [])
        ]
        recipes.append({
            "id": int(row["id"]),
            "food_id": int(row["foodID"]),
            "food_name": food.get("name", str(row["foodID"])),
            "ingredients": [{"id": item["id"], "name": item["name"]} for item in recipe_ingredients],
            "cooker_type": cooker_type,
            "cooker_name": cooker_lang.get("NAME") or cooker_lang.get("Name") or str(cooker_type),
            "cook_time": row.get("cookTime"),
            "food_tags": food.get("tags", []),
            "food_base_value": food.get("base_value"),
        })

    guests: list[dict[str, Any]] = []
    for row in profiles["NormalGuestProfile"]["normalGuests"]:
        guest_id = int(row["id"])
        lang = normal_guest_lang.get(guest_id, {})
        guests.append({
            "id": guest_id,
            "name": lang.get("NAME") or lang.get("Name") or str(guest_id),
            "description": lang.get("Description") or lang.get("DESCRIPTION") or "",
            "fund_multiplier": row.get("fundMultiplier"),
            "evaluation": row.get("evaluation"),
            "like_food_tag_ids": row.get("likeFoodTag", []),
            "like_food_tags": _tag_names([int(item) for item in row.get("likeFoodTag", [])], food_tags),
            "like_beverage_tag_ids": row.get("likeBevTag", []),
            "like_beverage_tags": _tag_names([int(item) for item in row.get("likeBevTag", [])], beverage_tags),
            "is_child": bool(row.get("isChild")),
        })

    special_guests: list[dict[str, Any]] = []
    for row in profiles["SpecialGuestProfile"]["specialGuests"]:
        guest_id = int(row["id"])
        lang = special_guest_lang.get(guest_id, {})
        special_guests.append({
            "id": guest_id,
            "string_id": row.get("stringId"),
            "name": lang.get("NAME") or lang.get("Name") or str(guest_id),
            "description": " ".join(
                value for key, value in lang.items()
                if key.lower().startswith("description") and value
            ),
            "fund_range": row.get("fundRange"),
            "endurance_limit": row.get("enduranceLimit"),
            "like_food_tags": _weighted_tag_names(row.get("likeFoodTag", []), food_tags),
            "hate_food_tags": _tag_names([int(item) for item in row.get("hateFoodTag", [])], food_tags),
            "like_beverage_tags": _weighted_tag_names(row.get("likeBevTag", []), beverage_tags),
            "commission_area": row.get("commisionAreaLabel"),
            "is_collab_character": bool(row.get("isCollabCharacter")),
        })

    catalog = {
        "schema_version": CATALOG_VERSION,
        "source": {
            "game_root": str(game_root),
            "profile_bundle": str(profile_bundle),
            "lang_bundle": str(lang_bundle),
            "bundle_xor_key": XOR_KEY,
            "asset_root": str(output_root),
        },
        "stats": {
            "foods": len(foods),
            "ingredients": len(ingredients),
            "beverages": len(beverages),
            "recipes": len(recipes),
            "guests": len(guests),
            "special_guests": len(special_guests),
            **asset_catalog["asset_stats"],
        },
        "foods": foods,
        "ingredients": ingredients,
        "beverages": beverages,
        "recipes": recipes,
        "guests": guests,
        "special_guests": special_guests,
        "images": asset_catalog["images"],
        "audio": asset_catalog["audio"],
    }
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "mystia_catalog.json").write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return catalog


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Touhou Mystia Izakaya reverse catalog.")
    parser.add_argument("--game-root", type=Path, default=DEFAULT_GAME_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_ANALYSIS_ROOT)
    args = parser.parse_args()
    catalog = build_catalog(args.game_root, args.output_root)
    print(json.dumps({"output": str(args.output_root / "mystia_catalog.json"), "stats": catalog["stats"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
