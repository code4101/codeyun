from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any

from backend.core.ai_task_space import (
    audit_task_space,
    build_automation_directive,
    build_execution_packet,
    build_automation_prompt,
)


def default_automation_toml_path() -> Path:
    return Path.home() / ".codex" / "automations" / "ai" / "automation.toml"


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def render_automation_toml(
    *,
    username: str,
    cwd: Path,
    automation_id: str = "ai",
    name: str = "AI任务空间自动化执行",
    rrule: str = "FREQ=HOURLY;INTERVAL=1",
    model: str = "gpt-5.4",
    reasoning_effort: str = "medium",
    status: str = "ACTIVE",
) -> str:
    prompt = build_automation_prompt(username)
    lines = [
        "version = 1",
        f"id = {_toml_string(automation_id)}",
        'kind = "cron"',
        f"name = {_toml_string(name)}",
        f"prompt = {_toml_string(prompt)}",
        f"status = {_toml_string(status)}",
        f"rrule = {_toml_string(rrule)}",
        f"model = {_toml_string(model)}",
        f"reasoning_effort = {_toml_string(reasoning_effort)}",
        'execution_environment = "local"',
        f"cwds = [{_toml_string(str(cwd.resolve(strict=False)))}]",
        "",
    ]
    return "\n".join(lines)


def write_automation_toml(
    path: Path,
    *,
    username: str,
    cwd: Path,
    automation_id: str = "ai",
    name: str = "AI任务空间自动化执行",
    rrule: str = "FREQ=HOURLY;INTERVAL=1",
    model: str = "gpt-5.4",
    reasoning_effort: str = "medium",
    status: str = "ACTIVE",
) -> str:
    text = render_automation_toml(
        username=username,
        cwd=cwd,
        automation_id=automation_id,
        name=name,
        rrule=rrule,
        model=model,
        reasoning_effort=reasoning_effort,
        status=status,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return text


def _selected_task_id(task_space: dict[str, Any]) -> str | None:
    latest_log = (task_space.get("plannerLogs") or [{}])[0]
    if not isinstance(latest_log, dict):
        return None
    selected = latest_log.get("selectedTaskId")
    return str(selected) if selected else None


def _require(condition: bool, code: str, message: str, failures: list[dict[str, str]]) -> None:
    if not condition:
        failures.append({"code": code, "message": message})


def validate_automation_toml(
    path: Path,
    *,
    expected_prompt: str,
    expected_cwd: Path,
) -> dict[str, Any]:
    failures: list[dict[str, str]] = []
    if not path.exists():
        return {
            "path": str(path),
            "exists": False,
            "config": None,
            "failures": [{"code": "automation_toml_missing", "message": "automation.toml does not exist"}],
        }

    try:
        config = tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # pragma: no cover - defensive for corrupt local config
        return {
            "path": str(path),
            "exists": True,
            "config": None,
            "failures": [{"code": "automation_toml_unreadable", "message": str(exc)}],
        }

    prompt = str(config.get("prompt") or "")
    cwds = config.get("cwds") if isinstance(config.get("cwds"), list) else []
    normalized_cwds = {str(Path(str(item)).resolve(strict=False)) for item in cwds}
    normalized_expected_cwd = str(expected_cwd.resolve(strict=False))

    _require(config.get("kind") == "cron", "automation_kind_drift", "automation kind must be cron", failures)
    _require(config.get("status") == "ACTIVE", "automation_status_drift", "automation status must be ACTIVE", failures)
    _require(config.get("execution_environment") == "local", "automation_environment_drift", "automation must run in local environment", failures)
    _require(normalized_expected_cwd in normalized_cwds, "automation_cwd_drift", "automation cwds must include this repository", failures)
    _require(prompt == expected_prompt, "automation_prompt_drift", "automation prompt must exactly match build_automation_prompt()", failures)

    return {
        "path": str(path),
        "exists": True,
        "config": {
            "id": config.get("id"),
            "name": config.get("name"),
            "kind": config.get("kind"),
            "status": config.get("status"),
            "rrule": config.get("rrule"),
            "model": config.get("model"),
            "reasoning_effort": config.get("reasoning_effort"),
            "execution_environment": config.get("execution_environment"),
            "cwds": cwds,
            "promptMatches": prompt == expected_prompt,
        },
        "failures": failures,
    }


def validate_contract(
    task_space: dict[str, Any],
    *,
    username: str,
    prompt: str,
) -> dict[str, Any]:
    audit = audit_task_space(task_space)
    selected_task_id = _selected_task_id(task_space)
    packet = build_execution_packet(task_space, selected_task_id, username=username)
    directive = build_automation_directive(packet, audit)
    failures: list[dict[str, str]] = []

    _require("scripts/ai_task_space_plan_once.py" in prompt, "prompt_missing_plan_once", "automation prompt must run planning check first", failures)
    _require("--json" in prompt, "prompt_missing_json", "automation prompt must request planning JSON output", failures)
    _require("不能凭聊天记忆选择任务" in prompt, "prompt_missing_no_memory_selection", "automation prompt must forbid selecting tasks from chat memory", failures)
    _require("全量读取任务空间" in prompt, "prompt_missing_full_task_space_read", "automation prompt must require full task-space reread each planning check", failures)
    _require("旧规划检查输出" in prompt and "视为过期" in prompt, "prompt_missing_stale_snapshot_boundary", "automation prompt must mark old planning/page/packet snapshots stale after writes", failures)
    _require("automation_directive" in prompt, "prompt_missing_directive", "automation prompt must treat automation_directive as boundary", failures)
    _require("planner_state" in prompt, "prompt_missing_planner_state", "automation prompt must require top-level planner_state review", failures)
    _require("planningDecision" in prompt, "prompt_missing_planning_decision", "automation prompt must require planningDecision review", failures)
    _require("plannerSuggestions" in prompt, "prompt_missing_planner_suggestions", "automation prompt must mention plannerSuggestions review boundary", failures)
    _require("completionTemplate" in prompt, "prompt_missing_completion_template", "automation prompt must reference completionTemplate", failures)
    _require("writeback.cli" in prompt, "prompt_missing_writeback_cli", "automation prompt must require execution_packet.writeback.cli", failures)
    _require("writeback.argvTemplate" in prompt, "prompt_missing_writeback_argv_template", "automation prompt must prefer execution_packet.writeback.argvTemplate", failures)
    _require("--max-*" in prompt, "prompt_missing_budget_warning", "automation prompt must preserve --max-* writeback guards", failures)
    _require("current_fingerprint" in prompt, "prompt_missing_current_fingerprint", "automation prompt must inspect writeback current_fingerprint", failures)
    _require("回写 JSON" in prompt, "prompt_missing_writeback_json_check", "automation prompt must require checking writeback JSON", failures)
    _require("采集脚本进入 Inbox" in prompt, "prompt_missing_capture_execution_boundary", "automation prompt must keep capture and execution decoupled", failures)
    _require("影响下一次规划检查" in prompt, "prompt_missing_next_plan_boundary", "automation prompt must defer new captures to the next planning check", failures)

    _require(isinstance(audit.get("summary"), dict), "audit_missing_summary", "audit must include summary", failures)
    _require(isinstance(directive.get("completionTemplate"), dict), "directive_missing_completion_template", "directive must include completionTemplate", failures)
    _require("action" in directive, "directive_missing_action", "directive must include action", failures)
    _require("requiredChecks" in directive, "directive_missing_required_checks", "directive must include requiredChecks", failures)

    if packet.get("hasTask"):
        task = packet.get("task") if isinstance(packet.get("task"), dict) else {}
        budget = packet.get("budget") if isinstance(packet.get("budget"), dict) else {}
        snapshot = packet.get("snapshot") if isinstance(packet.get("snapshot"), dict) else {}
        writeback = packet.get("writeback") if isinstance(packet.get("writeback"), dict) else {}
        planner_suggestions = packet.get("plannerSuggestions")
        planning_decision = packet.get("planningDecision") if isinstance(packet.get("planningDecision"), dict) else {}
        cli = str(writeback.get("cli") or "")
        argv_template = writeback.get("argvTemplate") if isinstance(writeback.get("argvTemplate"), list) else []
        argv_values = [str(item) for item in argv_template]

        _require(bool(task.get("id")), "packet_missing_task_id", "execution packet task must include id", failures)
        _require(bool(planning_decision), "packet_missing_planning_decision", "execution packet must include planningDecision", failures)
        _require(
            planning_decision.get("selectedTaskId") == task.get("id"),
            "packet_planning_decision_task_mismatch",
            "planningDecision selectedTaskId must match packet task id",
            failures,
        )
        _require(bool(snapshot.get("packetId")), "packet_missing_packet_id", "execution packet snapshot must include packetId", failures)
        _require(snapshot.get("taskId") == task.get("id"), "packet_snapshot_task_mismatch", "snapshot taskId must match packet task id", failures)
        _require(bool(writeback.get("taskId")), "writeback_missing_task_id", "writeback must include taskId", failures)
        _require(isinstance(planner_suggestions, list), "packet_missing_planner_suggestions", "execution packet must include plannerSuggestions list", failures)
        _require(f"--task-id {task.get('id')}" in cli, "cli_missing_task_id", "writeback cli must target task id", failures)
        _require("--packet-id " in cli, "cli_missing_packet_id", "writeback cli must include packet id", failures)
        _require("--expected-task-updated-at " in cli, "cli_missing_snapshot_guard", "writeback cli must include snapshot guard", failures)
        _require("--summary <本轮摘要>" in cli, "cli_missing_summary_placeholder", "writeback cli must include summary placeholder", failures)
        _require("--verification " in cli, "cli_missing_verification", "writeback cli must include verification field", failures)
        _require("--remaining-risk " in cli, "cli_missing_remaining_risk", "writeback cli must include remaining risk field", failures)
        _require("--next-step " in cli, "cli_missing_next_step", "writeback cli must include next step field", failures)
        _require("--json" in cli, "cli_missing_json", "writeback cli must request JSON output", failures)
        _require(bool(argv_values), "writeback_missing_argv_template", "writeback must include argvTemplate", failures)
        for expected in (
            "uv",
            "run",
            "python",
            "scripts/ai_task_space_append_execution_record.py",
            "--task-id",
            str(task.get("id")),
            "--packet-id",
            str(snapshot.get("packetId")),
            "--expected-task-updated-at",
            "--summary",
            "--verification",
            "--remaining-risk",
            "--next-step",
            "--json",
        ):
            _require(expected in argv_values, f"argv_missing_{expected}", f"argvTemplate must include {expected}", failures)

        for key, flag in (
            ("maxSteps", "--max-steps"),
            ("maxCommands", "--max-commands"),
            ("maxFilesChanged", "--max-files-changed"),
        ):
            _require(key in budget, f"budget_missing_{key}", f"budget must include {key}", failures)
            _require(f"{flag} {budget.get(key)}" in cli, f"cli_missing_{flag}", f"writeback cli must carry {flag}", failures)
            _require(flag in argv_values, f"argv_missing_{flag}", f"argvTemplate must include {flag}", failures)
            _require(str(budget.get(key)) in argv_values, f"argv_missing_{flag}_value", f"argvTemplate must carry {flag} value", failures)

        if directive.get("shouldWriteBack"):
            _require(bool(writeback), "directive_writeback_without_cli", "directive requires writeback but packet has no writeback cli", failures)
            _require(directive.get("writebackStatus") in {"progress", "done", "blocked"}, "directive_invalid_writeback_status", "writebackStatus must be a record status", failures)
    else:
        _require(directive.get("shouldExecute") is False, "skip_packet_should_not_execute", "empty packet must not execute", failures)
        _require(directive.get("shouldWriteBack") is False, "skip_packet_should_not_writeback", "empty packet must not require writeback", failures)

    completion = directive.get("completionTemplate") if isinstance(directive.get("completionTemplate"), dict) else {}
    writeback_template = completion.get("writeback") if isinstance(completion.get("writeback"), dict) else {}
    final_report = completion.get("finalReport") if isinstance(completion.get("finalReport"), list) else []
    notes = completion.get("notes") if isinstance(completion.get("notes"), list) else []
    final_report_text = "\n".join(str(item) for item in final_report)
    notes_text = "\n".join(str(item) for item in notes)
    _require("current_fingerprint" in final_report_text, "completion_missing_current_fingerprint", "completionTemplate.finalReport must include current_fingerprint", failures)
    _require("回写 JSON" in notes_text, "completion_missing_writeback_json_note", "completionTemplate.notes must require checking writeback JSON", failures)
    for key in ("summary", "verification", "remainingRisk", "nextStep"):
        _require(key in writeback_template, f"completion_missing_{key}", f"completionTemplate.writeback must include {key}", failures)

    return {
        "ok": not failures,
        "failures": failures,
        "audit": audit,
        "selectedTaskId": selected_task_id,
        "execution_packet": packet,
        "automation_directive": directive,
    }
