import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(r"C:\home\chenkunze\slns\xlproject\src")))

from xlsln.kq5034 import kqmain
from xlsln.kq5034.kqmain import 考勤行为树


def test_data_updates_precede_daily_morning_course_task():
    root = 考勤行为树().build_tree()
    selector = root.children[0]

    action_names = []
    for child in selector.children:
        inner = getattr(getattr(child, "child", None), "child", None)
        fn = getattr(inner, "func", None) or getattr(inner, "fn", None)
        action_names.append(getattr(fn, "__name__", type(child).__name__))

    assert action_names.index("更新店铺2用户数据") < action_names.index("每日早晨课程任务")
    assert action_names.index("更新店铺1用户数据") < action_names.index("每日早晨课程任务")
    assert "更新课程视频数据" not in action_names
    assert "每日晚上课程任务" not in action_names


def test_fanbei_runs_only_in_daily_morning_course_task(monkeypatch):
    calls = []

    def fake_execute_course_list(module_names, *, entry="main", task_name, weekday=None, continue_on_error=False, job_run_id=None):
        calls.append((tuple(module_names), entry, task_name))
        if False:
            yield None

    monkeypatch.setattr(kqmain, "_执行课程清单", fake_execute_course_list)
    monkeypatch.setattr(kqmain, "尝试关闭重复页面", lambda **kwargs: None)
    monkeypatch.setattr(kqmain, "_yield_log", lambda message: iter(()))
    monkeypatch.setattr(kqmain, "_通知课程任务完成", lambda task_name: None)

    list(kqmain.每日早晨课程任务())

    assert (tuple(kqmain.梵呗类型), "main", "每日早晨课程任务") in calls


def test_zen12_stage2_runs_in_weekly_sunday_course_tasks(monkeypatch):
    calls = []

    from xlsln.kq5034.engine import job_runs

    monkeypatch.setattr(job_runs, "begin_job_run", lambda **kwargs: "run-test")
    monkeypatch.setattr(job_runs, "finish_job_run", lambda *args, **kwargs: {})

    def fake_execute_course_list(module_names, *, entry="main", task_name, weekday=None, continue_on_error=False, job_run_id=None):
        calls.append(
            {
                "module_names": tuple(module_names),
                "entry": entry,
                "task_name": task_name,
                "weekday": weekday,
                "continue_on_error": continue_on_error,
            }
        )
        if False:
            yield None

    monkeypatch.setattr(kqmain, "_执行课程清单", fake_execute_course_list)
    monkeypatch.setattr(kqmain, "尝试关闭重复页面", lambda **kwargs: None)
    monkeypatch.setattr(kqmain, "_yield_log", lambda message: iter(()))
    monkeypatch.setattr(kqmain, "_通知课程任务完成", lambda task_name: None)

    list(kqmain.每日凌晨课程任务())
    list(kqmain.每日早晨课程任务())

    course_name = "d260712禅宗12期二阶"
    assert course_name in kqmain.禅宗类型
    assert any(
        course_name in call["module_names"] and call["entry"] == "main_a" and call["weekday"] == 7
        for call in calls
    )
    assert any(
        course_name in call["module_names"] and call["entry"] == "main_b" and call["weekday"] == 7
        for call in calls
    )


def test_midnight_course_groups_do_not_block_each_other(monkeypatch):
    calls = []
    cleanup_reasons = []
    finished_runs = []

    from xlsln.kq5034.engine import job_runs

    monkeypatch.setattr(job_runs, "begin_job_run", lambda **kwargs: "run-test")
    monkeypatch.setattr(
        job_runs,
        "finish_job_run",
        lambda run_id, **kwargs: finished_runs.append((run_id, kwargs)) or {},
    )

    def fake_execute_course_list(module_names, *, entry="main", task_name, weekday=None, continue_on_error=False, job_run_id=None):
        calls.append(
            {
                "module_names": tuple(module_names),
                "entry": entry,
                "task_name": task_name,
                "weekday": weekday,
                "continue_on_error": continue_on_error,
                "job_run_id": job_run_id,
            }
        )
        if module_names is kqmain.念住闯关类型:
            raise RuntimeError("念住闯关故障")
        if False:
            yield None

    monkeypatch.setattr(kqmain, "_执行课程清单", fake_execute_course_list)
    monkeypatch.setattr(kqmain, "尝试关闭重复页面", lambda **kwargs: cleanup_reasons.append(kwargs["reason"]))
    monkeypatch.setattr(kqmain, "_yield_log", lambda message: iter(()))
    monkeypatch.setattr(kqmain, "_通知课程任务完成", lambda task_name: None)

    with pytest.raises(RuntimeError, match="念住闯关故障"):
        list(kqmain.每日凌晨课程任务())

    assert calls == [
        {
            "module_names": tuple(kqmain.念住闯关类型),
            "entry": "main_a",
            "task_name": "每日凌晨课程任务",
            "weekday": None,
            "continue_on_error": True,
            "job_run_id": "run-test",
        },
        {
            "module_names": tuple(kqmain.禅宗类型),
            "entry": "main_a",
            "task_name": "每日凌晨课程任务",
            "weekday": 7,
            "continue_on_error": True,
            "job_run_id": "run-test",
        },
    ]
    assert cleanup_reasons == ["每日凌晨课程任务收尾"]
    assert finished_runs[0][0] == "run-test"
    assert finished_runs[0][1]["status"] == "failed"
