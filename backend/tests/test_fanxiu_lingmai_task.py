from backend.core.fanxiu.data_annotation.tasks import lingmai


def _seat(
    seat_id: int,
    *,
    name: str,
    server_id: int,
    battle_score: float,
    role_id: int | None = None,
    protect_end_time: int = 0,
    team_uid: int | None = None,
) -> dict:
    return {
        "seat_id": seat_id,
        "owner": {
            "role_id": role_id or seat_id + 100000,
            "name": name,
            "server_id": server_id,
            "alliance_id": 1001,
            "team_uid": team_uid,
            "battle_score": battle_score,
            "protect_end_time": protect_end_time,
        },
    }


def _facts(*seats: dict, available_count: int = 0, complete: bool = True) -> dict:
    return {
        "available": True,
        "complete": complete,
        "room_id": lingmai.LINGMAI_SHENMAI_ROOM_ID,
        "available_count": available_count,
        "seats": list(seats),
    }


def _profile(*, battle_score: float = 1000.0) -> dict:
    return {"available": True, "role_id": 42, "battle_score": battle_score}


def _self_seat(*, seated: bool = False, role_id: int = 42, pcap_name: str = "round.pcap") -> dict:
    return {
        "available": True,
        "seated": seated,
        "seat": (
            {"seat_id": 99, "owner": {"role_id": role_id, "name": "自己"}}
            if seated
            else None
        ),
        "evidence": {"pcap_name": pcap_name},
    }


def test_lingmai_selector_uses_empty_seat_without_requiring_full_roster(tmp_path) -> None:
    result = lingmai.select_lingmai_seat_action(
        _facts(available_count=2, complete=False),
        self_seat_facts=_self_seat(),
        player_profile=_profile(),
        data_dir=tmp_path,
        now_ms=100,
    )

    assert result["action"] == "occupy_empty"
    assert result["available_count"] == 2


def test_lingmai_selector_accepts_union_shenmai_room(tmp_path) -> None:
    facts = _facts(available_count=1, complete=False)
    facts["room_id"] = lingmai.LINGMAI_UNION_SHENMAI_ROOM_ID

    result = lingmai.select_lingmai_seat_action(
        facts,
        self_seat_facts=_self_seat(),
        player_profile=_profile(),
        data_dir=tmp_path,
        now_ms=100,
    )

    assert result["action"] == "occupy_empty"
    assert result["room_id"] == 17


def test_union_lingmai_selector_treats_same_veins_group_as_friendly(tmp_path) -> None:
    facts = _facts(
        _seat(1, name="同组低战", server_id=22991, battle_score=1, team_uid=777),
        _seat(2, name="异组目标", server_id=22992, battle_score=2, team_uid=888),
    )
    facts["room_id"] = lingmai.LINGMAI_UNION_SHENMAI_ROOM_ID

    result = lingmai.select_lingmai_seat_action(
        facts,
        self_seat_facts=_self_seat(),
        self_group_facts={"available": True, "veins_group": 777},
        player_profile=_profile(),
        data_dir=tmp_path,
        now_ms=100,
    )

    assert result["action"] == "kick"
    assert result["target"]["name"] == "异组目标"
    assert result["rejected"]["friendly"] == 1


def test_union_lingmai_selector_stops_without_self_veins_group(tmp_path) -> None:
    facts = _facts(_seat(1, name="未知关系", server_id=22991, battle_score=1, team_uid=777))
    facts["room_id"] = lingmai.LINGMAI_UNION_SHENMAI_ROOM_ID

    result = lingmai.select_lingmai_seat_action(
        facts,
        self_seat_facts=_self_seat(),
        player_profile=_profile(),
        data_dir=tmp_path,
        now_ms=100,
    )

    assert result == {
        "ok": False,
        "status": "invalid_facts",
        "reason": "self_veins_group_missing",
        "action": None,
    }


def test_lingmai_selector_stops_when_self_is_already_seated_even_with_empty_slots(tmp_path) -> None:
    result = lingmai.select_lingmai_seat_action(
        _facts(available_count=3),
        self_seat_facts=_self_seat(seated=True),
        player_profile=_profile(),
        data_dir=tmp_path,
        now_ms=100,
    )

    assert result["action"] == "already_seated"
    assert result["self_seat"]["seat_id"] == 99


def test_lingmai_selector_uses_self_seat_before_requiring_roster(tmp_path) -> None:
    result = lingmai.select_lingmai_seat_action(
        {
            "available": False,
            "complete": False,
            "room_id": 17,
            "reason": "room_roster_not_loaded",
            "seats": [],
        },
        self_seat_facts={
            **_self_seat(seated=True),
            "room_id": 17,
        },
        player_profile=_profile(),
        data_dir=tmp_path,
        now_ms=100,
    )

    assert result["ok"] is True
    assert result["action"] == "already_seated"


def test_lingmai_runtime_probe_selects_without_packet_or_gui(tmp_path) -> None:
    result = lingmai.read_and_select_lingmai_runtime_action(
        snapshot={
            "shenmai_roster": _facts(
                _seat(
                    7,
                    role_id=107,
                    name="低战目标",
                    server_id=22001,
                    battle_score=100,
                ),
                available_count=0,
            ),
            "self_seat_facts": _self_seat(seated=False),
            "union_group_facts": {
                "available": True,
                "veins_group": 500,
            },
            "self_profile": _profile(),
        },
        data_dir=tmp_path,
        now_ms=100,
    )

    assert result["ok"] is True
    assert result["action"] == "kick"
    assert result["target"]["name"] == "低战目标"
    assert result["source"] == "runtime_memory"


def test_lingmai_runtime_probe_preserves_unavailable_reason() -> None:
    result = lingmai.read_and_select_lingmai_runtime_action(
        snapshot={
            "available": False,
            "complete": False,
            "reason": "联盟灵脉 Runtime 模型尚未初始化",
        },
    )

    assert result["ok"] is False
    assert result["status"] == "runtime_unavailable"
    assert result["reason"] == "联盟灵脉 Runtime 模型尚未初始化"
    assert result["action"] is None


def test_lingmai_refresh_returns_runtime_decision_without_packet(monkeypatch) -> None:
    expected = {
        "ok": True,
        "status": "already_seated",
        "action": "already_seated",
        "source": "runtime_memory",
    }
    monkeypatch.setattr(
        lingmai,
        "read_and_select_lingmai_runtime_action",
        lambda **_kwargs: expected,
    )

    assert lingmai.refresh_and_select_lingmai_seat_action() is expected


def test_lingmai_selector_filters_then_picks_lowest_battle_score(tmp_path) -> None:
    friendly_config = tmp_path / "fanxiu" / "server_relations.json"
    friendly_config.parent.mkdir(parents=True)
    friendly_config.write_text(
        '{"groups":[{"key":"friendly","children":[{"key":"same_server","servers":'
        '[{"server_id":22077,"server_order":1,"server_name":"本服"}]}]}]}',
        encoding="utf-8",
    )
    result = lingmai.select_lingmai_seat_action(
        _facts(
            _seat(1, name="自己", server_id=22001, battle_score=1, role_id=42),
            _seat(2, name="友军", server_id=22077, battle_score=1),
            _seat(3, name="低于自身战力", server_id=22001, battle_score=999),
            _seat(4, name="等于自身战力", server_id=22002, battle_score=1000),
            _seat(5, name="最低战力", server_id=22003, battle_score=300),
            _seat(6, name="保护中", server_id=22004, battle_score=100, protect_end_time=200),
        ),
        self_seat_facts=_self_seat(),
        player_profile=_profile(),
        data_dir=tmp_path,
        now_ms=100,
    )

    assert result["action"] == "kick"
    assert result["target"]["name"] == "最低战力"
    assert result["eligible_count"] == 2
    assert result["rejected"] == {
        "self": 1,
        "friendly": 1,
        "protected": 1,
        "unsafe_power": 1,
        "invalid": 0,
    }


def test_lingmai_selector_retries_at_earliest_beatable_protection_end(tmp_path) -> None:
    result = lingmai.select_lingmai_seat_action(
        _facts(
            _seat(1, name="晚结束", server_id=22001, battle_score=100, protect_end_time=20_000),
            _seat(2, name="早结束", server_id=22002, battle_score=200, protect_end_time=10_000),
            _seat(3, name="虽早但打不过", server_id=22003, battle_score=1000, protect_end_time=5_000),
        ),
        self_seat_facts=_self_seat(),
        player_profile=_profile(),
        data_dir=tmp_path,
        now_ms=1_000,
    )

    assert result["action"] == "retry"
    assert result["retry_reason"] == "earliest_beatable_protection_end"
    assert result["future_target"]["name"] == "早结束"
    assert result["retry_at_ms"] == 15_000


def test_lingmai_selector_retries_in_thirty_minutes_without_future_target(tmp_path) -> None:
    result = lingmai.select_lingmai_seat_action(
        _facts(_seat(1, name="打不过", server_id=22001, battle_score=1000)),
        self_seat_facts=_self_seat(),
        player_profile=_profile(),
        data_dir=tmp_path,
        now_ms=1_000,
    )

    assert result["action"] == "retry"
    assert result["retry_reason"] == "no_current_or_future_beatable_target"
    assert result["retry_at_ms"] == 1_801_000


def test_lingmai_selector_rejects_incomplete_full_roster(tmp_path) -> None:
    result = lingmai.select_lingmai_seat_action(
        _facts(complete=False),
        self_seat_facts=_self_seat(),
        player_profile=_profile(),
        data_dir=tmp_path,
    )

    assert result == {
        "ok": False,
        "status": "invalid_facts",
        "reason": "seat_roster_incomplete",
        "action": None,
    }


def test_shengmai_requires_two_actions_of_strength_reserve(tmp_path) -> None:
    facts = _facts(available_count=1, complete=False)
    facts["room_id"] = lingmai.LINGMAI_UNION_SHENGMAI_ROOM_ID

    result = lingmai.select_lingmai_seat_action(
        facts,
        self_seat_facts={**_self_seat(seated=True), "room_id": 17},
        self_group_facts={"available": True, "veins_group": 777},
        player_profile=_profile(),
        current_strength=299,
        target_room_id=lingmai.LINGMAI_UNION_SHENGMAI_ROOM_ID,
        data_dir=tmp_path,
        now_ms=100,
    )

    assert result["action"] == "fallback_shenmai"
    assert result["minimum_strength"] == 300


def test_shengmai_allows_upgrade_from_shenmai_at_300_strength(tmp_path) -> None:
    facts = _facts(available_count=1, complete=False)
    facts["room_id"] = lingmai.LINGMAI_UNION_SHENGMAI_ROOM_ID

    result = lingmai.select_lingmai_seat_action(
        facts,
        self_seat_facts={**_self_seat(seated=True), "room_id": 17},
        self_group_facts={"available": True, "veins_group": 777},
        player_profile=_profile(),
        current_strength=300,
        target_room_id=lingmai.LINGMAI_UNION_SHENGMAI_ROOM_ID,
        data_dir=tmp_path,
        now_ms=100,
    )

    assert result["action"] == "occupy_empty"
    assert result["room_id"] == lingmai.LINGMAI_UNION_SHENGMAI_ROOM_ID
