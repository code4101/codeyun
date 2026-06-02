import json
import os
import time

from backend.core import fanxiu_packet_insight_worker as worker


def test_live_capture_backlog_decodes_stable_unprocessed_pcap(tmp_path, monkeypatch):
    live_dir = tmp_path / "fanxiu" / "tcp-flow" / "live-captures"
    live_dir.mkdir(parents=True)
    pcap = live_dir / "capture.pcap"
    pcap.write_bytes(b"pcap" * 16)
    old_time = time.time() - 60
    os.utime(pcap, (old_time, old_time))

    calls = []

    def fake_decode(path, **kwargs):
        calls.append((path, kwargs))
        return {
            "decoded_count": 1,
            "runtime_protocol_count": 2,
            "worship_protocol_count": 1,
            "packet_runtime_sync": {"worship_record_count": 3},
        }

    monkeypatch.setattr(worker, "decode_and_sync_fanxiu_runtime_capture", fake_decode)

    result = worker.sync_fanxiu_live_capture_backlog(data_dir=tmp_path, stable_seconds=1)

    assert result["decoded_count"] == 1
    assert result["error_count"] == 0
    assert calls == [(pcap, {"data_dir": tmp_path})]
    state = json.loads((tmp_path / "fanxiu" / "packet-insights" / "live_capture_worker_state.json").read_text(encoding="utf-8"))
    assert state["decoded"][0]["worship_protocol_count"] == 1


def test_live_capture_backlog_skips_already_decoded_digest(tmp_path, monkeypatch):
    live_dir = tmp_path / "fanxiu" / "tcp-flow" / "live-captures"
    live_dir.mkdir(parents=True)
    pcap = live_dir / "capture.pcap"
    pcap.write_bytes(b"pcap" * 16)
    old_time = time.time() - 60
    os.utime(pcap, (old_time, old_time))
    monkeypatch.setattr(worker, "_sha256_file", lambda _path: "digest-1")

    record_dir = tmp_path / "fanxiu" / "tcp-flow" / "record"
    record_dir.mkdir(parents=True)
    (record_dir / "meta.json").write_text(json.dumps({"capture_sha256": "digest-1"}), encoding="utf-8")

    calls = []
    monkeypatch.setattr(worker, "decode_and_sync_fanxiu_runtime_capture", lambda *args, **kwargs: calls.append(args))

    result = worker.sync_fanxiu_live_capture_backlog(data_dir=tmp_path, stable_seconds=1)

    assert result["decoded_count"] == 0
    assert result["skipped_count"] == 1
    assert calls == []


def test_live_capture_backlog_decodes_newest_stable_pcaps_first(tmp_path, monkeypatch):
    live_dir = tmp_path / "fanxiu" / "tcp-flow" / "live-captures"
    live_dir.mkdir(parents=True)
    older = live_dir / "older.pcap"
    newer = live_dir / "newer.pcap"
    older.write_bytes(b"older-pcap" * 16)
    newer.write_bytes(b"newer-pcap" * 16)
    now = time.time()
    os.utime(older, (now - 120, now - 120))
    os.utime(newer, (now - 60, now - 60))

    calls = []

    def fake_decode(path, **kwargs):
        calls.append(path.name)
        return {"decoded_count": 1}

    monkeypatch.setattr(worker, "decode_and_sync_fanxiu_runtime_capture", fake_decode)

    result = worker.sync_fanxiu_live_capture_backlog(data_dir=tmp_path, stable_seconds=1, limit=1)

    assert result["decoded_count"] == 1
    assert calls == ["newer.pcap"]


def test_live_capture_backlog_remembers_failed_digest(tmp_path, monkeypatch):
    live_dir = tmp_path / "fanxiu" / "tcp-flow" / "live-captures"
    live_dir.mkdir(parents=True)
    pcap = live_dir / "broken.pcap"
    pcap.write_bytes(b"pcap" * 16)
    old_time = time.time() - 60
    os.utime(pcap, (old_time, old_time))
    monkeypatch.setattr(worker, "_sha256_file", lambda _path: "broken-digest")

    calls = {"decode": 0}

    def fail_decode(*_args, **_kwargs):
        calls["decode"] += 1
        raise RuntimeError("broken pcap")

    monkeypatch.setattr(worker, "decode_and_sync_fanxiu_runtime_capture", fail_decode)

    first = worker.sync_fanxiu_live_capture_backlog(data_dir=tmp_path, stable_seconds=1)
    second = worker.sync_fanxiu_live_capture_backlog(data_dir=tmp_path, stable_seconds=1)

    assert first["error_count"] == 1
    assert second["skipped_count"] == 1
    assert second["skipped"][0]["reason"] == "previous_error"
    assert calls == {"decode": 1}
