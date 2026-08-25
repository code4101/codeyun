from __future__ import annotations

"""Pure target planner for resource-consuming Fanxiu activities."""

from dataclasses import dataclass
from hashlib import sha256
from statistics import fmean
from typing import Iterable, Literal


DecisionKind = Literal["act", "complete", "blocked"]


@dataclass(frozen=True)
class ResourceTargetContext:
    instance_id: str
    selected_pet_id: int
    applicable_gift_ids: tuple[int, ...]

    def __post_init__(self) -> None:
        if not self.instance_id or self.selected_pet_id <= 0:
            raise ValueError("活动实例与 selected_pet_id 必须有效")
        gifts = tuple(sorted({int(value) for value in self.applicable_gift_ids if int(value) > 0}))
        if not gifts:
            raise ValueError("当前灵兽 applicable_gift_ids 不能为空")
        object.__setattr__(self, "applicable_gift_ids", gifts)

    @property
    def context_key(self) -> str:
        gifts = ",".join(str(value) for value in self.applicable_gift_ids)
        return f"pet:{self.selected_pet_id}|gifts:{gifts}"


@dataclass(frozen=True)
class ResourceOption:
    resource_id: int
    name: str
    available: int
    # Item.effectValue is ``giftId:increment``. Its key is a PetGift id,
    # never a pet type. Only gifts with a positive limit on the selected pet
    # contribute to the configured score.
    gift_gains: tuple[tuple[int, int], ...]
    priority: int = 0
    calibration_required: bool = False
    minimum_samples: int = 1

    def __post_init__(self) -> None:
        gains = tuple((int(gift_id), int(gain)) for gift_id, gain in self.gift_gains)
        if (
            self.resource_id <= 0
            or self.available < 0
            or not gains
            or any(gift_id <= 0 or gain <= 0 for gift_id, gain in gains)
            or len({gift_id for gift_id, _gain in gains}) != len(gains)
            or self.minimum_samples <= 0
        ):
            raise ValueError(f"资源配置无效：{self.resource_id}")
        object.__setattr__(self, "gift_gains", gains)

    def configured_gain_for(self, context: ResourceTargetContext) -> int:
        applicable = set(context.applicable_gift_ids)
        return sum(
            int(gain)
            for gift_id, gain in self.gift_gains
            if int(gift_id) in applicable and int(gain) > 0
        )


@dataclass(frozen=True)
class ResourceActionPoint:
    instance_id: str
    selected_pet_id: int
    applicable_gift_ids: tuple[int, ...]
    action_id: str
    phase: Literal["before_action", "after_action"]
    resource_id: int
    quantity: int
    activity_progress: int
    inventory: int
    # attack, health, spirit, beast and demon aptitude in authoritative order.
    aptitude_values: tuple[int, int, int, int, int]
    captured_at: str = ""

    def __post_init__(self) -> None:
        gifts = tuple(sorted({int(value) for value in self.applicable_gift_ids if int(value) > 0}))
        aptitudes = tuple(int(value) for value in self.aptitude_values)
        if (
            not self.instance_id
            or self.selected_pet_id <= 0
            or not gifts
            or not self.action_id
            or self.phase not in {"before_action", "after_action"}
            or self.resource_id <= 0
            or self.quantity <= 0
            or self.activity_progress < 0
            or self.inventory < 0
            or len(aptitudes) != 5
            or any(value < 0 for value in aptitudes)
        ):
            raise ValueError("资源动作散点字段不完整")
        object.__setattr__(self, "applicable_gift_ids", gifts)
        object.__setattr__(self, "aptitude_values", aptitudes)

    @property
    def context_key(self) -> str:
        return ResourceTargetContext(
            self.instance_id,
            self.selected_pet_id,
            self.applicable_gift_ids,
        ).context_key


@dataclass(frozen=True)
class GainEstimate:
    sample_count: int
    minimum: int
    maximum: int
    mean: float
    configured_gain: int
    config_drift: bool


@dataclass(frozen=True)
class ResourcePlan:
    kind: DecisionKind
    reason: str
    target: int
    current: int
    remaining_gap: int
    action_id: str | None = None
    resource_id: int | None = None
    resource_name: str | None = None
    quantity: int = 0
    mode: Literal["none", "calibrate", "batch", "tail"] = "none"
    expected_delta_min: int = 0
    expected_delta_max: int = 0
    expected_remaining_min: int = 0
    expected_overflow_max: int = 0


def _paired_samples(
    observations: Iterable[ResourceActionPoint],
) -> tuple[dict[int, list[int]], set[str]]:
    by_action: dict[str, dict[str, ResourceActionPoint]] = {}
    for point in observations:
        phases = by_action.setdefault(point.action_id, {})
        previous = phases.get(point.phase)
        if previous is not None and previous != point:
            raise ValueError(f"动作散点冲突：{point.action_id}/{point.phase}")
        phases[point.phase] = point

    samples: dict[int, list[int]] = {}
    pending: set[str] = set()
    for action_id, phases in by_action.items():
        before = phases.get("before_action")
        after = phases.get("after_action")
        if before is None and after is not None:
            raise ValueError(f"动作缺少 before_action：{action_id}")
        if before is not None and after is None:
            pending.add(action_id)
            continue
        assert before is not None and after is not None
        if (
            before.instance_id != after.instance_id
            or before.context_key != after.context_key
            or before.resource_id != after.resource_id
            or before.quantity != after.quantity
        ):
            raise ValueError(f"动作前后身份不一致：{action_id}")
        consumed = before.inventory - after.inventory
        delta = after.activity_progress - before.activity_progress
        aptitude_delta = sum(after.aptitude_values) - sum(before.aptitude_values)
        if consumed != before.quantity or consumed <= 0 or delta <= 0:
            raise ValueError(
                f"动作散点不是有效单调样本：{action_id}, consumed={consumed}, delta={delta}"
            )
        if aptitude_delta <= 0:
            raise ValueError(f"动作没有五资质正向变化：{action_id}")
        if delta % consumed:
            raise ValueError(f"活动进度增量不能归一到单件：{action_id}, delta={delta}")
        samples.setdefault(before.resource_id, []).append(delta // consumed)
    return samples, pending


def estimate_resource_gain(
    option: ResourceOption,
    observations: Iterable[ResourceActionPoint],
    *,
    context: ResourceTargetContext,
) -> GainEstimate | None:
    relevant = (
        point for point in observations if point.context_key == context.context_key
    )
    samples, _pending = _paired_samples(relevant)
    values = samples.get(option.resource_id, [])
    configured = option.configured_gain_for(context)
    if not values and configured <= 0:
        return None
    if values:
        minimum = min(values)
        maximum = max(values)
        mean = fmean(values)
    else:
        minimum = maximum = configured
        mean = float(configured)
    return GainEstimate(
        sample_count=len(values),
        minimum=minimum,
        maximum=maximum,
        mean=mean,
        configured_gain=configured,
        config_drift=bool(values) and any(value != configured for value in values),
    )


def _action_id(
    *, context: ResourceTargetContext, current: int, resource_id: int, quantity: int
) -> str:
    raw = (
        f"{context.instance_id}|{context.context_key}|{current}|{resource_id}|{quantity}"
    ).encode()
    return "resource-" + sha256(raw).hexdigest()[:20]


def plan_next_resource_action(
    *,
    context: ResourceTargetContext,
    target: int,
    current: int,
    resources: Iterable[ResourceOption],
    observations: Iterable[ResourceActionPoint] = (),
    max_batch_size: int = 20,
) -> ResourcePlan:
    """Plan exactly one action, then require a fresh observation/replan."""

    if target <= 0 or current < 0:
        raise ValueError("target/current 无效")
    if max_batch_size <= 0:
        raise ValueError("max_batch_size 必须为正数")
    rows = tuple(resources)
    if len({row.resource_id for row in rows}) != len(rows):
        raise ValueError("resource_id 重复")
    points = tuple(observations)
    current_points = tuple(
        point
        for point in points
        if point.instance_id == context.instance_id
        and point.context_key == context.context_key
    )
    _samples, pending = _paired_samples(current_points)
    gap = max(0, target - current)
    if gap == 0:
        return ResourcePlan("complete", "目标已达成，幂等停止", target, current, 0)
    if pending:
        return ResourcePlan(
            "blocked",
            f"存在未闭合动作散点，拒绝重复消耗：{sorted(pending)}",
            target,
            current,
            gap,
        )

    available = [row for row in rows if row.available > 0]
    calibration: list[ResourceOption] = []
    usable: list[tuple[ResourceOption, GainEstimate]] = []
    for row in available:
        if row.minimum_samples <= 0:
            raise ValueError(f"minimum_samples 必须为正数：{row.resource_id}")
        estimate = estimate_resource_gain(row, points, context=context)
        # No overlap between the pill's gift ids and the selected pet's
        # positive-limit gift ids means the pill contributes nothing here.
        if estimate is None:
            continue
        if row.calibration_required and estimate.sample_count < row.minimum_samples:
            calibration.append(row)
        elif estimate.minimum > 0:
            usable.append((row, estimate))
    if calibration:
        row = max(calibration, key=lambda item: (item.priority, item.resource_id))
        return ResourcePlan(
            "act",
            "先单件采样特殊资源，动作后立即重观测",
            target,
            current,
            gap,
            _action_id(context=context, current=current, resource_id=row.resource_id, quantity=1),
            row.resource_id,
            row.name,
            1,
            "calibrate",
        )
    if not usable:
        return ResourcePlan("blocked", "没有对当前灵兽产生正向资质的可用资源", target, current, gap)

    exact = [
        (row, estimate, gap // estimate.maximum)
        for row, estimate in usable
        if not estimate.config_drift
        and estimate.minimum == estimate.maximum
        and gap % estimate.maximum == 0
        and 0 < gap // estimate.maximum <= row.available
    ]
    if exact:
        row, estimate, quantity = max(
            exact, key=lambda value: (value[0].priority, value[1].maximum)
        )
        quantity = min(quantity, max_batch_size)
    else:
        safe = [(row, estimate) for row, estimate in usable if estimate.maximum <= gap]
        if safe:
            row, estimate = max(
                safe,
                key=lambda value: (value[0].priority, value[1].mean, -value[1].maximum),
            )
            quantity = min(row.available, max_batch_size, gap // estimate.maximum)
            if estimate.config_drift:
                quantity = 1
        else:
            row, estimate = min(
                usable,
                key=lambda value: (
                    value[1].maximum - gap,
                    -value[0].priority,
                    value[1].maximum,
                ),
            )
            quantity = 1

    expected_min = quantity * estimate.minimum
    expected_max = quantity * estimate.maximum
    expected_remaining = max(0, gap - expected_min)
    expected_overflow = max(0, expected_max - gap)
    return ResourcePlan(
        "act",
        "按最新散点增量重规划；动作后必须重新读取进度和库存",
        target,
        current,
        gap,
        _action_id(
            context=context,
            current=current,
            resource_id=row.resource_id,
            quantity=quantity,
        ),
        row.resource_id,
        row.name,
        quantity,
        "tail" if expected_overflow or expected_remaining < estimate.maximum else "batch",
        expected_min,
        expected_max,
        expected_remaining,
        expected_overflow,
    )


__all__ = [
    "GainEstimate",
    "ResourceActionPoint",
    "ResourceOption",
    "ResourcePlan",
    "ResourceTargetContext",
    "estimate_resource_gain",
    "plan_next_resource_action",
]
