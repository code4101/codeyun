from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from backend.core.fanxiu.data_annotation.effective_time import job_now
from backend.core.fanxiu.data_annotation.jobs import (
    get_fanxiu_data_annotation_task_cell_definition,
    normalize_data_annotation_go_scene_payload,
    register_fanxiu_data_annotation_task_cell,
)


_DEFAULT_RUNTIME_JOB_TYPES = (
    "detect_scene",
    "manual_tick",
    "maintenance_recovery",
    "login_game",
    "weekly_gift_code",
    "tianjige_forum_quiz",
    "go_scene",
    "hide_floating_window",
    "daily_mozu",
    "daily_zhenxie",
    "activity_daily_list_sync",
    "daily_activity",
    "weekly_activity",
    "daily_redpacket",
    "daily_signup",
    "moyu_signup",
    "moyu_challenge",
    "daily_boss",
    "daily_experience",
    "daily_jianling",
    "jianling_cuiling",
    "lingzhuang_strengthening",
    "beast_spirit_update",
    "storage_bag_operation",
    "resource_auto_use",
    "wanxiang_baoge_six_yuan",
    "xianyuan_auto_gift",
    "holy_wood_prayer",
    "xianyan_host_baihua",
    "xianyan_participation",
    "xianyan_rewards",
    "daily_youli",
    "daily_lingta",
    "lingta_challenge",
    "daily_lingzu",
    "daily_shuangxiu",
    "daily_yaowang",
    "daily_yaozu",
    "daily_xianyuan",
    "daozu_challenge",
    "daily_task_rewards",
    "daily_xianyuan_duel",
    "daily_baiye",
    "daily_green_bottle_baiye",
    "daily_gongfeng",
    "daily_xianshi",
    "xianshi_weekly_resources",
    "xianshi_zhenwuge",
    "xianshi_langya_rankings",
    "daily_xianmeng",
    "daily_lundao",
    "daily_daofa",
    "daily_mojie_raid",
    "daily_weekly_dungeon",
    "bubble_weekly_pills",
    "take_medicine_batch",
    "weekly_hanli",
    "daily_lingquan",
    "weekly_shengzu",
    "daily_vip",
    "daily_signin",
    "daily_xuanhuang",
    "daily_dongtian",
    "daily_dongtian_clear",
    "dongtian_seating",
    "daily_lingmai",
    "daily_lingmai_clear",
    "daily_dungeon",
    "daily_assistant",
    "lilian_claim",
    "lilian_event",
    "activity_quiz",
    "activity_quiz_final",
    "daily_audit",
    "xianqiao_trial",
    "mail_selective_claim",
    "mail_claim_law",
    "resource_rank_daily_free_gift",
    "dandao_task_rewards",
    "yuanding_sansheng_daily_gift",
    "xianfu_visit_partner",
    "xianfu_learn_skill",
    "penglai_xianzang_config",
    "penglai_xianzang_lottery",
    "kunlun_secret_config",
    "kunlun_secret_lottery",
    "lingxiao_xianhui",
    "wanbao_zhenbao",
    "xutian_palace_rankings",
    "xutian_palace_native_auto",
    "yunmeng_trial_auto_challenge",
    "magic_invasion_explore",
    "ranking_lifecycle",
    "resource_ranking",
    "yunmeng_tail",
)


def _run_manual_standard_job(runner: Any, task_id: str, operation: Any):
    """Keep a manually triggered Job dormant after every normal return."""

    result = operation()
    if hasattr(result, "send"):
        result = yield from result
    runner._persist_scheduler_task_next_time(task_id, None)
    return result


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
        if scene_id is not None or preferred_scene_ids is None:
            runner._commit_scene_observation(ctx, frame, scene_id, score)
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
        "maintenance_recovery",
        "系统_维护恢复",
        scheduler_supported=True,
    )
    def _run_data_annotation_maintenance_recovery_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        return runner._execute_maintenance_recovery_task(ctx, stop_event, payload)

    @register_fanxiu_data_annotation_task_cell(
        "login_game",
        "登录",
        scheduler_supported=True,
        standard_job=True,
        standard_job_id="login-game",
        standard_job_description="手动",
        standard_job_payload={"unbounded_runtime": True},
    )
    def _run_data_annotation_login_game_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        result = yield from _run_manual_standard_job(
            runner,
            "login-game",
            lambda: runner._execute_login_game_task(ctx, stop_event, payload),
        )
        terminal_message = str(
            getattr(runner, "_login_game_terminal_message", "") or ""
        ).strip()
        if result == "success" and terminal_message:
            return {"result": "success", "message": terminal_message}
        return result

    @register_fanxiu_data_annotation_task_cell("weekly_gift_code", "每周_礼包码", scheduler_supported=True)
    def _run_data_annotation_weekly_gift_code_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> dict[str, Any]:
        # The weekly task owns its post-redemption departure because it must
        # persist the next trigger before treating departure as best effort.
        return (yield from runner._execute_weekly_gift_code_task(ctx, stop_event, payload))

    @register_fanxiu_data_annotation_task_cell(
        "tianjige_forum_quiz",
        "天机阁_有奖竞答",
        scheduler_supported=True,
    )
    def _run_data_annotation_tianjige_forum_quiz_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> dict[str, Any]:
        from backend.core.fanxiu.data_annotation.tasks.tianjige_forum_quiz import (
            execute_tianjige_forum_quiz_task,
        )

        return execute_tianjige_forum_quiz_task(runner, ctx, payload, stop_event)

    @register_fanxiu_data_annotation_task_cell(
        "go_scene",
        "到场景",
        scheduler_supported=True,
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

    @register_fanxiu_data_annotation_task_cell(
        "daily_mozu",
        "日常_魔祖",
        scheduler_supported=True,
        admission=lambda runner, payload: runner.daily_mozu_admission(payload),
    )
    def _run_data_annotation_daily_mozu_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        return (yield from runner._execute_daily_runtime_task(
            ctx,
            stop_event,
            payload,
            task_type="daily_mozu",
            label="日常_魔祖",
            flow=runner.daily_mozu_flow,
        ))

    @register_fanxiu_data_annotation_task_cell(
        "daily_zhenxie",
        "\u65e5\u5e38_\u9547\u90aa",
        scheduler_supported=True,
        admission=lambda runner, payload: runner.daily_zhenxie_admission(payload),
    )
    def _run_data_annotation_daily_zhenxie_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        result = yield from runner._execute_daily_runtime_task(
            ctx,
            stop_event,
            payload,
            task_type="daily_zhenxie",
            label="\u65e5\u5e38_\u9547\u90aa",
            flow=runner.daily_zhenxie_flow,
        )
        runtime = runner._fanxiu_runtime(ctx, stop_event=stop_event)
        yield from runtime.goto_view(34)
        return result

    @register_fanxiu_data_annotation_task_cell("daily_activity", "日常_活跃度", scheduler_supported=True)
    def _run_data_annotation_daily_activity_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        runtime = runner._fanxiu_runtime(ctx, stop_event=stop_event)
        yield from runtime.goto_view(34)
        result = yield from runner._execute_daily_activity_task(ctx, stop_event, payload)
        yield from runtime.goto_view(34)
        return result

    @register_fanxiu_data_annotation_task_cell(
        "activity_daily_list_sync",
        "活动_每日清单同步",
        scheduler_supported=True,
        standard_job=True,
        standard_job_id="activity-daily-list-sync",
        standard_job_description="每日",
    )
    def _run_data_annotation_daily_activity_list_sync_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        return (
            yield from runner._execute_daily_activity_list_sync_task(
                ctx,
                stop_event,
                payload,
            )
        )

    @register_fanxiu_data_annotation_task_cell("weekly_activity", "周常_活跃度", scheduler_supported=True)
    def _run_data_annotation_weekly_activity_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        runtime = runner._fanxiu_runtime(ctx, stop_event=stop_event)
        yield from runtime.goto_view(34)
        result = yield from runner._execute_weekly_activity_task(ctx, stop_event, payload)
        yield from runtime.goto_view(34)
        return result

    @register_fanxiu_data_annotation_task_cell("daily_redpacket", "日常_红包", scheduler_supported=True)
    def _run_data_annotation_daily_redpacket_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        runtime = runner._fanxiu_runtime(ctx, stop_event=stop_event)
        yield from runtime.goto_view(34)
        result = yield from runner._execute_daily_redpacket_task(ctx, stop_event, payload)
        yield from runtime.goto_view(34)
        return result

    @register_fanxiu_data_annotation_task_cell("daily_signup", "日常_报名", scheduler_supported=True)
    def _run_data_annotation_daily_signup_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        runtime = runner._fanxiu_runtime(ctx, stop_event=stop_event)
        yield from runtime.goto_view(34)
        result = yield from runner._execute_daily_runtime_task(
            ctx,
            stop_event,
            payload,
            task_type="daily_signup",
            label="日常_报名",
            flow=runner.日常报名流程,
        )
        yield from runtime.goto_view(34)
        return result

    @register_fanxiu_data_annotation_task_cell(
        "moyu_signup",
        "魔狱_报名",
        scheduler_supported=True,
        admission=lambda runner, payload: runner.moyu_signup_admission(payload),
    )
    def _run_data_annotation_moyu_signup_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        runtime = runner._fanxiu_runtime(ctx, stop_event=stop_event)
        yield from runtime.goto_view(34)
        result = yield from runner._execute_daily_runtime_task(
            ctx,
            stop_event,
            payload,
            task_type="moyu_signup",
            label="魔狱_报名",
            flow=runner.moyu_signup_flow,
        )
        yield from runtime.goto_view(34)
        return result

    @register_fanxiu_data_annotation_task_cell(
        "moyu_challenge",
        "魔狱_挑战",
        scheduler_supported=True,
        admission=lambda runner, payload: runner.moyu_challenge_admission(payload),
    )
    def _run_data_annotation_moyu_challenge_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        return (yield from runner._execute_daily_runtime_task(
            ctx,
            stop_event,
            payload,
            task_type="moyu_challenge",
            label="魔狱_挑战",
            flow=runner.moyu_challenge_flow,
        ))

    @register_fanxiu_data_annotation_task_cell("daily_boss", "日常_首领", scheduler_supported=True)
    def _run_data_annotation_daily_boss_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        # Every business branch already owns its entry normalization and
        # `_return_daily_boss_to_world` cleanup.  A wrapper-level second goto
        # can replay the unknown-scene fallback during the long exit animation
        # and overwrite a completed boss result with run_status=error.
        return (yield from runner._execute_daily_boss_task(ctx, stop_event, payload))

    @register_fanxiu_data_annotation_task_cell("daily_experience", "日常_经验", scheduler_supported=True)
    def _run_data_annotation_daily_experience_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        runtime = runner._fanxiu_runtime(ctx, stop_event=stop_event)
        yield from runtime.goto_view(34)
        # The Job owns its business completion point and best-effort departure.
        # A wrapper-level second goto can turn an already-complete run into an
        # error during a long exit animation and must not replay cleanup.
        return (yield from runner._execute_daily_experience_task(ctx, stop_event, payload))

    @register_fanxiu_data_annotation_task_cell("daily_jianling", "日常_剑灵", scheduler_supported=False)
    def _run_data_annotation_daily_jianling_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        return runner._execute_daily_jianling_task(ctx, stop_event, payload)

    @register_fanxiu_data_annotation_task_cell(
        "jianling_cuiling",
        "剑灵_淬灵",
        scheduler_supported=True,
        # This manual transaction starts from the #349 page the user has
        # already opened and finishes in place when the level reaches 1000.
    )
    def _run_data_annotation_jianling_cuiling_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        return runner._execute_jianling_cuiling_task(ctx, stop_event, payload)

    @register_fanxiu_data_annotation_task_cell(
        "lingzhuang_strengthening",
        "灵装化道_强化",
        scheduler_supported=True,
        standard_job=True,
        standard_job_id="lingzhuang-strengthening",
        standard_job_description="手动",
        standard_job_payload={
            "target_tier": 10,
            "max_clicks": 200,
            "max_runtime_seconds": 7200,
        },
    )
    def _run_data_annotation_lingzhuang_strengthening_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        from backend.core.fanxiu.data_annotation.tasks.lingzhuang_strengthening import (
            STANDARD_JOB_ID,
            execute_lingzhuang_strengthening_task,
        )

        runtime = runner._fanxiu_runtime(ctx, ctx.get("asset_tree_path"), stop_event=stop_event)
        current_fact_scene_ids = (445, 446)
        current_scene_id, _score, _frame = runtime.current_scene(current_fact_scene_ids, update=True)
        if current_scene_id not in current_fact_scene_ids:
            yield from runtime.goto_view(34)
        result = yield from _run_manual_standard_job(
            runner,
            STANDARD_JOB_ID,
            lambda: execute_lingzhuang_strengthening_task(
                runner,
                ctx,
                payload,
                stop_event,
            ),
        )
        yield from runtime.goto_view(34)
        return result

    @register_fanxiu_data_annotation_task_cell(
        "beast_spirit_update",
        "兽魂更新",
        scheduler_supported=True,
        standard_job=True,
        standard_job_id="beast-spirit-update",
        standard_job_description="每周",
        standard_job_payload={"max_source_level": 8},
    )
    def _run_data_annotation_beast_spirit_update_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        from backend.core.fanxiu.data_annotation.tasks.beast_spirit_update import (
            STANDARD_JOB_ID,
            execute_beast_spirit_update_task,
            next_beast_spirit_update_at,
        )

        runtime = runner._fanxiu_runtime(
            ctx,
            ctx.get("asset_tree_path"),
            stop_event=stop_event,
        )
        result = yield from execute_beast_spirit_update_task(
            runner,
            ctx,
            payload,
            stop_event,
        )
        yield from runtime.goto_view(34)
        runner._persist_scheduler_task_next_time(
            STANDARD_JOB_ID,
            next_beast_spirit_update_at().strftime("%Y-%m-%d %H:%M:%S"),
        )
        return result

    @register_fanxiu_data_annotation_task_cell(
        "storage_bag_operation",
        "储物袋_操作",
    )
    def _run_data_annotation_storage_bag_operation_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        from backend.core.fanxiu.data_annotation.tasks.storage_bag_operation import (
            execute_storage_bag_operation_task,
        )

        return (yield from execute_storage_bag_operation_task(
            runner,
            ctx,
            payload,
            stop_event,
        ))

    @register_fanxiu_data_annotation_task_cell(
        "resource_auto_use",
        "资源_自动使用",
        scheduler_supported=True,
        standard_job=True,
        standard_job_id="resource-auto-use",
        standard_job_description="手动",
        standard_job_payload={"max_rounds": 3},
    )
    def _run_data_annotation_resource_auto_use_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        from backend.core.fanxiu.data_annotation.tasks.resource_auto_use import (
            STANDARD_JOB_ID,
            execute_resource_auto_use_task,
        )

        return (
            yield from _run_manual_standard_job(
                runner,
                STANDARD_JOB_ID,
                lambda: execute_resource_auto_use_task(
                    runner,
                    ctx,
                    payload,
                    stop_event,
                ),
            )
        )

    @register_fanxiu_data_annotation_task_cell(
        "wanxiang_baoge_six_yuan",
        "万象宝阁_六元代币宝匣",
        scheduler_supported=True,
        standard_job=True,
        standard_job_id="wanxiang-baoge-six-yuan",
        standard_job_description="手动",
        standard_job_payload={"max_refreshes": 100},
    )
    def _run_data_annotation_wanxiang_baoge_six_yuan_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        from backend.core.fanxiu.data_annotation.tasks.wanxiang_baoge import (
            STANDARD_JOB_ID,
            execute_wanxiang_baoge_task,
        )

        return (
            yield from _run_manual_standard_job(
                runner,
                STANDARD_JOB_ID,
                lambda: execute_wanxiang_baoge_task(
                    runner,
                    ctx,
                    payload,
                    stop_event,
                ),
            )
        )

    @register_fanxiu_data_annotation_task_cell(
        "xianyuan_auto_gift",
        "仙缘_自动送礼",
        scheduler_supported=True,
        standard_job=True,
        standard_job_id="xianyuan-auto-gift",
        standard_job_description="手动",
    )
    def _run_data_annotation_xianyuan_auto_gift_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        from backend.core.fanxiu.data_annotation.tasks.xianyuan_auto_gift import (
            STANDARD_JOB_ID,
            execute_xianyuan_auto_gift_task,
        )

        return (
            yield from _run_manual_standard_job(
                runner,
                STANDARD_JOB_ID,
                lambda: execute_xianyuan_auto_gift_task(
                    runner,
                    ctx,
                    payload,
                    stop_event,
                ),
            )
        )

    @register_fanxiu_data_annotation_task_cell(
        "holy_wood_prayer",
        "圣木祈愿",
        scheduler_supported=True,
        standard_job=True,
        standard_job_id="holy-wood-prayer",
        standard_job_description="手动",
        standard_job_payload={
            "spend_budget": 2952,
            "max_task_clicks": 20,
            "max_draw_rounds": 64,
        },
    )
    def _run_data_annotation_holy_wood_prayer_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        from backend.core.fanxiu.data_annotation.tasks.holy_wood_prayer import (
            HOLY_WOOD_TASK_ID,
            execute_holy_wood_prayer_task,
        )

        return (
            yield from _run_manual_standard_job(
                runner,
                HOLY_WOOD_TASK_ID,
                lambda: execute_holy_wood_prayer_task(
                    runner,
                    ctx,
                    payload,
                    stop_event,
                ),
            )
        )

    @register_fanxiu_data_annotation_task_cell(
        "xianyan_host_baihua",
        "仙宴_清理",
        scheduler_supported=True,
        standard_job=True,
        standard_job_id="xianyan-host-baihua",
        standard_job_description="手动",
        standard_job_payload={
            "max_rounds": 100,
            "max_scrolls": 100,
            "max_runtime_seconds": 3600,
        },
    )
    def _run_data_annotation_xianyan_host_baihua_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        return (
            yield from _run_manual_standard_job(
                runner,
                "xianyan-host-baihua",
                lambda: runner._execute_xianyan_clean_task(ctx, stop_event, payload),
            )
        )

    @register_fanxiu_data_annotation_task_cell(
        "xianyan_participation",
        "仙宴_参与同档",
        scheduler_supported=True,
        standard_job=True,
        standard_job_id="xianyan-participation",
        standard_job_description="手动",
        standard_job_payload={"max_rounds": 100, "max_scrolls": 100, "max_runtime_seconds": 3600},
    )
    def _run_data_annotation_xianyan_participation_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        return (
            yield from _run_manual_standard_job(
                runner,
                "xianyan-participation",
                lambda: runner._execute_xianyan_participation_task(ctx, stop_event, payload),
            )
        )

    @register_fanxiu_data_annotation_task_cell(
        "xianyan_rewards",
        "仙宴_获得奖励",
        scheduler_supported=True,
    )
    def _run_data_annotation_xianyan_rewards_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        return runner._execute_xianyan_rewards_task(ctx, stop_event, payload)

    @register_fanxiu_data_annotation_task_cell(
        "daily_youli",
        "日常_游历",
        scheduler_supported=True,
        standard_job=True,
        standard_job_id="legacy-daily-youli",
        standard_job_description="手动",
    )
    def _run_data_annotation_daily_youli_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        runtime = runner._fanxiu_runtime(ctx, stop_event=stop_event)
        yield from runtime.goto_view(34)
        result = yield from _run_manual_standard_job(
            runner,
            "legacy-daily-youli",
            lambda: runner._execute_daily_youli_task(ctx, stop_event, payload),
        )
        yield from runtime.goto_view(34)
        return result

    @register_fanxiu_data_annotation_task_cell("daily_lingta", "日常_灵塔", scheduler_supported=False)
    def _run_data_annotation_daily_lingta_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        return runner._execute_daily_lingta_task(ctx, stop_event, payload)

    from backend.core.fanxiu.data_annotation.tasks.lingta_challenge import (
        lingta_challenge_admission,
    )

    @register_fanxiu_data_annotation_task_cell(
        "lingta_challenge",
        "灵塔_挑战",
        scheduler_supported=True,
        admission=lambda runner, payload: runner.apply_lingta_challenge_admission(payload),
        standard_job=True,
        standard_job_id="lingta-challenge",
        standard_job_description="每日",
        standard_job_payload={
            "max_runtime_seconds": 5400,
            "monitor_timeout_seconds": 3600,
            "monitor_poll_seconds": 2,
            "max_scrolls": 30,
        },
    )
    def _run_data_annotation_lingta_challenge_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        return runner._execute_lingta_challenge_task(ctx, stop_event, payload)

    @register_fanxiu_data_annotation_task_cell("daily_lingzu", "日常_灵祖", scheduler_supported=False)
    def _run_data_annotation_daily_lingzu_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        return runner._execute_daily_lingzu_task(ctx, stop_event, payload)

    @register_fanxiu_data_annotation_task_cell(
        "daily_shuangxiu",
        "日常_双修",
        scheduler_supported=True,
        standard_job=True,
        standard_job_id="legacy-daily-shuangxiu",
        standard_job_description="手动",
    )
    def _run_data_annotation_daily_shuangxiu_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        runtime = runner._fanxiu_runtime(ctx, stop_event=stop_event)
        yield from runtime.goto_view(34)
        result = yield from _run_manual_standard_job(
            runner,
            "legacy-daily-shuangxiu",
            lambda: runner._execute_daily_shuangxiu_task(ctx, stop_event, payload),
        )
        yield from runtime.goto_view(34)
        return result

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
    )
    def _run_data_annotation_daily_xianyuan_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        result = yield from runner._execute_daily_xianyuan_task(ctx, stop_event, payload)
        runtime = runner._fanxiu_runtime(ctx, stop_event=stop_event)
        yield from runtime.goto_view(34)
        return result

    @register_fanxiu_data_annotation_task_cell(
        "daozu_challenge",
        "道祖_挑战",
        scheduler_supported=True,
        standard_job=True,
        standard_job_id="daozu-challenge",
        standard_job_description="每日",
        standard_job_payload={"max_runtime_seconds": 1800},
    )
    def _run_data_annotation_daozu_challenge_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        return (yield from runner._execute_daozu_challenge_task(ctx, stop_event, payload))

    @register_fanxiu_data_annotation_task_cell(
        "daily_task_rewards",
        "日常_任务奖励",
        scheduler_supported=True,
        standard_job=True,
        standard_job_id="daily-task-rewards",
        standard_job_description="每日",
    )
    def _run_data_annotation_daily_task_rewards_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        return (yield from runner._execute_daily_task_rewards_task(ctx, stop_event, payload))

    @register_fanxiu_data_annotation_task_cell("daily_baiye", "日常_拜谒", scheduler_supported=True)
    def _run_data_annotation_daily_baiye_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        # The business handler owns entry normalization and the complete
        # #266 -> #265 -> #264 -> #34 return stack.  Repeating goto_view here
        # can turn a confirmed拜谒 into an error while the world transition is
        # still visually unknown.
        result = yield from runner._execute_daily_baiye_task(ctx, stop_event, payload)
        if result == "success":
            return {
                "result": "success",
                "message": "日常_拜谒：今日拜谒已确认完成",
            }
        return {
            "result": "success",
            "message": "日常_拜谒：本轮未确认完成，已按业务规则安排复查",
        }

    @register_fanxiu_data_annotation_task_cell("daily_green_bottle_baiye", "日常_绿瓶拜谒", scheduler_supported=True)
    def _run_data_annotation_daily_green_bottle_baiye_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        runtime = runner._fanxiu_runtime(ctx, stop_event=stop_event)
        yield from runtime.goto_view(34)
        result = yield from runner._execute_daily_green_bottle_baiye_task(ctx, stop_event, payload)
        yield from runtime.goto_view(34)
        return result

    @register_fanxiu_data_annotation_task_cell("daily_yihuo", "日常_异火", scheduler_supported=True)
    def _run_data_annotation_daily_yihuo_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        runtime = runner._fanxiu_runtime(ctx, stop_event=stop_event)
        yield from runtime.goto_view(34)
        result = yield from runner._execute_daily_runtime_task(
            ctx,
            stop_event,
            payload,
            task_type="daily_yihuo",
            label="日常_异火",
            flow=runner.日常异火流程,
        )
        yield from runtime.goto_view(34)
        return result

    # 日常_助手的一键执行已经覆盖供奉；保留 Cell 实现用于调试，但不再作为
    # 独立 Scheduler Job 暴露或触发。
    @register_fanxiu_data_annotation_task_cell("daily_gongfeng", "日常_供奉", scheduler_supported=False)
    def _run_data_annotation_daily_gongfeng_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        runtime = runner._fanxiu_runtime(ctx, stop_event=stop_event)
        yield from runtime.goto_view(34)
        result = yield from runner._execute_daily_runtime_task(
            ctx,
            stop_event,
            payload,
            task_type="daily_gongfeng",
            label="日常_供奉",
            flow=runner.日常供奉流程,
        )
        yield from runtime.goto_view(34)
        return result

    @register_fanxiu_data_annotation_task_cell("daily_xianshi", "仙市_秘藏阁", scheduler_supported=True)
    def _run_data_annotation_daily_xianshi_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        # The business handler owns both its #34 entry normalization and its
        # best-effort completion cleanup.  A second wrapper-level goto can
        # replay the unknown-scene fallback while the game is still in the
        # long return animation, turning a completed claim into run_status=error.
        return (yield from runner._execute_daily_xianshi_task(ctx, stop_event, payload))

    @register_fanxiu_data_annotation_task_cell(
        "xianshi_weekly_resources",
        "仙市_每周资源",
        scheduler_supported=True,
        admission=lambda runner, payload: runner.xianshi_weekly_resources_admission(payload),
    )
    def _run_data_annotation_xianshi_weekly_resources_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        runtime = runner._fanxiu_runtime(ctx, stop_event=stop_event)
        yield from runtime.goto_view(34)
        result = yield from runner._execute_xianshi_weekly_resources_task(ctx, stop_event, payload)
        yield from runtime.goto_view(34)
        return result

    @register_fanxiu_data_annotation_task_cell(
        "xianshi_zhenwuge",
        "仙市_真悟阁",
        scheduler_supported=True,
    )
    def _run_data_annotation_xianshi_zhenwuge_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        return runner._execute_xianshi_zhenwuge_task(ctx, stop_event, payload)

    @register_fanxiu_data_annotation_task_cell(
        "xianshi_langya_rankings",
        "仙市_琅琊榜",
        scheduler_supported=True,
    )
    def _run_data_annotation_xianshi_langya_rankings_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        return runner._execute_xianshi_langya_rankings_task(ctx, stop_event, payload)

    @register_fanxiu_data_annotation_task_cell(
        "daily_xianmeng",
        "仙盟_挑战",
        scheduler_supported=False,
        # This manual task starts from the Xianmeng page prepared by the user.
        # It intentionally performs no framework-level navigation.
    )
    def _run_data_annotation_daily_xianmeng_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        return runner._execute_daily_xianmeng_task(ctx, stop_event, payload)

    @register_fanxiu_data_annotation_task_cell(
        "daily_lundao",
        "论道_座位",
        scheduler_supported=True,
        admission=lambda runner, payload: runner.daily_lundao_admission(payload),
    )
    def _run_data_annotation_daily_lundao_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        runtime = runner._fanxiu_runtime(ctx, stop_event=stop_event)
        current_fact_scene_ids = (
            69, 296, 297, 298, 371, 372, 373, 375, 295,
            329, 301, 302, 303, 304, 391, 52, 53, 54,
        )
        current_scene_id, _score, _frame = runtime.current_scene(current_fact_scene_ids, update=True)
        if current_scene_id not in current_fact_scene_ids:
            yield from runtime.goto_view(34)
        result = yield from runner._execute_daily_lundao_task(ctx, stop_event, payload)
        yield from runtime.goto_view(34)
        return result

    @register_fanxiu_data_annotation_task_cell(
        "daily_daofa",
        "道法争锋",
        scheduler_supported=True,
        admission=lambda runner, payload: runner.daily_daofa_admission(payload),
    )
    def _run_data_annotation_daily_daofa_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        # The DaoFa task owns both its one-click idempotency mark and best-effort
        # cleanup.  A second unconditional goto(34) here used to turn a
        # completed zero-attempt run back into run_status=error when #376 had
        # no graph route to the world.
        return (yield from runner._execute_daily_daofa_task_managed(ctx, stop_event, payload))

    @register_fanxiu_data_annotation_task_cell(
        "daily_xianyuan_duel",
        "仙缘斗法",
        scheduler_supported=True,
        admission=lambda runner, payload: runner.daily_xianyuan_duel_admission(payload),
    )
    def _run_data_annotation_daily_xianyuan_duel_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        runtime = runner._fanxiu_runtime(ctx, stop_event=stop_event)
        yield from runtime.goto_view(34)
        result = yield from runner._execute_daily_xianyuan_duel_task(ctx, stop_event, payload)
        yield from runtime.goto_view(34)
        return result

    @register_fanxiu_data_annotation_task_cell("daily_mojie_raid", "日常_奇袭魔界", scheduler_supported=True)
    def _run_data_annotation_daily_mojie_raid_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        runtime = runner._fanxiu_runtime(ctx, stop_event=stop_event)
        yield from runtime.goto_view(34)
        result = yield from runner._execute_daily_mojie_raid_task(ctx, stop_event, payload)
        yield from runtime.goto_view(34)
        return result

    @register_fanxiu_data_annotation_task_cell("daily_weekly_dungeon", "日常_周本", scheduler_supported=True)
    def _run_data_annotation_daily_weekly_dungeon_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        runtime = runner._fanxiu_runtime(ctx, stop_event=stop_event)
        yield from runtime.goto_view(34)
        result = yield from runner._execute_daily_weekly_dungeon_task(ctx, stop_event, payload)
        yield from runtime.goto_view(34)
        return result

    @register_fanxiu_data_annotation_task_cell("weekly_hanli", "周常_韩立", scheduler_supported=True)
    def _run_data_annotation_weekly_hanli_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        runtime = runner._fanxiu_runtime(ctx, stop_event=stop_event)
        yield from runtime.goto_view(34)
        result = yield from runner._execute_weekly_hanli_task(ctx, stop_event, payload)
        yield from runtime.goto_view(34)
        return result

    @register_fanxiu_data_annotation_task_cell(
        "bubble_weekly_pills",
        "气泡_每周丹药",
        scheduler_supported=True,
    )
    def _run_data_annotation_bubble_weekly_pills_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        # The 37 bubble is an Android top-level overlay, not a game scene.
        # The lifecycle must read this week's claim fact before deciding
        # whether any SDK page needs opening; normalizing the underlying game
        # to #34 here strands claimed-week runs on unrelated pages.
        return (yield from runner._execute_bubble_weekly_task(ctx, stop_event, payload))

    @register_fanxiu_data_annotation_task_cell(
        "take_medicine_batch",
        "服用丹药_批量",
        scheduler_supported=True,
        standard_job=True,
        standard_job_id="take-medicine-batch",
        standard_job_description="手动",
        standard_job_payload={"timeout_seconds": 20.0},
    )
    def _run_data_annotation_take_medicine_batch_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        return (yield from runner._execute_take_medicine_batch_task(ctx, stop_event, payload))

    @register_fanxiu_data_annotation_task_cell(
        "daily_lingquan",
        "日常_灵泉",
        scheduler_supported=True,
        admission=lambda runner, payload: runner.daily_lingquan_admission(payload),
    )
    def _run_data_annotation_daily_lingquan_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        result = yield from runner._execute_daily_lingquan_task(ctx, stop_event, payload)
        runtime = runner._fanxiu_runtime(ctx, stop_event=stop_event)
        yield from runtime.goto_view(34)
        return result

    @register_fanxiu_data_annotation_task_cell(
        "weekly_shengzu",
        "周常_圣祖",
        scheduler_supported=True,
        admission=lambda runner, payload: runner.weekly_shengzu_admission(payload),
    )
    def _run_data_annotation_weekly_shengzu_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        result = yield from runner._execute_weekly_shengzu_task(ctx, stop_event, payload)
        runtime = runner._fanxiu_runtime(ctx, stop_event=stop_event)
        yield from runtime.goto_view(34)
        return result

    @register_fanxiu_data_annotation_task_cell("daily_vip", "日常_vip", scheduler_supported=True)
    def _run_data_annotation_daily_vip_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        runtime = runner._fanxiu_runtime(ctx, stop_event=stop_event)
        yield from runtime.goto_view(34)
        result = yield from runner._execute_daily_vip_task(ctx, stop_event, payload)
        yield from runtime.goto_view(34)
        return result

    @register_fanxiu_data_annotation_task_cell("daily_signin", "日常_签到", scheduler_supported=True)
    def _run_data_annotation_daily_signin_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        runtime = runner._fanxiu_runtime(ctx, stop_event=stop_event)
        # A new attempt never inherits a previous attempt's UI step.  Normalize
        # every start through the generic navigation graph, then run the whole
        # idempotent business operation from its stable entry.
        yield from runtime.goto_view(34)
        # Every successful sign-in branch closes through
        # ``_daily_signin_finish_from_404``.  That helper owns the one bounded
        # departure attempt after the business postcondition is committed.
        # Repeating ``goto_view(34)`` here used to mistake the text-free world
        # transition for an actionable unknown page and run the generic return
        # fallback a second time.
        return (yield from runner._execute_daily_signin_task(ctx, stop_event, payload))

    @register_fanxiu_data_annotation_task_cell("daily_xuanhuang", "日常_玄荒", scheduler_supported=True)
    def _run_data_annotation_daily_xuanhuang_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        runtime = runner._fanxiu_runtime(ctx, stop_event=stop_event)
        yield from runtime.goto_view(34)
        result = yield from runner._execute_daily_xuanhuang_task(ctx, stop_event, payload)
        yield from runtime.goto_view(34)
        return result

    @register_fanxiu_data_annotation_task_cell(
        "daily_dongtian",
        "洞天_领取",
        scheduler_supported=True,
        admission=lambda runner, payload: runner.daily_dongtian_admission(payload),
    )
    def _run_data_annotation_daily_dongtian_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        runtime = runner._fanxiu_runtime(ctx, stop_event=stop_event)
        yield from runtime.goto_view(34)
        result = yield from runner._execute_daily_dongtian_task(ctx, stop_event, payload)
        yield from runtime.goto_view(34)
        return result

    @register_fanxiu_data_annotation_task_cell(
        "daily_dongtian_clear",
        "洞天_行动力",
        scheduler_supported=True,
        admission=lambda runner, payload: runner.daily_dongtian_clear_admission(payload),
    )
    def _run_data_annotation_daily_dongtian_clear_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        runtime = runner._fanxiu_runtime(ctx, stop_event=stop_event)
        yield from runtime.goto_view(34)
        result = yield from runner._execute_daily_dongtian_clear_task(ctx, stop_event, payload)
        yield from runtime.goto_view(34)
        return result

    @register_fanxiu_data_annotation_task_cell(
        "dongtian_seating",
        "洞天_上座",
        scheduler_supported=True,
        standard_job=True,
        standard_job_id="dongtian-seating",
        standard_job_description="动态",
        standard_job_payload={"max_runtime_seconds": 900},
    )
    def _run_data_annotation_dongtian_seating_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        from backend.core.fanxiu.data_annotation.tasks.dongtian_seating_job import (
            execute_dongtian_seating_runtime_job,
        )

        return (yield from execute_dongtian_seating_runtime_job(
            runner,
            ctx,
            stop_event,
            payload,
        ))

    @register_fanxiu_data_annotation_task_cell(
        "daily_lingmai",
        "灵脉_座位",
        scheduler_supported=True,
        admission=lambda runner, payload: runner.daily_lingmai_admission(payload),
    )
    def _run_data_annotation_daily_lingmai_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        runtime = runner._fanxiu_runtime(ctx, stop_event=stop_event)
        yield from runtime.goto_view(34)
        result = yield from runner._execute_daily_lingmai_task(ctx, stop_event, payload)
        yield from runtime.goto_view(34)
        return result

    @register_fanxiu_data_annotation_task_cell(
        "daily_lingmai_clear",
        "灵脉_清体力",
        scheduler_supported=True,
        admission=lambda runner, payload: runner.daily_lingmai_clear_admission(payload),
    )
    def _run_data_annotation_daily_lingmai_clear_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        runtime = runner._fanxiu_runtime(ctx, stop_event=stop_event)
        yield from runtime.goto_view(34)
        result = yield from runner._execute_daily_lingmai_clear_task(ctx, stop_event, payload)
        yield from runtime.goto_view(34)
        return result

    @register_fanxiu_data_annotation_task_cell(
        "daily_dungeon",
        "日常_每日副本",
        scheduler_supported=True,
        standard_job=True,
        standard_job_id="legacy-daily-dungeon",
        standard_job_description="手动",
        standard_job_payload={"max_runs": 6, "max_purchase_uses": 3},
    )
    def _run_data_annotation_daily_dungeon_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        runtime = runner._fanxiu_runtime(ctx, stop_event=stop_event)
        yield from runtime.goto_view(34)
        result = yield from _run_manual_standard_job(
            runner,
            "legacy-daily-dungeon",
            lambda: runner._execute_daily_dungeon_task(ctx, stop_event, payload),
        )
        yield from runtime.goto_view(34)
        return result

    @register_fanxiu_data_annotation_task_cell("daily_assistant", "日常_助手", scheduler_supported=True)
    def _run_data_annotation_daily_assistant_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        runtime = runner._fanxiu_runtime(ctx, stop_event=stop_event)
        yield from runtime.goto_view(34)
        result = yield from runner._execute_daily_assistant_task(ctx, stop_event, payload)
        yield from runtime.goto_view(34)
        return result

    @register_fanxiu_data_annotation_task_cell(
        "lilian_claim",
        "历练_领取",
        scheduler_supported=True,
        standard_job=True,
        standard_job_id="lilian-claim",
        standard_job_description="手动",
    )
    def _run_data_annotation_lilian_claim_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        from backend.core.fanxiu.data_annotation.tasks.lilian_claim import (
            execute_lilian_claim_task,
        )

        runtime = runner._fanxiu_runtime(ctx, stop_event=stop_event)
        yield from runtime.goto_view(34)
        result = yield from _run_manual_standard_job(
            runner,
            "lilian-claim",
            lambda: execute_lilian_claim_task(
                runner,
                ctx,
                payload,
                stop_event,
            ),
        )
        yield from runtime.goto_view(34)
        return result

    @register_fanxiu_data_annotation_task_cell(
        "lilian_event",
        "历练_事件",
        scheduler_supported=True,
        standard_job=True,
        standard_job_id="lilian-event",
        standard_job_description="手动",
    )
    def _run_data_annotation_lilian_event_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        from backend.core.fanxiu.data_annotation.tasks.lilian_event import (
            execute_lilian_event_task,
        )
        from backend.core.fanxiu.data_annotation.popup_guard import (
            FanxiuEmulatorRestartRequired,
        )

        runtime = runner._fanxiu_runtime(
            ctx,
            ctx.get("asset_tree_path") if isinstance(ctx.get("asset_tree_path"), Path) else None,
            stop_event=stop_event,
        )
        # A Job attempt does not persist a previous generator's local state.
        # Normalize through the shared scene graph, then run the whole
        # idempotent business transaction from its stable #34 entry.
        yield from runtime.goto_view(34)
        try:
            result = yield from _run_manual_standard_job(
                runner,
                "lilian-event",
                lambda: execute_lilian_event_task(
                    runner,
                    ctx,
                    payload,
                    stop_event,
                ),
            )
        except (InterruptedError, GeneratorExit, FanxiuEmulatorRestartRequired):
            raise
        except Exception as primary_error:
            try:
                yield from runtime.goto_view(34)
            except (InterruptedError, GeneratorExit, FanxiuEmulatorRestartRequired):
                raise
            except Exception as cleanup_error:
                primary_error.add_note(
                    "历练_事件异常后的返回世界收尾失败："
                    f"{type(cleanup_error).__name__}: {cleanup_error}"
                )
            raise
        yield from runtime.goto_view(34)
        return result

    @register_fanxiu_data_annotation_task_cell(
        "activity_quiz",
        "活动_答题",
        scheduler_supported=True,
        standard_job=True,
        standard_job_id="activity-quiz",
        standard_job_description="手动",
        standard_job_payload={
            "max_runtime_seconds": 240,
            "native_snapshot_max_age_seconds": 2,
            "native_prompt_match_threshold": 82,
            "match_score_threshold": 82,
            "ai_timeout_seconds": 45,
        },
    )
    def _run_data_annotation_activity_quiz_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        # DAILY ONE-SHOT: this callable is intentionally registered as a manual
        # task.  An Agent may submit it only after an explicit user command;
        # questions such as “现在启动？” authorize inspection, not execution.
        # Once submitted, do not run screenshots/doctor probes beside its
        # ten-second countdown.  See docs/domains/fanxiu/jobs/凡修活动答题作业.md.
        from backend.core.fanxiu.data_annotation.tasks.activity_quiz import (
            execute_activity_quiz_task,
        )

        return execute_activity_quiz_task(runner, ctx, payload, stop_event)

    @register_fanxiu_data_annotation_task_cell(
        "activity_quiz_final",
        "活动_答题决赛",
        scheduler_supported=True,
        standard_job=True,
        standard_job_id="activity-quiz-final",
        standard_job_description="手动",
        standard_job_payload={
            "max_runtime_seconds": 900,
            "start_wait_seconds": 180,
            "idle_after_click_seconds": 15,
            "scene_exit_grace_seconds": 8,
            "poll_seconds": 0.12,
            "native_snapshot_max_age_seconds": 1,
            "native_wait_seconds": 1.2,
            "native_prompt_match_threshold": 82,
            "match_score_threshold": 82,
            "ai_timeout_seconds": 45,
            "ai_hint_interval_seconds": 1,
            "ai_hint_max_clicks": 3,
        },
    )
    def _run_data_annotation_activity_quiz_final_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        # LIMITED LIVE EVENT: explicit user authorization is required for every
        # run.  The entry/result flow is not known yet, so this task only waits
        # for read-only scene #61 and runs its question loop; it must not guess
        # navigation or assume that an option keeps the same position.
        from backend.core.fanxiu.data_annotation.tasks.activity_quiz_final import (
            execute_activity_quiz_final_task,
        )

        return _run_manual_standard_job(
            runner,
            "activity-quiz-final",
            lambda: execute_activity_quiz_final_task(runner, ctx, payload, stop_event),
        )

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
        runtime = runner._fanxiu_runtime(ctx, stop_event=stop_event)
        yield from runtime.goto_view(34)
        result = yield from runner._execute_xianqiao_trial_task(ctx, stop_event, payload)
        yield from runtime.goto_view(34)
        return result

    @register_fanxiu_data_annotation_task_cell("mail_selective_claim", "邮件_选择性领取", scheduler_supported=True)
    def _run_data_annotation_mail_selective_claim_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        runtime = runner._fanxiu_runtime(ctx, stop_event=stop_event)
        yield from runtime.goto_view(34)
        is_scan = bool(
            payload.get("observe_only")
            or payload.get("scan_only")
            or payload.get("full_scan")
            or str(payload.get("scan_mode") or "").strip().lower()
            in {"full", "full_scan", "observe", "observe_only", "refresh", "sync"}
        )
        business_message = ""
        runner._mail_selective_claim_terminal_message = ""
        if is_scan:
            result = yield from runner._execute_mail_legacy_scan_task(ctx, stop_event, payload)
        else:
            result = yield from runner._execute_mail_selective_claim_task(ctx, stop_event, payload)
            business_message = str(
                getattr(runner, "_mail_selective_claim_terminal_message", "") or ""
            ).strip()
        yield from runtime.goto_view(34)
        if not is_scan:
            if result != "success":
                raise RuntimeError(
                    "邮件_选择性领取：业务流程未返回 success，拒绝推进次日调度；"
                    f"result={result!r}"
                )
            if not business_message:
                raise RuntimeError(
                    "邮件_选择性领取：业务完成但缺少领取/删除终态摘要，拒绝推进次日调度"
                )
            business_message = runner._finish_mail_selective_claim_schedule(
                payload,
                business_message,
            )
            runner._mail_selective_claim_terminal_message = business_message
            return {"result": "success", "message": business_message}
        return result

    @register_fanxiu_data_annotation_task_cell(
        "mail_claim_law",
        "邮件_领法则",
        scheduler_supported=True,
        standard_job=True,
        standard_job_id="mail-claim-law",
        standard_job_description="动态",
        standard_job_payload={"max_runtime_seconds": 10800},
    )
    def _run_data_annotation_mail_claim_law_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        return (yield from runner._execute_mail_claim_law_task(ctx, stop_event, payload))

    @register_fanxiu_data_annotation_task_cell(
        "prayer_daily_resource",
        "祈愿_每日资源",
        scheduler_supported=True,
        # #449 is an existing visually similar store asset that can win the
        # scene score after the free prayer card disappears.  The task verifies
        # the Prayer OCR identity before treating it as a resumable state.
    )
    def _run_data_annotation_prayer_daily_resource_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        return (yield from runner._execute_prayer_daily_resource_task(ctx, stop_event, payload))

    @register_fanxiu_data_annotation_task_cell(
        "resource_rank_daily_free_gift",
        "资源榜_每日免费礼包",
        scheduler_supported=False,
    )
    def _run_data_annotation_resource_rank_daily_free_gift_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        return (
            yield from runner._execute_resource_rank_daily_free_gift_task(
                ctx,
                stop_event,
                payload,
            )
        )

    @register_fanxiu_data_annotation_task_cell(
        "dandao_task_rewards",
        "丹道_任务奖励",
        scheduler_supported=False,
    )
    def _run_data_annotation_dandao_task_rewards_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        internal_payload = dict(payload)
        internal_payload.pop("__scheduler_task_id", None)
        internal_payload["manage_schedule"] = False
        return (
            yield from runner._execute_dandao_task_rewards_task(
                ctx,
                stop_event,
                internal_payload,
            )
        )

    @register_fanxiu_data_annotation_task_cell(
        "yuanding_sansheng_daily_gift",
        "缘定三生_每日礼包",
        scheduler_supported=False,
    )
    def _run_data_annotation_yuanding_sansheng_daily_gift_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        internal_payload = dict(payload)
        internal_payload.pop("__scheduler_task_id", None)
        internal_payload["manage_schedule"] = False
        return (yield from runner._execute_yuanding_sansheng_daily_gift_task(
            ctx, stop_event, internal_payload
        ))

    @register_fanxiu_data_annotation_task_cell("xianfu_visit_partner", "仙府_寻访仙侣", scheduler_supported=True)
    def _run_data_annotation_xianfu_visit_partner_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        runtime = runner._fanxiu_runtime(ctx, stop_event=stop_event)
        yield from runtime.goto_view(34)
        # 业务函数的所有正常终态都经专属链路回到世界；包装器若再做一次
        # 世界场景导航，会重复整帧识别/规划并拉长短作业。异常终态原本也
        # 不会执行这里的尾部清理，因此只保留起点归一化。
        return (yield from runner._execute_xianfu_visit_partner_task(ctx, stop_event, payload))

    @register_fanxiu_data_annotation_task_cell("xianfu_learn_skill", "仙府_领悟绝技", scheduler_supported=True)
    def _run_data_annotation_xianfu_learn_skill_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        runtime = runner._fanxiu_runtime(ctx, stop_event=stop_event)
        yield from runtime.goto_view(34)
        result = yield from runner._execute_xianfu_learn_skill_task(ctx, stop_event, payload)
        yield from runtime.goto_view(34)
        return result

    @register_fanxiu_data_annotation_task_cell(
        "penglai_xianzang_config",
        "蓬莱仙藏_配置",
        scheduler_supported=True,
    )
    def _run_data_annotation_penglai_xianzang_config_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        from backend.core.fanxiu.data_annotation.tasks.penglai_xianzang_jobs import (
            execute_xianzang_config_job,
        )

        # The dedicated job owns both its resumable #448 transaction and its
        # final return to #34.  A generic wrapper-level goto would strand an
        # incomplete choice page before the job can idempotently resume it.
        return execute_xianzang_config_job(runner, ctx, payload, stop_event)

    @register_fanxiu_data_annotation_task_cell(
        "penglai_xianzang_lottery",
        "蓬莱仙藏_抽奖",
        scheduler_supported=True,
    )
    def _run_data_annotation_penglai_xianzang_lottery_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        from backend.core.fanxiu.data_annotation.tasks.penglai_xianzang_jobs import (
            execute_xianzang_lottery_job,
        )

        runtime = runner._fanxiu_runtime(ctx, stop_event=stop_event)
        yield from runtime.goto_view(34)
        result = execute_xianzang_lottery_job(runner, ctx, payload, stop_event)
        yield from runtime.goto_view(34)
        return result

    @register_fanxiu_data_annotation_task_cell(
        "kunlun_secret_config",
        "昆仑秘藏_配置",
        scheduler_supported=True,
    )
    def _run_data_annotation_kunlun_secret_config_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        from backend.core.fanxiu.data_annotation.tasks.kunlun_secret_jobs import (
            execute_kunlun_config_job,
        )

        return execute_kunlun_config_job(runner, ctx, payload, stop_event)

    @register_fanxiu_data_annotation_task_cell(
        "kunlun_secret_lottery",
        "昆仑秘藏_抽奖",
        scheduler_supported=True,
    )
    def _run_data_annotation_kunlun_secret_lottery_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        from backend.core.fanxiu.data_annotation.tasks.kunlun_secret_jobs import (
            execute_kunlun_lottery_job,
        )

        return execute_kunlun_lottery_job(runner, ctx, payload, stop_event)

    @register_fanxiu_data_annotation_task_cell(
        "lingxiao_xianhui",
        "灵霄仙会",
        scheduler_supported=True,
        standard_job=True,
        standard_job_id="lingxiao-xianhui",
        standard_job_description="动态",
        standard_job_payload={"max_runtime_seconds": 180},
    )
    def _run_data_annotation_lingxiao_xianhui_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        from backend.core.fanxiu.data_annotation.tasks.lingxiao_xianhui import (
            execute_lingxiao_xianhui_job,
        )

        return (yield from execute_lingxiao_xianhui_job(runner, ctx, payload, stop_event))

    @register_fanxiu_data_annotation_task_cell(
        "wanbao_zhenbao",
        "万宝臻宝",
        scheduler_supported=True,
        standard_job=True,
        standard_job_id="wanbao-zhenbao",
        standard_job_description="手动",
        standard_job_payload={"max_runtime_seconds": 600},
    )
    def _run_data_annotation_wanbao_zhenbao_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        from backend.core.fanxiu.data_annotation.tasks.wanbao_zhenbao_job import (
            WANBAO_ZHENBAO_TASK_ID,
            execute_wanbao_zhenbao_job,
        )

        return (
            yield from _run_manual_standard_job(
                runner,
                WANBAO_ZHENBAO_TASK_ID,
                lambda: execute_wanbao_zhenbao_job(
                    runner,
                    ctx,
                    payload,
                    stop_event,
                ),
            )
        )

    @register_fanxiu_data_annotation_task_cell(
        "xutian_palace_rankings",
        "虚天殿_榜单数据",
        scheduler_supported=False,
    )
    def _run_data_annotation_xutian_palace_rankings_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        from backend.core.fanxiu.data_annotation.tasks.xutian_palace_rankings import (
            execute_xutian_palace_rankings_job,
        )

        runtime = runner._fanxiu_runtime(ctx, stop_event=stop_event)
        yield from runtime.goto_view(34)
        result = yield from execute_xutian_palace_rankings_job(runner, ctx, payload, stop_event)
        yield from runtime.goto_view(34)
        return result

    @register_fanxiu_data_annotation_task_cell(
        "xutian_palace_native_auto",
        "虚天殿_自动挑战",
        scheduler_supported=False,
    )
    def _run_data_annotation_xutian_palace_native_auto_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        from backend.core.fanxiu.data_annotation.tasks.xutian_native_auto import (
            execute_xutian_native_auto_job,
        )

        return (
            yield from execute_xutian_native_auto_job(
                runner,
                ctx,
                payload,
                stop_event,
            )
        )

    @register_fanxiu_data_annotation_task_cell(
        "yunmeng_trial_auto_challenge",
        "云梦试剑_自动挑战",
        scheduler_supported=False,
    )
    def _run_data_annotation_yunmeng_trial_auto_challenge_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        """Run the verified native Yunmeng flow as an AI-callable subtask."""

        from backend.core.fanxiu.data_annotation.tasks.yunmeng_native_auto import (
            execute_yunmeng_native_auto_job,
        )

        return (
            yield from execute_yunmeng_native_auto_job(
                runner,
                ctx,
                payload,
                stop_event,
            )
        )

    @register_fanxiu_data_annotation_task_cell(
        "magic_invasion_explore",
        "魔道入侵_探查",
        scheduler_supported=False,
    )
    def _run_data_annotation_magic_invasion_explore_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        from backend.core.fanxiu.data_annotation.tasks.magic_invasion import (
            execute_magic_invasion_explore_job,
        )

        return (
            yield from execute_magic_invasion_explore_job(
                runner,
                ctx,
                payload,
                stop_event,
                manage_schedule=False,
            )
        )

    @register_fanxiu_data_annotation_task_cell(
        "ranking_lifecycle",
        "玩法榜",
        scheduler_supported=True,
        standard_job=True,
        standard_job_id="ranking-lifecycle",
        standard_job_description="动态",
        standard_job_payload={"max_runtime_seconds": 10800},
    )
    def _run_data_annotation_ranking_lifecycle_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        from backend.core.fanxiu.data_annotation.tasks.ranking_lifecycle import (
            execute_ranking_lifecycle_job,
        )

        return (
            yield from execute_ranking_lifecycle_job(
                runner,
                ctx,
                payload,
                stop_event,
            )
        )

    @register_fanxiu_data_annotation_task_cell(
        "resource_ranking",
        "资源榜",
        scheduler_supported=True,
        standard_job=True,
        standard_job_id="resource-ranking",
        standard_job_description="动态",
        standard_job_payload={"max_runtime_seconds": 10800},
    )
    def _run_data_annotation_resource_ranking_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        from backend.core.fanxiu.data_annotation.tasks.ranking_lifecycle import (
            execute_resource_ranking_job,
        )

        return (yield from execute_resource_ranking_job(
            runner, ctx, payload, stop_event
        ))

    @register_fanxiu_data_annotation_task_cell(
        "yunmeng_tail",
        "云梦_收尾",
        scheduler_supported=False,
    )
    def _run_data_annotation_yunmeng_tail_task_cell(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        from backend.core.fanxiu.data_annotation.tasks.yunmeng_tail import (
            execute_yunmeng_tail_job,
        )

        return (yield from execute_yunmeng_tail_job(runner, ctx, payload, stop_event))

