from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from backend.core.fanxiu.data_annotation.jobs import (
    get_fanxiu_data_annotation_task_cell_definition,
    normalize_data_annotation_go_scene_payload,
    register_fanxiu_data_annotation_task_cell,
)


_DEFAULT_RUNTIME_JOB_TYPES = (
    "detect_scene",
    "manual_tick",
    "login_game",
    "gift_code_redeem",
    "go_scene",
    "hide_floating_window",
    "daily_mozu",
    "daily_zhenxie",
    "daily_signup",
    "daily_boss",
    "daily_jianling",
    "jianling_cuiling",
    "daily_youli",
    "daily_lingta",
    "daily_lingzu",
    "daily_shuangxiu",
    "daily_yaowang",
    "daily_yaozu",
    "daily_xianyuan",
    "daily_xianyuan_duel",
    "daily_baiye",
    "daily_green_bottle_baiye",
    "daily_gongfeng",
    "daily_xianshi",
    "xianshi_weekly_resources",
    "daily_xianmeng",
    "daily_lundao",
    "daily_daofa",
    "daily_mojie_raid",
    "daily_weekly_dungeon",
    "weekly_hanli",
    "weekly_shengzu",
    "daily_vip",
    "daily_dongtian",
    "daily_dongtian_clear",
    "daily_lingmai",
    "daily_lingmai_clear",
    "daily_dungeon",
    "daily_assistant",
    "daily_audit",
    "xianqiao_trial",
    "mail_selective_claim",
    "xianfu_visit_partner",
    "xianfu_learn_skill",
)


def register_fanxiu_data_annotation_default_runtime_jobs() -> None:
    if all(get_fanxiu_data_annotation_task_cell_definition(task_type) is not None for task_type in _DEFAULT_RUNTIME_JOB_TYPES):
        return

    def _compact_detect_scene_trace(trace: list[dict[str, Any]], *, max_candidates: int = 12) -> list[dict[str, Any]]:
        compact: list[dict[str, Any]] = []
        for event in trace:
            item = dict(event)
            candidates = item.get("candidates")
            if isinstance(candidates, list) and len(candidates) > max_candidates:
                selected_ids = {int(scene_id) for scene_id in item.get("selected_ids") or []}
                selected = [
                    candidate
                    for candidate in candidates
                    if isinstance(candidate, dict) and int(candidate.get("scene_id") or -1) in selected_ids
                ]
                top = sorted(
                    [candidate for candidate in candidates if isinstance(candidate, dict)],
                    key=lambda candidate: float(candidate.get("score") or 0),
                    reverse=True,
                )[:max_candidates]
                merged: list[dict[str, Any]] = []
                seen: set[int] = set()
                for candidate in [*selected, *top]:
                    scene_id = int(candidate.get("scene_id") or -1)
                    if scene_id in seen:
                        continue
                    seen.add(scene_id)
                    merged.append(candidate)
                item["candidate_count"] = len(candidates)
                item["candidates"] = merged
                item["candidates_note"] = f"compact_top_{max_candidates}_plus_selected"
            compact.append(item)
        return compact

    @register_fanxiu_data_annotation_task_cell("detect_scene", "单步识别", scheduler_supported=False)
    def _run_data_annotation_detect_scene_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> str:
        del stop_event
        raw_preferred = payload.get("preferred_scene_ids") or payload.get("candidates") or payload.get("scene_ids")
        preferred_scene_ids: list[int] | None = None
        if isinstance(raw_preferred, list):
            preferred_scene_ids = [int(item) for item in raw_preferred if str(item).strip()]
        elif isinstance(raw_preferred, str) and raw_preferred.strip():
            preferred_scene_ids = [int(part.strip().lstrip("#")) for part in raw_preferred.split(",") if part.strip()]
        trace_enabled = bool(payload.get("trace") or payload.get("debug") or payload.get("explain"))
        trace: list[dict[str, Any]] | None = [] if trace_enabled else None
        frame = runner._screencap(ctx)
        scene_id, score = runner._identify_scene_number(ctx, frame, preferred_scene_ids, trace=trace)
        with runner._lock:
            runner._status.update({
                "phase": "detect_scene_debug" if trace_enabled else "manual_tick",
                "current_scene": scene_id,
                "message": f"单步识别：{('#' + str(scene_id)) if scene_id is not None else 'unknown'} {score:.0f}%",
                "updated_at": time.time(),
            })
            runner._log_locked("detail", runner._status["message"])
            if trace is not None:
                logged_trace = trace if bool(payload.get("full_trace")) else _compact_detect_scene_trace(
                    trace,
                    max_candidates=max(3, min(50, int(payload.get("max_trace_candidates") or 12))),
                )
                max_chars = max(1000, min(30000, int(payload.get("max_trace_chars") or 12000)))
                runner._log_locked("detail", f"detect_scene trace: {json.dumps(logged_trace, ensure_ascii=False, default=str)[:max_chars]}")
        return "success"

    @register_fanxiu_data_annotation_task_cell("manual_tick", "单步识别", scheduler_supported=False)
    def _run_data_annotation_manual_tick_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> str:
        return _run_data_annotation_detect_scene_task_cell(runner, ctx, payload, stop_event)

    @register_fanxiu_data_annotation_task_cell(
        "login_game",
        "登录游戏",
        scheduler_supported=True,
        stable_start_scene_id=None,
    )
    def _run_data_annotation_login_game_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        return runner._execute_login_game_task(ctx, stop_event, payload)

    @register_fanxiu_data_annotation_task_cell("gift_code_redeem", "兑换礼包码", scheduler_supported=True)
    def _run_data_annotation_gift_code_task_cell(
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

    @register_fanxiu_data_annotation_task_cell(
        "go_scene",
        "到场景",
        scheduler_supported=True,
        stable_start_scene_id=None,
        normalize_payload=normalize_data_annotation_go_scene_payload,
    )
    def _run_data_annotation_go_scene_task_cell(
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
        return runner._go_scene_task(
            ctx,
            asset_tree_path,
            target_scene_id,
            stop_event,
            layer0_wait_seconds=payload.get("layer0_wait_seconds"),
        )

    @register_fanxiu_data_annotation_task_cell(
        "hide_floating_window",
        "隐藏浮动窗",
        scheduler_supported=True,
        stable_start_scene_id=None,
    )
    def _run_data_annotation_hide_floating_window_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> str:
        del payload
        runner._execute_hide_floating_window(ctx, stop_event)
        return "success"

    @register_fanxiu_data_annotation_task_cell("daily_mozu", "日常_魔祖", scheduler_supported=True)
    def _run_data_annotation_daily_mozu_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        return runner._execute_daily_runtime_task(
            ctx,
            stop_event,
            payload,
            task_type="daily_mozu",
            label="日常_魔祖",
            flow=runner.daily_mozu_flow,
        )

    @register_fanxiu_data_annotation_task_cell(
        "daily_zhenxie",
        "\u65e5\u5e38_\u9547\u90aa",
        scheduler_supported=True,
        stable_start_scene_id=None,
    )
    def _run_data_annotation_daily_zhenxie_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        return runner._execute_daily_runtime_task(
            ctx,
            stop_event,
            payload,
            task_type="daily_zhenxie",
            label="\u65e5\u5e38_\u9547\u90aa",
            flow=runner.daily_zhenxie_flow,
        )

    @register_fanxiu_data_annotation_task_cell("daily_signup", "日常_报名", scheduler_supported=True)
    def _run_data_annotation_daily_signup_task_cell(
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

    @register_fanxiu_data_annotation_task_cell("daily_boss", "日常_首领", scheduler_supported=True)
    def _run_data_annotation_daily_boss_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        return runner._execute_daily_boss_task(ctx, stop_event, payload)

    @register_fanxiu_data_annotation_task_cell("daily_jianling", "日常_剑灵", scheduler_supported=False)
    def _run_data_annotation_daily_jianling_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        return runner._execute_daily_jianling_task(ctx, stop_event, payload)

    @register_fanxiu_data_annotation_task_cell("jianling_cuiling", "剑灵_淬灵", scheduler_supported=True)
    def _run_data_annotation_jianling_cuiling_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        return runner._execute_jianling_cuiling_task(ctx, stop_event, payload)

    @register_fanxiu_data_annotation_task_cell("daily_youli", "日常_游历", scheduler_supported=True)
    def _run_data_annotation_daily_youli_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        return runner._execute_daily_youli_task(ctx, stop_event, payload)

    @register_fanxiu_data_annotation_task_cell("daily_lingta", "日常_灵塔", scheduler_supported=False)
    def _run_data_annotation_daily_lingta_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        return runner._execute_daily_lingta_task(ctx, stop_event, payload)

    @register_fanxiu_data_annotation_task_cell("daily_lingzu", "日常_灵祖", scheduler_supported=False)
    def _run_data_annotation_daily_lingzu_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        return runner._execute_daily_lingzu_task(ctx, stop_event, payload)

    @register_fanxiu_data_annotation_task_cell("daily_shuangxiu", "日常_双修", scheduler_supported=True)
    def _run_data_annotation_daily_shuangxiu_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        return runner._execute_daily_shuangxiu_task(ctx, stop_event, payload)

    @register_fanxiu_data_annotation_task_cell("daily_yaowang", "日常_妖王来袭", scheduler_supported=False)
    def _run_data_annotation_daily_yaowang_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        return runner._execute_daily_yaowang_task(ctx, stop_event, payload)

    @register_fanxiu_data_annotation_task_cell("daily_yaozu", "日常_妖族袭城", scheduler_supported=False)
    def _run_data_annotation_daily_yaozu_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        return runner._execute_daily_yaozu_task(ctx, stop_event, payload)

    @register_fanxiu_data_annotation_task_cell(
        "daily_xianyuan",
        "日常_挑战仙缘",
        scheduler_supported=True,
        stable_start_scene_id=None,
    )
    def _run_data_annotation_daily_xianyuan_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        return runner._execute_daily_xianyuan_task(ctx, stop_event, payload)

    @register_fanxiu_data_annotation_task_cell("daily_baiye", "日常_拜谒", scheduler_supported=True)
    def _run_data_annotation_daily_baiye_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        return runner._execute_daily_baiye_task(ctx, stop_event, payload)

    @register_fanxiu_data_annotation_task_cell("daily_green_bottle_baiye", "日常_绿瓶拜谒", scheduler_supported=True)
    def _run_data_annotation_daily_green_bottle_baiye_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        return runner._execute_daily_green_bottle_baiye_task(ctx, stop_event, payload)

    @register_fanxiu_data_annotation_task_cell("daily_yihuo", "日常_异火", scheduler_supported=True)
    def _run_data_annotation_daily_yihuo_task_cell(
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

    @register_fanxiu_data_annotation_task_cell("daily_gongfeng", "日常_供奉", scheduler_supported=True)
    def _run_data_annotation_daily_gongfeng_task_cell(
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

    @register_fanxiu_data_annotation_task_cell("daily_xianshi", "仙市_秘藏阁", scheduler_supported=True)
    def _run_data_annotation_daily_xianshi_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        return runner._execute_daily_xianshi_task(ctx, stop_event, payload)

    @register_fanxiu_data_annotation_task_cell("xianshi_weekly_resources", "仙市_每周资源", scheduler_supported=True)
    def _run_data_annotation_xianshi_weekly_resources_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        return runner._execute_xianshi_weekly_resources_task(ctx, stop_event, payload)

    @register_fanxiu_data_annotation_task_cell("daily_xianmeng", "日常_仙盟", scheduler_supported=True)
    def _run_data_annotation_daily_xianmeng_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        return runner._execute_daily_xianmeng_task(ctx, stop_event, payload)

    @register_fanxiu_data_annotation_task_cell("daily_lundao", "论道_座位", scheduler_supported=True)
    def _run_data_annotation_daily_lundao_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        return runner._execute_daily_lundao_task(ctx, stop_event, payload)

    @register_fanxiu_data_annotation_task_cell("daily_daofa", "道法争锋", scheduler_supported=True)
    def _run_data_annotation_daily_daofa_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        return runner._execute_daily_daofa_task(ctx, stop_event, payload)

    @register_fanxiu_data_annotation_task_cell("daily_xianyuan_duel", "仙缘_斗法", scheduler_supported=True)
    def _run_data_annotation_daily_xianyuan_duel_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        return runner._execute_daily_xianyuan_duel_task(ctx, stop_event, payload)

    @register_fanxiu_data_annotation_task_cell("daily_mojie_raid", "日常_奇袭魔界", scheduler_supported=True)
    def _run_data_annotation_daily_mojie_raid_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        return runner._execute_daily_mojie_raid_task(ctx, stop_event, payload)

    @register_fanxiu_data_annotation_task_cell("daily_weekly_dungeon", "日常_周本", scheduler_supported=True)
    def _run_data_annotation_daily_weekly_dungeon_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        return runner._execute_daily_weekly_dungeon_task(ctx, stop_event, payload)

    @register_fanxiu_data_annotation_task_cell("weekly_hanli", "周常_韩立", scheduler_supported=True)
    def _run_data_annotation_weekly_hanli_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        return runner._execute_weekly_hanli_task(ctx, stop_event, payload)

    @register_fanxiu_data_annotation_task_cell("weekly_shengzu", "周常_圣祖", scheduler_supported=True)
    def _run_data_annotation_weekly_shengzu_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        return runner._execute_weekly_shengzu_task(ctx, stop_event, payload)

    @register_fanxiu_data_annotation_task_cell("daily_vip", "日常_vip", scheduler_supported=True)
    def _run_data_annotation_daily_vip_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        return runner._execute_daily_vip_task(ctx, stop_event, payload)

    @register_fanxiu_data_annotation_task_cell("daily_dongtian", "洞天_领取", scheduler_supported=True)
    def _run_data_annotation_daily_dongtian_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        return runner._execute_daily_dongtian_task(ctx, stop_event, payload)

    @register_fanxiu_data_annotation_task_cell("daily_dongtian_clear", "洞天_行动力", scheduler_supported=True)
    def _run_data_annotation_daily_dongtian_clear_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        return runner._execute_daily_dongtian_clear_task(ctx, stop_event, payload)

    @register_fanxiu_data_annotation_task_cell("daily_lingmai", "灵脉_座位", scheduler_supported=True)
    def _run_data_annotation_daily_lingmai_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        return runner._execute_daily_lingmai_task(ctx, stop_event, payload)

    @register_fanxiu_data_annotation_task_cell("daily_lingmai_clear", "灵脉_清体力", scheduler_supported=True)
    def _run_data_annotation_daily_lingmai_clear_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        return runner._execute_daily_lingmai_clear_task(ctx, stop_event, payload)

    @register_fanxiu_data_annotation_task_cell("daily_dungeon", "日常_每日副本", scheduler_supported=True)
    def _run_data_annotation_daily_dungeon_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        return runner._execute_daily_dungeon_task(ctx, stop_event, payload)

    @register_fanxiu_data_annotation_task_cell("daily_assistant", "日常_助手", scheduler_supported=True)
    def _run_data_annotation_daily_assistant_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        return runner._execute_daily_assistant_task(ctx, stop_event, payload)

    @register_fanxiu_data_annotation_task_cell("daily_audit", "日常_复核", scheduler_supported=False)
    def _run_data_annotation_daily_audit_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        return runner._execute_daily_audit_task(ctx, stop_event, payload)

    @register_fanxiu_data_annotation_task_cell("xianqiao_trial", "仙窍_试炼", scheduler_supported=True)
    def _run_data_annotation_xianqiao_trial_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        return runner._execute_xianqiao_trial_task(ctx, stop_event, payload)

    @register_fanxiu_data_annotation_task_cell("mail_selective_claim", "邮件_选择性领取", scheduler_supported=True)
    def _run_data_annotation_mail_selective_claim_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        if payload.get("observe_only") or payload.get("scan_only") or payload.get("full_scan") or str(payload.get("scan_mode") or "").strip().lower() in {"full", "full_scan", "observe", "observe_only", "refresh", "sync"}:
            return runner._execute_mail_legacy_scan_task(ctx, stop_event, payload)
        return runner._execute_mail_selective_claim_task(ctx, stop_event, payload)

    @register_fanxiu_data_annotation_task_cell("xianfu_visit_partner", "仙府_寻访仙侣", scheduler_supported=True)
    def _run_data_annotation_xianfu_visit_partner_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        return runner._execute_xianfu_visit_partner_task(ctx, stop_event, payload)

    @register_fanxiu_data_annotation_task_cell("xianfu_learn_skill", "仙府_领悟绝技", scheduler_supported=True)
    def _run_data_annotation_xianfu_learn_skill_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        return runner._execute_xianfu_learn_skill_task(ctx, stop_event, payload)

