from __future__ import annotations

from datetime import datetime as RealDateTime
from types import SimpleNamespace

from backend.core.fanxiu.data_annotation.tasks import zhenxie
from backend.core.fanxiu.data_annotation.tasks.zhenxie import ZhenxieTaskMixin
from backend.core.fanxiu.data_annotation.jobs import get_fanxiu_data_annotation_task_cell_definition
from backend.core.fanxiu.data_annotation.default_jobs import register_fanxiu_data_annotation_default_runtime_jobs


class _Runtime:
    def __init__(self):
        self.calls: list[tuple] = []

    def goto_view(self, scene_id, **options):
        self.calls.append(("goto_view", scene_id, options))
        if False:
            yield None
        return SimpleNamespace(id=scene_id)

    def wait_click(self, scene_id, shape):
        self.calls.append(("wait_click", scene_id, shape))
        if False:
            yield None
        return None

    def wait_action_settle(self, seconds):
        self.calls.append(("wait_action_settle", seconds))
        if False:
            yield None
        return None


class _InZhenxieWindow(RealDateTime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 7, 18, 21, 1, 0, tzinfo=tz)


def _drain(generator):
    while True:
        try:
            next(generator)
        except StopIteration as stop:
            return stop.value


def test_zhenxie_uses_goto_272_without_forcing_an_initial_scene(monkeypatch):
    monkeypatch.setattr(zhenxie, "datetime", _InZhenxieWindow)
    runtime = _Runtime()

    result = _drain(ZhenxieTaskMixin().daily_zhenxie_flow(runtime))

    assert ("goto_view", 272, {"layer0_wait_seconds": 90.0}) in runtime.calls
    assert not any(call[0] == "goto_view" and call[1] == 34 for call in runtime.calls)
    assert ("wait_click", 272, "\u524d\u5f80") in runtime.calls
    assert result["result"] == "success"


def test_zhenxie_task_definition_does_not_force_world_start():
    register_fanxiu_data_annotation_default_runtime_jobs()
    definition = get_fanxiu_data_annotation_task_cell_definition("daily_zhenxie")

    assert definition is not None
    assert definition.stable_start_scene_id is None
