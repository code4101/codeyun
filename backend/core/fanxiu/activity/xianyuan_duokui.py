from __future__ import annotations

"""仙缘夺魁在玩法榜通用模型中的兑换宝阁适配器。"""

from datetime import date, datetime
from typing import Any, Mapping

from sqlmodel import Session, select

from backend.core.fanxiu.catalog.item import load_fanxiu_item_runtime_index
from backend.core.fanxiu.instrumentation.activity_shop import (
    FanxiuActivityShopCollectionError,
    FanxiuActivityShopNotLoadedError,
    collect_activity_shop_runtime,
)
from backend.models import FanxiuExchangeActivity


XIANYUAN_DUOKUI_ACTIVITY_TYPE = "xianyuan-duokui"
XIANYUAN_DUOKUI_ACTIVITY_ID = 846001
XIANYUAN_DUOKUI_RUNTIME_ACTIVITY_TYPE = 129
XIANYUAN_DUOKUI_SHOP_BASE_ID = 360001
XIANYUAN_DUOKUI_CURRENCY_TYPE = 23002
XIANYUAN_DUOKUI_CURRENCY_NAME = "夺魁灵玉"


def _runtime_occurrence() -> dict[str, Any]:
    from backend.core.fanxiu.activity.runtime_schedule import (
        get_cached_fanxiu_activity_runtime_schedule,
    )

    schedule = get_cached_fanxiu_activity_runtime_schedule(
        max_runtime_age_seconds=6 * 60 * 60
    )
    matches = [
        dict(item)
        for item in schedule.get("items") or ()
        if isinstance(item, Mapping)
        and (
            int(item.get("activityId") or 0) == XIANYUAN_DUOKUI_ACTIVITY_ID
            or int(item.get("activityType") or 0)
            == XIANYUAN_DUOKUI_RUNTIME_ACTIVITY_TYPE
        )
    ]
    if len(matches) != 1:
        raise ValueError(f"当前日程没有唯一仙缘夺魁实例：{len(matches)} 个")
    item = matches[0]
    start_ms = int(item.get("startTime") or 0)
    end_ms = int(item.get("endTime") or 0)
    close_ms = int(item.get("closePanelTime") or end_ms)
    if start_ms <= 0 or end_ms < start_ms or close_ms < end_ms:
        raise ValueError("仙缘夺魁日程周期不完整")
    start_at = datetime.fromtimestamp(start_ms / 1000).astimezone()
    end_at = datetime.fromtimestamp(end_ms / 1000).astimezone()
    close_at = datetime.fromtimestamp(close_ms / 1000).astimezone()
    return {
        "runtime_id": str(item.get("id") or ""),
        "game_activity_id": int(item.get("activityId") or 0),
        "cross_count": max(1, int(item.get("serverCount") or 1)),
        "start_date": start_at.date().isoformat(),
        "end_date": end_at.date().isoformat(),
        "start_time_ms": start_ms,
        "end_time_ms": end_ms,
        "close_panel_time_ms": close_ms,
        "close_panel_date": close_at.date().isoformat(),
        "world_level": max(0, int(item.get("avgWorldLevel") or 0)),
        "captured_at": str(schedule.get("created_at") or ""),
    }


def _shop_snapshot(*, cross_count: int) -> dict[str, Any]:
    cards = load_fanxiu_item_runtime_index(rebuild_missing=False)["cards_by_id"]
    names = {
        int(item_id): str(card.get("name") or "")
        for item_id, card in cards.items()
        if str(item_id).isdigit() and isinstance(card, dict)
    }
    snapshot = collect_activity_shop_runtime(
        shop_base_id=XIANYUAN_DUOKUI_SHOP_BASE_ID,
        item_names=names,
        expected_currency_type=XIANYUAN_DUOKUI_CURRENCY_TYPE,
        expected_cross_count=int(cross_count),
    )
    if not snapshot.get("complete"):
        raise ValueError("仙缘夺魁兑换宝阁运行态快照不完整")
    return snapshot


def ensure_xianyuan_duokui_activity(session: Session) -> str:
    """用日程事实建立玩法榜 occurrence；GET 不访问游戏进程。"""

    from backend.core.fanxiu.activity.exchange_event import (
        upsert_exchange_activity_snapshot,
    )

    today = date.today().isoformat()
    persisted = session.exec(
        select(FanxiuExchangeActivity)
        .where(
            FanxiuExchangeActivity.activity_type == XIANYUAN_DUOKUI_ACTIVITY_TYPE
        )
        .order_by(FanxiuExchangeActivity.start_date.desc())
    ).all()
    for activity in persisted:
        close_date = str(
            dict(activity.evidence or {}).get("period_close_panel_date")
            or activity.end_date
        )
        if activity.start_date <= today <= close_date:
            return activity.id

    period = _runtime_occurrence()
    existing = session.exec(
        select(FanxiuExchangeActivity).where(
            FanxiuExchangeActivity.activity_type == XIANYUAN_DUOKUI_ACTIVITY_TYPE,
            FanxiuExchangeActivity.cross_count == period["cross_count"],
            FanxiuExchangeActivity.start_date == period["start_date"],
            FanxiuExchangeActivity.end_date == period["end_date"],
        )
    ).first()
    if existing is not None:
        return existing.id
    evidence = {
        "instance_key": (
            f"runtime:{period['runtime_id']}:activity:{period['game_activity_id']}:"
            f"{period['start_date']}:{period['end_date']}"
        ),
        "runtime_id": period["runtime_id"],
        "game_activity_id": period["game_activity_id"],
        "period_start_time": period["start_time_ms"],
        "period_end_time": period["end_time_ms"],
        "period_close_panel_time": period["close_panel_time_ms"],
        "period_close_panel_date": period["close_panel_date"],
        "world_level": period["world_level"],
        "rank_scope_activity_ids": {"personal": 46003, "plane": 46004},
        "refresh_status": {
            "rankings": "unavailable",
            "shop": "unavailable",
            "currency": "unavailable",
        },
        "lifecycle_seed_source": "worldline_activity_runtime_memory",
    }
    return upsert_exchange_activity_snapshot(
        session,
        {
            "activity_type": XIANYUAN_DUOKUI_ACTIVITY_TYPE,
            "cross_count": period["cross_count"],
            "start_date": period["start_date"],
            "end_date": period["end_date"],
            "game_rank_activity_id": 46003,
            "game_shop_base_id": XIANYUAN_DUOKUI_SHOP_BASE_ID,
            "currency_type": XIANYUAN_DUOKUI_CURRENCY_TYPE,
            "currency_name": XIANYUAN_DUOKUI_CURRENCY_NAME,
            "captured_at": period["captured_at"],
            "source_kind": "runtime_schedule_reconcile",
            "resource_strategy": {"活动方式": "挑战仙侣并积累夺魁灵玉"},
            "evidence": evidence,
        },
    )


def collect_and_store_xianyuan_duokui_activity(
    session: Session,
    *,
    activity_id: str,
) -> Any:
    """只刷新已打开兑换页的完整商品投影；钱包未同步时保留旧值。"""

    from backend.core.fanxiu.activity.exchange_event import (
        list_exchange_activity_snapshot,
        upsert_exchange_activity_snapshot,
    )
    from backend.core.fanxiu.activity.standard_observation import (
        store_runtime_currency_fact,
    )
    from backend.core.fanxiu.instrumentation.runtime_memory import (
        FanxiuRuntimeMemoryError,
    )
    from backend.core.fanxiu.instrumentation.wallet import (
        read_wallet_currency_snapshot,
    )

    activity = session.get(FanxiuExchangeActivity, activity_id)
    if activity is None or activity.activity_type != XIANYUAN_DUOKUI_ACTIVITY_TYPE:
        raise ValueError("仙缘夺魁活动实例不存在")
    period = _runtime_occurrence()
    if (
        activity.cross_count != period["cross_count"]
        or activity.start_date != period["start_date"]
        or activity.end_date != period["end_date"]
    ):
        raise ValueError("仙缘夺魁活动实例与当前运行时周期不一致")
    try:
        shop = _shop_snapshot(cross_count=period["cross_count"])
    except FanxiuActivityShopNotLoadedError as exc:
        raise ValueError(f"仙缘夺魁兑换宝阁尚未打开：{exc}") from exc
    except FanxiuActivityShopCollectionError as exc:
        raise ValueError(f"仙缘夺魁兑换宝阁采集失败：{exc}") from exc

    current_currency = int(activity.current_currency or 0)
    cumulative_currency = int(activity.cumulative_currency or 0)
    currency_status = "retained"
    currency_reason = ""
    currency_evidence: dict[str, Any] = {}
    captured_at = datetime.now().astimezone().isoformat(timespec="seconds")
    try:
        wallet = read_wallet_currency_snapshot(
            XIANYUAN_DUOKUI_CURRENCY_TYPE,
            allow_discovery=True,
        )
        store_runtime_currency_fact(session, wallet)
        current_currency = int(wallet.get("current") or wallet.get("amount") or 0)
        cumulative_currency = int(
            wallet.get("cumulative")
            or wallet.get("history_amount")
            or current_currency
        )
        currency_status = "updated"
        currency_evidence = dict(wallet.get("evidence") or {})
    except (FanxiuRuntimeMemoryError, ValueError) as exc:
        # The live redemption implementation calls GetCurrencyByType(type)
        # and renders zero when no VO exists; history is explicitly
        # ``walletvo and walletvo.history or 0``.  A complete V_ShowList panel
        # with the exact V_WalletType therefore makes this one absence case an
        # authoritative 0/0 projection rather than a stale unknown.
        if "未同步" in str(exc) and str(XIANYUAN_DUOKUI_CURRENCY_TYPE) in str(exc):
            current_currency = 0
            cumulative_currency = 0
            currency_status = "updated"
            currency_evidence = {
                "source": "activity_redemption_live_panel_absent_wallet_vo",
                "wallet_type": XIANYUAN_DUOKUI_CURRENCY_TYPE,
                "page_semantics": "GetCurrencyByType=0; missing WalletVO.history=0",
            }
        else:
            currency_reason = str(exc)

    evidence = dict(activity.evidence or {})
    refresh_status = dict(evidence.get("refresh_status") or {})
    refresh_status.update(
        {
            "shop": "updated",
            "shop_reason": "",
            "currency": currency_status,
            "currency_reason": currency_reason,
            "currency_stale": currency_status != "updated",
            "currency_captured_at": captured_at if currency_status == "updated" else "",
            "rankings": str(refresh_status.get("rankings") or "unavailable"),
        }
    )
    evidence.update(
        {
            "runtime_id": period["runtime_id"],
            "game_activity_id": period["game_activity_id"],
            "period_start_time": period["start_time_ms"],
            "period_end_time": period["end_time_ms"],
            "period_close_panel_time": period["close_panel_time_ms"],
            "period_close_panel_date": period["close_panel_date"],
            "world_level": period["world_level"],
            "rank_scope_activity_ids": {"personal": 46003, "plane": 46004},
            "refresh_status": refresh_status,
            "shop": dict(shop.get("evidence") or {}),
            "currency_runtime": currency_evidence,
        }
    )
    persisted_id = upsert_exchange_activity_snapshot(
        session,
        {
            "activity_type": XIANYUAN_DUOKUI_ACTIVITY_TYPE,
            "cross_count": period["cross_count"],
            "start_date": period["start_date"],
            "end_date": period["end_date"],
            "game_rank_activity_id": 46003,
            "game_shop_base_id": XIANYUAN_DUOKUI_SHOP_BASE_ID,
            "currency_type": XIANYUAN_DUOKUI_CURRENCY_TYPE,
            "currency_name": XIANYUAN_DUOKUI_CURRENCY_NAME,
            "current_currency": current_currency,
            "cumulative_currency": cumulative_currency,
            "captured_at": captured_at,
            "source_kind": "read_only_runtime_facts",
            "resource_strategy": {"活动方式": "挑战仙侣并积累夺魁灵玉"},
            "shop_items": list(shop["items"]),
            "expected_shop_item_count": int(shop["active_shop_item_count"]),
            "evidence": evidence,
        },
    )
    return list_exchange_activity_snapshot(
        session,
        activity_type=XIANYUAN_DUOKUI_ACTIVITY_TYPE,
        activity_id=persisted_id,
    ).selected_activity


__all__ = [
    "XIANYUAN_DUOKUI_ACTIVITY_TYPE",
    "XIANYUAN_DUOKUI_ACTIVITY_ID",
    "XIANYUAN_DUOKUI_RUNTIME_ACTIVITY_TYPE",
    "XIANYUAN_DUOKUI_SHOP_BASE_ID",
    "XIANYUAN_DUOKUI_CURRENCY_TYPE",
    "XIANYUAN_DUOKUI_CURRENCY_NAME",
    "collect_and_store_xianyuan_duokui_activity",
    "ensure_xianyuan_duokui_activity",
]
