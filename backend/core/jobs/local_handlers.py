from __future__ import annotations

import os
from dataclasses import asdict, is_dataclass
from typing import Any

from backend.core.jobs.local_runtime import (
    LocalJobCancelledError,
    LocalJobContext,
    LocalJobSpec,
    register_local_job,
)
from backend.core.jobs.resource_keys import device_media_list_resource_key


def run_system_health_check(context: LocalJobContext, payload: dict[str, Any]) -> dict[str, Any]:
    """Prove that a whitelisted job can execute without a running web process."""

    context.raise_if_cancelled()
    context.heartbeat(stage="health-check", message="独立 Worker 运行正常")
    return {"ok": True, "worker_pid": os.getpid(), "echo": payload.get("echo")}


register_local_job(
    LocalJobSpec(
        job_type="system.health-check",
        title="本地任务执行层健康检查",
        handler=run_system_health_check,
        resource_key="system:local-job-health-check",
        user_submittable=True,
    )
)


def run_media_membership_reconcile_job(
    context: LocalJobContext,
    payload: dict[str, Any],
) -> dict[str, Any]:
    from backend.core.media_membership_reconcile import run_media_membership_reconcile

    return run_media_membership_reconcile(context, payload)


register_local_job(
    LocalJobSpec(
        job_type="media.membership-reconcile",
        title="媒体站内喜好补齐",
        handler=run_media_membership_reconcile_job,
        resource_key=lambda payload: "resource:media-sync:membership:"
        + str(payload.get("platform") or "unknown").strip().lower(),
        user_submittable=False,
    )
)


def run_pixiv_candidate_recovery_job(
    context: LocalJobContext,
    payload: dict[str, Any],
) -> dict[str, Any]:
    from backend.core.pixiv_candidate_recovery import run_pixiv_candidate_recovery

    return run_pixiv_candidate_recovery(context, payload)


register_local_job(
    LocalJobSpec(
        job_type="media.pixiv-candidate-recovery",
        title="Pixiv 误删候选恢复",
        handler=run_pixiv_candidate_recovery_job,
        resource_key="resource:media-sync:curation:pixiv",
        user_submittable=False,
    )
)


def run_pixiv_url_migration_job(
    context: LocalJobContext,
    payload: dict[str, Any],
) -> dict[str, Any]:
    from backend.core.pixiv_url_migration import run_pixiv_url_migration

    return run_pixiv_url_migration(context, payload)


register_local_job(
    LocalJobSpec(
        job_type="media.pixiv-url-migration",
        title="Pixiv 历史 URL 候选迁移",
        handler=run_pixiv_url_migration_job,
        resource_key="resource:media-sync:curation:pixiv",
        user_submittable=False,
    )
)


def run_device_media_list(context: LocalJobContext, payload: dict[str, Any]) -> dict[str, Any]:
    from backend.api.device_entries import run_device_media_list_local_job

    context.raise_if_cancelled()
    context.heartbeat(stage="device-media-list", message="正在扫描设备媒体文件")
    return run_device_media_list_local_job(context, payload)


register_local_job(
    LocalJobSpec(
        job_type="device.media-list",
        title="设备媒体列表扫描",
        handler=run_device_media_list,
        resource_key=device_media_list_resource_key,
        user_submittable=False,
    )
)


def run_filesystem_media_list(context: LocalJobContext, payload: dict[str, Any]) -> dict[str, Any]:
    from backend.api.filesystem import run_filesystem_media_list_local_job

    context.raise_if_cancelled()
    context.heartbeat(stage="filesystem-media-list", message="正在扫描本机媒体文件")
    return run_filesystem_media_list_local_job(context, payload)


register_local_job(
    LocalJobSpec(
        job_type="filesystem.media-list",
        title="本机媒体列表扫描",
        handler=run_filesystem_media_list,
        resource_key=lambda payload: "resource:filesystem-media-list:"
        + str((payload.get("metadata") or {}).get("absolute_path") or (payload.get("metadata") or {}).get("root") or "default"),
        user_submittable=False,
    )
)


def run_pdf_display_title_generation(
    context: LocalJobContext,
    payload: dict[str, Any],
) -> dict[str, Any]:
    from backend.api.pdf_documents import _generate_pdf_display_titles_in_background
    from backend.db import engine

    pdf_ids = [int(item) for item in payload.get("pdf_ids") or [] if int(item) > 0]
    if not pdf_ids:
        raise ValueError("PDF 标题生成缺少文档 ID。")
    context.raise_if_cancelled()
    context.heartbeat(stage="pdf-display-titles", message=f"正在生成 {len(pdf_ids)} 份 PDF 标题")
    _generate_pdf_display_titles_in_background(engine, pdf_ids)
    return {"pdf_ids": pdf_ids, "processed_count": len(pdf_ids)}


register_local_job(
    LocalJobSpec(
        job_type="pdf.display-title-generation",
        title="PDF 展示标题生成",
        handler=run_pdf_display_title_generation,
        resource_key="resource:pdf-title-generation",
        cancellable=False,
        user_submittable=False,
    )
)


def run_stock_strategy_job(context: LocalJobContext, payload: dict[str, Any]) -> dict[str, Any]:
    from backend.api.eastmoney import (
        run_hk_connect_momentum_review_local_job,
        run_hk_pool_rotation_strategy_search_local_job,
        run_hk_pool_strategy_search_local_job,
    )

    job_type = context.job_type
    actions = {
        "stock.hk-pool-strategy-search": run_hk_pool_strategy_search_local_job,
        "stock.hk-pool-rotation-strategy-search": run_hk_pool_rotation_strategy_search_local_job,
        "stock.hk-connect-momentum-review-on-demand": run_hk_connect_momentum_review_local_job,
    }
    action = actions.get(job_type)
    if action is None:
        raise ValueError(f"未知股票策略作业：{job_type}")
    context.raise_if_cancelled()
    return action(context, payload)


for _stock_strategy_spec in (
    LocalJobSpec(
        job_type="stock.hk-pool-strategy-search",
        title="港股池策略搜索",
        handler=run_stock_strategy_job,
        resource_key="resource:stock-strategy",
        user_submittable=False,
    ),
    LocalJobSpec(
        job_type="stock.hk-pool-rotation-strategy-search",
        title="港股池轮动策略搜索",
        handler=run_stock_strategy_job,
        resource_key="resource:stock-strategy",
        user_submittable=False,
    ),
    LocalJobSpec(
        job_type="stock.hk-connect-momentum-review-on-demand",
        title="港股通动量即时复盘",
        handler=run_stock_strategy_job,
        resource_key="resource:stock-strategy",
        user_submittable=False,
    ),
):
    register_local_job(_stock_strategy_spec)


def _run_attendance_sheet_business_job(
    context: LocalJobContext,
    payload: dict[str, Any],
    *,
    kind: str,
) -> dict[str, Any]:
    import threading

    from backend.api import note_sheets

    run_id = str(payload.get("run_id") or "").strip()
    if not run_id:
        raise ValueError("考勤表任务缺少 run_id。")
    if kind == "registration-match":
        action = str(payload.get("action") or "")
        targets = {
            note_sheets.NOTE_SHEET_CELL_ACTION_REGISTRATION_USER_MATCH: note_sheets._run_registration_user_match_background,
            note_sheets.NOTE_SHEET_CELL_ACTION_REGISTRATION_ORDER_MATCH: note_sheets._run_registration_order_match_background,
            note_sheets.NOTE_SHEET_CELL_ACTION_REGISTRATION_COMPOSITE_UPDATE: note_sheets._run_registration_composite_update_background,
        }
        target = targets.get(action)
        if target is None:
            raise ValueError(f"未知考勤表综合更新动作：{action}")
        snapshot_getter = note_sheets._get_registration_match_run_snapshot
        cancel_business = note_sheets._request_cancel_registration_match_run
        kwargs = {
            "run_id": run_id,
            "sheet_id": int(payload.get("sheet_id") or 0),
            "workbook_id": payload.get("workbook_id"),
            "current_user_snapshot": dict(payload.get("current_user_snapshot") or {}),
            "use_browser_fallback": bool(payload.get("use_browser_fallback")),
        }
    else:
        target = note_sheets._run_clockin_link_detection_background
        snapshot_getter = note_sheets._get_clockin_link_detection_run_snapshot
        cancel_business = note_sheets._request_cancel_clockin_link_detection_run
        kwargs = {
            "run_id": run_id,
            "sheet_id": int(payload.get("sheet_id") or 0),
            "workbook_id": payload.get("workbook_id"),
            "current_user_snapshot": dict(payload.get("current_user_snapshot") or {}),
            "provider_id": str(payload.get("provider_id") or ""),
            "model": str(payload.get("model") or ""),
        }

    stop_watcher = threading.Event()

    def watch_cancel() -> None:
        while not stop_watcher.wait(0.5):
            try:
                context.raise_if_cancelled()
            except LocalJobCancelledError:
                cancel_business(run_id)
                return

    watcher = threading.Thread(target=watch_cancel, name=f"attendance-cancel-{run_id[:8]}", daemon=True)
    watcher.start()
    try:
        context.heartbeat(stage=kind, message="正在处理考勤表")
        target(**kwargs)
    finally:
        stop_watcher.set()
        watcher.join(timeout=1)
    state = snapshot_getter(run_id) or {}
    if state.get("status") == "cancelled":
        raise LocalJobCancelledError(str(state.get("message") or "考勤表任务已取消。"))
    if state.get("status") == "failed":
        raise RuntimeError(str(state.get("error_message") or state.get("message") or "考勤表任务失败。"))
    return {
        "run_id": run_id,
        "status": state.get("status"),
        "updated_count": int(state.get("updated_count") or 0),
        "error_count": int(state.get("error_count") or 0),
        "warning_count": int(state.get("warning_count") or 0),
    }


def run_attendance_registration_match(context: LocalJobContext, payload: dict[str, Any]) -> dict[str, Any]:
    return _run_attendance_sheet_business_job(context, payload, kind="registration-match")


def run_attendance_clockin_link_detection(
    context: LocalJobContext,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return _run_attendance_sheet_business_job(context, payload, kind="clockin-link-detection")


for _attendance_spec in (
    LocalJobSpec(
        job_type="attendance.registration-match",
        title="考勤报名综合更新",
        handler=run_attendance_registration_match,
        resource_key=lambda payload: f"resource:note-sheet:{payload.get('sheet_id') or 'unknown'}",
        user_submittable=False,
    ),
    LocalJobSpec(
        job_type="attendance.clockin-link-detection",
        title="考勤打卡链接检测",
        handler=run_attendance_clockin_link_detection,
        resource_key=lambda payload: f"resource:note-sheet:{payload.get('sheet_id') or 'unknown'}",
        user_submittable=False,
    ),
):
    register_local_job(_attendance_spec)


def run_wechat_chat_book(context: LocalJobContext, payload: dict[str, Any]) -> dict[str, Any]:
    from backend.api.wechat_archive import run_wechat_chat_book_local_job_payload

    context.raise_if_cancelled()
    context.heartbeat(stage="wechat-chat-book", message="正在准备整理群聊")
    return run_wechat_chat_book_local_job_payload(context, payload)


register_local_job(
    LocalJobSpec(
        job_type="library.wechat-chat-book",
        title="微信群聊成书",
        handler=run_wechat_chat_book,
        resource_key=lambda payload: f"resource:wechat-chat-book:{payload.get('cache_key') or 'unknown'}",
        user_submittable=False,
    )
)


def run_skill_book_translation(context: LocalJobContext, payload: dict[str, Any]) -> dict[str, Any]:
    from backend.api.skill_books import run_skill_book_translation_local_job_payload

    user_id = int(payload.get("user_id") or 0)
    if user_id <= 0:
        raise ValueError("Skill 手册翻译缺少 user_id。")

    def progress(current: int, total: int) -> None:
        context.heartbeat(
            stage="skill-book-translation",
            message=f"正在翻译 Skill 手册 {current}/{total}",
            progress_current=current,
            progress_total=total,
        )

    context.heartbeat(stage="skill-book-translation", message="正在扫描待翻译 Skill")
    return run_skill_book_translation_local_job_payload(
        user_id=user_id,
        progress_callback=progress,
    )


register_local_job(
    LocalJobSpec(
        job_type="library.skill-book-translation",
        title="Skill 手册翻译",
        handler=run_skill_book_translation,
        resource_key="resource:skill-book-translation",
        user_submittable=False,
    )
)


def run_wechat_archive_sync(context: LocalJobContext, payload: dict[str, Any]) -> dict[str, Any]:
    from backend.api.wechat_archive import (
        _run_wechat_archive_sync_job,
        _run_wechat_db_live_sync_job,
    )

    context.raise_if_cancelled()
    context.heartbeat(stage="wechat-archive", message="正在同步微信归档")
    if str(payload.get("mode") or "") == "db_storage_live":
        return _run_wechat_db_live_sync_job(payload)
    return _run_wechat_archive_sync_job(payload)


register_local_job(
    LocalJobSpec(
        job_type="archive.wechat-sync",
        title="微信数据同步",
        handler=run_wechat_archive_sync,
        resource_key="resource:wechat-archive",
        cancellable=False,
        user_submittable=False,
    )
)


def run_wechat_moments_archive(context: LocalJobContext, payload: dict[str, Any]) -> dict[str, Any]:
    from backend.core.wechat_moments import run_wechat_moments_sync

    context.raise_if_cancelled()
    context.heartbeat(stage="wechat-moments", message="正在增量归档微信朋友圈")
    return run_wechat_moments_sync(payload)


register_local_job(
    LocalJobSpec(
        job_type="archive.wechat-moments",
        title="微信朋友圈增量归档",
        handler=run_wechat_moments_archive,
        resource_key="resource:wechat-archive",
        cancellable=False,
        user_submittable=False,
    )
)


def run_codex_diary_import(context: LocalJobContext, payload: dict[str, Any]) -> dict[str, Any]:
    from sqlmodel import Session

    from backend.api.notes import _run_codex_diary_import_worker
    from backend.db import engine
    from backend.models import CodexDiaryImportRun

    run_id = str(payload.get("run_id") or "").strip()
    user_id = int(payload.get("user_id") or 0)
    entry_specs = payload.get("entry_specs")
    root_identity = payload.get("root_identity")
    if not run_id or user_id <= 0 or not isinstance(entry_specs, list) or not isinstance(root_identity, dict):
        raise ValueError("codex_diary_import 参数不完整。")
    context.raise_if_cancelled()
    context.heartbeat(stage="codex-diary", message="正在导入 Codex 星图日记")
    _run_codex_diary_import_worker(
        engine,
        run_id=run_id,
        user_id=user_id,
        entry_specs=entry_specs,
        root_identity=root_identity,
    )
    with Session(engine) as session:
        business_run = session.get(CodexDiaryImportRun, run_id)
        if business_run is None:
            raise RuntimeError(f"Codex 日记业务 Run 不存在：{run_id}")
        if business_run.status == "failed":
            raise RuntimeError(business_run.error_message or "Codex 日记导入失败。")
        return {
            "run_id": run_id,
            "status": business_run.status,
            "created_note_count": int(business_run.created_note_count or 0),
        }


register_local_job(
    LocalJobSpec(
        job_type="notes.codex-diary-import",
        title="Codex 星图日记导入",
        handler=run_codex_diary_import,
        resource_key="resource:codex-diary",
        user_submittable=False,
    )
)


def run_codex_diary_auto_import(context: LocalJobContext, payload: dict[str, Any]) -> dict[str, Any]:
    from backend.api.notes import run_codex_diary_auto_import_job
    from backend.db import engine

    context.raise_if_cancelled()
    context.heartbeat(stage="codex-diary-auto", message="正在导入昨日 Codex 星图日记")
    return run_codex_diary_auto_import_job(
        engine,
        str(payload.get("target_date") or "").strip() or None,
        trigger_reason=str(payload.get("trigger_reason") or "scheduled"),
    )


register_local_job(
    LocalJobSpec(
        job_type="notes.codex-diary-auto-import",
        title="昨日 Codex 星图日记导入",
        handler=run_codex_diary_auto_import,
        resource_key="resource:codex-diary",
        user_submittable=False,
    )
)


def run_git_reduction(context: LocalJobContext, payload: dict[str, Any]) -> dict[str, Any]:
    from backend.api.device_entries import run_git_reduction_local_job_payload

    run_id = str(payload.get("run_id") or "").strip()
    user_id = int(payload.get("user_id") or 0)
    if not run_id or user_id <= 0:
        raise ValueError("git_reduction 参数不完整。")
    context.raise_if_cancelled()
    context.heartbeat(stage="git-reduction", message="正在执行 Git 分层归纳")
    return run_git_reduction_local_job_payload(run_id=run_id, user_id=user_id)


register_local_job(
    LocalJobSpec(
        job_type="git.reduction",
        title="Git 分层归纳",
        handler=run_git_reduction,
        resource_key="resource:repo",
        user_submittable=False,
    )
)


def run_hk_pool_backtest(context: LocalJobContext, payload: dict[str, Any]) -> dict[str, Any]:
    from backend.api.eastmoney import run_hk_pool_backtest_local_job_payload

    def progress(result: Any) -> None:
        context.raise_if_cancelled()
        tested = int(getattr(result, "tested_count", 0) or 0)
        target = int(getattr(result, "target_count", 0) or 0)
        context.heartbeat(stage="stock-backtest", message=f"港股池回测 {tested}/{target}")

    context.raise_if_cancelled()
    context.heartbeat(stage="stock-backtest", message="正在执行港股池回测")
    return run_hk_pool_backtest_local_job_payload(payload, progress_callback=progress)


register_local_job(
    LocalJobSpec(
        job_type="stock.hk-pool-one-lot-backtest",
        title="港股池一手评分回测",
        handler=run_hk_pool_backtest,
        resource_key="resource:stock-backtest",
        user_submittable=False,
    )
)


def run_media_sync(context: LocalJobContext, payload: dict[str, Any]) -> dict[str, Any]:
    import threading
    from pathlib import Path

    from backend.core.media_sync_worker import _read_json, _run_worker, request_worker_cancel

    state_path = Path(str(payload.get("state_path") or "")).resolve()
    state = _read_json(state_path)
    if state is None or not isinstance(state.get("config"), dict):
        raise ValueError("媒体采集状态文件不存在或配置无效。")
    config = state["config"]
    stop_watcher = threading.Event()

    def watch_generic_cancel() -> None:
        while not stop_watcher.wait(0.5):
            try:
                context.raise_if_cancelled()
            except LocalJobCancelledError:
                try:
                    request_worker_cancel(int(config["user_id"]), str(config.get("scope_key") or "default"))
                except RuntimeError:
                    pass
                return

    watcher = threading.Thread(target=watch_generic_cancel, name="media-sync-cancel-bridge", daemon=True)
    watcher.start()
    try:
        context.heartbeat(stage="media-sync", message="正在执行媒体采集")
        exit_code = _run_worker(state_path)
    finally:
        stop_watcher.set()
        watcher.join(timeout=1)
    final_state = _read_json(state_path) or {}
    if final_state.get("stage") == "cancelled" or final_state.get("cancel_requested"):
        raise LocalJobCancelledError("媒体采集已取消。")
    if exit_code != 0 or final_state.get("error"):
        raise RuntimeError(str(final_state.get("error") or f"媒体采集退出码：{exit_code}"))
    return {
        "state_path": str(state_path),
        "stage": final_state.get("stage"),
        "message": final_state.get("message"),
    }


register_local_job(
    LocalJobSpec(
        job_type="media.sync",
        title="媒体采集",
        handler=run_media_sync,
        resource_key="resource:media-sync",
        user_submittable=False,
    )
)


def run_attendance_course_completion(context: LocalJobContext, payload: dict[str, Any]) -> dict[str, Any]:
    del payload
    from backend.core.attendance.course_completion import _run_attendance_course_completion_job_in_session

    context.raise_if_cancelled()
    context.heartbeat(stage="attendance-course-completion", message="正在执行考勤课程自动收尾")
    return _run_attendance_course_completion_job_in_session()


register_local_job(
    LocalJobSpec(
        job_type="attendance.course-completion",
        title="考勤课程自动收尾",
        handler=run_attendance_course_completion,
        resource_key="resource:repo",
        cancellable=False,
        user_submittable=False,
    )
)


def run_idle_maintenance(context: LocalJobContext, payload: dict[str, Any]) -> dict[str, Any]:
    del payload
    from backend.core.maintenance.idle_maintenance import run_idle_maintenance_once

    context.raise_if_cancelled()
    context.heartbeat(stage="idle-maintenance", message="正在执行闲时维护")
    result = run_idle_maintenance_once(queue_snapshot={"running": None, "pending": [], "recent": []})
    if result.get("status") == "failed":
        raise RuntimeError(str(result.get("error") or "闲时维护失败"))
    return result


register_local_job(
    LocalJobSpec(
        job_type="maintenance.idle",
        title="闲时维护",
        handler=run_idle_maintenance,
        resource_key="resource:repo",
        user_submittable=False,
    )
)


def run_storage_analysis(context: LocalJobContext, payload: dict[str, Any]) -> dict[str, Any]:
    del payload
    from backend.api.admin import scheduled_analysis_job

    context.raise_if_cancelled()
    context.heartbeat(stage="storage-analysis", message="正在执行资源备份存储治理")
    return scheduled_analysis_job()


register_local_job(
    LocalJobSpec(
        job_type="maintenance.storage-analysis",
        title="资源备份存储治理",
        handler=run_storage_analysis,
        resource_key="resource:storage-backup",
        cancellable=False,
        user_submittable=False,
    )
)


def run_music_process(context: LocalJobContext, payload: dict[str, Any]) -> dict[str, Any]:
    from backend.api.music_tools import run_music_local_job_payload

    context.raise_if_cancelled()
    context.heartbeat(stage="music", message="正在执行音乐处理")
    return run_music_local_job_payload(context, payload)


register_local_job(
    LocalJobSpec(
        job_type="music.process",
        title="音乐分轨与转谱",
        handler=run_music_process,
        resource_key="resource:music-processing",
        user_submittable=False,
    )
)


def _run_parameterless_job(
    context: LocalJobContext,
    *,
    stage: str,
    message: str,
    action,
) -> dict[str, Any]:
    context.raise_if_cancelled()
    context.heartbeat(stage=stage, message=message)
    result = action()
    if is_dataclass(result) and not isinstance(result, type):
        return asdict(result)
    return result if isinstance(result, dict) else {"ok": True}


def run_attendance_summary_templates(context: LocalJobContext, payload: dict[str, Any]) -> dict[str, Any]:
    del payload
    from backend.api.note_sheets import run_attendance_summary_template_job

    return _run_parameterless_job(
        context,
        stage="attendance-summary",
        message="正在生成考勤月度模板",
        action=run_attendance_summary_template_job,
    )


def run_media_scheduled_discovery(context: LocalJobContext, payload: dict[str, Any]) -> dict[str, Any]:
    del payload
    from backend.core.jobs.scheduler import _run_media_sync_home_discovery_job

    return _run_parameterless_job(
        context,
        stage="media-discovery",
        message="正在执行媒体候选补齐",
        action=_run_media_sync_home_discovery_job,
    )


def run_market_quote_refresh(context: LocalJobContext, payload: dict[str, Any]) -> dict[str, Any]:
    del payload
    from backend.core.jobs.scheduler import _run_market_quote_refresh_job

    return _run_parameterless_job(
        context,
        stage="market-quotes",
        message="正在刷新市场行情",
        action=_run_market_quote_refresh_job,
    )


def run_market_intraday_persist(context: LocalJobContext, payload: dict[str, Any]) -> dict[str, Any]:
    del payload
    from backend.core.jobs.scheduler import _run_market_intraday_persist_job

    return _run_parameterless_job(
        context,
        stage="market-intraday",
        message="正在持久化盘中行情快照",
        action=_run_market_intraday_persist_job,
    )


def run_hk_connect_momentum_review(context: LocalJobContext, payload: dict[str, Any]) -> dict[str, Any]:
    del payload
    from backend.core.jobs.scheduler import _run_hk_connect_momentum_review_job

    return _run_parameterless_job(
        context,
        stage="hk-momentum-review",
        message="正在执行港股通动量复盘",
        action=_run_hk_connect_momentum_review_job,
    )


def run_note_sheet_page_snapshot_backfill(context: LocalJobContext, payload: dict[str, Any]) -> dict[str, Any]:
    del payload
    from backend.core.jobs.scheduler import _run_note_sheet_page_snapshot_backfill_job

    return _run_parameterless_job(
        context,
        stage="sheet-snapshot-backfill",
        message="正在补齐星云表格快照",
        action=_run_note_sheet_page_snapshot_backfill_job,
    )


def run_rime_context_refresh(context: LocalJobContext, payload: dict[str, Any]) -> dict[str, Any]:
    del payload
    from backend.core.jobs.scheduler import _run_rime_context_refresh_job

    return _run_parameterless_job(
        context,
        stage="rime-context-refresh",
        message="正在刷新 Rime 上下文树",
        action=_run_rime_context_refresh_job,
    )


def run_rime_context_lint(context: LocalJobContext, payload: dict[str, Any]) -> dict[str, Any]:
    del payload
    from backend.core.jobs.scheduler import _run_rime_context_lint_job

    return _run_parameterless_job(
        context,
        stage="rime-context-lint",
        message="正在检查 Rime 上下文",
        action=_run_rime_context_lint_job,
    )


def run_dp_tab_cleanup(context: LocalJobContext, payload: dict[str, Any]) -> dict[str, Any]:
    del payload
    from backend.core.dp_browser_tab_cleanup import run_dp_browser_tab_cleanup

    return _run_parameterless_job(
        context,
        stage="browser-tab-cleanup",
        message="正在清理浏览器标签页",
        action=run_dp_browser_tab_cleanup,
    )


def run_public_frontend_deploy(context: LocalJobContext, payload: dict[str, Any]) -> dict[str, Any]:
    del payload
    from backend.core.runtime.public_frontend_deploy import run_public_frontend_deploy_check

    return _run_parameterless_job(
        context,
        stage="frontend-deploy",
        message="正在检查公共前端发布",
        action=run_public_frontend_deploy_check,
    )


def run_ruanyf_weekly_note(context: LocalJobContext, payload: dict[str, Any]) -> dict[str, Any]:
    del payload
    from backend.core.notes.weekly_scheduler import run_ruanyf_weekly_note_job

    return _run_parameterless_job(
        context,
        stage="ruanyf-weekly-note",
        message="正在更新阮一峰周刊笔记",
        action=run_ruanyf_weekly_note_job,
    )


def run_ruanyf_weekly_book(context: LocalJobContext, payload: dict[str, Any]) -> dict[str, Any]:
    del payload
    from backend.core.notes.weekly_scheduler import run_ruanyf_weekly_book_job

    return _run_parameterless_job(
        context,
        stage="ruanyf-weekly-book",
        message="正在更新阮一峰周刊图书",
        action=run_ruanyf_weekly_book_job,
    )


def run_ruanyf_weekly_excerpt_book(context: LocalJobContext, payload: dict[str, Any]) -> dict[str, Any]:
    del payload
    from backend.core.library.ruanyf_weekly_excerpt_book import run_ruanyf_weekly_excerpt_book_job

    return _run_parameterless_job(
        context,
        stage="ruanyf-weekly-excerpt-book",
        message="正在整理科技周刊摘抄图书",
        action=run_ruanyf_weekly_excerpt_book_job,
    )


def run_tibo_x_archive(context: LocalJobContext, payload: dict[str, Any]) -> dict[str, Any]:
    del payload
    from backend.core.library.x_archive import run_tibo_x_archive_job

    return _run_parameterless_job(
        context,
        stage="tibo-x-archive",
        message="正在同步 Tibo X 摘录",
        action=run_tibo_x_archive_job,
    )


def run_xiaoe_incremental_update(context: LocalJobContext, payload: dict[str, Any]) -> dict[str, Any]:
    del payload
    from backend.core.xiaoe_incremental_job import run_xiaoe_incremental_update

    return _run_parameterless_job(
        context,
        stage="xiaoe-incremental-update",
        message="正在执行小鹅通课程增量归档",
        action=run_xiaoe_incremental_update,
    )


for _spec in (
    LocalJobSpec("attendance.summary-templates", run_attendance_summary_templates, "resource:attendance-sheets", "考勤月度模板"),
    LocalJobSpec("media.scheduled-discovery", run_media_scheduled_discovery, "resource:media-discovery-orchestrator", "媒体候选定时补齐"),
    LocalJobSpec("stock.market-quote-refresh", run_market_quote_refresh, "resource:stock", "市场行情刷新"),
    LocalJobSpec("stock.market-intraday-persist", run_market_intraday_persist, "resource:stock", "盘中行情快照"),
    LocalJobSpec("stock.hk-connect-momentum-review", run_hk_connect_momentum_review, "resource:stock", "港股通动量复盘"),
    LocalJobSpec("notes.sheet-page-snapshot-backfill", run_note_sheet_page_snapshot_backfill, "resource:note-sheets", "星云表格快照补齐"),
    LocalJobSpec("rime.context-refresh", run_rime_context_refresh, "resource:rime", "Rime 上下文刷新"),
    LocalJobSpec("rime.context-lint", run_rime_context_lint, "resource:rime", "Rime 上下文检查"),
    LocalJobSpec("browser.dp-tab-cleanup", run_dp_tab_cleanup, "resource:browser", "浏览器标签页清理"),
    LocalJobSpec("frontend.public-deploy-check", run_public_frontend_deploy, "resource:repo", "公共前端发布检查"),
    LocalJobSpec("notes.ruanyf-weekly-note", run_ruanyf_weekly_note, "resource:ruanyf-weekly", "阮一峰周刊笔记"),
    LocalJobSpec("library.ruanyf-weekly-book", run_ruanyf_weekly_book, "resource:ruanyf-weekly", "阮一峰周刊图书"),
    LocalJobSpec("library.ruanyf-weekly-excerpt-book", run_ruanyf_weekly_excerpt_book, "resource:ruanyf-weekly", "科技周刊摘抄入书"),
    LocalJobSpec("library.tibo-x-archive", run_tibo_x_archive, "resource:tibo-x-archive", "Tibo X 摘录归档"),
    LocalJobSpec("archive.xiaoe-incremental-update", run_xiaoe_incremental_update, "resource:xiaoe-archive", "小鹅通课程增量归档"),
):
    register_local_job(_spec)


def run_auto_git_commit(context: LocalJobContext, payload: dict[str, Any]) -> dict[str, Any]:
    from backend.core.ai.auto_git_commit import run_auto_git_commit_worker
    from backend.db import engine

    run_id = str(payload.get("run_id") or "").strip()
    if not run_id:
        raise ValueError("auto_git_commit 缺少 run_id。")
    context.heartbeat(stage="auto-git-commit", message="正在执行自动 Git 提交")
    run_auto_git_commit_worker(engine, run_id, raise_on_failure=True)
    return {"run_id": run_id}


register_local_job(
    LocalJobSpec(
        job_type="maintenance.auto-git-commit",
        title="自动 Git 提交",
        handler=run_auto_git_commit,
        resource_key="resource:repo",
        cancellable=False,
        user_submittable=False,
    )
)


def run_note_metadata_feedback_optimization(
    context: LocalJobContext,
    payload: dict[str, Any],
) -> dict[str, Any]:
    from sqlmodel import Session

    from backend.core.notes.metadata_feedback import run_note_metadata_feedback_optimization_worker
    from backend.db import engine
    from backend.models import NoteMetadataFeedbackOptimizationRun

    run_id = str(payload.get("run_id") or "").strip()
    if not run_id:
        raise ValueError("元标签反馈优化缺少 run_id。")
    context.raise_if_cancelled()
    context.heartbeat(stage="metadata-feedback", message="正在分析元标签反馈")
    run_note_metadata_feedback_optimization_worker(engine, run_id)
    with Session(engine) as session:
        business_run = session.get(NoteMetadataFeedbackOptimizationRun, run_id)
        if business_run is None:
            raise RuntimeError(f"元标签反馈业务 Run 不存在：{run_id}")
        if business_run.status == "failed":
            raise RuntimeError(business_run.error_message or "元标签反馈优化失败。")
        return {
            "run_id": run_id,
            "status": business_run.status,
            "sample_count": int(business_run.sample_count or 0),
            "changed_files": list(business_run.changed_files or []),
        }


register_local_job(
    LocalJobSpec(
        job_type="notes.metadata-feedback-optimization",
        title="元标签反馈优化",
        handler=run_note_metadata_feedback_optimization,
        resource_key="resource:repo",
        cancellable=False,
        user_submittable=False,
    )
)
