"""Peak-pet GUI components for initial planning and individual resource batches."""

from backend.core.fanxiu.data_annotation.tasks.peakrace_stage import (
    enter_current_peakrace_lingchong_stage, require_peakrace_stage_page,
)
from backend.core.fanxiu.data_annotation.tasks.pet_aptitude_navigation import enter_first_growing_pet_aptitude
from backend.core.fanxiu.instrumentation.activity_rank_runtime import (
    read_activity_rank_runtime_snapshot, read_activity_rank_self_snapshot,
    prepare_activity_rank_runtime,
)


def load_peak_pet_rank_rows(context, *, activity_id: int, missing_ranks, max_scrolls: int = 30):
    """Load only missing ranks, retaining observed pages replaced by pagination.

    The merge belongs to this call only. Activity, actor and self score must
    remain identical; each source page retains its capture time for review.
    """
    scene = int((yield from context.wait_scene([702], wait=15)))
    if scene != 702:
        raise RuntimeError("当前不是灵兽阶段榜单")
    require_peakrace_stage_page(activity_id, tab_index=0)
    required = set(missing_ranks)
    if any(isinstance(r, bool) or not isinstance(r, int) or r < 1 for r in required):
        raise ValueError("缺失名次必须为正整数")
    merged = {}
    pages = []
    identity = None
    scrolls = 0
    reentries = 0
    while True:
        rank = read_activity_rank_runtime_snapshot(activity_id)
        if rank.get("error_code") == "process_cache_miss":
            recovery = prepare_activity_rank_runtime([activity_id], allow_discovery=False)
            if not recovery.get("complete"):
                raise RuntimeError(f"榜单缓存恢复失败：{recovery.get('error_code')} {recovery.get('reason')}")
            rank = read_activity_rank_runtime_snapshot(activity_id)
        if not rank.get("complete"):
            raise RuntimeError(f"榜单Runtime不可读：{rank.get('error_code')} {rank.get('reason')}")
        actor = rank.get("self_ranking") or {}
        actor_id = actor.get("role_id") or actor.get("role_key")
        current_identity = (rank.get("rank_activity_id"), actor_id, actor.get("score"))
        if current_identity[0] != activity_id or not actor_id or current_identity[2] is None:
            raise RuntimeError("榜单页缺少活动、角色或自身积分身份")
        if identity is not None and current_identity != identity:
            raise RuntimeError("分页期间活动、角色或自身积分改变，拒绝混合不同基线")
        identity = current_identity
        page_rows = rank["rankings"]
        positions = sorted(row["rank"] for row in page_rows)
        merged.update({row["rank"]: dict(row) for row in page_rows})
        pages.append({"captured_at": rank.get("captured_at"), "ranks": positions,
                      "elapsed_seconds": rank.get("elapsed_seconds"),
                      "runtime_object_identity": rank.get("runtime_object_identity")})
        missing = required - merged.keys()
        result = {**rank, "rankings": [merged[r] for r in sorted(merged)],
                  "loaded_rank_count": len(merged), "page_observations": pages,
                  "missing_ranks": sorted(missing), "scroll_count": scrolls,
                  "reentry_count": reentries}
        if not missing or not positions:
            return result
        if any(r > rank["rank_list_size"] for r in missing):
            return result
        # Scrolling upward reuses GUI rows without requesting the first page;
        # the manager still holds the last server packet. Reenter once through
        # the verified GUI route while retaining this call's observed rows.
        if min(missing) < positions[0]:
            if reentries:
                return result
            landed = yield from context.wait_click_then_scene(
                702, "返回", [701], settle_seconds=1, timeout=15, max_clicks=1,
            )
            if int(landed) != 701:
                raise RuntimeError("补第一页时未返回天道巅峰入口")
            stage = yield from enter_current_peakrace_lingchong_stage(context)
            if stage["activity_id"] != activity_id:
                raise RuntimeError("补第一页期间灵兽阶段已改变")
            reentries += 1
            continue
        if scrolls >= max_scrolls:
            return result
        # down loads later content: the finger moves upward.
        for _ in range(min(4, max_scrolls - scrolls)):
            context.drag_shape_content(context.shape(702, "榜单列表"), direction="down")
            yield from context.wait_action_settle(1)
            scrolls += 1


def refresh_peak_pet_rank(context, *, activity_id: int, missing_ranks):
    """Initial opponent-board loading; later batches use the self-only callback."""
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


def refresh_peak_pet_self_rank(context, *, activity_id: int, missing_ranks=()):
    """Development callback: switch stage tabs, then read only our rank/score.

    Use with a previously established fixed plan. Tab switching follows the
    client's rank-refresh request path; this shorter GUI route still needs
    live acceptance. A positive score delta must be verified before more use.
    No opponent rows are loaded or parsed here.
    """
    if missing_ranks:
        raise ValueError("自身积分刷新不负责初始对手榜单加载")
    scene = int((yield from context.wait_scene([545, 483, 702], wait=15)))
    if scene == 545:
        scene = int((yield from context.wait_click_then_scene(
            545, "关闭背景", [483], settle_seconds=1, timeout=15, max_clicks=1,
        )))
    if scene == 483:
        scene = int((yield from context.wait_click_then_scene(
            483, "返回", [702], settle_seconds=1, timeout=15, max_clicks=1,
        )))
    if scene != 702:
        raise RuntimeError("未返回灵兽阶段榜单")
    # These two tabs only navigate; validate the active activity once after
    # returning, before accepting any score from its Runtime cache.
    scene = int((yield from context.wait_click_then_scene(
        702, "任务", [704], settle_seconds=1, timeout=15, max_clicks=1,
    )))
    if scene != 704:
        raise RuntimeError("未进入同阶段任务页签")
    scene = int((yield from context.wait_click_then_scene(
        704, "榜单", [702], settle_seconds=1, timeout=15, max_clicks=1,
    )))
    if scene != 702:
        raise RuntimeError("未切回灵兽阶段榜单")
    require_peakrace_stage_page(activity_id, tab_index=0)
    rank = read_activity_rank_self_snapshot(activity_id)
    if rank.get("error_code") == "process_cache_miss":
        recovery = prepare_activity_rank_runtime([activity_id], allow_discovery=False)
        if not recovery.get("complete"):
            raise RuntimeError(f"自身榜分缓存恢复失败：{recovery.get('reason')}")
        rank = read_activity_rank_self_snapshot(activity_id)
    if not rank.get("complete") or rank.get("rank_activity_id") != activity_id:
        raise RuntimeError(f"自身榜分不可读：{rank.get('reason')}")
    return {**rank, "gui_refresh": "stage_tabs"}


def enter_peak_pet_rank_resources(context, *, activity_id: int, pet_id: int):
    """Navigate to the taught pet; use_pet_resource_batch verifies Runtime identity."""
    if pet_id != 7101:
        raise NotImplementedError("尚未教授其它成长灵兽的选择")
    scene = int((yield from context.wait_scene([702], wait=15)))
    if scene != 702:
        raise RuntimeError("需要从灵兽阶段榜单进入资源页")
    require_peakrace_stage_page(activity_id, tab_index=0)
    landed = yield from context.wait_click_then_scene(702, "前往吞噬", [483],
        settle_seconds=1, timeout=15, max_clicks=1)
    if int(landed) != 483:
        raise RuntimeError("未进入灵兽页")
    lines = context.ocr_fragments_in_shapes(483, ["第一个成长灵兽"], frame_data_url=landed.frame_data_url)
    if not any("玄霜冰凤" in row.get("text", "") for row in lines):
        raise RuntimeError("首个成长灵兽与预期不符")
    yield from enter_first_growing_pet_aptitude(context)
    return {"scene_id": 545, "pet_id": pet_id}
