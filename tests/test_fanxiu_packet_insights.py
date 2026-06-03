import json

from backend.core import fanxiu_packet_insights as insights
from backend.core.fanxiu_server_mapping import resolve_fanxiu_region_server_by_id


def test_packet_runtime_insights_extracts_account_wallet_bag_and_rank(tmp_path, monkeypatch):
    entries = [
        {
            "id": "login",
            "decoded_at": "2026-06-01 10:00:00",
            "record_id": "r1",
            "pcap_name": "a.pcap",
            "name": "SM_Login",
            "category": "未归类",
            "content": {
                "_class": "SM_Login",
                "accountId": "22077:1@MOBI37",
                "token": "secret",
                "role": {"roleId": 24082878061086206, "name": "止清ღ羊驼", "level": 230, "vipLevel": 9, "battleScore": 123456789},
            },
        },
        {
            "id": "wallet",
            "decoded_at": "2026-06-01 10:01:00",
            "record_id": "r1",
            "pcap_name": "a.pcap",
            "name": "SM_Wallet",
            "category": "奖励/消耗/道具",
            "content": {"_class": "SM_Wallet", "items": {"items": [{"type": 1, "amount": 99, "history": 120, "borrow": 0}]}},
        },
        {
            "id": "bag",
            "decoded_at": "2026-06-01 10:02:00",
            "record_id": "r1",
            "pcap_name": "a.pcap",
            "name": "SM_AllBagSyncInfo",
            "category": "奖励/消耗/道具",
            "content": {
                "_class": "SM_AllBagSyncInfo",
                "bagInfoVOs": {
                    "items": [
                        {
                            "itemVOs": {
                                "_count": 2,
                                "items": [
                                    {"ext": "{\"grade\":8}", "_super": {"baseId": 2001, "id": 5001, "num": 3}},
                                ],
                            }
                        }
                    ]
                },
            },
        },
        {
            "id": "show-other",
            "decoded_at": "2026-06-01 10:03:00",
            "record_id": "r1",
            "pcap_name": "a.pcap",
            "name": "SM_ShowOther",
            "category": "角色/属性",
            "content": {
                "_class": "SM_ShowOther",
                "otherRoleVO": {
                    "roleId": 24082878061087586,
                    "name": "凌霄༅青风”",
                    "server": 22077,
                    "level": 230,
                    "vipLevel": 9,
                    "battleScore": 9.04871285964366e19,
                    "location": "湖南",
                    "attrMap": {
                        "_count": 10,
                        "items": [
                            {"key": 7739004, "value": 21275.0},
                            {"key": 99, "value": 77244.0},
                            {"key": 109, "value": 6821280.0},
                            {"key": 110, "value": 6806700.0},
                            {"key": 111, "value": 1370900.0},
                            {"key": 112, "value": 6802960.0},
                            {"key": 35006, "value": 1.3124726699597634e19},
                            {"key": 2001, "value": 2.844220239520223e18},
                            {"key": 3001, "value": 5.593171933324373e18},
                            {"key": 4001, "value": 17935316859795.0},
                        ],
                    },
                },
            },
        },
        {
            "id": "show-other-old",
            "decoded_at": "2026-06-01 09:03:00",
            "record_id": "r1",
            "pcap_name": "a.pcap",
            "name": "SM_ShowOther",
            "category": "角色/属性",
            "content": {
                "_class": "SM_ShowOther",
                "otherRoleVO": {
                    "roleId": 24082878061087586,
                    "name": "凌霄༅青风”",
                    "server": 22077,
                    "level": 230,
                    "vipLevel": 9,
                    "battleScore": 1,
                    "attrMap": {"_count": 2, "items": [{"key": 7739004, "value": 1.0}, {"key": 99, "value": 2.0}]},
                },
            },
        },
        {
            "id": "sync-player",
            "decoded_at": "2026-06-01 10:02:30",
            "record_id": "r1",
            "pcap_name": "a.pcap",
            "name": "SM_SyncPlayer",
            "category": "场景/移动",
            "content": {
                "_class": "SM_SyncPlayer",
                "playerVO": {
                    "playerName": "止清",
                    "server": 22077,
                    "realServer": 22077,
                    "vipLevel": 9,
                    "fightScore": 1234567890000.0,
                    "_super": {
                        "id": 24082878061087500,
                        "attrMap": {
                            "_count": 4,
                            "items": [
                                {"key": 7739004, "value": 36868.0},
                                {"key": 99, "value": 99592.0},
                                {"key": 35006, "value": 3.989e19},
                                {"key": 2001, "value": 939600000000000000.0},
                                {"key": 4001, "value": 48800000000000.0},
                            ],
                        },
                    },
                },
            },
        },
        {
            "id": "sync-player-no-attack",
            "decoded_at": "2026-06-01 10:05:30",
            "record_id": "r1",
            "pcap_name": "a.pcap",
            "name": "SM_SyncPlayer",
            "category": "场景/移动",
            "content": {
                "_class": "SM_SyncPlayer",
                "playerVO": {
                    "playerName": "止清",
                    "server": 22077,
                    "realServer": 22077,
                    "vipLevel": 9,
                    "fightScore": 9876543210000.0,
                    "_super": {
                        "id": 24082878061087500,
                        "attrMap": {
                            "_count": 2,
                            "items": [
                                {"key": 35006, "value": 9.0},
                                {"key": 3001, "value": 8.0},
                            ],
                        },
                    },
                },
            },
        },
        {
            "id": "worship-record",
            "decoded_at": "2026-06-01 10:04:00",
            "record_id": "r1",
            "pcap_name": "a.pcap",
            "name": "SM_WorshipGotRecord",
            "category": "玩法状态",
            "content": {
                "_class": "SM_WorshipGotRecord",
                "fazeId": 58,
                "crossGroup": 8,
                "recordVOList": {
                    "items": [
                        {
                            "activityId": 2,
                            "score": 82931,
                            "gotTime": 1780308000,
                            "worshipRoleVO": {"playerId": 1, "server": 101, "playerName": "凌霄༅青风”"},
                            "redRoleVO": {"playerId": 2, "server": 102, "playerName": "止清"},
                        }
                    ]
                },
            },
        },
    ]
    monkeypatch.setattr(insights, "_build_fanxiu_tcp_entries", lambda *_args, **_kwargs: entries)
    monkeypatch.setattr(insights, "sync_fanxiu_activity_packets", lambda **_kwargs: {"ok": True})
    monkeypatch.setattr(
        insights,
        "get_fanxiu_activity_rank_records",
        lambda *_args, **_kwargs: {
            "records": [
                {
                    "last_seen_at": "2026-06-01 10:03:00",
                    "snapshot": {
                        "activity_id": "11621602",
                        "group": 0,
                        "personal_item": {"rank": 34, "name": "止清", "score": 102},
                        "items": [{"rank": 1, "name": "榜首"}],
                    },
                }
            ]
        },
    )
    monkeypatch.setattr(
        insights,
        "_load_item_index",
        lambda **_kwargs: {"cards_by_id": {"2001": {"name": "测试仙玉", "quality_name": "仙品", "type_name": "资源"}}},
    )

    record_dir = tmp_path / "fanxiu" / "tcp-flow" / "r1"
    record_dir.mkdir(parents=True)
    decoded_path = record_dir / "decoded.json"
    decoded_path.write_text(json.dumps({"frames": []}), encoding="utf-8")
    (record_dir / "meta.json").write_text(json.dumps({"decoded_path": str(decoded_path)}), encoding="utf-8")

    result = insights.sync_fanxiu_packet_runtime_insights(data_dir=tmp_path, force=True)
    snapshot = result["snapshot"]

    assert snapshot["account"]["latest_login"]["name"] == "止清ღ羊驼"
    assert snapshot["wallet"]["resources"][0]["amount"] == 99
    assert snapshot["bag"]["stack_count"] == 2
    assert snapshot["bag"]["owner_name"] == "止清ღ羊驼"
    assert snapshot["bag"]["owner_role_id_text"] == "24082878061086206"
    assert snapshot["bag"]["decoded_stack_count"] == 1
    assert snapshot["bag"]["items"][0]["item"]["name"] == "测试仙玉"
    assert snapshot["bag"]["notable_items"][0]["item"]["name"] == "测试仙玉"
    assert snapshot["player_profiles"]["count"] == 4
    assert snapshot["player_profiles"]["daily_count"] == 2
    assert snapshot["player_profiles"]["latest"]["name"] == "凌霄༅青风”"
    assert snapshot["player_profiles"]["latest"]["role_id_text"] == "24082878061087586"
    assert snapshot["player_profiles"]["latest"]["region_name"] == "天澜圣殿"
    assert snapshot["player_profiles"]["latest"]["server_name"] == "岁序更替"
    assert snapshot["player_profiles"]["latest"]["server_order"] == 53
    assert snapshot["player_profiles"]["latest"]["cultivation_level"] == 230
    assert snapshot["player_profiles"]["latest"]["cultivation_level_text"] == "大乘后期拾层"
    assert snapshot["player_profiles"]["daily_records"][0]["captured_at"] == "2026-06-01 10:03:00"
    assert all(any(attr["key"] == 2001 for attr in row["combat_attributes"]) for row in snapshot["player_profiles"]["daily_records"])
    sync_profile = next(row for row in snapshot["player_profiles"]["daily_records"] if row["name"] == "止清")
    assert sync_profile["source_kind"] == "sync_player"
    assert sync_profile["role_id_text"] == "24082878061087500"
    assert sync_profile["region_name"] == "天澜圣殿"
    assert sync_profile["server_name"] == "岁序更替"
    assert sync_profile["special_attributes"] == [
        {"key": 7739004, "name": "神识", "value": 36868.0, "text": "36868"},
        {"key": 99, "name": "天资", "value": 99592.0, "text": "99592"},
    ]
    assert sync_profile["combat_attributes"][-1] == {"key": 4001, "name": "守御", "value": 48800000000000.0, "text": "48.8兆"}
    assert sync_profile["captured_at"] == "2026-06-01 10:02:30"
    assert sync_profile["battle_score_text"] == "1.235兆"
    assert snapshot["player_profiles"]["latest"]["special_attributes"] == [
        {"key": 7739004, "name": "神识", "value": 21275.0, "text": "21275"},
        {"key": 99, "name": "天资", "value": 77244.0, "text": "77244"},
    ]
    assert snapshot["player_profiles"]["latest"]["immortal_attributes"][0] == {"key": 109, "name": "仙魂", "value": 6821280.0, "text": "682.1万"}
    assert snapshot["player_profiles"]["latest"]["combat_attributes"][1] == {"key": 2001, "name": "攻击", "value": 2.844220239520223e18, "text": "284.4京"}
    assert snapshot["player_profiles"]["latest"]["combat_attributes"][-1] == {"key": 4001, "name": "守御", "value": 17935316859795.0, "text": "17.94兆"}
    assert snapshot["activity_ranks"]["personal_count"] == 1
    assert snapshot["worship"]["count"] == 1
    assert snapshot["worship"]["records"][0]["role"] == "凌霄༅青风”"
    assert snapshot["worship"]["records"][0]["worship_role"] == "凌霄༅青风”"
    assert snapshot["worship"]["records"][0]["red_role"] == "止清"
    assert snapshot["worship"]["records"][0]["plane_label"] == "8跨"
    assert snapshot["worship"]["records"][0]["rank_type_label"] == "社团"
    assert snapshot["worship"]["records"][0]["friendship"] == 82931
    assert snapshot["worship"]["records"][0]["protocol"] == "SM_WorshipGotRecord"


def test_packet_runtime_insights_builds_database_business_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(insights, "resolve_fanxiu_tcp_store_root", lambda data_dir=None: tmp_path)
    monkeypatch.setattr(insights, "resolve_fanxiu_export_root", lambda export_root=None: tmp_path)
    monkeypatch.setattr(insights, "load_fanxiu_item_runtime_index", lambda export_root=None: {})
    monkeypatch.setattr(
        insights,
        "_build_fanxiu_tcp_entries",
        lambda root, export_root=None: [
            {
                "id": "login",
                "decoded_at": "2026-06-01 10:00:00",
                "record_id": "r1",
                "pcap_name": "a.pcap",
                "name": "SM_Login",
                "content": {
                    "_class": "SM_Login",
                    "accountId": "22077:1@MOBI37",
                    "role": {"roleId": 24082878061086206, "name": "止清ღ羊驼", "server": 22077},
                },
            },
            {
                "id": "wallet",
                "decoded_at": "2026-06-01 10:01:00",
                "record_id": "r1",
                "pcap_name": "a.pcap",
                "name": "SM_Wallet",
                "content": {"_class": "SM_Wallet", "items": {"items": [{"type": 1, "code": 2, "amount": 99}]}},
            },
            {
                "id": "bag",
                "decoded_at": "2026-06-01 10:02:00",
                "record_id": "r1",
                "pcap_name": "a.pcap",
                "name": "SM_AllBagSyncInfo",
                "content": {
                    "_class": "SM_AllBagSyncInfo",
                    "bagInfoVOs": {"items": [{"itemVOs": {"items": [{"_super": {"baseId": 2001, "id": 5001, "num": 3}}]}}]},
                },
            },
        ],
    )

    snapshot = insights.build_fanxiu_packet_runtime_insights(data_dir=tmp_path, export_root=tmp_path)
    rows = insights._runtime_business_record_rows(snapshot)
    domains = {row["domain"] for row in rows}

    assert "wallet_resource" in domains
    assert "storage_bag_item" in domains
    assert all(row["record_key"] for row in rows)


def test_packet_runtime_insights_builds_self_profile_from_attribute_changes(tmp_path, monkeypatch):
    monkeypatch.setattr(insights, "resolve_fanxiu_tcp_store_root", lambda data_dir=None: tmp_path)
    monkeypatch.setattr(insights, "resolve_fanxiu_export_root", lambda export_root=None: tmp_path)
    monkeypatch.setattr(insights, "load_fanxiu_item_runtime_index", lambda export_root=None: {})
    monkeypatch.setattr(
        insights,
        "_build_fanxiu_tcp_entries",
        lambda root, export_root=None: [
            {
                "id": "login",
                "decoded_at": "2026-06-03 10:00:00",
                "record_id": "r1",
                "pcap_name": "a.pcap",
                "name": "SM_Login",
                "content": {
                    "_class": "SM_Login",
                    "accountId": "22077:1@MOBI37",
                    "role": {"roleId": 24082878061086206, "name": "止清ღ羊驼", "server": 22077, "level": 230, "vipLevel": 9},
                },
            },
            {
                "id": "attrs",
                "decoded_at": "2026-06-03 12:28:50",
                "record_id": "r2",
                "pcap_name": "b.pcap",
                "name": "SM_RoleChangedAttrs",
                "pro_id": 30021,
                "content": {
                    "_class": "SM_RoleChangedAttrs",
                    "attrs": {
                        "finalAttrs": {"items": [{"key": 2001, "value": 9.398173848160592e18}, {"key": 35006, "value": 3.989350322728575e19}]},
                        "addAttrs": {"items": [{"key": 2001, "value": 517631111739392.0}]},
                    },
                },
            },
        ],
    )

    snapshot = insights.build_fanxiu_packet_runtime_insights(data_dir=tmp_path, export_root=tmp_path)
    row = snapshot["player_profiles"]["daily_records"][0]

    assert row["name"] == "止清ღ羊驼"
    assert row["source_kind"] == "self_attribute_change"
    assert row["server_name"] == "岁序更替"
    assert row["cultivation_level"] == 230
    assert row["cultivation_level_text"] == "大乘后期拾层"
    assert row["combat_attributes"][1] == {"key": 2001, "name": "攻击", "value": 9.398173848160592e18, "text": "939.8京"}


def test_packet_runtime_insights_stores_non_owner_storage_bag_without_default_display(tmp_path, monkeypatch):
    monkeypatch.setattr(insights, "resolve_fanxiu_tcp_store_root", lambda data_dir=None: tmp_path)
    monkeypatch.setattr(insights, "resolve_fanxiu_export_root", lambda export_root=None: tmp_path)
    monkeypatch.setattr(insights, "load_fanxiu_item_runtime_index", lambda export_root=None: {})
    monkeypatch.setattr(
        insights,
        "_build_fanxiu_tcp_entries",
        lambda root, export_root=None: [
            {
                "id": "other-login",
                "decoded_at": "2026-06-03 10:00:00",
                "record_id": "r1",
                "pcap_name": "a.pcap",
                "name": "SM_Login",
                "content": {
                    "_class": "SM_Login",
                    "accountId": "22077:2@MOBI37",
                    "role": {"roleId": 24082878061080000, "name": "止清ღ小号", "server": 22077},
                },
            },
            {
                "id": "other-bag",
                "decoded_at": "2026-06-03 10:01:00",
                "record_id": "r1",
                "pcap_name": "a.pcap",
                "name": "SM_AllBagSyncInfo",
                "content": {
                    "_class": "SM_AllBagSyncInfo",
                    "bagInfoVOs": {"items": [{"itemVOs": {"items": [{"_super": {"baseId": 2001, "id": 5001, "num": 999}}]}}]},
                },
            },
        ],
    )

    snapshot = insights.build_fanxiu_packet_runtime_insights(data_dir=tmp_path, export_root=tmp_path)

    assert snapshot["account"]["latest_login"]["name"] == "止清ღ小号"
    assert snapshot["bag"] is None
    assert len(snapshot["bag_records"]) == 1
    assert snapshot["bag_records"][0]["owner_name"] == "止清ღ小号"
    assert snapshot["bag_records"][0]["items"][0]["num"] == 999


def test_packet_runtime_insights_keeps_owner_storage_bag_after_account_switch(tmp_path, monkeypatch):
    monkeypatch.setattr(insights, "resolve_fanxiu_tcp_store_root", lambda data_dir=None: tmp_path)
    monkeypatch.setattr(insights, "resolve_fanxiu_export_root", lambda export_root=None: tmp_path)
    monkeypatch.setattr(insights, "load_fanxiu_item_runtime_index", lambda export_root=None: {})
    monkeypatch.setattr(
        insights,
        "_build_fanxiu_tcp_entries",
        lambda root, export_root=None: [
            {
                "id": "owner-login",
                "decoded_at": "2026-06-03 10:00:00",
                "record_id": "r1",
                "pcap_name": "a.pcap",
                "name": "SM_Login",
                "content": {
                    "_class": "SM_Login",
                    "accountId": "22077:1@MOBI37",
                    "role": {"roleId": 24082878061086206, "name": "止清ღ羊驼", "server": 22077},
                },
            },
            {
                "id": "owner-bag",
                "decoded_at": "2026-06-03 10:01:00",
                "record_id": "r1",
                "pcap_name": "a.pcap",
                "name": "SM_AllBagSyncInfo",
                "content": {
                    "_class": "SM_AllBagSyncInfo",
                    "bagInfoVOs": {"items": [{"itemVOs": {"items": [{"_super": {"baseId": 2001, "id": 5001, "num": 1}}]}}]},
                },
            },
            {
                "id": "other-login",
                "decoded_at": "2026-06-03 10:02:00",
                "record_id": "r2",
                "pcap_name": "b.pcap",
                "name": "SM_Login",
                "content": {
                    "_class": "SM_Login",
                    "accountId": "22077:2@MOBI37",
                    "role": {"roleId": 24082878061080000, "name": "止清ღ小号", "server": 22077},
                },
            },
            {
                "id": "other-bag",
                "decoded_at": "2026-06-03 10:03:00",
                "record_id": "r2",
                "pcap_name": "b.pcap",
                "name": "SM_AllBagSyncInfo",
                "content": {
                    "_class": "SM_AllBagSyncInfo",
                    "bagInfoVOs": {"items": [{"itemVOs": {"items": [{"_super": {"baseId": 2001, "id": 5002, "num": 999}}]}}]},
                },
            },
        ],
    )

    snapshot = insights.build_fanxiu_packet_runtime_insights(data_dir=tmp_path, export_root=tmp_path)

    assert snapshot["account"]["latest_login"]["name"] == "止清ღ小号"
    assert snapshot["bag"]["owner_name"] == "止清ღ羊驼"
    assert snapshot["bag"]["items"][0]["num"] == 1
    assert len(snapshot["bag_records"]) == 2
    assert {row["owner_name"] for row in snapshot["bag_records"]} == {"止清ღ羊驼", "止清ღ小号"}


def test_player_profile_cultivation_level_formats_panel_text() -> None:
    assert insights._format_player_cultivation_level(201) == "大乘前期壹层"
    assert insights._format_player_cultivation_level(217) == "大乘中期柒层"
    assert insights._format_player_cultivation_level(230) == "大乘后期拾层"


def test_panel_number_formats_large_chinese_units() -> None:
    assert insights._format_panel_number(3.03972e20) == "3.04垓"
    assert insights._format_panel_number(1.23456e24) == "1.235秭"


def test_worship_plane_label_formats_local_and_cross_values():
    assert insights._worship_plane_label(1) == "1跨"
    assert insights._worship_plane_label("1") == "1跨"
    assert insights._worship_plane_label(2) == "2跨"
    assert insights._worship_plane_label("2") == "2跨"
    assert insights._worship_plane_label(8) == "8跨"


def test_server_id_formula_matches_known_tianlan_server_names() -> None:
    assert resolve_fanxiu_region_server_by_id(22076) == {
        "server_id": 22076,
        "region_number": 17,
        "region_name": "天澜圣殿",
        "server_order": 52,
        "server_name": "海浪无声",
        "global_order": 1076,
        "known": True,
        "source": "server_id_formula",
    }
    assert resolve_fanxiu_region_server_by_id(22077)["server_name"] == "岁序更替"
    assert resolve_fanxiu_region_server_by_id(22079)["server_name"] == "金相玉质"


def test_worship_activity_cfg_infers_cross_guild_from_activity_base_id(tmp_path, monkeypatch):
    cfg_root = tmp_path / "activity" / "text_assets"
    cfg_root.mkdir(parents=True)
    (cfg_root / "Activity.lua").write_text(
        "\n".join(
            [
                "[4043201]=setmetatable({[1]=4043201,[18]=4,[28]=43200},_P),",
                "[4042801]=setmetatable({[1]=4042801,[18]=4,[28]=42800},_P),",
            ]
        ),
        encoding="utf-8",
    )
    (cfg_root / "ActivityList.lua").write_text("", encoding="utf-8")
    monkeypatch.setattr(insights, "_generated_cfg_dir", lambda *_args, **_kwargs: cfg_root)
    insights._worship_activity_cfg.cache_clear()

    assert insights._worship_activity_cfg(4043201)["rank_type_label"] == "社团"
    assert insights._worship_activity_cfg(4042801)["rank_type_label"] == "个人"

    insights._worship_activity_cfg.cache_clear()


def test_worship_target_label_infers_talent_and_daodan():
    assert insights._worship_target_label(activity_cfg={"activity_base_id": 42800}) == "天资"
    assert insights._worship_target_label(activity_cfg={"activity_base_id": 43200}) == "天资"
    assert insights._worship_target_label(activity_cfg={"activity_base_id": 43100}) == "道丹"
    assert insights._worship_target_label(activity_cfg={}, faze_id=10052) == "天资"
    assert insights._worship_target_label(activity_cfg={}, faze_id=10073) == "道丹"


def test_packet_runtime_insights_reuses_snapshot_when_sources_unchanged(tmp_path, monkeypatch):
    calls = {"build": 0, "activity_sync": 0}

    def fake_build(**_kwargs):
        calls["build"] += 1
        return {
            "schema_version": insights.PACKET_INSIGHT_SCHEMA_VERSION,
            "updated_at": "2026-06-01 10:00:00",
            "source_summary": {"entry_count": 1},
            "observations": [],
        }

    def fake_activity_sync(**_kwargs):
        calls["activity_sync"] += 1
        return {"ok": True}

    monkeypatch.setattr(insights, "build_fanxiu_packet_runtime_insights", fake_build)
    monkeypatch.setattr(insights, "sync_fanxiu_activity_packets", fake_activity_sync)

    record_dir = tmp_path / "fanxiu" / "tcp-flow" / "r1"
    record_dir.mkdir(parents=True)
    decoded_path = record_dir / "decoded.json"
    decoded_path.write_text(json.dumps({"frames": []}), encoding="utf-8")
    (record_dir / "meta.json").write_text(json.dumps({"decoded_path": str(decoded_path)}), encoding="utf-8")

    first = insights.sync_fanxiu_packet_runtime_insights(data_dir=tmp_path)
    second = insights.sync_fanxiu_packet_runtime_insights(data_dir=tmp_path)

    assert first["changed"] is True
    assert second["changed"] is False
    assert calls == {"build": 1, "activity_sync": 1}

    decoded_path.write_text(json.dumps({"frames": [{"name": "SM_Wallet"}]}), encoding="utf-8")
    third = insights.sync_fanxiu_packet_runtime_insights(data_dir=tmp_path)

    assert third["changed"] is True
    assert calls == {"build": 2, "activity_sync": 2}


def test_packet_runtime_insights_no_sync_reads_stale_snapshot_without_rebuild(tmp_path, monkeypatch):
    calls = {"build": 0}

    def fake_build(**_kwargs):
        calls["build"] += 1
        return {"schema_version": insights.PACKET_INSIGHT_SCHEMA_VERSION, "observations": []}

    monkeypatch.setattr(insights, "build_fanxiu_packet_runtime_insights", fake_build)

    root = tmp_path / "fanxiu" / "packet-insights"
    root.mkdir(parents=True)
    (root / "state.json").write_text(json.dumps({"schema_version": 1}), encoding="utf-8")
    (root / "account_runtime_insights.json").write_text(
        json.dumps({"schema_version": 1, "bag": {"items": [{"base_id": 1}]}}),
        encoding="utf-8",
    )

    result = insights.get_fanxiu_packet_runtime_insights(data_dir=tmp_path, sync=False)
    storage = insights.get_fanxiu_packet_storage_bag_snapshot(data_dir=tmp_path, sync=False)

    assert calls == {"build": 0}
    assert result["changed"] is False
    assert result["stale"] is True
    assert result["snapshot"]["bag"]["items"][0]["base_id"] == 1
    assert storage["stale"] is True
    assert storage["bag"]["items"][0]["base_id"] == 1


def test_packet_runtime_insights_syncs_only_relevant_decode_results(tmp_path, monkeypatch):
    calls = {"sync": 0, "player": 0}

    def fake_sync(**kwargs):
        calls["sync"] += 1
        return {"ok": True, "force": kwargs.get("force")}

    def fake_player_sync(**kwargs):
        calls["player"] += 1
        return {"ok": True, "scope": "player_profile", "force": kwargs.get("force")}

    monkeypatch.setattr(insights, "sync_fanxiu_packet_runtime_insights", fake_sync)
    monkeypatch.setattr(insights, "sync_fanxiu_packet_player_profiles", fake_player_sync)

    irrelevant = insights.sync_fanxiu_packet_runtime_insights_for_decode_result(
        {"frames": [{"name": "SM_SyncTime"}]},
        data_dir=tmp_path,
    )
    relevant = insights.sync_fanxiu_packet_runtime_insights_for_decode_result(
        {"frames": [{"name": "SM_AllBagSyncInfo"}]},
        data_dir=tmp_path,
    )
    player_profile_relevant = insights.sync_fanxiu_packet_runtime_insights_for_decode_result(
        {"frames": [{"name": "SM_ShowOther"}]},
        data_dir=tmp_path,
    )
    sync_player_relevant = insights.sync_fanxiu_packet_runtime_insights_for_decode_result(
        {"frames": [{"name": "SM_SyncPlayer"}]},
        data_dir=tmp_path,
    )
    worship_relevant = insights.sync_fanxiu_packet_runtime_insights_for_decode_result(
        {"frames": [{"name": "CM_WorshipRank"}]},
        data_dir=tmp_path,
    )

    assert irrelevant is None
    assert relevant == {"ok": True, "force": False}
    assert player_profile_relevant == {"ok": True, "scope": "player_profile", "force": False}
    assert sync_player_relevant == {"ok": True, "scope": "player_profile", "force": False}
    assert worship_relevant == {"ok": True, "force": False}
    assert calls == {"sync": 2, "player": 2}


def test_player_profile_decode_result_persists_incrementally(tmp_path, monkeypatch):
    decoded_path = tmp_path / "decoded.json"
    decoded_path.write_text(
        json.dumps(
            {
                "frames": [
                    {
                        "direction": "s2c",
                        "name": "SM_ShowOther",
                        "pro_id": 30008,
                        "sn": 1,
                        "parsed": {
                            "_class": "SM_ShowOther",
                            "otherRoleVO": {
                                "roleId": 24082878061087586,
                                "name": "凌霄༅青风”",
                                "server": 22077,
                                "attrMap": {
                                    "items": [
                                        {"key": 2001, "value": 2.844220239520223e18},
                                        {"key": 35006, "value": 1.3124726699597634e19},
                                    ]
                                },
                            },
                        },
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    captured: dict[str, object] = {}

    def fake_persist(rows):
        captured["rows"] = rows
        return {"created": len(rows), "skipped_invalid": 0, "skipped_duplicate": 0}

    monkeypatch.setattr(insights, "_persist_player_profile_rows_to_database", fake_persist)
    monkeypatch.setattr(insights, "_source_signature", lambda data_dir=None: {"hash": "h1", "decoded_file_count": 1, "files": []})

    result = insights.sync_fanxiu_packet_runtime_insights_for_decode_result(
        {
            "frames": [{"name": "SM_ShowOther"}],
            "stored_decoded_path": str(decoded_path),
            "record_id": "r1",
            "pcap_name": "a.pcap",
            "stream": 0,
        },
        data_dir=tmp_path,
    )

    rows = captured["rows"]
    assert result["database_sync"] == {"created": 1, "skipped_invalid": 0, "skipped_duplicate": 0}
    assert len(rows) == 1
    assert rows[0]["name"] == "凌霄༅青风”"
    assert rows[0]["combat_attributes"][0]["key"] == 35006
    assert any(attr["key"] == 2001 for attr in rows[0]["combat_attributes"])


def test_player_profile_parse_error_frame_recovers_from_pcap(tmp_path, monkeypatch):
    decoded_path = tmp_path / "decoded.json"
    decoded_path.write_text(
        json.dumps(
            {
                "frames": [
                    {
                        "direction": "s2c",
                        "name": "SM_ShowOther",
                        "pro_id": 30008,
                        "sn": 2,
                        "parse_error": "varint too long",
                        "parsed": None,
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    def fake_recover(entry, **_kwargs):
        assert entry["sn"] == 2
        return {
            "_class": "SM_ShowOther",
            "partial": True,
            "otherRoleVO": {
                "roleId": 24082878061085095,
                "name": "界外天魔主",
                "server": 22077,
                "level": 221,
                "attrMap": {"items": [{"key": 2001, "value": 2358700000000000.0}]},
            },
        }

    monkeypatch.setattr(insights, "_recover_show_other_parsed_from_packet", fake_recover)

    rows = insights._player_profile_rows_from_decoded_source(
        {
            "decoded_path": str(decoded_path),
            "record_id": "r1",
            "pcap_name": "a.pcap",
            "stored_pcap": str(tmp_path / "a.pcap"),
            "stream": 0,
            "created_at": "2026-06-03 14:01:56",
        }
    )

    assert len(rows) == 1
    assert rows[0]["name"] == "界外天魔主"
    assert rows[0]["cultivation_level_text"] == "大乘后期壹层"
    assert rows[0]["combat_attributes"] == [{"key": 2001, "name": "攻击", "value": 2358700000000000.0, "text": "2359兆"}]
    assert rows[0]["decoded_from_pcap"] is True
    assert rows[0]["decoded_partial"] is True


def test_decode_and_sync_runtime_capture_decodes_streams_and_syncs_snapshot(tmp_path, monkeypatch):
    pcap_path = tmp_path / "capture.pcap"
    pcap_path.write_bytes(b"pcap")
    calls = {"decode": []}

    monkeypatch.setattr(
        insights,
        "list_tcp_streams_with_tshark",
        lambda *_args, **_kwargs: [{"stream": 2}, {"stream": 5}],
    )

    def fake_decode(_path, *, stream, **_kwargs):
        calls["decode"].append(stream)
        return {
            "output_path": str(tmp_path / f"{stream}.json"),
            "record_id": f"r{stream}",
            "frames": [
                {"name": "CM_WorshipRank"},
                {"name": "SM_AllBagSyncInfo"},
            ],
        }

    monkeypatch.setattr(insights, "decode_fanxiu_tcp_pcap", fake_decode)
    monkeypatch.setattr(
        insights,
        "sync_fanxiu_packet_runtime_insights",
        lambda **_kwargs: {
            "changed": True,
            "snapshot_path": str(tmp_path / "snapshot.json"),
            "snapshot": {
                "worship": {"count": 1, "packet_count": 2},
                "bag": {"stack_count": 9},
            },
        },
    )

    result = insights.decode_and_sync_fanxiu_runtime_capture(pcap_path, data_dir=tmp_path)

    assert calls["decode"] == [2, 5]
    assert result["decoded_count"] == 2
    assert result["runtime_protocol_count"] == 4
    assert result["worship_protocol_count"] == 2
    assert result["packet_runtime_sync"]["worship_record_count"] == 1
    assert result["packet_runtime_sync"]["worship_packet_count"] == 2
    assert result["packet_runtime_sync"]["bag_stack_count"] == 9
