from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlmodel import Session, SQLModel, create_engine

from backend.core.fanxiu.activity import daily_activity_sync
from backend.core.fanxiu.activity.exchange_event import (
    list_exchange_activity_snapshot,
    select_exchange_activity_default,
)
from backend.core.fanxiu.activity.schedule_page import load_fanxiu_schedule_ranking_snapshot
from backend.models import FanxiuExchangeActivity


TODAY = datetime.now(ZoneInfo("Asia/Shanghai")).date()


def activity(runtime_id: str) -> FanxiuExchangeActivity:
    return FanxiuExchangeActivity(
        id=runtime_id, instance_key=runtime_id, runtime_id=runtime_id,
        activity_type="beast-abyss", family="gameplay_rank",
        game_activity_id=32150001, cross_count=32,
        start_date=TODAY.isoformat(), end_date=TODAY.isoformat(),
        close_at=f"{TODAY.isoformat()}T23:58:59+08:00",
    )


def schedule() -> dict:
    return {
        "source_kind": "worldline_activity_runtime_memory",
        "projection_date": TODAY.isoformat(),
        "occurrences": [{
            "runtime_ids": [32150001400004], "activity_id": 32150001,
            "cross_count": 32, "start_date": TODAY.isoformat(),
            "end_date": TODAY.isoformat(), "identity_complete": True,
            "close_panel_at": f"{TODAY.isoformat()}T23:58:59+08:00",
        }],
    }


def test_current_exact_runtime_wins_same_label_without_changing_history():
    rows = [activity("32150001400031"), activity("32150001400004")]
    selected = select_exchange_activity_default(rows, schedule=schedule(), business_date=TODAY)
    assert selected is rows[1]
    assert [row.id for row in rows] == ["32150001400031", "32150001400004"]


@pytest.mark.parametrize("change", [
    {"runtime_ids": [999]}, {"activity_id": 999}, {"cross_count": 64},
    {"start_date": "2000-01-01"}, {"identity_complete": False},
    {"identity_conflict": ["conflicting_scope"]},
])
def test_unconfirmed_or_different_identity_cannot_override_order(change):
    rows = [activity("32150001400031"), activity("32150001400004")]
    saved = schedule()
    saved["occurrences"][0].update(change)
    assert select_exchange_activity_default(rows, schedule=saved, business_date=TODAY) is rows[0]


def test_stale_or_ambiguous_schedule_preserves_fallback():
    rows = [activity("32150001400031"), activity("32150001400004")]
    saved = schedule()
    saved["projection_date"] = "2000-01-01"
    assert select_exchange_activity_default(rows, schedule=saved, business_date=TODAY) is rows[0]
    saved = schedule()
    saved["occurrences"][0]["runtime_ids"].append(32150001400031)
    assert select_exchange_activity_default(rows, schedule=saved, business_date=TODAY) is rows[0]


def test_today_activity_beats_preview_and_saved_settlement_identity():
    preview, current, settlement = (activity(name) for name in ("preview", "current", "32150001400004"))
    preview.start_date = preview.end_date = (TODAY + timedelta(days=1)).isoformat()
    settlement.start_date = settlement.end_date = (TODAY - timedelta(days=1)).isoformat()
    saved = schedule()
    saved["occurrences"][0].update(start_date=settlement.start_date, end_date=settlement.end_date)
    rows = [preview, settlement, current]
    assert select_exchange_activity_default(rows, schedule=saved, business_date=TODAY) is current
    assert select_exchange_activity_default(rows, schedule={}, business_date=TODAY) is current


def test_list_and_schedule_use_saved_identity_but_explicit_history_is_respected(monkeypatch):
    from backend.core.fanxiu.activity import runtime_schedule

    def unexpected_runtime_read(**kwargs):
        raise AssertionError("a snapshot GET must not access the game")

    monkeypatch.setattr(runtime_schedule, "read_fanxiu_activity_runtime_schedule", unexpected_runtime_read)
    monkeypatch.setattr(daily_activity_sync, "load_worldline_activity_schedule_snapshot", schedule)
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        old, current = activity("32150001400031"), activity("32150001400004")
        # A historical row being updated does not make it the current Runtime.
        old.updated_at, current.updated_at = 200, 100
        session.add_all([old, current])
        session.commit()
        default = list_exchange_activity_snapshot(session, activity_type="beast-abyss")
        assert default.selected_activity.id == current.id
        assert len(default.activities) == 2
        historical = list_exchange_activity_snapshot(
            session, activity_type="beast-abyss", activity_id=old.id,
        )
        assert historical.selected_activity.id == old.id
        displayed = load_fanxiu_schedule_ranking_snapshot(session, business_date=TODAY)
        assert displayed.gameplay_rank.snapshot.selected_activity.id == current.id
