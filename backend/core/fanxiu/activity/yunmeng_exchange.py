from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from sqlmodel import Session, col, select

from backend.core.fanxiu.activity.standard_exchange_materializer import (
    merge_occurrence_rankings,
    persist_exchange_materialization,
)
from backend.core.fanxiu.activity.yunmeng_trial_instrumentation import (
    collect_yunmeng_trial_shop_snapshot,
)
from backend.core.fanxiu.catalog.resources import resolve_fanxiu_export_root
from backend.core.fanxiu.instrumentation.activity_shop import (
    FanxiuActivityShopCollectionError,
    FanxiuActivityShopNotLoadedError,
)
from backend.models import (
    FanxiuExchangeActivity,
    FanxiuExchangeShopItem,
    FanxiuPacketBusinessRecord,
)


YUNMENG_ACTIVITY_TYPE = "yunmeng-trial"
YUNMENG_WORLDLINE_VO = "YunmengActivityVO"
YUNMENG_GAME_ACTIVITY_TYPE = 21
YUNMENG_SHOP_BASE_ID = 210001
YUNMENG_CURRENCY_TYPE = 19
YUNMENG_CURRENCY_NAME = "论剑玉"


def read_yunmeng_currency_snapshot(*, allow_discovery: bool) -> dict[str, Any]:
    """读取云梦论剑玉，按客户端零语义处理"余额为 0 时省略币种条目"。

    WalletData.GetCurrencyByType 在余额恰为 0 时返回 0，且游戏会省略该币种的
    WalletVO；此时严格读取会报"尚未同步到 Runtime"，让整条兑换预算链永远无法
    就绪。只有在钱包模型本身已初始化、且失败原因确实是该币种条目缺失时，才退到
    显式零语义，并把这次假设写进 evidence 供审计；进程/地址缓存失效等其它错误
    仍然照原样抛出。
    """

    from backend.core.fanxiu.instrumentation.runtime_memory import (
        FanxiuRuntimeMemoryError,
    )
    from backend.core.fanxiu.instrumentation.wallet import (
        read_wallet_currency_snapshot,
    )

    try:
        return read_wallet_currency_snapshot(
            YUNMENG_CURRENCY_TYPE,
            allow_discovery=allow_discovery,
        )
    except FanxiuRuntimeMemoryError as exc:
        message = str(exc)
        if f"兑币类型 {YUNMENG_CURRENCY_TYPE} " not in message:
            raise
        snapshot = read_wallet_currency_snapshot(
            YUNMENG_CURRENCY_TYPE,
            allow_discovery=allow_discovery,
            missing_as_zero=True,
        )
        evidence = dict(snapshot.get("evidence") or {})
        evidence["zero_balance_currency_absent"] = True
        evidence["strict_read_error"] = message
        return {**snapshot, "evidence": evidence}


def _period_from_item(
    item: dict[str, Any],
    *,
    cross_count: int | None,
    target_date: date,
    captured_at: str,
    record_id: str,
    packet_id: str,
) -> dict[str, Any] | None:
    if not (
        str(item.get("class") or "") == YUNMENG_WORLDLINE_VO
        or int(item.get("activityType") or 0) == YUNMENG_GAME_ACTIVITY_TYPE
        and str(item.get("name") or "") == "云梦试剑"
    ):
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
        "game_activity_id": int(item.get("activityId") or 0),
        "cross_count": server_count,
        "start_date": start_at.date().isoformat(),
        "end_date": end_at.date().isoformat(),
        "close_panel_date": close_panel_at.date().isoformat(),
        "captured_at": captured_at,
        "record_id": record_id,
        "packet_id": packet_id,
        "world_level": int(item.get("avgWorldLevel") or 0),
    }


def _runtime_period(
    session: Session,
    *,
    cross_count: int | None = None,
    target_date: date | None = None,
) -> dict[str, Any]:
    effective_date = target_date or date.today()
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
        )
        if period is not None:
            return period

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
        )
        if period is not None:
            return period
    raise ValueError("未找到云梦试剑运行时活动实例")


def _activity_definition(game_activity_id: int) -> dict[str, Any]:
    path = resolve_fanxiu_export_root() / "parsed_configs/Activity/rows.json"
    for row in json.loads(path.read_text(encoding="utf-8")):
        if int(row.get("id") or 0) == int(game_activity_id):
            return dict(row)
    raise ValueError(f"云梦试剑活动配置 {int(game_activity_id)} 不存在")


def _shop_snapshot(*, cross_count: int) -> dict[str, Any]:
    snapshot = collect_yunmeng_trial_shop_snapshot()
    if int(snapshot.get("cross_count") or 0) != int(cross_count):
        raise ValueError("云梦试剑商店跨服数与当前活动实例不一致")
    return snapshot




def collect_and_store_yunmeng_exchange_activity(
    session: Session,
    *,
    activity_id: str | None = None,
    collect_runtime_shop: bool = True,
) -> Any:
    """Materialize one Yunmeng occurrence in the generic exchange model."""

    from backend.core.fanxiu.activity.exchange_activity_registry import (
        get_exchange_activity_spec,
    )
    from backend.core.fanxiu.activity.standard_observation import (
        ActivityObservationUnavailable,
        ActivityObservationSpec,
        collect_standard_activity_observation,
        read_activity_rank_fact,
        store_runtime_currency_fact,
    )

    existing = session.get(FanxiuExchangeActivity, activity_id) if activity_id else None
    if activity_id is not None and (
        existing is None or existing.activity_type != YUNMENG_ACTIVITY_TYPE
    ):
        raise ValueError("云梦试剑活动实例不存在")
    if collect_runtime_shop:
        from backend.core.fanxiu.activity.runtime_schedule import (
            refresh_cached_fanxiu_activity_runtime_schedule,
        )

        refresh_cached_fanxiu_activity_runtime_schedule(allow_discovery=True)
    period = _runtime_period(
        session,
        cross_count=existing.cross_count if existing is not None else None,
        target_date=(date.fromisoformat(existing.end_date) if existing else None),
    )
    if existing is not None and (
        existing.start_date != period["start_date"]
        or existing.end_date != period["end_date"]
    ):
        raise ValueError("云梦试剑活动实例与当前运行时周期不一致")

    definition = _activity_definition(period["game_activity_id"])
    follow = tuple(int(value) for value in definition.get("follow") or [])
    spec = get_exchange_activity_spec(YUNMENG_ACTIVITY_TYPE)
    resolved_scopes: list[tuple[Any, int]] = []
    for scope_spec in spec.rank_scopes:
        try:
            rank_id = scope_spec.runtime_rank_activity_id.resolve(activity_follow=follow)
        except ValueError:
            if scope_spec.required:
                raise
            continue
        resolved_scopes.append((scope_spec, rank_id))
    primary_spec, personal_rank_id = next(
        (scope, rank_id)
        for scope, rank_id in resolved_scopes
        if scope.effective_role == "primary"
    )
    related = tuple(
        (scope.scope, rank_id)
        for scope, rank_id in resolved_scopes
        if scope.effective_role == "comparative"
    )

    previous_evidence = dict(existing.evidence or {}) if existing else {}
    previous_refresh = previous_evidence.get("refresh_status")
    previous_refresh = dict(previous_refresh) if isinstance(previous_refresh, dict) else {}
    currency_status = "retained"
    currency_reason = "只读数据库物化未请求运行态钱包"
    currency_runtime_evidence: dict[str, Any] = {}
    if collect_runtime_shop:
        from backend.core.fanxiu.instrumentation.runtime_memory import (
            FanxiuRuntimeMemoryError,
        )

        try:
            currency = read_yunmeng_currency_snapshot(allow_discovery=True)
            store_runtime_currency_fact(session, currency)
            currency_status = "updated"
            currency_reason = ""
            currency_runtime_evidence = dict(currency.get("evidence") or {})
        except (FanxiuRuntimeMemoryError, ValueError) as exc:
            currency_reason = str(exc)

    rank_runtime_status: dict[str, Any] = {}
    if collect_runtime_shop:
        # 云梦榜单事实长期只依赖抓包链路，页面开过也未必入库。已加载的活动榜
        # Runtime 投影是同一事实的只读来源，且与兑换宝阁共用一次页面窗口，因此
        # 在这里按标准事实通道落库，缺失时如实记录而不是伪造。
        from backend.core.fanxiu.activity.standard_observation import (
            store_runtime_activity_rank_fact,
        )
        from backend.core.fanxiu.instrumentation.activity_rank_runtime import (
            prepare_activity_rank_runtime,
            read_activity_rank_runtime_snapshot,
        )

        occurrence_runtime_id = str(existing.runtime_id if existing is not None else "")
        rank_targets = [
            (scope.scope, int(rank_id)) for scope, rank_id in resolved_scopes
        ]
        runtime_ranks = {
            rank_id: read_activity_rank_runtime_snapshot(rank_id)
            for _scope, rank_id in rank_targets
        }
        missing_rank_ids = [
            rank_id
            for _scope, rank_id in rank_targets
            if not runtime_ranks[rank_id].get("ok")
            and runtime_ranks[rank_id].get("error_code")
            in {"process_cache_miss", "root_cache_miss"}
        ]
        if missing_rank_ids:
            recovery = prepare_activity_rank_runtime(missing_rank_ids)
            if recovery.get("ok"):
                for rank_id in missing_rank_ids:
                    runtime_ranks[rank_id] = read_activity_rank_runtime_snapshot(
                        rank_id
                    )
            else:
                rank_runtime_status["recovery_error"] = str(
                    recovery.get("reason") or "云梦试剑榜单 Runtime 恢复失败"
                )
        if occurrence_runtime_id:
            for scope, rank_id in rank_targets:
                runtime_rank = runtime_ranks[rank_id]
                if not runtime_rank.get("ok") or not runtime_rank.get("complete"):
                    rank_runtime_status[scope] = str(
                        runtime_rank.get("reason") or "云梦试剑榜单尚未加载"
                    )
                    continue
                store_runtime_activity_rank_fact(
                    session,
                    runtime_rank,
                    occurrence_runtime_id=occurrence_runtime_id,
                )
                rank_runtime_status[scope] = "updated"
        else:
            rank_runtime_status["recovery_error"] = "云梦试剑实例缺少 runtime_id，无法绑定榜单事实"

    observation = collect_standard_activity_observation(
        session,
        ActivityObservationSpec(
            rank_activity_id=personal_rank_id,
            currency_type=YUNMENG_CURRENCY_TYPE,
            related_rank_activity_ids=related,
            primary_scope=primary_spec.scope,
            row_mode=primary_spec.row_mode,
            progress_protocols=(
                "SM_UpdateYunmengChallenge",
                "SM_YunmengPkQuickFight",
                "SM_YunmengPkMatch",
            ),
        ),
    )
    fact_end_date = str(period.get("close_panel_date") or period["end_date"])
    personal_date = str(observation.get("evidence", {}).get("rank_captured_at") or "")[:10]
    if not period["start_date"] <= personal_date <= fact_end_date:
        raise ActivityObservationUnavailable("云梦试剑个人榜事实不属于当前活动周期")

    ranking_merge = merge_occurrence_rankings(
        session,
        observation=observation,
        existing_activity_id=existing.id if existing is not None else None,
        primary_scope=primary_spec.scope,
        related_rank_activity_ids=related,
        valid_from=period["start_date"],
        valid_through=fact_end_date,
    )
    observation["rankings"] = list(ranking_merge.rankings)
    observation["captured_at"] = ranking_merge.captured_at

    has_shop = bool(existing) and session.exec(
        select(FanxiuExchangeShopItem.id)
        .where(FanxiuExchangeShopItem.activity_id == existing.id)
        .limit(1)
    ).first() is not None
    shop: dict[str, Any] | None = None
    shop_reason = "只读事实刷新未请求游戏内商店投影"
    if collect_runtime_shop:
        try:
            shop = _shop_snapshot(cross_count=period["cross_count"])
            shop_reason = ""
        except FanxiuActivityShopNotLoadedError as exc:
            if not has_shop:
                raise ValueError(f"云梦试剑兑换宝阁尚未加载：{exc}") from exc
            shop_reason = str(exc)
        except FanxiuActivityShopCollectionError as exc:
            raise ValueError(f"云梦试剑兑换宝阁采集失败：{exc}") from exc

    refresh_status = previous_refresh if not collect_runtime_shop else {
        "currency": currency_status,
        "currency_reason": currency_reason,
        "currency_stale": currency_status != "updated",
        "shop": "updated" if shop is not None else "retained",
        "shop_reason": shop_reason or ("" if shop is not None else "尚无完整商店快照"),
        "shop_had_persisted_snapshot": has_shop,
    }
    refresh_status.update(
        currency_captured_at=str(
            observation.get("evidence", {}).get("currency_captured_at") or ""
        ),
        rankings="updated",
    )
    evidence = previous_evidence
    evidence.update(
        game_activity_id=period["game_activity_id"],
        period_record_id=period["record_id"],
        period_packet_id=period["packet_id"],
        period_close_panel_date=period.get("close_panel_date"),
        world_level=period["world_level"],
        rank_activity_ids=list(follow),
        rank_scope_identities={
            scope.scope: {
                "runtime_rank_activity_id": rank_id,
                "reward_activity_id": rank_id if scope.reward_tiers_enabled else None,
            }
            for scope, rank_id in resolved_scopes
        },
        current_related_ranking_scopes=sorted(ranking_merge.current_related_scopes),
        retained_related_ranking_scopes=sorted(ranking_merge.retained_related_scopes),
        rank_runtime=rank_runtime_status,
        refresh_status=refresh_status,
    )
    if currency_runtime_evidence:
        evidence["currency_runtime"] = currency_runtime_evidence
    if shop is not None:
        evidence["shop"] = dict(shop.get("evidence") or {})

    payload: dict[str, Any] = {
        "activity_type": YUNMENG_ACTIVITY_TYPE,
        "cross_count": period["cross_count"],
        "start_date": period["start_date"],
        "end_date": period["end_date"],
        "game_rank_activity_id": personal_rank_id,
        "game_shop_base_id": YUNMENG_SHOP_BASE_ID,
        "currency_type": YUNMENG_CURRENCY_TYPE,
        "currency_name": YUNMENG_CURRENCY_NAME,
        "current_currency": int(observation["exchange_currency"]),
        "cumulative_currency": int(observation["cumulative_currency"]),
        "captured_at": str(observation["captured_at"]),
        "source_kind": "read_only_runtime_facts",
        "resource_strategy": {"活动方式": "原生自动挑战并积累论剑玉"},
        "evidence": evidence,
    }
    if shop is not None:
        payload["shop_items"] = list(shop["items"])
        payload["expected_shop_item_count"] = int(shop["active_shop_item_count"])
    return persist_exchange_materialization(
        session,
        activity_type=YUNMENG_ACTIVITY_TYPE,
        payload=payload,
        rankings=list(observation["rankings"]),
        captured_at=str(observation["captured_at"]),
    )


def collect_and_store_yunmeng_shop_snapshot(
    session: Session,
    *,
    activity_id: str,
) -> Any:
    """Refresh only the Yunmeng shop and wallet facts of one exact occurrence.

    兑换宝阁与钱包是同一次「页面已打开」窗口里的只读事实，榜单事实来自另一条
    协议链路，因此这里既不读取榜单，也不能用榜单缺失否证商店数据。调用方必须
    已经确认运行态兑换页处于打开状态；本函数只负责把已加载的商店投影与钱包投影
    写回该实例，并在证据里如实记录刷新状态。
    """

    from backend.core.fanxiu.activity.exchange_event import (
        upsert_exchange_activity_snapshot,
    )
    from backend.core.fanxiu.activity.runtime_schedule import (
        refresh_cached_fanxiu_activity_runtime_schedule,
    )
    from backend.core.fanxiu.activity.standard_observation import (
        store_runtime_currency_fact,
    )

    existing = session.get(FanxiuExchangeActivity, activity_id)
    if existing is None or existing.activity_type != YUNMENG_ACTIVITY_TYPE:
        raise ValueError("云梦试剑活动实例不存在")
    if (
        not existing.instance_key
        or not existing.runtime_id
        or existing.game_activity_id is None
    ):
        raise ValueError("云梦宝阁采集要求精确的已登记活动实例")
    refresh_cached_fanxiu_activity_runtime_schedule(allow_discovery=True)
    period = _runtime_period(
        session,
        cross_count=int(existing.cross_count),
        target_date=date.fromisoformat(str(existing.end_date)),
    )
    if (
        str(existing.start_date) != str(period["start_date"])
        or str(existing.end_date) != str(period["end_date"])
        or int(existing.cross_count) != int(period["cross_count"])
        or int(existing.game_activity_id) != int(period["game_activity_id"])
    ):
        raise ValueError("云梦宝阁实例与当前运行时周期不一致")

    shop = _shop_snapshot(cross_count=int(existing.cross_count))
    if not list(shop.get("items") or []):
        raise ValueError("云梦试剑兑换宝阁运行态快照没有商品，拒绝覆盖已保存清单")
    captured_at = datetime.now().astimezone().isoformat(timespec="seconds")
    currency_status = "retained"
    currency_reason = "只读商店采集未请求运行态钱包"
    current_currency = int(existing.current_currency)
    cumulative_currency = int(existing.cumulative_currency)
    currency_captured_at = ""
    try:
        wallet = read_yunmeng_currency_snapshot(allow_discovery=True)
        store_runtime_currency_fact(session, wallet)
        currency_status = "updated"
        currency_reason = ""
        current_currency = int(wallet["exchange_currency"])
        cumulative_currency = int(wallet["cumulative_currency"])
        currency_captured_at = captured_at
    except (ValueError, RuntimeError) as exc:
        currency_reason = str(exc)

    evidence = dict(existing.evidence or {})
    refresh_status = dict(evidence.get("refresh_status") or {})
    refresh_status.update({
        "currency": currency_status,
        "currency_reason": currency_reason,
        "currency_stale": currency_status != "updated",
        "currency_captured_at": currency_captured_at,
        "shop": "updated",
        "shop_reason": "",
        "shop_had_persisted_snapshot": True,
    })
    evidence.update({
        "shop": dict(shop.get("evidence") or {}),
        "shop_snapshot_captured_at": captured_at,
        "refresh_status": refresh_status,
    })
    payload: dict[str, Any] = {
        "activity_type": YUNMENG_ACTIVITY_TYPE,
        "instance_key": str(existing.instance_key),
        "family": existing.family,
        "runtime_id": str(existing.runtime_id),
        "game_activity_id": int(existing.game_activity_id),
        "cross_count": int(existing.cross_count),
        "start_date": str(existing.start_date),
        "end_date": str(existing.end_date),
        "game_shop_base_id": YUNMENG_SHOP_BASE_ID,
        "currency_type": YUNMENG_CURRENCY_TYPE,
        "currency_name": YUNMENG_CURRENCY_NAME,
        "current_currency": current_currency,
        "cumulative_currency": cumulative_currency,
        "evidence": evidence,
        "instance_data": dict(existing.instance_data or {}),
        "shop_items": list(shop["items"]),
        "expected_shop_item_count": int(shop["active_shop_item_count"]),
    }
    persisted_id = upsert_exchange_activity_snapshot(session, payload)
    if str(persisted_id) != str(activity_id):
        raise RuntimeError("云梦宝阁写入切换了目标实例")
    persisted = session.get(FanxiuExchangeActivity, activity_id)
    if persisted is None:
        raise RuntimeError("云梦宝阁写入后无法回读实例")
    return persisted


def ensure_yunmeng_exchange_activity(session: Session) -> None:
    from backend.core.fanxiu.activity.standard_observation import (
        ActivityObservationUnavailable,
    )

    period = _runtime_period(session)
    existing = session.exec(
        select(FanxiuExchangeActivity).where(
            FanxiuExchangeActivity.activity_type == YUNMENG_ACTIVITY_TYPE,
            FanxiuExchangeActivity.cross_count == period["cross_count"],
            FanxiuExchangeActivity.start_date == period["start_date"],
            FanxiuExchangeActivity.end_date == period["end_date"],
        )
    ).first()
    try:
        collect_and_store_yunmeng_exchange_activity(
            session,
            activity_id=existing.id if existing else None,
            collect_runtime_shop=False,
        )
    except ActivityObservationUnavailable:
        return


__all__ = [
    "YUNMENG_ACTIVITY_TYPE",
    "YUNMENG_CURRENCY_NAME",
    "YUNMENG_CURRENCY_TYPE",
    "YUNMENG_GAME_ACTIVITY_TYPE",
    "YUNMENG_SHOP_BASE_ID",
    "YUNMENG_WORLDLINE_VO",
    "collect_and_store_yunmeng_exchange_activity",
    "collect_and_store_yunmeng_shop_snapshot",
    "ensure_yunmeng_exchange_activity",
    "read_yunmeng_currency_snapshot",
]
