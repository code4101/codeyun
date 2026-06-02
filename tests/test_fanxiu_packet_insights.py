import json

from backend.core import fanxiu_packet_insights as insights


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
                "role": {"roleId": 100, "name": "止清", "level": 230, "vipLevel": 9, "battleScore": 123456789},
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

    assert snapshot["account"]["latest_login"]["name"] == "止清"
    assert snapshot["wallet"]["resources"][0]["amount"] == 99
    assert snapshot["bag"]["stack_count"] == 2
    assert snapshot["bag"]["decoded_stack_count"] == 1
    assert snapshot["bag"]["items"][0]["item"]["name"] == "测试仙玉"
    assert snapshot["bag"]["notable_items"][0]["item"]["name"] == "测试仙玉"
    assert snapshot["activity_ranks"]["personal_count"] == 1
    assert snapshot["worship"]["count"] == 1
    assert snapshot["worship"]["records"][0]["role"] == "凌霄༅青风”"
    assert snapshot["worship"]["records"][0]["worship_role"] == "凌霄༅青风”"
    assert snapshot["worship"]["records"][0]["red_role"] == "止清"
    assert snapshot["worship"]["records"][0]["plane_label"] == "8跨"
    assert snapshot["worship"]["records"][0]["rank_type_label"] == "社团"
    assert snapshot["worship"]["records"][0]["friendship"] == 82931
    assert snapshot["worship"]["records"][0]["protocol"] == "SM_WorshipGotRecord"


def test_worship_plane_label_formats_local_and_cross_values():
    assert insights._worship_plane_label(1) == "1跨"
    assert insights._worship_plane_label("1") == "1跨"
    assert insights._worship_plane_label(2) == "2跨"
    assert insights._worship_plane_label("2") == "2跨"
    assert insights._worship_plane_label(8) == "8跨"


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
    calls = {"sync": 0}

    def fake_sync(**kwargs):
        calls["sync"] += 1
        return {"ok": True, "force": kwargs.get("force")}

    monkeypatch.setattr(insights, "sync_fanxiu_packet_runtime_insights", fake_sync)

    irrelevant = insights.sync_fanxiu_packet_runtime_insights_for_decode_result(
        {"frames": [{"name": "SM_SyncTime"}]},
        data_dir=tmp_path,
    )
    relevant = insights.sync_fanxiu_packet_runtime_insights_for_decode_result(
        {"frames": [{"name": "SM_AllBagSyncInfo"}]},
        data_dir=tmp_path,
    )
    worship_relevant = insights.sync_fanxiu_packet_runtime_insights_for_decode_result(
        {"frames": [{"name": "CM_WorshipRank"}]},
        data_dir=tmp_path,
    )

    assert irrelevant is None
    assert relevant == {"ok": True, "force": False}
    assert worship_relevant == {"ok": True, "force": False}
    assert calls == {"sync": 2}


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
