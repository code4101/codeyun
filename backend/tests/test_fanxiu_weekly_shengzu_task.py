from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import backend.core.fanxiu.data_annotation.tasks.weekly_shengzu as weekly_shengzu
from backend.core.fanxiu.data_annotation.default_jobs import register_fanxiu_data_annotation_default_runtime_jobs
from backend.core.fanxiu.data_annotation.jobs import get_fanxiu_data_annotation_task_cell_definition
from backend.core.fanxiu.data_annotation.scheduler_defaults import default_data_annotation_scheduler_tasks
from backend.core.fanxiu.data_annotation.tasks.weekly_shengzu import WeeklyShengzuTaskMixin


def _drain(generator):
    try:
        while True:
            next(generator)
    except StopIteration as exc:
        return exc.value


class _FakeRuntime:
    def __init__(self, scene_id: int = 34) -> None:
        self.scene_id = scene_id
        self.actions: list[tuple] = []
        self.item = SimpleNamespace(name="圣祖")
        self.next_times: list[str | None] = []

    def set_next_time(self, next_time: str | None) -> None:
        self.next_times.append(next_time)

    def current_scene(self, _scenes, **_kwargs):
        self.actions.append(("current", self.scene_id))
        return self.scene_id, 100.0, "frame"

    def goto_view(self, scene, **_kwargs):
        self.actions.append(("goto", scene))
        self.scene_id = scene
        if False:
            yield None

    def cur_frame(self, **_kwargs):
        return "frame"

    def find_floating_items_by_anchor_text(self, *args, **kwargs):
        self.actions.append(("find", args, kwargs))
        return [self.item]

    def floating_item_is_fully_inside(self, item, container):
        self.actions.append(("inside", item.name, container))
        return True

    def floating_item_field_is_inside(self, item, field, container):
        self.actions.append(("field_inside", item.name, field, container))
        return True

    def read_floating_item_field(self, item, field, **_kwargs):
        self.actions.append(("read", item.name, field))
        return "前往"

    def click_floating_item_field(self, item, field):
        self.actions.append(("floating_click", item.name, field))
        self.scene_id = 384

    def wait_view(self, scene, **_kwargs):
        self.actions.append(("wait", scene))
        self.scene_id = scene
        if False:
            yield None
        return SimpleNamespace(id=scene)

    def scroll_shape_content(self, scene, shape):
        self.actions.append(("scroll", scene, shape))
        if False:
            yield None
        return False

    def click_shape_center_then_view(self, scene, shape, target, **_kwargs):
        self.actions.append(("shape", scene, shape, target))
        self.scene_id = target
        if False:
            yield None
        return SimpleNamespace(id=target)

    def wait_click(self, scene, shape):
        self.actions.append(("click", scene, shape))
        if False:
            yield None

    def wait_click_then_view(self, scene, shape, targets, **_kwargs):
        self.actions.append(("click_then_view", scene, shape, tuple(targets)))
        self.scene_id = 338
        if False:
            yield None
        return SimpleNamespace(id=338)

    def click_ocr_text(self, scene, text, **_kwargs):
        self.actions.append(("ocr_click", scene, text))
        self.scene_id = 86
        return SimpleNamespace(text=text)

    def wait_action_settle(self, seconds):
        self.actions.append(("settle", seconds))
        if False:
            yield None


class _Runner(WeeklyShengzuTaskMixin):
    def __init__(self, runtime: _FakeRuntime) -> None:
        self.runtime = runtime
        self.logs: list[tuple[str, str]] = []
        self.next_times = []

    def _persist_scheduler_task_next_time(self, task_id, next_time):
        self.next_times.append((task_id, next_time))

    def _persist_admission_decision(self, payload, decision):
        result = dict(decision)
        next_time = result.pop("next_time")
        self._persist_scheduler_task_next_time(payload["__scheduler_task_id"], next_time)
        return result

    def _fanxiu_runtime(self, *_args, **_kwargs):
        return self.runtime

    def _log(self, kind, message):
        self.logs.append((kind, message))


def test_weekly_shengzu_outside_window_does_not_touch_game(monkeypatch):
    monkeypatch.setattr(weekly_shengzu, "_now", lambda: datetime(2026, 7, 19, 20, 5, 1))
    runtime = _FakeRuntime()

    runner = _Runner(runtime)
    result = runner.weekly_shengzu_admission(
        {"__scheduler_task_id": "weekly-shengzu"}
    )

    assert result["result"] == "success"
    assert result["current_scene"] is None
    assert "next_time" not in result
    assert runner.next_times == [("weekly-shengzu", "2026-07-26 20:00:00")]
    assert runtime.actions == []


def test_weekly_shengzu_runs_full_flow_from_world_in_window(monkeypatch):
    monkeypatch.setattr(weekly_shengzu, "_now", lambda: datetime(2026, 7, 19, 20, 3, 0))
    runtime = _FakeRuntime(scene_id=34)

    result = _drain(_Runner(runtime)._execute_weekly_shengzu_task(
        {"asset_tree_path": Path("asset-tree.json")},
        object(),
        {},
    ))

    assert result["result"] == "success"
    assert result["current_scene"] == 34
    assert result["message"] == "周常_圣祖：已参战并运行至少 30 秒，离开后返回世界 #34"
    assert "next_time" not in result
    assert runtime.next_times == ["2026-07-26 20:00:00"]
    assert ("goto", 69) in runtime.actions
    assert ("floating_click", "圣祖", "任务状态") in runtime.actions
    assert ("shape", 384, "前往", 385) in runtime.actions
    assert ("click_then_view", 385, "前往挑战", (338, 34, 339)) in runtime.actions
    assert ("settle", 30.0) in runtime.actions
    assert ("ocr_click", 338, "离开") in runtime.actions
    assert runtime.actions.index(("settle", 30.0)) < runtime.actions.index(("ocr_click", 338, "离开"))
    assert runtime.actions[-1] == ("goto", 34)


def test_weekly_shengzu_can_goto_385_directly_from_383(monkeypatch):
    monkeypatch.setattr(weekly_shengzu, "_now", lambda: datetime(2026, 7, 19, 20, 0, 0))
    runtime = _FakeRuntime(scene_id=383)

    result = _drain(_Runner(runtime)._execute_weekly_shengzu_task(
        {"asset_tree_path": Path("asset-tree.json")},
        object(),
        {},
    ))

    assert result["result"] == "success"
    assert ("goto", 385) in runtime.actions
    assert not any(action[0] == "find" for action in runtime.actions)


def test_weekly_shengzu_is_registered_as_scheduler_supported_type():
    register_fanxiu_data_annotation_default_runtime_jobs()

    definition = get_fanxiu_data_annotation_task_cell_definition("weekly_shengzu")

    assert definition is not None
    assert definition.label == "周常_圣祖"
    assert definition.scheduler_supported is True
    assert not hasattr(definition, "lifecycle")


def test_weekly_shengzu_is_enabled_as_standard_sunday_job():
    task = next(
        item
        for item in default_data_annotation_scheduler_tasks()
        if item["id"] == "weekly-shengzu"
    )

    assert task["task_type"] == "weekly_shengzu"
    assert task["label"] == "周常_圣祖"
    assert task["trigger_description"] == "每周"
    assert task["next_time"]
    assert task["dispatch_level"] == 1
    assert task["error_retry_delay_seconds"] == 0
