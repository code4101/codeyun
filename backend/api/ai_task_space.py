from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.core.access.auth import get_current_active_user
from backend.core.ai_task_space import (
    CAPTURE_CONTEXT_KINDS,
    ExecutionPacketReplayConflict,
    ExecutionSnapshotMismatch,
    add_capture,
    append_execution_record,
    apply_planner_suggestion,
    apply_task_review_action,
    audit_task_space,
    build_automation_directive,
    build_automation_prompt,
    dismiss_planner_suggestion,
    build_execution_packet,
    confirm_task_user_ready,
    load_task_space,
    mutate_task_space,
    promote_capture,
    run_planner_check,
    save_capture_attachment_base64,
    task_space_fingerprint,
    task_space_with_fingerprint,
    user_task_space_path,
)
from backend.core.ai_task_space_automation import (
    default_automation_toml_path,
    validate_automation_toml,
    validate_contract,
)
from backend.core.settings import ROOT_DIR
from backend.models import User


router = APIRouter()


class TaskSpacePayload(BaseModel):
    task_space: dict[str, Any]
    expected_fingerprint: str = ""


class CaptureCreateRequest(BaseModel):
    raw_text: str
    source: str = "Codex 当前会话"
    tags: list[str] = []
    context_kind: str = "task"
    project_path: str = ""
    images: list[dict[str, Any]] = []


class ExecutionRecordRequest(BaseModel):
    summary: str
    verification: str = ""
    remaining_risk: str = ""
    next_step: str = ""
    status: str = "progress"
    packet_id: str = ""
    expected_task_updated_at: str = ""
    steps_done: int = 0
    commands_run: int = 0
    files_changed: int = 0


class ConfirmTaskRequest(BaseModel):
    note: str = ""
    expected_fingerprint: str = ""


class TaskReviewActionRequest(BaseModel):
    action: str
    expected_fingerprint: str = ""


class PlannerSuggestionActionRequest(BaseModel):
    action: str
    expected_fingerprint: str = ""


def _path_for_user(current_user: User):
    return user_task_space_path(current_user.id)


@router.get("")
def get_ai_task_space(current_user: User = Depends(get_current_active_user)):
    return task_space_with_fingerprint(load_task_space(_path_for_user(current_user)))


@router.put("")
def put_ai_task_space(
    payload: TaskSpacePayload,
    current_user: User = Depends(get_current_active_user),
):
    path = _path_for_user(current_user)
    expected_fingerprint = payload.expected_fingerprint or str(payload.task_space.get("_fingerprint") or "")

    def _save_if_current(current: dict[str, Any]) -> dict[str, Any]:
        if expected_fingerprint and expected_fingerprint != task_space_fingerprint(current):
            raise HTTPException(status_code=409, detail="任务空间已被其他采集或规划检查更新，请重新加载后再保存。")
        return payload.task_space

    return task_space_with_fingerprint(mutate_task_space(path, _save_if_current))


@router.post("/captures")
def create_capture(
    payload: CaptureCreateRequest,
    current_user: User = Depends(get_current_active_user),
):
    if not payload.raw_text.strip():
        raise HTTPException(status_code=400, detail="采集内容不能为空")
    if payload.context_kind not in CAPTURE_CONTEXT_KINDS:
        raise HTTPException(status_code=400, detail="采集类型非法")
    path = _path_for_user(current_user)
    attachments: list[dict[str, Any]] = []
    try:
        for image in payload.images[:12]:
            if not isinstance(image, dict):
                continue
            data_base64 = str(image.get("data_base64") or image.get("dataBase64") or "").strip()
            if not data_base64:
                continue
            attachments.append(
                save_capture_attachment_base64(
                    data_base64,
                    name=str(image.get("name") or ""),
                    mime_type=str(image.get("mime_type") or image.get("mimeType") or ""),
                )
            )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    def _append_capture(task_space: dict[str, Any]) -> dict[str, Any]:
        return add_capture(
            task_space,
            payload.raw_text,
            payload.source,
            tags=payload.tags,
            context_kind=payload.context_kind,
            project_path=payload.project_path,
            attachments=attachments,
        )

    return task_space_with_fingerprint(
        mutate_task_space(path, _append_capture)
    )


@router.post("/captures/{capture_id}/promote")
def promote_capture_route(
    capture_id: str,
    current_user: User = Depends(get_current_active_user),
):
    path = _path_for_user(current_user)
    return task_space_with_fingerprint(mutate_task_space(path, lambda task_space: promote_capture(task_space, capture_id)))


@router.post("/planner/run-once")
def run_planner_check_route(current_user: User = Depends(get_current_active_user)):
    path = _path_for_user(current_user)
    return task_space_with_fingerprint(mutate_task_space(path, run_planner_check))


@router.post("/planner/suggestions/{suggestion_id}")
def act_on_planner_suggestion_route(
    suggestion_id: str,
    payload: PlannerSuggestionActionRequest,
    current_user: User = Depends(get_current_active_user),
):
    path = _path_for_user(current_user)
    def _act_on_suggestion(task_space: dict[str, Any]) -> dict[str, Any]:
        if payload.expected_fingerprint and payload.expected_fingerprint != task_space_fingerprint(task_space):
            raise HTTPException(status_code=409, detail="任务空间已被其他采集或规划检查更新，请重新加载后再处理建议。")
        if payload.action == "apply":
            return apply_planner_suggestion(task_space, suggestion_id)
        elif payload.action == "dismiss":
            return dismiss_planner_suggestion(task_space, suggestion_id)
        raise HTTPException(status_code=400, detail="建议动作非法")

    try:
        return task_space_with_fingerprint(mutate_task_space(path, _act_on_suggestion))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="建议或任务不存在") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/tasks/{task_id}/review-action")
def apply_task_review_action_route(
    task_id: str,
    payload: TaskReviewActionRequest,
    current_user: User = Depends(get_current_active_user),
):
    path = _path_for_user(current_user)

    def _apply_review_action(task_space: dict[str, Any]) -> dict[str, Any]:
        if payload.expected_fingerprint and payload.expected_fingerprint != task_space_fingerprint(task_space):
            raise HTTPException(status_code=409, detail="任务空间已变化，请重新读取后再处理审核动作。")
        return apply_task_review_action(task_space, task_id, payload.action)

    try:
        return task_space_with_fingerprint(mutate_task_space(path, _apply_review_action))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="任务不存在") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/planner/execution-packet")
def get_execution_packet_route(
    task_id: str | None = None,
    current_user: User = Depends(get_current_active_user),
):
    task_space = load_task_space(_path_for_user(current_user))
    execution_packet = build_execution_packet(task_space, task_id, username=current_user.username)
    audit = audit_task_space(task_space)
    return {
        **execution_packet,
        "automationDirective": build_automation_directive(execution_packet, audit),
    }


@router.get("/audit")
def get_task_space_audit_route(current_user: User = Depends(get_current_active_user)):
    task_space = load_task_space(_path_for_user(current_user))
    return audit_task_space(task_space)


@router.get("/automation-health")
def get_automation_health_route(current_user: User = Depends(get_current_active_user)):
    task_space = load_task_space(_path_for_user(current_user))
    simulated_space = run_planner_check(task_space)
    current_fingerprint = task_space_fingerprint(task_space)
    validated_fingerprint = task_space_fingerprint(simulated_space)
    prompt = build_automation_prompt(current_user.username)
    sync_command = (
        "uv run python scripts/ai_task_space_sync_automation.py "
        f"--username {current_user.username} --json"
    )
    contract = validate_contract(simulated_space, username=current_user.username, prompt=prompt)
    automation = validate_automation_toml(
        default_automation_toml_path(),
        expected_prompt=prompt,
        expected_cwd=ROOT_DIR,
    )
    failures = [*contract["failures"], *automation["failures"]]
    directive = contract["automation_directive"]
    planning_decision = (
        contract["execution_packet"].get("planningDecision")
        if isinstance(contract.get("execution_packet"), dict)
        else {}
    )
    blockers = (
        planning_decision.get("skipped", [])
        if isinstance(planning_decision, dict) and isinstance(planning_decision.get("skipped"), list)
        else []
    )
    blocker_count = (
        planning_decision.get("skippedCount", len(blockers))
        if isinstance(planning_decision, dict)
        else len(blockers)
    )
    selected_task_id = contract["selectedTaskId"]
    selected_task = next((task for task in simulated_space.get("tasks", []) if task.get("id") == selected_task_id), None)
    saved_selected_task = next((task for task in task_space.get("tasks", []) if task.get("id") == selected_task_id), None)
    latest_saved_log = (task_space.get("plannerLogs") or [{}])[0]
    latest_execution_record = None
    if isinstance(saved_selected_task, dict):
        latest_execution_record = (saved_selected_task.get("executionRecords") or [None])[0]
    return {
        "ok": not failures,
        "mutated": False,
        "mode": "simulated_plan",
        "checkedAt": contract["audit"]["checkedAt"],
        "currentFingerprint": current_fingerprint,
        "validatedFingerprint": validated_fingerprint,
        "syncCommand": sync_command,
        "failures": failures,
        "contract": {
            "ok": contract["ok"],
            "selectedTaskId": contract["selectedTaskId"],
            "action": directive.get("action"),
            "shouldExecute": directive.get("shouldExecute"),
            "shouldModifyCode": directive.get("shouldModifyCode"),
            "shouldWriteBack": directive.get("shouldWriteBack"),
            "writebackStatus": directive.get("writebackStatus"),
            "stopReason": directive.get("stopReason"),
            "summaryHint": directive.get("summaryHint"),
            "requiredChecks": directive.get("requiredChecks") if isinstance(directive.get("requiredChecks"), list) else [],
            "blockerCount": blocker_count if isinstance(blocker_count, int) else len(blockers),
            "blockers": blockers[:5],
            "audit": contract["audit"]["summary"],
        },
        "recentRun": {
            "latestPlannerLog": latest_saved_log if isinstance(latest_saved_log, dict) and latest_saved_log else None,
            "selectedTask": (
                {
                    "id": selected_task.get("id"),
                    "title": selected_task.get("title"),
                    "status": selected_task.get("status"),
                    "updatedAt": selected_task.get("updatedAt"),
                }
                if isinstance(selected_task, dict)
                else None
            ),
            "latestExecutionRecord": latest_execution_record,
        },
        "automationToml": automation,
    }


@router.post("/tasks/{task_id}/execution-records")
def append_execution_record_route(
    task_id: str,
    payload: ExecutionRecordRequest,
    current_user: User = Depends(get_current_active_user),
):
    if not payload.summary.strip():
        raise HTTPException(status_code=400, detail="执行摘要不能为空")
    if payload.status not in {"progress", "done", "blocked"}:
        raise HTTPException(status_code=400, detail="执行状态非法")
    path = _path_for_user(current_user)
    def _append_record(task_space: dict[str, Any]) -> dict[str, Any]:
        return append_execution_record(
            task_space,
            task_id,
            summary=payload.summary,
            verification=payload.verification,
            remaining_risk=payload.remaining_risk,
            next_step=payload.next_step,
            status=payload.status,
            packet_id=payload.packet_id,
            expected_task_updated_at=payload.expected_task_updated_at,
            steps_done=payload.steps_done,
            commands_run=payload.commands_run,
            files_changed=payload.files_changed,
        )

    try:
        return task_space_with_fingerprint(mutate_task_space(path, _append_record))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="任务不存在") from exc
    except ExecutionPacketReplayConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ExecutionSnapshotMismatch as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/tasks/{task_id}/confirm-user-ready")
def confirm_task_user_ready_route(
    task_id: str,
    payload: ConfirmTaskRequest,
    current_user: User = Depends(get_current_active_user),
):
    path = _path_for_user(current_user)

    def _confirm_task_user_ready(task_space: dict):
        if payload.expected_fingerprint and payload.expected_fingerprint != task_space_fingerprint(task_space):
            raise HTTPException(status_code=409, detail="任务空间已变化，请重新读取后再确认。")
        return confirm_task_user_ready(task_space, task_id, note=payload.note)

    try:
        return task_space_with_fingerprint(mutate_task_space(path, _confirm_task_user_ready))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="任务不存在") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
