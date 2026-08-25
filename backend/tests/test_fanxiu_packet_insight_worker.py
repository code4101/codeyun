from __future__ import annotations

import json
import os
import struct
import threading
import time
from pathlib import Path

import pytest

from backend.core.fanxiu.history_museum.packet_capture import insight_worker as worker


_REAL_VALIDATE_SEALED_PCAP = worker._validate_sealed_pcap


@pytest.fixture(autouse=True)
def _no_host_commit_pressure(monkeypatch, request):
    if request.node.name == "test_packet_decode_pressure_thresholds_match_ocr_guard":
        return
    monkeypatch.setattr(worker, "_host_commit_pressure_for_packet_decode", lambda: {"skip": False})
    monkeypatch.setattr(
        worker,
        "_ensure_capture_runtime_from_packet_worker",
        lambda **_kwargs: {"ok": True, "ensured": False, "reason": "test_disabled"},
    )
    monkeypatch.setattr(worker, "_validate_sealed_pcap", lambda _path: (True, "valid"))


def test_validate_sealed_pcap_rejects_corrupt_record_boundary(tmp_path):
    pcap = tmp_path / "corrupt.pcap"
    global_header = bytes.fromhex("d4c3b2a10200040000000000000000000000040001000000")
    valid_record = struct.pack("<IIII", 1, 2, 4, 4) + b"data"
    corrupt_record = struct.pack("<IIII", 3, 4, 0xFFFF_FFFF, 4)
    pcap.write_bytes(global_header + valid_record + corrupt_record)

    valid, reason = _REAL_VALIDATE_SEALED_PCAP(pcap)

    assert valid is False
    assert reason.startswith("invalid_record_length_at_")


def test_validate_sealed_pcap_accepts_complete_records(tmp_path):
    pcap = tmp_path / "valid.pcap"
    global_header = bytes.fromhex("d4c3b2a10200040000000000000000000000040001000000")
    record = struct.pack("<IIII", 1, 2, 4, 4) + b"data"
    pcap.write_bytes(global_header + record)

    assert _REAL_VALIDATE_SEALED_PCAP(pcap) == (True, "valid")


def test_capture_paths_redecodes_when_digest_has_missing_target_stream(monkeypatch, tmp_path):
    pcap = tmp_path / "capture.pcap"
    pcap.write_bytes(b"x" * 128)
    calls: list[tuple[Path, int]] = []

    monkeypatch.setattr(worker, "_decoded_capture_index", lambda _data_dir=None: ({"digest-1"}, {"digest-1": {0}}, {"digest-1": []}))
    monkeypatch.setattr(worker, "_sha256_file", lambda _path: "digest-1")
    monkeypatch.setattr(worker, "_target_stream_ids_for_pcap", lambda _path, *, max_streams: [0, 1])
    monkeypatch.setattr(worker, "_sync_business_after_decoded", lambda decoded, data_dir=None: ({}, {"ok": True}))
    monkeypatch.setattr(worker, "_sync_decoded_record_db_after_decoded", lambda decoded: {"ok": True})

    def fake_decode(path, *, data_dir=None, max_streams=2, sync_business=False):
        calls.append((Path(path), max_streams))
        return {
            "decoded_count": 2,
            "runtime_protocol_count": 0,
            "worship_protocol_count": 0,
            "decoded": [
                {"stream": 0, "output_path": str(tmp_path / "stream0.json"), "record_id": "r0"},
                {"stream": 1, "output_path": str(tmp_path / "stream1.json"), "record_id": "r1"},
            ],
        }

    monkeypatch.setattr(worker, "decode_and_sync_fanxiu_runtime_capture", fake_decode)

    result = worker.sync_fanxiu_capture_paths([pcap], max_streams=2)

    assert calls == [(pcap.resolve(), 2)]
    assert result["new_decode_count"] == 1
    assert result["skipped_count"] == 0
    assert result["decoded"][0]["decoded_sources"][1]["stream"] == 1


def test_capture_paths_records_actual_decoded_streams(monkeypatch, tmp_path):
    pcap = tmp_path / "capture.pcap"
    pcap.write_bytes(b"x" * 128)

    monkeypatch.setattr(worker, "_decoded_capture_index", lambda _data_dir=None: (set(), {}, {}))
    monkeypatch.setattr(worker, "_sha256_file", lambda _path: "digest-1")
    monkeypatch.setattr(worker, "_target_stream_ids_for_pcap", lambda _path, *, max_streams: [0, 1])
    monkeypatch.setattr(worker, "_sync_business_after_decoded", lambda decoded, data_dir=None: ({}, {"ok": True}))
    monkeypatch.setattr(worker, "_sync_decoded_record_db_after_decoded", lambda decoded: {"ok": True})
    monkeypatch.setattr(
        worker,
        "decode_and_sync_fanxiu_runtime_capture",
        lambda *_args, **_kwargs: {
            "decoded_count": 1,
            "runtime_protocol_count": 0,
            "worship_protocol_count": 0,
            "decoded": [{"stream": 0, "output_path": str(tmp_path / "stream0.json"), "record_id": "r0"}],
        },
    )

    result = worker.sync_fanxiu_capture_paths([pcap], max_streams=2)

    assert result["decoded"][0]["target_stream_ids"] == [0, 1]
    assert result["decoded"][0]["decoded_stream_ids"] == [0]
    assert result["decoded"][0]["missing_stream_ids"] == [1]


def test_capture_paths_skips_only_when_target_streams_are_decoded(monkeypatch, tmp_path):
    pcap = tmp_path / "capture.pcap"
    pcap.write_bytes(b"x" * 128)
    calls: list[Path] = []

    monkeypatch.setattr(
        worker,
        "_decoded_capture_index",
        lambda _data_dir=None: (
            {"digest-1"},
            {"digest-1": {0, 1}},
            {"digest-1": [{"decoded_path": "stream0.json", "stream": 0}]},
        ),
    )
    monkeypatch.setattr(worker, "_sha256_file", lambda _path: "digest-1")
    monkeypatch.setattr(worker, "_target_stream_ids_for_pcap", lambda _path, *, max_streams: [0, 1])
    monkeypatch.setattr(worker, "_sync_business_after_decoded", lambda decoded, data_dir=None: ({}, {"ok": True}))
    monkeypatch.setattr(worker, "_sync_decoded_record_db_after_decoded", lambda decoded: {"ok": True})
    monkeypatch.setattr(worker, "decode_and_sync_fanxiu_runtime_capture", lambda path, **kwargs: calls.append(Path(path)))

    result = worker.sync_fanxiu_capture_paths([pcap], max_streams=2)

    assert calls == []
    assert result["new_decode_count"] == 0
    assert result["business_backfill_count"] == 1
    assert result["skipped"][0]["reason"] == "already_decoded"
    assert result["skipped"][0]["target_stream_ids"] == [0, 1]
    assert result["skipped"][0]["decoded_stream_ids"] == [0, 1]


def test_capture_paths_skips_decode_under_host_commit_pressure(monkeypatch, tmp_path):
    pcap = tmp_path / "capture.pcap"
    pcap.write_bytes(b"x" * 128)
    calls: list[Path] = []

    monkeypatch.setattr(
        worker,
        "_host_commit_pressure_for_packet_decode",
        lambda: {"skip": True, "reason": "host_commit_pressure", "commit": {"commit_percent": 94.0}},
    )
    monkeypatch.setattr(
        worker,
        "decode_and_sync_fanxiu_runtime_capture",
        lambda path, **kwargs: calls.append(Path(path)) or {"decoded_count": 0, "runtime_protocol_count": 0, "worship_protocol_count": 0, "decoded": []},
    )
    monkeypatch.setattr(worker, "_target_stream_ids_for_pcap", lambda _path, *, max_streams: [])
    monkeypatch.setattr(worker, "_sync_decoded_record_db_after_decoded", lambda decoded: {"ok": True})

    result = worker.sync_fanxiu_capture_paths([pcap], max_streams=2)

    assert calls == [pcap.resolve()]
    assert result["ok"] is True
    assert result["new_decode_count"] == 1


def test_incremental_decoded_batch_persists_exact_sources(monkeypatch, tmp_path):
    decoded_path = tmp_path / "decoded.json"
    decoded_path.write_text(
        json.dumps(
            {
                "stream": 2,
                "frames": [
                    {
                        "direction": "s2c",
                        "offset": 12,
                        "sn": 7,
                        "pro_id": 59518,
                        "name": "SM_SeatsNoInScene",
                        "parsed": {"roomId": 15},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    persisted: list[dict[str, object]] = []

    def fake_persist(result):
        persisted.append(result)
        return {"created": 1, "updated": 0, "skipped_invalid": 0, "skipped_duplicate": 0}

    monkeypatch.setattr(worker, "persist_fanxiu_packet_decoded_result", fake_persist)

    result = worker._sync_decoded_record_db_after_decoded(
        [
            {
                "decoded_sources": [
                    {
                        "decoded_path": str(decoded_path),
                        "record_id": "record-1",
                        "pcap_name": "capture.pcap",
                        "capture_sha256": "sha-1",
                        "created_at": "2026-07-20 14:26:00",
                        "stream": 2,
                    }
                ]
            }
        ]
    )

    assert result["ok"] is True
    assert result["source_count"] == 1
    assert result["persisted_source_count"] == 1
    assert result["created"] == 1
    assert persisted[0]["capture_sha256"] == "sha-1"
    assert persisted[0]["frames"][0]["name"] == "SM_SeatsNoInScene"


def test_capture_paths_reports_database_write_failure(monkeypatch, tmp_path):
    pcap = tmp_path / "capture.pcap"
    decoded_path = tmp_path / "decoded.json"
    pcap.write_bytes(b"x" * 128)
    decoded_path.write_text(json.dumps({"stream": 0, "frames": []}), encoding="utf-8")
    monkeypatch.setattr(worker, "_sha256_file", lambda _path: "digest-1")
    monkeypatch.setattr(worker, "_target_stream_ids_for_pcap", lambda _path, *, max_streams: [0])
    monkeypatch.setattr(
        worker,
        "decode_and_sync_fanxiu_runtime_capture",
        lambda *_args, **_kwargs: {
            "decoded_count": 1,
            "decoded": [{"stream": 0, "output_path": str(decoded_path), "record_id": "r0"}],
        },
    )
    monkeypatch.setattr(
        worker,
        "_sync_decoded_record_db_after_decoded",
        lambda decoded: {"ok": False, "source_count": 1, "persisted_source_count": 0, "errors": [{"error": "db locked"}]},
    )
    monkeypatch.setattr(worker, "_sync_business_after_decoded", lambda decoded, data_dir=None: ({"ok": True}, {"ok": True}))

    result = worker.sync_fanxiu_capture_paths([pcap], scan_existing_decoded=False)

    assert result["ok"] is False
    assert result["decoded_record_db_sync"]["errors"][0]["error"] == "db locked"


def test_same_capture_decode_artifact_is_never_written_concurrently(monkeypatch, tmp_path):
    pcap = tmp_path / "capture.pcap"
    pcap.write_bytes(b"x" * 128)
    active = 0
    max_active = 0
    entered = threading.Event()
    release = threading.Event()
    state_lock = threading.Lock()

    monkeypatch.setattr(worker, "_sha256_file", lambda _path: "same-digest")
    monkeypatch.setattr(worker, "_target_stream_ids_for_pcap", lambda _path, *, max_streams: [0])
    monkeypatch.setattr(worker, "_sync_decoded_record_db_after_decoded", lambda decoded: {"ok": True})
    monkeypatch.setattr(worker, "_sync_business_after_decoded", lambda decoded, data_dir=None: ({"ok": True}, {"ok": True}))

    def fake_decode(*_args, **_kwargs):
        nonlocal active, max_active
        with state_lock:
            active += 1
            max_active = max(max_active, active)
            entered.set()
        release.wait(timeout=2)
        with state_lock:
            active -= 1
        return {"decoded_count": 1, "decoded": [{"stream": 0, "record_id": "r0"}]}

    monkeypatch.setattr(worker, "_decode_runtime_capture_with_timeout", fake_decode)
    results: list[dict[str, object]] = []

    def run_once():
        results.append(worker.sync_fanxiu_capture_paths([pcap], scan_existing_decoded=False))

    first = threading.Thread(target=run_once)
    second = threading.Thread(target=run_once)
    first.start()
    assert entered.wait(timeout=1)
    second.start()
    time.sleep(0.05)
    assert max_active == 1
    release.set()
    first.join(timeout=2)
    second.join(timeout=2)

    assert len(results) == 2
    assert max_active == 1


def test_catch_up_covers_flushed_segment_and_recent_boundary(monkeypatch, tmp_path):
    from backend.core.fanxiu.history_museum.packet_capture import capture_runtime

    boundary = tmp_path / "boundary.pcap"
    flushed = tmp_path / "flushed.pcap"
    boundary.write_bytes(b"b" * 2048)
    flushed.write_bytes(b"f" * 24)
    selected: list[list[str]] = []
    monkeypatch.setattr(worker, "_latest_recent_sealed_live_pcap", lambda **_kwargs: boundary)
    monkeypatch.setattr(worker, "_capture_has_current_decoded_record", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(worker, "_ensure_capture_runtime_from_packet_worker", lambda **_kwargs: {"ok": True})
    monkeypatch.setattr(
        capture_runtime.fanxiu_capture_runtime_service,
        "flush_recent_capture",
        lambda *_args, **_kwargs: {
            "ok": True,
            "flushed": False,
            "pcap_path": str(flushed),
            "pcap_size": 24,
        },
    )

    def fake_sync(paths, **_kwargs):
        selected.append([str(item) for item in paths])
        return {
            "ok": True,
            "decoded_record_db_sync": {"ok": True, "source_count": 1, "persisted_source_count": 1},
        }

    monkeypatch.setattr(worker, "sync_fanxiu_capture_paths", fake_sync)

    result = worker.catch_up_fanxiu_packet_facts(reason="test", restart_capture=False)

    assert result["ok"] is True
    assert result["caught_up"] is True
    assert selected == [[str(flushed), str(boundary)]]
    assert result["capture_candidates"] == [
        {"path": str(flushed), "size": 24, "selected": True},
        {"path": str(boundary), "size": 2048, "selected": True},
    ]


def test_packet_decode_pressure_thresholds_match_ocr_guard(monkeypatch):
    from backend.core.fanxiu.runtime import mumu_control

    monkeypatch.delenv(worker.PACKET_DECODE_ALLOW_UNDER_COMMIT_PRESSURE_ENV, raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)

    monkeypatch.setattr(
        mumu_control,
        "_collect_windows_commit_snapshot",
        lambda: {"commit_percent": 90.0, "commit_available_mb": 40000},
    )
    assert worker._host_commit_pressure_for_packet_decode()["skip"] is True

    monkeypatch.setattr(
        mumu_control,
        "_collect_windows_commit_snapshot",
        lambda: {"commit_percent": 70.0, "commit_available_mb": (8 * 1024) - 1},
    )
    assert worker._host_commit_pressure_for_packet_decode()["skip"] is True

    monkeypatch.setattr(
        mumu_control,
        "_collect_windows_commit_snapshot",
        lambda: {"commit_percent": 70.0, "commit_available_mb": 8 * 1024},
    )
    assert worker._host_commit_pressure_for_packet_decode()["skip"] is False


def test_decode_max_streams_default_matches_runtime_flush_budget():
    assert worker.DEFAULT_DECODE_MAX_STREAMS == 4


def test_maintenance_once_runs_historical_business_backlog(monkeypatch):
    calls: list[dict[str, object]] = []

    def fake_maintenance(**kwargs):
        calls.append(kwargs)
        return {"ok": True}

    monkeypatch.setattr(worker, "sync_fanxiu_capture_maintenance_backlog", fake_maintenance)

    service = worker.FanxiuPacketInsightWorker(stable_seconds=1)
    result = service.maintenance_once()

    assert result["ok"] is True
    assert result["capture_runtime_backstop"]["reason"] == "test_disabled"
    assert calls
    assert calls[0]["include_historical_business_backlog"] is True
    assert calls[0]["include_mail_business_backlog"] is False


def test_maintenance_once_reports_stage_heartbeat(monkeypatch):
    captured_progress: list[dict[str, object]] = []
    state_dir = Path(os.getenv("TEMP", str(Path.cwd()))) / f"fanxiu-maintenance-heartbeat-{time.time_ns()}"
    monkeypatch.setenv("CODEYUN_DATA_DIR", str(state_dir))
    state_path = state_dir / "fanxiu" / "packet-insights" / "maintenance_worker_state.json"
    monkeypatch.setattr(worker, "_maintenance_state_path", lambda _data_dir=None: state_path)

    def fake_maintenance(**kwargs):
        progress_callback = kwargs.get("progress_callback")
        assert callable(progress_callback)
        progress_callback({"phase": "historical_business_backlog", "historical_runtime_business_sync": {"ok": True}})
        captured_progress.append(kwargs)
        return {"ok": True, "updated_at": "2026-07-10 16:10:00"}

    monkeypatch.setattr(worker, "sync_fanxiu_capture_maintenance_backlog", fake_maintenance)

    service = worker.FanxiuPacketInsightWorker(stable_seconds=1)
    result = service.maintenance_once()
    written_state = json.loads(state_path.read_text(encoding="utf-8"))

    assert result["ok"] is True
    assert captured_progress
    assert written_state["phase"] == "historical_business_backlog"
    assert written_state["heartbeat_at"]
    assert written_state["capture_runtime_backstop"]["reason"] == "test_disabled"


def test_historical_business_backlog_does_not_rebuild_full_json_snapshots(monkeypatch):
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        worker,
        "sync_fanxiu_decoded_business_backlog",
        lambda **kwargs: calls.append(kwargs) or {"ok": True, "changed": True, "source_count": 12},
    )

    runtime_sync, mail_sync = worker._sync_historical_business_backlog()

    assert runtime_sync["ok"] is True
    assert runtime_sync["changed"] is True
    assert runtime_sync["packet_runtime_sync"] == {
        "ok": True,
        "skipped": True,
        "reason": "database_incremental_facts_are_authoritative",
    }
    assert runtime_sync["player_profile_sync"] == {
        "ok": True,
        "skipped": True,
        "reason": "database_incremental_facts_are_authoritative",
    }
    assert calls == [
        {
            "data_dir": None,
            "latest_limit": worker.DEFAULT_MAINTENANCE_BUSINESS_LATEST_LIMIT,
            "historical_limit": worker.DEFAULT_MAINTENANCE_BUSINESS_HISTORICAL_LIMIT,
        }
    ]
    assert mail_sync == {
        "ok": True,
        "skipped": True,
        "reason": "handled_once_by_bounded_mail_phase",
    }


def test_maintenance_backlog_uses_per_capture_decoded_lookup(monkeypatch):
    calls: list[dict[str, object]] = []
    mail_calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        worker,
        "sync_fanxiu_live_capture_backlog",
        lambda **kwargs: calls.append(kwargs) or {"ok": True},
    )
    monkeypatch.setattr(worker, "_stale_decoder_evidence_pcaps", lambda *_args, **_kwargs: ([], 0, 0))
    monkeypatch.setattr(
        worker,
        "_sync_historical_business_backlog",
        lambda **_kwargs: ({"ok": True}, {"ok": True}),
    )
    monkeypatch.setattr(
        worker,
        "sync_fanxiu_mail_business_backlog",
        lambda **kwargs: mail_calls.append(kwargs) or {"ok": True},
    )
    monkeypatch.setattr(worker, "_prune_decoded_record_db_cache", lambda: {"ok": True})

    result = worker.sync_fanxiu_capture_maintenance_backlog()

    assert result["ok"] is True
    assert calls[0]["scan_existing_decoded"] is False
    assert calls[0]["newest_first"] is True
    assert mail_calls == []
    assert result["bounded_mail_packet_sync"]["skipped"] is True


def test_realtime_scan_uses_cursor_and_small_batch(monkeypatch):
    calls: list[dict[str, object]] = []

    def fake_live_backlog(**kwargs):
        calls.append(kwargs)
        return {"ok": True}

    monkeypatch.setattr(worker, "sync_fanxiu_live_capture_backlog", fake_live_backlog)

    service = worker.FanxiuPacketInsightWorker(stable_seconds=1)
    result = service.scan_once()

    assert result["ok"] is True
    assert calls
    assert calls[0]["stable_seconds"] == 1
    assert calls[0]["use_cursor"] is True
    assert calls[0]["newest_first"] is True
    assert calls[0]["limit"] == 2
    assert calls[0]["scan_existing_decoded"] is False
    assert calls[0]["decode_timeout_seconds"] == worker.DEFAULT_REALTIME_DECODE_TIMEOUT_SECONDS
    assert calls[0]["max_decode_attempts"] == 2
    assert result["decoded_record_db_sync"]["skipped"] is True
    assert result["activity_packet_sync"]["skipped"] is True
    assert result["capture_runtime_backstop"]["reason"] == "test_disabled"


def test_realtime_scan_skips_while_an_on_demand_ingestion_owns_writer(monkeypatch):
    service = worker.FanxiuPacketInsightWorker(stable_seconds=1)
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(worker, "sync_fanxiu_live_capture_backlog", lambda **kwargs: calls.append(kwargs) or {"ok": True})
    assert service._foreground_lock.acquire(blocking=False) is True
    try:
        result = service.scan_once()
    finally:
        service._foreground_lock.release()

    assert result["ok"] is True
    assert result["skipped"] is True
    assert result["reason"] == "foreground_cycle_active"
    assert calls == []


def test_catch_up_is_not_blocked_by_historical_maintenance(monkeypatch):
    service = worker.FanxiuPacketInsightWorker(stable_seconds=1)
    monkeypatch.setattr(
        worker,
        "catch_up_fanxiu_packet_facts",
        lambda **kwargs: {
            "ok": True,
            "caught_up": True,
            "reason": kwargs["reason"],
        },
    )

    assert service._maintenance_lock.acquire(blocking=False) is True
    try:
        result = service.catch_up_once(reason="urgent-business-read")
    finally:
        service._maintenance_lock.release()

    assert result == {
        "ok": True,
        "caught_up": True,
        "reason": "urgent-business-read",
    }
    assert result.get("status") != "ingestion_busy"


def test_catch_up_times_out_instead_of_leaking_a_permanent_command_lock():
    service = worker.FanxiuPacketInsightWorker(stable_seconds=1)
    assert service._foreground_lock.acquire(blocking=False) is True
    try:
        result = service.catch_up_once(
            reason="busy-realtime-probe",
            foreground_wait_seconds=0.01,
        )
    finally:
        service._foreground_lock.release()

    assert result["ok"] is False
    assert result["status"] == "ingestion_busy"
    assert result["reason"] == "foreground_cycle_timeout"
    assert service._catch_up_waiting.is_set() is False


def test_realtime_scan_runs_capture_runtime_backstop(monkeypatch):
    service = worker.FanxiuPacketInsightWorker(stable_seconds=1)
    calls: list[dict[str, object]] = []

    monkeypatch.setattr(worker, "sync_fanxiu_live_capture_backlog", lambda **_kwargs: {"ok": True})
    monkeypatch.setattr(worker, "sync_fanxiu_mail_business_backlog", lambda **_kwargs: {"ok": True})

    def fake_backstop(**kwargs):
        calls.append(kwargs)
        return {"ok": True, "ensured": True, "reason": "packet-worker-backstop"}

    monkeypatch.setattr(worker, "_ensure_capture_runtime_from_packet_worker", fake_backstop)

    result = service.scan_once()

    assert calls == [{}]
    assert result["capture_runtime_backstop"]["ensured"] is True
    assert result["capture_runtime_backstop"]["reason"] == "packet-worker-backstop"


def test_worker_start_writes_capture_runtime_backstop_state(monkeypatch, tmp_path):
    state_path = tmp_path / "live_capture_worker_state.json"
    monkeypatch.setattr(worker, "_worker_state_path", lambda: state_path)
    monkeypatch.setattr(
        worker,
        "_ensure_capture_runtime_from_packet_worker",
        lambda: {"ok": True, "ensured": True, "reason": "packet-worker-backstop"},
    )

    class FakeThread:
        def __init__(self, **_kwargs):
            pass

        def is_alive(self):
            return False

        def start(self):
            pass

    monkeypatch.setattr(worker.threading, "Thread", FakeThread)

    service = worker.FanxiuPacketInsightWorker(stable_seconds=1)
    service.start()

    payload = worker._load_json(state_path, {})
    assert payload["mode"] == "packet_worker_startup"
    assert payload["capture_runtime_backstop"]["reason"] == "packet-worker-backstop"


def test_worker_start_preserves_persistent_realtime_cursor(monkeypatch, tmp_path):
    state_path = tmp_path / "live_capture_worker_state.json"
    worker._write_json(
        state_path,
        {
            "cursor_mtime": 123.0,
            "cursor_pcap": "recent.pcap",
            "confirmed_cursor_mtime": 123.0,
            "confirmed_cursor_pcap": "recent.pcap",
            "pcap_states": [{"path": "recent.pcap", "status": "decoded", "mtime": 123.0}],
        },
    )
    monkeypatch.setattr(worker, "_worker_state_path", lambda: state_path)
    monkeypatch.setattr(worker, "_ensure_capture_runtime_from_packet_worker", lambda: {"ok": True})

    class FakeThread:
        def __init__(self, **_kwargs):
            pass

        def is_alive(self):
            return False

        def start(self):
            pass

    monkeypatch.setattr(worker.threading, "Thread", FakeThread)

    service = worker.FanxiuPacketInsightWorker(stable_seconds=1)
    service.start()

    payload = worker._load_json(state_path, {})
    assert payload["mode"] == "packet_worker_startup"
    assert payload["confirmed_cursor_mtime"] == 123.0
    assert payload["confirmed_cursor_pcap"] == "recent.pcap"
    assert payload["pcap_states"][0]["path"] == "recent.pcap"


def test_live_capture_backlog_skips_decode_under_host_commit_pressure(monkeypatch, tmp_path):
    pcap = tmp_path / "capture.pcap"
    pcap.write_bytes(b"x" * (worker.PACKET_DECODE_COMMIT_PRESSURE_SMALL_INPUT_BYTES + 1))
    monkeypatch.setattr(worker, "_worker_state_path", lambda _data_dir=None: tmp_path / "worker_state.json")
    monkeypatch.setattr(worker, "_decoded_capture_index", lambda _data_dir=None: (set(), {}, {}))
    monkeypatch.setattr(
        worker,
        "_host_commit_pressure_for_packet_decode",
        lambda: {"skip": True, "reason": "host_commit_pressure", "commit": {"commit_available_mb": 4096}},
    )
    monkeypatch.setattr(worker, "_iter_stable_live_pcaps", lambda **_kwargs: [pcap])

    result = worker.sync_fanxiu_live_capture_backlog(data_dir=tmp_path)

    assert result["ok"] is True
    assert result["skipped"] is True
    assert result["skip_reason"] == "host_commit_pressure"
    assert result["decode_attempts"] == 0
    assert result["host_commit_pressure"]["commit"]["commit_available_mb"] == 4096


def test_live_capture_backlog_allows_small_batch_under_host_commit_pressure(monkeypatch, tmp_path):
    pcap = tmp_path / "capture.pcap"
    pcap.write_bytes(b"x" * 128)
    state_path = tmp_path / "worker_state.json"
    calls: list[Path] = []

    monkeypatch.setattr(worker, "_worker_state_path", lambda _data_dir=None: state_path)
    monkeypatch.setattr(worker, "_decoded_capture_index", lambda _data_dir=None: (set(), {}, {}))
    monkeypatch.setattr(
        worker,
        "_host_commit_pressure_for_packet_decode",
        lambda: {"skip": True, "reason": "host_commit_pressure", "commit": {"commit_available_mb": 4096}},
    )
    monkeypatch.setattr(worker, "_iter_stable_live_pcaps", lambda **_kwargs: [pcap])
    monkeypatch.setattr(worker, "_sha256_file", lambda _path: "digest-1")
    monkeypatch.setattr(worker, "_target_stream_ids_for_pcap", lambda _path, *, max_streams: [0])
    monkeypatch.setattr(worker, "_sync_business_after_decoded", lambda decoded, data_dir=None: ({}, {"ok": True}))
    monkeypatch.setattr(
        worker,
        "_decode_runtime_capture_with_timeout",
        lambda path, **_kwargs: calls.append(Path(path)) or {"decoded_count": 0, "runtime_protocol_count": 0, "worship_protocol_count": 0, "decoded": []},
    )

    result = worker.sync_fanxiu_live_capture_backlog(data_dir=tmp_path)

    assert calls == [pcap]
    assert result.get("skip_reason") is None


def test_live_capture_backlog_prunes_resolved_previous_errors(monkeypatch, tmp_path):
    pcap = tmp_path / "capture.pcap"
    pcap.write_bytes(b"x" * 128)
    state_path = tmp_path / "worker_state.json"
    state_path.write_text(
        json.dumps(
            {
                "confirmed_cursor_mtime": 0,
                "errors": [
                    {
                        "path": str(pcap),
                        "digest": "digest-1",
                        "error": "old error",
                        "attempts": 1,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(worker, "_worker_state_path", lambda _data_dir=None: state_path)
    monkeypatch.setattr(
        worker,
        "_decoded_capture_index",
        lambda _data_dir=None: (
            {"digest-1"},
            {"digest-1": {0}},
            {"digest-1": [{"decoded_path": "stream0.json", "stream": 0}]},
        ),
    )
    monkeypatch.setattr(
        worker,
        "_host_commit_pressure_for_packet_decode",
        lambda: {"skip": True, "reason": "host_commit_pressure", "commit": {"commit_available_mb": 4096}},
    )
    monkeypatch.setattr(worker, "_iter_stable_live_pcaps", lambda **_kwargs: [])

    result = worker.sync_fanxiu_live_capture_backlog(data_dir=tmp_path)

    assert result["known_error_count"] == 0
    assert result["has_unconfirmed_gap"] is False
    assert result["errors"] == []


def test_realtime_loop_scans_before_wait(monkeypatch):
    service = worker.FanxiuPacketInsightWorker(scan_interval_seconds=60, stable_seconds=1)
    calls = 0

    def fake_scan_once():
        nonlocal calls
        calls += 1
        service._stop_event.set()
        return {"ok": True}

    monkeypatch.setattr(service, "scan_once", fake_scan_once)

    service._run_loop()

    assert calls == 1


def test_packet_background_defaults_are_low_frequency_and_bounded():
    assert worker.DEFAULT_SCAN_INTERVAL_SECONDS == 60.0
    assert worker.DEFAULT_MAINTENANCE_INTERVAL_SECONDS == 6 * 60 * 60.0
    assert worker.DEFAULT_MAINTENANCE_DECODE_LIMIT == 2
    assert worker.DEFAULT_MAINTENANCE_BUSINESS_LATEST_LIMIT == 2
    assert worker.DEFAULT_MAINTENANCE_BUSINESS_HISTORICAL_LIMIT == 2


def test_scheduled_maintenance_defers_while_user_is_active(monkeypatch):
    monkeypatch.setattr(worker, "_windows_user_idle_seconds", lambda: 12.0)
    monkeypatch.setattr(worker.psutil, "cpu_percent", lambda **_kwargs: 0.0)

    gate = worker._scheduled_maintenance_resource_gate()

    assert gate["defer"] is True
    assert gate["reason"] == "user_active"


def test_scheduled_maintenance_defers_under_cpu_pressure(monkeypatch):
    monkeypatch.setattr(worker, "_windows_user_idle_seconds", lambda: 600.0)
    monkeypatch.setattr(worker.psutil, "cpu_percent", lambda **_kwargs: 75.0)

    gate = worker._scheduled_maintenance_resource_gate()

    assert gate["defer"] is True
    assert gate["reason"] == "host_cpu_busy"


def test_realtime_scan_leaves_historical_mail_backlog_to_maintenance(monkeypatch):
    service = worker.FanxiuPacketInsightWorker(maintenance_interval_seconds=60, stable_seconds=1)
    calls: list[dict[str, int]] = []

    monkeypatch.setattr(worker, "sync_fanxiu_live_capture_backlog", lambda **_kwargs: {"ok": True})

    def fake_mail_backlog(**kwargs):
        calls.append(kwargs)
        return {"ok": True, "selected_count": 1}

    monkeypatch.setattr(worker, "sync_fanxiu_mail_business_backlog", fake_mail_backlog)

    result = service.scan_once()

    assert calls == []
    assert result["mail_business_backlog_sync"] == {
        "ok": True,
        "skipped": True,
        "reason": "historical_mail_backlog_runs_in_maintenance",
    }


def test_realtime_and_maintenance_do_not_compete_with_waiting_catch_up(monkeypatch):
    service = worker.FanxiuPacketInsightWorker(maintenance_interval_seconds=60, stable_seconds=1)
    realtime_calls: list[dict[str, object]] = []
    maintenance_calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        worker,
        "sync_fanxiu_live_capture_backlog",
        lambda **kwargs: realtime_calls.append(kwargs) or {"ok": True},
    )
    monkeypatch.setattr(
        worker,
        "sync_fanxiu_capture_maintenance_backlog",
        lambda **kwargs: maintenance_calls.append(kwargs) or {"ok": True},
    )

    service._catch_up_waiting.set()
    realtime = service.scan_once()
    maintenance = service.maintenance_once()

    assert realtime["reason"] == "packet_catch_up_pending"
    assert maintenance["reason"] == "packet_catch_up_pending"
    assert realtime_calls == []
    assert maintenance_calls == []


def test_realtime_scan_never_runs_historical_maintenance_inline(monkeypatch):
    service = worker.FanxiuPacketInsightWorker(scan_interval_seconds=15, maintenance_interval_seconds=600, stable_seconds=1)
    maintenance_calls = 0

    monkeypatch.setattr(
        worker,
        "sync_fanxiu_live_capture_backlog",
        lambda **_kwargs: {"ok": True, "has_unconfirmed_gap": True, "latest_scanned_mtime": 100.0},
    )
    monkeypatch.setattr(worker, "sync_fanxiu_mail_business_backlog", lambda **_kwargs: {"ok": True})
    monkeypatch.setattr(worker, "latest_fanxiu_live_capture_summary", lambda: {"latest_mtime": 400.0})

    def fake_maintenance_once():
        nonlocal maintenance_calls
        maintenance_calls += 1
        return {"ok": True, "mode": "maintenance", "updated_at": "2026-07-11 05:40:00"}

    monkeypatch.setattr(service, "maintenance_once", fake_maintenance_once)

    result = service.scan_once()

    assert maintenance_calls == 0
    assert result["maintenance_handoff"] == {
        "triggered": False,
        "reason": "maintenance_runs_on_independent_scheduler",
    }


def test_mail_source_protocol_probe_distinguishes_source_and_action_packets(tmp_path):
    mailbox_path = tmp_path / "mailbox.json"
    mailbox_path.write_text(
        json.dumps(
            {
                "frames": [
                    {"name": "SM_MailBox"},
                    {"name": "CM_DeleteMail"},
                ]
            }
        ),
        encoding="utf-8",
    )
    action_path = tmp_path / "action.json"
    action_path.write_text(json.dumps({"frames": [{"name": "SM_DeleteMail"}]}), encoding="utf-8")

    probe = worker._mail_source_protocol_probe(
        [
            {"decoded_path": str(mailbox_path), "record_id": "mailbox-record", "pcap_name": "mailbox.pcap"},
            {"decoded_path": str(action_path), "record_id": "action-record", "pcap_name": "action.pcap"},
        ]
    )

    assert probe["has_mailbox_source"] is True
    assert probe["has_any_mail_source"] is True
    assert probe["has_mail_action"] is True
    assert probe["protocol_counts"] == {"SM_MailBox": 1, "CM_DeleteMail": 1, "SM_DeleteMail": 1}
    assert probe["source_samples"][0]["protocol"] == "SM_MailBox"
    assert probe["action_samples"][0]["protocol"] == "CM_DeleteMail"


def test_mail_business_backlog_records_mail_source_probe(monkeypatch, tmp_path):
    decoded_path = tmp_path / "decoded.json"
    decoded_path.write_text(json.dumps({"frames": [{"name": "SM_DeleteMail"}]}), encoding="utf-8")
    sources = [{"decoded_path": str(decoded_path), "record_id": "r1", "stream": 0}]
    state_path = tmp_path / "mail_business_backlog_state.json"

    monkeypatch.setattr(worker, "_iter_fanxiu_tcp_decoded_sources", lambda _data_dir=None: sources)
    monkeypatch.setattr(worker, "_mail_business_backlog_state_path", lambda _data_dir=None: state_path)

    class FakeSession:
        def __init__(self, _engine):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr("sqlmodel.Session", FakeSession)
    monkeypatch.setattr("backend.core.fanxiu.history_museum.packet_capture.mail_sync.sync_fanxiu_mail_packets", lambda *_args, **_kwargs: {"ok": True})

    result = worker.sync_fanxiu_mail_business_backlog(latest_limit=1, historical_limit=0)

    assert result["skipped"] is True
    assert result["reason"] == "mail_runtime_memory_is_authoritative"


def test_decoded_business_backlog_skips_completed_legacy_state_without_enumerating(monkeypatch, tmp_path):
    state_path = tmp_path / "decoded_business_backlog_state.json"
    state_path.write_text(
        json.dumps(
            {
                "business_rule_version": worker.PACKET_BUSINESS_RULE_VERSION,
                "source_count": 36048,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(worker, "_decoded_business_backlog_state_path", lambda _data_dir=None: state_path)
    monkeypatch.setattr(
        worker,
        "_iter_fanxiu_tcp_decoded_sources",
        lambda _data_dir=None: pytest.fail("completed replay must not enumerate decoded history"),
    )

    result = worker.sync_fanxiu_decoded_business_backlog()

    assert result["ok"] is True
    assert result["skipped"] is True
    assert result["reason"] == "historical_replay_complete"
    assert result["source_count"] == 36048


def test_mail_business_backlog_skips_completed_legacy_state_without_enumerating(monkeypatch, tmp_path):
    state_path = tmp_path / "mail_business_backlog_state.json"
    state_path.write_text(
        json.dumps(
            {
                "business_rule_version": worker.PACKET_BUSINESS_RULE_VERSION,
                "source_count": 36048,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(worker, "_mail_business_backlog_state_path", lambda _data_dir=None: state_path)
    monkeypatch.setattr(
        worker,
        "_iter_fanxiu_tcp_decoded_sources",
        lambda _data_dir=None: pytest.fail("completed replay must not enumerate decoded history"),
    )

    result = worker.sync_fanxiu_mail_business_backlog()

    assert result["ok"] is True
    assert result["skipped"] is True
    assert result["reason"] == "mail_runtime_memory_is_authoritative"
    assert result["source_count"] == 0


def test_maintenance_loop_waits_before_first_scan(monkeypatch):
    service = worker.FanxiuPacketInsightWorker(maintenance_interval_seconds=60, stable_seconds=1)
    calls = 0
    waits: list[float] = []

    def fake_maintenance_once():
        nonlocal calls
        calls += 1
        service._maintenance_stop_event.set()
        return {"ok": True}

    monkeypatch.setattr(service, "maintenance_once", fake_maintenance_once)
    monkeypatch.setattr(
        service._maintenance_stop_event,
        "wait",
        lambda seconds: waits.append(seconds) or True,
    )

    service._maintenance_loop()

    assert calls == 0
    assert waits == [60.0]


def test_mail_business_backlog_processes_bounded_latest_and_historical_sources(monkeypatch, tmp_path):
    sources = [
        {
            "decoded_path": str(tmp_path / f"decoded-{index}.json"),
            "record_id": f"r{index}",
            "stream": 0,
        }
        for index in range(10)
    ]
    state_path = tmp_path / "mail_business_backlog_state.json"
    selected_keys: list[str] = []

    monkeypatch.setattr(worker, "_iter_fanxiu_tcp_decoded_sources", lambda _data_dir=None: sources)
    monkeypatch.setattr(worker, "_mail_business_backlog_state_path", lambda _data_dir=None: state_path)

    class FakeSession:
        def __init__(self, _engine):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def fake_sync(_session, *, decoded_sources, **_kwargs):
        selected_keys.extend(worker._decoded_source_key(source) for source in decoded_sources)
        return {"ok": True, "record_count": len(decoded_sources)}

    monkeypatch.setattr("sqlmodel.Session", FakeSession)
    monkeypatch.setattr("backend.core.fanxiu.history_museum.packet_capture.mail_sync.sync_fanxiu_mail_packets", fake_sync)

    result = worker.sync_fanxiu_mail_business_backlog(latest_limit=2, historical_limit=3)

    assert result["selected_count"] == 0
    assert result["reason"] == "mail_runtime_memory_is_authoritative"
    assert selected_keys == []


def test_batch_business_sync_passes_non_profile_runtime_protocols(monkeypatch):
    seen_names: list[str] = []

    monkeypatch.setattr(
        worker,
        "_decode_result_from_source",
        lambda _source: {
            "record_id": "wallet-record",
            "frames": [{"name": "SM_Wallet", "content": {"items": []}}],
        },
    )

    def fake_business_sync(result, **_kwargs):
        seen_names.extend(str(frame.get("name") or "") for frame in result.get("frames") or [])
        return {"ok": True, "changed": True}

    monkeypatch.setattr(worker, "sync_fanxiu_packet_business_for_decode_result", fake_business_sync)

    class FakeSession:
        def __init__(self, _engine):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr("sqlmodel.Session", FakeSession)
    monkeypatch.setattr("backend.core.fanxiu.history_museum.packet_capture.mail_sync.sync_fanxiu_mail_packets", lambda *_args, **_kwargs: {"ok": True})

    runtime_sync, mail_sync = worker._sync_business_after_decoded(
        [{"decoded_sources": [{"decoded_path": "decoded.json", "record_id": "wallet-record", "stream": 0}]}]
    )

    assert seen_names == ["SM_Wallet"]
    assert runtime_sync["source_count"] == 1
    assert runtime_sync["sync_count"] == 1
    assert runtime_sync["changed"] is True
    assert mail_sync["ok"] is True
    assert mail_sync["reason"] == "mail_runtime_memory_is_authoritative"


def test_live_capture_backlog_caps_single_pass_scan_window(monkeypatch, tmp_path):
    live_dir = tmp_path / "live-captures"
    live_dir.mkdir()
    for index in range(250):
        path = live_dir / f"fanxiu_runtime_20260608_{index:06d}.pcap"
        path.write_bytes(b"x" * 128)

    scanned: list[Path] = []

    monkeypatch.setattr(worker, "resolve_fanxiu_tcp_live_capture_dir", lambda _data_dir=None: live_dir)
    monkeypatch.setattr(worker, "resolve_fanxiu_tcp_store_root", lambda _data_dir=None: tmp_path / "tcp-flow")
    monkeypatch.setattr(worker, "_decoded_capture_index", lambda _data_dir=None: (set(), {}, {}))
    monkeypatch.setattr(worker, "_sha256_file", lambda path: f"digest:{Path(path).name}")
    monkeypatch.setattr(worker, "_target_stream_ids_for_pcap", lambda path, *, max_streams: scanned.append(Path(path)) or [0])
    monkeypatch.setattr(worker, "_sync_business_after_decoded", lambda decoded, data_dir=None: ({}, {"ok": True}))

    def fake_decode(*_args, **_kwargs):
        raise RuntimeError("force bounded failures")

    monkeypatch.setattr(worker, "decode_and_sync_fanxiu_runtime_capture", fake_decode)

    result = worker.sync_fanxiu_live_capture_backlog(
        data_dir=tmp_path,
        stable_seconds=1,
        retry_failed_after_seconds=0,
        max_capture_age_seconds=None,
        use_cursor=False,
        limit=2,
    )

    assert result["scanned"] <= 2 * worker.DEFAULT_LIVE_CAPTURE_SCAN_MULTIPLIER
    assert len(scanned) <= 2 * worker.DEFAULT_LIVE_CAPTURE_SCAN_MULTIPLIER


def test_live_capture_backlog_respects_decode_attempt_and_timeout_budget(monkeypatch, tmp_path):
    live_dir = tmp_path / "live-captures"
    live_dir.mkdir()
    for index in range(6):
        path = live_dir / f"fanxiu_runtime_20260608_{index:06d}.pcap"
        path.write_bytes(b"x" * 128)
        old_time = time.time() - 10 - index
        os.utime(path, (old_time, old_time))

    decode_timeouts: list[float] = []
    monkeypatch.setattr(worker, "resolve_fanxiu_tcp_live_capture_dir", lambda _data_dir=None: live_dir)
    monkeypatch.setattr(worker, "resolve_fanxiu_tcp_store_root", lambda _data_dir=None: tmp_path / "tcp-flow")
    monkeypatch.setattr(worker, "_decoded_capture_index", lambda _data_dir=None: (set(), {}, {}))
    monkeypatch.setattr(worker, "_sha256_file", lambda path: f"digest:{Path(path).name}")
    monkeypatch.setattr(worker, "_target_stream_ids_for_pcap", lambda _path, *, max_streams: [0])
    monkeypatch.setattr(worker, "_sync_business_after_decoded", lambda decoded, data_dir=None: ({}, {"ok": True}))

    def fail_decode(*_args, timeout_seconds, **_kwargs):
        decode_timeouts.append(timeout_seconds)
        raise TimeoutError("slow decode")

    monkeypatch.setattr(worker, "_decode_runtime_capture_with_timeout", fail_decode)

    result = worker.sync_fanxiu_live_capture_backlog(
        data_dir=tmp_path,
        stable_seconds=1,
        retry_failed_after_seconds=0,
        max_capture_age_seconds=None,
        use_cursor=False,
        limit=2,
        decode_timeout_seconds=7,
        max_decode_attempts=2,
    )

    assert result["decode_attempts"] == 2
    assert decode_timeouts == [7.0, 7.0]


def test_newest_first_interleaves_oldest_gap_in_same_pass():
    paths = [Path(f"{index}.pcap") for index in range(6, 0, -1)]

    assert worker._interleave_newest_and_oldest(paths) == [
        Path("6.pcap"),
        Path("1.pcap"),
        Path("5.pcap"),
        Path("2.pcap"),
        Path("4.pcap"),
        Path("3.pcap"),
    ]


def test_pcap_state_compaction_keeps_oldest_cursor_evidence_and_newest_health():
    updates = [
        {"path": f"{index}.pcap", "digest": f"d{index}", "mtime": float(index), "status": "decoded"}
        for index in range(10)
    ]

    compacted = worker._merge_pcap_states({}, updates, limit=4)

    assert {item["digest"] for item in compacted} == {"d0", "d1", "d8", "d9"}


def test_live_capture_backlog_skips_pcaps_without_target_stream(monkeypatch, tmp_path):
    live_dir = tmp_path / "live-captures"
    live_dir.mkdir()
    pcap = live_dir / "fanxiu_runtime_20260608_131252.pcap"
    pcap.write_bytes(b"x" * 128)
    old_time = time.time() - 10
    os.utime(pcap, (old_time, old_time))
    decode_calls: list[Path] = []

    monkeypatch.setattr(worker, "resolve_fanxiu_tcp_live_capture_dir", lambda _data_dir=None: live_dir)
    monkeypatch.setattr(worker, "resolve_fanxiu_tcp_store_root", lambda _data_dir=None: tmp_path / "tcp-flow")
    monkeypatch.setattr(worker, "_decoded_capture_index", lambda _data_dir=None: (set(), {}, {}))
    monkeypatch.setattr(worker, "_sha256_file", lambda path: f"digest:{Path(path).name}")
    monkeypatch.setattr(worker, "_target_stream_ids_for_pcap", lambda path, *, max_streams: [])
    monkeypatch.setattr(worker, "_sync_business_after_decoded", lambda decoded, data_dir=None: ({}, {"ok": True}))
    monkeypatch.setattr(worker, "decode_and_sync_fanxiu_runtime_capture", lambda path, **_kwargs: decode_calls.append(Path(path)))

    result = worker.sync_fanxiu_live_capture_backlog(
        data_dir=tmp_path,
        stable_seconds=1,
        retry_failed_after_seconds=0,
        max_capture_age_seconds=None,
        use_cursor=False,
        limit=1,
    )

    assert decode_calls == []
    assert result["decoded_count"] == 0
    assert result["skipped"][0]["reason"] == "no_target_stream"


def test_live_capture_backlog_marks_partial_decode_as_gap(monkeypatch, tmp_path):
    live_dir = tmp_path / "live-captures"
    live_dir.mkdir()
    pcap = live_dir / "fanxiu_runtime_20260608_131252.pcap"
    pcap.write_bytes(b"x" * 128)
    old_time = time.time() - 10
    os.utime(pcap, (old_time, old_time))
    state_path = tmp_path / "worker_state.json"

    monkeypatch.setattr(worker, "_worker_state_path", lambda _data_dir=None: state_path)
    monkeypatch.setattr(worker, "resolve_fanxiu_tcp_live_capture_dir", lambda _data_dir=None: live_dir)
    monkeypatch.setattr(worker, "resolve_fanxiu_tcp_store_root", lambda _data_dir=None: tmp_path / "tcp-flow")
    monkeypatch.setattr(worker, "_decoded_capture_index", lambda _data_dir=None: (set(), {}, {}))
    monkeypatch.setattr(worker, "_sha256_file", lambda path: f"digest:{Path(path).name}")
    monkeypatch.setattr(worker, "_target_stream_ids_for_pcap", lambda path, *, max_streams: [0, 1])
    monkeypatch.setattr(worker, "_sync_business_after_decoded", lambda decoded, data_dir=None: ({}, {"ok": True}))
    monkeypatch.setattr(
        worker,
        "decode_and_sync_fanxiu_runtime_capture",
        lambda *_args, **_kwargs: {
            "decoded_count": 1,
            "runtime_protocol_count": 0,
            "worship_protocol_count": 0,
            "decoded": [{"stream": 0, "output_path": str(tmp_path / "stream0.json"), "record_id": "r0"}],
        },
    )

    result = worker.sync_fanxiu_live_capture_backlog(
        data_dir=tmp_path,
        stable_seconds=1,
        retry_failed_after_seconds=0,
        max_capture_age_seconds=None,
        use_cursor=True,
        limit=1,
    )

    assert result["ok"] is False
    assert result["has_unconfirmed_gap"] is True
    assert result["confirmed_cursor_pcap"] == ""
    assert result["decoded"][0]["missing_stream_ids"] == [1]
    assert result["errors"][0]["missing_stream_ids"] == [1]
    assert result["pcap_states"][0]["status"] == "partial_decoded"


def test_iter_stable_live_pcaps_skips_current_runtime_capture(monkeypatch, tmp_path):
    live_dir = tmp_path / "live-captures"
    live_dir.mkdir()
    active = live_dir / "fanxiu_runtime_active.pcap"
    sealed = live_dir / "fanxiu_runtime_sealed.pcap"
    active.write_bytes(b"x" * 128)
    sealed.write_bytes(b"x" * 128)
    old_time = time.time() - 10
    os.utime(active, (old_time, old_time))
    os.utime(sealed, (old_time, old_time))

    monkeypatch.setattr(worker, "resolve_fanxiu_tcp_live_capture_dir", lambda _data_dir=None: live_dir)
    monkeypatch.setattr(worker, "_current_runtime_capture_path", lambda: active)

    rows = worker._iter_stable_live_pcaps(stable_seconds=1, max_age_seconds=None)

    assert rows == [sealed]


def test_iter_stable_live_pcaps_bounds_stat_window_before_scanning(monkeypatch, tmp_path):
    live_dir = tmp_path / "live-captures"
    live_dir.mkdir()
    paths = []
    for minute in range(10):
        path = live_dir / f"fanxiu_runtime_20260729_10{minute:02d}00.pcap"
        path.write_bytes(b"x" * 64)
        paths.append(path)
    monkeypatch.setattr(worker, "resolve_fanxiu_tcp_live_capture_dir", lambda _data_dir=None: live_dir)
    monkeypatch.setattr(worker, "_current_runtime_capture_path", lambda: None)

    rows = worker._iter_stable_live_pcaps(
        stable_seconds=1,
        max_age_seconds=None,
        newest_first=True,
        candidate_limit=4,
        now=time.time() + 60,
    )

    assert {path.name for path in rows} == {
        paths[0].name,
        paths[1].name,
        paths[-2].name,
        paths[-1].name,
    }


def test_live_capture_backlog_skips_locked_active_capture(monkeypatch, tmp_path):
    live_dir = tmp_path / "live-captures"
    live_dir.mkdir()
    pcap = live_dir / "fanxiu_runtime_locked.pcap"
    pcap.write_bytes(b"x" * 128)
    old_time = time.time() - 10
    os.utime(pcap, (old_time, old_time))

    monkeypatch.setattr(worker, "resolve_fanxiu_tcp_live_capture_dir", lambda _data_dir=None: live_dir)
    monkeypatch.setattr(worker, "resolve_fanxiu_tcp_store_root", lambda _data_dir=None: tmp_path / "tcp-flow")
    monkeypatch.setattr(worker, "_decoded_capture_index", lambda _data_dir=None: (set(), {}, {}))
    monkeypatch.setattr(worker, "_sha256_file", lambda _path: (_ for _ in ()).throw(OSError(32, "另一个程序正在使用此文件")))
    monkeypatch.setattr(worker, "_sync_business_after_decoded", lambda decoded, data_dir=None: ({}, {"ok": True}))

    result = worker.sync_fanxiu_live_capture_backlog(
        data_dir=tmp_path,
        stable_seconds=1,
        retry_failed_after_seconds=0,
        max_capture_age_seconds=None,
        use_cursor=False,
        limit=1,
    )

    assert result["ok"] is True
    assert result["error_count"] == 0
    assert result["skipped"][0]["reason"] == "locked_active_capture"


def test_decode_runtime_capture_with_timeout_raises_quickly(monkeypatch, tmp_path):
    pcap = tmp_path / "slow.pcap"
    pcap.write_bytes(b"x" * 128)

    def slow_decode(*_args, **_kwargs):
        time.sleep(5)
        return {"ok": True}

    monkeypatch.setattr(worker, "decode_and_sync_fanxiu_runtime_capture", slow_decode)

    started = time.time()
    try:
        worker._decode_runtime_capture_with_timeout(pcap, max_streams=1, timeout_seconds=0.1)
    except TimeoutError as exc:
        assert "pcap 解码超时" in str(exc)
    else:
        raise AssertionError("expected timeout")

    assert time.time() - started < 2


def test_worker_status_marks_active_maintenance_without_fake_heartbeat():
    service = worker.FanxiuPacketInsightWorker(scan_interval_seconds=3, maintenance_interval_seconds=60, stable_seconds=1)
    service._last_realtime_result = {"ok": True, "updated_at": "2026-07-02 15:00:00"}
    service._last_maintenance_result = {"ok": True, "updated_at": "2026-07-02 11:55:49", "decoded": list(range(20))}
    service._maintenance_cycle_started_at = time.time() - 5

    status = service.status()

    assert status["maintenance"]["active"] is True
    assert status["maintenance"]["started_at"]
    assert "heartbeat_at" not in status["maintenance"]
    assert status["maintenance"]["decoded_count"] == 20
    assert len(status["maintenance"]["decoded"]) == 5


def test_worker_status_preserves_active_realtime_heartbeat():
    service = worker.FanxiuPacketInsightWorker(scan_interval_seconds=3, maintenance_interval_seconds=60, stable_seconds=1)
    service._last_realtime_result = {
        "ok": True,
        "updated_at": "2026-07-06 20:35:50",
        "heartbeat_at": "2026-07-06 20:36:05",
        "phase": "mail_business_backlog",
    }
    service._realtime_cycle_started_at = time.time() - 5

    status = service.status()

    assert status["realtime"]["active"] is True
    assert status["realtime"]["started_at"]
    assert status["realtime"]["heartbeat_at"] == "2026-07-06 20:36:05"
    assert status["realtime"]["phase"] == "mail_business_backlog"
