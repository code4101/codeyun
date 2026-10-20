import json

from backend.core.fanxiu.data_annotation import kernel_scheduler_control as ksc

P = ksc.DISPATCH_HOLD_PREFIX


def _write(tmp_path, holds):
    path = tmp_path / "world_facts.json"
    path.write_text(json.dumps({"discoveries": holds}, ensure_ascii=False), encoding="utf-8")
    return path


def _rt(**fields):
    return {"status": "active", "kind": "runtime_error", "engineering_origin": True, **fields}


def test_needs_operator_and_legacy_are_diagnosed_without_touching_holds(tmp_path):
    path = _write(tmp_path, {
        f"{P}runtime_error:job-a:att-a": _rt(task_id="job-a", attempt_id="att-a", dispatch_id="d-1",
                                            launch_status="needs_operator", dispatch_failures=5),
        f"{P}runtime_error:job-b:att-b": _rt(task_id="job-b", attempt_id="att-b", launch_status="deferred"),
        f"{P}runtime_error:job-c:att-c": _rt(task_id="job-c", attempt_id="att-c", launch_status="deferred",
                                            request_context={"task_id": "job-c"}),
        f"{P}runtime_error:job-d:att-d": _rt(task_id="job-d", attempt_id="att-d", status="released",
                                            launch_status="needs_operator"),
        f"{P}123": {"status": "active", "label": "plain", "launch_status": "needs_operator"},
    })
    before = path.read_bytes()

    rows = {row["task_id"]: row for row in ksc.dispatch_hold_attention(path)}

    assert path.read_bytes() == before
    assert set(rows) == {"job-a", "job-b"}
    assert rows["job-a"]["key"] == f"{P}runtime_error:job-a:att-a"
    assert rows["job-a"]["reason"] == "needs_operator"
    assert rows["job-a"]["attempt_id"] == "att-a" and rows["job-a"]["dispatch_id"] == "d-1"
    assert rows["job-b"]["key"] == f"{P}runtime_error:job-b:att-b"
    assert rows["job-b"]["reason"] == "legacy_missing_request_context"
    assert all(row["auto_dispatch"] is False and row["identity_missing"] == [] for row in rows.values())


def test_missing_identity_and_malformed_key_stay_none(tmp_path):
    path = _write(tmp_path, {
        f"{P}x": _rt(launch_status="dispatch_failed"),
        f"{P}runtime_error:job-e:att-e": _rt(task_id="job-e", attempt_id="att-e", launch_status="needs_operator",
                                            dispatch_failures="legacy?", held_at_epoch=1.5),
    })

    rows = {row["key"]: row for row in ksc.dispatch_hold_attention(path)}
    row = rows[f"{P}x"]
    legacy_failures = rows[f"{P}runtime_error:job-e:att-e"]

    assert legacy_failures["dispatch_failures"] == "legacy?" and legacy_failures["held_at_epoch"] == 1.5
    assert row["task_id"] is None and row["attempt_id"] is None
    assert row["identity_missing"] == ["task_id", "attempt_id"]
    assert row["reason"] == "legacy_missing_request_context"
