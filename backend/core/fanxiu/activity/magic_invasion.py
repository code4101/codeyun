from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Mapping

from sqlmodel import Session, col, select

from backend.core.fanxiu.activity.standard_exchange_materializer import (
    merge_occurrence_rankings,
    persist_exchange_materialization,
)
from backend.core.fanxiu.catalog.item import load_fanxiu_item_runtime_index
from backend.core.fanxiu.catalog.resources import resolve_fanxiu_export_root
from backend.core.fanxiu.instrumentation.activity_shop import (
    FanxiuActivityShopCollectionError,
    FanxiuActivityShopNotLoadedError,
    collect_activity_shop_runtime,
)
from backend.models import (
    FanxiuExchangeActivity,
    FanxiuExchangeShopItem,
    FanxiuPacketBusinessRecord,
)


MAGIC_INVASION_ACTIVITY_TYPE = "magic-invasion"
MAGIC_INVASION_SERVER_SHOP_BASE_ID = 70000
MAGIC_INVASION_SERVER_CURRENCY_TYPE = 15
MAGIC_INVASION_SHOP_BASE_ID = 70001
MAGIC_INVASION_CURRENCY_TYPE = 17
MAGIC_INVASION_CURRENCY_NAME = "魔晶"


def resolve_magic_invasion_shop_identity(
    *, cross_count: int
) -> tuple[int, int, int | None]:
    """Resolve independent server-internal and cross-server shop identities."""

    if int(cross_count) <= 1:
        # The server-internal V_ShowList does not expose a cross-count cohort.
        return (
            MAGIC_INVASION_SERVER_SHOP_BASE_ID,
            MAGIC_INVASION_SERVER_CURRENCY_TYPE,
            None,
        )
    return MAGIC_INVASION_SHOP_BASE_ID, MAGIC_INVASION_CURRENCY_TYPE, int(cross_count)


# Compatibility for existing callers and tests while the public resolver is
# adopted by the shared ranking lifecycle adapter.
_shop_identity = resolve_magic_invasion_shop_identity


def _runtime_currency_snapshot(*, cross_count: int) -> dict[str, Any]:
    """Read the current activity instance's wallet currency."""

    from backend.core.fanxiu.instrumentation.wallet import (
        read_wallet_currency_snapshot,
    )

    _shop_base_id, currency_type, _expected_cross_count = resolve_magic_invasion_shop_identity(
        cross_count=cross_count
    )
    return read_wallet_currency_snapshot(
        currency_type,
        # This path is only reached by the explicit collect endpoint. A cold
        # cache may perform the existing process-external read-only discovery;
        # GET/ensure never enters it.
        allow_discovery=True,
    )


def _runtime_period(session: Session, *, cross_count: int | None = None) -> dict[str, Any]:
    rows = session.exec(
        select(FanxiuPacketBusinessRecord)
        .where(FanxiuPacketBusinessRecord.domain == "worldline_activity")
        .order_by(col(FanxiuPacketBusinessRecord.captured_at).desc())
    ).all()
    for row in rows:
        payload = dict(row.payload or {})
        item = payload.get("item") if isinstance(payload.get("item"), dict) else {}
        if str(item.get("class") or "") != "MagicInvadeActivityVO":
            continue
        server_count = int(item.get("serverCount") or 1)
        if cross_count is not None and server_count != int(cross_count):
            continue
        start_ms = int(item.get("startTime") or 0)
        end_ms = int(item.get("endTime") or 0)
        if start_ms <= 0 or end_ms < start_ms:
            continue
        start_at = datetime.fromtimestamp(start_ms / 1000).astimezone()
        end_at = datetime.fromtimestamp(end_ms / 1000).astimezone()
        return {
            "game_activity_id": int(item.get("activityId") or 0),
            "cross_count": server_count,
            "start_date": start_at.date().isoformat(),
            "end_date": end_at.date().isoformat(),
            "captured_at": row.captured_at,
            "record_id": row.id,
            "packet_id": row.packet_id,
            "world_level": int(item.get("avgWorldLevel") or 0),
        }
    raise ValueError("未找到魔道入侵运行时活动实例")


def _activity_definition(game_activity_id: int) -> dict[str, Any]:
    path = resolve_fanxiu_export_root() / "parsed_configs/Activity/rows.json"
    for row in json.loads(path.read_text(encoding="utf-8")):
        if int(row.get("id") or 0) == int(game_activity_id):
            return dict(row)
    raise ValueError(f"魔道入侵活动配置 {int(game_activity_id)} 不存在")


def _shop_snapshot(*, cross_count: int) -> dict[str, Any]:
    cards = load_fanxiu_item_runtime_index(rebuild_missing=False)["cards_by_id"]
    names = {
        int(item_id): str(card.get("name") or "")
        for item_id, card in cards.items()
        if str(item_id).isdigit() and isinstance(card, dict)
    }
    shop_base_id, currency_type, expected_cross_count = resolve_magic_invasion_shop_identity(
        cross_count=cross_count
    )
    snapshot = collect_activity_shop_runtime(
        shop_base_id=shop_base_id,
        item_names=names,
        # The server-internal dictionary projection is not partitioned by
        # currency. Decode its active table first, then validate the result.
        expected_currency_type=(currency_type if expected_cross_count is not None else None),
        expected_cross_count=expected_cross_count,
    )
    if not snapshot.get("complete"):
        raise ValueError("魔道入侵兑换宝阁运行态快照不完整")
    if set(snapshot.get("currency_types") or ()) != {currency_type}:
        raise ValueError("魔道入侵兑换宝阁币种与活动实例不一致")
    return snapshot




def collect_and_store_magic_invasion_activity(
    session: Session,
    *,
    activity_id: str | None = None,
    collect_runtime_shop: bool = True,
    runtime_period_override: Mapping[str, Any] | None = None,
) -> Any:
    """Materialize one Magic Invasion occurrence in the generic activity model."""

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
        raise ValueError("魔道入侵活动实例不存在")
    if existing is not None and existing.activity_type != MAGIC_INVASION_ACTIVITY_TYPE:
        raise ValueError("魔道入侵活动实例不存在")
    if runtime_period_override is not None:
        if existing is None:
            raise ValueError("历史魔道周期绑定要求已存在的活动实例")
        period = dict(runtime_period_override)
        required = {
            "game_activity_id",
            "cross_count",
            "start_date",
            "end_date",
            "captured_at",
            "record_id",
            "packet_id",
            "world_level",
            "runtime_id",
        }
        missing = sorted(required - set(period))
        if missing:
            raise ValueError(f"历史魔道周期绑定缺少字段：{missing[0]}")
        expected_game_activity_id = int(
            dict(existing.evidence or {}).get("game_activity_id") or 0
        )
        expected_runtime_id = str(
            dict(existing.evidence or {}).get("runtime_id") or ""
        )
        if (
            int(period["cross_count"]) != int(existing.cross_count)
            or str(period["start_date"]) != str(existing.start_date)
            or str(period["end_date"]) != str(existing.end_date)
            or int(period["game_activity_id"]) != expected_game_activity_id
            or str(period["runtime_id"]) != expected_runtime_id
        ):
            raise ValueError("历史魔道周期绑定与持久化 occurrence 不一致")
    else:
        period = _runtime_period(
            session,
            cross_count=existing.cross_count if existing is not None else None,
        )
    if existing is not None and (
        existing.start_date != period["start_date"]
        or existing.end_date != period["end_date"]
    ):
        raise ValueError("魔道入侵活动实例与当前运行时周期不一致")
    definition = _activity_definition(period["game_activity_id"])
    follow = [int(value) for value in definition.get("follow") or []]
    if not follow:
        raise ValueError("魔道入侵配置没有声明榜单")
    activity_spec = get_exchange_activity_spec(MAGIC_INVASION_ACTIVITY_TYPE)
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
    if runtime_period_override is not None and collect_runtime_shop:
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
                    str(recovery.get("reason") or "魔道入侵榜单 Runtime 恢复失败")
                )
            runtime_rank = read_activity_rank_runtime_snapshot(personal_rank_id)
        if not runtime_rank.get("ok") or not runtime_rank.get("complete"):
            raise ActivityObservationUnavailable(
                str(runtime_rank.get("reason") or "魔道入侵榜单 Runtime 尚未加载")
            )
        store_runtime_activity_rank_fact(
            session,
            runtime_rank,
            occurrence_runtime_id=str(period["runtime_id"]),
        )
    currency_refresh_status = "retained"
    currency_refresh_reason = "只读数据库物化未请求运行态钱包"
    currency_runtime_evidence: dict[str, Any] = {}
    if collect_runtime_shop:
        from backend.core.fanxiu.instrumentation.runtime_memory import (
            FanxiuRuntimeMemoryError,
        )

        try:
            runtime_currency = _runtime_currency_snapshot(
                cross_count=period["cross_count"]
            )
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

    shop_base_id, currency_type, _expected_cross_count = resolve_magic_invasion_shop_identity(
        cross_count=period["cross_count"]
    )
    observation = collect_standard_activity_observation(
        session,
        ActivityObservationSpec(
            rank_activity_id=personal_rank_id,
            currency_type=currency_type,
            related_rank_activity_ids=related,
            primary_scope=primary_scope_spec.scope,
            row_mode=primary_scope_spec.row_mode,
        ),
    )
    personal_captured_date = str(
        observation.get("evidence", {}).get("rank_captured_at") or ""
    )[:10]
    rank_occurrence_runtime_id = str(
        observation.get("evidence", {})
        .get("rank", {})
        .get("occurrence_runtime_id")
        or ""
    )
    rank_bound_to_override = bool(
        runtime_period_override is not None
        and rank_occurrence_runtime_id == str(period["runtime_id"])
    )
    if not rank_bound_to_override and not (
        period["start_date"] <= personal_captured_date <= period["end_date"]
    ):
        raise ActivityObservationUnavailable(
            "魔道入侵个人榜事实不属于当前活动周期"
        )
    ranking_merge = merge_occurrence_rankings(
        session,
        observation=observation,
        existing_activity_id=existing.id if existing is not None else None,
        primary_scope=primary_scope_spec.scope,
        related_rank_activity_ids=related,
        valid_from=period["start_date"],
        valid_through=period["end_date"],
    )
    observation["rankings"] = list(ranking_merge.rankings)
    observation["captured_at"] = ranking_merge.captured_at

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
                raise ValueError(f"魔道入侵兑换宝阁尚未加载：{exc}") from exc
            shop_reason = str(exc)
        except FanxiuActivityShopCollectionError as exc:
            # A base-id, active-index, cross-group or completeness mismatch is
            # a failed collection, not a successful refresh with zero rows.
            raise ValueError(f"魔道入侵兑换宝阁采集失败：{exc}") from exc

    evidence = dict(existing.evidence or {}) if existing is not None else {}
    evidence.update({
        "game_activity_id": period["game_activity_id"],
        "period_record_id": period["record_id"],
        "period_packet_id": period["packet_id"],
        "world_level": period["world_level"],
        "rank_activity_ids": follow,
        "rank_scope_activity_ids": {
            scope.scope: rank_id for scope, rank_id in resolved_scopes
        },
        "current_related_ranking_scopes": sorted(ranking_merge.current_related_scopes),
        "retained_related_ranking_scopes": sorted(ranking_merge.retained_related_scopes),
        "refresh_status": {
            "currency": currency_refresh_status,
            "currency_reason": currency_refresh_reason,
            "currency_stale": bool(
                collect_runtime_shop and currency_refresh_status != "updated"
            ),
            "currency_captured_at": str(
                observation.get("evidence", {}).get("currency_captured_at") or ""
            ),
            "rankings": "updated",
            "shop": "updated" if shop is not None else "retained",
            "shop_reason": shop_reason or ("" if shop is not None else "尚无完整商店快照"),
            "shop_had_persisted_snapshot": has_shop,
        },
    })
    if currency_runtime_evidence:
        evidence["currency_runtime"] = currency_runtime_evidence
    if shop is not None:
        evidence["shop"] = dict(shop.get("evidence") or {})
    payload: dict[str, Any] = {
        "activity_type": MAGIC_INVASION_ACTIVITY_TYPE,
        "cross_count": period["cross_count"],
        "start_date": period["start_date"],
        "end_date": period["end_date"],
        "game_rank_activity_id": personal_rank_id,
        "game_shop_base_id": shop_base_id,
        "currency_type": currency_type,
        "currency_name": MAGIC_INVASION_CURRENCY_NAME,
        "current_currency": int(observation["exchange_currency"]),
        "cumulative_currency": int(observation["cumulative_currency"]),
        "captured_at": str(observation["captured_at"]),
        "source_kind": "read_only_runtime_facts",
        "resource_strategy": {
            "执行方式": "自动除魔，从最高等级开始循环挑战",
            "敌人筛选": "关闭长老以下，开启长老及以上",
            "停止条件": "达到目标功勋后停止",
        },
        "evidence": evidence,
    }
    if shop is not None:
        payload["shop_items"] = list(shop["items"])
        payload["expected_shop_item_count"] = int(shop["active_shop_item_count"])
    return persist_exchange_materialization(
        session,
        activity_type=MAGIC_INVASION_ACTIVITY_TYPE,
        payload=payload,
        rankings=list(observation["rankings"]),
        captured_at=str(observation["captured_at"]),
    )


def ensure_magic_invasion_activity(session: Session) -> None:
    from backend.core.fanxiu.activity.standard_observation import (
        ActivityObservationUnavailable,
    )

    period = _runtime_period(session)
    existing = session.exec(
        select(FanxiuExchangeActivity).where(
            FanxiuExchangeActivity.activity_type == MAGIC_INVASION_ACTIVITY_TYPE,
            FanxiuExchangeActivity.cross_count == period["cross_count"],
            FanxiuExchangeActivity.start_date == period["start_date"],
            FanxiuExchangeActivity.end_date == period["end_date"],
        )
    ).first()
    try:
        # GET remains DB-backed and side-effect free with respect to the game,
        # but it must still catch up when Runtime has produced newer
        # currency/ranking facts after the instance was first created.
        collect_and_store_magic_invasion_activity(
            session,
            activity_id=existing.id if existing is not None else None,
            collect_runtime_shop=False,
        )
    except ActivityObservationUnavailable:
        # A not-yet-loaded personal rank must not make historical snapshots
        # disappear. The explicit collection endpoint will report the error.
        return
