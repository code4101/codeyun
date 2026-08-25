import json

from backend.core.fanxiu.history_museum.packet_capture import activity_sync as sync


def test_activity_item_merge_keeps_existing_when_new_value_is_empty() -> None:
    existing = {
        "activityId": 16150001,
        "name": "兽渊探秘",
        "startTime": 1780279200000,
        "serverIds": [1, 2],
    }
    incoming = {
        "activityId": 16150001,
        "name": "",
        "startTime": None,
        "endTime": 1780408800000,
        "serverIds": [],
    }

    merged = sync._merge_activity_item(existing, incoming)

    assert merged["name"] == "兽渊探秘"
    assert merged["startTime"] == 1780279200000
    assert merged["serverIds"] == [1, 2]
    assert merged["endTime"] == 1780408800000


def test_activity_schedule_uses_union_of_packet_snapshots(monkeypatch) -> None:
    old_full_snapshot = {
        "items": [
            {"activityId": 1, "id": 101, "scheduleId": 10, "loopDay": 0, "name": "兽渊探秘"},
            {"activityId": 2, "id": 201, "scheduleId": 20, "loopDay": 0, "name": "宗门灵泉"},
        ]
    }
    new_incremental_snapshot = {
        "items": [
            {"activityId": 2, "id": 201, "scheduleId": 20, "loopDay": 0, "name": "宗门灵泉", "state": 1},
        ]
    }

    monkeypatch.setattr(sync, "_load_records", lambda data_dir=None: {"records": []})
    monkeypatch.setattr(sync, "_load_state", lambda data_dir=None: {})
    monkeypatch.setattr(sync, "get_latest_fanxiu_worldline_activity_schedule", lambda **kwargs: new_incremental_snapshot)
    monkeypatch.setattr(
        sync,
        "list_fanxiu_worldline_activity_schedule_snapshots",
        lambda **kwargs: [new_incremental_snapshot, old_full_snapshot],
    )

    schedule = sync.get_fanxiu_activity_packet_schedule()
    names = {item["name"] for item in schedule["items"]}

    assert names == {"兽渊探秘", "宗门灵泉"}
    assert schedule["count"] == 2
    assert schedule["sync"]["snapshot_count"] == 2


def test_cached_runtime_schedule_does_not_scan_capture_history(monkeypatch) -> None:
    monkeypatch.setattr(
        sync,
        "_load_records",
        lambda data_dir=None: {
            "updated_at": "2026-08-12 10:20:00",
            "records": [
                {
                    "item": {
                        "activityId": 16150001,
                        "id": 16150001400001,
                        "name": "兽渊探秘",
                        "startTime": 1786464000000,
                        "endTime": 1786636800000,
                    }
                }
            ],
        },
    )
    monkeypatch.setattr(
        sync,
        "list_fanxiu_worldline_activity_schedule_snapshots",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("不应扫描历史抓包")),
    )
    monkeypatch.setattr(sync, "_load_json", lambda _path, fallback: fallback)

    schedule = sync.get_cached_fanxiu_activity_runtime_schedule()

    assert schedule["available"] is True
    assert schedule["complete"] is True
    assert schedule["source_kind"] == "activity_packet_runtime_cache"
    assert schedule["items"][0]["name"] == "兽渊探秘"


def test_cached_runtime_schedule_prefers_fresh_game_runtime(monkeypatch) -> None:
    monkeypatch.setattr(
        sync,
        "_load_records",
        lambda data_dir=None: {
            "updated_at": "2026-05-31 00:43:12",
            "records": [
                {
                    "item": {
                        "activityId": 8150001,
                        "id": 8150001400001,
                        "name": "兽渊探秘",
                        "startTime": 1785376800000,
                        "endTime": 1785506400000,
                    }
                }
            ],
        },
    )
    now = sync.time.time()
    monkeypatch.setattr(
        sync,
        "_load_json",
        lambda _path, _fallback: {
            "complete": True,
            "captured_at": "2026-08-12T11:35:00+08:00",
            "cached_at_epoch": now,
            "evidence": {"pid": 9348, "process_start_ticks": 4468},
            "items": [
                {
                    "activityId": 4150001,
                    "id": 4150001400002,
                    "activityType": 15,
                    "name": "兽渊探秘",
                    "startTime": 1786413600000,
                    "endTime": 1786543200000,
                    "serverCount": 4,
                }
            ],
        },
    )

    schedule = sync.get_cached_fanxiu_activity_runtime_schedule()

    assert schedule["runtime_current"] is True
    assert schedule["source_kind"].startswith("worldline_activity_runtime_memory")
    current = next(
        item for item in schedule["items"] if item["activityId"] == 4150001
    )
    assert current["name"] == "兽渊探秘"
    assert current["serverCount"] == 4
    assert schedule["runtime_evidence"]["pid"] == 9348


def test_failed_runtime_refresh_preserves_last_complete_cache(
    tmp_path, monkeypatch
) -> None:
    runtime_path = sync._runtime_snapshot_path(tmp_path)
    runtime_path.parent.mkdir(parents=True, exist_ok=True)
    runtime_path.write_text('{"complete": true, "items": [{"name": "旧快照"}]}', encoding="utf-8")
    monkeypatch.setattr(
        "backend.core.fanxiu.instrumentation.activity_runtime.read_worldline_activity_runtime_snapshot",
        lambda **_kwargs: {
            "ok": False,
            "complete": False,
            "reason": "data_not_loaded",
            "items": [],
        },
    )

    result = sync.refresh_cached_fanxiu_activity_runtime_schedule(
        data_dir=tmp_path
    )

    assert result["complete"] is False
    assert json.loads(runtime_path.read_text(encoding="utf-8"))["items"][0][
        "name"
    ] == "旧快照"


def test_incremental_activity_sync_is_normalized_into_activity_fact() -> None:
    rows = sync._extract_activity_sync_items(
        {
            "content": {
                "activityVO": {
                    "_class": "HeavenActivityVO",
                    "_super": {
                        "id": 32080001400002,
                        "activityId": 32080001,
                        "activityType": 8,
                        "state": 1,
                        "startTime": 1785722400000,
                        "endTime": 1786024800000,
                        "closePanelTime": 1786118339000,
                        "serverIds": {"_count": 32, "items": list(range(8)), "_truncated_items": 24},
                    },
                }
            }
        }
    )

    assert len(rows) == 1
    assert rows[0]["class"] == "HeavenActivityVO"
    assert rows[0]["activityId"] == 32080001
    assert rows[0]["startTimeText"] == "2026-08-03 10:00:00"
    assert rows[0]["endTimeText"] == "2026-08-06 22:00:00"
    assert rows[0]["serverCount"] == 32


def test_business_fact_updates_when_only_protocol_projection_changes() -> None:
    from sqlmodel import Session, SQLModel, create_engine, select

    from backend.core.fanxiu.history_museum.packet_capture.business_store_legacy import upsert_fanxiu_packet_business_records
    from backend.models import FanxiuPacketBusinessRecord

    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    base = {
        "domain": "worldline_activity",
        "record_key": "32080001|32080001400002",
        "packet_id": "packet-1",
        "source_kind": "record",
        "captured_at": "2026-08-03 05:02:52",
        "payload": {"item": {"activityId": 32080001}},
        "evidence": {"packet_id": "packet-1"},
    }
    with Session(engine) as session:
        assert upsert_fanxiu_packet_business_records(
            session, [{**base, "protocol": "SM_WorldLineActivitySync"}]
        )["created"] == 1
        assert upsert_fanxiu_packet_business_records(
            session, [{**base, "protocol": "SM_ActivitySync"}]
        )["updated"] == 1
        row = session.exec(select(FanxiuPacketBusinessRecord)).one()
        assert row.protocol == "SM_ActivitySync"


def test_incremental_decode_ingests_exact_cross_server_rank_without_history_scan(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        sync,
        "_iter_fanxiu_tcp_entries",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("incremental decode must not rescan packet history")
        ),
    )
    monkeypatch.setattr(
        sync,
        "list_fanxiu_worldline_activity_schedule_snapshots",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("incremental decode must not rescan snapshot history")
        ),
    )
    monkeypatch.setattr(
        sync,
        "_persist_activity_business_records",
        lambda records, rank_records: {
            "created": len(records) + len(rank_records),
            "updated": 0,
            "skipped_invalid": 0,
            "skipped_duplicate": 0,
        },
    )
    entry = {
        "id": "record-70842|s2c|12536|51104|87332",
        "decoded_at": "2026-08-10 19:39:05",
        "captured_at": "2026-08-10 19:33:49",
        "record_id": "record-70842",
        "pcap_name": "fanxiu_runtime_20260810_193349.pcap",
        "source_kind": "record",
        "name": "SM_ActivityRankSync",
        "pro_id": 51104,
        "content": {
            "vo": {
                "id": 70842400002,
                "activityId": 70842,
                "rankVOS": {
                    "_type": "ActivityRankCrossServerVO",
                    "_type_id": 51160,
                    "items": [
                        {
                            "_class": "ActivityRankCrossServerVO",
                            "score": 121000852,
                            "_super": {
                                "id": 22029,
                                "rank": 1,
                                "name": "",
                                "key": "22029",
                                "index": 0,
                            },
                        },
                        {
                            "_class": "ActivityRankCrossServerVO",
                            "score": 3690402,
                            "_super": {
                                "id": 22077,
                                "rank": 5,
                                "name": "",
                                "key": "22077",
                                "index": 1,
                            },
                        },
                    ]
                },
                "selfRankVO": {
                    "_class": "ActivityRankCrossServerVO",
                    "score": 3690402,
                    "_super": {
                        "id": 22077,
                        "rank": 5,
                        "name": "",
                        "key": "22077",
                        "index": 0,
                    },
                },
                "rankListSize": 8,
                "group": 0,
            }
        },
    }

    result = sync.sync_fanxiu_activity_packets(
        data_dir=tmp_path,
        decoded_entries=[entry],
    )
    payload = json.loads(
        (tmp_path / "fanxiu/activity-packet-sync/activity_rank_records.json").read_text(
            encoding="utf-8"
        )
    )
    record = payload["records"][0]

    assert result["rank_inserted"] == 1
    assert result["matched_rank_packets"] == 1
    assert result["mode"] == "incremental_decoded_entries"
    assert result["historical_snapshot_count"] == 0
    assert record["key"] == "70842|0"
    assert record["last_seen_at"] == "2026-08-10 19:39:05"
    assert record["snapshot"]["rank_vo_type"] == "ActivityRankCrossServerVO"
    assert len(record["snapshot"]["items"]) == 2
