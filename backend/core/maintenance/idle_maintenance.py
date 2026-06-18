from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Callable, Iterable

from backend.core.ai.git_tools import GitToolError, inspect_git_repository
from backend.core.runtime.background_task_queue import background_task_queue
from backend.core.settings import ROOT_DIR, get_settings


IDLE_MAINTENANCE_TASK_KEY = "idle_maintenance_runner"
IDLE_MAINTENANCE_QUEUE_NAME = "idle_maintenance"
IDLE_MAINTENANCE_INTERVAL_MINUTES = 5
IDLE_MAINTENANCE_REPORT_LIMIT = 50
MAINTENANCE_REPORT_VERSION = 1
AUTO_COMMIT_REPO_NAMES = ("pyxllib", "xlproject", "codeyun")

TEXT_CODE_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".jsx", ".vue"}
DOC_SUFFIXES = {".md", ".mdx", ".txt"}
SKIP_DIR_NAMES = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "dist",
    "node_modules",
}


@dataclass(frozen=True)
class IdleMaintenanceTask:
    key: str
    title: str
    category: str
    risk: str
    mode: str
    success_metric: str
    action: Callable[[], dict[str, Any]]


@dataclass
class IdleMaintenanceDecision:
    selected_task_key: str | None
    skipped_reason: str = ""
    candidates: list[dict[str, Any]] = field(default_factory=list)


def _now_ts() -> float:
    return time.time()


def _repo_root() -> Path:
    return ROOT_DIR.resolve(strict=False)


def _report_dir() -> Path:
    return get_settings().data_dir / "idle-maintenance"


def _write_report(payload: dict[str, Any], *, report_dir: Path | None = None) -> Path:
    target_dir = report_dir or _report_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(float(payload.get("started_at") or _now_ts())))
    task_key = re.sub(r"[^a-zA-Z0-9_.-]+", "-", str(payload.get("task_key") or "idle-maintenance")).strip("-")
    path = target_dir / f"{timestamp}-{task_key}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _last_report_task_key(*, report_dir: Path | None = None) -> str | None:
    target_dir = report_dir or _report_dir()
    try:
        report_paths = sorted(target_dir.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    except OSError:
        return None
    for path in report_paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        task_key = str(payload.get("task_key") or "")
        if task_key:
            return task_key
    return None


def _queue_is_available_for_idle_maintenance(queue_snapshot: dict[str, Any] | None = None) -> tuple[bool, str]:
    queue = queue_snapshot if isinstance(queue_snapshot, dict) else background_task_queue.snapshot()
    pending = [item for item in queue.get("pending") or [] if isinstance(item, dict)]
    if pending:
        return False, f"后台队列仍有 {len(pending)} 个等待任务"

    running = queue.get("running")
    if isinstance(running, dict):
        running_name = str(running.get("name") or "")
        if running_name and running_name != IDLE_MAINTENANCE_QUEUE_NAME:
            return False, f"后台队列正在运行 {running_name}"
    return True, ""


def _git_inspect_or_error(cwd: Path | str | None = None) -> dict[str, Any]:
    try:
        return inspect_git_repository(str(cwd or _repo_root()))
    except GitToolError as exc:
        return {"error": str(exc), "clean": True, "changed_files": [], "status_lines": []}


def _has_dirty_worktree(inspect_payload: dict[str, Any] | None = None) -> bool:
    payload = inspect_payload or _git_inspect_or_error()
    return not bool(payload.get("clean")) and not bool(payload.get("error"))


def _auto_commit_repo_paths() -> list[tuple[str, Path]]:
    slns_dir = _repo_root().parent
    return [(name, slns_dir / name) for name in AUTO_COMMIT_REPO_NAMES]


def _inspect_auto_commit_repositories() -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for name, path in _auto_commit_repo_paths():
        if not path.exists():
            results.append(
                {
                    "name": name,
                    "cwd": str(path),
                    "exists": False,
                    "clean": True,
                    "has_changes": False,
                    "changed_file_count": 0,
                    "error": "项目目录不存在",
                }
            )
            continue
        inspect_payload = _git_inspect_or_error(path)
        changed_files = list(inspect_payload.get("changed_files") or [])
        results.append(
            {
                "name": name,
                "cwd": str(path),
                "exists": True,
                "clean": bool(inspect_payload.get("clean")),
                "has_changes": _has_dirty_worktree(inspect_payload),
                "changed_file_count": len(changed_files),
                "branch": str(inspect_payload.get("branch") or ""),
                "status_lines": list(inspect_payload.get("status_lines") or [])[:20],
                "error": str(inspect_payload.get("error") or ""),
            }
        )
    return results


def _has_dirty_auto_commit_repository(repo_inspects: list[dict[str, Any]] | None) -> bool:
    return any(bool(item.get("has_changes")) for item in repo_inspects or [])


def _path_is_under_skipped_dir(path: Path) -> bool:
    return any(part in SKIP_DIR_NAMES for part in path.parts)


def _iter_repo_files(*, suffixes: set[str], limit: int = 5000) -> Iterable[Path]:
    root = _repo_root()
    yielded = 0
    for dirpath, dirnames, filenames in os.walk(root):
        current_dir = Path(dirpath)
        try:
            relative_dir = current_dir.relative_to(root)
        except ValueError:
            continue
        dirnames[:] = [name for name in dirnames if name not in SKIP_DIR_NAMES]
        if relative_dir != Path(".") and _path_is_under_skipped_dir(relative_dir):
            continue
        for filename in filenames:
            if yielded >= limit:
                return
            path = current_dir / filename
            if path.suffix.lower() not in suffixes:
                continue
            yielded += 1
            yield path


def _relative_path(path: Path) -> str:
    try:
        return path.resolve(strict=False).relative_to(_repo_root()).as_posix()
    except ValueError:
        return path.as_posix()


def _line_count(path: Path) -> int:
    try:
        return sum(1 for _ in path.open("r", encoding="utf-8", errors="ignore"))
    except OSError:
        return 0


def _find_large_code_files(limit: int = 20) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for path in _iter_repo_files(suffixes=TEXT_CODE_SUFFIXES):
        try:
            size_bytes = path.stat().st_size
        except OSError:
            continue
        lines = _line_count(path)
        if lines < 250 and size_bytes < 32_000:
            continue
        items.append(
            {
                "path": _relative_path(path),
                "line_count": lines,
                "size_bytes": size_bytes,
                "metric": "line_count",
                "why_candidate": "文件较大，适合作为后续人工/AI 代码瘦身候选；本任务只报告不修改。",
            }
        )
    return sorted(items, key=lambda item: (int(item["line_count"]), int(item["size_bytes"])), reverse=True)[:limit]


def _extract_doc_refs(text: str) -> list[str]:
    refs: list[str] = []
    patterns = [
        r"`([^`\n]*(?:backend|frontend|scripts|docs|tests|AGENTS\.md|README\.md)[^`\n]*)`",
        r"(?<![A-Za-z0-9_./-])((?:backend|frontend|scripts|docs|tests)/[A-Za-z0-9_./-]+)",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            raw = match.group(1).strip().strip(".,;:，。；：")
            if raw and raw not in refs:
                refs.append(raw)
    return refs


def _looks_like_path_ref(ref: str) -> bool:
    if any(part in ref for part in (" ", "\t", "$", "|", "&&")):
        return False
    return "/" in ref or "\\" in ref or "." in Path(ref).name


def _check_doc_path_refs(limit: int = 50) -> list[dict[str, Any]]:
    root = _repo_root()
    issues: list[dict[str, Any]] = []
    for doc_path in _iter_repo_files(suffixes=DOC_SUFFIXES):
        try:
            text = doc_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for ref in _extract_doc_refs(text):
            if not _looks_like_path_ref(ref):
                continue
            normalized = ref.replace("\\", "/").split("#", 1)[0]
            target = (root / normalized).resolve(strict=False)
            if target.exists():
                continue
            issues.append(
                {
                    "doc_path": _relative_path(doc_path),
                    "ref": ref,
                    "metric": "missing_doc_path_ref",
                    "why_candidate": "文档引用的仓库路径不存在，适合后续 docs_sync 任务修正。",
                }
            )
            if len(issues) >= limit:
                return issues
    return issues


def _run_auto_commit_task() -> dict[str, Any]:
    from sqlmodel import Session

    from backend.core.ai.auto_git_commit import create_auto_git_commit_run, mark_stale_auto_git_commit_runs
    from backend.db import engine
    from backend.models import AutoGitCommitRun
    from sqlmodel import select

    with Session(engine) as session:
        mark_stale_auto_git_commit_runs(session, queue_snapshot=background_task_queue.snapshot())
        active = session.exec(
            select(AutoGitCommitRun.id).where(AutoGitCommitRun.status.in_(["pending", "running"])).limit(1)
        ).first()
        if active:
            return {"status": "skipped", "reason": "已有自动提交任务在运行", "active_run_id": active}
        run = create_auto_git_commit_run(session, trigger_reason="idle_maintenance", enqueue=True)
        return {
            "status": "enqueued",
            "run_id": run.id,
            "queue_task_id": run.queue_task_id,
            "success_metric": "dirty_worktree_committed_or_safely_reported",
        }


def _run_docs_sync_scan_task() -> dict[str, Any]:
    issues = _check_doc_path_refs(limit=IDLE_MAINTENANCE_REPORT_LIMIT)
    return {
        "status": "completed",
        "mode": "read_only",
        "missing_path_ref_count": len(issues),
        "issues": issues,
        "success_metric": "missing_doc_path_ref_count",
    }


def _run_code_slimming_scan_task() -> dict[str, Any]:
    candidates = _find_large_code_files(limit=IDLE_MAINTENANCE_REPORT_LIMIT)
    return {
        "status": "completed",
        "mode": "read_only",
        "candidate_count": len(candidates),
        "candidates": candidates,
        "success_metric": "ranked_code_slimming_candidate_count",
    }


def build_idle_maintenance_task_pool() -> list[IdleMaintenanceTask]:
    return [
        IdleMaintenanceTask(
            key="auto_commit_dirty_worktree",
            title="GitHub 项目脏工作区自动提交",
            category="git",
            risk="medium",
            mode="delegated",
            success_metric="dirty_worktree_committed_or_safely_reported",
            action=_run_auto_commit_task,
        ),
        IdleMaintenanceTask(
            key="docs_sync_scan",
            title="文档事实对齐扫描",
            category="docs",
            risk="low",
            mode="read_only",
            success_metric="missing_doc_path_ref_count",
            action=_run_docs_sync_scan_task,
        ),
        IdleMaintenanceTask(
            key="code_slimming_scan",
            title="代码瘦身候选扫描",
            category="code_health",
            risk="low",
            mode="read_only",
            success_metric="ranked_code_slimming_candidate_count",
            action=_run_code_slimming_scan_task,
        ),
    ]


def _task_metadata(task: IdleMaintenanceTask) -> dict[str, Any]:
    return {
        "key": task.key,
        "title": task.title,
        "category": task.category,
        "risk": task.risk,
        "mode": task.mode,
        "success_metric": task.success_metric,
    }


def select_idle_maintenance_task(
    task_pool: list[IdleMaintenanceTask] | None = None,
    *,
    git_inspect: dict[str, Any] | None = None,
    repo_inspects: list[dict[str, Any]] | None = None,
    last_task_key: str | None = None,
) -> IdleMaintenanceDecision:
    tasks = task_pool or build_idle_maintenance_task_pool()
    candidates = [_task_metadata(task) for task in tasks]
    task_by_key = {task.key: task for task in tasks}

    if _has_dirty_auto_commit_repository(repo_inspects) or (repo_inspects is None and _has_dirty_worktree(git_inspect)):
        if "auto_commit_dirty_worktree" in task_by_key:
            return IdleMaintenanceDecision("auto_commit_dirty_worktree", candidates=candidates)

    read_only_order = ["docs_sync_scan", "code_slimming_scan"]
    if last_task_key in read_only_order:
        index = read_only_order.index(last_task_key)
        read_only_order = read_only_order[index + 1 :] + read_only_order[: index + 1]

    for key in read_only_order:
        if key in task_by_key:
            return IdleMaintenanceDecision(key, candidates=candidates)
    return IdleMaintenanceDecision(None, skipped_reason="没有可执行的维护任务", candidates=candidates)


def run_idle_maintenance_once(
    *,
    task_pool: list[IdleMaintenanceTask] | None = None,
    queue_snapshot: dict[str, Any] | None = None,
    report_dir: Path | None = None,
) -> dict[str, Any]:
    started_at = _now_ts()
    payload: dict[str, Any] = {
        "version": MAINTENANCE_REPORT_VERSION,
        "status": "pending",
        "task_key": "",
        "started_at": started_at,
        "finished_at": None,
        "repo_root": str(_repo_root()),
    }

    queue_ok, queue_reason = _queue_is_available_for_idle_maintenance(queue_snapshot)
    if not queue_ok:
        payload.update({"status": "skipped", "reason": queue_reason})
        payload["finished_at"] = _now_ts()
        path = _write_report(payload, report_dir=report_dir)
        payload["report_path"] = str(path)
        return payload

    repo_inspects = _inspect_auto_commit_repositories()
    payload["repo_inspects"] = repo_inspects
    decision = select_idle_maintenance_task(
        task_pool,
        repo_inspects=repo_inspects,
        last_task_key=_last_report_task_key(report_dir=report_dir),
    )
    payload["candidates"] = decision.candidates
    if not decision.selected_task_key:
        payload.update({"status": "skipped", "reason": decision.skipped_reason})
        payload["finished_at"] = _now_ts()
        path = _write_report(payload, report_dir=report_dir)
        payload["report_path"] = str(path)
        return payload

    tasks = {task.key: task for task in (task_pool or build_idle_maintenance_task_pool())}
    task = tasks[decision.selected_task_key]
    payload.update({"task_key": task.key, "task": _task_metadata(task)})
    try:
        result = task.action()
        payload.update({"status": str(result.get("status") or "completed"), "result": result})
    except Exception as exc:
        payload.update({"status": "failed", "error": str(exc)})
    payload["finished_at"] = _now_ts()
    path = _write_report(payload, report_dir=report_dir)
    payload["report_path"] = str(path)
    return payload


def enqueue_idle_maintenance() -> str | None:
    task_id, _created = background_task_queue.enqueue_once(
        IDLE_MAINTENANCE_QUEUE_NAME,
        run_idle_maintenance_once,
        metadata={"task_key": IDLE_MAINTENANCE_TASK_KEY},
        resource_lock="resource:repo",
    )
    return task_id
