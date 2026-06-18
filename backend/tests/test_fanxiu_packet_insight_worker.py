from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from backend.core.fanxiu.packet import insight_worker as worker


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


def test_capture_paths_redecodes_when_digest_has_missing_target_stream(monkeypatch, tmp_path):
    pcap = tmp_path / "capture.pcap"
    pcap.write_bytes(b"x" * 128)
    calls: list[tuple[Path, int]] = []

    monkeypatch.setattr(worker, "_decoded_capture_digests", lambda _data_dir=None: {"digest-1"})
    monkeypatch.setattr(worker, "_decoded_capture_streams_by_digest", lambda _data_dir=None: {"digest-1": {0}})
    monkeypatch.setattr(worker, "_decoded_capture_sources_by_digest", lambda _data_dir=None: {"digest-1": []})
    monkeypatch.setattr(worker, "_sha256_file", lambda _path: "digest-1")
    monkeypatch.setattr(worker, "_target_stream_ids_for_pcap", lambda _path, *, max_streams: [0, 1])
    monkeypatch.setattr(worker, "_sync_business_after_decoded", lambda decoded, data_dir=None: ({}, {"ok": True}))

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


def test_capture_paths_skips_only_when_target_streams_are_decoded(monkeypatch, tmp_path):
    pcap = tmp_path / "capture.pcap"
    pcap.write_bytes(b"x" * 128)
    calls: list[Path] = []

    monkeypatch.setattr(worker, "_decoded_capture_digests", lambda _data_dir=None: {"digest-1"})
    monkeypatch.setattr(worker, "_decoded_capture_streams_by_digest", lambda _data_dir=None: {"digest-1": {0, 1}})
    monkeypatch.setattr(
        worker,
        "_decoded_capture_sources_by_digest",
        lambda _data_dir=None: {"digest-1": [{"decoded_path": "stream0.json", "stream": 0}]},
    )
    monkeypatch.setattr(worker, "_sha256_file", lambda _path: "digest-1")
    monkeypatch.setattr(worker, "_target_stream_ids_for_pcap", lambda _path, *, max_streams: [0, 1])
    monkeypatch.setattr(worker, "_sync_business_after_decoded", lambda decoded, data_dir=None: ({}, {"ok": True}))
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
    monkeypatch.setattr(worker, "decode_and_sync_fanxiu_runtime_capture", lambda path, **kwargs: calls.append(Path(path)))

    result = worker.sync_fanxiu_capture_paths([pcap], max_streams=2)

    assert calls == []
    assert result["ok"] is True
    assert result["skipped_count"] == 1
    assert result["skipped"][0]["reason"] == "host_commit_pressure"
    assert result["host_commit_pressure"]["commit"]["commit_percent"] == 94.0


def test_packet_decode_pressure_thresholds_match_ocr_guard(monkeypatch):
    from backend.core.fanxiu.runtime import mumu_control

    monkeypatch.delenv(worker.PACKET_DECODE_ALLOW_UNDER_COMMIT_PRESSURE_ENV, raising=False)

    monkeypatch.setattr(
        mumu_control,
        "_collect_windows_commit_snapshot",
        lambda: {"commit_percent": 75.0, "commit_available_mb": 40000},
    )
    assert worker._host_commit_pressure_for_packet_decode()["skip"] is True

    monkeypatch.setattr(
        mumu_control,
        "_collect_windows_commit_snapshot",
        lambda: {"commit_percent": 70.0, "commit_available_mb": (24 * 1024) - 1},
    )
    assert worker._host_commit_pressure_for_packet_decode()["skip"] is True

    monkeypatch.setattr(
        mumu_control,
        "_collect_windows_commit_snapshot",
        lambda: {"commit_percent": 70.0, "commit_available_mb": 24 * 1024},
    )
    assert worker._host_commit_pressure_for_packet_decode()["skip"] is False


def test_decode_max_streams_default_matches_runtime_flush_budget():
    assert worker.DEFAULT_DECODE_MAX_STREAMS == 4


def test_maintenance_once_runs_bounded_mail_backlog_without_full_historical_sync(monkeypatch):
    calls: list[dict[str, object]] = []

    def fake_maintenance(**kwargs):
        calls.append(kwargs)
        return {"ok": True}

    monkeypatch.setattr(worker, "sync_fanxiu_capture_maintenance_backlog", fake_maintenance)

    service = worker.FanxiuPacketInsightWorker(stable_seconds=1)
    result = service.maintenance_once()

    assert result == {"ok": True}
    assert calls
    assert calls[0]["include_historical_business_backlog"] is False
    assert calls[0]["include_mail_business_backlog"] is True


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
    assert result["decoded_record_db_sync"]["skipped"] is True
    assert result["activity_packet_sync"]["skipped"] is True
    assert result["capture_runtime_backstop"]["reason"] == "test_disabled"


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


def test_live_capture_backlog_skips_decode_under_host_commit_pressure(monkeypatch, tmp_path):
    monkeypatch.setattr(worker, "_worker_state_path", lambda _data_dir=None: tmp_path / "worker_state.json")
    monkeypatch.setattr(worker, "_decoded_capture_digests", lambda _data_dir=None: set())
    monkeypatch.setattr(worker, "_decoded_capture_streams_by_digest", lambda _data_dir=None: {})
    monkeypatch.setattr(worker, "_decoded_capture_sources_by_digest", lambda _data_dir=None: {})
    monkeypatch.setattr(
        worker,
        "_host_commit_pressure_for_packet_decode",
        lambda: {"skip": True, "reason": "host_commit_pressure", "commit": {"commit_available_mb": 4096}},
    )
    monkeypatch.setattr(worker, "_iter_stable_live_pcaps", lambda **_kwargs: (_ for _ in ()).throw(AssertionError("should not scan")))

    result = worker.sync_fanxiu_live_capture_backlog(data_dir=tmp_path)

    assert result["ok"] is True
    assert result["skipped"] is True
    assert result["skip_reason"] == "host_commit_pressure"
    assert result["decode_attempts"] == 0
    assert result["host_commit_pressure"]["commit"]["commit_available_mb"] == 4096


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


def test_realtime_scan_runs_latest_mail_business_backlog(monkeypatch):
    service = worker.FanxiuPacketInsightWorker(maintenance_interval_seconds=60, stable_seconds=1)
    calls: list[dict[str, int]] = []

    monkeypatch.setattr(worker, "sync_fanxiu_live_capture_backlog", lambda **_kwargs: {"ok": True})

    def fake_mail_backlog(**kwargs):
        calls.append(kwargs)
        return {"ok": True, "selected_count": 1}

    monkeypatch.setattr(worker, "sync_fanxiu_mail_business_backlog", fake_mail_backlog)

    result = service.scan_once()

    assert calls == [{"latest_limit": 16, "historical_limit": 0}]
    assert result["mail_business_backlog_sync"] == {"ok": True, "selected_count": 1}


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
    monkeypatch.setattr("backend.core.fanxiu.mail.packet_sync.sync_fanxiu_mail_packets", lambda *_args, **_kwargs: {"ok": True})

    result = worker.sync_fanxiu_mail_business_backlog(latest_limit=1, historical_limit=0)

    assert result["mail_source_probe"]["has_any_mail_source"] is False
    assert result["mail_source_probe"]["has_mail_action"] is True
    assert result["mail_source_probe"]["protocol_counts"] == {"SM_DeleteMail": 1}


def test_maintenance_loop_scans_before_wait(monkeypatch):
    service = worker.FanxiuPacketInsightWorker(maintenance_interval_seconds=60, stable_seconds=1)
    calls = 0

    def fake_maintenance_once():
        nonlocal calls
        calls += 1
        service._maintenance_stop_event.set()
        return {"ok": True}

    monkeypatch.setattr(service, "maintenance_once", fake_maintenance_once)

    service._maintenance_loop()

    assert calls == 1


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
    monkeypatch.setattr("backend.core.fanxiu.mail.packet_sync.sync_fanxiu_mail_packets", fake_sync)

    result = worker.sync_fanxiu_mail_business_backlog(latest_limit=2, historical_limit=3)

    assert result["selected_count"] == 5
    assert result["latest_selected_count"] == 2
    assert result["historical_selected_count"] == 3
    assert len(selected_keys) == 5
    assert worker._decoded_source_key(sources[0]) in selected_keys
    assert worker._decoded_source_key(sources[-1]) in selected_keys


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
    monkeypatch.setattr("backend.core.fanxiu.mail.packet_sync.sync_fanxiu_mail_packets", lambda *_args, **_kwargs: {"ok": True})

    runtime_sync, mail_sync = worker._sync_business_after_decoded(
        [{"decoded_sources": [{"decoded_path": "decoded.json", "record_id": "wallet-record", "stream": 0}]}]
    )

    assert seen_names == ["SM_Wallet"]
    assert runtime_sync["source_count"] == 1
    assert runtime_sync["sync_count"] == 1
    assert runtime_sync["changed"] is True
    assert mail_sync["ok"] is True


def test_live_capture_backlog_caps_single_pass_scan_window(monkeypatch, tmp_path):
    live_dir = tmp_path / "live-captures"
    live_dir.mkdir()
    for index in range(250):
        path = live_dir / f"fanxiu_runtime_20260608_{index:06d}.pcap"
        path.write_bytes(b"x" * 128)

    scanned: list[Path] = []

    monkeypatch.setattr(worker, "resolve_fanxiu_tcp_live_capture_dir", lambda _data_dir=None: live_dir)
    monkeypatch.setattr(worker, "resolve_fanxiu_tcp_store_root", lambda _data_dir=None: tmp_path / "tcp-flow")
    monkeypatch.setattr(worker, "_decoded_capture_digests", lambda _data_dir=None: set())
    monkeypatch.setattr(worker, "_decoded_capture_streams_by_digest", lambda _data_dir=None: {})
    monkeypatch.setattr(worker, "_decoded_capture_sources_by_digest", lambda _data_dir=None: {})
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
    monkeypatch.setattr(worker, "_decoded_capture_digests", lambda _data_dir=None: set())
    monkeypatch.setattr(worker, "_decoded_capture_streams_by_digest", lambda _data_dir=None: {})
    monkeypatch.setattr(worker, "_decoded_capture_sources_by_digest", lambda _data_dir=None: {})
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


def test_live_capture_backlog_skips_locked_active_capture(monkeypatch, tmp_path):
    live_dir = tmp_path / "live-captures"
    live_dir.mkdir()
    pcap = live_dir / "fanxiu_runtime_locked.pcap"
    pcap.write_bytes(b"x" * 128)
    old_time = time.time() - 10
    os.utime(pcap, (old_time, old_time))

    monkeypatch.setattr(worker, "resolve_fanxiu_tcp_live_capture_dir", lambda _data_dir=None: live_dir)
    monkeypatch.setattr(worker, "resolve_fanxiu_tcp_store_root", lambda _data_dir=None: tmp_path / "tcp-flow")
    monkeypatch.setattr(worker, "_decoded_capture_digests", lambda _data_dir=None: set())
    monkeypatch.setattr(worker, "_decoded_capture_streams_by_digest", lambda _data_dir=None: {})
    monkeypatch.setattr(worker, "_decoded_capture_sources_by_digest", lambda _data_dir=None: {})
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
