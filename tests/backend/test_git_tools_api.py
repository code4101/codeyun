import json
import subprocess
import time

import pytest

from backend.api.git_tools import GitToolContextResponse
from backend.core.ai_chat_user_config import save_user_ai_chat_provider_config
from backend.core.git_tools import GitToolError, create_git_commit
from backend.core.ollama_access_keys import create_ollama_access_key
from backend.models import UserDevice


@pytest.fixture(autouse=True)
def _configure_test_ollama_access(session, auth_user):
    created = create_ollama_access_key(session, created_by_user_id=auth_user.id, label="Git 工具测试 Key")
    save_user_ai_chat_provider_config(
        session,
        auth_user.id,
        "ollama",
        api_key=created["plaintext_value"],
    )
    return created["plaintext_value"]


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


def test_git_tools_context_keeps_unicode_paths_readable(client, test_device, tmp_path):
    repo_path = tmp_path / "git-unicode-repo"
    _init_git_repo(repo_path)

    tracked_path = repo_path / "src" / "中文说明.py"
    tracked_path.parent.mkdir(parents=True, exist_ok=True)
    tracked_path.write_text("print('hello')\n", encoding="utf-8")
    _run_git(repo_path, "add", "src/中文说明.py")
    _run_git(repo_path, "commit", "-m", "add unicode file")

    tracked_path.write_text("print('hello')\nprint('world')\n", encoding="utf-8")
    (repo_path / "src" / "中文 计划.ipynb").write_text("{\"cells\": []}\n", encoding="utf-8")

    inspect_response = client.post(
        "/api/git-tools/inspect",
        json={"cwd": str(repo_path)},
        headers={"X-Device-Token": test_device["token"]},
    )

    assert inspect_response.status_code == 200
    inspect_payload = inspect_response.json()
    changed_paths = {item["path"] for item in inspect_payload["changed_files"]}
    assert "src/中文说明.py" in changed_paths
    assert "src/中文 计划.ipynb" in changed_paths
    assert any(line.endswith("src/中文说明.py") for line in inspect_payload["status_lines"])
    assert any(line.endswith("src/中文 计划.ipynb") for line in inspect_payload["status_lines"])
    src_group = next(item for item in inspect_payload["suggested_split_groups"] if item["label"] == "src")
    assert "src/中文说明.py" in src_group["sample_paths"]
    assert "src/中文 计划.ipynb" in src_group["sample_paths"]

    context_response = client.post(
        "/api/git-tools/context",
        json={"cwd": str(repo_path), "max_files": 8},
        headers={"X-Device-Token": test_device["token"]},
    )

    assert context_response.status_code == 200
    context_payload = context_response.json()
    assert "src/中文说明.py" in context_payload["prompt_context"]
    assert "src/中文 计划.ipynb" in context_payload["prompt_context"]
    assert "\\345" not in context_payload["prompt_context"]


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


def test_git_tools_reduction_input_endpoint_returns_source_units(client, test_device, tmp_path):
    repo_path = tmp_path / "git-reduction-input-repo"
    _init_git_repo(repo_path)
    (repo_path / "README.md").write_text("# demo\n\nmore changes\n", encoding="utf-8")
    nested = repo_path / "src" / "plan.py"
    nested.parent.mkdir(parents=True, exist_ok=True)
    nested.write_text("print('hello')\n", encoding="utf-8")

    response = client.post(
        "/api/git-tools/reduction-input",
        json={"cwd": str(repo_path)},
        headers={"X-Device-Token": test_device["token"]},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["clean"] is False
    assert payload["source_unit_count"] == 2
    unit_map = {item["unit_id"]: item for item in payload["source_units"]}
    assert "README.md" in unit_map
    assert "src/plan.py" in unit_map
    assert "文件路径: README.md" in unit_map["README.md"]["content"]


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


def test_git_tools_inspect_reports_precheck_findings(client, test_device, tmp_path):
    repo_path = tmp_path / "git-precheck-repo"
    _init_git_repo(repo_path)
    (repo_path / "app.py").write_text(
        "DATABASE_URL = 'postgresql://demo:secret123@example.com/demo'\n",
        encoding="utf-8",
    )
    (repo_path / ".env").write_text(
        "OPENAI_API_KEY=sk-proj-abcdefghijklmnopqrstuvwxyz1234567890\n",
        encoding="utf-8",
    )
    (repo_path / "logs").mkdir(parents=True, exist_ok=True)
    (repo_path / "logs" / "app.log").write_text("runtime log\n", encoding="utf-8")

    response = client.post(
        "/api/git-tools/inspect",
        json={"cwd": str(repo_path)},
        headers={"X-Device-Token": test_device["token"]},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["precheck"]["issue_count"] >= 3
    assert payload["precheck"]["has_blocking_issues"] is True
    assert {item["issue_type"] for item in payload["precheck"]["issues"]} == {
        "ignore_candidate",
        "sensitive_content",
    }
    assert any(item["path"] == ".env" for item in payload["precheck"]["issues"])
    assert any(item["path"] == "app.py" for item in payload["precheck"]["issues"])
    assert any(item["path"] == "logs/app.log" for item in payload["precheck"]["issues"])

    app_issue = next(
        item for item in payload["precheck"]["issues"]
        if item["path"] == "app.py" and item["issue_type"] == "sensitive_content"
    )
    assert app_issue["blocking"] is False
    assert app_issue["severity"] == "warning"
    assert app_issue["line"] == 1
    assert app_issue["context_lines"]
    assert any(line["is_match"] for line in app_issue["context_lines"])
    assert any("secret123" in line["text"] for line in app_issue["context_lines"])

    env_issue = next(
        item for item in payload["precheck"]["issues"]
        if item["path"] == ".env" and item["issue_type"] == "sensitive_content"
    )
    assert env_issue["context_lines"]
    assert any("abcdefghijklmnopqrstuvwxyz1234567890" in line["text"] for line in env_issue["context_lines"])


def test_git_tools_inspect_blocks_dot_tmp_directory(client, test_device, tmp_path):
    repo_path = tmp_path / "git-precheck-dot-tmp-repo"
    _init_git_repo(repo_path)
    tmp_dir = repo_path / ".tmp_pdf_check"
    tmp_dir.mkdir()
    (tmp_dir / "page_1.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    response = client.post(
        "/api/git-tools/inspect",
        json={"cwd": str(repo_path)},
        headers={"X-Device-Token": test_device["token"]},
    )

    assert response.status_code == 200
    payload = response.json()
    issue = next(
        item for item in payload["precheck"]["issues"]
        if item["path"] == ".tmp_pdf_check/page_1.png"
    )
    assert issue["issue_type"] == "ignore_candidate"
    assert issue["blocking"] is True
    assert issue["severity"] == "error"
    assert issue["suggestion"] == ".tmp_pdf_check/"


def test_git_tools_inspect_blocks_codex_tmp_artifacts(client, test_device, tmp_path):
    repo_path = tmp_path / "git-precheck-codex-tmp-repo"
    _init_git_repo(repo_path)
    tmp_dir = repo_path / ".codex_tmp"
    tmp_dir.mkdir()
    (tmp_dir / "frame.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    response = client.post(
        "/api/git-tools/inspect",
        json={"cwd": str(repo_path)},
        headers={"X-Device-Token": test_device["token"]},
    )

    assert response.status_code == 200
    payload = response.json()
    issue = next(
        item for item in payload["precheck"]["issues"]
        if item["path"] == ".codex_tmp/frame.png"
    )
    assert issue["issue_type"] == "local_artifact"
    assert issue["blocking"] is True
    assert issue["severity"] == "error"


def test_git_tools_inspect_blocks_root_dev_logs(client, test_device, tmp_path):
    repo_path = tmp_path / "git-precheck-root-dev-log-repo"
    _init_git_repo(repo_path)
    (repo_path / ".codex-dev-current.out.log").write_text("dev stdout\n", encoding="utf-8")
    (repo_path / ".dev_stderr.log").write_text("dev stderr\n", encoding="utf-8")
    (repo_path / "日志文件-2026-05-27-log.log").write_text("", encoding="utf-8")

    response = client.post(
        "/api/git-tools/inspect",
        json={"cwd": str(repo_path)},
        headers={"X-Device-Token": test_device["token"]},
    )

    assert response.status_code == 200
    payload = response.json()
    issues = {
        item["path"]: item
        for item in payload["precheck"]["issues"]
        if item["path"] in {
            ".codex-dev-current.out.log",
            ".dev_stderr.log",
            "日志文件-2026-05-27-log.log",
        }
    }
    assert set(issues) == {
        ".codex-dev-current.out.log",
        ".dev_stderr.log",
        "日志文件-2026-05-27-log.log",
    }
    assert {item["issue_type"] for item in issues.values()} == {"local_artifact"}
    assert all(item["blocking"] is True for item in issues.values())


def test_git_tools_precheck_allows_nested_source_logs_route(client, test_device, tmp_path):
    repo_path = tmp_path / "git-precheck-source-logs-repo"
    _init_git_repo(repo_path)
    route_dir = repo_path / "frontend" / "src" / "standard" / "cluster" / "logs"
    route_dir.mkdir(parents=True)
    (route_dir / "page.vue").write_text("<template>logs</template>\n", encoding="utf-8")

    response = client.post(
        "/api/git-tools/inspect",
        json={"cwd": str(repo_path)},
        headers={"X-Device-Token": test_device["token"]},
    )

    assert response.status_code == 200
    payload = response.json()
    assert not any(
        item["path"] == "frontend/src/standard/cluster/logs/page.vue"
        for item in payload["precheck"]["issues"]
    )


def test_git_tools_precheck_does_not_treat_route_password_path_as_secret_assignment(client, test_device, tmp_path):
    repo_path = tmp_path / "git-precheck-route-repo"
    _init_git_repo(repo_path)
    (repo_path / "admin.py").write_text(
        '@accounts_router.post("/accounts/{user_id}/password", response_model=UserRead)\n',
        encoding="utf-8",
    )

    response = client.post(
        "/api/git-tools/inspect",
        json={"cwd": str(repo_path)},
        headers={"X-Device-Token": test_device["token"]},
    )

    assert response.status_code == 200
    payload = response.json()
    assert not any(
        item["path"] == "admin.py" and item["issue_type"] == "sensitive_content"
        for item in payload["precheck"]["issues"]
    )


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


def test_local_entry_git_reduce_message_runs_hierarchical_draft(client, auth_user, test_device, tmp_path, monkeypatch):
    repo_path = tmp_path / "git-reduce-repo"
    _init_git_repo(repo_path)
    for index in range(10):
        relative = repo_path / ("frontend" if index % 2 == 0 else "backend") / f"file_{index}.py"
        relative.parent.mkdir(parents=True, exist_ok=True)
        relative.write_text(f"line {index}\n", encoding="utf-8")

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
        "backend.core.ai_git_reduction.chat_with_provider",
        lambda **_: {
            "model": "qwen3.5:4b-instruct",
            "content": json.dumps(
                {
                    "topic": "Git 分层归并",
                    "summary": "分层归并多个文件块",
                    "key_points": ["先拆叶子块", "再逐层合并"],
                    "risk_points": [],
                    "candidate_subject": "整理 Git 分层归并提交流程",
                    "candidate_body": ["补齐 reduction 输入接口", "新增本地分层提交草稿生成"],
                    "should_split": False,
                    "reason": "",
                },
                ensure_ascii=False,
            ),
        },
    )

    response = client.post(
        f"/api/device-entries/{entry_id}/git/reduce",
        json={
            "cwd": str(repo_path),
            "provider": "ollama",
            "model": "qwen3.5:4b-instruct",
            "style": "summary",
            "include_body": True,
            "branch_factor": 3,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["subject"] == "整理 Git 分层归并提交流程"
    assert payload["inspect"]["repo_root"] == str(repo_path.resolve())
    assert payload["reduction"]["level_count"] == 3
    assert payload["reduction"]["source_unit_count"] == 10


def test_local_entry_git_reduce_and_commit_runs_end_to_end(client, auth_user, test_device, tmp_path, monkeypatch):
    repo_path = tmp_path / "git-reduce-and-commit-repo"
    _init_git_repo(repo_path)
    for index in range(10):
        relative = repo_path / ("frontend" if index % 2 == 0 else "backend") / f"file_{index}.py"
        relative.parent.mkdir(parents=True, exist_ok=True)
        relative.write_text(f"line {index}\n", encoding="utf-8")

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
        "backend.core.ai_git_reduction.chat_with_provider",
        lambda **_: {
            "model": "qwen3.5:4b-instruct",
            "content": json.dumps(
                {
                    "topic": "Git 分层归并",
                    "summary": "分层归并多个文件块",
                    "key_points": ["先拆叶子块", "再逐层合并"],
                    "risk_points": [],
                    "candidate_subject": "整理超大改动的一键提交流程",
                    "candidate_body": ["超大改动先做分层归并", "再直接落 Git commit"],
                    "should_split": False,
                    "reason": "",
                },
                ensure_ascii=False,
            ),
        },
    )

    response = client.post(
        f"/api/device-entries/{entry_id}/git/reduce-and-commit",
        json={
            "cwd": str(repo_path),
            "provider": "ollama",
            "model": "qwen3.5:4b-instruct",
            "style": "summary",
            "include_body": True,
            "branch_factor": 3,
            "add_all": True,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["subject"] == "整理超大改动的一键提交流程"
    assert payload["inspect"]["repo_root"] == str(repo_path.resolve())
    assert payload["reduction"]["source_unit_count"] == 10
    assert payload["commit"]["summary"] == "整理超大改动的一键提交流程"
    assert payload["commit"]["clean"] is True
    assert _run_git(repo_path, "log", "-1", "--pretty=%s") == "整理超大改动的一键提交流程"


def test_local_entry_git_reduction_run_reports_progress_and_result(client, auth_user, test_device, tmp_path, monkeypatch):
    repo_path = tmp_path / "git-reduction-run-repo"
    _init_git_repo(repo_path)
    for index in range(10):
        relative = repo_path / ("frontend" if index % 2 == 0 else "backend") / f"file_{index}.py"
        relative.parent.mkdir(parents=True, exist_ok=True)
        relative.write_text(f"line {index}\n", encoding="utf-8")

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
        "backend.core.ai_git_reduction.chat_with_provider",
        lambda **_: {
            "model": "qwen3.5:4b-instruct",
            "content": json.dumps(
                {
                    "topic": "Git 分层归并",
                    "summary": "分层归并多个文件块",
                    "key_points": ["先拆叶子块", "再逐层合并"],
                    "risk_points": [],
                    "candidate_subject": "整理 Git 异步拆分进度",
                    "candidate_body": ["增加 reduction run 状态接口"],
                    "should_split": False,
                    "reason": "",
                },
                ensure_ascii=False,
            ),
        },
    )

    start_response = client.post(
        f"/api/device-entries/{entry_id}/git/reduce-runs",
        json={
            "cwd": str(repo_path),
            "provider": "ollama",
            "model": "qwen3.5:4b-instruct",
            "style": "summary",
            "include_body": True,
            "branch_factor": 3,
            "auto_commit": False,
            "add_all": True,
        },
    )

    assert start_response.status_code == 200
    run_payload = start_response.json()
    assert run_payload["status"] == "running"
    run_id = run_payload["id"]

    final_payload = run_payload
    for _ in range(80):
        response = client.get(f"/api/device-entries/{entry_id}/git/reduce-runs/{run_id}")
        assert response.status_code == 200
        final_payload = response.json()
        if final_payload["status"] != "running":
            break
        time.sleep(0.05)

    assert final_payload["status"] == "completed"
    assert final_payload["source_unit_count"] == 10
    assert final_payload["completed_chunk_count"] >= 1
    assert final_payload["result"]["subject"] == "整理 Git 异步拆分进度"
    assert final_payload["commit"] is None


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


def test_create_git_commit_does_not_commit_ignored_codex_tmp_artifacts(tmp_path):
    repo_path = tmp_path / "git-commit-ignore-codex-tmp-repo"
    _init_git_repo(repo_path)
    (repo_path / ".gitignore").write_text("/.codex_tmp/\n", encoding="utf-8")
    _run_git(repo_path, "add", ".gitignore")
    _run_git(repo_path, "commit", "-m", "ignore local artifacts")
    (repo_path / "feature.txt").write_text("new feature\n", encoding="utf-8")
    tmp_dir = repo_path / ".codex_tmp"
    tmp_dir.mkdir()
    (tmp_dir / "frame.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    payload = create_git_commit(
        str(repo_path),
        subject="提交源码改动",
        body=["不提交 Codex 本地产物"],
        add_all=True,
    )

    assert payload["clean"] is True
    committed_paths = _run_git(repo_path, "show", "--name-only", "--pretty=", "HEAD")
    assert "feature.txt" in committed_paths
    assert ".codex_tmp/frame.png" not in committed_paths


def test_create_git_commit_blocks_staged_codex_tmp_artifacts(tmp_path):
    repo_path = tmp_path / "git-commit-block-codex-tmp-repo"
    _init_git_repo(repo_path)
    tmp_dir = repo_path / ".codex_tmp"
    tmp_dir.mkdir()
    (tmp_dir / "frame.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    _run_git(repo_path, "add", "-f", ".codex_tmp/frame.png")

    with pytest.raises(GitToolError, match="Codex/调试/运行时本地产物"):
        create_git_commit(
            str(repo_path),
            subject="误提交本地产物",
            body=[],
            add_all=True,
        )


def test_local_entry_git_commit_allows_sensitive_precheck_warnings_when_add_all_enabled(client, auth_user, test_device, tmp_path):
    repo_path = tmp_path / "git-commit-precheck-warning-repo"
    _init_git_repo(repo_path)
    (repo_path / "app.py").write_text(
        "DATABASE_URL = 'postgresql://demo:secret123@example.com/demo'\n",
        encoding="utf-8",
    )

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
            "subject": "敏感预检仅提醒",
            "body": ["保留提示，但不再阻断提交"],
            "add_all": True,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"] == "敏感预检仅提醒"
    assert payload["clean"] is True
    assert _run_git(repo_path, "log", "-1", "--pretty=%s") == "敏感预检仅提醒"


def test_local_entry_git_commit_only_checks_staged_scope_when_add_all_disabled(client, auth_user, test_device, tmp_path):
    repo_path = tmp_path / "git-commit-staged-scope-repo"
    _init_git_repo(repo_path)
    (repo_path / "feature.txt").write_text("new feature\n", encoding="utf-8")
    _run_git(repo_path, "add", "feature.txt")
    (repo_path / ".env").write_text(
        "OPENAI_API_KEY=sk-proj-abcdefghijklmnopqrstuvwxyz1234567890\n",
        encoding="utf-8",
    )

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
            "subject": "仅提交已暂存改动",
            "body": ["未暂存的敏感文件不应阻断这次提交"],
            "add_all": False,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"] == "仅提交已暂存改动"
    assert payload["clean"] is False
    assert _run_git(repo_path, "log", "-1", "--pretty=%s") == "仅提交已暂存改动"
    assert "?? .env" in _run_git(repo_path, "status", "--short")


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

    def fake_request(method, url, headers=None, params=None, json=None, proxies=None, timeout=None, stream=False):
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


def test_remote_entry_git_reduce_reads_remote_reduction_input(client, session, auth_user, monkeypatch):
    entry = UserDevice(
        user_id=auth_user.id,
        device_id="remote-device-3",
        mode="remote",
        name="Remote Device",
        server_url="http://remote-device:8000",
        token="remote-token",
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)

    captured = {}
    remote_payload = {
        "cwd": "/workspace/demo",
        "repo_root": "/workspace/demo",
        "branch": "main",
        "branch_status": "## main",
        "clean": False,
        "status_lines": [" M app.py", " M docs/plan.md"],
        "diff_stat": " app.py | 2 ++",
        "staged_diff_stat": "",
        "changed_files": [
            {"path": "app.py", "status": " M", "staged": False, "unstaged": True, "untracked": False},
            {"path": "docs/plan.md", "status": " M", "staged": False, "unstaged": True, "untracked": False},
        ],
        "changed_file_count": 2,
        "estimated_changed_line_count": 12,
        "split_recommended": False,
        "split_reason": "",
        "oversized": False,
        "suggested_split_groups": [{"label": "docs", "file_count": 1, "sample_paths": ["docs/plan.md"]}],
        "source_units": [
            {
                "unit_id": "app.py",
                "path": "app.py",
                "group": "(仓库根目录)",
                "content": "文件路径: app.py\n状态: 未暂存\n[未暂存差异]\n+ hello",
                "truncated": False,
            },
            {
                "unit_id": "docs/plan.md",
                "path": "docs/plan.md",
                "group": "docs",
                "content": "文件路径: docs/plan.md\n状态: 未暂存\n[未暂存差异]\n+ plan",
                "truncated": False,
            },
        ],
        "source_unit_count": 2,
        "source_unit_truncated_count": 0,
    }

    class FakeResponse:
        status_code = 200
        headers = {"content-type": "application/json"}
        text = ""

        def json(self):
            return remote_payload

    def fake_request(method, url, headers=None, params=None, json=None, proxies=None, timeout=None, stream=False):
        captured["method"] = method
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr("backend.api.device_entries.requests.request", fake_request)
    monkeypatch.setattr(
        "backend.core.ai_git_reduction.chat_with_provider",
        lambda **_: {
            "model": "deepseek-chat",
            "content": json.dumps(
                {
                    "topic": "远程分层归并",
                    "summary": "从远程设备读取 source units 后在主节点归并",
                    "key_points": ["远程只返回 reduction 输入", "主节点负责实际 AI 汇总"],
                    "risk_points": [],
                    "candidate_subject": "整理远程 Git 分层归并入口",
                    "candidate_body": ["支持远程 reduction-input 拉取", "保留主节点统一的 AI 配置执行"],
                    "should_split": False,
                    "reason": "",
                },
                ensure_ascii=False,
            ),
        },
    )

    response = client.post(
        f"/api/device-entries/{entry.entry_id}/git/reduce",
        json={
            "cwd": "/workspace/demo",
            "provider": "deepseek",
            "model": "deepseek-chat",
            "style": "summary",
            "include_body": True,
            "branch_factor": 4,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["subject"] == "整理远程 Git 分层归并入口"
    assert payload["inspect"]["repo_root"] == "/workspace/demo"
    assert payload["reduction"]["source_unit_count"] == 2
    assert captured["method"] == "POST"
    assert captured["url"] == "http://remote-device:8000/api/git-tools/reduction-input"
    assert captured["headers"]["Authorization"] == "Bearer remote-token"
    assert captured["headers"]["X-Device-Token"] == "remote-token"
    assert captured["json"]["cwd"] == "/workspace/demo"


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

    def fake_request(method, url, headers=None, params=None, json=None, proxies=None, timeout=None, stream=False):
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


def test_remote_entry_git_reduce_and_commit_reads_reduction_input_then_commits(client, session, auth_user, monkeypatch):
    entry = UserDevice(
        user_id=auth_user.id,
        device_id="remote-device-4",
        mode="remote",
        name="Remote Device",
        server_url="http://remote-device:8000",
        token="remote-token",
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)

    captured_calls = []
    remote_reduction_payload = {
        "cwd": "/workspace/demo",
        "repo_root": "/workspace/demo",
        "branch": "main",
        "branch_status": "## main",
        "clean": False,
        "status_lines": [" M app.py", " M docs/plan.md"],
        "diff_stat": " app.py | 2 ++",
        "staged_diff_stat": "",
        "changed_files": [
            {"path": "app.py", "status": " M", "staged": False, "unstaged": True, "untracked": False},
            {"path": "docs/plan.md", "status": " M", "staged": False, "unstaged": True, "untracked": False},
        ],
        "changed_file_count": 2,
        "estimated_changed_line_count": 12,
        "split_recommended": True,
        "split_reason": "建议拆分",
        "oversized": True,
        "suggested_split_groups": [{"label": "docs", "file_count": 1, "sample_paths": ["docs/plan.md"]}],
        "source_units": [
            {
                "unit_id": "app.py",
                "path": "app.py",
                "group": "(仓库根目录)",
                "content": "文件路径: app.py\n状态: 未暂存\n[未暂存差异]\n+ hello",
                "truncated": False,
            },
            {
                "unit_id": "docs/plan.md",
                "path": "docs/plan.md",
                "group": "docs",
                "content": "文件路径: docs/plan.md\n状态: 未暂存\n[未暂存差异]\n+ plan",
                "truncated": False,
            },
        ],
        "source_unit_count": 2,
        "source_unit_truncated_count": 0,
    }
    remote_commit_payload = {
        "cwd": "/workspace/demo",
        "repo_root": "/workspace/demo",
        "branch": "main",
        "commit_hash": "abc123def456",
        "short_hash": "abc123d",
        "summary": "整理远程超大改动的一键提交",
        "full_message": "整理远程超大改动的一键提交\n\n- 先分层归并，再直接提交",
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

    def fake_request(method, url, headers=None, params=None, json=None, proxies=None, timeout=None, stream=False):
        captured_calls.append(
            {
                "method": method,
                "url": url,
                "headers": headers,
                "json": json,
                "timeout": timeout,
            }
        )
        if url.endswith("/api/git-tools/reduction-input"):
            return FakeResponse(remote_reduction_payload)
        if url.endswith("/api/git-tools/commit"):
            return FakeResponse(remote_commit_payload)
        raise AssertionError(f"unexpected remote request: {url}")

    monkeypatch.setattr("backend.api.device_entries.requests.request", fake_request)
    monkeypatch.setattr(
        "backend.core.ai_git_reduction.chat_with_provider",
        lambda **_: {
            "model": "deepseek-chat",
            "content": json.dumps(
                {
                    "topic": "远程分层归并",
                    "summary": "从远程设备读取 source units 后在主节点归并",
                    "key_points": ["远程只返回 reduction 输入", "主节点负责实际 AI 汇总"],
                    "risk_points": [],
                    "candidate_subject": "整理远程超大改动的一键提交",
                    "candidate_body": ["先分层归并，再直接提交"],
                    "should_split": False,
                    "reason": "",
                },
                ensure_ascii=False,
            ),
        },
    )

    response = client.post(
        f"/api/device-entries/{entry.entry_id}/git/reduce-and-commit",
        json={
            "cwd": "/workspace/demo",
            "provider": "deepseek",
            "model": "deepseek-chat",
            "style": "summary",
            "include_body": True,
            "branch_factor": 4,
            "add_all": True,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["subject"] == "整理远程超大改动的一键提交"
    assert payload["inspect"]["repo_root"] == "/workspace/demo"
    assert payload["reduction"]["source_unit_count"] == 2
    assert payload["commit"]["short_hash"] == "abc123d"
    assert len(captured_calls) == 2
    assert captured_calls[0]["url"] == "http://remote-device:8000/api/git-tools/reduction-input"
    assert captured_calls[0]["json"]["cwd"] == "/workspace/demo"
    assert captured_calls[1]["url"] == "http://remote-device:8000/api/git-tools/commit"
    assert captured_calls[1]["json"]["subject"] == "整理远程超大改动的一键提交"
    assert captured_calls[1]["json"]["body"] == ["先分层归并，再直接提交"]
    assert captured_calls[1]["json"]["add_all"] is True


def test_remote_entry_git_reduction_run_auto_commit_polls_to_completion(client, session, auth_user, monkeypatch):
    entry = UserDevice(
        user_id=auth_user.id,
        device_id="remote-device-5",
        mode="remote",
        name="Remote Device",
        server_url="http://remote-device:8000",
        token="remote-token",
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)

    captured_calls = []
    remote_reduction_payload = {
        "cwd": "/workspace/demo",
        "repo_root": "/workspace/demo",
        "branch": "main",
        "branch_status": "## main",
        "clean": False,
        "status_lines": [" M app.py"],
        "diff_stat": " app.py | 2 ++",
        "staged_diff_stat": "",
        "changed_files": [
            {"path": "app.py", "status": " M", "staged": False, "unstaged": True, "untracked": False},
        ],
        "changed_file_count": 1,
        "estimated_changed_line_count": 8,
        "split_recommended": True,
        "split_reason": "建议拆分",
        "oversized": True,
        "suggested_split_groups": [],
        "source_units": [
            {
                "unit_id": "app.py",
                "path": "app.py",
                "group": "(仓库根目录)",
                "content": "文件路径: app.py\n状态: 未暂存\n[未暂存差异]\n+ hello",
                "truncated": False,
            },
        ],
        "source_unit_count": 1,
        "source_unit_truncated_count": 0,
    }
    remote_commit_payload = {
        "cwd": "/workspace/demo",
        "repo_root": "/workspace/demo",
        "branch": "main",
        "commit_hash": "abc123def456",
        "short_hash": "abc123d",
        "summary": "整理远程异步拆分提交",
        "full_message": "整理远程异步拆分提交\n\n- 先异步归并，再提交",
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

    def fake_request(method, url, headers=None, params=None, json=None, proxies=None, timeout=None, stream=False):
        captured_calls.append({"method": method, "url": url, "json": json})
        if url.endswith("/api/git-tools/reduction-input"):
            return FakeResponse(remote_reduction_payload)
        if url.endswith("/api/git-tools/commit"):
            return FakeResponse(remote_commit_payload)
        raise AssertionError(f"unexpected remote request: {url}")

    monkeypatch.setattr("backend.api.device_entries.requests.request", fake_request)
    monkeypatch.setattr(
        "backend.core.ai_git_reduction.chat_with_provider",
        lambda **_: {
            "model": "deepseek-chat",
            "content": json.dumps(
                {
                    "topic": "远程分层归并",
                    "summary": "从远程设备读取 source units 后在主节点归并",
                    "key_points": ["远程只返回 reduction 输入", "主节点负责实际 AI 汇总"],
                    "risk_points": [],
                    "candidate_subject": "整理远程异步拆分提交",
                    "candidate_body": ["先异步归并，再提交"],
                    "should_split": False,
                    "reason": "",
                },
                ensure_ascii=False,
            ),
        },
    )

    start_response = client.post(
        f"/api/device-entries/{entry.entry_id}/git/reduce-runs",
        json={
            "cwd": "/workspace/demo",
            "provider": "deepseek",
            "model": "deepseek-chat",
            "style": "summary",
            "include_body": True,
            "branch_factor": 4,
            "auto_commit": True,
            "add_all": True,
        },
    )

    assert start_response.status_code == 200
    run_id = start_response.json()["id"]

    final_payload = None
    for _ in range(20):
        response = client.get(f"/api/device-entries/{entry.entry_id}/git/reduce-runs/{run_id}")
        assert response.status_code == 200
        final_payload = response.json()
        if final_payload["status"] != "running":
            break
        time.sleep(0.05)

    assert final_payload is not None
    assert final_payload["status"] == "completed"
    assert final_payload["result"]["subject"] == "整理远程异步拆分提交"
    assert final_payload["commit"]["short_hash"] == "abc123d"
    assert captured_calls[0]["url"] == "http://remote-device:8000/api/git-tools/reduction-input"
    assert captured_calls[1]["url"] == "http://remote-device:8000/api/git-tools/commit"
