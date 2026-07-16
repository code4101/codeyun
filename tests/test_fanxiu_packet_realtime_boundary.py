from __future__ import annotations

import os
import time

from backend.core.fanxiu.packet import insight_worker
from backend.core.fanxiu.runtime import capture_runtime


def test_latest_recent_sealed_live_pcap_ignores_snapshot_and_active(tmp_path, monkeypatch):
    old = tmp_path / "fanxiu_runtime_old.pcap"
    snapshot = tmp_path / "fanxiu_runtime_snapshot_new_4096.pcap"
    sealed = tmp_path / "fanxiu_runtime_sealed.pcap"
    active = tmp_path / "fanxiu_runtime_active.pcap"
    for path in (old, snapshot, sealed, active):
        path.write_bytes(b"pcap" * 16)
    now = time.time()
    os.utime(old, (now - 300, now - 300))
    os.utime(snapshot, (now - 3, now - 3))
    os.utime(sealed, (now - 2, now - 2))
    os.utime(active, (now - 1, now - 1))

    monkeypatch.setattr(insight_worker, "resolve_fanxiu_tcp_live_capture_dir", lambda _data_dir=None: tmp_path)
    monkeypatch.setattr(insight_worker, "_current_runtime_capture_path", lambda: active)

    result = insight_worker._latest_recent_sealed_live_pcap(
        data_dir=tmp_path,
        max_age_seconds=120,
        now=now,
    )

    assert result == sealed


def test_packet_catch_up_decodes_boundary_and_current_in_one_batch(tmp_path, monkeypatch):
    boundary = tmp_path / "fanxiu_runtime_boundary.pcap"
    current = tmp_path / "fanxiu_runtime_current.pcap"
    boundary.write_bytes(b"pcap" * 16)
    current.write_bytes(b"pcap" * 16)
    calls: list[str] = []

    class FakeCaptureService:
        def flush_recent_capture(self, reason: str, *, restart: bool):
            calls.append("flush")
            return {"ok": True, "flushed": True, "pcap_path": str(current), "reason": reason}

    monkeypatch.setattr(capture_runtime, "fanxiu_capture_runtime_service", FakeCaptureService())
    monkeypatch.setattr(
        insight_worker,
        "_ensure_capture_runtime_from_packet_worker",
        lambda **_kwargs: {"ok": True},
    )
    monkeypatch.setattr(insight_worker, "_latest_recent_sealed_live_pcap", lambda **_kwargs: boundary)
    monkeypatch.setattr(insight_worker, "_capture_has_current_decoded_record", lambda *_args, **_kwargs: False)

    def fake_sync(paths, **kwargs):
        calls.append("sync")
        assert paths == [str(boundary), str(current)]
        assert kwargs["scan_existing_decoded"] is False
        return {"ok": True, "decoded_count": 2}

    monkeypatch.setattr(insight_worker, "sync_fanxiu_capture_paths", fake_sync)

    result = insight_worker.catch_up_fanxiu_packet_facts(reason="unit-test", data_dir=tmp_path)

    assert calls == ["flush", "sync"]
    assert result["ok"] is True
    assert result["boundary_pcap"] == str(boundary)
    assert result["capture_paths"] == [str(boundary), str(current)]
