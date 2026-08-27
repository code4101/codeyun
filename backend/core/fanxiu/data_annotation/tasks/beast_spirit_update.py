from __future__ import annotations

import re
import threading
import time as monotonic_time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Any

from backend.core.fanxiu.data_annotation.unknown_recovery import (
    build_unknown_evidence,
)
from backend.core.fanxiu.instrumentation.service import (
    fanxiu_instrumentation_service,
)
from backend.core.fanxiu.runtime_gui import validate_runtime_evidence


STANDARD_JOB_ID = "beast-spirit-update"


def next_beast_spirit_update_at(now: datetime | None = None) -> datetime:
    """Return the next Tuesday 00:30 strictly after ``now``."""

    current = now or datetime.now()
    days_until_tuesday = (1 - current.weekday()) % 7
    candidate = datetime.combine(
        current.date() + timedelta(days=days_until_tuesday),
        time(hour=0, minute=30),
    )
    if candidate <= current:
        candidate += timedelta(days=7)
    return candidate


BEAST_SOUL_MAIN_SCENE = 478
BEAST_SOUL_DETAIL_SCENE = 479


class BeastSoulTargetNotFoundError(RuntimeError):
    """The complete bounded bag scan did not find the planned instance."""

    def __init__(self, item_id: str, message: str) -> None:
        super().__init__(message)
        self.item_id = str(item_id)
BEAST_SOUL_SYNTHESIS_SCENE = 480
BEAST_SOUL_QUICK_SYNTHESIS_SCENE = 481
BEAST_SOUL_MATERIAL_DROPDOWN_SCENE = 482
BEAST_SOUL_POST_SYNTHESIS_CONTINUE_SCENE = 346
SPIRIT_BEAST_MAIN_SCENE = 483
BEAST_SOUL_LOW_SUCCESS_CONFIRMATION_SCENE = 527
BEAST_SOUL_PRECIOUS_MATERIAL_CONFIRMATION_SCENE = 529
BEAST_SOUL_LOW_SUCCESS_REQUIRED_SHAPES = (
    "低成功率确认正文",
    "取消文字",
    "确认文字",
)
BEAST_SOUL_PRECIOUS_MATERIAL_REQUIRED_SHAPES = (
    "珍稀材料确认正文",
    "取消文字",
    "确认文字",
)

LEVEL_LABELS = {
    1: "一级",
    2: "二级",
    3: "三级",
    4: "四级",
    5: "神品",
    6: "神品一星",
    7: "神品二星",
    8: "神品三星",
    9: "神品四星",
}
BEAST_SOUL_SOUL_ATTR_ID = 10500001
BEAST_SOUL_BLOOD_RATE_ATTR_ID = 1002
_SIGNATURE_SAFE_MARGIN = 4
# Gen303/gen305 repeatedly moved exactly one five-slot row for the verified
# default drag.  Coarse registration deliberately budgets twice that observed
# displacement per gesture.  This is only a planning bound: every batch is
# followed by a real sequence anchor, and any overshoot fails closed.
_COARSE_SCROLL_MAX_ADVANCE_SLOTS = 10
_COARSE_SCROLL_NUMERATOR = 3
_COARSE_SCROLL_DENOMINATOR = 4
_FINE_REGISTRATION_DISTANCE_SLOTS = 20


@dataclass(frozen=True)
class QuickSynthesisPolicy:
    """One verified game policy for quick soul-crystal synthesis."""

    level: int
    batch_size: int
    success_probability: float
    auto_confirm_low_success: bool
    requires_precious_material_confirmation: bool
    confirmation_text: tuple[str, str] | None
    expected_material_cost: float


def quick_synthesis_policy(level: int) -> QuickSynthesisPolicy:
    """Return the verified synthesis policy without re-deriving it at runtime.

    Real 2026-08-09 batches proved that failures consume the complete batch and
    produce no upgraded crystal (evidence route: ``docs/domains/fanxiu/jobs/凡修兽魂研究与自动配置.md``
    H5/C2 and facts 30-31).  Therefore expected material cost is ``n / p``.
    Levels 1-3 use the material-optimal 2-at-55% policy and deliberately accept
    the exact low-success confirmation.  Levels 4+ use the separately verified
    fixed 3-at-100% policy.
    """

    source_level = int(level)
    if source_level < 1 or source_level > 8:
        raise ValueError(f"unsupported quick synthesis source level: {source_level}")
    if source_level <= 3:
        batch_size = 2
        probability = 0.55
        auto_confirm = True
        confirmation_text = ("当前成功率较低", "是否确认进行合成")
    else:
        batch_size = 3
        probability = 1.0
        auto_confirm = False
        confirmation_text = None
    return QuickSynthesisPolicy(
        level=source_level,
        batch_size=batch_size,
        success_probability=probability,
        auto_confirm_low_success=auto_confirm,
        # UpgradeProbability.materialLevel is 4 for source level 5 and 5
        # for source level 6; NEED_SECEND_CONFIRM_LEVEL is 5.  The Lua panel
        # opens its precious-material confirmation when any materialLevel is
        # >= that threshold, hence source levels 6+ require #529.
        requires_precious_material_confirmation=source_level >= 6,
        confirmation_text=confirmation_text,
        expected_material_cost=batch_size / probability,
    )


def synthesis_batch_size(level: int) -> int:
    """Compatibility wrapper for callers that only need the batch size."""

    return quick_synthesis_policy(level).batch_size


def consumable_count(snapshot: dict[str, Any], level: int) -> int:
    """Count items that the game's quick synthesis request may consume."""

    return sum(
        1
        for item in snapshot.get("items") or []
        if int(item.get("level") or 0) == int(level)
        and not item.get("equipped")
        and not item.get("locked")
        and not item.get("excluded_from_quick_synthesis")
    )


def synthesis_gate(snapshot: dict[str, Any], level: int) -> dict[str, Any]:
    """Fail closed unless the actual lock set exactly matches the protection plan."""

    layout = snapshot.get("layout") or {}
    unlocked = list(layout.get("unlocked_protected_item_ids") or [])
    obsolete = list(layout.get("obsolete_locked_item_ids") or [])
    if unlocked or obsolete or not bool(layout.get("safe_to_synthesize")):
        return {
            "allowed": False,
            "reason": "lock_set_mismatch",
            "unlocked_protected_item_ids": unlocked,
            "obsolete_locked_item_ids": obsolete,
        }
    count = consumable_count(snapshot, level)
    batch_size = quick_synthesis_policy(level).batch_size
    if count < batch_size:
        return {
            "allowed": False,
            "reason": "insufficient_consumable_items",
            "consumable_count": count,
            "batch_size": batch_size,
        }
    return {
        "allowed": True,
        "reason": "ready",
        "consumable_count": count,
        "batch_size": batch_size,
    }


def _settle(runtime: Any, seconds: float = 1.0):
    yield from runtime.wait_action_settle(seconds)


def _require_high_level_quick_synthesis_state(
    level: int,
    policy: QuickSynthesisPolicy,
) -> dict[str, Any]:
    snapshot = fanxiu_instrumentation_service.beast_spirit_quick_synthesis_snapshot()
    evidence = snapshot.get("evidence") or {}
    validation = validate_runtime_evidence(
        snapshot,
        max_age_seconds=5.0,
        now_epoch=monotonic_time.time(),
    )
    age = validation.age_seconds
    complete = (
        snapshot.get("ok") is True
        and snapshot.get("available") is True
        and validation.ok
        and snapshot.get("source") == "active_beast_spirit_batch_strength_panel"
        and type(evidence.get("pid")) is int
        and type(evidence.get("process_start_ticks")) is int
        and evidence.get("read_only") is True
        and age is not None
        and 0 <= age <= 5
    )
    expected_probability = policy.success_probability * 100
    matches = (
        snapshot.get("source_level") == int(level)
        and snapshot.get("batch_size") == int(policy.batch_size)
        and isinstance(snapshot.get("success_probability_percent"), (int, float))
        and not isinstance(snapshot.get("success_probability_percent"), bool)
        and abs(
            float(snapshot["success_probability_percent"])
            - expected_probability
        ) < 1e-9
    )
    if not complete or not matches:
        raise RuntimeError(
            "兽魂更新：高阶快捷合成只读面板状态不完整或不一致："
            f"level={snapshot.get('source_level')}, "
            f"count={snapshot.get('batch_size')}, "
            f"probability={snapshot.get('success_probability_percent')}, "
            f"age={age}, reason={snapshot.get('reason') or 'mismatch'}"
        )
    return snapshot


def _enter_beast_soul_main(runtime: Any):
    """Enter #478 through the annotated #35 -> #483 -> #478 path."""

    current_scene, _score, _frame = runtime.current_scene(
        views=[BEAST_SOUL_MAIN_SCENE],
        update=True,
    )
    if current_scene == BEAST_SOUL_MAIN_SCENE:
        return
    from backend.core.fanxiu.data_annotation.tasks.world_menu_navigation import (
        open_world_menu_function,
    )

    yield from open_world_menu_function(
        runtime,
        4000,
        expected_scene_ids=(SPIRIT_BEAST_MAIN_SCENE,),
        timeout_seconds=20,
    )
    yield from runtime.wait_click(SPIRIT_BEAST_MAIN_SCENE, "兽魂页签")
    yield from runtime.wait_scene(
        BEAST_SOUL_MAIN_SCENE,
        timeout=12,
        label="兽魂更新：等待兽魂主页",
    )


def _enter_quick_synthesis(runtime: Any):
    yield from runtime.wait_click(BEAST_SOUL_MAIN_SCENE, "合成魂晶")
    yield from runtime.wait_scene(
        BEAST_SOUL_SYNTHESIS_SCENE,
        timeout=8,
        label="兽魂更新：等待魂晶合成",
    )
    yield from runtime.wait_click(BEAST_SOUL_SYNTHESIS_SCENE, "快捷合成页签")
    yield from runtime.wait_scene(
        BEAST_SOUL_QUICK_SYNTHESIS_SCENE,
        timeout=8,
        label="兽魂更新：等待快捷合成",
    )


def _leave_quick_synthesis(runtime: Any):
    exit_views = [
        BEAST_SOUL_QUICK_SYNTHESIS_SCENE,
        BEAST_SOUL_MATERIAL_DROPDOWN_SCENE,
        BEAST_SOUL_POST_SYNTHESIS_CONTINUE_SCENE,
        BEAST_SOUL_MAIN_SCENE,
    ]
    scene_id, _score, _frame = runtime.current_scene(
        views=exit_views,
        update=True,
    )
    if scene_id == BEAST_SOUL_QUICK_SYNTHESIS_SCENE:
        # The result/reward overlay is asynchronous: bb4f first observed #481
        # after a valid delta, then the formal #346 appeared before the close
        # click.  Require one delayed second observation before treating #481
        # as the direct-return branch.
        yield from _settle(runtime, 1.0)
        scene_id, _score, _frame = runtime.current_scene(
            views=exit_views,
            update=True,
        )
    if scene_id == BEAST_SOUL_POST_SYNTHESIS_CONTINUE_SCENE:
        yield from runtime.wait_click(
            BEAST_SOUL_POST_SYNTHESIS_CONTINUE_SCENE,
            "继续",
        )
        yield from runtime.wait_scene(
            BEAST_SOUL_QUICK_SYNTHESIS_SCENE,
            BEAST_SOUL_MATERIAL_DROPDOWN_SCENE,
            BEAST_SOUL_MAIN_SCENE,
            timeout=10,
            label="兽魂更新：关闭合成结果",
        )
        scene_id, _score, _frame = runtime.current_scene(
            views=[
                BEAST_SOUL_QUICK_SYNTHESIS_SCENE,
                BEAST_SOUL_MATERIAL_DROPDOWN_SCENE,
                BEAST_SOUL_MAIN_SCENE,
            ],
            update=True,
        )
    if scene_id == BEAST_SOUL_MATERIAL_DROPDOWN_SCENE:
        yield from runtime.wait_click(
            BEAST_SOUL_MATERIAL_DROPDOWN_SCENE,
            "收起材料",
        )
        yield from runtime.wait_scene(
            BEAST_SOUL_QUICK_SYNTHESIS_SCENE,
            timeout=8,
            label="兽魂更新：收起材料列表",
        )
        scene_id = BEAST_SOUL_QUICK_SYNTHESIS_SCENE
    if scene_id == BEAST_SOUL_MAIN_SCENE:
        return
    if scene_id != BEAST_SOUL_QUICK_SYNTHESIS_SCENE:
        raise RuntimeError(
            f"兽魂更新：快捷合成退场前落在未支持场景 #{scene_id}"
        )
    yield from runtime.wait_click(
        BEAST_SOUL_QUICK_SYNTHESIS_SCENE,
        "点击空白处关闭",
    )
    yield from runtime.wait_scene(
        BEAST_SOUL_MAIN_SCENE,
        timeout=8,
        label="兽魂更新：关闭快捷合成",
    )


def _select_material(runtime: Any, level: int):
    label = LEVEL_LABELS[level]
    policy = quick_synthesis_policy(level)
    batch_size = policy.batch_size
    target = f"消耗所有{label}魂晶"
    yield from runtime.wait_click(BEAST_SOUL_QUICK_SYNTHESIS_SCENE, "材料下拉")
    yield from runtime.wait_scene(
        BEAST_SOUL_MATERIAL_DROPDOWN_SCENE,
        timeout=8,
        label="兽魂更新：等待材料下拉",
    )
    for _ in range(12):
        frame = runtime.cur_frame(update=True)
        items = runtime.find_floating_items_by_anchor_text(
            BEAST_SOUL_MATERIAL_DROPDOWN_SCENE,
            "材料选项模板",
            "材料等级",
            target,
            container_shape="材料选项列表",
            frame_data_url=frame,
            match_mode="exact",
        )
        if not items:
            items = runtime.find_floating_items_by_anchor_text(
                BEAST_SOUL_MATERIAL_DROPDOWN_SCENE,
                "材料选项模板",
                "材料等级",
                target,
                container_shape="材料选项列表",
                frame_data_url=frame,
                match_mode="exact",
                crop=True,
            )
        items = [
            item
            for item in items
            if runtime.floating_item_field_is_fully_inside(
                item,
                "材料等级",
                "材料选项列表",
            )
        ]
        if items:
            runtime.click_floating_item_field(items[0], "材料等级")
            break
        changed = yield from runtime.scroll_shape_content(
            BEAST_SOUL_MATERIAL_DROPDOWN_SCENE,
            "材料选项列表",
            direction="down",
            # A single visual-signature miss was observed at 19:16:48 even
            # though the next star material became visible.  Require two
            # consecutive unchanged drags; the loop always performs a fresh
            # bounded OCR read before asking for the next drag.
            unchanged_confirmations=2,
        )
        if not changed:
            raise RuntimeError(f"兽魂更新：材料列表中找不到「{target}」")
    else:
        raise RuntimeError(f"兽魂更新：材料列表滚动超过上限，仍找不到「{target}」")
    yield from runtime.wait_scene(
        BEAST_SOUL_QUICK_SYNTHESIS_SCENE,
        timeout=8,
        label="兽魂更新：材料选择完成",
    )
    text = runtime.ocr_text_in_shapes(
        BEAST_SOUL_QUICK_SYNTHESIS_SCENE,
        ("合成目标", "材料下拉"),
        padding=12,
    )
    text = re.sub(r"\s+", "", str(text or ""))
    next_label = LEVEL_LABELS[level + 1]
    expected = (
        f"随机{next_label}魂晶",
        f"消耗所有{label}魂晶",
    )
    if not all(part in text for part in expected):
        raise RuntimeError(
            "兽魂更新：快捷合成参数校验失败：" + " / ".join(expected)
        )
    if batch_size == 2:
        # Preserve the already-real-verified low-level image contract.
        if not runtime.match_shape(
            runtime.shape(BEAST_SOUL_QUICK_SYNTHESIS_SCENE, "每次消耗2")
        ):
            raise RuntimeError("兽魂更新：快捷合成每次消耗数值不是已验证资产 2")
    else:
        # BeastSpiritBatchStrengthPanel.UpdateLevelId clears _selectCountId;
        # UpdateDropdownList rebuilds the count list from
        # StrengthenLimit[minSlot+1..maxSlot+1] and selects its first item.
        # Level 4+ is the real-verified fixed 3/100% branch, so selecting its
        # material is sufficient to set 3.  Read the existing formal bounded
        # field and never click an unannotated count-dropdown coordinate.
        _require_high_level_quick_synthesis_state(level, policy)


def _capture_synthesis_evidence(
    runtime: Any,
    frame_data_url: str,
    *,
    scene_id: int | None,
    score: float,
    label: str = "beast_quick_synthesis_result",
) -> str:
    try:
        evidence = build_unknown_evidence(
            runtime.runner,
            runtime.ctx,
            frame_data_url,
            label=label,
            expected_scene_ids=[],
            last_scene_id=scene_id,
            last_score=score,
        )
        return str(evidence.report_path or evidence.frame_path or "")
    except Exception as exc:
        return f"evidence_capture_failed:{exc}"


def _verify_synthesis_snapshot_delta(
    before: dict[str, Any],
    after: dict[str, Any],
    *,
    policy: QuickSynthesisPolicy,
) -> dict[str, Any]:
    before_items = {
        str(item.get("item_id")): item for item in before.get("items") or []
    }
    after_items = {
        str(item.get("item_id")): item for item in after.get("items") or []
    }
    protected_ids = {
        str(item_id)
        for item_id in (before.get("layout") or {}).get("protected_item_ids") or []
    }
    damaged_protected = [
        item_id
        for item_id in sorted(protected_ids, key=int)
        if item_id not in after_items
        or not bool(after_items[item_id].get("locked"))
        or not bool(after_items[item_id].get("excluded_from_quick_synthesis"))
    ]
    if damaged_protected:
        raise RuntimeError(
            f"兽魂更新：快捷合成后保护魂晶异常：{damaged_protected}"
        )

    consumable_before = {
        item_id
        for item_id, item in before_items.items()
        if int(item.get("level") or 0) == policy.level
        and not item.get("equipped")
        and not item.get("locked")
        and not item.get("excluded_from_quick_synthesis")
    }
    removed_ids = set(before_items) - set(after_items)
    unexpected_removed = removed_ids - consumable_before
    if unexpected_removed:
        raise RuntimeError(
            f"兽魂更新：快捷合成移除了非材料实例：{sorted(unexpected_removed, key=int)}"
        )
    expected_cost_count = (
        len(consumable_before) // policy.batch_size * policy.batch_size
    )
    if len(removed_ids) != expected_cost_count:
        raise RuntimeError(
            "兽魂更新：快捷合成材料扣除数量异常："
            f"expected={expected_cost_count}, actual={len(removed_ids)}"
        )

    created_ids = set(after_items) - set(before_items)
    invalid_created = [
        item_id
        for item_id in created_ids
        if int(after_items[item_id].get("level") or 0) != policy.level + 1
    ]
    attempts = expected_cost_count // policy.batch_size
    if invalid_created or len(created_ids) > attempts:
        raise RuntimeError(
            "兽魂更新：快捷合成产物无法按标准成功/失败模型解释："
            f"invalid={sorted(invalid_created)}, created={len(created_ids)}, attempts={attempts}"
        )
    success = len(created_ids)
    return {
        "ok": True,
        "batch_size": policy.batch_size,
        "success_probability": policy.success_probability,
        "cost_count": expected_cost_count,
        "success": success,
        "failure": attempts - success,
        "created_item_ids": sorted(created_ids, key=int),
    }


def _synthesis_item_signature(snapshot: dict[str, Any]) -> tuple[tuple[Any, ...], ...]:
    """Return the material state relevant to safe quick-synthesis replay."""

    return tuple(sorted((
        str(item.get("item_id") or ""),
        int(item.get("level") or 0),
        bool(item.get("equipped")),
        bool(item.get("locked")),
        bool(item.get("excluded_from_quick_synthesis")),
    ) for item in snapshot.get("items") or []))


def _execute_current_batch(
    runtime: Any,
    before_snapshot: dict[str, Any],
    level: int,
):
    policy = quick_synthesis_policy(level)
    yield from runtime.wait_click(
        BEAST_SOUL_QUICK_SYNTHESIS_SCENE,
        "执行快捷合成",
    )
    yield from _settle(runtime, 0.8)
    scene_id, score, frame = runtime.observe_scene(
        views=[
            BEAST_SOUL_LOW_SUCCESS_CONFIRMATION_SCENE,
            BEAST_SOUL_PRECIOUS_MATERIAL_CONFIRMATION_SCENE,
            BEAST_SOUL_QUICK_SYNTHESIS_SCENE,
        ],
        update=True,
    )
    if policy.requires_precious_material_confirmation and scene_id == BEAST_SOUL_QUICK_SYNTHESIS_SCENE:
        # The precious-material alert is delayed: production evidence showed
        # transient #481 at 19:23:48 and #529 only at 19:23:52.  Observe only;
        # never treat the transient page as a completed synthesis or click a
        # generic popup/background while waiting.
        for _ in range(10):
            yield from _settle(runtime, 0.5)
            scene_id, score, frame = runtime.observe_scene(
                views=[
                    BEAST_SOUL_LOW_SUCCESS_CONFIRMATION_SCENE,
                    BEAST_SOUL_PRECIOUS_MATERIAL_CONFIRMATION_SCENE,
                    BEAST_SOUL_QUICK_SYNTHESIS_SCENE,
                ],
                update=True,
            )
            if scene_id != BEAST_SOUL_QUICK_SYNTHESIS_SCENE:
                break
    if policy.auto_confirm_low_success:
        if scene_id == BEAST_SOUL_QUICK_SYNTHESIS_SCENE:
            # The confirmation can be delayed, and a click can also be dropped
            # while the result toast is animating.  Observe first.  A single
            # formal replay is allowed only when the authoritative item state
            # proves the first click produced no synthesis side effect.
            for _ in range(10):
                yield from _settle(runtime, 0.5)
                scene_id, score, frame = runtime.observe_scene(
                    views=[
                        BEAST_SOUL_LOW_SUCCESS_CONFIRMATION_SCENE,
                        BEAST_SOUL_PRECIOUS_MATERIAL_CONFIRMATION_SCENE,
                        BEAST_SOUL_QUICK_SYNTHESIS_SCENE,
                    ],
                    update=True,
                )
                if scene_id != BEAST_SOUL_QUICK_SYNTHESIS_SCENE:
                    break
            if scene_id == BEAST_SOUL_QUICK_SYNTHESIS_SCENE:
                unchanged = _snapshot()
                if _synthesis_item_signature(unchanged) != _synthesis_item_signature(before_snapshot):
                    evidence = _capture_synthesis_evidence(
                        runtime,
                        frame,
                        scene_id=scene_id,
                        score=score,
                        label="beast_quick_synthesis_replay_guard",
                    )
                    raise RuntimeError(
                        "兽魂更新：首次快捷合成后物品状态已变化，拒绝重放；"
                        f"evidence={evidence}"
                    )
                yield from runtime.wait_click(
                    BEAST_SOUL_QUICK_SYNTHESIS_SCENE,
                    "执行快捷合成",
                )
                yield from _settle(runtime, 1.0)
                for _ in range(10):
                    scene_id, score, frame = runtime.observe_scene(
                        views=[
                            BEAST_SOUL_LOW_SUCCESS_CONFIRMATION_SCENE,
                            BEAST_SOUL_PRECIOUS_MATERIAL_CONFIRMATION_SCENE,
                            BEAST_SOUL_QUICK_SYNTHESIS_SCENE,
                        ],
                        update=True,
                    )
                    if scene_id != BEAST_SOUL_QUICK_SYNTHESIS_SCENE:
                        break
                    yield from _settle(runtime, 0.5)
        if scene_id != BEAST_SOUL_LOW_SUCCESS_CONFIRMATION_SCENE:
            evidence = _capture_synthesis_evidence(
                runtime,
                frame,
                scene_id=scene_id,
                score=score,
            )
            raise RuntimeError(
                "兽魂更新：未识别到正式低成功率确认场景，拒绝确认："
                f"scene={scene_id}, evidence={evidence}"
            )
        identity_matches = {
            title: runtime.match_shape(
                runtime.shape(BEAST_SOUL_LOW_SUCCESS_CONFIRMATION_SCENE, title)
            )
            for title in BEAST_SOUL_LOW_SUCCESS_REQUIRED_SHAPES
        }
        if not all(identity_matches.values()):
            evidence = _capture_synthesis_evidence(
                runtime,
                frame,
                scene_id=scene_id,
                score=score,
                label="beast_quick_synthesis_low_success_confirmation",
            )
            raise RuntimeError(
                "兽魂更新：#527 三项 required 身份未在同帧全部命中，"
                f"matches={identity_matches}, evidence={evidence}"
            )
        # resolve_shape_selector enforces the formal action is unique.  The
        # same observer frame remains cached and is passed into the click; no
        # #47 background, checkbox, OCR coordinate or guessed point is used.
        confirm_shape = runtime.shape(
            BEAST_SOUL_LOW_SUCCESS_CONFIRMATION_SCENE,
            "确认",
        )
        runtime.click_shape(
            BEAST_SOUL_LOW_SUCCESS_CONFIRMATION_SCENE,
            confirm_shape,
            frame_data_url=frame,
        )
        yield from _settle(runtime, 1.0)
    elif (
        policy.requires_precious_material_confirmation
        and scene_id == BEAST_SOUL_PRECIOUS_MATERIAL_CONFIRMATION_SCENE
    ):
        identity_matches = {
            title: runtime.match_shape(
                runtime.shape(
                    BEAST_SOUL_PRECIOUS_MATERIAL_CONFIRMATION_SCENE,
                    title,
                )
            )
            for title in BEAST_SOUL_PRECIOUS_MATERIAL_REQUIRED_SHAPES
        }
        if not all(identity_matches.values()):
            evidence = _capture_synthesis_evidence(
                runtime,
                frame,
                scene_id=scene_id,
                score=score,
                label="beast_quick_synthesis_precious_material_confirmation",
            )
            raise RuntimeError(
                "兽魂更新：#529 三项 required 身份未在同帧全部命中，"
                f"matches={identity_matches}, evidence={evidence}"
            )
        confirm_shape = runtime.shape(
            BEAST_SOUL_PRECIOUS_MATERIAL_CONFIRMATION_SCENE,
            "确认",
        )
        runtime.click_shape(
            BEAST_SOUL_PRECIOUS_MATERIAL_CONFIRMATION_SCENE,
            confirm_shape,
            frame_data_url=frame,
        )
        yield from _settle(runtime, 1.0)
    elif (
        policy.requires_precious_material_confirmation
        and scene_id != BEAST_SOUL_QUICK_SYNTHESIS_SCENE
    ):
        evidence = _capture_synthesis_evidence(
            runtime,
            frame,
            scene_id=scene_id,
            score=score,
            label="beast_quick_synthesis_precious_material_confirmation",
        )
        raise RuntimeError(
            "兽魂更新：预期珍稀材料确认但有界纯观察期内未命中 #529，"
            f"scene={scene_id}, evidence={evidence}"
        )
    elif scene_id in (
        47,
        BEAST_SOUL_LOW_SUCCESS_CONFIRMATION_SCENE,
        BEAST_SOUL_PRECIOUS_MATERIAL_CONFIRMATION_SCENE,
    ):
        evidence = _capture_synthesis_evidence(
            runtime,
            frame,
            scene_id=scene_id,
            score=score,
        )
        raise RuntimeError(
            f"兽魂更新：100%策略意外出现确认弹窗，evidence={evidence}"
        )

    result_scene, result_score, result_frame = runtime.observe_scene(update=True)
    if result_scene in (
        BEAST_SOUL_LOW_SUCCESS_CONFIRMATION_SCENE,
        BEAST_SOUL_PRECIOUS_MATERIAL_CONFIRMATION_SCENE,
    ):
        for _ in range(10):
            yield from _settle(runtime, 0.5)
            result_scene, result_score, result_frame = runtime.observe_scene(update=True)
            if result_scene not in (
                BEAST_SOUL_LOW_SUCCESS_CONFIRMATION_SCENE,
                BEAST_SOUL_PRECIOUS_MATERIAL_CONFIRMATION_SCENE,
            ):
                break
    if result_scene != BEAST_SOUL_QUICK_SYNTHESIS_SCENE:
        evidence = _capture_synthesis_evidence(
            runtime,
            result_frame,
            scene_id=result_scene,
            score=result_score,
        )
        raise RuntimeError(
            "兽魂更新：快捷合成确认后未返回正式快捷合成页，"
            f"observed_scene={result_scene}, evidence={evidence}"
        )
    after_snapshot = _snapshot()
    try:
        result = _verify_synthesis_snapshot_delta(
            before_snapshot,
            after_snapshot,
            policy=policy,
        )
    except RuntimeError as exc:
        evidence = _capture_synthesis_evidence(
            runtime,
            result_frame,
            scene_id=result_scene,
            score=result_score,
        )
        raise RuntimeError(f"{exc}；evidence={evidence}") from exc
    return result


def _snapshot() -> dict[str, Any]:
    snapshot = fanxiu_instrumentation_service.beast_spirit_snapshot(optimize=True)
    if not snapshot.get("complete"):
        raise RuntimeError("兽魂更新：只读兽魂快照不完整，拒绝操作")
    return snapshot


def _item(snapshot: dict[str, Any], item_id: str) -> dict[str, Any]:
    match = next(
        (
            item
            for item in snapshot.get("items") or []
            if str(item.get("item_id")) == str(item_id)
        ),
        None,
    )
    if match is None:
        raise RuntimeError(f"兽魂更新：快照中找不到魂晶 {item_id}")
    return match


def _verify_ui_bag_order(expected_item_ids: list[str | None]) -> None:
    projection = fanxiu_instrumentation_service.beast_spirit_ui_order(
        expected_item_ids=expected_item_ids
    )
    if projection.get("complete") is not True:
        raise RuntimeError(
            "兽魂更新：v_showList 窄复核不可用："
            f"{projection.get('reason') or 'unknown'}"
        )
    if list(projection.get("ui_bag_item_ids") or []) != expected_item_ids:
        raise RuntimeError("兽魂更新：定位期间 v_showList 顺序发生变化")


def _bag_locate_budget_seconds(snapshot: dict[str, Any]) -> float:
    row_count = max(1, (len(snapshot.get("ui_bag_item_ids") or []) + 4) // 5)
    # One default scroll plus one normally-unique anchor probe per row.  Keep a
    # 110s floor for small bags and a hard margin below the 10-minute Cell cap.
    return min(540.0, max(110.0, 45.0 + row_count * 18.0))


def _coarse_scroll_batch_size(
    viewport_start: int,
    target_index: int,
    *,
    remaining_scrolls: int,
) -> int:
    """Plan a 75%-distance batch without assuming exact gesture movement.

    The batch count uses the conservative per-gesture upper planning bound,
    not the observed five-slot average.  The caller still re-anchors from UI
    details after the batch and rejects directional non-progress or overshoot.
    """

    available = max(0, int(remaining_scrolls))
    if available <= 0:
        return 0
    distance = abs(int(target_index) - int(viewport_start))
    if distance <= _FINE_REGISTRATION_DISTANCE_SLOTS:
        return 1
    coarse = (
        distance
        * _COARSE_SCROLL_NUMERATOR
        // (
            _COARSE_SCROLL_DENOMINATOR
            * _COARSE_SCROLL_MAX_ADVANCE_SLOTS
        )
    )
    return max(1, min(coarse, available))


def _shape_center(runtime: Any, scene: int, selector: str) -> tuple[float, float]:
    box = runtime.shape(scene, selector).box()
    return (
        float(box.get("x") or 0) + float(box.get("w") or 0) / 2,
        float(box.get("y") or 0) + float(box.get("h") or 0) / 2,
    )


def _click_anchored_point(
    runtime: Any,
    scene: int,
    container_selector: str,
    x: float,
    y: float,
) -> None:
    """Click a dynamic grid point through its annotated container shape."""

    box = runtime.shape(scene, container_selector).box()
    width = float(box.get("w") or 0)
    height = float(box.get("h") or 0)
    if width <= 0 or height <= 0:
        raise RuntimeError(f"兽魂更新：资产容器 {container_selector} 尺寸无效")
    x_ratio = (float(x) - float(box.get("x") or 0)) / width
    y_ratio = (float(y) - float(box.get("y") or 0)) / height
    if not 0 <= x_ratio <= 1 or not 0 <= y_ratio <= 1:
        raise RuntimeError(
            f"兽魂更新：目标点不在资产容器 {container_selector} 内："
            f"x_ratio={x_ratio:.3f}, y_ratio={y_ratio:.3f}"
        )
    runtime.click_shape_center(
        scene,
        container_selector,
        x_ratio=x_ratio,
        y_ratio=y_ratio,
    )


def _board_point(runtime: Any, cells: list[list[int]]) -> tuple[float, float]:
    if not cells:
        raise RuntimeError("兽魂更新：已镶嵌魂晶没有占位格")
    row, column = map(int, sorted(cells)[0])
    if not 1 <= row <= 5 or not 1 <= column <= 6:
        raise RuntimeError(f"兽魂更新：棋盘格越界 row={row}, column={column}")
    left_x, bottom_y = _shape_center(
        runtime,
        BEAST_SOUL_MAIN_SCENE,
        "魂晶镶嵌盘/左下格",
    )
    right_x, top_y = _shape_center(
        runtime,
        BEAST_SOUL_MAIN_SCENE,
        "魂晶镶嵌盘/右上格",
    )
    return (
        left_x + (column - 1) * (right_x - left_x) / 5,
        bottom_y + (row - 1) * (top_y - bottom_y) / 4,
    )


def _bag_card_position(runtime: Any, bag_index: int) -> tuple[float, float, int]:
    """Resolve one bag card from annotated grid anchors and a verified page."""

    index = int(bag_index)
    if index < 0:
        raise ValueError("背包序号不能为负数")
    row, column = divmod(index, 5)
    if row > 1:
        raise RuntimeError(
            f"兽魂更新：背包第 {row + 1} 行尚无真实资产页验证，拒绝猜测坐标"
        )
    first_x, first_y = _shape_center(
        runtime,
        BEAST_SOUL_MAIN_SCENE,
        "魂晶背包/魂晶卡片模板",
    )
    last_x, _last_y = _shape_center(
        runtime,
        BEAST_SOUL_MAIN_SCENE,
        "魂晶背包/首行第五格",
    )
    _second_x, second_y = _shape_center(
        runtime,
        BEAST_SOUL_MAIN_SCENE,
        "魂晶背包/第二行第一格",
    )
    return (
        first_x + column * (last_x - first_x) / 4,
        first_y + row * (second_y - first_y),
        0,
    )


def _initial_bag_card_probe_points(
    runtime: Any,
    *,
    row: int,
    column: int,
) -> tuple[tuple[float, float], ...]:
    """Return bounded click probes for one visible bag card.

    The first/fifth cells fix all five column centres; the first cells of both
    rows fix the row offset.  Within the selected card, the two vertical
    samples are separated by exactly one quarter of its height, so a row gap
    narrower than ``h / 4`` cannot contain both points.  A partially visible
    third row is allowed only at its upper-quarter point and only when that
    point is strictly inside the annotated bag container.  It is never used
    as a viewport anchor; it only permits an already anchored target slot to
    be verified at the lower scroll boundary.
    """

    row_index = int(row)
    column_index = int(column)
    if not 0 <= row_index <= 2 or not 0 <= column_index <= 4:
        raise ValueError(
            f"初始魂晶格越界 row={row_index}, column={column_index}"
        )
    first_box = runtime.shape(
        BEAST_SOUL_MAIN_SCENE,
        "魂晶背包/魂晶卡片模板",
    ).box()
    fifth_box = runtime.shape(
        BEAST_SOUL_MAIN_SCENE,
        "魂晶背包/首行第五格",
    ).box()
    second_row_box = runtime.shape(
        BEAST_SOUL_MAIN_SCENE,
        "魂晶背包/第二行第一格",
    ).box()
    x = float(first_box.get("x") or 0)
    y = float(first_box.get("y") or 0)
    width = float(first_box.get("w") or 0)
    height = float(first_box.get("h") or 0)
    if width <= 0 or height <= 0:
        raise RuntimeError("兽魂更新：魂晶卡片模板尺寸无效")
    first_center_x = x + width / 2
    fifth_center_x = (
        float(fifth_box.get("x") or 0)
        + float(fifth_box.get("w") or 0) / 2
    )
    row_y = y + row_index * (float(second_row_box.get("y") or 0) - y)
    center_x = first_center_x + column_index * (
        fifth_center_x - first_center_x
    ) / 4
    points = (
        (center_x, row_y + height / 4),
        (center_x, row_y + height / 2),
    )
    if row_index < 2:
        return points

    bag_box = runtime.shape(BEAST_SOUL_MAIN_SCENE, "魂晶背包").box()
    bag_x = float(bag_box.get("x") or 0)
    bag_y = float(bag_box.get("y") or 0)
    bag_width = float(bag_box.get("w") or 0)
    bag_height = float(bag_box.get("h") or 0)
    quarter_point = points[0]
    if (
        bag_width <= 0
        or bag_height <= 0
        or not bag_x < quarter_point[0] < bag_x + bag_width
        or not bag_y < quarter_point[1] < bag_y + bag_height
    ):
        raise RuntimeError(
            "兽魂更新：部分可见第三行的 h/4 探测点不在魂晶背包容器内，"
            f"拒绝点击 row={row_index}, column={column_index}"
        )
    return (quarter_point,)


def _first_bag_card_probe_points(runtime: Any) -> tuple[tuple[float, float], ...]:
    return _initial_bag_card_probe_points(runtime, row=0, column=0)


def _open_initial_bag_card(
    runtime: Any,
    *,
    row: int,
    column: int,
    timeout: float = 3.0,
):
    """Open one visible bag card with one or two bounded probes."""

    points = _initial_bag_card_probe_points(runtime, row=row, column=column)
    attempt_count = len(points)
    for attempt, (x, y) in enumerate(points, 1):
        _click_anchored_point(
            runtime,
            BEAST_SOUL_MAIN_SCENE,
            "魂晶背包",
            x,
            y,
        )
        try:
            yield from runtime.wait_scene(
                BEAST_SOUL_DETAIL_SCENE,
                timeout=timeout,
                label=(
                    f"兽魂更新：打开初始魂晶 row={row}, column={column}"
                    f"（探测{attempt}/{attempt_count}）"
                ),
            )
            return {"attempt": attempt, "point": (x, y)}
        except (RuntimeError, TimeoutError):
            scene, _score, _frame = runtime.observe_scene(
                views=[BEAST_SOUL_MAIN_SCENE, BEAST_SOUL_DETAIL_SCENE],
                update=True,
            )
            if scene == BEAST_SOUL_DETAIL_SCENE:
                return {"attempt": attempt, "point": (x, y)}
            if scene != BEAST_SOUL_MAIN_SCENE:
                raise
    raise RuntimeError(
        f"兽魂更新：{attempt_count}个卡片探测点均未打开魂晶 "
        f"row={row}, column={column}"
    )


def _open_initial_first_bag_card(runtime: Any, *, timeout: float = 3.0):
    """Open the initial first bag card with at most two bounded probes."""

    return (
        yield from _open_initial_bag_card(
            runtime,
            row=0,
            column=0,
            timeout=timeout,
        )
    )


def _detail_identity(
    runtime: Any,
) -> tuple[tuple[int, int | None, int | None, int | None], str]:
    """Read the open detail's side-effect-free identity fingerprint."""

    text = runtime.ocr_text_in_shapes(
        BEAST_SOUL_DETAIL_SCENE,
        ("魂晶等级标题", "总评分"),
        padding=8,
    )
    text = re.sub(r"\s+", "", str(text or ""))
    level = next(
        (
            level
            for level, label in sorted(
                LEVEL_LABELS.items(),
                key=lambda pair: len(pair[1]),
                reverse=True,
            )
            if label in text
        ),
        0,
    )
    score_match = re.search(r"总评分[:：]?([0-9,]+)", text)
    score = int(score_match.group(1).replace(",", "")) if score_match else None
    basic_text = runtime.ocr_text_in_shapes(
        BEAST_SOUL_DETAIL_SCENE,
        ("基础属性",),
        padding=96,
    )
    basic_text = re.sub(r"\s+", "", str(basic_text or ""))
    soul_match = re.search(r"魂元[:：]?([0-9,]+)", basic_text)
    blood_match = re.search(r"气血加成[:：]?([0-9]+(?:\.[0-9]+)?)%", basic_text)
    soul = int(soul_match.group(1).replace(",", "")) if soul_match else None
    blood = (
        round(float(blood_match.group(1)) * 100)
        if blood_match
        else None
    )
    return (level, score, soul, blood), text + basic_text


def _item_visual_signature(
    item: dict[str, Any],
) -> tuple[int, int | None, int | None, int | None]:
    values = {
        int(entry.get("attribute_id") or 0): int(entry.get("value") or 0)
        for entry in item.get("main_entries") or []
        if entry.get("attribute_id") is not None and entry.get("value") is not None
    }
    return (
        int(item.get("level") or 0),
        int(item["score"]) if item.get("score") is not None else None,
        values.get(BEAST_SOUL_SOUL_ATTR_ID),
        values.get(BEAST_SOUL_BLOOD_RATE_ATTR_ID),
    )


def _digit_component_score(expected: int | None, observed: int | None):
    """Explainable OCR distance: exact, one substituted digit, or conflict."""

    if expected is None or observed is None:
        return 0, 0, 0
    expected_text, observed_text = str(abs(expected)), str(abs(observed))
    if expected_text == observed_text:
        return 4, 1, 0
    if len(expected_text) == len(observed_text) and sum(
        left != right for left, right in zip(expected_text, observed_text)
    ) == 1:
        return 1, 0, 0
    return -4, 0, 1


def _signature_score(expected, observed):
    score = exact = conflicts = observed_count = 0
    expected_level, *expected_numbers = expected
    observed_level, *observed_numbers = observed
    if observed_level:
        observed_count += 1
        if expected_level == observed_level:
            score += 4
            exact += 1
        elif abs(expected_level - observed_level) == 1:
            score += 1
        else:
            score -= 4
            conflicts += 1
    for expected_value, observed_value in zip(
        expected_numbers, observed_numbers, strict=True
    ):
        if observed_value is not None:
            observed_count += 1
        delta, exact_delta, conflict_delta = _digit_component_score(
            expected_value, observed_value
        )
        score += delta
        exact += exact_delta
        conflicts += conflict_delta
    return score, exact, conflicts, observed_count


def _rank_viewport_candidates(identities, observations):
    """滚动视窗序列配准：局部OCR容错，整段全局唯一后才定位。"""

    ranked = []
    for start in range(0, len(identities), 5):
        total = exact = conflicts = observed_count = 0
        valid = True
        for relative_index, observed in observations:
            if start + relative_index >= len(identities):
                valid = False
                break
            expected = identities[start + relative_index]
            if expected is None:
                valid = False
                break
            values = _signature_score(expected, observed)
            total += values[0]
            exact += values[1]
            conflicts += values[2]
            observed_count += values[3]
        if valid:
            ranked.append((total, exact, -conflicts, observed_count, start))
    ranked.sort(reverse=True)
    if not ranked:
        return None, ranked
    best = ranked[0]
    margin = best[0] - ranked[1][0] if len(ranked) > 1 else best[0]
    if best[0] >= 8 and best[1] >= 2 and margin >= _SIGNATURE_SAFE_MARGIN:
        return best[4], ranked
    return None, ranked


def _signature_accepts(expected, observed) -> bool:
    score, exact, conflicts, _observed = _signature_score(expected, observed)
    return score >= 8 and exact >= 2 and conflicts <= 1


def _target_signature_accepts(expected, observed) -> bool:
    """Final action carrier requires both newly-proven basic attributes."""

    if any(value is None for value in (*expected[2:4], *observed[2:4])):
        return False
    score, exact, conflicts, _observed = _signature_score(expected, observed)
    return score >= 8 and exact >= 2 and conflicts == 0


def _beast_soul_main_identity(
    runtime: Any,
    *,
    frame_data_url: str | None = None,
) -> bool:
    """Confirm #478 from three bounded formal anchors after an empty-card tap.

    A blank bag slot legitimately leaves the UI on #478.  The generic scene
    matcher can miss its two tightly bounded identity labels even though the
    larger formal action labels remain readable, so this check is used only
    to distinguish that no-op from an unknown transition.
    """

    # Read each bounded Shape independently.  A single union box spanning the
    # header and both bottom actions was observed to return an empty filtered
    # result on the 06:42 stable #478 frame even though all three lines existed
    # in the authoritative full-frame OCR evidence.
    for shape_title, anchor in (
        ("魂胎光", "魂胎光"),
        ("合成魂晶", "合成魂晶"),
        ("词条预览", "词条预览"),
    ):
        text = runtime.ocr_text_in_shapes(
            BEAST_SOUL_MAIN_SCENE,
            (shape_title,),
            padding=12,
            frame_data_url=frame_data_url,
        )
        if anchor not in re.sub(r"\s+", "", str(text or "")):
            return False
    return True


def _expected_identity(snapshot: dict[str, Any], item_id: str):
    item = _item(snapshot, item_id)
    identity = _item_visual_signature(item)
    counts = Counter(
        _item_visual_signature(candidate)
        for candidate in snapshot.get("items") or []
    )
    if counts[identity] != 1:
        raise RuntimeError(
            f"兽魂更新：魂晶 {item_id} 的等级+总评分指纹不唯一，拒绝操作"
        )
    return identity


def _ordered_bag_identity_sequence(
    snapshot: dict[str, Any],
) -> list[tuple[int, int | None, int | None, int | None] | None]:
    """Project the authoritative v_showList order to observable identities."""

    item_by_id = {
        str(item.get("item_id")): item for item in snapshot.get("items") or []
    }
    result: list[tuple[int, int | None] | None] = []
    for raw_item_id in snapshot.get("ui_bag_item_ids") or []:
        if raw_item_id is None:
            result.append(None)
            continue
        item = item_by_id.get(str(raw_item_id))
        if item is None:
            raise RuntimeError(
                f"兽魂更新：v_showList 实例 {raw_item_id} 不在完整库存快照中"
            )
        result.append(_item_visual_signature(item))
    return result


def _probe_visible_bag_identity(
    runtime: Any,
    *,
    row: int,
    column: int,
    require_time_budget,
    verify_order,
):
    """Observe one visible slot and always restore #478 afterwards."""

    require_time_budget()
    try:
        yield from _open_initial_bag_card(
            runtime,
            row=row,
            column=column,
            timeout=3.0,
        )
    except RuntimeError as exc:
        if "卡片探测点均未打开魂晶" not in str(exc):
            raise
        # A no-open may be empty padding or a missed hit.  Visual geometry
        # alone cannot distinguish those cases, so it is never admitted as an
        # authoritative ``None`` signature.
        raise BeastSoulTargetNotFoundError(
            "viewport-anchor",
            f"兽魂更新：可见格 row={row}, column={column} 未打开详情，拒绝当作空格",
        ) from exc
    require_time_budget()
    try:
        identity, _text = _detail_identity(runtime)
        if not identity[0] or identity[1] is None:
            raise RuntimeError(
                f"兽魂更新：可见格 row={row}, column={column} 详情签名不完整"
            )
        return identity
    finally:
        # Every observational detail probe is closed before another slot or
        # scroll is attempted.  No probe leaves the runtime on #479.
        yield from _close_item_detail(runtime)
        verify_order()
        require_time_budget()


def _anchor_visible_bag_start(
    runtime: Any,
    identities: list[tuple[int, int | None] | None],
    *,
    require_time_budget,
    verify_order,
):
    """Locate the current viewport start by a bounded continuous signature."""

    # The verified grid is row-major with five columns.  Its top-left cell is
    # therefore always a complete-row boundary (0, 5, 10, ...), never an
    # arbitrary item offset.
    observations = []
    probe_slots = [(row, column) for row in range(2) for column in range(5)]
    for row, column in probe_slots:
        relative_index = row * 5 + column
        observed = yield from _probe_visible_bag_identity(
            runtime,
            row=row,
            column=column,
            require_time_budget=require_time_budget,
            verify_order=verify_order,
        )
        observations.append((relative_index, observed))
        winner, ranked = _rank_viewport_candidates(identities, observations)
        if winner is not None:
            return winner, observations
    # If one viewport is still ambiguous, extend the registration by one
    # verified row.  Pick only a direction whose new row is complete and can
    # distinguish the current best-score candidates.  The five-cell overlap
    # is observed on both the outbound and restoring scroll; no displacement
    # is inferred from scroll count alone.
    best_score = ranked[0][0] if ranked else 0
    contenders = [
        item[4]
        for item in ranked
        if best_score - item[0] < _SIGNATURE_SAFE_MARGIN
    ]

    def diagnostic_direction():
        for direction in ("up", "down"):
            projected_rows = []
            for start in contenders:
                row_start = start - 5 if direction == "up" else start + 10
                row_end = row_start + 5
                if row_start < 0 or row_end > len(identities):
                    break
                row_values = tuple(identities[row_start:row_end])
                if any(value is None for value in row_values):
                    break
                projected_rows.append(row_values)
            else:
                if len(projected_rows) == len(contenders) and len(set(projected_rows)) > 1:
                    return direction
        return None

    direction = diagnostic_direction()
    if direction is not None:
        changed = yield from runtime.scroll_shape_content(
            BEAST_SOUL_MAIN_SCENE,
            "魂晶背包",
            recognition_shape="魂晶背包/魂晶卡片模板",
            direction=direction,
        )
        require_time_budget()
        if not changed:
            raise BeastSoulTargetNotFoundError(
                "viewport-anchor",
                f"兽魂更新：滚动视窗序列配准向 {direction} 扩展时已到边界",
            )
        verify_order()
        old_by_index = dict(observations)
        overlap_slots = (
            [(1, column, column) for column in range(5)]
            if direction == "up"
            else [(0, column, 5 + column) for column in range(5)]
        )
        extended = []
        for row, column, old_relative in overlap_slots:
            observed = yield from _probe_visible_bag_identity(
                runtime,
                row=row,
                column=column,
                require_time_budget=require_time_budget,
                verify_order=verify_order,
            )
            if not _signature_accepts(old_by_index[old_relative], observed):
                raise BeastSoulTargetNotFoundError(
                    "viewport-anchor",
                    "兽魂更新：滚动后5格重叠序列不一致，拒绝推定位移",
                )
            extended.append((row * 5 + column, observed))
        new_slots = (
            [(0, column) for column in range(5)]
            if direction == "up"
            else [(1, column) for column in range(5)]
        )
        for row, column in new_slots:
            extended.append((
                row * 5 + column,
                (yield from _probe_visible_bag_identity(
                    runtime,
                    row=row,
                    column=column,
                    require_time_budget=require_time_budget,
                    verify_order=verify_order,
                )),
            ))
        winner, extended_ranked = _rank_viewport_candidates(identities, extended)
        if winner is not None:
            restore_direction = "down" if direction == "up" else "up"
            restored = yield from runtime.scroll_shape_content(
                BEAST_SOUL_MAIN_SCENE,
                "魂晶背包",
                recognition_shape="魂晶背包/魂晶卡片模板",
                direction=restore_direction,
            )
            require_time_budget()
            if not restored:
                raise BeastSoulTargetNotFoundError(
                    "viewport-anchor",
                    "兽魂更新：扩展配准后无法恢复原视窗",
                )
            verify_order()
            extended_by_index = dict(extended)
            restore_slots = (
                [(0, column, 5 + column) for column in range(5)]
                if direction == "up"
                else [(1, column, column) for column in range(5)]
            )
            for row, column, extended_relative in restore_slots:
                restored_observed = yield from _probe_visible_bag_identity(
                    runtime,
                    row=row,
                    column=column,
                    require_time_budget=require_time_budget,
                    verify_order=verify_order,
                )
                if not _signature_accepts(
                    extended_by_index[extended_relative], restored_observed
                ):
                    raise BeastSoulTargetNotFoundError(
                        "viewport-anchor",
                        "兽魂更新：恢复视窗后的5格重叠序列不一致",
                    )
            original_start = winner + 5 if direction == "up" else winner - 5
            if original_start not in contenders:
                raise BeastSoulTargetNotFoundError(
                    "viewport-anchor",
                    "兽魂更新：跨视窗唯一结果不属于原候选集合",
                )
            return original_start, observations
        ranked = extended_ranked
    raise BeastSoulTargetNotFoundError(
        "viewport-anchor",
        "兽魂更新：滚动视窗连续10格证据仍未形成全局唯一安全间隔，"
        f"top_scores={[item[0] for item in ranked[:3]]}，拒绝盲点",
    )


def _open_bag_item_detail(
    runtime: Any,
    snapshot: dict[str, Any],
    item_id: str,
    *,
    locate_deadline: float | None = None,
):
    """Anchor the viewport by observed signatures, then open the exact slot."""

    item = _item(snapshot, item_id)
    ui_bag_index = item.get("ui_bag_index")
    if snapshot.get("ui_bag_complete") is not True:
        raise RuntimeError(
            "兽魂更新：活动魂晶列表未通过完整性校验，拒绝推定背包位置："
            f"{snapshot.get('ui_bag_reason') or 'v_showList unavailable'}"
        )
    if ui_bag_index is None or int(ui_bag_index) < 0:
        raise RuntimeError(
            f"兽魂更新：魂晶 {item_id} 缺少权威 UI 位置：{ui_bag_index}"
        )
    expected_identity = _item_visual_signature(item)
    expected_ui_order = list(snapshot.get("ui_bag_item_ids") or [])
    if not expected_ui_order:
        raise RuntimeError("兽魂更新：权威 v_showList 为空，拒绝定位")
    target_index = int(ui_bag_index)
    identities = _ordered_bag_identity_sequence(snapshot)
    budget_seconds = _bag_locate_budget_seconds(snapshot)
    deadline = (
        float(locate_deadline)
        if locate_deadline is not None
        else monotonic_time.monotonic() + budget_seconds
    )

    def require_time_budget() -> None:
        if monotonic_time.monotonic() >= deadline:
            raise BeastSoulTargetNotFoundError(
                item_id,
                f"兽魂更新：权威 UI 定位魂晶 {item_id} 超过"
                f"{budget_seconds:.0f}秒，停止探测",
            )

    max_scrolls = max(2, (len(identities) + 4) // 5 + 2)
    verify_order = lambda: _verify_ui_bag_order(expected_ui_order)
    previous_anchor: int | None = None
    previous_direction: str | None = None
    scroll_count = 0
    while scroll_count <= max_scrolls:
        require_time_budget()
        viewport_start, _observations = yield from _anchor_visible_bag_start(
            runtime,
            identities,
            require_time_budget=require_time_budget,
            verify_order=verify_order,
        )
        if previous_anchor is not None:
            progressed = (
                viewport_start > previous_anchor
                if previous_direction == "down"
                else viewport_start < previous_anchor
            )
            if not progressed:
                raise BeastSoulTargetNotFoundError(
                    item_id,
                    f"兽魂更新：{previous_direction} 滚动后锚点未按方向进展："
                    f"before={previous_anchor}, after={viewport_start}",
                )
            if (
                previous_direction == "down"
                and viewport_start > target_index
            ) or (
                previous_direction == "up"
                and target_index - viewport_start >= 15
            ):
                raise BeastSoulTargetNotFoundError(
                    item_id,
                    "兽魂更新：粗滚重锚发现越过目标，拒绝反向猜测："
                    f"direction={previous_direction}, anchor={viewport_start}, "
                    f"target={target_index}",
                )
        relative_index = target_index - viewport_start
        # The live #478 bag exposes two complete rows plus the upper quarter
        # of a third row.  Registration remains limited to the ten complete
        # cells; only an already registered target may use the bounded third
        # row point returned by ``_initial_bag_card_probe_points``.
        if 0 <= relative_index < 15:
            row, column = divmod(relative_index, 5)
            # Verify this exact anchored slot, close it, then reopen it as the
            # operational detail.  Every observational verification returns
            # to #478; the final open is not a probe and is consumed by the
            # caller's lock action.
            actual_identity = yield from _probe_visible_bag_identity(
                runtime,
                row=row,
                column=column,
                require_time_budget=require_time_budget,
                verify_order=verify_order,
            )
            if not _target_signature_accepts(expected_identity, actual_identity):
                raise BeastSoulTargetNotFoundError(
                    item_id,
                    f"兽魂更新：锚定目标格签名不一致，expected={expected_identity}, "
                    f"actual={actual_identity}",
                )
            yield from _open_initial_bag_card(
                runtime,
                row=row,
                column=column,
                timeout=3.0,
            )
            require_time_budget()
            # This second #479 is the one that will carry the lock action.
            # Revalidate it independently; the preceding observational detail
            # was deliberately closed and cannot authorize a later click.
            carrier_identity, _carrier_text = _detail_identity(runtime)
            if not _target_signature_accepts(expected_identity, carrier_identity):
                yield from _close_item_detail(runtime)
                raise BeastSoulTargetNotFoundError(
                    item_id,
                    f"兽魂更新：承载锁动作的详情签名不一致，"
                    f"expected={expected_identity}, actual={carrier_identity}",
                )
            try:
                verify_order()
                require_time_budget()
            except Exception:
                yield from _close_item_detail(runtime)
                raise
            return item

        direction = "up" if target_index < viewport_start else "down"
        previous_anchor = viewport_start
        previous_direction = direction
        batch_size = _coarse_scroll_batch_size(
            viewport_start,
            target_index,
            remaining_scrolls=max_scrolls - scroll_count,
        )
        if batch_size <= 0:
            break
        changed_count = 0
        for _batch_index in range(batch_size):
            changed = yield from runtime.scroll_shape_content(
                BEAST_SOUL_MAIN_SCENE,
                "魂晶背包",
                recognition_shape="魂晶背包/魂晶卡片模板",
                direction=direction,
            )
            scroll_count += 1
            require_time_budget()
            if not changed:
                break
            changed_count += 1
            scene, _score, _frame = runtime.observe_scene(
                views=[BEAST_SOUL_MAIN_SCENE, BEAST_SOUL_DETAIL_SCENE],
                update=True,
            )
            if scene != BEAST_SOUL_MAIN_SCENE:
                raise BeastSoulTargetNotFoundError(
                    item_id,
                    f"兽魂更新：粗滚期间离开 #478，当前 scene={scene}",
                )
            verify_order()
        if changed_count == 0:
            raise BeastSoulTargetNotFoundError(
                item_id,
                f"兽魂更新：目标 index={target_index} 位于当前锚点 "
                f"{viewport_start} 的 {direction} 方向，但已到滚动边界",
            )
    raise BeastSoulTargetNotFoundError(
        item_id,
        f"兽魂更新：超过 {max_scrolls} 次有界滚动仍未定位目标 {item_id}",
    )


def _open_item_detail(
    runtime: Any,
    snapshot: dict[str, Any],
    item_id: str,
    *,
    locate_deadline: float | None = None,
):
    item = _item(snapshot, item_id)
    if item.get("equipped"):
        placements = {
            str(entry["item_id"]): entry["cells"]
            for entry in (
                snapshot.get("layout", {})
                .get("transition_plan", {})
                .get("takeoff", [])
            )
        }
        cells = placements.get(str(item_id))
        if cells is None:
            from backend.core.fanxiu.beast_spirit_optimizer import board_placements

            cells = board_placements(snapshot.get("boards") or []).get(str(item_id))
        x, y = _board_point(runtime, cells or [])
        _click_anchored_point(
            runtime,
            BEAST_SOUL_MAIN_SCENE,
            "魂晶镶嵌盘",
            x,
            y,
        )
    else:
        return (yield from _open_bag_item_detail(
            runtime,
            snapshot,
            item_id,
            locate_deadline=locate_deadline,
        ))
    yield from runtime.wait_scene(
        BEAST_SOUL_DETAIL_SCENE,
        timeout=8,
        label="兽魂更新：等待魂晶详情",
    )
    actual_identity, _text = _detail_identity(runtime)
    expected_identity = _expected_identity(snapshot, item_id)
    if not _target_signature_accepts(expected_identity, actual_identity):
        raise RuntimeError(
            f"兽魂更新：详情身份校验失败，目标 {item_id} 应为"
            f"{expected_identity}，实际 {actual_identity}"
        )
    return item


def _close_item_detail(runtime: Any):
    yield from runtime.wait_click(BEAST_SOUL_DETAIL_SCENE, "关闭详情")
    yield from runtime.wait_scene(
        BEAST_SOUL_MAIN_SCENE,
        timeout=8,
        label="兽魂更新：关闭魂晶详情",
    )


def _toggle_item_lock(
    runtime: Any,
    snapshot: dict[str, Any],
    item_id: str,
    *,
    expected_locked: bool,
    locate_deadline: float | None = None,
):
    before_lock_state = {
        str(item["item_id"]): bool(item.get("locked"))
        for item in snapshot.get("items") or []
    }
    yield from _open_item_detail(
        runtime,
        snapshot,
        item_id,
        locate_deadline=locate_deadline,
    )
    yield from runtime.wait_click(BEAST_SOUL_DETAIL_SCENE, "锁定切换")
    yield from _settle(runtime)
    updated = _snapshot()
    target = _item(updated, item_id)
    target_ok = (
        bool(target.get("locked")) is expected_locked
        and bool(target.get("excluded_from_quick_synthesis")) is expected_locked
    )
    if target_ok:
        yield from _close_item_detail(runtime)
        return updated

    changed_ids = [
        str(item["item_id"])
        for item in updated.get("items") or []
        if bool(item.get("locked"))
        != before_lock_state.get(str(item.get("item_id")), False)
    ]
    # Reopen the item that actually changed before rolling it back.  This avoids
    # assuming that a misplaced card click left the intended detail on screen.
    if len(changed_ids) == 1 and changed_ids[0] != str(item_id):
        yield from _close_item_detail(runtime)
        yield from _open_item_detail(
            runtime,
            updated,
            changed_ids[0],
            locate_deadline=locate_deadline,
        )
        yield from runtime.wait_click(BEAST_SOUL_DETAIL_SCENE, "锁定切换")
        yield from _settle(runtime)
        rolled_back = _snapshot()
        yield from _close_item_detail(runtime)
        rollback_state = {
            str(item["item_id"]): bool(item.get("locked"))
            for item in rolled_back.get("items") or []
        }
        if rollback_state != before_lock_state:
            raise RuntimeError(
                f"兽魂更新：误点魂晶 {changed_ids[0]} 且回滚锁定状态失败"
            )
    else:
        yield from _close_item_detail(runtime)
    raise RuntimeError(
        f"兽魂更新：魂晶 {item_id} 锁定切换未命中目标，"
        f"expected_locked={expected_locked}, changed={changed_ids}"
    )


def _sync_protected_items(runtime: Any, snapshot: dict[str, Any]):
    actions: list[dict[str, str]] = []
    max_actions = len(snapshot.get("items") or []) + 4
    refresh_used = False
    locate_budget_seconds = _bag_locate_budget_seconds(snapshot)
    locate_deadline = monotonic_time.monotonic() + locate_budget_seconds

    def require_locate_budget(target_id: str) -> None:
        if monotonic_time.monotonic() >= locate_deadline:
            raise BeastSoulTargetNotFoundError(
                target_id,
                f"兽魂更新：同步保护集合定位超过{locate_budget_seconds:.0f}秒，"
                f"停止重试 {target_id}",
            )

    while True:
        layout = snapshot.get("layout") or {}
        missing = [
            str(item_id)
            for item_id in layout.get("unlocked_protected_item_ids") or []
        ]
        obsolete = [
            str(item_id) for item_id in layout.get("obsolete_locked_item_ids") or []
        ]
        if not missing and not obsolete:
            if not bool(layout.get("safe_to_synthesize")):
                raise RuntimeError("兽魂更新：锁定差异为空但合成门卫仍未满足")
            return snapshot, actions
        if len(actions) >= max_actions:
            raise RuntimeError("兽魂更新：同步保护集合超过安全动作上限")
        expected_locked = bool(missing)
        target_id = (missing or obsolete)[0]
        require_locate_budget(target_id)
        try:
            snapshot = yield from _toggle_item_lock(
                runtime,
                snapshot,
                target_id,
                expected_locked=expected_locked,
                locate_deadline=locate_deadline,
            )
        except BeastSoulTargetNotFoundError:
            if refresh_used:
                raise
            require_locate_budget(target_id)
            refreshed = _snapshot()
            require_locate_budget(target_id)
            refresh_used = True
            if refreshed.get("complete") is not True:
                raise RuntimeError("兽魂更新：目标定位失败后的刷新快照不完整")
            refreshed_ids = {
                str(item.get("item_id")) for item in refreshed.get("items") or []
            }
            if target_id in refreshed_ids:
                # UI presentation may have moved while the prior snapshot's
                # bag_index stayed stable. Retry exactly once with the fresh
                # identity and position; a second miss is a real scan defect.
                snapshot = yield from _toggle_item_lock(
                    runtime,
                    refreshed,
                    target_id,
                    expected_locked=expected_locked,
                    locate_deadline=locate_deadline,
                )
                actions.append(
                    {"kind": "lock" if expected_locked else "unlock", "item_id": target_id}
                )
                continue
            refreshed_layout = refreshed.get("layout") or {}
            refreshed_missing = {
                str(item_id)
                for item_id in refreshed_layout.get("unlocked_protected_item_ids") or []
            }
            refreshed_obsolete = {
                str(item_id)
                for item_id in refreshed_layout.get("obsolete_locked_item_ids") or []
            }
            refreshed_pending = refreshed_missing | refreshed_obsolete
            refreshed_safe = bool(refreshed_layout.get("safe_to_synthesize"))
            if target_id in refreshed_pending:
                raise RuntimeError("兽魂更新：消失实例仍被新优化快照列为待处理目标")
            if refreshed_safe == bool(refreshed_pending):
                raise RuntimeError("兽魂更新：刷新快照的保护集合与合成安全门禁不一致")
            snapshot = refreshed
            continue
        actions.append(
            {"kind": "lock" if expected_locked else "unlock", "item_id": target_id}
        )


def _apply_layout(runtime: Any, snapshot: dict[str, Any]):
    actions: list[dict[str, Any]] = []
    plan = (snapshot.get("layout") or {}).get("transition_plan") or {}
    for action in plan.get("takeoff") or []:
        item_id = str(action["item_id"])
        yield from _open_item_detail(runtime, snapshot, item_id)
        detail_action = runtime.ocr_text_in_shapes(
            BEAST_SOUL_DETAIL_SCENE,
            ("镶嵌或卸下",),
            padding=8,
        )
        if "卸下" not in re.sub(r"\s+", "", str(detail_action or "")):
            raise RuntimeError(f"兽魂更新：魂晶 {item_id} 详情未出现卸下按钮")
        yield from runtime.wait_click(BEAST_SOUL_DETAIL_SCENE, "镶嵌或卸下")
        yield from runtime.wait_scene(
            BEAST_SOUL_MAIN_SCENE,
            timeout=8,
            label="兽魂更新：等待卸下完成",
        )
        snapshot = _snapshot()
        if _item(snapshot, item_id).get("equipped"):
            raise RuntimeError(f"兽魂更新：卸下魂晶 {item_id} 后只读状态未更新")
        actions.append({"kind": "takeoff", "item_id": item_id})

    # Replan after takeoffs because native embedding always uses first-fit.
    plan = (snapshot.get("layout") or {}).get("transition_plan") or {}
    for action in plan.get("embed") or []:
        item_id = str(action["item_id"])
        expected_cells = sorted(action.get("cells") or [])
        yield from _open_item_detail(runtime, snapshot, item_id)
        detail_action = runtime.ocr_text_in_shapes(
            BEAST_SOUL_DETAIL_SCENE,
            ("镶嵌或卸下",),
            padding=8,
        )
        if "镶嵌" not in re.sub(r"\s+", "", str(detail_action or "")):
            raise RuntimeError(f"兽魂更新：魂晶 {item_id} 详情未出现镶嵌按钮")
        yield from runtime.wait_click(BEAST_SOUL_DETAIL_SCENE, "镶嵌或卸下")
        yield from runtime.wait_scene(
            BEAST_SOUL_MAIN_SCENE,
            timeout=8,
            label="兽魂更新：等待镶嵌完成",
        )
        snapshot = _snapshot()
        from backend.core.fanxiu.beast_spirit_optimizer import board_placements

        actual_cells = board_placements(snapshot.get("boards") or []).get(item_id)
        if actual_cells != expected_cells:
            raise RuntimeError(
                f"兽魂更新：魂晶 {item_id} 首次适配位置偏离规划："
                f"expected={expected_cells}, actual={actual_cells}"
            )
        actions.append({"kind": "embed", "item_id": item_id, "cells": actual_cells})
    return snapshot, actions


def execute_beast_spirit_update_task(
    runner: Any,
    ctx: dict[str, Any],
    payload: dict[str, Any],
    stop_event: threading.Event,
):
    """Lock protected souls, synthesize disposables, and apply the optimum."""

    runtime = runner._fanxiu_runtime(
        ctx,
        ctx.get("asset_tree_path"),
        stop_event=stop_event,
    )
    max_level = min(8, max(1, int(payload.get("max_source_level") or 8)))
    snapshot = _snapshot()
    yield from _enter_beast_soul_main(runtime)
    # v_showList is created by BeastSpiritSlotGridPanel only after the page is
    # active.  Refresh here so every bag action uses the exact current UI order.
    snapshot = _snapshot()
    snapshot, lock_actions = yield from _sync_protected_items(runtime, snapshot)

    batches: list[dict[str, Any]] = []
    if not any(
        synthesis_gate(snapshot, level)["allowed"]
        for level in range(1, max_level + 1)
    ):
        snapshot, layout_actions = yield from _apply_layout(runtime, snapshot)
        layout = snapshot.get("layout") or {}
        score_gain = int(layout.get("score_gain") or 0)
        if score_gain == 0:
            from backend.core.fanxiu.beast_spirit_default_layout import (
                save_beast_spirit_default_layout,
            )

            save_beast_spirit_default_layout(snapshot)
        return {
            "ok": score_gain == 0,
            "outcome": (
                "complete" if score_gain == 0 else "layout_update_required"
            ),
            "batches": [],
            "lock_actions": lock_actions,
            "layout_actions": layout_actions,
            "inventory_count": snapshot.get("inventory_count"),
            "level_counts": snapshot.get("level_counts"),
            "current_score": layout.get("current_score"),
            "optimal_score": layout.get("score"),
            "score_gain": score_gain,
            "protected_prefix_k": layout.get("protected_prefix_k"),
        }

    for level in range(1, max_level + 1):
        gate = synthesis_gate(snapshot, level)
        if not gate["allowed"]:
            continue
        yield from _enter_quick_synthesis(runtime)
        yield from _select_material(runtime, level)
        result = yield from _execute_current_batch(runtime, snapshot, level)
        yield from _leave_quick_synthesis(runtime)
        batches.append({"level": level, **result})
        snapshot = _snapshot()
        snapshot, new_lock_actions = yield from _sync_protected_items(runtime, snapshot)
        lock_actions.extend(new_lock_actions)
        if not result.get("ok"):
            break

    snapshot, layout_actions = yield from _apply_layout(runtime, snapshot)
    layout = snapshot.get("layout") or {}
    score_gain = int(layout.get("score_gain") or 0)
    outcome = "complete" if score_gain == 0 else "layout_update_required"
    if score_gain == 0:
        from backend.core.fanxiu.beast_spirit_default_layout import (
            save_beast_spirit_default_layout,
        )

        save_beast_spirit_default_layout(snapshot)
    return {
        "ok": score_gain == 0,
        "outcome": outcome,
        "batches": batches,
        "lock_actions": lock_actions,
        "layout_actions": layout_actions,
        "inventory_count": snapshot.get("inventory_count"),
        "level_counts": snapshot.get("level_counts"),
        "current_score": layout.get("current_score"),
        "optimal_score": layout.get("score"),
        "score_gain": score_gain,
        "protected_prefix_k": layout.get("protected_prefix_k"),
    }


__all__ = [
    "_bag_card_position",
    "LEVEL_LABELS",
    "QuickSynthesisPolicy",
    "STANDARD_JOB_ID",
    "consumable_count",
    "execute_beast_spirit_update_task",
    "next_beast_spirit_update_at",
    "quick_synthesis_policy",
    "synthesis_batch_size",
    "synthesis_gate",
]
