from __future__ import annotations

from sqlmodel import Session, SQLModel, create_engine

from backend.core.fanxiu.data_annotation.dongtian_personal_battle_observations import (
    build_dongtian_friend_personal_battle_collection_plan,
    build_fresh_dongtian_friend_personal_battle_observation,
    ingest_fresh_dongtian_friend_personal_battle_observation,
)
from backend.core.fanxiu.player_profiles import (
    list_daily_fanxiu_player_profile_records,
)


def _capabilities(**updates) -> dict:
    result = {
        "natural_gui_only": True,
        "route": "player_search_to_other_role_panel",
        "role_id_echo_available": True,
        "response_protocol": "SM_ShowOther",
        "same_server_search_verified": True,
        "cross_server_search_verified": False,
    }
    result.update(updates)
    return result


def _target(**updates) -> dict:
    result = {
        "strict_friend": True,
        "role_id": 42,
        "name": "友军甲",
        "server_id": 22001,
        "cross_server": False,
    }
    result.update(updates)
    return result


def _request(**updates) -> dict:
    result = {
        "natural_gui": True,
        "route": "player_search_to_other_role_panel",
        "search_result_identity_verified": True,
        "requested_role_id": 42,
        "search_result_role_id": 42,
        "searched_name": "友军甲",
        "requested_at": "2026-08-19T21:20:00+08:00",
    }
    result.update(updates)
    return result


def _response(**updates) -> dict:
    result = {
        "available": True,
        "complete": True,
        "protocol": "SM_ShowOther",
        "source": "saved_packet_or_readonly_runtime",
        "event_id": "packet-99",
        "role_id": 42,
        "request_role_id_echo": 42,
        "name": "友军甲",
        "battle_score": 1234,
        "battle_score_text": "1234",
        "observed_at": "2026-08-19T21:20:03+08:00",
    }
    result.update(updates)
    return result


def test_plan_blocks_unverified_cross_server_search_and_never_executes():
    result = build_dongtian_friend_personal_battle_collection_plan(
        friend_seats=[
            _target(),
            _target(role_id=43, name="跨服友军", server_id=22002),
        ],
        self_server_id=22001,
        capabilities=_capabilities(),
    )

    assert result["ok"] is False
    assert result["status"] == "dry_run_blocked"
    assert result["mode"] == "dry_run"
    assert result["targets"][0]["status"] == "ready_for_gui_research"
    assert result["targets"][1]["blocker"] == "cross_server_search_unverified"
    assert "kick_or_swap_friend" in result["forbidden_actions"]


def test_plan_requires_exact_strict_friend_identity():
    result = build_dongtian_friend_personal_battle_collection_plan(
        friend_seats=[_target(strict_friend=False)],
        self_server_id=22001,
        capabilities=_capabilities(),
    )

    assert result["ok"] is False
    assert "friend_seat_identity_incomplete" in result["blockers"]
    assert result["targets"] == []


def test_fresh_exact_role_echo_builds_independently_timed_observation():
    result = build_fresh_dongtian_friend_personal_battle_observation(
        target=_target(),
        gui_request=_request(),
        response_snapshot=_response(),
        self_server_id=22001,
    )

    assert result["ok"] is True
    observation = result["observation"]
    assert observation["role_id_text"] == "42"
    assert observation["battle_score"] == 1234
    assert observation["captured_at"] == "2026-08-19T21:20:03+08:00"
    assert "xianlv_team_observed_at" not in observation


def test_response_role_echo_mismatch_fails_closed():
    result = build_fresh_dongtian_friend_personal_battle_observation(
        target=_target(),
        gui_request=_request(),
        response_snapshot=_response(request_role_id_echo=43),
        self_server_id=22001,
    )

    assert result["ok"] is False
    assert result["reason"] == "response_role_id_echo_mismatch"


def test_stale_or_pre_request_response_fails_closed():
    stale = build_fresh_dongtian_friend_personal_battle_observation(
        target=_target(),
        gui_request=_request(),
        response_snapshot=_response(observed_at="2026-08-19T21:21:00+08:00"),
        self_server_id=22001,
    )
    old = build_fresh_dongtian_friend_personal_battle_observation(
        target=_target(),
        gui_request=_request(),
        response_snapshot=_response(observed_at="2026-08-19T21:19:59+08:00"),
        self_server_id=22001,
    )

    assert stale["reason"] == "response_not_fresh_for_request"
    assert old["reason"] == "response_not_fresh_for_request"


def test_cross_server_observation_remains_blocked_until_route_verified():
    result = build_fresh_dongtian_friend_personal_battle_observation(
        target=_target(server_id=22002, cross_server=True),
        gui_request=_request(),
        response_snapshot=_response(),
        self_server_id=22001,
    )

    assert result["ok"] is False
    assert result["reason"] == "cross_server_search_unverified"


def test_validated_observation_ingests_personal_score_without_team_timestamp():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        result = ingest_fresh_dongtian_friend_personal_battle_observation(
            session,
            target=_target(),
            gui_request=_request(),
            response_snapshot=_response(),
            self_server_id=22001,
        )
        rows = list_daily_fanxiu_player_profile_records(session)

    assert result["status"] == "observation_ingested"
    assert result["ingest"]["created"] == 1
    assert rows[0]["role_id_text"] == "42"
    assert rows[0]["battle_score"] == 1234
    assert rows[0]["xianlv_team_observed_at"] == ""
