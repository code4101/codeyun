from __future__ import annotations

"""Shared NPC gift/return-gift cycle; only explicitly configured themes opt in.

Scene assets own geometry. The caller owns theme lifetime and recipient policy.
The all-select route is restricted to a verified single-gift theme screen; it is
not an adapter for arbitrary flower inventories or recipient planning.
"""

from dataclasses import dataclass
import base64
import io
import time

from PIL import Image

from backend.core.fanxiu.instrumentation.backpack import read_backpack_item_counts


@dataclass(frozen=True)
class ThemeNpcGiftSpec:
    recipient: str
    item_id: int
    item_name: str
    cover_scene: int
    entry_shape: str
    single_gift_screen: bool = False


XIANYUAN_GIFT = ThemeNpcGiftSpec(
    "仙园仙尊", 7020030, "游宴趣闻", 630, "寻访仙尊", True,
)


def _wait(context, scenes, wait=5):
    match = yield from context.wait_scene(scenes, wait=wait)
    if int(match) not in scenes:
        raise RuntimeError(f"仙缘赠礼：期望 {scenes}，实际 #{int(match)}")
    return match


def _count(spec):
    counts, _ = read_backpack_item_counts(
        [spec.item_id], manager_key="theme-npc-gift", force_refresh=True,
    )
    return int(counts[spec.item_id])


def _text(context, scene, shape):
    return "".join(context.ocr_text_in_shapes(
        scene, [shape], frame_data_url=context.cur_frame(update=True),
    ).split())


def _recipient(context, scene, shape, spec):
    if spec.recipient not in _text(context, scene, shape):
        raise RuntimeError(f"仙缘赠礼：#{scene} 收礼人物无法确认为 {spec.recipient}")


def _all_selected(context):
    """Read the green tick inside the annotated all-select control."""
    box = context.shape(759, "全选").raw
    frame = context.cur_frame(update=True)
    im = Image.open(io.BytesIO(base64.b64decode(frame.split(",", 1)[1]))).convert("RGB")
    w, h = im.size
    crop = im.crop((int(box["x"]*w), int(box["y"]*h),
                    int((box["x"]+box["w"])*w), int((box["y"]+box["h"])*h)))
    return sum(g > r*1.25 and g > b*1.08 and g > 95 for r, g, b in crop.getdata()) >= 20


def claim_npc_return_gifts(context, spec=XIANYUAN_GIFT):
    """At the shared NPC bag, claim once and prove its no-reward button.

    The bag shell loads before the reward list: wait for its action, never
    classify an empty loading frame as completion.
    """
    yield from _wait(context, [762], wait=15)
    _recipient(context, 762, "人物名称", spec)
    claimed = False
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        button = _text(context, 762, "一键领取")
        if "前往送礼" in button:
            return {"claimed": claimed, "verified": True}
        if "键领取" in button and not claimed:
            yield from context.wait_click(762, "一键领取")
            claimed = True
        yield from context.wait_action_settle(1)
    raise RuntimeError("仙缘回礼未出现已领完的前往送礼按钮")


def execute_theme_npc_gift(context, spec=XIANYUAN_GIFT):
    """One idempotent bounded round, ending at #34 only after verification.

    Each attempt re-enters the configured NPC. Existing gift results/rewards
    remain claimable from the NPC bag, so no saved GUI cursor is required.
    """
    if not spec.single_gift_screen:
        raise ValueError("只有实测仅含指定礼品的主题允许全选赠送")
    before = _count(spec)
    yield from context.go_scene(spec.cover_scene)
    yield from context.wait_click(spec.cover_scene, spec.entry_shape)
    yield from _wait(context, [198], wait=15)
    _recipient(context, 198, "人物名称", spec)
    if before:
        yield from context.wait_click(198, "前往")
        # Location travel includes a long animation, unlike ordinary popups.
        yield from _wait(context, [199], wait=45)
        yield from context.wait_click(199, "送礼")
        yield from _wait(context, [759], wait=15)
        _recipient(context, 759, "收礼人物", spec)
        if not _all_selected(context):
            yield from context.wait_click(759, "全选")
            yield from context.wait_action_settle(0.5)
        if not _all_selected(context):
            raise RuntimeError("仙缘赠礼：全选状态未确认")
        yield from context.wait_click(759, "赠送")
        yield from _wait(context, [760], wait=15)
        _recipient(context, 760, "收礼人物", spec)
        if _count(spec) != 0:
            raise RuntimeError("仙缘赠礼：赠送后指定礼品未清空")
        yield from context.wait_click(760, "关闭")
        scene = yield from _wait(context, [761, 762], wait=15)
        if int(scene) == 761:
            yield from context.wait_click(761, "查看礼物")
    else:
        # Empty gift stock does not imply that return gifts were claimed.
        yield from context.wait_click(198, "储物袋页签")
    rewards = yield from claim_npc_return_gifts(context, spec)
    after = _count(spec)
    if after:
        raise RuntimeError("仙缘赠礼收尾期间出现新礼品，需要重新核对")
    yield from context.wait_click(762, "关闭")
    yield from context.go_scene(34)
    return {"status": "round_complete", "gift_before": before,
            "gift_after": after, "rewards": rewards, "scene": 34}
