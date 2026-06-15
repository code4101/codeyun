from backend.core.fanxiu.packet import activity_sync as sync


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
