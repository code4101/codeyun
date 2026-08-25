from __future__ import annotations

from datetime import datetime

from backend.core.fanxiu.data_annotation.jobs import (
    get_fanxiu_data_annotation_task_cell_definition,
)
from backend.core.fanxiu.data_annotation.default_jobs import (
    register_fanxiu_data_annotation_default_runtime_jobs,
)
from backend.core.fanxiu.data_annotation.scheduler_defaults import (
    default_data_annotation_scheduler_tasks,
)
from backend.core.fanxiu.data_annotation.effective_time import job_effective_time
from backend.core.fanxiu.data_annotation.tasks import kunlun_secret_jobs as kunlun
from backend.core.fanxiu.data_annotation.tasks import penglai_xianzang_jobs as penglai


def test_next_times_alternate_without_iso_week_boundary_assumptions() -> None:
    before_kunlun = datetime(2026, 8, 13, 0, 1)
    assert kunlun.next_kunlun_config_time(before_kunlun) == "2026-08-13 00:05:00"
    assert kunlun.next_kunlun_lottery_time(before_kunlun) == "2026-08-13 21:10:00"
    assert penglai.next_xianzang_config_time(before_kunlun) == "2026-08-20 00:05:00"
    assert penglai.next_xianzang_lottery_time(before_kunlun) == "2026-08-20 21:10:00"

    after_kunlun_config = datetime(2026, 8, 13, 19, 5)
    assert kunlun.next_kunlun_config_time(after_kunlun_config) == "2026-08-27 00:05:00"
    assert kunlun.next_kunlun_lottery_time(after_kunlun_config) == "2026-08-13 21:10:00"


def test_default_scheduler_contains_all_four_alternating_jobs_once() -> None:
    tasks = default_data_annotation_scheduler_tasks(datetime(2026, 8, 13, 0, 1))
    by_id = {item["id"]: item for item in tasks}

    assert by_id[penglai.XIANZANG_CONFIG_TASK_ID]["next_time"] == "2026-08-20 00:05:00"
    assert by_id[penglai.XIANZANG_LOTTERY_TASK_ID]["next_time"] == "2026-08-20 21:10:00"
    assert by_id[kunlun.KUNLUN_CONFIG_TASK_ID]["next_time"] == "2026-08-13 00:05:00"
    assert by_id[kunlun.KUNLUN_LOTTERY_TASK_ID]["next_time"] == "2026-08-13 21:10:00"
    assert {
        by_id[task_id]["trigger_description"]
        for task_id in (
            penglai.XIANZANG_CONFIG_TASK_ID,
            penglai.XIANZANG_LOTTERY_TASK_ID,
            kunlun.KUNLUN_CONFIG_TASK_ID,
            kunlun.KUNLUN_LOTTERY_TASK_ID,
        )
    } == {"动态"}
    assert len([item for item in tasks if item["id"] in {
        penglai.XIANZANG_CONFIG_TASK_ID,
        penglai.XIANZANG_LOTTERY_TASK_ID,
        kunlun.KUNLUN_CONFIG_TASK_ID,
        kunlun.KUNLUN_LOTTERY_TASK_ID,
    }]) == 4


def test_kunlun_jobs_are_registered_scheduler_jobs() -> None:
    register_fanxiu_data_annotation_default_runtime_jobs()
    for task_type in (
        kunlun.KUNLUN_CONFIG_TASK_TYPE,
        kunlun.KUNLUN_LOTTERY_TASK_TYPE,
    ):
        definition = get_fanxiu_data_annotation_task_cell_definition(task_type)
        assert definition is not None
        assert definition.scheduler_supported is True


class _Runner:
    def __init__(self) -> None:
        self.next_times: list[tuple[str, str]] = []
        self.logs: list[tuple[str, str]] = []

    def _persist_scheduler_task_next_time(self, task_id: str, next_time: str) -> None:
        self.next_times.append((task_id, next_time))

    def _log(self, kind: str, message: str) -> None:
        self.logs.append((kind, message))


def test_kunlun_config_job_runs_workflow_then_writes_its_own_next_time(monkeypatch) -> None:
    runner = _Runner()
    runtime = object()
    monkeypatch.setattr(kunlun, "_runtime", lambda *_args: runtime)
    monkeypatch.setattr(kunlun, "enter_kunlun", lambda actual: actual is runtime)
    monkeypatch.setattr(
        kunlun,
        "_run_kunlun_config_workflow",
        lambda actual: {"configured": actual is runtime},
    )
    monkeypatch.setattr(kunlun, "leave_kunlun", lambda actual: (34, 100.0))
    monkeypatch.setattr(
        kunlun,
        "next_kunlun_config_time",
        lambda: "2026-08-27 00:05:00",
    )

    result = kunlun.execute_kunlun_config_job(runner, {}, {}, object())

    assert result["result"] == "success"
    assert result["configured"] is True
    assert runner.next_times == [(kunlun.KUNLUN_CONFIG_TASK_ID, "2026-08-27 00:05:00")]
    assert runner.logs[0][0] == "success"


def test_kunlun_lottery_job_runs_workflow_then_advances_after_world_return(monkeypatch) -> None:
    runner = _Runner()
    runtime = object()
    monkeypatch.setattr(kunlun, "_runtime", lambda *_args: runtime)
    monkeypatch.setattr(kunlun, "enter_kunlun", lambda actual: actual is runtime)
    monkeypatch.setattr(
        kunlun,
        "_run_kunlun_lottery_workflow",
        lambda actual: {"lottery_outcome": {"done": actual is runtime}},
    )
    monkeypatch.setattr(kunlun, "leave_kunlun", lambda actual: (34, 100.0))
    monkeypatch.setattr(
        kunlun,
        "next_kunlun_lottery_time",
        lambda: "2026-08-27 21:10:00",
    )

    result = kunlun.execute_kunlun_lottery_job(runner, {}, {}, object())

    assert result["result"] == "success"
    assert result["lottery_outcome"] == {"done": True}
    assert result["job_notes"] == [
        "每抽可得 5 个昆仑古玉",
        "后续需求：实现兑换宝阁相关功能",
    ]
    assert runner.next_times == [
        (kunlun.KUNLUN_LOTTERY_TASK_ID, "2026-08-27 21:10:00")
    ]


def test_kunlun_lottery_early_run_uses_job_effective_now_for_next_period(monkeypatch) -> None:
    runner = _Runner()
    runtime = object()
    monkeypatch.setattr(kunlun, "_runtime", lambda *_args: runtime)
    monkeypatch.setattr(kunlun, "enter_kunlun", lambda actual: actual is runtime)
    monkeypatch.setattr(
        kunlun,
        "_run_kunlun_lottery_workflow",
        lambda _actual: {"lottery_outcome": {"done": True}},
    )
    monkeypatch.setattr(kunlun, "leave_kunlun", lambda _actual: (34, 100.0))

    with job_effective_time({"effective_now": "2026-08-13 21:15:00"}):
        result = kunlun.execute_kunlun_lottery_job(runner, {}, {}, object())

    assert "next_time" not in result
    assert runner.next_times[-1] == (kunlun.KUNLUN_LOTTERY_TASK_ID, "2026-08-27 21:10:00")
    assert runner.next_times == [
        (kunlun.KUNLUN_LOTTERY_TASK_ID, "2026-08-27 21:10:00")
    ]


def test_kunlun_evening_workflow_claims_tasks_then_continues_shared_state(monkeypatch) -> None:
    runtime = object()
    monkeypatch.setattr(
        kunlun,
        "complete_kunlun_tasks",
        lambda actual: type(
            "TaskResult",
            (),
            {"clicked_count": 2, "stop_reason": "all_claimed"},
        )(),
    )
    monkeypatch.setattr(
        kunlun,
        "complete_kunlun_lottery",
        lambda actual, **kwargs: {
            "result": "success",
            "stop_reason": "stop_first_grand_prize",
            "runtime_matches": actual is runtime,
            "allow_single_draws": kwargs["allow_single_draws"],
        },
    )
    result = kunlun._run_kunlun_lottery_workflow(runtime)

    assert result["task_clicked_count"] == 2
    assert result["lottery_outcome"]["stop_reason"] == "stop_first_grand_prize"
    assert result["lottery_outcome"]["runtime_matches"] is True
    assert result["lottery_outcome"]["allow_single_draws"] is True


def test_kunlun_lottery_job_does_not_advance_when_result_asset_is_missing(monkeypatch) -> None:
    runner = _Runner()
    monkeypatch.setattr(kunlun, "_runtime", lambda *_args: object())
    monkeypatch.setattr(kunlun, "enter_kunlun", lambda _runtime: None)
    monkeypatch.setattr(
        kunlun,
        "_run_kunlun_lottery_workflow",
        lambda _runtime: (_ for _ in ()).throw(
            RuntimeError("昆仑秘藏缺少独立鉴宝结果页场景资产")
        ),
    )

    try:
        kunlun.execute_kunlun_lottery_job(runner, {}, {}, object())
    except RuntimeError as exc:
        assert "独立鉴宝结果页" in str(exc)
    else:
        raise AssertionError("缺失结果页必须安全失败")
    assert runner.next_times == []
