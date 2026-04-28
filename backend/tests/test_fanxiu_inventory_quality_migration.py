import json

from backend.core.fanxiu_inventory import (
    _MAGIC_TREASURE_HALL_KEY,
    _MAGIC_TREASURE_SECTION_KEYS,
    _SPIRIT_BEAST_HALL_KEY,
    _SPIRIT_BEAST_SECTION_KEYS,
    _load_inventory_hall,
    _save_inventory_hall,
)


def _inventory_item(quality: int) -> dict:
    return {
        "id": "item-1",
        "name": "测试法宝",
        "rank": 23,
        "shenlian": 0,
        "quality": quality,
        "date": "2026-04-23",
    }


def test_load_inventory_hall_shifts_legacy_quality_values() -> None:
    storage = {
        "version": 1,
        "warehouses": {
            _MAGIC_TREASURE_HALL_KEY: {
                "fabao": [_inventory_item(6), _inventory_item(7)],
            }
        },
    }

    hall = _load_inventory_hall(storage, _MAGIC_TREASURE_HALL_KEY, _MAGIC_TREASURE_SECTION_KEYS)

    assert hall["fabao"][0]["quality"] == 6
    assert hall["fabao"][1]["quality"] == 8


def test_load_inventory_hall_keeps_new_schema_quality_values() -> None:
    storage = {
        "version": 2,
        "warehouses": {
            _MAGIC_TREASURE_HALL_KEY: {
                "fabao": [_inventory_item(7)],
            }
        },
    }

    hall = _load_inventory_hall(storage, _MAGIC_TREASURE_HALL_KEY, _MAGIC_TREASURE_SECTION_KEYS)

    assert hall["fabao"][0]["quality"] == 7


def test_save_inventory_hall_migrates_other_legacy_halls_before_version_bump(tmp_path) -> None:
    storage_path = tmp_path / "fanxiu_inventory.json"
    storage = {
        "version": 1,
        "warehouses": {
            _SPIRIT_BEAST_HALL_KEY: {
                "lingshou": [_inventory_item(7)],
            }
        },
        "collections": {},
    }

    saved = _save_inventory_hall(
        storage,
        storage_path,
        _MAGIC_TREASURE_HALL_KEY,
        _MAGIC_TREASURE_SECTION_KEYS,
        {"fabao": [_inventory_item(7)]},
    )
    written = json.loads(storage_path.read_text(encoding="utf-8"))

    assert saved["fabao"][0]["quality"] == 7
    assert written["version"] == 2
    assert written["warehouses"][_MAGIC_TREASURE_HALL_KEY]["fabao"][0]["quality"] == 7
    assert written["warehouses"][_SPIRIT_BEAST_HALL_KEY]["lingshou"][0]["quality"] == 8
    assert set(written["warehouses"][_SPIRIT_BEAST_HALL_KEY]) == set(_SPIRIT_BEAST_SECTION_KEYS)
