from __future__ import annotations

import datetime as dt
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Callable

from filelock import FileLock

from backend.core.settings import get_settings


CODEX_WEEKLY_QUOTA_TASK_KEY = "codex_weekly_quota_snapshot"
CODEX_WEEKLY_QUOTA_RUN_TIME = "00:00"
CODEX_USAGE_URL = "https://chatgpt.com/codex/cloud/settings/analytics#usage"
CODEX_WEEKLY_QUOTA_HISTORY_VERSION = 1


class CodexWeeklyQuotaError(RuntimeError):
    pass


class CodexWeeklyQuotaLoginRequired(CodexWeeklyQuotaError):
    pass


def get_codex_weekly_quota_history_path() -> Path:
    return get_settings().data_dir / "codex" / "weekly_quota_history.json"


def parse_codex_weekly_quota_text(text: str) -> dict[str, Any]:
    normalized = re.sub(r"\s+", " ", str(text or "").replace("\u00a0", " ")).strip()
    patterns = (
        r"每周使用限额\s*(\d{1,3})\s*%\s*剩余",
        r"Weekly usage limit\s*(\d{1,3})\s*%\s*(?:left|remaining)",
    )
    remaining_percent: int | None = None
    for pattern in patterns:
        matched = re.search(pattern, normalized, flags=re.IGNORECASE | re.DOTALL)
        if matched:
            remaining_percent = int(matched.group(1))
            break
    if remaining_percent is None or not 0 <= remaining_percent <= 100:
        raise CodexWeeklyQuotaError("未从 Codex 分析页读到每周使用限额的剩余百分比")

    reset_at = ""
    reset_patterns = (
        r"每周使用限额.*?重置时间[：:]\s*(\d{4}年\d{1,2}月\d{1,2}日\s+\d{1,2}:\d{2})",
        r"Weekly usage limit.*?(?:resets?|reset time)[：:]?\s*([A-Za-z]+\s+\d{1,2},?\s+\d{4}[^%]{0,20}\d{1,2}:\d{2})",
    )
    for pattern in reset_patterns:
        matched = re.search(pattern, normalized, flags=re.IGNORECASE)
        if matched:
            reset_at = matched.group(1).strip()
            break
    return {
        "remaining_percent": remaining_percent,
        "reset_at": reset_at,
    }


def read_codex_weekly_quota_history(path: Path | None = None) -> dict[str, Any]:
    resolved_path = path or get_codex_weekly_quota_history_path()
    if not resolved_path.exists():
        return {"version": CODEX_WEEKLY_QUOTA_HISTORY_VERSION, "snapshots": []}
    try:
        payload = json.loads(resolved_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": CODEX_WEEKLY_QUOTA_HISTORY_VERSION, "snapshots": []}
    snapshots = payload.get("snapshots") if isinstance(payload, dict) else []
    return {
        "version": CODEX_WEEKLY_QUOTA_HISTORY_VERSION,
        "snapshots": [dict(item) for item in snapshots if isinstance(item, dict)],
    }


def list_codex_weekly_quota_snapshots(path: Path | None = None) -> list[dict[str, Any]]:
    snapshots = read_codex_weekly_quota_history(path).get("snapshots") or []
    return sorted(
        (dict(item) for item in snapshots if str(item.get("date") or "")),
        key=lambda item: str(item.get("date") or ""),
    )


def record_codex_weekly_quota_snapshot(
    *,
    remaining_percent: int,
    observed_at: dt.datetime,
    reset_at: str = "",
    path: Path | None = None,
) -> dict[str, Any]:
    if not 0 <= int(remaining_percent) <= 100:
        raise ValueError("Codex 每周余额必须在 0 到 100 之间")
    resolved_path = path or get_codex_weekly_quota_history_path()
    resolved_path.parent.mkdir(parents=True, exist_ok=True)
    effective_date = (observed_at.date() - dt.timedelta(days=1)).isoformat()
    record = {
        "date": effective_date,
        "remaining_percent": int(remaining_percent),
        "observed_at": observed_at.replace(microsecond=0).isoformat(),
        "reset_at": str(reset_at or "").strip(),
        "source_url": CODEX_USAGE_URL,
    }

    lock_path = resolved_path.with_suffix(f"{resolved_path.suffix}.lock")
    with FileLock(str(lock_path), timeout=10):
        history = read_codex_weekly_quota_history(resolved_path)
        snapshots = [
            dict(item)
            for item in history.get("snapshots") or []
            if str(item.get("date") or "") != effective_date
        ]
        snapshots.append(record)
        snapshots.sort(key=lambda item: str(item.get("date") or ""))
        payload = {
            "version": CODEX_WEEKLY_QUOTA_HISTORY_VERSION,
            "snapshots": snapshots,
        }
        temp_path = resolved_path.with_suffix(f"{resolved_path.suffix}.{os.getpid()}.tmp")
        temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp_path, resolved_path)
    return record


def _find_reusable_usage_tab(browser: Any) -> Any | None:
    for tab in browser.get_tabs():
        url = str(getattr(tab, "url", "") or "")
        if url.startswith("https://chatgpt.com/codex/cloud/settings/analytics") or url == "https://chatgpt.com/#usage":
            return tab
    return None


def _read_usage_page_text(tab: Any, *, timeout_seconds: float) -> str:
    deadline = time.monotonic() + max(1.0, float(timeout_seconds))
    latest_text = ""
    while time.monotonic() < deadline:
        body = tab.ele("tag:body", timeout=1)
        latest_text = str(body.text if body else "")
        try:
            return_text = parse_codex_weekly_quota_text(latest_text)
        except CodexWeeklyQuotaError:
            return_text = None
        if return_text is not None:
            return latest_text
        tab.wait(0.25)
    return latest_text


def collect_codex_weekly_quota_snapshot(
    *,
    now: dt.datetime | None = None,
    history_path: Path | None = None,
    browser_factory: Callable[[], Any] | None = None,
    timeout_seconds: float = 25.0,
) -> dict[str, Any]:
    if browser_factory is None:
        from DrissionPage import Chromium

        browser_factory = Chromium

    observed_at = (now or dt.datetime.now()).replace(microsecond=0)
    browser = browser_factory()
    tab = _find_reusable_usage_tab(browser)
    created_tab = tab is None
    if tab is None:
        tab = browser.new_tab()
    try:
        tab.set.timeouts(base=10, page_load=max(15, int(timeout_seconds)))
        tab.get(CODEX_USAGE_URL, timeout=max(15, int(timeout_seconds)))
        page_text = _read_usage_page_text(tab, timeout_seconds=timeout_seconds)
        page_url = str(getattr(tab, "url", "") or "")
        if "/codex/cloud/settings/analytics" not in page_url and re.search(r"登录|Log in|Sign in", page_text, re.IGNORECASE):
            raise CodexWeeklyQuotaLoginRequired(
                "DrissionPage 默认浏览器尚未登录 ChatGPT；已保留用量页，请先在该窗口完成登录"
            )
        parsed = parse_codex_weekly_quota_text(page_text)
        record = record_codex_weekly_quota_snapshot(
            remaining_percent=int(parsed["remaining_percent"]),
            observed_at=observed_at,
            reset_at=str(parsed.get("reset_at") or ""),
            path=history_path,
        )
        print(
            "Codex weekly quota recorded: "
            f"date={record['date']} remaining={record['remaining_percent']}% observed_at={record['observed_at']}"
        )
        return record
    finally:
        if created_tab and "/codex/cloud/settings/analytics" in str(getattr(tab, "url", "") or ""):
            if len(browser.tab_ids) > 1:
                tab.close()
