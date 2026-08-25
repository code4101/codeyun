from __future__ import annotations

import os
import socket
import time
import uuid
from typing import Any

from sqlmodel import Session

from backend.core.messaging.wechat_ilink import WechatIlinkError, list_accounts, send_text_message
from backend.core.settings import get_settings
from backend.models import AppSetting


FANXIU_WECHAT_BOSS_REMINDER_TASK_KEY = "fanxiu_wechat_boss_reminder"
FANXIU_WECHAT_SHENGZU_REMINDER_TASK_KEY = "fanxiu_wechat_shengzu_reminder"
FANXIU_WECHAT_BOSS_REMINDER_RUN_TIME = "17:57"
FANXIU_WECHAT_SHENGZU_REMINDER_RUN_TIME = "19:57"
FANXIU_WECHAT_SHENGZU_REMINDER_WEEKDAYS = (7,)


_REMINDERS: dict[str, dict[str, str]] = {
    FANXIU_WECHAT_BOSS_REMINDER_TASK_KEY: {
        "label": "@所有人 准备打魔狱封阵",
        "target": "wechat_ilink:send_text_message",
        "env_prefix": "CODEYUN_FANXIU_WECHAT_BOSS_REMINDER",
    },
    FANXIU_WECHAT_SHENGZU_REMINDER_TASK_KEY: {
        "label": "打圣祖",
        "target": "wechat_ilink:send_text_message",
        "env_prefix": "CODEYUN_FANXIU_WECHAT_SHENGZU_REMINDER",
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


def _env_value(*names: str) -> str:
    for name in names:
        value = os.getenv(name)
        if value and value.strip():
            return value.strip()
    return ""


def _resolve_account_id(account_id: str | None = None) -> str:
    configured = (account_id or "").strip() or _env_value("CODEYUN_FANXIU_WECHAT_ILINK_ACCOUNT_ID")
    if configured:
        return configured
    accounts = list_accounts()
    if len(accounts) == 1:
        return str(accounts[0].get("account_id") or "").strip()
    raise ValueError("未配置凡修微信群提醒的微信 iLink 账号：CODEYUN_FANXIU_WECHAT_ILINK_ACCOUNT_ID")


def _resolve_to_user_id(task_key: str, to_user_id: str | None = None) -> str:
    spec = _REMINDERS[task_key]
    prefix = spec["env_prefix"]
    value = (to_user_id or "").strip() or _env_value(
        f"{prefix}_TO_USER_ID",
        "CODEYUN_FANXIU_WECHAT_REMINDER_TO_USER_ID",
    )
    if not value:
        raise ValueError(
            f"未配置凡修微信群提醒接收方：{prefix}_TO_USER_ID 或 CODEYUN_FANXIU_WECHAT_REMINDER_TO_USER_ID"
        )
    return value


def _resolve_context_token(task_key: str, context_token: str | None = None) -> str:
    spec = _REMINDERS[task_key]
    prefix = spec["env_prefix"]
    return (context_token or "").strip() or _env_value(
        f"{prefix}_CONTEXT_TOKEN",
        "CODEYUN_FANXIU_WECHAT_REMINDER_CONTEXT_TOKEN",
    )


def run_fanxiu_wechat_reminder_worker(
    task_key: str,
    *,
    db_bind: Any | None = None,
    account_id: str | None = None,
    to_user_id: str | None = None,
    context_token: str | None = None,
    timeout_seconds: int | None = None,
    require_allowed_host: bool = True,
) -> dict[str, Any]:
    if task_key not in _REMINDERS:
        raise ValueError(f"未知凡修微信群提醒任务：{task_key}")

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

    timeout = int(timeout_seconds or FANXIU_WECHAT_REMINDER_TIMEOUT_SECONDS)
    try:
        resolved_account_id = _resolve_account_id(account_id)
        resolved_to_user_id = _resolve_to_user_id(task_key, to_user_id)
        resolved_context_token = _resolve_context_token(task_key, context_token)
    except Exception as exc:
        _update_run(
            task_key,
            run,
            db_bind,
            status="failed",
            stage="missing_config",
            stage_label="凡修微信群提醒配置不完整",
            finished_at=_now_ts(),
            error_message=str(exc),
        )
        raise

    _update_run(
        task_key,
        run,
        db_bind,
        stage="sending",
        stage_label=f"通过微信 iLink 发送凡修提醒：{reminder['label']}",
        account_id=resolved_account_id,
        to_user_id=resolved_to_user_id,
        used_context_token=bool(resolved_context_token),
    )

    try:
        sent = send_text_message(
            resolved_account_id,
            to_user_id=resolved_to_user_id,
            text=reminder["label"],
            context_token=resolved_context_token or None,
            timeout_seconds=timeout,
        )
    except WechatIlinkError as exc:
        _update_run(
            task_key,
            run,
            db_bind,
            status="failed",
            stage="send_failed",
            stage_label="微信 iLink 发送提醒失败",
            finished_at=_now_ts(),
            error_message=str(exc),
        )
        raise
    except Exception as exc:
        _update_run(
            task_key,
            run,
            db_bind,
            status="failed",
            stage="send_failed",
            stage_label="微信 iLink 发送提醒失败",
            finished_at=_now_ts(),
            error_message=str(exc),
        )
        raise

    _update_run(
        task_key,
        run,
        db_bind,
        status="completed",
        stage="completed",
        stage_label="提醒已发送",
        finished_at=_now_ts(),
        sent=sent,
        result_text=f"已通过微信 iLink 发送：{reminder['label']}",
    )
    return run


def enqueue_fanxiu_wechat_boss_reminder() -> str | None:
    from backend.core.jobs.executor import background_task_queue

    task_id, _ = background_task_queue.enqueue_once(
        FANXIU_WECHAT_BOSS_REMINDER_TASK_KEY,
        run_fanxiu_wechat_reminder_worker,
        FANXIU_WECHAT_BOSS_REMINDER_TASK_KEY,
    )
    return task_id


def enqueue_fanxiu_wechat_shengzu_reminder() -> str | None:
    from backend.core.jobs.executor import background_task_queue

    task_id, _ = background_task_queue.enqueue_once(
        FANXIU_WECHAT_SHENGZU_REMINDER_TASK_KEY,
        run_fanxiu_wechat_reminder_worker,
        FANXIU_WECHAT_SHENGZU_REMINDER_TASK_KEY,
    )
    return task_id
