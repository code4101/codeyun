import pytest

from backend.core.fanxiu.data_annotation.kernel_scheduler_control import apply_incident_hold_attention

HOLD = {"key": "dispatch_hold:x", "reason": "legacy_missing_request_context", "task_id": None,
        "attempt_id": None, "identity_missing": ["task_id", "attempt_id"], "auto_dispatch": False}


@pytest.mark.parametrize("severity", [None, "ok", "attention", "blocked", "error"])
def test_no_hold_keeps_severity(severity):
    report = {"maintenance": {"severity": severity}}

    assert apply_incident_hold_attention(report, []) is report
    assert report["incident_holds"] == [] and report["maintenance"] == {"severity": severity}


@pytest.mark.parametrize("severity,expected", [(None, "attention"), ("ok", "attention"), ("attention", "attention"),
                                               ("blocked", "blocked"), ("error", "error")])
def test_holds_raise_to_attention_without_downgrade(severity, expected):
    report = {"maintenance": {"severity": severity}}

    apply_incident_hold_attention(report, [HOLD])

    assert report["maintenance"]["severity"] == expected
    assert report["maintenance"]["incident_hold_count"] == 1
    assert report["incident_holds"] == [HOLD] and report["incident_holds"][0]["task_id"] is None
