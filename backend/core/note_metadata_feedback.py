from __future__ import annotations

from datetime import datetime
import hashlib
import html
import json
from pathlib import Path
import re
import subprocess
import time
from typing import Any, Callable

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlmodel import Session, func, select

from backend.core.ai_chat import (
    CODEX_CLI_DEFAULT_COMMAND,
    CODEX_CLI_DEFAULT_MODEL,
    AiProviderConfig,
    OllamaClientError,
    chat_with_provider,
)
from backend.core.background_task_queue import background_task_queue
from backend.core.note_progress import get_completion_progress_expr, is_note_system_custom_field_key
from backend.core.settings import ROOT_DIR, get_settings
from backend.models import (
    CodexDailySummaryRun,
    CodexDiaryImportRun,
    NoteMetadataFeedback,
    NoteMetadataFeedbackOptimizationRun,
)


NOTE_METADATA_FEEDBACK_FIELDS = (
    "title",
    "note_categories",
    "primary_category",
    "note_form",
    "lifecycle_stage",
    "completion_progress_expr",
    "weight",
    "custom_fields",
)
NOTE_METADATA_FEEDBACK_STATUS_PENDING = "pending"
NOTE_METADATA_FEEDBACK_STATUS_CONSUMED = "consumed"
NOTE_METADATA_FEEDBACK_COALESCE_SECONDS = 10 * 60
NOTE_METADATA_FEEDBACK_TRIGGER_THRESHOLD = 200
NOTE_METADATA_FEEDBACK_CONSUME_LIMIT = 200
NOTE_METADATA_FEEDBACK_COMPRESS_AFTER_SECONDS = 30 * 24 * 60 * 60
NOTE_METADATA_FEEDBACK_CONTENT_SUMMARY_LIMIT = 600
NOTE_METADATA_FEEDBACK_PROVIDER_ID = "note-metadata-feedback-optimizer"
NOTE_METADATA_FEEDBACK_ALLOWED_FILES = (
    "backend/api/notes.py",
    "backend/core/codex_sessions.py",
    "tests/backend/test_note_ai_categorize_api.py",
    "tests/backend/test_codex_diary_import_api.py",
    "tests/backend/test_note_metadata_feedback_api.py",
)
NOTE_METADATA_FEEDBACK_TEST_COMMANDS = (
    "uv run pytest tests/backend/test_note_ai_categorize_api.py tests/backend/test_codex_diary_import_api.py tests/backend/test_note_metadata_feedback_api.py",
)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")

metadata_feedback_scheduler = BackgroundScheduler()


def _safe_json_value(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _get_value(note: Any, field: str, overrides: dict[str, Any] | None = None) -> Any:
    if overrides and field in overrides:
        return overrides[field]
    if isinstance(note, dict):
        return note.get(field)
    return getattr(note, field, None)


def _plain_text_summary(content: Any) -> tuple[str, str, int]:
    raw = str(content or "")
    text = html.unescape(_HTML_TAG_RE.sub(" ", raw))
    text = _WHITESPACE_RE.sub(" ", text).strip()
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest() if text else ""
    if len(text) > NOTE_METADATA_FEEDBACK_CONTENT_SUMMARY_LIMIT:
        text = text[:NOTE_METADATA_FEEDBACK_CONTENT_SUMMARY_LIMIT].rstrip()
    return text, digest, len(raw)


def _normalize_custom_fields(value: Any) -> list[Any] | dict[str, Any]:
    if isinstance(value, list):
        normalized: list[Any] = []
        for item in value:
            if isinstance(item, (list, tuple)) and item:
                key = str(item[0] or "")
                if is_note_system_custom_field_key(key):
                    continue
                normalized.append(_safe_json_value(list(item)))
            elif isinstance(item, dict):
                key = str(item.get("key") or item.get("name") or "")
                if is_note_system_custom_field_key(key):
                    continue
                normalized.append(_safe_json_value(item))
        return normalized
    if isinstance(value, dict):
        return {
            str(key): _safe_json_value(item_value)
            for key, item_value in sorted(value.items(), key=lambda pair: str(pair[0]))
            if not is_note_system_custom_field_key(str(key))
        }
    return []


def build_note_metadata_feedback_snapshot(note: Any, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    custom_fields = _get_value(note, "custom_fields", overrides)
    return {
        "title": str(_get_value(note, "title", overrides) or ""),
        "note_categories": _safe_json_value(_get_value(note, "note_categories", overrides) or []),
        "primary_category": str(_get_value(note, "primary_category", overrides) or ""),
        "note_form": str(_get_value(note, "note_form", overrides) or ""),
        "lifecycle_stage": str(_get_value(note, "lifecycle_stage", overrides) or ""),
        "completion_progress_expr": get_completion_progress_expr(custom_fields) or "",
        "weight": int(_get_value(note, "weight", overrides) or 0),
        "custom_fields": _normalize_custom_fields(custom_fields),
    }


def build_note_metadata_feedback_content_sample(note: Any, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    title = str(_get_value(note, "title", overrides) or "")
    summary, digest, raw_length = _plain_text_summary(_get_value(note, "content", overrides))
    return {
        "title_sample": title[:160],
        "content_summary": summary,
        "content_hash": digest,
        "content_length": raw_length,
    }


def _changed_metadata_fields(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    return [field for field in NOTE_METADATA_FEEDBACK_FIELDS if before.get(field) != after.get(field)]


def _field_signature(before: dict[str, Any], after: dict[str, Any], field_names: list[str]) -> str:
    if before.get("note_form") == "document" or after.get("note_form") == "document":
        return "__document_metadata__"
    return "|".join(sorted(field_names))


def record_note_metadata_feedback(
    session: Session,
    *,
    user_id: int,
    note_id: str,
    before_snapshot: dict[str, Any],
    after_snapshot: dict[str, Any],
    content_sample: dict[str, Any],
    source_kind: str,
    source_ref_id: str | None = None,
    now: float | None = None,
) -> NoteMetadataFeedback | None:
    field_names = _changed_metadata_fields(before_snapshot, after_snapshot)
    if not field_names:
        return None

    now_ts = float(now or time.time())
    signature = _field_signature(before_snapshot, after_snapshot, field_names)
    if signature != "__document_metadata__":
        existing_document_feedback = session.exec(
            select(NoteMetadataFeedback).where(
                NoteMetadataFeedback.user_id == user_id,
                NoteMetadataFeedback.note_id == note_id,
                NoteMetadataFeedback.status == NOTE_METADATA_FEEDBACK_STATUS_PENDING,
                NoteMetadataFeedback.field_signature == "__document_metadata__",
            )
        ).first()
        if existing_document_feedback:
            signature = "__document_metadata__"
    cutoff = 0.0 if signature == "__document_metadata__" else now_ts - NOTE_METADATA_FEEDBACK_COALESCE_SECONDS
    existing = session.exec(
        select(NoteMetadataFeedback)
        .where(
            NoteMetadataFeedback.user_id == user_id,
            NoteMetadataFeedback.note_id == note_id,
            NoteMetadataFeedback.status == NOTE_METADATA_FEEDBACK_STATUS_PENDING,
            NoteMetadataFeedback.field_signature == signature,
            NoteMetadataFeedback.last_event_at >= cutoff,
        )
        .order_by(NoteMetadataFeedback.last_event_at.desc())
    ).first()

    if existing:
        merged_sources = list(dict.fromkeys([*(existing.source_kinds or []), source_kind]))
        existing.source_kind = existing.source_kind or source_kind
        existing.source_kinds = merged_sources
        existing.source_ref_id = source_ref_id or existing.source_ref_id
        existing.field_names = sorted(set([*(existing.field_names or []), *field_names]))
        existing.after_snapshot = _safe_json_value(after_snapshot)
        existing.title_sample = str(content_sample.get("title_sample") or "")
        existing.content_summary = str(content_sample.get("content_summary") or "")
        existing.content_hash = str(content_sample.get("content_hash") or "")
        existing.content_length = int(content_sample.get("content_length") or 0)
        existing.event_count = int(existing.event_count or 1) + 1
        existing.last_event_at = now_ts
        existing.updated_at = now_ts
        session.add(existing)
        return existing

    row = NoteMetadataFeedback(
        user_id=user_id,
        note_id=note_id,
        status=NOTE_METADATA_FEEDBACK_STATUS_PENDING,
        source_kind=source_kind,
        source_kinds=[source_kind],
        source_ref_id=source_ref_id,
        field_signature=signature,
        field_names=field_names,
        before_snapshot=_safe_json_value(before_snapshot),
        after_snapshot=_safe_json_value(after_snapshot),
        title_sample=str(content_sample.get("title_sample") or ""),
        content_summary=str(content_sample.get("content_summary") or ""),
        content_hash=str(content_sample.get("content_hash") or ""),
        content_length=int(content_sample.get("content_length") or 0),
        first_event_at=now_ts,
        last_event_at=now_ts,
        created_at=now_ts,
        updated_at=now_ts,
    )
    session.add(row)
    return row


def record_note_metadata_feedback_for_update(
    session: Session,
    *,
    note: Any,
    updates: dict[str, Any],
    source_kind: str,
    source_ref_id: str | None = None,
    now: float | None = None,
) -> NoteMetadataFeedback | None:
    before_snapshot = build_note_metadata_feedback_snapshot(note)
    after_snapshot = build_note_metadata_feedback_snapshot(note, updates)
    content_sample = build_note_metadata_feedback_content_sample(note, updates)
    user_id = int(_get_value(note, "user_id") or 0)
    note_id = str(_get_value(note, "id") or "")
    if not user_id or not note_id:
        return None
    return record_note_metadata_feedback(
        session,
        user_id=user_id,
        note_id=note_id,
        before_snapshot=before_snapshot,
        after_snapshot=after_snapshot,
        content_sample=content_sample,
        source_kind=source_kind,
        source_ref_id=source_ref_id,
        now=now,
    )


def record_note_metadata_feedback_for_created_note(
    session: Session,
    *,
    note: Any,
    source_kind: str,
    source_ref_id: str | None = None,
    now: float | None = None,
) -> NoteMetadataFeedback | None:
    after_snapshot = build_note_metadata_feedback_snapshot(note)
    before_snapshot = {field: None for field in NOTE_METADATA_FEEDBACK_FIELDS}
    content_sample = build_note_metadata_feedback_content_sample(note)
    return record_note_metadata_feedback(
        session,
        user_id=int(_get_value(note, "user_id") or 0),
        note_id=str(_get_value(note, "id") or ""),
        before_snapshot=before_snapshot,
        after_snapshot=after_snapshot,
        content_sample=content_sample,
        source_kind=source_kind,
        source_ref_id=source_ref_id,
        now=now,
    )


def get_note_metadata_feedback_status(session: Session) -> dict[str, Any]:
    pending_count = session.exec(
        select(func.count()).select_from(NoteMetadataFeedback).where(NoteMetadataFeedback.status == "pending")
    ).one()
    consumed_count = session.exec(
        select(func.count()).select_from(NoteMetadataFeedback).where(NoteMetadataFeedback.status == "consumed")
    ).one()
    compressed_count = session.exec(
        select(func.count()).select_from(NoteMetadataFeedback).where(NoteMetadataFeedback.compressed_at.is_not(None))
    ).one()
    latest_run = session.exec(
        select(NoteMetadataFeedbackOptimizationRun).order_by(NoteMetadataFeedbackOptimizationRun.created_at.desc())
    ).first()
    return {
        "pending_count": int(pending_count or 0),
        "consumed_count": int(consumed_count or 0),
        "compressed_count": int(compressed_count or 0),
        "trigger_threshold": NOTE_METADATA_FEEDBACK_TRIGGER_THRESHOLD,
        "coalesce_seconds": NOTE_METADATA_FEEDBACK_COALESCE_SECONDS,
        "cleanup_retention_days": int(NOTE_METADATA_FEEDBACK_COMPRESS_AFTER_SECONDS / 86400),
        "latest_run": serialize_note_metadata_feedback_optimization_run(latest_run) if latest_run else None,
        "queue": background_task_queue.snapshot(),
    }


def serialize_note_metadata_feedback_optimization_run(run: NoteMetadataFeedbackOptimizationRun | None) -> dict[str, Any] | None:
    if run is None:
        return None
    return {
        "id": run.id,
        "status": run.status,
        "trigger_reason": run.trigger_reason,
        "stage": run.stage,
        "stage_label": run.stage_label,
        "provider": run.provider,
        "model": run.model,
        "sample_count": run.sample_count,
        "consumed_feedback_ids": list(run.consumed_feedback_ids or []),
        "changed_files": list(run.changed_files or []),
        "result_text": run.result_text,
        "test_results": run.test_results or {},
        "error_message": run.error_message,
        "queue_task_id": run.queue_task_id,
        "heartbeat_at": run.heartbeat_at,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "created_at": run.created_at,
        "updated_at": run.updated_at,
    }


def _build_default_feedback_optimizer_provider() -> AiProviderConfig:
    return AiProviderConfig(
        id=NOTE_METADATA_FEEDBACK_PROVIDER_ID,
        label="Codex CLI",
        kind="codex_cli",
        base_url=CODEX_CLI_DEFAULT_COMMAND,
        default_model=CODEX_CLI_DEFAULT_MODEL,
        timeout_seconds=1800,
        api_key="",
        supports_stream=False,
        supports_vision=False,
        requires_api_key=False,
        configured=True,
        models=(CODEX_CLI_DEFAULT_MODEL,),
        is_custom=False,
    )


def _has_active_related_runs(session: Session) -> bool:
    daily = session.exec(
        select(func.count()).select_from(CodexDailySummaryRun).where(CodexDailySummaryRun.status.in_(["pending", "running"]))
    ).one()
    diary = session.exec(
        select(func.count()).select_from(CodexDiaryImportRun).where(CodexDiaryImportRun.status.in_(["pending", "running"]))
    ).one()
    feedback = session.exec(
        select(func.count())
        .select_from(NoteMetadataFeedbackOptimizationRun)
        .where(NoteMetadataFeedbackOptimizationRun.status.in_(["pending", "running"]))
    ).one()
    return bool((daily or 0) or (diary or 0) or (feedback or 0))


def _is_automatic_window(now_dt: datetime | None = None) -> bool:
    current = now_dt or datetime.now()
    return 0 <= current.hour < 6


def _pending_feedback_count(session: Session) -> int:
    value = session.exec(
        select(func.count()).select_from(NoteMetadataFeedback).where(NoteMetadataFeedback.status == NOTE_METADATA_FEEDBACK_STATUS_PENDING)
    ).one()
    return int(value or 0)


def create_note_metadata_feedback_optimization_run(
    session: Session,
    *,
    trigger_reason: str = "manual",
    enqueue: bool = True,
    require_auto_conditions: bool = False,
    now_dt: datetime | None = None,
) -> NoteMetadataFeedbackOptimizationRun | None:
    if require_auto_conditions:
        if not _is_automatic_window(now_dt):
            return None
        if not background_task_queue.is_idle() or _has_active_related_runs(session):
            return None
        if _pending_feedback_count(session) < NOTE_METADATA_FEEDBACK_TRIGGER_THRESHOLD:
            return None

    now_ts = time.time()
    run = NoteMetadataFeedbackOptimizationRun(
        status="pending",
        trigger_reason=trigger_reason,
        stage="queued",
        stage_label="已进入队列",
        provider=NOTE_METADATA_FEEDBACK_PROVIDER_ID,
        model=CODEX_CLI_DEFAULT_MODEL,
        created_at=now_ts,
        updated_at=now_ts,
        heartbeat_at=now_ts,
    )
    session.add(run)
    session.commit()
    session.refresh(run)

    if enqueue:
        queue_task_id = background_task_queue.enqueue(
            "note_metadata_feedback_optimization",
            run_note_metadata_feedback_optimization_worker,
            session.get_bind(),
            run.id,
            metadata={"run_id": run.id, "trigger_reason": trigger_reason},
        )
        run.queue_task_id = queue_task_id
        run.updated_at = time.time()
        session.add(run)
        session.commit()
        session.refresh(run)
    return run


def _select_feedback_samples(session: Session) -> list[NoteMetadataFeedback]:
    return session.exec(
        select(NoteMetadataFeedback)
        .where(NoteMetadataFeedback.status == NOTE_METADATA_FEEDBACK_STATUS_PENDING)
        .order_by(NoteMetadataFeedback.last_event_at)
        .limit(NOTE_METADATA_FEEDBACK_CONSUME_LIMIT)
    ).all()


def _build_optimizer_prompt(samples: list[NoteMetadataFeedback]) -> str:
    payload = {
        "task": "Analyze note metadata correction feedback and directly improve CodeYun source code for AI分类 and Codex diary metadata/title generation.",
        "allowed_files": list(NOTE_METADATA_FEEDBACK_ALLOWED_FILES),
        "rules": [
            "Only edit allowed files.",
            "Prefer small prompt/rule/test improvements grounded in the samples.",
            "Do not change unrelated product behavior.",
            "If no safe improvement is justified, leave files unchanged and explain why.",
        ],
        "samples": [
            {
                "id": item.id,
                "source_kinds": item.source_kinds or [item.source_kind],
                "field_names": item.field_names,
                "before": item.before_snapshot,
                "after": item.after_snapshot,
                "title": item.title_sample,
                "content_summary": item.content_summary,
                "event_count": item.event_count,
            }
            for item in samples
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _file_hash(path: Path) -> str:
    if not path.exists():
        return ""
    return hashlib.sha1(path.read_bytes()).hexdigest()


def _capture_allowed_file_backups() -> dict[str, dict[str, Any]]:
    backups: dict[str, dict[str, Any]] = {}
    for rel_path in NOTE_METADATA_FEEDBACK_ALLOWED_FILES:
        path = ROOT_DIR / rel_path
        backups[rel_path] = {
            "exists": path.exists(),
            "sha1": _file_hash(path),
            "content": path.read_text(encoding="utf-8") if path.exists() else None,
        }
    return backups


def _changed_allowed_files(backups: dict[str, dict[str, Any]]) -> list[str]:
    changed: list[str] = []
    for rel_path, before in backups.items():
        path = ROOT_DIR / rel_path
        if bool(before.get("exists")) != path.exists() or str(before.get("sha1") or "") != _file_hash(path):
            changed.append(rel_path)
    return changed


def _restore_changed_files(backups: dict[str, dict[str, Any]], changed_files: list[str]) -> None:
    for rel_path in changed_files:
        path = ROOT_DIR / rel_path
        backup = backups.get(rel_path) or {}
        if backup.get("exists"):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(str(backup.get("content") or ""), encoding="utf-8")
        elif path.exists():
            path.unlink()


def _run_test_command(command: str) -> dict[str, Any]:
    completed = subprocess.run(
        command,
        cwd=ROOT_DIR,
        shell=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        check=False,
    )
    return {
        "command": command,
        "returncode": completed.returncode,
        "stdout_tail": completed.stdout[-4000:],
        "stderr_tail": completed.stderr[-4000:],
    }


def _consume_feedback_samples(session: Session, samples: list[NoteMetadataFeedback], run_id: str, now_ts: float) -> None:
    for item in samples:
        item.status = NOTE_METADATA_FEEDBACK_STATUS_CONSUMED
        item.consumer_run_id = run_id
        item.consumed_at = now_ts
        item.updated_at = now_ts
        session.add(item)


def cleanup_consumed_note_metadata_feedback(session: Session, *, now: float | None = None) -> int:
    now_ts = float(now or time.time())
    cutoff = now_ts - NOTE_METADATA_FEEDBACK_COMPRESS_AFTER_SECONDS
    rows = session.exec(
        select(NoteMetadataFeedback).where(
            NoteMetadataFeedback.status == NOTE_METADATA_FEEDBACK_STATUS_CONSUMED,
            NoteMetadataFeedback.consumed_at.is_not(None),
            NoteMetadataFeedback.consumed_at < cutoff,
            NoteMetadataFeedback.compressed_at.is_(None),
        )
    ).all()
    for row in rows:
        row.before_snapshot = None
        row.after_snapshot = None
        row.content_summary = ""
        row.compressed_at = now_ts
        row.updated_at = now_ts
        session.add(row)
    return len(rows)


def run_note_metadata_feedback_optimization_worker(
    db_bind: Any,
    run_id: str,
    *,
    chat_func: Callable[..., dict[str, Any]] = chat_with_provider,
    test_command_runner: Callable[[str], dict[str, Any]] = _run_test_command,
) -> None:
    with Session(db_bind) as session:
        run = session.get(NoteMetadataFeedbackOptimizationRun, run_id)
        if run is None:
            return
        now_ts = time.time()
        run.status = "running"
        run.stage = "sampling"
        run.stage_label = "读取反馈样本"
        run.started_at = now_ts
        run.heartbeat_at = now_ts
        run.updated_at = now_ts
        session.add(run)
        session.commit()

        samples = _select_feedback_samples(session)
        if not samples:
            run.status = "skipped"
            run.stage = "empty"
            run.stage_label = "没有待处理反馈"
            run.finished_at = time.time()
            run.updated_at = run.finished_at
            run.heartbeat_at = run.finished_at
            session.add(run)
            session.commit()
            return

        run.sample_count = len(samples)
        run.stage = "calling_codex"
        run.stage_label = "调用 Codex CLI 分析反馈"
        run.heartbeat_at = time.time()
        run.updated_at = run.heartbeat_at
        session.add(run)
        session.commit()

        backups = _capture_allowed_file_backups()
        run.backup_json = {
            rel_path: {"exists": data.get("exists"), "sha1": data.get("sha1")}
            for rel_path, data in backups.items()
        }
        session.add(run)
        session.commit()

        provider = _build_default_feedback_optimizer_provider()
        try:
            response = chat_func(
                provider_id=provider.id,
                model=provider.default_model,
                system_prompt=(
                    "你是 CodeYun 的后台代码优化代理。你只能根据输入样本改进 AI分类 和 Codex 日记生成的标题/元标签准确性。"
                    "允许直接修改工作区源码，但只能改 allowed_files。"
                ),
                messages=[{"role": "user", "content": _build_optimizer_prompt(samples)}],
                timeout_seconds=provider.timeout_seconds,
                extra_providers=(provider,),
            )
        except OllamaClientError as exc:
            run.status = "skipped"
            run.stage = "codex_unavailable"
            run.stage_label = "Codex CLI 不可用，已跳过"
            run.error_message = str(exc)
            run.finished_at = time.time()
            run.updated_at = run.finished_at
            run.heartbeat_at = run.finished_at
            session.add(run)
            session.commit()
            return
        except Exception as exc:
            run.status = "skipped"
            run.stage = "codex_failed"
            run.stage_label = "Codex CLI 异常，已跳过"
            run.error_message = str(exc)
            run.finished_at = time.time()
            run.updated_at = run.finished_at
            run.heartbeat_at = run.finished_at
            session.add(run)
            session.commit()
            return

        run.result_text = str(response.get("content") or "")
        run.model = str(response.get("model") or provider.default_model)
        changed_files = _changed_allowed_files(backups)
        run.changed_files = changed_files

        test_results: list[dict[str, Any]] = []
        if changed_files:
            run.stage = "testing"
            run.stage_label = "运行定向测试"
            run.heartbeat_at = time.time()
            run.updated_at = run.heartbeat_at
            session.add(run)
            session.commit()
            for command in NOTE_METADATA_FEEDBACK_TEST_COMMANDS:
                result = test_command_runner(command)
                test_results.append(result)
                if int(result.get("returncode") or 0) != 0:
                    _restore_changed_files(backups, changed_files)
                    run.status = "failed"
                    run.stage = "tests_failed"
                    run.stage_label = "测试失败，已恢复本轮改动"
                    run.test_results = {"items": test_results, "restored": True}
                    run.error_message = f"测试失败：{command}"
                    run.finished_at = time.time()
                    run.updated_at = run.finished_at
                    run.heartbeat_at = run.finished_at
                    session.add(run)
                    session.commit()
                    return

        now_ts = time.time()
        _consume_feedback_samples(session, samples, run.id, now_ts)
        cleanup_count = cleanup_consumed_note_metadata_feedback(session, now=now_ts)
        run.consumed_feedback_ids = [item.id for item in samples]
        run.status = "completed"
        run.stage = "completed"
        run.stage_label = f"已消费 {len(samples)} 条反馈"
        run.test_results = {"items": test_results, "cleanup_count": cleanup_count}
        run.finished_at = now_ts
        run.updated_at = now_ts
        run.heartbeat_at = now_ts
        session.add(run)
        session.commit()


def maybe_enqueue_note_metadata_feedback_optimization() -> None:
    from backend.db import engine

    with Session(engine) as session:
        create_note_metadata_feedback_optimization_run(
            session,
            trigger_reason="auto_threshold",
            enqueue=True,
            require_auto_conditions=True,
        )


def init_note_metadata_feedback_scheduler() -> None:
    if get_settings().is_test:
        return
        
    from backend.db import engine
    from backend.models import AppSetting
    from sqlmodel import Session
    with Session(engine) as session:
        row = session.get(AppSetting, "background_task.note_metadata_feedback_optimization.enabled")
        enabled = bool(row.value.get("enabled", False)) if row and isinstance(row.value, dict) else False
        
    if not enabled:
        return
        
    if not metadata_feedback_scheduler.running:
        metadata_feedback_scheduler.start()
    metadata_feedback_scheduler.add_job(
        maybe_enqueue_note_metadata_feedback_optimization,
        CronTrigger.from_crontab("5 0 * * *"),
        id="note_metadata_feedback_optimization",
        replace_existing=True,
        max_instances=1,
    )
    print("Note metadata feedback optimization scheduled: 5 0 * * *")


def shutdown_note_metadata_feedback_scheduler() -> None:
    if metadata_feedback_scheduler.running:
        metadata_feedback_scheduler.shutdown(wait=False)
