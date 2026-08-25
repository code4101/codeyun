from datetime import datetime
from threading import Event
from zoneinfo import ZoneInfo

import pytest

from backend.core.fanxiu.activity.ranking_lifecycle import RankingOccurrence
from backend.core.fanxiu.data_annotation.tasks import magic_invasion_compound as compound


TZ = ZoneInfo("Asia/Shanghai")


class _Runtime:
    def __init__(self, events) -> None:
        self.events = events

    def goto_view(self, scene):
        self.events.append(("goto", scene))
        yield None

    def wait_scene(self, scene, **_kwargs):
        self.events.append(("wait_scene", scene))
        yield None


class _Runner:
    def __init__(self, events, *, mail_fails=False) -> None:
        self.events = events
        self.runtime = _Runtime(events)
        self.mail_fails = mail_fails

    def _fanxiu_runtime(self, _ctx, **_kwargs):
        return self.runtime

    def _log(self, level, message):
        self.events.append(("log", level, message))


def _finish(generator):
    while True:
        try:
            next(generator)
        except StopIteration as exc:
            return exc.value


def _occurrence() -> RankingOccurrence:
    return RankingOccurrence(
        activity_type="magic-invasion",
        family="gameplay_rank",
        runtime_id="8070001400004",
        activity_id=8070001,
        start_at=datetime(2026, 8, 22, 10, tzinfo=TZ),
        end_at=datetime(2026, 8, 22, 22, tzinfo=TZ),
        prepare_at=datetime(2026, 8, 21, 0, tzinfo=TZ),
        close_at=datetime(2026, 8, 23, 23, 59, tzinfo=TZ),
        cross_count=8,
    )


def test_compound_skips_mail_and_keeps_sufficient_supply_inside_activity(
    monkeypatch,
) -> None:
    events = []
    runner = _Runner(events, mail_fails=True)
    monkeypatch.setattr(
        "backend.core.fanxiu.activity.runtime_schedule.read_fanxiu_activity_runtime_schedule",
        lambda **_kwargs: {"available": True, "complete": True, "items": []},
    )

    def select(*_args, **_kwargs):
        events.append(("select_magic",))
        yield None

    def tasks(*_args, **_kwargs):
        events.append(("tasks",))
        yield None
        return {"status": "claimed"}

    def supply(*_args, **kwargs):
        events.append(("supply", kwargs["required_tianyan"]))
        yield None
        return {"status": "supplied"}

    def explore(*_args, **kwargs):
        events.append(
            (
                "explore",
                kwargs["manage_schedule"],
                kwargs["already_on_main_scene"],
            )
        )
        yield None
        return {
            "result": "success",
            "progress": {
                "occurrence_id": "8070001400004",
                "state": "complete",
                "base_explore_count": 1500,
                "confirmed_batches": [{}, {}, {}],
            },
        }

    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.schedule_navigation.select_schedule_activity",
        select,
    )
    monkeypatch.setattr(compound, "claim_magic_invasion_task_rewards", tasks)
    monkeypatch.setattr(
        compound,
        "read_backpack_item_counts",
        lambda *_args, **_kwargs: ({1010004: 1533}, {"read_only": True}),
    )
    monkeypatch.setattr(compound, "ensure_magic_tianyan_supply", supply)
    monkeypatch.setattr(compound, "execute_magic_invasion_explore_job", explore)

    result = _finish(
        compound.execute_magic_invasion_compound_checkpoint(
            runner,
            {},
            {},
            Event(),
            occurrence=_occurrence(),
        )
    )

    assert result["status"] == "completed"
    assert "mail" not in result
    assert not any(event[0] == "mail" for event in events)
    assert not any(event[0] == "supply" for event in events)
    assert not any(event == ("goto", 34) for event in events)
    assert events.index(("tasks",)) < events.index(("explore", False, True))
    assert result["supply"]["status"] == "sufficient"
    assert result["supply"]["tianyan_before"] == 1533


def test_compound_rejects_zero_action_explore_success(monkeypatch) -> None:
    events = []
    runner = _Runner(events)
    monkeypatch.setattr(
        "backend.core.fanxiu.activity.runtime_schedule.read_fanxiu_activity_runtime_schedule",
        lambda **_kwargs: {"available": True, "complete": True, "items": []},
    )

    def select(*_args, **_kwargs):
        yield None

    def tasks(*_args, **_kwargs):
        yield None
        return {"status": "claimed"}

    def supply(*_args, **_kwargs):
        yield None
        return {"status": "supplied"}

    def explore(*_args, **_kwargs):
        yield None
        return {"result": "success", "performed_actions": False}

    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.schedule_navigation.select_schedule_activity",
        select,
    )
    monkeypatch.setattr(compound, "claim_magic_invasion_task_rewards", tasks)
    monkeypatch.setattr(
        compound,
        "read_backpack_item_counts",
        lambda *_args, **_kwargs: ({1010004: 1500}, {"read_only": True}),
    )
    monkeypatch.setattr(compound, "ensure_magic_tianyan_supply", supply)
    monkeypatch.setattr(compound, "execute_magic_invasion_explore_job", explore)

    with pytest.raises(RuntimeError, match="3×500 完成证据"):
        _finish(
            compound.execute_magic_invasion_compound_checkpoint(
                runner,
                {},
                {},
                Event(),
                occurrence=_occurrence(),
            )
        )


def test_compound_resume_supplies_only_unconfirmed_batches(monkeypatch) -> None:
    events = []
    runner = _Runner(events)
    monkeypatch.setattr(
        "backend.core.fanxiu.activity.runtime_schedule.read_fanxiu_activity_runtime_schedule",
        lambda **_kwargs: {"available": True, "complete": True, "items": []},
    )

    def select(*_args, **_kwargs):
        yield None

    def tasks(*_args, **_kwargs):
        yield None
        return {"status": "already_claimed"}

    def supply(*_args, **kwargs):
        events.append(("supply", kwargs["required_tianyan"]))
        yield None
        return {"status": "already_sufficient", "tianyan_after": 1000}

    def explore(_runner, _ctx, explore_payload, _stop, **kwargs):
        progress = explore_payload["magic_invasion_progress"]
        events.append(
            (
                "explore",
                kwargs["manage_schedule"],
                progress["base_explore_count"],
                len(progress["confirmed_batches"]),
            )
        )
        yield None
        return {
            "result": "success",
            "progress": {
                **progress,
                "state": "complete",
                "base_explore_count": 1500,
                "confirmed_batches": [{}, {}, {}],
            },
        }

    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.schedule_navigation.select_schedule_activity",
        select,
    )
    monkeypatch.setattr(compound, "claim_magic_invasion_task_rewards", tasks)
    monkeypatch.setattr(
        compound,
        "read_backpack_item_counts",
        lambda *_args, **_kwargs: ({1010004: 0}, {"read_only": True}),
    )
    monkeypatch.setattr(compound, "ensure_magic_tianyan_supply", supply)
    monkeypatch.setattr(compound, "execute_magic_invasion_explore_job", explore)
    payload = {
        "magic_invasion_progress": {
            "occurrence_id": "8070001400004",
            "state": "confirmed",
            "base_explore_count": 500,
            "confirmed_batches": [{"batch_index": 1}],
        }
    }

    result = _finish(
        compound.execute_magic_invasion_compound_checkpoint(
            runner,
            {},
            payload,
            Event(),
            occurrence=_occurrence(),
        )
    )

    assert result["status"] == "completed"
    assert ("supply", 1000) in events
    assert ("explore", False, 500, 1) in events


def test_armed_exploration_blocks_before_any_optional_action() -> None:
    events = []
    runner = _Runner(events)
    with pytest.raises(RuntimeError, match="未确认"):
        _finish(
            compound.execute_magic_invasion_compound_checkpoint(
                runner,
                {},
                {
                    "magic_invasion_progress": {
                        "occurrence_id": "8070001400004",
                        "state": "armed",
                    }
                },
                Event(),
                occurrence=_occurrence(),
            )
        )
    assert events == []
