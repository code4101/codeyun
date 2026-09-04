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

    def go_scene(self, scene):
        self.events.append(("goto", scene))
        yield None

    def wait_scene(self, layer0, **_kwargs):
        scene = layer0[0]
        self.events.append(("wait_scene", scene))
        yield None


class _Runner:
    def __init__(self, events, *, mail_fails=False) -> None:
        self.events = events
        self.runtime = _Runtime(events)
        self.mail_fails = mail_fails

    def _behavior_tree_context(self, _ctx, **_kwargs):
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
        lambda *_args, **_kwargs: ({1010004: 2033}, {"read_only": True}),
    )
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
    assert events.index(("explore", False, True)) < events.index(("tasks",))
    assert [event for event in events if event == ("select_magic",)] == [
        ("select_magic",),
        ("select_magic",),
    ]
    assert result["supply"]["status"] == "sufficient"
    assert result["supply"]["tianyan_before"] == 2033


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
        lambda *_args, **_kwargs: ({1010004: 2000}, {"read_only": True}),
    )
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
    assert not any(event[0] == "tasks" for event in events)


def test_compound_uses_tianlei_exchange_to_raise_low_tianyan_to_3000(
    monkeypatch,
) -> None:
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

    def explore(_runner, _ctx, explore_payload, _stop, **kwargs):
        events.append(
            (
                "explore",
                kwargs["manage_schedule"],
                "magic_invasion_progress" in explore_payload,
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
        lambda *_args, **_kwargs: ({1010004: 1999}, {"read_only": True}),
    )
    monkeypatch.setattr(compound, "execute_magic_invasion_explore_job", explore)

    def supply(_runner, _ctx, _stop, *, required_tianyan):
        events.append(("supply", required_tianyan))
        yield None
        return {"status": "supplied", "tianyan_after": required_tianyan}

    monkeypatch.setattr(compound, "ensure_magic_tianyan_supply", supply)
    monkeypatch.setattr(
        compound,
        "load_magic_invasion_occurrence_progress",
        lambda _occurrence: {
            "occurrence_id": "8070001400004",
            "state": "ready",
            "base_explore_count": 0,
            "confirmed_batches": [],
        },
    )
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
    assert ("supply", 3000) in events
    assert ("explore", False, False) in events
    assert result["supply"]["tianyan_before"] == 1999
    assert result["supply"]["tianyan_after"] == 3000


def test_occurrence_evidence_blocks_before_any_optional_action(monkeypatch) -> None:
    events = []
    runner = _Runner(events)
    monkeypatch.setattr(
        compound,
        "load_magic_invasion_occurrence_progress",
        lambda _occurrence: (_ for _ in ()).throw(
            RuntimeError("魔道入侵上一 attempt 留有未闭合不可逆证据")
        ),
    )
    with pytest.raises(RuntimeError, match="未闭合不可逆证据"):
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
