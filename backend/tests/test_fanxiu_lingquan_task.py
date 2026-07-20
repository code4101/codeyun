from datetime import datetime, timedelta

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

    def cur_frame(self, update=False):
        assert update is True
        return "frame"

    def ocr_numbers_in_shapes(self, *_args, **_kwargs):
        self.ocr_times.append(self.clock.value)
        value = self.countdowns.pop(0)
        return [value], str(value)

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


def test_outside_window_does_not_touch_runtime(monkeypatch):
    monkeypatch.setattr(lingquan, "_now", lambda: datetime(2026, 7, 20, 21, 0, 0))
    result = _drain(_OutsideWindowRunner()._execute_daily_lingquan_task({}, None, {}))
    assert result["result"] == "success"
    assert result["current_scene"] is None
    assert "未执行游戏操作" in result["message"]


def test_lingquan_is_registered_and_scheduled_at_2030():
    register_fanxiu_data_annotation_default_runtime_jobs()
    definition = get_fanxiu_data_annotation_task_cell_definition("daily_lingquan")
    assert definition is not None
    assert definition.scheduler_supported is True
    assert definition.stable_start_scene_id is None

    task = next(item for item in scheduler_defaults.default_data_annotation_scheduler_tasks() if item["id"] == "legacy-daily-lingquan")
    assert task["task_type"] == "daily_lingquan"
    assert task["enabled"] is True
    assert task["schedule_times"] == ["20:30"]
    assert task["window"] == ["20:30", "20:43"]


def test_countdown_cooldown_starts_when_countdown_is_observed(monkeypatch):
    clock = _Clock()
    runtime = _TimedRuntime(clock)
    monkeypatch.setattr(lingquan, "_now", lambda: clock.value)
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
        datetime(2026, 7, 20, 20, 30, 32),
    ]
