from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from enum import StrEnum
from typing import Iterable, Mapping, Protocol
from zoneinfo import ZoneInfo

from backend.core.fanxiu.prayer_cycle import prayer_cycle_week


PRAYER_RESOURCE_BY_CYCLE = {
    "炼丹": "炼丹灵草匣",
    "淬体": "淬体精魄",
    "灵兽": "珍品饲灵丸",
    "洗灵": "洗灵奇石",
    "仙花": "瑶池玉莲",
}
PRAYER_RESOURCE_PRIORITY = (
    "瑶池玉莲",
    "洗灵奇石",
    "淬体精魄",
    "珍品饲灵丸",
    "炼丹灵草匣",
)
EQUIPMENT_IRON_BOX = "装备玄铁宝匣"
DAO_FRAGMENT = "道则碎片·淬灵域"
DAO_FRAGMENT_EDGE = "道则碎片·淬锋域"
DAO_FRAGMENT_NAMES = (DAO_FRAGMENT, DAO_FRAGMENT_EDGE)
XIANYUAN_DUOKUI_DISCOUNTED_DAIER = "誓约·黛儿"
PARTNER_ROOT_ITEM_IDS = frozenset({29601, 29602, 29603, 29604, 29605})
PARTNER_ROOT_NAMES = (
    "金蛟心·绝品",
    "建木果·绝品",
    "忘川水·绝品",
    "神火眼·绝品",
    "息壤土·绝品",
)
TEACHING_JADE = "授业玉简"
ALCHEMY_SCRAP_BOX_SUFFIX = "废料匣·壹"
OVERFLOW_ITEMS = ("玄灵丹·珍", "玄灵丹·尚")


class ExchangePriorityId(StrEnum):
    """Stable business identities; their display order may change."""

    DAIER = "黛儿"
    DAO_FRAGMENT = "道则碎片"
    CURRENT_PRAYER = "本周祈愿"
    NEXT_PRAYER = "下周祈愿"
    CARD_MAIL = "卡邮件"
    PRAYER_RESOURCE = "祈愿资源"
    RESOURCE = "资源"
    PARTNER_ROOT = "仙侣灵根"
    LOWEST_DISCOUNT = "最低折扣"
    OTHER_DISCOUNT = "其他折扣"
    ORDERED_GOODS = "顺序道具"
    CLOSING_GOODS = "收尾道具"
    OVERFLOW_PILL = "溢出丹药"
    NOT_NEEDED = "不需要领"


EXCHANGE_PRIORITY_ORDER = tuple(ExchangePriorityId)


@dataclass(frozen=True)
class ExchangeShopPriorityPolicy:
    schema: int
    prayer_resource_by_cycle: Mapping[str, str]
    prayer_resource_priority: tuple[str, ...]
    equipment_resource_names: tuple[str, ...]
    daier_names: tuple[str, ...]
    dao_fragment_names: tuple[str, ...]
    partner_root_item_ids: frozenset[int]
    partner_root_names: tuple[str, ...]
    closing_goods_names: tuple[str, ...]
    overflow_names: tuple[str, ...]


DEFAULT_EXCHANGE_SHOP_PRIORITY_POLICY = ExchangeShopPriorityPolicy(
    schema=9,
    prayer_resource_by_cycle=PRAYER_RESOURCE_BY_CYCLE,
    prayer_resource_priority=PRAYER_RESOURCE_PRIORITY,
    equipment_resource_names=(EQUIPMENT_IRON_BOX,),
    daier_names=(),
    dao_fragment_names=DAO_FRAGMENT_NAMES,
    partner_root_item_ids=PARTNER_ROOT_ITEM_IDS,
    partner_root_names=PARTNER_ROOT_NAMES,
    closing_goods_names=(TEACHING_JADE, "灵根补全自选匣"),
    overflow_names=OVERFLOW_ITEMS,
)
XIANYUAN_DUOKUI_EXCHANGE_SHOP_PRIORITY_POLICY = replace(
    DEFAULT_EXCHANGE_SHOP_PRIORITY_POLICY,
    daier_names=(XIANYUAN_DUOKUI_DISCOUNTED_DAIER,),
)


def exchange_shop_priority_policy(activity_type: str) -> ExchangeShopPriorityPolicy:
    if str(activity_type or "").strip() == "xianyuan-duokui":
        return XIANYUAN_DUOKUI_EXCHANGE_SHOP_PRIORITY_POLICY
    return DEFAULT_EXCHANGE_SHOP_PRIORITY_POLICY


class ShopItemLike(Protocol):
    goods_id: int
    item_id: int
    source_order: int
    name: str
    purchase_limit: int
    purchased_count: int
    token_cost: int
    discount: int | None


@dataclass(frozen=True)
class ExchangeShopPlan:
    policy_schema: int
    priority_order_ids: tuple[str, ...]
    priority_group_goods_ids: Mapping[str, tuple[int, ...]]
    ordered_goods_ids: tuple[int, ...]
    locked_goods_ids: tuple[int, ...]
    current_prayer_cycle: str
    current_prayer_resource: str
    planning_date: str
    next_prayer_resource: str | None
    card_mail_resource: str | None
    activity_page_close_at: str | None
    next_prayer_cutoff_at: str
    activity_page_closes_after_next_prayer_cutoff: bool
    target_goods_ids: Mapping[str, tuple[int, ...]]
    target_total_tokens: Mapping[str, int]
    target_remaining_tokens: Mapping[str, int]
    closing_goods_items_complete: bool
    card_mail_reserved_tokens: int
    locked_reserved_tokens: int


@dataclass(frozen=True)
class ExchangeShopFundingStatus:
    target_id: str
    current_tokens: int
    remaining_tokens: int
    additional_tokens_required: int
    funded: bool


def exchange_shop_funding_status(
    plan: ExchangeShopPlan,
    *,
    current_tokens: int,
    target_id: ExchangePriorityId | str = ExchangePriorityId.CLOSING_GOODS,
) -> ExchangeShopFundingStatus:
    if int(current_tokens) < 0:
        raise ValueError("current_tokens 不能为负数")
    semantic_id = str(target_id)
    if semantic_id not in plan.target_remaining_tokens:
        raise ValueError(f"不支持的兑换目标：{semantic_id}")
    remaining = int(plan.target_remaining_tokens[semantic_id])
    shortfall = max(0, remaining - int(current_tokens))
    return ExchangeShopFundingStatus(
        target_id=semantic_id,
        current_tokens=int(current_tokens),
        remaining_tokens=remaining,
        additional_tokens_required=shortfall,
        funded=shortfall == 0,
    )


def _local_moment(value: datetime | str | None) -> datetime | None:
    if value is None:
        return None
    moment = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    timezone = ZoneInfo("Asia/Shanghai")
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone)
    return moment.astimezone(timezone)


def _is_discounted_item(item: ShopItemLike) -> bool:
    return (
        item.purchase_limit >= 0
        and item.discount is not None
        and int(item.discount) < 100
    )


def _discounted_item_groups(
    items: Iterable[ShopItemLike],
) -> tuple[list[ShopItemLike], list[ShopItemLike]]:
    """Return every item's lowest discount, then other rows in page order."""

    grouped: dict[int, list[ShopItemLike]] = {}
    for item in items:
        if _is_discounted_item(item):
            grouped.setdefault(int(item.item_id), []).append(item)
    lowest: list[ShopItemLike] = []
    other: list[ShopItemLike] = []
    for rows in grouped.values():
        minimum = min(int(row.discount or 100) for row in rows)
        candidates = [row for row in rows if int(row.discount or 100) == minimum]
        chosen = min(candidates, key=lambda row: (row.source_order, row.goods_id))
        lowest.append(chosen)
        other.extend(row for row in rows if row is not chosen)
    lowest.sort(key=lambda row: (row.source_order, row.goods_id))
    other.sort(key=lambda row: (row.source_order, row.goods_id))
    return lowest, other


def build_exchange_shop_plan(
    items: Iterable[ShopItemLike],
    *,
    activity_end_date: str,
    planning_date: str | date | None = None,
    shop_close_at: datetime | str | None = None,
    policy: ExchangeShopPriorityPolicy = DEFAULT_EXCHANGE_SHOP_PRIORITY_POLICY,
) -> ExchangeShopPlan:
    """Build the semantic exchange order and target budgets.

    The next-week prayer lock exists only when the exact activity-page close
    time is strictly later than next Monday 01:00. Missing exact time fails
    closed. ``activity_end_date`` still defines which prayer week is current.
    """

    rows = sorted(items, key=lambda row: (row.source_order, row.goods_id))
    by_name: dict[str, list[ShopItemLike]] = {}
    for item in rows:
        by_name.setdefault(str(item.name or "").strip(), []).append(item)

    end_date = datetime.fromisoformat(activity_end_date).date()
    effective_planning_date = (
        planning_date
        if isinstance(planning_date, date)
        else datetime.fromisoformat(str(planning_date)).date()
        if planning_date is not None
        else end_date
    )
    planning_moment = datetime.combine(
        effective_planning_date, time(hour=12), tzinfo=ZoneInfo("Asia/Shanghai")
    )
    current_week = prayer_cycle_week(planning_moment)
    current_resource = policy.prayer_resource_by_cycle[current_week.name]
    next_monday = current_week.week_start + timedelta(weeks=1)
    next_prayer_cutoff = next_monday + timedelta(hours=1)
    close_moment = _local_moment(shop_close_at)
    closes_after_cutoff = bool(
        close_moment is not None and close_moment > next_prayer_cutoff
    )
    next_resource = (
        policy.prayer_resource_by_cycle[prayer_cycle_week(next_monday).name]
        if closes_after_cutoff
        else None
    )

    groups: dict[ExchangePriorityId, list[ShopItemLike]] = {
        semantic_id: [] for semantic_id in EXCHANGE_PRIORITY_ORDER
    }
    selected_ids: set[int] = set()

    def select(semantic_id: ExchangePriorityId, candidates: Iterable[ShopItemLike]) -> None:
        for candidate in candidates:
            goods_id = int(candidate.goods_id)
            if goods_id in selected_ids:
                continue
            groups[semantic_id].append(candidate)
            selected_ids.add(goods_id)

    select(
        ExchangePriorityId.DAIER,
        (
            item
            for name in policy.daier_names
            for item in by_name.get(name, ())
            if item.purchase_limit >= 0 and int(item.discount or 100) == 50
        ),
    )
    for name in policy.dao_fragment_names:
        select(ExchangePriorityId.DAO_FRAGMENT, by_name.get(name, ()))
    select(ExchangePriorityId.CURRENT_PRAYER, by_name.get(current_resource, ()))
    locked: list[ShopItemLike] = []
    if next_resource:
        next_rows = list(by_name.get(next_resource, ()))
        select(ExchangePriorityId.NEXT_PRAYER, next_rows)
        locked.extend(next_rows)

    excluded_reservations = {current_resource, next_resource}
    card_mail_resource = next(
        (
            name
            for name in policy.prayer_resource_priority
            if name not in excluded_reservations and by_name.get(name)
        ),
        None,
    )
    if card_mail_resource:
        card_rows = list(by_name[card_mail_resource])
        select(ExchangePriorityId.CARD_MAIL, card_rows)
        locked.extend(card_rows)

    for name in policy.prayer_resource_priority:
        select(ExchangePriorityId.PRAYER_RESOURCE, by_name.get(name, ()))
    for name in policy.equipment_resource_names:
        select(ExchangePriorityId.RESOURCE, by_name.get(name, ()))
    select(
        ExchangePriorityId.PARTNER_ROOT,
        (
            item
            for item in rows
            if int(item.item_id) in policy.partner_root_item_ids
            and str(item.name or "").strip() in policy.partner_root_names
        ),
    )

    lowest_discount, other_discount = _discounted_item_groups(rows)
    select(ExchangePriorityId.LOWEST_DISCOUNT, lowest_discount)
    select(ExchangePriorityId.OTHER_DISCOUNT, other_discount)

    closing_names = set(policy.closing_goods_names)
    select(
        ExchangePriorityId.ORDERED_GOODS,
        (
            item
            for item in rows
            if item.purchase_limit >= 0
            and str(item.name or "").strip() not in closing_names
        ),
    )
    for name in policy.closing_goods_names:
        select(
            ExchangePriorityId.CLOSING_GOODS,
            (item for item in by_name.get(name, ()) if item.purchase_limit >= 0),
        )
    for name in policy.overflow_names:
        select(
            ExchangePriorityId.OVERFLOW_PILL,
            (item for item in by_name.get(name, ()) if item.purchase_limit < 0),
        )
    select(
        ExchangePriorityId.NOT_NEEDED,
        (item for item in rows if int(item.goods_id) not in selected_ids),
    )

    # “不需要领”保留在档次表中供页面展示，但不能进入实际领取顺序。
    locked_unique: list[ShopItemLike] = []
    for item in locked:
        if item not in locked_unique:
            locked_unique.append(item)
    locked_unique = locked_unique[:2]
    locked_id_set = {int(item.goods_id) for item in locked_unique}
    ordered = [
        item
        for semantic_id in EXCHANGE_PRIORITY_ORDER
        if semantic_id != ExchangePriorityId.NOT_NEEDED
        for item in groups[semantic_id]
    ]

    def total_tokens(targets: Iterable[ShopItemLike]) -> int:
        return sum(
            item.token_cost * item.purchase_limit
            for item in targets
            if item.purchase_limit >= 0
        )

    def remaining_tokens(targets: Iterable[ShopItemLike]) -> int:
        return sum(
            item.token_cost * max(0, item.purchase_limit - item.purchased_count)
            for item in targets
            if item.purchase_limit >= 0
        )

    locked_total_tokens = total_tokens(locked_unique)
    locked_reserved_tokens = remaining_tokens(locked_unique)
    target_goods: dict[str, tuple[int, ...]] = {}
    target_totals: dict[str, int] = {}
    target_remaining: dict[str, int] = {}
    for target_id in (
        ExchangePriorityId.DAIER,
        ExchangePriorityId.OTHER_DISCOUNT,
        ExchangePriorityId.CLOSING_GOODS,
    ):
        through: list[ShopItemLike] = []
        for semantic_id in EXCHANGE_PRIORITY_ORDER:
            through.extend(
                item
                for item in groups[semantic_id]
                if item.purchase_limit >= 0
                and int(item.goods_id) not in locked_id_set
            )
            if semantic_id == target_id:
                break
        target_goods[str(target_id)] = tuple(int(item.goods_id) for item in through)
        # 黛儿是仙缘夺魁的独立止损目标；后续两项锁定不能反向抬高它。
        reserve_total = (
            0 if target_id == ExchangePriorityId.DAIER else locked_total_tokens
        )
        reserve_remaining = (
            0 if target_id == ExchangePriorityId.DAIER else locked_reserved_tokens
        )
        target_totals[str(target_id)] = total_tokens(through) + reserve_total
        target_remaining[str(target_id)] = (
            remaining_tokens(through) + reserve_remaining
        )

    closing_target_ids = set(target_goods[str(ExchangePriorityId.CLOSING_GOODS)])
    closing_rows = [
        item
        for item in ordered
        if item.purchase_limit >= 0
        and int(item.goods_id) not in locked_id_set
        and int(item.goods_id) in closing_target_ids
    ]
    closing_complete = bool(closing_rows) and all(
        item.purchased_count >= item.purchase_limit for item in closing_rows
    )
    card_mail_reserved_tokens = remaining_tokens(
        by_name.get(card_mail_resource or "", ())
    )

    return ExchangeShopPlan(
        policy_schema=policy.schema,
        priority_order_ids=tuple(str(item) for item in EXCHANGE_PRIORITY_ORDER),
        priority_group_goods_ids={
            str(semantic_id): tuple(int(item.goods_id) for item in groups[semantic_id])
            for semantic_id in EXCHANGE_PRIORITY_ORDER
        },
        ordered_goods_ids=tuple(int(item.goods_id) for item in ordered),
        locked_goods_ids=tuple(int(item.goods_id) for item in locked_unique),
        current_prayer_cycle=current_week.name,
        current_prayer_resource=current_resource,
        planning_date=effective_planning_date.isoformat(),
        next_prayer_resource=next_resource,
        card_mail_resource=card_mail_resource,
        activity_page_close_at=(
            close_moment.isoformat(timespec="seconds") if close_moment else None
        ),
        next_prayer_cutoff_at=next_prayer_cutoff.isoformat(timespec="seconds"),
        activity_page_closes_after_next_prayer_cutoff=closes_after_cutoff,
        target_goods_ids=target_goods,
        target_total_tokens=target_totals,
        target_remaining_tokens=target_remaining,
        closing_goods_items_complete=closing_complete,
        card_mail_reserved_tokens=card_mail_reserved_tokens,
        locked_reserved_tokens=locked_reserved_tokens,
    )


__all__ = [
    "ALCHEMY_SCRAP_BOX_SUFFIX",
    "DAO_FRAGMENT",
    "DAO_FRAGMENT_EDGE",
    "DAO_FRAGMENT_NAMES",
    "DEFAULT_EXCHANGE_SHOP_PRIORITY_POLICY",
    "EQUIPMENT_IRON_BOX",
    "EXCHANGE_PRIORITY_ORDER",
    "ExchangePriorityId",
    "ExchangeShopPlan",
    "ExchangeShopFundingStatus",
    "ExchangeShopPriorityPolicy",
    "OVERFLOW_ITEMS",
    "PARTNER_ROOT_ITEM_IDS",
    "PARTNER_ROOT_NAMES",
    "PRAYER_RESOURCE_BY_CYCLE",
    "PRAYER_RESOURCE_PRIORITY",
    "TEACHING_JADE",
    "XIANYUAN_DUOKUI_DISCOUNTED_DAIER",
    "XIANYUAN_DUOKUI_EXCHANGE_SHOP_PRIORITY_POLICY",
    "build_exchange_shop_plan",
    "exchange_shop_priority_policy",
    "exchange_shop_funding_status",
]
