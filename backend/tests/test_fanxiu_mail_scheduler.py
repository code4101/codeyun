from datetime import datetime
import threading

from backend.core.fanxiu.data_annotation.default_jobs import (
    register_fanxiu_data_annotation_default_runtime_jobs,
)
from backend.core.fanxiu.data_annotation.jobs import (
    get_fanxiu_data_annotation_task_cell_definition,
)
from backend.core.fanxiu.data_annotation.runner import create_behavior_tree_runtime_runner


def test_mail_success_advances_its_daily_trigger(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    next_times = []

    monkeypatch.setitem(
        runner._finish_mail_selective_claim_schedule.__func__.__globals__,
        "job_now",
        lambda: datetime(2026, 7, 24, 12, 0, 0),
    )
    monkeypatch.setattr(
        runner,
        "_persist_scheduler_task_next_time",
        lambda task_id, next_time: next_times.append((task_id, next_time)),
    )

    message = runner._finish_mail_selective_claim_schedule(
        {"__scheduler_task_id": "mail-selective-claim"},
        "邮件_选择性领取：完成",
    )

    assert message == "邮件_选择性领取：完成，下次 2026-07-25 00:00:00"
    assert next_times == [("mail-selective-claim", "2026-07-25 00:00:00")]


def test_plain_mail_debug_cell_does_not_change_scheduler(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    monkeypatch.setattr(
        runner,
        "_persist_scheduler_task_next_time",
        lambda *_args: (_ for _ in ()).throw(AssertionError("must stay manual")),
    )

    assert runner._finish_mail_selective_claim_schedule({}, "完成") == "完成"


def test_mail_job_wrapper_preserves_business_summary_after_return_to_world():
    class Runtime:
        def goto_view(self, scene_id):
            assert scene_id == 34
            if False:
                yield None
            return 34

    class Runner:
        def _fanxiu_runtime(self, *_args, **_kwargs):
            return Runtime()

        def _execute_mail_selective_claim_task(self, *_args, **_kwargs):
            self._mail_selective_claim_terminal_message = (
                "邮件_选择性领取：完整闭环，领取 2 封，删除前 8 封，"
                "删除 8 封，剩余垃圾 0 封，保留 69 封"
            )
            if False:
                yield None
            return "success"

        def _finish_mail_selective_claim_schedule(self, _payload, message):
            return f"{message}，下次 2026-07-25 00:00:00"

    register_fanxiu_data_annotation_default_runtime_jobs()
    definition = get_fanxiu_data_annotation_task_cell_definition("mail_selective_claim")
    execution = definition.handler(Runner(), {}, {}, threading.Event())
    while True:
        try:
            next(execution)
        except StopIteration as stop:
            result = stop.value
            break

    assert result == {
        "result": "success",
        "message": (
            "邮件_选择性领取：完整闭环，领取 2 封，删除前 8 封，"
            "删除 8 封，剩余垃圾 0 封，保留 69 封，下次 2026-07-25 00:00:00"
        ),
    }
    for field in ("领取 2 封", "删除前 8 封", "删除 8 封", "剩余垃圾 0 封", "保留 69 封"):
        assert field in result["message"]


def test_mail_job_failure_does_not_advance_next_day():
    class Runtime:
        def goto_view(self, _scene_id):
            if False:
                yield None
            return 34

    class Runner:
        _mail_selective_claim_terminal_message = "上一轮旧摘要"

        def _fanxiu_runtime(self, *_args, **_kwargs):
            return Runtime()

        def _execute_mail_selective_claim_task(self, *_args, **_kwargs):
            if False:
                yield None
            raise RuntimeError("部分领取后验证失败")

        def _finish_mail_selective_claim_schedule(self, *_args, **_kwargs):
            raise AssertionError("失败不得推进次日")

    register_fanxiu_data_annotation_default_runtime_jobs()
    definition = get_fanxiu_data_annotation_task_cell_definition("mail_selective_claim")
    execution = definition.handler(
        Runner(),
        {},
        {"__scheduler_task_id": "mail-selective-claim"},
        threading.Event(),
    )
    try:
        while True:
            next(execution)
    except RuntimeError as exc:
        assert "部分领取后验证失败" in str(exc)


def test_mail_job_final_world_failure_does_not_advance_next_day():
    class Runtime:
        calls = 0

        def goto_view(self, _scene_id):
            self.calls += 1
            if False:
                yield None
            if self.calls == 2:
                raise RuntimeError("最终返回世界失败")
            return 34

    class Runner:
        def __init__(self):
            self.runtime = Runtime()
            self.schedule_calls = []

        def _fanxiu_runtime(self, *_args, **_kwargs):
            return self.runtime

        def _execute_mail_selective_claim_task(self, *_args, **_kwargs):
            self._mail_selective_claim_terminal_message = (
                "邮件_选择性领取：完整闭环，领取 1 封，删除前 1 封，"
                "删除 1 封，剩余垃圾 0 封，保留 3 封"
            )
            if False:
                yield None
            return "success"

        def _finish_mail_selective_claim_schedule(self, *_args, **_kwargs):
            self.schedule_calls.append(True)
            return "不应调用"

    runner = Runner()
    register_fanxiu_data_annotation_default_runtime_jobs()
    definition = get_fanxiu_data_annotation_task_cell_definition("mail_selective_claim")
    execution = definition.handler(
        runner,
        {},
        {"__scheduler_task_id": "mail-selective-claim"},
        threading.Event(),
    )
    try:
        while True:
            next(execution)
    except RuntimeError as exc:
        assert "最终返回世界失败" in str(exc)
    assert runner.schedule_calls == []
