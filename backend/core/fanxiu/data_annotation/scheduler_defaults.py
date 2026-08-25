from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from backend.core.fanxiu.data_annotation.arena_schedule import (
    next_daofa_trigger_at,
    next_xianyuan_duel_trigger_at,
)
from backend.core.fanxiu.data_annotation.tasks.tianjige_forum_quiz import (
    next_tianjige_forum_quiz_trigger_at,
)
from backend.core.fanxiu.data_annotation.tasks.kunlun_secret_jobs import (
    next_kunlun_config_time,
    next_kunlun_lottery_time,
)
from backend.core.fanxiu.data_annotation.tasks.penglai_xianzang_jobs import (
    next_xianzang_config_time,
    next_xianzang_lottery_time,
)
from backend.core.fanxiu.data_annotation.tasks.daozu_challenge import (
    next_daozu_challenge_time,
)
from backend.core.fanxiu.data_annotation.tasks.daily_task_rewards import (
    next_daily_task_reward_time,
)
from backend.core.fanxiu.data_annotation.tasks.dandao_task_rewards import (
    next_dandao_task_reward_time,
)


_CONSOLIDATED_ARENA_SCHEDULER_IDS = {
    "sunday-daofa": "daily-daofa",
    "sunday-xianyuan-duel": "daily-xianyuan-duel",
}

_RETIRED_SCHEDULER_TASK_IDS = {
    # 日常_助手的一键执行已经包含供奉，不再保留独立调度实例。
    "legacy-daily-gongfeng",
    # 资源_自动使用聚合已接管储物袋；旧实例必须显式迁移删除。
    "storage-bag-operation",
    # 三段式气泡链路已合并为一个闭环 Job。
    "bubble-weekly-restart",
    "bubble-claim-pills",
    "bubble-hide",
    # 魔道探查已成为统一榜单生命周期的 19:00 adapter。
    "magic-invasion-explore",
}

_RETIRED_SCHEDULER_TASK_TYPES = {
    "bubble_weekly_restart",
    "bubble_claim_pills",
    "bubble_hide",
    "magic_invasion_explore",
}

# Every Scheduler-supported task cell currently has one visible standard Job.
# Keep the exception set explicit so the contract test rejects future hidden
# Scheduler primitives unless their invisibility is deliberately justified.
INTERNAL_SCHEDULER_PRIMITIVE_TASK_TYPES = frozenset()
LOGIN_GAME_SCHEDULER_TASK_ID = "login-game"


def consolidate_arena_scheduler_instances(
    raw: Any,
    *,
    now: datetime | None = None,
) -> tuple[Any, bool]:
    """Fold obsolete instances and remove explicitly retired Scheduler Jobs."""

    if not isinstance(raw, list):
        return raw, False
    items = [dict(item) if isinstance(item, dict) else item for item in raw]
    migration_changed = False
    ranking_bootstrap_time = _next_initial_time(now or datetime.now(), ("00:30",))
    legacy_magic = next(
        (
            item
            for item in items
            if isinstance(item, dict)
            and str(item.get("id") or "") == "magic-invasion-explore"
        ),
        None,
    )
    ranking_lifecycle = next(
        (
            item
            for item in items
            if isinstance(item, dict)
            and str(item.get("id") or "") == "ranking-lifecycle"
        ),
        None,
    )
    if legacy_magic is not None and ranking_lifecycle is None:
        legacy_magic.update(
            {
                "id": "ranking-lifecycle",
                "task_type": "ranking_lifecycle",
                "label": "日程_榜单系统更新",
                "template_id": "ranking_lifecycle",
                "template_label": "日程_榜单系统更新",
                "trigger_description": "动态",
            }
        )
        payload = dict(legacy_magic.get("payload") or {})
        payload.pop("target_batches", None)
        payload.pop("batch_size", None)
        payload["max_runtime_seconds"] = 10800
        legacy_magic["payload"] = payload
        trigger_candidates = [
            str(value)
            for value in (legacy_magic.get("next_time"), ranking_bootstrap_time)
            if str(value or "").strip()
        ]
        legacy_magic["next_time"] = min(trigger_candidates) if trigger_candidates else None
        ranking_lifecycle = legacy_magic
        migration_changed = True
    elif legacy_magic is not None and ranking_lifecycle is not None:
        legacy_payload = dict(legacy_magic.get("payload") or {})
        ranking_payload = dict(ranking_lifecycle.get("payload") or {})
        if (
            "magic_invasion_progress" in legacy_payload
            and "magic_invasion_progress" not in ranking_payload
        ):
            ranking_payload["magic_invasion_progress"] = legacy_payload[
                "magic_invasion_progress"
            ]
            ranking_lifecycle["payload"] = ranking_payload
        candidates = [
            str(value)
            for value in (
                ranking_lifecycle.get("next_time"),
                legacy_magic.get("next_time"),
                ranking_bootstrap_time,
            )
            if str(value or "").strip()
        ]
        ranking_lifecycle["next_time"] = min(candidates) if candidates else None
        migration_changed = True
    before_retirement = len(items)
    items = [
        item
        for item in items
        if not (
            isinstance(item, dict)
            and (
                str(item.get("id") or "") in _RETIRED_SCHEDULER_TASK_IDS
                or str(item.get("task_type") or "") in _RETIRED_SCHEDULER_TASK_TYPES
            )
        )
    ]
    changed = migration_changed or len(items) != before_retirement
    for obsolete_id, canonical_id in _CONSOLIDATED_ARENA_SCHEDULER_IDS.items():
        obsolete_index = next(
            (
                index
                for index, item in enumerate(items)
                if isinstance(item, dict) and str(item.get("id") or "") == obsolete_id
            ),
            None,
        )
        if obsolete_index is None:
            continue
        canonical_index = next(
            (
                index
                for index, item in enumerate(items)
                if isinstance(item, dict) and str(item.get("id") or "") == canonical_id
            ),
            None,
        )
        obsolete = items[obsolete_index]
        if canonical_index is None:
            obsolete["id"] = canonical_id
        else:
            canonical = items[canonical_index]
            trigger_candidates = [
                str(value)
                for value in (canonical.get("next_time"), obsolete.get("next_time"))
                if str(value or "").strip()
            ]
            canonical["next_time"] = min(trigger_candidates) if trigger_candidates else None
            if str(obsolete.get("last_run_at") or "") > str(canonical.get("last_run_at") or ""):
                for key in ("last_run_at", "last_result", "last_message", "finished_at"):
                    canonical[key] = obsolete.get(key)
            if str(obsolete.get("last_result") or "") == "running":
                for key in (
                    "last_result",
                    "last_message",
                    "attempt_id",
                    "attempt_original_trigger",
                    "attempt_kernel_generation",
                    "attempt_kernel_idle_since",
                    "queued_at",
                    "started_at",
                ):
                    canonical[key] = obsolete.get(key)
            items.pop(obsolete_index)
        changed = True
    return items, changed


def _next_initial_time(
    now: datetime,
    times: tuple[str, ...],
    weekdays: tuple[int, ...] = tuple(range(7)),
) -> str | None:
    candidates: list[datetime] = []
    for day_offset in range(8):
        day = now.date() + timedelta(days=day_offset)
        if day.weekday() not in weekdays:
            continue
        for value in times:
            clock = datetime.strptime(value, "%H:%M").time()
            candidate = datetime.combine(day, clock)
            if candidate > now:
                candidates.append(candidate)
    return min(candidates).strftime("%Y-%m-%d %H:%M:%S") if candidates else None


def default_data_annotation_scheduler_tasks(
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Return unified jobs whose only executable schedule fact is ``next_time``.

    Architecture invariant:
    - ``trigger_description`` is short, display-only copy such as ``每日`` or
      ``动态``.  It must never be parsed into a scheduling mechanism.
    - ``initial_times`` and ``initial_weekdays`` only seed ``next_time`` when a
      default job is first created.  They are not recurring scheduler rules.
    - After that, every job owns its business-specific next-time algorithm and
      writes a new absolute ``next_time`` on each successful scheduled run.

    Keeping these facts separate is what lets conditional jobs express rules
    such as cooldowns, reward exhaustion and special first runs without adding
    another schedule type to the framework.
    """

    current = now or datetime.now()

    def job(
        task_id: str,
        task_type: str,
        label: str,
        *,
        description: str = "",
        initial_times: tuple[str, ...] = (),
        initial_weekdays: tuple[int, ...] = tuple(range(7)),
        initial_next_time: str | None = None,
        source: str = "data_annotation_runtime",
        interruptible: bool = True,
        dispatch_level: int = 0,
        dispatch_order: int = 0,
        error_retry_delay_seconds: int = 600,
        payload: dict[str, Any] | None = None,
        system_task: bool = False,
    ) -> dict[str, Any]:
        # This is the standard job record template, not a catalogue of trigger
        # strategies.  Do not add fields such as schedule_kind / cron / daily
        # and make the Scheduler interpret them.  Business recurrence belongs
        # to the job; the Scheduler only compares the resulting ``next_time``.
        return {
            "id": task_id,
            "task_type": task_type,
            "label": label,
            "template_id": task_type,
            "template_label": label,
            "template_source": "system" if system_task else "preset",
            # Human-facing hint only.  Its wording must not affect execution.
            "trigger_description": description,
            "source": source,
            "legacy_name": label,
            "interruptible": interruptible,
            "dispatch_level": dispatch_level,
            "dispatch_order": dispatch_order,
            # Bootstrap value only.  Persisted jobs subsequently replace it
            # with the absolute time selected from their own runtime context.
            "next_time": (
                initial_next_time
                if initial_next_time is not None
                else _next_initial_time(
                    current,
                    initial_times,
                    initial_weekdays,
                ) if initial_times else None
            ),
            "last_run_at": None,
            "last_result": "",
            "last_message": "",
            "error_retry_delay_seconds": error_retry_delay_seconds,
            "payload": payload or {},
            "scheduler_meta": None,
            **({"system_task": True} if system_task else {}),
        }

    tasks = [
        job(
            "ranking-lifecycle",
            "ranking_lifecycle",
            "日程_榜单系统更新",
            description="动态",
            initial_times=("00:30",),
            error_retry_delay_seconds=600,
            payload={"max_runtime_seconds": 10800},
        ),
        job(
            "daozu-challenge",
            "daozu_challenge",
            "道祖_挑战",
            description="每日",
            initial_next_time=next_daozu_challenge_time(current),
            payload={"max_runtime_seconds": 1800},
        ),
        job(
            "daily-task-rewards",
            "daily_task_rewards",
            "日常_任务奖励",
            description="每日",
            initial_next_time=next_daily_task_reward_time(current),
        ),
        job(
            "system-maintenance-recovery",
            "maintenance_recovery",
            "系统_维护恢复",
            description="手动",
            source="system",
            interruptible=False,
            dispatch_level=5,
            error_retry_delay_seconds=1800,
            payload={
                "system_task": True,
                "unbounded_runtime": True,
                "startup_timeout_seconds": 300,
            },
            system_task=True,
        ),
        job(
            LOGIN_GAME_SCHEDULER_TASK_ID,
            "login_game",
            "登录",
            description="手动",
            dispatch_level=5,
            error_retry_delay_seconds=0,
            payload={"unbounded_runtime": True},
        ),
        job(
            "gift-code-weekly",
            "weekly_gift_code",
            "每周_礼包码",
            description="每周",
            initial_times=("23:30",),
            initial_weekdays=(0,),
        ),
        job(
            "tianjige-forum-quiz",
            "tianjige_forum_quiz",
            "天机阁_有奖竞答",
            description="每周",
            initial_next_time=next_tianjige_forum_quiz_trigger_at(current).strftime("%Y-%m-%d %H:%M:%S"),
            error_retry_delay_seconds=60,
            payload={
                "poll_seconds": 10,
                "page_timeout_seconds": 15,
                "submit_timeout_seconds": 15,
                "minimum_answer_score": 2,
                "submit_enabled": True,
            },
        ),
        job("go-settings", "go_scene", "到设置页 #49", description="手动", payload={"target_scene_id": 49}),
        job("hide-floating-window", "hide_floating_window", "隐藏浮动窗", description="手动"),
        job("jianling-cuiling", "jianling_cuiling", "剑灵_淬灵", description="手动"),
        job(
            "beast-spirit-update",
            "beast_spirit_update",
            "兽魂更新",
            description="每周",
            initial_times=("00:30",),
            initial_weekdays=(1,),
            payload={"max_source_level": 8},
        ),
        job(
            "resource-auto-use",
            "resource_auto_use",
            "资源_自动使用",
            description="手动",
            error_retry_delay_seconds=0,
            payload={"max_rounds": 3},
        ),
        job(
            "wanxiang-baoge-six-yuan",
            "wanxiang_baoge_six_yuan",
            "万象宝阁_六元代币宝匣",
            description="手动",
            error_retry_delay_seconds=0,
            payload={"max_refreshes": 100},
        ),
        job(
            "xianyuan-auto-gift",
            "xianyuan_auto_gift",
            "仙缘_自动送礼",
            description="手动",
            error_retry_delay_seconds=0,
        ),
        job(
            "xianyan-rewards",
            "xianyan_rewards",
            "仙宴_获得奖励",
            description="手动",
            payload={"max_rounds": 100, "settle_seconds": 1.0, "wait_timeout": 15.0},
        ),
        job("weekly-hanli", "weekly_hanli", "周常_韩立", description="每周", initial_times=("05:00",), initial_weekdays=(0,)),
        job(
            "bubble-weekly-pills",
            "bubble_weekly_pills",
            "气泡_每周丹药",
            description="每周",
            initial_times=("00:10",),
            initial_weekdays=(0,),
            dispatch_level=1,
            error_retry_delay_seconds=0,
            payload={"minimum_completed_rewards": 3},
        ),
        job(
            "daily-lundao-seat",
            "daily_lundao",
            "论道_座位",
            description="动态",
            initial_times=("15:30",),
            # Scene-local transient evidence has its own bounded retry.  A
            # failed Job must not form a one-second whole-task restart storm.
            error_retry_delay_seconds=60,
            payload={"daily_start_time": "15:30", "daily_end_time": "22:00"},
        ),
        job(
            "daily-xianyuan-duel",
            "daily_xianyuan_duel",
            "仙缘斗法",
            description="动态",
            initial_next_time=next_xianyuan_duel_trigger_at(current).strftime("%Y-%m-%d %H:%M:%S"),
            error_retry_delay_seconds=60,
            # Seven purchased rounds plus Runtime/OCR selection normally take
            # about 11-12 minutes.  The generic 600s task default used to let
            # the client time out while the healthy Cell kept running to a
            # business success, leaving Scheduler last_result=error.
            payload={
                "retry_seconds": 60,
                "purchase_max_price": 300,
                "max_runtime_seconds": 1800,
            },
        ),
        job(
            "weekly-shengzu",
            "weekly_shengzu",
            "周常_圣祖",
            description="每周",
            initial_times=("20:00",),
            initial_weekdays=(6,),
            dispatch_level=1,
            error_retry_delay_seconds=0,
        ),
        job(
            "daily-daofa",
            "daily_daofa",
            "道法争锋",
            description="动态",
            initial_next_time=next_daofa_trigger_at(current).strftime("%Y-%m-%d %H:%M:%S"),
            error_retry_delay_seconds=60,
            payload={"retry_seconds": 60, "no_target_retry_seconds": 3600},
        ),
        job(
            "daily-lingmai-seat",
            "daily_lingmai",
            "灵脉_座位",
            description="动态",
            initial_times=("17:30",),
            payload={"daily_start_time": "17:30", "daily_end_time": "22:00", "lingmai_no_target_retry_seconds": 1800},
        ),
        job("mail-selective-claim", "mail_selective_claim", "邮件_选择性领取", description="每日", initial_times=("00:00",), payload={"max_runtime_seconds": 10800}),
        job("prayer-daily-resource", "prayer_daily_resource", "祈愿_每日资源", description="每日", initial_times=("00:00",), error_retry_delay_seconds=600),
        job(
            "resource-rank-daily-free-gift",
            "resource_rank_daily_free_gift",
            "资源榜_每日免费礼包",
            description="每日",
            initial_times=("05:10",),
            error_retry_delay_seconds=600,
            payload={"max_runtime_seconds": 600},
        ),
        job(
            "dandao-task-rewards",
            "dandao_task_rewards",
            "丹道_任务奖励",
            description="每日",
            initial_next_time=next_dandao_task_reward_time(current),
            error_retry_delay_seconds=600,
            payload={"max_runtime_seconds": 600, "max_claims": 20},
        ),
        job(
            "lingta-challenge",
            "lingta_challenge",
            "灵塔_挑战",
            description="每日",
            initial_times=("07:00",),
            error_retry_delay_seconds=600,
            payload={
                "max_runtime_seconds": 5400,
                "monitor_timeout_seconds": 3600,
                "monitor_poll_seconds": 2,
                "max_scrolls": 30,
            },
        ),
        job("yuanding-sansheng-daily-gift", "yuanding_sansheng_daily_gift", "缘定三生_每日礼包", description="每日", initial_times=("05:00",), error_retry_delay_seconds=600),
        job("daily-boss", "daily_boss", "日常_首领", description="每日", initial_times=("05:00",), payload={"max_runtime_seconds": 1800}),
        job("daily-experience", "daily_experience", "日常_经验", description="手动", payload={"max_runtime_seconds": 1800}),
        job("legacy-daily-youli", "daily_youli", "日常_游历", description="手动"),
        job("legacy-daily-shuangxiu", "daily_shuangxiu", "日常_双修", description="手动"),
        job(
            "legacy-daily-dungeon",
            "daily_dungeon",
            "日常_每日副本",
            description="手动",
            payload={"max_runs": 6, "max_purchase_uses": 3},
        ),
        job(
            "xianfu-visit-partner",
            "xianfu_visit_partner",
            "仙府_寻访仙侣",
            description="动态",
            initial_times=("05:00",),
            payload={"max_runtime_seconds": 600},
        ),
        job("xianfu-learn-skill", "xianfu_learn_skill", "仙府_领悟绝技", description="动态", initial_times=("05:00",)),
        job("legacy-daily-mozu", "daily_mozu", "日常_魔祖", description="每日", initial_times=("12:30",), dispatch_level=1, error_retry_delay_seconds=0),
        job("legacy-daily-lingquan", "daily_lingquan", "日常_灵泉", description="每日", initial_times=("20:30",), dispatch_level=1, error_retry_delay_seconds=0, payload={"max_runtime_seconds": 1800}),
        job("daily-zhenxie", "daily_zhenxie", "日常_镇邪", description="每日", initial_times=("21:00",), dispatch_level=1, error_retry_delay_seconds=0),
        job("legacy-daily-assistant", "daily_assistant", "日常_助手", description="每日", initial_times=("00:00", "05:00", "12:00", "18:00")),
        job("legacy-daily-signup", "daily_signup", "日常_报名", description="每日", initial_times=("05:00",)),
        job(
            "moyu-signup",
            "moyu_signup",
            "魔狱_报名",
            description="每日",
            initial_times=("05:00", "14:00"),
        ),
        job(
            "moyu-challenge",
            "moyu_challenge",
            "魔狱_挑战",
            description="每日",
            initial_times=("11:59", "17:59"),
            error_retry_delay_seconds=60,
            payload={
                "activity_entry_timeout_seconds": 60,
                "entry_timeout_seconds": 12,
                "battle_timeout_seconds": 1200,
                "reward_view_timeout_seconds": 30,
                "max_runtime_seconds": 1800,
            },
        ),
        job("xianqiao-trial", "xianqiao_trial", "仙窍_试炼", description="每日", initial_times=("05:00",), payload={"target_daily_purchases": 0, "max_challenges": 10, "battle_timeout": 360, "max_runtime_seconds": 3600}),
        job("legacy-daily-vip", "daily_vip", "日常_vip", description="每日", initial_times=("00:00",)),
        job("daily-signin", "daily_signin", "日常_签到", description="每日", initial_times=("00:00",)),
        job("daily-xuanhuang", "daily_xuanhuang", "日常_玄荒", description="每日", initial_times=("05:00",), payload={"recommend_timeout_seconds": 60, "battle_timeout_seconds": 300, "max_runtime_seconds": 3600}),
        job("daily-redpacket", "daily_redpacket", "日常_红包", description="动态", initial_times=("05:00",), payload={"interval_seconds": 43200}),
        job("legacy-daily-dongtian", "daily_dongtian", "洞天_领取", description="每日", initial_times=("14:00",)),
        job(
            "legacy-daily-dongtian-clear",
            "daily_dongtian_clear",
            "洞天_行动力",
            description="每日",
            initial_times=("21:30",),
            dispatch_order=20,
            error_retry_delay_seconds=60,
            payload={"daily_start_time": "21:30", "daily_end_time": "22:00"},
        ),
        job("legacy-daily-lingmai-clear", "daily_lingmai_clear", "灵脉_清体力", description="每日", initial_times=("21:30",), dispatch_order=10),
        job(
            "legacy-daily-mojie-raid",
            "daily_mojie_raid",
            "日常_奇袭魔界",
            description="每日",
            initial_times=("10:00",),
            initial_weekdays=(0,),
            dispatch_order=30,
        ),
        job("legacy-daily-baiye", "daily_baiye", "日常_拜谒", description="每日", initial_times=("05:00",), payload={"args": ["魔道"]}),
        job("legacy-daily-green-bottle-baiye", "daily_green_bottle_baiye", "日常_绿瓶拜谒", description="每日", initial_times=("05:00",)),
        job("legacy-daily-xianshi", "daily_xianshi", "仙市_秘藏阁", description="每日", initial_times=("05:00",)),
        job("xianshi-weekly-resources", "xianshi_weekly_resources", "仙市_每周资源", description="每周", initial_times=("00:00", "05:00"), initial_weekdays=(0,)),
        job(
            "xianshi-zhenwuge",
            "xianshi_zhenwuge",
            "仙市_真悟阁",
            description="每周",
            initial_times=("00:10",),
            initial_weekdays=(1,),
            dispatch_order=10,
            error_retry_delay_seconds=600,
        ),
        job(
            "xianshi-langya-rankings",
            "xianshi_langya_rankings",
            "仙市_琅琊榜",
            description="每周",
            initial_times=("00:10",),
            initial_weekdays=(1,),
            dispatch_order=20,
            error_retry_delay_seconds=600,
        ),
        job("daily-weekly-dungeon", "daily_weekly_dungeon", "日常_周本", description="每周", initial_times=("05:00",), initial_weekdays=(0,), payload={"battle_return_world_timeout": 600}),
        job("weekly-activity", "weekly_activity", "周常_活跃度", description="每周", initial_times=("00:00",), initial_weekdays=(3,), payload={"weekly_activity_threshold": 2400}),
        job(
            "legacy-daily-xianmeng",
            "daily_xianmeng",
            "仙盟_挑战",
            description="动态",
            payload={
                "max_runtime_seconds": 7200,
                "schedule_tail_from_daily_activity_list": True,
                "daily_end_time": "22:00",
                "error_retry_after_daily_end": "none",
            },
        ),
        job("legacy-daily-xianyuan", "daily_xianyuan", "日常_挑战仙缘", description="每日", initial_times=("05:00",)),
        job("legacy-daily-activity", "daily_activity", "日常_活跃度", description="每日", initial_times=("07:00",), error_retry_delay_seconds=3600, payload={"fallback_seconds": 3600}),
        job(
            "activity-daily-list-sync",
            "activity_daily_list_sync",
            "活动_每日清单同步",
            description="每日",
            initial_times=("00:20",),
            error_retry_delay_seconds=3600,
        ),
        job(
            "penglai-xianzang-config",
            "penglai_xianzang_config",
            "蓬莱仙藏_配置",
            description="动态",
            initial_next_time=next_xianzang_config_time(current),
            payload={"max_runtime_seconds": 1800},
        ),
        job(
            "penglai-xianzang-lottery",
            "penglai_xianzang_lottery",
            "蓬莱仙藏_抽奖",
            description="动态",
            initial_next_time=next_xianzang_lottery_time(current),
            payload={"max_runtime_seconds": 1800},
        ),
        job(
            "kunlun-secret-config",
            "kunlun_secret_config",
            "昆仑秘藏_配置",
            description="动态",
            initial_next_time=next_kunlun_config_time(current),
            payload={"max_runtime_seconds": 1800},
        ),
        job(
            "kunlun-secret-lottery",
            "kunlun_secret_lottery",
            "昆仑秘藏_抽奖",
            description="动态",
            initial_next_time=next_kunlun_lottery_time(current),
            payload={"max_runtime_seconds": 1800},
        ),
    ]

    # STANDARD JOB CONTRACT: a registered standard Job is automatically part
    # of the Scheduler checklist.  This prevents a task implementation and its
    # visible instance from becoming two manually maintained, drifting lists.
    from backend.core.fanxiu.data_annotation.default_jobs import (
        register_fanxiu_data_annotation_default_runtime_jobs,
    )
    from backend.core.fanxiu.data_annotation.jobs import (
        list_fanxiu_data_annotation_task_cell_definitions,
    )

    register_fanxiu_data_annotation_default_runtime_jobs()
    definitions = list_fanxiu_data_annotation_task_cell_definitions()
    existing_types = {str(item.get("task_type") or "") for item in tasks}
    for definition in definitions:
        if (
            not definition.scheduler_supported
            or not definition.standard_job
            or definition.task_type in existing_types
        ):
            continue
        tasks.append(
            job(
                definition.standard_job_id or definition.task_type.replace("_", "-"),
                definition.task_type,
                definition.label,
                description=definition.standard_job_description,
                interruptible=definition.interruptible,
                payload=definition.standard_job_payload,
            )
        )
        existing_types.add(definition.task_type)

    return tasks
