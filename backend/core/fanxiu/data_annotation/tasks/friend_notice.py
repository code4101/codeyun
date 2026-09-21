"""消除世界页临时「友」提示；不添加好友、不审批、不发送消息。"""
from __future__ import annotations

STAGE_ID = "friend-notice"
NEW_FRIENDS_SCENE = 780
WORLD_SCENES = (34, 661)


def find_friend_notice(context, *, frame_data_url=None):
    """单字在全帧 OCR 中易漏检，使用资产框局部 OCR 保留真实文字证据。"""
    return context.find_ocr_text(
        34, "友", in_shapes=["友"], crop=True, padding=8,
        frame_data_url=frame_data_url,
    )


def dismiss_friend_notice(context):
    """从当前事实整单重入；世界页无「友」才完成。

    中断在新的好友/通讯录时先退出，再观察世界页。进入后仅关闭窗口，
    一次调用至多点击一次提示；仍有提示就报错，避免无进展的重复点击。
    """
    match = yield from context.wait_scene(
        [NEW_FRIENDS_SCENE, 333, *WORLD_SCENES], wait=8,
        label="好友提示：定位当前页面",
    )
    if match.scene_id == NEW_FRIENDS_SCENE:
        yield from context.wait_click(NEW_FRIENDS_SCENE, "返回")
        match = yield from context.wait_scene([333, *WORLD_SCENES], wait=8)
    if match.scene_id == 333:
        yield from context.wait_click(333, "返回")
        match = yield from context.wait_scene(list(WORLD_SCENES), wait=8)
    if match.scene_id not in WORLD_SCENES:
        yield from context.go_scene(34)
        match = yield from context.wait_scene(list(WORLD_SCENES), wait=8)
    if match.scene_id not in WORLD_SCENES:
        raise RuntimeError(f"好友提示：未回到世界页，当前 #{match.scene_id}")

    frame = context.cur_frame(update=True)
    if find_friend_notice(context, frame_data_url=frame) is None:
        return {"result": "success", "outcome": "absent", "clicked": False}
    context.click_ocr_text(34, "友", in_shapes=["友"], crop=True,
                           padding=8, frame_data_url=frame)
    match = yield from context.wait_scene([NEW_FRIENDS_SCENE, 333], wait=8)
    if match.scene_id == NEW_FRIENDS_SCENE:
        yield from context.wait_click(NEW_FRIENDS_SCENE, "返回")
        match = yield from context.wait_scene([333, *WORLD_SCENES], wait=8)
    if match.scene_id == 333:
        yield from context.wait_click(333, "返回")
        match = yield from context.wait_scene(list(WORLD_SCENES), wait=8)
    if match.scene_id not in WORLD_SCENES:
        raise RuntimeError(f"好友提示：点击后未安全退出，当前 #{match.scene_id}")
    if find_friend_notice(context) is not None:
        raise RuntimeError("好友提示：退出后仍有「友」，保留现场待检查")
    return {"result": "success", "outcome": "dismissed", "clicked": True}
