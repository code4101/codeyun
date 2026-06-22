from __future__ import annotations

import json
import base64

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api import ai_task_space as ai_task_space_api
from backend.core.ai_task_space import build_automation_prompt
from backend.models import User


def _client(tmp_path, monkeypatch) -> TestClient:
    app = FastAPI()
    app.include_router(ai_task_space_api.router, prefix="/api/ai-task-space")
    user = User(
        id=7,
        username="tester",
        nickname="Tester",
        hashed_password="hashed",
        password_plain="secret",
        is_active=True,
        is_superuser=True,
    )
    app.dependency_overrides[ai_task_space_api.get_current_active_user] = lambda: user
    monkeypatch.setattr(
        ai_task_space_api,
        "user_task_space_path",
        lambda user_id: tmp_path / f"user_{user_id}.json",
    )
    return TestClient(app)


def test_ai_task_space_api_rejects_stale_full_save(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    initial_response = client.get("/api/ai-task-space")
    assert initial_response.status_code == 200
    initial_space = initial_response.json()
    assert initial_space["_fingerprint"]

    capture_response = client.post(
        "/api/ai-task-space/captures",
        json={
            "raw_text": "后台新增采集项",
            "source": "pytest",
            "tags": ["api", "constraint"],
            "context_kind": "constraint",
            "project_path": str(tmp_path),
        },
    )
    assert capture_response.status_code == 200
    assert capture_response.json()["_fingerprint"] != initial_space["_fingerprint"]
    capture = capture_response.json()["captures"][0]
    assert capture["tags"] == ["api", "constraint"]
    assert capture["contextKind"] == "constraint"
    assert capture["projectPath"] == str(tmp_path)

    stale_response = client.put(
        "/api/ai-task-space",
        json={
            "task_space": initial_space,
            "expected_fingerprint": initial_space["_fingerprint"],
        },
    )
    assert stale_response.status_code == 409

    latest_space = client.get("/api/ai-task-space").json()
    assert any(capture["rawText"] == "后台新增采集项" for capture in latest_space["captures"])


def test_ai_task_space_api_preserves_capture_images(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEYUN_DATA_DIR", str(tmp_path / "data"))
    from backend.core.settings import get_settings
    get_settings.cache_clear()
    client = _client(tmp_path, monkeypatch)

    try:
        response = client.post(
            "/api/ai-task-space/captures",
            json={
                "raw_text": "带截图的反馈。",
                "source": "pytest",
                "images": [
                    {
                        "name": "feedback.png",
                        "mime_type": "image/png",
                        "data_base64": base64.b64encode(b"\x89PNG\r\n\x1a\napi-image").decode("ascii"),
                    }
                ],
            },
        )

        assert response.status_code == 200
        capture = response.json()["captures"][0]
        assert capture["attachments"][0]["name"] == "feedback.png"
        assert capture["attachments"][0]["mimeType"] == "image/png"
        assert capture["attachments"][0]["url"].startswith("/static/attachments/")
    finally:
        get_settings.cache_clear()


def test_ai_task_space_api_rejects_invalid_capture_context_kind(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    response = client.post(
        "/api/ai-task-space/captures",
        json={
            "raw_text": "非法采集类型不应进入任务空间。",
            "source": "pytest",
            "context_kind": "typo",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "采集类型非法"


def test_ai_task_space_execution_packet_includes_automation_directive(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    plan_response = client.post("/api/ai-task-space/planner/run-once")
    assert plan_response.status_code == 200
    selected_task_id = plan_response.json()["plannerLogs"][0]["selectedTaskId"]

    packet_response = client.get(
        "/api/ai-task-space/planner/execution-packet",
        params={"task_id": selected_task_id},
    )
    assert packet_response.status_code == 200
    packet = packet_response.json()
    assert packet["automationDirective"]["action"] in {
        "ask_user",
        "report_only",
        "execute_safe",
        "skip",
        "stop_for_audit",
    }
    assert "shouldModifyCode" in packet["automationDirective"]


def test_ai_task_space_execution_record_api_is_idempotent_by_packet_id(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    space = client.get("/api/ai-task-space").json()
    task = space["tasks"][0]
    payload = {
        "summary": "API 重复执行包只写入一次。",
        "status": "progress",
        "packet_id": "packet_api_repeat",
        "steps_done": 1,
        "commands_run": 1,
        "files_changed": 0,
    }

    first = client.post(f"/api/ai-task-space/tasks/{task['id']}/execution-records", json=payload)
    second = client.post(f"/api/ai-task-space/tasks/{task['id']}/execution-records", json=payload)

    assert first.status_code == 200
    assert second.status_code == 200
    latest = second.json()
    updated_task = next(item for item in latest["tasks"] if item["id"] == task["id"])
    assert len(updated_task["executionRecords"]) == 1
    assert updated_task["executionRecords"][0]["packetId"] == "packet_api_repeat"

    conflict = client.post(
        f"/api/ai-task-space/tasks/{task['id']}/execution-records",
        json={
            **payload,
            "summary": "API 同 packet 不允许改摘要。",
        },
    )
    assert conflict.status_code == 409
    assert "packet_api_repeat" in conflict.json()["detail"]


def test_ai_task_space_confirm_user_ready_reopens_waiting_task(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    space = client.get("/api/ai-task-space").json()
    task = space["tasks"][1]
    task["parentId"] = None
    task["status"] = "ready"
    task["executionPolicy"] = "ask_before_execute"
    task["executionRecords"] = [
        {
            "id": "exec_wait",
            "recordedAt": "2026-06-19T00:00:00Z",
            "summary": "已整理建议，等待用户确认，未修改业务代码。",
            "verification": "确认未修改业务代码。",
            "remainingRisk": "需要用户确认范围。",
            "nextStep": "等待用户确认后继续。",
            "status": "progress",
        }
    ]
    saved = client.put(
        "/api/ai-task-space",
        json={"task_space": space, "expected_fingerprint": space["_fingerprint"]},
    )
    assert saved.status_code == 200
    saved_space = saved.json()

    stale = client.post(
        f"/api/ai-task-space/tasks/{task['id']}/confirm-user-ready",
        json={"note": "旧页面确认不应生效。", "expected_fingerprint": "stale"},
    )
    assert stale.status_code == 409
    unchanged = client.get("/api/ai-task-space").json()
    unchanged_task = next(item for item in unchanged["tasks"] if item["id"] == task["id"])
    assert unchanged_task["executionRecords"][0]["id"] == "exec_wait"

    response = client.post(
        f"/api/ai-task-space/tasks/{task['id']}/confirm-user-ready",
        json={"note": "范围已确认。", "expected_fingerprint": saved_space["_fingerprint"]},
    )
    assert response.status_code == 200
    confirmed = response.json()
    confirmed_task = next(item for item in confirmed["tasks"] if item["id"] == task["id"])
    assert confirmed_task["executionRecords"][0]["summary"].startswith("用户已确认继续推进")
    assert "范围已确认" in confirmed_task["executionRecords"][0]["verification"]
    assert confirmed_task["document"]["currentState"] == "用户已确认继续推进，等待下一次规划检查重新评估。"
    assert confirmed_task["document"]["nextStep"] == "等待用户确认后继续。"

    plan = client.post("/api/ai-task-space/planner/run-once")
    assert plan.status_code == 200
    assert plan.json()["plannerLogs"][0]["selectedTaskId"] == task["id"]


def test_ai_task_space_review_action_api_uses_explicit_state_transitions(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    space = client.get("/api/ai-task-space").json()
    task = space["tasks"][1]
    task["parentId"] = None
    task["status"] = "ready"
    saved = client.put(
        "/api/ai-task-space",
        json={"task_space": space, "expected_fingerprint": space["_fingerprint"]},
    )
    assert saved.status_code == 200
    current = saved.json()

    stale = client.post(
        f"/api/ai-task-space/tasks/{task['id']}/review-action",
        json={"action": "mark_done", "expected_fingerprint": "stale"},
    )
    assert stale.status_code == 409

    done = client.post(
        f"/api/ai-task-space/tasks/{task['id']}/review-action",
        json={"action": "mark_done", "expected_fingerprint": current["_fingerprint"]},
    )
    assert done.status_code == 200
    done_space = done.json()
    done_task = next(item for item in done_space["tasks"] if item["id"] == task["id"])
    assert done_task["status"] == "done"
    assert done_task["completedAt"]
    assert done_task["document"]["currentState"] == "已完成。"
    assert done_task["evidenceLog"][0].endswith("标记完成，保留在活跃任务空间供近期规划参考。")

    invalid_repeat = client.post(
        f"/api/ai-task-space/tasks/{task['id']}/review-action",
        json={"action": "mark_done", "expected_fingerprint": done_space["_fingerprint"]},
    )
    assert invalid_repeat.status_code == 400

    review = client.post(
        f"/api/ai-task-space/tasks/{task['id']}/review-action",
        json={"action": "request_archive_review", "expected_fingerprint": done_space["_fingerprint"]},
    )
    assert review.status_code == 200
    review_space = review.json()
    review_task = next(item for item in review_space["tasks"] if item["id"] == task["id"])
    assert review_task["status"] == "done"

    kept = client.post(
        f"/api/ai-task-space/tasks/{task['id']}/review-action",
        json={"action": "keep_unarchived", "expected_fingerprint": review_space["_fingerprint"]},
    )
    assert kept.status_code == 200
    kept_space = kept.json()
    kept_task = next(item for item in kept_space["tasks"] if item["id"] == task["id"])
    assert kept_task["status"] == "done"
    assert kept_task["document"]["currentState"] == "用户选择暂不归档，保留在近期完成参考中。"

    review_again = client.post(
        f"/api/ai-task-space/tasks/{task['id']}/review-action",
        json={"action": "request_archive_review", "expected_fingerprint": kept_space["_fingerprint"]},
    )
    assert review_again.status_code == 200
    archive = client.post(
        f"/api/ai-task-space/tasks/{task['id']}/review-action",
        json={"action": "archive", "expected_fingerprint": review_again.json()["_fingerprint"]},
    )
    assert archive.status_code == 200
    archived_task = next(item for item in archive.json()["tasks"] if item["id"] == task["id"])
    assert archived_task["status"] == "archived"
    assert archived_task["archivedAt"]
    assert archived_task["document"]["currentState"] == "已归档；节点仍保留在任务树中，可通过任务树显示设置隐藏。"
    assert archived_task["statusBeforeArchive"] == "done"


def test_ai_task_space_automation_health_is_read_only(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    automation_toml = tmp_path / "automation.toml"
    automation_toml.write_text(
        "\n".join(
            [
                'version = 1',
                'id = "ai"',
                'kind = "cron"',
                'name = "AI任务空间自动化执行"',
                f'prompt = {json.dumps(build_automation_prompt("tester"), ensure_ascii=False)}',
                'status = "ACTIVE"',
                'rrule = "FREQ=HOURLY;INTERVAL=1"',
                'model = "gpt-5.4"',
                'reasoning_effort = "medium"',
                'execution_environment = "local"',
                f'cwds = [{json.dumps(str(tmp_path), ensure_ascii=False)}]',
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(ai_task_space_api, "default_automation_toml_path", lambda: automation_toml)
    monkeypatch.setattr(ai_task_space_api, "ROOT_DIR", tmp_path)

    before = client.get("/api/ai-task-space").json()
    response = client.get("/api/ai-task-space/automation-health")
    after = client.get("/api/ai-task-space").json()

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["mutated"] is False
    assert payload["currentFingerprint"] == before["_fingerprint"]
    assert payload["validatedFingerprint"]
    assert payload["automationToml"]["config"]["promptMatches"] is True
    assert payload["syncCommand"] == (
        "uv run python scripts/ai_task_space_sync_automation.py --username tester --json"
    )
    assert payload["contract"]["action"] in {"ask_user", "report_only", "execute_safe", "skip", "stop_for_audit"}
    assert isinstance(payload["contract"]["requiredChecks"], list)
    assert "stopReason" in payload["contract"]
    assert "summaryHint" in payload["contract"]
    assert "writebackStatus" in payload["contract"]
    assert isinstance(payload["contract"]["blockerCount"], int)
    assert isinstance(payload["contract"]["blockers"], list)
    assert "recentRun" in payload
    assert payload["recentRun"]["selectedTask"] is not None
    assert "latestPlannerLog" in payload["recentRun"]
    assert "latestExecutionRecord" in payload["recentRun"]
    assert after["plannerLogs"] == before["plannerLogs"]


def test_ai_task_space_automation_health_reports_total_blocker_count(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    automation_toml = tmp_path / "automation.toml"
    automation_toml.write_text(
        "\n".join(
            [
                'version = 1',
                'id = "ai"',
                'kind = "cron"',
                'name = "AI任务空间自动化执行"',
                f'prompt = {json.dumps(build_automation_prompt("tester"), ensure_ascii=False)}',
                'status = "ACTIVE"',
                'rrule = "FREQ=HOURLY;INTERVAL=1"',
                'model = "gpt-5.4"',
                'reasoning_effort = "medium"',
                'execution_environment = "local"',
                f'cwds = [{json.dumps(str(tmp_path), ensure_ascii=False)}]',
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(ai_task_space_api, "default_automation_toml_path", lambda: automation_toml)
    monkeypatch.setattr(ai_task_space_api, "ROOT_DIR", tmp_path)

    space = client.get("/api/ai-task-space").json()
    template = json.loads(json.dumps(space["tasks"][0]))
    tasks = []
    for index in range(7):
        task = json.loads(json.dumps(template))
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

    save_response = client.put(
        "/api/ai-task-space",
        json={
            "task_space": space,
            "expected_fingerprint": space["_fingerprint"],
        },
    )
    assert save_response.status_code == 200

    response = client.get("/api/ai-task-space/automation-health")

    assert response.status_code == 200
    contract = response.json()["contract"]
    assert contract["selectedTaskId"] == "manual_blocker_0"
    assert contract["blockerCount"] == 0
    assert contract["blockers"] == []


def test_ai_task_space_planner_suggestion_action(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    space = client.get("/api/ai-task-space").json()
    task = space["tasks"][0]
    task["status"] = "ready"
    task["document"]["doneCriteria"] = ""
    response = client.put(
        "/api/ai-task-space",
        json={
            "task_space": space,
            "expected_fingerprint": space["_fingerprint"],
        },
    )
    assert response.status_code == 200

    plan = client.post("/api/ai-task-space/planner/run-once").json()
    suggestion = next(item for item in plan["plannerSuggestions"] if item.get("taskId") == task["id"])

    apply_response = client.post(
        f"/api/ai-task-space/planner/suggestions/{suggestion['id']}",
        json={"action": "apply"},
    )
    assert apply_response.status_code == 200
    applied = apply_response.json()
    applied_task = next(item for item in applied["tasks"] if item["id"] == task["id"])
    assert applied_task["document"]["doneCriteria"]
    assert next(item for item in applied["plannerSuggestions"] if item["id"] == suggestion["id"])["status"] == "applied"

    dismiss_response = client.post(
        f"/api/ai-task-space/planner/suggestions/{suggestion['id']}",
        json={"action": "dismiss"},
    )
    assert dismiss_response.status_code == 200


def test_ai_task_space_planner_suggestion_action_rejects_stale_fingerprint(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    space = client.get("/api/ai-task-space").json()
    task = space["tasks"][0]
    task["status"] = "ready"
    task["document"]["doneCriteria"] = ""
    saved = client.put(
        "/api/ai-task-space",
        json={
            "task_space": space,
            "expected_fingerprint": space["_fingerprint"],
        },
    ).json()
    stale_fingerprint = saved["_fingerprint"]

    plan = client.post("/api/ai-task-space/planner/run-once").json()
    suggestion = next(item for item in plan["plannerSuggestions"] if item.get("taskId") == task["id"])

    stale_response = client.post(
        f"/api/ai-task-space/planner/suggestions/{suggestion['id']}",
        json={"action": "apply", "expected_fingerprint": stale_fingerprint},
    )

    assert stale_response.status_code == 409
    latest = client.get("/api/ai-task-space").json()
    latest_task = next(item for item in latest["tasks"] if item["id"] == task["id"])
    assert latest_task["document"]["doneCriteria"] == ""

