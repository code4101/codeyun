from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

from backend.core.fanxiu.data_annotation.jobs import (
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
    "daily_boss",
    "daily_lingzu",
    "daily_jianling",
    "daily_lingta",
    "daily_xianyuan",
    "daily_yaowang",
    "daily_yaozu",
    "daily_youli",
    "daily_baiye",
    "daily_yihuo",
    "daily_gongfeng",
    "daily_xianshi",
    "daily_dungeon",
    "daily_assistant",
    "daily_shuangxiu",
    "daily_audit",
    "mail_cleanup",
    "xianfu_visit_partner",
    "xianfu_learn_skill",
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
        scene_id, score = runner._identify_scene_number(ctx, frame)
        with runner._lock:
            runner._status.update({
                "phase": "manual_tick",
                "current_scene": scene_id,
                "message": f"单步识别：{('#' + str(scene_id)) if scene_id is not None else 'unknown'} {score:.0f}%",
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
        return runner._execute_daily_runtime_task(
            ctx,
            stop_event,
            payload,
            task_type="daily_signup",
            label="日常_报名",
            flow=runner.日常报名流程,
        )

    @register_fanxiu_data_annotation_manual_job("daily_boss", "日常_首领", scheduler_supported=True)
    def _run_data_annotation_daily_boss_manual_job(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        return runner._execute_daily_boss_task(ctx, stop_event, payload)

    @register_fanxiu_data_annotation_manual_job("daily_lingzu", "日常_灵祖", scheduler_supported=True)
    def _run_data_annotation_daily_lingzu_manual_job(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        return runner._execute_daily_lingzu_task(ctx, stop_event, payload)

    @register_fanxiu_data_annotation_manual_job("daily_jianling", "日常_剑灵", scheduler_supported=True)
    def _run_data_annotation_daily_jianling_manual_job(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        return runner._execute_daily_jianling_task(ctx, stop_event, payload)

    @register_fanxiu_data_annotation_manual_job("daily_lingta", "日常_灵塔", scheduler_supported=True)
    def _run_data_annotation_daily_lingta_manual_job(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        return runner._execute_daily_lingta_task(ctx, stop_event, payload)

    @register_fanxiu_data_annotation_manual_job("daily_xianyuan", "日常_挑战仙缘", scheduler_supported=True)
    def _run_data_annotation_daily_xianyuan_manual_job(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        return runner._execute_daily_xianyuan_task(ctx, stop_event, payload)

    @register_fanxiu_data_annotation_manual_job("daily_yaowang", "日常_妖王来袭", scheduler_supported=True)
    def _run_data_annotation_daily_yaowang_manual_job(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        return runner._execute_daily_yaowang_task(ctx, stop_event, payload)

    @register_fanxiu_data_annotation_manual_job("daily_yaozu", "日常_妖族袭城", scheduler_supported=True)
    def _run_data_annotation_daily_yaozu_manual_job(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        return runner._execute_daily_yaozu_task(ctx, stop_event, payload)

    @register_fanxiu_data_annotation_manual_job("daily_youli", "日常_游历", scheduler_supported=True)
    def _run_data_annotation_daily_youli_manual_job(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        return runner._execute_daily_youli_task(ctx, stop_event, payload)

    @register_fanxiu_data_annotation_manual_job("daily_baiye", "日常_拜谒", scheduler_supported=True)
    def _run_data_annotation_daily_baiye_manual_job(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        return runner._execute_daily_baiye_task(ctx, stop_event, payload)

    @register_fanxiu_data_annotation_manual_job("daily_yihuo", "日常_异火", scheduler_supported=True)
    def _run_data_annotation_daily_yihuo_manual_job(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        return runner._execute_daily_runtime_task(
            ctx,
            stop_event,
            payload,
            task_type="daily_yihuo",
            label="日常_异火",
            flow=runner.日常异火流程,
        )

    @register_fanxiu_data_annotation_manual_job("daily_gongfeng", "日常_供奉", scheduler_supported=True)
    def _run_data_annotation_daily_gongfeng_manual_job(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        return runner._execute_daily_runtime_task(
            ctx,
            stop_event,
            payload,
            task_type="daily_gongfeng",
            label="日常_供奉",
            flow=runner.日常供奉流程,
        )

    @register_fanxiu_data_annotation_manual_job("daily_xianshi", "日常_仙市", scheduler_supported=True)
    def _run_data_annotation_daily_xianshi_manual_job(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        return runner._execute_daily_xianshi_task(ctx, stop_event, payload)

    @register_fanxiu_data_annotation_manual_job("daily_dungeon", "日常_每日副本", scheduler_supported=True)
    def _run_data_annotation_daily_dungeon_manual_job(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        return runner._execute_daily_dungeon_task(ctx, stop_event, payload)

    @register_fanxiu_data_annotation_manual_job("daily_assistant", "日常_助手", scheduler_supported=True)
    def _run_data_annotation_daily_assistant_manual_job(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        return runner._execute_daily_assistant_task(ctx, stop_event, payload)

    @register_fanxiu_data_annotation_manual_job("daily_shuangxiu", "日常_双修", scheduler_supported=True)
    def _run_data_annotation_daily_shuangxiu_manual_job(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        return runner._execute_daily_shuangxiu_task(ctx, stop_event, payload)

    @register_fanxiu_data_annotation_manual_job("daily_audit", "日常_复核", scheduler_supported=False)
    def _run_data_annotation_daily_audit_manual_job(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        return runner._execute_daily_audit_task(ctx, stop_event, payload)

    @register_fanxiu_data_annotation_manual_job("mail_cleanup", "邮件_清理", scheduler_supported=True)
    def _run_data_annotation_mail_cleanup_manual_job(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        return runner._execute_mail_cleanup_task(ctx, stop_event, payload)

    @register_fanxiu_data_annotation_manual_job("xianfu_visit_partner", "仙府_寻访仙侣", scheduler_supported=True)
    def _run_data_annotation_xianfu_visit_partner_manual_job(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        return runner._execute_xianfu_visit_partner_task(ctx, stop_event, payload)

    @register_fanxiu_data_annotation_manual_job("xianfu_learn_skill", "仙府_领悟绝技", scheduler_supported=True)
    def _run_data_annotation_xianfu_learn_skill_manual_job(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        return runner._execute_xianfu_learn_skill_task(ctx, stop_event, payload)

