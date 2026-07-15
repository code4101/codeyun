import time
from types import SimpleNamespace

from backend.core.fanxiu.packet import service_runtime


def test_packet_service_state_read_prefers_newer_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(service_runtime, "get_settings", lambda: SimpleNamespace(data_dir=tmp_path))
    state_path = service_runtime.get_fanxiu_packet_service_state_path()
    snapshot_dir = service_runtime.get_fanxiu_packet_service_state_snapshot_dir()

    service_runtime._write_json(state_path, {"updated_at": "2026-07-10 04:00:00", "packet_worker": {"ok": False}})
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = snapshot_dir / "packet_service_state.20260710_040500.test.json"
    snapshot_path.write_text('{"updated_at": "2026-07-10 04:05:00", "packet_worker": {"ok": true}}', encoding="utf-8")

    payload = service_runtime._read_packet_service_state_json(state_path)

    assert payload["updated_at"] == "2026-07-10 04:05:00"
    assert payload["packet_worker"]["ok"] is True


def test_packet_service_state_write_falls_back_to_direct_write_when_atomic_replace_is_blocked(tmp_path, monkeypatch):
    monkeypatch.setattr(service_runtime, "get_settings", lambda: SimpleNamespace(data_dir=tmp_path))
    state_path = service_runtime.get_fanxiu_packet_service_state_path()
    original_write_json = service_runtime._write_json

    def fake_write_json(path, payload):
        if path == state_path:
            raise PermissionError("locked")
        return original_write_json(path, payload)

    monkeypatch.setattr(service_runtime, "_write_json", fake_write_json)

    service_runtime._write_packet_service_state_json(state_path, {"updated_at": "2026-07-10 04:10:00", "ok": True})
    snapshot_dir = service_runtime.get_fanxiu_packet_service_state_snapshot_dir()
    snapshots = list(snapshot_dir.glob("*.json"))

    assert state_path.exists()
    assert len(snapshots) == 1
    assert service_runtime._read_json(state_path, {})["updated_at"] == "2026-07-10 04:10:00"
    assert service_runtime._read_packet_service_state_json(state_path)["updated_at"] == "2026-07-10 04:10:00"


def test_packet_service_command_processes_catch_up(tmp_path, monkeypatch):
    monkeypatch.setattr(service_runtime, "get_settings", lambda: SimpleNamespace(data_dir=tmp_path))

    calls: list[str] = []

    class FakeWorker:
        def catch_up_once(self, *, reason: str = "manual"):
            calls.append(reason)
            return {"ok": True, "mode": "packet_facts_catch_up", "reason": reason}

    monkeypatch.setattr(service_runtime, "fanxiu_packet_insight_worker", FakeWorker())

    command_path = service_runtime._packet_service_command_path("cmd-1")
    service_runtime._write_json(
        command_path,
        {
            "schema_version": service_runtime.FANXIU_PACKET_SERVICE_COMMAND_SCHEMA_VERSION,
            "command_id": "cmd-1",
            "action": "packet_facts_catch_up",
            "reason": "unit-test",
        },
    )

    processed = service_runtime.process_pending_fanxiu_packet_service_commands()

    result_path = service_runtime._packet_service_result_path("cmd-1")
    result = service_runtime._read_json(result_path, {})
    assert calls == ["unit-test"]
    assert len(processed) == 1
    assert processed[0]["ok"] is True
    assert result["result"]["mode"] == "packet_facts_catch_up"
    assert not command_path.exists()


def test_packet_service_command_times_out_and_clears_stuck_request(tmp_path, monkeypatch):
    monkeypatch.setattr(service_runtime, "get_settings", lambda: SimpleNamespace(data_dir=tmp_path))
    monkeypatch.setattr(service_runtime, "_packet_service_command_timeout_seconds", lambda: 1.0)
    monkeypatch.setattr(service_runtime, "_packet_service_action_timeout_seconds", lambda _action: 1.0)

    class FakeWorker:
        def maintenance_once(self):
            time.sleep(1.2)
            return {"ok": True}

    monkeypatch.setattr(service_runtime, "fanxiu_packet_insight_worker", FakeWorker())

    command_path = service_runtime._packet_service_command_path("cmd-timeout")
    service_runtime._write_json(
        command_path,
        {
            "schema_version": service_runtime.FANXIU_PACKET_SERVICE_COMMAND_SCHEMA_VERSION,
            "command_id": "cmd-timeout",
            "action": "maintenance",
            "reason": "unit-timeout",
        },
    )

    processed = service_runtime.process_pending_fanxiu_packet_service_commands()

    result_path = service_runtime._packet_service_result_path("cmd-timeout")
    result = service_runtime._read_json(result_path, {})
    assert len(processed) == 1
    assert processed[0]["ok"] is False
    assert processed[0]["timed_out"] is True
    assert result["timed_out"] is True
    assert not command_path.exists()


def test_packet_service_rejects_overlapping_command_threads(monkeypatch):
    acquired = service_runtime._PACKET_SERVICE_REALTIME_COMMAND_ACTION_LOCK.acquire(blocking=False)
    assert acquired is True
    try:
        result = service_runtime._run_packet_service_command_action("packet_facts_catch_up", reason="unit-test")
    finally:
        service_runtime._PACKET_SERVICE_REALTIME_COMMAND_ACTION_LOCK.release()

    assert result["ok"] is False
    assert result["reason"] == "packet_service_command_already_running"


def test_stuck_maintenance_command_does_not_block_realtime_catch_up(monkeypatch):
    class FakeWorker:
        def catch_up_once(self, *, reason: str):
            return {"ok": True, "reason": reason}

    monkeypatch.setattr(service_runtime, "fanxiu_packet_insight_worker", FakeWorker())
    maintenance_lock = service_runtime.threading.Lock()
    assert maintenance_lock.acquire(blocking=False) is True
    monkeypatch.setattr(service_runtime, "_PACKET_SERVICE_MAINTENANCE_COMMAND_ACTION_LOCK", maintenance_lock)
    try:
        result = service_runtime._run_packet_service_command_action("packet_facts_catch_up", reason="urgent")
    finally:
        maintenance_lock.release()

    assert result == {"ok": True, "reason": "urgent"}


def test_submit_packet_service_command_returns_pending_when_daemon_has_not_processed(tmp_path, monkeypatch):
    monkeypatch.setattr(service_runtime, "get_settings", lambda: SimpleNamespace(data_dir=tmp_path))

    result = service_runtime.request_fanxiu_packet_service_catch_up(reason="unit-test", wait_seconds=0)

    assert result["ok"] is False
    assert result["status"] == "pending"
    assert result["action"] == "packet_facts_catch_up"
    assert service_runtime._packet_service_command_path(result["command_id"]).is_file()


def test_packet_worker_catch_up_api_delegates_to_service(monkeypatch):
    from backend.api import fanxiu as fanxiu_api

    monkeypatch.setattr(fanxiu_api, "ensure_fanxiu_write_permission", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(fanxiu_api, "start_fanxiu_packet_service", lambda: {"status": "started"})
    monkeypatch.setattr(
        fanxiu_api,
        "request_fanxiu_packet_service_catch_up",
        lambda **kwargs: {
            "ok": True,
            "status": "completed",
            "action": "packet_facts_catch_up",
            "kwargs": kwargs,
        },
    )
    monkeypatch.setattr(fanxiu_api, "get_fanxiu_packet_daemon_worker_status", lambda: {"running": True})

    result = fanxiu_api.run_fanxiu_packet_worker_catch_up(
        reason="api-test",
        wait_seconds=5,
        current_user=object(),
        session=object(),
    )

    assert result["status"] == "completed"
    assert result["action"] == "packet-facts-catch-up"
    assert result["command"]["kwargs"] == {"reason": "api-test", "wait_seconds": 5}
    assert result["worker"] == {"running": True}


def test_packet_decoded_records_api_delegates_to_store(monkeypatch):
    from backend.api import fanxiu as fanxiu_api

    monkeypatch.setattr(fanxiu_api, "ensure_fanxiu_write_permission", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        fanxiu_api,
        "list_fanxiu_packet_decoded_records",
        lambda session, **kwargs: {"ok": True, "session": session, "kwargs": kwargs},
    )

    session = object()
    result = fanxiu_api.list_fanxiu_packet_decoded_records_api(
        names=["SM_XianLvMineEnterSync"],
        pro_ids=[95102],
        since_seconds=60,
        limit=5,
        current_user=object(),
        session=session,
    )

    assert result["ok"] is True
    assert result["session"] is session
    assert result["kwargs"] == {
        "names": ["SM_XianLvMineEnterSync"],
        "pro_ids": [95102],
        "since_seconds": 60,
        "limit": 5,
    }


def test_packet_decoded_records_prune_api_delegates_to_store(monkeypatch):
    from backend.api import fanxiu as fanxiu_api

    monkeypatch.setattr(fanxiu_api, "ensure_fanxiu_write_permission", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        fanxiu_api,
        "prune_fanxiu_packet_decoded_records",
        lambda session, **kwargs: {"ok": True, "session": session, "kwargs": kwargs},
    )

    session = object()
    result = fanxiu_api.prune_fanxiu_packet_decoded_records_api(
        max_age_seconds=3600,
        min_keep=10,
        current_user=object(),
        session=session,
    )

    assert result["ok"] is True
    assert result["session"] is session
    assert result["kwargs"] == {"max_age_seconds": 3600, "min_keep": 10}


def test_packet_decoded_records_catch_up_api_updates_then_queries(monkeypatch):
    from backend.api import fanxiu as fanxiu_api

    monkeypatch.setattr(fanxiu_api, "ensure_fanxiu_write_permission", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        fanxiu_api,
        "catch_up_and_list_fanxiu_packet_decoded_records",
        lambda session, **kwargs: {"ok": True, "session": session, "kwargs": kwargs},
    )

    session = object()
    result = fanxiu_api.catch_up_fanxiu_packet_decoded_records_api(
        names=["SM_XianLvMineEnterSync"],
        pro_ids=[95102],
        since_seconds=120,
        limit=8,
        reason="unit-test",
        wait_seconds=6,
        current_user=object(),
        session=session,
    )

    assert result["ok"] is True
    assert result["session"] is session
    assert result["kwargs"] == {
        "names": ["SM_XianLvMineEnterSync"],
        "pro_ids": [95102],
        "since_seconds": 120,
        "limit": 8,
        "reason": "unit-test",
        "wait_seconds": 6,
    }


def test_core_packet_current_facts_helper_updates_then_queries(monkeypatch):
    from backend.core.fanxiu.packet import current_facts

    calls: list[str] = []

    def fake_start():
        calls.append("start")
        return {"status": "started"}

    def fake_catch_up(**kwargs):
        calls.append("catch_up")
        return {"ok": True, "status": "completed", "kwargs": kwargs}

    def fake_list(session, **kwargs):
        calls.append("list")
        return {"ok": True, "count": 1, "session": session, "kwargs": kwargs}

    monkeypatch.setattr(current_facts, "start_fanxiu_packet_service", fake_start)
    monkeypatch.setattr(current_facts, "request_fanxiu_packet_service_catch_up", fake_catch_up)
    monkeypatch.setattr(current_facts, "list_fanxiu_packet_decoded_records", fake_list)
    monkeypatch.setattr(current_facts, "get_fanxiu_packet_worker_status", lambda: {"running": True})

    session = object()
    result = current_facts.catch_up_and_list_fanxiu_packet_decoded_records(
        session,
        names=["SM_XianLvMineEnterSync"],
        pro_ids=[95102],
        since_seconds=120,
        limit=8,
        reason="unit-test",
        wait_seconds=6,
    )

    assert calls == ["start", "catch_up", "list"]
    assert result["ok"] is True
    assert result["status"] == "completed"
    assert result["catch_up"]["kwargs"] == {"reason": "unit-test", "wait_seconds": 6}
    assert result["decoded_records"]["kwargs"] == {
        "names": ["SM_XianLvMineEnterSync"],
        "pro_ids": [95102],
        "since_seconds": 120,
        "limit": 8,
    }
