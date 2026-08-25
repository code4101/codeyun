"""仙窍试炼逐级探测的纯业务状态模型。

给后续维护者和 AI agent 的业务心智：

* 每天从游戏画面重新取状态，不保存昨天的成功等级。#357 是否出现
  “开启扫荡”就是当前难度是否已经通关的权威事实：出现则下一场加1级，
  不出现则当前已是越级状态，直接挑战。
* 点击 #357“挑战”后的候选由游戏决定，可能出现 #359“开始挑战”、
  #360“继续挑战”或 #366“开启扫荡”。程序必须响应实际场景，不能根据
  “刚才是否加过难度”等外部历史预判分支。
* 扫荡的真实视觉时序是 ``#366 -> 瞬时 #357 -> #367 奖励层 -> #357``。
  #367 约3秒自动消失，且中间的 #357 只是闪现，不代表扫荡已经稳定完成。
  工程实现因此故意不把 #367 作为硬识别条件，而是点击后等待5秒，再等待
  稳定 #357。若超时，应保留当前帧、OCR 和场景分数排查，不要盲点屏幕。
* 当当天业务全部完成后，#357“返回”已真实验证为直接回 #34 世界页，
  不是回 #356。单步 AI 调试只有在用户明确要求时才执行这项收尾动作。

本模块本身不截图、不点击，只保存上述状态模型并解析 #357 的剩余次数；
具体场景响应和短暂画面等待位于 ``BehaviorTreeRuntime``。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from backend.core.fanxiu.data_annotation.ocr_values import parse_ocr_values

_FULLWIDTH_DIGITS = str.maketrans("０１２３４５６７８９／：", "0123456789/:")


@dataclass(frozen=True)
class ObservedTrialAttempts:
    """从 #357 实时读取到的剩余奖励次数。"""

    remaining: int
    capacity: int | None
    text: str


@dataclass(frozen=True)
class ObservedTrialHomeState:
    """#357 一帧画面提供的试炼推进事实。"""

    attempts: ObservedTrialAttempts
    sweep_available: bool
    sweep_score: float


def parse_xianqiao_trial_attempts(text: str) -> ObservedTrialAttempts:
    """解析“今日剩余奖励次数”区域。

    界面可能识别成 ``今日剩余:奖励次数:2/2``、``剩余奖励次数：2/2``
    或仅 ``奖励次数:2``。第一个数字始终按剩余可挑战次数解释。
    """

    normalized = str(text or "").translate(_FULLWIDTH_DIGITS)
    compact = re.sub(r"\s+", "", normalized)
    match = re.search(
        r"(?:今日)?剩余.{0,8}?(?:奖励)?次数(.*)",
        compact,
    )
    if match is None:
        match = re.search(r"次数(.*)", compact)
    if match is None:
        raise ValueError(f"无法解析仙窍试炼剩余次数：{normalized!r}")
    tail = match.group(1)
    fraction = parse_ocr_values(tail, expected_count=2, allow_extra_numbers=True)
    if fraction is not None:
        remaining, capacity = fraction
    else:
        single = parse_ocr_values(tail, expected_count=1)
        if single is None:
            raise ValueError(f"无法解析仙窍试炼剩余次数：{normalized!r}")
        remaining, capacity = single[0], None
    return ObservedTrialAttempts(
        remaining=remaining,
        capacity=capacity,
        text=normalized,
    )
