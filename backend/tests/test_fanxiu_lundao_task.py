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

    assert [result["target"]["name"], result["eligible_count"]] == ["低跨再高也压制", 2]
    assert result["rejected"]["unsafe_same_law_power"] == 1
    assert result["rejected"]["stronger_law"] == 1


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
