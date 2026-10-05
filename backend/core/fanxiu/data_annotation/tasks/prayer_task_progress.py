"""祈愿任务页当前轮次的只读观察；与领奖、补积分动作分离。"""
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any


FLOWER_PRAYER_ROUND_TARGET_SCORE = 160_000
PrayerRoundTargetRule = int | Mapping[str | int, int]


def resolve_prayer_round_target(rule: Any, current_round: int) -> int:
    """解析已核实轮次规则；整数仅适用于已证明每轮目标相同的活动。

    炼丹实机第3轮80000、第4轮100000，不能用一轮目标覆盖整个类别。
    映射未包含当前轮时失败关闭，不推算未验证的后续目标。
    """
    value = (rule.get(str(current_round), rule.get(current_round))
             if isinstance(rule, Mapping) else rule)
    if type(value) is not int or value <= 0:
        raise ValueError(f"祈愿第{current_round}轮最高档规则尚未核实")
    return value


def _score(value: str) -> int:
    number = value.rstrip("万亿")
    multiplier = 100_000_000 if value.endswith("亿") else 10_000 if value.endswith("万") else 1
    return int(float(number) * multiplier)


def parse_prayer_round_progress(
    round_text: str, gap_text: str, rows_text: str, *, maximum_task_score: PrayerRoundTargetRule,
) -> dict[str, Any]:
    """最高档必须由已核实的任务规则传入，仙花祈愿为16万。

    奖励轮次与进度条都含斜杠，必须分 ROI 解析。积分只表示本轮，
    不与仙花跨服榜的累计友好度或其他轮次相加。顶部“再获得”是另一个
    奖励的差额，不是任务最高档；当前可见列表也可能未滚到最高档。
    已领取档位会显示1000/1000等封顶值，不能当作真实本轮积分。
    """
    round_text, gap_text = (re.sub(r"\s+", "", x) for x in (round_text, gap_text))
    # Preserve OCR line boundaries: reward quantities 80, 300 adjacent to
    # 26674/32000 must never become the fabricated numerator 8030026674.
    rows_text = re.sub(r"[ \t]+", "", rows_text)
    rounds = re.findall(r"奖励轮次[:：]?(\d+)/(\d+)", round_text)
    gaps = re.findall(r"再获得(\d+)积分可获得", gap_text)
    number = r"\d+(?:\.\d+)?[万亿]?"
    ratios = [(_score(a), _score(b)) for a, b in re.findall(rf"({number})/({number})", rows_text)]
    if len(rounds) != 1:
        raise ValueError("祈愿轮次观察不完整")
    current_round, total_rounds = map(int, rounds[0])
    target = resolve_prayer_round_target(maximum_task_score, current_round)
    if any(a > b or b > target or b <= 0 for a, b in ratios):
        raise ValueError("祈愿进度条数字与未完成档次不符")
    scores = {a for a, b in ratios if a < b}
    if not scores and (target, target) in ratios:
        scores = {target}
    if len(rounds) != 1 or len(scores) != 1 or not ratios:
        raise ValueError("祈愿轮次/本轮积分观察不完整")
    current_round, total_rounds = map(int, rounds[0])
    current_score = scores.pop()
    remaining = target - current_score
    if not 0 < current_round <= total_rounds:
        raise ValueError("祈愿本轮最高档与任务进度矛盾")
    return {
        "complete": True, "current_round": current_round, "total_rounds": total_rounds,
        "current_score": current_score, "round_target_score": target,
        "round_score_gap": remaining, "visible_thresholds": sorted({b for _, b in ratios}),
        "header_reward_score_gap": int(gaps[0]) if len(gaps) == 1 else None,
        "target_source": "已核实的祈愿任务最高档规则",
    }


def observe_prayer_round_progress(context: Any, *, maximum_task_score: PrayerRoundTargetRule) -> dict[str, Any]:
    """调用方须已打开 #455 祈愿任务页；只采一张新帧，不导航、不领奖。

    maximum_task_score 必须按当前祈愿类型的任务规则提供，仙花为160000，
    不能将这一常量套用于其他祈愿。当前轮领取后若自动进入下一轮，必须
    重新调用，不能重放旧积分差额。
    供原子实验与周末补积分执行器取证；该观察本身不授权消耗资源。
    """
    frame = context.cur_frame(update=True)
    texts = []
    for title in ("祈愿奖励轮次", "祈愿本轮收尾缺口", "任务奖励"):
        lines = context.ocr_fragments_in_shapes(455, (title,), frame_data_url=frame, crop=True, padding=0)
        texts.append("\n".join(str(row.get("text") or "") for row in lines))
    return {**parse_prayer_round_progress(*texts, maximum_task_score=maximum_task_score),
            "evidence": dict(zip(("round_text", "gap_text", "rows_text"), texts))}


def prayer_rows_need_score_scroll(rows_text: str, *, maximum_task_score: int) -> bool:
    """可见档位全封顶且未到最高档时，积分未知，须继续观察列表下方。"""
    number = r"\d+(?:\.\d+)?[万亿]?"
    text = re.sub(r"[ \t]+", "", rows_text)
    ratios = [(_score(a), _score(b)) for a, b in re.findall(rf"({number})/({number})", text)]
    return bool(ratios) and all(0 < a == b < maximum_task_score for a, b in ratios)


def wait_prayer_round_progress(context: Any, *, maximum_task_score: PrayerRoundTargetRule, attempts: int = 32):
    """等待任务页加载后取得连续两次相同积分；不点击、不领奖。

    页面切换可先露出空背景，不能把空白视为零分。观察不完整继续等待，
    连续两次只见封顶中间档时，从列表空白边缘滚动查找未封顶或最高档。
    真机奖励图标会拦截长拖，不能从图标上发起。超过有界次数仍不完整
    则停止，保留现场；适用于实验前后积分取证。
    """
    if attempts < 2:
        raise ValueError("稳定观察至少需要两次")
    previous = None
    last_error = None
    capped_rows = None
    for _ in range(attempts):
        try:
            current = observe_prayer_round_progress(context, maximum_task_score=maximum_task_score)
        except ValueError as exc:
            last_error = exc
            previous = None
            # 空白加载页或矛盾数字不触发滚动，只有完整封顶进度条才有依据。
            lines = context.ocr_fragments_in_shapes(455, ("任务奖励",),
                frame_data_url=context.cur_frame(), crop=True, padding=0)
            rows = "\n".join(str(row.get("text") or "") for row in lines)
            round_lines = context.ocr_fragments_in_shapes(455, ("祈愿奖励轮次",),
                frame_data_url=context.cur_frame(), crop=True, padding=0)
            round_text = re.sub(r"\s+", "", "".join(str(row.get("text") or "") for row in round_lines))
            rounds = re.findall(r"奖励轮次[:：]?(\d+)/(\d+)", round_text)
            if len(rounds) == 1:
                target = resolve_prayer_round_target(maximum_task_score, int(rounds[0][0]))
            else:
                target = None
            if target is not None and prayer_rows_need_score_scroll(rows, maximum_task_score=target):
                # 图标中的奖励数量 OCR 会波动，只比较进度条，不等待整页字串相同。
                number = r"\d+(?:\.\d+)?[万亿]?"
                row_scores = tuple(re.findall(rf"({number})/({number})", re.sub(r"[ \t]+", "", rows)))
                if row_scores == capped_rows:
                    context.drag_shape_content(455, "任务奖励", direction="down",
                                               ratio=0.85, cross_axis_ratio=0.01)
                    capped_rows = None
                else:
                    capped_rows = row_scores
            else:
                capped_rows = None
        else:
            keys = ("current_round", "total_rounds", "current_score", "round_target_score")
            if previous is not None and all(current[k] == previous[k] for k in keys):
                return current
            previous = current
        yield from context.wait_action_settle(1.0)
    raise ValueError(f"祈愿积分未形成连续稳定观察：{last_error}")


def open_prayer_activity_page(context: Any):
    """从已确认世界进入祈愿活动壳；只导航，不领奖或使用资源。

    世界入口可被公告/OCR合行影响，复用既有字符定位和有限点击候选。
    每次尝试都核验落点；仍在世界才允许尝试下一处，不猜其他页面。
    """
    from backend.core.fanxiu.data_annotation.tasks.prayer_daily_resource import (
        prayer_entry_fragment, prayer_entry_from_tokens, prayer_entry_action_points,
    )
    match = yield from context.wait_scene([34,455],wait=8)
    if match.scene_id == 34:
        entry = None
        for _ in range(12):
            frame = context.cur_frame(update=True)
            entry = prayer_entry_fragment(context.ocr_fragments(frame)) or prayer_entry_from_tokens(
                context.ocr_tokens_in_shapes(34,("左侧菜单",),frame_data_url=frame))
            if entry is not None:
                break
            yield from context.wait_action_settle(0.75)
        if entry is None:
            raise RuntimeError("未可靠定位祈愿入口")
        for point in prayer_entry_action_points(entry):
            context.click_frame_point(34,*point)
            yield from context.wait_action_settle(1.0)
            match = yield from context.wait_scene([34,455],wait=12)
            if match.scene_id == 455:
                break
        if match.scene_id != 455:
            raise RuntimeError("限定祈愿入口点击未到达主页")
    return 455


def open_prayer_task_page(context: Any):
    """复用活动入口，再按已确认页签进入任务页；不领奖或使用资源。"""
    from .prayer_daily_resource import prayer_task_tab_fragment, fragment_center
    yield from open_prayer_activity_page(context)
    tab = None
    for _ in range(8):
        frame = context.cur_frame(update=True)
        tab = prayer_task_tab_fragment(context.ocr_fragments(frame))
        if tab is not None:
            break
        yield from context.wait_action_settle(0.75)
    if tab is None:
        raise RuntimeError("未定位祈愿任务页签")
    context.click_frame_point(455,*fragment_center(tab))
    yield from context.wait_action_settle(1.0)
    return 455


def inspect_active_prayer_task_panel(context: Any, *, expected_activity_id: int):
    """导航后取得连续两次相同的完整当前轮面板证据，不领奖或使用资源。

    用于核验新轮次最高档；返回原始行，不把目录推测自动写成满轮规则。
    已在炼丹第5轮实机核验完整20行及连续稳定读取、返回世界。
    每次调用均从世界重新进入当前任务页。
    """
    from backend.core.fanxiu.instrumentation.prayer_task_panel import read_prayer_task_panel
    yield from context.go_scene(34)
    yield from open_prayer_task_page(context)
    previous = None
    for _ in range(8):
        snapshot = read_prayer_task_panel(expected_activity_id=expected_activity_id)
        if previous is not None and all(snapshot[k] == previous[k]
                for k in ('activity_id', 'current_round', 'rows')):
            yield from context.go_scene(34)
            return snapshot
        previous = snapshot
        yield from context.wait_action_settle(1.0)
    raise ValueError('当前祈愿任务面板未形成连续稳定的完整列表')


def observe_active_prayer_round(context: Any, *, round_targets: dict[str, PrayerRoundTargetRule]):
    """核对世界页真实祈愿名称与本周，再导航读取稳定积分并绑定活动身份。

    round_targets由已核实任务规则提供，不用仙花常量代替其他类型。日期仅
    确定周界，必须同时核对真实入口；跨周或入口不符不返回实验观察。
    本函数会导航和采帧，只能在已取得游戏运行权的正式任务内调用。
    """
    from backend.core.fanxiu.prayer_cycle import prayer_cycle_week, prayer_entry_category
    from backend.core.fanxiu.data_annotation.tasks.prayer_daily_resource import (
        prayer_entry_fragment,prayer_entry_from_tokens,
    )
    week = prayer_cycle_week()
    target = round_targets.get(week.name)
    if not isinstance(target, Mapping) and (type(target) is not int or target <= 0):
        raise ValueError(f"{week.name}祈愿最高档规则尚未核实")
    yield from context.go_scene(34)
    entry = None
    for _ in range(8):
        frame = context.cur_frame(update=True)
        entry = prayer_entry_fragment(context.ocr_fragments(frame)) or prayer_entry_from_tokens(
            context.ocr_tokens_in_shapes(34,("左侧菜单",),frame_data_url=frame))
        if entry is not None:
            break
        yield from context.wait_action_settle(0.75)
    entry_text = re.sub(r"\s+", "", str((entry or {}).get("text") or "")).replace("祈原","祈愿")
    if entry is None or prayer_entry_category(entry_text) != week.name:
        raise ValueError("真实祈愿入口与本周类型不一致，停止积分观察")
    yield from open_prayer_task_page(context)
    progress = yield from wait_prayer_round_progress(context,maximum_task_score=target)
    if prayer_cycle_week().week_start != week.week_start:
        raise ValueError("积分观察跨越祈愿周界，丢弃本次观察")
    return {**progress,"activity_key":f"{week.name}:{week.week_start.isoformat()}",
            "prayer_category":week.name,"activity_entry_text":entry["text"]}
