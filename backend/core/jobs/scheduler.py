from __future__ import annotations

from dataclasses import dataclass
import datetime as dt
import threading
import time
from pathlib import Path
from typing import Any, Callable

from filelock import FileLock, Timeout
from sqlmodel import Session

from pyxllib.prog.behavior_tree import Action, BehaviorTreeRunner, DynamicTime, IdleUntilNextWake, MemorySelector, NextWake, Root, Sequence, Status
from pyxllib.prog.schedule_policy import compute_next_trigger_at, schedule_policy_label

from backend.core.jobs.executor import background_task_queue
from backend.core.jobs.local_runtime import find_active_local_job_run, submit_local_job
from backend.core.codex.weekly_quota import (
    CODEX_WEEKLY_QUOTA_RUN_TIME,
    CODEX_WEEKLY_QUOTA_TASK_KEY,
    collect_codex_weekly_quota_snapshot,
)
from backend.core.dp_browser_tab_cleanup import (
    DP_BROWSER_TAB_CLEANUP_TASK_KEY,
    run_dp_browser_tab_cleanup,
)
from backend.core.attendance.course_completion import (
    COURSE_COMPLETION_RUN_TIME,
    COURSE_COMPLETION_TASK_KEY,
    enqueue_attendance_course_completion_job,
)
from backend.core.fanxiu_wechat_reminder import (
    FANXIU_WECHAT_BOSS_REMINDER_RUN_TIME,
    FANXIU_WECHAT_BOSS_REMINDER_TASK_KEY,
    FANXIU_WECHAT_SHENGZU_REMINDER_RUN_TIME,
    FANXIU_WECHAT_SHENGZU_REMINDER_TASK_KEY,
    FANXIU_WECHAT_SHENGZU_REMINDER_WEEKDAYS,
    enqueue_fanxiu_wechat_boss_reminder,
    enqueue_fanxiu_wechat_shengzu_reminder,
)
from backend.core.runtime.public_frontend_deploy import (
    PUBLIC_FRONTEND_DEPLOY_TASK_KEY,
    run_public_frontend_deploy_check,
)
from backend.core.library.ruanyf_weekly_excerpt_book import (
    RUANYF_WEEKLY_EXCERPT_BOOK_RUN_TIME,
    RUANYF_WEEKLY_EXCERPT_BOOK_TASK_KEY,
    RUANYF_WEEKLY_EXCERPT_BOOK_WEEKDAYS,
    enqueue_ruanyf_weekly_excerpt_book_job,
)
from backend.core.library.x_archive import (
    TIBO_X_ARCHIVE_TASK_KEY,
    enqueue_tibo_x_archive_job,
)
from backend.core.settings import get_settings
from backend.core.notes.weekly_scheduler import RUANYF_WEEKLY_TASK_NAME, enqueue_ruanyf_weekly_note_job
from backend.core.xiaoe_incremental_job import (
    XIAOE_INCREMENTAL_UPDATE_RUN_TIME,
    XIAOE_INCREMENTAL_UPDATE_TASK_KEY,
    XIAOE_INCREMENTAL_UPDATE_WEEKDAYS,
    enqueue_xiaoe_incremental_update_job,
)
from backend.models import AppSetting


TaskAction = Callable[[], Any]
CODEX_DIARY_RUN_TIME = "00:10"
ATTENDANCE_SUMMARY_RUN_TIME = "00:00"
MEDIA_SYNC_HOME_DISCOVERY_TASK_KEY = "media_sync_home_discovery"
MEDIA_SYNC_HOME_DISCOVERY_RUN_TIME = "00:25"
MEDIA_SYNC_PIXIV_DAILY_ACQUISITION_COUNT = 1000
MEDIA_SYNC_PINTEREST_DAILY_ACQUISITION_COUNT = 500
STORAGE_ANALYSIS_RUN_TIME = "01:00"
MARKET_QUOTE_REFRESH_TASK_KEY = "market_quote_refresh"
MARKET_INTRADAY_PERSIST_TASK_KEY = "market_intraday_persist"
MARKET_INTRADAY_PERSIST_RUN_TIME = "16:30"
HK_CONNECT_MOMENTUM_REVIEW_TASK_KEY = "hk_connect_momentum_review"
HK_CONNECT_MOMENTUM_REVIEW_RUN_TIME = "17:40"
WECHAT_ARCHIVE_INCREMENTAL_SYNC_TASK_KEY = "wechat_archive_incremental_sync"
WECHAT_MOMENTS_INCREMENTAL_SYNC_TASK_KEY = "wechat_moments_incremental_sync"
NOTE_SHEET_PAGE_SNAPSHOT_BACKFILL_TASK_KEY = "note_sheet_page_snapshot_backfill"
RIME_CONTEXT_REFRESH_TASK_KEY = "rime_context_refresh"
RIME_CONTEXT_LINT_TASK_KEY = "rime_context_lint"
RUANYF_WEEKLY_START_TIME = "06:00"
PUBLIC_FRONTEND_DEPLOY_INTERVAL_MINUTES = 30
BACKGROUND_TASK_SCHEDULE_STATE_VERSION = 20
BACKGROUND_QUEUE_POLL_SECONDS = 1.0
SCHEDULE_VERSIONED_TASK_KEYS = {
    "codex_diary_yesterday_import",
    CODEX_WEEKLY_QUOTA_TASK_KEY,
    RUANYF_WEEKLY_TASK_NAME,
    RUANYF_WEEKLY_EXCERPT_BOOK_TASK_KEY,
    TIBO_X_ARCHIVE_TASK_KEY,
    "attendance_summary_monthly_templates",
    COURSE_COMPLETION_TASK_KEY,
    MEDIA_SYNC_HOME_DISCOVERY_TASK_KEY,
    "storage_analysis",
    MARKET_QUOTE_REFRESH_TASK_KEY,
    MARKET_INTRADAY_PERSIST_TASK_KEY,
    HK_CONNECT_MOMENTUM_REVIEW_TASK_KEY,
    FANXIU_WECHAT_BOSS_REMINDER_TASK_KEY,
    FANXIU_WECHAT_SHENGZU_REMINDER_TASK_KEY,
    WECHAT_ARCHIVE_INCREMENTAL_SYNC_TASK_KEY,
    WECHAT_MOMENTS_INCREMENTAL_SYNC_TASK_KEY,
    NOTE_SHEET_PAGE_SNAPSHOT_BACKFILL_TASK_KEY,
    RIME_CONTEXT_REFRESH_TASK_KEY,
    RIME_CONTEXT_LINT_TASK_KEY,
    DP_BROWSER_TAB_CLEANUP_TASK_KEY,
    PUBLIC_FRONTEND_DEPLOY_TASK_KEY,
    XIAOE_INCREMENTAL_UPDATE_TASK_KEY,
}


@dataclass(frozen=True)
class BackgroundTaskSpec:
    key: str
    title: str
    category: str
    description: str
    schedule_label: str
    retry_label: str
    action: TaskAction
    manual_warning: str = ""
    default_visible: bool = True


DEFAULT_ENABLED_TASK_KEYS: set[str] = set()


class _StoppableBehaviorTreeRunner(BehaviorTreeRunner):
    def __init__(
        self,
        *args: Any,
        stop_event: threading.Event,
        wake_event: threading.Event,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._stop_event = stop_event
        self._wake_event = wake_event

    def sleep(self, seconds: float) -> None:
        deadline = time.monotonic() + max(0.0, float(seconds))
        while not self._stop_event.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            wait_seconds = min(remaining, 1.0)
            self._wake_event.wait(wait_seconds)
            if self._wake_event.is_set():
                self._wake_event.clear()
                return


def _setting_key(task_key: str) -> str:
    return f"background_task.{task_key}.enabled"


def _deleted_setting_key(task_key: str) -> str:
    return f"background_task.{task_key}.deleted"


def _schedule_policy_setting_key(task_key: str) -> str:
    return f"background_task.{task_key}.schedule_policy"


def _retry_after_policy(minutes: int) -> dict[str, Any]:
    return {
        "on_failure": {"type": "retry_after", "minutes": minutes},
        "on_timeout": {"type": "retry_after", "minutes": minutes},
    }


def _job_schedule_policy(trigger: dict[str, Any], *, retry_minutes: int | None = None) -> dict[str, Any]:
    policy: dict[str, Any] = {
        "enabled": True,
        "trigger": trigger,
        "action": {"type": "enqueue"},
        "concurrency": {"scope": "group", "policy": "queue"},
    }
    if retry_minutes:
        policy["outcome"] = _retry_after_policy(retry_minutes)
    return policy


def _storage_analysis_schedule_policy() -> dict[str, Any]:
    cron = ""
    try:
        from apscheduler.triggers.cron import CronTrigger
        from backend.db import engine

        with Session(engine) as session:
            row = session.get(AppSetting, "storage.schedule")
            if row and isinstance(row.value, dict):
                cron = str(row.value.get("cron_expression") or "").strip()
        if cron:
            CronTrigger.from_crontab(cron)
    except Exception:
        cron = ""

    if cron:
        return _job_schedule_policy({"type": "cron", "expression": cron})
    return _job_schedule_policy({"type": "daily", "time": STORAGE_ANALYSIS_RUN_TIME})


def _default_background_task_schedule_policy(task_key: str) -> dict[str, Any] | None:
    if task_key == CODEX_WEEKLY_QUOTA_TASK_KEY:
        return _job_schedule_policy({"type": "daily", "time": CODEX_WEEKLY_QUOTA_RUN_TIME}, retry_minutes=10)
    if task_key == "codex_diary_yesterday_import":
        return _job_schedule_policy({"type": "daily", "time": CODEX_DIARY_RUN_TIME}, retry_minutes=10)
    if task_key == RUANYF_WEEKLY_TASK_NAME:
        return _job_schedule_policy(
            {"type": "weekly", "weekdays": [5], "time": RUANYF_WEEKLY_START_TIME},
            retry_minutes=10,
        )
    if task_key == RUANYF_WEEKLY_EXCERPT_BOOK_TASK_KEY:
        return _job_schedule_policy(
            {
                "type": "weekly",
                "weekdays": list(RUANYF_WEEKLY_EXCERPT_BOOK_WEEKDAYS),
                "time": RUANYF_WEEKLY_EXCERPT_BOOK_RUN_TIME,
            },
            retry_minutes=10,
        )
    if task_key == TIBO_X_ARCHIVE_TASK_KEY:
        return _job_schedule_policy(
            {"type": "interval", "minutes": 60, "anchor": "last_finish"},
            retry_minutes=10,
        )
    if task_key == "attendance_summary_monthly_templates":
        return _job_schedule_policy({"type": "monthly", "day": 27, "time": ATTENDANCE_SUMMARY_RUN_TIME}, retry_minutes=10)
    if task_key == COURSE_COMPLETION_TASK_KEY:
        return _job_schedule_policy({"type": "daily", "time": COURSE_COMPLETION_RUN_TIME}, retry_minutes=10)
    if task_key == MEDIA_SYNC_HOME_DISCOVERY_TASK_KEY:
        return _job_schedule_policy({"type": "daily", "time": MEDIA_SYNC_HOME_DISCOVERY_RUN_TIME}, retry_minutes=10)
    if task_key == FANXIU_WECHAT_BOSS_REMINDER_TASK_KEY:
        return _job_schedule_policy({"type": "daily", "time": FANXIU_WECHAT_BOSS_REMINDER_RUN_TIME})
    if task_key == FANXIU_WECHAT_SHENGZU_REMINDER_TASK_KEY:
        return _job_schedule_policy(
            {
                "type": "weekly",
                "weekdays": list(FANXIU_WECHAT_SHENGZU_REMINDER_WEEKDAYS),
                "time": FANXIU_WECHAT_SHENGZU_REMINDER_RUN_TIME,
            }
        )
    if task_key == MARKET_QUOTE_REFRESH_TASK_KEY:
        return _job_schedule_policy({"type": "interval", "minutes": 60, "anchor": "last_finish"})
    if task_key == MARKET_INTRADAY_PERSIST_TASK_KEY:
        return _job_schedule_policy({"type": "daily", "time": MARKET_INTRADAY_PERSIST_RUN_TIME}, retry_minutes=10)
    if task_key == HK_CONNECT_MOMENTUM_REVIEW_TASK_KEY:
        return _job_schedule_policy({"type": "daily", "time": HK_CONNECT_MOMENTUM_REVIEW_RUN_TIME}, retry_minutes=10)
    if task_key == WECHAT_ARCHIVE_INCREMENTAL_SYNC_TASK_KEY:
        return _job_schedule_policy(
            {"type": "interval", "minutes": 120, "anchor": "last_finish"},
            retry_minutes=10,
        )
    if task_key == WECHAT_MOMENTS_INCREMENTAL_SYNC_TASK_KEY:
        return _job_schedule_policy(
            {"type": "interval", "minutes": 360, "anchor": "last_finish"},
            retry_minutes=10,
        )
    if task_key == RIME_CONTEXT_REFRESH_TASK_KEY:
        return _job_schedule_policy({"type": "interval", "minutes": 360, "anchor": "last_finish"}, retry_minutes=10)
    if task_key == RIME_CONTEXT_LINT_TASK_KEY:
        return _job_schedule_policy({"type": "daily", "time": "03:20"}, retry_minutes=10)
    if task_key == DP_BROWSER_TAB_CLEANUP_TASK_KEY:
        return _job_schedule_policy({"type": "interval", "minutes": 60, "anchor": "last_finish"})
    if task_key == PUBLIC_FRONTEND_DEPLOY_TASK_KEY:
        return _job_schedule_policy(
            {
                "type": "interval",
                "minutes": PUBLIC_FRONTEND_DEPLOY_INTERVAL_MINUTES,
                "anchor": "last_finish",
            },
            retry_minutes=10,
        )
    if task_key == XIAOE_INCREMENTAL_UPDATE_TASK_KEY:
        return _job_schedule_policy(
            {
                "type": "weekly",
                "weekdays": list(XIAOE_INCREMENTAL_UPDATE_WEEKDAYS),
                "time": XIAOE_INCREMENTAL_UPDATE_RUN_TIME,
            },
            retry_minutes=30,
        )
    if task_key == "storage_analysis":
        return _storage_analysis_schedule_policy()
    return None


def _read_background_task_schedule_policy(task_key: str, session: Session | None = None) -> dict[str, Any] | None:
    def _read(current_session: Session) -> dict[str, Any] | None:
        row = current_session.get(AppSetting, _schedule_policy_setting_key(task_key))
        if not row or not isinstance(row.value, dict):
            return None
        policy = row.value.get("policy")
        return dict(policy) if isinstance(policy, dict) else None

    if session is not None:
        return _read(session)

    from backend.db import engine

    with Session(engine) as current_session:
        return _read(current_session)


def _effective_background_task_schedule_policy(task_key: str, *, enabled: bool | None = None) -> dict[str, Any] | None:
    policy = _read_background_task_schedule_policy(task_key) or _default_background_task_schedule_policy(task_key)
    if policy is None:
        return None
    policy = dict(policy)
    policy["enabled"] = bool(_is_task_enabled(task_key) if enabled is None else enabled)
    return policy


def set_background_task_schedule_policy(task_key: str, policy: dict[str, Any] | None) -> None:
    from backend.db import engine

    with Session(engine) as session:
        row = session.get(AppSetting, _schedule_policy_setting_key(task_key))
        if policy:
            if row is None:
                row = AppSetting(key=_schedule_policy_setting_key(task_key))
            next_policy = dict(policy)
            next_policy["enabled"] = True
            row.value = {"policy": next_policy}
            row.updated_at = time.time()
            session.add(row)
        elif row is not None:
            session.delete(row)
        session.commit()


def _is_task_deleted(task_key: str, session: Session | None = None) -> bool:
    def _read(current_session: Session) -> bool:
        row = current_session.get(AppSetting, _deleted_setting_key(task_key))
        if row and isinstance(row.value, dict):
            return bool(row.value.get("deleted", False))
        spec = get_background_task_spec(task_key)
        return bool(spec and not spec.default_visible)

    if session is not None:
        return _read(session)

    from backend.db import engine

    with Session(engine) as current_session:
        return _read(current_session)


def _is_task_enabled(task_key: str) -> bool:
    from backend.db import engine

    with Session(engine) as session:
        if _is_task_deleted(task_key, session):
            return False
        row = session.get(AppSetting, _setting_key(task_key))
        if row and isinstance(row.value, dict):
            return bool(row.value.get("enabled", False))
        if task_key in DEFAULT_ENABLED_TASK_KEYS:
            return True
        if task_key == "storage_analysis":
            storage_row = session.get(AppSetting, "storage.schedule")
            if storage_row and isinstance(storage_row.value, dict):
                return bool(storage_row.value.get("schedule_enabled", False))
    return False


def set_background_task_enabled(task_key: str, enabled: bool) -> None:
    from backend.db import engine

    with Session(engine) as session:
        row = session.get(AppSetting, _setting_key(task_key))
        if row is None:
            row = AppSetting(key=_setting_key(task_key))
        row.value = {"enabled": bool(enabled)}
        row.updated_at = time.time()
        session.add(row)
        session.commit()


def set_background_task_deleted(task_key: str, deleted: bool = True) -> None:
    from backend.db import engine

    with Session(engine) as session:
        deleted_row = session.get(AppSetting, _deleted_setting_key(task_key))
        if deleted_row is None:
            deleted_row = AppSetting(key=_deleted_setting_key(task_key))
        deleted_row.value = {"deleted": bool(deleted)}
        deleted_row.updated_at = time.time()
        session.add(deleted_row)

        if deleted:
            enabled_row = session.get(AppSetting, _setting_key(task_key))
            if enabled_row is None:
                enabled_row = AppSetting(key=_setting_key(task_key))
            enabled_row.value = {"enabled": False}
            enabled_row.updated_at = time.time()
            session.add(enabled_row)

        session.commit()


def is_background_task_visible(task_key: str, session: Session | None = None) -> bool:
    return not _is_task_deleted(task_key, session)


def _enqueue_storage_analysis() -> str:
    from backend.api.admin import enqueue_storage_analysis_job

    return enqueue_storage_analysis_job()


def _enqueue_attendance_summary() -> str:
    active = find_active_local_job_run("attendance.summary-templates")
    if active is not None:
        return active.id
    return submit_local_job(job_type="attendance.summary-templates", payload={}).id


def _enqueue_codex_diary() -> str | None:
    from backend.api.notes import maybe_enqueue_codex_diary_yesterday_import

    return maybe_enqueue_codex_diary_yesterday_import(trigger_reason="scheduled")


def _enqueue_codex_weekly_quota_snapshot() -> str:
    task_id, _queued = background_task_queue.enqueue_once(
        CODEX_WEEKLY_QUOTA_TASK_KEY,
        collect_codex_weekly_quota_snapshot,
        resource_lock="resource:browser",
    )
    return task_id


def _enqueue_ruanyf_weekly_note() -> str | None:
    return enqueue_ruanyf_weekly_note_job()


def _run_media_sync_home_discovery_job() -> None:
    try:
        from backend.plugins.modules.media_sync.runtime import run_scheduled_home_discovery
    except Exception as exc:
        print(f"Media sync home discovery skipped: plugin unavailable ({exc})")
        return

    result = run_scheduled_home_discovery(
        target_counts={
            "pixiv": MEDIA_SYNC_PIXIV_DAILY_ACQUISITION_COUNT,
            "pinterest": MEDIA_SYNC_PINTEREST_DAILY_ACQUISITION_COUNT,
        }
    )
    print(
        "Media candidate replenishment completed: "
        f"profiles={result.get('profile_count', 0)} "
        f"targets={result.get('target_counts', {})} "
        f"success={result.get('success_count', 0)} "
        f"failed={len(result.get('failures') or {})}"
    )
    from backend.core.media_membership_reconcile import enqueue_all_media_membership_reconciles

    reconciliation = enqueue_all_media_membership_reconciles()
    print(
        "Media membership reconciliation scheduled: "
        f"profiles={reconciliation['profile_count']} "
        f"jobs={len(reconciliation['jobs'])} "
        f"queued={reconciliation['queued_count']}"
    )
    if result.get("failures"):
        raise RuntimeError(f"媒体候选补齐未完成：{result['failures']}")


def _enqueue_media_sync_home_discovery() -> str | None:
    active = find_active_local_job_run("media.scheduled-discovery")
    if active is not None:
        return active.id
    return submit_local_job(job_type="media.scheduled-discovery", payload={}).id


def _run_market_quote_refresh_job() -> None:
    from sqlmodel import select

    from backend.core.stock import refresh_market_quotes_from_akshare
    from backend.db import engine
    from backend.models import EastmoneyPositionSnapshot

    with Session(engine) as session:
        user_ids = sorted(
            {
                int(user_id)
                for user_id in session.exec(select(EastmoneyPositionSnapshot.user_id).distinct()).all()
                if user_id is not None
            }
        )
        if not user_ids:
            print("Market quote refresh skipped: no Eastmoney positions.")
            return

        refreshed_count = 0
        error_count = 0
        failures: list[str] = []
        for user_id in user_ids:
            try:
                result = refresh_market_quotes_from_akshare(session, user_id=user_id)
            except Exception as exc:
                failures.append(f"user={user_id}: {exc}")
                continue
            refreshed_count += result.refreshed_count
            error_count += result.error_count

    message = f"Market quote refresh completed: users={len(user_ids)} refreshed={refreshed_count} errors={error_count}"
    if failures:
        message += " failures=" + " | ".join(failures[:3])
    print(message)


def _enqueue_market_quote_refresh() -> str | None:
    active = find_active_local_job_run("stock.market-quote-refresh")
    if active is not None:
        return active.id
    return submit_local_job(job_type="stock.market-quote-refresh", payload={}).id


def _run_market_intraday_persist_job() -> dict:
    from backend.api.eastmoney import run_market_intraday_persist_snapshot_job

    payload = run_market_intraday_persist_snapshot_job()
    print(
        "Market intraday persist completed: "
        f"persisted={payload.get('persisted')} "
        f"failed={payload.get('failed')}"
    )
    return payload


def _enqueue_market_intraday_persist() -> str | None:
    active = find_active_local_job_run("stock.market-intraday-persist")
    if active is not None:
        return active.id
    return submit_local_job(job_type="stock.market-intraday-persist", payload={}).id


def _run_hk_connect_momentum_review_job() -> dict:
    from backend.api.eastmoney import run_hk_connect_momentum_review_snapshot_job

    payload = run_hk_connect_momentum_review_snapshot_job()
    print(
        "HK connect momentum review completed: "
        f"signal_date={payload.get('signal_date')} "
        f"action={payload.get('action')} "
        f"selected={len(payload.get('selected') or [])}"
    )
    return payload


def _enqueue_hk_connect_momentum_review() -> str | None:
    active = find_active_local_job_run("stock.hk-connect-momentum-review")
    if active is not None:
        return active.id
    return submit_local_job(job_type="stock.hk-connect-momentum-review", payload={}).id


def _enqueue_wechat_archive_incremental_sync() -> str | None:
    from backend.api.wechat_archive import _enqueue_wechat_db_live_sync

    try:
        return _enqueue_wechat_db_live_sync({
            "mode": "db_storage_live",
            "save_media": True,
        })
    except Exception as exc:
        if getattr(exc, "status_code", None) == 409:
            print("WeChat archive sync skipped: task already queued or running.")
            return None
        raise


def _enqueue_wechat_moments_incremental_sync() -> str | None:
    active = find_active_local_job_run("archive.wechat-moments")
    if active is not None:
        return active.id
    return submit_local_job(
        job_type="archive.wechat-moments",
        payload={"download_media": True, "media_preview_limit": 100},
    ).id


def _run_note_sheet_page_snapshot_backfill_job() -> dict[str, Any]:
    from backend.api.note_sheets import backfill_default_sheet_page_snapshots
    from backend.db import engine

    with Session(engine) as session:
        result = backfill_default_sheet_page_snapshots(session)
    print(
        "Note sheet page snapshot backfill completed: "
        f"candidates={result.get('candidate_count')} "
        f"processed={len(result.get('processed') or [])} "
        f"created={result.get('created')} "
        f"refreshed={result.get('refreshed')} "
        f"skipped={len(result.get('skipped') or [])}"
    )
    return result


def _enqueue_note_sheet_page_snapshot_backfill() -> str | None:
    active = find_active_local_job_run("notes.sheet-page-snapshot-backfill")
    if active is not None:
        return active.id
    return submit_local_job(job_type="notes.sheet-page-snapshot-backfill", payload={}).id


def _run_rime_context_refresh_job() -> dict[str, Any]:
    from backend.core.ai.rime_context_prediction import refresh_rime_context_prediction_tree

    result = refresh_rime_context_prediction_tree(limit=50000, source="snapshot")
    print(
        "Rime context refresh completed: "
        f"available={result.get('available')} "
        f"status={result.get('status')} "
        f"nodes={len(result.get('nodes') or [])}"
    )
    return result


def _enqueue_rime_context_refresh() -> str | None:
    active = find_active_local_job_run("rime.context-refresh")
    if active is not None:
        return active.id
    return submit_local_job(job_type="rime.context-refresh", payload={}).id


def _run_rime_context_lint_job() -> dict[str, Any]:
    from backend.core.ai.rime_context_prediction import collect_rime_context_prediction_lint

    result = collect_rime_context_prediction_lint(source="all")
    print(
        "Rime context lint completed: "
        f"available={result.get('available')} "
        f"status={result.get('status')} "
        f"issues={len(result.get('issues') or [])}"
    )
    return result


def _enqueue_rime_context_lint() -> str | None:
    active = find_active_local_job_run("rime.context-lint")
    if active is not None:
        return active.id
    return submit_local_job(job_type="rime.context-lint", payload={}).id


def _enqueue_dp_browser_tab_cleanup() -> str | None:
    active = find_active_local_job_run("browser.dp-tab-cleanup")
    if active is not None:
        return active.id
    return submit_local_job(job_type="browser.dp-tab-cleanup", payload={}).id


def _enqueue_public_frontend_deploy() -> str | None:
    active = find_active_local_job_run("frontend.public-deploy-check")
    if active is not None:
        return active.id
    return submit_local_job(job_type="frontend.public-deploy-check", payload={}).id


BACKGROUND_TASK_SPECS: tuple[BackgroundTaskSpec, ...] = (
    BackgroundTaskSpec(
        key=CODEX_WEEKLY_QUOTA_TASK_KEY,
        title="Codex 每周余额记录",
        category="AI",
        description="每天零点读取 Codex 分析页的每周使用限额，将剩余百分比记到前一天，并在星图日历中替代当天的工作小时显示。",
        schedule_label=f"每天 {CODEX_WEEKLY_QUOTA_RUN_TIME}",
        retry_label="失败后 10 分钟重试",
        action=_enqueue_codex_weekly_quota_snapshot,
        manual_warning="会复用 DrissionPage 默认浏览器访问 ChatGPT；首次使用需在该浏览器中完成登录。",
        default_visible=False,
    ),
    BackgroundTaskSpec(
        key=XIAOE_INCREMENTAL_UPDATE_TASK_KEY,
        title="小鹅通课程增量归档",
        category="考勤",
        description="每周按视频、音频、图文顺序检查小鹅通后台新增课程，并将新内容串行归档到本地资料库。",
        schedule_label=f"每周日 {XIAOE_INCREMENTAL_UPDATE_RUN_TIME}",
        retry_label="失败后 30 分钟重试",
        action=enqueue_xiaoe_incremental_update_job,
        manual_warning="会使用小鹅通后台登录态下载新增视频、音频和图文；已有全量任务未完成时跳过对应类别，全程不并行下载。",
        default_visible=False,
    ),
    BackgroundTaskSpec(
        key=PUBLIC_FRONTEND_DEPLOY_TASK_KEY,
        title="公网前端发布",
        category="部署",
        description="每半小时检查前端源文件指纹，有变化才构建 dist；只有构建、上传和 yun 软链接切换全部成功后，公网才更新到新版本。",
        schedule_label=f"每 {PUBLIC_FRONTEND_DEPLOY_INTERVAL_MINUTES} 分钟检查",
        retry_label="失败后 10 分钟重试",
        action=_enqueue_public_frontend_deploy,
        manual_warning="会执行前端生产构建，并在成功后把 dist 上传到 yun 的静态站点目录；失败不会影响当前公网版本。",
    ),
    BackgroundTaskSpec(
        key="codex_diary_yesterday_import",
        title="Codex 星图日记",
        category="AI",
        description="每天凌晨读取昨日 Codex 会话，复用现有日记导入流程写入星图笔记。",
        schedule_label=f"每天 {CODEX_DIARY_RUN_TIME}",
        retry_label="失败后 10 分钟重试",
        action=_enqueue_codex_diary,
        manual_warning="会调用 AI 配置里的 Codex 星图日记模型生成昨日总结；已导入过的日期会自动跳过。",
    ),
    BackgroundTaskSpec(
        key=RUANYF_WEEKLY_TASK_NAME,
        title="阮一峰周刊笔记",
        category="笔记",
        description="周五发布窗口轮询阮一峰科技爱好者周刊，发现新一期后复用现有周刊笔记模板写入星图笔记。",
        schedule_label=f"每周五 {RUANYF_WEEKLY_START_TIME}",
        retry_label="失败后 10 分钟重试",
        action=_enqueue_ruanyf_weekly_note,
        manual_warning="会访问 GitHub 上的 ruanyf/weekly 仓库；已写入过当前发布窗口的新一期会自动跳过。",
    ),
    BackgroundTaskSpec(
        key=RUANYF_WEEKLY_EXCERPT_BOOK_TASK_KEY,
        title="科技周刊摘抄入书",
        category="笔记",
        description="每周日凌晨扫描星图日记中的科技周刊摘抄，把书中尚缺的期数补入“我的科技周刊摘抄”；延期补写的旧期也会在后续扫描中补齐。",
        schedule_label=f"每周日 {RUANYF_WEEKLY_EXCERPT_BOOK_RUN_TIME}",
        retry_label="失败后 10 分钟重试",
        action=enqueue_ruanyf_weekly_excerpt_book_job,
        manual_warning="会读取星图日记并更新匹配的个人摘抄书；按期号去重，不会重复加入同一期。",
        default_visible=False,
    ),
    BackgroundTaskSpec(
        key=TIBO_X_ARCHIVE_TASK_KEY,
        title="Tibo X 消息摘录",
        category="图书馆",
        description="每小时增量抓取 @thsottiaux 的公开 X 消息，翻译成中文后更新图书馆动态摘录；首次回溯最近 30 天，书内始终按时间倒序展示。",
        schedule_label="每小时检查",
        retry_label="失败后 10 分钟重试",
        action=enqueue_tibo_x_archive_job,
        manual_warning="会访问 X 的公开 RSS 镜像并调用本机 AI 翻译；按消息 ID 去重，只更新专用摘录书。",
        default_visible=False,
    ),
    BackgroundTaskSpec(
        key="attendance_summary_monthly_templates",
        title="考勤汇总模板",
        category="表格",
        description="每月 27 日凌晨为考勤汇总表补下月模板。",
        schedule_label=f"每月 27 日 {ATTENDANCE_SUMMARY_RUN_TIME}",
        retry_label="失败后 10 分钟重试",
        action=_enqueue_attendance_summary,
    ),
    BackgroundTaskSpec(
        key=COURSE_COMPLETION_TASK_KEY,
        title="考勤课程自动收尾",
        category="考勤",
        description="每天扫描课程汇总页，自动将结束日已过且统计已就绪的念住/觉观月课与梵呗课按既有完课动作移动到已完成组，并从 kqmain.py 的觉观念住行为树列表中移除对应念住/觉观课程。",
        schedule_label=f"每天 {COURSE_COMPLETION_RUN_TIME}",
        retry_label="失败后 10 分钟重试",
        action=enqueue_attendance_course_completion_job,
        manual_warning="会修改工作簿 2 的课程汇总表，并编辑 kq5034/kqmain.py 中的觉观念住类型列表；不会删除课程脚本文件。",
        default_visible=False,
    ),
    BackgroundTaskSpec(
        key=MEDIA_SYNC_HOME_DISCOVERY_TASK_KEY,
        title="媒体候选补齐",
        category="图片",
        description="每天新增落盘 Pixiv 1000 张、Pinterest 500 张到本地备货仓库；待整理区分别维持 200 张。Pixiv 不保存 URL 候选缓存。",
        schedule_label=f"每天 {MEDIA_SYNC_HOME_DISCOVERY_RUN_TIME}",
        retry_label="失败后 10 分钟重试",
        action=_enqueue_media_sync_home_discovery,
        manual_warning="会使用媒体同步插件和浏览器登录态访问外部推荐流；每日新增 Pixiv 1000 张、Pinterest 500 张到备货仓库，并把各待整理区补到 200 张。",
    ),
    BackgroundTaskSpec(
        key=FANXIU_WECHAT_BOSS_REMINDER_TASK_KEY,
        title="凡修魔狱封阵微信群提醒",
        category="凡修",
        description="每天在魔狱封阵前通过 CodeYun 微信 iLink 接入提醒三清道宗微信群。",
        schedule_label=f"每天 {FANXIU_WECHAT_BOSS_REMINDER_RUN_TIME}",
        retry_label="失败后下次调度重试",
        action=enqueue_fanxiu_wechat_boss_reminder,
        manual_warning="会通过已连接的微信 iLink 账号向三清道宗群发送 @所有人 提醒；需要配置接入账号和接收群。",
        default_visible=False,
    ),
    BackgroundTaskSpec(
        key=FANXIU_WECHAT_SHENGZU_REMINDER_TASK_KEY,
        title="凡修圣祖微信群提醒",
        category="凡修",
        description="每周在圣祖活动前通过 CodeYun 微信 iLink 接入提醒三清道宗微信群。",
        schedule_label=f"每周日 {FANXIU_WECHAT_SHENGZU_REMINDER_RUN_TIME}",
        retry_label="失败后下次调度重试",
        action=enqueue_fanxiu_wechat_shengzu_reminder,
        manual_warning="会通过已连接的微信 iLink 账号向三清道宗群发送提醒；需要配置接入账号和接收群。",
        default_visible=False,
    ),
    BackgroundTaskSpec(
        key=MARKET_QUOTE_REFRESH_TASK_KEY,
        title="持仓行情刷新",
        category="股票",
        description="每小时通过免登录的东方财富公共行情刷新当前持仓现价，并写入本地行情缓存；页面打开时会额外按分钟刷新。",
        schedule_label="每小时刷新",
        retry_label="失败后下次调度重试",
        action=_enqueue_market_quote_refresh,
        manual_warning="会访问东方财富公共行情接口，仅刷新当前持仓现价缓存，不修改交易数据。",
    ),
    BackgroundTaskSpec(
        key=MARKET_INTRADAY_PERSIST_TASK_KEY,
        title="股票分时持久化",
        category="股票",
        description="每天收盘后审计股票量化分析标的最近交易日的 1 分钟分时缺口，按公开数据源可返回范围分批补齐并写入本地 market_intraday。",
        schedule_label=f"每天 {MARKET_INTRADAY_PERSIST_RUN_TIME}",
        retry_label="失败后 10 分钟重试",
        action=_enqueue_market_intraday_persist,
        manual_warning="会访问 AkShare/公开行情数据源并写入本地分时数据库；只保存行情，不修改交易数据。",
        default_visible=False,
    ),
    BackgroundTaskSpec(
        key=HK_CONNECT_MOMENTUM_REVIEW_TASK_KEY,
        title="港股通策略复盘",
        category="股票",
        description="每天港股收盘后刷新港股通大市值成交额动量策略快照，给出次日开盘买入或空仓建议；无信号也会写入复盘摘要。",
        schedule_label=f"每天 {HK_CONNECT_MOMENTUM_REVIEW_RUN_TIME}",
        retry_label="失败后 10 分钟重试",
        action=_enqueue_hk_connect_momentum_review,
        manual_warning="会访问东方财富和 AkShare 公共行情，刷新策略复盘缓存；只生成建议，不修改交易数据。",
        default_visible=False,
    ),
    BackgroundTaskSpec(
        key="storage_analysis",
        title="存储清理",
        category="存储",
        description="按配置检查数据工作区总占用，超出上限时优先永久清理旧回收站资源。",
        schedule_label=f"每天 {STORAGE_ANALYSIS_RUN_TIME}",
        retry_label="无额外重试",
        action=_enqueue_storage_analysis,
    ),
    BackgroundTaskSpec(
        key=WECHAT_ARCHIVE_INCREMENTAL_SYNC_TASK_KEY,
        title="微信数据同步",
        category="微信",
        description="每 2 小时通过纯数据库通道同步本机微信新增消息和图片等资源，并按增量水位整理微信支付与聊天转账到 Freebill；不会操作微信 GUI，已有同步在队列中会自动跳过。",
        schedule_label="每 2 小时同步",
        retry_label="失败后 10 分钟重试",
        action=_enqueue_wechat_archive_incremental_sync,
        manual_warning="会只读复制本机微信数据库、解密快照并导出图片等资源；不会修改官方微信原始数据，也不会操作微信窗口。",
        default_visible=False,
    ),
    BackgroundTaskSpec(
        key=WECHAT_MOMENTS_INCREMENTAL_SYNC_TASK_KEY,
        title="微信朋友圈增量归档",
        category="微信",
        description="每 6 小时只读同步并解密本机微信朋友圈数据库，按动态 ID 将正文、作者、时间、媒体索引、点赞和评论增量写入 CodeYun；已归档内容不会因好友后续改为三天可见而删除。",
        schedule_label="每 6 小时增量归档",
        retry_label="失败后 10 分钟重试",
        action=_enqueue_wechat_moments_incremental_sync,
        manual_warning="会读取你当前微信账号本来可见的朋友圈缓存，并把完整 XML 版本和可下载的图片预览保存到微信逆向数据目录；不会绕过可见性、修改微信数据或操作微信窗口。",
        default_visible=False,
    ),
    BackgroundTaskSpec(
        key=NOTE_SHEET_PAGE_SNAPSHOT_BACKFILL_TASK_KEY,
        title="星云表格快照补齐",
        category="表格",
        description="扫描历史普通分页表，为默认第一页生成轻量页面快照，避免首次打开大 JSON 表时再同步读取完整 document_json。",
        schedule_label="未配置自动触发",
        retry_label="失败后下次手动重试",
        action=_enqueue_note_sheet_page_snapshot_backfill,
        manual_warning="会读取历史普通分页表并写入 sheetpagesnapshot 缓存；跳过考勤表，不改原始表格数据。",
        default_visible=False,
    ),
    BackgroundTaskSpec(
        key=RIME_CONTEXT_REFRESH_TASK_KEY,
        title="Rime 预测索引刷新",
        category="输入法",
        description="按周期合并 Rime 输入历史和语料文章，重建上下文预测索引、运行时 TSV、热词表和英文学习词典。",
        schedule_label="每 6 小时刷新",
        retry_label="失败后 10 分钟重试",
        action=_enqueue_rime_context_refresh,
        manual_warning="会读写本机 Rime 用户目录中的上下文预测文件；不会启动或操作 Rime 程序。",
        default_visible=False,
    ),
    BackgroundTaskSpec(
        key=RIME_CONTEXT_LINT_TASK_KEY,
        title="Rime 语料质量检查",
        category="输入法",
        description="按周期扫描 Rime 输入历史和导入语料，生成错别字、重复片段、异常英文 token 等质量检查结果。",
        schedule_label="每天 03:20",
        retry_label="失败后 10 分钟重试",
        action=_enqueue_rime_context_lint,
        manual_warning="只读取本机 Rime 上下文预测语料并生成检查结果；不会启动或操作 Rime 程序。",
        default_visible=False,
    ),
    BackgroundTaskSpec(
        key=DP_BROWSER_TAB_CLEANUP_TASK_KEY,
        title="DP 浏览器重复标签页清理",
        category="自动化",
        description="每小时检查 DrissionPage 专用调试浏览器的标签页；同一域名页面连续跨周期重复、观测状态稳定且未被调试器附着时，关闭旧重复页，并为每个域名保留一个最新窗口。",
        schedule_label="每小时扫描",
        retry_label="失败后下次调度重试",
        action=_enqueue_dp_browser_tab_cleanup,
        manual_warning="只连接本机 DP 调试端口，默认仅处理白名单域名；登录、扫码、验证码、授权、正在被调试器附着的页面不会关闭。",
        default_visible=False,
    ),
)


def get_background_task_spec(task_key: str) -> BackgroundTaskSpec | None:
    normalized = task_key.strip()
    return next((spec for spec in BACKGROUND_TASK_SPECS if spec.key == normalized), None)


class BackgroundTaskRunner:
    def __init__(self) -> None:
        scheduler_dir = get_settings().data_dir / "scheduler"
        self.state_path = scheduler_dir / "background_tasks.state.json"
        self.log_path = scheduler_dir / "background_tasks.log"
        self.lock_path = scheduler_dir / "background_tasks.lock"
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._runner: _StoppableBehaviorTreeRunner | None = None
        self._last_error: str | None = None

    def start(self) -> None:
        if get_settings().is_test:
            return
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._wake_event.clear()
            self._thread = threading.Thread(target=self._run_thread, name="codeyun-background-task-runner", daemon=True)
            self._thread.start()

    def shutdown(self) -> None:
        self._stop_event.set()
        self._wake_event.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=5)

    def is_running(self) -> bool:
        thread = self._thread
        return bool(thread and thread.is_alive() and not self._stop_event.is_set())

    def _run_thread(self) -> None:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock = FileLock(str(self.lock_path))
        try:
            with lock.acquire(timeout=0):
                runner = self.build_runner()
                with self._lock:
                    self._runner = runner
                    self._last_error = None
                while not self._stop_event.is_set():
                    try:
                        status = runner.run_once()
                    except Exception as exc:  # pragma: no cover - individual jobs must not kill the runner.
                        self._last_error = str(exc)
                        self._stop_event.wait(5)
                        if self._stop_event.is_set():
                            break
                        runner = self.build_runner()
                        with self._lock:
                            self._runner = runner
                        continue
                    if status != Status.RUNNING:
                        self._stop_event.wait(1)
        except Timeout:
            self._last_error = f"后台任务行为树已在运行：{self.lock_path}"
        except Exception as exc:  # pragma: no cover - runner must not crash app startup.
            self._last_error = str(exc)
        finally:
            with self._lock:
                self._runner = None

    def build_runner(self) -> _StoppableBehaviorTreeRunner:
        runner = _StoppableBehaviorTreeRunner(
            self.build_tree(),
            self.state_path,
            trace=1,
            log_path=self.log_path,
            stop_event=self._stop_event,
            wake_event=self._wake_event,
        )
        self._ensure_schedule_state_version(runner)
        return runner

    def _ensure_schedule_state_version(self, runner: BehaviorTreeRunner) -> None:
        blackboard = runner.state.setdefault("blackboard", {})
        if blackboard.get("schedule_version") == BACKGROUND_TASK_SCHEDULE_STATE_VERSION:
            return
        nodes = runner.state.setdefault("nodes", {})
        active_task_keys = {spec.key for spec in BACKGROUND_TASK_SPECS}
        for path, state in list(nodes.items()):
            if path.startswith("Root/MemorySelector/") and not any(
                _path_matches_task(path, task_key) for task_key in active_task_keys
            ):
                nodes.pop(path, None)
                continue
            if not isinstance(state, dict) or "next_run_at" not in state:
                continue
            if any(_path_matches_task(path, task_key) for task_key in SCHEDULE_VERSIONED_TASK_KEYS):
                state.pop("next_run_at", None)
        blackboard["schedule_version"] = BACKGROUND_TASK_SCHEDULE_STATE_VERSION
        runner.save_state()

    def build_tree(self) -> Root:
        def scheduled(spec: BackgroundTaskSpec) -> DynamicTime:
            return self._build_scheduled_task(spec)

        return Root(
            MemorySelector(
                *(scheduled(spec) for spec in BACKGROUND_TASK_SPECS),
                Sequence(
                    Action(self._record_idle_summary),
                    IdleUntilNextWake(ratio=0.8, min_seconds=1, max_seconds=300),
                ),
            )
        )

    def _build_scheduled_task(self, spec: BackgroundTaskSpec) -> DynamicTime:
        task_key = spec.key

        def default_next_time(runner: BehaviorTreeRunner):
            policy = _effective_background_task_schedule_policy(task_key)
            return compute_next_trigger_at(policy, base_time=runner.now())

        node = DynamicTime(
            Action(self._run_task_if_enabled, task_key),
            label=task_key,
            persist=True,
            default_next_time=default_next_time,
            enabled=_is_task_enabled(task_key),
        )
        retry_policy = ((_effective_background_task_schedule_policy(task_key) or {}).get("outcome") or {}).get("on_failure") or {}
        if str(retry_policy.get("type") or "").lower() == "retry_after":
            minutes = int(retry_policy.get("minutes") or max(1, round(int(retry_policy.get("seconds") or 600) / 60)))
            node.retry(minutes=minutes)
        return node

    def _run_task_if_enabled(self, ctx, task_key: str):
        if not _is_task_enabled(task_key):
            return
        spec = get_background_task_spec(task_key)
        if spec is None:
            return
        policy = _effective_background_task_schedule_policy(task_key, enabled=True)
        ctx.next_run_at = compute_next_trigger_at(policy, base_time=ctx.now())
        action_result = spec.action()
        queue_task_id = _queue_task_id_from_action_result(action_result)
        run_result = action_result
        if queue_task_id:
            run_result = yield from self._wait_for_queue_task(ctx, queue_task_id)
        next_run_at = _next_run_at_from_action_result(run_result)
        if next_run_at is not None:
            return NextWake(next_run_at)
        next_run_at = compute_next_trigger_at(policy, base_time=ctx.now())
        if next_run_at is not None:
            return NextWake(next_run_at)

    def _wait_for_queue_task(self, ctx, queue_task_id: str):
        while True:
            queue_task = _find_queue_task_by_id(background_task_queue.snapshot(), queue_task_id)
            if queue_task is None:
                raise RuntimeError(f"后台队列任务丢失：{queue_task_id}")

            status = str(queue_task.get("status") or "")
            if status in {"pending", "running"}:
                ctx.sleep(BACKGROUND_QUEUE_POLL_SECONDS)
                yield
                continue

            if status == "completed":
                return queue_task.get("result")

            error_message = queue_task.get("error_message") or f"后台队列任务失败：{queue_task_id}"
            raise RuntimeError(str(error_message))

    def _record_idle_summary(self) -> None:
        return None

    def refresh_enabled_states(self, task_key: str | None = None) -> None:
        with self._lock:
            runner = self._runner
            if runner is not None:
                _sync_runner_enabled_states(runner, task_key=task_key)
                self.reset_task(task_key) if task_key else None
            self._wake_event.set()

    def snapshot(self) -> dict[str, Any]:
        runner = self._runner or self.build_runner()
        enabled_by_key = _build_enabled_by_key()
        _sync_runner_enabled_states(runner, enabled_by_key=enabled_by_key)
        next_wake = runner.next_wake()
        node_states = runner.state.get("nodes", {}) if isinstance(runner.state, dict) else {}
        tasks: dict[str, dict[str, Any]] = {}
        for spec in BACKGROUND_TASK_SPECS:
            if _is_task_deleted(spec.key):
                continue
            enabled = bool(enabled_by_key.get(spec.key))
            tasks[spec.key] = _background_task_schedule_status(spec, node_states, enabled=enabled)

        return {
            "runner_running": self.is_running(),
            "next_wake_at": _format_datetime(next_wake),
            "state_path": str(self.state_path),
            "log_path": str(self.log_path),
            "last_error": self._last_error,
            "tasks": tasks,
        }

    def reset_task(self, task_key: str) -> bool:
        runner = self._runner or self.build_runner()
        changed = False
        nodes = runner.state.setdefault("nodes", {})
        for path, state in list(nodes.items()):
            if not isinstance(state, dict):
                continue
            if _path_matches_task(path, task_key) and "next_run_at" in state:
                state.pop("next_run_at", None)
                changed = True
        if changed:
            runner.save_state()
        return changed

    def set_task_next_run_at(self, task_key: str, next_run_at: Any) -> str | None:
        runner = self._runner or self.build_runner()
        formatted = _format_datetime(_parse_datetime(next_run_at))
        nodes = runner.state.setdefault("nodes", {})
        changed = False
        matched = False
        for path, state in list(nodes.items()):
            if not isinstance(state, dict) or not _path_matches_task(path, task_key):
                continue
            matched = True
            if formatted:
                state["next_run_at"] = formatted
            else:
                state.pop("next_run_at", None)
            changed = True

        if not matched:
            state = nodes.setdefault(f"Root/MemorySelector/{task_key}", {})
            if formatted:
                state["next_run_at"] = formatted
            else:
                state.pop("next_run_at", None)
            changed = True

        if changed:
            runner.save_state()
        with self._lock:
            self._wake_event.set()
        return formatted


def _format_datetime(value: dt.datetime | None) -> str | None:
    if value is None:
        return None
    return value.replace(microsecond=0).isoformat()


def _parse_datetime(value: Any) -> dt.datetime | None:
    if not value:
        return None
    if isinstance(value, dt.datetime):
        return value.replace(microsecond=0)
    try:
        return dt.datetime.fromisoformat(str(value)).replace(microsecond=0)
    except ValueError:
        try:
            return dt.datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None


def _queue_task_id_from_action_result(value: Any) -> str | None:
    if isinstance(value, str):
        return value or None
    if isinstance(value, dict):
        queue_task_id = value.get("queue_task_id") or value.get("task_id")
        return str(queue_task_id) if queue_task_id else None
    return None


def _next_run_at_from_action_result(value: Any) -> dt.datetime | None:
    if isinstance(value, dict):
        return _parse_datetime(value.get("next_run_at") or value.get("next_trigger_at"))
    return _parse_datetime(getattr(value, "next_run_at", None))


def _path_matches_task(path: str, task_key: str) -> bool:
    return any(part == task_key or part.startswith(f"{task_key}[") for part in str(path).split("/"))


def _iter_tree_nodes(node: Any):
    yield node
    for child in getattr(node, "children", []) or []:
        yield from _iter_tree_nodes(child)


def _build_enabled_by_key() -> dict[str, bool]:
    return {spec.key: _is_task_enabled(spec.key) for spec in BACKGROUND_TASK_SPECS if not _is_task_deleted(spec.key)}


def _sync_runner_enabled_states(
    runner: BehaviorTreeRunner,
    *,
    task_key: str | None = None,
    enabled_by_key: dict[str, bool] | None = None,
) -> None:
    enabled_by_key = dict(enabled_by_key or _build_enabled_by_key())
    task_keys = [task_key] if task_key else list(enabled_by_key)
    for node in _iter_tree_nodes(runner.root):
        if not hasattr(node, "enabled"):
            continue
        for key in task_keys:
            if _path_matches_task(getattr(node, "path", ""), key):
                setattr(node, "enabled", bool(enabled_by_key.get(key)))
                break


def _find_task_next_run(node_states: dict[str, Any], task_key: str) -> dt.datetime | None:
    values: list[dt.datetime] = []
    for path, state in node_states.items():
        if not isinstance(state, dict) or not _path_matches_task(path, task_key):
            continue
        parsed = _parse_datetime(state.get("next_run_at"))
        if parsed is not None:
            values.append(parsed)
    return min(values) if values else None


def _background_task_schedule_status(
    spec: BackgroundTaskSpec,
    node_states: dict[str, Any],
    *,
    enabled: bool,
) -> dict[str, Any]:
    policy = _effective_background_task_schedule_policy(spec.key, enabled=enabled)
    return {
        "next_run_at": _format_datetime(_find_task_next_run(node_states, spec.key)) if enabled else None,
        "enabled": enabled,
        "schedule_policy": policy,
        "schedule_label": schedule_policy_label(policy) or spec.schedule_label,
        "retry_label": spec.retry_label,
    }


def _find_queue_task_by_id(queue: dict[str, Any], queue_task_id: str) -> dict[str, Any] | None:
    normalized_id = str(queue_task_id or "").strip()
    if not normalized_id:
        return None

    running = queue.get("running")
    if isinstance(running, dict) and running.get("id") == normalized_id:
        return running

    for section in ("pending", "recent"):
        for item in queue.get(section) or []:
            if isinstance(item, dict) and item.get("id") == normalized_id:
                return item

    from backend.core.jobs.local_runtime import get_local_job_run

    local_run = get_local_job_run(normalized_id)
    if local_run is not None:
        status_map = {
            "queued": "pending",
            "running": "running",
            "succeeded": "completed",
            "failed": "failed",
            "cancelled": "failed",
            "interrupted": "failed",
        }
        return {
            "id": local_run.id,
            "name": local_run.job_type,
            "status": status_map.get(local_run.status, local_run.status),
            "queued_at": local_run.queued_at,
            "started_at": local_run.started_at,
            "finished_at": local_run.finished_at,
            "error_message": local_run.error_message,
            "metadata": {"resource_lock": local_run.resource_key},
            "resource_lock": local_run.resource_key,
            "result": local_run.result_json or {},
        }

    return None


background_task_runner = BackgroundTaskRunner()


def init_background_task_runner() -> None:
    background_task_runner.start()


def shutdown_background_task_runner() -> None:
    background_task_runner.shutdown()


def get_background_task_runner_snapshot() -> dict[str, Any]:
    return background_task_runner.snapshot()


def refresh_background_task_schedule_states(task_key: str | None = None) -> None:
    background_task_runner.refresh_enabled_states(task_key)


def reset_background_task_schedule(task_key: str) -> bool:
    return background_task_runner.reset_task(task_key)


def set_background_task_next_run_at(task_key: str, next_run_at: Any) -> str | None:
    return background_task_runner.set_task_next_run_at(task_key, next_run_at)


def is_background_task_deleted(task_key: str) -> bool:
    return _is_task_deleted(task_key)
