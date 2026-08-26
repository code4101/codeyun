from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict
from datetime import date, datetime, time as datetime_time
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field as PydanticField
from sqlmodel import Session, col, select

from backend.models import (
    FanxiuExchangeActivity,
    FanxiuExchangeActivityObservation,
    FanxiuExchangeRanking,
    FanxiuExchangeShopItem,
)
from backend.core.fanxiu.activity.exchange_shop_planner import (
    build_exchange_shop_plan,
    exchange_shop_priority_policy,
)
from backend.core.fanxiu.activity.exchange_planning import (
    calculate_exchange_currency_gap,
)
from backend.core.fanxiu.catalog.server_mapping import (
    resolve_fanxiu_region_server_by_id,
)


TALENT_PILL_ITEM_ID = 9070095


class ExchangeActivitySummary(BaseModel):
    id: str
    label: str
    activity_type: str
    cross_count: int
    start_date: str
    end_date: str
    start_at: str
    end_at: str
    captured_at: str
    is_active: bool
    close_panel_date: str
    close_panel_at: str
    lifecycle_phase: str
    is_collectible: bool


class ExchangeShopItemView(BaseModel):
    id: str
    goods_id: int
    item_id: int
    source_order: int
    priority_order: int | None
    locked: bool
    name: str
    goods_num: int
    token_cost: int
    purchase_limit: int
    purchased_count: int
    row_total_tokens: int | None
    cumulative_tokens: int | None
    remaining_challenges: int | None = None
    discount: int | None
    original_price: int | None


class ExchangeActivityDetail(ExchangeActivitySummary):
    game_rank_activity_id: int | None
    game_shop_base_id: int | None
    currency_type: int | None
    currency_name: str
    current_currency: int
    cumulative_currency: int
    resource_strategy: dict[str, Any]
    source_kind: str
    yield_rate: None = None
    currency_fact_fresh: bool = True
    shop_fact_fresh: bool = True
    budget_ready: bool = True
    budget_block_reason: str = ""
    currency_captured_at: str = ""
    shop_snapshot_captured_at: str = ""
    shop_refresh_status: str = ""
    shop_refresh_reason: str = ""
    rankings_refresh_status: str = ""
    rankings_refresh_reason: str = ""
    exchange_plan: dict[str, Any] = PydanticField(default_factory=dict)
    shop_items: list[ExchangeShopItemView]


class ExchangeActivitySnapshot(BaseModel):
    activities: list[ExchangeActivitySummary]
    selected_activity: ExchangeActivityDetail | None


class LatestExchangeActivitySnapshot(BaseModel):
    activity_type: str | None = None
    snapshot: ExchangeActivitySnapshot | None = None


class ExchangeActivityObservationView(BaseModel):
    id: str
    activity_id: str
    captured_at: str
    lifecycle_phase: str
    snapshot_kind: str
    current_currency: int
    cumulative_currency: int
    shop_status: str
    rankings_status: str
    payload: dict[str, Any]


class ExchangeActivityObservationPage(BaseModel):
    items: list[ExchangeActivityObservationView]
    total: int


class ExchangePriorityUpdateRequest(BaseModel):
    ordered_goods_ids: list[int] = PydanticField(default_factory=list)


class ExchangeShopItemLockUpdateRequest(BaseModel):
    locked: bool


class ExchangeRankingView(BaseModel):
    id: str
    ranking_scope: str
    rank: int
    score: int
    name: str
    server_id: int | None
    server_name: str
    club_name: str
    is_self: bool
    is_reward_guard: bool
    reward_rank_start: int | None
    reward_rank_end: int | None
    talent_pill_count: int | None
    score_per_talent_pill: float | None
    has_player: bool
    is_last_player: bool
    captured_at: str
    subject: ExchangeRankingSubject | None = None


class ExchangeRankingSubject(BaseModel):
    kind: str
    id: str | None = None
    name: str = ""
    server_id: int | None = None
    server_name: str = ""
    members: list[dict[str, Any]] | None = None


class ExchangeRankingScope(BaseModel):
    key: str
    label: str
    role: str
    subject_kind: str


class ExchangeRankingPage(BaseModel):
    scope: ExchangeRankingScope
    view_mode: str
    page: int
    page_size: int
    total: int = PydanticField(description="Deprecated compatibility projection row count")
    items: list[ExchangeRankingView]
    entries: list[ExchangeRankingView]
    reward_tiers: list[ExchangeRankingView]
    entry_total: int
    declared_rank_count: int
    loaded_entry_count: int
    complete: bool
    self_entry: ExchangeRankingView | None
    last_entry: ExchangeRankingView | None
    last_captured_at: str


def format_exchange_activity_label(cross_count: int, start_date: str, end_date: str) -> str:
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    prefix = f"{int(cross_count)}跨,"
    start_text = f"{start.year}/{start.month}/{start.day}"
    if start == end:
        return prefix + start_text
    if start.year == end.year:
        return prefix + f"{start_text}-{end.month}/{end.day}"
    return prefix + f"{start_text}-{end.year}/{end.month}/{end.day}"


def is_exchange_activity_active(
    activity: FanxiuExchangeActivity,
    *,
    today: date | None = None,
) -> bool:
    current_day = today or date.today()
    return (
        date.fromisoformat(activity.start_date)
        <= current_day
        <= date.fromisoformat(activity.end_date)
    )


def exchange_activity_close_panel_date(activity: FanxiuExchangeActivity) -> date:
    return exchange_activity_close_panel_at(activity).date()


def exchange_activity_close_panel_at(activity: FanxiuExchangeActivity) -> datetime:
    evidence = dict(activity.evidence or {})
    raw_ms = evidence.get("period_close_panel_time")
    try:
        close_ms = int(raw_ms or 0)
    except (TypeError, ValueError):
        close_ms = 0
    if close_ms > 0:
        return datetime.fromtimestamp(close_ms / 1000).astimezone()
    raw = str(evidence.get("period_close_panel_date") or activity.end_date)
    try:
        close_day = date.fromisoformat(raw)
    except ValueError:
        close_day = date.fromisoformat(activity.end_date)
    close_day = max(date.fromisoformat(activity.end_date), close_day)
    return datetime.combine(close_day, datetime_time.max).astimezone()


def _exchange_activity_boundary_at(
    activity: FanxiuExchangeActivity,
    *,
    evidence_key: str,
    fallback_date: str,
    end_of_day: bool,
) -> datetime:
    evidence = dict(activity.evidence or {})
    try:
        epoch_ms = int(evidence.get(evidence_key) or 0)
    except (TypeError, ValueError):
        epoch_ms = 0
    if epoch_ms > 0:
        return datetime.fromtimestamp(epoch_ms / 1000).astimezone()
    boundary_time = datetime_time.max if end_of_day else datetime_time.min
    return datetime.combine(date.fromisoformat(fallback_date), boundary_time).astimezone()


def exchange_activity_lifecycle_phase(
    activity: FanxiuExchangeActivity,
    *,
    today: date | None = None,
    at: datetime | None = None,
) -> str:
    if today is not None and at is not None:
        raise ValueError("today 与 at 不能同时提供")
    current_moment = (at or datetime.now().astimezone()).astimezone()
    current_day = today or current_moment.date()
    start_day = date.fromisoformat(activity.start_date)
    end_day = date.fromisoformat(activity.end_date)
    close_day = exchange_activity_close_panel_date(activity)
    if today is None:
        start_at = _exchange_activity_boundary_at(
            activity,
            evidence_key="period_start_time",
            fallback_date=activity.start_date,
            end_of_day=False,
        )
        end_at = _exchange_activity_boundary_at(
            activity,
            evidence_key="period_end_time",
            fallback_date=activity.end_date,
            end_of_day=True,
        )
        if current_moment < start_at:
            return "scheduled"
        if current_moment <= end_at:
            return "active"
        if current_moment <= exchange_activity_close_panel_at(activity):
            return "settlement"
        return "closed"
    if current_day < start_day:
        return "scheduled"
    if current_day <= end_day:
        return "active"
    if current_day <= close_day and (
        today is not None or current_moment <= exchange_activity_close_panel_at(activity)
    ):
        return "settlement"
    return "closed"


def store_exchange_activity_observation(
    session: Session,
    *,
    activity: FanxiuExchangeActivity,
    captured_at: str,
    current_day: date,
    current_currency: int,
    cumulative_currency: int,
    shop_status: str,
    rankings_status: str,
    payload: dict[str, Any],
    snapshot_kind: str | None = None,
) -> str:
    """Persist one immutable, deduplicated occurrence observation."""

    try:
        captured_moment = datetime.fromisoformat(str(captured_at))
    except ValueError:
        captured_moment = None
    if captured_moment is not None and captured_moment.tzinfo is not None:
        phase = exchange_activity_lifecycle_phase(activity, at=captured_moment)
    else:
        phase = exchange_activity_lifecycle_phase(activity, today=current_day)
    kind = snapshot_kind or ("formal_end" if phase == "settlement" else "running")
    canonical = {
        "captured_at": str(captured_at),
        "lifecycle_phase": phase,
        "snapshot_kind": kind,
        "current_currency": int(current_currency),
        "cumulative_currency": int(cumulative_currency),
        "shop_status": str(shop_status),
        "rankings_status": str(rankings_status),
        "payload": payload,
    }
    fingerprint = hashlib.sha256(
        json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    existing = session.exec(
        select(FanxiuExchangeActivityObservation).where(
            FanxiuExchangeActivityObservation.activity_id == activity.id,
            FanxiuExchangeActivityObservation.fingerprint == fingerprint,
        )
    ).first()
    if existing is not None:
        return existing.id
    row = FanxiuExchangeActivityObservation(
        activity_id=activity.id,
        captured_at=str(captured_at),
        lifecycle_phase=phase,
        snapshot_kind=kind,
        current_currency=int(current_currency),
        cumulative_currency=int(cumulative_currency),
        shop_status=str(shop_status),
        rankings_status=str(rankings_status),
        fingerprint=fingerprint,
        payload=payload,
    )
    session.add(row)
    session.commit()
    return row.id


def list_exchange_activity_snapshot(
    session: Session, *, activity_type: str, activity_id: str | None = None
) -> ExchangeActivitySnapshot:
    activities = list(
        session.exec(
            select(FanxiuExchangeActivity)
            .where(FanxiuExchangeActivity.activity_type == activity_type)
            .order_by(
                col(FanxiuExchangeActivity.start_date).desc(),
                col(FanxiuExchangeActivity.end_date).desc(),
                col(FanxiuExchangeActivity.cross_count).desc(),
            )
        ).all()
    )
    selected = next((row for row in activities if row.id == activity_id), None) if activity_id else (activities[0] if activities else None)
    return ExchangeActivitySnapshot(
        activities=[_summary(row) for row in activities],
        selected_activity=_detail(session, selected) if selected else None,
    )


def list_exchange_activity_observations(
    session: Session,
    *,
    activity_type: str,
    activity_id: str,
) -> ExchangeActivityObservationPage:
    _get_activity(session, activity_type, activity_id)
    rows = list(
        session.exec(
            select(FanxiuExchangeActivityObservation)
            .where(FanxiuExchangeActivityObservation.activity_id == activity_id)
            .order_by(
                col(FanxiuExchangeActivityObservation.captured_at),
                col(FanxiuExchangeActivityObservation.created_at),
            )
        ).all()
    )
    return ExchangeActivityObservationPage(
        total=len(rows),
        items=[
            ExchangeActivityObservationView(
                id=row.id,
                activity_id=row.activity_id,
                captured_at=row.captured_at,
                lifecycle_phase=row.lifecycle_phase,
                snapshot_kind=row.snapshot_kind,
                current_currency=row.current_currency,
                cumulative_currency=row.cumulative_currency,
                shop_status=row.shop_status,
                rankings_status=row.rankings_status,
                payload=dict(row.payload or {}),
            )
            for row in rows
        ],
    )


def latest_exchange_activity_snapshot(
    session: Session,
    *,
    activity_types: list[str],
) -> LatestExchangeActivitySnapshot:
    """Resolve the newest persisted occurrence with one indexed DB query."""

    allowed = sorted({str(value).strip() for value in activity_types if str(value).strip()})
    if not allowed:
        return LatestExchangeActivitySnapshot()
    latest = session.exec(
        select(FanxiuExchangeActivity)
        .where(FanxiuExchangeActivity.activity_type.in_(allowed))
        .order_by(
            col(FanxiuExchangeActivity.start_date).desc(),
            col(FanxiuExchangeActivity.end_date).desc(),
            col(FanxiuExchangeActivity.captured_at).desc(),
            col(FanxiuExchangeActivity.updated_at).desc(),
        )
        .limit(1)
    ).first()
    if latest is None:
        return LatestExchangeActivitySnapshot()
    return LatestExchangeActivitySnapshot(
        activity_type=latest.activity_type,
        snapshot=ExchangeActivitySnapshot(
            activities=[_summary(latest)],
            selected_activity=_detail(session, latest),
        ),
    )
def update_exchange_priorities(
    session: Session, *, activity_type: str, activity_id: str, ordered_goods_ids: list[int]
) -> ExchangeActivityDetail:
    activity = _get_activity(session, activity_type, activity_id)
    goods_ids = [int(value) for value in ordered_goods_ids]
    if len(goods_ids) != len(set(goods_ids)):
        raise ValueError("兑换优先级中存在重复商品")
    items = _items(session, activity.id)
    known = {row.goods_id for row in items}
    unknown = next((goods_id for goods_id in goods_ids if goods_id not in known), None)
    if unknown is not None:
        raise ValueError(f"兑换优先级包含未知商品：{unknown}")
    priorities = {goods_id: index + 1 for index, goods_id in enumerate(goods_ids)}
    now = time.time()
    for item in items:
        item.priority_order = priorities.get(item.goods_id)
        item.updated_at = now
        session.add(item)
    activity.updated_at = now
    session.add(activity)
    session.commit()
    session.refresh(activity)
    return _detail(session, activity)


def update_exchange_shop_item_lock(
    session: Session, *, activity_type: str, activity_id: str, goods_id: int, locked: bool
) -> ExchangeActivityDetail:
    activity = _get_activity(session, activity_type, activity_id)
    item = session.exec(
        select(FanxiuExchangeShopItem).where(
            FanxiuExchangeShopItem.activity_id == activity.id,
            FanxiuExchangeShopItem.goods_id == int(goods_id),
        )
    ).first()
    if item is None:
        raise ValueError("兑换商品不存在")
    if locked and not item.locked:
        locked_count = sum(
            1
            for row in _items(session, activity.id)
            if row.locked and row.goods_id != item.goods_id
        )
        if locked_count >= 2:
            raise ValueError("兑换宝阁最多锁定两个商品行")
    item.locked = bool(locked)
    item.updated_at = time.time()
    session.add(item)
    session.commit()
    return _detail(session, activity)


def apply_exchange_shop_plan(
    session: Session,
    *,
    activity_type: str,
    activity_id: str,
) -> ExchangeActivityDetail:
    """Calculate and persist the shared exchange priority and reservation plan."""

    activity = _get_activity(session, activity_type, activity_id)
    items = _items(session, activity.id)
    if not items:
        raise ValueError("兑换宝阁尚无完整商品，无法计算优先级")
    _persist_exchange_shop_plan(session, activity=activity, items=items)
    session.commit()
    session.refresh(activity)
    return _detail(session, activity)


def _persist_exchange_shop_plan(
    session: Session,
    *,
    activity: FanxiuExchangeActivity,
    items: list[FanxiuExchangeShopItem],
) -> None:
    """Apply a plan inside the caller's transaction after a complete upsert."""

    today = date.today()
    planning_date = (
        today
        if date.fromisoformat(activity.start_date)
        <= today
        <= date.fromisoformat(activity.end_date)
        else date.fromisoformat(activity.end_date)
    )
    activity_evidence = dict(activity.evidence or {})
    raw_close_ms = (
        activity_evidence.get("period_close_panel_time")
        or activity_evidence.get("period_close_panel_time_ms")
    )
    try:
        exact_close_at = (
            datetime.fromtimestamp(
                int(raw_close_ms) / 1000,
                tz=ZoneInfo("Asia/Shanghai"),
            )
            if int(raw_close_ms or 0) > 0
            else None
        )
    except (TypeError, ValueError, OSError):
        exact_close_at = None
    plan = build_exchange_shop_plan(
        items,
        activity_end_date=activity.end_date,
        planning_date=planning_date,
        shop_close_at=exact_close_at,
        policy=exchange_shop_priority_policy(activity.activity_type),
    )
    priorities = {
        goods_id: index + 1
        for index, goods_id in enumerate(plan.ordered_goods_ids)
    }
    locked_ids = set(plan.locked_goods_ids)
    now = time.time()
    for item in items:
        item.priority_order = priorities.get(int(item.goods_id))
        item.locked = int(item.goods_id) in locked_ids
        item.updated_at = now
        session.add(item)

    universe = {
        (int(item_id), str(name or "").strip())
        for item_id, name in session.exec(
            select(FanxiuExchangeShopItem.item_id, FanxiuExchangeShopItem.name)
        ).all()
    }
    strategy = dict(activity.resource_strategy or {})
    strategy.pop("周日顺延预留", None)
    strategy.update(
        {
            "本周祈愿": f"{plan.current_prayer_cycle}（{plan.current_prayer_resource}最高优先）",
            "跨周祈愿预留": plan.next_prayer_resource or "无",
            "卡邮件预留": plan.card_mail_resource or "当前商店无候选",
            "溢出兑换": "玄灵丹·珍 > 玄灵丹·尚（不属于活动目标）",
        }
    )
    if activity.activity_type == "xianyuan-duokui" and plan.front_discounted_goods_ids:
        strategy["常规目标"] = "只生产足够兑换5折誓约·黛儿的1万夺魁灵玉；取得后停止"
        strategy["条件目标"] = "仅用自然多出的兑币顺吃后续商品，不为通用第8/9层继续加打"
    else:
        strategy.setdefault(
            "常规目标",
            "完成至各功法最低折扣轮次及其余折扣条目（第8层）",
        )
        strategy.setdefault("条件目标", "有条件完成全部限购物品（第9层）")
    activity.resource_strategy = strategy
    evidence = dict(activity.evidence or {})
    refresh_status = evidence.get("refresh_status")
    if isinstance(refresh_status, dict):
        currency_fresh = (
            refresh_status.get("currency") == "updated"
            and not bool(refresh_status.get("currency_stale"))
        )
        shop_fresh = refresh_status.get("shop") == "updated"
        budget_ready = bool(currency_fresh and shop_fresh)
        budget_block_reason = (
            ""
            if budget_ready
            else "钱包 amount/history 与购买进度不是同窗口最新 Runtime 事实"
        )
    elif str(activity.source_kind or "") == "read_only_runtime_facts":
        # Snapshots produced before the freshness envelope was introduced are
        # still real Runtime facts, not imported/manual fixtures.  Their value
        # may remain useful as a dated observation, but it cannot authorize a
        # current budget or purchase decision after the activity has moved on.
        currency_fresh = False
        shop_fresh = False
        budget_ready = False
        budget_block_reason = "旧版 Runtime 快照缺少钱包与购买进度的同窗口 freshness 证据"
    else:
        # Imported/manual fixtures have no Runtime freshness envelope. Keep
        # the generic planner usable; their source kind makes that provenance
        # explicit and prevents them from masquerading as a live Runtime read.
        currency_fresh = True
        shop_fresh = True
        budget_ready = True
        budget_block_reason = ""
    stage8_gap = calculate_exchange_currency_gap(
        target_total_tokens=plan.stage8_total_tokens,
        target_remaining_tokens=plan.stage8_remaining_tokens,
        current_currency=activity.current_currency,
        cumulative_currency=activity.cumulative_currency,
    )
    stage9_gap = calculate_exchange_currency_gap(
        target_total_tokens=plan.stage9_total_tokens,
        target_remaining_tokens=plan.stage9_remaining_tokens,
        current_currency=activity.current_currency,
        cumulative_currency=activity.cumulative_currency,
    )
    economical_uses_front_discount = bool(
        activity.activity_type == "xianyuan-duokui"
        and plan.front_discounted_goods_ids
    )
    economical_gap = calculate_exchange_currency_gap(
        target_total_tokens=(
            plan.front_discounted_total_tokens
            if economical_uses_front_discount
            else plan.stage8_total_tokens
        ),
        target_remaining_tokens=(
            plan.front_discounted_remaining_tokens
            if economical_uses_front_discount
            else plan.stage8_remaining_tokens
        ),
        current_currency=activity.current_currency,
        cumulative_currency=activity.cumulative_currency,
    )
    locked_reserve_funded = (
        int(activity.current_currency) >= int(plan.locked_reserved_tokens)
    )
    stage9_goal_complete = bool(
        budget_ready
        and plan.stage9_complete
        and locked_reserve_funded
        and int(activity.cumulative_currency) >= int(plan.stage9_total_tokens)
    )
    evidence["exchange_plan"] = {
        "schema": plan.policy_schema,
        "ordered_goods_ids": list(plan.ordered_goods_ids),
        "locked_goods_ids": list(plan.locked_goods_ids),
        "current_prayer_cycle": plan.current_prayer_cycle,
        "current_prayer_resource": plan.current_prayer_resource,
        "planning_date": plan.planning_date,
        "shop_close_at": plan.shop_close_at,
        "weekly_rollover_at": plan.weekly_rollover_at,
        "shop_closes_after_weekly_rollover": (
            plan.shop_closes_after_weekly_rollover
        ),
        "next_prayer_resource": plan.next_prayer_resource,
        "card_mail_resource": plan.card_mail_resource,
        "card_mail_reserved_tokens": plan.card_mail_reserved_tokens,
        "locked_reserved_tokens": plan.locked_reserved_tokens,
        "front_discounted_goods_ids": list(plan.front_discounted_goods_ids),
        "economical_target": (
            "front_discounted" if economical_uses_front_discount else "stage8"
        ),
        "economical_budget": asdict(economical_gap),
        "discounted_book_goods_ids": list(plan.discounted_book_goods_ids),
        "stage8_goods_ids": list(plan.stage8_goods_ids),
        "stage9_goods_ids": list(plan.stage9_goods_ids),
        "stage8_budget": asdict(stage8_gap),
        "stage9_budget": asdict(stage9_gap),
        "budget_ready": budget_ready,
        "budget_block_reason": budget_block_reason,
        "currency_fact_fresh": currency_fresh,
        "shop_fact_fresh": shop_fresh,
        "stage9_items_complete": plan.stage9_complete,
        "locked_reserve_funded": locked_reserve_funded,
        "stage9_complete": stage9_goal_complete,
        "card_mail_close_action": (
            "leave_for_mail"
            if stage9_goal_complete and plan.card_mail_resource
            else "redeem_during_grace_period"
        ),
        "observed_item_universe_count": len(universe),
    }
    activity.evidence = evidence
    activity.updated_at = now
    session.add(activity)


def list_exchange_rankings(
    session: Session,
    *,
    activity_type: str,
    activity_id: str,
    ranking_scope: str,
    page: int = 1,
    page_size: int = 20,
) -> ExchangeRankingPage:
    from backend.core.fanxiu.activity.exchange_activity_registry import (
        get_exchange_activity_spec,
    )

    activity = _get_activity(session, activity_type, activity_id)
    scope = str(ranking_scope or "personal").lower()
    activity_spec = get_exchange_activity_spec(activity_type)
    scope_spec = next(
        (item for item in activity_spec.rank_scopes if item.scope == scope),
        None,
    )
    if scope_spec is None or scope not in activity_spec.page.ranking_scopes:
        raise ValueError("未知榜单范围")
    rows = list(
        session.exec(
            select(FanxiuExchangeRanking)
            .where(
                FanxiuExchangeRanking.activity_id == activity.id,
                FanxiuExchangeRanking.ranking_scope == scope,
            )
            .order_by(col(FanxiuExchangeRanking.rank), col(FanxiuExchangeRanking.name))
        ).all()
    )
    entries = [
        _ranking_view(row, subject_kind=scope_spec.subject)
        for row in rows if row.has_player
    ]
    rank_activity_ids = dict((activity.evidence or {}).get("rank_scope_activity_ids") or {})
    rank_activity_id = rank_activity_ids.get(scope)
    if rank_activity_id is None and scope_spec.effective_role == "primary":
        rank_activity_id = activity.game_rank_activity_id
    tiers: list[dict[str, Any]] = []
    if scope_spec.reward_tiers_enabled and rank_activity_id:
        from backend.core.fanxiu.activity.rank_reward_context import (
            resolve_exchange_rank_reward_context,
        )
        from backend.core.fanxiu.activity.yunmeng_rank_reward import (
            load_yunmeng_rank_reward_tiers,
        )

        reward_context = resolve_exchange_rank_reward_context(session, activity)
        tiers = load_yunmeng_rank_reward_tiers(
            rank_activity_id=int(rank_activity_id),
            event_date=activity.start_date,
            server_day=reward_context["server_day"],
            world_level=reward_context["world_level"],
        )
    from backend.core.fanxiu.activity.ranking_key_points import (
        project_ranking_key_points,
        rank_reward_item_count,
    )

    def tier_placeholder(start: int, end: int, pill_count: int) -> ExchangeRankingView:
        return ExchangeRankingView(
            id=f"{activity.id}:{scope}:tier:{start}-{end}",
            ranking_scope=scope, rank=end, score=0, name="",
            server_id=None, server_name="", club_name="", is_self=False,
            is_reward_guard=True, reward_rank_start=start,
            reward_rank_end=end, talent_pill_count=pill_count,
            score_per_talent_pill=None, has_player=False,
            is_last_player=False, captured_at="", subject=None,
        )

    projected = project_ranking_key_points(
        entries,
        reward_tiers=tiers,
        reward_count=lambda tier: rank_reward_item_count(
            tier,
            item_id=TALENT_PILL_ITEM_ID,
        ),
        placeholder_factory=tier_placeholder,
        # Primary boards historically exposed reward guards/self/last through
        # ``items``. Comparative boards exposed their full observed entities;
        # keep both legacy behaviours while canonical ``entries`` is always
        # the complete real-entity collection.
        retain_non_key_rows=scope_spec.effective_role == "comparative",
        include_placeholders=scope_spec.effective_role == "primary",
    )
    items = projected
    reward_tier_items = [
        tier_placeholder(
            int(tier["rank_start"]),
            int(tier["rank_end"]),
            rank_reward_item_count(tier, item_id=TALENT_PILL_ITEM_ID),
        )
        for tier in tiers
    ]
    items.sort(
        key=lambda item: (
            item.rank <= 0,
            item.rank if item.rank > 0 else 0,
            not item.is_reward_guard,
            item.name,
        )
    )
    page = max(1, int(page))
    page_size = min(100, max(1, int(page_size)))
    start = (page - 1) * page_size
    declared_counts = [
        int((row.raw_data or {}).get("reported_rank_list_size") or 0)
        for row in rows
    ]
    declared_count = max(declared_counts, default=0)
    reported_loaded_counts = [
        int((row.raw_data or {}).get("loaded_player_count") or 0)
        for row in rows
        if "loaded_player_count" in (row.raw_data or {})
    ]
    loaded_count = max(reported_loaded_counts, default=len(entries))
    reported_completeness = [
        bool((row.raw_data or {}).get("scope_complete"))
        for row in rows
        if "scope_complete" in (row.raw_data or {})
    ]
    complete = (
        all(reported_completeness)
        if reported_completeness
        else declared_count > 0 and loaded_count >= declared_count
    )
    self_entry = next((item for item in entries if item.is_self), None)
    last_entry = next((item for item in entries if item.is_last_player), None)
    return ExchangeRankingPage(
        scope=ExchangeRankingScope(
            key=scope,
            label=scope_spec.effective_label,
            role=scope_spec.effective_role,
            subject_kind=scope_spec.subject,
        ),
        view_mode=scope_spec.row_mode,
        page=page,
        page_size=page_size,
        total=len(items),
        items=items[start : start + page_size],
        entries=entries[start : start + page_size],
        reward_tiers=reward_tier_items,
        entry_total=len(entries),
        declared_rank_count=declared_count,
        loaded_entry_count=loaded_count,
        complete=complete,
        self_entry=self_entry,
        last_entry=last_entry,
        last_captured_at=max((item.captured_at for item in items if item.captured_at), default=""),
    )


def replace_exchange_rankings(
    session: Session,
    *,
    activity_type: str,
    activity_id: str,
    rows: list[dict[str, Any]],
    captured_at: str,
) -> None:
    activity = _get_activity(session, activity_type, activity_id)
    for row in session.exec(
        select(FanxiuExchangeRanking).where(FanxiuExchangeRanking.activity_id == activity.id)
    ).all():
        session.delete(row)
    # Flush deletions before inserting the replacement snapshot. Otherwise
    # SQLite can evaluate INSERTs first and hit the activity/scope/rank/key
    # unique constraint when an activity is refreshed for the second time.
    session.flush()
    now = time.time()
    for raw in rows:
        session.add(
            FanxiuExchangeRanking(
                activity_id=activity.id,
                ranking_scope=str(raw.get("ranking_scope") or "personal"),
                rank=int(raw.get("rank") or 0),
                score=int(raw.get("score") or 0),
                role_key=str(raw.get("role_key") or raw.get("name") or raw.get("rank") or ""),
                name=str(raw.get("name") or ""),
                server_id=raw.get("server_id"),
                server_name=str(raw.get("server_name") or ""),
                club_name=str(raw.get("club_name") or ""),
                is_self=bool(raw.get("is_self")),
                is_reward_guard=bool(raw.get("is_reward_guard")),
                is_last_player=bool(raw.get("is_last_player")),
                has_player=bool(raw.get("has_player", True)),
                reward_rank_start=raw.get("reward_rank_start"),
                reward_rank_end=raw.get("reward_rank_end"),
                captured_at=captured_at,
                raw_data=dict(raw.get("raw_data") or {}),
                updated_at=now,
            )
        )
    activity.captured_at = captured_at
    activity.updated_at = now
    session.add(activity)
    session.commit()


def upsert_exchange_activity_snapshot(session: Session, payload: dict[str, Any]) -> str:
    """Agent-only persistence entry for a complete, validated exchange snapshot."""

    activity_type = str(payload["activity_type"]).strip()
    if not activity_type:
        raise ValueError("活动类型不能为空")
    has_shop_items = "shop_items" in payload
    shop_items = list(payload.get("shop_items") or [])
    if has_shop_items:
        goods_ids = [int(item["goods_id"]) for item in shop_items]
        if len(goods_ids) != len(set(goods_ids)):
            raise ValueError("兑换宝阁快照包含重复商品")
        expected_count = payload.get("expected_shop_item_count")
        if expected_count is not None and len(shop_items) != int(expected_count):
            raise ValueError(f"兑换宝阁快照不完整：期望 {int(expected_count)} 项，实际 {len(shop_items)} 项")
    cross_count = int(payload["cross_count"])
    start_date = str(payload["start_date"])
    end_date = str(payload.get("end_date") or start_date)
    date.fromisoformat(start_date)
    date.fromisoformat(end_date)
    activity = session.exec(
        select(FanxiuExchangeActivity).where(
            FanxiuExchangeActivity.activity_type == activity_type,
            FanxiuExchangeActivity.cross_count == cross_count,
            FanxiuExchangeActivity.start_date == start_date,
            FanxiuExchangeActivity.end_date == end_date,
        )
    ).first()
    if activity is None:
        same_start = list(
            session.exec(
                select(FanxiuExchangeActivity).where(
                    FanxiuExchangeActivity.activity_type == activity_type,
                    FanxiuExchangeActivity.cross_count == cross_count,
                    FanxiuExchangeActivity.start_date == start_date,
                )
            ).all()
        )
        activity = same_start[0] if len(same_start) == 1 else None
    if activity is None:
        activity = FanxiuExchangeActivity(
            id=f"{activity_type}-{cross_count}-{start_date}-{end_date}",
            activity_type=activity_type,
            cross_count=cross_count,
            start_date=start_date,
            end_date=end_date,
        )
    else:
        activity.end_date = end_date
    now = time.time()
    for field in (
        "game_rank_activity_id", "game_shop_base_id", "currency_type", "currency_name",
        "current_currency", "cumulative_currency", "captured_at", "source_kind",
    ):
        if field in payload:
            setattr(activity, field, payload[field])
    if "resource_strategy" in payload:
        activity.resource_strategy = dict(payload.get("resource_strategy") or {})
    if "evidence" in payload:
        activity.evidence = dict(payload.get("evidence") or {})
    activity.updated_at = now
    session.add(activity)
    session.flush()
    if has_shop_items:
        existing = {row.goods_id: row for row in _items(session, activity.id)}
        seen: set[int] = set()
        for fallback_order, raw in enumerate(shop_items, start=1):
            goods_id = int(raw["goods_id"])
            seen.add(goods_id)
            item = existing.get(goods_id) or FanxiuExchangeShopItem(
                activity_id=activity.id, goods_id=goods_id, item_id=int(raw["item_id"])
            )
            for field, default in (
                ("item_id", 0), ("source_order", fallback_order), ("goods_num", 1),
                ("token_cost", 0), ("purchase_limit", 0), ("purchased_count", 0),
            ):
                setattr(item, field, int(raw.get(field) if raw.get(field) is not None else default))
            item.name = str(raw.get("name") or "")
            item.discount = raw.get("discount")
            item.original_price = raw.get("original_price")
            item.show_limit = str(raw.get("show_limit") or "")
            item.disappear_limit = str(raw.get("disappear_limit") or "")
            item.raw_data = dict(raw.get("raw_data") or {})
            item.updated_at = now
            session.add(item)
        for goods_id, item in existing.items():
            if goods_id not in seen:
                session.delete(item)
    session.flush()
    persisted_items = _items(session, activity.id)
    if persisted_items:
        # Currency/ranking-only refreshes may replace resource_strategy and
        # evidence without sending shop_items again. Recompute from the last
        # complete persisted shop so the dynamic policy cannot silently vanish.
        _persist_exchange_shop_plan(
            session,
            activity=activity,
            items=persisted_items,
        )
    session.commit()
    return activity.id


def _get_activity(session: Session, activity_type: str, activity_id: str) -> FanxiuExchangeActivity:
    activity = session.get(FanxiuExchangeActivity, activity_id)
    if activity is None or activity.activity_type != activity_type:
        raise ValueError("活动不存在")
    return activity


def _items(session: Session, activity_id: str) -> list[FanxiuExchangeShopItem]:
    return list(
        session.exec(
            select(FanxiuExchangeShopItem)
            .where(FanxiuExchangeShopItem.activity_id == activity_id)
            .order_by(col(FanxiuExchangeShopItem.source_order), col(FanxiuExchangeShopItem.goods_id))
        ).all()
    )


def _summary(activity: FanxiuExchangeActivity) -> ExchangeActivitySummary:
    start_at = _exchange_activity_boundary_at(
        activity,
        evidence_key="period_start_time",
        fallback_date=activity.start_date,
        end_of_day=False,
    )
    end_at = _exchange_activity_boundary_at(
        activity,
        evidence_key="period_end_time",
        fallback_date=activity.end_date,
        end_of_day=True,
    )
    close_panel_at = exchange_activity_close_panel_at(activity)
    close_panel_date = close_panel_at.date().isoformat()
    lifecycle_phase = exchange_activity_lifecycle_phase(activity)
    return ExchangeActivitySummary(
        id=activity.id,
        label=format_exchange_activity_label(activity.cross_count, activity.start_date, activity.end_date),
        activity_type=activity.activity_type,
        cross_count=activity.cross_count,
        start_date=activity.start_date,
        end_date=activity.end_date,
        start_at=start_at.isoformat(timespec="seconds"),
        end_at=end_at.isoformat(timespec="seconds"),
        captured_at=activity.captured_at,
        is_active=is_exchange_activity_active(activity),
        close_panel_date=close_panel_date,
        close_panel_at=close_panel_at.isoformat(timespec="seconds"),
        lifecycle_phase=lifecycle_phase,
        is_collectible=lifecycle_phase in {"active", "settlement"},
    )


def _detail(session: Session, activity: FanxiuExchangeActivity) -> ExchangeActivityDetail:
    items = _items(session, activity.id)
    evidence = dict(activity.evidence or {})
    exchange_plan = evidence.get("exchange_plan")
    exchange_plan = exchange_plan if isinstance(exchange_plan, dict) else {}
    refresh_status = evidence.get("refresh_status")
    refresh_status = refresh_status if isinstance(refresh_status, dict) else {}
    cumulative_by_id: dict[str, int | None] = {}
    cumulative: int | None = 0
    for item in sorted((row for row in items if row.priority_order is not None), key=lambda row: (int(row.priority_order or 0), row.source_order)):
        cumulative = None if cumulative is None or item.purchase_limit < 0 else cumulative + item.token_cost * item.purchase_limit
        cumulative_by_id[item.id] = cumulative
    return ExchangeActivityDetail(
        **_summary(activity).model_dump(),
        game_rank_activity_id=activity.game_rank_activity_id,
        game_shop_base_id=activity.game_shop_base_id,
        currency_type=activity.currency_type,
        currency_name=activity.currency_name,
        current_currency=activity.current_currency,
        cumulative_currency=activity.cumulative_currency,
        resource_strategy=dict(activity.resource_strategy or {}),
        source_kind=activity.source_kind,
        currency_fact_fresh=bool(exchange_plan.get("currency_fact_fresh", True)),
        shop_fact_fresh=bool(exchange_plan.get("shop_fact_fresh", True)),
        budget_ready=bool(exchange_plan.get("budget_ready", True)),
        budget_block_reason=str(exchange_plan.get("budget_block_reason") or ""),
        currency_captured_at=str(refresh_status.get("currency_captured_at") or ""),
        shop_snapshot_captured_at=str(evidence.get("shop_snapshot_captured_at") or ""),
        shop_refresh_status=str(refresh_status.get("shop") or ""),
        shop_refresh_reason=str(refresh_status.get("shop_reason") or ""),
        rankings_refresh_status=str(refresh_status.get("rankings") or ""),
        rankings_refresh_reason=str(refresh_status.get("rankings_reason") or ""),
        exchange_plan=exchange_plan,
        shop_items=[
            ExchangeShopItemView(
                id=item.id, goods_id=item.goods_id, item_id=item.item_id,
                source_order=item.source_order, priority_order=item.priority_order,
                locked=item.locked, name=item.name, goods_num=item.goods_num,
                token_cost=item.token_cost, purchase_limit=item.purchase_limit,
                purchased_count=item.purchased_count,
                row_total_tokens=item.token_cost * item.purchase_limit if item.purchase_limit >= 0 else None,
                cumulative_tokens=cumulative_by_id.get(item.id), discount=item.discount,
                original_price=item.original_price,
            )
            for item in items
        ],
    )


def _ranking_view(
    row: FanxiuExchangeRanking,
    *,
    subject_kind: str = "role",
) -> ExchangeRankingView:
    pill_count = row.raw_data.get("talent_pill_count") if row.raw_data else None
    server_name = row.server_name
    if not server_name:
        resolved_server_name = str(
            resolve_fanxiu_region_server_by_id(row.server_id).get("server_name") or ""
        )
        server_name = (
            row.name or resolved_server_name
            if subject_kind == "server"
            else resolved_server_name
        )
    subject_name = server_name if subject_kind == "server" else row.name
    return ExchangeRankingView(
        id=row.id, ranking_scope=row.ranking_scope, rank=row.rank, score=row.score,
        name=subject_name, server_id=row.server_id, server_name=server_name,
        club_name=row.club_name, is_self=row.is_self,
        is_reward_guard=row.is_reward_guard,
        reward_rank_start=row.reward_rank_start,
        reward_rank_end=row.reward_rank_end,
        talent_pill_count=int(pill_count) if pill_count is not None else None,
        score_per_talent_pill=(row.score / int(pill_count) if row.has_player and pill_count else None),
        has_player=row.has_player, is_last_player=row.is_last_player,
        captured_at=row.captured_at,
        subject=ExchangeRankingSubject(
            kind=subject_kind,
            id=row.role_key,
            name=subject_name,
            server_id=row.server_id,
            server_name=server_name,
            members=(row.raw_data or {}).get("members"),
        ),
    )
