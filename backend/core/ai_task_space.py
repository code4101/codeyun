from __future__ import annotations

import html
import base64
import hashlib
import json
import mimetypes
import os
import re
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable

from backend.core.resources.storage import build_attachment_url, get_attachments_dir
from backend.core.settings import get_settings


TASK_SPACE_VERSION = 2
ACTIVE_TASK_STATUSES = {
    "ready",
    "done",
}
PLANNER_SUGGESTION_KINDS = {"split", "merge", "dependency", "document", "archive"}
CAPTURE_CONTEXT_KINDS = {"task", "context", "constraint", "preference", "knowledge"}
CAPTURE_ATTACHMENT_MIME_SUFFIX = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
}


def normalize_task_kind_value(value: Any) -> str:
    kind = str(value or "").strip()
    if kind in {"project", "goal", "context", "learning_case"}:
        return "project"
    return "task"


def normalize_task_status_value(value: Any) -> str:
    status = str(value or "").strip()
    if status in {"done", "review_for_archive"}:
        return "done"
    if status == "archived":
        return "archived"
    return "ready"


class ExecutionSnapshotMismatch(ValueError):
    pass


class ExecutionPacketReplayConflict(ValueError):
    pass


class TaskSpaceLockTimeout(TimeoutError):
    pass


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def task_space_dir() -> Path:
    path = get_settings().data_dir / "ai-task-space"
    path.mkdir(parents=True, exist_ok=True)
    return path


def user_task_space_path(user_id: int | str) -> Path:
    return task_space_dir() / f"user_{user_id}.json"


def empty_document() -> dict[str, str]:
    return {
        "goal": "",
        "currentState": "",
        "context": "",
        "knownFacts": "",
        "dependencies": "",
        "nextStep": "",
        "doneCriteria": "",
        "resultSummary": "",
    }


def normalize_document_text(value: Any) -> str:
    text = str(value or "")
    text = text.encode("utf-8", errors="replace").decode("utf-8")
    if not text.strip():
        return ""
    if not re.search(r"<[a-z][\s\S]*>", text, re.IGNORECASE):
        return text

    plain = text.strip()
    plain = re.sub(r"<br\s*/?>", "\n", plain, flags=re.IGNORECASE)
    plain = re.sub(r"</p>\s*<p[^>]*>", "\n", plain, flags=re.IGNORECASE)
    plain = re.sub(r"</?(p|div)[^>]*>", "", plain, flags=re.IGNORECASE)
    plain = re.sub(r"<[^>]+>", "", plain, flags=re.IGNORECASE)
    plain = html.unescape(plain).replace("\xa0", " ")
    plain = re.sub(r"[ \t]+\n", "\n", plain)
    plain = re.sub(r"\n{3,}", "\n\n", plain)
    return plain.strip()


def normalize_document(document_payload: dict[str, Any] | None) -> dict[str, str]:
    document = empty_document()
    if isinstance(document_payload, dict):
        document.update(
            {
                key: normalize_document_text(value)
                for key, value in document_payload.items()
                if key in document
            }
        )
    return document


def normalize_execution_record(item: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    summary = str(item.get("summary") or "").strip()
    if not summary:
        return None
    status = item.get("status")
    if status not in {"progress", "done", "blocked"}:
        status = "progress"
    verification = str(item.get("verification") or "").strip()
    remaining_risk = str(item.get("remainingRisk") or "").strip()
    next_step = str(item.get("nextStep") or "").strip()
    budget_used = item.get("budgetUsed") if isinstance(item.get("budgetUsed"), dict) else {}
    return {
        "id": str(item.get("id") or new_id("exec")),
        "recordedAt": str(item.get("recordedAt") or now_iso()),
        "summary": summary,
        "verification": verification,
        "remainingRisk": remaining_risk,
        "nextStep": next_step,
        "status": status,
        "packetId": str(item.get("packetId") or ""),
        "budgetUsed": {
            "stepsDone": max(0, int(budget_used.get("stepsDone") or 0)),
            "commandsRun": max(0, int(budget_used.get("commandsRun") or 0)),
            "filesChanged": max(0, int(budget_used.get("filesChanged") or 0)),
        },
    }


def _same_execution_packet_payload(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return all(
        left.get(key) == right.get(key)
        for key in ("summary", "verification", "remainingRisk", "nextStep", "status", "packetId", "budgetUsed")
    )


def normalize_capture_tags(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    tags: list[str] = []
    for item in value:
        tag = str(item or "").strip()
        if tag and tag not in tags:
            tags.append(tag[:40])
    return tags[:12]


def normalize_capture_context_kind(value: Any) -> str:
    kind = str(value or "task").strip().lower()
    return kind if kind in CAPTURE_CONTEXT_KINDS else "task"


def normalize_capture_attachments(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    attachments: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        filename = str(item.get("filename") or "").strip()
        attachment_id = str(item.get("id") or "").strip() or (filename or url)
        if not attachment_id or attachment_id in seen:
            continue
        seen.add(attachment_id)
        mime_type = str(item.get("mimeType") or item.get("mime_type") or "").strip()
        attachments.append(
            {
                "id": attachment_id[:80],
                "name": str(item.get("name") or filename or attachment_id).strip()[:160],
                "mimeType": mime_type[:80],
                "filename": filename[:180],
                "url": url[:260],
                "size": max(0, int(item.get("size") or 0)),
                "sha256": str(item.get("sha256") or "").strip()[:64],
            }
        )
    return attachments[:12]


def merge_capture_attachments(*groups: Any) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for group in groups:
        for attachment in normalize_capture_attachments(group):
            key = attachment.get("sha256") or attachment.get("url") or attachment.get("id")
            if not key or key in seen:
                continue
            seen.add(str(key))
            merged.append(attachment)
    return merged[:12]


def _attachment_suffix(name: str, mime_type: str) -> str:
    normalized_mime = mime_type.strip().lower()
    if normalized_mime in CAPTURE_ATTACHMENT_MIME_SUFFIX:
        return CAPTURE_ATTACHMENT_MIME_SUFFIX[normalized_mime]
    guessed = mimetypes.guess_extension(normalized_mime)
    if guessed:
        return guessed
    source_suffix = Path(name or "").suffix.lower()
    if re.fullmatch(r"\.[a-z0-9]{1,8}", source_suffix):
        return source_suffix
    return ".bin"


def save_capture_attachment_bytes(
    data: bytes,
    *,
    name: str = "",
    mime_type: str = "",
    prefix: str = "ai-task-capture",
) -> dict[str, Any]:
    if not data:
        raise ValueError("附件内容不能为空")
    resolved_mime = (mime_type or mimetypes.guess_type(name)[0] or "application/octet-stream").strip()
    suffix = _attachment_suffix(name, resolved_mime)
    digest = hashlib.sha256(data).hexdigest()
    filename = f"{prefix}-{digest[:16]}{suffix}"
    path = get_attachments_dir() / filename
    if not path.exists():
        path.write_bytes(data)
    return {
        "id": f"att_{digest[:16]}",
        "name": str(name or filename).strip()[:160],
        "mimeType": resolved_mime,
        "filename": filename,
        "url": build_attachment_url(filename),
        "size": len(data),
        "sha256": digest,
    }


def save_capture_attachment_base64(
    data_base64: str,
    *,
    name: str = "",
    mime_type: str = "",
) -> dict[str, Any]:
    value = str(data_base64 or "").strip()
    if "," in value and value.split(",", 1)[0].lower().startswith("data:"):
        header, value = value.split(",", 1)
        if not mime_type:
            mime_type = header[5:].split(";", 1)[0]
    try:
        data = base64.b64decode("".join(value.split()), validate=True)
    except ValueError as exc:
        raise ValueError("图片附件必须是 base64 内容") from exc
    return save_capture_attachment_bytes(data, name=name, mime_type=mime_type)


def save_capture_attachment_file(path: str | Path) -> dict[str, Any]:
    source = Path(path).resolve(strict=False)
    if not source.exists() or not source.is_file():
        raise FileNotFoundError(os.fspath(source))
    return save_capture_attachment_bytes(
        source.read_bytes(),
        name=source.name,
        mime_type=mimetypes.guess_type(source.name)[0] or "application/octet-stream",
    )


def stable_suggestion_id(*parts: Any) -> str:
    payload = json.dumps([str(part or "") for part in parts], ensure_ascii=False, separators=(",", ":"))
    return f"sug_{hashlib.sha1(payload.encode('utf-8')).hexdigest()[:12]}"


def normalize_planner_suggestion(item: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    kind = item.get("kind")
    if kind not in PLANNER_SUGGESTION_KINDS:
        return None
    title = str(item.get("title") or "").strip()
    rationale = str(item.get("rationale") or "").strip()
    proposed_action = str(item.get("proposedAction") or "").strip()
    if not title or not rationale or not proposed_action:
        return None
    severity = item.get("severity")
    if severity not in {"info", "warning"}:
        severity = "info"
    status = item.get("status")
    if status not in {"open", "dismissed", "applied"}:
        status = "open"
    related_task_ids = [str(value) for value in item.get("relatedTaskIds", []) if isinstance(value, str)]
    task_id = item.get("taskId")
    preview = item.get("preview")
    resolved_at = str(item.get("resolvedAt") or "").strip()
    return {
        "id": str(item.get("id") or stable_suggestion_id(kind, task_id, ",".join(related_task_ids), title)),
        "kind": kind,
        "severity": severity,
        **({"taskId": task_id} if isinstance(task_id, str) else {}),
        "relatedTaskIds": related_task_ids,
        "title": title,
        "rationale": rationale,
        "proposedAction": proposed_action,
        **({"preview": preview} if isinstance(preview, dict) else {}),
        "status": status,
        "createdAt": str(item.get("createdAt") or now_iso()),
        **({"resolvedAt": resolved_at} if status in {"dismissed", "applied"} and resolved_at else {}),
    }


def seed_task_space() -> dict[str, Any]:
    timestamp = now_iso()
    root_id = new_id("task")
    planner_id = new_id("task")
    return {
        "version": TASK_SPACE_VERSION,
        "captures": [
            {
                "id": new_id("cap"),
                "rawText": "建立任务采集缓存 skill：聊天中只记录任务和上下文，不立即执行。",
                "source": "设计种子",
                "capturedAt": timestamp,
                "status": "triaged",
                "linkedTaskId": root_id,
            },
            {
                "id": new_id("cap"),
                "rawText": "规划检查需要每轮重新整理任务空间，分析依赖，优先推进前置任务，必要时拆分和合并任务。",
                "source": "设计种子",
                "capturedAt": timestamp,
                "status": "triaged",
                "linkedTaskId": planner_id,
            },
        ],
        "tasks": [
            {
                "id": root_id,
                "title": "建立 AI 任务采集与执行体系",
                "kind": "project",
                "status": "ready",
                "parentId": None,
                "sortOrder": 0,
                "executionPolicy": "auto_safe",
                "risk": "low",
                "dependsOn": [],
                "relatedTaskIds": [planner_id],
                "suggestedSkill": "任务采集缓存",
                "document": {
                    "goal": "把聊天中产生的想法先进入任务空间，再由周期规划器选择待运行任务执行。",
                    "currentState": "已形成采集流、任务树、规划检查、执行回写的 v1 架构。",
                    "context": "采集和执行解耦；执行完成后必须重新读取整个任务空间。",
                    "knownFacts": "任务正文应是当前棋局式状态文档；原始对话和执行记录放在证据层。",
                    "dependencies": "需要先验证任务空间模型，再接入真实 Codex 自动化。",
                    "nextStep": "接入后端任务空间 API，让页面和自动化共享事实源。",
                    "doneCriteria": "采集、规划、执行回写都能围绕同一份任务空间工作。",
                    "resultSummary": "",
                },
                "evidenceLog": [f"{timestamp} 从讨论中创建目标节点。"],
                "createdAt": timestamp,
                "updatedAt": timestamp,
            },
            {
                "id": planner_id,
                "title": "实现规划检查重规划器",
                "kind": "task",
                "status": "ready",
                "parentId": root_id,
                "sortOrder": 0,
                "executionPolicy": "auto_safe",
                "risk": "low",
                "dependsOn": [],
                "relatedTaskIds": [root_id],
                "suggestedSkill": "任务采集缓存",
                "document": {
                    "goal": "每次规划检查重新读取任务空间，整理 Inbox、依赖和下一步，而不是随便挑一个任务。",
                    "currentState": "后端先提供确定性规划检查，后续再接入 Codex automation。",
                    "context": "真实自动化应复用同一套任务空间数据结构。",
                    "knownFacts": "采集是流式的；规划是周期性的；执行是事务性的；任务空间是事实源。",
                    "dependencies": "依赖任务节点状态字段和证据日志。",
                    "nextStep": "用启发式规则整理待采集输入，选择待运行任务直接推进。",
                    "doneCriteria": "每次规划检查能写入规划日志，并更新任务状态或创建新任务。",
                    "resultSummary": "",
                },
                "evidenceLog": [f"{timestamp} 从体系目标拆出前置任务。"],
                "createdAt": timestamp,
                "updatedAt": timestamp,
            },
        ],
        "plannerLogs": [],
        "plannerSuggestions": [],
    }


def normalize_task_space(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return seed_task_space()

    captures = payload.get("captures")
    tasks = payload.get("tasks")
    planner_logs = payload.get("plannerLogs")
    planner_suggestions = payload.get("plannerSuggestions")
    if not isinstance(captures, list) or not isinstance(tasks, list):
        return seed_task_space()

    normalized_tasks: list[dict[str, Any]] = []
    for item in tasks:
        if not isinstance(item, dict):
            continue
        task_id = str(item.get("id") or "").strip()
        title = str(item.get("title") or "").strip()
        if not task_id or not title:
            continue
        document = normalize_document(item.get("document") if isinstance(item.get("document"), dict) else None)
        normalized_tasks.append(
            {
                **item,
                "id": task_id,
                "title": title,
                "kind": normalize_task_kind_value(item.get("kind")),
                "status": normalize_task_status_value(item.get("status")),
                "parentId": item.get("parentId") if isinstance(item.get("parentId"), str) else None,
                "sortOrder": int(item.get("sortOrder") or 0),
                "executionPolicy": item.get("executionPolicy") if item.get("executionPolicy") in {"manual_only", "ask_before_execute", "auto_report", "auto_safe"} else "auto_safe",
                "risk": item.get("risk") if item.get("risk") in {"low", "medium", "high"} else "low",
                "dependsOn": [str(value) for value in item.get("dependsOn", []) if isinstance(value, str)],
                "relatedTaskIds": [str(value) for value in item.get("relatedTaskIds", []) if isinstance(value, str)],
                "suggestedSkill": str(item.get("suggestedSkill") or ""),
                "document": document,
                "attachments": normalize_capture_attachments(item.get("attachments")),
                "evidenceLog": [str(value) for value in item.get("evidenceLog", []) if isinstance(value, str)][:50],
                "executionRecords": [
                    record
                    for record in (normalize_execution_record(value) for value in item.get("executionRecords", []))
                    if record is not None
                ][:50],
                "createdAt": str(item.get("createdAt") or now_iso()),
                "updatedAt": str(item.get("updatedAt") or now_iso()),
            }
        )

    normalized_captures: list[dict[str, Any]] = []
    task_ids = {task["id"] for task in normalized_tasks}
    for item in captures:
        if not isinstance(item, dict):
            continue
        raw_text = str(item.get("rawText") or "").encode("utf-8", errors="replace").decode("utf-8").strip()
        if not raw_text:
            continue
        linked_task_id = item.get("linkedTaskId")
        normalized_captures.append(
            {
                "id": str(item.get("id") or new_id("cap")),
                "rawText": raw_text,
                "source": str(item.get("source") or "Codex 当前会话"),
                "capturedAt": str(item.get("capturedAt") or now_iso()),
                "status": item.get("status") if item.get("status") in {"inbox", "triaged", "discarded"} else "inbox",
                "tags": normalize_capture_tags(item.get("tags")),
                "contextKind": normalize_capture_context_kind(item.get("contextKind")),
                "projectPath": str(item.get("projectPath") or "").strip(),
                "attachments": normalize_capture_attachments(item.get("attachments")),
                **({"linkedTaskId": linked_task_id} if isinstance(linked_task_id, str) and linked_task_id in task_ids else {}),
            }
        )

    return {
        "version": TASK_SPACE_VERSION,
        "captures": normalized_captures,
        "tasks": normalized_tasks,
        "plannerLogs": [item for item in planner_logs if isinstance(item, dict)][:50] if isinstance(planner_logs, list) else [],
        "plannerSuggestions": [
            suggestion
            for suggestion in (normalize_planner_suggestion(value) for value in planner_suggestions)
            if suggestion is not None
        ][:50]
        if isinstance(planner_suggestions, list)
        else [],
    }


def load_task_space(path: Path) -> dict[str, Any]:
    if not path.exists():
        space = seed_task_space()
        save_task_space(path, space)
        return space
    try:
        return normalize_task_space(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        return seed_task_space()


def save_task_space(path: Path, task_space: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_task_space(task_space)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    temp_path.write_text(
        json.dumps(normalized, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp_path.replace(path)
    return normalized


def _task_space_lock_path(path: Path) -> Path:
    return path.with_name(f"{path.name}.lock")


@contextmanager
def task_space_file_lock(
    path: Path,
    *,
    timeout_seconds: float = 10.0,
    poll_seconds: float = 0.05,
    stale_seconds: float = 120.0,
):
    lock_path = _task_space_lock_path(path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout_seconds
    fd: int | None = None
    while fd is None:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                if time.time() - lock_path.stat().st_mtime > stale_seconds:
                    lock_path.unlink()
                    continue
            except FileNotFoundError:
                continue
            if time.monotonic() >= deadline:
                raise TaskSpaceLockTimeout(f"任务空间正在被其他进程写入，等待超时：{path}")
            time.sleep(poll_seconds)
    try:
        os.write(fd, f"{os.getpid()} {now_iso()}\n".encode("utf-8", errors="replace"))
        yield
    finally:
        os.close(fd)
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


def mutate_task_space(
    path: Path,
    updater: Callable[[dict[str, Any]], dict[str, Any]],
    *,
    timeout_seconds: float = 10.0,
) -> dict[str, Any]:
    with task_space_file_lock(path, timeout_seconds=timeout_seconds):
        current = load_task_space(path)
        return save_task_space(path, updater(current))


def task_space_fingerprint(task_space: dict[str, Any]) -> str:
    normalized = normalize_task_space(task_space)
    payload = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def task_space_with_fingerprint(task_space: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_task_space(task_space)
    return {**normalized, "_fingerprint": task_space_fingerprint(normalized)}


def add_capture(
    task_space: dict[str, Any],
    raw_text: str,
    source: str = "Codex 当前会话",
    *,
    tags: list[str] | None = None,
    context_kind: str = "task",
    project_path: str = "",
    attachments: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    normalized = normalize_task_space(task_space)
    text = str(raw_text or "").encode("utf-8", errors="replace").decode("utf-8").strip()
    if not text:
        return normalized
    context_kind = normalize_capture_context_kind(context_kind)
    project_path = str(project_path or "").strip()
    normalized["captures"].insert(
        0,
        {
            "id": new_id("cap"),
            "rawText": text,
            "source": source.strip() or "Codex 当前会话",
            "capturedAt": now_iso(),
            "status": "inbox",
            "tags": normalize_capture_tags(tags or []),
            "contextKind": context_kind,
            "projectPath": project_path,
            "attachments": normalize_capture_attachments(attachments or []),
        },
    )
    return normalized


def _title_from_text(text: str) -> str:
    title = text.replace("\r", "\n").split("\n", 1)[0]
    for separator in ("。", "；", ";"):
        title = title.split(separator, 1)[0]
    return title.strip()[:42] or "待整理任务"


def _capture_profile(context_kind: str) -> dict[str, str]:
    kind = str(context_kind or "task").strip().lower()
    profiles = {
        "task": {
            "task_kind": "task",
            "execution_policy": "auto_safe",
            "risk": "low",
            "current_state": "从采集流水进入任务空间，等待规划器进一步整理。",
            "next_step": "澄清任务边界，判断是否需要拆分、合并或建立前置依赖。",
        },
        "context": {
            "task_kind": "project",
            "execution_policy": "auto_safe",
            "risk": "low",
            "current_state": "作为任务空间上下文沉淀，不进入自动执行候选。",
            "next_step": "在相关任务规划或执行时作为参考材料调度。",
        },
        "constraint": {
            "task_kind": "task",
            "execution_policy": "auto_safe",
            "risk": "low",
            "current_state": "作为约束或决策条件沉淀，等待绑定到相关任务。",
            "next_step": "由规划检查识别受影响任务，并在任务文档或依赖关系中引用该约束。",
        },
        "preference": {
            "task_kind": "project",
            "execution_policy": "auto_safe",
            "risk": "low",
            "current_state": "作为用户偏好沉淀，不作为独立执行任务。",
            "next_step": "后续规划和界面调整时优先遵循该偏好。",
        },
        "knowledge": {
            "task_kind": "project",
            "execution_policy": "auto_safe",
            "risk": "low",
            "current_state": "作为知识沉淀案例保存，不进入自动执行候选。",
            "next_step": "在相似任务出现时作为经验材料引用。",
        },
    }
    return profiles.get(kind, profiles["task"])


def _capture_known_facts(capture: dict[str, Any]) -> str:
    return ""

def promote_capture(task_space: dict[str, Any], capture_id: str) -> dict[str, Any]:
    normalized = normalize_task_space(task_space)
    capture = next((item for item in normalized["captures"] if item["id"] == capture_id), None)
    if capture is None or capture.get("status") != "inbox":
        return normalized
    timestamp = now_iso()
    task_id = new_id("task")
    title = _title_from_text(capture["rawText"])
    profile = _capture_profile(capture.get("contextKind", "task"))
    normalized["tasks"].append(
        {
            "id": task_id,
            "title": title,
            "kind": profile["task_kind"],
            "status": "ready",
            "parentId": None,
            "sortOrder": len([item for item in normalized["tasks"] if item.get("parentId") is None]),
            "executionPolicy": profile["execution_policy"],
            "risk": profile["risk"],
            "dependsOn": [],
            "relatedTaskIds": [],
            "suggestedSkill": "",
            "attachments": normalize_capture_attachments(capture.get("attachments")),
            "document": {
                "goal": title,
                "currentState": profile["current_state"],
                "context": capture["rawText"],
                "knownFacts": _capture_known_facts(capture),
                "dependencies": "",
                "nextStep": profile["next_step"],
                "doneCriteria": "",
                "resultSummary": "",
            },
            "evidenceLog": [f"{timestamp} 从采集项 {capture_id} 创建。"],
            "createdAt": timestamp,
            "updatedAt": timestamp,
        }
    )
    capture["status"] = "triaged"
    capture["linkedTaskId"] = task_id
    return normalized


def _dependency_is_done(tasks_by_id: dict[str, dict[str, Any]], task_id: str) -> bool:
    return tasks_by_id.get(task_id, {}).get("status") == "done"


def _dependency_summary(task: dict[str, Any], tasks_by_id: dict[str, dict[str, Any]]) -> tuple[list[str], list[str]]:
    missing: list[str] = []
    pending: list[str] = []
    for dependency_id in task.get("dependsOn", []):
        dependency = tasks_by_id.get(dependency_id)
        if dependency is None:
            missing.append(dependency_id)
        elif dependency.get("status") != "done":
            pending.append(dependency.get("title") or dependency_id)
    return missing, pending


def _dependency_block_text(missing: list[str], pending: list[str]) -> str:
    parts: list[str] = []
    if pending:
        parts.append(f"等待前置任务完成：{'、'.join(pending)}")
    if missing:
        parts.append(f"依赖任务不存在：{'、'.join(missing)}")
    return "；".join(parts)


PLANNER_STATUS_SCORE = {
    "ready": 0,
    "planned": 0,
    "inbox": 2,
    "running": 0,
    "blocked": 0,
    "done": 9,
    "review_for_archive": 9,
    "archived": 9,
}
PLANNER_POLICY_SCORE = {
    "auto_safe": 0,
    "auto_report": 0,
    "ask_before_execute": 0,
    "manual_only": 0,
}
PLANNER_RISK_SCORE = {
    "low": 0,
    "medium": 0,
    "high": 0,
}
PLANNER_KIND_SCORE = {
    "task": 0,
    "project": 5,
}


def _planner_candidate_rank(task: dict[str, Any], child_parent_ids: set[Any]) -> tuple[Any, ...]:
    return (
        3 if task["id"] in child_parent_ids else 0,
        PLANNER_POLICY_SCORE.get(task.get("executionPolicy"), 9),
        PLANNER_RISK_SCORE.get(task.get("risk"), 9),
        PLANNER_STATUS_SCORE.get(task.get("status"), 9),
        PLANNER_KIND_SCORE.get(task.get("kind"), 9),
        len(task.get("dependsOn", [])),
        task.get("createdAt", ""),
    )


def _planner_candidate_reason(task: dict[str, Any], child_parent_ids: set[Any]) -> str:
    parts = [
        "未完成",
        f"类型 {task.get('kind')}",
    ]
    if task["id"] in child_parent_ids:
        parts.append("已有子任务，父节点降级为容器")
    if task.get("dependsOn"):
        parts.append(f"前置依赖已满足 {len(task.get('dependsOn', []))} 项")
    else:
        parts.append("无未完成前置依赖")
    return "；".join(parts)


def _planner_skip_reasons(task: dict[str, Any], tasks_by_id: dict[str, dict[str, Any]]) -> list[str]:
    reasons: list[str] = []
    status = task.get("status")
    if status == "archived":
        reasons.append("已归档，不进入直接规划")
    elif status == "done":
        reasons.append("已完成")
    if task.get("kind") == "project":
        reasons.append("项目节点只作为分组，不直接运行")
    active_children = [
        child.get("title", child.get("id", ""))
        for child in tasks_by_id.values()
        if child.get("parentId") == task.get("id")
        and child.get("status") not in {"done", "archived"}
    ]
    if active_children:
        reasons.append(f"存在未完成子任务：{'、'.join(str(title) for title in active_children[:3])}")
    missing_dependencies, pending_dependencies = _dependency_summary(task, tasks_by_id)
    if missing_dependencies:
        reasons.append(f"缺失依赖：{'、'.join(missing_dependencies)}")
    if pending_dependencies:
        reasons.append(f"等待依赖完成：{'、'.join(pending_dependencies)}")
    return reasons


def _execution_blocking_reasons(task: dict[str, Any], tasks_by_id: dict[str, dict[str, Any]]) -> list[str]:
    confirmation_reasons = {
        "执行策略为仅手动",
        "最近执行记录已等待用户确认",
        "高风险，需要用户确认",
    }
    return [
        reason
        for reason in _planner_skip_reasons(task, tasks_by_id)
        if reason not in confirmation_reasons
    ]


def build_planner_decision(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    tasks_by_id = {task["id"]: task for task in tasks}
    child_parent_ids = {task.get("parentId") for task in tasks if isinstance(task.get("parentId"), str)}
    candidates: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for task in tasks:
        skip_reasons = _planner_skip_reasons(task, tasks_by_id)
        if skip_reasons:
            if task.get("status") != "archived":
                skipped.append(
                    {
                        "taskId": task["id"],
                        "title": task.get("title", task["id"]),
                        "reasons": skip_reasons[:4],
                    }
                )
            continue

        rank = _planner_candidate_rank(task, child_parent_ids)
        candidates.append(
            {
                "taskId": task["id"],
                "title": task.get("title", task["id"]),
                "status": task.get("status"),
                "executionPolicy": task.get("executionPolicy"),
                "risk": task.get("risk"),
                "kind": task.get("kind"),
                "rank": [value for value in rank if isinstance(value, int)],
                "reason": _planner_candidate_reason(task, child_parent_ids),
                "_rank": rank,
            }
        )

    candidates.sort(key=lambda item: item["_rank"])
    selected = candidates[0] if candidates else None
    visible_candidates = [{key: value for key, value in item.items() if key != "_rank"} for item in candidates[:5]]
    visible_skipped = skipped[:8]
    selected_reason = (
        f"优先选择「{selected['title']}」：{selected['reason']}"
        if selected
        else "当前没有待运行候选。"
    )
    return {
        "selectedTaskId": selected["taskId"] if selected else None,
        "selectedReason": selected_reason,
        "candidateCount": len(candidates),
        "skippedCount": len(skipped),
        "candidates": visible_candidates,
        "skipped": visible_skipped,
    }


def _find_planner_candidate(tasks: list[dict[str, Any]]) -> dict[str, Any] | None:
    decision = build_planner_decision(tasks)
    selected_task_id = decision.get("selectedTaskId")
    return next((task for task in tasks if task["id"] == selected_task_id), None)


def _latest_selected_task_id(task_space: dict[str, Any]) -> str | None:
    latest_log = (task_space.get("plannerLogs") or [{}])[0]
    selected_task_id = latest_log.get("selectedTaskId")
    return selected_task_id if isinstance(selected_task_id, str) else None


def _compact_task_evidence(task: dict[str, Any], marker: str) -> bool:
    evidence_log = task.get("evidenceLog")
    if not isinstance(evidence_log, list):
        return False

    marker_seen = False
    compacted: list[str] = []
    changed = False
    for line in evidence_log:
        if not isinstance(line, str):
            changed = True
            continue
        if marker in line:
            if marker_seen:
                changed = True
                continue
            marker_seen = True
        compacted.append(line)

    if changed:
        task["evidenceLog"] = compacted[:50]
    return changed


def audit_task_space(task_space: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_task_space(task_space)
    tasks = normalized["tasks"]
    task_ids = {task["id"] for task in tasks}
    active_tasks = [task for task in tasks if task.get("status") != "archived"]
    running_tasks: list[dict[str, Any]] = []
    latest_selected_id = _latest_selected_task_id(normalized)
    latest_selected_task = (
        next((task for task in tasks if task["id"] == latest_selected_id), None)
        if latest_selected_id
        else None
    )

    issues: list[dict[str, Any]] = []

    def add_issue(code: str, severity: str, message: str, task_id: str | None = None) -> None:
        issues.append(
            {
                "code": code,
                "severity": severity,
                "message": message,
                **({"taskId": task_id} if task_id else {}),
            }
        )

    if latest_selected_id and latest_selected_task is None:
        add_issue("selected_task_missing", "error", "最新规划日志选中的任务已不存在。")
    elif latest_selected_task and latest_selected_task.get("status") == "archived":
        add_issue(
            "selected_task_archived",
            "error",
            "最新规划日志选中了已归档任务。",
            latest_selected_task["id"],
        )
    elif latest_selected_task and latest_selected_task.get("status") == "done":
        add_issue(
            "selected_task_closed",
            "warning",
            "最新规划日志选中了已完成任务，本轮执行包应跳过。",
            latest_selected_task["id"],
        )

    for task in active_tasks:
        for dependency_id in task.get("dependsOn", []):
            dependency = next((item for item in tasks if item["id"] == dependency_id), None)
            if dependency is None:
                add_issue(
                    "dependency_missing",
                    "error",
                    f"任务「{task['title']}」依赖的任务不存在。",
                    task["id"],
                )
            elif dependency.get("status") == "archived":
                add_issue(
                    "dependency_archived",
                    "warning",
                    f"任务「{task['title']}」依赖的任务已归档，需要确认是否仍应保留该依赖。",
                    task["id"],
                )

        parent_id = task.get("parentId")
        if isinstance(parent_id, str) and parent_id not in task_ids:
            add_issue(
                "parent_missing",
                "warning",
                f"任务「{task['title']}」的父任务不存在，页面会临时提升到一级显示。",
                task["id"],
            )

        document = task.get("document", {})
        if task.get("status") == "done" and not document.get("resultSummary"):
            add_issue(
                "done_without_result_summary",
                "warning",
                f"任务「{task['title']}」已完成但缺少结果摘要。",
                task["id"],
            )
        if task.get("status") == "ready" and not document.get("nextStep"):
            add_issue(
                "active_without_next_step",
                "warning",
                f"任务「{task['title']}」缺少下一步说明。",
                task["id"],
            )

    severity_rank = {"error": 0, "warning": 1, "info": 2}
    issues.sort(key=lambda item: (severity_rank.get(item["severity"], 9), item.get("code", "")))
    error_count = len([item for item in issues if item["severity"] == "error"])
    warning_count = len([item for item in issues if item["severity"] == "warning"])
    return {
        "ok": error_count == 0,
        "checkedAt": now_iso(),
        "summary": {
            "tasks": len(tasks),
            "activeTasks": len(active_tasks),
            "inboxCaptures": len([item for item in normalized["captures"] if item.get("status") == "inbox"]),
            "runningTasks": len(running_tasks),
            "errors": error_count,
            "warnings": warning_count,
            "latestSelectedTaskId": latest_selected_id,
        },
        "issues": issues[:50],
    }


def _execution_budget_for(mode: str) -> dict[str, Any]:
    base_budget = {
        "maxSteps": 999,
        "maxFilesChanged": 999,
        "maxCommands": 999,
        "mayModifyCode": True,
        "requiresVerification": True,
        "stopConditions": [
            "任务空间审计出现 error",
        ],
    }
    if mode == "skip":
        return {
            **base_budget,
            "maxSteps": 0,
            "maxFilesChanged": 0,
            "maxCommands": 1,
            "mayModifyCode": False,
            "requiresVerification": False,
        }
    if mode == "ask_user":
        return {
            **base_budget,
        }
    if mode == "report_only":
        return {
            **base_budget,
        }
    if mode == "execute_safe":
        return base_budget
    return base_budget


def _suggestions_for_execution_packet(task_space: dict[str, Any], task_id: str | None) -> list[dict[str, Any]]:
    suggestions = [
        suggestion
        for suggestion in task_space.get("plannerSuggestions", [])
        if isinstance(suggestion, dict) and suggestion.get("status") == "open"
    ]
    if task_id:
        related = [
            suggestion
            for suggestion in suggestions
            if suggestion.get("taskId") == task_id or task_id in suggestion.get("relatedTaskIds", [])
        ]
        other = [suggestion for suggestion in suggestions if suggestion not in related]
        suggestions = [*related, *other]

    result: list[dict[str, Any]] = []
    for suggestion in suggestions[:8]:
        preview = suggestion.get("preview") if isinstance(suggestion.get("preview"), dict) else {}
        result.append(
            {
                "id": suggestion.get("id"),
                "kind": suggestion.get("kind"),
                "severity": suggestion.get("severity"),
                "taskId": suggestion.get("taskId"),
                "relatedTaskIds": suggestion.get("relatedTaskIds", []),
                "title": suggestion.get("title"),
                "proposedAction": suggestion.get("proposedAction"),
                **({"previewSummary": preview.get("summary")} if preview.get("summary") else {}),
            }
        )
    return result


def build_execution_packet(
    task_space: dict[str, Any],
    task_id: str | None = None,
    username: str = "",
) -> dict[str, Any]:
    normalized = normalize_task_space(task_space)
    selected_task_id = task_id or _latest_selected_task_id(normalized)
    latest_log = (normalized.get("plannerLogs") or [{}])[0]
    planning_decision = (
        latest_log.get("planningDecision")
        if isinstance(latest_log, dict) and isinstance(latest_log.get("planningDecision"), dict)
        else build_planner_decision(normalized["tasks"])
    )
    planner_suggestions = _suggestions_for_execution_packet(normalized, selected_task_id)
    task = next((item for item in normalized["tasks"] if item["id"] == selected_task_id), None)
    tasks_by_id = {item["id"]: item for item in normalized["tasks"]}
    if task is None:
        return {
            "hasTask": False,
            "task": None,
            "decision": {
                "mode": "skip",
                "reason": "当前规划日志没有选中任务。",
                "allowedActions": ["重新运行规划检查", "记录新的采集项"],
                "forbiddenActions": ["不要凭会话记忆执行旧任务"],
            },
            "budget": _execution_budget_for("skip"),
            "planningDecision": planning_decision,
            "plannerSuggestions": planner_suggestions,
            "snapshot": None,
            "writeback": None,
            "prompt": "",
        }
    if task_id and planning_decision.get("selectedTaskId") != selected_task_id:
        planning_candidate = next(
            (
                item.get("title") or item["id"]
                for item in normalized["tasks"]
                if item["id"] == planning_decision.get("selectedTaskId")
            ),
            "无",
        )
        planning_decision = {
            **planning_decision,
            "requestedTaskId": selected_task_id,
            "selectedTaskId": selected_task_id,
            "selectedReason": (
                f"当前执行包由用户选中「{task.get('title', task['id'])}」生成；"
                f"最新规划候选为「{planning_candidate}」。"
            ),
        }

    policy = task.get("executionPolicy")
    risk = task.get("risk")
    status = task.get("status")
    execution_blockers = (
        _execution_blocking_reasons(task, tasks_by_id)
        if task_id and planning_decision.get("requestedTaskId") == selected_task_id
        else []
    )
    if execution_blockers:
        mode = "skip"
        reason = f"任务当前不应直接执行：{'；'.join(execution_blockers[:3])}。"
        allowed_actions = ["查看当前任务状态", "优先处理前置任务或子任务", "重新运行规划检查"]
    else:
        mode = "execute_safe"
        reason = "默认拥有完整执行权限，直接推进任务。"
        allowed_actions = ["直接执行任务", "修改必要文件", "运行必要命令", "回写结果和剩余风险"]

    if status in {"done", "archived"}:
        mode = "skip"
        reason = "任务已经完成或已归档，本轮不执行。"
        allowed_actions = ["报告任务无需继续执行"]

    forbidden_actions = [
        "不要依赖聊天记忆代替任务空间",
        "不要跳过验证直接标记完成",
        "不要自动归档任务",
    ]

    budget = _execution_budget_for(mode)
    snapshot = {
        "packetId": new_id("packet"),
        "createdAt": now_iso(),
        "taskId": task["id"],
        "taskUpdatedAt": task.get("updatedAt"),
        "plannerLogId": latest_log.get("id") if isinstance(latest_log, dict) else None,
        "plannerRanAt": latest_log.get("ranAt") if isinstance(latest_log, dict) else None,
        "documentDigest": {
            "goal": task["document"].get("goal", ""),
            "currentState": task["document"].get("currentState", ""),
            "nextStep": task["document"].get("nextStep", ""),
            "doneCriteria": task["document"].get("doneCriteria", ""),
        },
    }
    username = username.strip()
    username_args = f"--username {username} " if username else ""
    argv_template = [
        "uv",
        "run",
        "python",
        "scripts/ai_task_space_append_execution_record.py",
        *(["--username", username] if username else []),
        "--task-id",
        task["id"],
        "--packet-id",
        snapshot["packetId"],
        "--expected-task-updated-at",
        str(snapshot["taskUpdatedAt"] or ""),
        "--max-steps",
        str(budget["maxSteps"]),
        "--max-commands",
        str(budget["maxCommands"]),
        "--max-files-changed",
        str(budget["maxFilesChanged"]),
        "--steps-done",
        "<n>",
        "--commands-run",
        "<n>",
        "--files-changed",
        "<n>",
        "--summary",
        "<本轮摘要>",
        "--verification",
        "<验证命令或无法验证原因>",
        "--remaining-risk",
        "<剩余风险或待审核点>",
        "--next-step",
        "<下一轮最小步骤>",
        "--status",
        "progress",
        "--json",
    ]
    writeback = {
        "taskId": task["id"],
        **({"username": username} if username else {}),
        "endpoint": f"/api/ai-task-space/tasks/{task['id']}/execution-records",
        "cli": (
            "uv run python scripts/ai_task_space_append_execution_record.py "
            f"{username_args}"
            f"--task-id {task['id']} "
            f"--packet-id {snapshot['packetId']} "
            f"--expected-task-updated-at {snapshot['taskUpdatedAt']} "
            f"--max-steps {budget['maxSteps']} "
            f"--max-commands {budget['maxCommands']} "
            f"--max-files-changed {budget['maxFilesChanged']} "
            "--steps-done <n> --commands-run <n> --files-changed <n> "
            "--summary <本轮摘要> "
            "--verification <验证命令或无法验证原因> "
            "--remaining-risk <剩余风险或待审核点> "
            "--next-step <下一轮最小步骤> "
            "--status progress "
            "--json"
        ),
        "argvTemplate": argv_template,
        "statuses": ["progress", "done", "blocked"],
    }
    prompt = "\n".join(
        [
            "你是 CodeYun AI 任务空间的自动化执行器。",
            "先读取本执行包，不要重新按聊天记忆挑任务。",
            f"任务标题：{task['title']}",
            f"执行模式：{mode}",
            f"原因：{reason}",
            f"规划选择依据：{planning_decision.get('selectedReason', '')}",
            (
                "执行权限：完整权限；"
                f"回写统计上限为 {budget['maxSteps']} 步、"
                f"{budget['maxFilesChanged']} 个文件、"
                f"{budget['maxCommands']} 条命令。"
            ),
            f"目标：{task['document'].get('goal', '')}",
            f"当前状态：{task['document'].get('currentState', '')}",
            f"下一步：{task['document'].get('nextStep', '')}",
            (
                "本轮整理建议："
                + (
                    "；".join(
                        f"[{item.get('kind')}] {item.get('title')} -> {item.get('proposedAction')}"
                        for item in planner_suggestions[:5]
                    )
                    if planner_suggestions
                    else "无"
                )
            ),
            "完成后按 automation_directive.shouldWriteBack 判断是否回写；为 true 时用执行回写接口或 CLI 写入摘要、验证、剩余风险和下一步，为 false 时只在最终报告说明跳过回写原因。",
        ]
    )
    return {
        "hasTask": True,
        "task": task,
        "decision": {
            "mode": mode,
            "reason": reason,
            "allowedActions": allowed_actions,
            "forbiddenActions": forbidden_actions,
        },
        "budget": budget,
        "planningDecision": planning_decision,
        "plannerSuggestions": planner_suggestions,
        "snapshot": snapshot,
        "writeback": writeback,
        "prompt": prompt,
    }


def _latest_execution_record(task: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(task, dict):
        return None
    records = task.get("executionRecords")
    if not isinstance(records, list):
        return None
    for record in records:
        if isinstance(record, dict) and str(record.get("summary") or "").strip():
            return record
    return None


def _has_recent_waiting_confirmation_record(task: dict[str, Any] | None) -> bool:
    record = _latest_execution_record(task)
    if not record or record.get("status") != "progress":
        return False

    text = "\n".join(
        str(record.get(field) or "")
        for field in ("summary", "verification", "remainingRisk", "nextStep")
    )
    confirmation_markers = ("等待用户确认", "需要用户确认", "待确认", "用户确认")
    no_code_markers = ("未修改业务代码", "未改业务代码", "未改代码", "不修改业务代码")
    return any(marker in text for marker in confirmation_markers) and any(
        marker in text for marker in no_code_markers
    )


def task_waits_for_user_confirmation(task: dict[str, Any] | None) -> bool:
    return False


def build_automation_directive(execution_packet: dict[str, Any], audit: dict[str, Any]) -> dict[str, Any]:
    issues = audit.get("issues") if isinstance(audit.get("issues"), list) else []
    error_issues = [issue for issue in issues if issue.get("severity") == "error"]
    task = execution_packet.get("task") if isinstance(execution_packet.get("task"), dict) else None
    decision = execution_packet.get("decision") if isinstance(execution_packet.get("decision"), dict) else {}
    budget = execution_packet.get("budget") if isinstance(execution_packet.get("budget"), dict) else {}
    writeback = execution_packet.get("writeback") if isinstance(execution_packet.get("writeback"), dict) else None
    planner_suggestions = (
        execution_packet.get("plannerSuggestions")
        if isinstance(execution_packet.get("plannerSuggestions"), list)
        else []
    )
    mode = decision.get("mode") or "skip"

    def completion_template(
        action: str,
        *,
        writeback_status: str | None,
        summary: str,
        verification: str,
        remaining_risk: str,
        next_step: str,
    ) -> dict[str, Any]:
        return {
            "finalReport": [
                "action",
                "是否修改代码",
                "验证结果",
                "回写是否成功",
                "回写 current_fingerprint",
                "下一步",
            ],
            "writeback": {
                "status": writeback_status,
                "summary": summary,
                "verification": verification,
                "remainingRisk": remaining_risk,
                "nextStep": next_step,
            },
            "notes": [
                "回写摘要必须是当前局面状态，不写聊天流水。",
                "没有执行命令时，verification 写明未执行及原因。",
            "无法继续推进时，remainingRisk 必须写清楚。",
                "调用回写 CLI 后必须读取回写 JSON；成功时在最终报告记录 current_fingerprint，失败时记录 code/message。",
            ],
        }

    if error_issues:
        return {
            "action": "stop_for_audit",
            "shouldExecute": False,
            "shouldModifyCode": False,
            "shouldWriteBack": writeback is not None,
            "writebackStatus": "blocked" if writeback is not None else None,
            "stopReason": "任务空间审计存在 error，必须先收敛任务空间。",
            "summaryHint": "审计失败，未执行任务；先处理任务空间错误。",
            "requiredChecks": ["重新运行规划检查", "确认审计 error 已清零"],
            "completionTemplate": completion_template(
                "stop_for_audit",
                writeback_status="blocked" if writeback is not None else None,
                summary="审计失败，本轮未执行任务；任务空间需要先收敛。",
                verification="未执行代码；已检查 audit.errors。",
                remaining_risk="列出 audit error 和需要先修复的任务空间不变量。",
                next_step="重新运行规划检查，确认 audit error 清零后再进入执行包。",
            ),
        }

    if not execution_packet.get("hasTask") or mode == "skip":
        return {
            "action": "skip",
            "shouldExecute": False,
            "shouldModifyCode": False,
            "shouldWriteBack": False,
            "writebackStatus": None,
            "stopReason": decision.get("reason") or "当前没有可执行任务。",
            "summaryHint": "本轮没有可执行任务。",
            "requiredChecks": ["下一轮重新读取任务空间"],
            "completionTemplate": completion_template(
                "skip",
                writeback_status=None,
                summary="本轮无可执行任务，未修改任务空间。",
                verification="未执行代码；已读取规划检查输出和 directive。",
                remaining_risk="无直接执行风险；等待下一轮任务空间变化。",
                next_step="下一轮重新读取任务空间。",
            ),
        }

    if mode == "ask_user":
        suggestion_hint = (
            f"同时摘要 {len(planner_suggestions)} 条待审核整理建议。"
            if planner_suggestions
            else "当前没有待审核整理建议。"
        )
        if _has_recent_waiting_confirmation_record(task):
            return {
                "action": "ask_user",
                "shouldExecute": False,
                "shouldModifyCode": False,
                "shouldWriteBack": False,
                "writebackStatus": None,
                "stopReason": decision.get("reason") or "任务要求用户确认。",
                "summaryHint": f"已有等待用户确认的最近回写，本轮不重复写入。{suggestion_hint}",
                "requiredChecks": [
                    "确认未修改业务代码",
                    "阅读 execution_packet.planningDecision",
                    "阅读 execution_packet.plannerSuggestions",
                    "等待用户确认或任务空间变化",
                ],
                "completionTemplate": completion_template(
                    "ask_user",
                    writeback_status=None,
                    summary="已有等待用户确认的最近回写，本轮未重复写入，未修改业务代码。",
                    verification="确认未修改业务代码；本轮只检查到最近回写已覆盖待确认状态。",
                    remaining_risk="等待用户确认；如任务空间出现新采集、新建议或状态变化，下一次规划检查重新判断。",
                    next_step="等待用户确认或任务空间变化后再进入下一次规划检查。",
                ),
            }
        return {
            "action": "ask_user",
            "shouldExecute": False,
            "shouldModifyCode": False,
            "shouldWriteBack": True,
            "writebackStatus": "progress",
            "stopReason": decision.get("reason") or "任务要求用户确认。",
            "summaryHint": f"整理建议和需要用户确认的问题，不修改业务代码。{suggestion_hint}",
            "requiredChecks": [
                "确认未修改业务代码",
                "阅读 execution_packet.planningDecision",
                "阅读 execution_packet.plannerSuggestions",
                "回写建议、风险和下一步",
            ],
            "completionTemplate": completion_template(
                "ask_user",
                writeback_status="progress",
                summary="已整理执行建议、待审核任务树建议和需要用户确认的问题，未修改业务代码。",
                verification="确认未修改业务代码；说明只读检查或未执行命令的原因。",
                remaining_risk="列出需要用户确认的范围、风险、决策点和仍待审核的 plannerSuggestions。",
                next_step="等待用户确认后，下一次规划检查再决定是否执行。",
            ),
        }

    if mode == "report_only":
        suggestion_hint = (
            f"同时检查 {len(planner_suggestions)} 条待审核整理建议。"
            if planner_suggestions
            else "当前没有待审核整理建议。"
        )
        return {
            "action": "report_only",
            "shouldExecute": True,
            "shouldModifyCode": False,
            "shouldWriteBack": True,
            "writebackStatus": "progress",
            "stopReason": "",
            "summaryHint": f"只读取上下文并生成分析报告，回写当前状态。{suggestion_hint}",
            "requiredChecks": [
                "确认未修改业务代码",
                "阅读 execution_packet.planningDecision",
                "阅读 execution_packet.plannerSuggestions",
                "回写分析结论和下一步",
            ],
            "completionTemplate": completion_template(
                "report_only",
                writeback_status="progress",
                summary="已完成只读分析，整理待审核任务树建议，并更新当前状态，未修改业务代码。",
                verification="列出实际读取/检查的文件、命令或未运行命令的原因。",
                remaining_risk="列出分析后仍不确定、需要用户审核、后续验证或待处理 plannerSuggestions 的点。",
                next_step="写下一轮可直接接手的最小可执行步骤。",
            ),
        }

    if mode == "execute_safe":
        return {
            "action": "execute_safe",
            "shouldExecute": True,
            "shouldModifyCode": bool(budget.get("mayModifyCode")),
            "shouldWriteBack": True,
            "writebackStatus": "progress",
            "stopReason": "",
            "summaryHint": "按完整权限直接推进任务，并回写当前状态。",
            "requiredChecks": [
                "默认拥有完整执行权限",
                "按实际执行情况填写回写统计",
                "阅读 execution_packet.planningDecision",
                "回写验证结果、剩余风险和下一步",
            ],
            "completionTemplate": completion_template(
                "execute_safe",
                writeback_status="progress",
                summary="已按完整权限推进任务，并说明当前局面变化。",
                verification="写明实际运行的测试/构建/人工检查结果；无法验证时必须说明原因。",
                remaining_risk="列出未验证、依赖未满足或需要后续处理的点。",
                next_step="写下一轮可直接接手的步骤。",
            ),
        }

    return {
        "action": "skip",
        "shouldExecute": False,
        "shouldModifyCode": False,
        "shouldWriteBack": False,
        "writebackStatus": None,
        "stopReason": f"未知执行模式：{mode}",
        "summaryHint": "本轮跳过，等待下一次规划检查重新判断。",
        "requiredChecks": ["重新运行规划检查"],
        "completionTemplate": completion_template(
            "skip",
            writeback_status=None,
            summary=f"未知执行模式 {mode}，本轮跳过。",
            verification="未执行代码；需要重新运行规划检查。",
            remaining_risk="执行模式未知，不能安全推进。",
            next_step="重新运行规划检查并检查执行包。",
        ),
    }


def build_automation_prompt(username: str = "") -> str:
    username = username.strip()
    username_args = f" --username {username}" if username else ""
    plan_command = f"uv run python scripts/ai_task_space_plan_once.py{username_args} --json"
    return "\n".join(
        [
            "你是 CodeYun AI 任务空间的自动化执行器，调度由 Codex automation 负责。",
            "",
            "每次被 Codex automation 唤醒后，必须先运行一次规划检查，不能凭聊天记忆选择任务：",
            "",
            f"```bash\n{plan_command}\n```",
            "",
            "读取 JSON 输出后，以 `automation_directive` 为最高执行边界：",
            "先读顶层 `planner_state` 判断本轮是否有候选、待运行总数和前几条未运行原因；再读 `execution_packet.planningDecision` 做完整校验。",
            "",
            "- `stop_for_audit`：不执行代码；若有 `writeback.cli`，用 blocked/progress 写回审计结果和下一步。",
            "- `skip`：不执行，不回写；只报告本轮跳过原因。",
            "- `ask_user` / `report_only`：兼容旧执行包；新规划默认不会生成这两类权限模式。",
            "- `execute_safe`：默认拥有完整执行权限，可直接修改必要文件、运行必要命令，并在结束后回写。",
            "",
            "执行约束：",
            "",
            "- 每次规划检查会全量读取任务空间；规划检查 JSON 是本轮唯一执行依据，旧规划检查输出、旧页面状态和上轮执行包在任意写入后都视为过期。",
            "- 每轮只推进 `execution_packet.task` 指向的一个任务。",
            "- 执行前先阅读 `planner_state` 和 `execution_packet.planningDecision`，理解本轮候选池、跳过原因和选中依据。",
            "- `execution_packet.plannerSuggestions` 是任务树整理建议；拆分、合并、补文档这类结构整理由 agent 自行判断并直接应用，不需要用户审核。",
            "- 应用或忽略整理建议时，使用 `scripts/ai_task_space_planner_suggestion.py`，并带上最新 fingerprint。",
            "- 不要重新收集任务；执行期间出现的新需求、上下文或约束必须通过采集脚本进入 Inbox，影响下一次规划检查。",
            "- 不要依赖本会话记忆代替任务空间。",
            "- 不要跳过验证直接标记完成。",
            "- 不要自动归档任务。",
            "- 执行包中的 `budget.maxSteps / maxCommands / maxFilesChanged` 只是回写统计上限，默认给足权限，不作为执行阻断。",
            "- `execution_packet.writeback.cli` 已携带预算上限参数；保持这些 `--max-*` 参数，按实际执行数量填写。",
            "- 优先使用 `execution_packet.writeback.argvTemplate` 组装回写命令，避免 shell 引号和空格导致参数错位；`cli` 只作为可读模板。",
            "- 如果回写因快照过期失败，停止本轮，下一次由 Codex automation 重新运行规划检查。",
            "- 结束前按 `automation_directive.completionTemplate` 填写最终报告和回写字段；如果 `shouldWriteBack=false`，只在最终报告说明未回写原因。",
            "- 调用回写 CLI 后必须读取回写 JSON：`ok=false` 时按 `code/message` 报告失败并停止；`ok=true` 时记录返回的 `current_fingerprint`，作为本轮已成功写回的任务空间版本。",
            "",
            "回写要求：",
            "",
            "- 只有 `automation_directive.shouldWriteBack=true` 时才写回；为 false 时不要调用回写 CLI，避免重复证据。",
            "- 需要回写时，必须使用 `execution_packet.writeback.argvTemplate` 或 `execution_packet.writeback.cli`。",
            "- `--summary` 写当前棋局式状态，不写聊天流水。",
            "- `--verification` 写验证命令、检查结果，或明确说明无法验证原因。",
            "- `--remaining-risk` 写未解决依赖、风险、待审核 plannerSuggestions 或需要用户审核的点。",
            "- `--next-step` 写下一轮可直接接手的最小步骤。",
            "",
            "结束报告只保留：本轮 action、是否改代码、验证结果、回写是否成功、回写 JSON 的 `current_fingerprint`、下一步。",
        ]
    )


def _make_planner_suggestion(
    *,
    kind: str,
    title: str,
    rationale: str,
    proposed_action: str,
    task_id: str | None = None,
    related_task_ids: list[str] | None = None,
    severity: str = "info",
    preview: dict[str, Any] | None = None,
    timestamp: str | None = None,
) -> dict[str, Any]:
    related_task_ids = related_task_ids or []
    return {
        "id": stable_suggestion_id(kind, task_id, ",".join(sorted(related_task_ids)), title),
        "kind": kind,
        "severity": severity if severity in {"info", "warning"} else "info",
        **({"taskId": task_id} if task_id else {}),
        "relatedTaskIds": related_task_ids,
        "title": title,
        "rationale": rationale,
        "proposedAction": proposed_action,
        **({"preview": preview} if isinstance(preview, dict) else {}),
        "status": "open",
        "createdAt": timestamp or now_iso(),
    }


def _split_suggestion_preview(task: dict[str, Any]) -> dict[str, Any]:
    title = task.get("title") or "任务"
    return {
        "summary": "把任务文档整理成边界、推进和验证三个段落，任务树仍保留单一节点。",
        "creates": [
            {
                "title": f"{title}：梳理边界",
                "kind": "document_section",
                "document": {
                    "goal": "明确目标、范围、输入输出和不做的部分。",
                    "nextStep": "把现有上下文整理成可执行边界和验收口径。",
                    "doneCriteria": "目标范围、入口、依赖和验证方式都明确。",
                },
            },
            {
                "title": f"{title}：最小推进",
                "kind": "document_section",
                "dependsOnPrevious": True,
                "dependsOnTitle": f"{title}：梳理边界",
                "document": {
                    "goal": "完成一个最小、可验证的实现或整理步骤。",
                    "nextStep": "基于已确认边界推进最小改动，并保留验证证据。",
                    "doneCriteria": "改动可被测试、构建或人工检查验证。",
                },
            },
            {
                "title": f"{title}：验证回写",
                "kind": "document_section",
                "dependsOnPrevious": True,
                "dependsOnTitle": f"{title}：最小推进",
                "document": {
                    "goal": "验证结果并回写当前局面。",
                    "nextStep": "运行必要检查，更新父任务状态和证据层。",
                    "doneCriteria": "验证结果、剩余风险和下一步已写回任务空间。",
                },
            },
        ],
        "updates": [
            {
                "taskId": task.get("id", ""),
                "field": "document",
                "note": "只补充任务文档结构，不创建子任务。",
            }
        ],
    }


def _merge_suggestion_preview(primary: dict[str, Any], related_tasks: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "summary": f"保留「{primary.get('title', '主任务')}」，把 {len(related_tasks)} 个重复任务标记为已完成。",
        "targetTaskId": primary.get("id", ""),
        "sourceTaskIds": [task["id"] for task in related_tasks],
        "updates": [
            {
                "taskId": primary.get("id", ""),
                "field": "document",
                "note": "合并重复任务的上下文、已知事实、依赖和证据。",
            },
            *[
                {
                    "taskId": task["id"],
                    "field": "status",
                    "value": "done",
                    "note": f"重复项「{task.get('title', task['id'])}」合并后标记为已完成。",
                }
                for task in related_tasks
            ],
        ],
    }


def build_planner_suggestions(task_space: dict[str, Any]) -> list[dict[str, Any]]:
    normalized = normalize_task_space(task_space)
    tasks = [task for task in normalized["tasks"] if task.get("status") != "archived"]
    suggestions: list[dict[str, Any]] = []
    existing_suggestions = [
        suggestion
        for suggestion in normalized.get("plannerSuggestions", [])
        if isinstance(suggestion, dict)
    ]
    existing_by_id = {suggestion["id"]: suggestion for suggestion in existing_suggestions}
    timestamp = now_iso()

    def add(suggestion: dict[str, Any]) -> None:
        existing = existing_by_id.get(suggestion["id"])
        if existing and existing.get("status") in {"dismissed", "applied"}:
            return
        if suggestion["id"] not in {item["id"] for item in suggestions}:
            suggestions.append(suggestion)

    title_groups: dict[str, list[dict[str, Any]]] = {}
    for task in tasks:
        title_key = re.sub(r"\s+", "", task["title"]).lower()
        if title_key:
            title_groups.setdefault(title_key, []).append(task)

        document = task.get("document", {})
        can_auto_prepare = True
        if can_auto_prepare and task.get("status") == "ready" and not document.get("nextStep"):
            add(
                _make_planner_suggestion(
                    kind="document",
                    task_id=task["id"],
                    title=f"补齐「{task['title']}」下一步",
                    rationale="待运行任务缺少 nextStep，下一轮 automation 无法直接接手。",
                    proposed_action="把 nextStep 改写成一个可验证的小步，并保留当前状态说明。",
                    severity="warning",
                    timestamp=timestamp,
                )
            )
        if can_auto_prepare and task.get("status") == "ready" and not document.get("doneCriteria"):
            add(
                _make_planner_suggestion(
                    kind="document",
                    task_id=task["id"],
                    title=f"补齐「{task['title']}」完成标准",
                    rationale="待运行任务缺少 doneCriteria，执行器无法判断什么时候能安全标记完成。",
                    proposed_action="写清楚验收标准、验证方式和哪些情况不能算完成。",
                    severity="warning",
                    timestamp=timestamp,
                )
            )

        if task.get("status") == "done" and document.get("resultSummary"):
            add(
                _make_planner_suggestion(
                    kind="document",
                    task_id=task["id"],
                    title=f"补充「{task['title']}」完成记录",
                    rationale="任务已完成且有结果摘要，可继续补充验证记录或经验摘要。",
                    proposed_action="确认完成记录是否足够清楚；不改变任务状态。",
                    severity="info",
                    timestamp=timestamp,
                )
            )

        if task.get("dependsOn") and not document.get("dependencies"):
            add(
                _make_planner_suggestion(
                    kind="dependency",
                    task_id=task["id"],
                    related_task_ids=task.get("dependsOn", []),
                    title=f"补写「{task['title']}」依赖说明",
                    rationale="结构化 dependsOn 已存在，但文档层 dependencies 为空，后续执行者读文档时看不到依赖来源。",
                    proposed_action="把依赖关系转写为简短 dependencies 文档，不改变 dependsOn 结构。",
                    severity="info",
                    timestamp=timestamp,
                )
            )

    for same_title_tasks in title_groups.values():
        active_same_title_tasks = [
            task for task in same_title_tasks if task.get("status") != "done"
        ]
        if len(active_same_title_tasks) > 1:
            primary = active_same_title_tasks[0]
            related_tasks = active_same_title_tasks[1:]
            related_ids = [task["id"] for task in related_tasks]
            add(
                _make_planner_suggestion(
                    kind="merge",
                    task_id=primary["id"],
                    related_task_ids=related_ids,
                    title=f"合并重复任务「{primary['title']}」",
                    rationale="存在多个未关闭任务标题相同，后续规划检查可能在重复节点之间来回选择。",
                    proposed_action="保留一个主任务，把其他任务的上下文、证据和依赖合并进去后关闭重复项。",
                    severity="warning",
                    preview=_merge_suggestion_preview(primary, related_tasks),
                    timestamp=timestamp,
                )
            )

    severity_rank = {"warning": 0, "info": 1}
    kind_rank = {"dependency": 0, "document": 1, "split": 2, "merge": 3, "archive": 4}
    suggestions.sort(
        key=lambda item: (
            severity_rank.get(item.get("severity"), 9),
            kind_rank.get(item.get("kind"), 9),
            item.get("title", ""),
        )
    )
    closed_suggestions = [
        suggestion
        for suggestion in existing_suggestions
        if suggestion.get("status") in {"dismissed", "applied"}
    ][:30]
    return [*suggestions[:20], *closed_suggestions][:50]


def _mark_suggestion_status(
    normalized: dict[str, Any],
    suggestion_id: str,
    status: str,
    timestamp: str,
) -> dict[str, Any]:
    next_suggestions = []
    marked = False
    for suggestion in normalized.get("plannerSuggestions", []):
        if suggestion.get("id") == suggestion_id:
            next_suggestions.append({**suggestion, "status": status, "resolvedAt": timestamp})
            marked = True
        else:
            next_suggestions.append(suggestion)
    if not marked:
        raise KeyError(suggestion_id)
    normalized["plannerSuggestions"] = next_suggestions
    return normalized


def dismiss_planner_suggestion(task_space: dict[str, Any], suggestion_id: str) -> dict[str, Any]:
    normalized = normalize_task_space(task_space)
    return _mark_suggestion_status(normalized, suggestion_id, "dismissed", now_iso())


def _append_unique_lines(existing: str, lines: list[str]) -> str:
    values = [line.strip() for line in str(existing or "").splitlines() if line.strip()]
    for line in lines:
        value = str(line or "").strip()
        if value and value not in values:
            values.append(value)
    return "\n".join(values)


def _replace_prefixed_line(existing: str, prefix: str, value: str) -> str:
    values = [
        line.strip()
        for line in str(existing or "").splitlines()
        if line.strip() and not line.strip().startswith(prefix)
    ]
    next_value = str(value or "").strip()
    if next_value:
        values.append(f"{prefix}{next_value}")
    return "\n".join(values)


def _apply_split_suggestion(
    normalized: dict[str, Any],
    task: dict[str, Any],
    suggestion: dict[str, Any],
    timestamp: str,
) -> None:
    preview = suggestion.get("preview") if isinstance(suggestion.get("preview"), dict) else _split_suggestion_preview(task)
    creates = preview.get("creates") if isinstance(preview, dict) else []
    if not isinstance(creates, list) or not creates:
        raise ValueError("拆分建议缺少可审核预览。")

    sections: list[str] = []
    for item in creates[:5]:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()[:80]
        if not title:
            continue
        document_patch = item.get("document") if isinstance(item.get("document"), dict) else {}
        goal = str(document_patch.get("goal") or "").strip()
        next_step = str(document_patch.get("nextStep") or "").strip()
        done_criteria = str(document_patch.get("doneCriteria") or "").strip()
        section = "；".join(part for part in [goal, next_step, done_criteria] if part)
        sections.append(f"{title}：{section}" if section else title)

    if not sections:
        raise ValueError("拆分建议没有可整理的文档段落。")

    task["status"] = "ready" if task.get("status") != "done" else "done"
    task["document"]["currentState"] = "已按规划建议补充任务文档；任务仍作为单一节点直接推进。"
    task["document"]["nextStep"] = task["document"].get("nextStep") or "按任务文档继续推进一个可验证小步。"
    task["updatedAt"] = timestamp


def _apply_merge_suggestion(
    normalized: dict[str, Any],
    task: dict[str, Any],
    suggestion: dict[str, Any],
    timestamp: str,
) -> None:
    related_ids = [value for value in suggestion.get("relatedTaskIds", []) if isinstance(value, str)]
    sources = [
        item
        for item in normalized["tasks"]
        if item.get("id") in related_ids and item.get("status") != "archived"
    ]
    if not sources:
        raise ValueError("合并建议没有可合并的重复任务。")

    document = task["document"]
    merged_context: list[str] = []
    merged_dependencies: list[str] = []
    merged_related_ids = set(task.get("relatedTaskIds", []))
    merged_depends_on = set(task.get("dependsOn", []))
    merged_evidence: list[str] = []
    merged_attachments = task.get("attachments", [])

    for source in sources:
        source_document = source.get("document", {})
        for value in [source_document.get("context", ""), source_document.get("resultSummary", "")]:
            if value:
                merged_context.append(f"来自重复任务「{source.get('title', source['id'])}」：{value}")
        if source_document.get("dependencies"):
            merged_dependencies.append(f"来自重复任务「{source.get('title', source['id'])}」：{source_document['dependencies']}")
        merged_related_ids.add(source["id"])
        merged_related_ids.update(source.get("relatedTaskIds", []))
        merged_depends_on.update(source.get("dependsOn", []))
        merged_evidence.extend(source.get("evidenceLog", [])[:10])
        merged_attachments = merge_capture_attachments(merged_attachments, source.get("attachments", []))

        source["status"] = "done"
        source["relatedTaskIds"] = sorted({*source.get("relatedTaskIds", []), task["id"]})
        source["document"]["currentState"] = f"已合并到「{task.get('title', task['id'])}」，作为重复任务完成。"
        source["document"]["resultSummary"] = source["document"].get("resultSummary") or f"重复任务内容已合并到 {task['id']}。"
        source["evidenceLog"] = [f"{timestamp} 按规划建议合并到任务 {task['id']}。", *source.get("evidenceLog", [])][:50]
        source["updatedAt"] = timestamp

    document["context"] = _append_unique_lines(document.get("context", ""), merged_context)
    document["dependencies"] = _append_unique_lines(document.get("dependencies", ""), merged_dependencies)
    task["relatedTaskIds"] = sorted(value for value in merged_related_ids if value != task["id"])
    task["dependsOn"] = sorted(value for value in merged_depends_on if value != task["id"])
    task["attachments"] = merged_attachments
    task["evidenceLog"] = [
        f"{timestamp} 合并重复任务：{'、'.join(source.get('title', source['id']) for source in sources)}。",
        *merged_evidence,
        *task.get("evidenceLog", []),
    ][:50]


def apply_planner_suggestion(task_space: dict[str, Any], suggestion_id: str) -> dict[str, Any]:
    normalized = normalize_task_space(task_space)
    timestamp = now_iso()
    suggestion = next(
        (item for item in normalized.get("plannerSuggestions", []) if item.get("id") == suggestion_id),
        None,
    )
    if suggestion is None:
        raise KeyError(suggestion_id)
    if suggestion.get("status") != "open":
        return normalized

    task_id = suggestion.get("taskId")
    task = next((item for item in normalized["tasks"] if item.get("id") == task_id), None)
    if task is None:
        raise KeyError(task_id or suggestion_id)

    document = task["document"]
    kind = suggestion.get("kind")
    title = suggestion.get("title", "")
    if kind == "document" and "完成标准" in title:
        document["doneCriteria"] = (
            document.get("doneCriteria")
            or "验收标准：目标、入口、数据来源和结果边界明确。\n验证方式：能通过对应测试、构建或人工检查说明结果。\n未完成情况：范围仍不明确、无法验证或需要用户决策时不能标记完成。"
        )
    elif kind == "document" and "下一步" in title:
        document["nextStep"] = document.get("nextStep") or "先补齐任务边界、完成标准和最小验证方式，再进入执行选择。"
    elif kind == "dependency":
        related_titles = [
            item.get("title") or item["id"]
            for item in normalized["tasks"]
            if item["id"] in set(suggestion.get("relatedTaskIds", []))
        ]
        document["dependencies"] = document.get("dependencies") or f"依赖任务：{'、'.join(related_titles) if related_titles else '见 dependsOn'}。"
    elif kind == "archive":
        if task.get("status") != "done":
            raise ValueError("只有已完成任务才能应用完成记录建议。")
        document["currentState"] = "任务已完成。"
    elif kind == "split":
        _apply_split_suggestion(normalized, task, suggestion, timestamp)
    elif kind == "merge":
        _apply_merge_suggestion(normalized, task, suggestion, timestamp)
    else:
        raise ValueError("该建议需要更明确的用户审核动作，暂不支持一键应用。")

    task["evidenceLog"] = [f"{timestamp} 应用规划建议：{suggestion.get('title')}", *task.get("evidenceLog", [])][:50]
    task["updatedAt"] = timestamp
    return _mark_suggestion_status(normalized, suggestion_id, "applied", timestamp)


def run_planner_check(task_space: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_task_space(task_space)
    timestamp = now_iso()
    actions: list[str] = []
    tasks = normalized["tasks"]
    previous_selected_task_id = _latest_selected_task_id(normalized)

    for capture in [item for item in normalized["captures"] if item.get("status") == "inbox"]:
        title = _title_from_text(capture["rawText"])
        existing = next(
            (
                task
                for task in tasks
                if task.get("status") not in {"done", "archived"}
                and (title[:12] and title[:12] in task.get("title", ""))
            ),
            None,
        )
        if existing:
            existing["document"]["context"] = "\n".join(
                value for value in [existing["document"].get("context", ""), capture["rawText"]] if value
            )
            existing["attachments"] = merge_capture_attachments(existing.get("attachments", []), capture.get("attachments", []))
            existing["evidenceLog"] = [f"{timestamp} 归并采集项 {capture['id']}。", *existing.get("evidenceLog", [])][:50]
            existing["updatedAt"] = timestamp
            capture["status"] = "triaged"
            capture["linkedTaskId"] = existing["id"]
            actions.append(f"归并采集项到「{existing['title']}」")
            continue
        before_count = len(tasks)
        normalized = promote_capture(normalized, capture["id"])
        tasks = normalized["tasks"]
        if len(tasks) > before_count:
            actions.append(f"创建任务「{title}」")

    tasks_by_id = {task["id"]: task for task in tasks}
    for task in tasks:
        if _compact_task_evidence(task, "规划检查选为本轮执行候选"):
            actions.append(f"压缩「{task['title']}」的重复候选证据")

        if task.get("status") not in {"archived", "done"}:
            missing_dependencies, pending_dependencies = _dependency_summary(task, tasks_by_id)
            if missing_dependencies or pending_dependencies:
                block_text = _dependency_block_text(missing_dependencies, pending_dependencies)
                if task["document"].get("dependencies") != block_text:
                    task["document"]["currentState"] = "前置依赖尚未满足，暂不运行。"
                    task["document"]["dependencies"] = block_text
                    task["updatedAt"] = timestamp
                    actions.append(f"记录依赖「{task['title']}」：{block_text}")
                continue
            dependency_text = task["document"].get("dependencies", "")
            if (
                task.get("risk") != "high"
                and dependency_text.startswith(("等待前置任务完成", "依赖任务不存在"))
            ):
                task["document"]["currentState"] = "前置依赖已满足，待运行。"
                task["document"]["dependencies"] = ""
                task["updatedAt"] = timestamp
                actions.append(f"解除依赖记录「{task['title']}」")

        if task.get("status") == "done" and not task.get("document", {}).get("resultSummary"):
            task["document"]["resultSummary"] = "已完成，但还缺少验证摘要。"
            task["updatedAt"] = timestamp
            actions.append(f"补齐「{task['title']}」的完成摘要占位")

    planner_decision = build_planner_decision(tasks)
    candidate = next((task for task in tasks if task["id"] == planner_decision.get("selectedTaskId")), None)
    if candidate:
        has_candidate_selection_evidence = any(
            "规划检查选为本轮执行候选" in line
            for line in candidate.get("evidenceLog", [])
            if isinstance(line, str)
        )
        is_repeat_candidate_selection = previous_selected_task_id == candidate["id"] and has_candidate_selection_evidence
        candidate["document"]["currentState"] = "本轮规划选中，等待执行器按完整权限直接推进。"
        candidate["document"]["nextStep"] = candidate["document"].get("nextStep") or "执行一个小步，验证后回写任务文档和证据日志。"
        if not has_candidate_selection_evidence:
            candidate["evidenceLog"] = [f"{timestamp} 规划检查选为本轮执行候选。", *candidate.get("evidenceLog", [])][:50]
        candidate["updatedAt"] = timestamp
        actions.append(
            f"继续本轮候选「{candidate['title']}」"
            if is_repeat_candidate_selection
            else f"选中本轮候选「{candidate['title']}」"
        )

    suggestions = build_planner_suggestions(normalized)
    normalized["plannerSuggestions"] = suggestions
    open_suggestions = [suggestion for suggestion in suggestions if suggestion.get("status") == "open"]
    if open_suggestions:
        actions.append(f"生成 {len(open_suggestions)} 条待处理任务树整理建议")

    log = {
        "id": new_id("log"),
        "ranAt": timestamp,
        "summary": (
            f"整理任务空间，当前任务树 {len([task for task in tasks if task.get('status') != 'archived'])} 个节点。"
            if actions
            else "任务空间无明显变化，本轮只完成全量检查。"
        ),
        "selectedTaskId": candidate["id"] if candidate else None,
        "planningDecision": planner_decision,
        "actions": actions or ["全量读取任务空间，未发现需要改写的节点。"],
        "suggestionIds": [suggestion["id"] for suggestion in open_suggestions[:8]],
    }
    normalized["plannerLogs"] = [log, *normalized.get("plannerLogs", [])][:50]
    return normalized


def append_execution_record(
    task_space: dict[str, Any],
    task_id: str,
    *,
    summary: str,
    verification: str = "",
    remaining_risk: str = "",
    next_step: str = "",
    status: str = "progress",
    packet_id: str = "",
    expected_task_updated_at: str = "",
    steps_done: int = 0,
    commands_run: int = 0,
    files_changed: int = 0,
) -> dict[str, Any]:
    normalized = normalize_task_space(task_space)
    task = next((item for item in normalized["tasks"] if item["id"] == task_id), None)
    if task is None:
        raise KeyError(task_id)

    timestamp = now_iso()
    record = normalize_execution_record(
        {
            "summary": summary,
            "verification": verification,
            "remainingRisk": remaining_risk,
            "nextStep": next_step,
            "status": status,
            "recordedAt": timestamp,
            "packetId": packet_id,
            "budgetUsed": {
                "stepsDone": steps_done,
                "commandsRun": commands_run,
                "filesChanged": files_changed,
            },
        }
    )
    if record is None:
        return normalized
    if record["packetId"]:
        existing_record = next(
            (
                item
                for item in task.get("executionRecords", [])
                if isinstance(item, dict) and item.get("packetId") == record["packetId"]
            ),
            None,
        )
        if existing_record is not None:
            if _same_execution_packet_payload(existing_record, record):
                return normalized
            raise ExecutionPacketReplayConflict(
                f"执行包 {record['packetId']} 已写回过不同内容，请重新运行规划检查获取新执行包。"
            )
    if expected_task_updated_at and task.get("updatedAt") != expected_task_updated_at:
        raise ExecutionSnapshotMismatch(
            f"任务 {task_id} 已从 {expected_task_updated_at} 更新到 {task.get('updatedAt')}，需要重新运行规划检查。"
        )

    task["executionRecords"] = [record, *task.get("executionRecords", [])][:50]
    task["evidenceLog"] = [f"{timestamp} 执行回写：{record['summary']}", *task.get("evidenceLog", [])][:50]
    document = task["document"]
    document["currentState"] = record["summary"]
    if record["verification"]:
        document["knownFacts"] = _replace_prefixed_line(
            document.get("knownFacts", ""),
            "验证：",
            record["verification"],
        )
    if record["nextStep"]:
        document["nextStep"] = record["nextStep"]
    if record["remainingRisk"]:
        document["dependencies"] = _replace_prefixed_line(
            document.get("dependencies", ""),
            "剩余风险：",
            record["remainingRisk"],
        )
    if record["status"] == "done":
        task["status"] = "done"
        task["completedAt"] = timestamp
        document["resultSummary"] = record["summary"]
    elif record["status"] == "blocked":
        task["status"] = "ready"
    else:
        task["status"] = "ready"
    task["updatedAt"] = timestamp
    return normalized


def confirm_task_user_ready(
    task_space: dict[str, Any],
    task_id: str,
    *,
    note: str = "",
) -> dict[str, Any]:
    normalized = normalize_task_space(task_space)
    task = next((item for item in normalized["tasks"] if item["id"] == task_id), None)
    if task is None:
        raise KeyError(task_id)
    if not _has_recent_waiting_confirmation_record(task):
        raise ValueError("任务最近状态不是等待用户确认。")

    latest_record = _latest_execution_record(task) or {}
    next_step = (
        str(latest_record.get("nextStep") or "").strip()
        or task["document"].get("nextStep")
        or "下一次规划检查重新评估候选。"
    )
    verification = "用户在任务系统页面确认继续推进。"
    if note.strip():
        verification = f"{verification} 补充说明：{note.strip()}"
    confirmed = append_execution_record(
        normalized,
        task_id,
        summary="用户已确认继续推进，等待下一次规划检查重新评估。",
        verification=verification,
        remaining_risk="",
        next_step=next_step,
        status="progress",
    )
    confirmed_task = next(item for item in confirmed["tasks"] if item["id"] == task_id)
    confirmed_task["document"]["currentState"] = "用户已确认继续推进，等待下一次规划检查重新评估。"
    confirmed_task["document"]["nextStep"] = next_step
    confirmed_task["updatedAt"] = now_iso()
    return confirmed


def apply_task_review_action(
    task_space: dict[str, Any],
    task_id: str,
    action: str,
) -> dict[str, Any]:
    normalized = normalize_task_space(task_space)
    task = next((item for item in normalized["tasks"] if item["id"] == task_id), None)
    if task is None:
        raise KeyError(task_id)

    timestamp = now_iso()
    document = task["document"]
    evidence = task.setdefault("evidenceLog", [])

    if action == "mark_done":
        if task.get("status") in {"done", "archived"}:
            raise ValueError("任务已完成或已归档，不能重复标记完成。")
        task["status"] = "done"
        task["completedAt"] = timestamp
        document["currentState"] = "已完成。"
        if not document.get("resultSummary"):
            document["resultSummary"] = "已完成。请补充验证方式和剩余风险。"
        evidence.insert(0, f"{timestamp} 标记完成，保留在活跃任务空间供近期规划参考。")
    elif action == "request_archive_review":
        if task.get("status") != "done":
            raise ValueError("只有已完成任务才能补充完成记录。")
        document["currentState"] = "已完成。"
        evidence.insert(0, f"{timestamp} 已完成任务保持完成状态。")
    elif action == "keep_unarchived":
        if task.get("status") == "archived":
            previous_status = str(task.get("statusBeforeArchive") or "done")
            task["status"] = normalize_task_status_value(previous_status)
            if task["status"] == "archived":
                task["status"] = "done"
            task.pop("archivedAt", None)
            task.pop("statusBeforeArchive", None)
            document["currentState"] = "已取消归档，节点保留在任务树中。"
            evidence.insert(0, f"{timestamp} 取消归档。")
        elif task.get("status") == "done":
            task["status"] = "done"
            document["currentState"] = "用户选择暂不归档，保留在近期完成参考中。"
            evidence.insert(0, f"{timestamp} 用户选择保留已完成任务。")
        else:
            raise ValueError("只有已完成或已归档任务才能取消归档。")
    elif action == "archive":
        if task.get("status") == "archived":
            return normalized
        task["statusBeforeArchive"] = task.get("status") or "ready"
        task["status"] = "archived"
        task["archivedAt"] = timestamp
        document["currentState"] = "已归档；节点仍保留在任务树中，可通过任务树显示设置隐藏。"
        evidence.insert(0, f"{timestamp} 归档节点。")
    else:
        raise ValueError("任务审核动作非法。")

    task["evidenceLog"] = evidence[:30]
    task["updatedAt"] = timestamp
    return normalized

