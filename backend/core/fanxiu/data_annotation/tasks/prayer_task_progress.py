"""祈愿任务页当前轮次的只读观察；与领奖、补积分动作分离。"""
from __future__ import annotations

import re
from typing import Any


FLOWER_PRAYER_ROUND_TARGET_SCORE = 160_000


def _score(value: str) -> int:
    number = value.rstrip("万亿")
    multiplier = 100_000_000 if value.endswith("亿") else 10_000 if value.endswith("万") else 1
    return int(float(number) * multiplier)


def parse_prayer_round_progress(
    round_text: str, gap_text: str, rows_text: str, *, maximum_task_score: int,
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
    target = int(maximum_task_score)
    if target <= 0:
        raise ValueError("祈愿最高档必须为已核实的正整数积分")
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


def observe_prayer_round_progress(context: Any, *, maximum_task_score: int) -> dict[str, Any]:
    """调用方须已打开 #455 祈愿任务页；只采一张新帧，不导航、不领奖。

    maximum_task_score 必须按当前祈愿类型的任务规则提供，仙花为160000，
    不能将这一常量套用于其他祈愿。当前轮领取后若自动进入下一轮，必须
    重新调用，不能重放旧积分差额。
    尚未接入周末补积分执行器；该观察本身不授权消耗资源。
    """
    frame = context.cur_frame(update=True)
    texts = []
    for title in ("祈愿奖励轮次", "祈愿本轮收尾缺口", "任务奖励"):
        lines = context.ocr_fragments_in_shapes(455, (title,), frame_data_url=frame, crop=True, padding=0)
        texts.append("\n".join(str(row.get("text") or "") for row in lines))
    return {**parse_prayer_round_progress(*texts, maximum_task_score=maximum_task_score),
            "evidence": dict(zip(("round_text", "gap_text", "rows_text"), texts))}


def wait_prayer_round_progress(context: Any, *, maximum_task_score: int, attempts: int = 8):
    """等待任务页加载后取得连续两次相同积分；不点击、不领奖。

    页面切换可先露出空背景，不能把空白视为零分。观察不完整继续等待，
    超过有界次数仍不完整则停止，保留现场；适用于实验前后积分取证。
    """
    if attempts < 2:
        raise ValueError("稳定观察至少需要两次")
    previous = None
    last_error = None
    for _ in range(attempts):
        try:
            current = observe_prayer_round_progress(context, maximum_task_score=maximum_task_score)
        except ValueError as exc:
            last_error = exc
            previous = None
        else:
            keys = ("current_round", "total_rounds", "current_score", "round_target_score")
            if previous is not None and all(current[k] == previous[k] for k in keys):
                return current
            previous = current
        yield from context.wait_action_settle(1.0)
    raise ValueError(f"祈愿积分未形成连续稳定观察：{last_error}")


def open_prayer_task_page(context: Any):
    """从已确认世界/祈愿页进入任务页；只导航，不领奖或使用资源。

    世界入口可被公告/OCR合行影响，复用既有字符定位和有限点击候选。
    每次尝试都核验落点；仍在世界才允许尝试下一处，不猜其他页面。
    """
    from backend.core.fanxiu.data_annotation.tasks.prayer_daily_resource import (
        prayer_entry_fragment, prayer_entry_from_tokens, prayer_entry_action_points,
        prayer_task_tab_fragment, fragment_center,
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
