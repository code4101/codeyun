from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.api import music_tools
from backend.core.jobs.local_runtime import create_local_job_run


def test_music_tools_accepts_mp4_and_extracts_audio(client, tmp_path, monkeypatch):
    captured: dict[str, Any] = {}

    def fake_storage_root() -> Path:
        tmp_path.mkdir(parents=True, exist_ok=True)
        return tmp_path

    def fake_extract_video_audio(input_path: Path, output_path: Path) -> None:
        captured["input_path"] = input_path
        captured["output_path"] = output_path
        output_path.write_bytes(b"ID3 extracted")

    def fake_start(**kwargs):
        message = kwargs["message"]
        metadata = kwargs["metadata"]
        captured["metadata"] = metadata
        return {
            "task_id": "task-video",
            "kind": "music-separation",
            "status": "queued",
            "running": True,
            "stage": "queued",
            "message": message,
            "created_at": 1,
            "started_at": None,
            "updated_at": 1,
            "finished_at": None,
            "progress_current": None,
            "progress_total": None,
            "metadata": metadata,
            "result": None,
            "error": None,
            "error_status_code": None,
            "elapsed_ms": 0,
        }

    monkeypatch.setattr(music_tools, "_storage_root", fake_storage_root)
    monkeypatch.setattr(music_tools, "_extract_video_audio", fake_extract_video_audio)
    monkeypatch.setattr(music_tools, "_submit_music_local_task", fake_start)

    response = client.post(
        "/api/music-tools/separate",
        files={"file": ("clip.mp4", b"fake video bytes", "video/mp4")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["metadata"]["filename"] == "clip.mp4"
    assert payload["metadata"]["input_kind"] == "video"
    assert captured["input_path"].name == "clip.mp4"
    assert captured["output_path"].name == "original.mp3"
    assert captured["metadata"]["files"][0]["stem"] == "original"
    assert captured["metadata"]["files"][0]["filename"] == "original.mp3"


def test_music_tools_rejects_unknown_extension(client, tmp_path, monkeypatch):
    started = False

    def fake_storage_root() -> Path:
        tmp_path.mkdir(parents=True, exist_ok=True)
        return tmp_path

    def fake_start(*args, **kwargs):
        nonlocal started
        started = True
        return {}

    monkeypatch.setattr(music_tools, "_storage_root", fake_storage_root)
    monkeypatch.setattr(music_tools, "_submit_music_local_task", fake_start)

    response = client.post(
        "/api/music-tools/separate",
        files={"file": ("notes.txt", b"not media", "text/plain")},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "不支持的音频或视频格式"
    assert started is False


def test_music_tools_submits_persistent_local_job_and_projects_task_api(
    client,
    session,
    tmp_path,
    monkeypatch,
):
    submitted = []
    monkeypatch.setattr(music_tools, "_storage_root", lambda: tmp_path)

    def fake_submit(**kwargs):
        submitted.append(kwargs)
        return create_local_job_run(db_engine=session.get_bind(), **kwargs)

    monkeypatch.setattr(music_tools, "submit_local_job", fake_submit)
    response = client.post(
        "/api/music-tools/separate",
        files={"file": ("sample.wav", b"RIFF fake", "audio/wav")},
    )

    assert response.status_code == 200
    task = response.json()
    assert task["status"] == "queued"
    assert task["kind"] == "music-separation"
    assert submitted[0]["job_type"] == "music.process"
    assert submitted[0]["payload"]["operation"] == "separate"
    assert submitted[0]["payload"]["engine"] == "demucs"
    assert "RIFF fake" not in str(submitted[0]["payload"])

    from backend.core.jobs.local_runtime import get_local_job_run

    run = get_local_job_run(task["task_id"], db_engine=session.get_bind())
    monkeypatch.setattr(music_tools, "get_local_job_run", lambda _task_id: run)
    status_response = client.get(f"/api/music-tools/tasks/{task['task_id']}")
    assert status_response.status_code == 200
    assert status_response.json()["metadata"]["job_id"] == submitted[0]["payload"]["job_id"]


def test_music_task_cancel_is_persistent_command_not_immediate_terminal(
    client,
    session,
    monkeypatch,
):
    from backend.core.jobs.local_runtime import get_local_job_run, request_local_job_cancel

    run = create_local_job_run(
        job_type="music.process",
        payload={"operation": "separate", "job_id": "music-1", "metadata": {"job_id": "music-1"}},
        db_engine=session.get_bind(),
    )
    monkeypatch.setattr(
        music_tools,
        "get_local_job_run",
        lambda task_id: get_local_job_run(task_id, db_engine=session.get_bind()),
    )
    monkeypatch.setattr(
        music_tools,
        "request_local_job_cancel",
        lambda task_id: request_local_job_cancel(task_id, db_engine=session.get_bind()),
    )

    response = client.post(f"/api/music-tools/tasks/{run.id}/cancel")

    assert response.status_code == 200
    assert response.json()["status"] == "queued"
    saved = get_local_job_run(run.id, db_engine=session.get_bind())
    assert saved.cancel_requested_at is not None
    assert saved.status == "queued"
