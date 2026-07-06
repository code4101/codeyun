import json
import os
import time

from backend.core.fanxiu.packet.tcp_flow import prune_fanxiu_tcp_storage


def _write_record(root, name: str, *, age_seconds: int):
    record_dir = root / name
    record_dir.mkdir(parents=True)
    decoded_path = record_dir / "decoded.json"
    meta_path = record_dir / "meta.json"
    decoded_path.write_text(json.dumps({"frames": []}), encoding="utf-8")
    meta_path.write_text(json.dumps({"decoded_path": str(decoded_path)}), encoding="utf-8")
    ts = time.time() - age_seconds
    os.utime(decoded_path, (ts, ts))
    os.utime(meta_path, (ts, ts))
    os.utime(record_dir, (ts, ts))
    return record_dir


def test_prune_tcp_storage_deletes_expired_decoded_records(tmp_path):
    root = tmp_path / "fanxiu" / "tcp-flow"
    old_record = _write_record(root, "old", age_seconds=10 * 24 * 60 * 60)
    new_record = _write_record(root, "new", age_seconds=60)

    result = prune_fanxiu_tcp_storage(
        data_dir=tmp_path,
        max_record_age_seconds=7 * 24 * 60 * 60,
        max_live_age_seconds=0,
        min_keep=1,
    )

    assert result["records"]["deleted_count"] == 1
    assert not old_record.exists()
    assert new_record.exists()


def test_prune_tcp_storage_preserves_current_paths_even_when_expired(tmp_path):
    root = tmp_path / "fanxiu" / "tcp-flow"
    old_record = _write_record(root, "old", age_seconds=10 * 24 * 60 * 60)
    _write_record(root, "new", age_seconds=60)

    result = prune_fanxiu_tcp_storage(
        data_dir=tmp_path,
        max_record_age_seconds=7 * 24 * 60 * 60,
        max_live_age_seconds=0,
        min_keep=1,
        preserve_paths={old_record},
    )

    assert result["records"]["deleted_count"] == 0
    assert old_record.exists()

