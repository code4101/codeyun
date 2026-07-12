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


FANXIU_WECHAT_BOSS_REMINDER_TASK_KEY = "fanxiu_wechat_boss_reminder"
FANXIU_WECHAT_SHENGZU_REMINDER_TASK_KEY = "fanxiu_wechat_shengzu_reminder"
FANXIU_WECHAT_BOSS_REMINDER_RUN_TIME = "17:57"
FANXIU_WECHAT_SHENGZU_REMINDER_RUN_TIME = "19:57"
FANXIU_WECHAT_SHENGZU_REMINDER_WEEKDAYS = (7,)
FANXIU_WECHAT_REMINDER_RESULT_TEXT_LIMIT = 20000
DEFAULT_XLPROJECT_ROOT = Path(r"C:\home\chenkunze\slns\xlproject")


_REMINDERS: dict[str, dict[str, str]] = {
    FANXIU_WECHAT_BOSS_REMINDER_TASK_KEY: {
        "function": "提醒boss",
        "label": "准备打boss",
        "target": "xlsln.ckz2025.fx.tools.prompt:提醒boss",
    },
    FANXIU_WECHAT_SHENGZU_REMINDER_TASK_KEY: {
        "function": "提醒圣祖",
        "label": "打圣祖",
        "target": "xlsln.ckz2025.fx.tools.prompt:提醒圣祖",
    },
}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name) or default)
    except ValueError:
        return default


FANXIU_WECHAT_REMINDER_TIMEOUT_SECONDS = max(
    30,
    _env_int("CODEYUN_FANXIU_WECHAT_REMINDER_TIMEOUT_SECONDS", 120),
)


def _now_ts() -> float:
    return time.time()


def _format_ts(value: float | None) -> str:
    if not value:
        return ""
    return dt.datetime.fromtimestamp(value).replace(microsecond=0).isoformat(sep=" ")


def _safe_path_text(path: Path) -> str:
    return os.fspath(path.resolve(strict=False))


def _truncate_text(text: Any, limit: int = FANXIU_WECHAT_REMINDER_RESULT_TEXT_LIMIT) -> str:
    if isinstance(text, bytes):
        normalized = text.decode("utf-8", errors="replace").strip()
    else:
        normalized = str(text or "").strip()
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3].rstrip() + "..."


def _setting_key(task_key: str) -> str:
    return f"background_task.{task_key}.latest_run"


def _save_latest_run(task_key: str, run: dict[str, Any], db_bind: Any | None = None) -> None:
    if db_bind is None:
        from backend.db import engine

        db_bind = engine

    with Session(db_bind) as session:
        row = session.get(AppSetting, _setting_key(task_key))
        if row is None:
            row = AppSetting(key=_setting_key(task_key))
        row.value = {"latest_run": run}
        row.updated_at = _now_ts()
        session.add(row)
        session.commit()


def _update_run(task_key: str, run: dict[str, Any], db_bind: Any | None = None, **updates: Any) -> None:
    run.update(updates)
    run["updated_at"] = _now_ts()
    _save_latest_run(task_key, run, db_bind=db_bind)


def get_fanxiu_wechat_reminder_status(task_key: str, session: Session) -> dict[str, Any]:
    row = session.get(AppSetting, _setting_key(task_key))
    latest_run = row.value.get("latest_run") if row and isinstance(row.value, dict) else None
    return {"latest_run": latest_run if isinstance(latest_run, dict) else None}


def _host_variants(value: str) -> set[str]:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return set()
    return {normalized, normalized.replace("-", "_"), normalized.replace("_", "-")}


def is_fanxiu_wechat_reminder_allowed_host() -> bool:
    allowed_hosts = (
        os.getenv("CODEYUN_FANXIU_WECHAT_REMINDER_ALLOWED_HOSTS")
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
    configured = os.getenv("CODEYUN_FANXIU_WECHAT_REMINDER_XLPROJECT_ROOT")
    return Path(configured or DEFAULT_XLPROJECT_ROOT).resolve(strict=False)


def _resolve_python_executable(
    xlproject_root: Path,
    python_executable: Path | str | None = None,
) -> Path:
    if python_executable is not None:
        return Path(python_executable).resolve(strict=False)
    configured = os.getenv("CODEYUN_FANXIU_WECHAT_REMINDER_PYTHON")
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


def _build_reminder_script(task_key: str) -> str:
    spec = _REMINDERS[task_key]
    function_name = spec["function"]
    return "\n".join(
        [
            f"from xlsln.ckz2025.fx.tools.prompt import {function_name}",
            f"{function_name}()",
        ]
    )


def run_fanxiu_wechat_reminder_worker(
    task_key: str,
    *,
    db_bind: Any | None = None,
    xlproject_root: Path | str | None = None,
    python_executable: Path | str | None = None,
    timeout_seconds: int | None = None,
    require_allowed_host: bool = True,
) -> dict[str, Any]:
    if task_key not in _REMINDERS:
        raise ValueError(f"未知凡修微信群提醒任务：{task_key}")

    root = _resolve_xlproject_root(xlproject_root)
    python_path = _resolve_python_executable(root, python_executable)
    reminder = _REMINDERS[task_key]
    now_ts = _now_ts()
    run = {
        "id": uuid.uuid4().hex,
        "status": "running",
        "stage": "starting",
        "stage_label": f"准备发送凡修提醒：{reminder['label']}",
        "created_at": now_ts,
        "started_at": now_ts,
        "updated_at": now_ts,
        "finished_at": None,
        "xlproject_root": _safe_path_text(root),
        "python_executable": _safe_path_text(python_path),
        "target": reminder["target"],
        "message": reminder["label"],
    }
    _save_latest_run(task_key, run, db_bind=db_bind)

    if require_allowed_host and not is_fanxiu_wechat_reminder_allowed_host():
        _update_run(
            task_key,
            run,
            db_bind,
            status="skipped",
            stage="wrong_host",
            stage_label="当前机器未启用凡修微信群提醒",
            finished_at=_now_ts(),
            result_text="当前任务默认只允许在 codepc_mi15/mi15 运行；可用 CODEYUN_FANXIU_WECHAT_REMINDER_ALLOWED_HOSTS 覆盖。",
        )
        return run

    missing_paths = [path for path in (root, python_path) if not path.exists()]
    if missing_paths:
        missing_text = "; ".join(_safe_path_text(path) for path in missing_paths)
        _update_run(
            task_key,
            run,
            db_bind,
            status="failed",
            stage="missing_paths",
            stage_label="xlproject 或 Python 解释器不存在",
            finished_at=_now_ts(),
            error_message=missing_text,
        )
        raise FileNotFoundError(f"凡修微信群提醒运行路径不存在：{missing_text}")

    command = [os.fspath(python_path), "-c", _build_reminder_script(task_key)]
    timeout = int(timeout_seconds or FANXIU_WECHAT_REMINDER_TIMEOUT_SECONDS)
    _update_run(task_key, run, db_bind, stage="running_reminder", stage_label=f"发送凡修提醒：{reminder['label']}")

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
            task_key,
            run,
            db_bind,
            status="failed",
            stage="timeout",
            stage_label="发送提醒超时",
            finished_at=_now_ts(),
            error_message=f"超过 {timeout} 秒未结束",
            stdout=_truncate_text(exc.stdout),
            stderr=_truncate_text(exc.stderr),
        )
        raise TimeoutError(f"凡修微信群提醒超过 {timeout} 秒未结束") from exc
    except Exception as exc:
        _update_run(
            task_key,
            run,
            db_bind,
            status="failed",
            stage="subprocess_failed",
            stage_label="启动提醒进程失败",
            finished_at=_now_ts(),
            error_message=str(exc),
        )
        raise

    stdout = _truncate_text(completed.stdout)
    stderr = _truncate_text(completed.stderr)
    if completed.returncode != 0:
        _update_run(
            task_key,
            run,
            db_bind,
            status="failed",
            stage="reminder_failed",
            stage_label="提醒进程返回失败",
            finished_at=_now_ts(),
            returncode=completed.returncode,
            stdout=stdout,
            stderr=stderr,
            error_message=f"提醒进程退出码 {completed.returncode}",
        )
        raise RuntimeError(f"凡修微信群提醒进程退出码 {completed.returncode}")

    _update_run(
        task_key,
        run,
        db_bind,
        status="completed",
        stage="completed",
        stage_label="提醒已发送",
        finished_at=_now_ts(),
        returncode=completed.returncode,
        stdout=stdout,
        stderr=stderr,
        result_text=stdout or stderr or f"已发送：{reminder['label']}",
    )
    return run


def enqueue_fanxiu_wechat_boss_reminder() -> str | None:
    from backend.core.background_task_queue import background_task_queue

    task_id, _ = background_task_queue.enqueue_once(
        FANXIU_WECHAT_BOSS_REMINDER_TASK_KEY,
        run_fanxiu_wechat_reminder_worker,
        FANXIU_WECHAT_BOSS_REMINDER_TASK_KEY,
    )
    return task_id


def enqueue_fanxiu_wechat_shengzu_reminder() -> str | None:
    from backend.core.background_task_queue import background_task_queue

    task_id, _ = background_task_queue.enqueue_once(
        FANXIU_WECHAT_SHENGZU_REMINDER_TASK_KEY,
        run_fanxiu_wechat_reminder_worker,
        FANXIU_WECHAT_SHENGZU_REMINDER_TASK_KEY,
    )
    return task_id
