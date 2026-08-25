from datetime import datetime, timedelta

from backend.core.fanxiu.data_annotation.tasks import lundao


def _seat(
    seat_id: int,
    *,
    name: str,
    server_id: int,
    faze: int,
    battle_score: float,
    role_id: int | None = None,
    protect_end_time: int = 0,
) -> dict:
    return {
        "seat_id": seat_id,
        "owner": {
            "role_id": role_id or seat_id + 100000,
            "name": name,
            "server_id": server_id,
            "alliance_id": 1001,
            "faze": faze,
            "battle_score": battle_score,
            "protect_end_time": protect_end_time,
        },
    }


def _facts(*seats: dict) -> dict:
    return {
        "available": True,
        "complete": True,
        "room_id": lundao.LUNDAO_DALUO_ROOM_ID,
        "seats": list(seats),
    }


def _profile(*, quality: int = 1, battle_score: float = 1000.0) -> dict:
    return {
        "available": True,
        "role_id": 42,
        "quality": quality,
        "battle_score": battle_score,
    }


def _patch_catalog(monkeypatch) -> None:
    monkeypatch.setattr(
        lundao,
        "load_lundao_faze_catalog",
        lambda **_kwargs: {
            0: {"faze_id": 0, "name": "无法则", "quality": 0, "cross": 0},
            10010: {"faze_id": 10010, "name": "魔道法则", "quality": 1, "cross": 1},
            10011: {"faze_id": 10011, "name": "魔道法则(2跨)", "quality": 2, "cross": 2},
        },
    )


def test_lundao_runtime_profile_uses_self_seat_owner(monkeypatch) -> None:
    _patch_catalog(monkeypatch)

    result = lundao.lundao_player_profile_from_runtime(
        {
            "self_profile": {
                "available": True,
                "role_id": 42,
                "battle_score": 1000,
                "faze": 10010,
            }
        }
    )

    assert result["available"] is True
    assert result["quality"] == 1
    assert result["cross"] == 1
    assert result["source"] == "runtime_memory"


def test_lundao_runtime_probe_selects_without_packet_or_gui(
    monkeypatch,
    tmp_path,
) -> None:
    _patch_catalog(monkeypatch)

    result = lundao.read_and_select_lundao_runtime_target(
        snapshot={
            "daluo_roster": _facts(
                _seat(
                    7,
                    name="低跨目标",
                    server_id=22001,
                    faze=0,
                    battle_score=999_999,
                )
            ),
            "self_profile": {
                "available": True,
                "role_id": 42,
                "battle_score": 1000,
                "faze": 10010,
            },
        },
        data_dir=tmp_path,
        now_ms=100,
    )

    assert result["ok"] is True
    assert result["target"]["name"] == "低跨目标"
    assert result["source"] == "runtime_memory"


def test_lundao_refresh_returns_runtime_decision_without_packet(monkeypatch) -> None:
    expected = {
        "ok": True,
        "status": "selected",
        "target": {"seat_id": 7},
        "source": "runtime_memory",
    }
    monkeypatch.setattr(
        lundao,
        "read_and_select_lundao_runtime_target",
        lambda **_kwargs: expected,
    )

    assert lundao.refresh_and_select_lundao_kick_target() is expected


def test_lundao_selector_prefers_lowest_law_then_lowest_power(monkeypatch, tmp_path) -> None:
    _patch_catalog(monkeypatch)
    facts = _facts(
        _seat(1, name="零跨高战", server_id=22001, faze=0, battle_score=999999.0),
        _seat(2, name="零跨低战", server_id=22002, faze=0, battle_score=800000.0),
        _seat(3, name="同跨低战", server_id=22003, faze=10010, battle_score=100.0),
    )

    result = lundao.select_lundao_kick_target(
        facts,
        player_profile=_profile(quality=1, battle_score=1000.0),
        data_dir=tmp_path,
        now_ms=100,
    )

    assert result["status"] == "selected"
    assert result["target"]["name"] == "零跨低战"
    assert result["target"]["faze_cross"] == 0
    assert result["eligible_no_law_count"] == 2
    assert result["eligible_with_law_count"] == 1


def test_lundao_selector_applies_ninety_percent_only_to_same_law(monkeypatch, tmp_path) -> None:
    _patch_catalog(monkeypatch)
    facts = _facts(
        _seat(1, name="同跨九成", server_id=22001, faze=10010, battle_score=900.0),
        _seat(2, name="同跨超线", server_id=22002, faze=10010, battle_score=900.01),
        _seat(3, name="低跨再高也压制", server_id=22003, faze=0, battle_score=999999.0),
        _seat(4, name="高跨", server_id=22004, faze=10011, battle_score=1.0),
    )

    result = lundao.select_lundao_kick_target(
        facts,
        player_profile=_profile(quality=1, battle_score=1000.0),
        data_dir=tmp_path,
        now_ms=100,
    )

    assert [result["target"]["name"], result["eligible_count"]] == ["低跨再高也压制", 1]
    assert result["eligible_with_law_count"] == 1
    assert result["rejected"]["unsafe_same_law_power"] == 1
    assert result["rejected"]["stronger_law"] == 1


def test_lundao_selector_does_not_attack_law_target_when_no_law_target_missing(
    monkeypatch,
    tmp_path,
) -> None:
    _patch_catalog(monkeypatch)
    facts = _facts(
        _seat(1, name="同跨低战", server_id=22001, faze=10010, battle_score=100.0),
    )

    result = lundao.select_lundao_kick_target(
        facts,
        player_profile=_profile(quality=1, battle_score=1000.0),
        data_dir=tmp_path,
        now_ms=100,
    )

    assert result["status"] == "no_target"
    assert result["target"] is None
    assert result["eligible_count"] == 0
    assert result["eligible_no_law_count"] == 0
    assert result["eligible_with_law_count"] == 1


def test_lundao_selector_treats_explicit_empty_faze_as_no_law(
    monkeypatch,
    tmp_path,
) -> None:
    _patch_catalog(monkeypatch)
    seat = _seat(1, name="空法则字段", server_id=22001, faze=0, battle_score=100.0)
    seat["owner"]["faze"] = None

    result = lundao.select_lundao_kick_target(
        _facts(seat),
        player_profile=_profile(quality=1, battle_score=1000.0),
        data_dir=tmp_path,
        now_ms=100,
    )

    assert result["status"] == "selected"
    assert result["target"]["name"] == "空法则字段"
    assert result["target"]["faze_id"] == 0


def test_lundao_selector_no_law_target_does_not_require_self_law_quality(
    monkeypatch,
    tmp_path,
) -> None:
    _patch_catalog(monkeypatch)
    facts = _facts(
        _seat(1, name="有法则低战", server_id=22001, faze=10010, battle_score=10.0),
        _seat(2, name="无法则低战", server_id=22002, faze=0, battle_score=20.0),
    )

    result = lundao.select_lundao_kick_target(
        facts,
        player_profile={
            "available": True,
            "role_id": 42,
            "battle_score": 1000.0,
        },
        data_dir=tmp_path,
        now_ms=100,
    )

    assert result["status"] == "selected"
    assert result["target"]["name"] == "无法则低战"
    assert result["eligible_no_law_count"] == 1
    assert result["eligible_with_law_count"] == 0


def test_lundao_selector_never_falls_back_to_friendly_or_protected(monkeypatch, tmp_path) -> None:
    _patch_catalog(monkeypatch)
    friendly_config = tmp_path / "fanxiu" / "server_relations.json"
    friendly_config.parent.mkdir(parents=True)
    friendly_config.write_text(
        '{"groups":[{"key":"friendly","children":[{"key":"same_server","servers":'
        '[{"server_id":22077,"server_order":1,"server_name":"本服"}]}]}]}',
        encoding="utf-8",
    )
    facts = _facts(
        _seat(1, name="友军", server_id=22077, faze=0, battle_score=1.0),
        _seat(2, name="保护中非友军", server_id=22001, faze=0, battle_score=1.0, protect_end_time=101),
    )

    result = lundao.select_lundao_kick_target(
        facts,
        player_profile=_profile(),
        data_dir=tmp_path,
        now_ms=100,
    )

    assert result["status"] == "no_target"
    assert result["target"] is None
    assert result["rejected"]["friendly"] == 1
    assert result["rejected"]["protected"] == 1


def test_lundao_selector_rejects_non_daluo_roster(monkeypatch, tmp_path) -> None:
    _patch_catalog(monkeypatch)
    facts = _facts(_seat(1, name="三清玩家", server_id=22001, faze=0, battle_score=1.0))
    facts["room_id"] = 14

    result = lundao.select_lundao_kick_target(
        facts,
        player_profile=_profile(),
        data_dir=tmp_path,
    )

    assert result == {"ok": False, "status": "invalid_facts", "reason": "not_daluo_room", "target": None}


def test_lundao_safety_threshold_boundaries() -> None:
    values = {
        (15, 29): None,
        (15, 30): 6,
        (16, 29): 6,
        (16, 30): 5,
        (17, 0): 4,
        (18, 0): 3,
        (19, 30): 2,
        (21, 0): 1,
        (22, 0): None,
    }
    for (hour, minute), expected in values.items():
        assert lundao.lundao_safety_threshold(datetime(2026, 7, 20, hour, minute)) == expected


def test_lundao_daily_trigger_keeps_today_before_opening() -> None:
    assert lundao.next_lundao_daily_trigger(
        datetime(2026, 8, 15, 10, 23, 41),
    ) == datetime(2026, 8, 15, 15, 30)
    assert lundao.next_lundao_daily_trigger(
        datetime(2026, 8, 15, 15, 30),
    ) == datetime(2026, 8, 16, 15, 30)
    assert lundao.next_lundao_daily_trigger(
        datetime(2026, 8, 15, 22, 0),
    ) == datetime(2026, 8, 16, 15, 30)


def test_lundao_recheck_uses_calm_half_hour_cadence() -> None:
    assert lundao.next_lundao_recheck(
        datetime(2026, 7, 20, 20, 55),
    ) == datetime(2026, 7, 20, 21, 25)
    assert lundao.next_lundao_recheck(
        datetime(2026, 7, 20, 19, 30),
    ) == datetime(2026, 7, 20, 20, 0)
    assert lundao.next_lundao_recheck(
        datetime(2026, 7, 20, 21, 10),
        protect_end_time_ms=int(datetime(2026, 7, 20, 21, 10, 20).timestamp() * 1000),
    ) == datetime(2026, 7, 20, 21, 40)


def test_lundao_unseated_retry_uses_ten_minute_cadence_until_first_seat() -> None:
    assert lundao.next_lundao_unseated_retry(
        datetime(2026, 7, 20, 15, 30, 5),
    ) == datetime(2026, 7, 20, 15, 40, 5)
    assert lundao.next_lundao_unseated_retry(
        datetime(2026, 7, 20, 16, 38, 5),
    ) == datetime(2026, 7, 20, 16, 48, 5)


def test_lundao_opportunity_counts_empty_friendly_and_protected_but_only_attacks_legal_target(monkeypatch, tmp_path) -> None:
    _patch_catalog(monkeypatch)
    friendly_config = tmp_path / "fanxiu" / "server_relations.json"
    friendly_config.parent.mkdir(parents=True)
    friendly_config.write_text(
        '{"groups":[{"key":"friendly","children":[{"key":"same_server","servers":'
        '[{"server_id":22077,"server_order":1,"server_name":"本服"}]}]}]}',
        encoding="utf-8",
    )
    facts = _facts(
        _seat(1, name="自己", server_id=22077, faze=10010, battle_score=1000, role_id=42),
        _seat(2, name="友军弱者", server_id=22077, faze=0, battle_score=1),
        _seat(3, name="保护弱者", server_id=22001, faze=0, battle_score=1, protect_end_time=2000),
        _seat(4, name="合法弱者", server_id=22002, faze=0, battle_score=2),
        _seat(5, name="同跨太强", server_id=22003, faze=10010, battle_score=901),
    )

    result = lundao.evaluate_lundao_room_opportunity(
        facts,
        player_profile=_profile(),
        available_count=1,
        at=datetime(2026, 7, 20, 19, 30),
        room_id=lundao.LUNDAO_DALUO_ROOM_ID,
        require_safety_threshold=True,
        data_dir=tmp_path,
        now_ms=1000,
    )

    assert result["safety_score"] == 4
    assert result["weaker_count"] == 3
    assert result["threshold"] == 2
    assert result["actionable"] is True
    assert result["action"] == "empty"
    assert result["target"] is None
    assert result["eligible_count"] == 1
    assert result["earliest_protect_end_time"] == 2000


def test_lundao_opportunity_does_not_attack_when_cushion_is_only_friendly(monkeypatch, tmp_path) -> None:
    _patch_catalog(monkeypatch)
    friendly_config = tmp_path / "fanxiu" / "server_relations.json"
    friendly_config.parent.mkdir(parents=True)
    friendly_config.write_text(
        '{"groups":[{"key":"friendly","children":[{"key":"same_server","servers":'
        '[{"server_id":22077,"server_order":1,"server_name":"本服"}]}]}]}',
        encoding="utf-8",
    )
    result = lundao.evaluate_lundao_room_opportunity(
        _facts(_seat(1, name="友军弱者", server_id=22077, faze=0, battle_score=1)),
        player_profile=_profile(),
        available_count=0,
        at=datetime(2026, 7, 20, 21, 0),
        room_id=lundao.LUNDAO_DALUO_ROOM_ID,
        require_safety_threshold=True,
        data_dir=tmp_path,
        now_ms=1000,
    )

    assert result["safety_score"] == 1
    assert result["threshold_met"] is True
    assert result["has_action"] is False
    assert result["actionable"] is False


def test_lundao_plan_stays_sanqing_with_zero_strength_and_only_completes_from_reward_time() -> None:
    now = datetime(2026, 7, 20, 19, 30)
    wait = lundao.plan_lundao_strategy(
        {"available": True, "strength": 1, "left_listen_time": 1000, "room_id": 14},
        daluo_opportunity={"ok": True, "actionable": False, "earliest_protect_end_time": None},
        at=now,
    )
    zero_strength = lundao.plan_lundao_strategy(
        {"available": True, "strength": 0, "left_listen_time": 1000, "room_id": 14},
        daluo_opportunity={"ok": True, "actionable": False, "earliest_protect_end_time": None},
        at=now,
    )
    done = lundao.plan_lundao_strategy(
        {"available": True, "strength": 0, "left_listen_time": 0, "room_id": 14},
        daluo_opportunity=None,
        at=now,
    )

    assert wait["action"] == "stay_sanqing"
    assert wait["next_time"] == datetime(2026, 7, 20, 20, 0)
    assert zero_strength["action"] == "stay_sanqing"
    assert done["action"] == "done"
    assert done["next_time"] == datetime(2026, 7, 21, 15, 30)


def test_lundao_plan_with_zero_strength_still_requests_an_actionable_daluo_seat() -> None:
    now = datetime(2026, 7, 20, 19, 30)

    decision = lundao.plan_lundao_strategy(
        {"available": True, "strength": 0, "left_listen_time": 1000, "room_id": 14},
        daluo_opportunity={"ok": True, "actionable": True, "earliest_protect_end_time": None},
        at=now,
    )

    assert decision["action"] == "seat_daluo"


def test_lundao_plan_with_zero_strength_keeps_an_existing_daluo_seat_without_purchase() -> None:
    now = datetime(2026, 7, 20, 19, 30)

    decision = lundao.plan_lundao_strategy(
        {"available": True, "strength": 0, "left_listen_time": 1000, "room_id": 15},
        daluo_opportunity=None,
        at=now,
    )

    assert decision["action"] == "stay_daluo"
    assert decision["next_time"] == datetime(2026, 7, 21, 15, 30)


def test_lundao_live_remaining_reward_time_subtracts_current_seat_elapsed_time() -> None:
    sit_down = datetime(2026, 7, 20, 16, 18, 59)
    now = datetime(2026, 7, 20, 18, 15, 9)
    status = {
        "seated": True,
        "left_listen_time": 18_900_000,
        "sit_down_time": int(sit_down.timestamp() * 1000),
    }

    remaining = lundao.current_lundao_left_listen_time(status, at=now)

    assert remaining == 11_930_000


def test_lundao_live_remaining_reward_time_is_raw_when_unseated_and_clamped_when_complete() -> None:
    now = datetime(2026, 7, 20, 18, 0)
    assert lundao.current_lundao_left_listen_time(
        {"seated": False, "left_listen_time": 18_900_000, "sit_down_time": 1},
        at=now,
    ) == 18_900_000
    assert lundao.current_lundao_left_listen_time(
        {
            "seated": True,
            "left_listen_time": 1_000,
            "sit_down_time": int((now - timedelta(seconds=2)).timestamp() * 1000),
        },
        at=now,
    ) == 0
