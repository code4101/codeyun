from __future__ import annotations

from datetime import datetime, timedelta
import json

import pytest

from backend.core.fanxiu.data_annotation.game_state_inspection import (
    GAME_STATE_INSPECTION_ALLOWED_SOURCES,
    GameStateProbe,
    inspect_game_state_once,
    read_game_state_inspection_status,
    registered_game_state_probes,
)
from backend.core.fanxiu.data_annotation.redpacket_state import (
    inspect_redpacket_game_state,
    read_current_redpacket_state,
    recover_redpacket_runtime_snapshot,
    refresh_redpacket_runtime_snapshot,
)
from backend.core.fanxiu.data_annotation.behavior_tree_control import (
    set_scheduler_task_next_time,
    task_payload_with_meta,
)
from backend.core.fanxiu.data_annotation.seat_mail_state import (
    inspect_seat_displacement_mail_state,
)
from backend.core.fanxiu.instrumentation import mail as mail_instrumentation
from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
)
from backend.core.fanxiu.data_annotation.state import (
    normalize_data_annotation_scheduler_task,
)


def test_empty_game_state_inspection_is_a_successful_noop(tmp_path):
    state_path = tmp_path / "game-state-inspection.json"

    snapshot = inspect_game_state_once(
        probes=[],
        state_path=state_path,
        now=datetime(2026, 7, 28, 12, 0, 0),
    )

    assert snapshot["status"] == "running"
    assert snapshot["last_result"] == "empty"
    assert snapshot["probe_count"] == 0
    assert snapshot["due_task_ids"] == []
    assert snapshot["last_checked_at"] == "2026-07-28 12:00:00"
    assert state_path.exists()


def test_game_state_inspection_is_paused_in_ai_mode(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.game_state_inspection."
        "_scheduler_job_group_enabled",
        lambda: False,
    )

    status = read_game_state_inspection_status(
        state_path=tmp_path / "game-state-inspection.json",
    )

    assert status["enabled"] is False
    assert status["status"] == "paused"
    assert status["next_check_at"] is None


def test_game_state_probe_sets_due_time_without_business_context(tmp_path):
    due_calls: list[tuple[str, datetime]] = []
    probe = GameStateProbe(
        id="red-packet",
        label="红包",
        source="runtime",
        read=lambda: {
            "facts": {"pending_count": 1},
            "due_task_ids": ["daily-redpacket"],
        },
    )

    snapshot = inspect_game_state_once(
        probes=[probe],
        due_sink=lambda task_id, due_at: due_calls.append((task_id, due_at)),
        state_path=tmp_path / "game-state-inspection.json",
        now=datetime(2026, 7, 28, 12, 0, 0),
    )

    assert snapshot["facts"] == {"red-packet": {"pending_count": 1}}
    assert snapshot["due_task_ids"] == ["daily-redpacket"]
    assert due_calls == [(
        "daily-redpacket",
        datetime(2026, 7, 28, 12, 0, 0),
    )]


def test_game_state_inspection_only_allows_runtime_sources():
    assert GAME_STATE_INSPECTION_ALLOWED_SOURCES == {"runtime"}

    with pytest.raises(ValueError, match="禁止抓包"):
        GameStateProbe(
            id="forbidden-visual-probe",
            label="视觉巡检",
            source="visual",
            read=lambda: {},
        )
    with pytest.raises(ValueError, match="禁止抓包"):
        GameStateProbe(
            id="forbidden-packet-probe",
            label="抓包巡检",
            source="packet",
            read=lambda: {},
        )


def test_async_recovery_keeps_idle_and_two_minute_production_runway(monkeypatch):
    from backend.core.fanxiu.data_annotation import game_state_inspection
    from backend.core.fanxiu.data_annotation import behavior_tree_control
    from backend.core.fanxiu.behavior_tree import jupyter_kernel

    monkeypatch.setattr(game_state_inspection, "_scheduler_job_group_enabled", lambda: True)
    monkeypatch.setattr(
        jupyter_kernel,
        "fanxiu_kernel_manager_status",
        lambda: {"alive": True, "execution_state": "busy"},
    )
    monkeypatch.setattr(behavior_tree_control, "read_scheduler_tasks", lambda: [])
    allowed, reason = game_state_inspection._inspection_recovery_allowed()
    assert allowed is False
    assert reason == "Kernel 正忙"

    monkeypatch.setattr(
        jupyter_kernel,
        "fanxiu_kernel_manager_status",
        lambda: {"alive": True, "execution_state": "idle"},
    )
    monkeypatch.setattr(
        behavior_tree_control,
        "read_scheduler_tasks",
        lambda: [{
            "id": "production-job",
            "label": "到期生产作业",
            "next_time": (datetime.now() + timedelta(seconds=90)).strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
        }],
    )
    allowed, reason = game_state_inspection._inspection_recovery_allowed()
    assert allowed is False
    assert reason == "两分钟内有到期作业：到期生产作业"


def test_scheduler_atomically_sets_only_next_time(tmp_path):
    path = tmp_path / "scheduler_tasks.json"
    path.write_text(json.dumps([{
        "id": "daily-redpacket",
        "next_time": "2026-07-28 18:00:00",
        "last_result": "success",
        "payload": {"interval_seconds": 43200},
    }]), encoding="utf-8")

    result = set_scheduler_task_next_time(
        "daily-redpacket",
        datetime(2026, 7, 28, 12, 1, 0),
        scheduler_state_path=path,
    )

    assert result == "2026-07-28 12:01:00"
    task = json.loads(path.read_text(encoding="utf-8"))[0]
    assert task == {
        "id": "daily-redpacket",
        "next_time": "2026-07-28 12:01:00",
        "last_result": "success",
        "payload": {"interval_seconds": 43200},
    }


def test_scheduler_next_time_command_repairs_missing_standard_instance(tmp_path):
    path = tmp_path / "scheduler_tasks.json"

    result = set_scheduler_task_next_time(
        "daily-redpacket",
        datetime(2026, 7, 28, 12, 1, 0),
        scheduler_state_path=path,
    )

    assert result == "2026-07-28 12:01:00"
    tasks = json.loads(path.read_text(encoding="utf-8"))
    redpacket = next(task for task in tasks if task["id"] == "daily-redpacket")
    assert redpacket["next_time"] == "2026-07-28 12:01:00"


def test_scheduler_payload_never_transports_inspection_business_context():
    payload = task_payload_with_meta({
        "id": "daily-redpacket",
        "payload": {"interval_seconds": 43200},
        "scheduler_meta": {
            "business_context": {"must_not_reach_cell": True},
        },
    })

    assert payload["interval_seconds"] == 43200
    assert payload["__scheduler_task_id"] == "daily-redpacket"
    assert "__state_inspection_context" not in payload


def test_scheduler_normalization_removes_legacy_inspection_business_context():
    task = normalize_data_annotation_scheduler_task({
        "id": "daily-redpacket",
        "task_type": "daily_redpacket",
        "scheduler_meta": {
            "state_inspection": {"context": {"pending_count": 14}},
            "blocked_message": "保留的通用调度信息",
        },
    })

    assert task is not None
    assert task["scheduler_meta"] == {
        "blocked_message": "保留的通用调度信息",
    }


def test_redpacket_current_state_reads_fresh_runtime_hot_path(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.redpacket_state."
        "read_cached_chat_red_packet_pending",
        lambda: calls.append({}) or {
            "ok": True,
            "available": True,
            "complete": True,
            "source": "runtime_memory_chat_hot_path",
            "pending": True,
            "pending_count": 1,
            "items": [{"uid": 3, "channel": 6, "sub_channel_id": 0}],
            "sources": {"chat": {"available": True, "complete": True, "pending_count": 1}},
        },
    )

    result = read_current_redpacket_state()

    assert calls == [{}]
    assert result["pending_count"] == 1
    assert result["trigger_authoritative"] is False
    assert result["trigger_reason"] == "chat_semantics_incomplete"


def test_redpacket_current_state_requires_explicit_semantic_completion(monkeypatch):
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.redpacket_state."
        "read_cached_chat_red_packet_pending",
        lambda: {
            "ok": True,
            "available": True,
            "complete": True,
            "pending": True,
            "pending_count": 1,
            "items": [{"uid": 3, "channel": 6, "sub_channel_id": 0}],
            "sources": {
                "chat": {
                    "available": True,
                    "complete": True,
                    "semantic_complete": True,
                    "pending_count": 1,
                }
            },
        },
    )

    result = read_current_redpacket_state()

    assert result["trigger_authoritative"] is True
    assert result["trigger_reason"] == "fresh_semantic_chat_candidates"
    assert result["recovery_required"] is False


def test_redpacket_hot_path_failure_requires_recovery_and_cannot_trigger(monkeypatch):
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.redpacket_state."
        "read_cached_chat_red_packet_pending",
        lambda: {
            "ok": False,
            "available": False,
            "pending": False,
            "pending_count": 0,
            "reason": "红包地址缓存尚未预热",
        },
    )

    result = read_current_redpacket_state(max_age_seconds=180.0)

    assert result["ok"] is False
    assert result["pending"] is False
    assert result["trigger_authoritative"] is False
    assert result["trigger_reason"] == "chat_semantics_incomplete"
    assert result["recovery_required"] is True


def test_redpacket_runtime_levels_do_not_authorize_claim_actions(monkeypatch):
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.redpacket_state."
        "read_cached_chat_red_packet_pending",
        lambda: {
            "ok": True,
            "available": True,
            "complete": True,
            "pending": True,
            "pending_count": 1,
            "items": [{"uid": 9, "channel": 6, "sub_channel_id": 0}],
            "sources": {
                "chat": {
                    "available": True,
                    "complete": True,
                    "semantic_complete": True,
                    "pending_count": 1,
                }
            },
        },
    )

    result = read_current_redpacket_state()

    assert result["evidence_levels"] == {
        "structural": True,
        "semantic": True,
        "trigger": True,
        "claimability": True,
        "action": False,
    }
    assert result["pending_uids"] == ["9"]
    assert result["trigger_ready"] is True
    assert result["action_authorized"] is False
    assert result["recovery_required"] is False


def test_redpacket_unknown_claimability_still_triggers_gui_without_action_authority(
    monkeypatch,
):
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.redpacket_state."
        "read_cached_chat_red_packet_pending",
        lambda: {
            "ok": True,
            "available": True,
            "complete": True,
            "pending": True,
            "pending_count": 1,
            "items": [{"uid": 52001, "channel": 6, "sub_channel_id": 0}],
            "sources": {
                "chat": {
                    "available": True,
                    "complete": True,
                    "trigger_complete": True,
                    "claimability_complete": False,
                    "semantic_complete": False,
                    "pending_count": 1,
                }
            },
        },
    )

    result = read_current_redpacket_state()

    assert result["trigger_ready"] is True
    assert result["trigger_reason"] == (
        "fresh_structural_chat_candidates_claimability_unknown"
    )
    assert result["evidence_levels"] == {
        "structural": True,
        "semantic": True,
        "trigger": True,
        "claimability": False,
        "action": False,
    }
    assert result["action_authorized"] is False
    assert result["recovery_required"] is False


def test_redpacket_receive_queue_transition_triggers_deep_check_but_not_action(
    monkeypatch,
):
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.redpacket_state."
        "read_cached_chat_red_packet_pending",
        lambda: {
            "ok": True,
            "available": True,
            "complete": True,
            "pending": True,
            "pending_count": 1,
            "items": [],
            "sources": {
                "chat": {
                    "available": True,
                    "complete": True,
                    "trigger_complete": True,
                    "claimability_complete": False,
                    "semantic_complete": False,
                    "receive_queue_count": 1,
                    "pending_count": 1,
                }
            },
        },
    )

    result = read_current_redpacket_state()

    assert result["trigger_ready"] is True
    assert result["pending_uids"] == []
    assert result["action_authorized"] is False


def test_redpacket_patrol_refresh_never_discovers_manager_inline(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.redpacket_state."
        "read_cached_chat_red_packet_pending",
        lambda: calls.append({}) or {"ok": True, "pending": False},
    )

    result = refresh_redpacket_runtime_snapshot()

    assert result["ok"] is True
    assert calls == [{}]


def test_redpacket_recovery_rebuilds_all_runtime_caches(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.redpacket_state."
        "read_red_packet_pending",
        lambda **kwargs: calls.append(kwargs) or {"ok": True},
    )

    result = recover_redpacket_runtime_snapshot()

    assert result["ok"] is True
    assert calls == [{
        "allow_discovery": True,
        "allow_runtime_initialization": False,
        "unavailable_cache_ttl_seconds": 0.0,
        "chat_only": True,
    }]


def test_inspection_schedules_one_bottom_runtime_recovery(tmp_path, monkeypatch):
    from backend.core.fanxiu.data_annotation import game_state_inspection

    threads = []

    class FakeThread:
        def __init__(self, *, target, args, name, daemon):
            self.target = target
            self.args = args
            self.name = name
            self.daemon = daemon
            self.started = False
            threads.append(self)

        def is_alive(self):
            return self.started

        def start(self):
            self.started = True

    probe = GameStateProbe(
        id="recoverable-runtime",
        label="可恢复 Runtime",
        source="runtime",
        read=lambda: {
            "facts": {"available": False},
            "due_task_ids": [],
            "recovery_required": True,
        },
        recover=lambda: {"ok": True},
    )
    monkeypatch.setattr(
        game_state_inspection,
        "_inspection_recovery_allowed",
        lambda: (True, ""),
    )
    monkeypatch.setattr(game_state_inspection.threading, "Thread", FakeThread)
    monkeypatch.setattr(game_state_inspection, "_recovery_states", {})

    first = inspect_game_state_once(
        probes=[probe],
        state_path=tmp_path / "first.json",
        asynchronous_recovery=True,
    )
    second = inspect_game_state_once(
        probes=[probe],
        state_path=tmp_path / "second.json",
        asynchronous_recovery=True,
    )

    assert len(threads) == 1
    assert first["recoveries"]["recoverable-runtime"]["status"] == "recovering"
    assert second["recoveries"]["recoverable-runtime"]["status"] == "recovering"


def test_inspection_rereads_after_synchronous_recovery_and_triggers_same_patrol(tmp_path, monkeypatch):
    from backend.core.fanxiu.data_annotation import game_state_inspection

    monkeypatch.setattr(
        game_state_inspection,
        "_inspection_recovery_allowed",
        lambda: (True, ""),
    )
    reads = iter([
        {
            "ok": False,
            "message": "红包地址缓存尚未预热",
            "facts": {"pending_count": 0},
            "due_task_ids": [],
            "recovery_required": True,
        },
        {
            "ok": True,
            "facts": {"pending_count": 1},
            "due_task_ids": ["daily-redpacket"],
            "recovery_required": False,
        },
    ])
    due_calls = []
    probe = GameStateProbe(
        id="red-packet",
        label="红包",
        source="runtime",
        read=lambda: next(reads),
        recover=lambda: {"ok": True},
    )

    snapshot = inspect_game_state_once(
        probes=[probe],
        due_sink=lambda task_id, _due_at: due_calls.append(task_id),
        state_path=tmp_path / "inspection.json",
    )

    assert snapshot["status"] == "running"
    assert snapshot["last_result"] == "success"
    assert snapshot["recoveries"]["red-packet"]["status"] == "recovered"
    assert snapshot["facts"]["red-packet"]["pending_count"] == 1
    assert due_calls == ["daily-redpacket"]


def test_synchronous_recovery_obeys_the_same_production_runway(monkeypatch, tmp_path):
    from backend.core.fanxiu.data_annotation import game_state_inspection

    monkeypatch.setattr(
        game_state_inspection,
        "_inspection_recovery_allowed",
        lambda: (False, "两分钟内有到期作业：生产作业"),
    )
    monkeypatch.setattr(game_state_inspection, "_recovery_states", {})
    calls = []
    probe = GameStateProbe(
        id="sync-gated",
        label="同步恢复",
        source="runtime",
        read=lambda: {
            "ok": False,
            "facts": {},
            "due_task_ids": [],
            "recovery_required": True,
        },
        recover=lambda: calls.append("recover") or {"ok": True},
    )

    snapshot = inspect_game_state_once(
        probes=[probe],
        state_path=tmp_path / "inspection.json",
        asynchronous_recovery=False,
    )

    assert calls == []
    assert snapshot["recoveries"]["sync-gated"]["status"] == "deferred"
    assert "两分钟内有到期作业" in snapshot["recoveries"]["sync-gated"]["deferred_reason"]


def test_inspection_reports_unavailable_runtime_probe_as_error(tmp_path):
    probe = GameStateProbe(
        id="red-packet",
        label="红包",
        source="runtime",
        read=lambda: {
            "ok": False,
            "message": "红包 Runtime 不可用",
            "facts": {"pending_count": 0},
            "due_task_ids": [],
            "recovery_required": True,
        },
    )

    snapshot = inspect_game_state_once(
        probes=[probe],
        state_path=tmp_path / "inspection.json",
    )

    assert snapshot["status"] == "error"
    assert snapshot["last_result"] == "error"
    assert snapshot["errors"] == [{
        "probe_id": "red-packet",
        "message": "红包 Runtime 不可用",
    }]
    assert snapshot["last_message"] == "巡检异常：red-packet：红包 Runtime 不可用"


def test_redpacket_probe_only_marks_job_due(monkeypatch):
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.redpacket_state.read_current_redpacket_state",
        lambda: {
            "ok": True,
            "available": True,
            "pending": True,
            "pending_count": 1,
            "trigger_ready": True,
            "sources": {"chat": {"pending_count": 1}},
            "pending_groups": [{
                "channel": 6,
                "channel_key": "ALLIANCE",
                "channel_label": "宗门",
                "sub_channel_id": 0,
                "group_key": "6_0",
                "target_name": "万妖谷",
                "display_name": "宗门 / 万妖谷",
                "pending_count": 1,
            }],
        },
    )
    result = inspect_redpacket_game_state()

    assert result["due_task_ids"] == ["daily-redpacket"]
    assert result["facts"]["red_packet"]["pending_groups"][0]["target_name"] == "万妖谷"


def test_redpacket_probe_does_not_schedule_chat_job_for_npc_only_marker(monkeypatch):
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.redpacket_state.read_current_redpacket_state",
        lambda: {
            "ok": True,
            "available": True,
            "pending": True,
            "pending_count": 2,
            "sources": {
                "chat": {"pending_count": 0},
                "npc": {"pending_count": 2},
            },
        },
    )

    result = inspect_redpacket_game_state()

    assert result["due_task_ids"] == []


def test_redpacket_probe_does_not_suppress_chat_fact_with_empty_main_ui_queue(monkeypatch):
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.redpacket_state.read_current_redpacket_state",
        lambda: {
            "ok": True,
            "available": True,
            "pending": True,
            "pending_count": 1,
            "trigger_ready": True,
            "sources": {
                "chat": {
                    "pending_count": 1,
                    # MainUI's transient display queue is not authoritative
                    # negative evidence for the passive chat fact.
                    "main_ui_queue_count": 0,
                },
            },
        },
    )

    result = inspect_redpacket_game_state()

    assert result["due_task_ids"] == ["daily-redpacket"]


def test_redpacket_probe_schedules_live_main_ui_queue(monkeypatch):
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.redpacket_state.read_current_redpacket_state",
        lambda: {
            "ok": True,
            "available": True,
            "pending": True,
            "pending_count": 1,
            "trigger_ready": True,
            "sources": {
                "chat": {
                    "pending_count": 1,
                    "main_ui_queue_count": 1,
                },
            },
        },
    )

    result = inspect_redpacket_game_state()

    assert result["due_task_ids"] == ["daily-redpacket"]


def test_redpacket_probe_does_not_reschedule_rewarded_qmch_terminal(monkeypatch):
    terminal = {
        "uid": 24082878061488473,
        "id": 5022,
        "event_type": 9033,
        "event_key": "qmch_reward",
        "channel": 101,
        "sub_channel_id": 20050134,
        "detail_loaded": True,
        "trigger_candidate": True,
        "exclusion_reasons": ["server_rewarded", "detail_rewarded"],
    }
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.redpacket_state.read_current_redpacket_state",
        lambda: {
            "ok": True,
            "pending": True,
            "trigger_ready": True,
            "sources": {"chat": {"pending_count": 1, "items": [terminal]}},
        },
    )

    result = inspect_redpacket_game_state()

    assert result["due_task_ids"] == []


@pytest.mark.parametrize(
    "extra_item",
    [
        {"uid": 9001, "id": 1001, "channel": 6, "sub_channel_id": 0},
        {
            "uid": 9002,
            "id": 5022,
            "event_type": 9033,
            "event_key": "qmch_reward",
            "channel": 101,
            "sub_channel_id": 20050134,
            "detail_loaded": False,
            "exclusion_reasons": [],
        },
    ],
)
def test_redpacket_probe_rewarded_qmch_does_not_hide_new_candidates(
    monkeypatch,
    extra_item,
):
    terminal = {
        "uid": 24082878061488473,
        "id": 5022,
        "event_type": 9033,
        "event_key": "qmch_reward",
        "channel": 101,
        "sub_channel_id": 20050134,
        "detail_loaded": True,
        "exclusion_reasons": ["server_rewarded"],
    }
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.redpacket_state.read_current_redpacket_state",
        lambda: {
            "ok": True,
            "pending": True,
            "trigger_ready": True,
            "sources": {
                "chat": {
                    "pending_count": 2,
                    "items": [terminal, extra_item],
                }
            },
        },
    )

    result = inspect_redpacket_game_state()

    assert result["due_task_ids"] == ["daily-redpacket"]


def test_redpacket_probe_does_not_schedule_stale_packet_projection(monkeypatch):
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.redpacket_state.read_current_redpacket_state",
        lambda: {
            "ok": True,
            "pending": True,
            "pending_count": 1,
            "trigger_authoritative": False,
            "trigger_reason": "stale_packet_projection_waiting_for_runtime_refresh",
            "sources": {"chat": {"pending_count": 1}},
        },
    )

    result = inspect_redpacket_game_state()

    assert result["due_task_ids"] == []


def test_redpacket_probe_marks_unchanged_pending_set_due_every_time(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.redpacket_state.read_current_redpacket_state",
        lambda: {
            "ok": True,
            "available": True,
            "pending": True,
            "pending_count": 2,
            "trigger_ready": True,
            "sources": {"chat": {"pending_count": 2}},
            "pending_groups": [{
                "group_key": "6_0",
                "target_name": "万妖谷",
                "pending_count": 2,
            }],
        },
    )
    calls: list[str] = []
    probe = GameStateProbe(
        id="red-packet",
        label="红包",
        source="runtime",
        read=inspect_redpacket_game_state,
    )

    inspect_game_state_once(
        probes=[probe],
        due_sink=lambda task_id, _due_at: calls.append(task_id),
        state_path=tmp_path / "first.json",
    )
    inspect_game_state_once(
        probes=[probe],
        due_sink=lambda task_id, _due_at: calls.append(task_id),
        state_path=tmp_path / "second.json",
    )

    assert calls == ["daily-redpacket", "daily-redpacket"]


def test_redpacket_probe_is_registered_as_builtin():
    probes = {probe.id: probe for probe in registered_game_state_probes()}

    assert set(probes) == {"red-packet", "seat-displacement-mail"}
    assert probes["red-packet"].source == "runtime"
    assert probes["red-packet"].recover is recover_redpacket_runtime_snapshot
    assert {probe.source for probe in probes.values()} <= GAME_STATE_INSPECTION_ALLOWED_SOURCES


@pytest.mark.parametrize("clock", [(15, 29, 59), (22, 0, 0), (23, 59, 59)])
def test_seat_mail_probe_does_not_read_outside_daily_window(tmp_path, clock):
    calls = []

    result = inspect_seat_displacement_mail_state(
        at=datetime(2026, 8, 13, *clock),
        state_path=tmp_path / "cursor.json",
        reader=lambda **kwargs: calls.append(kwargs),
    )

    assert calls == []
    assert result["facts"]["status"] == "outside_window"
    assert result["due_task_ids"] == []


def test_seat_mail_probe_baselines_then_triggers_all_affected_seat_jobs(tmp_path):
    state_path = tmp_path / "cursor.json"
    baseline = {
        "ok": True,
        "complete": True,
        "total": 2,
        "head": {"id": "old", "type": 100, "create_time": 1},
        "items": [
            {"id": "old", "type": 100, "create_time": 1},
            {"id": "older", "type": 100, "create_time": 0},
        ],
    }
    changed = {
        "ok": True,
        "complete": True,
        "total": 5,
        "head": {"id": "dong", "type": 67003, "create_time": 4},
        "items": [
            {"id": "dong", "type": 67003, "create_time": 4},
            {"id": "lingmai", "type": 2205, "create_time": 3},
            {"id": "lundao", "type": 2104, "create_time": 2},
            {"id": "old", "type": 100, "create_time": 1},
        ],
    }

    first = inspect_seat_displacement_mail_state(
        at=datetime(2026, 8, 13, 15, 30),
        state_path=state_path,
        reader=lambda **_kwargs: baseline,
        scheduler_reader=lambda: [],
    )
    second = inspect_seat_displacement_mail_state(
        at=datetime(2026, 8, 13, 15, 31),
        state_path=state_path,
        reader=lambda **_kwargs: changed,
        scheduler_reader=lambda: [],
    )
    third = inspect_seat_displacement_mail_state(
        at=datetime(2026, 8, 13, 15, 32),
        state_path=state_path,
        reader=lambda **_kwargs: changed,
        scheduler_reader=lambda: [],
    )

    assert first["facts"]["status"] == "baseline_created"
    assert second["due_task_ids"] == [
        "daily-lundao-seat",
        "daily-lingmai-seat",
        "dongtian-seating",
    ]
    assert second["facts"]["dongtian_downstream"] == "pass"
    assert third["due_task_ids"] == [
        "daily-lundao-seat",
        "daily-lingmai-seat",
        "dongtian-seating",
    ]
    assert third["facts"]["pending_task_ids"] == third["due_task_ids"]


def test_seat_mail_cursor_commits_only_after_due_sink_succeeds(tmp_path):
    state_path = tmp_path / "cursor.json"
    state_path.write_text(json.dumps({"head_id": "old"}), encoding="utf-8")

    def reader(**_kwargs):
        return {
            "ok": True,
            "complete": True,
            "total": 2,
            "head": {"id": "new", "type": 67004, "create_time": 2},
            "items": [
                {"id": "new", "type": 67004, "create_time": 2},
                {"id": "old", "type": 100, "create_time": 1},
            ],
        }

    probe = GameStateProbe(
        id="seat-test",
        label="座位邮件",
        source="runtime",
        read=lambda: inspect_seat_displacement_mail_state(
            at=datetime(2026, 8, 13, 16, 0),
            state_path=state_path,
            reader=reader,
            scheduler_reader=lambda: [],
            defer_cursor_commit=True,
        ),
    )
    failed = inspect_game_state_once(
        probes=[probe],
        due_sink=lambda *_args: (_ for _ in ()).throw(RuntimeError("disk busy")),
        state_path=tmp_path / "inspection-failed.json",
    )
    assert failed["status"] == "error"
    assert json.loads(state_path.read_text(encoding="utf-8"))["head_id"] == "old"

    calls = []
    succeeded = inspect_game_state_once(
        probes=[probe],
        due_sink=lambda task_id, _due_at: calls.append(task_id),
        state_path=tmp_path / "inspection-success.json",
    )
    assert succeeded["status"] == "running"
    assert calls == ["dongtian-seating"]
    assert json.loads(state_path.read_text(encoding="utf-8"))["head_id"] == "new"


def test_seat_mail_probe_rebases_missing_cursor_without_replaying_history(tmp_path):
    state_path = tmp_path / "cursor.json"
    state_path.write_text(
        json.dumps({"head_id": "missing", "mail_total": 80}),
        encoding="utf-8",
    )

    result = inspect_seat_displacement_mail_state(
        at=datetime(2026, 8, 13, 16, 0),
        state_path=state_path,
        scheduler_reader=lambda: [],
        reader=lambda **_kwargs: {
            "ok": True,
            "complete": True,
            "total": 100,
            "head": {"id": "new", "type": 2101, "create_time": 10},
            "items": [{"id": "new", "type": 2101, "create_time": 10}],
        },
    )

    assert result["facts"]["status"] == "cursor_rebased"
    assert result["due_task_ids"] == []


def test_first_window_patrol_does_not_miss_mail_after_1530(tmp_path):
    at = datetime(2026, 8, 13, 15, 30, 40)
    created_ms = int(datetime(2026, 8, 13, 15, 30, 10).timestamp() * 1000)

    result = inspect_seat_displacement_mail_state(
        at=at,
        state_path=tmp_path / "cursor.json",
        scheduler_reader=lambda: [],
        reader=lambda **_kwargs: {
            "ok": True,
            "complete": True,
            "total": 1,
            "head": {"id": "new", "type": 2101, "create_time": created_ms},
            "items": [{"id": "new", "type": 2101, "create_time": created_ms}],
        },
    )

    assert result["facts"]["status"] == "new_mail"
    assert result["due_task_ids"] == ["daily-lundao-seat"]


def test_seat_mail_pending_event_clears_only_after_later_formal_success(tmp_path):
    state_path = tmp_path / "cursor.json"
    event_time = int(datetime(2026, 8, 13, 16, 0, 0).timestamp() * 1000)
    snapshot = {
        "ok": True,
        "complete": True,
        "total": 1,
        "head": {"id": "kicked", "type": 2213, "create_time": event_time},
        "items": [{"id": "kicked", "type": 2213, "create_time": event_time}],
    }

    first = inspect_seat_displacement_mail_state(
        at=datetime(2026, 8, 13, 16, 1),
        state_path=state_path,
        reader=lambda **_kwargs: snapshot,
        scheduler_reader=lambda: [{
            "id": "daily-lingmai-seat",
            "last_result": "success",
            "last_run_at": "2026-08-13 15:45:00",
        }],
    )
    still_pending = inspect_seat_displacement_mail_state(
        at=datetime(2026, 8, 13, 16, 2),
        state_path=state_path,
        reader=lambda **_kwargs: snapshot,
        scheduler_reader=lambda: [{
            "id": "daily-lingmai-seat",
            "last_result": "error",
            "last_run_at": "2026-08-13 16:01:30",
        }],
    )
    completed = inspect_seat_displacement_mail_state(
        at=datetime(2026, 8, 13, 16, 3),
        state_path=state_path,
        reader=lambda **_kwargs: snapshot,
        scheduler_reader=lambda: [{
            "id": "daily-lingmai-seat",
            "last_result": "success",
            "last_run_at": "2026-08-13 16:02:30",
        }],
    )

    assert first["due_task_ids"] == ["daily-lingmai-seat"]
    assert still_pending["due_task_ids"] == ["daily-lingmai-seat"]
    assert completed["due_task_ids"] == []
    assert completed["facts"]["pending_task_ids"] == []


def test_seat_mail_runtime_unavailable_is_an_explicit_probe_error(tmp_path):
    result = inspect_seat_displacement_mail_state(
        at=datetime(2026, 8, 13, 16, 0),
        state_path=tmp_path / "cursor.json",
        reader=lambda **_kwargs: {
            "ok": False,
            "complete": False,
            "reason": "stale Lua table",
        },
        scheduler_reader=lambda: [],
    )

    assert result["ok"] is False
    assert result["recovery_required"] is False
    assert "stale Lua table" in result["message"]


def test_mail_header_reader_rebinds_after_stale_runtime_mapping(monkeypatch):
    memories = [object(), object()]
    force_rebinds = []

    def retry(reader):
        try:
            return reader(memories[0], False)
        except FanxiuRuntimeMemoryError:
            return reader(memories[1], True)

    monkeypatch.setattr(mail_instrumentation, "read_runtime_snapshot_with_rebind", retry)
    monkeypatch.setattr(mail_instrumentation, "_lua_addresses", lambda _memory: {"state": "0x10"})

    def resolve(memory, **kwargs):
        force_rebinds.append((memory, kwargs["force_refresh"]))
        if not kwargs["force_refresh"]:
            raise FanxiuRuntimeMemoryError("stale mapping")
        return 0x20, False, 0x30

    monkeypatch.setattr(mail_instrumentation, "resolve_lua_global_manager_root", resolve)
    monkeypatch.setattr(
        mail_instrumentation,
        "_header_snapshot",
        lambda memory, *_args, **_kwargs: {
            "ok": True,
            "complete": True,
            "evidence": {"memory": id(memory)},
        },
    )

    result = mail_instrumentation.read_mail_header_snapshot(limit=24)

    assert result["ok"] is True
    assert result["evidence"]["snapshot_attempts"] == 2
    assert force_rebinds == [(memories[0], False), (memories[1], True)]
