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
XIANYUAN_DUOKUI_ECONOMICAL_TARGET = 10_000
XIANYUAN_DUOKUI_TASK_SWEET_SPOT = 1000
XIANYUAN_DUOKUI_ECONOMICAL_RANK_LIMIT = 256
XIANYUAN_DUOKUI_DISCOUNTED_DAIER_GOODS_ID = 8460001
XIANYUAN_DUOKUI_DISCOUNTED_DAIER_ITEM_ID = 9023

# ActiveTask(activityId=846001) 的云梦夺分任务。1000 点会与下方
# 试炼四同时命中，是两组奖励的重叠档。
XIANYUAN_DUOKUI_CURRENCY_TIERS: tuple[dict[str, Any], ...] = (
    {"currency": 200, "rewards": {23003: 1, 9052006: 5}},
    {"currency": 400, "rewards": {23003: 1, 9052006: 5}},
    {"currency": 700, "rewards": {23003: 1, 9052006: 5}},
    {"currency": 1000, "rewards": {23003: 1, 9052006: 5}},
    {"currency": 1400, "rewards": {23003: 1, 9052006: 5}},
    {"currency": 1800, "rewards": {23003: 1, 9052006: 5}},
    {"currency": 2400, "rewards": {23003: 1, 9052006: 10}},
    {"currency": 3000, "rewards": {2008003: 1, 9052006: 10}},
)

# ActiveTask(activityId=846001) 的云梦试炼任务。这里保留静态事实，
# 不把“冲榜”误写成 Scheduler 的独立作业或无界资源目标。
XIANYUAN_DUOKUI_TRIAL_TIERS: tuple[dict[str, Any], ...] = (
    {
        "currency": 200,
        "rank_limit": None,
        "rewards": {9020010: 2, 6010001: 1},
    },
    {
        "currency": 450,
        "rank_limit": None,
        "rewards": {9020010: 2, 6010001: 2},
    },
    {
        "currency": 800,
        "rank_limit": 512,
        "rewards": {9020010: 3, 6010001: 3},
    },
    {
        "currency": 1000,
        "rank_limit": 256,
        "rewards": {9020002: 1, 6010001: 6},
    },
    {
        "currency": 1600,
        "rank_limit": 64,
        "rewards": {9020005: 2, 6010001: 8},
    },
)


def recommended_xianyuan_duokui_trial_target(
    personal_rank: int | None,
) -> dict[str, Any]:
    """Choose a bounded task target from the rank already held by the player.

    1000/前256 is the double-task sweet spot.  Fall back to 800/前512 when the
    live rank cannot claim it; select 1600 only when rank 64 is already held.
    This function never recommends spending to speculate on a higher rank.
    """

    rank = int(personal_rank or 0)
    if 0 < rank <= 64:
        target = XIANYUAN_DUOKUI_TRIAL_TIERS[4]
    elif 0 < rank <= 256:
        target = XIANYUAN_DUOKUI_TRIAL_TIERS[3]
    else:
        target = XIANYUAN_DUOKUI_TRIAL_TIERS[2]
    return dict(target)


def xianyuan_duokui_resource_strategy() -> dict[str, Any]:
    """Return the bounded, evidence-backed resource target for this activity."""

    return {
        "活动方式": "挑战仙侣并积累夺魁灵玉",
        "默认经济档": {
            "累计夺魁灵玉": XIANYUAN_DUOKUI_ECONOMICAL_TARGET,
            "兑换目标": "5折誓约·黛儿",
            "goods_id": XIANYUAN_DUOKUI_DISCOUNTED_DAIER_GOODS_ID,
            "item_id": XIANYUAN_DUOKUI_DISCOUNTED_DAIER_ITEM_ID,
            "判断": "仙缘夺魁资源产能有限，通常取得折扣黛儿即停止",
        },
        "任务性价比档": {
            "累计夺魁灵玉": XIANYUAN_DUOKUI_TASK_SWEET_SPOT,
            "个人榜要求": f"前{XIANYUAN_DUOKUI_ECONOMICAL_RANK_LIMIT}",
            "判断": "云梦夺分四与云梦试炼四的双任务重叠档",
        },
        "保底档": {"累计夺魁灵玉": 800, "个人榜要求": "前512"},
        "顺吃高档": [{"累计夺魁灵玉": 1600, "个人榜要求": "前64"}],
        "挑战道具": {
            "2008000": "夺魁令：增加1次挑战",
            "2008001": "四倍积分令：胜利时个人积分及夺魁灵玉4倍",
            "2008003": "狙击令：增加1次狙击",
            "2008005": "属性增幅令：下一场挑战属性2倍",
            "2008007": "争仙令：榜单积分+100%，可与其他积分增益叠加",
        },
        "收益模型": {
            "单次基础个人积分": "40~60",
            "四倍积分令": "胜利时个人积分与夺魁灵玉均为4倍",
            "争仙令": "榜单积分再乘2；与四倍积分令叠加时个人积分最高8倍",
            "执行要求": "先小批实测绝对钱包增量，再按剩余缺口分批收敛",
        },
        "禁用道具": {"2008006": "静态配置明确标记为废弃"},
        "自动挑战安全设置": {
            "高战力对手使用属性增幅令": True,
            "自动使用四倍积分令": True,
            "自动使用争仙令": True,
            "默认跳过战斗": True,
            "自动使用夺魁令补充挑战体力": False,
            "开启快速自动挑战": True,
            "跳过动画": True,
        },
        "常规目标": "只生产足够兑换5折誓约·黛儿的1万夺魁灵玉；取得后停止",
        "条件目标": "仅使用自然获得的额外资源顺吃后续商品，不为其他折扣或收尾道具继续加打",
        "策略边界": "兑换目标以5折黛儿为硬停止线；任务奖励先吃1000/前256，排名不足则800保底，已自然前64则顺吃1600",
    }


def _wallet_amounts(snapshot: Mapping[str, Any]) -> tuple[int, int]:
    """Normalize the canonical wallet snapshot while retaining legacy keys."""

    current = snapshot.get("exchange_currency")
    if current is None:
        current = snapshot.get("current")
    if current is None:
        current = snapshot.get("amount")
    current_value = int(current or 0)
    cumulative = snapshot.get("cumulative_currency")
    if cumulative is None:
        cumulative = snapshot.get("cumulative")
    if cumulative is None:
        cumulative = snapshot.get("history_amount")
    return current_value, int(cumulative if cumulative is not None else current_value)


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
            "resource_strategy": xianyuan_duokui_resource_strategy(),
            "evidence": evidence,
        },
    )


def collect_and_store_xianyuan_duokui_activity(
    session: Session,
    *,
    activity_id: str,
) -> Any:
    """刷新已打开兑换页、精确钱包和已缓存的个人榜事实。"""

    from backend.core.fanxiu.activity.exchange_event import (
        list_exchange_activity_snapshot,
        replace_exchange_rankings,
        upsert_exchange_activity_snapshot,
    )
    from backend.core.fanxiu.activity.standard_observation import (
        store_runtime_currency_fact,
    )
    from backend.core.fanxiu.instrumentation.runtime_memory import (
        FanxiuRuntimeMemoryError,
    )
    from backend.core.fanxiu.instrumentation.xianyuan_duokui import (
        read_xianyuan_duokui_status_snapshot,
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
        current_currency, cumulative_currency = _wallet_amounts(wallet)
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
    rankings: list[dict[str, Any]] | None = None
    prior_rankings_status = str(refresh_status.get("rankings") or "unavailable")
    rankings_status = (
        "retained" if prior_rankings_status == "updated" else prior_rankings_status
    )
    rankings_reason = ""
    personal_rank: int | None = None
    try:
        status = read_xianyuan_duokui_status_snapshot(
            event_date=activity.start_date,
        )
        current_currency = int(status["exchange_currency"])
        cumulative_currency = int(status["cumulative_currency"])
        currency_status = "updated"
        currency_reason = ""
        currency_evidence = dict(status.get("evidence") or {})
        rankings = [
            {**dict(row), "ranking_scope": "personal"}
            for row in status.get("rankings") or ()
        ]
        rankings_status = "updated"
        personal_rank = int(status.get("rank") or 0) or None
    except (FanxiuRuntimeMemoryError, KeyError, TypeError, ValueError) as exc:
        rankings_reason = str(exc)

    refresh_status.update(
        {
            "shop": "updated",
            "shop_reason": "",
            "currency": currency_status,
            "currency_reason": currency_reason,
            "currency_stale": currency_status != "updated",
            "currency_captured_at": captured_at if currency_status == "updated" else "",
            "rankings": rankings_status,
            "rankings_reason": rankings_reason,
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
    resource_strategy = xianyuan_duokui_resource_strategy()
    if personal_rank is not None:
        resource_strategy["本期执行档"] = recommended_xianyuan_duokui_trial_target(
            personal_rank
        )
        resource_strategy["本期个人排名"] = personal_rank
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
            "resource_strategy": resource_strategy,
            "shop_items": list(shop["items"]),
            "expected_shop_item_count": int(shop["active_shop_item_count"]),
            "evidence": evidence,
        },
    )
    if rankings is not None:
        replace_exchange_rankings(
            session,
            activity_type=XIANYUAN_DUOKUI_ACTIVITY_TYPE,
            activity_id=persisted_id,
            rows=rankings,
            captured_at=captured_at,
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
    "XIANYUAN_DUOKUI_ECONOMICAL_TARGET",
    "XIANYUAN_DUOKUI_TASK_SWEET_SPOT",
    "XIANYUAN_DUOKUI_ECONOMICAL_RANK_LIMIT",
    "XIANYUAN_DUOKUI_DISCOUNTED_DAIER_GOODS_ID",
    "XIANYUAN_DUOKUI_DISCOUNTED_DAIER_ITEM_ID",
    "XIANYUAN_DUOKUI_CURRENCY_TIERS",
    "XIANYUAN_DUOKUI_TRIAL_TIERS",
    "collect_and_store_xianyuan_duokui_activity",
    "ensure_xianyuan_duokui_activity",
    "recommended_xianyuan_duokui_trial_target",
    "xianyuan_duokui_resource_strategy",
]
