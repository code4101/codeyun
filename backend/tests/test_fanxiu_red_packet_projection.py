from backend.core.fanxiu.history_museum.packet_capture.red_packet_state import project_red_packet_state


def _record(name, captured_at, parsed, *, offset=0):
    return {
        "packet_id": f"{name}-{captured_at}-{offset}",
        "pcap_name": f"fanxiu_runtime_{captured_at.replace(':', '').replace(' ', '_')}.pcap",
        "stream": 0,
        "frame_index": 0,
        "offset": offset,
        "name": name,
        "captured_at": captured_at,
        "payload": {"parsed": {"_class": name, **parsed, "_super": {"code": 0}}},
    }


def _vo(uid=101, *, end=2_000_000, channel=6):
    return {
        "uid": uid,
        "id": 523,
        "channel": channel,
        "subChannelId": 0,
        "endTimeStamp": end,
        "senderVO": {"id": 9, "name": "发包人"},
    }


def test_new_red_packet_is_projected_by_uid_without_process_address():
    result = project_red_packet_state(
        [_record("SM_NewRedBag", "2026-08-02 10:00:00", {
            "redBagVOList": {"items": [_vo()]},
            "canReceive": True,
        })],
        self_role_id=88,
        now_epoch_ms=1_000_000,
    )

    assert result["pending_count"] == 1
    assert result["items"][0]["uid"] == 101
    assert result["evidence"]["identity"] == "redbag_uid"
    assert result["baseline_mode"] == "bounded_server_event_history"


def test_server_can_receive_false_and_expiry_are_not_false_positives():
    records = [
        _record("SM_NewRedBag", "2026-08-02 10:00:00", {
            "redBagVOList": {"items": [_vo(101), _vo(102, end=900_000)]},
            "canReceive": False,
        }),
    ]

    result = project_red_packet_state(records, self_role_id=88, now_epoch_ms=1_000_000)

    assert result["pending"] is False


def test_self_receive_update_removes_candidate_and_replay_is_idempotent():
    new = _record("SM_NewRedBag", "2026-08-02 10:00:00", {
        "redBagVOList": {"items": [_vo()]},
        "canReceive": True,
    })
    received = _record("SM_UpdateRedBag", "2026-08-02 10:00:01", {
        "uid": 101,
        "id": 523,
        "num": 40,
        "receiveNum": 2,
        "receiveId": 88,
    })

    result = project_red_packet_state(
        [received, new, received],
        self_role_id=88,
        now_epoch_ms=1_000_000,
    )

    assert result["pending_count"] == 0


def test_offline_baseline_replaces_preexisting_events_and_marks_received():
    records = [
        _record("SM_NewRedBag", "2026-08-02 10:00:00", {
            "redBagVOList": {"items": [_vo(100)]},
            "canReceive": True,
        }),
        _record("SM_OfflineRedBag", "2026-08-02 10:00:01", {
            "redBagVOList": {"items": [
                {"redBagVO": _vo(101), "isReceive": False},
                {"redBagVO": _vo(102), "isReceive": True},
            ]},
        }),
    ]

    result = project_red_packet_state(records, self_role_id=88, now_epoch_ms=1_000_000)

    assert result["baseline_seen"] is True
    assert result["baseline_mode"] == "offline_snapshot"
    assert [item["uid"] for item in result["items"]] == [101]


def test_full_packet_removes_candidate_even_when_other_player_received_last_one():
    records = [
        _record("SM_NewRedBag", "2026-08-02 10:00:00", {
            "redBagVOList": {"items": [_vo()]},
            "canReceive": True,
        }),
        _record("SM_UpdateRedBag", "2026-08-02 10:00:01", {
            "uid": 101,
            "num": 40,
            "receiveNum": 40,
            "receiveId": 999,
        }),
    ]

    result = project_red_packet_state(records, self_role_id=88, now_epoch_ms=1_000_000)

    assert result["pending_count"] == 0


def test_client_login_restart_does_not_clear_server_red_packet():
    records = [
        _record("SM_NewRedBag", "2026-08-02 10:00:00", {
            "redBagVOList": {"items": [_vo()]},
            "canReceive": True,
        }),
        _record("SM_Login", "2026-08-02 10:01:00", {}),
    ]

    result = project_red_packet_state(records, self_role_id=88, now_epoch_ms=1_000_000)

    assert result["pending_count"] == 1
