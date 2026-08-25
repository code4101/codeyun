from backend.core.fanxiu.catalog import inventory as fanxiu_inventory


def test_spirit_artifact_hall_defaults_to_fixed_artifacts(tmp_path, monkeypatch) -> None:
    storage_path = tmp_path / "fanxiu_inventory.json"
    monkeypatch.setattr(fanxiu_inventory, "get_inventory_storage_path", lambda: storage_path)

    hall = fanxiu_inventory.load_spirit_artifact_hall()

    assert [artifact["name"] for artifact in hall["artifacts"]] == [
        "血晶摩诃剑",
        "天月落星幡",
        "弥罗宝光幢",
        "鸿古干天戈",
        "青暝岁月灯",
        "苍烟神火炉",
        "御海镇神图",
        "六界轮回盘",
    ]
    assert [row["part_name"] for row in hall["artifacts"][0]["rows"]] == ["柄", "刃", "穗", "鞘", "珠", "纹"]
    assert hall["artifacts"][0]["rows"][0]["stat_raw_values"] == {
        "chaos_power": "",
        "attack": "",
        "spirit_power": "",
        "health": "",
        "defense": "",
    }
    assert hall["artifacts"][0]["rows"][0]["exclusive_stats"] == {"暴击附伤": "", "暴击": ""}
    assert hall["artifacts"][0]["rows"][0]["exclusive_stat_raw_values"] == {"暴击附伤": "", "暴击": ""}
    assert hall["artifacts"][4]["rows"][0]["exclusive_stats"] == {
        "灵宝抵御": "",
        "功法抵御": "",
        "全技能减伤": "",
    }
    assert hall["market_currency_count"] == 0
    assert hall["market_items"] == []
    assert hall["storage_bag_items"] == []


def test_spirit_artifact_hall_saves_and_loads_rows(tmp_path, monkeypatch) -> None:
    storage_path = tmp_path / "fanxiu_inventory.json"
    monkeypatch.setattr(fanxiu_inventory, "get_inventory_storage_path", lambda: storage_path)

    saved = fanxiu_inventory.save_spirit_artifact_hall(
        {
            "artifacts": [
                {
                    "name": "血晶摩诃剑",
                    "rows": [
                        {
                            "part_name": "柄",
                            "rank": 4,
                            "realm": 1,
                            "artifact_peerless_1": 25,
                            "artifact_peerless_2": 30,
                            "chaos_power": "123456789",
                            "attack": "100",
                            "stat_raw_values": {
                                "chaos_power": "5000",
                                "attack": "10000",
                                "unknown": "x",
                            },
                            "exclusive_stats": {
                                "暴击附伤": "200",
                                "暴击": "300",
                                "非本灵器属性": "999",
                            },
                            "exclusive_stat_raw_values": {
                                "暴击附伤": "10000",
                                "暴击": "30000",
                                "非本灵器属性": "999",
                            },
                        }
                    ],
                }
            ],
            "market_currency_count": 835,
            "market_items": [
                {"artifact_name": "血晶摩诃剑", "part_name": "珠", "cost": 80},
                {"artifact_name": "青冥岁月灯", "part_name": "荧", "cost": 80},
                {"artifact_name": "血晶摩诃剑", "part_name": "珠", "cost": 80},
                {"artifact_name": "血晶摩诃剑", "part_name": "不存在", "cost": 80},
            ],
            "storage_bag_items": [
                {
                    "title": "灵器部件自选箱",
                    "quantity": 3,
                    "choices": [
                        {"raw_name": "摩诃剑珠", "artifact_name": "血晶摩诃剑", "part_name": "珠"},
                        {"raw_name": "落星幡纹", "artifact_name": "天月落星幡", "part_name": "纹"},
                        {"raw_name": "重复", "artifact_name": "天月落星幡", "part_name": "纹"},
                        {"raw_name": "未知", "artifact_name": "未知灵器", "part_name": "珠"},
                    ],
                },
            ],
        }
    )
    loaded = fanxiu_inventory.load_spirit_artifact_hall()
    first_row = loaded["artifacts"][0]["rows"][0]

    assert saved == loaded
    assert first_row["part_name"] == "柄"
    assert first_row["rank"] == 4
    assert first_row["realm"] == 1
    assert first_row["artifact_peerless_1"] == 25
    assert first_row["artifact_peerless_2"] == 30
    assert first_row["chaos_power"] == "123456789"
    assert first_row["attack"] == "100"
    assert first_row["stat_raw_values"] == {
        "chaos_power": "5000",
        "attack": "10000",
        "spirit_power": "",
        "health": "",
        "defense": "",
    }
    assert first_row["exclusive_stats"] == {"暴击附伤": "200", "暴击": "300"}
    assert first_row["exclusive_stat_raw_values"] == {"暴击附伤": "10000", "暴击": "30000"}
    assert loaded["artifacts"][0]["rows"][1]["rank"] == 0
    assert loaded["market_currency_count"] == 835
    assert loaded["market_items"] == [
        {"order": 1, "artifact_name": "血晶摩诃剑", "part_name": "珠", "cost": 80},
        {"order": 2, "artifact_name": "青暝岁月灯", "part_name": "荧", "cost": 80},
    ]
    assert loaded["storage_bag_items"] == [
        {
            "order": 1,
            "title": "灵器部件自选箱",
            "quantity": 3,
            "choices": [
                {"order": 1, "raw_name": "摩诃剑珠", "artifact_name": "血晶摩诃剑", "part_name": "珠"},
                {"order": 2, "raw_name": "落星幡纹", "artifact_name": "天月落星幡", "part_name": "纹"},
            ],
        },
    ]


def test_spirit_artifact_hall_migrates_legacy_qingming_name(tmp_path, monkeypatch) -> None:
    storage_path = tmp_path / "fanxiu_inventory.json"
    monkeypatch.setattr(fanxiu_inventory, "get_inventory_storage_path", lambda: storage_path)

    saved = fanxiu_inventory.save_spirit_artifact_hall(
        {
            "artifacts": [
                {
                    "name": "青冥岁月灯",
                    "rows": [
                        {
                            "part_name": "盏",
                            "rank": 6,
                        }
                    ],
                }
            ]
        }
    )

    migrated_artifact = saved["artifacts"][4]
    assert migrated_artifact["name"] == "青暝岁月灯"
    assert migrated_artifact["rows"][0]["part_name"] == "盏"
    assert migrated_artifact["rows"][0]["rank"] == 6


def test_spirit_artifact_hall_migrates_legacy_aura_peerless(tmp_path, monkeypatch) -> None:
    storage_path = tmp_path / "fanxiu_inventory.json"
    monkeypatch.setattr(fanxiu_inventory, "get_inventory_storage_path", lambda: storage_path)

    saved = fanxiu_inventory.save_spirit_artifact_hall(
        {
            "artifacts": [
                {
                    "name": "血晶摩诃剑",
                    "rows": [
                        {
                            "part_name": "柄",
                            "aura_peerless": 25,
                        }
                    ],
                }
            ]
        }
    )

    first_row = saved["artifacts"][0]["rows"][0]
    assert first_row["artifact_peerless_1"] == 25
    assert first_row["artifact_peerless_2"] == 0
    assert "aura_peerless" not in first_row
