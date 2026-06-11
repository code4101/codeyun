from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.api import music_tools


def test_music_tools_accepts_mp4_and_extracts_audio(client, tmp_path, monkeypatch):
    captured: dict[str, Any] = {}

    def fake_storage_root() -> Path:
        tmp_path.mkdir(parents=True, exist_ok=True)
        return tmp_path

    def fake_extract_video_audio(input_path: Path, output_path: Path) -> None:
        captured["input_path"] = input_path
        captured["output_path"] = output_path
        output_path.write_bytes(b"ID3 extracted")

    def fake_start(run, *, stage: str, message: str, metadata: dict[str, Any]):
        captured["metadata"] = metadata
        return {
            "task_id": "task-video",
            "kind": "music-separation",
            "status": "queued",
            "running": True,
            "stage": stage,
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
    monkeypatch.setattr(music_tools._task_manager, "start", fake_start)

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
    monkeypatch.setattr(music_tools._task_manager, "start", fake_start)

    response = client.post(
        "/api/music-tools/separate",
        files={"file": ("notes.txt", b"not media", "text/plain")},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "不支持的音频或视频格式"
    assert started is False
