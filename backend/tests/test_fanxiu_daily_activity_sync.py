from __future__ import annotations

import json
from datetime import datetime

from backend.core.fanxiu.activity.daily_activity_sync import (
    load_worldline_activity_schedule_snapshot,
    synchronize_daily_activities,
    synchronize_daily_activity_plan,
)


NOW = datetime.fromisoformat("2026-08-14T00:06:00+08:00")


def _proposal_operation() -> dict:
    raw = {
        "id": 4150001400002,
        "activityId": 4150001,
        "activityType": 15,
        "scheduleId": 6400001,
        "startTime": 1786586400000,
        "endTime": 1786716000000,
        "serverCount": 4,
        "name": "兽渊探秘",
        "identityComplete": True,
    }
    return {
        "action": "propose_create",
        "reason": "图鉴身份完整，但本期活动实例尚未登记",
        "occurrence": {
            "runtime_ids": [raw["id"]],
            "activity_id": raw["activityId"],
            "schedule_id": raw["scheduleId"],
            "name": raw["name"],
            "display_name": raw["name"],
            "cross_count": 4,
            "start_date": "2026-08-13",
            "end_date": "2026-08-14",
            "catalog_status": "known",
            "identity_complete": True,
            "raw": raw,
        },
        "proposed_occurrence": {
            "id": "runtime-4150001-1786586400000-4",
            "name": "兽渊探秘",
            "cross_count": 4,
            "start_date": "2026-08-13",
            "end_date": "2026-08-14",
        },
    }


def _plan(*operations: dict, status: str = "ready") -> dict:
    occurrences = [
        dict(operation["occurrence"])
        for operation in operations
        if isinstance(operation.get("occurrence"), dict)
    ]
    return {
        "status": status,
        "target_date": "2026-08-14",
        "timezone": "Asia/Shanghai",
        "source_kind": "worldline_activity_runtime_memory",
        "captured_at": "2026-08-14T00:05:00+08:00",
        "source_evidence": {
            "count": len(operations),
            "declared_count": len(operations),
            "runtime": {
                "pid": 4321,
                "process_start_ticks": 987654,
                "manager_resolver": "lua_global",
            },
        },
        "occurrences": occurrences,
        "operations": list(operations),
    }


def test_not_loaded_plan_performs_zero_reads_or_writes(tmp_path) -> None:
    calls: list[str] = []
    result = synchronize_daily_activity_plan(
        _plan(status="not_loaded"),
        persist=True,
        now=NOW,
        load_occurrences=lambda: calls.append("load") or [],
        save_occurrences=lambda items: calls.append("save") or items,
        audit_path=tmp_path / "audit.json",
    )

    assert result["status"] == "not_written"
    assert calls == []
    assert not (tmp_path / "audit.json").exists()


def test_dry_run_reconciles_but_does_not_write(tmp_path) -> None:
    calls: list[str] = []
    result = synchronize_daily_activity_plan(
        _plan(_proposal_operation()),
        now=NOW,
        load_occurrences=lambda: calls.append("load") or [],
        save_occurrences=lambda items: calls.append("save") or items,
        audit_path=tmp_path / "audit.json",
    )

    assert result["status"] == "planned"
    assert result["write_authorized"] is False
    assert result["created_count"] == 1
    assert calls == ["load"]
    assert not (tmp_path / "audit.json").exists()


def test_persist_appends_only_validated_occurrence_and_writes_evidence(tmp_path) -> None:
    stored: list[dict] = []

    def save(items: list[dict]) -> list[dict]:
        stored[:] = [dict(item) for item in items]
        return [dict(item) for item in stored]

    audit_path = tmp_path / "audit.json"
    schedule_path = tmp_path / "schedule.json"
    result = synchronize_daily_activity_plan(
        _plan(_proposal_operation()),
        persist=True,
        now=NOW,
        load_occurrences=lambda: [dict(item) for item in stored],
        save_occurrences=save,
        audit_path=audit_path,
        schedule_snapshot_path=schedule_path,
    )

    assert result["status"] == "updated"
    assert result["persisted"] is True
    assert result["schedule_snapshot_written"] is True
    assert stored == [
        {
            "id": "runtime-4150001-1786586400000-4",
            "name": "兽渊探秘",
            "cross_count": 4,
            "start_date": "2026-08-13",
            "end_date": "2026-08-14",
        }
    ]
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    assert audit["latest"]["source_evidence"]["runtime"] == {
        "pid": 4321,
        "process_start_ticks": 987654,
        "manager_resolver": "lua_global",
    }
    assert audit["latest"]["created_items"] == stored
    assert audit["latest"]["occurrences"][0]["raw"]["activityId"] == 4150001
    schedule = json.loads(schedule_path.read_text(encoding="utf-8"))
    assert schedule["occurrence_count"] == 1
    assert schedule["occurrences"][0]["raw"]["activityId"] == 4150001
    assert audit["latest"]["created_sources"][0]["raw_runtime"] == {
        "id": 4150001400002,
        "activityId": 4150001,
        "activityType": 15,
        "scheduleId": 6400001,
        "startTime": 1786586400000,
        "endTime": 1786716000000,
        "serverCount": 4,
        "name": "兽渊探秘",
        "identityComplete": True,
    }


def test_repeating_same_plan_does_not_duplicate_activity(tmp_path) -> None:
    stored: list[dict] = []
    save_calls = 0

    def save(items: list[dict]) -> list[dict]:
        nonlocal save_calls
        save_calls += 1
        stored[:] = [dict(item) for item in items]
        return stored

    kwargs = {
        "persist": True,
        "now": NOW,
        "load_occurrences": lambda: [dict(item) for item in stored],
        "save_occurrences": save,
        "audit_path": tmp_path / "audit.json",
        "schedule_snapshot_path": tmp_path / "schedule.json",
    }
    first = synchronize_daily_activity_plan(_plan(_proposal_operation()), **kwargs)
    second = synchronize_daily_activity_plan(_plan(_proposal_operation()), **kwargs)

    assert first["status"] == "updated"
    assert second["status"] == "no_change"
    assert second["noop_count"] == 1
    assert save_calls == 1
    assert len(stored) == 1


def test_full_snapshot_keeps_future_and_unknown_rows_without_projection_write(tmp_path) -> None:
    operation = _proposal_operation()
    operation["action"] = "review_unknown_identity"
    operation["proposed_occurrence"] = None
    operation["occurrence"].update(
        {
            "identity_complete": False,
            "catalog_status": "missing",
            "on_target_day": False,
            "day_relation": "outside_day",
            "prepare_at": "2026-08-15T05:00:00+08:00",
            "start_at": "2026-08-16T10:00:00+08:00",
            "close_panel_at": "2026-08-18T23:59:59+08:00",
        }
    )
    schedule_path = tmp_path / "schedule.json"
    saved: list[list[dict]] = []

    result = synchronize_daily_activity_plan(
        _plan(operation),
        persist=True,
        now=NOW,
        load_occurrences=lambda: [],
        save_occurrences=lambda items: saved.append(items) or items,
        audit_path=tmp_path / "audit.json",
        schedule_snapshot_path=schedule_path,
    )

    assert saved == []
    assert result["status"] == "review_required"
    assert result["persisted"] is False
    assert result["schedule_snapshot_written"] is True
    snapshot = json.loads(schedule_path.read_text(encoding="utf-8"))
    assert snapshot["occurrences"][0]["day_relation"] == "outside_day"
    assert snapshot["occurrences"][0]["raw"]["id"] == 4150001400002

    loaded = load_worldline_activity_schedule_snapshot(schedule_path)
    assert loaded["occurrence_count"] == 1
    assert loaded["occurrences"][0]["prepare_at"] == "2026-08-15T05:00:00+08:00"


def test_missing_full_snapshot_loads_as_empty_fact_set(tmp_path) -> None:
    loaded = load_worldline_activity_schedule_snapshot(tmp_path / "missing.json")

    assert loaded["occurrence_count"] == 0
    assert loaded["occurrences"] == []
    assert loaded["activity_observation_count"] == 0
    assert loaded["activity_observations"] == []


def test_full_snapshot_persists_observation_without_dated_occurrence(tmp_path) -> None:
    plan = _plan()
    plan["activity_observations"] = [
        {
            "observation_id": "revenue:712",
            "activity_id": 712,
            "template_id": 909,
            "name": "万宝臻宝",
            "is_schedule_occurrence": False,
        }
    ]
    saved: list[list[dict]] = []
    schedule_path = tmp_path / "schedule.json"

    result = synchronize_daily_activity_plan(
        plan,
        persist=True,
        now=NOW,
        load_occurrences=lambda: [],
        save_occurrences=lambda items: saved.append(items) or items,
        audit_path=tmp_path / "audit.json",
        schedule_snapshot_path=schedule_path,
    )

    assert saved == []
    assert result["schedule_occurrence_count"] == 0
    assert result["activity_observation_count"] == 1
    loaded = load_worldline_activity_schedule_snapshot(schedule_path)
    assert loaded["occurrence_count"] == 0
    assert loaded["activity_observation_count"] == 1
    assert loaded["activity_observations"][0]["activity_id"] == 712
    assert "schedule_id" not in loaded["activity_observations"][0]


def test_unknown_identity_is_audited_but_never_added(tmp_path) -> None:
    operation = _proposal_operation()
    operation["action"] = "review_unknown_identity"
    operation["proposed_occurrence"] = None
    operation["occurrence"]["identity_complete"] = False
    operation["occurrence"]["catalog_status"] = "missing"
    save_calls: list[list[dict]] = []
    result = synchronize_daily_activity_plan(
        _plan(operation),
        persist=True,
        now=NOW,
        load_occurrences=lambda: [],
        save_occurrences=lambda items: save_calls.append(items) or items,
        audit_path=tmp_path / "audit.json",
        schedule_snapshot_path=tmp_path / "schedule.json",
    )

    assert result["status"] == "review_required"
    assert result["persisted"] is False
    assert result["review_count"] == 1
    assert save_calls == []
    audit = json.loads((tmp_path / "audit.json").read_text(encoding="utf-8"))
    assert audit["latest"]["reviews"][0]["raw_runtime"]["activityId"] == 4150001


def test_write_time_scope_conflict_blocks_proposal(tmp_path) -> None:
    current = [
        {
            "id": "manual",
            "name": "兽渊探秘",
            "cross_count": 8,
            "start_date": "2026-08-13",
            "end_date": "2026-08-14",
        }
    ]
    save_calls: list[list[dict]] = []
    result = synchronize_daily_activity_plan(
        _plan(_proposal_operation()),
        persist=True,
        now=NOW,
        load_occurrences=lambda: current,
        save_occurrences=lambda items: save_calls.append(items) or items,
        audit_path=tmp_path / "audit.json",
        schedule_snapshot_path=tmp_path / "schedule.json",
    )

    assert result["status"] == "review_required"
    assert result["reviews"][0]["action"] == "review_scope_conflict"
    assert save_calls == []


def test_stale_plan_is_rejected_before_storage_read(tmp_path) -> None:
    calls: list[str] = []
    result = synchronize_daily_activity_plan(
        _plan(_proposal_operation()),
        persist=True,
        now=datetime.fromisoformat("2026-08-14T00:20:00+08:00"),
        load_occurrences=lambda: calls.append("load") or [],
        save_occurrences=lambda items: calls.append("save") or items,
        audit_path=tmp_path / "audit.json",
    )

    assert result["status"] == "not_written"
    assert "过期" in result["reason"]
    assert calls == []


def test_service_reads_one_plan_and_forwards_bounded_runtime_options(tmp_path) -> None:
    reader_calls: list[dict] = []
    result = synchronize_daily_activities(
        persist=False,
        target_date="2026-08-14",
        allow_discovery=False,
        force_refresh=False,
        export_root="D:/exports",
        now=NOW,
        plan_reader=lambda **kwargs: reader_calls.append(kwargs)
        or _plan(_proposal_operation()),
        load_occurrences=lambda: [],
        audit_path=tmp_path / "audit.json",
    )

    assert result["status"] == "planned"
    assert reader_calls == [
        {
            "target_date": "2026-08-14",
            "timezone_name": "Asia/Shanghai",
            "allow_discovery": False,
            "force_refresh": False,
            "export_root": "D:/exports",
        }
    ]
