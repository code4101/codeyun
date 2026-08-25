from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
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


@dataclass(frozen=True)
class ExchangeShopPriorityPolicy:
    """Declarative policy: stable layers plus small ordered tuning points."""

    schema: int
    prayer_resource_by_cycle: Mapping[str, str]
    prayer_resource_priority: tuple[str, ...]
    equipment_resource_names: tuple[str, ...]
    dao_fragment_names: tuple[str, ...]
    partner_root_item_ids: frozenset[int]
    partner_root_names: tuple[str, ...]
    discounted_book_suffixes: tuple[str, ...]
    stage9_front_suffixes: tuple[str, ...]
    stage9_tail_names: tuple[str, ...]
    overflow_names: tuple[str, ...]


DEFAULT_EXCHANGE_SHOP_PRIORITY_POLICY = ExchangeShopPriorityPolicy(
    schema=1,
    prayer_resource_by_cycle=PRAYER_RESOURCE_BY_CYCLE,
    prayer_resource_priority=PRAYER_RESOURCE_PRIORITY,
    equipment_resource_names=(EQUIPMENT_IRON_BOX,),
    dao_fragment_names=(DAO_FRAGMENT,),
    partner_root_item_ids=PARTNER_ROOT_ITEM_IDS,
    partner_root_names=PARTNER_ROOT_NAMES,
    discounted_book_suffixes=("残页", "残篇"),
    stage9_front_suffixes=(ALCHEMY_SCRAP_BOX_SUFFIX,),
    stage9_tail_names=(TEACHING_JADE, "灵根补全自选匣"),
    overflow_names=OVERFLOW_ITEMS,
)


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
    ordered_goods_ids: tuple[int, ...]
    locked_goods_ids: tuple[int, ...]
    current_prayer_cycle: str
    current_prayer_resource: str
    planning_date: str
    next_prayer_resource: str | None
    card_mail_resource: str | None
    activity_ends_on_sunday: bool
    discounted_book_goods_ids: tuple[int, ...]
    stage8_goods_ids: tuple[int, ...]
    stage9_goods_ids: tuple[int, ...]
    stage8_total_tokens: int
    stage8_remaining_tokens: int
    stage9_total_tokens: int
    stage9_remaining_tokens: int
    stage9_complete: bool
    card_mail_reserved_tokens: int
    locked_reserved_tokens: int


@dataclass(frozen=True)
class ExchangeShopFundingStatus:
    stage: int
    current_tokens: int
    remaining_tokens: int
    additional_tokens_required: int
    funded: bool


def exchange_shop_funding_status(
    plan: ExchangeShopPlan,
    *,
    current_tokens: int,
    stage: int = 9,
) -> ExchangeShopFundingStatus:
    """Report the additional currency needed for one policy stage."""

    if int(current_tokens) < 0:
        raise ValueError("current_tokens 不能为负数")
    if int(stage) == 8:
        remaining = int(plan.stage8_remaining_tokens)
    elif int(stage) == 9:
        remaining = int(plan.stage9_remaining_tokens)
    else:
        raise ValueError("仅支持兑换策略第8层或第9层")
    shortfall = max(0, remaining - int(current_tokens))
    return ExchangeShopFundingStatus(
        stage=int(stage),
        current_tokens=int(current_tokens),
        remaining_tokens=remaining,
        additional_tokens_required=shortfall,
        funded=shortfall == 0,
    )


def _activity_end_moment(end_date: str) -> datetime:
    return datetime.combine(
        datetime.fromisoformat(end_date).date(),
        time(hour=12),
        tzinfo=ZoneInfo("Asia/Shanghai"),
    )


def _is_discounted_book(
    item: ShopItemLike,
    policy: ExchangeShopPriorityPolicy,
) -> bool:
    name = str(item.name or "").strip()
    return (
        item.purchase_limit >= 0
        and item.discount is not None
        and int(item.discount) < 100
        and name.endswith(policy.discounted_book_suffixes)
    )


def _ordered_discounted_books(
    items: Iterable[ShopItemLike],
    policy: ExchangeShopPriorityPolicy,
) -> list[ShopItemLike]:
    """Order discounted book rows in a fair first pass, then by discount.

    Every book gets its lowest-price row before any book gets a second row.
    Remaining discounted rows then follow discount strength and GUI order.
    Full-price rows stay in the later source-ordered limited tier.
    """

    grouped: dict[int, list[ShopItemLike]] = {}
    for item in items:
        if not _is_discounted_book(item, policy):
            continue
        grouped.setdefault(int(item.item_id), []).append(item)
    for rows in grouped.values():
        rows.sort(
            key=lambda row: (
                int(row.discount or 100),
                row.source_order,
                row.goods_id,
            )
        )
    first_pass = sorted(
        (rows[0] for rows in grouped.values()),
        key=lambda row: (row.source_order, row.goods_id),
    )
    remaining = sorted(
        (row for rows in grouped.values() for row in rows[1:]),
        key=lambda row: (
            int(row.discount or 100),
            row.source_order,
            row.goods_id,
        ),
    )
    return [*first_pass, *remaining]


def build_exchange_shop_plan(
    items: Iterable[ShopItemLike],
    *,
    activity_end_date: str,
    planning_date: str | date | None = None,
    policy: ExchangeShopPriorityPolicy = DEFAULT_EXCHANGE_SHOP_PRIORITY_POLICY,
) -> ExchangeShopPlan:
    """Calculate the shared limited-activity exchange order.

    ``planning_date`` determines the current prayer week while the activity
    is live. Callers clamp a post-activity grace-period refresh to the end
    date, so Monday does not reinterpret a Sunday-ending occurrence.
    Unknown goods remain in the source-ordered limited tier; evidence-poor
    unlimited goods are excluded instead of guessed into the overflow tier.
    """

    rows = sorted(items, key=lambda row: (row.source_order, row.goods_id))
    by_name: dict[str, list[ShopItemLike]] = {}
    for item in rows:
        by_name.setdefault(str(item.name or "").strip(), []).append(item)

    end_moment = _activity_end_moment(activity_end_date)
    effective_planning_date = (
        planning_date
        if isinstance(planning_date, date)
        else datetime.fromisoformat(str(planning_date)).date()
        if planning_date is not None
        else end_moment.date()
    )
    planning_moment = datetime.combine(
        effective_planning_date,
        time(hour=12),
        tzinfo=ZoneInfo("Asia/Shanghai"),
    )
    current_week = prayer_cycle_week(planning_moment)
    current_resource = policy.prayer_resource_by_cycle[current_week.name]
    ends_on_sunday = end_moment.weekday() == 6
    next_resource = (
        policy.prayer_resource_by_cycle[
            prayer_cycle_week(end_moment, offset_weeks=1).name
        ]
        if ends_on_sunday
        else None
    )

    ordered: list[ShopItemLike] = []
    selected_ids: set[int] = set()

    def append_rows(candidates: Iterable[ShopItemLike]) -> None:
        for candidate in candidates:
            if int(candidate.goods_id) in selected_ids:
                continue
            ordered.append(candidate)
            selected_ids.add(int(candidate.goods_id))

    append_rows(by_name.get(current_resource, ()))
    locked: list[ShopItemLike] = []
    if next_resource:
        next_rows = by_name.get(next_resource, ())
        append_rows(next_rows)
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
        card_rows = by_name[card_mail_resource]
        append_rows(card_rows)
        locked.extend(card_rows)

    for name in (
        *policy.prayer_resource_priority,
        *policy.equipment_resource_names,
    ):
        append_rows(by_name.get(name, ()))
    for name in policy.dao_fragment_names:
        append_rows(by_name.get(name, ()))
    append_rows(
        item
        for item in rows
        if int(item.item_id) in policy.partner_root_item_ids
        and str(item.name or "").strip() in policy.partner_root_names
    )

    discounted_books = _ordered_discounted_books(rows, policy)
    append_rows(discounted_books)

    stage9_front = [
        item
        for item in rows
        if item.purchase_limit >= 0
        and int(item.goods_id) not in selected_ids
        and str(item.name or "").strip().endswith(policy.stage9_front_suffixes)
    ]
    remaining_limited = [
        item
        for item in rows
        if item.purchase_limit >= 0
        and int(item.goods_id) not in selected_ids
        and str(item.name or "").strip() not in policy.stage9_tail_names
        and not str(item.name or "").strip().endswith(
            policy.stage9_front_suffixes
        )
    ]
    stage9_tail = [
        item
        for name in policy.stage9_tail_names
        for item in by_name.get(name, ())
        if item.purchase_limit >= 0 and int(item.goods_id) not in selected_ids
    ]
    stage9 = [*stage9_front, *remaining_limited, *stage9_tail]
    append_rows(stage9)

    for name in policy.overflow_names:
        append_rows(
            item for item in by_name.get(name, ()) if item.purchase_limit < 0
        )

    # The business contract permits at most two concrete locked goods rows.
    locked_unique: list[ShopItemLike] = []
    for item in locked:
        if item not in locked_unique:
            locked_unique.append(item)
    locked_unique = locked_unique[:2]

    locked_id_set = {int(item.goods_id) for item in locked_unique}
    stage8 = [
        item
        for item in ordered
        if item.purchase_limit >= 0
        and int(item.goods_id) not in {int(row.goods_id) for row in stage9}
        and int(item.goods_id) not in locked_id_set
    ]
    stage9_through = [*stage8, *stage9]

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

    stage9_complete = bool(stage9_through) and all(
        item.purchase_limit >= 0
        and item.purchased_count >= item.purchase_limit
        for item in stage9_through
    )
    card_mail_rows = by_name.get(card_mail_resource or "", ())
    card_mail_reserved_tokens = sum(
        max(0, item.purchase_limit - item.purchased_count) * item.token_cost
        for item in card_mail_rows
        if item.purchase_limit >= 0
    )
    locked_reserved_tokens = remaining_tokens(locked_unique)
    locked_total_tokens = total_tokens(locked_unique)
    return ExchangeShopPlan(
        policy_schema=policy.schema,
        ordered_goods_ids=tuple(int(item.goods_id) for item in ordered),
        locked_goods_ids=tuple(int(item.goods_id) for item in locked_unique),
        current_prayer_cycle=current_week.name,
        current_prayer_resource=current_resource,
        planning_date=effective_planning_date.isoformat(),
        next_prayer_resource=next_resource,
        card_mail_resource=card_mail_resource,
        activity_ends_on_sunday=ends_on_sunday,
        discounted_book_goods_ids=tuple(
            int(item.goods_id) for item in discounted_books
        ),
        stage8_goods_ids=tuple(int(item.goods_id) for item in stage8),
        stage9_goods_ids=tuple(int(item.goods_id) for item in stage9),
        # Locked goods are intentionally not redemption targets, but their
        # currency must remain funded for the post-activity mail strategy.
        stage8_total_tokens=total_tokens(stage8) + locked_total_tokens,
        stage8_remaining_tokens=(
            remaining_tokens(stage8) + locked_reserved_tokens
        ),
        stage9_total_tokens=total_tokens(stage9_through) + locked_total_tokens,
        stage9_remaining_tokens=(
            remaining_tokens(stage9_through) + locked_reserved_tokens
        ),
        stage9_complete=stage9_complete,
        card_mail_reserved_tokens=card_mail_reserved_tokens,
        locked_reserved_tokens=locked_reserved_tokens,
    )


__all__ = [
    "ALCHEMY_SCRAP_BOX_SUFFIX",
    "DAO_FRAGMENT",
    "DEFAULT_EXCHANGE_SHOP_PRIORITY_POLICY",
    "EQUIPMENT_IRON_BOX",
    "ExchangeShopPlan",
    "ExchangeShopFundingStatus",
    "ExchangeShopPriorityPolicy",
    "OVERFLOW_ITEMS",
    "PARTNER_ROOT_ITEM_IDS",
    "PARTNER_ROOT_NAMES",
    "PRAYER_RESOURCE_BY_CYCLE",
    "PRAYER_RESOURCE_PRIORITY",
    "TEACHING_JADE",
    "build_exchange_shop_plan",
    "exchange_shop_funding_status",
]
