from __future__ import annotations

from types import SimpleNamespace

from backend.api import filesystem
from backend.core.jobs import result_store


def test_large_local_job_result_uses_external_snapshot(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        result_store,
        "get_settings",
        lambda: SimpleNamespace(data_dir=tmp_path),
    )
    payload = {"media": [{"path": f"image-{index}.jpg"} for index in range(100)]}

    path = result_store.write_local_job_result_snapshot("abc123", payload)

    assert path.parent == tmp_path / "local-jobs" / "result-snapshots"
    assert result_store.read_local_job_result_snapshot("abc123") == payload


def test_filesystem_media_list_start_submits_serializable_local_job(monkeypatch) -> None:
    submitted = []
    queued = SimpleNamespace(
        id="abc123",
        job_type="filesystem.media-list",
        status="queued",
        input_json={},
        result_json={},
        queued_at=1.0,
        started_at=None,
        finished_at=None,
    )
    monkeypatch.setattr(
        filesystem,
        "submit_local_job",
        lambda **kwargs: submitted.append(kwargs) or queued,
    )
    monkeypatch.setattr(
        filesystem,
        "serialize_local_job_run",
        lambda _run: {"stage": "queued", "message": "等待执行"},
    )

    result = filesystem.start_media_list_task(
        filesystem.MediaListRequest(root="pictures", recursive=True, scan_limit=500)
    )

    assert result["task_id"] == "abc123"
    assert result["status"] == "queued"
    assert submitted[0]["job_type"] == "filesystem.media-list"
    assert submitted[0]["payload"]["request"]["root"] == "pictures"
    assert submitted[0]["payload"]["request"]["scan_limit"] == 500
