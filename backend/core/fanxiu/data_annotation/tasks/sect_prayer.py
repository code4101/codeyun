"""宗门祈福只读检查：自然导航、核对实时成本、安全离场。"""

import re

from backend.core.fanxiu.data_annotation.scene_diagnostics import save_scene_diagnostic_frame
from backend.core.fanxiu.instrumentation.sect_prayer import read_sect_prayer_dialog_snapshot


PRAYER_STEWARD = "胡巧玲"


def _record_npc_world_page(context, frame_data_url):
    """保存 #951 同帧原图与人物名称 OCR；不点击任何 Shape。"""
    frame = frame_data_url
    if not frame:
        # 不用后续帧冒充匹配 #951 的同帧现场。
        raise RuntimeError("宗门祈福前往后停在 #951，但匹配结果无同帧图像，未保存现场")
    try:
        name = context.ocr_text_in_shapes(951, ["人物名称"], frame_data_url=frame)
    except Exception as exc:
        name = f"<OCR失败 {type(exc).__name__}: {exc}>"
    evidence = save_scene_diagnostic_frame(
        context.runner, frame, kind="sect-prayer-npc-world",
        label=f"前往祈福停在951 人物名称={name!r}",
        expected_scene_ids=[953], matched_scene_id=951,
    )
    return name, evidence


def _open_steward_dialog(context, frame_data_url):
    """仅当首位气泡确为祈福管事时点该气泡的聊天 Shape，并核验 #953 身份。"""
    name, evidence = _record_npc_world_page(context, frame_data_url)
    if PRAYER_STEWARD not in name:
        raise RuntimeError(
            f"宗门祈福前往后停在 #951，首位气泡不是{PRAYER_STEWARD}；"
            f"人物名称OCR={name!r}；首现场={evidence}"
        )

    def steward_still_first():
        # 点击前用实时帧再核身份，气泡顺序变化时不点。
        live = context.ocr_text_in_shapes(951, ["人物名称"])
        if PRAYER_STEWARD not in live:
            raise RuntimeError(f"宗门祈福点击前首位气泡已变为 {live!r}，未点击")

    yield from context.wait_click(951, "聊天", before_click=steward_still_first)
    dialog = yield from context.wait_scene_exact([953], timeout=20)
    steward = context.ocr_text_in_shapes(
        953, ["祈福管事"], frame_data_url=dialog.frame_data_url,
    )
    if PRAYER_STEWARD not in steward:
        raise RuntimeError(f"宗门祈福对话身份不是{PRAYER_STEWARD}：{steward!r}")
    return {"npc_world_name": name, "npc_world_frame": evidence, "steward": steward}


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
    arrived = yield from context.wait_scene_exact(
        [953, 951], timeout=70, observation_scenes=[953, 951, 20, 34],
    )
    npc_world = None
    if arrived.scene_id == 951:
        # 前往祈福停在 NPC 驻留世界页，管事气泡需要点击才打开 #953。
        # wait_scene 返回任一正式层匹配（含 #951），全层未命中才为 None。
        late = yield from context.wait_scene([953], wait=15, required=False)
        if late is not None and late.scene_id == 951:
            npc_world = yield from _open_steward_dialog(context, late.frame_data_url)
        elif late is None or late.scene_id != 953:
            scene = "unknown" if late is None else f"#{late.scene_id}"
            raise RuntimeError(f"宗门祈福前往后等待 #953 时识别为 {scene}，保留现场")
    yield from context.wait_scene_exact([953], timeout=20)
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
            "resource_consumption": 0, "world_scene": 34,
            "overview_text": text, "npc_world": npc_world}
