"""巅峰赛阶段组件：由人工进入并确认阶段后调用，暂不接自动调度。"""

from typing import Any
from datetime import datetime

from backend.core.fanxiu.instrumentation.activity_gift import read_activity_gift_runtime_snapshot
from backend.core.fanxiu.data_annotation.tasks.resource_rank_daily_gift import validate_one_free_gift_increment
from backend.core.fanxiu.instrumentation.activity_rank_page import read_activity_rank_page_snapshot


PEAKRACE_STAGE_SCENES = (702, 703, 704, 705)
PEAKRACE_STAGE_TABS = {"榜单": (702, 0), "奖励": (705, 1), "任务": (704, 2), "礼包": (703, 3)}


def current_peakrace_lingchong_stage(*, now: datetime | None = None) -> dict[str, Any]:
    """按实时活动窗口选择灵兽阶段，不依赖六天或轮次顺序。"""
    from backend.core.fanxiu.activity.runtime_schedule import read_fanxiu_activity_runtime_schedule

    current = now or datetime.now().astimezone()
    if current.tzinfo is None:
        raise ValueError("阶段选择需要带时区时间")
    schedule = read_fanxiu_activity_runtime_schedule()
    if not schedule.get("complete"):
        raise RuntimeError("活动日程未完整加载")
    rows = []
    for family in schedule.get("peakraceSchedules", []):
        for stage in family.get("stages", []):
            for occurrence in stage.get("runtimeOccurrences", []):
                if (occurrence.get("identityComplete") and occurrence.get("state") == 2
                        and occurrence["startTime"] <= current.timestamp() * 1000 < occurrence["endTime"]):
                    rows.append({"outer_activity_id": family["outerActivityId"],
                                 "activity_id": stage["activityId"], "base_id": stage["baseId"],
                                 "start_time_ms": occurrence["startTime"],
                                 "end_time_ms": occurrence["endTime"],
                                 "world_level": occurrence.get("avgWorldLevel")})
    if len(rows) != 1:
        raise RuntimeError("当前巅峰赛阶段不唯一或尚未开放")
    if rows[0]["base_id"] != 42900:
        raise NotImplementedError("当前巅峰赛资源阶段尚未实现")
    return rows[0]


def enter_current_peakrace_lingchong_stage(context: Any):
    """从天道巅峰主页首行进入今天的灵兽阶段，再回读真实页面 ID。"""
    yield from context.wait_scene([701], wait=10)
    stage = current_peakrace_lingchong_stage()
    lines = context.ocr_fragments_in_shapes(701, ["首行活动名称"],
                                           frame_data_url=context.cur_frame(update=True))
    if not any("灵兽巅峰" in r.get("text", "") or "灵宠巅峰" in r.get("text", "") for r in lines):
        raise RuntimeError("首行未显示当前灵兽阶段，保留现场")
    yield from context.wait_click(701, "首行前往", timeout=10)
    yield from context.wait_scene([702], wait=10)
    require_peakrace_stage_page(stage["activity_id"], tab_index=0)
    return stage


def require_peakrace_stage_page(activity_id: int, *, tab_index: int | None = None) -> dict[str, Any]:
    """Read the active page after scene readiness, never infer it from rank cache."""
    page = read_activity_rank_page_snapshot()
    if not page.get("complete") or page.get("activity_id") != activity_id:
        raise RuntimeError(f"当前活动榜页面归属不符：{page}")
    if tab_index is not None and page.get("tab_index") != tab_index:
        raise RuntimeError("当前活动榜页签不符")
    return page


def open_peakrace_stage_tab(context: Any, *, activity_id: int, tab: str):
    """切换已打开灵兽巅峰阶段的业务页签；不提供竞猜动作。"""
    if tab not in PEAKRACE_STAGE_TABS:
        raise ValueError("尚未支持该巅峰赛页签")
    scene = yield from context.wait_scene(list(PEAKRACE_STAGE_SCENES), wait=10)
    page = require_peakrace_stage_page(activity_id)
    target_scene, target_index = PEAKRACE_STAGE_TABS[tab]
    if page["tab_index"] != target_index:
        yield from context.wait_click(int(scene), tab, timeout=10)
    yield from context.wait_scene([target_scene], wait=10)
    require_peakrace_stage_page(activity_id, tab_index=target_index)
    return target_scene


def claim_peakrace_free_gift_on_current_page(context: Any, *, activity_id: int):
    """在已确认归属的 #703 礼包页领取一项；只支持单个剩余免费项。

    不导航、不滚动、不离场。未验收的多免费项刷新流程留待后续补全。
    """
    yield from context.wait_scene([703], wait=10)
    require_peakrace_stage_page(activity_id, tab_index=3)
    before = read_activity_gift_runtime_snapshot([activity_id])
    if not before.get("ok") or not before.get("complete"):
        raise RuntimeError("当前礼包有效性尚未确认")
    free = [r for r in before.get("items", []) if r.get("is_free") and r.get("claimable")]
    if not free:
        return {"claimed_ids": [], "status": "no_remaining_free_candidates"}
    if len(free) != 1 or free[0].get("remaining_times") != 1:
        raise RuntimeError("多免费项流程尚未实现")
    yield from context.wait_click(703, "免费", timeout=10)
    yield from context.wait_action_settle(1.5)
    yield from context.wait_scene([703], wait=10)
    require_peakrace_stage_page(activity_id, tab_index=3)
    after = read_activity_gift_runtime_snapshot([activity_id])
    if not after.get("complete"):
        raise RuntimeError("领取后的礼包状态不可读，请先回读再重试")
    claimed_id = validate_one_free_gift_increment(before, after)
    return {"claimed_ids": [claimed_id], "status": "claimed"}


def check_peakrace_lingchong_task_rewards(context: Any, *, activity_id: int):
    """进入任务页并检查可领状态；有可领项时保留现场供研发。"""
    from backend.core.fanxiu.activity.lingchong_jingwu import read_lingchong_task_milestones

    yield from open_peakrace_stage_tab(context, activity_id=activity_id, tab="任务")
    snapshot = read_lingchong_task_milestones(activity_id)
    rewards = snapshot.get("reward_state", {})
    if not snapshot.get("complete") or not rewards.get("complete"):
        raise RuntimeError("灵兽阶段任务档次尚未完整加载")
    claimable = rewards.get("authorized_claim_task_ids", [])
    return {"status": "claim_action_not_validated" if claimable else "no_claimable_rewards",
            "complete": not bool(claimable), "claimed_ids": [],
            "claimable_task_ids": claimable, "tasks": snapshot}


def run_peakrace_lingchong_research_flow(context: Any):
    """手动研发入口：进入当前阶段、免费礼包、任务检查、榜单与奖励观察。

    无 Scheduler 注册，无资源消耗或竞猜点击。未验收的领奖分支显式留空。
    """
    from backend.core.fanxiu.data_annotation.tasks.activity_menu_navigation import open_loaded_activity_menu_item
    from backend.core.fanxiu.activity.peakrace_stage import read_peakrace_stage_snapshot, preview_peakrace_support

    scene = int((yield from context.wait_scene([34, 701, *PEAKRACE_STAGE_SCENES], wait=15)))
    stage = current_peakrace_lingchong_stage()
    if scene == 34:
        yield from open_loaded_activity_menu_item(
            context, stage["outer_activity_id"], kind="world_left", source_scene_id=34,
            ocr_shape_names=["左侧菜单"], expected_scene_ids=[701], target_gui_name="天道巅峰",
        )
        scene = 701
    if scene == 701:
        stage = yield from enter_current_peakrace_lingchong_stage(context)
    else:
        require_peakrace_stage_page(stage["activity_id"])
    activity_id = stage["activity_id"]
    yield from open_peakrace_stage_tab(context, activity_id=activity_id, tab="礼包")
    gifts = yield from claim_peakrace_free_gift_on_current_page(context, activity_id=activity_id)
    tasks = yield from check_peakrace_lingchong_task_rewards(context, activity_id=activity_id)
    if not tasks["complete"]:
        return {"status": "partial", "activity": stage, "gifts": gifts, "tasks": tasks,
                "deferred": ["task_reward_claim_action"], "current_scene": 704,
                "automatic_execution_enabled": False}
    yield from open_peakrace_stage_tab(context, activity_id=activity_id, tab="奖励")
    yield from open_peakrace_stage_tab(context, activity_id=activity_id, tab="榜单")
    snapshot = read_peakrace_stage_snapshot(
        activity_id, reward_activity_id=activity_id,
        event_date=datetime.fromtimestamp(stage["start_time_ms"] / 1000).astimezone().date().isoformat(),
        world_level=stage["world_level"],
    )
    return {"status": "partial", "activity": stage, "gifts": gifts, "tasks": tasks,
            "observation": snapshot,
            "support_preview": preview_peakrace_support(snapshot, now=datetime.now().astimezone()),
            "deferred": ["resource_usage", "task_reward_claim_action", "stage_rank_reward_claim", "guess_execution"],
            "automatic_execution_enabled": False}
