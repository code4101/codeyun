from __future__ import annotations

"""One-shot Beast Abyss batch measurement and resource planning."""

from dataclasses import dataclass
from fractions import Fraction
import hashlib
import json
from math import ceil, floor
from collections.abc import Iterable, Mapping
from typing import Any


BEAST_ABYSS_MEASUREMENT_EXPLORES = 10


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


@dataclass(frozen=True)
class BeastAbyssChallengePlan:
    target_tier: str
    requested_explores: int
    target_new_currency: int
    estimated_new_currency: int
    explore_capacity: int
    challenge_limited_capacity: int
    challenge_rate_with_margin: Fraction
    reason: str


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


def validate_beast_abyss_auto_settings(
    settings: BeastAbyssAutoSettings,
    *,
    measurement: bool,
) -> None:
    """Validate GUI-read settings before clicking ``开启自动``."""

    values = (
        settings.fairy_events,
        settings.beast_events,
        settings.player_events,
        settings.auto_use_explore_items,
        settings.stop_when_killed,
        settings.fast_auto,
        settings.skip_animation,
    )
    if any(value is None for value in values) or settings.requested_explores is None:
        raise ValueError("兽渊自动探查开关未完整读回")
    if int(settings.requested_explores) <= 0:
        raise ValueError("兽渊自动探查次数必须为正数")
    if settings.skip_animation and not settings.fast_auto:
        raise ValueError("兽渊跳过动画必须同时启用快速自动")
    if measurement:
        if not settings.stop_when_killed:
            raise ValueError("兽渊测速必须开启被击杀停止")
        if settings.player_events:
            raise ValueError("兽渊测速禁止自动处理玩家事件")
        if settings.requested_explores != BEAST_ABYSS_MEASUREMENT_EXPLORES:
            raise ValueError("兽渊测速 GUI 必须回读为10次")
        if settings.auto_use_explore_items:
            raise ValueError("兽渊测速禁止自动使用探查符")


def measure_beast_abyss_batch(
    before: BeastAbyssResourceLedger,
    after: BeastAbyssResourceLedger,
    *,
    requested_explores: int,
    completed_explores: int,
    duration_seconds: float,
    challenge_item_automatic: int = 0,
) -> BeastAbyssBatchMeasurement:
    if not before.activity_instance_id or (
        before.activity_instance_id != after.activity_instance_id
    ):
        raise ValueError("兽渊测速前后活动实例不一致")
    if not before.shop_snapshot_key or (
        before.shop_snapshot_key != after.shop_snapshot_key
    ):
        raise ValueError("兽渊测速前后兑换购买进度快照不一致")
    if before.hierarchy <= 0 or before.hierarchy != after.hierarchy:
        raise ValueError("兽渊测速前后当前层级不一致")
    if requested_explores != BEAST_ABYSS_MEASUREMENT_EXPLORES:
        raise ValueError("兽渊测速必须使用一次完整的10次原生批次")
    if completed_explores != requested_explores:
        raise ValueError("兽渊测速未完整完成10次，拒绝外推")
    if duration_seconds <= 0:
        raise ValueError("兽渊测速耗时必须为正数")
    new_currency = after.cumulative_currency - before.cumulative_currency
    if new_currency <= 0:
        raise ValueError("兽渊测速没有得到正数累计兽元增量")
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
        personal_score_delta=after.personal_score - before.personal_score,
        explore_items_used=explore_items_used,
        challenge_items_used=challenge_items_used,
        challenge_capacity_used=challenge_capacity_used,
        currency_per_explore=Fraction(new_currency, completed_explores),
        seconds_per_explore=float(duration_seconds) / completed_explores,
        challenge_per_explore=Fraction(
            challenge_capacity_used, completed_explores
        ),
    )


def plan_beast_abyss_measurement_batch(
    snapshot: BeastAbyssResourceLedger,
    *,
    hierarchy_consume: int,
) -> int:
    """Fail closed unless a 10-explore sample needs no supplement items."""

    if hierarchy_consume <= 0:
        raise ValueError("兽渊当前层探索消耗无效")
    if snapshot.hierarchy <= 0:
        raise ValueError("兽渊当前层级身份无效")
    required_explore_points = (
        BEAST_ABYSS_MEASUREMENT_EXPLORES * hierarchy_consume
    )
    if snapshot.explore_points < required_explore_points:
        raise ValueError("兽渊现有探索点不足以完成不使用探查符的10次测速")
    if snapshot.challenge_points < BEAST_ABYSS_MEASUREMENT_EXPLORES:
        raise ValueError("兽渊现有挑战点不足以为10次测速保留保守容量")
    return BEAST_ABYSS_MEASUREMENT_EXPLORES


def plan_beast_abyss_challenge_once(
    snapshot: BeastAbyssResourceLedger,
    measurement: BeastAbyssBatchMeasurement,
    *,
    other_discount_new_currency: int,
    closing_goods_new_currency: int,
    explore_item_automatic: int,
    challenge_item_automatic: int = 0,
    hierarchy_consume: int = 1,
    challenge_margin_percent: int = 25,
) -> BeastAbyssChallengePlan:
    """Produce one post-measurement plan; callers must not roll it forward."""

    if snapshot.activity_instance_id != measurement.activity_instance_id:
        raise ValueError("兽渊计划实例与测速实例不一致")
    if snapshot.shop_snapshot_key != measurement.shop_snapshot_key:
        raise ValueError("兽渊购买进度已变化，旧测速计划不得继续使用")
    if snapshot.hierarchy != measurement.hierarchy:
        raise ValueError("兽渊当前层级已变化，旧测速计划不得继续使用")
    if hierarchy_consume <= 0 or explore_item_automatic < 0:
        raise ValueError("兽渊资源换算配置无效")
    if measurement.currency_per_explore <= 0:
        raise ValueError("兽渊测速产出率无效")
    explore_capacity = (
        snapshot.explore_points
        + snapshot.explore_items * explore_item_automatic
    ) // hierarchy_consume
    challenge_capacity = (
        snapshot.challenge_points
        + snapshot.challenge_items * max(0, challenge_item_automatic)
    )
    # A zero-event sample cannot prove that future exploration needs no
    # challenge points.  Fall back to one challenge per exploration.  For a
    # positive sample, preserve the observed fraction plus a fixed margin.
    observed_rate = measurement.challenge_per_explore
    challenge_rate = (
        Fraction(1, 1)
        if observed_rate <= 0
        else observed_rate
        * Fraction(100 + max(0, challenge_margin_percent), 100)
    )
    challenge_limited = floor(Fraction(challenge_capacity, 1) / challenge_rate)
    resource_capacity = max(0, min(explore_capacity, challenge_limited))

    def explores_for(currency: int) -> int:
        if currency <= 0:
            return 0
        return ceil(Fraction(currency, 1) / measurement.currency_per_explore)

    closing_goods_explores = explores_for(closing_goods_new_currency)
    other_discount_explores = explores_for(other_discount_new_currency)
    if closing_goods_explores <= resource_capacity:
        tier = "收尾道具"
        target_currency = max(0, closing_goods_new_currency)
        requested = closing_goods_explores
        reason = "当前三账本按测速上界可覆盖收尾道具"
    elif other_discount_explores <= resource_capacity:
        tier = "其他折扣"
        target_currency = max(0, other_discount_new_currency)
        requested = other_discount_explores
        reason = "资源不足覆盖收尾道具，按固定顺序完成其他折扣"
    else:
        tier = "尽量接近其他折扣"
        target_currency = max(0, other_discount_new_currency)
        requested = resource_capacity
        reason = "资源不足覆盖其他折扣，使用一次性安全容量尽可能接近"
    estimated = floor(measurement.currency_per_explore * requested)
    return BeastAbyssChallengePlan(
        target_tier=tier,
        requested_explores=requested,
        target_new_currency=target_currency,
        estimated_new_currency=estimated,
        explore_capacity=explore_capacity,
        challenge_limited_capacity=challenge_limited,
        challenge_rate_with_margin=challenge_rate,
        reason=reason,
    )


__all__ = [
    "BEAST_ABYSS_MEASUREMENT_EXPLORES",
    "BeastAbyssBatchMeasurement",
    "BeastAbyssChallengePlan",
    "BeastAbyssResourceLedger",
    "BeastAbyssAutoSettings",
    "build_beast_abyss_shop_snapshot_key",
    "measure_beast_abyss_batch",
    "plan_beast_abyss_measurement_batch",
    "plan_beast_abyss_challenge_once",
    "validate_beast_abyss_auto_settings",
]
