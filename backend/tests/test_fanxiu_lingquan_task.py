from datetime import datetime, timedelta

import pytest

from backend.core.fanxiu.data_annotation import scheduler_defaults
from backend.core.fanxiu.data_annotation.default_jobs import register_fanxiu_data_annotation_default_runtime_jobs
from backend.core.fanxiu.data_annotation.jobs import get_fanxiu_data_annotation_task_cell_definition
from backend.core.fanxiu.data_annotation.tasks import lingquan
from backend.core.fanxiu.data_annotation.tasks.lingquan import LingquanTaskMixin


def _drain(generator):
    try:
        while True:
            next(generator)
    except StopIteration as exc:
        return exc.value


class _OutsideWindowRunner(LingquanTaskMixin):
    def _persist_scheduler_task_next_time(self, task_id, next_time):
        self.next_time = (task_id, next_time)

    def _persist_admission_decision(self, payload, decision):
        result = dict(decision)
        next_time = result.pop("next_time")
        task_id = str(payload.get("__scheduler_task_id") or "").strip()
        if not task_id:
            raise RuntimeError("作业准入写入 next_time 时缺少 __scheduler_task_id")
        self._persist_scheduler_task_next_time(task_id, next_time)
        return result

    def _fanxiu_runtime(self, *_args, **_kwargs):
        raise AssertionError("窗口外不得取得 Runtime 或操作游戏")


class _Clock:
    def __init__(self):
        self.value = datetime(2026, 7, 20, 20, 30, 0)


class _TimedRuntime:
    def __init__(self, clock):
        self.clock = clock
        self.ocr_times = []
        self.countdowns = [32, 1]
        self.questions = ["第一题", ""]

    def cur_frame(self, update=False):
        assert update is True
        return "frame"

    def current_scene(self, preferred_scene_ids, update=False):
        assert 389 in preferred_scene_ids
        assert update is True
        return 389, 100.0, "frame"

    def ocr_numbers_in_shapes(self, *_args, **_kwargs):
        self.ocr_times.append(self.clock.value)
        value = self.countdowns.pop(0)
        return [value], str(value)

    def ocr_text_in_shapes(self, *_args, **_kwargs):
        return self.questions.pop(0)

    def wait_action_settle(self, seconds):
        self.clock.value += timedelta(seconds=seconds)
        if False:
            yield None


class _TimedRunner(LingquanTaskMixin):
    def _log(self, *_args, **_kwargs):
        return None

    def _answer_lingquan_question(self, runtime, **_kwargs):
        yield from runtime.wait_action_settle(5)
        return {"answered": True}


class _ExpiredEntryRuntime:
    def __init__(self, clock):
        self.clock = clock
        self.actions = []
        self.next_times = []

    def set_next_time(self, next_time):
        self.next_times.append(next_time)

    def current_scene(self, *_args, **_kwargs):
        return None, 0, "frame"

    def wait_view(self, *scenes, **kwargs):
        self.actions.append(("wait_view", scenes, kwargs))
        self.clock.value = datetime(2026, 7, 20, 20, 41, 0)
        if False:
            yield None
        raise TimeoutError("window ended")

    def goto_view(self, scene):
        self.actions.append(("goto", scene))
        if scene == 66:
            self.clock.value = datetime(2026, 7, 20, 20, 41, 0)
        if False:
            yield None


class _ExpiredEntryRunner(LingquanTaskMixin):
    def _log(self, *_args, **_kwargs):
        return None

    def __init__(self, runtime):
        self.runtime = runtime

    def _fanxiu_runtime(self, *_args, **_kwargs):
        return self.runtime


def test_outside_window_does_not_touch_runtime(monkeypatch):
    monkeypatch.setattr(lingquan, "_now", lambda: datetime(2026, 7, 20, 21, 0, 0))
    runner = _OutsideWindowRunner()
    result = runner.daily_lingquan_admission({"__scheduler_task_id": "legacy-daily-lingquan"})
    assert result["result"] == "success"
    assert result["current_scene"] is None
    assert "未执行游戏操作" in result["message"]
    assert runner.next_time == ("legacy-daily-lingquan", "2026-07-21 20:30:00")


def test_outside_window_without_scheduler_job_id_fails_closed(monkeypatch):
    monkeypatch.setattr(lingquan, "_now", lambda: datetime(2026, 7, 20, 21, 0, 0))
    runner = _OutsideWindowRunner()

    with pytest.raises(RuntimeError, match="缺少 __scheduler_task_id"):
        runner.daily_lingquan_admission({})

    assert not hasattr(runner, "next_time")


def test_lingquan_is_registered_and_scheduled_at_2030():
    register_fanxiu_data_annotation_default_runtime_jobs()
    definition = get_fanxiu_data_annotation_task_cell_definition("daily_lingquan")
    assert definition is not None
    assert definition.scheduler_supported is True
    assert not hasattr(definition, "lifecycle")

    task = next(item for item in scheduler_defaults.default_data_annotation_scheduler_tasks() if item["id"] == "legacy-daily-lingquan")
    assert task["task_type"] == "daily_lingquan"
    assert task["trigger_description"] == "每日"
    assert task["next_time"]
    assert task["dispatch_level"] == 1
    assert task["error_retry_delay_seconds"] == 0
    assert "window" not in task


def test_countdown_cooldown_starts_when_countdown_is_observed(monkeypatch):
    clock = _Clock()
    runtime = _TimedRuntime(clock)
    monkeypatch.setattr(lingquan, "_now", lambda: clock.value)
    monkeypatch.setattr(
        "backend.core.fanxiu.instrumentation."
        "fanxiu_instrumentation_service.lingquan_question_snapshot",
        lambda **_kwargs: {"available": False},
    )
    cutoff = clock.value + timedelta(seconds=33)

    answers = _drain(_TimedRunner()._run_lingquan_question_loop(
        runtime,
        cutoff=cutoff,
        transition_timeout=20,
        score_threshold=90,
        poll_seconds=1,
    ))

    assert answers == 1
    assert runtime.ocr_times == [
        datetime(2026, 7, 20, 20, 30, 0),
    ]


def test_question_text_can_start_answer_when_countdown_ocr_is_missing(monkeypatch):
    clock = _Clock()
    runtime = _TimedRuntime(clock)
    runtime.countdowns = [0]
    runtime.questions = ["第一题"]
    monkeypatch.setattr(lingquan, "_now", lambda: clock.value)
    monkeypatch.setattr(
        "backend.core.fanxiu.instrumentation."
        "fanxiu_instrumentation_service.lingquan_question_snapshot",
        lambda **_kwargs: {"available": False},
    )

    answers = _drain(_TimedRunner()._run_lingquan_question_loop(
        runtime,
        cutoff=clock.value + timedelta(seconds=1),
        transition_timeout=20,
        score_threshold=90,
        poll_seconds=1,
    ))

    assert answers == 1


def test_lingquan_prefers_fresh_runtime_question_without_ocr(monkeypatch):
    clock = _Clock()
    runtime = _TimedRuntime(clock)
    monkeypatch.setattr(lingquan, "_now", lambda: clock.value)
    monkeypatch.setattr(
        "backend.core.fanxiu.instrumentation."
        "fanxiu_instrumentation_service.lingquan_question_snapshot",
        lambda **_kwargs: {
            "available": True,
            "fresh": True,
            "phase": "question",
            "question": "Runtime题目",
            "question_index": 1,
            "remaining_seconds": 1,
        },
    )

    answers = _drain(_TimedRunner()._run_lingquan_question_loop(
        runtime,
        cutoff=clock.value + timedelta(seconds=1),
        transition_timeout=20,
        score_threshold=90,
        poll_seconds=1,
    ))

    assert answers == 1
    assert runtime.ocr_times == []
    assert runtime.questions == ["第一题", ""]


def test_entry_expiring_at_cutoff_finishes_without_click_or_retry(monkeypatch, tmp_path):
    clock = _Clock()
    runtime = _ExpiredEntryRuntime(clock)
    runner = _ExpiredEntryRunner(runtime)
    monkeypatch.setattr(lingquan, "_now", lambda: clock.value)

    result = _drain(runner._execute_daily_lingquan_task(
        {"asset_tree_path": tmp_path / "asset-tree.json"},
        None,
        {},
    ))

    assert runtime.actions == [
        ("wait_view", (389, 388, 387, 386), {
            "timeout": 660.0,
            "label": "日常_灵泉：等待过渡结束并进入稳定业务场景",
        }),
    ]
    assert result["result"] == "success"
    assert "next_time" not in result
    assert runtime.next_times == ["2026-07-21 20:30:00"]
    assert "等待明日触发" in result["message"]


def test_lingquan_exit_consumes_scene_388_and_confirmation_86():
    calls = []

    class Runtime:
        def __init__(self):
            self.landings = iter([86])

        def current_scene(self, preferred_scene_ids, update=False):
            calls.append(("current_scene", preferred_scene_ids, update))
            return 388, 100.0, "frame"

        def click_shape(self, scene_id, shape):
            calls.append(("click_shape", scene_id, shape))

        def wait_action_settle(self, seconds):
            calls.append(("settle", seconds))
            if False:
                yield None

        def wait_view(self, *scene_ids, **kwargs):
            calls.append(("wait_view", scene_ids, kwargs))
            if False:
                yield None
            return next(self.landings)

        def wait_click_then_view(self, source, shape, target, **kwargs):
            calls.append(("wait_click_then_view", source, shape, target, kwargs))
            if False:
                yield None
            return 34

    _drain(_TimedRunner()._exit_lingquan_to_world(Runtime(), timeout=30.0))

    assert calls[0:3] == [
        ("current_scene", [34, 388, 186, 86], True),
        ("click_shape", 388, "离开"),
        ("settle", 2.0),
    ]
    assert calls[3][0:2] == ("wait_view", (86, 34, 388, 186))
    assert calls[3][2]["label"] == "日常_灵泉：点击离开后重新识别落点"
    assert calls[4][0:4] == (
        "wait_click_then_view",
        86,
        "确认",
        [34, 388, 186, 86],
    )


def test_lingquan_exit_consumes_two_nested_leave_layers_before_world():
    calls = []

    class Runtime:
        def __init__(self):
            self.leave_landings = iter([86, 86])
            self.confirm_landings = iter([186, 34])

        def current_scene(self, preferred_scene_ids, update=False):
            calls.append(("current_scene", preferred_scene_ids, update))
            return 388, 100.0, "frame"

        def click_shape(self, scene_id, shape):
            calls.append(("click_shape", scene_id, shape))

        def wait_action_settle(self, seconds):
            calls.append(("settle", seconds))
            if False:
                yield None

        def wait_view(self, *scene_ids, **kwargs):
            calls.append(("wait_view", scene_ids, kwargs))
            if False:
                yield None
            return next(self.leave_landings)

        def wait_click_then_view(self, source, shape, target, **kwargs):
            calls.append(("wait_click_then_view", source, shape, target, kwargs))
            if False:
                yield None
            return next(self.confirm_landings)

    _drain(_TimedRunner()._exit_lingquan_to_world(Runtime(), timeout=30.0))

    assert [call[:3] for call in calls if call[0] == "click_shape"] == [
        ("click_shape", 388, "离开"),
        ("click_shape", 186, "离开"),
    ]
    assert len([call for call in calls if call[0] == "wait_click_then_view"]) == 2


def test_lingquan_exit_accepts_world_landing_at_exact_timeout(monkeypatch):
    class Runtime:
        def current_scene(self, preferred_scene_ids, update=False):
            return 86, 100.0, "frame"

        def wait_click_then_view(self, source, shape, target, **kwargs):
            if False:
                yield None
            return 34

    monotonic_values = iter([0.0, 0.0, 0.0, 2.0])
    monkeypatch.setattr(lingquan.time, "monotonic", lambda: next(monotonic_values))

    _drain(_TimedRunner()._exit_lingquan_to_world(Runtime(), timeout=1.0))


def test_lingquan_does_not_swallow_exit_timeout_after_question_window(monkeypatch, tmp_path):
    class Runtime:
        def wait_click_then_view(self, *_args, **_kwargs):
            if False:
                yield None

    class Runner(LingquanTaskMixin):
        def _log(self, *_args, **_kwargs):
            return None

        def _fanxiu_runtime(self, *_args, **_kwargs):
            return Runtime()

        def _enter_lingquan(self, *_args, **_kwargs):
            if False:
                yield None

        def _wait_lingquan_until(self, *_args, **_kwargs):
            if False:
                yield None

        def _run_lingquan_question_loop(self, *_args, **_kwargs):
            if False:
                yield None
            return 0

        def _exit_lingquan_to_world(self, *_args, **_kwargs):
            if False:
                yield None
            raise TimeoutError("nested exit failed")

    monkeypatch.setattr(lingquan, "_now", lambda: datetime(2026, 7, 20, 20, 42, 59))
    monkeypatch.setattr(
        "backend.core.fanxiu.instrumentation."
        "fanxiu_instrumentation_service.lingquan_question_snapshot",
        lambda **_kwargs: {"available": False},
    )

    with pytest.raises(TimeoutError, match="nested exit failed"):
        _drain(Runner()._execute_daily_lingquan_task(
            {"asset_tree_path": tmp_path / "asset-tree.json"},
            None,
            {},
        ))


def test_lingquan_waits_three_seconds_after_entering_scene_66(monkeypatch):
    calls = []
    now = datetime(2026, 7, 20, 20, 30, 0)
    monkeypatch.setattr(lingquan, "_now", lambda: now)

    def select_activity(_runtime, pattern, **options):
        calls.append(("select_schedule_activity", pattern, options))
        if False:
            yield None
        raise RuntimeError("stop-after-66")

    monkeypatch.setattr(lingquan, "select_schedule_activity", select_activity)

    class Runtime:
        def current_scene(self, *_args, **_kwargs):
            return 34, 100.0, "frame"

        def goto_view(self, scene_id):
            calls.append(("goto_view", scene_id))
            if False:
                yield None

        def wait_action_settle(self, seconds):
            calls.append(("wait_action_settle", seconds))
            if False:
                yield None

        def wait_click_then_view(self, scene_id, shape, target, **kwargs):
            calls.append(("wait_click_then_view", scene_id, shape, target, kwargs))
            if False:
                yield None
            raise RuntimeError("stop-after-66")

    with pytest.raises(RuntimeError, match="stop-after-66"):
        _drain(_TimedRunner()._enter_lingquan(
            Runtime(),
            transition_timeout=20.0,
            deadline=now + timedelta(minutes=11),
        ))

    assert calls == [
        ("goto_view", 66),
        ("wait_action_settle", 3.0),
        ("select_schedule_activity", "灵泉", {
            "enter": True,
            "settle_seconds": 0.8,
        }),
    ]


@pytest.mark.parametrize(
    ("scene_id", "expected_calls"),
    [
        (389, []),
        (388, [("wait_click_then_view", 388, "进入问答", 389)]),
    ],
)
def test_lingquan_resumes_from_quiz_scenes_without_returning_world(monkeypatch, scene_id, expected_calls):
    calls = []
    now = datetime(2026, 7, 20, 20, 35, 0)
    monkeypatch.setattr(lingquan, "_now", lambda: now)

    class Runtime:
        def current_scene(self, preferred_scene_ids, update=False):
            assert preferred_scene_ids == [389, 388, 387, 386, 66, 34]
            assert update is True
            return scene_id, 100.0, "frame"

        def goto_view(self, target):
            calls.append(("goto_view", target))
            if False:
                yield None

        def wait_click_then_view(self, source, shape, target, **_kwargs):
            calls.append(("wait_click_then_view", source, shape, target))
            if False:
                yield None

    _drain(_TimedRunner()._enter_lingquan(
        Runtime(),
        transition_timeout=20.0,
        deadline=now + timedelta(minutes=6),
    ))

    assert calls == expected_calls


def test_lingquan_waits_through_2033_before_requiring_scene_389(monkeypatch):
    calls = []
    now = datetime(2026, 7, 20, 20, 30, 56)
    monkeypatch.setattr(lingquan, "_now", lambda: now)

    class Runtime:
        def current_scene(self, *_args, **_kwargs):
            return 388, 100.0, "frame"

        def wait_click_then_view(self, source, shape, target, **kwargs):
            calls.append((source, shape, target, kwargs))
            if False:
                yield None

    _drain(_TimedRunner()._enter_lingquan(
        Runtime(),
        transition_timeout=20.0,
        deadline=datetime(2026, 7, 20, 20, 41, 0),
    ))

    assert calls == [(388, "进入问答", 389, {"timeout": 144.0})]


def test_lingquan_question_loop_recovers_scene_388_before_answering(monkeypatch):
    clock = _Clock()
    clock.value = datetime(2026, 7, 20, 20, 33, 0)
    recovered = []

    class Runtime(_TimedRuntime):
        def __init__(self, value):
            super().__init__(value)
            self.scenes = iter([388, 389])

        def current_scene(self, preferred_scene_ids, update=False):
            assert update is True
            return next(self.scenes), 100.0, "frame"

    class Runner(_TimedRunner):
        def _enter_lingquan(self, *_args, **_kwargs):
            recovered.append("enter")
            if False:
                yield None

    runtime = Runtime(clock)
    runtime.questions = ["", "第一题"]
    monkeypatch.setattr(lingquan, "_now", lambda: clock.value)
    monkeypatch.setattr(
        "backend.core.fanxiu.instrumentation."
        "fanxiu_instrumentation_service.lingquan_question_snapshot",
        lambda **_kwargs: {"available": False},
    )

    answers = _drain(Runner()._run_lingquan_question_loop(
        runtime,
        cutoff=clock.value + timedelta(seconds=2),
        transition_timeout=20,
        score_threshold=90,
        poll_seconds=1,
    ))

    assert recovered == ["enter"]
    assert answers == 1


def test_lingquan_waits_through_unknown_transition_until_stable_scene(monkeypatch):
    calls = []
    now = datetime(2026, 7, 20, 20, 36, 0)
    monkeypatch.setattr(lingquan, "_now", lambda: now)

    class Runtime:
        def current_scene(self, preferred_scene_ids, update=False):
            assert preferred_scene_ids == [389, 388, 387, 386, 66, 34]
            assert update is True
            return None, 0.0, "transition-frame"

        def wait_view(self, *targets, **kwargs):
            calls.append(("wait_view", targets, kwargs))
            if False:
                yield None
            return 388

        def wait_click_then_view(self, source, shape, target, **kwargs):
            calls.append(("wait_click_then_view", source, shape, target, kwargs))
            if False:
                yield None
            return target

    _drain(_TimedRunner()._enter_lingquan(
        Runtime(),
        transition_timeout=20.0,
        deadline=now + timedelta(minutes=5),
    ))

    assert calls == [
        ("wait_view", (389, 388, 387, 386), {
            "timeout": 300.0,
            "label": "日常_灵泉：等待过渡结束并进入稳定业务场景",
        }),
        ("wait_click_then_view", 388, "进入问答", 389, {"timeout": 20.0}),
    ]


def test_lingquan_click_386_accepts_late_or_skipped_stable_landing(monkeypatch):
    calls = []
    now = datetime(2026, 7, 20, 20, 30, 0)
    monkeypatch.setattr(lingquan, "_now", lambda: now)

    class Runtime:
        def current_scene(self, *_args, **_kwargs):
            return 386, 100.0, "frame"

        def wait_click_then_view(self, source, shape, target, **kwargs):
            calls.append(("wait_click_then_view", source, shape, target, kwargs))
            if False:
                yield None
            return 389

    _drain(_TimedRunner()._enter_lingquan(
        Runtime(),
        transition_timeout=20.0,
        deadline=now + timedelta(minutes=11),
    ))

    assert calls == [
        ("wait_click_then_view", 386, "前往", [387, 388, 389], {
            "timeout": 660.0,
            "label": "日常_灵泉：等待过渡结束并进入 #387/#388/#389",
        }),
    ]


def test_answer_uses_scene_390_as_shape_host_without_waiting_for_keyboard_scene(monkeypatch):
    calls = []

    class Matched:
        question = "韩立在灵界创建的势力叫什么？"
        answer = "青元宫"

    class Session:
        def __init__(self, _engine):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    class Runtime:
        def wait_click(self, scene_id, shape):
            calls.append(("wait_click", scene_id, shape))
            if False:
                yield None

        def wait_action_settle(self, seconds):
            calls.append(("settle", seconds))
            if False:
                yield None

        def click_shape_center_fast(self, scene_id, shape):
            calls.append(("click_shape_center_fast", scene_id, shape))

    class Runner(LingquanTaskMixin):
        def _log(self, *_args, **_kwargs):
            return None

    monkeypatch.setattr("sqlmodel.Session", Session)
    monkeypatch.setattr(
        "backend.core.fanxiu.quiz.store.match_lingquan_question_cached",
        lambda *_args: (Matched(), 100.0),
    )
    monkeypatch.setattr(lingquan, "text_mumu_adb", lambda text: calls.append(("text", text)))

    result = _drain(Runner()._answer_lingquan_question(
        Runtime(),
        frame_data_url="frame",
        transition_timeout=20,
        score_threshold=90,
        question_text="韩立在灵界创建的势力叫什么？",
    ))

    assert result["answered"] is True
    assert calls == [
        ("wait_click", 389, "输入"),
        ("settle", 0.5),
        ("text", "青元宫"),
        ("settle", 0.5),
        ("click_shape_center_fast", 390, "发送"),
        ("settle", 2.0),
        ("click_shape_center_fast", 390, "发送"),
    ]
