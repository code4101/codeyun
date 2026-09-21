from __future__ import annotations

import json
from datetime import datetime

import pytest

from backend.core.fanxiu.data_annotation import kernel_scheduler_control


JOB_ID = "daily-aggregate"
SIBLING_TASK_ID = "sibling-job"
ATTEMPT = "attempt-live"
CYCLE = "2026-09-21"


def test_business_retry_skips_ten_completed_components_and_restarts_failed_one(tmp_path):
    """Exercise real orchestration/persistence with pure numeric operations."""
    from backend.core.fanxiu.data_annotation.tasks.aggregate_progress import AggregateJobProgress
    path = tmp_path / "scheduler.json"
    _seed_state(path)
    calls = []
    progress = AggregateJobProgress(JOB_ID, ATTEMPT, scheduler_state_path=path)

    def component(index, fail=False):
        calls.append(index)
        if fail:
            raise RuntimeError("component failure")
        yield index
        return {"completed": index}

    for index in range(1, 11):
        list(progress.run(str(index), CYCLE, lambda i=index: component(i)))
    with pytest.raises(RuntimeError, match="component failure"):
        list(progress.run("11", CYCLE, lambda: component(11, fail=True)))
    # A fresh orchestrator uses the stored facts, not a retained generator.
    retry = AggregateJobProgress(JOB_ID, ATTEMPT, scheduler_state_path=path)
    for index in range(1, 13):
        list(retry.run(str(index), CYCLE, lambda i=index: component(i)))
    assert calls == [*range(1, 12), 11, 12]
    list(retry.run("1", "2026-09-22", lambda: component(1)))
    list(retry.run("2", CYCLE, lambda: component(2), version="2"))
    assert calls[-2:] == [1, 2]


def _seed_state(
    path,
    *,
    attempt_id: str = ATTEMPT,
    payload: dict | None = None,
    include_sibling: bool = True,
) -> list[dict]:
    tasks: list[dict] = [
        {
            "id": JOB_ID,
            "task_type": "daily_aggregate",
            "label": "聚合作业",
            "attempt_id": attempt_id,
            "next_time": "2026-09-21 00:00:00",
            "last_result": "running",
            "payload": dict(payload if payload is not None else {"daily_window": "00:00-01:00"}),
        }
    ]
    if include_sibling:
        tasks.append(
            {
                "id": SIBLING_TASK_ID,
                "task_type": "sibling_job",
                "label": "兄弟作业",
                "attempt_id": "sibling-attempt",
                "payload": {"keep": "sibling"},
            }
        )
    path.write_text(json.dumps(tasks, ensure_ascii=False, indent=2), encoding="utf-8")
    return tasks


def _record(path, stage_id, *, status, stage_version="v1", result=None, cycle=CYCLE,
            attempt=ATTEMPT, now=None):
    return kernel_scheduler_control.record_scheduler_job_stage(
        JOB_ID,
        cycle,
        stage_id,
        stage_version=stage_version,
        status=status,
        result=result,
        expected_attempt_id=attempt,
        scheduler_state_path=path,
        now=now,
    )


def _read(path, cycle=CYCLE, task_id=JOB_ID):
    return kernel_scheduler_control.read_scheduler_job_progress(
        task_id, cycle, scheduler_state_path=path
    )


def test_completed_stages_survive_a_later_failed_stage(tmp_path):
    path = tmp_path / "scheduler_tasks.json"
    _seed_state(path)

    for index in range(1, 11):
        record = _record(
            path,
            f"stage-{index}",
            status="complete",
            result={"index": index},
            now=datetime(2026, 9, 21, 0, index, 0),
        )
        assert record["status"] == "complete"
        assert record["updated_at"] == f"2026-09-21T00:{index:02d}:00"

    failed = _record(
        path,
        "stage-11",
        status="failed",
        result={"reason": "business miss"},
        now=datetime(2026, 9, 21, 0, 11, 0),
    )
    assert failed["status"] == "failed"

    progress = _read(path)
    assert len(progress) == 11
    for index in range(1, 11):
        assert progress[f"stage-{index}"] == {
            "stage_version": "v1",
            "status": "complete",
            "result": {"index": index},
            "updated_at": f"2026-09-21T00:{index:02d}:00",
        }
    assert progress["stage-11"]["status"] == "failed"
    assert progress["stage-11"]["result"] == {"reason": "business miss"}


def test_stage_update_preserves_sibling_payload_and_other_task_fields(tmp_path):
    path = tmp_path / "scheduler_tasks.json"
    _seed_state(
        path,
        payload={
            "daily_window": "00:00-01:00",
            "transient": {"keep": True},
            "aggregate_progress": {
                CYCLE: {"stage-1": {"stage_version": "v1", "status": "complete"}}
            },
        },
    )

    _record(path, "stage-2", status="complete", result={"ok": True})

    stored = json.loads(path.read_text(encoding="utf-8"))
    job = next(item for item in stored if item.get("id") == JOB_ID)
    assert job["next_time"] == "2026-09-21 00:00:00"
    assert job["last_result"] == "running"
    assert job["attempt_id"] == ATTEMPT
    assert job["payload"]["daily_window"] == "00:00-01:00"
    assert job["payload"]["transient"] == {"keep": True}
    assert job["payload"]["aggregate_progress"][CYCLE]["stage-1"] == {
        "stage_version": "v1",
        "status": "complete",
    }
    assert job["payload"]["aggregate_progress"][CYCLE]["stage-2"]["result"] == {"ok": True}

    sibling = next(item for item in stored if item.get("id") == SIBLING_TASK_ID)
    assert sibling == {
        "id": SIBLING_TASK_ID,
        "task_type": "sibling_job",
        "label": "兄弟作业",
        "attempt_id": "sibling-attempt",
        "payload": {"keep": "sibling"},
    }


def test_cycles_and_stage_versions_stay_distinguishable(tmp_path):
    path = tmp_path / "scheduler_tasks.json"
    _seed_state(path)

    _record(path, "stage-1", status="complete", result={"day": 20},
            cycle="2026-09-20", stage_version="v1")
    _record(path, "stage-1", status="complete", result={"day": 21},
            cycle="2026-09-21", stage_version="v2")

    day20 = _read(path, cycle="2026-09-20")
    day21 = _read(path, cycle="2026-09-21")
    assert day20["stage-1"]["result"] == {"day": 20}
    assert day20["stage-1"]["stage_version"] == "v1"
    assert day21["stage-1"]["result"] == {"day": 21}
    assert day21["stage-1"]["stage_version"] == "v2"

    _record(path, "stage-1", status="complete", result={"day": 21, "rev": 2},
            cycle="2026-09-21", stage_version="v3")

    assert _read(path, cycle="2026-09-20")["stage-1"]["stage_version"] == "v1"
    refreshed = _read(path, cycle="2026-09-21")["stage-1"]
    assert refreshed["stage_version"] == "v3"
    assert refreshed["result"] == {"day": 21, "rev": 2}


def test_stale_attempt_is_rejected_and_leaves_the_file_unchanged(tmp_path):
    path = tmp_path / "scheduler_tasks.json"
    _seed_state(path)
    before = path.read_bytes()

    with pytest.raises(kernel_scheduler_control.SchedulerJobProgressConflict):
        _record(path, "stage-1", status="complete", result={"ok": True}, attempt="attempt-stale")

    assert path.read_bytes() == before
    assert _read(path) == {}


def test_unknown_job_is_never_created(tmp_path):
    path = tmp_path / "scheduler_tasks.json"
    _seed_state(path)
    before = path.read_bytes()

    with pytest.raises(LookupError):
        kernel_scheduler_control.record_scheduler_job_stage(
            "missing-job",
            CYCLE,
            "stage-1",
            stage_version="v1",
            status="complete",
            result={"ok": True},
            expected_attempt_id=ATTEMPT,
            scheduler_state_path=path,
        )

    assert path.read_bytes() == before
    assert _read(path, task_id="missing-job") == {}


def test_read_is_side_effect_free_when_state_is_missing(tmp_path):
    path = tmp_path / "absent.json"

    assert _read(path) == {}
    assert not path.exists()


def test_complete_requires_a_json_serializable_result(tmp_path):
    path = tmp_path / "scheduler_tasks.json"
    _seed_state(path)

    with pytest.raises(ValueError):
        _record(path, "stage-1", status="complete", result=None)
    with pytest.raises(ValueError):
        _record(path, "stage-1", status="complete", result=object())

    failed = _record(path, "stage-1", status="failed", result=None)
    assert failed["status"] == "failed"
    assert failed["result"] is None


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"task_id": "", "cycle_key": CYCLE, "stage_id": "s"}, "task_id"),
        ({"task_id": JOB_ID, "cycle_key": " ", "stage_id": "s"}, "cycle_key"),
        ({"task_id": JOB_ID, "cycle_key": CYCLE, "stage_id": ""}, "stage_id"),
    ],
)
def test_record_rejects_empty_identity_fields(tmp_path, kwargs, message):
    path = tmp_path / "scheduler_tasks.json"
    _seed_state(path)

    with pytest.raises(ValueError, match=message):
        kernel_scheduler_control.record_scheduler_job_stage(
            kwargs["task_id"],
            kwargs["cycle_key"],
            kwargs["stage_id"],
            stage_version="v1",
            status="complete",
            result={"ok": True},
            expected_attempt_id=ATTEMPT,
            scheduler_state_path=path,
        )


def test_record_rejects_bad_stage_version_status_and_owner(tmp_path):
    path = tmp_path / "scheduler_tasks.json"
    _seed_state(path)

    with pytest.raises(ValueError, match="stage_version"):
        _record(path, "stage-1", status="complete", result={"ok": True}, stage_version="")
    with pytest.raises(ValueError, match="status"):
        _record(path, "stage-1", status="running", result={"ok": True})
    with pytest.raises(ValueError, match="expected_attempt_id"):
        _record(path, "stage-1", status="complete", result={"ok": True}, attempt="")

    assert _read(path) == {}


def test_read_rejects_empty_identity_fields(tmp_path):
    path = tmp_path / "scheduler_tasks.json"
    _seed_state(path)

    with pytest.raises(ValueError, match="task_id"):
        _read(path, task_id="")
    with pytest.raises(ValueError, match="cycle_key"):
        _read(path, cycle=" ")
