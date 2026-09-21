from __future__ import annotations

"""园中仙宴的一轮：领奖/开宴 → 高档随礼 → 基础随礼 → 领奖。

库存耗尽和当前没有适配宴席都是合法的本轮终态；预算耗尽不是完成。
主题皮肤只在适配表中命名，送礼策略仅使用宴席 A/B/C 与随礼 a/b。
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
import io
import re
import time
from typing import Any
from zoneinfo import ZoneInfo
from PIL import Image

from backend.core.fanxiu.instrumentation.backpack import read_backpack_item_counts


@dataclass(frozen=True)
class BanquetSkin:
    gifts: dict[str, tuple[int, str, str]]
    banquets: dict[str, tuple[str, ...]]
    filter_shape: str = "仅显示接受碧螺春"


XIANYUAN_SKIN = BanquetSkin(
    gifts={"a": (133, "白玉酿", "随礼·白玉酿"),
           "b": (134, "醉仙酿", "随礼·醉仙酿")},
    banquets={"A": ("百花宴",), "B": ("龙凤仙宴", "龙凤宴"), "C": ("瑶星宴",)},
)


def xianyan_window(occurrences, *, now=None):
    """Pure occurrence gate and next two-hour slot, including the final 21:00 tail.

    No weekday inference. End is exclusive; a midnight end belongs to the
    preceding final day. The caller owns scheduling the returned checkpoint.
    """
    zone = ZoneInfo("Asia/Shanghai")
    now = now or datetime.now(zone)
    if now.tzinfo is None:
        raise ValueError("仙宴窗口需要带时区的当前时间")
    now = now.astimezone(zone)
    rows = [r for r in occurrences if r.get("base_id") == 118000
            and int(r.get("start_time_ms") or 0) <= now.timestamp()*1000
            < int(r.get("end_time_ms") or 0)]
    if len(rows) != 1:
        return {"active": False, "reason": "no_unique_active_occurrence", "next_time": None}
    end = datetime.fromtimestamp(rows[0]['end_time_ms']/1000, zone)
    last_day = (end - timedelta(microseconds=1)).date()
    times = []
    for offset in (0, 1):
        day = now + timedelta(days=offset)
        for hour in (10, 12, 14, 16, 18, 20, 21):
            if hour == 21 and day.date() != last_day:
                continue
            slot = day.replace(hour=hour, minute=0, second=0, microsecond=0)
            if now < slot < end:
                times.append(slot)
    return {"active": True, "open": 10 <= now.hour < 22,
            "allow_high_on_a": now.date() == last_day and now.hour >= 21,
            "activity_id": rows[0]['activity_id'],
            "next_time": min(times).strftime('%Y-%m-%d %H:%M:%S') if times else None}


def _inventory(skin):
    counts, _ = read_backpack_item_counts(
        [spec[0] for spec in skin.gifts.values()],
        manager_key="xianyan-gifts", force_refresh=True,
    )
    return {tier: int(counts[spec[0]]) for tier, spec in skin.gifts.items()}


def _observe(runner, context, *scenes):
    scene = yield from _wait(
        runner,
        context, *scenes, timeout=10, label="园中仙宴闭环",
    )
    frame = context.cur_frame(update=True)
    tokens = context.full_frame_ocr_tokens(frame)
    return scene, frame, tokens


def _wait(runner, context, *scenes, timeout=10, label="园中仙宴"):
    """宴席陆续结算会打断任一局部阶段；只处理有专属标题证明的结果层。"""
    for _ in range(40):
        scene = yield from runner._wait_xianyan_exact_scene(
            context, *scenes, 659, 423, timeout=timeout, label=label,
        )
        if scene not in (659, 423) or scene in scenes:
            return scene
        text = _text(context.full_frame_ocr_tokens(context.cur_frame(update=True)))
        if "仙宴圆满结束" not in text and not all(s in text for s in ("宾客名单", "点击屏幕继续")):
            raise RuntimeError(f"拒绝处理非仙宴结果层：{text}")
        yield from context.wait_click(scene, "点击屏幕继续" if scene == 659 else "结算继续")
        yield from context.wait_action_settle(1)
    raise RuntimeError("仙宴连续结算超过有界预算")


def _text(tokens):
    return "".join(str(t.get("text", "")) for t in tokens).replace(" ", "")


def _button(runner, context, tokens, frame):
    # 只读详情底部按钮；标题也叫“参与仙宴”，不能全帧搜这个词。
    x, y, w, h = runner._xianyan_shape_box(context, 652, "参与仙宴")
    with Image.open(io.BytesIO(runner._decode_frame_data_url(frame))) as image:
        width, height = image.size
    return _text([t for t in tokens
                  if x*width <= float(t.get('x', 0)) <= (x+w)*width
                  and y*height <= float(t.get('y', 0)) <= (y+h)*height])


def _tier(text, skin):
    found = [tier for tier, names in skin.banquets.items() if any(n in text for n in names)]
    if len(found) != 1:
        raise RuntimeError(f"仙宴品质不能唯一识别：{text}")
    return found[0]


def _toggle(runner, context, scene, shape, checked):
    yield from _wait(runner, context, scene, timeout=8, label="仙宴配置")
    box = runner._xianyan_shape_box(context, scene, shape)
    frame = context.cur_frame(update=True)
    if runner._xianyan_checkbox_is_checked(context, frame, box) == checked:
        return
    yield from context.wait_click(scene, shape, timeout=8)
    deadline = time.monotonic() + 6
    while time.monotonic() < deadline:
        yield from context.wait_action_settle(0.6)
        context.clear_frame()
        frame = context.cur_frame(update=True)
        if runner._xianyan_checkbox_is_checked(context, frame, box) == checked:
            return
    raise RuntimeError(f"#{scene} {shape} 未切换到 {checked}")


def _home(runner, context, stop_event):
    match = yield from context.wait_scene(
        [642, 651, 652, 653, 756, 757, 422, 423, 659, 649, 34, 20, 630],
        wait=5, label="仙宴局部归位",
    )
    # 全局识别出的其它稳定页也是合法起点，交给 go_scene 自动规划。
    scene = match.scene_id
    if scene == 757:
        # 新 attempt 不继承未提交的消费意图。取消后重新按当前品质配置。
        yield from context.wait_click(757, "取消")
        scene = yield from _wait(runner, context, 653, 652, timeout=10)
    if scene == 756:
        # 确定只保存选项，不提交随礼。
        yield from context.wait_click(756, "确定")
        scene = 652
    if scene == 653:
        yield from context.wait_click(653, "礼品层背景关闭")
        scene = 652
    if scene == 652:
        yield from context.wait_click(652, "关闭")
        scene = 651
    if scene == 651:
        yield from context.wait_click(651, "遮罩关闭")
        scene = 642
    if scene == 649:
        yield from context.wait_click(649, "关闭")
        scene = 642
    if scene not in {642, 422, 423, 659}:
        yield from context.go_scene(630)
        yield from context.wait_click(630, "园中仙宴", timeout=10)
    scene = yield from _wait(runner,
        context, 642, 422, 423, 659, 631, timeout=15, label="仙宴入口",
    )
    if scene == 631:
        raise RuntimeError("园中仙宴当前不在开放时间")
    if scene != 642:
        _, landing, _ = yield from runner._claim_available_xianyan_rewards(
            context, stop_event, max_rounds=100, settle_seconds=0.7, wait_timeout=10,
        )
        if landing == 651:
            # 领奖结果可能晚于主页刷新，覆盖刚打开的列表；以实际落点收口。
            yield from context.wait_click(651, "遮罩关闭")
    yield from _wait(runner, context, 642, timeout=10, label="仙宴主页")


def _open_list(runner, context, stop_event, skin, *, low_only):
    scene = yield from _wait(runner,
        context, 651, 652, 642, 422, 423, 659, 34, 20, 630, 756, 653, 757,
        timeout=10, label="仙宴随礼起点",
    )
    if scene == 652:
        yield from context.wait_click(652, "关闭")
        scene = 651
    if scene != 651:
        yield from _home(runner, context, stop_event)
        for _ in range(40):
            yield from context.wait_click(642, "参与宴会")
            landing = yield from _wait(runner, context, 651, 422, timeout=10)
            if landing == 651:
                break
            yield from _home(runner, context, stop_event)
        else:
            raise RuntimeError("宴席结算持续阻断参与列表，保留现场")
    yield from _toggle(runner, context, 651, skin.filter_shape, low_only)
    _, _, tokens = yield from _observe(runner, context, 651)
    text = _text(tokens)
    if "查看" not in text and "已赴宴" not in text:
        if any(s in text for s in ("暂无", "没有仙宴", "暂无仙宴")):
            return False
        raise RuntimeError(f"宴会列表既未证实可查看，也未证实空列表：{text}")
    yield from context.wait_click(651, "第一个查看")
    yield from _wait(runner, context, 652, timeout=10, label="仙宴详情")
    return True


def _configure(runner, context, skin, tier, text):
    """返回是否已提交首次随礼；该动作可能继续到专属消费确认层。"""
    name, choice = skin.gifts[tier][1:]
    if re.search(rf"赴宴礼物.*?{re.escape(name)}[（(]\d+[）)]", text):
        return
    if "更换" in text:
        yield from context.wait_click(652, "更换随礼")
        scene = 756
    elif "拥有可用礼物" in text:
        yield from context.wait_click(652, "参与仙宴")
        scene = 653
    else:
        raise RuntimeError(f"不能安全配置随礼 {tier}：{text}")
    shape = ("低档随礼a" if tier == "a" else "高档随礼b") if scene == 756 else choice
    yield from _toggle(runner, context, scene, shape, True)
    if scene == 653:
        yield from _toggle(runner, context, 653, "记住选择", True)
        # 背景只取消配置；首次参与必须提交，不能再重复点详情参与。
        yield from context.wait_click(653, "参与仙宴")
        return True
    else:
        yield from context.wait_click(756, "确定")
    _, _, tokens = yield from _observe(runner, context, 652)
    if not re.search(rf"赴宴礼物.*?{re.escape(name)}[（(]\d+[）)]", _text(tokens)):
        raise RuntimeError("随礼选择尚未在详情页得到确认，停止提交")


def execute_xianyan_gifts(
    runner: Any, context: Any, stop_event: Any, *,
    skin: BanquetSkin = XIANYUAN_SKIN, allow_high_on_a: bool = False,
    max_batches: int = 60,
):
    """先 b 后 a；高档逐席确认，基础每批最多十次。

    allow_high_on_a 只由上层真实主题最后一天21点门禁传入，默认禁止。
    当前排序由游戏保证未参加的高档在前；首个 A 是本轮高档截止边界。
    """
    initial = _inventory(skin)
    reasons = {}
    batches = 0
    for gift in ("b", "a"):
        configured = False
        if _inventory(skin)[gift] == 0:
            reasons[gift] = "empty"
            continue
        if not (yield from _open_list(runner, context, stop_event, skin, low_only=gift == "a")):
            reasons[gift] = "no_banquets"
            continue
        while batches < max_batches:
            runner._raise_if_stopped(stop_event)
            scene, frame, tokens = yield from _observe(runner, context, 652, 651)
            if scene == 651:
                reasons[gift] = "round_exhausted"
                break
            text, button = _text(tokens), _button(runner, context, tokens, frame)
            stock = _inventory(skin)
            if stock[gift] == 0:
                reasons[gift] = "empty"
                break
            if "关闭" in button:
                yield from context.wait_click(652, "参与仙宴")
                yield from _wait(runner, context, 651, timeout=10, label="结束本轮")
                reasons[gift] = "round_exhausted"
                break
            if gift == "b" and _tier(text, skin) == "A" and not allow_high_on_a:
                reasons[gift] = "no_high_banquet"
                break
            attended = "查看下一个" in button or "已赴宴" in text
            submitted = False
            if not attended:
                submitted = yield from _configure(runner, context, skin, gift, text)
                # 配置之后重新检查品质；记忆选择不能授权下一席。
                if not submitted:
                    _, _, tokens = yield from _observe(runner, context, 652)
                if not submitted and gift == "b" and _tier(_text(tokens), skin) == "A" and not allow_high_on_a:
                    raise RuntimeError("配置过程中宴席变成 A，禁止消费 b")
            remaining = re.search(r"剩余(\d+)位道友", _text(tokens))
            # 全部已赴宴时没有礼物选择入口。b 为零、低档筛选已开启，
            # 可以批量翻过已参加页；即使遇到新桌也不可能误耗高档礼物。
            fast = configured or (attended and stock['b'] == 0)
            clicks = 1 if submitted or gift == "b" or not fast else min(10, stock['a'], int(remaining.group(1)) + 1 if remaining else 1)
            for _ in range(0 if submitted else clicks):
                runner._raise_if_stopped(stop_event)
                context.click_shape_center_fast(652, "参与仙宴")
                # 一个最多十次的小批次，不能每次都 yield 到工程 Scheduler：
                # 它的 tick 间隔会把 0.6 秒放大成数秒。Event.wait 保持可中断。
                stop_event.wait(0.6)
                runner._raise_if_stopped(stop_event)
            yield from context.wait_action_settle(0.8)
            if submitted or not configured or gift == "b":
                landing = yield from _wait(runner, context, 652, 651, 653, 757, timeout=10)
                if landing == 757:
                    confirmation = _text(context.full_frame_ocr_tokens(context.cur_frame(update=True)))
                    name = skin.gifts[gift][1]
                    if not re.search(rf"确认使用1个随礼.*?{re.escape(name)}.*?参与仙宴", confirmation):
                        raise RuntimeError(f"随礼确认与本轮品质不一致：{confirmation}")
                    yield from _toggle(runner, context, 757, "本次登录不再提醒", True)
                    yield from context.wait_click(757, "确定")
                    yield from _wait(runner, context, 652, 651, timeout=10)
                elif landing == 653:
                    raise RuntimeError("随礼配置未生效，保留现场")
            after = _inventory(skin)
            other = "a" if gift == "b" else "b"
            if after[other] != stock[other] or not 0 <= stock[gift] - after[gift] <= clicks:
                raise RuntimeError(f"随礼库存异常：{stock} → {after}")
            runner._log("detail", f"仙宴随礼 {gift}：{stock} → {after}，{clicks}次")
            if gift == "b" and not attended and after[gift] != stock[gift] - 1:
                raise RuntimeError("高档随礼提交后未扣减，保留现场")
            if gift == "a" and not configured and after['a'] == stock['a'] - 1:
                # 首次配置已勾记忆，或详情已经明示 a；实际扣减证明提交成功。
                # 结果可停在“已赴宴”，不强求当前桌再次显示礼物选择行。
                configured = True
            batches += 1
        else:
            return {"status": "pending", "before": initial, "after": _inventory(skin), "reasons": reasons}
    yield from _home(runner, context, stop_event)
    return {"status": "round_complete", "before": initial, "after": _inventory(skin), "reasons": reasons}


def execute_xianyan_cycle(runner: Any, context: Any, stop_event: Any, *, max_batches: int = 60):
    """从当前真实现场或通用入口运行一轮，成功后回 #34；可安全再次调用。

    研发公共入口不写调度状态。主题有效期及两小时窗口由主题集负责；
    这里通过实时入口和场景确认园中仙宴确实开放。
    """
    from backend.core.fanxiu.instrumentation.xianyuan_banquet import read_xianyuan_banquet_runtime

    snapshot = read_xianyuan_banquet_runtime()
    window = xianyan_window(snapshot.get('occurrences', []))
    if not window['active']:
        raise RuntimeError(f"仙宴有效期未得到唯一真实活动证明：{snapshot.get('status')}")
    if not window['open']:
        yield from context.go_scene(34)
        return {"status": "outside_window", "next_time": window['next_time'], "scene": 34}
    yield from _home(runner, context, stop_event)
    host = yield from runner._execute_xianyan_host_baihua_same_attempt(
        context, stop_event, {"schedule_rewards": False},
    )
    if host != "success":
        return {"status": "pending", "host": host}
    gifts = yield from execute_xianyan_gifts(
        runner, context, stop_event, max_batches=max_batches,
        allow_high_on_a=window['allow_high_on_a'],
    )
    if gifts['status'] != 'round_complete':
        return gifts
    claimed, scene, _ = yield from runner._claim_available_xianyan_rewards(
        context, stop_event, max_rounds=100, settle_seconds=0.7, wait_timeout=10,
    )
    if scene != 642:
        raise RuntimeError(f"仙宴收尾意外场景 #{scene}")
    yield from context.go_scene(34)
    yield from _wait(runner, context, 34, timeout=10, label="仙宴闭环收尾")
    result = {"status": "round_complete", "host": host, "gifts": gifts,
              "claimed": claimed, "scene": 34, "next_time": window['next_time']}
    runner._log("success", f"园中仙宴本轮闭环：{result}")
    return result
