from __future__ import annotations

from datetime import date, timedelta

from backend.core.fanxiu.data_annotation import schedule_navigation
from backend.core.fanxiu.data_annotation.tasks import yunmeng_active


class _NoopContext:
    def __init__(self) -> None:
        self.scenes: list[int] = []

    def go_scene(self, scene: int):
        self.scenes.append(int(scene))
        yield None


def test_enter_yunmeng_activity_home_offsets_historical_date_against_real_today(
    monkeypatch,
) -> None:
    captured: dict = {}

    def fake_select(context, pattern, **kwargs):
        captured["pattern"] = pattern
        captured.update(kwargs)
        return None
        yield

    monkeypatch.setattr(
        schedule_navigation,
        "select_schedule_activity",
        fake_select,
    )

    context = _NoopContext()
    target_date = date.today() - timedelta(days=1)
    list(yunmeng_active.enter_yunmeng_activity_home(context, target_date=target_date))

    # The calendar column is chosen with day_offset against the real current
    # day; ``now`` must stay the GUI's current moment so #66 does not shift its
    # 今天 anchor to the historical occurrence date.
    assert captured["pattern"] == r"云梦试剑"
    assert captured["day_offset"] == -1
    assert captured["now"].date() == date.today()
    assert captured["enter"] is True
    assert captured["require_runtime_alignment"] is True
    assert context.scenes == [34, 66]


def test_enter_yunmeng_activity_home_keeps_today_column_for_current_occurrence(
    monkeypatch,
) -> None:
    captured: dict = {}

    def fake_select(context, pattern, **kwargs):
        captured.update(kwargs)
        return None
        yield

    monkeypatch.setattr(
        schedule_navigation,
        "select_schedule_activity",
        fake_select,
    )

    list(
        yunmeng_active.enter_yunmeng_activity_home(
            _NoopContext(),
            target_date=date.today(),
        )
    )

    assert captured["day_offset"] == 0
