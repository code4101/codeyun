from __future__ import annotations

"""Probability-point persistence adapter for the 万宝臻宝 Mining lottery."""

import re
from typing import Any, Mapping

from sqlmodel import Session

from backend.core.fanxiu.activity.bothdraw_lottery_points import (
    BothdrawLotteryDataset,
    BothdrawLotteryPointSpec,
    list_bothdraw_lottery_points,
    record_bothdraw_lottery_point,
)


WANBAO_LOTTERY_POINT_SPEC = BothdrawLotteryPointSpec(
    namespace="wanbao-zhenbao-lottery-point",
    activity_label="万宝臻宝",
    entity_name="万宝臻宝抽奖散点",
)


def normalize_wanbao_lottery_snapshot(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Map the Mining ``progress/hitCount`` projection to the shared ledger."""

    if snapshot.get("complete") is not True:
        raise ValueError(str(snapshot.get("reason") or "万宝臻宝 Runtime 不完整"))
    draw = snapshot.get("draw")
    if not isinstance(draw, Mapping) or draw.get("enabled") is not True:
        raise ValueError("万宝臻宝抽奖状态未获 Runtime 授权")
    grand = draw.get("grand_prize")
    if not isinstance(grand, Mapping):
        raise ValueError("万宝臻宝大奖身份缺失")
    reward = str(grand.get("reward") or "")
    match = re.fullmatch(r"Item\|(\d+)_\d+(?:_\d+)?", reward)
    if not match:
        raise ValueError(f"万宝臻宝大奖奖励串无法识别：{reward}")
    prize_id = int(grand.get("id") or 0)
    if prize_id <= 0:
        raise ValueError("万宝臻宝大奖配置 id 无效")
    return {
        "complete": True,
        "captured_at": str(snapshot.get("captured_at") or ""),
        "activity_id": int(snapshot.get("activity_id") or 0),
        "x": int(draw.get("x") or 0),
        "y": int(draw.get("y") or 0),
        "selected_library_id": prize_id,
        "selected_big_reward": {
            "library_id": prize_id,
            "item_id": int(match.group(1)),
            "name": reward,
        },
        "available_currency": draw.get("available_currency"),
        "available_draws": draw.get("available_draws"),
        "cost_type": draw.get("cost_type"),
        "cost_per_draw": draw.get("cost_per_draw"),
        "progress": int(draw.get("progress") or 0),
        "evidence": dict(snapshot.get("evidence") or {}),
    }


def record_wanbao_lottery_point(
    session: Session,
    *,
    snapshot: dict[str, Any],
    instance_id: str,
) -> BothdrawLotteryDataset:
    return record_bothdraw_lottery_point(
        session,
        spec=WANBAO_LOTTERY_POINT_SPEC,
        snapshot=snapshot,
        instance_id=instance_id,
    )


def list_wanbao_lottery_points(
    session: Session, *, instance_id: str
) -> BothdrawLotteryDataset:
    return list_bothdraw_lottery_points(
        session,
        spec=WANBAO_LOTTERY_POINT_SPEC,
        instance_id=instance_id,
    )


__all__ = [
    "WANBAO_LOTTERY_POINT_SPEC",
    "list_wanbao_lottery_points",
    "normalize_wanbao_lottery_snapshot",
    "record_wanbao_lottery_point",
]
