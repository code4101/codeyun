from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

from backend.core.fanxiu_data_annotation_jobs import (
    get_fanxiu_data_annotation_manual_job_definition,
    normalize_data_annotation_go_scene_payload,
    register_fanxiu_data_annotation_manual_job,
)


_DEFAULT_RUNTIME_JOB_TYPES = (
    "detect_scene",
    "manual_tick",
    "gift_code_redeem",
    "go_scene",
    "hide_floating_window",
    "daily_signup",
    "mail_claim_check",
)


def register_fanxiu_data_annotation_default_runtime_jobs() -> None:
    if all(get_fanxiu_data_annotation_manual_job_definition(task_type) is not None for task_type in _DEFAULT_RUNTIME_JOB_TYPES):
        return

    @register_fanxiu_data_annotation_manual_job("detect_scene", "单步识别", scheduler_supported=False)
    def _run_data_annotation_detect_scene_manual_job(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> str:
        del payload, stop_event
        frame = runner._screencap(ctx)
        key, score = runner._identify_scene(ctx, frame)
        scene_id = runner.scene_ids.get(key) if runner._scene_matches(key, score) else None
        with runner._lock:
            runner._status.update({
                "phase": "manual_tick",
                "current_scene": scene_id,
                "message": f"单步识别：{key if scene_id is not None else 'unknown'} {score:.0f}%",
                "updated_at": time.time(),
            })
            runner._log_locked("detail", runner._status["message"])
        return "success"

    @register_fanxiu_data_annotation_manual_job("manual_tick", "单步识别", scheduler_supported=False)
    def _run_data_annotation_manual_tick_job(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> str:
        return _run_data_annotation_detect_scene_manual_job(runner, ctx, payload, stop_event)

    @register_fanxiu_data_annotation_manual_job("gift_code_redeem", "兑换礼包码", scheduler_supported=True)
    def _run_data_annotation_gift_code_manual_job(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> str:
        raw_codes = payload.get("codes")
        codes = [str(item).strip() for item in raw_codes] if isinstance(raw_codes, list) else []
        codes = [code for code in codes if code]
        if not codes:
            runner._log("skip", "礼包码为空，跳过")
            return "skipped"
        runner._execute_gift_code_task(ctx, codes, stop_event)
        return "success"

    @register_fanxiu_data_annotation_manual_job(
        "go_scene",
        "到场景",
        scheduler_supported=True,
        normalize_payload=normalize_data_annotation_go_scene_payload,
    )
    def _run_data_annotation_go_scene_manual_job(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        target_scene_id = int(payload.get("target_scene_id") or 49)
        with runner._lock:
            runner._set_status_locked("running", f"场景移动到 #{target_scene_id}", phase="go_scene")
        if target_scene_id == 49:
            runner._align_settings(ctx, stop_event)
            return "success"
        asset_tree_path = ctx.get("asset_tree_path")
        if not isinstance(asset_tree_path, Path):
            raise RuntimeError("缺少场景移动资产树路径，当前只支持直接对齐 #49")
        return runner._go_scene_task(ctx, asset_tree_path, target_scene_id, stop_event)

    @register_fanxiu_data_annotation_manual_job("hide_floating_window", "隐藏浮动窗", scheduler_supported=True)
    def _run_data_annotation_hide_floating_window_manual_job(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> str:
        del payload
        runner._execute_hide_floating_window(ctx, stop_event)
        return "success"

    @register_fanxiu_data_annotation_manual_job("daily_signup", "日常_报名", scheduler_supported=True)
    def _run_data_annotation_daily_signup_manual_job(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        del payload
        return runner._execute_daily_signup_task(ctx, stop_event)

    @register_fanxiu_data_annotation_manual_job("mail_claim_check", "邮件_领取检查", scheduler_supported=True)
    def _run_data_annotation_mail_claim_check_manual_job(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        return runner._execute_mail_claim_check_v2_task(ctx, stop_event, payload)
