from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]


def _script_env(tmp_path: Path) -> dict[str, str]:
    data_dir = tmp_path / "data"
    db_path = tmp_path / "codeyun-test.db"
    env = os.environ.copy()
    env.update(
        {
            "CODEYUN_LOAD_DOTENV": "0",
            "CODEYUN_ENV": "test",
            "CODEYUN_DATA_DIR": str(data_dir),
            "CODEYUN_DATABASE_URL": f"sqlite:///{db_path}",
        }
    )
    return env


def _create_cli_user(env: dict[str, str]) -> None:
    setup_code = textwrap.dedent(
        """
        from sqlmodel import SQLModel, Session

        import backend.models
        from backend.db import engine
        from backend.models import User

        SQLModel.metadata.create_all(engine)
        with Session(engine) as session:
            session.add(
                User(
                    username="cli_user",
                    hashed_password="unused",
                    password_plain="unused",
                    is_active=True,
                    is_superuser=True,
                )
            )
            session.commit()
        """
    )
    subprocess.run(
        [sys.executable, "-c", setup_code],
        cwd=ROOT_DIR,
        env=env,
        check=True,
        text=True,
        capture_output=True,
    )


def _run_capture(env: dict[str, str], *args: str, stdin: str | None = None) -> dict:
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/ai_task_space_capture.py",
            "--username",
            "cli_user",
            "--source",
            "pytest",
            "--json",
            *args,
        ],
        input=stdin.encode("utf-8") if stdin is not None else None,
        cwd=ROOT_DIR,
        env=env,
        check=True,
        capture_output=True,
    )
    return json.loads(completed.stdout.decode("utf-8"))


def _run_capture_raw(
    env: dict[str, str],
    *args: str,
    stdin: str | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [
            sys.executable,
            "scripts/ai_task_space_capture.py",
            "--username",
            "cli_user",
            "--source",
            "pytest",
            "--json",
            *args,
        ],
        input=stdin.encode("utf-8") if stdin is not None else None,
        cwd=ROOT_DIR,
        env=env,
        check=check,
        capture_output=True,
    )


def _run_append(env: dict[str, str], *args: str, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [
            sys.executable,
            "scripts/ai_task_space_append_execution_record.py",
            "--username",
            "cli_user",
            "--json",
            *args,
        ],
        cwd=ROOT_DIR,
        env=env,
        check=check,
        capture_output=True,
    )


def _run_confirm(env: dict[str, str], *args: str, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [
            sys.executable,
            "scripts/ai_task_space_confirm_user_ready.py",
            "--username",
            "cli_user",
            "--json",
            *args,
        ],
        cwd=ROOT_DIR,
        env=env,
        check=check,
        capture_output=True,
    )


def _run_suggestion(env: dict[str, str], *args: str, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [
            sys.executable,
            "scripts/ai_task_space_planner_suggestion.py",
            "--username",
            "cli_user",
            "--json",
            *args,
        ],
        cwd=ROOT_DIR,
        env=env,
        check=check,
        capture_output=True,
    )


def _run_review_action(env: dict[str, str], *args: str, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [
            sys.executable,
            "scripts/ai_task_space_review_action.py",
            "--username",
            "cli_user",
            "--json",
            *args,
        ],
        cwd=ROOT_DIR,
        env=env,
        check=check,
        capture_output=True,
    )


def _run_plan_once(env: dict[str, str], *args: str, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [
            sys.executable,
            "scripts/ai_task_space_plan_once.py",
            "--username",
            "cli_user",
            "--json",
            *args,
        ],
        cwd=ROOT_DIR,
        env=env,
        check=check,
        capture_output=True,
    )


def _run_plan_once_text(env: dict[str, str], *args: str, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [
            sys.executable,
            "scripts/ai_task_space_plan_once.py",
            "--username",
            "cli_user",
            *args,
        ],
        cwd=ROOT_DIR,
        env=env,
        check=check,
        capture_output=True,
    )


def _run_validate(env: dict[str, str], *args: str, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [
            sys.executable,
            "scripts/ai_task_space_validate_automation_contract.py",
            "--username",
            "cli_user",
            "--json",
            *args,
        ],
        cwd=ROOT_DIR,
        env=env,
        check=check,
        capture_output=True,
    )


def _run_status(env: dict[str, str], *args: str, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [
            sys.executable,
            "scripts/ai_task_space_status.py",
            "--username",
            "cli_user",
            "--json",
            *args,
        ],
        cwd=ROOT_DIR,
        env=env,
        check=check,
        capture_output=True,
    )


def _run_sync_automation(env: dict[str, str], *args: str, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [
            sys.executable,
            "scripts/ai_task_space_sync_automation.py",
            "--username",
            "cli_user",
            "--json",
            *args,
        ],
        cwd=ROOT_DIR,
        env=env,
        check=check,
        capture_output=True,
    )


def _write_task_space_fixture(env: dict[str, str], *, duplicate_title: bool = False) -> Path:
    setup_code = textwrap.dedent(
        f"""
        from backend.core.ai_task_space import save_task_space, seed_task_space, user_task_space_path

        path = user_task_space_path(1)
        space = seed_task_space()
        space["tasks"][0]["id"] = "task_codeyun"
        space["tasks"][0]["title"] = "codeyun"
        space["tasks"][0]["status"] = "ready"
        space["tasks"][0]["executionPolicy"] = "auto_report"
        space["tasks"][0]["risk"] = "low"
        space["tasks"][0]["document"]["doneCriteria"] = ""
        if {duplicate_title!r}:
            duplicate = {{**space["tasks"][0], "id": "task_codeyun_duplicate"}}
            space["tasks"].append(duplicate)
        save_task_space(path, space)
        print(path)
        """
    )
    completed = subprocess.run(
        [sys.executable, "-c", setup_code],
        cwd=ROOT_DIR,
        env=env,
        check=True,
        text=True,
        capture_output=True,
    )
    return Path(completed.stdout.strip())


def _write_waiting_confirmation_fixture(env: dict[str, str]) -> Path:
    setup_code = textwrap.dedent(
        """
        from backend.core.ai_task_space import empty_document, save_task_space, user_task_space_path

        path = user_task_space_path(1)
        task = {
            "id": "task_waiting",
            "title": "等待确认任务",
            "kind": "task",
            "status": "ready",
            "parentId": None,
            "sortOrder": 0,
            "executionPolicy": "ask_before_execute",
            "risk": "medium",
            "dependsOn": [],
            "relatedTaskIds": [],
            "suggestedSkill": "",
            "document": {
                **empty_document(),
                "goal": "等待确认任务",
                "currentState": "等待用户确认后继续。",
                "nextStep": "等待用户确认后继续。",
            },
            "evidenceLog": [],
            "createdAt": "2026-06-19T00:00:00Z",
            "updatedAt": "2026-06-19T00:00:00Z",
            "executionRecords": [
                {
                    "id": "exec_wait",
                    "recordedAt": "2026-06-19T00:00:00Z",
                    "summary": "已整理建议，等待用户确认，未修改业务代码。",
                    "verification": "确认未修改业务代码。",
                    "remainingRisk": "需要用户确认范围。",
                    "nextStep": "等待用户确认后继续。",
                    "status": "progress",
                }
            ],
        }
        space = {"version": 2, "captures": [], "tasks": [task], "plannerLogs": [], "plannerSuggestions": []}
        save_task_space(path, space)
        print(path)
        """
    )
    completed = subprocess.run(
        [sys.executable, "-c", setup_code],
        cwd=ROOT_DIR,
        env=env,
        check=True,
        text=True,
        capture_output=True,
    )
    return Path(completed.stdout.strip())


def test_capture_cli_accepts_text_stdin_and_file_without_running_planning_check(tmp_path):
    env = _script_env(tmp_path)
    _create_cli_user(env)

    text_result = _run_capture(
        env,
        "--text",
        "命令行采集",
        "--tag",
        "constraint",
        "--tag",
        "codex",
        "--context-kind",
        "constraint",
        "--project-path",
        str(ROOT_DIR),
    )
    assert text_result["ok"] is True
    assert text_result["current_fingerprint"]
    assert text_result["capture"]["rawText"] == "命令行采集"
    assert text_result["capture"]["tags"] == ["constraint", "codex"]
    assert text_result["capture"]["contextKind"] == "constraint"
    assert text_result["capture"]["projectPath"] == str(ROOT_DIR)
    assert text_result["inbox_count"] == 1

    stdin_result = _run_capture(env, stdin="stdin 长文本采集")
    assert stdin_result["current_fingerprint"] != text_result["current_fingerprint"]
    assert stdin_result["capture"]["rawText"] == "stdin 长文本采集"
    assert stdin_result["inbox_count"] == 2

    capture_file = tmp_path / "capture.txt"
    capture_file.write_text("文件采集内容", encoding="utf-8")
    file_result = _run_capture(env, "--file", str(capture_file))
    assert file_result["capture"]["rawText"] == "文件采集内容"
    assert file_result["inbox_count"] == 3

    task_space = json.loads(Path(file_result["task_space_path"]).read_text(encoding="utf-8"))
    inbox_texts = [item["rawText"] for item in task_space["captures"] if item["status"] == "inbox"]
    assert inbox_texts[:3] == ["文件采集内容", "stdin 长文本采集", "命令行采集"]
    new_captures = [
        item
        for item in task_space["captures"]
        if item["rawText"] in {"文件采集内容", "stdin 长文本采集", "命令行采集"}
    ]
    assert len(new_captures) == 3
    assert all("linkedTaskId" not in item for item in new_captures)
    assert task_space["plannerLogs"] == []


def test_capture_cli_preserves_image_attachment(tmp_path):
    env = _script_env(tmp_path)
    _create_cli_user(env)
    image_path = tmp_path / "clipboard.png"
    image_path.write_bytes(b"\x89PNG\r\n\x1a\ncli-image")

    result = _run_capture(env, "--text", "带截图的任务采集", "--image", str(image_path))

    capture = result["capture"]
    assert capture["rawText"] == "带截图的任务采集"
    assert capture["attachments"][0]["name"] == "clipboard.png"
    assert capture["attachments"][0]["url"].startswith("/static/attachments/")
    assert (tmp_path / "data" / "attachments" / capture["attachments"][0]["filename"]).exists()


def test_capture_cli_preserves_concurrent_captures(tmp_path):
    env = _script_env(tmp_path)
    _create_cli_user(env)

    processes = [
        subprocess.Popen(
            [
                sys.executable,
                "scripts/ai_task_space_capture.py",
                "--username",
                "cli_user",
                "--source",
                "pytest",
                "--json",
                "--text",
                f"并发采集 {index}",
            ],
            cwd=ROOT_DIR,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for index in range(8)
    ]
    results = [process.communicate(timeout=30) for process in processes]

    for process, (stdout, stderr) in zip(processes, results, strict=True):
        assert process.returncode == 0, stderr.decode("utf-8", errors="replace")
        payload = json.loads(stdout.decode("utf-8"))
        assert payload["ok"] is True

    task_space_path = Path(json.loads(results[-1][0].decode("utf-8"))["task_space_path"])
    task_space = json.loads(task_space_path.read_text(encoding="utf-8"))
    inbox_texts = {item["rawText"] for item in task_space["captures"] if item["status"] == "inbox"}
    assert {f"并发采集 {index}" for index in range(8)} <= inbox_texts
    assert not task_space_path.with_name(f"{task_space_path.name}.lock").exists()


def test_capture_cli_rejects_blank_text(tmp_path):
    env = _script_env(tmp_path)
    _create_cli_user(env)

    completed = _run_capture_raw(env, "--text", "   ", check=False)

    assert completed.returncode != 0
    assert "采集内容不能为空" in completed.stderr.decode("utf-8", errors="replace")


def test_capture_cli_rejects_invalid_context_kind(tmp_path):
    env = _script_env(tmp_path)
    _create_cli_user(env)

    completed = _run_capture_raw(env, "--text", "非法类型", "--context-kind", "typo", check=False)

    assert completed.returncode != 0
    stderr = completed.stderr.decode("utf-8", errors="replace")
    assert "invalid choice" in stderr
    assert "typo" in stderr


def test_append_execution_record_cli_can_target_unique_task_title(tmp_path):
    env = _script_env(tmp_path)
    _create_cli_user(env)
    task_space_path = _write_task_space_fixture(env)

    completed = _run_append(
        env,
        "--task-title",
        "codeyun",
        "--summary",
        "标题定位回写到 codeyun。",
        "--status",
        "progress",
    )
    result = json.loads(completed.stdout.decode("utf-8"))

    assert result["ok"] is True
    assert result["current_fingerprint"]
    assert result["task_id"] == "task_codeyun"
    assert result["task_title"] == "codeyun"
    task_space = json.loads(task_space_path.read_text(encoding="utf-8"))
    task = next(item for item in task_space["tasks"] if item["id"] == "task_codeyun")
    assert task["executionRecords"][0]["summary"] == "标题定位回写到 codeyun。"


def test_append_execution_record_cli_does_not_duplicate_same_packet(tmp_path):
    env = _script_env(tmp_path)
    _create_cli_user(env)
    task_space_path = _write_task_space_fixture(env)
    initial_task_space = json.loads(task_space_path.read_text(encoding="utf-8"))
    initial_task = next(item for item in initial_task_space["tasks"] if item["id"] == "task_codeyun")

    args = [
        "--task-id",
        "task_codeyun",
        "--summary",
        "同一个执行包重复调用只保留一条记录。",
        "--status",
        "progress",
        "--packet-id",
        "packet_cli_repeat",
        "--steps-done",
        "1",
        "--commands-run",
        "1",
        "--files-changed",
        "0",
        "--expected-task-updated-at",
        initial_task["updatedAt"],
    ]
    first = json.loads(_run_append(env, *args).stdout.decode("utf-8"))
    second = json.loads(_run_append(env, *args).stdout.decode("utf-8"))

    assert first["ok"] is True
    assert second["ok"] is True
    task_space = json.loads(task_space_path.read_text(encoding="utf-8"))
    task = next(item for item in task_space["tasks"] if item["id"] == "task_codeyun")
    assert len(task["executionRecords"]) == 1
    assert task["executionRecords"][0]["packetId"] == "packet_cli_repeat"

    conflict = _run_append(
        env,
        "--task-id",
        "task_codeyun",
        "--summary",
        "同一个执行包不允许改摘要。",
        "--status",
        "progress",
        "--packet-id",
        "packet_cli_repeat",
        check=False,
    )
    assert conflict.returncode != 0
    conflict_payload = json.loads(conflict.stdout.decode("utf-8"))
    assert conflict_payload["ok"] is False
    assert conflict_payload["code"] == "packet_replay_conflict"
    assert "重复回写冲突" in conflict_payload["message"]


def test_append_execution_record_cli_rejects_ambiguous_task_title(tmp_path):
    env = _script_env(tmp_path)
    _create_cli_user(env)
    task_space_path = _write_task_space_fixture(env, duplicate_title=True)

    completed = _run_append(
        env,
        "--task-title",
        "codeyun",
        "--summary",
        "不应写入任意同名任务。",
        check=False,
    )

    assert completed.returncode != 0
    payload = json.loads(completed.stdout.decode("utf-8"))
    assert payload["ok"] is False
    assert payload["code"] == "ambiguous_task_title"
    assert "匹配到多个任务" in payload["message"]
    task_space = json.loads(task_space_path.read_text(encoding="utf-8"))
    assert all(not task.get("executionRecords") for task in task_space["tasks"])


def test_append_execution_record_cli_rejects_budget_overrun(tmp_path):
    env = _script_env(tmp_path)
    _create_cli_user(env)
    task_space_path = _write_task_space_fixture(env)

    completed = _run_append(
        env,
        "--task-title",
        "codeyun",
        "--summary",
        "超预算不应写入。",
        "--steps-done",
        "2",
        "--max-steps",
        "1",
        "--json",
        check=False,
    )

    assert completed.returncode != 0
    payload = json.loads(completed.stdout.decode("utf-8"))
    assert payload["ok"] is False
    assert payload["code"] == "budget_overrun"
    assert "超出执行包预算" in payload["message"]
    task_space = json.loads(task_space_path.read_text(encoding="utf-8"))
    task = next(item for item in task_space["tasks"] if item["id"] == "task_codeyun")
    assert not task.get("executionRecords")


def test_append_execution_record_cli_reports_stale_snapshot_as_json(tmp_path):
    env = _script_env(tmp_path)
    _create_cli_user(env)
    task_space_path = _write_task_space_fixture(env)
    task_space = json.loads(task_space_path.read_text(encoding="utf-8"))
    task = next(item for item in task_space["tasks"] if item["id"] == "task_codeyun")

    completed = _run_append(
        env,
        "--task-id",
        task["id"],
        "--summary",
        "过期执行包不应写入。",
        "--packet-id",
        "packet_stale_cli",
        "--expected-task-updated-at",
        "2000-01-01T00:00:00Z",
        "--json",
        check=False,
    )

    assert completed.returncode != 0
    payload = json.loads(completed.stdout.decode("utf-8"))
    assert payload["ok"] is False
    assert payload["code"] == "snapshot_mismatch"
    assert payload["task_id"] == task["id"]
    assert payload["packet_id"] == "packet_stale_cli"
    task_space_after = json.loads(task_space_path.read_text(encoding="utf-8"))
    task_after = next(item for item in task_space_after["tasks"] if item["id"] == "task_codeyun")
    assert not task_after.get("executionRecords")


def test_confirm_user_ready_cli_reopens_waiting_task_for_planning_check(tmp_path):
    env = _script_env(tmp_path)
    _create_cli_user(env)
    task_space_path = _write_waiting_confirmation_fixture(env)

    stale = _run_confirm(
        env,
        "--task-title",
        "等待确认任务",
        "--expected-fingerprint",
        "stale",
        check=False,
    )
    stale_payload = json.loads(stale.stdout.decode("utf-8"))
    assert stale.returncode != 0
    assert stale_payload["ok"] is False
    assert stale_payload["code"] == "stale_fingerprint"
    before_confirm = json.loads(task_space_path.read_text(encoding="utf-8"))
    before_task = next(item for item in before_confirm["tasks"] if item["id"] == "task_waiting")
    assert before_task["executionRecords"][0]["id"] == "exec_wait"

    completed = _run_confirm(
        env,
        "--task-title",
        "等待确认任务",
        "--note",
        "用户在 Codex 会话里确认可以继续。",
    )
    result = json.loads(completed.stdout.decode("utf-8"))

    assert result["ok"] is True
    assert result["current_fingerprint"]
    assert result["task_id"] == "task_waiting"
    assert result["latest_execution_record"]["summary"].startswith("用户已确认继续推进")
    assert "Codex 会话" in result["latest_execution_record"]["verification"]

    plan_code = textwrap.dedent(
        """
        import json
        from backend.core.ai_task_space import load_task_space, run_planner_check, save_task_space, user_task_space_path

        path = user_task_space_path(1)
        after = run_planner_check(load_task_space(path))
        save_task_space(path, after)
        print(json.dumps(after["plannerLogs"][0], ensure_ascii=False))
        """
    )
    plan = subprocess.run(
        [sys.executable, "-c", plan_code],
        cwd=ROOT_DIR,
        env=env,
        check=True,
        text=True,
        capture_output=True,
    )
    latest_log = json.loads(plan.stdout)
    assert latest_log["selectedTaskId"] == "task_waiting"

    task_space = json.loads(task_space_path.read_text(encoding="utf-8"))
    task = next(item for item in task_space["tasks"] if item["id"] == "task_waiting")
    assert task["status"] == "ready"


def test_confirm_user_ready_cli_rejects_task_without_waiting_record(tmp_path):
    env = _script_env(tmp_path)
    _create_cli_user(env)
    task_space_path = _write_task_space_fixture(env)

    completed = _run_confirm(env, "--task-title", "codeyun", check=False)

    assert completed.returncode != 0
    payload = json.loads(completed.stdout.decode("utf-8"))
    assert payload["ok"] is False
    assert payload["code"] == "task_not_waiting_confirmation"
    task_space = json.loads(task_space_path.read_text(encoding="utf-8"))
    task = next(item for item in task_space["tasks"] if item["id"] == "task_codeyun")
    assert not task.get("executionRecords")


def test_review_action_cli_moves_task_through_completion_and_archive_review(tmp_path):
    env = _script_env(tmp_path)
    _create_cli_user(env)
    task_space_path = _write_task_space_fixture(env)

    stale = _run_review_action(
        env,
        "--task-title",
        "codeyun",
        "--action",
        "mark_done",
        "--expected-fingerprint",
        "stale",
        check=False,
    )
    stale_payload = json.loads(stale.stdout.decode("utf-8"))
    assert stale.returncode != 0
    assert stale_payload["ok"] is False
    assert stale_payload["code"] == "stale_fingerprint"

    done = json.loads(
        _run_review_action(
            env,
            "--task-title",
            "codeyun",
            "--action",
            "mark_done",
        ).stdout.decode("utf-8")
    )
    assert done["ok"] is True
    assert done["current_fingerprint"]
    assert done["previous_status"] == "ready"
    assert done["task_status"] == "done"

    repeat_done = _run_review_action(
        env,
        "--task-title",
        "codeyun",
        "--action",
        "mark_done",
        check=False,
    )
    repeat_payload = json.loads(repeat_done.stdout.decode("utf-8"))
    assert repeat_done.returncode != 0
    assert repeat_payload["ok"] is False
    assert repeat_payload["code"] == "review_action_rejected"

    review = json.loads(
        _run_review_action(
            env,
            "--task-title",
            "codeyun",
            "--action",
            "request_archive_review",
        ).stdout.decode("utf-8")
    )
    assert review["previous_status"] == "done"
    assert review["task_status"] == "done"

    kept = json.loads(
        _run_review_action(
            env,
            "--task-title",
            "codeyun",
            "--action",
            "keep_unarchived",
        ).stdout.decode("utf-8")
    )
    assert kept["previous_status"] == "done"
    assert kept["task_status"] == "done"

    review_again = json.loads(
        _run_review_action(
            env,
            "--task-title",
            "codeyun",
            "--action",
            "request_archive_review",
        ).stdout.decode("utf-8")
    )
    archived = json.loads(
        _run_review_action(
            env,
            "--task-title",
            "codeyun",
            "--action",
            "archive",
        ).stdout.decode("utf-8")
    )

    assert review_again["task_status"] == "done"
    assert archived["previous_status"] == "done"
    assert archived["task_status"] == "archived"
    task_space = json.loads(task_space_path.read_text(encoding="utf-8"))
    task = next(item for item in task_space["tasks"] if item["id"] == "task_codeyun")
    assert task["status"] == "archived"
    assert task["archivedAt"]
    assert task["evidenceLog"][0].endswith("用户确认归档。")


def test_planner_suggestion_cli_applies_document_suggestion(tmp_path):
    env = _script_env(tmp_path)
    _create_cli_user(env)
    task_space_path = _write_task_space_fixture(env)

    plan = json.loads(_run_plan_once(env).stdout.decode("utf-8"))
    suggestion = next(
        item
        for item in plan["planner_suggestions"]
        if item["kind"] == "document" and item.get("taskId") == "task_codeyun"
    )
    task_space_before = json.loads(task_space_path.read_text(encoding="utf-8"))
    assert suggestion["status"] == "open"
    assert not next(item for item in task_space_before["tasks"] if item["id"] == "task_codeyun")["document"]["doneCriteria"]

    completed = _run_suggestion(
        env,
        "--suggestion-id",
        suggestion["id"],
        "--action",
        "apply",
    )
    result = json.loads(completed.stdout.decode("utf-8"))

    assert result["ok"] is True
    assert result["current_fingerprint"]
    assert result["suggestion"]["status"] == "applied"
    task_space = json.loads(task_space_path.read_text(encoding="utf-8"))
    task = next(item for item in task_space["tasks"] if item["id"] == "task_codeyun")
    assert "验收标准" in task["document"]["doneCriteria"]
    assert any("应用规划建议" in line for line in task["evidenceLog"])


def test_planning_check_cli_exposes_top_level_planner_state(tmp_path):
    env = _script_env(tmp_path)
    _create_cli_user(env)
    _write_task_space_fixture(env)

    plan = json.loads(_run_plan_once(env).stdout.decode("utf-8"))
    decision = plan["execution_packet"]["planningDecision"]

    assert plan["current_fingerprint"]
    assert plan["planner_state"]["selectedTaskId"] == decision["selectedTaskId"]
    assert plan["planner_state"]["selectedReason"] == decision["selectedReason"]
    assert plan["planner_state"]["candidateCount"] == decision["candidateCount"]
    assert plan["planner_state"]["blockerCount"] == decision["skippedCount"]
    assert plan["planner_state"]["blockers"] == decision["skipped"][:5]


def test_status_cli_reads_current_task_space_without_mutating(tmp_path):
    env = _script_env(tmp_path)
    _create_cli_user(env)
    task_space_path = _write_waiting_confirmation_fixture(env)
    before = task_space_path.read_text(encoding="utf-8")

    status = json.loads(_run_status(env).stdout.decode("utf-8"))

    assert status["ok"] is True
    assert status["mutated"] is False
    assert status["current_fingerprint"]
    assert status["action_hint_contract"]["fingerprint"] == status["current_fingerprint"]
    assert status["action_hint_contract"]["requiresApproval"] is False
    assert status["action_hint_contract"]["staleAfterAnyWrite"] is True
    assert status["action_hint_contract"]["reloadAfterSuccess"] is True
    assert status["summary"]["waitingConfirmationCount"] == 0
    assert status["waiting_tasks"] == []
    assert not any(hint["kind"] == "confirm_waiting_task" for hint in status["action_hints"])
    assert status["latest_planner_log"] is None
    assert task_space_path.read_text(encoding="utf-8") == before


def test_planning_check_cli_text_output_includes_planner_blockers(tmp_path):
    env = _script_env(tmp_path)
    _create_cli_user(env)
    setup_code = textwrap.dedent(
        """
        from copy import deepcopy

        from backend.core.ai_task_space import save_task_space, seed_task_space, user_task_space_path

        path = user_task_space_path(1)
        space = seed_task_space()
        template = space["tasks"][0]
        tasks = []
        for index in range(6):
            task = deepcopy(template)
            task["id"] = f"manual_blocker_{index}"
            task["title"] = f"手动阻塞任务 {index}"
            task["kind"] = "task"
            task["status"] = "ready"
            task["parentId"] = None
            task["dependsOn"] = []
            task["relatedTaskIds"] = []
            task["executionPolicy"] = "manual_only"
            task["risk"] = "low"
            task["sortOrder"] = index
            task["executionRecords"] = []
            tasks.append(task)
        space["tasks"] = tasks
        save_task_space(path, space)
        """
    )
    subprocess.run(
        [sys.executable, "-c", setup_code],
        cwd=ROOT_DIR,
        env=env,
        check=True,
        text=True,
        capture_output=True,
    )

    completed = _run_plan_once_text(env)
    output = completed.stdout.decode("utf-8")

    assert "Planner: 优先选择「手动阻塞任务 0」" in output
    assert "Candidates: 6, blockers: 0" in output
    assert "Execution mode: execute_safe" in output


def test_planning_check_cli_outputs_only_open_planner_suggestions(tmp_path):
    env = _script_env(tmp_path)
    _create_cli_user(env)
    setup_code = textwrap.dedent(
        """
        from backend.core.ai_task_space import save_task_space, seed_task_space, user_task_space_path

        path = user_task_space_path(1)
        space = seed_task_space()
        space["tasks"][0]["id"] = "task_codeyun"
        space["tasks"][0]["title"] = "codeyun"
        space["tasks"][0]["status"] = "ready"
        space["tasks"][0]["executionPolicy"] = "auto_report"
        space["tasks"][0]["risk"] = "low"
        space["tasks"][0]["document"]["doneCriteria"] = ""
        space["plannerSuggestions"] = [
            {
                "id": "sug_resolved_applied",
                "kind": "document",
                "severity": "warning",
                "taskId": "task_codeyun",
                "relatedTaskIds": [],
                "title": "已应用建议",
                "rationale": "",
                "proposedAction": "",
                "status": "applied",
                "createdAt": "2026-01-01T00:00:00Z",
            },
            {
                "id": "sug_resolved_dismissed",
                "kind": "document",
                "severity": "warning",
                "taskId": "task_codeyun",
                "relatedTaskIds": [],
                "title": "已忽略建议",
                "rationale": "",
                "proposedAction": "",
                "status": "dismissed",
                "createdAt": "2026-01-01T00:00:00Z",
            },
            {
                "id": "sug_still_open",
                "kind": "document",
                "severity": "warning",
                "taskId": "task_codeyun",
                "relatedTaskIds": [],
                "title": "仍待审核建议",
                "rationale": "",
                "proposedAction": "",
                "status": "open",
                "createdAt": "2026-01-01T00:00:00Z",
            },
        ]
        save_task_space(path, space)
        """
    )
    subprocess.run(
        [sys.executable, "-c", setup_code],
        cwd=ROOT_DIR,
        env=env,
        check=True,
        text=True,
        capture_output=True,
    )

    plan = json.loads(_run_plan_once(env).stdout.decode("utf-8"))
    suggestion_ids = {item["id"] for item in plan["planner_suggestions"]}

    assert plan["planner_suggestions"]
    assert "sug_resolved_applied" not in suggestion_ids
    assert "sug_resolved_dismissed" not in suggestion_ids
    assert {item["status"] for item in plan["planner_suggestions"]} == {"open"}


def test_planner_suggestion_cli_dismisses_and_rejects_stale_fingerprint(tmp_path):
    env = _script_env(tmp_path)
    _create_cli_user(env)
    task_space_path = _write_task_space_fixture(env)

    plan = json.loads(_run_plan_once(env).stdout.decode("utf-8"))
    suggestion = next(item for item in plan["planner_suggestions"] if item["status"] == "open")
    stale = _run_suggestion(
        env,
        "--suggestion-id",
        suggestion["id"],
        "--action",
        "dismiss",
        "--expected-fingerprint",
        "stale",
        check=False,
    )
    stale_payload = json.loads(stale.stdout.decode("utf-8"))
    assert stale.returncode != 0
    assert stale_payload["ok"] is False
    assert stale_payload["code"] == "stale_fingerprint"

    dismissed = json.loads(
        _run_suggestion(
            env,
            "--suggestion-id",
            suggestion["id"],
            "--action",
            "dismiss",
        ).stdout.decode("utf-8")
    )

    assert dismissed["ok"] is True
    assert dismissed["current_fingerprint"]
    assert dismissed["suggestion"]["status"] == "dismissed"
    task_space = json.loads(task_space_path.read_text(encoding="utf-8"))
    assert next(item for item in task_space["plannerSuggestions"] if item["id"] == suggestion["id"])["status"] == "dismissed"


def test_validate_automation_contract_cli_simulates_planning_check_without_mutating_task_space(tmp_path):
    env = _script_env(tmp_path)
    _create_cli_user(env)
    task_space_path = _write_task_space_fixture(env)
    before = task_space_path.read_text(encoding="utf-8")

    completed = _run_validate(env)
    result = json.loads(completed.stdout.decode("utf-8"))

    assert result["ok"] is True
    assert result["mutated"] is False
    assert result["mode"] == "simulated_plan"
    assert result["current_fingerprint"]
    assert result["validated_fingerprint"]
    assert result["execution_packet"]["hasTask"] is True
    assert result["automation_directive"]["completionTemplate"]["writeback"]["summary"]
    writeback_cli = result["execution_packet"]["writeback"]["cli"]
    assert "--max-steps" in writeback_cli
    assert "--verification " in writeback_cli
    assert "--remaining-risk " in writeback_cli
    assert "--next-step " in writeback_cli
    argv_template = result["execution_packet"]["writeback"]["argvTemplate"]
    assert argv_template[:4] == ["uv", "run", "python", "scripts/ai_task_space_append_execution_record.py"]
    assert "--summary" in argv_template
    assert "--verification" in argv_template
    assert "--remaining-risk" in argv_template
    assert "--next-step" in argv_template
    assert task_space_path.read_text(encoding="utf-8") == before

    use_current = json.loads(_run_validate(env, "--use-current").stdout.decode("utf-8"))
    assert use_current["current_fingerprint"] == use_current["validated_fingerprint"]


def test_validate_automation_contract_reports_prompt_contract_failures(tmp_path):
    env = _script_env(tmp_path)
    _create_cli_user(env)
    _write_task_space_fixture(env)
    code = textwrap.dedent(
        """
        import json
        from backend.core.ai_task_space import load_task_space, run_planner_check, user_task_space_path
        from scripts.ai_task_space_validate_automation_contract import validate_contract

        task_space = run_planner_check(load_task_space(user_task_space_path(1)))
        result = validate_contract(task_space, username="cli_user", prompt="bad prompt")
        print(json.dumps(result, ensure_ascii=False))
        """
    )

    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT_DIR,
        env=env,
        check=True,
        text=True,
        capture_output=True,
    )
    result = json.loads(completed.stdout)

    assert result["ok"] is False
    failure_codes = {item["code"] for item in result["failures"]}
    assert "prompt_missing_plan_once" in failure_codes
    assert "prompt_missing_no_memory_selection" in failure_codes
    assert "prompt_missing_full_task_space_read" in failure_codes
    assert "prompt_missing_stale_snapshot_boundary" in failure_codes
    assert "prompt_missing_planner_state" in failure_codes
    assert "prompt_missing_completion_template" in failure_codes
    assert "prompt_missing_current_fingerprint" in failure_codes
    assert "prompt_missing_writeback_json_check" in failure_codes
    assert "prompt_missing_capture_execution_boundary" in failure_codes
    assert "prompt_missing_next_plan_boundary" in failure_codes


def _write_automation_toml(path: Path, *, prompt: str, cwd: Path = ROOT_DIR) -> None:
    path.write_text(
        "\n".join(
            [
                'version = 1',
                'id = "ai"',
                'kind = "cron"',
                'name = "AI任务空间自动化执行"',
                f"prompt = {json.dumps(prompt, ensure_ascii=False)}",
                'status = "ACTIVE"',
                'rrule = "FREQ=HOURLY;INTERVAL=1"',
                'model = "gpt-5.4"',
                'reasoning_effort = "medium"',
                'execution_environment = "local"',
                f"cwds = [{json.dumps(str(cwd), ensure_ascii=False)}]",
                "",
            ]
        ),
        encoding="utf-8",
    )


def test_validate_automation_contract_accepts_matching_automation_toml(tmp_path):
    env = _script_env(tmp_path)
    _create_cli_user(env)
    _write_task_space_fixture(env)
    prompt_code = "from backend.core.ai_task_space import build_automation_prompt; print(build_automation_prompt('cli_user'))"
    prompt = subprocess.run(
        [sys.executable, "-c", prompt_code],
        cwd=ROOT_DIR,
        env=env,
        check=True,
        text=True,
        capture_output=True,
    ).stdout.rstrip("\n")
    automation_toml = tmp_path / "automation.toml"
    _write_automation_toml(automation_toml, prompt=prompt)

    completed = _run_validate(env, "--automation-toml", str(automation_toml))
    result = json.loads(completed.stdout.decode("utf-8"))

    assert result["ok"] is True
    assert result["automation_toml"]["config"]["promptMatches"] is True
    assert result["automation_toml"]["failures"] == []


def test_validate_automation_contract_rejects_drifted_automation_toml(tmp_path):
    env = _script_env(tmp_path)
    _create_cli_user(env)
    _write_task_space_fixture(env)
    automation_toml = tmp_path / "automation.toml"
    _write_automation_toml(automation_toml, prompt="old prompt")

    completed = _run_validate(env, "--automation-toml", str(automation_toml), check=False)
    result = json.loads(completed.stdout.decode("utf-8"))

    assert completed.returncode != 0
    assert result["ok"] is False
    failure_codes = {item["code"] for item in result["failures"]}
    assert "automation_prompt_drift" in failure_codes


def test_sync_automation_cli_dry_run_does_not_write_toml(tmp_path):
    env = _script_env(tmp_path)
    _create_cli_user(env)
    automation_toml = tmp_path / "automation.toml"

    completed = _run_sync_automation(env, "--path", str(automation_toml), "--dry-run")
    result = json.loads(completed.stdout.decode("utf-8"))

    assert result["ok"] is True
    assert result["written"] is False
    assert "scripts/ai_task_space_plan_once.py --username cli_user --json" in result["toml"]
    assert not automation_toml.exists()


def test_sync_automation_cli_writes_contract_matching_toml(tmp_path):
    env = _script_env(tmp_path)
    _create_cli_user(env)
    _write_task_space_fixture(env)
    automation_toml = tmp_path / "automation.toml"

    sync = json.loads(_run_sync_automation(env, "--path", str(automation_toml)).stdout.decode("utf-8"))
    validated = json.loads(
        _run_validate(env, "--automation-toml", str(automation_toml)).stdout.decode("utf-8")
    )

    assert sync["ok"] is True
    assert sync["written"] is True
    assert automation_toml.exists()
    assert validated["ok"] is True
    assert validated["automation_toml"]["config"]["promptMatches"] is True


