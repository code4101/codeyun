from __future__ import annotations

import os
import time
from pathlib import Path

from backend.core import fanxiu_packet_insight_worker as worker


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
    monkeypatch.setattr("backend.core.fanxiu_mail_packet_sync.sync_fanxiu_mail_packets", fake_sync)

    result = worker.sync_fanxiu_mail_business_backlog(latest_limit=2, historical_limit=3)

    assert result["selected_count"] == 5
    assert result["latest_selected_count"] == 2
    assert result["historical_selected_count"] == 3
    assert len(selected_keys) == 5
    assert worker._decoded_source_key(sources[0]) in selected_keys
    assert worker._decoded_source_key(sources[-1]) in selected_keys


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
