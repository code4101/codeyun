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


def _create_many_dirty_files(repo_path, *, count: int):
    for index in range(count):
        if index % 2 == 0:
            relative_path = repo_path / "frontend" / f"view_{index}.ts"
        else:
            relative_path = repo_path / "backend" / f"worker_{index}.py"
        relative_path.parent.mkdir(parents=True, exist_ok=True)
        relative_path.write_text(f"line {index}\n", encoding="utf-8")


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


def test_git_tools_context_switches_to_overview_mode_for_large_change_set(client, test_device, tmp_path):
    repo_path = tmp_path / "git-large-context-repo"
    _init_git_repo(repo_path)
    _create_many_dirty_files(repo_path, count=84)

    response = client.post(
        "/api/git-tools/context",
        json={"cwd": str(repo_path), "max_files": 8},
        headers={"X-Device-Token": test_device["token"]},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["oversized"] is True
    assert payload["split_recommended"] is True
    assert payload["context_mode"] == "overview_only"
    assert payload["context_truncated"] is True
    assert payload["changed_file_count"] == 84
    assert payload["selected_paths"]
    assert payload["suggested_split_groups"]
    assert "frontend" in {item["label"] for item in payload["suggested_split_groups"]}
    assert "backend" in {item["label"] for item in payload["suggested_split_groups"]}
    assert "建议拆分" in payload["prompt_context"] or "提交规模提示" in payload["prompt_context"]


def test_git_tools_inspect_marks_large_untracked_file_as_split_recommended(client, test_device, tmp_path):
    repo_path = tmp_path / "git-large-untracked-file-repo"
    _init_git_repo(repo_path)
    (repo_path / "huge.txt").write_text("line\n" * 9000, encoding="utf-8")

    response = client.post(
        "/api/git-tools/inspect",
        json={"cwd": str(repo_path)},
        headers={"X-Device-Token": test_device["token"]},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["split_recommended"] is True
    assert payload["estimated_changed_line_count"] >= 9000


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


def test_local_entry_git_generate_message_forces_split_for_large_change_set(client, auth_user, test_device, tmp_path, monkeypatch):
    repo_path = tmp_path / "git-generate-large-repo"
    _init_git_repo(repo_path)
    _create_many_dirty_files(repo_path, count=84)

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
                    "subject": "整理超大提交",
                    "body": ["给出一个粗略总结"],
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
    assert payload["subject"] == "整理超大提交"
    assert payload["needs_split"] is True
    assert payload["reason"]
    assert payload["inspect"]["oversized"] is True
    assert payload["inspect"]["split_recommended"] is True


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


def test_local_entry_git_generate_and_commit_runs_end_to_end(client, auth_user, test_device, tmp_path, monkeypatch):
    repo_path = tmp_path / "git-generate-and-commit-repo"
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

    monkeypatch.setattr(
        "backend.core.ai_git_commit.chat_with_provider",
        lambda **_: {
            "model": "qwen3:14b",
            "content": json.dumps(
                {
                    "subject": "整理本地一键 AI 提交",
                    "body": ["让 AI 生成后直接完成提交"],
                    "needs_split": False,
                    "reason": "",
                },
                ensure_ascii=False,
            ),
        },
    )

    response = client.post(
        f"/api/device-entries/{entry_id}/git/generate-and-commit",
        json={
            "cwd": str(repo_path),
            "provider": "ollama",
            "model": "qwen3:14b",
            "style": "summary",
            "include_body": True,
            "add_all": True,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["subject"] == "整理本地一键 AI 提交"
    assert payload["inspect"]["repo_root"] == str(repo_path.resolve())
    assert payload["inspect"]["clean"] is False
    assert payload["commit"]["summary"] == "整理本地一键 AI 提交"
    assert payload["commit"]["clean"] is True
    assert payload["commit"]["commit_hash"]
    assert _run_git(repo_path, "log", "-1", "--pretty=%s") == "整理本地一键 AI 提交"


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


def test_remote_entry_git_generate_and_commit_reads_context_then_commits(client, session, auth_user, monkeypatch):
    entry = UserDevice(
        user_id=auth_user.id,
        device_id="remote-device-2",
        mode="remote",
        name="Remote Device",
        server_url="http://remote-device:8000",
        token="remote-token",
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)

    captured_calls = []
    remote_context_payload = GitToolContextResponse(
        cwd="/workspace/demo",
        repo_root="/workspace/demo",
        branch="main",
        branch_status="## main",
        clean=False,
        status_lines=[" M app.py"],
        diff_stat=" app.py | 2 ++",
        staged_diff_stat="",
        changed_files=[
            {"path": "app.py", "status": " M", "staged": False, "unstaged": True, "untracked": False},
        ],
        prompt_context="仓库概览\n- 当前分支: main\n\n重点文件变更片段\n### 文件: app.py\n[未暂存差异]\n+ hello",
        selected_paths=["app.py"],
        omitted_path_count=0,
        context_truncated=False,
    ).model_dump()
    remote_commit_payload = {
        "cwd": "/workspace/demo",
        "repo_root": "/workspace/demo",
        "branch": "main",
        "commit_hash": "abc123def456",
        "short_hash": "abc123d",
        "summary": "整理远程一键 AI 提交",
        "full_message": "整理远程一键 AI 提交\n\n- 支持远程仓库直接提交",
        "clean": True,
        "status_lines": [],
    }

    class FakeResponse:
        def __init__(self, payload):
            self.status_code = 200
            self.headers = {"content-type": "application/json"}
            self.text = ""
            self._payload = payload

        def json(self):
            return self._payload

    def fake_request(method, url, headers=None, params=None, json=None, timeout=None, stream=False):
        captured_calls.append(
            {
                "method": method,
                "url": url,
                "headers": headers,
                "json": json,
                "timeout": timeout,
            }
        )
        if url.endswith("/api/git-tools/context"):
            return FakeResponse(remote_context_payload)
        if url.endswith("/api/git-tools/commit"):
            return FakeResponse(remote_commit_payload)
        raise AssertionError(f"unexpected remote request: {url}")

    monkeypatch.setattr("backend.api.device_entries.requests.request", fake_request)
    monkeypatch.setattr(
        "backend.core.ai_git_commit.chat_with_provider",
        lambda **_: {
            "model": "deepseek-chat",
            "content": json.dumps(
                {
                    "subject": "整理远程一键 AI 提交",
                    "body": ["支持远程仓库直接提交"],
                    "needs_split": False,
                    "reason": "",
                },
                ensure_ascii=False,
            ),
        },
    )

    response = client.post(
        f"/api/device-entries/{entry.entry_id}/git/generate-and-commit",
        json={
            "cwd": "/workspace/demo",
            "provider": "deepseek",
            "model": "deepseek-chat",
            "style": "summary",
            "include_body": True,
            "add_all": True,
            "max_files": 6,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["subject"] == "整理远程一键 AI 提交"
    assert payload["inspect"]["repo_root"] == "/workspace/demo"
    assert payload["commit"]["short_hash"] == "abc123d"
    assert len(captured_calls) == 2
    assert captured_calls[0]["method"] == "POST"
    assert captured_calls[0]["url"] == "http://remote-device:8000/api/git-tools/context"
    assert captured_calls[0]["headers"]["Authorization"] == "Bearer remote-token"
    assert captured_calls[0]["json"]["cwd"] == "/workspace/demo"
    assert captured_calls[0]["json"]["max_files"] == 6
    assert captured_calls[1]["url"] == "http://remote-device:8000/api/git-tools/commit"
    assert captured_calls[1]["json"]["subject"] == "整理远程一键 AI 提交"
    assert captured_calls[1]["json"]["body"] == ["支持远程仓库直接提交"]
    assert captured_calls[1]["json"]["add_all"] is True
