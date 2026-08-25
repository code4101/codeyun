from __future__ import annotations

import json
from datetime import date, datetime
import logging
import time
from typing import Any

from sqlmodel import Session, col, select

from backend.core.fanxiu.catalog.item import load_fanxiu_item_runtime_index
from backend.core.fanxiu.catalog.resources import resolve_fanxiu_export_root
from backend.core.fanxiu.instrumentation.activity_shop import (
    FanxiuActivityShopCollectionError,
    FanxiuActivityShopNotLoadedError,
    collect_activity_shop_runtime,
)
from backend.models import (
    FanxiuExchangeActivity,
    FanxiuExchangeRanking,
    FanxiuExchangeShopItem,
    FanxiuPacketBusinessRecord,
)


BEAST_ABYSS_ACTIVITY_TYPE = "beast-abyss"
BEAST_ABYSS_SHOP_BASE_ID = 150000
BEAST_ABYSS_CURRENCY_TYPE = 14
BEAST_ABYSS_CURRENCY_NAME = "兽元"
_LOGGER = logging.getLogger(__name__)


def _runtime_currency_snapshot() -> dict[str, Any]:
    """Read currency 14 through the shared, process-external Wallet adapter."""

    from backend.core.fanxiu.instrumentation.wallet import (
        read_wallet_currency_snapshot,
    )

    return read_wallet_currency_snapshot(
        BEAST_ABYSS_CURRENCY_TYPE,
        # This path is only reached by the explicit collect endpoint. A cold
        # cache may perform the existing process-external read-only discovery;
        # GET/ensure never enters it.
        allow_discovery=True,
    )


def _period_from_item(
    item: dict[str, Any],
    *,
    cross_count: int | None,
    target_date: date,
    captured_at: str,
    record_id: str,
    packet_id: str,
    source_kind: str,
) -> dict[str, Any] | None:
    is_beast = (
        str(item.get("class") or "") == "BeastExplodeActivityVO"
        or int(item.get("activityType") or 0) == 15
        and str(item.get("name") or "") == "兽渊探秘"
    )
    if not is_beast:
        return None
    server_count = int(item.get("serverCount") or 1)
    if cross_count is not None and server_count != int(cross_count):
        return None
    start_ms = int(item.get("startTime") or 0)
    end_ms = int(item.get("endTime") or 0)
    close_panel_ms = int(item.get("closePanelTime") or 0)
    if start_ms <= 0 or end_ms < start_ms:
        return None
    start_at = datetime.fromtimestamp(start_ms / 1000).astimezone()
    end_at = datetime.fromtimestamp(end_ms / 1000).astimezone()
    close_panel_at = (
        datetime.fromtimestamp(close_panel_ms / 1000).astimezone()
        if close_panel_ms >= end_ms
        else end_at
    )
    if not start_at.date() <= target_date <= end_at.date():
        return None
    return {
        "runtime_id": str(item.get("id") or ""),
        "game_activity_id": int(item.get("activityId") or 0),
        "cross_count": server_count,
        "start_time_ms": start_ms,
        "end_time_ms": end_ms,
        "close_panel_time_ms": close_panel_ms if close_panel_ms >= end_ms else end_ms,
        "start_date": start_at.date().isoformat(),
        "end_date": end_at.date().isoformat(),
        "close_panel_date": close_panel_at.date().isoformat(),
        "captured_at": captured_at,
        "record_id": record_id,
        "packet_id": packet_id,
        "world_level": int(item.get("avgWorldLevel") or 0),
        "source_kind": source_kind,
    }


def _runtime_period(
    session: Session,
    *,
    cross_count: int | None = None,
    target_date: date | None = None,
) -> dict[str, Any]:
    effective_date = target_date or date.today()
    # The compact Runtime snapshot is the current game truth. Missing state
    # fails closed; no historical network fallback is consulted.
    from backend.core.fanxiu.activity.runtime_schedule import (
        get_cached_fanxiu_activity_runtime_schedule,
    )

    schedule = get_cached_fanxiu_activity_runtime_schedule(
        max_runtime_age_seconds=6 * 60 * 60
    )
    for item in schedule.get("items") or []:
        if not isinstance(item, dict):
            continue
        period = _period_from_item(
            item,
            cross_count=cross_count,
            target_date=effective_date,
            captured_at=str(schedule.get("created_at") or ""),
            record_id=f"runtime:{item.get('id') or item.get('activityId') or ''}",
            packet_id="",
            source_kind=str(schedule.get("source_kind") or "runtime_cache"),
        )
        if period is not None:
            return period

    # Saved packets remain authoritative historical facts, but only an
    # occurrence covering the requested date may represent the current page.
    rows = session.exec(
        select(FanxiuPacketBusinessRecord)
        .where(FanxiuPacketBusinessRecord.domain == "worldline_activity")
        .order_by(col(FanxiuPacketBusinessRecord.captured_at).desc())
    ).all()
    for row in rows:
        payload = dict(row.payload or {})
        item = payload.get("item") if isinstance(payload.get("item"), dict) else {}
        period = _period_from_item(
            item,
            cross_count=cross_count,
            target_date=effective_date,
            captured_at=row.captured_at,
            record_id=row.id,
            packet_id=row.packet_id,
            source_kind="activity_packet_business_record",
        )
        if period is not None:
            return period
    raise ValueError("未找到兽渊探秘运行时活动实例")


def _activity_definition(game_activity_id: int) -> dict[str, Any]:
    path = resolve_fanxiu_export_root() / "parsed_configs/Activity/rows.json"
    for row in json.loads(path.read_text(encoding="utf-8")):
        if int(row.get("id") or 0) == int(game_activity_id):
            return dict(row)
    raise ValueError(f"兽渊探秘活动配置 {int(game_activity_id)} 不存在")


def _shop_snapshot(*, cross_count: int) -> dict[str, Any]:
    cards = load_fanxiu_item_runtime_index(rebuild_missing=False)["cards_by_id"]
    names = {
        int(item_id): str(card.get("name") or "")
        for item_id, card in cards.items()
        if str(item_id).isdigit() and isinstance(card, dict)
    }
    snapshot = collect_activity_shop_runtime(
        shop_base_id=BEAST_ABYSS_SHOP_BASE_ID,
        item_names=names,
        expected_currency_type=BEAST_ABYSS_CURRENCY_TYPE,
        expected_cross_count=int(cross_count),
    )
    if not snapshot.get("complete"):
        raise ValueError("兽渊探秘兑换宝阁运行态快照不完整")
    return snapshot


def _stored_ranking_rows(
    session: Session,
    *,
    activity_id: str,
    scopes: set[str],
) -> list[dict[str, Any]]:
    """Return already validated rows for scopes not refreshed this time.

    A companion rank is loaded on demand by the game.  Refreshing the
    personal rank must not erase a plane snapshot that was previously bound
    to this exact persisted activity occurrence.
    """

    if not scopes:
        return []
    rows = session.exec(
        select(FanxiuExchangeRanking).where(
            FanxiuExchangeRanking.activity_id == activity_id,
            FanxiuExchangeRanking.ranking_scope.in_(scopes),
        )
    ).all()
    return [
        {
            "ranking_scope": row.ranking_scope,
            "rank": row.rank,
            "score": row.score,
            "role_key": row.role_key,
            "name": row.name,
            "server_id": row.server_id,
            "server_name": row.server_name,
            "club_name": row.club_name,
            "is_self": row.is_self,
            "is_reward_guard": row.is_reward_guard,
            "is_last_player": row.is_last_player,
            "has_player": row.has_player,
            "reward_rank_start": row.reward_rank_start,
            "reward_rank_end": row.reward_rank_end,
            "raw_data": dict(row.raw_data or {}),
        }
        for row in rows
    ]


def collect_and_store_beast_abyss_activity(
    session: Session,
    *,
    activity_id: str | None = None,
    collect_runtime_shop: bool = True,
) -> Any:
    """Materialize one Beast Abyss occurrence in the generic activity model."""

    collection_started_at = time.perf_counter()
    phase_started_at = collection_started_at
    collection_phase_seconds: dict[str, float] = {}

    def finish_phase(name: str) -> None:
        nonlocal phase_started_at
        elapsed = time.perf_counter() - phase_started_at
        collection_phase_seconds[name] = elapsed
        phase_started_at = time.perf_counter()
        _LOGGER.info("beast-abyss-collect phase=%s elapsed=%.3fs", name, elapsed)

    from backend.core.fanxiu.activity.exchange_event import (
        list_exchange_activity_snapshot,
        replace_exchange_rankings,
        upsert_exchange_activity_snapshot,
    )
    from backend.core.fanxiu.activity.standard_observation import (
        ActivityObservationUnavailable,
        ActivityObservationSpec,
        collect_standard_activity_observation,
        read_activity_rank_fact,
        store_runtime_activity_rank_fact,
        store_runtime_currency_fact,
    )
    from backend.core.fanxiu.activity.exchange_activity_registry import (
        get_exchange_activity_spec,
    )

    existing = session.get(FanxiuExchangeActivity, activity_id) if activity_id else None
    if activity_id is not None and existing is None:
        raise ValueError("兽渊探秘活动实例不存在")
    if existing is not None and existing.activity_type != BEAST_ABYSS_ACTIVITY_TYPE:
        raise ValueError("兽渊探秘活动实例不存在")
    if collect_runtime_shop:
        # Explicit collection is allowed to refresh the strictly read-only
        # compact Runtime cache before any ranking/shop materialization.  A
        # failed refresh never overwrites the last complete cache.
        from backend.core.fanxiu.activity.runtime_schedule import (
            refresh_cached_fanxiu_activity_runtime_schedule,
        )

        refresh_cached_fanxiu_activity_runtime_schedule(allow_discovery=True)
        finish_phase("runtime_schedule")
    period = _runtime_period(
        session,
        cross_count=existing.cross_count if existing is not None else None,
        # The exchange page remains available through its grace period after
        # the activity itself disappears from the current Runtime schedule.
        # For an explicitly selected persisted occurrence, resolve the
        # authoritative historical packet covering that occurrence's end day
        # instead of incorrectly asking for an occurrence covering today.
        target_date=(
            date.fromisoformat(existing.end_date)
            if existing is not None
            else None
        ),
    )
    finish_phase("period")
    occurrence_runtime_id = str(period.get("runtime_id") or "")
    if existing is not None and (
        existing.start_date != period["start_date"]
        or existing.end_date != period["end_date"]
    ):
        raise ValueError("兽渊探秘活动实例与当前运行时周期不一致")
    definition = _activity_definition(period["game_activity_id"])
    follow = [int(value) for value in definition.get("follow") or []]
    if not follow:
        raise ValueError("兽渊探秘配置没有声明榜单")
    activity_spec = get_exchange_activity_spec(BEAST_ABYSS_ACTIVITY_TYPE)
    resolved_scopes: list[tuple[Any, int]] = []
    for scope_spec in activity_spec.rank_scopes:
        try:
            rank_id = scope_spec.activity_id.resolve(activity_follow=tuple(follow))
        except ValueError:
            if scope_spec.required:
                raise
            continue
        resolved_scopes.append((scope_spec, rank_id))
    primary_scope_spec = next(
        scope for scope, _rank_id in resolved_scopes
        if scope.effective_role == "primary"
    )
    personal_rank_id = next(
        rank_id for scope, rank_id in resolved_scopes
        if scope is primary_scope_spec
    )
    related = tuple(
        (scope.scope, rank_id) for scope, rank_id in resolved_scopes
        if scope.effective_role == "comparative"
    )
    if collect_runtime_shop:
        from backend.core.fanxiu.instrumentation.activity_rank_runtime import (
            prepare_activity_rank_runtime,
            read_activity_rank_runtime_snapshot,
        )

        runtime_rank = read_activity_rank_runtime_snapshot(personal_rank_id)
        if (
            not runtime_rank.get("ok")
            and runtime_rank.get("error_code")
            in {"process_cache_miss", "root_cache_miss"}
        ):
            recovery = prepare_activity_rank_runtime([personal_rank_id])
            if not recovery.get("ok"):
                raise ActivityObservationUnavailable(
                    str(recovery.get("reason") or "兽渊探秘榜单 Runtime 恢复失败")
                )
            runtime_rank = read_activity_rank_runtime_snapshot(personal_rank_id)
        if not runtime_rank.get("ok") or not runtime_rank.get("complete"):
            raise ActivityObservationUnavailable(
                str(runtime_rank.get("reason") or "兽渊探秘榜单 Runtime 尚未加载")
            )
        store_runtime_activity_rank_fact(
            session,
            runtime_rank,
            occurrence_runtime_id=occurrence_runtime_id,
        )
        finish_phase("rank")
    previous_evidence = dict(existing.evidence or {}) if existing is not None else {}
    previous_refresh_status = previous_evidence.get("refresh_status")
    previous_refresh_status = (
        dict(previous_refresh_status)
        if isinstance(previous_refresh_status, dict)
        else {}
    )
    currency_refresh_status = "retained"
    currency_refresh_reason = "只读数据库物化未请求运行态钱包"
    currency_runtime_evidence: dict[str, Any] = {}
    if collect_runtime_shop:
        from backend.core.fanxiu.instrumentation.runtime_memory import (
            FanxiuRuntimeMemoryError,
        )

        try:
            runtime_currency = _runtime_currency_snapshot()
            store_runtime_currency_fact(session, runtime_currency)
            currency_refresh_status = "updated"
            currency_refresh_reason = ""
            currency_runtime_evidence = dict(runtime_currency.get("evidence") or {})
        except (FanxiuRuntimeMemoryError, ValueError) as exc:
            # A cold Wallet manager must not erase a durable absolute fact. The
            # response explicitly says retained/stale instead of claiming an
            # update from the old database value.
            currency_refresh_status = "retained"
            currency_refresh_reason = str(exc)
        finish_phase("wallet")

    observation = collect_standard_activity_observation(
        session,
        ActivityObservationSpec(
            rank_activity_id=personal_rank_id,
            currency_type=BEAST_ABYSS_CURRENCY_TYPE,
            related_rank_activity_ids=related,
            primary_scope=primary_scope_spec.scope,
            row_mode=primary_scope_spec.row_mode,
        ),
    )
    fact_end_date = str(period.get("close_panel_date") or period["end_date"])
    personal_captured_date = str(
        observation.get("evidence", {}).get("rank_captured_at") or ""
    )[:10]
    if not (
        period["start_date"] <= personal_captured_date <= fact_end_date
    ):
        raise ActivityObservationUnavailable(
            "兽渊探秘个人榜事实不属于当前活动周期"
        )
    declared_related_scopes = {scope for scope, _rank_id in related}
    current_related_scopes: set[str] = set()
    current_related_captured_at: list[str] = []
    for scope, rank_id in related:
        try:
            fact = read_activity_rank_fact(session, rank_id)
        except ActivityObservationUnavailable:
            continue
        captured_date = str(fact.get("captured_at") or "")[:10]
        if period["start_date"] <= captured_date <= fact_end_date:
            current_related_scopes.add(scope)
            current_related_captured_at.append(str(fact.get("captured_at") or ""))
    observation["rankings"] = [
        row for row in observation["rankings"]
        if str(row.get("ranking_scope") or primary_scope_spec.scope) == primary_scope_spec.scope
        or str(row.get("ranking_scope") or "") in current_related_scopes
    ]
    retained_related_scopes: set[str] = set()
    if existing is not None:
        missing_scopes = declared_related_scopes - current_related_scopes
        retained_rows = _stored_ranking_rows(
            session,
            activity_id=existing.id,
            scopes=missing_scopes,
        )
        observation["rankings"].extend(retained_rows)
        retained_related_scopes = {
            str(row["ranking_scope"]) for row in retained_rows
        }
    observation["captured_at"] = max(
        str(observation.get("evidence", {}).get("rank_captured_at") or ""),
        str(observation.get("evidence", {}).get("currency_captured_at") or ""),
        *current_related_captured_at,
    )
    finish_phase("rankings")

    shop: dict[str, Any] | None = None
    has_shop = bool(existing) and session.exec(
        select(FanxiuExchangeShopItem.id)
        .where(FanxiuExchangeShopItem.activity_id == existing.id)
        .limit(1)
    ).first() is not None
    shop_reason = "只读事实刷新未请求游戏内商店投影"
    if collect_runtime_shop:
        shop_reason = ""
        try:
            # The explicit collection endpoint is a refresh operation.  Always
            # retry the runtime projection, even after an earlier shop snapshot
            # was stored; if the game has not loaded it, retain the last complete
            # snapshot instead of replacing it with an empty list.
            shop = _shop_snapshot(cross_count=period["cross_count"])
        except FanxiuActivityShopNotLoadedError as exc:
            if not has_shop:
                raise ValueError(f"兽渊探秘兑换宝阁尚未加载：{exc}") from exc
            shop_reason = str(exc)
        except FanxiuActivityShopCollectionError as exc:
            # A base-id, active-index, cross-group or completeness mismatch is
            # a failed collection, not a successful refresh with zero rows.
            raise ValueError(f"兽渊探秘兑换宝阁采集失败：{exc}") from exc
        finish_phase("shop")

    evidence = previous_evidence
    from backend.core.fanxiu.activity.rank_reward_context import (
        server_day_for_start_time,
    )

    server_day = server_day_for_start_time(session, period.get("start_time_ms"))
    refresh_status = (
        previous_refresh_status
        if not collect_runtime_shop
        else {
            "currency": currency_refresh_status,
            "currency_reason": currency_refresh_reason,
            "currency_stale": currency_refresh_status != "updated",
            "shop": "updated" if shop is not None else "retained",
            "shop_reason": shop_reason or ("" if shop is not None else "尚无完整商店快照"),
            "shop_had_persisted_snapshot": has_shop,
        }
    )
    refresh_status.update({
        "currency_captured_at": str(
            observation.get("evidence", {}).get("currency_captured_at") or ""
        ),
        "rankings": "updated",
    })
    evidence.update({
        "game_activity_id": period["game_activity_id"],
        "period_start_time_ms": period.get("start_time_ms"),
        "period_end_time_ms": period.get("end_time_ms"),
        "period_close_panel_time_ms": period.get("close_panel_time_ms"),
        "period_close_panel_date": period.get("close_panel_date"),
        "server_day": server_day,
        "period_record_id": period["record_id"],
        "period_packet_id": period["packet_id"],
        "world_level": period["world_level"],
        "rank_activity_ids": follow,
        "rank_scope_activity_ids": {
            scope.scope: rank_id for scope, rank_id in resolved_scopes
        },
        "current_related_ranking_scopes": sorted(current_related_scopes),
        "retained_related_ranking_scopes": sorted(retained_related_scopes),
        # A ranking-only GET does not observe the wallet or shop again.  It
        # must preserve the last explicit Runtime freshness envelope instead
        # of interpreting omitted collection as evidence that both facts are
        # stale.  Explicit collection still replaces the envelope atomically.
        "refresh_status": refresh_status,
        "collection_phase_seconds": collection_phase_seconds,
    })
    if currency_runtime_evidence:
        evidence["currency_runtime"] = currency_runtime_evidence
    if shop is not None:
        evidence["shop"] = dict(shop.get("evidence") or {})
    payload: dict[str, Any] = {
        "activity_type": BEAST_ABYSS_ACTIVITY_TYPE,
        "cross_count": period["cross_count"],
        "start_date": period["start_date"],
        "end_date": period["end_date"],
        "game_rank_activity_id": personal_rank_id,
        "game_shop_base_id": BEAST_ABYSS_SHOP_BASE_ID,
        "currency_type": BEAST_ABYSS_CURRENCY_TYPE,
        "currency_name": BEAST_ABYSS_CURRENCY_NAME,
        "current_currency": int(observation["exchange_currency"]),
        "cumulative_currency": int(observation["cumulative_currency"]),
        "captured_at": str(observation["captured_at"]),
        "source_kind": "read_only_runtime_facts",
        "resource_strategy": {
            "活动方式": "探索兽渊并积累兽元",
        },
        "evidence": evidence,
    }
    if shop is not None:
        payload["shop_items"] = list(shop["items"])
        payload["expected_shop_item_count"] = int(shop["active_shop_item_count"])
    persisted_id = upsert_exchange_activity_snapshot(session, payload)
    replace_exchange_rankings(
        session,
        activity_type=BEAST_ABYSS_ACTIVITY_TYPE,
        activity_id=persisted_id,
        rows=list(observation["rankings"]),
        captured_at=str(observation["captured_at"]),
    )
    _LOGGER.info(
        "beast-abyss-collect phase=persist elapsed=%.3fs total=%.3fs",
        time.perf_counter() - phase_started_at,
        time.perf_counter() - collection_started_at,
    )
    return list_exchange_activity_snapshot(
        session,
        activity_type=BEAST_ABYSS_ACTIVITY_TYPE,
        activity_id=persisted_id,
    ).selected_activity


def ensure_beast_abyss_activity(session: Session) -> None:
    from backend.core.fanxiu.activity.standard_observation import (
        ActivityObservationUnavailable,
    )

    period = _runtime_period(session)
    existing = session.exec(
        select(FanxiuExchangeActivity).where(
            FanxiuExchangeActivity.activity_type == BEAST_ABYSS_ACTIVITY_TYPE,
            FanxiuExchangeActivity.cross_count == period["cross_count"],
            FanxiuExchangeActivity.start_date == period["start_date"],
            FanxiuExchangeActivity.end_date == period["end_date"],
        )
    ).first()
    try:
        # GET remains DB-backed and side-effect free with respect to the game,
        # but it must still catch up when Runtime has produced newer
        # currency/ranking facts after the instance was first created.
        collect_and_store_beast_abyss_activity(
            session,
            activity_id=existing.id if existing is not None else None,
            collect_runtime_shop=False,
        )
    except ActivityObservationUnavailable:
        # A not-yet-loaded personal rank must not make historical snapshots
        # disappear. The explicit collection endpoint will report the error.
        return
