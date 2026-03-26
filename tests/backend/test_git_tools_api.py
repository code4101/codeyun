import json
import subprocess

from backend.api.git_tools import GitToolContextResponse
from backend.models import UserDevice


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


def test_git_tools_device_endpoint_can_inspect_repo(client, test_device, tmp_path):
    repo_path = tmp_path / "git-inspect-repo"
    _init_git_repo(repo_path)
    (repo_path / "README.md").write_text("# demo\n\nmore changes\n", encoding="utf-8")
    (repo_path / "notes.txt").write_text("hello\n", encoding="utf-8")

    response = client.post(
        "/api/git-tools/inspect",
        json={"cwd": str(repo_path)},
        headers={"X-Device-Token": test_device["token"]},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["repo_root"] == str(repo_path.resolve())
    assert payload["clean"] is False
    assert payload["branch"]
    changed_paths = {item["path"] for item in payload["changed_files"]}
    assert "README.md" in changed_paths
    assert "notes.txt" in changed_paths


def test_local_entry_git_generate_message_uses_ai_draft(client, auth_user, test_device, tmp_path, monkeypatch):
    repo_path = tmp_path / "git-generate-repo"
    _init_git_repo(repo_path)
    (repo_path / "app.py").write_text("print('hello')\nprint('world')\n", encoding="utf-8")

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

    monkeypatch.setattr(
        "backend.core.ai_git_commit.chat_with_provider",
        lambda **_: {
            "model": "qwen3:14b",
            "content": json.dumps(
                {
                    "subject": "整理本地 Git 提交工具",
                    "body": ["补齐仓库变更读取能力", "支持 AI 生成提交标题和正文"],
                    "needs_split": False,
                    "reason": "",
                },
                ensure_ascii=False,
            ),
        },
    )

    response = client.post(
        f"/api/device-entries/{entry_id}/git/generate-message",
        json={
            "cwd": str(repo_path),
            "provider": "ollama",
            "model": "qwen3:14b",
            "style": "summary",
            "include_body": True,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["subject"] == "整理本地 Git 提交工具"
    assert payload["body"] == ["补齐仓库变更读取能力", "支持 AI 生成提交标题和正文"]
    assert payload["inspect"]["repo_root"] == str(repo_path.resolve())
    assert payload["inspect"]["clean"] is False


def test_local_entry_git_commit_creates_commit(client, auth_user, test_device, tmp_path):
    repo_path = tmp_path / "git-commit-repo"
    _init_git_repo(repo_path)
    (repo_path / "feature.txt").write_text("new feature\n", encoding="utf-8")

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

    response = client.post(
        f"/api/device-entries/{entry_id}/git/commit",
        json={
            "cwd": str(repo_path),
            "subject": "新增 Git 提交工具测试",
            "body": ["补充本地提交接口回归用例"],
            "add_all": True,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"] == "新增 Git 提交工具测试"
    assert payload["clean"] is True
    assert payload["commit_hash"]
    assert _run_git(repo_path, "log", "-1", "--pretty=%s") == "新增 Git 提交工具测试"


def test_remote_entry_git_generate_message_reads_remote_context(client, session, auth_user, monkeypatch):
    entry = UserDevice(
        user_id=auth_user.id,
        device_id="remote-device-1",
        mode="remote",
        name="Remote Device",
        server_url="http://remote-device:8000",
        token="remote-token",
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)

    captured = {}
    remote_payload = GitToolContextResponse(
        cwd="/workspace/demo",
        repo_root="/workspace/demo",
        branch="main",
        branch_status="## main",
        clean=False,
        status_lines=[" M app.py", "?? docs/plan.md"],
        diff_stat=" app.py | 2 ++",
        staged_diff_stat="",
        changed_files=[
            {"path": "app.py", "status": " M", "staged": False, "unstaged": True, "untracked": False},
            {"path": "docs/plan.md", "status": "??", "staged": False, "unstaged": False, "untracked": True},
        ],
        prompt_context="仓库概览\n- 当前分支: main\n\n重点文件变更片段\n### 文件: app.py\n[未暂存差异]\n+ hello",
        selected_paths=["app.py"],
        omitted_path_count=1,
        context_truncated=False,
    ).model_dump()

    class FakeResponse:
        status_code = 200
        headers = {"content-type": "application/json"}
        text = ""

        def json(self):
            return remote_payload

    def fake_request(method, url, headers=None, params=None, json=None, timeout=None, stream=False):
        captured["method"] = method
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr("backend.api.device_entries.requests.request", fake_request)
    monkeypatch.setattr(
        "backend.core.ai_git_commit.chat_with_provider",
        lambda **_: {
            "model": "deepseek-chat",
            "content": json.dumps(
                {
                    "subject": "feat: 补充远程 Git 提交草稿生成",
                    "body": ["支持通过设备代理读取远程仓库上下文"],
                    "needs_split": False,
                    "reason": "",
                },
                ensure_ascii=False,
            ),
        },
    )

    response = client.post(
        f"/api/device-entries/{entry.entry_id}/git/generate-message",
        json={
            "cwd": "/workspace/demo",
            "provider": "deepseek",
            "model": "deepseek-chat",
            "style": "conventional",
            "include_body": True,
            "max_files": 6,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["subject"] == "feat: 补充远程 Git 提交草稿生成"
    assert payload["inspect"]["repo_root"] == "/workspace/demo"
    assert captured["method"] == "POST"
    assert captured["url"] == "http://remote-device:8000/api/git-tools/context"
    assert captured["headers"]["Authorization"] == "Bearer remote-token"
    assert captured["headers"]["X-Device-Token"] == "remote-token"
    assert captured["json"]["cwd"] == "/workspace/demo"
    assert captured["json"]["max_files"] == 6
