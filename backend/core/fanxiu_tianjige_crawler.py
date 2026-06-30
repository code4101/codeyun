from __future__ import annotations

import datetime as dt
import os
import socket
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

from sqlmodel import Session

from backend.core.runtime.process_launcher import run_quiet
from backend.core.settings import get_settings
from backend.models import AppSetting


FANXIU_TIANJIGE_QUIZ_TASK_KEY = "fanxiu_tianjige_quiz"
FANXIU_TIANJIGE_QUIZ_RUN_TIME = "17:59:50"
FANXIU_TIANJIGE_QUIZ_WEEKDAYS = (2, 3, 4)
FANXIU_TIANJIGE_QUIZ_SETTING_KEY = "background_task.fanxiu_tianjige_quiz.latest_run"
FANXIU_TIANJIGE_QUIZ_RESULT_TEXT_LIMIT = 20000
DEFAULT_XLPROJECT_ROOT = Path(r"C:\home\chenkunze\slns\xlproject")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name) or default)
    except ValueError:
        return default


FANXIU_TIANJIGE_QUIZ_TIMEOUT_SECONDS = max(
    60,
    _env_int("CODEYUN_FANXIU_TIANJIGE_QUIZ_TIMEOUT_SECONDS", 3600),
)


def _now_ts() -> float:
    return time.time()


def _format_ts(value: float | None) -> str:
    if not value:
        return ""
    return dt.datetime.fromtimestamp(value).replace(microsecond=0).isoformat(sep=" ")


def _safe_path_text(path: Path) -> str:
    return os.fspath(path.resolve(strict=False))


def _truncate_text(text: Any, limit: int = FANXIU_TIANJIGE_QUIZ_RESULT_TEXT_LIMIT) -> str:
    if isinstance(text, bytes):
        normalized = text.decode("utf-8", errors="replace").strip()
    else:
        normalized = str(text or "").strip()
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3].rstrip() + "..."


def _save_latest_run(run: dict[str, Any], db_bind: Any | None = None) -> None:
    if db_bind is None:
        from backend.db import engine

        db_bind = engine

    with Session(db_bind) as session:
        row = session.get(AppSetting, FANXIU_TIANJIGE_QUIZ_SETTING_KEY)
        if row is None:
            row = AppSetting(key=FANXIU_TIANJIGE_QUIZ_SETTING_KEY)
        row.value = {"latest_run": run}
        row.updated_at = _now_ts()
        session.add(row)
        session.commit()


def _update_run(run: dict[str, Any], db_bind: Any | None = None, **updates: Any) -> None:
    run.update(updates)
    run["updated_at"] = _now_ts()
    _save_latest_run(run, db_bind=db_bind)


def get_fanxiu_tianjige_quiz_status(session: Session) -> dict[str, Any]:
    row = session.get(AppSetting, FANXIU_TIANJIGE_QUIZ_SETTING_KEY)
    latest_run = row.value.get("latest_run") if row and isinstance(row.value, dict) else None
    return {"latest_run": latest_run if isinstance(latest_run, dict) else None}


def _host_variants(value: str) -> set[str]:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return set()
    return {normalized, normalized.replace("-", "_"), normalized.replace("_", "-")}


def is_fanxiu_tianjige_quiz_allowed_host() -> bool:
    allowed_hosts = (
        os.getenv("CODEYUN_FANXIU_TIANJIGE_QUIZ_ALLOWED_HOSTS")
        or "codepc_mi15,codepc-mi15,mi15"
    ).strip()
    if allowed_hosts == "*":
        return True
    allowed = {item.strip().lower() for item in allowed_hosts.split(",") if item.strip()}
    candidates = _host_variants(socket.gethostname())
    candidates.update(_host_variants(get_settings().data_dir.name))
    return bool(candidates & allowed)


def _resolve_xlproject_root(xlproject_root: Path | str | None = None) -> Path:
    if xlproject_root is not None:
        return Path(xlproject_root).resolve(strict=False)
    configured = os.getenv("CODEYUN_FANXIU_TIANJIGE_XLPROJECT_ROOT")
    return Path(configured or DEFAULT_XLPROJECT_ROOT).resolve(strict=False)


def _resolve_python_executable(
    xlproject_root: Path,
    python_executable: Path | str | None = None,
) -> Path:
    if python_executable is not None:
        return Path(python_executable).resolve(strict=False)
    configured = os.getenv("CODEYUN_FANXIU_TIANJIGE_PYTHON")
    if configured:
        return Path(configured).resolve(strict=False)
    return (xlproject_root / ".venv" / "Scripts" / "python.exe").resolve(strict=False)


def _build_subprocess_env(xlproject_root: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    pythonpath_entries = [
        os.fspath(xlproject_root / "src"),
        os.fspath(xlproject_root),
        r"C:\home\chenkunze",
    ]
    existing_pythonpath = env.get("PYTHONPATH")
    if existing_pythonpath:
        pythonpath_entries.append(existing_pythonpath)
    env["PYTHONPATH"] = os.pathsep.join(pythonpath_entries)
    return env


def _build_crawler_script() -> str:
    return "\n".join(
        [
            "from xlsln.ckz2025.fx.tools.prompt import 天机阁爬虫",
            "天机阁爬虫().自动抢答有奖竞答()",
        ]
    )


def run_fanxiu_tianjige_quiz_worker(
    *,
    db_bind: Any | None = None,
    xlproject_root: Path | str | None = None,
    python_executable: Path | str | None = None,
    timeout_seconds: int | None = None,
    require_allowed_host: bool = True,
) -> None:
    root = _resolve_xlproject_root(xlproject_root)
    python_path = _resolve_python_executable(root, python_executable)
    now_ts = _now_ts()
    run = {
        "id": uuid.uuid4().hex,
        "status": "running",
        "stage": "starting",
        "stage_label": "准备运行天机阁爬虫",
        "created_at": now_ts,
        "started_at": now_ts,
        "updated_at": now_ts,
        "finished_at": None,
        "xlproject_root": _safe_path_text(root),
        "python_executable": _safe_path_text(python_path),
        "target": "xlsln.ckz2025.fx.tools.prompt:天机阁爬虫.自动抢答有奖竞答",
    }
    _save_latest_run(run, db_bind=db_bind)

    if require_allowed_host and not is_fanxiu_tianjige_quiz_allowed_host():
        _update_run(
            run,
            db_bind,
            status="skipped",
            stage="wrong_host",
            stage_label="当前机器不是 mi15，已跳过",
            finished_at=_now_ts(),
            result_text="当前任务默认只允许在 codepc_mi15/mi15 运行。",
        )
        return

    missing_paths = [path for path in (root, python_path) if not path.exists()]
    if missing_paths:
        missing_text = "; ".join(_safe_path_text(path) for path in missing_paths)
        _update_run(
            run,
            db_bind,
            status="failed",
            stage="missing_paths",
            stage_label="xlproject 或 Python 解释器不存在",
            finished_at=_now_ts(),
            error_message=missing_text,
        )
        raise FileNotFoundError(f"凡修天机阁爬虫运行路径不存在：{missing_text}")

    command = [os.fspath(python_path), "-c", _build_crawler_script()]
    timeout = int(timeout_seconds or FANXIU_TIANJIGE_QUIZ_TIMEOUT_SECONDS)
    _update_run(run, db_bind, stage="running_crawler", stage_label="运行天机阁抢答爬虫")

    try:
        completed = run_quiet(
            command,
            cwd=root,
            env=_build_subprocess_env(root),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        _update_run(
            run,
            db_bind,
            status="failed",
            stage="timeout",
            stage_label="爬虫运行超时",
            finished_at=_now_ts(),
            error_message=f"超过 {timeout} 秒未结束",
            stdout=_truncate_text(exc.stdout),
            stderr=_truncate_text(exc.stderr),
        )
        raise TimeoutError(f"凡修天机阁爬虫超过 {timeout} 秒未结束") from exc
    except Exception as exc:
        _update_run(
            run,
            db_bind,
            status="failed",
            stage="subprocess_failed",
            stage_label="启动爬虫失败",
            finished_at=_now_ts(),
            error_message=str(exc),
        )
        raise

    stdout = _truncate_text(completed.stdout)
    stderr = _truncate_text(completed.stderr)
    if completed.returncode != 0:
        _update_run(
            run,
            db_bind,
            status="failed",
            stage="crawler_failed",
            stage_label="爬虫返回失败",
            finished_at=_now_ts(),
            returncode=completed.returncode,
            stdout=stdout,
            stderr=stderr,
            error_message=f"爬虫进程退出码 {completed.returncode}",
        )
        raise RuntimeError(f"凡修天机阁爬虫进程退出码 {completed.returncode}")

    _update_run(
        run,
        db_bind,
        status="completed",
        stage="completed",
        stage_label="爬虫完成",
        finished_at=_now_ts(),
        returncode=completed.returncode,
        stdout=stdout,
        stderr=stderr,
        result_text=stdout or stderr or "爬虫进程已成功结束。",
    )


def enqueue_fanxiu_tianjige_quiz() -> str | None:
    from backend.core.background_task_queue import background_task_queue

    task_id, _ = background_task_queue.enqueue_once(
        FANXIU_TIANJIGE_QUIZ_TASK_KEY,
        run_fanxiu_tianjige_quiz_worker,
    )
    return task_id
