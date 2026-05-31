from backend.core import fanxiu_activity_packet_sync as sync_mod


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
