from __future__ import annotations

"""Beast Abyss batch measurement, scatter modeling and tier planning."""

from dataclasses import dataclass
from datetime import datetime
from fractions import Fraction
import hashlib
import json
from math import ceil, floor
from collections.abc import Iterable, Mapping
from typing import Any

from backend.core.fanxiu.activity.batch_stability import (
    positive_relative_change_is_stable,
)


BEAST_ABYSS_MEASUREMENT_EXPLORES = 100


def build_beast_abyss_shop_snapshot_key(
    shop_items: Iterable[Mapping[str, Any]],
    *,
    captured_at: str,
) -> str:
    """Fingerprint the exact purchase-progress facts used by a plan."""

    captured = str(captured_at or "").strip()
    if not captured:
        raise ValueError("兽渊商店快照缺少采集时间")
    rows: list[dict[str, int]] = []
    seen_goods_ids: set[int] = set()
    for item in shop_items:
        goods_id = int(item.get("goods_id") or 0)
        if goods_id <= 0 or goods_id in seen_goods_ids:
            raise ValueError("兽渊商店快照商品身份无效或重复")
        seen_goods_ids.add(goods_id)
        purchase_limit = int(item.get("purchase_limit") or 0)
        purchased_count = int(item.get("purchased_count") or 0)
        if purchased_count < 0 or (
            purchase_limit >= 0 and purchased_count > purchase_limit
        ):
            raise ValueError("兽渊商店快照购买进度越界")
        rows.append(
            {
                "goods_id": goods_id,
                "source_order": int(item.get("source_order") or 0),
                "purchase_limit": purchase_limit,
                "purchased_count": purchased_count,
            }
        )
    if not rows:
        raise ValueError("兽渊商店快照没有商品行")
    payload = json.dumps(
        {"captured_at": captured, "items": sorted(rows, key=lambda row: row["goods_id"])},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


@dataclass(frozen=True)
class BeastAbyssResourceLedger:
    activity_instance_id: str
    shop_snapshot_key: str
    hierarchy: int
    cumulative_currency: int
    current_currency: int
    explore_points: int
    explore_items: int
    challenge_points: int
    challenge_items: int
    personal_score: int
    personal_rank_object_identity: str = ""


@dataclass(frozen=True)
class BeastAbyssBatchMeasurement:
    activity_instance_id: str
    shop_snapshot_key: str
    hierarchy: int
    requested_explores: int
    completed_explores: int
    duration_seconds: float
    new_currency: int
    balance_delta: int
    personal_score_delta: int
    explore_items_used: int
    challenge_items_used: int
    challenge_capacity_used: int
    currency_per_explore: Fraction
    seconds_per_explore: float
    challenge_per_explore: Fraction
    ending_hierarchy: int | None = None
    duration_reliable: bool = True
    native_batch_size: int = 1
    duration_is_upper_bound: bool = False


@dataclass(frozen=True)
class BeastAbyssYieldScatterModel:
    """Occurrence-bound dual-y model; shop/layer fields are audit context only."""

    activity_instance_id: str
    shop_snapshot_key: str
    hierarchy: int
    points: tuple[tuple[int, int, int], ...]
    currency_per_explore: Fraction
    personal_score_per_explore: Fraction
    seconds_per_explore: float | None
    challenge_per_explore: Fraction
    hierarchy_transitions: tuple[tuple[int, int], ...] = ()


@dataclass(frozen=True)
class BeastAbyssChallengePlan:
    target_tier: str
    remaining_target_explores: int
    requested_explores: int
    target_new_currency: int
    estimated_new_currency: int
    explore_capacity: int
    challenge_limited_capacity: int
    challenge_rate_with_margin: Fraction
    reason: str
    status: str = "ready"
    capacity: int = 0
    deficit: int = 0
    target_goods_id: int | None = None
    unlock_at: str | None = None


@dataclass(frozen=True)
class BeastAbyssAutoSettings:
    fairy_events: bool | None
    beast_events: bool | None
    player_events: bool | None
    auto_use_explore_items: bool | None
    stop_when_killed: bool | None
    fast_auto: bool | None
    skip_animation: bool | None
    requested_explores: int | None
    use_find_demon_talisman: bool | None = False

    def option_values(self) -> dict[str, bool | None]:
        """Return toggle state by stable semantic key, independent of UI order."""

        return {
            "fairy_events": self.fairy_events,
            "beast_events": self.beast_events,
            "player_events": self.player_events,
            "auto_use_explore_items": self.auto_use_explore_items,
            "stop_when_killed": self.stop_when_killed,
            "fast_auto": self.fast_auto,
            "skip_animation": self.skip_animation,
            "use_find_demon_talisman": self.use_find_demon_talisman,
        }


def validate_beast_abyss_auto_settings(
    settings: BeastAbyssAutoSettings,
    *,
    measurement: bool,
) -> None:
    """Validate GUI-read settings before clicking ``开启自动``."""

    values = settings.option_values()
    if any(value is None for value in values.values()) or settings.requested_explores is None:
        raise ValueError("兽渊自动探查开关未完整读回")
    if int(settings.requested_explores) <= 0:
        raise ValueError("兽渊自动探查次数必须为正数")
    if settings.skip_animation and not settings.fast_auto:
        raise ValueError("兽渊跳过动画必须同时启用快速自动")
    if measurement:
        expected = {
            "fairy_events": False,
            "beast_events": True,
            "player_events": True,
            "auto_use_explore_items": True,
            "stop_when_killed": False,
            "fast_auto": True,
            "skip_animation": True,
            "use_find_demon_talisman": False,
        }
        if values != expected:
            mismatches = {
                key: {"expected": desired, "actual": values.get(key)}
                for key, desired in expected.items()
                if values.get(key) is not desired
            }
            raise ValueError(f"兽渊首轮测速配置不一致：{mismatches!r}")
        if settings.requested_explores != BEAST_ABYSS_MEASUREMENT_EXPLORES:
            raise ValueError("兽渊测速 GUI 必须回读为100次")


def beast_abyss_completed_count_matches(requested: int, completed: int | None, native_batch_size: int) -> bool:
    """Native terminal may cross the target within its last quick group.

    The live counter need not be a multiple of ten (50 configured, 55 observed).
    Require reaching the target with less than one group of excess; callers
    must separately prove the native completed terminal and stopped state.
    """
    return (requested > 0 and native_batch_size in (1, 10) and completed is not None
            and requested <= completed < requested + native_batch_size)


def measure_beast_abyss_completed_batch(
    before: BeastAbyssResourceLedger,
    after: BeastAbyssResourceLedger,
    *,
    requested_explores: int,
    completed_explores: int,
    duration_seconds: float,
    challenge_item_automatic: int = 0,
    duration_reliable: bool = True,
    native_batch_size: int = 1,
    duration_is_upper_bound: bool = False,
) -> BeastAbyssBatchMeasurement:
    if not before.activity_instance_id or (
        before.activity_instance_id != after.activity_instance_id
    ):
        raise ValueError("兽渊测速前后活动实例不一致")
    if not before.shop_snapshot_key or (
        before.shop_snapshot_key != after.shop_snapshot_key
    ):
        raise ValueError("兽渊测速前后兑换购买进度快照不一致")
    if before.hierarchy <= 0 or after.hierarchy <= 0:
        raise ValueError("兽渊测速前后缺少层级观测")
    if requested_explores <= 0:
        raise ValueError("兽渊批次目标次数必须为正数")
    if native_batch_size not in (1, 10):
        raise ValueError("兽渊原生批量单位只能是1或10")
    if not beast_abyss_completed_count_matches(requested_explores, completed_explores, native_batch_size):
        raise ValueError("兽渊批次未完整完成目标次数，拒绝更新模型")
    if duration_seconds <= 0:
        raise ValueError("兽渊测速耗时必须为正数")
    new_currency = after.cumulative_currency - before.cumulative_currency
    if new_currency < 0:
        raise ValueError("兽渊测速累计兽元倒退")
    personal_score_delta = after.personal_score - before.personal_score
    if personal_score_delta < 0:
        raise ValueError("兽渊测速前后排行积分倒退")
    explore_items_used = max(0, before.explore_items - after.explore_items)
    challenge_items_used = max(
        0, before.challenge_items - after.challenge_items
    )
    challenge_capacity_used = max(
        0,
        before.challenge_points
        + challenge_items_used * max(0, int(challenge_item_automatic))
        - after.challenge_points,
    )
    return BeastAbyssBatchMeasurement(
        activity_instance_id=before.activity_instance_id,
        shop_snapshot_key=before.shop_snapshot_key,
        hierarchy=before.hierarchy,
        requested_explores=requested_explores,
        completed_explores=completed_explores,
        duration_seconds=float(duration_seconds),
        new_currency=new_currency,
        balance_delta=after.current_currency - before.current_currency,
        personal_score_delta=personal_score_delta,
        explore_items_used=explore_items_used,
        challenge_items_used=challenge_items_used,
        challenge_capacity_used=challenge_capacity_used,
        currency_per_explore=Fraction(new_currency, completed_explores),
        seconds_per_explore=float(duration_seconds) / completed_explores,
        challenge_per_explore=Fraction(
            challenge_capacity_used, completed_explores
        ),
        ending_hierarchy=after.hierarchy,
        duration_reliable=bool(duration_reliable),
        native_batch_size=native_batch_size,
        duration_is_upper_bound=bool(duration_is_upper_bound),
    )


def measure_beast_abyss_batch(
    before: BeastAbyssResourceLedger,
    after: BeastAbyssResourceLedger,
    *,
    requested_explores: int,
    completed_explores: int,
    duration_seconds: float,
    challenge_item_automatic: int = 0,
    duration_reliable: bool = True,
) -> BeastAbyssBatchMeasurement:
    """Measure one initialization sample using the shared 100-run batch."""

    if requested_explores != BEAST_ABYSS_MEASUREMENT_EXPLORES:
        raise ValueError("兽渊测速必须使用一次完整的100次原生批次")
    if completed_explores != requested_explores:
        raise ValueError("兽渊测速未完整完成100次，拒绝外推")
    return measure_beast_abyss_completed_batch(
        before,
        after,
        requested_explores=requested_explores,
        completed_explores=completed_explores,
        duration_seconds=duration_seconds,
        challenge_item_automatic=challenge_item_automatic,
        duration_reliable=duration_reliable,
    )


def build_beast_abyss_yield_scatter_model(
    measurements: Iterable[BeastAbyssBatchMeasurement],
) -> BeastAbyssYieldScatterModel:
    """Fit an origin-anchored model without mixing activity occurrences."""

    rows = tuple(measurements)
    if not rows:
        raise ValueError("兽渊散点模型没有有效测速点")
    first = rows[0]
    for row in rows:
        if row.activity_instance_id != first.activity_instance_id:
            raise ValueError("兽渊散点模型混入了其他活动实例")
        if (
            row.requested_explores <= 0
            or row.native_batch_size not in (1, 10)
            or not beast_abyss_completed_count_matches(row.requested_explores, row.completed_explores, row.native_batch_size)
            or row.new_currency < 0
        ):
            raise ValueError("兽渊散点模型包含无效测速点")
    # Quick auto dispatches groups of ten. Use the count proved by native
    # completion, retaining the original configured count separately.
    denominator = sum(row.completed_explores ** 2 for row in rows)
    currency_numerator = sum(
        row.completed_explores * row.new_currency for row in rows
    )
    score_numerator = sum(
        row.completed_explores * row.personal_score_delta for row in rows
    )
    challenge_numerator = sum(
        row.completed_explores * row.challenge_capacity_used for row in rows
    )
    timed_rows = tuple(row for row in rows if row.duration_reliable)
    seconds_denominator = sum(row.completed_explores ** 2 for row in timed_rows)
    seconds_numerator = sum(
        row.completed_explores * row.duration_seconds for row in timed_rows
    )
    return BeastAbyssYieldScatterModel(
        activity_instance_id=first.activity_instance_id,
        shop_snapshot_key=first.shop_snapshot_key,
        hierarchy=first.hierarchy,
        points=tuple(
            (row.completed_explores, row.new_currency, row.personal_score_delta)
            for row in rows
        ),
        currency_per_explore=Fraction(currency_numerator, denominator),
        personal_score_per_explore=Fraction(score_numerator, denominator),
        seconds_per_explore=seconds_numerator / seconds_denominator if seconds_denominator else None,
        challenge_per_explore=Fraction(challenge_numerator, denominator),
        hierarchy_transitions=tuple(
            (row.hierarchy, row.ending_hierarchy or row.hierarchy) for row in rows
        ),
    )


def is_beast_abyss_currency_yield_stable(
    previous: BeastAbyssBatchMeasurement,
    current: BeastAbyssBatchMeasurement,
    *,
    maximum_change: Fraction = Fraction(1, 2),
) -> bool:
    """Return whether adjacent occurrence-bound batch yields vary by at most 50%."""

    if previous.activity_instance_id != current.activity_instance_id:
        raise ValueError("兽渊稳定性比较混入了其他活动实例")
    if previous.completed_explores != current.completed_explores:
        raise ValueError("兽渊稳定性比较必须使用等大批次")
    if previous.new_currency <= 0 or current.new_currency <= 0:
        raise ValueError("兽渊稳定性比较缺少正数兑币产出")
    return positive_relative_change_is_stable(
        previous.new_currency,
        current.new_currency,
        maximum_change=maximum_change,
    )
def plan_beast_abyss_measurement_batch(
    snapshot: BeastAbyssResourceLedger,
    *,
    hierarchy_consume: int,
    explore_item_automatic: int = 0,
    challenge_item_automatic: int = 0,
) -> int:
    """Prove that exploration resources can finish the configured 100-run batch.

    Challenge events are stochastic and do not consume one challenge point per
    exploration.  Their actual usage belongs in the completed batch sample;
    treating them as a one-to-one prerequisite incorrectly blocks valid runs.
    """

    if hierarchy_consume <= 0:
        raise ValueError("兽渊当前层探索消耗无效")
    if snapshot.hierarchy <= 0:
        raise ValueError("兽渊当前层级身份无效")
    required_explore_points = (
        BEAST_ABYSS_MEASUREMENT_EXPLORES * hierarchy_consume
    )
    explore_capacity = (
        snapshot.explore_points
        + snapshot.explore_items * max(0, int(explore_item_automatic))
    )
    if explore_capacity < required_explore_points:
        raise ValueError("兽渊探索资源不足以完成100次测速")
    del challenge_item_automatic
    return BEAST_ABYSS_MEASUREMENT_EXPLORES


def plan_beast_abyss_challenge_once(
    snapshot: BeastAbyssResourceLedger,
    measurement: BeastAbyssBatchMeasurement,
    *, now: datetime, activity_end_at: datetime,
    other_discount_new_currency: int, closing_goods_new_currency: int,
    explore_item_automatic: int, challenge_item_automatic: int = 0,
    hierarchy_consume: int = 1, challenge_margin_percent: int = 25,
    batch_size: int = BEAST_ABYSS_MEASUREMENT_EXPLORES,
) -> BeastAbyssChallengePlan:
    """Compatibility adapter for callers with only two ordered budget gaps.

    Production supplies every commodity row to ``plan_beast_abyss_formal_batch``.
    This adapter follows the same next-target/full-batch policy.
    """
    milestones = []
    for goods_id, name, gap in ((1, "其他折扣", other_discount_new_currency),
                                (2, "收尾道具", closing_goods_new_currency)):
        if gap < 0:
            raise ValueError("兽渊兑换缺口不能为负数")
        milestones.append({"goods_id": goods_id, "name": name,
            "target_total_tokens": snapshot.cumulative_currency + gap,
            "target_remaining_tokens": snapshot.current_currency + gap})
    return plan_beast_abyss_formal_batch(snapshot, measurement,
        {"budget_ready": True, "milestones": milestones},
        explore_item_automatic=explore_item_automatic, challenge_item_automatic=challenge_item_automatic,
        hierarchy_consume=hierarchy_consume, challenge_margin_percent=challenge_margin_percent,
        batch_size=batch_size, now=now, activity_end_at=activity_end_at)


def plan_beast_abyss_formal_batch(
    snapshot: BeastAbyssResourceLedger,
    measurement: BeastAbyssBatchMeasurement,
    exchange_plan: Mapping[str, Any],
    *,
    now: datetime,
    activity_end_at: datetime,
    explore_item_automatic: int,
    challenge_item_automatic: int = 0,
    hierarchy_consume: int = 1,
    challenge_margin_percent: int = 25,
    batch_size: int = BEAST_ABYSS_MEASUREMENT_EXPLORES,
    available_seconds: float | None = None,
    native_batch_size: int = 1,
    timing_measurement: BeastAbyssBatchMeasurement | None = None,
) -> BeastAbyssChallengePlan:
    """Next commodity milestone, using only the last settled batch.

    Exploration and challenge supplies gate the entire requested batch. No
    cheaper target, half batch or partial spending is substituted on a pass.
    ``batch_size`` is retained for callers; it no longer splits the plan.
    Yield always comes from ``measurement``. A last reliable complete timing
    sample may separately replace an interrupted observation's wall-time bound.
    """
    from .exchange_challenge_planning import plan_exchange_challenge_batch

    if not exchange_plan.get("budget_ready"):
        raise ValueError("兽渊正式规划要求同窗口最新兑换宝阁与钱包事实")
    if not isinstance(measurement, BeastAbyssBatchMeasurement):
        raise ValueError("兽渊逐档规划必须提供上一完整批次，不能提供全历史拟合")
    if snapshot.activity_instance_id != measurement.activity_instance_id:
        raise ValueError("兽渊计划实例与测速实例不一致")
    if hierarchy_consume <= 0 or min(explore_item_automatic, challenge_item_automatic) < 0:
        raise ValueError("兽渊资源换算配置无效")
    explore_capacity = (snapshot.explore_points + snapshot.explore_items * explore_item_automatic) // hierarchy_consume
    # Zero observed consumption is not one challenge per exploration. Keep a
    # finite reserve of one challenge per *observed batch*, then apply the
    # same margin as positive samples. This preserves a supply gate without
    # turning 100 successful zero-consumption explores into a 1:1 cost model.
    challenge_rate = max(
        measurement.challenge_per_explore,
        Fraction(1, measurement.completed_explores),
    ) * Fraction(100 + max(0, challenge_margin_percent), 100)
    challenge_capacity = snapshot.challenge_points + snapshot.challenge_items * challenge_item_automatic
    challenge_limited = floor(Fraction(challenge_capacity) / challenge_rate)
    capacity = max(0, min(explore_capacity, challenge_limited))
    time_unknown = False
    if available_seconds is not None:
        timing = timing_measurement or measurement
        if timing_measurement is not None and (
            timing.activity_instance_id != measurement.activity_instance_id
            or not timing.duration_reliable or timing.native_batch_size != measurement.native_batch_size
        ):
            raise ValueError("兽渊耗时样本必须来自本期同批量模式的完整实测")
        time_unknown = not (timing.duration_reliable or timing.duration_is_upper_bound) or timing.seconds_per_explore <= 0
        time_capacity = 0 if time_unknown else floor(max(0, available_seconds) / timing.seconds_per_explore)
        capacity = min(capacity, time_capacity)
    milestones = exchange_plan.get("milestones")
    if not milestones:
        raise ValueError("兽渊缺少商品累计兑换档次")
    plan = plan_exchange_challenge_batch(
        milestones=milestones, current_currency=snapshot.current_currency,
        cumulative_currency=snapshot.cumulative_currency,
        samples=[{"completed_attempts": measurement.completed_explores,
                  "requested_attempts": measurement.completed_explores,
                  "currency_delta": measurement.new_currency}], capacity=capacity,
        now=now, activity_end_at=activity_end_at, batch_unit=native_batch_size)
    if time_unknown and plan["reason"] == "resource_insufficient":
        plan["reason"] = "duration_unknown"
    elif available_seconds is not None and plan["reason"] == "resource_insufficient" and plan["needed"] <= min(explore_capacity, challenge_limited):
        plan["reason"] = "time_insufficient"
    target = plan.get("target") or {}
    return BeastAbyssChallengePlan(
        target_tier=str(target.get("name") or target.get("goods_id") or "全部有限商品"),
        remaining_target_explores=plan["needed"], requested_explores=plan["count"],
        target_new_currency=int(plan.get("gap") or 0),
        estimated_new_currency=floor(measurement.currency_per_explore * plan["count"]),
        explore_capacity=explore_capacity, challenge_limited_capacity=challenge_limited,
        challenge_rate_with_margin=challenge_rate, reason=plan["reason"],
        status=plan["status"], capacity=capacity, deficit=plan["deficit"],
        target_goods_id=target.get("goods_id"),
        unlock_at=plan.get("unlock_at"),
    )


def plan_beast_abyss_next_batch(
    remaining_explores: int,
    *,
    batch_size: int = BEAST_ABYSS_MEASUREMENT_EXPLORES,
) -> int:
    """Return the full planned remainder; milestones now bound each batch."""

    remaining = int(remaining_explores)
    threshold = int(batch_size)
    if remaining < 0:
        raise ValueError("兽渊剩余目标次数不能为负数")
    if threshold <= 0:
        raise ValueError("兽渊统一批次阈值必须为正数")
    return remaining


__all__ = [
    "BEAST_ABYSS_MEASUREMENT_EXPLORES",
    "BeastAbyssBatchMeasurement",
    "BeastAbyssYieldScatterModel",
    "BeastAbyssChallengePlan",
    "BeastAbyssResourceLedger",
    "BeastAbyssAutoSettings",
    "build_beast_abyss_shop_snapshot_key",
    "build_beast_abyss_yield_scatter_model",
    "is_beast_abyss_currency_yield_stable",
    "measure_beast_abyss_completed_batch",
    "measure_beast_abyss_batch",
    "plan_beast_abyss_measurement_batch",
    "plan_beast_abyss_formal_batch",
    "plan_beast_abyss_next_batch",
    "plan_beast_abyss_challenge_once",
    "validate_beast_abyss_auto_settings",
]
