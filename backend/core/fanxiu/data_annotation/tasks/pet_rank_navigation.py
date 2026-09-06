"""Verified peak-pet navigation callbacks for the development ranking loop."""

from backend.core.fanxiu.data_annotation.tasks.peakrace_stage import (
    enter_current_peakrace_lingchong_stage, require_peakrace_stage_page,
)
from backend.core.fanxiu.data_annotation.tasks.pet_aptitude_navigation import enter_first_growing_pet_aptitude
from backend.core.fanxiu.instrumentation.activity_rank_runtime import read_activity_rank_runtime_snapshot, prepare_activity_rank_runtime
from backend.core.fanxiu.instrumentation.pet_aptitude import read_pet_aptitude_runtime


def load_peak_pet_rank_rows(context, *, activity_id: int, missing_ranks, max_scrolls: int = 30):
    scene = int((yield from context.wait_scene([702], wait=15)))
    if scene != 702:
        raise RuntimeError("当前不是灵兽阶段榜单")
    require_peakrace_stage_page(activity_id, tab_index=0)
    required = set(missing_ranks)
    scrolls = 0
    while True:
        rank = read_activity_rank_runtime_snapshot(activity_id)
        if rank.get("error_code") == "process_cache_miss":
            recovery = prepare_activity_rank_runtime([activity_id], allow_discovery=False)
            if not recovery.get("complete"):
                raise RuntimeError(f"榜单缓存恢复失败：{recovery.get('error_code')} {recovery.get('reason')}")
            rank = read_activity_rank_runtime_snapshot(activity_id)
        if not rank.get("complete"):
            raise RuntimeError(f"榜单Runtime不可读：{rank.get('error_code')} {rank.get('reason')}")
        if required.issubset({row["rank"] for row in rank["rankings"]}):
            return rank
        if scrolls >= max_scrolls or any(r > rank["rank_list_size"] for r in required):
            break
        # The repeated gold row background can look unchanged after real
        # movement. Loaded rank identities, rather than image similarity,
        # determine completion; scrolling itself uses the shared defaults.
        for _ in range(min(4, max_scrolls - scrolls)):
            context.drag_shape_content(context.shape(702, "榜单列表"), direction="down")
            yield from context.wait_action_settle(1)
            scrolls += 1
    return rank


def refresh_peak_pet_rank(context, *, activity_id: int, missing_ranks):
    scene = int((yield from context.wait_scene([545, 483, 702, 701], wait=15)))
    if scene == 702 and missing_ranks:
        return (yield from load_peak_pet_rank_rows(context, activity_id=activity_id, missing_ranks=missing_ranks))
    if scene == 545:
        scene = int((yield from context.wait_click_then_scene(545, "关闭背景", [483],
            settle_seconds=1, timeout=15, max_clicks=1)))
    if scene == 483:
        scene = int((yield from context.wait_click_then_scene(483, "返回", [702],
            settle_seconds=1, timeout=15, max_clicks=1)))
    if scene == 702:
        require_peakrace_stage_page(activity_id, tab_index=0)
        scene = int((yield from context.wait_click_then_scene(702, "返回", [701],
            settle_seconds=1, timeout=15, max_clicks=1)))
    if scene != 701:
        raise RuntimeError("未返回天道巅峰入口")
    stage = yield from enter_current_peakrace_lingchong_stage(context)
    if stage["activity_id"] != activity_id:
        raise RuntimeError("灵兽阶段已改变")
    return (yield from load_peak_pet_rank_rows(context, activity_id=activity_id, missing_ranks=missing_ranks))


def enter_peak_pet_rank_resources(context, *, activity_id: int, pet_id: int):
    """Pure navigation after a score baseline; never perform quick swallow."""
    scene = int((yield from context.wait_scene([702], wait=15)))
    if scene != 702:
        raise RuntimeError("需要从灵兽阶段榜单进入资源页")
    require_peakrace_stage_page(activity_id, tab_index=0)
    scene = int((yield from context.wait_click_then_scene(702, "前往吞噬", [483],
        settle_seconds=1, timeout=15, max_clicks=1)))
    if scene != 483:
        raise RuntimeError("未进入灵兽页")
    if pet_id != 7101:
        raise NotImplementedError("尚未教授其它成长灵兽的选择")
    lines = context.ocr_fragments_in_shapes(483, ["第一个成长灵兽"], frame_data_url=context.cur_frame(update=True))
    if not any("玄霜冰凤" in row.get("text", "") for row in lines):
        raise RuntimeError("首个成长灵兽与预期不符")
    yield from enter_first_growing_pet_aptitude(context)
    if int((yield from context.wait_scene([545], wait=15))) != 545:
        raise RuntimeError("未进入资质页")
    read_pet_aptitude_runtime(expected_pet_id=pet_id)
    return {"scene_id": 545, "pet_id": pet_id}
