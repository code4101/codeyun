from backend.core.fanxiu.packet import activity_sync as sync_mod


def test_activity_packet_sync_persists_cursor_and_skips_duplicates(tmp_path, monkeypatch):
    entries = [
        {
            "id": "packet-1",
            "decoded_at": "2026-05-01 10:00:00",
            "record_id": "record-1",
            "pcap_name": "a.pcap",
            "source_kind": "live",
            "name": "SM_WorldLineActivitySync",
            "pro_id": 51006,
            "content": {
                "activityVOS": {
                    "items": [
                        {
                            "_super": {
                                "id": 11,
                                "activityId": 271770,
                                "startTime": 1779984005000,
                                "endTime": 1780070390000,
                                "closePanelTime": 1780243199000,
                                "scheduleId": 3,
                                "loopDay": 1,
                            },
                            "name": "神魔夺旗",
                        }
                    ]
                }
            },
        }
    ]
    monkeypatch.setattr(sync_mod, "_build_fanxiu_tcp_entries", lambda *_args, **_kwargs: entries)

    first = sync_mod.sync_fanxiu_activity_packets(data_dir=tmp_path)
    second = sync_mod.sync_fanxiu_activity_packets(data_dir=tmp_path)
    schedule = sync_mod.get_fanxiu_activity_packet_schedule(data_dir=tmp_path)

    assert first["inserted"] == 1
    assert first["record_count"] == 1
    assert first["cursor"]["last_packet_scan_at"] == "2026-05-01 10:00:00"
    assert second["scanned_packets"] == 0
    assert second["inserted"] == 0
    assert schedule["available"] is True
    assert schedule["items"][0]["name"] == "神魔夺旗"


def test_activity_packet_sync_persists_personal_rank(tmp_path, monkeypatch):
    entries = [
        {
            "id": "rank-1",
            "decoded_at": "2026-05-01 11:00:00",
            "record_id": "record-1",
            "pcap_name": "a.pcap",
            "source_kind": "live",
            "name": "SM_ActivityRankSync",
            "pro_id": 51104,
            "content": {
                "vo": {
                    "activityId": 11621602,
                    "group": 0,
                    "rankVOS": {
                        "items": [
                            {
                                "serverId": 22055,
                                "score": 200,
                                "_super": {"rank": 1, "name": "榜首", "index": 0, "id": 1},
                            }
                        ]
                    },
                    "selfRankVO": {
                        "serverId": 22077,
                        "score": 102,
                        "clubName": "凌霄阁",
                        "_super": {"rank": 34, "name": "止清", "index": 0, "id": 2, "key": "22077:1@MOBI37"},
                    },
                }
            },
        }
    ]
    monkeypatch.setattr(sync_mod, "_build_fanxiu_tcp_entries", lambda *_args, **_kwargs: entries)

    result = sync_mod.sync_fanxiu_activity_packets(data_dir=tmp_path)
    records = sync_mod.get_fanxiu_activity_rank_records(data_dir=tmp_path)["records"]

    assert result["rank_inserted"] == 1
    assert records[0]["snapshot"]["personal_item"]["rank"] == 34
    assert records[0]["snapshot"]["personal_item"]["name"] == "止清"


def test_activity_packet_schedule_keeps_open_server_time_from_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(sync_mod, "_build_fanxiu_tcp_entries", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        sync_mod,
        "list_fanxiu_worldline_activity_schedule_snapshots",
        lambda **_kwargs: [
            {
                "source_path": "snapshot.json",
                "created_at": "2026-06-01 10:00:00",
                "source_kind": "worldline_activity_json",
                "openServerTime": 1745098200000,
                "openServerTimeText": "2025-04-20 05:30:00",
                "items": [
                    {
                        "id": 1043011400118,
                        "activityId": 1043011,
                        "name": "炼体法相",
                        "startTime": 1780261205000,
                        "endTime": 1780322400000,
                        "closePanelTime": 1780502339000,
                    }
                ],
            }
        ],
    )

    sync_mod.sync_fanxiu_activity_packets(data_dir=tmp_path)
    schedule = sync_mod.get_fanxiu_activity_packet_schedule(data_dir=tmp_path)

    assert schedule["openServerTime"] == 1745098200000
    assert schedule["openServerTimeText"] == "2025-04-20 05:30:00"
