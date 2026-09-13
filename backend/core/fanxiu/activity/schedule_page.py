from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel
from sqlmodel import Session, col, select

from backend.core.fanxiu.activity.exchange_event import (
    ExchangeActivitySnapshot,
    LatestExchangeActivitySnapshot,
    exchange_activity_close_panel_at,
    list_exchange_activity_snapshot,
    select_exchange_activity_default,
)
from backend.models import FanxiuExchangeActivity


class FanxiuScheduleRankingSnapshot(BaseModel):
    business_date: str
    gameplay_rank: LatestExchangeActivitySnapshot
    resource_rank: LatestExchangeActivitySnapshot


def _boundary_date(value: str, *, fallback: str) -> date:
    raw = str(value or "").strip()
    if raw:
        try:
            return datetime.fromisoformat(raw).date()
        except ValueError:
            pass
    return date.fromisoformat(fallback)


def _current_family_snapshot(
    session: Session,
    *,
    family: str,
    business_date: date,
) -> LatestExchangeActivitySnapshot:
    rows = list(
        session.exec(
            select(FanxiuExchangeActivity)
            .where(FanxiuExchangeActivity.family == family)
            .order_by(
                col(FanxiuExchangeActivity.start_date).desc(),
                col(FanxiuExchangeActivity.updated_at).desc(),
            )
        ).all()
    )
    relevant = [
        row
        for row in rows
        # 日程展示当日期次，明日活动的预告不能覆盖今日活动或领奖期。
        if _boundary_date(row.start_at, fallback=row.start_date)
        <= business_date
        <= exchange_activity_close_panel_at(row).date()
    ]
    if not relevant:
        return LatestExchangeActivitySnapshot()
    from backend.core.fanxiu.activity.daily_activity_sync import (
        load_worldline_activity_schedule_snapshot,
    )

    selected = select_exchange_activity_default(
        relevant,
        schedule=load_worldline_activity_schedule_snapshot(),
        business_date=business_date,
    )
    assert selected is not None
    snapshot: ExchangeActivitySnapshot = list_exchange_activity_snapshot(
        session,
        activity_type=selected.activity_type,
        activity_id=selected.id,
    )
    return LatestExchangeActivitySnapshot(
        activity_type=selected.activity_type,
        snapshot=snapshot,
    )


def load_fanxiu_schedule_ranking_snapshot(
    session: Session,
    *,
    business_date: date | None = None,
) -> FanxiuScheduleRankingSnapshot:
    """Project today's persisted ranking pages without reading the game.

    Peak Race uses the same resource family selection, with its persisted total
    board and reward matrix rendered by a dedicated page component.
    """

    resolved_date = business_date or datetime.now().astimezone().date()
    return FanxiuScheduleRankingSnapshot(
        business_date=resolved_date.isoformat(),
        gameplay_rank=_current_family_snapshot(
            session,
            family="gameplay_rank",
            business_date=resolved_date,
        ),
        resource_rank=_current_family_snapshot(
            session,
            family="resource_rank",
            business_date=resolved_date,
        ),
    )


__all__ = [
    "FanxiuScheduleRankingSnapshot",
    "load_fanxiu_schedule_ranking_snapshot",
]
