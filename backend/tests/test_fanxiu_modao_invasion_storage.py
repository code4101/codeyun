from backend.core.fanxiu.catalog import inventory as fanxiu_inventory


def test_modao_invasion_exchange_list_roundtrip(tmp_path, monkeypatch) -> None:
    storage_path = tmp_path / "fanxiu_inventory.json"
    monkeypatch.setattr(fanxiu_inventory, "get_inventory_storage_path", lambda: storage_path)

    saved = fanxiu_inventory.save_modao_invasion_exchange_list(
        {
            "records": [
                {
                    "activity_id": "activity-32",
                    "label": "32跨",
                    "personal_rankings": [
                        {
                            "rank": "1",
                            "name": "福泽丶叶秋",
                            "plane": "福泽天下",
                            "merit": "20443646",
                        },
                        {
                            "id": "personal-2",
                            "rank": None,
                            "name": "春风、子墨尘心",
                            "plane": "心向往之",
                            "merit": -8616919,
                        },
                    ],
                    "items": [
                        {
                            "name": "建木果·绝品",
                            "magic_crystal_cost": "1000",
                            "purchase_limit": "20",
                            "checked": True,
                        },
                        {
                            "id": "item-2",
                            "name": "坤元土灵碎片",
                            "magic_crystal_cost": -80,
                            "purchase_limit": None,
                            "checked": "false",
                        },
                    ],
                },
                {
                    "id": "record-2",
                    "activity_id": "activity-16",
                    "label": "16跨",
                    "items": [],
                },
            ]
        }
    )

    loaded = fanxiu_inventory.load_modao_invasion_exchange_list()

    assert saved == loaded
    assert loaded["records"][0]["activity_id"] == "activity-32"
    assert loaded["records"][0]["label"] == "32跨"
    assert loaded["records"][0]["personal_rankings"][0]["rank"] == 1
    assert loaded["records"][0]["personal_rankings"][0]["name"] == "福泽丶叶秋"
    assert loaded["records"][0]["personal_rankings"][0]["plane"] == "福泽天下"
    assert loaded["records"][0]["personal_rankings"][0]["merit"] == 20443646
    assert loaded["records"][0]["personal_rankings"][1]["id"] == "personal-2"
    assert loaded["records"][0]["personal_rankings"][1]["rank"] == 0
    assert loaded["records"][0]["personal_rankings"][1]["merit"] == 0
    assert loaded["records"][0]["items"][0]["name"] == "建木果·绝品"
    assert loaded["records"][0]["items"][0]["magic_crystal_cost"] == 1000
    assert loaded["records"][0]["items"][0]["purchase_limit"] == 20
    assert loaded["records"][0]["items"][0]["checked"] is True
    assert loaded["records"][0]["items"][1]["id"] == "item-2"
    assert loaded["records"][0]["items"][1]["magic_crystal_cost"] == 0
    assert loaded["records"][0]["items"][1]["purchase_limit"] == 0
    assert loaded["records"][0]["items"][1]["checked"] is False
    assert loaded["records"][1]["id"] == "record-2"
    assert loaded["records"][1]["label"] == "16跨"


def test_modao_invasion_exchange_list_migrates_legacy_items_to_default_record(tmp_path, monkeypatch) -> None:
    storage_path = tmp_path / "fanxiu_inventory.json"
    monkeypatch.setattr(fanxiu_inventory, "get_inventory_storage_path", lambda: storage_path)

    saved = fanxiu_inventory.save_modao_invasion_exchange_list(
        {
            "items": [
                {
                    "name": "建木果·绝品",
                    "magic_crystal_cost": 1000,
                    "purchase_limit": 20,
                }
            ]
        }
    )

    assert len(saved["records"]) == 1
    assert saved["records"][0]["id"] == "modao-invasion-record-32"
    assert saved["records"][0]["activity_id"] == ""
    assert saved["records"][0]["label"] == "32跨"
    assert saved["records"][0]["personal_rankings"] == []
    assert saved["records"][0]["items"][0]["name"] == "建木果·绝品"


def test_shouyuan_exploration_exchange_list_uses_own_collection_and_default_label(tmp_path, monkeypatch) -> None:
    storage_path = tmp_path / "fanxiu_inventory.json"
    monkeypatch.setattr(fanxiu_inventory, "get_inventory_storage_path", lambda: storage_path)

    fanxiu_inventory.save_modao_invasion_exchange_list(
        {
            "records": [
                {
                    "id": "modao-record",
                    "activity_id": "modao-activity",
                    "label": "32跨",
                    "items": [{"name": "建木果·绝品", "magic_crystal_cost": 1000, "purchase_limit": 20}],
                }
            ]
        }
    )
    saved = fanxiu_inventory.save_shouyuan_exploration_exchange_list(
        {
            "items": [
                {
                    "name": "星海火树",
                    "magic_crystal_cost": 2500,
                    "purchase_limit": 8,
                }
            ],
            "income_speeds": [
                {
                    "captured_date": "2026-04-27",
                    "search_count": "50",
                    "beast_crystal": "7500",
                    "score": "14000",
                    "merit": "13500",
                    "remark": "低加成测试",
                }
            ],
            "consumption_evaluations": [
                {
                    "label": "兽晶目标",
                    "current": "15099",
                    "target": "60000",
                    "speed": "150.5",
                }
            ],
        }
    )

    loaded_shouyuan = fanxiu_inventory.load_shouyuan_exploration_exchange_list()
    loaded_modao = fanxiu_inventory.load_modao_invasion_exchange_list()

    assert saved == loaded_shouyuan
    assert loaded_shouyuan["records"][0]["id"] == "shouyuan-exploration-record-8"
    assert loaded_shouyuan["records"][0]["label"] == "8跨"
    assert loaded_shouyuan["records"][0]["income_speeds"][0]["captured_date"] == "2026-04-27"
    assert loaded_shouyuan["records"][0]["income_speeds"][0]["search_count"] == 50
    assert loaded_shouyuan["records"][0]["income_speeds"][0]["beast_crystal"] == 7500
    assert loaded_shouyuan["records"][0]["income_speeds"][0]["score"] == 14000
    assert loaded_shouyuan["records"][0]["income_speeds"][0]["merit"] == 13500
    assert loaded_shouyuan["records"][0]["income_speeds"][0]["remark"] == "低加成测试"
    assert loaded_shouyuan["records"][0]["consumption_evaluations"][0]["label"] == "兽晶目标"
    assert loaded_shouyuan["records"][0]["consumption_evaluations"][0]["current"] == 15099
    assert loaded_shouyuan["records"][0]["consumption_evaluations"][0]["target"] == 60000
    assert loaded_shouyuan["records"][0]["consumption_evaluations"][0]["speed"] == 150.5
    assert loaded_shouyuan["records"][0]["items"][0]["name"] == "星海火树"
    assert loaded_modao["records"][0]["id"] == "modao-record"
    assert loaded_modao["records"][0]["items"][0]["name"] == "建木果·绝品"


def test_shouyuan_exploration_exchange_list_missing_collection_loads_empty(tmp_path, monkeypatch) -> None:
    storage_path = tmp_path / "fanxiu_inventory.json"
    monkeypatch.setattr(fanxiu_inventory, "get_inventory_storage_path", lambda: storage_path)

    assert fanxiu_inventory.load_shouyuan_exploration_exchange_list() == {"records": []}
