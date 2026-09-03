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
from backend.core.fanxiu.activity.ranking_lifecycle import RankingOccurrence
from backend.core.fanxiu.catalog.resources import resolve_fanxiu_export_root
from backend.models import (
    FanxiuExchangeActivity,
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
    activity_id = upsert_exchange_activity_snapshot(session, payload)
    activity = session.get(FanxiuExchangeActivity, activity_id)
    if activity is None:
        raise RuntimeError("榜单 occurrence 入库后无法回读")
    return activity


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


def reconcile_ranking_occurrence(
    session: Session,
    occurrence: RankingOccurrence,
    *,
    captured_at: str,
    required_fact_watermark: datetime | None = None,
) -> dict[str, Any]:
    """Reconcile static tiers and retain current facts for one occurrence."""

    spec = get_exchange_activity_spec(occurrence.activity_type)
    materialize_error = ""
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
    collected_activity: Any | None = None
    resource_collect_error = ""
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
    if isinstance(spec.adapter, ResourceRankingResourceAdapter):
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
            if shop_refresh_status == "updated" or shop_watermark_satisfied
            else "retained"
        )
    )
    required_fact_errors: list[str] = []
    if spec.shop is not None and shop_status != "updated":
        required_fact_errors.append(
            "兑换宝阁本次未刷新"
            + (f"：{shop_refresh_reason}" if shop_refresh_reason else "")
        )
    if any(scope.required for scope in spec.rank_scopes) and reward_tier_total <= 0:
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
        "blocked"
        if blocked_reasons
        else ("completed" if reward_tier_total else "retained")
    )
    return {
        "status": status,
        "message": (
            f"{occurrence.activity_type} Runtime 采集未完成："
            + "；".join(blocked_reasons)
            if blocked_reasons
            else ""
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
    "reconcile_ranking_occurrence",
    "seed_ranking_occurrence",
]
