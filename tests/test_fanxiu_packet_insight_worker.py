import json
import os
import time

from backend.core.fanxiu.packet import insight_worker as worker


def test_worker_heartbeat_clears_stale_error_and_skip_flags_on_new_ok_cycle():
    packet_worker = worker.FanxiuPacketInsightWorker()
    packet_worker._last_realtime_result = {
        "ok": False,
        "updated_at": "2026-07-11 02:00:00",
        "error": "stale realtime error",
        "skipped": True,
        "skip_reason": "host_commit_pressure",
    }
    packet_worker._last_maintenance_result = {
        "ok": False,
        "updated_at": "2026-07-11 02:00:00",
        "error": "stale maintenance error",
        "skipped": True,
        "skip_reason": "host_commit_pressure",
    }

    packet_worker._mark_realtime_heartbeat(ok=True, mode="realtime_scan", phase="capture_backstop")
    packet_worker._mark_maintenance_heartbeat(ok=True, mode="maintenance", phase="capture_backstop")

    assert "error" not in packet_worker._last_realtime_result
    assert "skipped" not in packet_worker._last_realtime_result
    assert "skip_reason" not in packet_worker._last_realtime_result
    assert "error" not in packet_worker._last_maintenance_result
    assert "skipped" not in packet_worker._last_maintenance_result
    assert "skip_reason" not in packet_worker._last_maintenance_result


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
    assert calls == [(pcap, {"data_dir": tmp_path, "max_streams": 2, "sync_business": False})]
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
    (record_dir / "meta.json").write_text(
        json.dumps({"capture_sha256": "digest-1", "decoder_version": worker.FANXIU_TCP_DECODE_SCHEMA_VERSION}),
        encoding="utf-8",
    )

    calls = []
    monkeypatch.setattr(worker, "decode_and_sync_fanxiu_runtime_capture", lambda *args, **kwargs: calls.append(args))

    result = worker.sync_fanxiu_live_capture_backlog(data_dir=tmp_path, stable_seconds=1)

    assert result["decoded_count"] == 0
    assert result["skipped_count"] == 1
    assert calls == []


def test_live_capture_backlog_backfills_business_for_already_decoded_digest(tmp_path, monkeypatch):
    live_dir = tmp_path / "fanxiu" / "tcp-flow" / "live-captures"
    live_dir.mkdir(parents=True)
    pcap = live_dir / "capture.pcap"
    pcap.write_bytes(b"pcap" * 16)
    old_time = time.time() - 60
    os.utime(pcap, (old_time, old_time))
    monkeypatch.setattr(worker, "_sha256_file", lambda _path: "digest-1")

    record_dir = tmp_path / "fanxiu" / "tcp-flow" / "record"
    record_dir.mkdir(parents=True)
    decoded_path = record_dir / "decoded.json"
    decoded_path.write_text(json.dumps({"frames": [{"name": "SM_ShowOther"}]}), encoding="utf-8")
    (record_dir / "meta.json").write_text(
        json.dumps(
            {
                "record_id": "record",
                "capture_sha256": "digest-1",
                "decoder_version": worker.FANXIU_TCP_DECODE_SCHEMA_VERSION,
                "decoded_path": str(decoded_path),
                "pcap_name": "capture.pcap",
                "stream": 0,
            }
        ),
        encoding="utf-8",
    )

    runtime_sources: list[str] = []

    def fake_runtime_sync(result, **_kwargs):
        runtime_sources.append(result["stored_decoded_path"])
        return {"ok": True, "changed": True}

    mail_sources: list[str] = []

    def fake_business_sync(decoded, **_kwargs):
        for item in decoded:
            for source in item.get("decoded_sources") or []:
                mail_sources.append(str(source.get("decoded_path") or ""))
        for item in decoded:
            item["batch_mail_packet_sync"] = {"source_count": len(mail_sources)}
        return {"changed": True}, {"source_count": len(mail_sources)}

    monkeypatch.setattr(worker, "decode_and_sync_fanxiu_runtime_capture", lambda *args, **kwargs: None)
    monkeypatch.setattr(worker, "sync_fanxiu_packet_runtime_insights_for_decode_result", fake_runtime_sync)
    monkeypatch.setattr(worker, "_sync_business_after_decoded", fake_business_sync)

    result = worker.sync_fanxiu_live_capture_backlog(data_dir=tmp_path, stable_seconds=1)

    assert result["decoded_count"] == 1
    assert result["new_decode_count"] == 0
    assert result["business_backfill_count"] == 1
    assert result["skipped"][0]["reason"] == "already_decoded"
    assert mail_sources == [str(decoded_path)]


def test_live_capture_backlog_decodes_oldest_stable_pcaps_first(tmp_path, monkeypatch):
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
    assert calls == ["older.pcap"]


def test_live_capture_backlog_retries_failed_digest_after_backoff(tmp_path, monkeypatch):
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

    first = worker.sync_fanxiu_live_capture_backlog(data_dir=tmp_path, stable_seconds=1, use_cursor=False)
    second = worker.sync_fanxiu_live_capture_backlog(data_dir=tmp_path, stable_seconds=1, use_cursor=False)
    third = worker.sync_fanxiu_live_capture_backlog(
        data_dir=tmp_path,
        stable_seconds=1,
        retry_failed_after_seconds=0,
        use_cursor=False,
    )

    assert first["error_count"] == 1
    assert second["skipped_count"] == 1
    assert second["skipped"][0]["reason"] == "recent_error"
    assert third["error_count"] == 1
    assert calls == {"decode": 2}


def test_live_capture_backlog_keeps_confirmed_cursor_before_failed_gap_but_decodes_later_pcaps(tmp_path, monkeypatch):
    live_dir = tmp_path / "fanxiu" / "tcp-flow" / "live-captures"
    live_dir.mkdir(parents=True)
    broken = live_dir / "0001-broken.pcap"
    later = live_dir / "0002-later.pcap"
    broken.write_bytes(b"broken-pcap" * 16)
    later.write_bytes(b"later-pcap" * 16)
    now = time.time()
    os.utime(broken, (now - 120, now - 120))
    os.utime(later, (now - 60, now - 60))

    monkeypatch.setattr(worker, "_sha256_file", lambda path: f"digest-{path.name}")
    calls: list[str] = []

    def fake_decode(path, **_kwargs):
        calls.append(path.name)
        if path.name.startswith("0001"):
            raise RuntimeError("broken pcap")
        return {"decoded_count": 1, "runtime_protocol_count": 1}

    monkeypatch.setattr(worker, "decode_and_sync_fanxiu_runtime_capture", fake_decode)

    result = worker.sync_fanxiu_live_capture_backlog(
        data_dir=tmp_path,
        stable_seconds=1,
        retry_failed_after_seconds=600,
        limit=2,
    )

    assert calls == ["0001-broken.pcap", "0002-later.pcap"]
    assert result["decoded_count"] == 1
    assert result["error_count"] == 1
    assert result["has_unconfirmed_gap"] is True
    assert result["confirmed_cursor_pcap"] == ""
    assert result["latest_scanned_pcap"].endswith("0002-later.pcap")
    states = {item["name"]: item["status"] for item in result["pcap_states"]}
    assert states["0001-broken.pcap"] == "failed"
    assert states["0002-later.pcap"] == "decoded"


def test_newest_first_scan_does_not_advance_confirmed_cursor_across_older_gap(tmp_path, monkeypatch):
    live_dir = tmp_path / "fanxiu" / "tcp-flow" / "live-captures"
    live_dir.mkdir(parents=True)
    older = live_dir / "0001-broken.pcap"
    newer = live_dir / "0002-good.pcap"
    older.write_bytes(b"broken" * 16)
    newer.write_bytes(b"good" * 16)
    now = time.time()
    os.utime(older, (now - 120, now - 120))
    os.utime(newer, (now - 60, now - 60))
    monkeypatch.setattr(worker, "_sha256_file", lambda path: f"digest-{path.name}")

    def fake_decode(path, **_kwargs):
        if path == older:
            raise RuntimeError("broken")
        return {"decoded_count": 1, "runtime_protocol_count": 1}

    monkeypatch.setattr(worker, "decode_and_sync_fanxiu_runtime_capture", fake_decode)
    result = worker.sync_fanxiu_live_capture_backlog(
        data_dir=tmp_path,
        stable_seconds=1,
        newest_first=True,
        limit=2,
    )

    assert result["latest_scanned_pcap"].endswith("0002-good.pcap")
    assert result["confirmed_cursor_pcap"] == ""
    assert result["has_unconfirmed_gap"] is True


def test_business_rule_version_change_resets_historical_cursor():
    state = {
        "business_rule_version": worker.PACKET_BUSINESS_RULE_VERSION - 1,
        "historical_cursor_source_key": "old-cursor",
        "recent_processed_source_keys": ["old-source"],
    }

    normalized, reset = worker._business_backlog_state_for_current_rule(state)

    assert normalized == {}
    assert reset is True


def test_old_decoder_version_is_not_treated_as_completed_decode(tmp_path):
    record_dir = tmp_path / "fanxiu" / "tcp-flow" / "record-old"
    record_dir.mkdir(parents=True)
    stored_pcap = record_dir / "old.pcap"
    stored_pcap.write_bytes(b"pcap" * 16)
    (record_dir / "decoded.json").write_text("{}", encoding="utf-8")
    (record_dir / "meta.json").write_text(
        json.dumps(
            {
                "capture_sha256": "old-digest",
                "decoder_version": worker.FANXIU_TCP_DECODE_SCHEMA_VERSION - 1,
                "stream": 1,
                "decoded_path": str(record_dir / "decoded.json"),
                "stored_pcap": str(stored_pcap),
            }
        ),
        encoding="utf-8",
    )

    assert "old-digest" not in worker._decoded_capture_digests(tmp_path)
    assert "old-digest" not in worker._decoded_capture_streams_by_digest(tmp_path)
    assert "old-digest" not in worker._decoded_capture_sources_by_digest(tmp_path)
    selected, stale_count, missing_count = worker._stale_decoder_evidence_pcaps(tmp_path, limit=1)
    assert selected == [stored_pcap]
    assert stale_count == 1
    assert missing_count == 0


def test_live_capture_backlog_newest_failures_do_not_starve_decodable_pcaps(tmp_path, monkeypatch):
    live_dir = tmp_path / "fanxiu" / "tcp-flow" / "live-captures"
    live_dir.mkdir(parents=True)
    older_good = live_dir / "0001-good.pcap"
    newer_bad = live_dir / "0002-bad.pcap"
    newest_bad = live_dir / "0003-bad.pcap"
    for path in (older_good, newer_bad, newest_bad):
        path.write_bytes(path.name.encode("utf-8") * 16)
    now = time.time()
    os.utime(older_good, (now - 180, now - 180))
    os.utime(newer_bad, (now - 120, now - 120))
    os.utime(newest_bad, (now - 60, now - 60))

    monkeypatch.setattr(worker, "_sha256_file", lambda path: f"digest-{path.name}")
    calls: list[str] = []

    def fake_decode(path, **_kwargs):
        calls.append(path.name)
        if path.name.endswith("bad.pcap"):
            raise RuntimeError("broken pcap")
        return {"decoded_count": 1, "runtime_protocol_count": 1}

    monkeypatch.setattr(worker, "decode_and_sync_fanxiu_runtime_capture", fake_decode)

    result = worker.sync_fanxiu_live_capture_backlog(
        data_dir=tmp_path,
        stable_seconds=1,
        retry_failed_after_seconds=600,
        newest_first=True,
        limit=1,
    )

    assert calls == ["0003-bad.pcap", "0001-good.pcap"]
    assert result["decoded_count"] == 1
    assert result["error_count"] == 1
    assert result["has_unconfirmed_gap"] is True
    states = {item["name"]: item["status"] for item in result["pcap_states"]}
    assert states["0001-good.pcap"] == "decoded"
    assert states["0003-bad.pcap"] == "failed"


def test_sync_capture_paths_decodes_only_given_pcaps_and_syncs_once(tmp_path, monkeypatch):
    pcap = tmp_path / "recent.pcap"
    pcap.write_bytes(b"pcap" * 16)
    missing = tmp_path / "missing.pcap"
    calls: list[tuple[str, dict]] = []

    def fake_decode(path, **kwargs):
        calls.append((path.name, kwargs))
        return {"decoded_count": 1, "runtime_protocol_count": 0, "worship_protocol_count": 0}

    monkeypatch.setattr(worker, "decode_and_sync_fanxiu_runtime_capture", fake_decode)
    business_sync_calls = {"count": 0}

    def fake_business_sync(decoded, **_kwargs):
        business_sync_calls["count"] += 1
        for item in decoded:
            item["batch_mail_packet_sync"] = {"record_count": 1}
        return {"changed": True}, {"record_count": 1}

    monkeypatch.setattr(worker, "_sync_business_after_decoded", fake_business_sync)

    result = worker.sync_fanxiu_capture_paths([pcap, missing], data_dir=tmp_path, max_streams=4)

    assert result["decoded_count"] == 1
    assert result["skipped_count"] == 1
    assert result["skipped"][0]["reason"] == "missing"
    assert calls == [("recent.pcap", {"data_dir": tmp_path, "max_streams": 4, "sync_business": False})]
    assert business_sync_calls == {"count": 1}
    assert result["mail_packet_sync"]["record_count"] == 1


def test_packet_facts_catch_up_flushes_current_capture_and_syncs(tmp_path, monkeypatch):
    pcap = tmp_path / "current.pcap"
    pcap.write_bytes(b"pcap" * 16)
    monkeypatch.setattr(worker, "_ensure_capture_runtime_from_packet_worker", lambda **_kwargs: {"ok": True})
    sync_calls: list[tuple[list[str], dict]] = []

    class FakeCaptureRuntime:
        def flush_recent_capture(self, reason, *, restart=True):
            return {
                "ok": True,
                "flushed": True,
                "reason": reason,
                "restarted": restart,
                "pcap_path": str(pcap),
                "pcap_size": pcap.stat().st_size,
            }

    monkeypatch.setattr(
        "backend.core.fanxiu.runtime.capture_runtime.fanxiu_capture_runtime_service",
        FakeCaptureRuntime(),
    )

    def fake_sync(paths, **kwargs):
        sync_calls.append(([str(path) for path in paths], kwargs))
        return {"ok": True, "decoded_count": 1, "new_decode_count": 1, "business_backfill_count": 0}

    monkeypatch.setattr(worker, "sync_fanxiu_capture_paths", fake_sync)
    monkeypatch.setattr(
        worker,
        "_prune_decoded_record_db_cache",
        lambda: (_ for _ in ()).throw(AssertionError("realtime catch-up must not prune historical records")),
    )

    result = worker.catch_up_fanxiu_packet_facts(reason="test", data_dir=tmp_path, max_streams=3)

    assert result["ok"] is True
    assert result["mode"] == "packet_facts_catch_up"
    assert result["flush"]["flushed"] is True
    assert sync_calls == [
        ([str(pcap)], {"data_dir": tmp_path, "max_streams": 3, "scan_existing_decoded": False})
    ]
    assert result["sync"]["decoded_count"] == 1
    assert result["decoded_record_db_prune"] == {
        "ok": True,
        "skipped": True,
        "reason": "realtime_catch_up_does_not_run_maintenance_prune",
    }


def test_packet_facts_catch_up_skips_when_no_capture_flushed(monkeypatch):
    monkeypatch.setattr(worker, "_ensure_capture_runtime_from_packet_worker", lambda **_kwargs: {"ok": True})

    class FakeCaptureRuntime:
        def flush_recent_capture(self, reason, *, restart=True):
            return {"ok": True, "flushed": False, "reason": reason}

    monkeypatch.setattr(
        "backend.core.fanxiu.runtime.capture_runtime.fanxiu_capture_runtime_service",
        FakeCaptureRuntime(),
    )
    monkeypatch.setattr(
        worker,
        "sync_fanxiu_capture_paths",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not sync without pcap")),
    )
    monkeypatch.setattr(
        worker,
        "_prune_decoded_record_db_cache",
        lambda: (_ for _ in ()).throw(AssertionError("realtime catch-up must not prune historical records")),
    )

    result = worker.catch_up_fanxiu_packet_facts(reason="test")

    assert result["ok"] is True
    assert result["sync"]["skipped"] is True
    assert result["sync"]["reason"] == "no_flushed_capture"
    assert result["decoded_record_db_prune"] == {
        "ok": True,
        "skipped": True,
        "reason": "realtime_catch_up_does_not_run_maintenance_prune",
    }


def test_packet_facts_catch_up_does_not_run_decoded_db_prune(tmp_path, monkeypatch):
    pcap = tmp_path / "current.pcap"
    pcap.write_bytes(b"pcap" * 16)
    monkeypatch.setattr(worker, "_ensure_capture_runtime_from_packet_worker", lambda **_kwargs: {"ok": True})

    class FakeCaptureRuntime:
        def flush_recent_capture(self, reason, *, restart=True):
            return {"ok": True, "flushed": True, "reason": reason, "pcap_path": str(pcap)}

    monkeypatch.setattr(
        "backend.core.fanxiu.runtime.capture_runtime.fanxiu_capture_runtime_service",
        FakeCaptureRuntime(),
    )
    monkeypatch.setattr(worker, "sync_fanxiu_capture_paths", lambda *_args, **_kwargs: {"ok": True, "decoded_count": 1})
    monkeypatch.setattr(
        worker,
        "_prune_decoded_record_db_cache",
        lambda: (_ for _ in ()).throw(AssertionError("realtime catch-up must not prune historical records")),
    )

    result = worker.catch_up_fanxiu_packet_facts(reason="test", data_dir=tmp_path)

    assert result["ok"] is True
    assert result["decoded_record_db_prune"] == {
        "ok": True,
        "skipped": True,
        "reason": "realtime_catch_up_does_not_run_maintenance_prune",
    }


def test_maintenance_backlog_ignores_realtime_cursor_and_age_window(tmp_path, monkeypatch):
    live_dir = tmp_path / "fanxiu" / "tcp-flow" / "live-captures"
    live_dir.mkdir(parents=True)
    old_gap = live_dir / "0001-old-gap.pcap"
    old_gap.write_bytes(b"old-gap-pcap" * 16)
    record_dir = tmp_path / "fanxiu" / "tcp-flow" / "record"
    record_dir.mkdir(parents=True)
    decoded_path = record_dir / "decoded.json"
    decoded_path.write_text(json.dumps({"frames": []}), encoding="utf-8")
    (record_dir / "meta.json").write_text(
        json.dumps(
            {
                "record_id": "record",
                "capture_sha256": "historical-digest",
                "decoder_version": worker.FANXIU_TCP_DECODE_SCHEMA_VERSION,
                "decoded_path": str(decoded_path),
                "pcap_name": "historical.pcap",
                "stream": 0,
            }
        ),
        encoding="utf-8",
    )
    now = time.time()
    os.utime(old_gap, (now - 3 * 24 * 60 * 60, now - 3 * 24 * 60 * 60))

    state_dir = tmp_path / "fanxiu" / "packet-insights"
    state_dir.mkdir(parents=True)
    (state_dir / "live_capture_worker_state.json").write_text(
        json.dumps(
            {
                "schema_version": worker.PACKET_INSIGHT_WORKER_SCHEMA_VERSION,
                "confirmed_cursor_mtime": now,
                "confirmed_cursor_pcap": str(live_dir / "newer.pcap"),
            }
        ),
        encoding="utf-8",
    )

    calls: list[str] = []

    def fake_decode(path, **_kwargs):
        calls.append(path.name)
        return {"decoded_count": 1, "runtime_protocol_count": 1, "decoded": []}

    monkeypatch.setattr(worker, "decode_and_sync_fanxiu_runtime_capture", fake_decode)
    monkeypatch.setattr(worker, "sync_fanxiu_decoded_record_backlog", lambda **_kwargs: {"ok": True, "scanned": 0})
    monkeypatch.setattr(worker, "sync_fanxiu_activity_packets", lambda **_kwargs: {"ok": True, "record_count": 0})
    monkeypatch.setattr(worker, "_sync_historical_business_backlog", lambda **_kwargs: ({"changed": True}, {"source_count": 1}))
    monkeypatch.setattr(worker, "_prune_decoded_record_db_cache", lambda: {"ok": True, "deleted": 3})

    result = worker.sync_fanxiu_capture_maintenance_backlog(data_dir=tmp_path, stable_seconds=1, limit=1)

    assert result["mode"] == "maintenance"
    assert result["decoded_count"] == 1
    assert calls == ["0001-old-gap.pcap"]
    assert result["historical_mail_packet_sync"]["source_count"] == 1
    assert result["decoded_record_db_prune"] == {"ok": True, "deleted": 3}
    state = json.loads((state_dir / "maintenance_worker_state.json").read_text(encoding="utf-8"))
    assert state["mode"] == "maintenance"
    assert state["decoded_count"] == 1


def test_maintenance_backlog_does_not_overwrite_realtime_worker_state(tmp_path, monkeypatch):
    live_dir = tmp_path / "fanxiu" / "tcp-flow" / "live-captures"
    live_dir.mkdir(parents=True)
    pcap = live_dir / "0001-old-gap.pcap"
    pcap.write_bytes(b"old-gap-pcap" * 16)
    now = time.time()
    os.utime(pcap, (now - 120, now - 120))

    state_dir = tmp_path / "fanxiu" / "packet-insights"
    state_dir.mkdir(parents=True)
    realtime_state_path = state_dir / "live_capture_worker_state.json"
    realtime_payload = {
        "schema_version": worker.PACKET_INSIGHT_WORKER_SCHEMA_VERSION,
        "mode": "realtime",
        "confirmed_cursor_pcap": "keep-realtime-cursor.pcap",
        "confirmed_cursor_mtime": now - 30,
        "has_unconfirmed_gap": False,
        "known_error_count": 0,
    }
    realtime_state_path.write_text(json.dumps(realtime_payload), encoding="utf-8")

    monkeypatch.setattr(worker, "decode_and_sync_fanxiu_runtime_capture", lambda *_args, **_kwargs: {"decoded_count": 1})
    monkeypatch.setattr(worker, "sync_fanxiu_decoded_record_backlog", lambda **_kwargs: {"ok": True, "scanned": 0})
    monkeypatch.setattr(worker, "sync_fanxiu_activity_packets", lambda **_kwargs: {"ok": True, "record_count": 0})
    monkeypatch.setattr(worker, "_sync_historical_business_backlog", lambda **_kwargs: ({"changed": True}, {"source_count": 0}))
    monkeypatch.setattr(worker, "_prune_decoded_record_db_cache", lambda: {"ok": True, "deleted": 0})

    worker.sync_fanxiu_capture_maintenance_backlog(data_dir=tmp_path, stable_seconds=1, limit=1)

    realtime_state = json.loads(realtime_state_path.read_text(encoding="utf-8"))
    maintenance_state = json.loads((state_dir / "maintenance_worker_state.json").read_text(encoding="utf-8"))

    assert realtime_state["confirmed_cursor_pcap"] == "keep-realtime-cursor.pcap"
    assert realtime_state["has_unconfirmed_gap"] is False
    assert maintenance_state["mode"] == "maintenance"
    assert maintenance_state["decoded_count"] == 1


def test_maintenance_reports_stale_decoder_records_with_missing_raw_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(worker, "sync_fanxiu_live_capture_backlog", lambda **_kwargs: {"ok": True})
    monkeypatch.setattr(worker, "_stale_decoder_evidence_pcaps", lambda *_args, **_kwargs: ([], 2, 2))
    monkeypatch.setattr(worker, "_sync_historical_business_backlog", lambda **_kwargs: ({"ok": True}, {"ok": True}))
    monkeypatch.setattr(worker, "sync_fanxiu_mail_business_backlog", lambda **_kwargs: {"ok": True})
    monkeypatch.setattr(worker, "_prune_decoded_record_db_cache", lambda: {"ok": True, "deleted": 0})

    result = worker.sync_fanxiu_capture_maintenance_backlog(data_dir=tmp_path)

    assert result["ok"] is False
    assert result["parser_version_backfill"]["reason"] == "stale_decoder_evidence_missing"
    assert result["parser_version_backfill"]["missing_evidence_count"] == 2


def test_packet_worker_status_separates_realtime_and_maintenance(monkeypatch):
    service = worker.FanxiuPacketInsightWorker(scan_interval_seconds=999, maintenance_interval_seconds=999)
    monkeypatch.setattr(worker, "sync_fanxiu_live_capture_backlog", lambda **_kwargs: {"ok": True, "mode": "realtime"})
    monkeypatch.setattr(worker, "sync_fanxiu_decoded_record_backlog", lambda **_kwargs: {"ok": True})
    monkeypatch.setattr(worker, "sync_fanxiu_activity_packets", lambda **_kwargs: {"ok": True})
    monkeypatch.setattr(worker, "sync_fanxiu_capture_maintenance_backlog", lambda **_kwargs: {"ok": True, "mode": "maintenance"})

    service.scan_once()
    service.maintenance_once()
    status = service.status()

    assert status["realtime"]["mode"] == "realtime"
    assert status["maintenance"]["mode"] == "maintenance"
    assert "realtime_running" in status
    assert "maintenance_running" in status
