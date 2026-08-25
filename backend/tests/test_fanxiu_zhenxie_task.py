from __future__ import annotations

from datetime import datetime as RealDateTime
from types import SimpleNamespace

from backend.core.fanxiu.data_annotation.tasks import zhenxie
from backend.core.fanxiu.data_annotation.tasks.zhenxie import ZhenxieTaskMixin
from backend.core.fanxiu.data_annotation.jobs import get_fanxiu_data_annotation_task_cell_definition
from backend.core.fanxiu.data_annotation.default_jobs import register_fanxiu_data_annotation_default_runtime_jobs


class _Runtime:
    def __init__(self, initial_scene=63, landings=None):
        self.calls: list[tuple] = []
        self.initial_scene = initial_scene
        self.landings = landings or {}
        self.ctx = {}
        self.runner = SimpleNamespace(
            _click_generic_back=lambda ctx: self.calls.append(("generic_back", ctx))
        )
        self.next_times = []

    def set_next_time(self, next_time):
        self.next_times.append(next_time)

    def goto_view(self, scene_id, **options):
        self.calls.append(("goto_view", scene_id, options))
        if False:
            yield None
        return SimpleNamespace(id=scene_id)

    def current_scene(self, scene_ids, *, update=False):
        self.calls.append(("current_scene", scene_ids, {"update": update}))
        return self.initial_scene, 100.0, "frame"

    def wait_click_then_view(self, scene_id, shape, *targets, **options):
        self.calls.append(("wait_click_then_view", scene_id, shape, targets, options))
        if False:
            yield None
        landing = self.landings.get(scene_id, {66: 63, 63: 271, 271: 272, 85: 34, 186: 34}[scene_id])
        return SimpleNamespace(id=landing)

    def wait_view(self, *scene_ids, **options):
        self.calls.append(("wait_view", scene_ids, options))
        if False:
            yield None
        return SimpleNamespace(id=272)

    def wait_scene(self, *scene_ids, **options):
        self.calls.append(("wait_scene", scene_ids, options))
        if False:
            yield None
        return SimpleNamespace(id=271)

    def wait_click(self, scene_id, shape):
        self.calls.append(("wait_click", scene_id, shape))
        if False:
            yield None
        return None

    def click_shape(self, scene_id, shape):
        self.calls.append(("click_shape", scene_id, shape))

    def wait_action_settle(self, seconds):
        self.calls.append(("wait_action_settle", seconds))
        if False:
            yield None
        return None

    def clear_frame(self):
        self.calls.append(("clear_frame",))


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


def test_zhenxie_prefers_scene_63_prompt_then_reaches_272(monkeypatch):
    monkeypatch.setattr(zhenxie, "datetime", _InZhenxieWindow)
    runtime = _Runtime()

    result = _drain(ZhenxieTaskMixin().daily_zhenxie_flow(runtime))

    assert not any(call[:2] == ("goto_view", 66) for call in runtime.calls)
    assert ("wait_click", 63, "前往") in runtime.calls
    assert ("wait_action_settle", 1.0) in runtime.calls
    assert ("wait_scene", (271,), {
        "timeout": 180.0,
        "layer0_wait_seconds": 180.0,
        "label": "日常_镇邪：#63[前往] 后等待 #271",
    }) in runtime.calls
    assert ("wait_click_then_view", 271, "参加", (272, 85), {"timeout": 20.0}) in runtime.calls
    assert ("wait_click", 272, "前往") in runtime.calls
    assert ("wait_action_settle", 30.0) in runtime.calls
    assert ("wait_action_settle", 241.0) not in runtime.calls
    assert ("wait_view", (34, 85, 186, 86, 272, 271), {
        "timeout": 90.0,
        "label": "日常_镇邪：参战后等待可离开的稳定场景",
    }) in runtime.calls
    assert result["result"] == "success"
    assert ("generic_back", runtime.ctx) in runtime.calls
    assert ("goto_view", 34, {}) in runtime.calls
    assert result["current_scene"] == 34


class _AfterZhenxieWindow(RealDateTime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 7, 18, 21, 6, 0, tzinfo=tz)


def test_zhenxie_after_window_advances_schedule_even_if_cleanup_is_still_transitioning(monkeypatch):
    monkeypatch.setattr(zhenxie, "datetime", _AfterZhenxieWindow)

    class Runner(ZhenxieTaskMixin):
        def _log(self, *_args):
            return None

        def _persist_admission_decision(self, payload, decision):
            result = dict(decision)
            self.next_time = (payload["__scheduler_task_id"], result.pop("next_time"))
            return result

    result = Runner().daily_zhenxie_admission(
        {"__scheduler_task_id": "daily-zhenxie"}
    )

    assert result["result"] == "success"
    assert "next_time" not in result
    assert result["current_scene"] is None
    assert "未执行游戏操作" in result["message"]


def test_zhenxie_after_window_reports_scene_271_without_waiting_unknown(monkeypatch):
    monkeypatch.setattr(zhenxie, "datetime", _AfterZhenxieWindow)

    class Scene271Runtime(_Runtime):
        def wait_view(self, *scene_ids, **options):
            self.calls.append(("wait_view", scene_ids, options))
            if False:
                yield None
            return SimpleNamespace(id=271)

    class Runner(ZhenxieTaskMixin):
        def _log(self, *_args):
            return None

        def _persist_admission_decision(self, payload, decision):
            result = dict(decision)
            self.next_time = (payload["__scheduler_task_id"], result.pop("next_time"))
            return result

    result = Runner().daily_zhenxie_admission(
        {"__scheduler_task_id": "daily-zhenxie"}
    )

    assert result["result"] == "success"
    assert "next_time" not in result
    assert result["current_scene"] is None
    assert "未执行游戏操作" in result["message"]


def test_zhenxie_falls_back_to_scene_66_when_prompt_is_absent(monkeypatch):
    monkeypatch.setattr(zhenxie, "datetime", _InZhenxieWindow)
    runtime = _Runtime(initial_scene=34)

    result = _drain(ZhenxieTaskMixin().daily_zhenxie_flow(runtime))

    navigation_calls = [call for call in runtime.calls if call[0] in {"goto_view", "wait_click_then_view", "wait_click"}]
    assert navigation_calls == [
        ("goto_view", 66, {}),
        ("wait_click_then_view", 66, "前往", (63, 271, 272, 85), {"timeout": 20.0}),
        ("wait_click", 63, "前往"),
        ("wait_click_then_view", 271, "参加", (272, 85), {"timeout": 20.0}),
        ("wait_click", 272, "前往"),
        ("goto_view", 34, {}),
    ]
    assert runtime.calls.index(("wait_action_settle", 3.0)) < runtime.calls.index(
        ("wait_click_then_view", 66, "前往", (63, 271, 272, 85), {"timeout": 20.0})
    )
    assert result["result"] == "success"


def test_zhenxie_keeps_scene_271_as_layer0_after_scene_63(monkeypatch):
    monkeypatch.setattr(zhenxie, "datetime", _InZhenxieWindow)
    runtime = _Runtime(initial_scene=63)

    result = _drain(ZhenxieTaskMixin().daily_zhenxie_flow(runtime))

    assert ("wait_scene", (271,), {
        "timeout": 180.0,
        "layer0_wait_seconds": 180.0,
        "label": "日常_镇邪：#63[前往] 后等待 #271",
    }) in runtime.calls
    assert not any(call[:2] == ("goto_view", 66) for call in runtime.calls)
    assert result["result"] == "success"


def test_zhenxie_resumes_from_participation_page_without_returning_to_schedule(monkeypatch):
    monkeypatch.setattr(zhenxie, "datetime", _InZhenxieWindow)
    runtime = _Runtime(initial_scene=271)

    result = _drain(ZhenxieTaskMixin().daily_zhenxie_flow(runtime))

    assert not any(call[:2] == ("goto_view", 66) for call in runtime.calls)
    assert ("wait_click_then_view", 271, "参加", (272, 85), {"timeout": 20.0}) in runtime.calls
    assert ("wait_click", 272, "前往") in runtime.calls
    assert result["result"] == "success"


def test_zhenxie_accepts_already_entered_activity_region(monkeypatch):
    monkeypatch.setattr(zhenxie, "datetime", _InZhenxieWindow)
    runtime = _Runtime(initial_scene=85)

    result = _drain(ZhenxieTaskMixin().daily_zhenxie_flow(runtime))

    assert not any(call[:2] == ("goto_view", 66) for call in runtime.calls)
    assert not any(call[0] in {"wait_click_then_view", "wait_click"} for call in runtime.calls)
    assert result["result"] == "success"


def test_zhenxie_accepts_specific_scene_186_and_uses_its_leave_shape():
    class Scene186Runtime(_Runtime):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.wait_landings = iter([186, 34])

        def wait_view(self, *scene_ids, **options):
            self.calls.append(("wait_view", scene_ids, options))
            if False:
                yield None
            return SimpleNamespace(id=next(self.wait_landings))

    runtime = Scene186Runtime(initial_scene=34)
    result = _drain(ZhenxieTaskMixin()._leave_daily_zhenxie(runtime))

    assert ("wait_view", (34, 85, 186, 86, 272, 271), {
        "timeout": 90.0,
        "label": "日常_镇邪：参战后等待可离开的稳定场景",
    }) in runtime.calls
    assert ("click_shape", 186, "离开") in runtime.calls
    assert any(
        call[0:2] == ("wait_view", (34, 85, 186, 86))
        and call[2]["label"] == "日常_镇邪：点击离开后重新识别多层落点"
        for call in runtime.calls
    )
    assert result == 34


def test_zhenxie_consumes_two_leave_and_confirmation_layers():
    class NestedRuntime(_Runtime):
        def __init__(self):
            super().__init__(initial_scene=34)
            self.wait_landings = iter([85, 86, 86])
            self.confirm_landings = iter([186, 34])

        def wait_view(self, *scene_ids, **options):
            self.calls.append(("wait_view", scene_ids, options))
            if False:
                yield None
            return SimpleNamespace(id=next(self.wait_landings))

        def wait_click_then_view(self, scene_id, shape, *targets, **options):
            self.calls.append(("wait_click_then_view", scene_id, shape, targets, options))
            if False:
                yield None
            return SimpleNamespace(id=next(self.confirm_landings))

    runtime = NestedRuntime()
    result = _drain(ZhenxieTaskMixin()._leave_daily_zhenxie(runtime))

    assert result == 34
    assert [call[:3] for call in runtime.calls if call[0] == "click_shape"] == [
        ("click_shape", 85, "离开"),
        ("click_shape", 186, "离开"),
    ]
    assert len([call for call in runtime.calls if call[0] == "wait_click_then_view"]) == 2


def test_zhenxie_task_definition_does_not_force_world_start():
    register_fanxiu_data_annotation_default_runtime_jobs()
    definition = get_fanxiu_data_annotation_task_cell_definition("daily_zhenxie")

    assert definition is not None
    assert not hasattr(definition, "lifecycle")


def test_task_managed_gameplay_jobs_still_finish_at_world():
    register_fanxiu_data_annotation_default_runtime_jobs()

    for task_type in {
        "daily_zhenxie",
        "daily_lingquan",
        "daily_daofa",
        "daily_xianyuan",
        "weekly_shengzu",
    }:
        definition = get_fanxiu_data_annotation_task_cell_definition(task_type)
        assert definition is not None
        assert not hasattr(definition, "lifecycle")
