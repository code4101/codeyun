from __future__ import annotations

from datetime import datetime

from backend.core.fanxiu.data_annotation.scheduler_incidents import (
    detect_scheduler_environment_circuit,
    list_scheduler_incidents,
    record_scheduler_incident,
)


def test_scheduler_window_expiry_persists_ai_review_evidence(tmp_path) -> None:
    scheduler_path = tmp_path / "scheduler_tasks.json"
    task = {
        "id": "daily-daofa",
        "task_type": "daily_daofa",
        "label": "道法争锋",
        "next_time": "2026-07-30 23:00:00",
        "last_run_at": "2026-07-30 23:05:20",
        "last_result": "error",
        "last_message": "等待挑战结果超时",
        "started_at": "2026-07-30 23:05:20",
        "attempt_kernel_generation": 86,
    }

    recorded = record_scheduler_incident(
        task=task,
        original_next_time="2026-07-30 23:00:00",
        next_time="2026-07-31 23:00:00",
        incident={
            "kind": "window_expired",
            "cycle_kind": "daily",
            "window": "周一至周六 23:00-23:59",
            "reason": "该日窗口已结束，禁止跨日补跑",
        },
        attempt_id="attempt-a",
        entry_id="entry-a",
        occurred_at=datetime(2026, 7, 31, 9, 22, 0),
        runtime_status={
            "status": "error",
            "phase": "wait_scene",
            "message": "等待挑战结果超时",
            "logs": [
                {"time": "23:15:00", "kind": "error", "message": "未检测到结果页"},
            ],
        },
        scheduler_state_path=scheduler_path,
    )

    assert recorded["cycle_kind"] == "daily"
    assert recorded["cycle_date"] == "2026-07-30"
    assert recorded["analysis_status"] == "pending"
    assert recorded["schedule"]["next_time"] == "2026-07-31 23:00:00"
    assert recorded["evidence"]["recent_logs"][0]["message"] == "未检测到结果页"
    assert recorded["ai_handoff"]["questions"]
    assert list_scheduler_incidents(
        scheduler_state_path=scheduler_path,
        analysis_status="pending",
    ) == [recorded]


def test_scheduler_incident_is_idempotent_for_same_expired_attempt(tmp_path) -> None:
    scheduler_path = tmp_path / "scheduler_tasks.json"
    kwargs = {
        "task": {"id": "daily-daofa", "task_type": "daily_daofa"},
        "original_next_time": "2026-07-30 23:00:00",
        "next_time": "2026-07-31 23:00:00",
        "incident": {"kind": "window_expired"},
        "attempt_id": "attempt-a",
        "entry_id": "entry-a",
        "occurred_at": datetime(2026, 7, 31, 9, 22, 0),
        "scheduler_state_path": scheduler_path,
    }

    first = record_scheduler_incident(**kwargs)
    second = record_scheduler_incident(**kwargs)

    assert first["id"] == second["id"]
    assert len(list_scheduler_incidents(scheduler_state_path=scheduler_path)) == 1


def test_scheduler_incident_excludes_logs_from_earlier_jobs(tmp_path) -> None:
    recorded = record_scheduler_incident(
        task={
            "id": "prayer-daily-resource",
            "task_type": "prayer_daily_resource",
            "started_at": "2026-08-13 00:40:30",
            "last_result": "running",
        },
        original_next_time="2026-08-13 00:19:15",
        next_time="2026-08-13 00:52:28",
        incident={"kind": "attempt_failed"},
        attempt_id="attempt-prayer",
        entry_id="entry-a",
        occurred_at=datetime(2026, 8, 13, 0, 42, 28),
        runtime_status={
            "logs": [
                {"time": "00:35:24", "message": "上一项邮件完成"},
                {"time": "00:40:45", "message": "本轮祈愿开始"},
                {"time": "00:42:27", "message": "本轮祈愿失败"},
            ]
        },
        scheduler_state_path=tmp_path / "scheduler_tasks.json",
    )

    assert [
        item["message"] for item in recorded["evidence"]["recent_logs"]
    ] == ["本轮祈愿开始", "本轮祈愿失败"]


def _environment_failure_incident(
    *,
    incident_id: str,
    task_id: str,
    occurred_at: str,
    scene_id: int = 74,
) -> dict:
    reason = (
        "RuntimeError: unknown诊断=full_frame_similar_identity_mismatch："
        f"当前帧与已有 #{scene_id} 全图相似 95%，但身份证据仅 0%"
    )
    return {
        "id": incident_id,
        "kind": "attempt_failed",
        "occurred_at": occurred_at,
        "task": {"id": task_id},
        "schedule": {"reason": reason},
        "evidence": {"incident": {"kind": "attempt_failed", "reason": reason}},
    }


def test_environment_circuit_requires_same_signature_across_distinct_tasks() -> None:
    incidents = [
        _environment_failure_incident(
            incident_id="incident-a",
            task_id="daily-boss",
            occurred_at="2026-08-17 05:01:43",
        ),
        _environment_failure_incident(
            incident_id="incident-b",
            task_id="daily-assistant",
            occurred_at="2026-08-17 05:03:10",
        ),
    ]

    circuit = detect_scheduler_environment_circuit(
        incidents,
        now=datetime(2026, 8, 17, 5, 4, 0),
    )

    assert circuit == {
        "kind": "repeated_environment_failure",
        "signature_kind": "full_frame_similar_identity_mismatch",
        "scene_id": 74,
        "first_occurred_at": "2026-08-17 05:01:43",
        "last_occurred_at": "2026-08-17 05:03:10",
        "task_ids": ["daily-assistant", "daily-boss"],
        "incident_ids": ["incident-a", "incident-b"],
        "failure_count": 2,
        "distinct_task_count": 2,
    }


def test_environment_circuit_rejects_one_task_and_ordinary_known_scene_errors() -> None:
    repeated_same_task = [
        _environment_failure_incident(
            incident_id="incident-a",
            task_id="moyu-challenge",
            occurred_at="2026-08-17 18:21:16",
            scene_id=466,
        ),
        _environment_failure_incident(
            incident_id="incident-b",
            task_id="moyu-challenge",
            occurred_at="2026-08-17 18:22:24",
            scene_id=466,
        ),
        {
            "id": "ordinary",
            "kind": "attempt_failed",
            "occurred_at": "2026-08-17 18:23:00",
            "task": {"id": "daily-lingmai"},
            "schedule": {"reason": "最后 #382 100%"},
            "evidence": {"incident": {"reason": "最后 #382 100%"}},
        },
    ]

    assert detect_scheduler_environment_circuit(
        repeated_same_task,
        now=datetime(2026, 8, 17, 18, 24, 0),
    ) is None
