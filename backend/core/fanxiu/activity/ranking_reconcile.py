from __future__ import annotations

"""Database-side 00:30 reconciliation for one ranking occurrence.

This layer may seed a page occurrence from authoritative Runtime identity and
static configuration, then retain any existing live facts.  It never operates
the game.  Activity adapters may add an explicit pre-load/collection step in
the outer Job when current Runtime facts require a visible page.
"""

import json
from datetime import datetime
from functools import lru_cache
from typing import Any, Mapping

from sqlmodel import Session, func, select

from backend.core.fanxiu.activity.exchange_activity_registry import (
    collect_registered_exchange_activity,
    collect_registered_resource_ranking_resources,
    get_exchange_activity_spec,
    materialize_registered_exchange_activity,
    resolve_registered_occurrence_rank_identities,
    resolve_registered_occurrence_shop,
)
from backend.core.fanxiu.activity.exchange_activity_spec import (
    ResourceRankingResourceAdapter,
)
from backend.core.fanxiu.activity.exchange_event import (
    list_exchange_rankings,
    store_exchange_activity_observation,
    upsert_exchange_activity_snapshot,
)
from backend.core.fanxiu.activity.ranking_lifecycle import (
    RankingOccurrence, RankingFamily, discover_ranking_occurrences, occurrence_relevant_on,
)
from backend.core.fanxiu.catalog.resources import resolve_fanxiu_export_root
from backend.models import (
    FanxiuExchangeActivity,
    FanxiuExchangeActivityObservation,
    FanxiuExchangeRanking,
    FanxiuExchangeShopItem,
)


@lru_cache(maxsize=1)
def _activity_definition_index() -> dict[int, dict[str, Any]]:
    path = resolve_fanxiu_export_root() / "parsed_configs/Activity/rows.json"
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise RuntimeError("Activity 静态配置不是列表")
    return {
        int(row["id"]): dict(row)
        for row in rows
        if isinstance(row, Mapping) and str(row.get("id") or "").isdigit()
    }


def _rank_scope_identities(occurrence: RankingOccurrence):
    definition = _activity_definition_index().get(occurrence.activity_id)
    if definition is None:
        raise ValueError(f"活动静态配置 {occurrence.activity_id} 不存在")
    follow = tuple(int(value) for value in definition.get("follow") or ())
    return resolve_registered_occurrence_rank_identities(
        activity_type=occurrence.activity_type,
        game_activity_id=occurrence.activity_id,
        cross_count=occurrence.cross_count,
        activity_follow=follow,
    )


def _proven_server_day_floor(
    session: Session,
    occurrence: RankingOccurrence,
) -> tuple[int, str]:
    """Reuse only an explicit, monotonic same-family server-day proof.

    Some reward UIs prove only a tier boundary (for example ``>=31``), not an
    exact open-server day.  That lower bound remains true for a later
    occurrence, whereas inventing an exact day from wall-clock dates does not.
    """

    candidates: list[tuple[int, str, str]] = []
    rows = session.exec(select(FanxiuExchangeActivity)).all()
    target_date = occurrence.start_at.date().isoformat()
    for row in rows:
        evidence = dict(row.evidence or {})
        value = int(evidence.get("server_day") or 0)
        proof = str(evidence.get("server_day_evidence") or "").strip()
        if value > 0 and proof and row.start_date <= target_date:
            candidates.append((value, row.start_date, row.id))
    if not candidates:
        return 0, ""
    value, source_date, source_id = max(candidates)
    return (
        value,
        f"monotonic lower bound inherited from {source_id} ({source_date}): >= {value}",
    )


def seed_ranking_occurrence(
    session: Session,
    occurrence: RankingOccurrence,
    *,
    captured_at: str,
) -> FanxiuExchangeActivity:
    """Ensure the page has an exact occurrence before live rankings open."""

    spec = get_exchange_activity_spec(occurrence.activity_type)
    occurrence_shop = resolve_registered_occurrence_shop(
        activity_type=occurrence.activity_type,
        cross_count=occurrence.cross_count,
        activity_id=occurrence.activity_id,
    )
    scope_identities = _rank_scope_identities(occurrence)
    primary = next(
        scope for scope in spec.rank_scopes if scope.effective_role == "primary"
    )
    primary_identity = scope_identities.get(primary.scope)
    if primary_identity is None:
        raise ValueError(f"{spec.label} 缺少必需主榜静态绑定")
    existing = session.exec(
        select(FanxiuExchangeActivity).where(
            FanxiuExchangeActivity.instance_key == occurrence.instance_key,
        )
    ).first()
    if existing is None:
        existing = session.exec(
            select(FanxiuExchangeActivity).where(
                FanxiuExchangeActivity.activity_type == occurrence.activity_type,
                FanxiuExchangeActivity.cross_count == occurrence.cross_count,
                FanxiuExchangeActivity.start_date == occurrence.start_at.date().isoformat(),
                FanxiuExchangeActivity.end_date == occurrence.end_at.date().isoformat(),
            )
        ).first()
    evidence = dict(existing.evidence or {}) if existing is not None else {}
    refresh_status = dict(evidence.get("refresh_status") or {})
    refresh_status.setdefault("rankings", "unavailable")
    refresh_status.setdefault("shop", "unavailable" if occurrence_shop else "not_applicable")
    refresh_status.setdefault("currency", "unavailable" if occurrence_shop else "not_applicable")
    server_day, server_day_evidence = _proven_server_day_floor(session, occurrence)
    evidence.update(
        {
            "instance_key": occurrence.instance_key,
            "runtime_id": occurrence.runtime_id,
            "game_activity_id": occurrence.activity_id,
            "period_start_time": occurrence.start_at.isoformat(timespec="seconds"),
            "period_end_time": occurrence.end_at.isoformat(timespec="seconds"),
            "period_prepare_time": occurrence.prepare_at.isoformat(timespec="seconds"),
            # Exchange lifecycle readers consume the established
            # ``period_close_panel_*`` envelope.  Keeping a differently named
            # field here made a still-open settlement shop look closed at the
            # activity end date.
            "period_close_panel_time": int(occurrence.close_at.timestamp() * 1000),
            "period_close_panel_date": occurrence.close_at.date().isoformat(),
            "period_start_time_ms": int(occurrence.start_at.timestamp() * 1000),
            "world_level": occurrence.world_level,
            "rank_scope_identities": {
                scope: identity.as_dict()
                for scope, identity in scope_identities.items()
            },
            "refresh_status": refresh_status,
            "lifecycle_seed_source": "worldline_activity_runtime_memory",
        }
    )
    if server_day and not int(evidence.get("server_day") or 0):
        evidence["server_day"] = server_day
        evidence["server_day_evidence"] = server_day_evidence
    instance_data = dict(existing.instance_data or {}) if existing is not None else {}
    instance_data.update(
        {
            "base_id": occurrence.base_id,
            "world_level": occurrence.world_level,
            "rank_scope_identities": {
                scope: identity.as_dict()
                for scope, identity in scope_identities.items()
            },
        }
    )
    payload: dict[str, Any] = {
        "instance_key": occurrence.instance_key,
        "family": occurrence.family,
        "activity_type": occurrence.activity_type,
        "runtime_id": occurrence.runtime_id,
        "game_activity_id": occurrence.activity_id,
        "cross_count": occurrence.cross_count,
        "prepare_at": occurrence.prepare_at.isoformat(timespec="seconds"),
        "start_at": occurrence.start_at.isoformat(timespec="seconds"),
        "end_at": occurrence.end_at.isoformat(timespec="seconds"),
        "close_at": occurrence.close_at.isoformat(timespec="seconds"),
        "start_date": occurrence.start_at.date().isoformat(),
        "end_date": occurrence.end_at.date().isoformat(),
        "game_rank_activity_id": primary_identity.runtime_rank_activity_id,
        "currency_type": occurrence_shop.currency_type if occurrence_shop else (spec.currency_type or None),
        "currency_name": spec.currency_name,
        # Seeding an already-known occurrence updates its schedule envelope; it
        # does not constitute a new observation of the shop, wallet, or ranks.
        "captured_at": (
            str(existing.captured_at or captured_at)
            if existing is not None
            else captured_at
        ),
        "source_kind": (
            str(existing.source_kind or "runtime_schedule_reconcile")
            if existing is not None
            else "runtime_schedule_reconcile"
        ),
        "instance_data": instance_data,
        "evidence": evidence,
    }
    if occurrence_shop is not None:
        payload["game_shop_base_id"] = occurrence_shop.base_id
    # Updating schedule identity must not change the user's existing lock
    # selection. Snapshot upsert also recalculates policy locks, so restore
    # the exact prior selection inside this provider after that calculation.
    existing_items = list(session.exec(select(FanxiuExchangeShopItem).where(
        FanxiuExchangeShopItem.activity_id == existing.id,
    )).all()) if existing is not None else []
    prior_locks = {int(item.goods_id): bool(item.locked) for item in existing_items}
    # Static occurrence bindings are complete, not a partial observation.
    # Retaining an old optional scope here can resurrect a previous variant's
    # plane leaderboard on a local preliminary that has no plane rank.
    for container in (payload["instance_data"], payload["evidence"]):
        container.pop("rank_scope_activity_ids", None)
        container.pop("reward_scope_activity_ids", None)
    activity_id = upsert_exchange_activity_snapshot(
        session, payload, replace_rank_scope_identities=True,
    )
    for item in existing_items:
        item.locked = prior_locks[int(item.goods_id)]
        session.add(item)
    session.flush()
    activity = session.get(FanxiuExchangeActivity, activity_id)
    if activity is None:
        raise RuntimeError("榜单 occurrence 入库后无法回读")
    _retire_factless_duplicate_rows(session, activity, occurrence)
    return activity


def _retire_factless_duplicate_rows(
    session: Session,
    activity: FanxiuExchangeActivity,
    occurrence: RankingOccurrence,
) -> None:
    """Collapse same-occurrence rows left behind by game Runtime id churn.

    同一次活动（同类型、同跨服、同起止日）在游戏客户端重建过 Runtime 对象之后，
    两套发现源会各建一条记录，而页面默认选中的未必是真正采集到事实的那条。
    只有能证明自己完全没有事实（无商店、无榜单、无观测、无货币快照）的重复记录
    才允许在这里废弃；任何带事实的记录一律保留，交由人来决定如何合并。
    """

    rows = session.exec(
        select(FanxiuExchangeActivity).where(
            FanxiuExchangeActivity.activity_type == occurrence.activity_type,
            FanxiuExchangeActivity.cross_count == occurrence.cross_count,
            FanxiuExchangeActivity.start_date
            == occurrence.start_at.date().isoformat(),
            FanxiuExchangeActivity.end_date == occurrence.end_at.date().isoformat(),
        )
    ).all()
    retired = False
    for row in rows:
        if row.id == activity.id or row.instance_key == occurrence.instance_key:
            continue
        if int(row.current_currency or 0) or int(row.cumulative_currency or 0):
            continue
        shop_exists = session.exec(
            select(FanxiuExchangeShopItem.id)
            .where(FanxiuExchangeShopItem.activity_id == row.id)
            .limit(1)
        ).first()
        ranking_exists = session.exec(
            select(FanxiuExchangeRanking.id)
            .where(FanxiuExchangeRanking.activity_id == row.id)
            .limit(1)
        ).first()
        observation_exists = session.exec(
            select(FanxiuExchangeActivityObservation.id)
            .where(FanxiuExchangeActivityObservation.activity_id == row.id)
            .limit(1)
        ).first()
        if shop_exists or ranking_exists or observation_exists:
            continue
        evidence = dict(row.evidence or {})
        refresh_status = dict(evidence.get("refresh_status") or {})
        if (
            evidence.get("shop_snapshot_captured_at")
            or refresh_status.get("shop") == "updated"
            or refresh_status.get("currency") == "updated"
        ):
            continue
        session.delete(row)
        retired = True
    if retired:
        # 调用方（榜单 Job）在同一 session 里只做读取，删除必须由提供方自己提交，
        # 否则重复档案会在 session 关闭时被回滚。
        session.commit()


def sync_ranking_schedule(
    session: Session,
    schedule: Mapping[str, Any],
    *,
    now: datetime,
    family: RankingFamily | None = None,
) -> list[str]:
    """Persist today's registered occurrences independently of action maturity.

    Explicit database synchronization from a complete Runtime schedule; no GUI
    or ranking collection. Repeating it updates the same occurrence identities.
    Unloaded ranks remain unavailable rather than being represented as empty.
    """
    if not (schedule.get("available") and schedule.get("complete")):
        raise ValueError("榜单入库需要完整 Runtime 日程")
    from backend.core.fanxiu.activity.exchange_activity_registry import EXCHANGE_ACTIVITY_SPECS

    return [
        seed_ranking_occurrence(session, occurrence, captured_at=str(schedule.get("captured_at") or now.isoformat())).id
        for occurrence in discover_ranking_occurrences(schedule, family=family)
        if occurrence.activity_type in EXCHANGE_ACTIVITY_SPECS
        and occurrence_relevant_on(occurrence, now.date())
        and (occurrence.activity_type != 'shengxian-hui' or occurrence.base_id == 10000)
    ]


def _count(session: Session, model: Any, predicate: Any) -> int:
    value = session.exec(select(func.count()).select_from(model).where(predicate)).one()
    return int(value or 0)


def _ranking_snapshot_kind(
    now: datetime,
    occurrence: RankingOccurrence,
) -> str:
    """Name the daily snapshot by lifecycle, including pre-close finalization."""

    if now <= occurrence.end_at:
        return "running"
    if now.date() >= occurrence.close_at.date():
        # The last scheduled 00:30 normally occurs before closePanelTime on
        # its calendar day. Waiting until the timestamp itself would make the
        # final state unreachable because no later daily checkpoint exists.
        return "final"
    return "formal_end"


# Adapters refuse live collection once an instance sits outside its own
# effective dates.  That is a fact about the occurrence — the calendar day can
# never become collectable again — so it must reach a terminal business
# outcome instead of consuming the Scheduler's ten-minute retry budget forever.
_INSTANCE_CLOSED_COLLECT_ERROR_MARKERS = ("不在有效日期内",)


def _collect_error_closes_instance(error: Exception) -> bool:
    """Whether a collection failure proves the instance can never be read again."""

    if not isinstance(error, ValueError):
        return False
    message = str(error).strip()
    return message.endswith(_INSTANCE_CLOSED_COLLECT_ERROR_MARKERS)


def reconcile_ranking_occurrence(
    session: Session,
    occurrence: RankingOccurrence,
    *,
    captured_at: str,
    required_fact_watermark: datetime | None = None,
    collect_live_facts: bool = True,
) -> dict[str, Any]:
    """Reconcile static tiers and retain current facts for one occurrence.

    Adapters that already collected independent facts may pass
    ``collect_live_facts=False`` to validate and record the persisted projection
    without reloading Runtime ranks or replacing their complete shop snapshot.
    Retained shop evidence must cover the checkpoint watermark.
    """

    spec = get_exchange_activity_spec(occurrence.activity_type)
    materialize_error = ""
    observed_at = datetime.fromisoformat(captured_at)
    if observed_at.tzinfo is None:
        observed_at = observed_at.astimezone()
    if occurrence.activity_type == "tiandi-yiju" and (
        observed_at < occurrence.prepare_at
        or observed_at.date() < occurrence.start_at.date()
    ):
        # The complete worldline advertises the next board before its entry
        # opens. The calendar admits only its start date onward, even when
        # prepareEndTime is yesterday. Seed its identity now, but do not read
        # a previous board's shop
        # or turn lawful waiting into a missing-Runtime error until tomorrow.
        activity = seed_ranking_occurrence(session, occurrence, captured_at=captured_at)
        session.commit()
        return {
            "status": "pending",
            "message": f"{spec.label} 已初始化，等待本期预备入口开放",
            "activity_id": activity.id,
            "retry_at": (
                occurrence.start_at if observed_at >= occurrence.prepare_at
                else occurrence.prepare_at
            ).isoformat(timespec="seconds"),
        }
    if occurrence.family == "resource_rank" and observed_at < occurrence.start_at:
        activity = seed_ranking_occurrence(session, occurrence, captured_at=captured_at)
        session.commit()
        return {
            "status": "pending",
            "message": f"{spec.label} 已初始化，等待本期开始",
            "activity_id": activity.id,
            "retry_at": occurrence.start_at.isoformat(timespec="seconds"),
        }
    if collect_live_facts:
        try:
            materialize_registered_exchange_activity(
                session,
                activity_type=occurrence.activity_type,
            )
        except (RuntimeError, ValueError) as exc:
            # Seeding below is occurrence-exact. A materializer is an opportunistic
            # DB catch-up and may legitimately have no open live rank at 00:30.
            materialize_error = str(exc)
    activity = seed_ranking_occurrence(
        session,
        occurrence,
        captured_at=captured_at,
    )
    collect_error = ""
    collect_closed_instance = False
    collected_activity: Any | None = None
    resource_collect_error = ""
    if collect_live_facts:
        try:
            collected_activity = collect_registered_exchange_activity(
                session,
                activity_type=occurrence.activity_type,
                activity_id=activity.id,
            )
        except (RuntimeError, ValueError) as exc:
            # Runtime pages/managers are commonly unavailable at 00:30.  The
            # exact seed and static reward projection remain valid; old live facts
            # must be retained instead of being replaced with an empty snapshot.
            collect_error = str(exc)
            # "Outside dates" also means not started yet. Only the actual
            # closing boundary can prove this occurrence permanently unavailable.
            observed_at = datetime.fromisoformat(captured_at)
            if observed_at.tzinfo is None:
                observed_at = observed_at.astimezone()
            collect_closed_instance = (
                _collect_error_closes_instance(exc)
                and (
                    observed_at > occurrence.close_at
                    # Resource-rank collectors admit scoring dates, unlike
                    # gameplay shops which remain open until panel closure.
                    # A previous-day preliminary cannot be freshly collected
                    # after its scoring date, even while the shared panel lives.
                    or (occurrence.family == "resource_rank"
                        and observed_at.date() > occurrence.end_at.date())
                )
            )
    if collect_live_facts and isinstance(spec.adapter, ResourceRankingResourceAdapter):
        try:
            collect_registered_resource_ranking_resources(
                session,
                activity_type=occurrence.activity_type,
                activity_id=activity.id,
            )
        except (RuntimeError, ValueError) as exc:
            resource_collect_error = str(exc)
    session.refresh(activity)
    scope_results: dict[str, dict[str, Any]] = {}
    reward_tier_total = 0
    for scope in spec.rank_scopes:
        try:
            page = list_exchange_rankings(
                session,
                activity_type=occurrence.activity_type,
                activity_id=activity.id,
                ranking_scope=scope.scope,
                page=1,
                page_size=1,
            )
            tier_count = len(page.reward_tiers)
            reward_tier_total += tier_count
            scope_results[scope.scope] = {
                "status": "updated" if page.loaded_entry_count else "retained",
                "reward_tier_count": tier_count,
                "loaded_player_count": page.loaded_entry_count,
                "declared_player_count": page.declared_rank_count,
                "complete": page.complete,
            }
        except (RuntimeError, ValueError) as exc:
            scope_results[scope.scope] = {
                "status": "unavailable",
                "reason": str(exc),
                "reward_tier_count": 0,
            }
    ranking_count = _count(
        session,
        FanxiuExchangeRanking,
        FanxiuExchangeRanking.activity_id == activity.id,
    )
    shop_count = _count(
        session,
        FanxiuExchangeShopItem,
        FanxiuExchangeShopItem.activity_id == activity.id,
    )
    rankings_status = "updated" if ranking_count else "retained"
    refresh_status = dict((activity.evidence or {}).get("refresh_status") or {})
    shop_refresh_status = str(
        getattr(collected_activity, "shop_refresh_status", "")
        or refresh_status.get("shop")
        or ""
    )
    shop_refresh_reason = str(
        getattr(collected_activity, "shop_refresh_reason", "")
        or refresh_status.get("shop_reason")
        or ""
    )
    shop_snapshot_captured_at = str(
        getattr(collected_activity, "shop_snapshot_captured_at", "")
        or (activity.evidence or {}).get("shop_snapshot_captured_at")
        or ""
    )
    shop_snapshot_time: datetime | None = None
    try:
        shop_snapshot_time = datetime.fromisoformat(shop_snapshot_captured_at)
    except ValueError:
        pass
    required_watermark = required_fact_watermark or datetime.fromisoformat(captured_at)
    if required_watermark.tzinfo is None:
        required_watermark = required_watermark.astimezone()
    if shop_snapshot_time is not None and shop_snapshot_time.tzinfo is None:
        shop_snapshot_time = shop_snapshot_time.astimezone()
    shop_watermark_satisfied = bool(
        shop_count
        and shop_snapshot_time is not None
        and shop_snapshot_time >= required_watermark
    )
    shop_status = (
        "not_applicable"
        if spec.shop is None
        else (
            "updated"
            if (collect_live_facts and shop_refresh_status == "updated") or shop_watermark_satisfied
            else "retained"
        )
    )
    required_fact_errors: list[str] = []
    if occurrence.activity_type in {"lingzhuang-huadao", "xiling-zhengwu"}:
        for scope in spec.rank_scopes:
            if scope.required and not (scope_results.get(scope.scope) or {}).get("complete"):
                required_fact_errors.append(f"必需榜单 {scope.scope} 本次未完整覆盖")
    if spec.shop is not None and shop_status != "updated":
        required_fact_errors.append(
            "兑换宝阁本次未刷新"
            + (f"：{shop_refresh_reason}" if shop_refresh_reason else "")
        )
    # A scope that intentionally disables reward tiers has no static tier
    # projection by contract, so its zero count is a legal terminal state and
    # must not block.  Only required scopes that do declare tiers are gated, and
    # each is checked on its own projection so an optional scope's tiers can
    # never mask a required scope that stayed empty.
    required_reward_tier_missing = any(
        scope.required
        and scope.reward_tiers_enabled
        and int(
            (scope_results.get(scope.scope) or {}).get("reward_tier_count") or 0
        )
        <= 0
        for scope in spec.rank_scopes
    )
    if required_reward_tier_missing:
        required_fact_errors.append("榜单奖励档次本次未加载")
    now = datetime.fromisoformat(captured_at)
    snapshot_kind = _ranking_snapshot_kind(now, occurrence)
    observation_payload = {
        "instance_key": occurrence.instance_key,
        "checkpoint": "daily_reconcile",
        "family": occurrence.family,
        "scope_results": scope_results,
        "reward_tier_count": reward_tier_total,
        "ranking_row_count": ranking_count,
        "shop_item_count": shop_count,
        "shop_refresh_status": shop_refresh_status,
        "shop_refresh_reason": shop_refresh_reason,
        "shop_snapshot_captured_at": shop_snapshot_captured_at,
        "required_fact_watermark": required_watermark.isoformat(timespec="seconds"),
        "shop_watermark_satisfied": shop_watermark_satisfied,
        "required_fact_errors": required_fact_errors,
        "materialize_error": materialize_error,
        "collect_error": collect_error,
        "resource_collect_error": resource_collect_error,
    }
    observation_id = store_exchange_activity_observation(
        session,
        activity=activity,
        captured_at=captured_at,
        current_day=now.date(),
        current_currency=activity.current_currency,
        cumulative_currency=activity.cumulative_currency,
        shop_status=shop_status,
        rankings_status=rankings_status,
        payload=observation_payload,
        snapshot_kind=snapshot_kind,
    )
    blocked_reasons = [
        reason for reason in (collect_error, *required_fact_errors) if reason
    ]
    status = (
        "unavailable"
        if collect_closed_instance
        else (
            "blocked"
            if blocked_reasons
            else ("completed" if reward_tier_total else "retained")
        )
    )
    return {
        "status": status,
        "message": (
            f"{occurrence.activity_type} Runtime 采集未完成："
            + "；".join(blocked_reasons)
            if blocked_reasons
            else ""
        ),
        "terminal_reason": (
            "activity_out_of_effective_dates" if collect_closed_instance else ""
        ),
        "activity_id": activity.id,
        "activity_type": occurrence.activity_type,
        "family": occurrence.family,
        "instance_key": occurrence.instance_key,
        "snapshot_kind": snapshot_kind,
        "observation_id": observation_id,
        "facts": {
            "rankings": rankings_status,
            "shop": shop_status,
            "shop_refresh_status": shop_refresh_status,
            "shop_refresh_reason": shop_refresh_reason,
            "shop_snapshot_captured_at": shop_snapshot_captured_at,
            "required_fact_watermark": required_watermark.isoformat(timespec="seconds"),
            "shop_watermark_satisfied": shop_watermark_satisfied,
            "reward_tier_count": reward_tier_total,
            "ranking_row_count": ranking_count,
            "shop_item_count": shop_count,
            "scopes": scope_results,
        },
        "materialize_error": materialize_error,
        "collect_error": collect_error,
        "resource_collect_error": resource_collect_error,
    }


__all__ = [
    "sync_ranking_schedule",
    "reconcile_ranking_occurrence",
    "seed_ranking_occurrence",
]
