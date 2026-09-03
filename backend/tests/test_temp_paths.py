from __future__ import annotations

import os

from backend.core.temp_paths import prune_temp_files, trim_file_tail


def _write(path, size: int, modified_at: float) -> None:
    path.write_bytes(b"x" * size)
    os.utime(path, (modified_at, modified_at))


def test_prune_temp_files_applies_age_and_byte_low_watermark(tmp_path):
    _write(tmp_path / "expired.png", 4, 10)
    _write(tmp_path / "old.png", 4, 80)
    _write(tmp_path / "new.png", 4, 90)
    (tmp_path / "index.jsonl").write_text("keep\n", encoding="utf-8")

    result = prune_temp_files(
        tmp_path,
        patterns=("*.png",),
        max_age_seconds=50,
        max_bytes=6,
        target_bytes=4,
        protected_names=("index.jsonl",),
        now=100,
    )

    assert result == {
        "scanned_files": 3,
        "removed_files": 2,
        "removed_bytes": 8,
        "remaining_files": 1,
        "remaining_bytes": 4,
    }
    assert not (tmp_path / "expired.png").exists()
    assert not (tmp_path / "old.png").exists()
    assert (tmp_path / "new.png").exists()
    assert (tmp_path / "index.jsonl").exists()


def test_prune_temp_files_can_bound_nested_evidence(tmp_path):
    first = tmp_path / "20260901"
    second = tmp_path / "20260902"
    first.mkdir()
    second.mkdir()
    _write(first / "old.png", 3, 10)
    _write(first / "old.json", 2, 11)
    _write(second / "new.png", 3, 20)

    result = prune_temp_files(
        tmp_path,
        patterns=("*.png", "*.json"),
        recursive=True,
        max_files=2,
        target_files=2,
    )

    assert result["remaining_files"] == 2
    assert not (first / "old.png").exists()
    assert (first / "old.json").exists()
    assert (second / "new.png").exists()


def test_trim_file_tail_keeps_complete_recent_lines(tmp_path):
    path = tmp_path / "index.jsonl"
    path.write_bytes(b"first\nsecond\nthird\n")

    assert trim_file_tail(path, max_bytes=14) is True
    assert path.read_bytes() == b"second\nthird\n"
    assert trim_file_tail(path, max_bytes=14) is False


def test_prune_temp_files_never_removes_a_protected_active_log(tmp_path):
    _write(tmp_path / "active.ndjson", 8, 1)
    _write(tmp_path / "stale.ndjson", 8, 1)

    result = prune_temp_files(
        tmp_path,
        patterns=("*.ndjson",),
        max_age_seconds=1,
        protected_names=("active.ndjson",),
        now=10,
    )

    assert result["removed_files"] == 1
    assert (tmp_path / "active.ndjson").exists()
    assert not (tmp_path / "stale.ndjson").exists()
