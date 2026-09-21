from datetime import datetime
from types import SimpleNamespace

import pytest

from backend.core.fanxiu.data_annotation.tasks.lianti_faxiang import _occurrence_day_offset


def moment(value):
    return datetime.fromisoformat(value + "+08:00")


@pytest.fixture
def occurrence():
    return SimpleNamespace(
        start_at=moment("2026-09-18T05:00:05"),
        end_at=moment("2026-09-18T22:00:00"),
        close_at=moment("2026-09-20T23:58:59"),
    )


def test_settlement_panel_uses_historical_event_day(occurrence):
    assert _occurrence_day_offset(occurrence, moment("2026-09-20T00:50:00")) == -2


def test_active_event_uses_today(occurrence):
    assert _occurrence_day_offset(occurrence, moment("2026-09-18T06:00:00")) == 0


@pytest.mark.parametrize("value", ["2026-09-18T04:59:59", "2026-09-21T00:00:00"])
def test_calendar_date_does_not_authorize_unopened_or_closed_panel(occurrence, value):
    with pytest.raises(RuntimeError):
        _occurrence_day_offset(occurrence, moment(value))
