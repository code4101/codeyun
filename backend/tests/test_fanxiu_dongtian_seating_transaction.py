from __future__ import annotations

from backend.core.fanxiu.data_annotation.dongtian_seating_transaction import (
    DongtianSeatingTransaction,
)


def _team(team_id: int = 3, power: int = 500) -> dict:
    return {
        "id": team_id,
        "state": 1,
        "mine_id": 0,
        "fight_score": power,
        "dead": False,
        "xianlv_ids": [1, 2, 3, 4, 5],
        "complete": True,
        "idle": True,
    }


def _probe(mine_id: int, *, empty: bool = False, quality: int = 2) -> dict:
    return {
        "available": True,
        "complete": True,
        "status": "ready",
        "own_union_id": 99,
        "teams": [_team()],
        "selected_mine": {
            "id": mine_id,
            "cross_union_id": 99,
            "seats_complete": True,
            "seats": [
                {
                    "id": 1,
                    "quality": quality,
                    "primary_master": quality == 1,
                    "empty": empty,
                    "guarder_type": 0 if empty else 2,
                    "guarder_cross_union_id": None if empty else 66,
                    "complete": True,
                }
            ],
        },
    }


class Session:
    def __init__(self, probes: list[dict]) -> None:
        self.probes = list(probes)
        self.detail = None
        self.final_detail = None
        self.identity_ok = True
        self.snapshot_identity_override: dict[str, int] = {}

    def probe(self, *, excluded_mine_ids: set[int]) -> dict:
        del excluded_mine_ids
        return self.probes.pop(0)

    def cached_seat_detail(self, **_kwargs) -> dict:
        detail = dict(self.detail or {})
        complete = detail.get("complete") is True
        return {
            "ok": complete,
            "available": True,
            "complete": complete,
            "cache_found": bool(detail),
            "mine_id": self.snapshot_identity_override.get(
                "mine_id", detail.get("mine_id")
            ),
            "quality": self.snapshot_identity_override.get(
                "quality", detail.get("quality")
            ),
            "seat_id": self.snapshot_identity_override.get(
                "seat_id", detail.get("seat_id")
            ),
            "detail": detail or None,
        }

    def cached_final_guard_team_detail(self, **_kwargs) -> dict:
        detail = dict(self.final_detail or {})
        complete = detail.get("complete") is True
        return {
            "ok": complete,
            "available": True,
            "complete": complete,
            "cache_found": bool(detail),
            "detail_layer": "site_info_guard_team",
            "mine_id": self.snapshot_identity_override.get(
                "mine_id", detail.get("mine_id")
            ),
            "quality": self.snapshot_identity_override.get(
                "quality", detail.get("quality")
            ),
            "seat_id": self.snapshot_identity_override.get(
                "seat_id", detail.get("seat_id")
            ),
            "detail": detail or None,
        }

    def revalidate_process_identity(self) -> dict:
        return {"ok": self.identity_ok}


def test_empty_target_is_revalidated_from_fresh_team_and_seat_facts():
    session = Session([_probe(3, empty=True), _probe(3, empty=True)])
    transaction = DongtianSeatingTransaction(session)

    decision = transaction.next_action()
    checked = transaction.revalidate_ready_target(decision)

    assert decision["status"] == "ready"
    assert decision["target"]["team_id"] == 3
    assert checked["status"] == "ready_revalidated"


def test_old_cache_is_rejected_but_new_lua_generation_is_accepted():
    session = Session([_probe(3), _probe(3), _probe(3), _probe(3)])
    transaction = DongtianSeatingTransaction(session)
    decision = transaction.next_action()
    target = decision["target"]
    base = {
        "complete": True,
        "mine_id": 3,
        "quality": 2,
        "seat_id": 1,
        "fight_score": 100,
    }

    old = transaction.record_final_guard_detail_response(
        target=target,
        before={"detail_layer": "site_info_guard_team", "detail": {**base, "cache_generation_address": 10}},
        after={"detail_layer": "site_info_guard_team", "detail": {**base, "cache_generation_address": 10}},
    )
    fresh = transaction.record_final_guard_detail_response(
        target=target,
        before={"detail_layer": "site_info_guard_team", "detail": {**base, "cache_generation_address": 10}},
        after={"detail_layer": "site_info_guard_team", "detail": {**base, "cache_generation_address": 20}},
    )
    ready = transaction.next_action()
    session.final_detail = {**base, "cache_generation_address": 20}
    checked = transaction.revalidate_ready_target(ready)

    assert decision["status"] == "need_final_detail"
    assert old["status"] == "final_detail_not_fresh"
    assert fresh["freshness"] == "fresh_runtime_generation"
    assert fresh["status"] == "final_detail_recorded"
    assert ready["status"] == "ready"
    assert checked["status"] == "ready_revalidated"


def test_process_change_fails_closed_before_irreversible_action():
    session = Session([_probe(3, empty=True)])
    transaction = DongtianSeatingTransaction(session)
    decision = transaction.next_action()
    session.identity_ok = False

    checked = transaction.revalidate_ready_target(decision)

    assert checked["ok"] is False
    assert checked["status"] == "stale_process"


def test_follower_requires_final_guard_detail_without_native_list_stage():
    session = Session([_probe(3, quality=2)])
    transaction = DongtianSeatingTransaction(session)

    decision = transaction.next_action()

    assert decision["status"] == "need_final_detail"
    assert decision["action"] == "inspect_final_guard"


def test_final_detail_rejects_non_site_info_layer():
    session = Session([_probe(3, quality=2)])
    transaction = DongtianSeatingTransaction(session)
    target = transaction.next_action()["target"]

    recorded = transaction.record_final_guard_detail_response(
        target=target,
        before=None,
        after={
            "detail_layer": "master_list",
            "detail": {
                "complete": True,
                "mine_id": 3,
                "quality": 2,
                "seat_id": 1,
                "fight_score": 100,
            },
        },
    )

    assert recorded["ok"] is False
    assert recorded["status"] == "final_detail_not_fresh"
    assert recorded["reason"] == "final_guard_detail_layer_missing"


def test_failed_before_probe_is_not_treated_as_proven_cache_absence():
    session = Session([_probe(3)])
    transaction = DongtianSeatingTransaction(session)
    decision = transaction.next_action()
    detail = {
        "complete": True,
        "mine_id": 3,
        "quality": 2,
        "seat_id": 1,
        "fight_score": 100,
    }

    recorded = transaction.record_final_guard_detail_response(
        target=decision["target"],
        before={
            "ok": False,
            "available": False,
            "complete": False,
            "cache_found": False,
            "detail": None,
        },
        after={"ok": True, "available": True, "complete": True, "detail_layer": "site_info_guard_team", "detail": detail},
    )

    assert recorded["ok"] is False
    assert recorded["status"] == "final_detail_not_fresh"
    assert recorded["freshness"] == "compatible_unproven"


def _ready_battle_transaction() -> tuple[DongtianSeatingTransaction, Session, dict]:
    session = Session([_probe(3), _probe(3), _probe(3)])
    transaction = DongtianSeatingTransaction(session)
    detail_decision = transaction.next_action()
    detail = {
        "complete": True,
        "mine_id": 3,
        "quality": 2,
        "seat_id": 1,
        "fight_score": 100,
        "team_id": 88,
        "guarder_role_id": 99,
        "cache_generation_address": 20,
    }
    final_recorded = transaction.record_final_guard_detail_response(
        target=detail_decision["target"],
        before={
            "ok": True,
            "available": True,
            "complete": True,
            "cache_found": True,
            "detail_layer": "site_info_guard_team",
            "detail": {**detail, "cache_generation_address": 30},
        },
        after={
            "ok": True,
            "available": True,
            "complete": True,
            "cache_found": True,
            "detail_layer": "site_info_guard_team",
            "detail": detail,
        },
    )
    assert final_recorded["status"] == "final_detail_recorded"
    ready = transaction.next_action()
    assert ready["status"] == "ready"
    session.final_detail = dict(detail)
    return transaction, session, ready


def test_latest_incomplete_detail_fails_closed_before_battle():
    transaction, session, ready = _ready_battle_transaction()
    session.final_detail["complete"] = False

    checked = transaction.revalidate_ready_target(ready)

    assert checked["ok"] is False
    assert checked["status"] == "target_changed"
    assert checked["reason"] == "defender_cache_changed"


def test_latest_detail_identity_mismatch_fails_closed_before_battle():
    transaction, session, ready = _ready_battle_transaction()
    session.final_detail["seat_id"] = 999

    checked = transaction.revalidate_ready_target(ready)

    assert checked["ok"] is False
    assert checked["status"] == "target_changed"
    assert checked["reason"] == "defender_cache_changed"


def test_latest_snapshot_envelope_identity_mismatch_fails_closed():
    transaction, session, ready = _ready_battle_transaction()
    session.snapshot_identity_override["mine_id"] = 999

    checked = transaction.revalidate_ready_target(ready)

    assert checked["ok"] is False
    assert checked["status"] == "target_changed"
    assert checked["reason"] == "defender_cache_changed"


def test_latest_defender_identity_change_at_same_power_fails_closed():
    transaction, session, ready = _ready_battle_transaction()
    session.final_detail["team_id"] = 777

    checked = transaction.revalidate_ready_target(ready)

    assert checked["ok"] is False
    assert checked["status"] == "target_changed"
    assert checked["reason"] == "defender_cache_changed"


def test_fresh_probe_rejects_target_when_another_team_entered_same_mine():
    transaction, session, ready = _ready_battle_transaction()
    occupied = {
        **_team(team_id=4, power=600),
        "state": 2,
        "mine_id": 3,
        "idle": False,
    }
    session.probes[-1] = {
        **_probe(3),
        "teams": [_team(), occupied],
    }

    checked = transaction.revalidate_ready_target(ready)

    assert checked["ok"] is False
    assert checked["status"] == "target_changed"
    assert checked["reason"] == "fresh_probe_changed_decision"
    assert checked.get("target") is None
