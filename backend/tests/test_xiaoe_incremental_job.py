from pathlib import Path

from backend.core import xiaoe_incremental_job as module


def test_video_repair_does_not_run_audio_or_text(tmp_path: Path, monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(module, "_run_script", lambda *args: calls.append(args) or {"status": "completed"})
    result = module.run_xiaoe_incremental_update(tmp_path, video_only=True, retry_special=True)
    assert len(calls) == 1
    assert calls[0] == ("download_xiaoe_video_incremental.py", "--output-dir", str(tmp_path), "--retry-special")
    assert set(result) == {"status", "video"}


def test_default_job_uses_complete_archive_root() -> None:
    assert module.XIAOE_ARCHIVE_ROOT == Path(r"F:\data\m2311禅课合辑")
