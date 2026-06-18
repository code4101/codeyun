from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
import time
from contextlib import nullcontext
from pathlib import Path
from typing import Any

from sqlmodel import Session

from backend.core.codex.sessions import build_codex_overview, build_codex_thread_detail
from backend.core.settings import get_settings
from backend.db import engine


UI_LEARNING_TASK_KEY = "ui_design_skill_learning"
UI_LEARNING_QUEUE_NAME = "ui_design_skill_learning"
UI_LEARNING_REPORT_VERSION = 1
UI_LEARNING_DAILY_RUN_TIME = "03:10"
UI_LEARNING_TARGET_SKILL = "D:/home/chenkunze/slns/skills/前端UI规范/SKILL.md"

UI_INCLUDE_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bui\b",
        r"\bux\b",
        r"\bfrontend\b",
        r"\bvue\b",
        r"前端",
        r"页面",
        r"界面",
        r"布局",
        r"审美",
        r"设计",
        r"交互",
        r"卡片",
        r"按钮",
        r"菜单",
        r"侧边栏",
        r"弹窗",
        r"表格",
        r"看起来",
        r"简洁",
        r"优雅",
        r"冗余",
        r"密度",
    )
)
UI_CORRECTION_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"不是",
        r"不对",
        r"别",
        r"不要",
        r"应该",
        r"更",
        r"太.*了",
        r"过度",
        r"冗余",
        r"臃肿",
        r"难看",
        r"不好看",
        r"不够",
        r"重新",
        r"改成",
        r"删掉",
        r"收口",
    )
)
META_LEARNING_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"EvoMind",
        r"自主学习",
        r"学习器",
        r"案例池",
        r"skill\s*patch",
        r"提示词优化",
        r"自发觉",
    )
)


@dataclass(frozen=True)
class UiLearningCase:
    id: str
    thread_id: str
    thread_title: str
    thread_updated_at: float
    project_label: str
    user_seq: int
    user_text: str
    previous_assistant_text: str
    reason: str


def _learning_root() -> Path:
    return get_settings().data_dir / "ai-learning" / "ui-design"


def _checkpoint_path(root: Path | None = None) -> Path:
    return (root or _learning_root()) / "checkpoint.json"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _timestamp_seconds(value: Any) -> float:
    if value is None or value == "":
        return 0.0
    try:
        timestamp = float(value)
    except (TypeError, ValueError):
        return 0.0
    return timestamp / 1000 if abs(timestamp) >= 1_000_000_000_000 else timestamp


def _compact_text(value: Any, *, limit: int = 700) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _matches_any(patterns: tuple[re.Pattern[str], ...] | Any, text: str) -> bool:
    return any(pattern.search(text) for pattern in patterns)


def _looks_like_ui_material(text: str) -> bool:
    return _matches_any(UI_INCLUDE_PATTERNS, text)


def _looks_like_user_correction(text: str) -> bool:
    return _matches_any(UI_CORRECTION_PATTERNS, text)


def _looks_like_meta_learning(text: str) -> bool:
    return _matches_any(META_LEARNING_PATTERNS, text)


def _case_id(thread_id: str, seq: int, text: str) -> str:
    digest = hashlib.sha1(f"{thread_id}:{seq}:{text}".encode("utf-8", errors="ignore")).hexdigest()[:12]
    return f"ui_case_{digest}"


def _iter_threads_for_learning(
    overview: dict[str, Any],
    *,
    checkpoint: dict[str, Any],
    max_threads: int,
) -> list[dict[str, Any]]:
    last_updated_at = float(checkpoint.get("last_thread_updated_at") or 0)
    processed_at_last = set(str(item) for item in checkpoint.get("processed_thread_ids_at_last_updated_at") or [])
    threads = [
        thread
        for group in overview.get("groups") or []
        if isinstance(group, dict)
        for thread in group.get("threads") or []
        if isinstance(thread, dict)
    ]
    selected: list[dict[str, Any]] = []
    for thread in sorted(threads, key=lambda item: (_timestamp_seconds(item.get("updated_at")), str(item.get("id") or ""))):
        updated_at = _timestamp_seconds(thread.get("updated_at"))
        thread_id = str(thread.get("id") or "")
        if updated_at < last_updated_at:
            continue
        if updated_at == last_updated_at and thread_id in processed_at_last:
            continue
        selected.append(thread)
        if len(selected) >= max_threads:
            break
    return selected


def _extract_ui_cases_from_thread(thread: dict[str, Any], messages: list[dict[str, Any]]) -> list[UiLearningCase]:
    cases: list[UiLearningCase] = []
    previous_assistant = ""
    thread_text = " ".join(
        str(thread.get(key) or "")
        for key in ("title", "preview", "project_label", "project_secondary_label", "cwd", "workspace_root")
    )
    thread_has_ui_hint = _looks_like_ui_material(thread_text)
    thread_is_meta = _looks_like_meta_learning(thread_text)

    for message in messages:
        role = str(message.get("role") or "")
        text = str(message.get("text") or "").strip()
        if role == "assistant":
            previous_assistant = text
            continue
        if role != "user" or not text:
            continue

        haystack = f"{thread_text}\n{text}"
        if _looks_like_meta_learning(haystack) and not _looks_like_ui_material(text):
            continue
        if thread_is_meta and not _looks_like_ui_material(text):
            continue
        if not (thread_has_ui_hint or _looks_like_ui_material(text)):
            continue
        if not _looks_like_user_correction(text):
            continue

        seq = int(message.get("seq") or 0)
        reason = "用户对 UI/前端结果给出纠正或审美偏好"
        cases.append(
            UiLearningCase(
                id=_case_id(str(thread.get("id") or ""), seq, text),
                thread_id=str(thread.get("id") or ""),
                thread_title=str(thread.get("title") or "未命名会话"),
                thread_updated_at=_timestamp_seconds(thread.get("updated_at")),
                project_label=str(thread.get("project_label") or ""),
                user_seq=seq,
                user_text=_compact_text(text, limit=900),
                previous_assistant_text=_compact_text(previous_assistant, limit=900),
                reason=reason,
            )
        )
    return cases


def _serialize_case(item: UiLearningCase) -> dict[str, Any]:
    return {
        "id": item.id,
        "source_kind": "raw_codex_session",
        "thread_id": item.thread_id,
        "thread_title": item.thread_title,
        "thread_updated_at": item.thread_updated_at,
        "project_label": item.project_label,
        "user_seq": item.user_seq,
        "user_text": item.user_text,
        "previous_assistant_text": item.previous_assistant_text,
        "reason": item.reason,
    }


def _build_skill_patch_proposal(cases: list[UiLearningCase]) -> str:
    lines = [
        "# UI 自主学习 Skill Patch 建议",
        "",
        f"- 目标 skill：`{UI_LEARNING_TARGET_SKILL}`",
        "- 产物类型：人工审核建议，不自动写入 skill。",
        "- 学习层面：前端 UI 审美、布局密度、控件边界、信息组织。",
        "",
    ]
    if not cases:
        lines.extend(["本次增量扫描没有发现足够明确的 UI 学习案例。", ""])
        return "\n".join(lines)

    lines.extend(
        [
            "## 候选规则",
            "",
            "1. 展示复杂素材、案例、日志或聊天证据时，优先使用可扫描的列表 + 详情结构，不要把所有材料堆成长文本。",
            "2. 前端工具页应优先服务重复使用和快速判断，避免营销式 hero、过度卡片化和无功能装饰。",
            "3. 对高信息密度页面，控件宽度、列宽和文本密度应由内容驱动；允许留白，但不要为了填满区域制造冗余容器。",
            "",
            "## 来源案例",
            "",
        ]
    )
    for index, item in enumerate(cases[:20], start=1):
        lines.extend(
            [
                f"### {index}. {item.thread_title}",
                "",
                f"- 会话：`{item.thread_id}`",
                f"- 消息：`#{item.user_seq}`",
                f"- 项目：{item.project_label or '未记录'}",
                f"- 用户纠正：{item.user_text}",
                "",
            ]
        )
    return "\n".join(lines)


def run_ui_design_learning_once(
    *,
    root_dir: str | None = None,
    max_threads: int = 80,
    report_root: Path | None = None,
    session: Session | None = None,
) -> dict[str, Any]:
    started_at = time.time()
    target_root = report_root or _learning_root()
    checkpoint = _read_json(_checkpoint_path(target_root))
    cases: list[UiLearningCase] = []
    scanned_thread_ids: list[str] = []
    max_seen_updated_at = float(checkpoint.get("last_thread_updated_at") or 0)
    thread_ids_at_max: set[str] = set(str(item) for item in checkpoint.get("processed_thread_ids_at_last_updated_at") or [])

    with (Session(engine) if session is None else nullcontext(session)) as active_session:
        overview = build_codex_overview(root_dir, session=active_session, thread_limit=2000)
        threads = _iter_threads_for_learning(overview, checkpoint=checkpoint, max_threads=max_threads)
        for thread in threads:
            thread_id = str(thread.get("id") or "")
            if not thread_id:
                continue
            scanned_thread_ids.append(thread_id)
            updated_at = _timestamp_seconds(thread.get("updated_at"))
            if updated_at > max_seen_updated_at:
                max_seen_updated_at = updated_at
                thread_ids_at_max = {thread_id}
            elif updated_at == max_seen_updated_at:
                thread_ids_at_max.add(thread_id)
            try:
                detail = build_codex_thread_detail(root_dir, thread_id, session=active_session)
            except Exception:
                continue
            cases.extend(_extract_ui_cases_from_thread(thread, list(detail.get("messages") or [])))

    finished_at = time.time()
    timestamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(started_at))
    report = {
        "version": UI_LEARNING_REPORT_VERSION,
        "task_key": UI_LEARNING_TASK_KEY,
        "source_layer": "raw_codex_session",
        "derived_layer": "ui_design_learning_report",
        "target_skill": UI_LEARNING_TARGET_SKILL,
        "status": "completed",
        "started_at": started_at,
        "finished_at": finished_at,
        "root_dir": root_dir or "",
        "scanned_thread_count": len(scanned_thread_ids),
        "scanned_thread_ids": scanned_thread_ids,
        "case_count": len(cases),
        "cases": [_serialize_case(item) for item in cases],
        "self_consumption_guard": {
            "default_source_filter": "raw_codex_session",
            "excluded_meta_patterns": [pattern.pattern for pattern in META_LEARNING_PATTERNS],
        },
    }
    report_path = target_root / f"{timestamp}-ui-learning-report.json"
    proposal_path = target_root / f"{timestamp}-ui-skill-patch-proposal.md"
    _write_json(report_path, report)
    proposal_path.write_text(_build_skill_patch_proposal(cases), encoding="utf-8")

    next_checkpoint = {
        "version": UI_LEARNING_REPORT_VERSION,
        "last_thread_updated_at": max_seen_updated_at,
        "processed_thread_ids_at_last_updated_at": sorted(thread_ids_at_max),
        "updated_at": finished_at,
        "last_report_path": str(report_path),
        "last_proposal_path": str(proposal_path),
    }
    _write_json(_checkpoint_path(target_root), next_checkpoint)

    return {
        "status": "completed" if scanned_thread_ids else "skipped",
        "reason": "" if scanned_thread_ids else "没有新的 Codex 会话需要学习",
        "scanned_thread_count": len(scanned_thread_ids),
        "case_count": len(cases),
        "report_path": str(report_path),
        "proposal_path": str(proposal_path),
        "checkpoint": next_checkpoint,
    }


def enqueue_ui_design_learning() -> str | None:
    from backend.core.runtime.background_task_queue import background_task_queue

    task_id, _created = background_task_queue.enqueue_once(
        UI_LEARNING_QUEUE_NAME,
        run_ui_design_learning_once,
        metadata={
            "task_key": UI_LEARNING_TASK_KEY,
            "source_layer": "raw_codex_session",
            "derived_layer": "ui_design_learning_report",
        },
        resource_lock="resource:ai-learning",
    )
    return task_id
