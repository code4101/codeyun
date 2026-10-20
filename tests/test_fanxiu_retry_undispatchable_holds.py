import json

from backend.core.fanxiu.data_annotation import ai_assistance
from backend.core.fanxiu.data_annotation import kernel_scheduler_control as ksc

P = ksc.DISPATCH_HOLD_PREFIX


def _rt(**fields):
    return {"status": "active", "kind": "runtime_error", "engineering_origin": True, **fields}


def test_retry_tick_with_another_due_hold_still_exposes_undispatchable_holds(tmp_path, monkeypatch):
    settings = tmp_path / "scheduler_settings.json"
    world = tmp_path / "world_facts.json"
    world.write_text(json.dumps({"discoveries": {
        f"{P}runtime_error:job-due:att-due": _rt(task_id="job-due", attempt_id="att-due", launch_status="deferred",
                                                retry_after=0, held_at_epoch=1.0,
                                                request_context={"task_id": "job-due", "entry_id": "e"}),
        f"{P}runtime_error:job-op:att-op": _rt(task_id="job-op", attempt_id="att-op", launch_status="needs_operator",
                                              dispatch_failures=5, held_at_epoch=2.0),
        f"{P}runtime_error:job-old:att-old": _rt(task_id="job-old", attempt_id="att-old", launch_status="deferred",
                                                retry_after=0, held_at_epoch=0.5),
    }}, ensure_ascii=False), encoding="utf-8")
    before = world.read_bytes()

    def launcher_must_not_run(*args, **kwargs):
        raise AssertionError("real launcher reached")

    monkeypatch.setattr(ai_assistance, "request_ai_assistance", launcher_must_not_run)

    result = ai_assistance.retry_deferred_runtime_error_launches(
        now=100.0, scheduler_settings_path=settings, scheduler_state_path=tmp_path / "scheduler_state.json",
        kernel_status={"execution_state": "busy"})

    # The legacy hold is older but never selected; the due hold is blocked, not dispatched.
    assert result["dispatched"] == 0 and result["reason"] == "kernel_busy"
    assert result["hold"] == f"{P}runtime_error:job-due:att-due"
    reasons = {row["task_id"]: row["reason"] for row in result["undispatchable_holds"]}
    assert reasons == {"job-op": "needs_operator", "job-old": "legacy_missing_request_context"}
    assert world.read_bytes() == before
