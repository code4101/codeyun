from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from backend.core.fanxiu.data_annotation.dongtian_seat_geometry import (
    DEFAULT_VIEWPORT,
    resolve_dongtian_target_gui_route,
)

from backend.core.fanxiu.data_annotation.dongtian_seating_transaction import (
    DongtianSeatingTransaction,
)


@dataclass(frozen=True)
class DongtianSeatingGuiCapabilities:
    """GUI evidence required before an occupy action may be authorized.

    All capabilities default to false.  A caller must opt in only after the
    corresponding asset and real Runtime transition have been verified.
    Keeping this declaration separate from the Runtime transaction prevents a
    GUI shortcut from silently weakening the authoritative seating decision.
    """

    follower_seat_mapping: bool = False
    scene_342_generic_detail: bool = False
    scene_343_team_selected_state: bool = False
    occupy_postcondition: bool = False


CURRENT_DONGTIAN_SEATING_GUI_CAPABILITIES = DongtianSeatingGuiCapabilities()


def gate_dongtian_seating_first_click_foreground(
    evidence: Mapping[str, Any] | None,
    *,
    expected_scene_id: int,
) -> dict[str, Any]:
    """Require fresh foreground ownership before the first seating click.

    ``evidence`` is produced from one fresh unified Layer 0--3 recognition
    pass with interruption handling disabled.  The gate is intentionally
    stricter than a base-page check: the *resolved foreground* must be the
    exact source scene of the planned click.  A business overlay such as
    ``#284`` therefore blocks the click instead of being closed, claimed, or
    treated as harmless noise.  Layer 3 is diagnostic-only and can never
    authorize an action.

    Expected evidence keys are ``fresh``, ``scene_id``, ``layer`` and
    ``status``.  Callers must not synthesize these values from Runtime state;
    they describe the GUI frame on which the click would land.
    """

    def blocked(reason: str, **detail: Any) -> dict[str, Any]:
        return {
            "ok": False,
            "status": "foreground_blocked",
            "reason": reason,
            "click_enabled": False,
            "interruption_action": "none",
            "evidence": detail,
        }

    if not isinstance(evidence, Mapping):
        return blocked("foreground_evidence_missing")
    if evidence.get("fresh") is not True:
        return blocked("foreground_evidence_stale", observed=dict(evidence))

    layer = str(evidence.get("layer") or "").strip().lower()
    status = str(evidence.get("status") or "").strip().lower()
    scene_id = evidence.get("scene_id")
    if layer == "layer3" or status in {
        "ambiguous",
        "no_match",
        "unknown",
        "unavailable",
    }:
        return blocked("foreground_scene_unresolved", observed=dict(evidence))
    if layer not in {"layer0", "layer1", "layer2"}:
        return blocked("foreground_layer_invalid", observed=dict(evidence))
    if isinstance(scene_id, bool) or not isinstance(scene_id, int):
        return blocked("foreground_scene_unresolved", observed=dict(evidence))
    if int(scene_id) != int(expected_scene_id):
        return blocked(
            "foreground_scene_mismatch",
            expected_scene_id=int(expected_scene_id),
            blocking_scene_id=int(scene_id),
            observed=dict(evidence),
        )
    return {
        "ok": True,
        "status": "foreground_ready",
        "reason": None,
        "click_enabled": True,
        "interruption_action": "none",
        "evidence": dict(evidence),
    }

_COMMIT_TARGET_FIELDS = (
    "mine_id",
    "quality",
    "seat_id",
    "team_id",
    "mode",
    "ui_route",
)


def _same_commit_target(
    expected: Mapping[str, Any],
    observed: Mapping[str, Any] | None,
) -> bool:
    return isinstance(observed, Mapping) and all(
        observed.get(field) == expected.get(field)
        for field in _COMMIT_TARGET_FIELDS
    )


def _blocker(
    code: str,
    *,
    stage: str,
    scene_ids: tuple[int, ...],
    message: str,
) -> dict[str, Any]:
    return {
        "code": code,
        "stage": stage,
        "scene_ids": list(scene_ids),
        "message": message,
    }


def dongtian_seating_capability_blockers(
    capabilities: DongtianSeatingGuiCapabilities = (
        CURRENT_DONGTIAN_SEATING_GUI_CAPABILITIES
    ),
    *,
    decision: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Describe unverified GUI facts relevant to one decision.

    With no decision this remains a full capability inventory.  A concrete
    follower target goes directly from #341 to #343, so it must not be blocked
    by the unrelated #342 master-list evidence.  Conversely, a master target
    does not consume the fixed follower-seat mapping.
    """

    blockers: list[dict[str, Any]] = []
    target = decision.get("target") if isinstance(decision, Mapping) else None
    quality = target.get("quality") if isinstance(target, Mapping) else None
    follower_route = quality == 2
    master_route = quality == 1
    inventory = decision is None
    if (inventory or follower_route) and not capabilities.follower_seat_mapping:
        blockers.append(
            _blocker(
                "follower_seat_mapping_missing",
                stage="target_navigation",
                scene_ids=(279, 341),
                message="从 #279 到从座详情的 Runtime seat -> GUI 落点映射尚未验收",
            )
        )
    if (inventory or master_route) and not capabilities.scene_342_generic_detail:
        blockers.append(
            _blocker(
                "scene_342_generic_detail_unverified",
                stage="detail_navigation",
                scene_ids=(341, 342),
                message="#342 只能证明既有样本，尚未验收为所有目标均可复用的详情页",
            )
        )
    if not capabilities.scene_343_team_selected_state:
        blockers.append(
            _blocker(
                "scene_343_team_selected_state_unverified",
                stage="team_selection",
                scene_ids=(343,),
                message="#343 尚无可证明指定 Runtime team 已被选中的正式状态锚点",
            )
        )
    if not capabilities.occupy_postcondition:
        blockers.append(
            _blocker(
                "occupy_postcondition_unverified",
                stage="postcondition",
                scene_ids=(344, 606, 279),
                message="占领后的 #344/#606 分支及最终 Runtime 入座事实尚未形成验收闭环",
            )
        )
    return blockers


def _read_only_gui_step(decision: Mapping[str, Any]) -> dict[str, Any] | None:
    """Translate a transaction decision into observation-only GUI intent."""

    action = str(decision.get("action") or "")
    target = decision.get("target")
    route = str(target.get("ui_route") or "") if isinstance(target, Mapping) else ""
    common = {
        "side_effect": "read_only",
        "ui_route": route or None,
        "target": dict(target) if isinstance(target, Mapping) else None,
    }
    if action in {"inspect_defender", "refresh_defender"}:
        return {
            **common,
            "kind": "observe_native_detail",
            "source_scene_ids": [279, 341],
            "expected_scene_ids": [342],
        }
    if action == "inspect_final_guard":
        return {
            **common,
            "kind": "observe_final_guard_detail",
            "source_scene_ids": [279, 341, 342],
            "expected_scene_ids": [343],
        }
    if decision.get("status") == "ready":
        return {
            **common,
            "kind": "observe_commit_preconditions",
            "source_scene_ids": [279, 341, 342, 343],
            "expected_scene_ids": [343],
            "deferred_irreversible_transition": {
                "source_scene_id": 343,
                "candidate_scene_ids": [344, 606, 279],
                "authorized": False,
            },
        }
    return None


def gate_dongtian_seating_commit(
    transaction: DongtianSeatingTransaction,
    decision: Mapping[str, Any],
    *,
    capabilities: DongtianSeatingGuiCapabilities = (
        CURRENT_DONGTIAN_SEATING_GUI_CAPABILITIES
    ),
) -> dict[str, Any]:
    """Fail closed before an irreversible occupy action.

    This function is deliberately only a gate: it has no GUI/runtime action
    dependency and cannot perform the occupy transition.  Even after all GUI
    capabilities are proven, a fresh transaction revalidation is required and
    the caller must consume ``commit_enabled`` explicitly in a future executor.
    """

    blockers = dongtian_seating_capability_blockers(
        capabilities,
        decision=decision,
    )
    revalidation: dict[str, Any] | None = None
    if decision.get("status") != "ready":
        blockers.append(
            _blocker(
                "transaction_not_ready",
                stage="runtime_decision",
                scene_ids=(),
                message="DongtianSeatingTransaction 尚未返回 ready",
            )
        )
    else:
        revalidation = transaction.revalidate_ready_target(decision)
        if not revalidation.get("ok"):
            blockers.append(
                {
                    **_blocker(
                        "transaction_revalidation_failed",
                        stage="runtime_revalidation",
                        scene_ids=(),
                        message="提交前 Runtime 目标或进程身份已变化",
                    ),
                    "detail": dict(revalidation),
                }
            )
        else:
            expected_target = decision.get("target")
            observed_target = revalidation.get("target")
            if not isinstance(expected_target, Mapping) or not _same_commit_target(
                expected_target,
                observed_target if isinstance(observed_target, Mapping) else None,
            ):
                blockers.append(
                    {
                        **_blocker(
                            "transaction_revalidation_target_mismatch",
                            stage="runtime_revalidation",
                            scene_ids=(),
                            message="提交前 Runtime 重验返回了不同座位或队伍",
                        ),
                        "detail": dict(revalidation),
                    }
                )

    return {
        "ok": not blockers,
        "status": "commit_ready" if not blockers else "commit_blocked",
        "commit_enabled": not blockers,
        "blockers": blockers,
        "revalidation": revalidation,
    }


def build_dongtian_seating_target_click_plan(
    transaction: DongtianSeatingTransaction,
    decision: Mapping[str, Any],
    *,
    landing_evidence: Mapping[str, Any],
    foreground_evidence: Mapping[str, Any] | None = None,
    capabilities: DongtianSeatingGuiCapabilities = (
        CURRENT_DONGTIAN_SEATING_GUI_CAPABILITIES
    ),
    viewport: Sequence[int] = DEFAULT_VIEWPORT,
    scene_341_shapes: Mapping[str, Mapping[str, Any]] | None = None,
    scene_342_shapes: Mapping[str, Mapping[str, Any]] | None = None,
    scroll_offset_verified: bool = False,
) -> dict[str, Any]:
    """Prepare the first reversible target-navigation click for a research Cell.

    This helper deliberately stops before team selection and before the
    irreversible ``#343[占领]`` action.  It accepts only a transaction-ready
    Runtime target, a fresh transaction revalidation, and independent evidence
    that GUI navigation landed on the same mine.  Immediately before exposing
    the click, a fresh unified Layer 0--3 GUI observation must also prove that
    the resolved foreground is the exact source scene.  The mine group is
    derived from the decision's Runtime probe rather than supplied by the
    caller.
    """

    def blocked(reason: str, **evidence: Any) -> dict[str, Any]:
        return {
            "ok": False,
            "status": "target_click_blocked",
            "reason": reason,
            "click_enabled": False,
            "step": None,
            "evidence": evidence,
        }

    if decision.get("status") != "ready":
        return blocked("transaction_not_ready")
    target = decision.get("target")
    probe = decision.get("probe")
    selected_mine = probe.get("selected_mine") if isinstance(probe, Mapping) else None
    if not isinstance(target, Mapping) or not isinstance(selected_mine, Mapping):
        return blocked("runtime_target_or_probe_missing")

    revalidation = transaction.revalidate_ready_target(decision)
    if not revalidation.get("ok"):
        return blocked("transaction_revalidation_failed", revalidation=dict(revalidation))
    fresh_target = revalidation.get("target")
    if not isinstance(fresh_target, Mapping):
        return blocked("revalidated_target_missing")
    if not _same_commit_target(target, fresh_target):
        return blocked(
            "revalidated_target_mismatch",
            revalidation=dict(revalidation),
        )

    target_mine_id = target.get("mine_id")
    if (
        landing_evidence.get("ok") is not True
        or landing_evidence.get("status") != "click_authorized"
        or landing_evidence.get("mine_id") != target_mine_id
    ):
        return blocked("landing_evidence_mismatch")
    if selected_mine.get("id") != target_mine_id:
        return blocked("selected_mine_mismatch")
    group = selected_mine.get("config_group")
    if isinstance(group, bool) or not isinstance(group, int):
        return blocked("mine_group_missing")

    try:
        route = resolve_dongtian_target_gui_route(
            fresh_target,
            group=group,
            viewport=viewport,
            scene_341_shapes=scene_341_shapes,
            scene_342_shapes=scene_342_shapes,
            scroll_offset_verified=scroll_offset_verified,
            empty_hitbox_verified=capabilities.follower_seat_mapping,
        )
    except (TypeError, ValueError) as exc:
        return blocked("target_geometry_invalid", detail=str(exc))
    if not route.steps:
        return blocked("target_route_empty")
    step = route.steps[0]
    if not step.verified_for_click or step.point is None or route.blockers:
        return blocked(
            "target_geometry_unverified",
            route_blockers=list(route.blockers),
            point=list(step.point) if step.point is not None else None,
        )

    foreground_gate = gate_dongtian_seating_first_click_foreground(
        foreground_evidence,
        expected_scene_id=int(step.scene_id),
    )
    if not foreground_gate.get("ok"):
        return blocked(
            str(foreground_gate.get("reason") or "foreground_blocked"),
            foreground_gate=dict(foreground_gate),
        )

    return {
        "ok": True,
        "status": "target_click_ready",
        "click_enabled": True,
        "irreversible": False,
        "target": dict(fresh_target),
        "step": {
            "scene_id": step.scene_id,
            "locator_kind": step.locator_kind,
            "shape_title": step.shape_title,
            "point": list(step.point),
            "expected_scene_ids": list(step.expected_scene_ids),
        },
        "evidence": {
            "mine_id": target_mine_id,
            "group": group,
            "landing": dict(landing_evidence),
            "revalidation": dict(revalidation),
            "foreground_gate": dict(foreground_gate),
        },
    }


def build_dongtian_seating_dry_run_plan(
    transaction: DongtianSeatingTransaction,
    *,
    capabilities: DongtianSeatingGuiCapabilities = (
        CURRENT_DONGTIAN_SEATING_GUI_CAPABILITIES
    ),
    max_mines: int = 39,
) -> dict[str, Any]:
    """Build one bounded GUI plan without performing any GUI action."""

    decision = transaction.next_action(max_mines=max_mines)
    gate = gate_dongtian_seating_commit(
        transaction,
        decision,
        capabilities=capabilities,
    )
    gui_step = _read_only_gui_step(decision)
    return {
        "ok": bool(decision.get("ok")),
        "status": "dry_run_ready" if gate["commit_enabled"] else "dry_run_blocked",
        "dry_run": True,
        "commit_enabled": bool(gate["commit_enabled"]),
        "decision": dict(decision),
        "gui_step": gui_step,
        "blockers": list(gate["blockers"]),
        "revalidation": gate["revalidation"],
    }


__all__ = [
    "CURRENT_DONGTIAN_SEATING_GUI_CAPABILITIES",
    "DongtianSeatingGuiCapabilities",
    "build_dongtian_seating_target_click_plan",
    "build_dongtian_seating_dry_run_plan",
    "dongtian_seating_capability_blockers",
    "gate_dongtian_seating_first_click_foreground",
    "gate_dongtian_seating_commit",
]
