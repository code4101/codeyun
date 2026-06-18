from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
import time
from typing import Any, Callable

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlmodel import Session, select

from backend.core.ai.chat import AiProviderConfig
from backend.core.ai.git_commit import AiGitCommitError, resolve_ai_git_commit_runtime_config as resolve_ai_runtime_config
from backend.core.ai.git_reduction import generate_ai_git_commit_draft_hierarchical
from backend.core.ai.git_repos import list_user_ai_git_repos
from backend.core.runtime.background_task_queue import background_task_queue
from backend.core.ai.git_tools import GitToolError, create_git_commit, inspect_git_repository
from backend.core.settings import ROOT_DIR, get_settings
from backend.models import AppSetting, AutoGitCommitRun, User


AUTO_GIT_COMMIT_TASK_KEY = "auto_git_commit"
AUTO_GIT_COMMIT_REPO_KEYS = ("pyxllib", "xlproject", "codeyun")
AUTO_GIT_COMMIT_CRON = "15 0 * * *"
AUTO_GIT_COMMIT_SCHEDULE_SETTING_KEY = f"background_task.{AUTO_GIT_COMMIT_TASK_KEY}.schedule"
AUTO_GIT_COMMIT_BRANCH_FACTOR = 10
AUTO_GIT_COMMIT_CHANGED_PATH_LIMIT = 80
AUTO_GIT_COMMIT_CRON_LOOKBACK_DAYS = 32
AUTO_GIT_COMMIT_STALE_HEARTBEAT_SECONDS = 2700
AUTO_GIT_LIGHTWEIGHT_DRAFT_REPO_KEYS = ("codeyun",)

auto_git_commit_scheduler = BackgroundScheduler()


@dataclass(frozen=True)
class AutoGitCommitCandidate:
    user_id: int
    username: str
    name: str
    cwd: str
    entry_id: str = ""


def _auto_git_cron_trigger() -> CronTrigger:
    return CronTrigger.from_crontab(AUTO_GIT_COMMIT_CRON)


def _coerce_cron_datetime(value: datetime, trigger: CronTrigger | None = None) -> datetime:
    cron_trigger = trigger or _auto_git_cron_trigger()
    if value.tzinfo is None:
        return value.replace(tzinfo=cron_trigger.timezone)
    return value.astimezone(cron_trigger.timezone)


def _auto_git_now(now: datetime | None = None, trigger: CronTrigger | None = None) -> datetime:
    cron_trigger = trigger or _auto_git_cron_trigger()
    if now is not None:
        return _coerce_cron_datetime(now, cron_trigger).replace(microsecond=0)
    return datetime.now(cron_trigger.timezone).replace(microsecond=0)


def _parse_schedule_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return _coerce_cron_datetime(datetime.fromisoformat(text)).replace(microsecond=0)
    except ValueError:
        return None


def _format_schedule_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    return _coerce_cron_datetime(value).replace(microsecond=0).isoformat()


def _next_future_cron_fire(now: datetime | None = None) -> datetime:
    trigger = _auto_git_cron_trigger()
    current = _auto_git_now(now, trigger)
    next_fire = trigger.get_next_fire_time(None, current)
    if next_fire is None:
        raise RuntimeError(f"无法计算 GitHub 项目自动提交的下次运行时间：{AUTO_GIT_COMMIT_CRON}")
    next_fire = _coerce_cron_datetime(next_fire, trigger).replace(microsecond=0)
    if next_fire <= current:
        next_fire = trigger.get_next_fire_time(next_fire, current + timedelta(seconds=1))
    if next_fire is None:
        raise RuntimeError(f"无法计算 GitHub 项目自动提交的下次运行时间：{AUTO_GIT_COMMIT_CRON}")
    return _coerce_cron_datetime(next_fire, trigger).replace(microsecond=0)


def _latest_cron_fire_at_or_before(now: datetime | None = None) -> datetime | None:
    trigger = _auto_git_cron_trigger()
    current = _auto_git_now(now, trigger)
    cursor = current - timedelta(days=AUTO_GIT_COMMIT_CRON_LOOKBACK_DAYS)
    fire = trigger.get_next_fire_time(None, cursor)
    latest: datetime | None = None
    guard = 0
    while fire is not None and guard < 5000:
        fire = _coerce_cron_datetime(fire, trigger).replace(microsecond=0)
        if fire > current:
            break
        latest = fire
        fire = trigger.get_next_fire_time(fire, fire + timedelta(seconds=1))
        guard += 1
    return latest


def _load_auto_git_schedule_next_run_at(session: Session) -> datetime | None:
    row = session.get(AppSetting, AUTO_GIT_COMMIT_SCHEDULE_SETTING_KEY)
    if row is None or not isinstance(row.value, dict):
        return None
    if row.value.get("cron") != AUTO_GIT_COMMIT_CRON:
        return None
    return _parse_schedule_datetime(row.value.get("next_run_at"))


def _save_auto_git_schedule_next_run_at(session: Session, next_run_at: datetime) -> None:
    row = session.get(AppSetting, AUTO_GIT_COMMIT_SCHEDULE_SETTING_KEY)
    if row is None:
        row = AppSetting(key=AUTO_GIT_COMMIT_SCHEDULE_SETTING_KEY)
    row.value = {
        "cron": AUTO_GIT_COMMIT_CRON,
        "next_run_at": _format_schedule_datetime(next_run_at),
    }
    row.updated_at = time.time()
    session.add(row)
    session.commit()


def _has_any_auto_git_run(session: Session) -> bool:
    return bool(session.exec(select(AutoGitCommitRun.id).limit(1)).first())


def _has_auto_git_run_started_since(session: Session, since_at: datetime) -> bool:
    since_ts = _coerce_cron_datetime(since_at).timestamp()
    return bool(
        session.exec(
            select(AutoGitCommitRun.id)
            .where(AutoGitCommitRun.created_at >= since_ts)
            .limit(1)
        ).first()
    )


def _infer_initial_auto_git_next_run_at(session: Session, now: datetime | None = None) -> datetime:
    latest_due = _latest_cron_fire_at_or_before(now)
    if (
        latest_due is not None
        and _has_any_auto_git_run(session)
        and not _has_auto_git_run_started_since(session, latest_due)
    ):
        return latest_due
    return _next_future_cron_fire(now)


def _ensure_auto_git_schedule_next_run_at(session: Session, now: datetime | None = None) -> datetime:
    next_run_at = _load_auto_git_schedule_next_run_at(session)
    if next_run_at is None:
        next_run_at = _infer_initial_auto_git_next_run_at(session, now)
        _save_auto_git_schedule_next_run_at(session, next_run_at)
    return next_run_at


def _advance_auto_git_schedule_next_run_at(session: Session, now: datetime | None = None) -> datetime:
    next_run_at = _next_future_cron_fire(now)
    _save_auto_git_schedule_next_run_at(session, next_run_at)
    return next_run_at


def mark_auto_git_schedule_consumed_if_due(session: Session, now: datetime | None = None) -> bool:
    next_run_at = _load_auto_git_schedule_next_run_at(session)
    if next_run_at is None:
        return False
    current = _auto_git_now(now)
    if next_run_at > current:
        return False
    _advance_auto_git_schedule_next_run_at(session, current)
    return True


def _repo_basename(path_text: str) -> str:
    normalized = (path_text or "").strip().replace("\\", "/").rstrip("/")
    if not normalized:
        return ""
    return normalized.rsplit("/", 1)[-1].strip()


def _normalize_repo_key(value: str) -> str:
    return (value or "").strip().lower()


def _match_auto_repo_key(repo: dict[str, Any]) -> str | None:
    candidates = [
        str(repo.get("name") or ""),
        _repo_basename(str(repo.get("cwd") or "")),
    ]
    for value in candidates:
        normalized = _normalize_repo_key(value)
        if normalized in AUTO_GIT_COMMIT_REPO_KEYS:
            return normalized
    return None


def _sort_saved_repos(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        items,
        key=lambda item: (
            int(item.get("order_index") or 0),
            str(item.get("name") or ""),
            str(item.get("cwd") or ""),
        ),
    )


def _dedupe_candidates(candidates: list[AutoGitCommitCandidate]) -> list[AutoGitCommitCandidate]:
    results: list[AutoGitCommitCandidate] = []
    seen_paths: set[str] = set()
    seen_names: set[str] = set()
    for candidate in candidates:
        cwd_key = candidate.cwd.replace("\\", "/").rstrip("/").casefold()
        name_key = candidate.name.casefold()
        if not cwd_key or cwd_key in seen_paths or name_key in seen_names:
            continue
        seen_paths.add(cwd_key)
        seen_names.add(name_key)
        results.append(candidate)
    return results


def _fallback_candidates_for_user(user: User | None) -> list[AutoGitCommitCandidate]:
    if user is None or user.id is None:
        return []
    slns_dir = ROOT_DIR.parent
    candidates: list[AutoGitCommitCandidate] = []
    for repo_name in AUTO_GIT_COMMIT_REPO_KEYS:
        cwd = slns_dir / repo_name
        if not cwd.exists():
            continue
        candidates.append(
            AutoGitCommitCandidate(
                user_id=int(user.id),
                username=user.username,
                name=repo_name,
                cwd=str(cwd),
                entry_id="",
            )
        )
    return candidates


def select_auto_git_commit_candidates(session: Session) -> list[AutoGitCommitCandidate]:
    users = session.exec(
        select(User)
        .where(User.is_active == True)  # noqa: E712
        .order_by(User.id)
    ).all()
    candidates: list[AutoGitCommitCandidate] = []
    for user in users:
        if user.id is None:
            continue
        saved = list_user_ai_git_repos(session, int(user.id))
        matched_names: set[str] = set()
        for repo in _sort_saved_repos(list(saved.get("items") or [])):
            repo_key = _match_auto_repo_key(repo)
            if repo_key is None or repo_key in matched_names:
                continue
            cwd = str(repo.get("cwd") or "").strip()
            if not cwd:
                continue
            matched_names.add(repo_key)
            candidates.append(
                AutoGitCommitCandidate(
                    user_id=int(user.id),
                    username=user.username,
                    name=repo_key,
                    cwd=cwd,
                    entry_id=str(repo.get("entry_id") or ""),
                )
            )

    deduped = _dedupe_candidates(candidates)
    if deduped:
        return deduped
    return _fallback_candidates_for_user(users[0] if users else None)


def serialize_auto_git_commit_run(run: AutoGitCommitRun | None) -> dict[str, Any] | None:
    if run is None:
        return None
    return {
        "id": run.id,
        "status": run.status,
        "trigger_reason": run.trigger_reason,
        "run_date": run.run_date,
        "stage": run.stage,
        "stage_label": run.stage_label,
        "repo_count": run.repo_count,
        "changed_repo_count": run.changed_repo_count,
        "committed_repo_count": run.committed_repo_count,
        "skipped_repo_count": run.skipped_repo_count,
        "failed_repo_count": run.failed_repo_count,
        "result": run.result_json or {},
        "error_message": run.error_message,
        "queue_task_id": run.queue_task_id,
        "heartbeat_at": run.heartbeat_at,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "created_at": run.created_at,
        "updated_at": run.updated_at,
    }


def _queue_has_active_auto_git_task(queue_snapshot: dict[str, Any] | None) -> bool:
    if not isinstance(queue_snapshot, dict):
        return False
    running = queue_snapshot.get("running")
    if isinstance(running, dict) and running.get("name") == "auto_git_commit":
        return True
    return any(
        isinstance(item, dict) and item.get("name") == "auto_git_commit"
        for item in queue_snapshot.get("pending") or []
    )


def mark_stale_auto_git_commit_runs(
    session: Session,
    *,
    now_ts: float | None = None,
    queue_snapshot: dict[str, Any] | None = None,
) -> int:
    if _queue_has_active_auto_git_task(queue_snapshot):
        return 0

    current_ts = float(now_ts if now_ts is not None else time.time())
    stale_before = current_ts - AUTO_GIT_COMMIT_STALE_HEARTBEAT_SECONDS
    stale_minutes = int(AUTO_GIT_COMMIT_STALE_HEARTBEAT_SECONDS // 60)
    runs = session.exec(
        select(AutoGitCommitRun)
        .where(AutoGitCommitRun.status.in_(["pending", "running"]))
        .order_by(AutoGitCommitRun.created_at.asc())
    ).all()
    changed_count = 0
    for run in runs:
        heartbeat = run.heartbeat_at or run.updated_at or run.started_at or run.created_at
        if heartbeat is None or float(heartbeat) > stale_before:
            continue
        run.status = "failed"
        run.stage = "stale"
        run.stage_label = "任务心跳超时"
        run.error_message = (
            f"后台任务心跳超过 {stale_minutes} 分钟未更新，且当前执行队列中没有对应任务；"
            "通常是服务重启、进程中断或 AI 提交总结调用被外部终止。"
        )
        run.finished_at = current_ts
        run.heartbeat_at = current_ts
        run.updated_at = current_ts
        session.add(run)
        changed_count += 1

    if changed_count:
        session.commit()
    return changed_count


def get_auto_git_commit_status(session: Session) -> dict[str, Any]:
    queue = background_task_queue.snapshot()
    mark_stale_auto_git_commit_runs(session, queue_snapshot=queue)
    latest_run = session.exec(
        select(AutoGitCommitRun).order_by(AutoGitCommitRun.created_at.desc())
    ).first()
    active_run = session.exec(
        select(AutoGitCommitRun)
        .where(AutoGitCommitRun.status.in_(["pending", "running"]))
        .order_by(AutoGitCommitRun.created_at.desc())
    ).first()
    next_run_at = _load_auto_git_schedule_next_run_at(session)
    return {
        "repo_keys": list(AUTO_GIT_COMMIT_REPO_KEYS),
        "cron": AUTO_GIT_COMMIT_CRON,
        "next_run_at": _format_schedule_datetime(next_run_at),
        "latest_run": serialize_auto_git_commit_run(latest_run),
        "active_run": serialize_auto_git_commit_run(active_run),
        "queue": queue,
    }


def create_auto_git_commit_run(
    session: Session,
    *,
    trigger_reason: str = "manual",
    enqueue: bool = True,
) -> AutoGitCommitRun:
    now_ts = time.time()
    run = AutoGitCommitRun(
        status="pending",
        trigger_reason=trigger_reason,
        run_date=date.today().isoformat(),
        stage="queued",
        stage_label="已进入队列",
        created_at=now_ts,
        updated_at=now_ts,
        heartbeat_at=now_ts,
    )
    session.add(run)
    session.commit()
    session.refresh(run)

    if enqueue:
        queue_task_id = background_task_queue.enqueue(
            "auto_git_commit",
            run_auto_git_commit_worker,
            session.get_bind(),
            run.id,
            metadata={"run_id": run.id, "trigger_reason": trigger_reason},
            raise_on_failure=True,
        )
        run.queue_task_id = queue_task_id
        run.updated_at = time.time()
        session.add(run)
        session.commit()
        session.refresh(run)
    return run


def _has_active_auto_git_commit_run(session: Session) -> bool:
    return bool(
        session.exec(
            select(AutoGitCommitRun.id)
            .where(AutoGitCommitRun.status.in_(["pending", "running"]))
            .limit(1)
        ).first()
    )


def _changed_paths_from_inspect(inspect_payload: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    for item in inspect_payload.get("changed_files") or []:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "").strip()
        if path:
            paths.append(path)
        if len(paths) >= AUTO_GIT_COMMIT_CHANGED_PATH_LIMIT:
            break
    return paths


def _update_result_from_inspect(result: dict[str, Any], inspect_payload: dict[str, Any]) -> None:
    changed_files = list(inspect_payload.get("changed_files") or [])
    result.update(
        {
            "repo_root": str(inspect_payload.get("repo_root") or ""),
            "branch": str(inspect_payload.get("branch") or ""),
            "changed_file_count": len(changed_files),
            "changed_paths": _changed_paths_from_inspect(inspect_payload),
            "has_changes": not bool(inspect_payload.get("clean")),
        }
    )


def _auto_git_summary_only_reason(candidate: AutoGitCommitCandidate) -> str:
    return f"{candidate.name} 自动提交只生成提交信息，不执行提交前自动优化"


def _build_skipped_pre_commit_review(reason: str) -> dict[str, str]:
    return {
        "status": "skipped",
        "reason": reason,
        "summary": reason,
    }


def _auto_git_processing_stage_label(candidate: AutoGitCommitCandidate) -> str:
    return f"检查/提交 {candidate.name}"


def _uses_lightweight_draft(candidate: AutoGitCommitCandidate) -> bool:
    return _normalize_repo_key(candidate.name) in AUTO_GIT_LIGHTWEIGHT_DRAFT_REPO_KEYS


def _format_auto_git_diff_stat(value: Any, *, line_limit: int = AUTO_GIT_COMMIT_CHANGED_PATH_LIMIT) -> str:
    lines = [line.rstrip() for line in str(value or "").splitlines() if line.strip()]
    if len(lines) <= line_limit:
        return "\n".join(lines)
    omitted_count = len(lines) - line_limit
    return "\n".join([*lines[:line_limit], f"... 还有 {omitted_count} 行 diff stat 未列出"])


def _format_auto_git_split_groups(inspect_payload: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for item in inspect_payload.get("suggested_split_groups") or []:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip()
        file_count = int(item.get("file_count") or 0)
        sample_paths = [
            str(path).strip()
            for path in (item.get("sample_paths") or [])
            if str(path).strip()
        ]
        if not label and not sample_paths:
            continue
        sample_text = f"；例如：{', '.join(sample_paths)}" if sample_paths else ""
        lines.append(f"- {label or '未分类'}：{file_count} 个文件{sample_text}")
    return lines


def _build_lightweight_reduction_input(
    candidate: AutoGitCommitCandidate,
    inspect_payload: dict[str, Any],
) -> dict[str, Any]:
    changed_file_count = len(inspect_payload.get("changed_files") or [])
    added_line_count = int(inspect_payload.get("added_line_count") or 0)
    deleted_line_count = int(inspect_payload.get("deleted_line_count") or 0)
    changed_paths = _changed_paths_from_inspect(inspect_payload)
    omitted_path_count = max(0, changed_file_count - len(changed_paths))
    path_lines = [f"- {path}" for path in changed_paths]
    if omitted_path_count:
        path_lines.append(f"- ... 还有 {omitted_path_count} 个文件未列出")

    status_lines = [
        str(line).strip()
        for line in inspect_payload.get("status_lines") or []
        if str(line).strip()
    ]
    if len(status_lines) > AUTO_GIT_COMMIT_CHANGED_PATH_LIMIT:
        omitted_status_count = len(status_lines) - AUTO_GIT_COMMIT_CHANGED_PATH_LIMIT
        status_lines = [
            *status_lines[:AUTO_GIT_COMMIT_CHANGED_PATH_LIMIT],
            f"... 还有 {omitted_status_count} 行状态未列出",
        ]

    content_sections = [
        "仓库轻量提交摘要输入",
        "",
        "仓库信息：",
        f"- 名称：{candidate.name}",
        f"- 路径：{candidate.cwd}",
        f"- 分支：{inspect_payload.get('branch') or ''}",
        f"- 变更文件数：{changed_file_count}",
        f"- 估算变更行数：+{added_line_count}/-{deleted_line_count}",
        "",
        "工作区状态：",
        *(f"- {line}" for line in status_lines[:AUTO_GIT_COMMIT_CHANGED_PATH_LIMIT]),
        "",
        "变更文件：",
        *(path_lines or ["- (未列出)"]),
        "",
        "未暂存 diff 统计：",
        _format_auto_git_diff_stat(inspect_payload.get("diff_stat")) or "(无)",
        "",
        "已暂存 diff 统计：",
        _format_auto_git_diff_stat(inspect_payload.get("staged_diff_stat")) or "(无)",
    ]
    group_lines = _format_auto_git_split_groups(inspect_payload)
    if group_lines:
        content_sections.extend(["", "路径分组概览：", *group_lines])
    if bool(inspect_payload.get("split_recommended")):
        content_sections.extend(["", "规模提示：", str(inspect_payload.get("split_reason") or "建议拆分提交")])
    content_sections.extend(
        [
            "",
            "生成要求：",
            "- 只根据工作区状态、路径和 diff 统计生成提交信息。",
            "- 不要展开或臆测具体代码实现细节。",
            "- 如果主题混杂，标题要概括主要业务范围或整体维护性质，不要使用固定 checkpoint 占位标题。",
        ]
    )

    return {
        **inspect_payload,
        "source_units": [
            {
                "unit_id": "lightweight_git_summary",
                "path": "(lightweight-summary)",
                "group": "(lightweight)",
                "content": "\n".join(content_sections).strip(),
                "truncated": omitted_path_count > 0,
            }
        ],
        "source_unit_count": 1,
        "source_unit_truncated_count": int(omitted_path_count > 0),
        "lightweight": True,
    }

def _sync_run(session: Session, run: AutoGitCommitRun, results: list[dict[str, Any]]) -> None:
    run.repo_count = len(results)
    run.changed_repo_count = sum(1 for item in results if bool(item.get("has_changes")))
    run.committed_repo_count = sum(1 for item in results if item.get("status") == "committed")
    run.skipped_repo_count = sum(1 for item in results if item.get("status") in {"clean", "skipped"})
    run.failed_repo_count = sum(1 for item in results if item.get("status") == "failed")
    run.result_json = {"repos": results}
    run.heartbeat_at = time.time()
    run.updated_at = run.heartbeat_at
    session.add(run)
    session.commit()


def _format_auto_git_completed_stage_label(run: AutoGitCommitRun) -> str:
    parts: list[str] = []
    if run.committed_repo_count:
        parts.append(f"已提交 {run.committed_repo_count} 个仓库")
    if run.failed_repo_count:
        parts.append(f"{run.failed_repo_count} 个仓库失败")
    if not parts:
        parts.append("未提交仓库")
    return "，".join(parts)


def _normalize_auto_gitignore_pattern(value: Any) -> str:
    pattern = str(value or "").strip().replace("\\", "/")
    if not pattern or "\n" in pattern or "\r" in pattern:
        return ""
    if pattern in {".", "..", "/"} or pattern.startswith("!"):
        return ""
    return pattern


def _auto_gitignore_compare_key(pattern: str) -> str:
    return pattern.strip().replace("\\", "/").lstrip("/").casefold()


def _collect_auto_gitignore_patterns(precheck: dict[str, Any]) -> list[str]:
    patterns: list[str] = []
    seen: set[str] = set()
    for issue in precheck.get("issues") or []:
        if not isinstance(issue, dict):
            continue
        if issue.get("issue_type") != "ignore_candidate" or not bool(issue.get("blocking")):
            continue
        pattern = _normalize_auto_gitignore_pattern(issue.get("suggestion"))
        key = _auto_gitignore_compare_key(pattern)
        if not pattern or key in seen:
            continue
        seen.add(key)
        patterns.append(pattern)
    return patterns


def _append_auto_gitignore_patterns(repo_root: Path, patterns: list[str]) -> list[str]:
    if not patterns:
        return []

    gitignore_path = repo_root / ".gitignore"
    try:
        existing_text = gitignore_path.read_text(encoding="utf-8") if gitignore_path.exists() else ""
    except OSError as exc:
        raise GitToolError(f"自动更新 .gitignore 失败：{exc}") from exc

    existing_keys = {
        _auto_gitignore_compare_key(line.split("#", 1)[0])
        for line in existing_text.splitlines()
        if line.split("#", 1)[0].strip()
    }
    additions = [
        pattern
        for pattern in patterns
        if _auto_gitignore_compare_key(pattern) not in existing_keys
    ]
    if not additions:
        return []

    next_text = existing_text
    if next_text and not next_text.endswith("\n"):
        next_text += "\n"
    if next_text and not next_text.endswith("\n\n"):
        next_text += "\n"
    next_text += "# Auto Git ignored local artifacts\n"
    next_text += "\n".join(additions)
    next_text += "\n"
    try:
        gitignore_path.write_text(next_text, encoding="utf-8", newline="\n")
    except OSError as exc:
        raise GitToolError(f"自动更新 .gitignore 失败：{exc}") from exc
    return additions


def _apply_auto_gitignore_suggestions(inspect_payload: dict[str, Any]) -> dict[str, Any] | None:
    precheck = inspect_payload.get("precheck") if isinstance(inspect_payload.get("precheck"), dict) else {}
    patterns = _collect_auto_gitignore_patterns(precheck)
    if not patterns:
        return None

    repo_root = Path(str(inspect_payload.get("repo_root") or ""))
    if not repo_root.exists():
        return None
    additions = _append_auto_gitignore_patterns(repo_root, patterns)
    return {
        "status": "updated" if additions else "already_ignored",
        "patterns": additions or patterns,
    }


def _refresh_after_auto_gitignore(
    candidate: AutoGitCommitCandidate,
    result: dict[str, Any],
    inspect_payload: dict[str, Any],
    inspect_func: Callable[[str], dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    precheck = inspect_payload.get("precheck") if isinstance(inspect_payload.get("precheck"), dict) else {}
    gitignore_update = _apply_auto_gitignore_suggestions(inspect_payload)
    if gitignore_update:
        result["auto_gitignore"] = gitignore_update
        inspect_payload = inspect_func(candidate.cwd)
        _update_result_from_inspect(result, inspect_payload)
        precheck = inspect_payload.get("precheck") if isinstance(inspect_payload.get("precheck"), dict) else {}
    return inspect_payload, precheck


def _run_one_repo(
    session: Session,
    candidate: AutoGitCommitCandidate,
    *,
    inspect_func: Callable[[str], dict[str, Any]],
    draft_generator: Callable[..., dict[str, Any]],
    commit_func: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "name": candidate.name,
        "cwd": candidate.cwd,
        "entry_id": candidate.entry_id,
        "user_id": candidate.user_id,
        "username": candidate.username,
        "status": "pending",
        "has_changes": False,
    }
    if not Path(candidate.cwd).exists():
        result.update({"status": "skipped", "error_message": "项目目录不存在"})
        return result

    inspect_payload = inspect_func(candidate.cwd)
    _update_result_from_inspect(result, inspect_payload)
    if bool(inspect_payload.get("clean")):
        result["status"] = "clean"
        return result

    inspect_payload, precheck = _refresh_after_auto_gitignore(candidate, result, inspect_payload, inspect_func)
    if bool(precheck.get("has_blocking_issues")):
        result.update(
            {
                "status": "failed",
                "error_message": f"提交前预检未通过，发现 {int(precheck.get('blocking_issue_count') or 0)} 条阻断项",
            }
        )
        return result

    result["pre_commit_review"] = _build_skipped_pre_commit_review(
        _auto_git_summary_only_reason(candidate)
    )

    user = session.get(User, candidate.user_id)
    if user is None:
        result.update({"status": "failed", "error_message": "关联用户不存在"})
        return result

    runtime_config = resolve_ai_runtime_config(
        session=session,
        current_user=user,
        provider=None,
        base_url=None,
        api_key=None,
        model=None,
    )
    if len(runtime_config) == 4:
        provider_id, base_url, api_key, extra_providers = runtime_config
        model = None
    else:
        provider_id, base_url, api_key, model, extra_providers = runtime_config

    draft_kwargs: dict[str, Any] = {
        "cwd": candidate.cwd,
        "provider_id": provider_id,
        "base_url": base_url,
        "api_key": api_key,
        "model": model,
        "style": "summary",
        "include_body": True,
        "extra_providers": extra_providers,
        "branch_factor": AUTO_GIT_COMMIT_BRANCH_FACTOR,
    }
    if _uses_lightweight_draft(candidate):
        draft_kwargs["reduction_input"] = _build_lightweight_reduction_input(candidate, inspect_payload)
    draft = draft_generator(**draft_kwargs)

    subject = str(draft.get("subject") or "").strip()
    if not subject:
        raise AiGitCommitError("AI 没有生成有效的提交标题")
    body = [str(item) for item in draft.get("body") or []]
    commit_payload = commit_func(
        candidate.cwd,
        subject=subject,
        body=body,
        add_all=True,
    )
    result.update(
        {
            "status": "committed",
            "provider": provider_id,
            "model": str(draft.get("model") or model or provider_id),
            "subject": subject,
            "body": body,
            "needs_split": bool(draft.get("needs_split")),
            "split_reason": str(draft.get("reason") or ""),
            "commit_strategy": str(
                draft.get("strategy")
                or ("lightweight_ai" if _uses_lightweight_draft(candidate) else "ai")
            ),
            "commit": commit_payload,
        }
    )
    return result


def run_auto_git_commit_worker(
    db_bind: Any,
    run_id: str,
    *,
    candidate_selector: Callable[[Session], list[AutoGitCommitCandidate]] = select_auto_git_commit_candidates,
    inspect_func: Callable[[str], dict[str, Any]] = inspect_git_repository,
    draft_generator: Callable[..., dict[str, Any]] = generate_ai_git_commit_draft_hierarchical,
    commit_func: Callable[..., dict[str, Any]] = create_git_commit,
    raise_on_failure: bool = False,
) -> None:
    with Session(db_bind) as session:
        run = session.get(AutoGitCommitRun, run_id)
        if run is None:
            return
        failure_to_raise: str | None = None
        now_ts = time.time()
        run.status = "running"
        run.stage = "selecting_repos"
        run.stage_label = "读取自动提交项目"
        run.started_at = now_ts
        run.heartbeat_at = now_ts
        run.updated_at = now_ts
        session.add(run)
        session.commit()

        try:
            candidates = candidate_selector(session)
            results: list[dict[str, Any]] = [
                {
                    "name": candidate.name,
                    "cwd": candidate.cwd,
                    "entry_id": candidate.entry_id,
                    "user_id": candidate.user_id,
                    "username": candidate.username,
                    "status": "pending",
                    "has_changes": False,
                }
                for candidate in candidates
            ]
            _sync_run(session, run, results)

            if not candidates:
                run.status = "skipped"
                run.stage = "empty"
                run.stage_label = "没有配置自动提交项目"
                run.finished_at = time.time()
                run.updated_at = run.finished_at
                run.heartbeat_at = run.finished_at
                session.add(run)
                session.commit()
                return

            final_results: list[dict[str, Any]] = []
            for candidate in candidates:
                run.stage = "processing_repo"
                run.stage_label = _auto_git_processing_stage_label(candidate)
                run.heartbeat_at = time.time()
                run.updated_at = run.heartbeat_at
                session.add(run)
                session.commit()

                try:
                    repo_result = _run_one_repo(
                        session,
                        candidate,
                        inspect_func=inspect_func,
                        draft_generator=draft_generator,
                        commit_func=commit_func,
                    )
                except (AiGitCommitError, GitToolError, RuntimeError, OSError) as exc:
                    repo_result = {
                        "name": candidate.name,
                        "cwd": candidate.cwd,
                        "entry_id": candidate.entry_id,
                        "user_id": candidate.user_id,
                        "username": candidate.username,
                        "status": "failed",
                        "has_changes": True,
                        "error_message": str(exc),
                    }
                except Exception as exc:  # pragma: no cover - background task must stay opportunistic.
                    repo_result = {
                        "name": candidate.name,
                        "cwd": candidate.cwd,
                        "entry_id": candidate.entry_id,
                        "user_id": candidate.user_id,
                        "username": candidate.username,
                        "status": "failed",
                        "has_changes": True,
                        "error_message": str(exc),
                    }
                final_results.append(repo_result)
                _sync_run(session, run, final_results + results[len(final_results):])

            if not any(bool(item.get("has_changes")) for item in final_results):
                run.status = "skipped"
                run.stage = "no_changes"
                run.stage_label = "没有可提交变更"
            else:
                run.status = "failed" if run.failed_repo_count else "completed"
                run.stage = "failed" if run.failed_repo_count else "completed"
                run.stage_label = _format_auto_git_completed_stage_label(run)
                if run.failed_repo_count:
                    run.error_message = f"{run.failed_repo_count} 个仓库自动提交失败"
            run.finished_at = time.time()
            run.heartbeat_at = run.finished_at
            run.updated_at = run.finished_at
            session.add(run)
            session.commit()
            if raise_on_failure and run.status == "failed":
                failure_to_raise = run.error_message or run.stage_label or "自动提交失败"
        except Exception as exc:  # pragma: no cover - fatal bookkeeping guard.
            run.status = "failed"
            run.stage = "failed"
            run.stage_label = "自动提交任务异常"
            run.error_message = str(exc)
            run.finished_at = time.time()
            run.heartbeat_at = run.finished_at
            run.updated_at = run.finished_at
            session.add(run)
            session.commit()
            if raise_on_failure:
                failure_to_raise = str(exc)
        if failure_to_raise:
            raise RuntimeError(failure_to_raise)


def maybe_create_due_auto_git_commit_run(
    session: Session,
    *,
    trigger_reason: str = "scheduled",
    now: datetime | None = None,
    enqueue: bool = True,
) -> AutoGitCommitRun | None:
    mark_stale_auto_git_commit_runs(session, queue_snapshot=background_task_queue.snapshot())
    current = _auto_git_now(now)
    next_run_at = _ensure_auto_git_schedule_next_run_at(session, current)
    if next_run_at > current:
        return None

    if _has_active_auto_git_commit_run(session):
        _advance_auto_git_schedule_next_run_at(session, current)
        return None

    run = create_auto_git_commit_run(
        session,
        trigger_reason=trigger_reason,
        enqueue=enqueue,
    )
    _advance_auto_git_schedule_next_run_at(session, current)
    return run


def maybe_enqueue_auto_git_commit(*, trigger_reason: str = "scheduled") -> AutoGitCommitRun | None:
    from backend.db import engine

    with Session(engine) as session:
        return maybe_create_due_auto_git_commit_run(
            session,
            trigger_reason=trigger_reason,
            enqueue=True,
        )


def schedule_auto_git_commit_job(*, run_catchup: bool = True) -> None:
    if not auto_git_commit_scheduler.running:
        auto_git_commit_scheduler.start()
    auto_git_commit_scheduler.add_job(
        maybe_enqueue_auto_git_commit,
        _auto_git_cron_trigger(),
        id=AUTO_GIT_COMMIT_TASK_KEY,
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=None,
    )
    if run_catchup:
        maybe_enqueue_auto_git_commit(trigger_reason="scheduled_catchup")


def init_auto_git_commit_scheduler() -> None:
    if get_settings().is_test:
        return
        
    from backend.db import engine
    from backend.models import AppSetting
    from sqlmodel import Session
    with Session(engine) as session:
        row = session.get(AppSetting, "background_task.auto_git_commit.enabled")
        enabled = bool(row.value.get("enabled", False)) if row and isinstance(row.value, dict) else False
        
    if not enabled:
        return

    schedule_auto_git_commit_job(run_catchup=True)
    print(f"Auto git commit scheduled: {AUTO_GIT_COMMIT_CRON}")


def shutdown_auto_git_commit_scheduler() -> None:
    if auto_git_commit_scheduler.running:
        auto_git_commit_scheduler.shutdown(wait=False)
