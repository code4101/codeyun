from __future__ import annotations

from pathlib import Path

from backend.core.fanxiu.data_annotation.tasks.dongtian_seating_executor import (
    DongtianSeatingGuiCapabilities,
    build_dongtian_seating_dry_run_plan,
    build_dongtian_seating_target_click_plan,
    gate_dongtian_seating_first_click_foreground,
    gate_dongtian_seating_commit,
)


class Transaction:
    def __init__(self, decision: dict, revalidation: dict | None = None) -> None:
        self.decision = decision
        self.revalidation = revalidation or {
            "ok": True,
            "status": "ready_revalidated",
            "target": decision.get("target"),
        }
        self.next_calls: list[int] = []
        self.revalidate_calls: list[dict] = []

    def next_action(self, *, max_mines: int = 39) -> dict:
        self.next_calls.append(max_mines)
        return self.decision

    def revalidate_ready_target(self, decision: dict) -> dict:
        self.revalidate_calls.append(decision)
        return self.revalidation


def _ready(*, route: str = "master_list_primary") -> dict:
    return {
        "ok": True,
        "status": "ready",
        "action": "occupy_empty",
        "target": {
            "mine_id": 7,
            "quality": 1,
            "seat_id": 2,
            "team_id": 3,
            "mode": "occupy_empty",
            "ui_route": route,
        },
    }


def test_current_capability_gaps_keep_ready_transaction_in_dry_run() -> None:
    transaction = Transaction(_ready())

    plan = build_dongtian_seating_dry_run_plan(transaction, max_mines=8)

    assert plan["dry_run"] is True
    assert plan["commit_enabled"] is False
    assert transaction.next_calls == [8]
    assert transaction.revalidate_calls == [transaction.decision]
    assert {item["code"] for item in plan["blockers"]} == {
        "scene_342_generic_detail_unverified",
        "scene_343_team_selected_state_unverified",
        "occupy_postcondition_unverified",
    }
    assert plan["gui_step"]["side_effect"] == "read_only"
    assert plan["gui_step"]["deferred_irreversible_transition"]["authorized"] is False


def test_follower_detail_plan_reports_route_without_inventing_a_click_point() -> None:
    transaction = Transaction(
        {
            "ok": True,
            "status": "need_final_detail",
            "action": "inspect_final_guard",
            "target": {
                "mine_id": 7,
                "quality": 2,
                "seat_id": 4,
                "ui_route": "follower_seat_direct",
            },
        }
    )

    plan = build_dongtian_seating_dry_run_plan(transaction)

    assert plan["commit_enabled"] is False
    assert plan["gui_step"] == {
        "kind": "observe_final_guard_detail",
        "side_effect": "read_only",
        "ui_route": "follower_seat_direct",
        "target": transaction.decision["target"],
        "source_scene_ids": [279, 341, 342],
        "expected_scene_ids": [343],
    }
    assert transaction.revalidate_calls == []
    assert "transaction_not_ready" in {item["code"] for item in plan["blockers"]}


def test_commit_gate_opens_only_after_all_gui_evidence_and_fresh_revalidation() -> None:
    transaction = Transaction(_ready())
    capabilities = DongtianSeatingGuiCapabilities(
        follower_seat_mapping=True,
        scene_342_generic_detail=True,
        scene_343_team_selected_state=True,
        occupy_postcondition=True,
    )

    gate = gate_dongtian_seating_commit(
        transaction,
        transaction.decision,
        capabilities=capabilities,
    )

    assert gate == {
        "ok": True,
        "status": "commit_ready",
        "commit_enabled": True,
        "blockers": [],
        "revalidation": transaction.revalidation,
    }


def test_fresh_revalidation_failure_blocks_even_with_complete_gui_evidence() -> None:
    transaction = Transaction(
        _ready(),
        revalidation={
            "ok": False,
            "status": "target_changed",
            "reason": "fresh_probe_changed_decision",
        },
    )
    capabilities = DongtianSeatingGuiCapabilities(True, True, True, True)

    gate = gate_dongtian_seating_commit(
        transaction,
        transaction.decision,
        capabilities=capabilities,
    )

    assert gate["commit_enabled"] is False
    assert gate["blockers"][0]["code"] == "transaction_revalidation_failed"
    assert gate["blockers"][0]["detail"]["reason"] == "fresh_probe_changed_decision"


def test_commit_gate_rejects_changed_native_default_team_even_if_probe_says_ok() -> None:
    decision = _ready()
    changed_target = {**decision["target"], "team_id": 1}
    transaction = Transaction(
        decision,
        revalidation={
            "ok": True,
            "status": "ready_revalidated",
            "target": changed_target,
        },
    )
    capabilities = DongtianSeatingGuiCapabilities(True, True, True, True)

    gate = gate_dongtian_seating_commit(
        transaction,
        decision,
        capabilities=capabilities,
    )

    assert gate["commit_enabled"] is False
    assert gate["blockers"][0]["code"] == (
        "transaction_revalidation_target_mismatch"
    )


def test_target_click_plan_rejects_changed_team_from_fresh_revalidation() -> None:
    decision = _ready_follower()
    changed_target = {**decision["target"], "team_id": 1}
    transaction = Transaction(
        decision,
        revalidation={
            "ok": True,
            "status": "ready_revalidated",
            "target": changed_target,
        },
    )

    plan = build_dongtian_seating_target_click_plan(
        transaction,
        decision,
        landing_evidence={
            "ok": True,
            "status": "click_authorized",
            "mine_id": 7,
        },
        capabilities=DongtianSeatingGuiCapabilities(follower_seat_mapping=True),
        scroll_offset_verified=True,
    )

    assert plan["click_enabled"] is False
    assert plan["reason"] == "revalidated_target_mismatch"


def test_executor_module_contains_no_gui_mutation_primitive() -> None:
    source = Path(
        "backend/core/fanxiu/data_annotation/tasks/dongtian_seating_executor.py"
    ).read_text(encoding="utf-8")

    assert ".click" not in source
    assert "wait_click" not in source
    assert "run_task(" not in source


def _ready_follower() -> dict:
    target = {
        "mine_id": 7,
        "quality": 2,
        "seat_id": 5,
        "seat_key": "7:2:5",
        "team_id": 3,
        "mode": "occupy_empty",
        "ui_route": "follower_seat_direct",
        "friendly_place": True,
    }
    return {
        "ok": True,
        "status": "ready",
        "action": "occupy_empty",
        "target": target,
        "probe": {
            "selected_mine": {"id": 7, "config_group": 4},
        },
    }


def _fresh_scene_341() -> dict:
    return {
        "fresh": True,
        "scene_id": 341,
        "layer": "layer0",
        "status": "matched",
    }


def test_follower_commit_gate_does_not_require_unrelated_scene_342() -> None:
    transaction = Transaction(_ready_follower())
    capabilities = DongtianSeatingGuiCapabilities(
        follower_seat_mapping=True,
        scene_342_generic_detail=False,
        scene_343_team_selected_state=True,
        occupy_postcondition=True,
    )

    gate = gate_dongtian_seating_commit(
        transaction,
        transaction.decision,
        capabilities=capabilities,
    )

    assert gate["commit_enabled"] is True
    assert gate["blockers"] == []


def test_target_click_plan_revalidates_and_projects_exact_follower_seat() -> None:
    decision = _ready_follower()
    transaction = Transaction(decision)
    capabilities = DongtianSeatingGuiCapabilities(follower_seat_mapping=True)

    plan = build_dongtian_seating_target_click_plan(
        transaction,
        decision,
        landing_evidence={
            "ok": True,
            "status": "click_authorized",
            "mine_id": 7,
            "place_name": "月胎穴",
        },
        foreground_evidence=_fresh_scene_341(),
        capabilities=capabilities,
        scroll_offset_verified=True,
    )

    assert plan["click_enabled"] is True
    assert plan["irreversible"] is False
    assert plan["step"] == {
        "scene_id": 341,
        "locator_kind": "projected_point",
        "shape_title": None,
        "point": [447, 754],
        "expected_scene_ids": [343],
    }
    assert transaction.revalidate_calls == [decision]


def test_target_click_plan_fails_closed_without_verified_empty_hitbox() -> None:
    decision = _ready_follower()
    plan = build_dongtian_seating_target_click_plan(
        Transaction(decision),
        decision,
        landing_evidence={
            "ok": True,
            "status": "click_authorized",
            "mine_id": 7,
        },
        foreground_evidence=_fresh_scene_341(),
        scroll_offset_verified=True,
    )

    assert plan["click_enabled"] is False
    assert plan["reason"] == "target_geometry_unverified"
    assert plan["evidence"]["point"] == [447, 754]
    assert plan["evidence"]["route_blockers"] == [
        "empty_follower_hitbox_unverified"
    ]


def test_target_click_plan_rejects_landing_for_another_mine() -> None:
    decision = _ready_follower()
    plan = build_dongtian_seating_target_click_plan(
        Transaction(decision),
        decision,
        landing_evidence={
            "ok": True,
            "status": "click_authorized",
            "mine_id": 8,
        },
        foreground_evidence=_fresh_scene_341(),
        capabilities=DongtianSeatingGuiCapabilities(follower_seat_mapping=True),
        scroll_offset_verified=True,
    )

    assert plan["click_enabled"] is False
    assert plan["reason"] == "landing_evidence_mismatch"


def test_first_click_foreground_gate_accepts_only_exact_fresh_source_scene() -> None:
    gate = gate_dongtian_seating_first_click_foreground(
        _fresh_scene_341(),
        expected_scene_id=341,
    )

    assert gate["ok"] is True
    assert gate["click_enabled"] is True
    assert gate["interruption_action"] == "none"


def test_target_click_plan_blocks_scene_284_overlay_without_cleanup_action() -> None:
    decision = _ready_follower()
    plan = build_dongtian_seating_target_click_plan(
        Transaction(decision),
        decision,
        landing_evidence={
            "ok": True,
            "status": "click_authorized",
            "mine_id": 7,
        },
        foreground_evidence={
            "fresh": True,
            "scene_id": 284,
            "layer": "layer2",
            "status": "matched",
        },
        capabilities=DongtianSeatingGuiCapabilities(follower_seat_mapping=True),
        scroll_offset_verified=True,
    )

    assert plan["click_enabled"] is False
    assert plan["reason"] == "foreground_scene_mismatch"
    assert plan["evidence"]["foreground_gate"]["evidence"]["blocking_scene_id"] == 284
    assert plan["evidence"]["foreground_gate"]["interruption_action"] == "none"


def test_first_click_foreground_gate_rejects_layer3_and_unknown() -> None:
    layer3 = gate_dongtian_seating_first_click_foreground(
        {
            "fresh": True,
            "scene_id": None,
            "layer": "layer3",
            "status": "auxiliary_match",
            "reference_id": 284,
        },
        expected_scene_id=341,
    )
    unknown = gate_dongtian_seating_first_click_foreground(
        {
            "fresh": True,
            "scene_id": None,
            "layer": "layer2",
            "status": "unknown",
        },
        expected_scene_id=341,
    )

    assert layer3["reason"] == "foreground_scene_unresolved"
    assert unknown["reason"] == "foreground_scene_unresolved"
    assert layer3["interruption_action"] == "none"
    assert unknown["interruption_action"] == "none"


def test_target_click_plan_fails_closed_without_fresh_foreground_evidence() -> None:
    decision = _ready_follower()
    plan = build_dongtian_seating_target_click_plan(
        Transaction(decision),
        decision,
        landing_evidence={
            "ok": True,
            "status": "click_authorized",
            "mine_id": 7,
        },
        capabilities=DongtianSeatingGuiCapabilities(follower_seat_mapping=True),
        scroll_offset_verified=True,
    )

    assert plan["click_enabled"] is False
    assert plan["reason"] == "foreground_evidence_missing"
