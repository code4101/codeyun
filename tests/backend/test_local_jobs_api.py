from __future__ import annotations

from backend.models import LocalJobRun


def test_local_job_api_only_lists_user_submittable_types(client, auth_user) -> None:
    response = client.get("/api/local-jobs/types")

    assert response.status_code == 200
    assert [item["job_type"] for item in response.json()["items"]] == ["system.health-check"]


def test_local_job_api_rejects_privileged_internal_job(client, auth_user) -> None:
    response = client.post(
        "/api/local-jobs/runs",
        json={"job_type": "maintenance.auto-git-commit", "payload": {}},
    )

    assert response.status_code == 403
    assert "受信业务入口" in response.json()["detail"]


def test_local_job_api_submits_whitelisted_job(client, auth_user, monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(
        "backend.api.local_jobs.submit_local_job",
        lambda **kwargs: calls.append(kwargs)
        or LocalJobRun(
            id="local-api-1",
            user_id=auth_user.id,
            job_type="system.health-check",
            resource_key="system:local-job-health-check",
        ),
    )

    response = client.post(
        "/api/local-jobs/runs",
        json={"job_type": "system.health-check", "payload": {"echo": "api"}},
    )

    assert response.status_code == 200
    assert response.json()["id"] == "local-api-1"
    assert calls == [
        {
            "job_type": "system.health-check",
            "payload": {"echo": "api"},
            "user_id": auth_user.id,
        }
    ]
