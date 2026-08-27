from __future__ import annotations

"""Structured 天地弈局 projection for the shared gameplay-ranking page."""

from typing import Any

from sqlmodel import Session

from backend.core.fanxiu.activity.exchange_event import (
    list_exchange_activity_snapshot,
    replace_exchange_rankings,
    upsert_exchange_activity_snapshot,
)
from backend.core.fanxiu.catalog.item import load_fanxiu_item_runtime_index
from backend.core.fanxiu.instrumentation.activity_shop import (
    collect_activity_shop_runtime,
)
from backend.core.fanxiu.instrumentation.tiandi_yiju import (
    read_tiandi_yiju_runtime_snapshot,
)
from backend.models import FanxiuExchangeActivity


TIANDI_YIJU_ACTIVITY_TYPE = "tiandi-yiju"
_OCCURRENCE_SHOPS = {
    8090001: (90000, 11),
    8090004: (90002, 13),
}


def _item_names() -> dict[int, str]:
    cards = load_fanxiu_item_runtime_index(rebuild_missing=False)["cards_by_id"]
    return {
        int(item_id): str(card.get("name") or "")
        for item_id, card in cards.items()
        if str(item_id).isdigit() and isinstance(card, dict)
    }


def _ranking_rows(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    scope_by_kind = {1: "alliance", 2: "personal"}
    for raw_kind, rows in dict(snapshot.get("rank_rows") or {}).items():
        kind = int(raw_kind)
        scope = scope_by_kind.get(kind)
        if scope is None:
            continue
        expected_rank = int(snapshot.get(f"{scope}_rank") or 0)
        expected_score = int(snapshot.get(f"{scope}_score") or 0)
        for row in rows:
            rank = int(row.get("rank") or 0)
            score = int(row.get("score") or 0)
            identity = int(row.get("id") or 0)
            result.append({
                "ranking_scope": scope,
                "rank": rank,
                "score": score,
                "role_key": str(identity),
                "name": str(row.get("name") or identity),
                "is_self": bool(rank == expected_rank and score == expected_score),
                "raw_data": {
                    "runtime_identity": identity,
                    "scope_complete": True,
                    "loaded_player_count": len(rows),
                    "reported_rank_list_size": len(rows),
                },
            })
    return result


def collect_and_store_tiandi_yiju_activity(
    session: Session,
    *,
    activity_id: str,
) -> Any:
    activity = session.get(FanxiuExchangeActivity, activity_id)
    if activity is None or activity.activity_type != TIANDI_YIJU_ACTIVITY_TYPE:
        raise ValueError("天地弈局活动实例不存在")
    evidence = dict(activity.evidence or {})
    game_activity_id = int(evidence.get("game_activity_id") or 0)
    try:
        shop_base_id, currency_type = _OCCURRENCE_SHOPS[game_activity_id]
    except KeyError as exc:
        raise ValueError(f"天地弈局活动 {game_activity_id} 不是可采集棋局") from exc

    runtime = read_tiandi_yiju_runtime_snapshot()
    shop = collect_activity_shop_runtime(
        shop_base_id=shop_base_id,
        item_names=_item_names(),
        expected_currency_type=currency_type,
        expected_cross_count=activity.cross_count,
    )
    if not shop.get("complete"):
        raise ValueError("天地弈局兑换宝阁运行态快照不完整")
    captured_at = str(runtime.get("captured_at") or "")
    evidence.update({
        "runtime": dict(runtime.get("evidence") or {}),
        "shop": dict(shop.get("evidence") or {}),
        "refresh_status": {
            "currency": "updated",
            "shop": "updated",
            "rankings": "updated",
        },
    })
    personal_score = int(runtime.get("personal_score") or 0)
    persisted_id = upsert_exchange_activity_snapshot(session, {
        "activity_type": TIANDI_YIJU_ACTIVITY_TYPE,
        "cross_count": activity.cross_count,
        "start_date": activity.start_date,
        "end_date": activity.end_date,
        "game_rank_activity_id": activity.game_rank_activity_id,
        "game_shop_base_id": shop_base_id,
        "currency_type": currency_type,
        "currency_name": "棋符",
        "current_currency": personal_score,
        "cumulative_currency": personal_score,
        "captured_at": captured_at,
        "source_kind": "runtime_memory.alliance_play_chess_and_activity_shop",
        "shop_items": list(shop["items"]),
        "expected_shop_item_count": int(shop["active_shop_item_count"]),
        "evidence": evidence,
    })
    replace_exchange_rankings(
        session,
        activity_type=TIANDI_YIJU_ACTIVITY_TYPE,
        activity_id=persisted_id,
        rows=_ranking_rows(runtime),
        captured_at=captured_at,
    )
    return list_exchange_activity_snapshot(
        session,
        activity_type=TIANDI_YIJU_ACTIVITY_TYPE,
        activity_id=persisted_id,
    ).selected_activity


__all__ = [
    "TIANDI_YIJU_ACTIVITY_TYPE",
    "collect_and_store_tiandi_yiju_activity",
]
