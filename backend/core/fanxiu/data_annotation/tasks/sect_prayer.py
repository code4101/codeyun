"""宗门祈福只读检查：自然导航、核对实时成本、安全离场。"""

import re

from backend.core.fanxiu.instrumentation.sect_prayer import read_sect_prayer_dialog_snapshot


def inspect_sect_prayer(context):
    """检查当前可进入的祈福详情并返回世界页，不点击消费按钮。

    调用方须先取得工程空闲窗口与 AI 运行权。返回费用仅描述下一次，
    不授权祈福，也不推断后续免费次数。界面与 Runtime 不一致时保留
    现场；当前实现只接受概览仍可前往的状态，不复用已完成次数的旧游标。
    """
    yield from context.enter_daily_list_direct(label="宗门祈福检查")
    opened = yield from context.open_daily_entry(
        label="宗门祈福检查", title_pattern=r"宗门\s*祈福",
        progress_can_mark_done=False,
    )
    if opened != "open":
        raise RuntimeError("宗门祈福检查未找到可进入的日常入口")
    yield from context.wait_scene_exact([846], timeout=20)
    yield from context.wait_click(846, "功能列表/宗门祈福")
    overview = yield from context.wait_scene_exact([952], timeout=20)
    text = context.ocr_text_in_shapes(
        952, ["今日剩余次数"], frame_data_url=overview.frame_data_url,
    )
    count = re.search(r"(\d+)\s*/\s*(\d+)", text)
    if count is None:
        raise RuntimeError("宗门祈福概览未读到明确剩余次数")
    remaining, maximum = map(int, count.groups())
    if not 0 < remaining <= maximum:
        raise RuntimeError("宗门祈福概览当前不可前往，保留现场")
    yield from context.wait_click(952, "前往祈福")
    yield from context.wait_scene_exact(
        [953], timeout=70, observation_scenes=[953, 951, 20, 34],
    )
    yield from context.wait_click(953, "祈福")
    yield from context.wait_scene_exact([954], timeout=20)
    snapshot = read_sect_prayer_dialog_snapshot()
    if (snapshot.get("complete") is not True
            or snapshot.get("currency_item_id") != 1
            or snapshot.get("donated_count") != maximum - remaining
            or snapshot.get("is_max") is not False):
        raise RuntimeError("宗门祈福详情与概览次数不一致，保留现场")
    yield from context.wait_click(954, "背景关闭")
    yield from context.wait_scene_exact([951], timeout=20)
    closed = read_sect_prayer_dialog_snapshot()
    if closed.get("reason") != "sect_prayer_dialog_not_open":
        raise RuntimeError("宗门祈福详情尚未关闭，保留现场")
    yield from context.wait_click(951, "离开")
    yield from context.go_scene(34)
    yield from context.wait_scene_exact([34], timeout=20)
    return {"complete": True, "snapshot": snapshot,
            "remaining_count": remaining, "daily_max": maximum,
            "resource_consumption": 0, "world_scene": 34}
