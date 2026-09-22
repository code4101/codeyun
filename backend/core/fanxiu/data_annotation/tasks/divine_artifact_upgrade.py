"""神器升阶：游戏自动把可进阶项前置，只处理列表首项直到标记消失。"""
from __future__ import annotations

STAGE_ID = "divine-artifact-upgrade"
STAGE_VERSION = "1"


def upgrade_divine_artifacts(context):
    """每次升阶必须见到成功页；整单重入先关闭成功/详情层再重新选取。

    不滚动、不沿用旧条目身份。一次升阶后重新回列表，由游戏排序决定
    下一项；本轮无可进阶标记才完成。只使用升阶，不购买材料。
    """
    match = yield from context.wait_scene([797, 796, 795, 35, 34], wait=5)
    if match.scene_id == 797:
        yield from context.wait_click(797, "返回")
        match = yield from context.wait_scene([796, 795], wait=5)
    if match.scene_id == 796:
        yield from context.wait_click(796, "返回")
        match = yield from context.wait_scene([795, 34], wait=5)
    if match.scene_id != 795:
        yield from context.go_scene(35)
        yield from context.wait_click(35, "菜单/神器")
    upgraded = 0
    while upgraded < 1000:
        match = yield from context.wait_scene([795], wait=5)
        if match.scene_id != 795:
            raise RuntimeError(f"神器升阶：未到列表，当前 #{match.scene_id}")
        # 翻页先绘制标题、后加载卡片，不能把空白过渡帧当作完成。
        frame = yield from context.wait_shape(795, "首个道具名称", timeout=8)
        available = context.shape_score(795, "可进阶", frame_data_url=frame) >= 80
        if not available:
            yield from context.wait_click(795, "返回")
            match = yield from context.wait_scene([34], wait=8)
            if match.scene_id != 34:
                raise RuntimeError("神器升阶：完成后未回世界")
            return {"result": "success", "outcome": "complete",
                    "upgraded": upgraded, "upgrade_available": False}
        yield from context.wait_click(795, "可进阶")
        match = yield from context.wait_scene([796], wait=5)
        if match.scene_id != 796:
            raise RuntimeError("神器升阶：未进入升阶详情")
        yield from context.wait_click(796, "升阶")
        match = yield from context.wait_scene([797], wait=8)
        if match.scene_id != 797:
            raise RuntimeError("神器升阶：未取得升阶成功证据，保留现场")
        upgraded += 1
        yield from context.wait_click(797, "返回")
        match = yield from context.wait_scene([796, 795], wait=5)
        if match.scene_id == 796:
            yield from context.wait_click(796, "返回")
        elif match.scene_id != 795:
            raise RuntimeError("神器升阶：成功页关闭后落点未知")
    raise RuntimeError("神器升阶：超过单次保护上限，不能标记完成")
