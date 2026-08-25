import json
from pathlib import Path

import pytest

from scripts.download_xiaoe_video_queue import (
    _cleanup_stale_temp_files,
    _default_state_path,
    _wait_for_completion,
)


def test_wait_for_completion_returns_for_completed_state(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps({"status": "completed"}, ensure_ascii=False),
        encoding="utf-8",
    )

    _wait_for_completion(state_path, timeout_seconds=0)


def test_wait_for_completion_does_not_accept_running_state(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps({"status": "running"}, ensure_ascii=False),
        encoding="utf-8",
    )

    with pytest.raises(TimeoutError, match="等待前置队列完成超时"):
        _wait_for_completion(state_path, timeout_seconds=0)


def test_default_state_path_is_kept_out_of_video_root(tmp_path: Path) -> None:
    assert _default_state_path(tmp_path) == tmp_path / "_下载辅助" / "current-state.json"


def test_cleanup_stale_temp_files_keeps_current_queue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("scripts.download_xiaoe_video_queue.tempfile.gettempdir", lambda: str(tmp_path))
    queue_dir = tmp_path / "codeyun" / "xiaoe-video-serial"
    queue_dir.mkdir(parents=True)
    current = queue_dir / "page4-item3.json"
    current_stdout = queue_dir / "page4-item3.stdout.log"
    current_stderr = queue_dir / "page4-item3.stderr.log"
    stale_queue = queue_dir / "page4-item2.json"
    stale_log = queue_dir / "page4-item2.stdout.log"
    unrelated = queue_dir / "notes.txt"
    for path in (
        current,
        current_stdout,
        current_stderr,
        stale_queue,
        stale_log,
        unrelated,
    ):
        path.write_text("test", encoding="utf-8")

    _cleanup_stale_temp_files(current)

    assert current.exists()
    assert current_stdout.exists()
    assert current_stderr.exists()
    assert unrelated.exists()
    assert not stale_queue.exists()
    assert not stale_log.exists()
