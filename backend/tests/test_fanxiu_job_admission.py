from datetime import datetime

import pytest

from backend.core.fanxiu.data_annotation import behavior_tree_runtime
from backend.core.fanxiu.data_annotation.tasks.daily_foundation import DailyFoundationTaskMixin


class _Runner(DailyFoundationTaskMixin):
    def __init__(self):
        self.next_times = []

    def _persist_scheduler_task_next_time(self, task_id, next_time):
        self.next_times.append((task_id, next_time))

    def _persist_admission_decision(self, payload, decision):
        if decision is None:
            return None
        task_id = str((payload or {}).get("__scheduler_task_id") or "").strip()
        if not task_id:
            raise RuntimeError("作业准入写入 next_time 时缺少 __scheduler_task_id")
        result = dict(decision)
        next_time = result.pop("next_time")
        self._persist_scheduler_task_next_time(task_id, next_time)
        return result


@pytest.mark.parametrize(
    ("method_name", "task_id", "now", "expected_next_time"),
    [
        ("daily_dongtian_admission", "legacy-daily-dongtian", datetime(2026, 7, 29, 13, 59, 59), "2026-07-29 14:00:00"),
        ("daily_dongtian_admission", "legacy-daily-dongtian", datetime(2026, 7, 29, 22, 0, 0), "2026-07-30 14:00:00"),
        ("daily_lingmai_clear_admission", "legacy-daily-lingmai-clear", datetime(2026, 7, 29, 22, 0, 0), "2026-07-30 21:30:00"),
        ("daily_dongtian_clear_admission", "legacy-daily-dongtian-clear", datetime(2026, 7, 29, 21, 29, 59), "2026-07-29 21:30:00"),
    ],
)
def test_daily_job_admission_owns_window_and_next_time(
    monkeypatch,
    method_name,
    task_id,
    now,
    expected_next_time,
):
    monkeypatch.setattr(behavior_tree_runtime, "_now", lambda: now)
    runner = _Runner()

    result = getattr(runner, method_name)({"__scheduler_task_id": task_id})

    assert result["result"] == "success"
    assert "next_time" not in result
    assert runner.next_times[0][1] == expected_next_time
    assert result["current_scene"] is None
    assert "未执行游戏操作" in result["message"]


def test_daily_job_admission_allows_business_only_inside_window(monkeypatch):
    monkeypatch.setattr(
        behavior_tree_runtime,
        "_now",
        lambda: datetime(2026, 7, 29, 21, 45, 0),
    )
    runner = _Runner()

    assert runner.daily_lingmai_clear_admission(
        {"__scheduler_task_id": "legacy-daily-lingmai-clear"}
    ) is None
    assert runner.daily_dongtian_clear_admission(
        {"__scheduler_task_id": "legacy-daily-dongtian-clear"}
    ) is None


def test_daily_job_admission_missing_scheduler_task_id_fails_closed(monkeypatch):
    monkeypatch.setattr(
        behavior_tree_runtime,
        "_now",
        lambda: datetime(2026, 7, 29, 22, 0, 0),
    )
    runner = _Runner()

    with pytest.raises(RuntimeError, match="缺少 __scheduler_task_id"):
        runner.daily_lingmai_clear_admission({})

    assert runner.next_times == []


def test_dongtian_clear_debug_override_is_explicit(monkeypatch):
    monkeypatch.setattr(
        behavior_tree_runtime,
        "_now",
        lambda: datetime(2026, 7, 29, 12, 0, 0),
    )

    assert DailyFoundationTaskMixin().daily_dongtian_clear_admission(
        {"ignore_schedule_window": True}
    ) is None
