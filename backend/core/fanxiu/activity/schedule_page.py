from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel
from sqlmodel import Session, col, select

from backend.core.fanxiu.activity.exchange_event import (
    ExchangeActivitySnapshot,
    LatestExchangeActivitySnapshot,
    exchange_activity_close_panel_at,
    list_exchange_activity_snapshot,
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
        if _boundary_date(row.prepare_at, fallback=row.start_date)
        <= business_date
        <= exchange_activity_close_panel_at(row).date()
    ]
    if not relevant:
        return LatestExchangeActivitySnapshot()
    selected = relevant[0]
    snapshot: ExchangeActivitySnapshot = list_exchange_activity_snapshot(
        session,
        activity_type=selected.activity_type,
        activity_id=selected.id,
    )
    if selected.activity_type == "peakrace" and snapshot.selected_activity is not None:
        from backend.core.fanxiu.activity.peakrace_page import peakrace_ranking_key_points

        total = snapshot.selected_activity.instance_data.get("peakrace_total")
        if total:
            total["key_points"] = peakrace_ranking_key_points(total)
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
