from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
import time
from typing import Any, Callable

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlmodel import Session, select

from backend.core.ai_chat_user_config import get_user_ai_chat_provider_runtime_config
from backend.core.ai_git_commit import AiGitCommitError, resolve_ai_runtime_config
from backend.core.ai_git_reduction import generate_ai_git_commit_draft_hierarchical
from backend.core.ai_git_repos import get_user_ai_git_commit_config, list_user_ai_git_repos
from backend.core.background_task_queue import background_task_queue
from backend.core.git_tools import GitToolError, create_git_commit, inspect_git_repository
from backend.core.settings import ROOT_DIR, get_settings
from backend.models import AutoGitCommitRun, User


AUTO_GIT_COMMIT_REPO_KEYS = ("pyxllib", "xlproject", "codeyun")
AUTO_GIT_COMMIT_CRON = "20 3 * * *"
AUTO_GIT_COMMIT_BRANCH_FACTOR = 10
AUTO_GIT_COMMIT_CHANGED_PATH_LIMIT = 80

auto_git_commit_scheduler = BackgroundScheduler()


@dataclass(frozen=True)
class AutoGitCommitCandidate:
    user_id: int
    username: str
    name: str
    cwd: str
    entry_id: str = ""


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


def get_auto_git_commit_status(session: Session) -> dict[str, Any]:
    latest_run = session.exec(
        select(AutoGitCommitRun).order_by(AutoGitCommitRun.created_at.desc())
    ).first()
    active_run = session.exec(
        select(AutoGitCommitRun)
        .where(AutoGitCommitRun.status.in_(["pending", "running"]))
        .order_by(AutoGitCommitRun.created_at.desc())
    ).first()
    return {
        "repo_keys": list(AUTO_GIT_COMMIT_REPO_KEYS),
        "cron": AUTO_GIT_COMMIT_CRON,
        "latest_run": serialize_auto_git_commit_run(latest_run),
        "active_run": serialize_auto_git_commit_run(active_run),
        "queue": background_task_queue.snapshot(),
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


def _resolve_auto_git_model(session: Session, user_id: int, provider_id: str) -> str | None:
    try:
        runtime_config = get_user_ai_chat_provider_runtime_config(session, user_id, provider_id)
    except Exception:
        return None
    model = str(runtime_config.get("preferred_model") or "").strip()
    return model or None


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

    user = session.get(User, candidate.user_id)
    if user is None:
        result.update({"status": "failed", "error_message": "关联用户不存在"})
        return result

    config = get_user_ai_git_commit_config(session, candidate.user_id)
    user_provider = config.get("provider_id") or None
    user_model = config.get("model") or None

    provider_id, base_url, api_key, extra_providers = resolve_ai_runtime_config(
        session=session,
        current_user=user,
        provider=user_provider,
        base_url=None,
        api_key=None,
    )
    
    if user_provider and provider_id == user_provider:
        model = user_model
    else:
        model = _resolve_auto_git_model(session, candidate.user_id, provider_id)

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
                run.stage_label = f"检查 {candidate.name}"
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


def maybe_enqueue_auto_git_commit() -> AutoGitCommitRun | None:
    from backend.db import engine

    with Session(engine) as session:
        if _has_active_auto_git_commit_run(session):
            return None
        return create_auto_git_commit_run(
            session,
            trigger_reason="scheduled",
            enqueue=True,
        )


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
        
    if not auto_git_commit_scheduler.running:
        auto_git_commit_scheduler.start()
    auto_git_commit_scheduler.add_job(
        maybe_enqueue_auto_git_commit,
        CronTrigger.from_crontab(AUTO_GIT_COMMIT_CRON),
        id="auto_git_commit",
        replace_existing=True,
        max_instances=1,
    )
    print(f"Auto git commit scheduled: {AUTO_GIT_COMMIT_CRON}")


def shutdown_auto_git_commit_scheduler() -> None:
    if auto_git_commit_scheduler.running:
        auto_git_commit_scheduler.shutdown(wait=False)
