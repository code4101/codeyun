from __future__ import annotations

import threading

from backend.core.fanxiu.data_annotation.default_jobs import (
    register_fanxiu_data_annotation_default_runtime_jobs,
)
from backend.core.fanxiu.data_annotation.jobs import (
    get_fanxiu_data_annotation_task_cell_definition,
)
from backend.core.fanxiu.runtime.behavior_tree import (
    create_fanxiu_runtime_runner,
)
from backend.core.fanxiu.data_annotation.scheduler_defaults import (
    default_data_annotation_scheduler_tasks,
)


def _finish(generator):
    while True:
        try:
            next(generator)
        except StopIteration as stop:
            return stop.value


def test_xianqiao_trial_is_registered_as_enabled_daily_five_task():
    register_fanxiu_data_annotation_default_runtime_jobs()

    definition = get_fanxiu_data_annotation_task_cell_definition("xianqiao_trial")
    assert definition is not None
    assert definition.label == "仙窍_试炼"
    assert definition.scheduler_supported is True
    assert definition.stable_start_scene_id == 34

    task = next(
        item
        for item in default_data_annotation_scheduler_tasks()
        if item["id"] == "xianqiao-trial"
    )
    assert task["task_type"] == "xianqiao_trial"
    assert task["label"] == "仙窍_试炼"
    assert task["schedule_kind"] == "daily"
    assert task["schedule_times"] == ["05:00"]
    assert task["enabled"] is True
    assert task["payload"]["target_daily_purchases"] == 3


def test_xianqiao_trial_task_flow_enters_then_runs_complete_daily(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    runtime = runner._fanxiu_runtime({"images": {}, "attrs": {"payload": {}}}, stop_event=threading.Event())
    events: list[str] = []

    def enter(**_kwargs):
        events.append("enter")
        if False:
            yield None
        return {"terminal_scene": 357}

    def daily(**_kwargs):
        events.append("daily")
        if False:
            yield None
        return {
            "result": "success",
            "message": "仙窍_试炼完成，已回到世界 #34",
            "current_scene": 34,
        }

    monkeypatch.setattr(runtime, "enter_xianqiao_trial", enter)
    monkeypatch.setattr(runtime, "run_xianqiao_trial_daily", daily)

    result = _finish(runner.xianqiao_trial_flow(runtime))

    assert events == ["enter", "daily"]
    assert result["entry"] == {"terminal_scene": 357}
    assert result["current_scene"] == 34


def test_enter_xianqiao_trial_uses_daily_entry_then_unique_zhenxian_ocr(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    runtime = runner._fanxiu_runtime(
        {
            "images": {
                69: {"id": 69, "title": "日常", "width": 900, "height": 1600, "shapes": []},
                356: {
                    "id": 356,
                    "title": "仙窍分类",
                    "width": 900,
                    "height": 1600,
                    "shapes": [{"title": "试炼", "x": 0.1, "y": 0.7, "w": 0.8, "h": 0.06}],
                },
                357: {"id": 357, "title": "仙窍试炼", "width": 900, "height": 1600, "shapes": []},
            }
        },
        stop_event=threading.Event(),
    )
    events: list[object] = []

    def goto(view, **_kwargs):
        events.append(("goto", int(view)))
        if False:
            yield None
        return "success"

    def open_entry(**kwargs):
        events.append(("entry", kwargs["title_pattern"]))
        if False:
            yield None
        return "open"

    def wait_view(view, **_kwargs):
        events.append(("wait", int(view)))
        if False:
            yield None
        return runtime.view(view)

    def settle(_seconds):
        if False:
            yield None

    monkeypatch.setattr(runtime, "goto_view", goto)
    monkeypatch.setattr(runtime, "open_daily_entry", open_entry)
    monkeypatch.setattr(runtime, "wait_view", wait_view)
    monkeypatch.setattr(runtime, "cur_frame", lambda **_kwargs: "frame")
    monkeypatch.setattr(
        runtime,
        "ocr_centers_in_shape",
        lambda *_args, **_kwargs: [(450.0, 1160.0, "真仙")],
    )
    monkeypatch.setattr(
        runtime,
        "click_frame_point",
        lambda view, x, y: events.append(("click", int(view), x, y)),
    )
    monkeypatch.setattr(runtime, "wait_action_settle", settle)

    result = _finish(runtime.enter_xianqiao_trial(settle_seconds=0))

    assert events == [
        ("goto", 69),
        ("entry", r"仙\s*窍"),
        ("wait", 356),
        ("click", 356, 450.0, 1160.0),
        ("wait", 357),
    ]
    assert result["terminal_scene"] == 357
