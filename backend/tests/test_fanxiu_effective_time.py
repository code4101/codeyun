from __future__ import annotations

import threading
from datetime import datetime

import pytest

from backend.core.fanxiu.data_annotation import effective_time
from backend.core.fanxiu.data_annotation.effective_time import (
    job_effective_time,
    job_now,
    job_today,
)


def test_job_effective_time_defaults_to_real_clock_and_resets(monkeypatch) -> None:
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            value = cls(2026, 8, 13, 20, 55, 0)
            return value if tz is None else value.astimezone(tz)

    monkeypatch.setattr(effective_time, "datetime", FixedDateTime)

    with job_effective_time({}):
        assert job_now() == datetime(2026, 8, 13, 20, 55, 0)
        assert job_today().isoformat() == "2026-08-13"

    with job_effective_time({"effective_now": "2026-08-13 21:15:00"}):
        assert job_now() == datetime(2026, 8, 13, 21, 15, 0)

    assert job_now() == datetime(2026, 8, 13, 20, 55, 0)


def test_invalid_effective_now_fails_before_job_business_runs() -> None:
    with pytest.raises(ValueError, match="effective_now"):
        with job_effective_time({"effective_now": "tonight"}):
            raise AssertionError("unreachable")


def test_behavior_tree_runtime_clock_uses_job_effective_now() -> None:
    from backend.core.fanxiu.data_annotation import behavior_tree_runtime

    with job_effective_time({"effective_now": "2026-08-13 21:31:00"}):
        assert behavior_tree_runtime._now() == datetime(2026, 8, 13, 21, 31, 0)


def test_registered_task_cell_applies_effective_now_to_handler() -> None:
    from backend.core.fanxiu.behavior_tree.jupyter_kernel import FanxiuJupyterBinding
    from backend.core.fanxiu.data_annotation.jobs import (
        register_fanxiu_data_annotation_task_cell,
    )

    observed_times: list[str] = []

    @register_fanxiu_data_annotation_task_cell(
        "test_effective_now_context",
        "测试业务时钟上下文",
        scheduler_supported=True,
    )
    def handler(_runner, _ctx, _payload, _stop_event):
        observed_times.append(job_now().isoformat(sep=" "))
        return {"result": "success", "message": "clock observed"}

    class Runner:
        @staticmethod
        def _task_timeout_seconds(_payload):
            return 60.0

        @staticmethod
        def _runtime_guard_override_from_payload(_payload):
            return None

        @staticmethod
        def _normalize_runtime_task_result(value):
            return str(value.get("result") or "success"), ""

    binding = object.__new__(FanxiuJupyterBinding)
    binding.runner = Runner()
    binding.runtime_ctx = {}
    binding.stop_event = threading.Event()

    def drain(value, **_kwargs):
        while True:
            try:
                next(value)
            except StopIteration as stop:
                return stop.value

    binding.run = drain

    result = binding.run_task(
        "test_effective_now_context",
        {"effective_now": "2026-08-13 21:15:00"},
    )

    assert observed_times[-1] == "2026-08-13 21:15:00"
    assert result["result"] == "success"

    before = datetime.now()
    binding.run_task("test_effective_now_context", {})
    after = datetime.now()
    assert before <= datetime.fromisoformat(observed_times[-1]) <= after


def test_effective_clock_enters_window_before_cell_can_report_success() -> None:
    from backend.core.fanxiu.behavior_tree.jupyter_kernel import FanxiuJupyterBinding
    from backend.core.fanxiu.data_annotation.jobs import (
        register_fanxiu_data_annotation_task_cell,
    )

    business_ran: list[datetime] = []

    def admission(_runner, _payload):
        if job_now().hour < 15:
            return {"result": "success", "message": "outside business window"}
        return None

    @register_fanxiu_data_annotation_task_cell(
        "test_planned_window_contract",
        "测试 planned 窗口契约",
        scheduler_supported=True,
        admission=admission,
    )
    def handler(_runner, _ctx, _payload, _stop_event):
        business_ran.append(job_now())
        return {"result": "success", "message": "business window entered"}

    class Runner:
        @staticmethod
        def _task_timeout_seconds(_payload):
            return 60.0

        @staticmethod
        def _runtime_guard_override_from_payload(_payload):
            return None

        @staticmethod
        def _normalize_runtime_task_result(value):
            return str(value.get("result") or "success"), str(value.get("message") or "")

    binding = object.__new__(FanxiuJupyterBinding)
    binding.runner = Runner()
    binding.runtime_ctx = {}
    binding.stop_event = threading.Event()

    def drain(value, **_kwargs):
        while True:
            try:
                next(value)
            except StopIteration as stop:
                return stop.value

    binding.run = drain

    terminal = binding.run_task(
        "test_planned_window_contract",
        {"effective_now": "2026-08-13 15:31:00"},
    )

    assert terminal == {"result": "success", "message": "business window entered"}
    assert business_ran == [datetime(2026, 8, 13, 15, 31)]


def test_kernel_task_compiles_effective_now_into_the_single_cell_protocol() -> None:
    from backend.core.fanxiu.behavior_tree.kernel import FanxiuKernel

    cell = FanxiuKernel().task(
        "kunlun_secret_lottery",
        effective_now="2026-08-13 21:15:00",
    )

    assert cell.code.startswith("# fanxiu:managed-task-cell\nrun_task_cell(")
    assert "'effective_now': '2026-08-13 21:15:00'" in cell.code


def test_api_requests_expose_effective_now_without_requiring_payload_internals() -> None:
    from backend.core.fanxiu.data_annotation.models import (
        FanxiuBehaviorTreeRuntimeTaskCellRequest,
        FanxiuDataAnnotationSchedulerRunNowRequest,
    )

    task_cell = FanxiuBehaviorTreeRuntimeTaskCellRequest(
        entry_id="mumu-0",
        task_type="kunlun_secret_lottery",
        effective_now="2026-08-13 21:15:00",
    )
    run_now = FanxiuDataAnnotationSchedulerRunNowRequest(
        entry_id="mumu-0",
        task_id="kunlun-secret-lottery",
        effective_now="2026-08-13 21:15:00",
    )

    assert task_cell.effective_now == datetime(2026, 8, 13, 21, 15)
    assert run_now.effective_now == datetime(2026, 8, 13, 21, 15)


def test_scheduler_run_now_api_forwards_planned_mode_to_the_common_entry(monkeypatch) -> None:
    from backend.api import fanxiu as fanxiu_api
    from backend.core.fanxiu.data_annotation.models import (
        FanxiuDataAnnotationSchedulerRunNowRequest,
    )

    captured: dict = {}
    monkeypatch.setattr(fanxiu_api, "_sync_behavior_tree_runtime_runner_to_core", lambda: None)

    def run_now(**kwargs):
        captured.update(kwargs)
        return {"status": "success", "phase": "done", "message": "accepted"}

    monkeypatch.setattr(fanxiu_api._behavior_tree_control, "run_now_scheduler_task", run_now)

    result = fanxiu_api._run_now_fanxiu_data_annotation_scheduler_task(
        object(),
        "entry-a",
        FanxiuDataAnnotationSchedulerRunNowRequest(
            entry_id="entry-a",
            task_id="job-a",
            business_time_mode="planned",
        ),
    )

    assert result.status == "success"
    assert captured["business_time_mode"] == "planned"
    assert captured["payload_override"] == {}
