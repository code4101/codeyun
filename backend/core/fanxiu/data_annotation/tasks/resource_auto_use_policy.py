from __future__ import annotations

"""Pure safety policy for the ``资源_自动使用`` aggregate job.

The game-side buttons are broad batch operations.  This module deliberately
does not click them and does not infer eligibility from a red dot or OCR.  A
reader must first project the exact native candidate set and the resources it
will consume; this policy then decides whether the batch is inside the user's
explicit authorization boundary.
"""

from collections import Counter
from dataclasses import dataclass
from typing import Any, Literal


DecisionAction = Literal["execute", "complete", "fail"]

EXPECTED_STORAGE_SETTINGS = {
    "1": 0,  # open boxes: forbidden
    "2": 1,  # dismantle
    "3": 1,  # merge
    "4": 1,  # use
}
STORAGE_TERMINAL_TOAST = "暂无可快捷操作的选项"

_AUTHORIZED_PET_RESOURCE_KINDS = frozenset({"ordinary_pet_upgrade_item"})
_AUTHORIZED_TALISMAN_CATEGORIES = frozenset(
    {"法宝", "先天古宝", "后天古宝"}
)
_AUTHORIZED_TALISMAN_RESOURCE_KINDS = frozenset(
    {"talisman_upgrade_material"}
)
_FORBIDDEN_RESOURCE_KINDS = frozenset(
    {
        "cash",
        "premium_currency",
        "self_select",
        "optional_box",
        "unknown",
    }
)


@dataclass(frozen=True)
class ResourceAutoUseDecision:
    action: DecisionAction
    reason: str
    candidate_count: int = 0
    expected_units: int = 0


def _fail(reason: str) -> ResourceAutoUseDecision:
    return ResourceAutoUseDecision("fail", reason)


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def decide_storage_bag_round(observation: dict[str, Any]) -> ResourceAutoUseDecision:
    """Decide whether another storage-bag quick-operation round is safe.

    The common ``2-3 rounds`` observation is not a completion condition.  The
    terminal state is either the exact game toast, or a fresh unchanged panel
    signature observed for a bounded quiet window with no result transition.
    """

    if not observation.get("complete"):
        return _fail("储物袋观测不完整")
    if observation.get("quick_settings") != EXPECTED_STORAGE_SETTINGS:
        return _fail("储物袋快捷设置不在授权边界内")

    rounds = _positive_int(observation.get("max_rounds"))
    try:
        completed_rounds = int(observation.get("completed_rounds") or 0)
    except (TypeError, ValueError):
        return _fail("储物袋执行预算无效")
    if rounds is None or rounds > 3 or completed_rounds < 0:
        return _fail("储物袋执行预算无效")

    if observation.get("toast") == STORAGE_TERMINAL_TOAST:
        return ResourceAutoUseDecision("complete", "游戏明确报告无可快捷操作项")

    before = str(observation.get("panel_signature_before") or "")
    after = str(observation.get("panel_signature_after") or "")
    try:
        stable_seconds = float(observation.get("stable_seconds") or 0)
        stable_polls = int(observation.get("stable_polls") or 0)
    except (TypeError, ValueError):
        return _fail("储物袋稳定窗口证据无效")
    if (
        before
        and before == after
        and stable_seconds >= 10.0
        and stable_polls >= 3
        and not observation.get("result_transition_seen")
        and not observation.get("transition_pending")
    ):
        return ResourceAutoUseDecision(
            "complete", "快捷面板前后签名一致且已形成无结果固定点"
        )

    if completed_rounds >= rounds:
        return _fail("储物袋达到轮数上限但未取得完成证据")
    return ResourceAutoUseDecision("execute", "仍需执行一轮快捷操作")


def _validate_resources(
    resources: Any,
    *,
    allowed_kinds: frozenset[str],
) -> tuple[bool, str]:
    if not isinstance(resources, list) or not resources:
        return False, "缺少精确资源消耗清单"
    for resource in resources:
        if not isinstance(resource, dict):
            return False, "资源消耗行结构无效"
        kind = str(resource.get("kind") or "unknown")
        if kind in _FORBIDDEN_RESOURCE_KINDS or kind not in allowed_kinds:
            return False, f"未授权资源类型：{kind}"
        if _positive_int(resource.get("item_id")) is None:
            return False, "资源消耗行缺少有效 item_id"
        if _positive_int(resource.get("quantity")) is None:
            return False, "资源消耗数量无效"
    return True, ""


def plan_pet_quick_swallow(snapshot: dict[str, Any]) -> ResourceAutoUseDecision:
    """Authorize the native ordinary-pet quick-swallow candidate set.

    The button consumes every currently usable item, so one unsafe or unknown
    candidate rejects the whole batch.  Holy-beast/therion rows are outside the
    current authorization even if a future UI includes them.
    """

    if not snapshot.get("complete"):
        return _fail("灵兽快捷吞噬候选快照不完整")
    if snapshot.get("source") != "PetData.CheckPetCardUpCount":
        return _fail("灵兽候选不是游戏原生普通升阶集合")
    candidates = snapshot.get("candidates")
    if not isinstance(candidates, list):
        return _fail("灵兽候选列表缺失")
    if not candidates:
        return ResourceAutoUseDecision("complete", "当前暂无可吞噬的灵兽")

    expected_units = 0
    seen: set[int] = set()
    for candidate in candidates:
        if not isinstance(candidate, dict):
            return _fail("灵兽候选行结构无效")
        pet_id = _positive_int(candidate.get("pet_id"))
        if pet_id is None or pet_id in seen:
            return _fail("灵兽候选身份缺失或重复")
        seen.add(pet_id)
        therion_type = _nonnegative_int(candidate.get("therion_type"))
        if therion_type is None:
            return _fail(f"候选 {pet_id} 缺少明确灵兽类型")
        if therion_type != 0:
            return _fail(f"候选 {pet_id} 不是普通灵兽")
        count = _positive_int(candidate.get("upgrade_count"))
        if not candidate.get("owned") or count is None:
            return _fail(f"候选 {pet_id} 不是已拥有且可升阶的普通灵兽")
        ok, reason = _validate_resources(
            candidate.get("resources"),
            allowed_kinds=_AUTHORIZED_PET_RESOURCE_KINDS,
        )
        if not ok:
            return _fail(f"灵兽 {pet_id}：{reason}")
        expected_units += count
    return ResourceAutoUseDecision(
        "execute",
        "候选全部属于已授权的普通灵兽升阶材料",
        candidate_count=len(candidates),
        expected_units=expected_units,
    )


def plan_talisman_quick_upgrade(snapshot: dict[str, Any]) -> ResourceAutoUseDecision:
    """Authorize the game's native all-upgradeable-talisman batch."""

    if not snapshot.get("complete"):
        return _fail("法宝快速升级候选快照不完整")
    if snapshot.get("source") != "TalismanModel.GetAllUpgradeableTalismanList":
        return _fail("法宝候选不是游戏原生可升级集合")
    candidates = snapshot.get("candidates")
    if not isinstance(candidates, list):
        return _fail("法宝候选列表缺失")
    if not candidates:
        return ResourceAutoUseDecision("complete", "暂无可升级法宝")

    expected_units = 0
    seen: set[int] = set()
    for candidate in candidates:
        if not isinstance(candidate, dict):
            return _fail("法宝候选行结构无效")
        talisman_id = _positive_int(candidate.get("talisman_id"))
        if talisman_id is None or talisman_id in seen:
            return _fail("法宝候选身份缺失或重复")
        seen.add(talisman_id)
        category = str(candidate.get("category") or "")
        if category not in _AUTHORIZED_TALISMAN_CATEGORIES:
            return _fail(f"法宝 {talisman_id} 的类别未授权：{category or 'unknown'}")
        count = _positive_int(candidate.get("upgrade_count"))
        if not candidate.get("owned") or not candidate.get("active") or count is None:
            return _fail(f"法宝 {talisman_id} 不是已激活且可升级对象")
        if count > 50:
            return _fail(f"法宝 {talisman_id} 单批升级超过游戏原生50阶上限")
        ok, reason = _validate_resources(
            candidate.get("resources"),
            allowed_kinds=_AUTHORIZED_TALISMAN_RESOURCE_KINDS,
        )
        if not ok:
            return _fail(f"法宝 {talisman_id}：{reason}")
        expected_units += count
    return ResourceAutoUseDecision(
        "execute",
        "候选全部属于已授权的法宝原生升阶集合",
        candidate_count=len(candidates),
        expected_units=expected_units,
    )


def verify_progress_effect(
    before: dict[int, int],
    after: dict[int, int],
    *,
    expected_ids: set[int],
    expected_targets: dict[int, int] | None = None,
) -> bool:
    """Require a complete, scoped and optionally exact progress transition.

    ``expected_targets`` is intentionally optional for compatibility with
    callers that only know which entities may change.  Broad batch actions
    should supply it: candidate disappearance alone is not completion proof.
    """

    if not expected_ids or set(before) != set(after) or not expected_ids <= set(before):
        return False
    if expected_targets is not None and set(expected_targets) != expected_ids:
        return False
    changed = False
    for entity_id, old_value in before.items():
        new_value = after[entity_id]
        if new_value < old_value:
            return False
        if entity_id in expected_ids:
            if expected_targets is not None:
                target = _nonnegative_int(expected_targets.get(entity_id))
                if target is None or target <= old_value or new_value != target:
                    return False
                changed = True
            elif new_value > old_value:
                changed = True
        if entity_id not in expected_ids and new_value != old_value:
            return False
    return changed


def verify_pet_quick_swallow_effect(
    before: dict[str, Any], after: dict[str, Any]
) -> bool:
    """Prove the exact native pet batch effect from two complete snapshots.

    Success requires all three independent facts to agree:

    * every authorized pet reaches its projected target and every other owned
      pet remains unchanged;
    * every relevant material count drops by the exact projected aggregate,
      while unrelated relevant counts stay unchanged;
    * the fresh native candidate projection converges to an empty set.

    This deliberately rejects a mere candidate disappearance, partial batch,
    over-consumption, newly appeared candidates, and incomplete post-reads.
    """

    if (
        not before.get("complete")
        or not after.get("complete")
        or before.get("source") != "PetData.CheckPetCardUpCount"
        or after.get("source") != "PetData.CheckPetCardUpCount"
    ):
        return False
    if plan_pet_quick_swallow(before).action != "execute":
        return False
    if plan_pet_quick_swallow(after).action != "complete":
        return False
    before_candidates = before.get("candidates")
    after_candidates = after.get("candidates")
    if not isinstance(before_candidates, list) or not before_candidates:
        return False
    if not isinstance(after_candidates, list) or after_candidates:
        return False
    if after.get("candidate_count") != 0:
        return False

    expected_targets: dict[int, int] = {}
    expected_materials: Counter[int] = Counter()
    for candidate in before_candidates:
        if not isinstance(candidate, dict):
            return False
        pet_id = _positive_int(candidate.get("pet_id"))
        current_level = _nonnegative_int(candidate.get("current_level"))
        target_level = _nonnegative_int(candidate.get("target_level"))
        upgrade_count = _positive_int(candidate.get("upgrade_count"))
        if (
            pet_id is None
            or pet_id in expected_targets
            or current_level is None
            or target_level is None
            or upgrade_count is None
            or target_level != current_level + upgrade_count
        ):
            return False
        expected_targets[pet_id] = target_level
        resources = candidate.get("resources")
        if not isinstance(resources, list):
            return False
        for resource in resources:
            if not isinstance(resource, dict):
                return False
            item_id = _positive_int(resource.get("item_id"))
            quantity = _positive_int(resource.get("quantity"))
            if item_id is None or quantity is None:
                return False
            expected_materials[item_id] += quantity

    before_progress = before.get("entity_progress")
    after_progress = after.get("entity_progress")
    if not isinstance(before_progress, dict) or not isinstance(after_progress, dict):
        return False
    def normalize_progress(values: dict[Any, Any]) -> dict[int, int] | None:
        normalized: dict[int, int] = {}
        for raw_id, raw_level in values.items():
            entity_id = _positive_int(raw_id)
            level = _nonnegative_int(raw_level)
            if entity_id is None or level is None or entity_id in normalized:
                return None
            normalized[entity_id] = level
        return normalized

    before_levels = normalize_progress(before_progress)
    after_levels = normalize_progress(after_progress)
    if before_levels is None or after_levels is None:
        return False
    for candidate in before_candidates:
        pet_id = int(candidate["pet_id"])
        if before_levels.get(pet_id) != int(candidate["current_level"]):
            return False
    if not verify_progress_effect(
        before_levels,
        after_levels,
        expected_ids=set(expected_targets),
        expected_targets=expected_targets,
    ):
        return False

    material_totals = before.get("material_totals")
    before_inventory = before.get("inventory_counts")
    after_inventory = after.get("inventory_counts")
    if (
        not isinstance(material_totals, dict)
        or not material_totals
        or not isinstance(before_inventory, dict)
        or not isinstance(after_inventory, dict)
    ):
        return False

    def normalize_counts(values: dict[Any, Any]) -> dict[int, int] | None:
        normalized: dict[int, int] = {}
        for raw_id, raw_count in values.items():
            item_id = _positive_int(raw_id)
            count = _nonnegative_int(raw_count)
            if item_id is None or count is None or item_id in normalized:
                return None
            normalized[item_id] = count
        return normalized

    totals = normalize_counts(material_totals)
    inventory_before = normalize_counts(before_inventory)
    inventory_after = normalize_counts(after_inventory)
    if (
        totals is None
        or inventory_before is None
        or inventory_after is None
        or not totals
        or any(quantity <= 0 for quantity in totals.values())
        or totals != dict(expected_materials)
        or set(inventory_before) != set(inventory_after)
        or not set(totals) <= set(inventory_before)
    ):
        return False
    for item_id, old_count in inventory_before.items():
        expected_count = old_count - totals.get(item_id, 0)
        if expected_count < 0 or inventory_after[item_id] != expected_count:
            return False
    if after.get("material_totals") != {}:
        return False
    return True


__all__ = [
    "EXPECTED_STORAGE_SETTINGS",
    "ResourceAutoUseDecision",
    "STORAGE_TERMINAL_TOAST",
    "decide_storage_bag_round",
    "plan_pet_quick_swallow",
    "plan_talisman_quick_upgrade",
    "verify_pet_quick_swallow_effect",
    "verify_progress_effect",
]
