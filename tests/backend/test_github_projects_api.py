from sqlmodel import select

from backend.models import GithubProject


def _payload(**overrides):
    payload = {
        "github_repo_id": 123456,
        "full_name": "owner/repo",
        "html_url": "https://github.com/owner/repo",
        "default_branch": "main",
        "description": "Useful project",
        "language": "Python",
        "license_spdx_id": "MIT",
        "topics": ["ai", "crawler", "ai"],
        "stars": 10,
        "forks": 2,
        "open_issues": 1,
        "created_at": "2026-05-01T00:00:00Z",
        "pushed_at": "2026-06-01T00:00:00Z",
        "updated_at": "2026-06-02T00:00:00Z",
        "source": {"source_type": "manual", "source_label": "seed"},
    }
    payload.update(overrides)
    return payload


def test_github_project_upsert_skips_unchanged_scan(client, auth_user, session):
    first = client.post("/api/github-projects/upsert", json=_payload())
    assert first.status_code == 200
    assert first.json()["created"] is True
    assert first.json()["changed"] is True

    second = client.post("/api/github-projects/upsert", json=_payload(stars=20))
    assert second.status_code == 200
    body = second.json()
    assert body["created"] is False
    assert body["changed"] is False
    assert body["item"]["stars"] == 20
    assert body["item"]["created_at_github"] == "2026-05-01T00:00:00Z"
    assert body["item"]["topics"] == ["ai", "crawler"]
    assert body["item"]["update_notes"] == []

    rows = session.exec(select(GithubProject)).all()
    assert len(rows) == 1


def test_github_project_upsert_records_time_change(client, auth_user):
    client.post("/api/github-projects/upsert", json=_payload())

    changed = client.post(
        "/api/github-projects/upsert",
        json=_payload(pushed_at="2026-06-10T00:00:00Z"),
    )

    assert changed.status_code == 200
    body = changed.json()
    assert body["created"] is False
    assert body["changed"] is True
    assert body["item"]["needs_review"] is True
    assert len(body["item"]["update_notes"]) == 1
    assert body["item"]["update_notes"][0]["old_pushed_at"] == "2026-06-01T00:00:00Z"
    assert body["item"]["update_notes"][0]["new_pushed_at"] == "2026-06-10T00:00:00Z"
