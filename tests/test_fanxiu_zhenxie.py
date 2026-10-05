from __future__ import annotations

from datetime import datetime as RealDateTime
import pytest

from backend.core.fanxiu.data_annotation.kernel_scheduler_defaults import default_kernel_scheduler_tasks
from backend.core.fanxiu.data_annotation.tasks import zhenxie as zhenxie_module
from backend.core.fanxiu.data_annotation.tasks.zhenxie import ZhenxieTaskMixin


class _AdmissionProbe(ZhenxieTaskMixin):
    """只捕获纯时间准入决策；不模拟 Runtime、画面或动作。"""
    def _persist_admission_decision(self, payload, decision):
        return decision


def test_daily_zhenxie_admission_allows_inside_window(monkeypatch):
    monkeypatch.setattr(zhenxie_module, 'job_now', lambda: RealDateTime(2026, 7, 12, 21, 1))

    assert ZhenxieTaskMixin().daily_zhenxie_admission() is None


def test_daily_zhenxie_admission_skips_all_actions_outside_window(monkeypatch):
    monkeypatch.setattr(zhenxie_module, 'job_now', lambda: RealDateTime(2026, 7, 12, 21, 5, 1))

    with_result = _AdmissionProbe().daily_zhenxie_admission()

    assert with_result["result"] == "success"
    assert with_result["next_time"] == "2026-07-13 21:00:00"


def test_daily_zhenxie_scheduler_definition_is_runtime_job():
    task = next(item for item in default_kernel_scheduler_tasks() if item["id"] == "daily-zhenxie")

    assert task["task_type"] == "daily_zhenxie"
    assert task["source"] == "kernel_scheduler"
    assert task["trigger_description"] == "每日"
    assert task["next_time"]
    assert task["dispatch_level"] == 1
    assert "window" not in task


@pytest.mark.parametrize('text,expected', [
    ('玄枪邪灵已被击败', True), ('玄枪邪灵 已 被 击 败', True),
    ('玄枪邪灵尚未被击败', False), ('已被击败', False),
    ('前往', False), ('宗门镇邪', False),
])
def test_boss_terminal_status_line(text, expected):
    assert zhenxie_module.zhenxie_boss_is_defeated(text) is expected


def test_explicit_after_window_reconciliation_is_admitted(monkeypatch):
    monkeypatch.setattr(zhenxie_module, 'job_now', lambda: RealDateTime(2026, 7, 12, 21, 8))
    assert _AdmissionProbe().daily_zhenxie_admission({'reconcile_after_window': True}) is None
    with pytest.raises(ValueError):
        _AdmissionProbe().daily_zhenxie_admission({'reconcile_after_window': 1})


def test_reconciliation_does_not_admit_before_window(monkeypatch):
    monkeypatch.setattr(zhenxie_module, 'job_now', lambda: RealDateTime(2026, 7, 12, 20, 58))
    decision = _AdmissionProbe().daily_zhenxie_admission({'reconcile_after_window': True})
    assert decision['next_time'] == '2026-07-12 21:00:00'
