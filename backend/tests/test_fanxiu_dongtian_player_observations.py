from __future__ import annotations

from sqlmodel import Session, SQLModel, create_engine

from backend.core.fanxiu.data_annotation.dongtian_player_observations import (
    build_fresh_dongtian_xianlv_team_observation,
    ingest_fresh_dongtian_xianlv_team_observation,
)
from backend.core.fanxiu.player_profiles import (
    list_daily_fanxiu_player_xianlv_team_records,
)


def _probe(role_id: int, *, pid: int = 11, start_ticks: int = 22) -> dict:
    return {
        "available": True,
        "complete": True,
        "selected_mine": {
            "id": 3,
            "seats_complete": True,
            "seats": [
                {
                    "id": 7,
                    "quality": 2,
                    "empty": False,
                    "complete": True,
                    "guarder_role_id": role_id,
                }
            ],
        },
        "evidence": {"pid": pid, "process_start_ticks": start_ticks},
    }


def _target(*, quality: int = 2) -> dict:
    return {
        "mine_id": 3,
        "quality": quality,
        "seat_id": 7,
        "guarder_role_id": 42,
    }


def _detail_snapshot(
    generation: int,
    *,
    quality: int = 2,
    score: int = 600,
    source_cache: str = "V_GuarderTeamDic",
) -> dict:
    protocol = (
        "dongtian.seat-detail.final-guard-team-cache.v1"
        if source_cache == "V_GuarderTeamDic"
        else "dongtian.seat-detail.cache.v1"
    )
    result = {
        "ok": True,
        "available": True,
        "complete": True,
        "cache_found": True,
        "source": "runtime_memory_cache",
        "protocol": protocol,
        "detail": {
            "complete": True,
            "mine_id": 3,
            "quality": quality,
            "seat_id": 7,
            "fight_score": score,
            "team_id": 99,
            "cache_generation_address": generation,
        },
        "evidence": {"pid": 11, "process_start_ticks": 22},
    }
    if source_cache == "V_GuarderTeamDic":
        result["detail_layer"] = "site_info_guard_team"
    else:
        result["detail"]["guarder_role_id"] = 42
    return result


def _kwargs(**updates) -> dict:
    values = {
        "source_cache": "V_GuarderTeamDic",
        "target": _target(),
        "before_probe": _probe(42),
        "after_probe": _probe(42),
        "before_detail_snapshot": _detail_snapshot(100),
        "after_detail_snapshot": _detail_snapshot(200),
        "observed_at": "2026-08-19T20:25:00+08:00",
    }
    values.update(updates)
    return values


def test_fresh_guard_team_is_ingested_without_public_team_slot():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        result = ingest_fresh_dongtian_xianlv_team_observation(
            session,
            **_kwargs(),
        )
        rows = list_daily_fanxiu_player_xianlv_team_records(session)

    assert result["status"] == "observation_ingested"
    assert result["ingest"]["created"] == 1
    assert rows[0]["role_id_text"] == "42"
    assert rows[0]["xianlv_team_fight_score_max"] == 600
    assert "xianlv_team_slot" not in rows[0]
    assert rows[0]["source_kind"] == "dongtian_xianlv_team_runtime"


def test_master_list_source_requires_master_and_preserves_exact_role():
    before = _detail_snapshot(
        100,
        quality=1,
        source_cache="V_MineMasterDetailListDic",
    )
    after = _detail_snapshot(
        200,
        quality=1,
        source_cache="V_MineMasterDetailListDic",
    )
    before_probe = _probe(42)
    after_probe = _probe(42)
    before_probe["selected_mine"]["seats"][0]["quality"] = 1
    after_probe["selected_mine"]["seats"][0]["quality"] = 1

    result = build_fresh_dongtian_xianlv_team_observation(
        **_kwargs(
            source_cache="V_MineMasterDetailListDic",
            target=_target(quality=1),
            before_probe=before_probe,
            after_probe=after_probe,
            before_detail_snapshot=before,
            after_detail_snapshot=after,
        )
    )

    assert result["ok"] is True
    assert result["observation"]["evidence"]["source_cache"] == "V_MineMasterDetailListDic"
    assert "xianlv_team_slot" not in result["observation"]


def test_changed_occupant_fails_closed_without_ingest():
    result = build_fresh_dongtian_xianlv_team_observation(
        **_kwargs(after_probe=_probe(43))
    )

    assert result == {
        "ok": False,
        "status": "observation_rejected",
        "reason": "occupant_identity_changed",
        "observation": None,
    }


def test_unchanged_cache_generation_is_not_fresh():
    result = build_fresh_dongtian_xianlv_team_observation(
        **_kwargs(after_detail_snapshot=_detail_snapshot(100))
    )

    assert result["ok"] is False
    assert result["reason"] == "detail_not_fresh"


def test_process_change_between_probe_and_detail_fails_closed():
    result = build_fresh_dongtian_xianlv_team_observation(
        **_kwargs(after_probe=_probe(42, start_ticks=23))
    )

    assert result["ok"] is False
    assert result["reason"] == "process_identity_changed_or_missing"


def test_daily_projection_keeps_highest_observed_team_score():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        ingest_fresh_dongtian_xianlv_team_observation(
            session,
            **_kwargs(
                after_detail_snapshot=_detail_snapshot(200, score=500),
                observed_at="2026-08-19T20:25:00+08:00",
            ),
        )
        ingest_fresh_dongtian_xianlv_team_observation(
            session,
            **_kwargs(
                before_detail_snapshot=_detail_snapshot(200, score=500),
                after_detail_snapshot=_detail_snapshot(300, score=700),
                observed_at="2026-08-19T20:26:00+08:00",
            ),
        )
        rows = list_daily_fanxiu_player_xianlv_team_records(session)

    assert len(rows) == 1
    assert rows[0]["xianlv_team_fight_score_max"] == 700
