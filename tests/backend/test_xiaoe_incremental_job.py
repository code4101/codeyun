from pathlib import Path

from backend.core.jobs import scheduler
from backend.core.xiaoe_incremental_job import (
    XIAOE_INCREMENTAL_UPDATE_TASK_KEY,
    run_xiaoe_incremental_update,
)


def _write_state(root: Path, name: str, status: str) -> None:
    helper = root / "_下载辅助"
    helper.mkdir(parents=True, exist_ok=True)
    (helper / name).write_text(f'{{"status":"{status}"}}', encoding="utf-8")


def test_xiaoe_job_is_registered_with_sunday_schedule() -> None:
    spec = scheduler.get_background_task_spec(XIAOE_INCREMENTAL_UPDATE_TASK_KEY)
    assert spec is not None
    assert spec.title == "小鹅通课程增量归档"
    policy = scheduler._default_background_task_schedule_policy(spec.key)
    assert policy["trigger"] == {"type": "weekly", "weekdays": [7], "time": "08:00"}


def test_xiaoe_job_runs_three_incremental_stages_in_order(tmp_path: Path, monkeypatch) -> None:
    _write_state(tmp_path, "audio-current-state.json", "completed")
    _write_state(tmp_path, "text-current-state.json", "completed")
    calls: list[str] = []

    def fake_run(script_name: str, *arguments: str):
        calls.append(script_name)
        return {"status": "completed"}

    monkeypatch.setattr("backend.core.xiaoe_incremental_job._run_script", fake_run)
    result = run_xiaoe_incremental_update(tmp_path)

    assert calls == [
        "download_xiaoe_video_incremental.py",
        "download_xiaoe_audio.py",
        "download_xiaoe_text.py",
    ]
    assert result["status"] == "completed"


def test_xiaoe_job_skips_incomplete_audio_and_text_full_archives(tmp_path: Path, monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        "backend.core.xiaoe_incremental_job._run_script",
        lambda script_name, *arguments: calls.append(script_name) or {"status": "completed"},
    )

    result = run_xiaoe_incremental_update(tmp_path)

    assert calls == ["download_xiaoe_video_incremental.py"]
    assert result["audio"]["status"] == "skipped"
    assert result["text"]["status"] == "skipped"
