import subprocess


def _run_git(repo_path, *args):
    completed = subprocess.run(
        ["git", *args],
        cwd=str(repo_path),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr or completed.stdout or f"git {' '.join(args)} failed")
    return completed.stdout.strip()


def _init_git_repo(repo_path):
    repo_path.mkdir(parents=True, exist_ok=True)
    _run_git(repo_path, "init")
    _run_git(repo_path, "config", "user.name", "CodeYun Test")
    _run_git(repo_path, "config", "user.email", "codeyun-test@example.com")
    (repo_path / "README.md").write_text("# demo\n", encoding="utf-8")
    _run_git(repo_path, "add", "README.md")
    _run_git(repo_path, "commit", "-m", "init")


def test_ai_git_saved_repos_can_persist_and_touch(client, auth_user):
    response = client.put(
        "/api/ai-git-repos",
        json={
            "items": [
                {
                    "id": "",
                    "name": "codeyun",
                    "entry_id": "entry-local",
                    "cwd": r"D:\home\chenkunze\slns\codeyun",
                }
            ],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["items"]) == 1
    item = payload["items"][0]
    assert item["id"].startswith("repo-")
    assert item["name"] == "codeyun"
    assert item["entry_id"] == "entry-local"
    assert item["cwd"] == r"D:\home\chenkunze\slns\codeyun"
    assert item["order_index"] == 0
    assert isinstance(item["created_at"], float)
    assert isinstance(item["updated_at"], float)
    assert item["last_used_at"] is None

    list_response = client.get("/api/ai-git-repos")
    assert list_response.status_code == 200
    assert list_response.json() == payload

    touch_response = client.post(f"/api/ai-git-repos/{item['id']}/touch")
    assert touch_response.status_code == 200
    touched = touch_response.json()["item"]
    assert touched["id"] == item["id"]
    assert touched["order_index"] == 0
    assert isinstance(touched["last_used_at"], float)
    assert touched["updated_at"] >= item["updated_at"]


def test_ai_git_saved_repos_keep_payload_order_and_reindex(client, auth_user):
    response = client.put(
        "/api/ai-git-repos",
        json={
            "items": [
                {
                    "name": "third",
                    "entry_id": "entry-local",
                    "cwd": r"D:\demo\third",
                },
                {
                    "name": "first",
                    "entry_id": "entry-local",
                    "cwd": r"D:\demo\first",
                },
                {
                    "name": "second",
                    "entry_id": "entry-local",
                    "cwd": r"D:\demo\second",
                },
            ],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert [item["name"] for item in payload["items"]] == ["third", "first", "second"]
    assert [item["order_index"] for item in payload["items"]] == [0, 1, 2]

    reordered = client.put(
        "/api/ai-git-repos",
        json={
            "items": [payload["items"][1], payload["items"][2], payload["items"][0]],
        },
    )

    assert reordered.status_code == 200
    reordered_payload = reordered.json()
    assert [item["name"] for item in reordered_payload["items"]] == ["first", "second", "third"]
    assert [item["order_index"] for item in reordered_payload["items"]] == [0, 1, 2]


def test_ai_git_repo_statuses_can_detect_dirty_local_repo(client, auth_user, test_device, tmp_path):
    repo_path = tmp_path / "saved-repo-status"
    _init_git_repo(repo_path)
    (repo_path / "feature.txt").write_text("dirty\n", encoding="utf-8")

    entry_resp = client.post(
        "/api/devices/add",
        json={
            "mode": "local",
            "token": "local-entry-token",
            "alias": "当前机器",
        },
    )
    assert entry_resp.status_code == 200
    entry_id = entry_resp.json()["id"]

    save_response = client.put(
        "/api/ai-git-repos",
        json={
            "items": [
                {
                    "name": "saved-repo-status",
                    "entry_id": entry_id,
                    "cwd": str(repo_path),
                }
            ],
        },
    )
    assert save_response.status_code == 200
    repo_id = save_response.json()["items"][0]["id"]

    status_response = client.post(
        "/api/ai-git-repos/statuses",
        json={"repo_ids": [repo_id]},
    )

    assert status_response.status_code == 200
    payload = status_response.json()
    assert len(payload["items"]) == 1
    item = payload["items"][0]
    assert item["repo_id"] == repo_id
    assert item["ok"] is True
    assert item["clean"] is False
    assert item["repo_root"] == str(repo_path.resolve())
    assert item["branch"]
    assert item["changed_file_count"] == 1
    assert item["changed_paths"] == ["feature.txt"]
    assert item["error"] is None


def test_ai_git_repo_statuses_report_missing_entry_error(client, auth_user):
    save_response = client.put(
        "/api/ai-git-repos",
        json={
            "items": [
                {
                    "name": "missing-entry-repo",
                    "entry_id": "missing-entry",
                    "cwd": r"D:\demo\missing",
                }
            ],
        },
    )
    assert save_response.status_code == 200

    status_response = client.post("/api/ai-git-repos/statuses", json={})
    assert status_response.status_code == 200
    payload = status_response.json()
    assert payload["items"] == [
        {
            "repo_id": save_response.json()["items"][0]["id"],
            "name": "missing-entry-repo",
            "entry_id": "missing-entry",
            "cwd": r"D:\demo\missing",
            "ok": False,
            "clean": None,
            "branch": "",
            "branch_status": "",
            "repo_root": None,
            "changed_file_count": 0,
            "changed_paths": [],
            "error": "关联设备不存在或已停用",
            "split_recommended": False,
            "split_reason": "",
            "oversized": False,
            "suggested_split_groups": [],
            "estimated_changed_line_count": 0,
        }
    ]
