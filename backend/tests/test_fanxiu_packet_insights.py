from backend.core import fanxiu_packet_insights as insights


def test_runtime_player_profile_rows_collects_unique_snapshot_records() -> None:
    valid_row = {
        "name": "止清",
        "combat_attributes": [{"key": 2001, "value": 1, "text": "1"}],
        "evidence": {"packet_id": "packet-1", "protocol": "SM_RoleChangedAttrs"},
    }
    duplicate_row = {
        "name": "止清",
        "combat_attributes": [{"key": 2001, "value": 2, "text": "2"}],
        "evidence": {"packet_id": "packet-1", "protocol": "SM_RoleChangedAttrs"},
    }
    record_only_row = {
        "name": "玩家",
        "combat_attributes": [{"key": 2001, "value": 3, "text": "3"}],
        "evidence": {"packet_id": "packet-2", "protocol": "SM_ShowOther"},
    }

    rows = insights._runtime_player_profile_rows(
        {
            "player_profiles": {
                "daily_records": [valid_row, {"name": "missing evidence"}],
                "records": [duplicate_row, record_only_row],
            }
        }
    )

    assert rows == [valid_row, record_only_row]


def test_decode_result_with_worldline_activity_triggers_activity_sync(monkeypatch) -> None:
    calls = []

    def fake_sync(**kwargs):
        calls.append(kwargs)
        return {
            "inserted": 1,
            "updated": 0,
            "rank_inserted": 0,
            "rank_updated": 0,
        }

    monkeypatch.setattr(insights, "sync_fanxiu_activity_packets", fake_sync)

    result = insights.sync_fanxiu_packet_runtime_insights_for_decode_result(
        {"frames": [{"name": "SM_WorldLineActivitySync"}]},
        data_dir="data-dir",
        export_root="export-root",
    )

    assert calls == [{"data_dir": "data-dir", "export_root": "export-root", "force": False}]
    assert result == {
        "ok": True,
        "changed": True,
        "activity_packet_sync": {
            "inserted": 1,
            "updated": 0,
            "rank_inserted": 0,
            "rank_updated": 0,
        },
    }
