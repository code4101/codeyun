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
from backend.core.dp_browser_tab_cleanup import (
    DP_BROWSER_TAB_CLEANUP_TASK_KEY,
    run_dp_browser_tab_cleanup,
)
from backend.core.attendance.course_completion import (
    COURSE_COMPLETION_RUN_TIME,
    COURSE_COMPLETION_TASK_KEY,
    enqueue_attendance_course_completion_job,
)
from backend.core.attendance.fanbei_schedule import (
    FANBEI_ATTENDANCE_EVENING_RUN_TIME,
    FANBEI_ATTENDANCE_EVENING_TASK_KEY,
    FANBEI_ATTENDANCE_MORNING_RUN_TIME,
    FANBEI_ATTENDANCE_MORNING_TASK_KEY,
    enqueue_fanbei_attendance_evening_steps,
    enqueue_fanbei_attendance_morning_steps,
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
from backend.core.settings import get_settings
from backend.core.notes.weekly_scheduler import RUANYF_WEEKLY_TASK_NAME, enqueue_ruanyf_weekly_note_job
from backend.models import AppSetting


TaskAction = Callable[[], Any]
CODEX_DIARY_RUN_TIME = "00:10"
ATTENDANCE_SUMMARY_RUN_TIME = "00:00"
MEDIA_SYNC_HOME_DISCOVERY_TASK_KEY = "media_sync_home_discovery"
MEDIA_SYNC_HOME_DISCOVERY_RUN_TIME = "00:25"
MEDIA_SYNC_HOME_DISCOVERY_TARGET_COUNT = 200
STORAGE_ANALYSIS_RUN_TIME = "01:00"
MARKET_QUOTE_REFRESH_TASK_KEY = "market_quote_refresh"
MARKET_INTRADAY_PERSIST_TASK_KEY = "market_intraday_persist"
MARKET_INTRADAY_PERSIST_RUN_TIME = "16:30"
HK_CONNECT_MOMENTUM_REVIEW_TASK_KEY = "hk_connect_momentum_review"
HK_CONNECT_MOMENTUM_REVIEW_RUN_TIME = "17:40"
WECHAT_ARCHIVE_INCREMENTAL_SYNC_TASK_KEY = "wechat_archive_incremental_sync"
NOTE_SHEET_PAGE_SNAPSHOT_BACKFILL_TASK_KEY = "note_sheet_page_snapshot_backfill"
RUANYF_WEEKLY_START_TIME = "06:00"
PUBLIC_FRONTEND_DEPLOY_INTERVAL_MINUTES = 30
BACKGROUND_TASK_SCHEDULE_STATE_VERSION = 12
BACKGROUND_QUEUE_POLL_SECONDS = 1.0
SCHEDULE_VERSIONED_TASK_KEYS = {
    "codex_diary_yesterday_import",
    RUANYF_WEEKLY_TASK_NAME,
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
    NOTE_SHEET_PAGE_SNAPSHOT_BACKFILL_TASK_KEY,
    DP_BROWSER_TAB_CLEANUP_TASK_KEY,
    PUBLIC_FRONTEND_DEPLOY_TASK_KEY,
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
    if task_key == "codex_diary_yesterday_import":
        return _job_schedule_policy({"type": "daily", "time": CODEX_DIARY_RUN_TIME}, retry_minutes=10)
    if task_key == RUANYF_WEEKLY_TASK_NAME:
        return _job_schedule_policy(
            {"type": "weekly", "weekdays": [5], "time": RUANYF_WEEKLY_START_TIME},
            retry_minutes=10,
        )
    if task_key == "attendance_summary_monthly_templates":
        return _job_schedule_policy({"type": "monthly", "day": 27, "time": ATTENDANCE_SUMMARY_RUN_TIME}, retry_minutes=10)
    if task_key == COURSE_COMPLETION_TASK_KEY:
        return _job_schedule_policy({"type": "daily", "time": COURSE_COMPLETION_RUN_TIME}, retry_minutes=10)
    if task_key == MEDIA_SYNC_HOME_DISCOVERY_TASK_KEY:
        return _job_schedule_policy({"type": "daily", "time": MEDIA_SYNC_HOME_DISCOVERY_RUN_TIME}, retry_minutes=10)
    if task_key == FANBEI_ATTENDANCE_EVENING_TASK_KEY:
        return _job_schedule_policy({"type": "daily", "time": FANBEI_ATTENDANCE_EVENING_RUN_TIME})
    if task_key == FANBEI_ATTENDANCE_MORNING_TASK_KEY:
        return _job_schedule_policy({"type": "daily", "time": FANBEI_ATTENDANCE_MORNING_RUN_TIME})
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
        return _job_schedule_policy({"type": "cron", "expression": "0 * * * *"}, retry_minutes=10)
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
    from backend.api.admin import scheduled_analysis_job

    task_id, _ = background_task_queue.enqueue_once("storage_analysis", scheduled_analysis_job)
    return task_id


def _enqueue_attendance_summary() -> str:
    from backend.api.note_sheets import run_attendance_summary_template_job

    task_id, _ = background_task_queue.enqueue_once("attendance_summary_monthly_templates", run_attendance_summary_template_job)
    return task_id


def _enqueue_codex_diary() -> str | None:
    from backend.api.notes import maybe_enqueue_codex_diary_yesterday_import

    return maybe_enqueue_codex_diary_yesterday_import(trigger_reason="scheduled")


def _enqueue_ruanyf_weekly_note() -> str | None:
    return enqueue_ruanyf_weekly_note_job()


def _run_media_sync_home_discovery_job() -> None:
    try:
        from backend.plugins.modules.media_sync.runtime import run_scheduled_home_discovery
    except Exception as exc:
        print(f"Media sync home discovery skipped: plugin unavailable ({exc})")
        return

    result = run_scheduled_home_discovery(target_count=MEDIA_SYNC_HOME_DISCOVERY_TARGET_COUNT)
    print(
        "Media candidate replenishment completed: "
        f"profiles={result.get('profile_count', 0)} "
        f"target={result.get('target_count', 0)} "
        f"success={result.get('success_count', 0)} "
        f"failed={len(result.get('failures') or {})}"
    )
    if result.get("failures"):
        raise RuntimeError(f"媒体候选补齐未完成：{result['failures']}")


def _enqueue_media_sync_home_discovery() -> str | None:
    task_id, _ = background_task_queue.enqueue_once(MEDIA_SYNC_HOME_DISCOVERY_TASK_KEY, _run_media_sync_home_discovery_job)
    return task_id


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
    task_id, _ = background_task_queue.enqueue_once(MARKET_QUOTE_REFRESH_TASK_KEY, _run_market_quote_refresh_job)
    return task_id


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
    task_id, _ = background_task_queue.enqueue_once(
        MARKET_INTRADAY_PERSIST_TASK_KEY,
        _run_market_intraday_persist_job,
    )
    return task_id


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
    task_id, _ = background_task_queue.enqueue_once(
        HK_CONNECT_MOMENTUM_REVIEW_TASK_KEY,
        _run_hk_connect_momentum_review_job,
    )
    return task_id


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
    task_id, _ = background_task_queue.enqueue_once(
        NOTE_SHEET_PAGE_SNAPSHOT_BACKFILL_TASK_KEY,
        _run_note_sheet_page_snapshot_backfill_job,
    )
    return task_id


def _enqueue_dp_browser_tab_cleanup() -> str | None:
    task_id, _ = background_task_queue.enqueue_once(
        DP_BROWSER_TAB_CLEANUP_TASK_KEY,
        run_dp_browser_tab_cleanup,
    )
    return task_id


def _enqueue_public_frontend_deploy() -> str | None:
    task_id, _ = background_task_queue.enqueue_once(
        PUBLIC_FRONTEND_DEPLOY_TASK_KEY,
        run_public_frontend_deploy_check,
    )
    return task_id


BACKGROUND_TASK_SPECS: tuple[BackgroundTaskSpec, ...] = (
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
        description="每天先补齐两个候选池的 URL 缓存，再依次下载图片；实际落盘不足目标时继续采集并补足。两个候选池互不占用同一个运行锁。",
        schedule_label=f"每天 {MEDIA_SYNC_HOME_DISCOVERY_RUN_TIME}",
        retry_label="失败后 10 分钟重试",
        action=_enqueue_media_sync_home_discovery,
        manual_warning=f"会使用媒体同步插件和浏览器登录态访问外部推荐流，先补 URL 缓存，再把每个候选池的实际图片分别补齐到 {MEDIA_SYNC_HOME_DISCOVERY_TARGET_COUNT} 张。",
    ),
    BackgroundTaskSpec(
        key=FANBEI_ATTENDANCE_EVENING_TASK_KEY,
        title="梵呗课程数据晚间步骤",
        category="考勤",
        description="梵呗课程数据每天晚间执行 step1-step3。step1 默认调用课程数据浏览器下载小鹅通数据，step2/step3 默认在课程数据主机写表、计算返款并渲染高亮；每步运行位置可在考勤配置中覆盖。",
        schedule_label=f"每天 {FANBEI_ATTENDANCE_EVENING_RUN_TIME}",
        retry_label="无额外重试",
        action=enqueue_fanbei_attendance_evening_steps,
        manual_warning="会按考勤配置里的课程数据 step1-step6 运行位置执行：step1 默认使用课程数据浏览器，step2/step3 默认使用课程数据主机。",
    ),
    BackgroundTaskSpec(
        key=FANBEI_ATTENDANCE_MORNING_TASK_KEY,
        title="梵呗课程数据上午步骤",
        category="考勤",
        description="梵呗课程数据每天上午执行 step4-step6。当前仅保留调度框架，具体步骤为空实现。",
        schedule_label=f"每天 {FANBEI_ATTENDANCE_MORNING_RUN_TIME}",
        retry_label="无额外重试",
        action=enqueue_fanbei_attendance_morning_steps,
        manual_warning="当前仅执行课程数据 step4-step6 空框架，不会修改考勤数据。",
    ),
    BackgroundTaskSpec(
        key=FANXIU_WECHAT_BOSS_REMINDER_TASK_KEY,
        title="凡修魔狱封阵微信群提醒",
        category="凡修",
        description="每天在魔狱封阵前通过 xlproject 的微信发送脚本提醒三清道宗微信群。",
        schedule_label=f"每天 {FANXIU_WECHAT_BOSS_REMINDER_RUN_TIME}",
        retry_label="失败后下次调度重试",
        action=enqueue_fanxiu_wechat_boss_reminder,
        manual_warning="会操作本机微信向三清道宗群发送 @所有人 提醒；需要微信登录态和 xlproject 环境可用。",
        default_visible=False,
    ),
    BackgroundTaskSpec(
        key=FANXIU_WECHAT_SHENGZU_REMINDER_TASK_KEY,
        title="凡修圣祖微信群提醒",
        category="凡修",
        description="每周在圣祖活动前通过 xlproject 的微信发送脚本提醒三清道宗微信群。",
        schedule_label=f"每周日 {FANXIU_WECHAT_SHENGZU_REMINDER_RUN_TIME}",
        retry_label="失败后下次调度重试",
        action=enqueue_fanxiu_wechat_shengzu_reminder,
        manual_warning="会操作本机微信向三清道宗群发送提醒；需要微信登录态和 xlproject 环境可用。",
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
        description="通过纯数据库通道同步本机微信新增消息和图片等资源到 CodeYun 数据区；不会操作微信 GUI，已有同步在队列中会自动跳过。",
        schedule_label="未配置自动触发",
        retry_label="失败后 10 分钟重试",
        action=_enqueue_wechat_archive_incremental_sync,
        manual_warning="会只读复制本机微信数据库、解密快照并导出图片等资源；不会修改官方微信原始数据，也不会操作微信窗口。",
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
        key=DP_BROWSER_TAB_CLEANUP_TASK_KEY,
        title="DP 浏览器重复标签页清理",
        category="自动化",
        description="每小时检查 DrissionPage 专用调试浏览器的标签页；同一域名同一 URL 连续跨周期重复且未被调试器附着时，关闭旧重复页，并为每个域名保留一个最新窗口。",
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
                    status = runner.run_once()
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
