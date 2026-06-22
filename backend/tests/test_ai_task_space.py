from __future__ import annotations

import os

from backend.core.ai_task_space import (
    ExecutionPacketReplayConflict,
    ExecutionSnapshotMismatch,
    _split_suggestion_preview,
    add_capture,
    append_execution_record,
    apply_planner_suggestion,
    audit_task_space,
    build_automation_directive,
    build_automation_prompt,
    build_execution_packet,
    build_planner_decision,
    build_planner_suggestions,
    confirm_task_user_ready,
    dismiss_planner_suggestion,
    load_task_space,
    mutate_task_space,
    now_iso,
    promote_capture,
    run_planner_check,
    save_task_space,
    seed_task_space,
    task_space_fingerprint,
    task_space_with_fingerprint,
)


def test_ai_task_space_capture_promote_and_planning_check():
    space = seed_task_space()
    space = add_capture(
        space,
        "后续要把任务空间接到 Codex automation 自动化执行。",
        "pytest",
        tags=["automation", "后续"],
        context_kind="constraint",
        project_path="D:/home/chenkunze/slns/codeyun",
    )

    inbox = [item for item in space["captures"] if item["status"] == "inbox"]
    assert len(inbox) == 1

    promoted = promote_capture(space, inbox[0]["id"])
    capture = next(item for item in promoted["captures"] if item["id"] == inbox[0]["id"])
    assert capture["status"] == "triaged"
    assert capture["linkedTaskId"]
    assert capture["tags"] == ["automation", "后续"]
    assert capture["contextKind"] == "constraint"
    assert any(task["id"] == capture["linkedTaskId"] for task in promoted["tasks"])

    planned = next(task for task in promoted["tasks"] if task["id"] == capture["linkedTaskId"])
    assert planned["kind"] == "task"
    assert planned["executionPolicy"] == "auto_safe"
    assert planned["risk"] == "low"
    assert planned["document"]["context"] == "后续要把任务空间接到 Codex automation 自动化执行。"
    assert planned["document"]["knownFacts"] == ""

    planned["executionPolicy"] = "auto_report"
    planned["risk"] = "low"
    plan = run_planner_check(promoted)
    latest_log = plan["plannerLogs"][0]
    assert latest_log["actions"]
    assert latest_log["selectedTaskId"]
    selected = next(task for task in plan["tasks"] if task["id"] == latest_log["selectedTaskId"])
    assert selected["status"] == "ready"


def test_ai_task_space_capture_preserves_image_attachments(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEYUN_DATA_DIR", str(tmp_path / "data"))
    from backend.core.settings import get_settings
    get_settings.cache_clear()
    from backend.core.ai_task_space import save_capture_attachment_file

    try:
        image_path = tmp_path / "question.png"
        image_path.write_bytes(b"\x89PNG\r\n\x1a\npytest-image")
        attachment = save_capture_attachment_file(image_path)
        space = add_capture(
            {"version": 2, "captures": [], "tasks": [], "plannerLogs": [], "plannerSuggestions": []},
            "用户带截图反馈：页面状态不对。",
            "pytest",
            attachments=[attachment],
        )

        capture = space["captures"][0]
        assert capture["attachments"][0]["name"] == "question.png"
        assert capture["attachments"][0]["url"].startswith("/static/attachments/")
        assert (tmp_path / "data" / "attachments" / capture["attachments"][0]["filename"]).exists()

        promoted = promote_capture(space, capture["id"])
        task = next(item for item in promoted["tasks"] if item["id"] == promoted["captures"][0]["linkedTaskId"])
        assert task["attachments"] == capture["attachments"]
        assert task["document"]["knownFacts"] == ""
    finally:
        get_settings.cache_clear()


def test_planning_check_keeps_non_task_captures_out_of_execution_candidates():
    space = {
        "version": 2,
        "captures": [],
        "tasks": [],
        "plannerLogs": [],
        "plannerSuggestions": [],
    }
    space = add_capture(
        space,
        "界面偏好：右侧详情保持简洁，只保留当前任务状态和回写。",
        "pytest",
        tags=["UI", "偏好"],
        context_kind="preference",
        project_path="D:/home/chenkunze/slns/codeyun",
    )

    after = run_planner_check(space)
    capture = after["captures"][0]
    task = next(item for item in after["tasks"] if item["id"] == capture["linkedTaskId"])

    assert capture["status"] == "triaged"
    assert task["kind"] == "project"
    assert task["executionPolicy"] == "auto_safe"
    assert task["risk"] == "low"
    assert task["status"] == "ready"
    assert "不作为独立执行任务" in task["document"]["currentState"]
    assert after["plannerLogs"][0]["selectedTaskId"] is None


def test_manual_only_tasks_do_not_get_execution_preparation_suggestions():
    space = seed_task_space()
    task = space["tasks"][0]
    task["status"] = "ready"
    task["executionPolicy"] = "manual_only"
    task["document"]["nextStep"] = ""
    task["document"]["doneCriteria"] = ""
    task["document"]["context"] = "很长的上下文。" * 80

    suggestions = build_planner_suggestions(space)
    task_suggestions = [item for item in suggestions if item.get("taskId") == task["id"]]

    assert any(item["kind"] == "document" for item in task_suggestions)


def test_planning_check_promotes_task_capture_as_execution_candidate():
    space = {
        "version": 2,
        "captures": [],
        "tasks": [],
        "plannerLogs": [],
        "plannerSuggestions": [],
    }
    space = add_capture(
        space,
        "实现任务系统 Inbox 只读检视区。",
        "pytest",
        tags=["任务空间"],
        context_kind="task",
    )

    after = run_planner_check(space)
    capture = after["captures"][0]
    task = next(item for item in after["tasks"] if item["id"] == capture["linkedTaskId"])

    assert task["kind"] == "task"
    assert task["executionPolicy"] == "auto_safe"
    assert task["status"] == "ready"
    assert after["plannerLogs"][0]["selectedTaskId"]


def test_planning_check_does_not_merge_new_capture_into_completed_task():
    space = {
        "version": 2,
        "captures": [],
        "tasks": [],
        "plannerLogs": [],
        "plannerSuggestions": [],
    }
    space = add_capture(space, "图鉴整理：第一阶段已经完成。", "pytest", context_kind="task")
    first = run_planner_check(space)
    completed = next(item for item in first["tasks"] if item["id"] == first["captures"][0]["linkedTaskId"])
    completed["status"] = "done"
    completed["document"]["resultSummary"] = "第一阶段已完成。"
    completed_context = completed["document"]["context"]

    second_space = add_capture(first, "图鉴整理：第二阶段补充新需求。", "pytest", context_kind="task")
    second = run_planner_check(second_space)
    second_capture = next(item for item in second["captures"] if item["rawText"].endswith("第二阶段补充新需求。"))
    linked = next(item for item in second["tasks"] if item["id"] == second_capture["linkedTaskId"])
    completed_after = next(item for item in second["tasks"] if item["id"] == completed["id"])

    assert second_capture["status"] == "triaged"
    assert linked["id"] != completed["id"]
    assert linked["document"]["context"] == "图鉴整理：第二阶段补充新需求。"
    assert completed_after["status"] == "done"
    assert completed_after["document"]["context"] == completed_context
    assert "第二阶段补充新需求" not in completed_after["document"]["context"]


def test_unknown_capture_context_kind_normalizes_to_task():
    space = add_capture(
        {"version": 2, "captures": [], "tasks": [], "plannerLogs": [], "plannerSuggestions": []},
        "未知采集类型应按普通任务兼容处理。",
        "pytest",
        context_kind="typo",
    )
    capture = space["captures"][0]

    assert capture["contextKind"] == "task"


def test_ai_task_space_capture_replaces_invalid_surrogates(tmp_path):
    path = tmp_path / "task-space.json"
    space = add_capture(seed_task_space(), "坏字符\udcac仍可采集", "pytest")
    saved = save_task_space(path, space)
    loaded = load_task_space(path)
    assert "\udcac" not in saved["captures"][0]["rawText"]
    assert "坏字符" in loaded["captures"][0]["rawText"]


def test_mutate_task_space_recovers_from_stale_lock(tmp_path):
    path = tmp_path / "task-space.json"
    save_task_space(path, {"version": 2, "captures": [], "tasks": [], "plannerLogs": [], "plannerSuggestions": []})
    lock_path = path.with_name(f"{path.name}.lock")
    lock_path.write_text("stale", encoding="utf-8")
    os.utime(lock_path, (1, 1))

    updated = mutate_task_space(path, lambda space: add_capture(space, "陈旧锁后仍可采集", "pytest"))

    assert updated["captures"][0]["rawText"] == "陈旧锁后仍可采集"
    assert not lock_path.exists()


def test_ai_task_space_save_load_normalizes_document(tmp_path):
    path = tmp_path / "task-space.json"
    save_task_space(
        path,
        {
            "version": 2,
            "captures": [],
            "tasks": [
                {
                    "id": "task_x",
                    "title": "归一化任务",
                    "document": {
                        "goal": "目标",
                        "context": "<p><br></p>",
                        "knownFacts": "<p>事实一</p><p>事实二<br></p>",
                    },
                }
            ],
            "plannerLogs": [],
        },
    )

    loaded = load_task_space(path)
    assert loaded["tasks"][0]["kind"] == "task"
    assert loaded["tasks"][0]["status"] == "ready"
    assert loaded["tasks"][0]["document"]["goal"] == "目标"
    assert loaded["captures"] == []
    assert loaded["tasks"][0]["document"]["context"] == ""
    assert loaded["tasks"][0]["document"]["knownFacts"] == "事实一\n事实二"
    assert "nextStep" in loaded["tasks"][0]["document"]
    assert loaded["plannerSuggestions"] == []

    packet = build_execution_packet(
        {
            **loaded,
            "plannerLogs": [
                {
                    "id": "log_x",
                    "ranAt": "2026-06-19T00:00:00Z",
                    "summary": "selected",
                    "selectedTaskId": "task_x",
                    "actions": [],
                }
            ],
        }
    )
    assert "<p><br></p>" not in packet["prompt"]


def test_task_space_fingerprint_changes_without_persisting_metadata(tmp_path):
    path = tmp_path / "task-space.json"
    space = seed_task_space()
    first = task_space_fingerprint(space)
    with_fingerprint = task_space_with_fingerprint(space)
    assert with_fingerprint["_fingerprint"] == first

    changed = add_capture(space, "后台采集的新任务。", "pytest")
    assert task_space_fingerprint(changed) != first

    saved = save_task_space(path, with_fingerprint)
    loaded = load_task_space(path)
    assert "_fingerprint" not in saved
    assert "_fingerprint" not in loaded


def test_append_execution_record_updates_document_state_and_keeps_history():
    space = seed_task_space()
    task_id = space["tasks"][0]["id"]

    progressed = append_execution_record(
        space,
        task_id,
        summary="页面已接入后端任务空间，正在补执行回写入口。",
        verification="uv run pytest backend/tests/test_ai_task_space.py -q",
        remaining_risk="前端还需要类型检查和浏览器验收。",
        next_step="补 UI 后运行 typecheck 和 build。",
        status="progress",
        packet_id="packet_x",
        expected_task_updated_at=space["tasks"][0]["updatedAt"],
        steps_done=1,
        commands_run=2,
        files_changed=0,
    )
    task = next(item for item in progressed["tasks"] if item["id"] == task_id)
    assert task["status"] == "ready"
    assert task["document"]["currentState"] == "页面已接入后端任务空间，正在补执行回写入口。"
    assert task["document"]["nextStep"] == "补 UI 后运行 typecheck 和 build。"
    assert "验证：" in task["document"]["knownFacts"]
    assert "剩余风险：" in task["document"]["dependencies"]
    assert task["executionRecords"][0]["summary"] == task["document"]["currentState"]
    assert task["executionRecords"][0]["packetId"] == "packet_x"
    assert task["executionRecords"][0]["budgetUsed"] == {
        "stepsDone": 1,
        "commandsRun": 2,
        "filesChanged": 0,
    }
    assert task["evidenceLog"][0].endswith("执行回写：页面已接入后端任务空间，正在补执行回写入口。")

    completed = append_execution_record(
        progressed,
        task_id,
        summary="执行回写入口已完成。",
        status="done",
    )
    task = next(item for item in completed["tasks"] if item["id"] == task_id)
    assert task["status"] == "done"
    assert task["completedAt"]
    assert task["document"]["currentState"] == "执行回写入口已完成。"
    assert task["document"]["resultSummary"] == "执行回写入口已完成。"


def test_append_execution_record_keeps_latest_verification_and_risk_in_document():
    space = seed_task_space()
    task_id = space["tasks"][0]["id"]

    first = append_execution_record(
        space,
        task_id,
        summary="第一轮推进完成。",
        verification="第一轮验证。",
        remaining_risk="第一轮风险。",
        next_step="第二轮继续。",
        status="progress",
    )
    second = append_execution_record(
        first,
        task_id,
        summary="第二轮推进完成。",
        verification="第二轮验证。",
        remaining_risk="第二轮风险。",
        next_step="第三轮继续。",
        status="progress",
    )

    task = next(item for item in second["tasks"] if item["id"] == task_id)
    assert len(task["executionRecords"]) == 2
    assert task["executionRecords"][1]["verification"] == "第一轮验证。"
    assert "验证：第一轮验证。" not in task["document"]["knownFacts"]
    assert "验证：第二轮验证。" in task["document"]["knownFacts"]
    assert "剩余风险：第一轮风险。" not in task["document"]["dependencies"]
    assert "剩余风险：第二轮风险。" in task["document"]["dependencies"]


def test_append_execution_record_rejects_stale_snapshot():
    space = seed_task_space()
    task = space["tasks"][0]

    try:
        append_execution_record(
            space,
            task["id"],
            summary="旧执行包尝试回写。",
            expected_task_updated_at="2026-01-01T00:00:00Z",
        )
    except ExecutionSnapshotMismatch as exc:
        assert task["updatedAt"] in str(exc)
    else:
        raise AssertionError("stale execution snapshot should be rejected")


def test_append_execution_record_is_idempotent_for_same_packet_payload():
    space = seed_task_space()
    task_id = space["tasks"][0]["id"]

    first = append_execution_record(
        space,
        task_id,
        summary="本轮完成了执行包幂等保护。",
        verification="uv run pytest backend/tests/test_ai_task_space.py -q",
        remaining_risk="需要继续验证 CLI 和 API 行为。",
        next_step="补 CLI/API 测试。",
        status="progress",
        packet_id="packet_repeat",
        expected_task_updated_at=space["tasks"][0]["updatedAt"],
        steps_done=1,
        commands_run=1,
        files_changed=1,
    )
    second = append_execution_record(
        first,
        task_id,
        summary="本轮完成了执行包幂等保护。",
        verification="uv run pytest backend/tests/test_ai_task_space.py -q",
        remaining_risk="需要继续验证 CLI 和 API 行为。",
        next_step="补 CLI/API 测试。",
        status="progress",
        packet_id="packet_repeat",
        expected_task_updated_at=space["tasks"][0]["updatedAt"],
        steps_done=1,
        commands_run=1,
        files_changed=1,
    )

    task = next(item for item in second["tasks"] if item["id"] == task_id)
    assert len(task["executionRecords"]) == 1
    assert len([line for line in task["evidenceLog"] if "执行包幂等保护" in line]) == 1
    assert task["executionRecords"][0]["packetId"] == "packet_repeat"


def test_append_execution_record_rejects_same_packet_with_different_payload():
    space = seed_task_space()
    task_id = space["tasks"][0]["id"]
    first = append_execution_record(
        space,
        task_id,
        summary="第一次回写。",
        status="progress",
        packet_id="packet_conflict",
        expected_task_updated_at=space["tasks"][0]["updatedAt"],
    )

    try:
        append_execution_record(
            first,
            task_id,
            summary="第二次回写改了摘要。",
            status="progress",
            packet_id="packet_conflict",
        )
    except ExecutionPacketReplayConflict as exc:
        assert "packet_conflict" in str(exc)
    else:
        raise AssertionError("same packet id with different payload should be rejected")


def test_build_execution_packet_respects_execution_policy_and_risk():
    space = seed_task_space()
    task = space["tasks"][0]
    task["executionPolicy"] = "auto_safe"
    task["risk"] = "low"
    task["status"] = "running"
    space["plannerLogs"] = [
        {
            "id": "log_x",
            "ranAt": "2026-06-19T00:00:00Z",
            "summary": "selected",
            "selectedTaskId": task["id"],
            "actions": [],
        }
    ]

    packet = build_execution_packet(space, username="code4101")
    assert packet["hasTask"] is True
    assert packet["task"]["id"] == task["id"]
    assert packet["decision"]["mode"] == "execute_safe"
    assert packet["budget"]["mayModifyCode"] is True
    assert packet["budget"]["maxFilesChanged"] == 999
    assert packet["snapshot"]["taskId"] == task["id"]
    assert packet["snapshot"]["documentDigest"]["goal"] == task["document"]["goal"]
    assert packet["plannerSuggestions"] == []
    assert packet["writeback"]["taskId"] == task["id"]
    assert packet["writeback"]["username"] == "code4101"
    assert "--username code4101" in packet["writeback"]["cli"]
    assert "--max-steps 999" in packet["writeback"]["cli"]
    assert "--max-commands 999" in packet["writeback"]["cli"]
    assert "--max-files-changed 999" in packet["writeback"]["cli"]
    assert "--summary <本轮摘要>" in packet["writeback"]["cli"]
    assert "--verification <验证命令或无法验证原因>" in packet["writeback"]["cli"]
    assert "--remaining-risk <剩余风险或待审核点>" in packet["writeback"]["cli"]
    assert "--next-step <下一轮最小步骤>" in packet["writeback"]["cli"]
    assert "--json" in packet["writeback"]["cli"]
    assert packet["writeback"]["argvTemplate"][:4] == [
        "uv",
        "run",
        "python",
        "scripts/ai_task_space_append_execution_record.py",
    ]
    assert "--task-id" in packet["writeback"]["argvTemplate"]
    assert task["id"] in packet["writeback"]["argvTemplate"]
    assert "--summary" in packet["writeback"]["argvTemplate"]
    assert "<本轮摘要>" in packet["writeback"]["argvTemplate"]
    assert "--verification" in packet["writeback"]["argvTemplate"]
    assert "--remaining-risk" in packet["writeback"]["argvTemplate"]
    assert "--next-step" in packet["writeback"]["argvTemplate"]
    assert "--json" in packet["writeback"]["argvTemplate"]
    assert "automation_directive.shouldWriteBack" in packet["prompt"]
    assert "执行回写接口" in packet["prompt"]
    assert "执行权限：完整权限" in packet["prompt"]
    assert "本轮整理建议" in packet["prompt"]

    task["executionPolicy"] = "ask_before_execute"
    packet = build_execution_packet(space)
    assert packet["decision"]["mode"] == "execute_safe"
    assert packet["budget"]["mayModifyCode"] is True
    assert packet["budget"]["maxFilesChanged"] == 999

    task["executionPolicy"] = "manual_only"
    packet = build_execution_packet(space)
    assert packet["decision"]["mode"] == "execute_safe"
    assert packet["budget"]["maxSteps"] == 999

    task["executionPolicy"] = "auto_safe"
    task["risk"] = "high"
    packet = build_execution_packet(space)
    assert packet["decision"]["mode"] == "execute_safe"
    assert "完整执行权限" in packet["decision"]["reason"]


def test_automation_directive_combines_audit_and_execution_mode():
    space = seed_task_space()
    task = space["tasks"][0]
    task["status"] = "running"
    task["executionPolicy"] = "auto_safe"
    task["risk"] = "low"
    space["plannerLogs"] = [
        {
            "id": "log_x",
            "ranAt": "2026-06-19T00:00:00Z",
            "summary": "selected",
            "selectedTaskId": task["id"],
            "actions": [],
        }
    ]

    packet = build_execution_packet(space, username="code4101")
    directive = build_automation_directive(packet, audit_task_space(space))
    assert directive["action"] == "execute_safe"
    assert directive["shouldExecute"] is True
    assert directive["shouldModifyCode"] is True
    assert directive["shouldWriteBack"] is True
    assert directive["completionTemplate"]["writeback"]["status"] == "progress"
    assert "验证" in directive["completionTemplate"]["writeback"]["verification"]
    assert "回写 current_fingerprint" in directive["completionTemplate"]["finalReport"]
    assert any("回写 JSON" in note for note in directive["completionTemplate"]["notes"])

    task["executionPolicy"] = "ask_before_execute"
    packet = build_execution_packet(space, username="code4101")
    directive = build_automation_directive(packet, audit_task_space(space))
    assert directive["action"] == "execute_safe"
    assert directive["shouldExecute"] is True
    assert directive["shouldModifyCode"] is True
    assert directive["writebackStatus"] == "progress"
    assert "未验证" in directive["completionTemplate"]["writeback"]["remainingRisk"]

    task["executionRecords"] = [
        {
            "id": "exec_waiting",
            "recordedAt": "2026-06-19T00:10:00Z",
            "summary": "已整理执行建议，等待用户确认，未修改业务代码。",
            "verification": "未执行命令，确认未修改业务代码。",
            "remainingRisk": "需要用户确认范围。",
            "nextStep": "等待用户确认后再推进。",
            "status": "progress",
        }
    ]
    packet = build_execution_packet(space, username="code4101")
    directive = build_automation_directive(packet, audit_task_space(space))
    assert directive["action"] == "execute_safe"
    assert directive["shouldExecute"] is True
    assert directive["shouldModifyCode"] is True
    assert directive["shouldWriteBack"] is True
    assert directive["writebackStatus"] == "progress"

    task["executionRecords"] = [
        {
            "id": "exec_report",
            "recordedAt": "2026-06-19T00:11:00Z",
            "summary": "已完成只读分析，未修改业务代码。",
            "verification": "未执行命令。",
            "remainingRisk": "",
            "nextStep": "继续分析。",
            "status": "progress",
        }
    ]
    packet = build_execution_packet(space, username="code4101")
    directive = build_automation_directive(packet, audit_task_space(space))
    assert directive["action"] == "execute_safe"
    assert directive["shouldWriteBack"] is True
    assert directive["writebackStatus"] == "progress"

    space["plannerLogs"][0]["selectedTaskId"] = "missing"
    packet = build_execution_packet(space, username="code4101")
    directive = build_automation_directive(packet, audit_task_space(space))
    assert directive["action"] == "stop_for_audit"
    assert directive["shouldExecute"] is False
    assert directive["writebackStatus"] is None
    assert directive["completionTemplate"]["writeback"]["status"] is None
    assert "audit error" in directive["completionTemplate"]["writeback"]["remainingRisk"]


def test_build_automation_prompt_contains_stable_execution_contract():
    prompt = build_automation_prompt("code4101")
    assert "uv run python scripts/ai_task_space_plan_once.py --username code4101 --json" in prompt
    assert "`automation_directive`" in prompt
    assert "`planner_state`" in prompt
    assert "`ask_user` / `report_only`：兼容旧执行包" in prompt
    assert "`execute_safe`：默认拥有完整执行权限" in prompt
    assert "execution_packet.writeback.cli" in prompt
    assert "execution_packet.writeback.argvTemplate" in prompt
    assert "automation_directive.shouldWriteBack" in prompt
    assert "为 false 时不要调用回写 CLI" in prompt
    assert "automation_directive.completionTemplate" in prompt
    assert "execution_packet.plannerSuggestions" in prompt
    assert "全量读取任务空间" in prompt
    assert "旧规划检查输出、旧页面状态和上轮执行包" in prompt
    assert "采集脚本进入 Inbox，影响下一次规划检查" in prompt
    assert "回写 JSON" in prompt
    assert "current_fingerprint" in prompt
    assert "保持这些 `--max-*` 参数" in prompt
    assert "不要自动归档任务" in prompt


def test_execution_packet_carries_relevant_planner_suggestions():
    space = seed_task_space()
    task = space["tasks"][0]
    task["status"] = "ready"
    task["document"]["doneCriteria"] = ""
    planned = run_planner_check(space)
    packet = build_execution_packet(planned, task["id"])

    assert packet["plannerSuggestions"]
    assert packet["plannerSuggestions"][0]["taskId"] == task["id"]
    assert "完成标准" in packet["plannerSuggestions"][0]["title"]
    assert "完成标准" in packet["prompt"]


def test_planner_prefers_actionable_leaf_task_and_converges_running_state():
    space = seed_task_space()
    root = space["tasks"][0]
    child = space["tasks"][1]

    root["status"] = "running"
    root["executionPolicy"] = "ask_before_execute"
    root["risk"] = "medium"
    child["status"] = "running"
    child["executionPolicy"] = "auto_report"
    child["risk"] = "low"
    child["parentId"] = root["id"]

    after = run_planner_check(space)
    latest_log = after["plannerLogs"][0]
    assert latest_log["selectedTaskId"] == child["id"]
    assert latest_log["planningDecision"]["selectedTaskId"] == child["id"]
    assert latest_log["planningDecision"]["candidateCount"] >= 1
    assert "优先选择" in latest_log["planningDecision"]["selectedReason"]
    assert any(
        item["taskId"] == root["id"] and any("存在未完成子任务" in reason for reason in item["reasons"])
        for item in latest_log["planningDecision"]["skipped"]
    )

    selected = next(task for task in after["tasks"] if task["id"] == child["id"])
    parent = next(task for task in after["tasks"] if task["id"] == root["id"])
    assert selected["status"] == "ready"
    assert parent["status"] == "ready"
    assert [task for task in after["tasks"] if task["status"] == "running"] == []

    packet = build_execution_packet(after)
    assert packet["task"]["id"] == child["id"]
    assert packet["decision"]["mode"] == "execute_safe"
    assert packet["planningDecision"]["selectedTaskId"] == child["id"]


def test_execution_packet_explains_manual_task_selection_when_not_latest_candidate():
    space = seed_task_space()
    first = space["tasks"][0]
    second = space["tasks"][1]
    first["status"] = "ready"
    first["executionPolicy"] = "ask_before_execute"
    second["status"] = "ready"
    second["executionPolicy"] = "auto_report"

    planned = run_planner_check(space)
    assert planned["plannerLogs"][0]["selectedTaskId"] == second["id"]

    packet = build_execution_packet(planned, first["id"])

    assert packet["task"]["id"] == first["id"]
    assert packet["planningDecision"]["selectedTaskId"] == first["id"]
    assert packet["planningDecision"]["requestedTaskId"] == first["id"]
    assert "由用户选中" in packet["planningDecision"]["selectedReason"]
    assert second["title"] in packet["planningDecision"]["selectedReason"]


def test_manual_execution_packet_skips_parent_with_active_child():
    space = seed_task_space()
    parent = space["tasks"][0]
    child = space["tasks"][1]
    parent["status"] = "ready"
    parent["executionPolicy"] = "auto_safe"
    parent["risk"] = "low"
    child["status"] = "ready"
    child["executionPolicy"] = "auto_report"
    child["risk"] = "low"
    child["parentId"] = parent["id"]

    planned = run_planner_check(space)
    assert planned["plannerLogs"][0]["selectedTaskId"] == child["id"]

    packet = build_execution_packet(planned, parent["id"])
    directive = build_automation_directive(packet, audit_task_space(planned))

    assert packet["task"]["id"] == parent["id"]
    assert packet["decision"]["mode"] == "skip"
    assert "存在未完成子任务" in packet["decision"]["reason"]
    assert packet["budget"]["maxSteps"] == 0
    assert packet["budget"]["mayModifyCode"] is False
    assert directive["action"] == "skip"
    assert directive["shouldExecute"] is False
    assert directive["shouldWriteBack"] is False


def test_planner_does_not_repeat_candidate_evidence_for_same_selected_task():
    space = seed_task_space()
    task = space["tasks"][1]
    task["status"] = "ready"
    task["executionPolicy"] = "ask_before_execute"
    task["risk"] = "medium"
    task["evidenceLog"] = [
        "2026-06-19T02:00:00Z 规划检查选为本轮执行候选。",
        "2026-06-19T01:00:00Z 规划检查选为本轮执行候选。",
        "2026-06-19T00:00:00Z 其它证据。",
    ]

    first = run_planner_check(space)
    selected_id = first["plannerLogs"][0]["selectedTaskId"]
    selected = next(item for item in first["tasks"] if item["id"] == selected_id)
    assert len([line for line in selected["evidenceLog"] if "规划检查选为本轮执行候选" in line]) == 1
    assert any("压缩" in action and selected["title"] in action for action in first["plannerLogs"][0]["actions"])

    progressed = append_execution_record(
        first,
        selected["id"],
        summary="已整理建议，等待用户确认。",
        verification="未执行代码；仅整理建议。",
        remaining_risk="等待用户确认范围。",
        next_step="等待用户确认后继续。",
        status="progress",
        packet_id="packet_same_candidate",
        expected_task_updated_at=selected["updatedAt"],
    )
    second = run_planner_check(progressed)
    selected_again = next(item for item in second["tasks"] if item["id"] == selected["id"])

    assert second["plannerLogs"][0]["selectedTaskId"] == selected["id"]
    assert any("继续本轮候选" in action for action in second["plannerLogs"][0]["actions"])
    assert len([line for line in selected_again["evidenceLog"] if "规划检查选为本轮执行候选" in line]) == 1


def test_planner_triages_all_inbox_captures_each_planning_check():
    space = seed_task_space()
    for index in range(5):
        space = add_capture(space, f"批量采集任务 {index}：每次规划检查都应整理。", "pytest")

    after = run_planner_check(space)

    assert [capture["status"] for capture in after["captures"]].count("inbox") == 0
    linked_task_ids = {
        capture.get("linkedTaskId")
        for capture in after["captures"]
        if capture.get("rawText", "").startswith("批量采集任务")
    }
    assert len(linked_task_ids) == 5
    assert all(
        any(task["id"] == task_id for task in after["tasks"])
        for task_id in linked_task_ids
    )
    assert any("批量采集任务 0" in action for action in after["plannerLogs"][0]["actions"])


def test_planner_rebuilds_dependency_blocking_each_planning_check():
    space = seed_task_space()
    root = space["tasks"][0]
    child = space["tasks"][1]
    root["status"] = "ready"
    root["kind"] = "task"
    root["executionPolicy"] = "auto_report"
    root["risk"] = "low"
    root["dependsOn"] = [child["id"]]
    child["status"] = "planned"
    child["executionPolicy"] = "auto_report"
    child["risk"] = "low"

    after = run_planner_check(space)
    root_after = next(task for task in after["tasks"] if task["id"] == root["id"])
    child_after = next(task for task in after["tasks"] if task["id"] == child["id"])
    assert root_after["status"] == "ready"
    assert "等待前置任务完成" in root_after["document"]["dependencies"]
    assert child_after["status"] == "ready"
    assert after["plannerLogs"][0]["selectedTaskId"] == child["id"]

    child_after["status"] = "done"
    after_done = run_planner_check(after)
    root_done = next(task for task in after_done["tasks"] if task["id"] == root["id"])
    assert root_done["status"] == "ready"
    assert root_done["document"]["dependencies"] == ""
    assert after_done["plannerLogs"][0]["selectedTaskId"] == root["id"]


def test_planner_keeps_completed_context_but_skips_closed_tasks():
    space = seed_task_space()
    active = add_capture(space, "后续整理归档语义测试。", "pytest")
    promoted = promote_capture(active, active["captures"][0]["id"])
    completed = promoted["tasks"][0]
    archived = promoted["tasks"][1]
    active_task = next(task for task in promoted["tasks"] if task["id"] == promoted["captures"][0]["linkedTaskId"])

    completed["status"] = "done"
    completed["document"]["resultSummary"] = "完成任务保留为参考素材。"
    archived["status"] = "archived"
    active_task["status"] = "ready"
    active_task["executionPolicy"] = "auto_report"
    active_task["risk"] = "low"
    active_task["dependsOn"] = [completed["id"]]
    promoted["plannerLogs"] = [
        {
            "id": "log_old",
            "ranAt": "2026-06-19T00:00:00Z",
            "summary": "old",
            "selectedTaskId": archived["id"],
            "actions": [],
        }
    ]

    after = run_planner_check(promoted)
    selected_id = after["plannerLogs"][0]["selectedTaskId"]
    assert selected_id == active_task["id"]

    completed_after = next(task for task in after["tasks"] if task["id"] == completed["id"])
    archived_after = next(task for task in after["tasks"] if task["id"] == archived["id"])
    assert completed_after["status"] == "done"
    assert completed_after["document"]["resultSummary"] == "完成任务保留为参考素材。"
    assert archived_after["status"] == "archived"
    assert all(task["status"] != "running" for task in [completed_after, archived_after])

    packet = build_execution_packet(after)
    assert packet["task"]["id"] == active_task["id"]
    audit = audit_task_space(after)
    assert audit["summary"]["activeTasks"] == len([task for task in after["tasks"] if task["status"] != "archived"])
    assert not any(issue["code"] == "selected_task_archived" for issue in audit["issues"])


def test_planner_does_not_clear_manual_block_without_dependency_marker():
    space = seed_task_space()
    task = space["tasks"][0]
    task["status"] = "blocked"
    task["risk"] = "low"
    task["executionPolicy"] = "auto_report"
    task["document"]["dependencies"] = "等待用户确认范围。"

    after = run_planner_check(space)
    task_after = next(item for item in after["tasks"] if item["id"] == task["id"])
    assert task_after["status"] == "ready"
    assert task_after["document"]["dependencies"] == "等待用户确认范围。"
    assert after["plannerLogs"][0]["selectedTaskId"]


def test_planner_ignores_waiting_confirmation_as_permission_constraint():
    space = seed_task_space()
    fallback = space["tasks"][0]
    waiting = space["tasks"][1]

    fallback["status"] = "ready"
    fallback["executionPolicy"] = "auto_report"
    fallback["risk"] = "low"
    waiting["status"] = "ready"
    waiting["parentId"] = None
    waiting["executionPolicy"] = "ask_before_execute"
    waiting["risk"] = "medium"
    waiting["executionRecords"] = [
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

    after = run_planner_check(space)
    latest_log = after["plannerLogs"][0]

    assert latest_log["selectedTaskId"] == waiting["id"]
    skipped = latest_log["planningDecision"]["skipped"]
    assert not any(
        item["taskId"] == waiting["id"] and "最近执行记录已等待用户确认" in item["reasons"]
        for item in skipped
    )


def test_user_confirmation_allows_waiting_task_to_reenter_planner_candidates():
    space = seed_task_space()
    waiting = space["tasks"][1]
    waiting["status"] = "ready"
    waiting["parentId"] = None
    waiting["executionPolicy"] = "ask_before_execute"
    waiting["risk"] = "medium"
    waiting["executionRecords"] = [
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

    confirmed = confirm_task_user_ready(space, waiting["id"], note="范围已确认。")
    confirmed_task = next(item for item in confirmed["tasks"] if item["id"] == waiting["id"])
    assert confirmed_task["executionRecords"][0]["summary"].startswith("用户已确认继续推进")
    assert "范围已确认" in confirmed_task["executionRecords"][0]["verification"]
    assert confirmed_task["document"]["currentState"] == "用户已确认继续推进，等待下一次规划检查重新评估。"
    assert confirmed_task["document"]["nextStep"] == "等待用户确认后继续。"

    after = run_planner_check(confirmed)
    latest_log = after["plannerLogs"][0]
    assert latest_log["selectedTaskId"] == waiting["id"]
    assert not any(
        item["taskId"] == waiting["id"] and "最近执行记录已等待用户确认" in item["reasons"]
        for item in latest_log["planningDecision"]["skipped"]
    )


def test_planner_has_no_candidate_when_all_tasks_are_blocked():
    space = seed_task_space()
    for task in space["tasks"]:
        task["status"] = "blocked"
        task["executionPolicy"] = "auto_report"
        task["risk"] = "low"
        task["document"]["dependencies"] = "等待用户确认。"

    after = run_planner_check(space)
    latest_log = after["plannerLogs"][0]

    assert latest_log["selectedTaskId"]
    assert latest_log["planningDecision"]["candidateCount"] >= 1
    assert latest_log["planningDecision"]["skippedCount"] >= 0


def test_planner_generates_restructuring_suggestions():
    space = seed_task_space()
    root = space["tasks"][0]
    child = space["tasks"][1]
    root["title"] = "重复任务"
    root["status"] = "ready"
    root["document"]["nextStep"] = ""
    root["document"]["context"] = "很长的上下文。" * 80
    child["title"] = "重复任务"
    child["parentId"] = None
    child["status"] = "ready"

    suggestions = build_planner_suggestions(space)
    assert {suggestion["kind"] for suggestion in suggestions} >= {"document", "merge"}
    assert all(suggestion["id"].startswith("sug_") for suggestion in suggestions)
    merge_suggestion = next(suggestion for suggestion in suggestions if suggestion["kind"] == "merge")
    assert merge_suggestion["preview"]["sourceTaskIds"] == [child["id"]]

    after = run_planner_check(space)
    latest_log = after["plannerLogs"][0]
    assert after["plannerSuggestions"]
    assert latest_log["suggestionIds"]
    assert any("任务树整理建议" in action for action in latest_log["actions"])


def test_planner_suggestion_apply_and_dismiss_are_stable_across_planning_check():
    space = seed_task_space()
    task = space["tasks"][0]
    task["status"] = "ready"
    task["document"]["doneCriteria"] = ""

    planned = run_planner_check(space)
    suggestion = next(item for item in planned["plannerSuggestions"] if "完成标准" in item["title"])
    applied = apply_planner_suggestion(planned, suggestion["id"])
    applied_task = next(item for item in applied["tasks"] if item["id"] == task["id"])
    applied_suggestion = next(item for item in applied["plannerSuggestions"] if item["id"] == suggestion["id"])
    assert applied_task["document"]["doneCriteria"]
    assert applied_suggestion["status"] == "applied"
    assert applied_suggestion["resolvedAt"]

    after_apply_plan = run_planner_check(applied)
    applied_after_plan = next(
        item for item in after_apply_plan["plannerSuggestions"] if item["id"] == suggestion["id"]
    )
    assert applied_after_plan["status"] == "applied"
    assert applied_after_plan["resolvedAt"] == applied_suggestion["resolvedAt"]
    assert not [
        item
        for item in after_apply_plan["plannerSuggestions"]
        if item["id"] == suggestion["id"] and item["status"] == "open"
    ]
    assert suggestion["id"] not in after_apply_plan["plannerLogs"][0]["suggestionIds"]

    task_2 = space["tasks"][1]
    task_2["status"] = "ready"
    task_2["document"]["doneCriteria"] = ""
    planned_again = run_planner_check(space)
    suggestion_2 = next(item for item in planned_again["plannerSuggestions"] if item.get("taskId") == task_2["id"])
    dismissed = dismiss_planner_suggestion(planned_again, suggestion_2["id"])
    dismissed_suggestion = next(item for item in dismissed["plannerSuggestions"] if item["id"] == suggestion_2["id"])
    assert dismissed_suggestion["resolvedAt"]
    after_dismiss_plan = run_planner_check(dismissed)
    dismissed_after_plan = next(
        item for item in after_dismiss_plan["plannerSuggestions"] if item["id"] == suggestion_2["id"]
    )
    assert dismissed_after_plan["status"] == "dismissed"
    assert dismissed_after_plan["resolvedAt"] == dismissed_suggestion["resolvedAt"]
    assert not [
        item
        for item in after_dismiss_plan["plannerSuggestions"]
        if item["id"] == suggestion_2["id"] and item["status"] == "open"
    ]
    assert suggestion_2["id"] not in after_dismiss_plan["plannerLogs"][0]["suggestionIds"]


def test_split_suggestion_apply_keeps_single_task_node():
    space = seed_task_space()
    task = space["tasks"][0]
    task["status"] = "ready"
    task["document"]["context"] = "很长的上下文。" * 80
    space["tasks"][1]["parentId"] = None

    suggestion = {
        "id": "sug_manual_split",
        "kind": "split",
        "taskId": task["id"],
        "relatedTaskIds": [],
        "title": f"拆分「{task['title']}」",
        "rationale": "历史 split 建议。",
        "proposedAction": "整理文档段落。",
        "preview": _split_suggestion_preview(task),
        "status": "open",
        "createdAt": now_iso(),
    }
    space["plannerSuggestions"] = [suggestion]
    applied = apply_planner_suggestion(space, suggestion["id"])
    parent = next(item for item in applied["tasks"] if item["id"] == task["id"])
    children = [item for item in applied["tasks"] if item.get("parentId") == task["id"]]

    assert children == []
    assert suggestion["preview"]["creates"][1]["dependsOnPrevious"] is True
    assert suggestion["preview"]["creates"][1]["dependsOnTitle"] == suggestion["preview"]["creates"][0]["title"]
    assert suggestion["preview"]["creates"][2]["dependsOnPrevious"] is True
    assert suggestion["preview"]["creates"][2]["dependsOnTitle"] == suggestion["preview"]["creates"][1]["title"]
    assert parent["document"]["currentState"] == "已按规划建议补充任务文档；任务仍作为单一节点直接推进。"
    assert "未创建子任务" not in parent["document"]["knownFacts"]
    assert next(item for item in applied["plannerSuggestions"] if item["id"] == suggestion["id"])["status"] == "applied"


def test_planner_no_longer_generates_split_children_for_long_context():
    space = seed_task_space()
    task = space["tasks"][0]
    task["status"] = "ready"
    task["executionPolicy"] = "auto_report"
    task["document"]["context"] = "很长的上下文。" * 80
    space["tasks"][1]["parentId"] = None
    space["tasks"][1]["status"] = "done"

    planned = run_planner_check(space)
    assert not [item for item in planned["plannerSuggestions"] if item["kind"] == "split"]
    assert not [item for item in planned["tasks"] if item.get("parentId") == task["id"]]


def test_merge_suggestion_apply_marks_duplicates_for_archive_review():
    space = seed_task_space()
    primary = space["tasks"][0]
    duplicate = space["tasks"][1]
    primary["title"] = "重复任务"
    primary["status"] = "ready"
    duplicate["title"] = "重复任务"
    duplicate["parentId"] = None
    duplicate["status"] = "ready"
    duplicate["document"]["context"] = "重复任务里的上下文。"
    duplicate["evidenceLog"] = ["旧证据"]

    planned = run_planner_check(space)
    suggestion = next(item for item in planned["plannerSuggestions"] if item["kind"] == "merge")
    applied = apply_planner_suggestion(planned, suggestion["id"])
    merged_primary = next(item for item in applied["tasks"] if item["id"] == primary["id"])
    merged_duplicate = next(item for item in applied["tasks"] if item["id"] == duplicate["id"])

    assert "重复任务里的上下文" in merged_primary["document"]["context"]
    assert duplicate["id"] in merged_primary["relatedTaskIds"]
    assert merged_duplicate["status"] == "done"
    assert merged_duplicate["relatedTaskIds"] == [primary["id"]]
    assert "已合并到" in merged_duplicate["document"]["currentState"]
    assert next(item for item in applied["plannerSuggestions"] if item["id"] == suggestion["id"])["status"] == "applied"


def test_audit_task_space_reports_invariants():
    space = seed_task_space()
    root = space["tasks"][0]
    child = space["tasks"][1]
    root["status"] = "running"
    child["status"] = "running"
    child["dependsOn"] = ["missing_task"]
    space["plannerLogs"] = [
        {
            "id": "log_x",
            "ranAt": "2026-06-19T00:00:00Z",
            "summary": "selected",
            "selectedTaskId": "missing_selected",
            "actions": [],
        }
    ]

    audit = audit_task_space(space)
    assert audit["ok"] is False
    assert audit["summary"]["runningTasks"] == 0
    assert audit["summary"]["errors"] >= 2
    assert {issue["code"] for issue in audit["issues"]} >= {
        "selected_task_missing",
        "dependency_missing",
    }

    after = run_planner_check(space)
    audit = audit_task_space(after)
    assert audit["summary"]["runningTasks"] == 0
    assert audit["summary"]["latestSelectedTaskId"] is None

