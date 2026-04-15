from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from backend.core.settings import get_settings


_CONFIG_FILENAME = "fanxiu_status_config.json"
_DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
_CLOCK_RE = re.compile(r"^(?P<prev>-)?(?P<hour>\d{1,2}):(?P<minute>\d{1,2})$")
_REMOVED_STATUS_KEYS = ("需要回到世界",)

_DEFAULT_DAILY_TASKS = (
    "日常_游历",
    "日常_报名",
    "日常_助手",
    "日常_每日vip",
    "日常_宗门红包",
    "日常_灵塔",
    "日常_双修",
    "日常_灵祖",
    "日常_剑灵",
    "日常_妖王来袭",
    "日常_妖族袭城",
    "日常_活跃度",
    "日常_每日副本",
    "仙府_寻访仙侣",
    "仙府_领悟绝技",
)
_WINDOW_TASKS = (
    ("日常_魔祖", "12:29"),
    ("日常_灵泉", "20:29"),
    ("日常_镇邪", "20:59"),
)


def get_status_config_path() -> Path:
    return get_settings().data_dir / _CONFIG_FILENAME


def load_status_config() -> dict[str, Any]:
    path = get_status_config_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_status_config(status_path: str | None) -> dict[str, Any]:
    path = get_status_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    normalized = normalize_status_path(status_path)
    payload: dict[str, Any] = {}
    if normalized:
        payload["status_path"] = normalized

    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return payload


def normalize_status_path(value: object) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None

    path = Path(text).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return os.fspath(path.resolve(strict=False))


def detect_auto_status_path() -> str | None:
    for candidate in _build_auto_candidates():
        if candidate.exists():
            return os.fspath(candidate)
    return None


def resolve_status_path_config() -> dict[str, Any]:
    config = load_status_config()
    configured_path = normalize_status_path(config.get("status_path"))
    auto_detected_path = detect_auto_status_path()
    effective_path = configured_path or auto_detected_path
    if configured_path:
        mode = "configured"
    elif auto_detected_path:
        mode = "auto"
    else:
        mode = "unset"

    file_exists = False
    if effective_path:
        file_exists = Path(effective_path).exists()

    return {
        "status_path": configured_path,
        "auto_detected_path": auto_detected_path,
        "effective_path": effective_path,
        "mode": mode,
        "file_exists": file_exists,
    }


def load_status_payload() -> dict[str, Any]:
    config_state = resolve_status_path_config()
    path_text = config_state["effective_path"]
    if not path_text:
        return {**config_state, "error": "未配置 status.json 路径，也没有探测到默认位置。", "raw_status": None}

    path = Path(path_text)
    if not path.exists():
        return {**config_state, "error": f"状态文件不存在：{path_text}", "raw_status": None}

    try:
        raw_status = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {**config_state, "error": f"读取状态文件失败：{exc}", "raw_status": None}

    if not isinstance(raw_status, dict):
        return {**config_state, "error": "状态文件根节点不是对象。", "raw_status": None}

    return {
        **config_state,
        "error": None,
        "raw_status": sanitize_status_payload(raw_status),
    }


def save_status_payload(raw_status: dict[str, Any]) -> dict[str, Any]:
    config_state = resolve_status_path_config()
    path_text = config_state["effective_path"]
    if not path_text:
        raise ValueError("未配置 status.json 路径，也没有探测到默认位置。")

    target_path = Path(path_text)
    parent_path = target_path.parent
    if not parent_path.exists():
        raise FileNotFoundError(f"状态文件目录不存在：{parent_path}")
    if not parent_path.is_dir():
        raise NotADirectoryError(f"状态文件父路径不是目录：{parent_path}")

    sanitized_raw_status = sanitize_status_payload(raw_status)
    target_path.write_text(
        json.dumps(sanitized_raw_status, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return load_status_payload()


def derive_status_snapshot(raw_status: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    current = now or datetime.now()
    status = sanitize_status_payload(raw_status)

    accounts = _normalize_accounts(status)
    for account in accounts:
        _fill_default_tasks(status[account["name"]], now=current)

    account_items = [_build_account_item(status, account, now=current) for account in accounts]
    recommended_account = _choose_recommended_account(account_items)
    next_task = _choose_next_task(account_items)

    runtime_timers = []
    for name in ("托管重连", "卡死检测"):
        scheduled_at = status.get(name)
        deadline = parse_timestamp(scheduled_at)
        if not scheduled_at or deadline is None:
            continue
        runtime_timers.append(
            {
                "name": name,
                "scheduled_at": scheduled_at,
                "due": deadline <= current,
                "seconds_until_due": int((deadline - current).total_seconds()),
            }
        )

    return {
        "loaded_at": current.strftime("%Y-%m-%d %H:%M:%S"),
        "current_account": _normalize_string(status.get("当前账号")),
        "recommended_account": recommended_account,
        "next_task_path": next_task["path"] if next_task else None,
        "next_task_name": next_task["name"] if next_task else None,
        "next_task_at": next_task["scheduled_at"] if next_task else None,
        "next_task_seconds_until_due": next_task["seconds_until_due"] if next_task else None,
        "program_initialized": bool(status.get("程序初始化", False)),
        "all_tasks_completed": bool(status.get("已执行完所有任务", False)),
        "watchdog_hash": _normalize_string(status.get("卡死检测_last_hash")),
        "runtime_timers": runtime_timers,
        "accounts": account_items,
    }


def parse_timestamp(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None

    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def sanitize_status_payload(raw_status: dict[str, Any]) -> dict[str, Any]:
    sanitized = json.loads(json.dumps(raw_status, ensure_ascii=False))
    for key in _REMOVED_STATUS_KEYS:
        sanitized.pop(key, None)
    return sanitized


def _normalize_accounts(status: dict[str, Any]) -> list[dict[str, str | None]]:
    rows = status.get("账号清单", [])
    accounts: list[dict[str, str | None]] = []
    seen: set[str] = set()

    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, (list, tuple)) or not row:
                continue
            name = _normalize_string(row[0])
            if not name or name in seen:
                continue
            phone = _normalize_string(row[1]) if len(row) > 1 else None
            seen.add(name)
            accounts.append({"name": name, "phone": phone})
            status.setdefault(name, {})

    if accounts:
        return accounts

    for key, value in status.items():
        if isinstance(value, dict):
            name = _normalize_string(key)
            if not name or name in seen:
                continue
            seen.add(name)
            accounts.append({"name": name, "phone": None})
    return accounts


def _fill_default_tasks(account_status: dict[str, Any], *, now: datetime) -> None:
    for task_name in _DEFAULT_DAILY_TASKS:
        account_status.setdefault(task_name, _next_time("-05:00", base_time=now))

    biased_now = now + timedelta(minutes=-6)
    for task_name, anchor in _WINDOW_TASKS:
        account_status.setdefault(task_name, _next_time(anchor, base_time=biased_now))


def _build_account_item(
    status: dict[str, Any],
    account: dict[str, str | None],
    *,
    now: datetime,
) -> dict[str, Any]:
    name = str(account["name"])
    current_account = _normalize_string(status.get("当前账号"))
    task_rows = _extract_tasks(status.get(name, {}), now=now)
    next_task = task_rows[0] if task_rows else None
    due_count = sum(1 for row in task_rows if row["due"])

    for index, row in enumerate(task_rows):
        row["is_next"] = index == 0

    return {
        "name": name,
        "phone": account.get("phone"),
        "is_current": current_account == name,
        "has_due_task": bool(next_task and next_task["due"]),
        "due_count": due_count,
        "task_count": len(task_rows),
        "next_task_name": next_task["name"] if next_task else None,
        "next_task_at": next_task["scheduled_at"] if next_task else None,
        "tasks": task_rows,
    }


def _extract_tasks(account_status: Any, *, now: datetime) -> list[dict[str, Any]]:
    if not isinstance(account_status, dict):
        return []

    tasks: list[tuple[str, str]] = []
    for key, value in account_status.items():
        if isinstance(value, str) and _DATETIME_RE.match(value):
            tasks.append((str(key), value))
    tasks.sort(key=lambda item: item[1])

    rows: list[dict[str, Any]] = []
    for name, scheduled_at in tasks:
        deadline = parse_timestamp(scheduled_at)
        if deadline is None:
            continue
        rows.append(
            {
                "name": name,
                "scheduled_at": scheduled_at,
                "due": deadline <= now,
                "seconds_until_due": int((deadline - now).total_seconds()),
                "is_next": False,
            }
        )
    return rows


def _choose_recommended_account(accounts: list[dict[str, Any]]) -> str | None:
    nearest: tuple[str, str] | None = None
    for account in accounts:
        tasks = account.get("tasks") or []
        if not tasks:
            continue
        first_task = tasks[0]
        if first_task["due"]:
            return account["name"]
        if nearest is None or first_task["scheduled_at"] < nearest[1]:
            nearest = (account["name"], first_task["scheduled_at"])
    return nearest[0] if nearest else None


def _choose_next_task(accounts: list[dict[str, Any]]) -> dict[str, Any] | None:
    nearest: dict[str, Any] | None = None
    for account in accounts:
        tasks = account.get("tasks") or []
        if not tasks:
            continue
        candidate = tasks[0]
        if nearest is None or candidate["scheduled_at"] < nearest["scheduled_at"]:
            nearest = {
                "path": f"{account['name']}/{candidate['name']}",
                "name": candidate["name"],
                "scheduled_at": candidate["scheduled_at"],
                "seconds_until_due": candidate["seconds_until_due"],
            }
    return nearest


def _normalize_string(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _next_time(anchor: str | None = None, *, base_time: datetime | None = None) -> str:
    current = base_time or datetime.now()
    if not anchor:
        target = current
    else:
        match = _CLOCK_RE.fullmatch(anchor.strip())
        if match is None:
            raise ValueError(f"invalid anchor: {anchor!r}")

        is_prev = bool(match.group("prev"))
        hour = int(match.group("hour"))
        minute = int(match.group("minute"))
        target = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if is_prev:
            if target > current:
                target -= timedelta(days=1)
        else:
            if target <= current:
                target += timedelta(days=1)

    return target.strftime("%Y-%m-%d %H:%M:%S")


def _build_auto_candidates() -> list[Path]:
    if os.name != "nt":
        return []

    drives: list[str] = []
    for raw_drive in (Path.cwd().drive, "D:", "C:"):
        text = str(raw_drive or "").strip().upper()
        if not text:
            continue
        if not text.endswith(":"):
            text = f"{text}:"
        if text not in drives:
            drives.append(text)

    return [Path(f"{drive}\\home\\chenkunze\\data\\m2508凡修\\mainwin\\status.json") for drive in drives]
