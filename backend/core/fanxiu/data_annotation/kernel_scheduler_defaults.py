from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
from typing import Any

from backend.core.fanxiu.activity.ranking_lifecycle import (
    RANKING_LIFECYCLE_TASK_ID,
    RANKING_LIFECYCLE_TASK_TYPE,
    RESOURCE_RANKING_TASK_ID,
    RESOURCE_RANKING_TASK_TYPE,
    RETIRED_GAMEPLAY_RANKING_TASK_IDS,
    RETIRED_GAMEPLAY_RANKING_TASK_TYPES,
    RETIRED_RESOURCE_RANKING_TASK_IDS,
    RETIRED_RESOURCE_RANKING_TASK_TYPES,
)
from backend.core.fanxiu.activity.theme_collection import (
    THEME_COLLECTION_INTERNALIZED_TASK_IDS,
    THEME_COLLECTION_INTERNALIZED_TASK_TYPES,
    THEME_COLLECTION_LABEL,
    THEME_COLLECTION_TASK_ID,
    THEME_COLLECTION_TASK_TYPE,
)
from backend.core.fanxiu.data_annotation.arena_schedule import (
    next_daofa_trigger_at,
    next_xianyuan_duel_trigger_at,
)
from backend.core.fanxiu.data_annotation.tasks.tianjige_forum_quiz import (
    next_tianjige_forum_quiz_trigger_at,
)
from backend.core.fanxiu.data_annotation.tasks.daozu_challenge import (
    next_daozu_challenge_time,
)
from backend.core.fanxiu.data_annotation.tasks.resource_daily_contract import (
    RESOURCE_DAILY_INTERNALIZED_JOBS_KEY,
    RESOURCE_DAILY_LABEL,
    RESOURCE_DAILY_PAYLOAD_SCHEMA_KEY,
    RESOURCE_DAILY_PROGRESS_KEY,
    RESOURCE_DAILY_RETIRED_TASK_IDS,
    RESOURCE_DAILY_RETIRED_TASK_TYPES,
    RESOURCE_DAILY_SCHEMA_VERSION,
    RESOURCE_DAILY_STAGES,
    RESOURCE_DAILY_TASK_ID,
    RESOURCE_DAILY_TASK_TYPE,
    RESOURCE_DAILY_TRIGGER_DESCRIPTION,
    match_resource_daily_stage,
    next_resource_daily_time,
    resource_daily_completion,
)
_CONSOLIDATED_ARENA_SCHEDULER_IDS = {
    "sunday-daofa": "daily-daofa",
    "sunday-xianyuan-duel": "daily-xianyuan-duel",
}

_RETIRED_SCHEDULER_TASK_IDS = {
    # 日常_助手的一键执行已经包含供奉，不再保留独立调度实例。
    "legacy-daily-gongfeng",
    # 三段式气泡链路已合并为一个闭环 Job。
    "bubble-weekly-restart",
    "bubble-claim-pills",
    "bubble-hide",
    # 玩法榜专用子任务只允许通过普通行为树 Cell 调试；Scheduler
    # 持久实例统一归属于 ranking-lifecycle。
    *RETIRED_GAMEPLAY_RANKING_TASK_IDS,
    *RETIRED_RESOURCE_RANKING_TASK_IDS,
    # 主题集旧一级任务统一归属唯一的 theme-collection。
    *THEME_COLLECTION_INTERNALIZED_TASK_IDS,
    # 六项资源日常统一归属唯一的 resource-auto-use（资源_每日处理）。
    *RESOURCE_DAILY_RETIRED_TASK_IDS,
}

_RETIRED_SCHEDULER_TASK_TYPES = {
    "bubble_weekly_restart",
    "bubble_claim_pills",
    "bubble_hide",
    *RETIRED_GAMEPLAY_RANKING_TASK_TYPES,
    *RETIRED_RESOURCE_RANKING_TASK_TYPES,
    *THEME_COLLECTION_INTERNALIZED_TASK_TYPES,
    *RESOURCE_DAILY_RETIRED_TASK_TYPES,
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
    items = deepcopy(raw)
    migration_changed = False
    bootstrap_time = _next_initial_time(now or datetime.now(), ("00:10",))

    def migrate_family(
        *,
        canonical_id: str,
        canonical_type: str,
        label: str,
        retired_ids: frozenset[str],
        retired_types: frozenset[str],
        clean_payload_keys: frozenset[str] = frozenset(),
    ) -> bool:
        canonical = next(
            (
                item for item in items
                if isinstance(item, dict) and str(item.get("id") or "") == canonical_id
            ),
            None,
        )
        retired = [
            item for item in items
            if isinstance(item, dict)
            and (
                str(item.get("id") or "") in retired_ids
                or str(item.get("task_type") or "") in retired_types
            )
        ]
        changed = False
        if retired:
            candidates = [item.get("next_time") for item in retired]
            candidates.append(canonical.get("next_time") if canonical is not None else bootstrap_time)
            valid: list[str] = []
            for value in candidates:
                text = str(value or "").strip()
                try:
                    datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    continue
                valid.append(text)
            earliest = min(valid) if valid else None
            if canonical is None:
                canonical = {
                    "id": canonical_id,
                    "task_type": canonical_type,
                    "label": label,
                    "template_id": canonical_type,
                    "template_label": label,
                    "trigger_description": "动态",
                    "next_time": earliest,
                    "payload": {"max_execution_seconds": 10800},
                }
                items.append(canonical)
            elif earliest is not None and canonical.get("next_time") != earliest:
                canonical["next_time"] = earliest
            changed = True
        if canonical is not None and clean_payload_keys:
            payload = canonical.get("payload")
            if isinstance(payload, dict):
                cleaned = {key: value for key, value in payload.items() if key not in clean_payload_keys}
                if cleaned != payload:
                    canonical["payload"] = cleaned
                    changed = True
        if canonical is not None:
            canonical_shape = {
                "task_type": canonical_type,
                "label": label,
                "template_id": canonical_type,
                "template_label": label,
            }
            for key, value in canonical_shape.items():
                if canonical.get(key) != value:
                    canonical[key] = value
                    changed = True
        return changed

    def migrate_resource_daily_family() -> bool:
        """Absorb the six retired first-level Jobs into one canonical daily Job.

        This is deliberately *not* ``migrate_family``: the canonical trigger is
        a fixed next 00:00, never the earliest retired ``next_time``, and the
        retired runs are converted into durable ``aggregate_progress`` evidence
        instead of being added as new triggers.  The one-time
        ``resource_daily_schema_version`` marker makes the work idempotent and
        keeps later maintenance from overwriting a running ``next_time``.
        """

        canonical = next(
            (
                item for item in items
                if isinstance(item, dict) and str(item.get("id") or "") == RESOURCE_DAILY_TASK_ID
            ),
            None,
        )
        retired = [
            item for item in items
            if isinstance(item, dict) and match_resource_daily_stage(item) is not None
        ]
        if canonical is None and not retired:
            # No canonical and no retired instances: the default catalogue owns
            # the sole canonical Job, so an empty legacy raw needs no creation.
            return False

        changed = False
        if canonical is None:
            canonical = {
                "id": RESOURCE_DAILY_TASK_ID,
                "task_type": RESOURCE_DAILY_TASK_TYPE,
                "label": RESOURCE_DAILY_LABEL,
                "template_id": RESOURCE_DAILY_TASK_TYPE,
                "template_label": RESOURCE_DAILY_LABEL,
                "legacy_name": RESOURCE_DAILY_LABEL,
                "trigger_description": RESOURCE_DAILY_TRIGGER_DESCRIPTION,
                # Initial value only; the conversion below rewrites it.
                "next_time": next_resource_daily_time(now or datetime.now()),
                "last_run_at": None,
                "last_result": "",
                "last_message": "",
                "error_retry_delay_seconds": 0,
                "payload": {"max_rounds": 3, "max_execution_seconds": 10800},
                "scheduler_meta": None,
            }
            items.append(canonical)
            changed = True

        canonical_shape = {
            "task_type": RESOURCE_DAILY_TASK_TYPE,
            "label": RESOURCE_DAILY_LABEL,
            "template_id": RESOURCE_DAILY_TASK_TYPE,
            "template_label": RESOURCE_DAILY_LABEL,
            "legacy_name": RESOURCE_DAILY_LABEL,
            "trigger_description": RESOURCE_DAILY_TRIGGER_DESCRIPTION,
        }
        for key, value in canonical_shape.items():
            if canonical.get(key) != value:
                canonical[key] = value
                changed = True

        payload = canonical.get("payload")
        if not isinstance(payload, dict):
            payload = {}
            canonical["payload"] = payload
            changed = True
        if payload.get(RESOURCE_DAILY_PAYLOAD_SCHEMA_KEY) != RESOURCE_DAILY_SCHEMA_VERSION:
            # ``items`` only shallow-copies each record, so replace the payload
            # before writing to avoid mutating the caller's raw snapshot.
            payload = deepcopy(payload)
            canonical["payload"] = payload
            payload.setdefault("max_execution_seconds", 10800)
            internalized = payload.get(RESOURCE_DAILY_INTERNALIZED_JOBS_KEY)
            internalized = dict(internalized) if isinstance(internalized, dict) else {}
            progress = payload.get(RESOURCE_DAILY_PROGRESS_KEY)
            progress = dict(progress) if isinstance(progress, dict) else {}
            for stage in RESOURCE_DAILY_STAGES:
                matches = [
                    item for item in retired
                    if match_resource_daily_stage(item) == stage
                ]
                source = next(
                    (item for item in matches if str(item.get("id") or "") == stage.task_id),
                    matches[0] if matches else None,
                )
                if source is None:
                    continue
                # Full copy preserves every historical and configuration field.
                internalized[stage.task_id] = deepcopy(source)
                completion = resource_daily_completion(stage, source)
                if completion is not None:
                    cycle_key, record = completion
                    cycle_records = progress.get(cycle_key)
                    cycle_records = dict(cycle_records) if isinstance(cycle_records, dict) else {}
                    cycle_records[stage.task_id] = record
                    progress[cycle_key] = cycle_records
            payload[RESOURCE_DAILY_INTERNALIZED_JOBS_KEY] = internalized
            payload[RESOURCE_DAILY_PROGRESS_KEY] = progress
            payload[RESOURCE_DAILY_PAYLOAD_SCHEMA_KEY] = RESOURCE_DAILY_SCHEMA_VERSION
            canonical["error_retry_delay_seconds"] = 600
            canonical["next_time"] = next_resource_daily_time(now or datetime.now())
            changed = True
        return changed

    migration_changed |= migrate_family(
        canonical_id=RANKING_LIFECYCLE_TASK_ID,
        canonical_type=RANKING_LIFECYCLE_TASK_TYPE,
        label="玩法榜",
        retired_ids=RETIRED_GAMEPLAY_RANKING_TASK_IDS,
        retired_types=RETIRED_GAMEPLAY_RANKING_TASK_TYPES,
        clean_payload_keys=frozenset({"magic_invasion_progress"}),
    )
    migration_changed |= migrate_family(
        canonical_id=RESOURCE_RANKING_TASK_ID,
        canonical_type=RESOURCE_RANKING_TASK_TYPE,
        label="资源榜",
        retired_ids=RETIRED_RESOURCE_RANKING_TASK_IDS,
        retired_types=RETIRED_RESOURCE_RANKING_TASK_TYPES,
    )

    # The single canonical theme-collection Job subsumes every legacy
    # first-level theme Task.  migrate_family folds their earliest next_time
    # into the canonical record, and the retired-id/type filter below then
    # removes the legacy instances so a persisted catalogue cannot readmit them.
    migration_changed |= migrate_family(
        canonical_id=THEME_COLLECTION_TASK_ID,
        canonical_type=THEME_COLLECTION_TASK_TYPE,
        label=THEME_COLLECTION_LABEL,
        retired_ids=frozenset(THEME_COLLECTION_INTERNALIZED_TASK_IDS),
        retired_types=frozenset(THEME_COLLECTION_INTERNALIZED_TASK_TYPES),
    )
    migration_changed |= migrate_resource_daily_family()
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


def default_kernel_scheduler_tasks(
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
    from backend.core.fanxiu.data_annotation.dongtian_seating_schedule import next_dongtian_seating_at

    def job(
        task_id: str,
        task_type: str,
        label: str,
        *,
        description: str = "",
        initial_times: tuple[str, ...] = (),
        initial_weekdays: tuple[int, ...] = tuple(range(7)),
        initial_next_time: str | None = None,
        source: str = "kernel_scheduler",
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
            # with the absolute time selected from their own context context.
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
            "玩法榜",
            description="动态",
            initial_times=("00:10",),
            dispatch_level=0,
            error_retry_delay_seconds=600,
            payload={"max_execution_seconds": 10800},
        ),
        job(
            "resource-ranking",
            "resource_ranking",
            "资源榜",
            description="动态",
            initial_times=("00:10",),
            error_retry_delay_seconds=600,
            payload={"max_execution_seconds": 10800},
        ),
        job(
            "daozu-challenge",
            "daozu_challenge",
            "道祖_挑战",
            description="每日",
            initial_next_time=next_daozu_challenge_time(current),
            payload={"max_execution_seconds": 1800},
        ),
        job(
            "daily-task-rewards",
            "daily_task_rewards",
            "日常_任务奖励",
            description="首领完成后",
            initial_next_time=None,
            # Three activity domains took over ten minutes on the real client.
            payload={"max_execution_seconds": 1800},
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
                "unbounded_execution": True,
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
            payload={"unbounded_execution": True},
        ),
        job(
            "gift-code-weekly",
            "weekly_gift_code",
            "每周_礼包码",
            description="每周",
            initial_times=("00:05",),
            initial_weekdays=(1,),
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
            initial_times=("00:05",),
            initial_weekdays=(0,),
            payload={"max_source_level": 8},
        ),
        job(
            "storage-bag-operation",
            "storage_bag_operation",
            "储物袋_操作",
            description="每日",
            initial_times=("00:00",),
            payload={"max_rounds": 3},
        ),
        job(
            "resource-auto-use",
            "resource_auto_use",
            "资源_每日处理",
            description="每日",
            initial_times=("00:00",),
            error_retry_delay_seconds=600,
            payload={"max_rounds": 3, "max_execution_seconds": 10800},
        ),
        job(
            "xianyuan-auto-gift",
            "xianyuan_auto_gift",
            "仙缘_自动送礼",
            description="手动",
            error_retry_delay_seconds=0,
        ),
        job("weekly-hanli", "weekly_hanli", "周常_韩立", description="每周", initial_times=("00:05",), initial_weekdays=(0,)),
        job(
            "weekly-wanxian",
            "weekly_wanxian",
            "周常_万仙",
            description="每周",
            initial_times=("05:00",),
            initial_weekdays=(6,),
        ),
        job(
            "bubble-weekly-pills",
            "bubble_weekly_pills",
            "气泡_每周丹药",
            description="每周",
            initial_times=("00:00",),
            initial_weekdays=(0,),
            dispatch_level=1,
            error_retry_delay_seconds=600,
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
            # Seven purchased rounds plus behavior-tree/OCR selection normally take
            # about 11-12 minutes.  The generic 600s task default used to let
            # the client time out while the healthy Cell kept running to a
            # business success, leaving Scheduler last_result=error.
            payload={
                "retry_seconds": 60,
                "purchase_max_price": 300,
                "max_execution_seconds": 1800,
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
        job("mail-selective-claim", "mail_selective_claim", "邮件_选择性领取", description="每日", initial_times=("00:00",), payload={"max_execution_seconds": 10800}),
        job(
            "lingta-challenge",
            "lingta_challenge",
            "灵塔_挑战",
            description="每日",
            initial_times=("07:00",),
            error_retry_delay_seconds=600,
            payload={
                "max_execution_seconds": 5400,
                "monitor_timeout_seconds": 3600,
                "monitor_poll_seconds": 2,
                "max_scrolls": 30,
            },
        ),
        job("daily-boss", "daily_boss", "日常_首领", description="每日", initial_times=("05:00",), payload={"max_execution_seconds": 1800}),
        job("daily-experience", "daily_experience", "日常_经验", description="首领完成后1分钟", payload={"max_execution_seconds": 1800}),
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
            payload={"max_execution_seconds": 600},
        ),
        job("xianfu-learn-skill", "xianfu_learn_skill", "仙府_领悟绝技", description="动态", initial_times=("05:00",)),
        job("legacy-daily-mozu", "daily_mozu", "日常_魔祖", description="每日", initial_times=("12:30",), dispatch_level=1, error_retry_delay_seconds=0),
        job("legacy-daily-lingquan", "daily_lingquan", "日常_灵泉", description="每日", initial_times=("20:30",), dispatch_level=1, error_retry_delay_seconds=0, payload={"max_execution_seconds": 1800}),
        job("daily-zhenxie", "daily_zhenxie", "日常_镇邪", description="每日", initial_times=("21:00",), dispatch_level=1, error_retry_delay_seconds=0),
        job("legacy-daily-assistant", "daily_assistant", "日常_助手", description="每日", initial_times=("00:00", "05:00", "12:00", "18:00"), payload={"max_execution_seconds": 1800}),
        job("legacy-daily-signup", "daily_signup", "日常_报名", description="每日", initial_times=("05:00",), payload={"max_execution_seconds": 900}),
        job(
            "moyu-signup",
            "moyu_signup",
            "魔狱_报名",
            description="每日",
            initial_times=("05:00", "14:00"),
            payload={"max_execution_seconds": 900},
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
                "max_execution_seconds": 1800,
            },
        ),
        job("xianqiao-trial", "xianqiao_trial", "仙窍_试炼", description="每日", initial_times=("05:00",), payload={"target_daily_purchases": 0, "max_challenges": 10, "battle_timeout": 360, "max_execution_seconds": 3600}),
        job("resource-xinghai", "resource_xinghai", "资源_星海", description="每日", initial_times=("00:00",)),
        job("daily-xuanhuang", "daily_xuanhuang", "日常_玄荒", description="每日", initial_times=("05:00",), payload={"recommend_timeout_seconds": 60, "battle_timeout_seconds": 120, "max_execution_seconds": 10800}),
        job("daily-redpacket", "daily_redpacket", "日常_红包", description="动态", initial_times=("05:00",), payload={"interval_seconds": 43200}),
        job("dongtian-seating", "dongtian_seating", "洞天_上座", description="每日", initial_next_time=next_dongtian_seating_at(current, include_current=True).strftime("%Y-%m-%d %H:%M:%S"), payload={"max_execution_seconds": 900}),
        job("legacy-daily-dongtian", "daily_dongtian", "洞天_领取", description="每日", initial_times=("14:00",)),
        job(
            "legacy-daily-dongtian-clear",
            "daily_dongtian_clear",
            "洞天_行动力",
            description="每日",
            initial_times=("21:00",),
            dispatch_order=20,
            error_retry_delay_seconds=60,
            payload={"daily_start_time": "21:00", "daily_end_time": "22:00"},
        ),
        job("legacy-daily-lingmai-clear", "daily_lingmai_clear", "灵脉_清体力", description="每日", initial_times=("21:00",), dispatch_order=10),
        job(
            "legacy-daily-mojie-raid",
            "daily_mojie_raid",
            "日常_奇袭魔界",
            description="每日",
            initial_times=("13:00",),
            initial_weekdays=(0,),
            dispatch_order=30,
        ),
        job("legacy-daily-baiye", "daily_baiye", "日常_拜谒", description="每日", initial_times=("05:00",), payload={"args": ["魔道"], "max_execution_seconds": 900}),
        job("legacy-daily-green-bottle-baiye", "daily_green_bottle_baiye", "日常_绿瓶拜谒", description="每日", initial_times=("05:00",), payload={"max_execution_seconds": 900}),
        job("xianshi-weekly-resources", "xianshi_weekly_resources", "仙市_每周资源", description="每周", initial_times=("00:00", "05:00"), initial_weekdays=(0,)),
        job("daily-weekly-dungeon", "daily_weekly_dungeon", "日常_周本", description="每周", initial_times=("05:00",), initial_weekdays=(0,), payload={"battle_return_world_timeout": 600}),
        job("weekly-activity", "weekly_activity", "周常_活跃度", description="每周", initial_times=("00:00",), initial_weekdays=(3,), payload={"weekly_activity_threshold": 2400}),
        job("legacy-daily-xianyuan", "daily_xianyuan", "日常_挑战仙缘", description="每日", initial_times=("05:00",), payload={"max_execution_seconds": 900}),
        job("legacy-daily-activity", "daily_activity", "日常_活跃度", description="每日", initial_times=("07:00",), error_retry_delay_seconds=3600, payload={"fallback_seconds": 3600}),
        job(
            "activity-daily-list-sync",
            "activity_daily_list_sync",
            "活动_每日清单同步",
            description="每日",
            initial_times=("00:00",),
            dispatch_level=0,
            error_retry_delay_seconds=3600,
        ),
    ]

    # STANDARD JOB CONTRACT: a registered standard Job is automatically part
    # of the Scheduler checklist.  This prevents a task implementation and its
    # visible instance from becoming two manually maintained, drifting lists.
    from backend.core.fanxiu.data_annotation.default_jobs import (
        register_fanxiu_default_jobs,
    )
    from backend.core.fanxiu.data_annotation.jobs import (
        list_fanxiu_data_annotation_task_cell_definitions,
    )

    register_fanxiu_default_jobs()
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
