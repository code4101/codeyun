"""天河仙会正式页面导航；不转盘、不赠礼、不购买。

入口绑定当前 Runtime 活动菜单的精确 ID，旧截图只提供场景与几何。
已有页面可整单重入；当前页面与活动 ID 必须由 Runtime 再次核对。
真实首页重入已验收；各子页重入仍待逐项验收。
"""
from .activity_menu_navigation import open_loaded_activity_menu_item
from backend.core.fanxiu.instrumentation.prize_wheel import read_prize_wheel_view_snapshot
from backend.core.fanxiu.instrumentation.activity_runtime import read_activity_period_runtime_snapshot


def enter_tianhe_activity_home(context, *, activity_id: int):
    """进入已加载菜单中的当期天河首页，并核对活动 ID。

    本入口只接受调用方已核验的当期 ID。未知画面先走正式导航回世界，
    不沿用旧确认/消费意图；弹窗只使用既有返回 Shape。
    """
    if type(activity_id) is not int or activity_id <= 0:
        raise ValueError("需要当期天河仙会活动 ID")
    period = read_activity_period_runtime_snapshot(activity_id)
    landing = yield from context.wait_scene(
        [498, 499, 500, 501, 502, 503, 504, 505, 506, 34],
        wait=5.0, required=False, label="天河仙会：识别重入页面",
    )
    scene = landing.scene_id if landing is not None else None
    if scene in {503, 504, 505, 506}:
        yield from context.wait_click(scene, "返回")
        landing = yield from context.wait_scene_exact([498, 499], timeout=15.0)
        scene = landing.scene_id
    if scene in {499, 500, 501, 502}:
        yield from context.wait_click(scene, "仙花宝盘")
        yield from context.wait_scene_exact([498], timeout=15.0)
        scene = 498
    if scene != 498:
        yield from context.go_scene(34)
        yield from open_loaded_activity_menu_item(
            context, activity_id, kind="world_left", source_scene_id=34,
            ocr_shape_names=("左侧菜单",), expected_scene_ids=(498,),
            target_gui_name="天河仙会", timeout_seconds=20.0, max_scrolls=4,
        )
    view = read_prize_wheel_view_snapshot(activity_id)
    if view.get("complete") is not True:
        raise RuntimeError("天河首页画面与当期 Runtime 页面身份未对齐：" + str(view.get("reason")))
    if (any(view.get(key) != period.get(key)
            for key in ("runtime_id", "start_time_ms", "end_time_ms"))
            or any(view.get("evidence", {}).get(key) != period.get("evidence", {}).get(key)
                   for key in ("pid", "process_start_ticks"))):
        raise RuntimeError("天河首页与指定活动周期或进程身份不一致")
    return {"scene_id": 498, "activity_id": activity_id, "view": view}
