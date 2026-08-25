from __future__ import annotations

import time
from datetime import date
from typing import Any

from pydantic import BaseModel, Field as PydanticField
from sqlmodel import Session, col, select

from backend.models import (
    FanxiuYunmengTrialActivity,
    FanxiuYunmengTrialMeasurement,
    FanxiuYunmengTrialRanking,
    FanxiuYunmengTrialShopItem,
)
from backend.core.fanxiu.activity.exchange_planning import (
    ExchangeMeasurement,
    ExchangeYieldRate,
    estimate_remaining_attempts,
    latest_exchange_yield_rate,
)
from backend.core.fanxiu.activity.yunmeng_trial_instrumentation import (
    infer_yunmeng_cross_count,
)
from backend.core.fanxiu.activity.yunmeng_rank_reward import (
    YunmengRankRewardConfigError,
    load_yunmeng_rank_reward_tiers,
)


YUNMENG_TALENT_PILL_ITEM_ID = 9070095


class YunmengTrialActivitySummary(BaseModel):
    id: str
    label: str
    cross_count: int
    start_date: str
    end_date: str
    captured_at: str


class YunmengTrialShopItemView(BaseModel):
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
    remaining_challenges: int | None
    discount: int | None
    original_price: int | None


class YunmengTrialYieldRateView(BaseModel):
    sample_challenges: int
    average_score_per_100: float
    average_exchange_currency_per_100: float
    captured_at: str


class YunmengTrialActivityDetail(BaseModel):
    id: str
    label: str
    cross_count: int
    start_date: str
    end_date: str
    game_rank_activity_id: int | None
    game_shop_base_id: int | None
    currency_type: int | None
    current_currency: int
    cumulative_currency: int
    resource_strategy: dict[str, Any]
    captured_at: str
    source_kind: str
    yield_rate: YunmengTrialYieldRateView | None
    shop_items: list[YunmengTrialShopItemView]


class YunmengTrialSnapshotResponse(BaseModel):
    activities: list[YunmengTrialActivitySummary]
    selected_activity: YunmengTrialActivityDetail | None


class YunmengTrialPriorityUpdateRequest(BaseModel):
    ordered_goods_ids: list[int] = PydanticField(default_factory=list)


class YunmengTrialShopItemLockUpdateRequest(BaseModel):
    locked: bool


class YunmengTrialRankingView(BaseModel):
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
    talent_pill_count: int | None = None
    score_per_talent_pill: float | None = None
    has_player: bool = True
    is_last_player: bool = False
    captured_at: str = ""


class YunmengTrialRankingPage(BaseModel):
    page: int
    page_size: int
    total: int
    items: list[YunmengTrialRankingView]
    last_captured_at: str = ""


class YunmengTrialMeasurementCollectRequest(BaseModel):
    challenge_count_delta: int | None = PydanticField(default=None, ge=1)
    note: str = ""


class YunmengTrialMeasurementView(BaseModel):
    id: str
    captured_at: str
    score: int
    exchange_currency: int
    rank: int | None
    challenge_count_delta: int | None
    note: str
    source_kind: str


class YunmengTrialMeasurementPage(BaseModel):
    items: list[YunmengTrialMeasurementView]


class YunmengTrialMeasurementCollectResult(BaseModel):
    measurement: YunmengTrialMeasurementView
    previous_measurement: YunmengTrialMeasurementView | None
    score_delta: int | None
    exchange_currency_delta: int | None
    average_score_per_challenge: float | None
    average_exchange_currency_per_challenge: float | None


def format_yunmeng_trial_label(cross_count: int, start_date: str, end_date: str) -> str:
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    prefix = f"{int(cross_count)}跨,"
    start_text = f"{start.year}/{start.month}/{start.day}"
    if start == end:
        return prefix + start_text
    if start.year == end.year:
        return prefix + f"{start_text}-{end.month}/{end.day}"
    return prefix + f"{start_text}-{end.year}/{end.month}/{end.day}"


def list_yunmeng_trial_snapshot(
    session: Session,
    *,
    activity_id: str | None = None,
) -> YunmengTrialSnapshotResponse:
    activities = list(
        session.exec(
            select(FanxiuYunmengTrialActivity).order_by(
                col(FanxiuYunmengTrialActivity.start_date).desc(),
                col(FanxiuYunmengTrialActivity.end_date).desc(),
                col(FanxiuYunmengTrialActivity.cross_count).desc(),
            )
        ).all()
    )
    selected = None
    if activity_id:
        selected = next((item for item in activities if item.id == activity_id), None)
    elif activities:
        selected = activities[0]
    return YunmengTrialSnapshotResponse(
        activities=[_activity_summary(item) for item in activities],
        selected_activity=_activity_detail(session, selected) if selected else None,
    )


def update_yunmeng_trial_priorities(
    session: Session,
    *,
    activity_id: str,
    ordered_goods_ids: list[int],
) -> YunmengTrialActivityDetail:
    activity = session.get(FanxiuYunmengTrialActivity, activity_id)
    if activity is None:
        raise ValueError("云梦试剑活动不存在")
    goods_ids = [int(value) for value in ordered_goods_ids]
    if len(goods_ids) != len(set(goods_ids)):
        raise ValueError("兑换优先级中存在重复商品")
    items = list(
        session.exec(
            select(FanxiuYunmengTrialShopItem).where(
                FanxiuYunmengTrialShopItem.activity_id == activity_id
            )
        ).all()
    )
    item_by_goods_id = {item.goods_id: item for item in items}
    unknown = [goods_id for goods_id in goods_ids if goods_id not in item_by_goods_id]
    if unknown:
        raise ValueError(f"兑换优先级包含未知商品：{unknown[0]}")
    priority_by_goods_id = {
        goods_id: index + 1 for index, goods_id in enumerate(goods_ids)
    }
    now = time.time()
    for item in items:
        item.priority_order = priority_by_goods_id.get(item.goods_id)
        item.updated_at = now
        session.add(item)
    activity.updated_at = now
    session.add(activity)
    session.commit()
    session.refresh(activity)
    return _activity_detail(session, activity)


def update_yunmeng_trial_shop_item_lock(
    session: Session,
    *,
    activity_id: str,
    goods_id: int,
    locked: bool,
) -> YunmengTrialActivityDetail:
    activity = session.get(FanxiuYunmengTrialActivity, activity_id)
    if activity is None:
        raise ValueError("云梦试剑活动不存在")
    item = session.exec(
        select(FanxiuYunmengTrialShopItem).where(
            FanxiuYunmengTrialShopItem.activity_id == activity_id,
            FanxiuYunmengTrialShopItem.goods_id == int(goods_id),
        )
    ).first()
    if item is None:
        raise ValueError("云梦试剑兑换商品不存在")
    now = time.time()
    item.locked = bool(locked)
    item.updated_at = now
    activity.updated_at = now
    session.add(item)
    session.add(activity)
    session.commit()
    session.refresh(activity)
    return _activity_detail(session, activity)


def list_yunmeng_trial_rankings(
    session: Session,
    *,
    activity_id: str,
    page: int = 1,
    page_size: int = 20,
    ranking_scope: str = "personal",
) -> YunmengTrialRankingPage:
    page = max(1, int(page))
    page_size = min(100, max(1, int(page_size)))
    activity = session.get(FanxiuYunmengTrialActivity, activity_id)
    if activity is None:
        raise ValueError("云梦试剑活动不存在")
    scope = str(ranking_scope or "personal").strip().lower()
    if scope not in {"personal", "plane"}:
        raise ValueError("未知的云梦试剑榜单范围")
    rows = list(
        session.exec(
            select(FanxiuYunmengTrialRanking)
            .where(
                FanxiuYunmengTrialRanking.activity_id == activity_id,
                FanxiuYunmengTrialRanking.ranking_scope == scope,
            )
            .order_by(
                col(FanxiuYunmengTrialRanking.rank).asc(),
                col(FanxiuYunmengTrialRanking.name).asc(),
            )
        ).all()
    )
    items = [_ranking_view(row) for row in rows]
    if scope == "personal" and activity.game_rank_activity_id is not None:
        try:
            reward_tiers = load_yunmeng_rank_reward_tiers(
                rank_activity_id=activity.game_rank_activity_id,
                event_date=activity.start_date,
            )
        except YunmengRankRewardConfigError:
            reward_tiers = []
        from backend.core.fanxiu.activity.ranking_key_points import (
            project_ranking_key_points,
            rank_reward_item_count,
        )

        items = project_ranking_key_points(
            items,
            reward_tiers=reward_tiers,
            reward_count=lambda tier: rank_reward_item_count(
                tier,
                item_id=YUNMENG_TALENT_PILL_ITEM_ID,
            ),
            placeholder_factory=lambda start, end, pill_count: YunmengTrialRankingView(
                id=f"{activity_id}:reward-tier:{start}-{end}",
                ranking_scope="personal",
                rank=end,
                score=0,
                name="",
                server_id=None,
                server_name="",
                club_name="",
                is_self=False,
                is_reward_guard=True,
                reward_rank_start=start,
                reward_rank_end=end,
                talent_pill_count=pill_count,
                has_player=False,
                is_last_player=False,
            ),
        )
    for item in items:
        item.score_per_talent_pill = (
            item.score / item.talent_pill_count
            if item.has_player
            and item.talent_pill_count is not None
            and item.talent_pill_count > 0
            else None
        )
    items.sort(key=lambda item: (item.rank, not item.is_reward_guard, item.name))
    start = (page - 1) * page_size
    return YunmengTrialRankingPage(
        page=page,
        page_size=page_size,
        total=len(items),
        items=items[start : start + page_size],
        last_captured_at=max(
            (item.captured_at for item in items if item.has_player and item.captured_at),
            key=lambda value: value.replace("T", " "),
            default="",
        ),
    )


def list_yunmeng_trial_measurements(
    session: Session,
    *,
    activity_id: str,
) -> YunmengTrialMeasurementPage:
    if session.get(FanxiuYunmengTrialActivity, activity_id) is None:
        raise ValueError("云梦试剑活动不存在")
    rows = list(
        session.exec(
            select(FanxiuYunmengTrialMeasurement)
            .where(FanxiuYunmengTrialMeasurement.activity_id == activity_id)
            .order_by(
                col(FanxiuYunmengTrialMeasurement.created_at).asc(),
                col(FanxiuYunmengTrialMeasurement.id).asc(),
            )
        ).all()
    )
    return YunmengTrialMeasurementPage(items=[_measurement_view(row) for row in rows])


def collect_and_store_yunmeng_trial_measurement(
    session: Session,
    *,
    activity_id: str,
    challenge_count_delta: int | None = None,
    note: str = "",
) -> YunmengTrialMeasurementCollectResult:
    """Collect one observation through the shared runtime-fact contract."""

    activity = session.get(FanxiuYunmengTrialActivity, activity_id)
    if activity is None:
        raise ValueError("云梦试剑活动不存在")
    if activity.game_rank_activity_id is None or activity.currency_type is None:
        raise ValueError("云梦试剑活动缺少积分榜或兑币类型配置")
    _backfill_yunmeng_trial_baseline(session, activity)
    from backend.core.fanxiu.activity.standard_observation import (
        ActivityObservationSpec,
        collect_standard_activity_observation,
    )
    from backend.core.fanxiu.activity.yunmeng_rank_reward import (
        load_yunmeng_rank_reward_tiers,
    )

    snapshot = collect_standard_activity_observation(
        session,
        ActivityObservationSpec(
            rank_activity_id=activity.game_rank_activity_id,
            currency_type=activity.currency_type,
            progress_protocols=(
                "SM_UpdateYunmengChallenge",
                "SM_YunmengPkQuickFight",
                "SM_YunmengPkMatch",
            ),
        ),
        reward_tiers=load_yunmeng_rank_reward_tiers(
            rank_activity_id=activity.game_rank_activity_id,
            event_date=activity.start_date,
        ),
    )
    return store_yunmeng_trial_measurement(
        session,
        activity=activity,
        snapshot=snapshot,
        challenge_count_delta=challenge_count_delta,
        note=note,
    )


def _replace_runtime_rankings(
    session: Session,
    *,
    activity: FanxiuYunmengTrialActivity,
    rows: list[dict[str, Any]],
    captured_at: str,
) -> None:
    """Merge a possibly partial reward-guard snapshot into persisted history.

    Rank protocols commonly return only the first page. Missing guard ranks do
    not mean those players disappeared, so only guards explicitly refreshed at
    the same rank and an explicitly refreshed self row may replace old rows.
    """

    existing_rows = list(
        session.exec(
            select(FanxiuYunmengTrialRanking).where(
                FanxiuYunmengTrialRanking.activity_id == activity.id
            )
        ).all()
    )
    existing_by_key = {
        (row.ranking_scope, row.rank, row.role_key): row for row in existing_rows
    }
    seen_ids: set[str] = set()
    incoming_guard_ranks: set[tuple[str, int]] = set()
    incoming_self_scopes: set[str] = set()
    incoming_last_ranks: set[tuple[str, int]] = set()
    complete_scopes: set[str] = set()
    now = time.time()
    for raw in rows:
        scope = str(raw.get("ranking_scope") or "personal")
        rank = int(raw.get("rank") or 0)
        role_key = str(raw.get("role_key") or raw.get("name") or "")
        key = (scope, rank, role_key)
        row = existing_by_key.get(key) or FanxiuYunmengTrialRanking(
            activity_id=activity.id,
            ranking_scope=scope,
            rank=rank,
            role_key=role_key,
        )
        row.score = int(raw.get("score") or 0)
        row.name = str(raw.get("name") or "")
        row.server_id = raw.get("server_id")
        row.club_name = str(raw.get("club_name") or "")
        row.is_self = bool(raw.get("is_self"))
        row.raw_data = {
            "is_reward_guard": bool(raw.get("is_reward_guard")),
            "reward_rank_start": raw.get("reward_rank_start"),
            "reward_rank_end": raw.get("reward_rank_end"),
            "is_last_player": bool(raw.get("is_last_player")),
            "has_player": bool(raw.get("has_player", True)),
            "rank_list_size": raw.get("rank_list_size"),
            "captured_at": captured_at,
        }
        row.updated_at = now
        session.add(row)
        seen_ids.add(row.id)
        if bool(raw.get("is_reward_guard")):
            incoming_guard_ranks.add((scope, rank))
        if bool(raw.get("is_self")):
            incoming_self_scopes.add(scope)
        if bool(raw.get("is_last_player")):
            incoming_last_ranks.add((scope, rank))
        if bool(raw.get("scope_complete")):
            complete_scopes.add(scope)
    for row in existing_rows:
        if row.id in seen_ids:
            continue
        raw_data = dict(row.raw_data or {})
        should_replace_guard = (
            bool(raw_data.get("is_reward_guard"))
            and (row.ranking_scope, row.rank) in incoming_guard_ranks
        )
        should_replace_self = row.is_self and row.ranking_scope in incoming_self_scopes
        should_replace_last = (
            bool(raw_data.get("is_last_player"))
            and (row.ranking_scope, row.rank) in incoming_last_ranks
        )
        should_replace_complete_scope = row.ranking_scope in complete_scopes
        if should_replace_guard or should_replace_self or should_replace_last or should_replace_complete_scope:
            session.delete(row)


def store_yunmeng_trial_measurement(
    session: Session,
    *,
    activity: FanxiuYunmengTrialActivity,
    snapshot: dict[str, Any],
    challenge_count_delta: int | None = None,
    note: str = "",
) -> YunmengTrialMeasurementCollectResult:
    """Validate and persist a collector result; split out for deterministic tests."""

    if not snapshot.get("complete"):
        raise ValueError("云梦试剑 Runtime 状态快照不完整")
    if int(snapshot.get("rank_activity_id") or 0) != activity.game_rank_activity_id:
        raise ValueError("云梦试剑 Runtime 积分榜 ID 与活动配置不一致")
    if int(snapshot.get("currency_type") or 0) != activity.currency_type:
        raise ValueError("云梦试剑 Runtime 兑币类型与活动配置不一致")
    if (
        snapshot.get("currency_derivation")
        == "previous_measurement_plus_quick_auto_reward"
        and challenge_count_delta is None
    ):
        raise ValueError("自动挑战增量采集必须明确提供本批挑战次数")
    if challenge_count_delta is not None and int(challenge_count_delta) <= 0:
        raise ValueError("挑战次数增量必须大于 0")
    previous = session.exec(
        select(FanxiuYunmengTrialMeasurement)
        .where(FanxiuYunmengTrialMeasurement.activity_id == activity.id)
        .order_by(
            col(FanxiuYunmengTrialMeasurement.created_at).desc(),
            col(FanxiuYunmengTrialMeasurement.id).desc(),
        )
    ).first()
    if (
        previous is not None
        and snapshot.get("currency_derivation") == "standard_resource_state"
        and previous.captured_at == str(snapshot.get("captured_at") or "")
        and previous.score == int(snapshot["score"])
        and previous.exchange_currency == int(snapshot["exchange_currency"])
    ):
        raise ValueError("当前标准运行态事实已经采集，无需重复入库")
    quick_auto_run_key = snapshot.get("quick_auto_run_key")
    previous_evidence = dict(previous.evidence or {}) if previous is not None else {}
    snapshot_runtime_evidence = dict(snapshot.get("evidence") or {})
    quick_signature_fields = (
        "process_start_ticks",
        "yunmeng_root_address",
        "auto_fight_count",
        "auto_win_count",
        "auto_fail_count",
        "auto_total_score",
        "exchange_currency_delta",
    )
    current_quick_signature = {
        key: (
            snapshot.get(key)
            if key in snapshot
            else snapshot_runtime_evidence.get(key)
        )
        for key in quick_signature_fields
    }
    runtime_rankings = list(snapshot.get("rankings") or [])
    same_legacy_quick_run = (
        snapshot.get("currency_derivation")
        == "previous_measurement_plus_quick_auto_reward"
        and previous_evidence.get("currency_derivation")
        == "previous_measurement_plus_quick_auto_reward"
        and all(
            previous_evidence.get(key) == current_quick_signature.get(key)
            for key in quick_signature_fields
        )
    )
    duplicate_quick_run = (
        previous is not None
        and (
            (
                quick_auto_run_key
                and previous_evidence.get("quick_auto_run_key") == quick_auto_run_key
            )
            or same_legacy_quick_run
        )
    )
    if duplicate_quick_run:
        if runtime_rankings:
            _replace_runtime_rankings(
                session,
                activity=activity,
                rows=runtime_rankings,
                captured_at=str(snapshot["captured_at"]),
            )
            session.commit()
        raise ValueError("当前这轮云梦自动挑战已经采集，无需重复入库")
    score = int(snapshot["score"])
    exchange_currency = int(snapshot["exchange_currency"])
    measurement = FanxiuYunmengTrialMeasurement(
        activity_id=activity.id,
        captured_at=str(snapshot["captured_at"]),
        score=score,
        exchange_currency=exchange_currency,
        rank=int(snapshot["rank"]) if snapshot.get("rank") is not None else None,
        challenge_count_delta=(
            int(challenge_count_delta) if challenge_count_delta is not None else None
        ),
        note=str(note or ""),
        source_kind=str(snapshot.get("source") or "standard_runtime_facts"),
        evidence={
            "protocol": snapshot.get("protocol"),
            "rank_activity_id": snapshot.get("rank_activity_id"),
            "currency_type": snapshot.get("currency_type"),
            "currency_amount": snapshot.get("currency_amount"),
            "currency_borrow": snapshot.get("currency_borrow"),
            "currency_derivation": snapshot.get("currency_derivation"),
            "auto_fight_count": snapshot.get("auto_fight_count"),
            "auto_win_count": snapshot.get("auto_win_count"),
            "auto_fail_count": snapshot.get("auto_fail_count"),
            "auto_total_score": snapshot.get("auto_total_score"),
            "exchange_currency_delta": snapshot.get("exchange_currency_delta"),
            "quick_auto_run_key": quick_auto_run_key,
            "quick_auto_result_address": snapshot.get("quick_auto_result_address"),
            "quick_auto_reward_list_address": snapshot.get(
                "quick_auto_reward_list_address"
            ),
            **dict(snapshot.get("evidence") or {}),
        },
    )
    session.add(measurement)
    activity.current_currency = exchange_currency
    activity.cumulative_currency = int(
        snapshot.get("cumulative_currency") or exchange_currency
    )
    activity.captured_at = str(snapshot["captured_at"])
    activity.source_kind = str(snapshot.get("source") or "standard_runtime_facts")
    activity.updated_at = time.time()
    session.add(activity)
    if runtime_rankings:
        _replace_runtime_rankings(
            session,
            activity=activity,
            rows=runtime_rankings,
            captured_at=str(snapshot["captured_at"]),
        )
    else:
        self_ranking = session.exec(
            select(FanxiuYunmengTrialRanking).where(
                FanxiuYunmengTrialRanking.activity_id == activity.id,
                FanxiuYunmengTrialRanking.is_self == True,  # noqa: E712
            )
        ).first()
        if self_ranking is None:
            self_ranking = FanxiuYunmengTrialRanking(
                activity_id=activity.id,
                ranking_scope="personal",
                rank=int(snapshot.get("rank") or 0),
                score=score,
                role_key=str(
                    snapshot.get("role_key") or snapshot.get("name") or "self"
                ),
                name=str(snapshot.get("name") or ""),
                server_id=snapshot.get("server_id"),
                club_name=str(snapshot.get("club_name") or ""),
                is_self=True,
            )
        else:
            self_ranking.rank = int(snapshot.get("rank") or 0)
            self_ranking.score = score
            self_ranking.role_key = str(
                snapshot.get("role_key") or self_ranking.role_key
            )
            self_ranking.name = str(snapshot.get("name") or self_ranking.name)
            self_ranking.server_id = snapshot.get("server_id")
            self_ranking.club_name = str(
                snapshot.get("club_name") or self_ranking.club_name
            )
            self_ranking.updated_at = time.time()
        session.add(self_ranking)
    session.commit()
    session.refresh(measurement)
    score_delta = score - previous.score if previous is not None else None
    currency_delta = (
        exchange_currency - previous.exchange_currency if previous is not None else None
    )
    count = int(challenge_count_delta) if challenge_count_delta is not None else None
    return YunmengTrialMeasurementCollectResult(
        measurement=_measurement_view(measurement),
        previous_measurement=_measurement_view(previous) if previous else None,
        score_delta=score_delta,
        exchange_currency_delta=currency_delta,
        average_score_per_challenge=(score_delta / count if score_delta is not None and count else None),
        average_exchange_currency_per_challenge=(
            currency_delta / count if currency_delta is not None and count else None
        ),
    )


def _backfill_yunmeng_trial_baseline(
    session: Session,
    activity: FanxiuYunmengTrialActivity,
) -> None:
    existing = session.exec(
        select(FanxiuYunmengTrialMeasurement).where(
            FanxiuYunmengTrialMeasurement.activity_id == activity.id
        )
    ).first()
    if existing is not None:
        return
    self_ranking = session.exec(
        select(FanxiuYunmengTrialRanking).where(
            FanxiuYunmengTrialRanking.activity_id == activity.id,
            FanxiuYunmengTrialRanking.is_self == True,  # noqa: E712
        )
    ).first()
    if self_ranking is None:
        return
    session.add(
        FanxiuYunmengTrialMeasurement(
            activity_id=activity.id,
            captured_at=activity.captured_at,
            score=self_ranking.score,
            exchange_currency=activity.current_currency,
            rank=self_ranking.rank,
            challenge_count_delta=0,
            note="历史活动快照回填基线",
            source_kind="backfilled_activity_snapshot",
            evidence={"backfilled": True},
            created_at=activity.updated_at,
        )
    )
    session.commit()


def upsert_yunmeng_trial_snapshot(session: Session, payload: dict[str, Any]) -> str:
    """Agent-only persistence entry for a completed instrumentation snapshot."""

    if "shop_items" in payload:
        shop_items = list(payload.get("shop_items") or [])
        goods_ids = [int(item["goods_id"]) for item in shop_items]
        if len(goods_ids) != len(set(goods_ids)):
            raise ValueError("云梦试剑兑换宝阁快照包含重复商品")
        expected_count = payload.get("expected_shop_item_count")
        if expected_count is not None and len(shop_items) != int(expected_count):
            raise ValueError(
                "云梦试剑兑换宝阁快照不完整："
                f"期望 {int(expected_count)} 项，实际 {len(shop_items)} 项"
            )

    cross_count = int(payload["cross_count"])
    start_date = str(payload["start_date"])
    end_date = str(payload.get("end_date") or start_date)
    date.fromisoformat(start_date)
    date.fromisoformat(end_date)
    if "shop_items" in payload:
        inferred_cross_count = infer_yunmeng_cross_count(
            (item.get("show_limit") for item in payload.get("shop_items") or []),
            shop_base_id=int(payload.get("game_shop_base_id") or 210001),
        )
        if inferred_cross_count is not None and inferred_cross_count != cross_count:
            raise ValueError(
                "云梦试剑活动跨服数与运行时条件不一致："
                f"填写 {cross_count} 跨，运行时为 {inferred_cross_count} 跨"
            )
    existing = session.exec(
        select(FanxiuYunmengTrialActivity).where(
            FanxiuYunmengTrialActivity.cross_count == cross_count,
            FanxiuYunmengTrialActivity.start_date == start_date,
            FanxiuYunmengTrialActivity.end_date == end_date,
        )
    ).first()
    now = time.time()
    activity = existing or FanxiuYunmengTrialActivity(
        id=f"yunmeng-{cross_count}-{start_date}-{end_date}",
        cross_count=cross_count,
        start_date=start_date,
        end_date=end_date,
    )
    for field in (
        "game_rank_activity_id",
        "game_shop_base_id",
        "currency_type",
        "current_currency",
        "cumulative_currency",
        "captured_at",
        "source_kind",
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

    if "shop_items" in payload:
        existing_items = {
            item.goods_id: item
            for item in session.exec(
                select(FanxiuYunmengTrialShopItem).where(
                    FanxiuYunmengTrialShopItem.activity_id == activity.id
                )
            ).all()
        }
        seen_goods_ids: set[int] = set()
        for source_order, raw in enumerate(payload.get("shop_items") or [], start=1):
            goods_id = int(raw["goods_id"])
            seen_goods_ids.add(goods_id)
            item = existing_items.get(goods_id) or FanxiuYunmengTrialShopItem(
                activity_id=activity.id,
                goods_id=goods_id,
                item_id=int(raw["item_id"]),
            )
            item.item_id = int(raw["item_id"])
            item.source_order = int(raw.get("source_order") or source_order)
            item.name = str(raw.get("name") or "")
            item.goods_num = int(raw.get("goods_num") or 1)
            item.token_cost = int(raw.get("token_cost") or 0)
            item.purchase_limit = int(raw.get("purchase_limit") or 0)
            item.purchased_count = int(raw.get("purchased_count") or 0)
            item.discount = raw.get("discount")
            item.original_price = raw.get("original_price")
            item.show_limit = str(raw.get("show_limit") or "")
            item.disappear_limit = str(raw.get("disappear_limit") or "")
            item.raw_data = dict(raw.get("raw_data") or {})
            item.updated_at = now
            session.add(item)
        for goods_id, item in existing_items.items():
            if goods_id not in seen_goods_ids:
                session.delete(item)

    if "rankings" in payload:
        for row in session.exec(
            select(FanxiuYunmengTrialRanking).where(
                FanxiuYunmengTrialRanking.activity_id == activity.id
            )
        ).all():
            session.delete(row)
        for raw in payload.get("rankings") or []:
            raw_data = dict(raw.get("raw_data") or {})
            raw_data.update(
                {
                    "is_reward_guard": bool(raw.get("is_reward_guard")),
                    "reward_rank_start": raw.get("reward_rank_start"),
                    "reward_rank_end": raw.get("reward_rank_end"),
                    "is_last_player": bool(raw.get("is_last_player")),
                    "has_player": bool(raw.get("has_player", True)),
                    "rank_list_size": raw.get("rank_list_size"),
                    "captured_at": str(payload.get("captured_at") or ""),
                }
            )
            session.add(
                FanxiuYunmengTrialRanking(
                    activity_id=activity.id,
                    ranking_scope=str(raw.get("ranking_scope") or "personal"),
                    rank=int(raw.get("rank") or 0),
                    score=int(raw.get("score") or 0),
                    role_key=str(raw.get("role_key") or raw.get("name") or ""),
                    name=str(raw.get("name") or ""),
                    server_id=raw.get("server_id"),
                    club_name=str(raw.get("club_name") or ""),
                    is_self=bool(raw.get("is_self")),
                    raw_data=raw_data,
                    updated_at=now,
                )
            )
    session.commit()
    return activity.id


def _activity_summary(activity: FanxiuYunmengTrialActivity) -> YunmengTrialActivitySummary:
    return YunmengTrialActivitySummary(
        id=activity.id,
        label=format_yunmeng_trial_label(
            activity.cross_count, activity.start_date, activity.end_date
        ),
        cross_count=activity.cross_count,
        start_date=activity.start_date,
        end_date=activity.end_date,
        captured_at=activity.captured_at,
    )


def _activity_detail(
    session: Session,
    activity: FanxiuYunmengTrialActivity,
) -> YunmengTrialActivityDetail:
    items = list(
        session.exec(
            select(FanxiuYunmengTrialShopItem)
            .where(FanxiuYunmengTrialShopItem.activity_id == activity.id)
            .order_by(
                col(FanxiuYunmengTrialShopItem.source_order).asc(),
                col(FanxiuYunmengTrialShopItem.goods_id).asc(),
            )
        ).all()
    )
    cumulative_by_id: dict[str, int | None] = {}
    cumulative: int | None = 0
    for item in sorted(
        (row for row in items if row.priority_order is not None),
        key=lambda row: (int(row.priority_order or 0), row.source_order),
    ):
        if cumulative is None or item.purchase_limit < 0:
            cumulative = None
        else:
            cumulative += item.token_cost * item.purchase_limit
        cumulative_by_id[item.id] = cumulative
    earning_rate = _latest_exchange_currency_rate(session, activity.id)
    yield_rate = _latest_yunmeng_trial_yield_rate(session, activity.id)
    return YunmengTrialActivityDetail(
        **_activity_summary(activity).model_dump(),
        game_rank_activity_id=activity.game_rank_activity_id,
        game_shop_base_id=activity.game_shop_base_id,
        currency_type=activity.currency_type,
        current_currency=activity.current_currency,
        cumulative_currency=activity.cumulative_currency,
        resource_strategy=dict(activity.resource_strategy or {}),
        source_kind=activity.source_kind,
        yield_rate=yield_rate,
        shop_items=[
            YunmengTrialShopItemView(
                id=item.id,
                goods_id=item.goods_id,
                item_id=item.item_id,
                source_order=item.source_order,
                priority_order=item.priority_order,
                locked=item.locked,
                name=item.name,
                goods_num=item.goods_num,
                token_cost=item.token_cost,
                purchase_limit=item.purchase_limit,
                purchased_count=item.purchased_count,
                row_total_tokens=(
                    item.token_cost * item.purchase_limit
                    if item.purchase_limit >= 0
                    else None
                ),
                cumulative_tokens=cumulative_by_id.get(item.id),
                remaining_challenges=estimate_remaining_attempts(
                    accumulated_exchange_currency=activity.cumulative_currency,
                    target_exchange_currency=(
                        cumulative_by_id.get(item.id)
                        if item.purchase_limit >= 0
                        else None
                    ),
                    yield_rate=earning_rate,
                ),
                discount=item.discount,
                original_price=item.original_price,
            )
            for item in items
        ],
    )


def _latest_exchange_currency_rate(
    session: Session,
    activity_id: str,
) -> ExchangeYieldRate | None:
    """Adapt Yunmeng measurement rows to the shared exchange planner."""

    rows = list(
        session.exec(
            select(FanxiuYunmengTrialMeasurement)
            .where(FanxiuYunmengTrialMeasurement.activity_id == activity_id)
            .order_by(
                col(FanxiuYunmengTrialMeasurement.created_at).asc(),
                col(FanxiuYunmengTrialMeasurement.id).asc(),
            )
        ).all()
    )
    return latest_exchange_yield_rate(
        ExchangeMeasurement(
            exchange_currency=row.exchange_currency,
            attempt_count_delta=row.challenge_count_delta,
        )
        for row in rows
    )


def _latest_yunmeng_trial_yield_rate(
    session: Session,
    activity_id: str,
) -> YunmengTrialYieldRateView | None:
    """Project the latest valid challenge batch into a per-100 display rate."""

    rows = list(
        session.exec(
            select(FanxiuYunmengTrialMeasurement)
            .where(FanxiuYunmengTrialMeasurement.activity_id == activity_id)
            .order_by(
                col(FanxiuYunmengTrialMeasurement.created_at).asc(),
                col(FanxiuYunmengTrialMeasurement.id).asc(),
            )
        ).all()
    )
    previous: FanxiuYunmengTrialMeasurement | None = None
    latest: YunmengTrialYieldRateView | None = None
    for current in rows:
        if previous is not None:
            count = int(current.challenge_count_delta or 0)
            currency_delta = current.exchange_currency - previous.exchange_currency
            if count > 0 and currency_delta > 0:
                latest = YunmengTrialYieldRateView(
                    sample_challenges=count,
                    average_score_per_100=(current.score - previous.score) * 100 / count,
                    average_exchange_currency_per_100=currency_delta * 100 / count,
                    captured_at=current.captured_at,
                )
        previous = current
    return latest


def _ranking_view(row: FanxiuYunmengTrialRanking) -> YunmengTrialRankingView:
    from backend.core.fanxiu.catalog.server_mapping import (
        resolve_fanxiu_region_server_by_id,
    )

    raw_data = row.raw_data or {}
    server = resolve_fanxiu_region_server_by_id(row.server_id)
    return YunmengTrialRankingView(
        id=row.id,
        ranking_scope=row.ranking_scope,
        rank=row.rank,
        score=row.score,
        name=row.name,
        server_id=row.server_id,
        server_name=str(server.get("server_name") or ""),
        club_name=row.club_name,
        is_self=row.is_self,
        is_reward_guard=bool(raw_data.get("is_reward_guard")),
        reward_rank_start=raw_data.get("reward_rank_start"),
        reward_rank_end=raw_data.get("reward_rank_end"),
        has_player=bool(raw_data.get("has_player", True)),
        is_last_player=bool(raw_data.get("is_last_player")),
        captured_at=str(raw_data.get("captured_at") or ""),
    )


def _measurement_view(
    row: FanxiuYunmengTrialMeasurement,
) -> YunmengTrialMeasurementView:
    return YunmengTrialMeasurementView(
        id=row.id,
        captured_at=row.captured_at,
        score=row.score,
        exchange_currency=row.exchange_currency,
        rank=row.rank,
        challenge_count_delta=row.challenge_count_delta,
        note=row.note,
        source_kind=row.source_kind,
    )
