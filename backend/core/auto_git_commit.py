from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
import time
from typing import Any, Callable

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlmodel import Session, select

from backend.core.ai_chat import (
    CODEX_CLI_DEFAULT_COMMAND,
    CODEX_CLI_DEFAULT_MODEL,
    AiProviderConfig,
    OllamaClientError,
    chat_with_provider,
)
from backend.core.ai_git_commit import AiGitCommitError, resolve_ai_git_commit_runtime_config as resolve_ai_runtime_config
from backend.core.ai_git_reduction import generate_ai_git_commit_draft_hierarchical
from backend.core.ai_git_repos import list_user_ai_git_repos
from backend.core.background_task_queue import background_task_queue
from backend.core.git_tools import GitToolError, create_git_commit, inspect_git_repository
from backend.core.settings import ROOT_DIR, get_settings
from backend.models import AppSetting, AutoGitCommitRun, User


AUTO_GIT_COMMIT_TASK_KEY = "auto_git_commit"
AUTO_GIT_COMMIT_REPO_KEYS = ("pyxllib", "xlproject", "codeyun")
AUTO_GIT_COMMIT_CRON = "15 0 * * *"
AUTO_GIT_COMMIT_SCHEDULE_SETTING_KEY = f"background_task.{AUTO_GIT_COMMIT_TASK_KEY}.schedule"
AUTO_GIT_COMMIT_BRANCH_FACTOR = 10
AUTO_GIT_COMMIT_CHANGED_PATH_LIMIT = 80
AUTO_GIT_COMMIT_CRON_LOOKBACK_DAYS = 32
AUTO_GIT_PRE_COMMIT_CODEX_PROVIDER_ID = "auto-git-pre-commit-codex"
AUTO_GIT_PRE_COMMIT_CODEX_TIMEOUT_SECONDS = 1800
AUTO_GIT_COMMIT_STALE_HEARTBEAT_SECONDS = AUTO_GIT_PRE_COMMIT_CODEX_TIMEOUT_SECONDS + 900
AUTO_GIT_PRE_COMMIT_RESULT_LIMIT = 3000
AUTO_GIT_PRE_COMMIT_SKIP_REPO_KEYS = ("codeyun",)

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
        raise RuntimeError(f"无法计算自动 Git 提交的下次运行时间：{AUTO_GIT_COMMIT_CRON}")
    next_fire = _coerce_cron_datetime(next_fire, trigger).replace(microsecond=0)
    if next_fire <= current:
        next_fire = trigger.get_next_fire_time(next_fire, current + timedelta(seconds=1))
    if next_fire is None:
        raise RuntimeError(f"无法计算自动 Git 提交的下次运行时间：{AUTO_GIT_COMMIT_CRON}")
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
            "通常是服务重启、进程中断或 Codex CLI 调用被外部终止。"
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


def _limit_codex_result_text(value: Any) -> str:
    text = str(value or "").strip()
    if len(text) <= AUTO_GIT_PRE_COMMIT_RESULT_LIMIT:
        return text
    return text[: AUTO_GIT_PRE_COMMIT_RESULT_LIMIT - 1].rstrip() + "…"


def _skip_pre_commit_optimizer_reason(candidate: AutoGitCommitCandidate) -> str | None:
    repo_key = _normalize_repo_key(candidate.name)
    if repo_key in AUTO_GIT_PRE_COMMIT_SKIP_REPO_KEYS:
        return f"{candidate.name} 自动提交只生成提交信息，不执行提交前 Codex 优化"
    return None


def _build_skipped_pre_commit_review(reason: str) -> dict[str, str]:
    return {
        "status": "skipped",
        "reason": reason,
        "summary": reason,
    }


def _auto_git_processing_stage_label(candidate: AutoGitCommitCandidate) -> str:
    if _skip_pre_commit_optimizer_reason(candidate):
        return f"检查/提交 {candidate.name}"
    return f"检查/优化 {candidate.name}"


def _build_auto_git_pre_commit_codex_provider(cwd: str) -> AiProviderConfig:
    return AiProviderConfig(
        id=AUTO_GIT_PRE_COMMIT_CODEX_PROVIDER_ID,
        label="Codex CLI",
        kind="codex_cli",
        base_url=CODEX_CLI_DEFAULT_COMMAND,
        default_model=CODEX_CLI_DEFAULT_MODEL,
        timeout_seconds=AUTO_GIT_PRE_COMMIT_CODEX_TIMEOUT_SECONDS,
        api_key="",
        supports_stream=False,
        supports_vision=False,
        requires_api_key=False,
        configured=True,
        models=(CODEX_CLI_DEFAULT_MODEL,),
        is_custom=False,
        workspace_dir=cwd,
    )


def _build_auto_git_pre_commit_codex_prompt(
    candidate: AutoGitCommitCandidate,
    inspect_payload: dict[str, Any],
) -> str:
    changed_paths = _changed_paths_from_inspect(inspect_payload)
    omitted_count = max(0, len(inspect_payload.get("changed_files") or []) - len(changed_paths))
    path_lines = [f"- {path}" for path in changed_paths]
    if omitted_count:
        path_lines.append(f"- ... 还有 {omitted_count} 个文件未列出")

    status_lines = [str(line) for line in inspect_payload.get("status_lines") or [] if str(line).strip()]
    diff_stat = str(inspect_payload.get("diff_stat") or "").strip()
    staged_diff_stat = str(inspect_payload.get("staged_diff_stat") or "").strip()

    return "\n".join(
        [
            "请在这个仓库提交前先做一次代码 review 和工程优化。",
            "",
            "仓库信息：",
            f"- 名称: {candidate.name}",
            f"- 路径: {candidate.cwd}",
            f"- 分支: {inspect_payload.get('branch') or ''}",
            f"- 变更文件数: {len(inspect_payload.get('changed_files') or [])}",
            "",
            "当前状态：",
            *(f"- {line}" for line in status_lines[:40]),
            "",
            "变更文件：",
            *(path_lines or ["- (未列出)"]),
            "",
            "未暂存 diff 统计：",
            diff_stat or "(无)",
            "",
            "已暂存 diff 统计：",
            staged_diff_stat or "(无)",
            "",
            "执行要求：",
            "- 直接检查当前工作区改动，优先发现真实 bug、回归风险、缺失校验、低质量实现和明显工程结构问题。",
            "- 可以直接修改源码做小范围工程优化；保持改动与当前待提交内容相关。",
            "- 不要执行 git commit、git reset、git checkout 或破坏用户未提交改动的命令。",
            "- 不要新增大规模重构、格式化全仓、迁移依赖或修改与当前变更无关的文件。",
            "- 如果合理，运行最小必要验证；无法运行时在最终回复说明。",
            "- 最终用简短中文说明 review 发现、做了哪些优化、验证结果。",
        ]
    ).strip()


def run_auto_git_pre_commit_codex_review(
    candidate: AutoGitCommitCandidate,
    inspect_payload: dict[str, Any],
    *,
    chat_func: Callable[..., dict[str, Any]] = chat_with_provider,
) -> dict[str, Any]:
    provider = _build_auto_git_pre_commit_codex_provider(candidate.cwd)
    try:
        response = chat_func(
            provider_id=provider.id,
            model=provider.default_model,
            system_prompt=(
                "你是 CodeYun 自动 Git 提交前的 Codex 工程代理。"
                "你的职责是在提交前审查当前仓库改动，并做必要的小范围工程优化。"
                "必须尊重用户已有改动，不要提交，不要回滚，不要扩大改动范围。"
            ),
            messages=[{"role": "user", "content": _build_auto_git_pre_commit_codex_prompt(candidate, inspect_payload)}],
            timeout_seconds=provider.timeout_seconds,
            extra_providers=(provider,),
        )
    except OllamaClientError as exc:
        raise GitToolError(f"Codex CLI 预提交 review/优化失败：{exc}") from exc

    return {
        "status": "completed",
        "provider": provider.id,
        "model": str(response.get("model") or provider.default_model),
        "summary": _limit_codex_result_text(response.get("content")),
        "session_id": response.get("session_id"),
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


def _run_one_repo(
    session: Session,
    candidate: AutoGitCommitCandidate,
    *,
    inspect_func: Callable[[str], dict[str, Any]],
    pre_commit_optimizer: Callable[[AutoGitCommitCandidate, dict[str, Any]], dict[str, Any] | None],
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

    precheck = inspect_payload.get("precheck") if isinstance(inspect_payload.get("precheck"), dict) else {}
    if bool(precheck.get("has_blocking_issues")):
        result.update(
            {
                "status": "failed",
                "error_message": f"提交前预检未通过，发现 {int(precheck.get('blocking_issue_count') or 0)} 条阻断项",
            }
        )
        return result

    skip_pre_commit_reason = _skip_pre_commit_optimizer_reason(candidate)
    if skip_pre_commit_reason:
        result["pre_commit_review"] = _build_skipped_pre_commit_review(skip_pre_commit_reason)
    else:
        result["pre_commit_review"] = pre_commit_optimizer(candidate, inspect_payload) or {"status": "skipped"}

    inspect_payload = inspect_func(candidate.cwd)
    _update_result_from_inspect(result, inspect_payload)
    if bool(inspect_payload.get("clean")):
        result["status"] = "clean"
        return result

    precheck = inspect_payload.get("precheck") if isinstance(inspect_payload.get("precheck"), dict) else {}
    if bool(precheck.get("has_blocking_issues")):
        result.update(
            {
                "status": "failed",
                "error_message": f"Codex 优化后提交前预检未通过，发现 {int(precheck.get('blocking_issue_count') or 0)} 条阻断项",
            }
        )
        return result

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

    draft = draft_generator(
        cwd=candidate.cwd,
        provider_id=provider_id,
        base_url=base_url,
        api_key=api_key,
        model=model,
        style="summary",
        include_body=True,
        extra_providers=extra_providers,
        branch_factor=AUTO_GIT_COMMIT_BRANCH_FACTOR,
    )
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
    pre_commit_optimizer: Callable[[AutoGitCommitCandidate, dict[str, Any]], dict[str, Any] | None] = run_auto_git_pre_commit_codex_review,
    draft_generator: Callable[..., dict[str, Any]] = generate_ai_git_commit_draft_hierarchical,
    commit_func: Callable[..., dict[str, Any]] = create_git_commit,
) -> None:
    with Session(db_bind) as session:
        run = session.get(AutoGitCommitRun, run_id)
        if run is None:
            return
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
                        pre_commit_optimizer=pre_commit_optimizer,
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
                run.status = "completed"
                run.stage = "completed"
                run.stage_label = f"已提交 {run.committed_repo_count} 个仓库"
                if run.failed_repo_count:
                    run.error_message = f"{run.failed_repo_count} 个仓库自动提交失败"
            run.finished_at = time.time()
            run.heartbeat_at = run.finished_at
            run.updated_at = run.finished_at
            session.add(run)
            session.commit()
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
