from __future__ import annotations

import threading

import pytest

from backend.core.fanxiu.data_annotation.tasks.dongtian_research import (
    enter_dongtian_home_for_research,
)


def _drain(generator):
    try:
        while True:
            next(generator)
    except StopIteration as exc:
        return exc.value


class _Runtime:
    def __init__(self, start_scene_id: int) -> None:
        self.start_scene_id = start_scene_id

    def current_scene(self, candidates, *, update=False):
        candidate_ids = tuple(candidates)
        if candidate_ids == (34, 66, 477, 69):
            return self.start_scene_id, 100.0, f"scene-{self.start_scene_id}"
        if candidate_ids == (34, 69):
            return 34, 100.0, "scene-34"
        raise AssertionError(f"unexpected current_scene candidates: {candidate_ids}")

    def ocr_text(self, frame):
        return str(frame)


class _Runner:
    def __init__(self, start_scene_id: int) -> None:
        self.runtime = _Runtime(start_scene_id)
        self.start_scene_id = start_scene_id
        self.actions: list[tuple] = []

    def _fanxiu_runtime(self, _ctx, *, stop_event):
        assert isinstance(stop_event, threading.Event)
        return self.runtime

    def _leave_world_side_scene_if_present(
        self,
        _ctx,
        _stop_event,
        frame,
        text,
        *,
        label,
    ):
        self.actions.append(("normalize_side", self.start_scene_id, frame, text, label))
        if False:
            yield None
        return self.start_scene_id in {66, 477}

    def _enter_daily_from_world_like(
        self,
        _ctx,
        runtime,
        _stop_event,
        frame,
        scene_id,
        text,
        *,
        label,
    ):
        assert runtime is self.runtime
        self.actions.append(("enter_daily", scene_id, frame, text, label))
        if False:
            yield None
        return 69

    def _open_daily_entry_from_daily(
        self,
        _ctx,
        _stop_event,
        _payload,
        **kwargs,
    ):
        self.actions.append(("open_daily_entry", kwargs))
        if False:
            yield None
        return "open"

    def _wait_daily_dongtian_home(
        self,
        _ctx,
        _stop_event,
        _payload,
        **kwargs,
    ):
        self.actions.append(("wait_home", kwargs))
        if False:
            yield None
        return 279

    def _claim_daily_dongtian_profit(self, *_args, **_kwargs):
        pytest.fail("research helper must not claim profit")

    def _execute_daily_dongtian_task(self, *_args, **_kwargs):
        pytest.fail("research helper must not execute the production claim job")

    def _execute_daily_dongtian_clear_task(self, *_args, **_kwargs):
        pytest.fail("research helper must not occupy or battle")

    def _persist_scheduler_task_next_time(self, *_args, **_kwargs):
        pytest.fail("research helper must not write next_time")


@pytest.mark.parametrize("start_scene_id", [34, 66, 477, 69])
def test_dongtian_research_helper_stops_at_279_without_business_actions(
    start_scene_id,
):
    runner = _Runner(start_scene_id)
    result = _drain(
        enter_dongtian_home_for_research(
            runner,
            {},
            threading.Event(),
            {"max_scrolls": 8},
        )
    )

    assert result == {
        "status": "ready_for_research",
        "scene_id": 279,
        "message": "已到洞天福地 #279，未执行收益、返回或占领动作",
    }
    action_names = [action[0] for action in runner.actions]
    if start_scene_id == 69:
        assert action_names == ["open_daily_entry", "wait_home"]
    else:
        assert action_names == [
            "normalize_side",
            "enter_daily",
            "open_daily_entry",
            "wait_home",
        ]
    open_kwargs = next(
        action[1] for action in runner.actions if action[0] == "open_daily_entry"
    )
    assert open_kwargs == {
        "task_label": "洞天_研究入口",
        "title_pattern": r"洞天|九曜\s*玄墨",
        "progress_can_mark_done": False,
    }
    wait_kwargs = next(
        action[1] for action in runner.actions if action[0] == "wait_home"
    )
    assert wait_kwargs == {
        "task_label": "洞天_研究入口",
        "allow_claim_page": False,
    }


def test_dongtian_research_helper_rejects_unproven_start_scene():
    runner = _Runner(279)

    with pytest.raises(RuntimeError, match="只接受 #34/#66/#477/#69"):
        _drain(
            enter_dongtian_home_for_research(
                runner,
                {},
                threading.Event(),
            )
        )

    assert runner.actions == []


def test_dongtian_research_helper_is_not_registered_as_a_task_cell():
    from backend.core.fanxiu.data_annotation.default_jobs import (
        register_fanxiu_data_annotation_default_runtime_jobs,
    )
    from backend.core.fanxiu.data_annotation.jobs import (
        list_fanxiu_data_annotation_task_cell_definitions,
    )

    register_fanxiu_data_annotation_default_runtime_jobs()

    assert "洞天_研究入口" not in {
        definition.label
        for definition in list_fanxiu_data_annotation_task_cell_definitions()
    }
