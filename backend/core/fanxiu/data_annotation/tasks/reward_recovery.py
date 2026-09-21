"""找回：仅领取免费奖励，以日常入口「领」消失为完成事实。"""
from __future__ import annotations

STAGE_ID = "reward-recovery"
STAGE_VERSION = "1"
DAILY_SCENE = 69
RECOVERY_SCENES = (793, 794)


def recover_rewards(context):
    """整单重入先返回日常重新判断；一轮最多点击一次一键免费。

    「已找回部分」只说明列表中的一项状态，不能替代入口完成判据。
    不点击任何付费找回按钮；完成后回到 #34 才返回组件凭证。
    """
    match = yield from context.wait_scene(
        [DAILY_SCENE, *RECOVERY_SCENES, 34], wait=5,
        label="找回：确认当前页面",
    )
    if match.scene_id in RECOVERY_SCENES:
        yield from context.wait_click(match.scene_id, "返回")
    yield from context.go_scene(DAILY_SCENE)

    def has_reward():
        match = yield from context.wait_scene([DAILY_SCENE], wait=5)
        if match.scene_id != DAILY_SCENE:
            raise RuntimeError("找回：未确认日常页面，不能判定无奖励")
        frame = yield from context.wait_shape(DAILY_SCENE, "奖励找回文字", timeout=5)
        return context.find_ocr_text(
            DAILY_SCENE, "领", in_shapes=["奖励找回/可领取"],
            crop=True, padding=4, frame_data_url=frame,
        ) is not None

    claimed = False
    if (yield from has_reward()):
        yield from context.wait_click(DAILY_SCENE, "奖励找回")
        match = yield from context.wait_scene(list(RECOVERY_SCENES), wait=8)
        if match.scene_id not in RECOVERY_SCENES:
            raise RuntimeError(f"找回：进入了非预期页面 #{match.scene_id}")
        yield from context.wait_click(match.scene_id, "一键免费")
        match = yield from context.wait_scene(list(RECOVERY_SCENES), wait=8)
        if match.scene_id not in RECOVERY_SCENES:
            raise RuntimeError(f"找回：领取后出现未知页面 #{match.scene_id}")
        yield from context.wait_click(match.scene_id, "返回")
        if (yield from has_reward()):
            raise RuntimeError("找回：一键免费后入口仍有领，保留现场待检查")
        claimed = True

    yield from context.wait_click(DAILY_SCENE, "退出")
    match = yield from context.wait_scene([34], wait=8)
    if match.scene_id != 34:
        raise RuntimeError("找回：完成后未返回 #34")
    return {"result": "success", "outcome": "claimed" if claimed else "absent",
            "claim_badge_present": False, "scene_id": 34}
