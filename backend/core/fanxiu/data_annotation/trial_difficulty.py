"""仙窍试炼 #358 的难度业务模型。

本模块只描述“应该配置成什么”，不截图、不 OCR、不点击。界面动作由
``FanxiuRuntime`` 完成。分层后，后续业务规则变化（例如不再均匀分配）只需
调整计划函数；滑杆坐标、滚动和 OCR 精度变化则留在 Runtime 控件层处理。

当前业务规则：

1. 五个难度属性各自至少占 1 档，所以初始难度是 6 级。
2. 显示难度等于 ``1 + 五项档位之和``，五项全满时为 101 级。
3. “均匀模型、优先填充前面的”表示先做整数均分，余数依次给前面的属性。
4. 进入 #358 后先处理五行增益，再读取实时当前难度；本轮目标难度默认是
   ``当前难度 + 1``。这个先后顺序属于 Runtime 总编排，不属于本纯模型。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable


_FULLWIDTH_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")
TRIAL_DIFFICULTY_BASE_LEVEL = 1


@dataclass(frozen=True)
class TrialDifficultyAxis:
    """一个试炼难度属性的离散配置。

    :param str label: 实时 OCR 标题中用于定位该行的稳定子串。
    :param int minimum: 第一档对应的百分比。
    :param int maximum: 最大百分比。
    :param int step: 每增加一档增加的百分比。
    """

    label: str
    minimum: int
    maximum: int
    step: int

    @property
    def max_position(self) -> int:
        return self.maximum // self.step

    def value_at(self, position: int) -> int:
        """把从 1 开始的档位换算成界面百分比。"""

        position = int(position)
        if position < 1 or position > self.max_position:
            raise ValueError(f"{self.label}档位 {position} 超出 1..{self.max_position}")
        value = position * self.step
        if value < self.minimum or value > self.maximum:
            raise ValueError(f"{self.label}档位 {position} 对应的百分比无效")
        return value


TRIAL_DIFFICULTY_AXES = (
    TrialDifficultyAxis("伤害降低", 2, 40, 2),
    TrialDifficultyAxis("攻击提升", 2, 40, 2),
    TrialDifficultyAxis("暴击", 3, 60, 3),
    TrialDifficultyAxis("致命抵御", 2, 40, 2),
    TrialDifficultyAxis("攻击频率", 10, 200, 10),
)
TRIAL_DIFFICULTY_MIN_LEVEL = TRIAL_DIFFICULTY_BASE_LEVEL + len(TRIAL_DIFFICULTY_AXES)
TRIAL_DIFFICULTY_MAX_LEVEL = TRIAL_DIFFICULTY_BASE_LEVEL + sum(
    axis.max_position for axis in TRIAL_DIFFICULTY_AXES
)


@dataclass(frozen=True)
class TrialDifficultyPlan:
    """一个目标等级对应的五项档位与百分比。"""

    level: int
    positions: tuple[int, ...]
    values: tuple[int, ...]


@dataclass(frozen=True)
class ObservedTrialDifficulty:
    """从当前真实画面读取到的难度及其原始 OCR 文本。"""

    level: int
    text: str


def build_even_trial_difficulty_plan(level: int) -> TrialDifficultyPlan:
    """按“均匀分配、余数优先前项”生成目标难度计划。

    难度不是五项档位的简单和，而是固定再加 1。每项至少 1 档，因此合法
    等级范围当前为 6..101。例如 25 级得到 ``(5, 5, 5, 5, 4)``，26 级
    得到 ``(5, 5, 5, 5, 5)``。

    :param int level: 希望配置的最终难度等级。
    :return TrialDifficultyPlan: 五项档位和对应百分比。
    """

    level = int(level)
    distributable = level - TRIAL_DIFFICULTY_BASE_LEVEL
    axis_count = len(TRIAL_DIFFICULTY_AXES)
    minimum_level = TRIAL_DIFFICULTY_MIN_LEVEL
    maximum_level = TRIAL_DIFFICULTY_MAX_LEVEL
    if level < minimum_level or level > maximum_level:
        raise ValueError(f"试炼难度 {level} 超出 {minimum_level}..{maximum_level}")

    quotient, remainder = divmod(distributable, axis_count)
    positions = tuple(quotient + (1 if index < remainder else 0) for index in range(axis_count))
    values = tuple(axis.value_at(position) for axis, position in zip(TRIAL_DIFFICULTY_AXES, positions))
    return TrialDifficultyPlan(level=level, positions=positions, values=values)


def find_current_trial_difficulty(lines: Iterable[dict[str, Any]]) -> ObservedTrialDifficulty | None:
    """从 OCR 行中读取“当前难度为 N 级”。

    该读数是本轮 ``当前+1`` 策略的事实起点，不用于推算各滑杆当前百分比；
    每根滑杆仍由自己的标题百分比做闭环校验。
    """

    for line in lines:
        text = re.sub(r"\s+", "", str(line.get("text") or "")).translate(_FULLWIDTH_DIGITS)
        match = re.search(r"当前难度(?:为)?(\d{1,3})级", text)
        if match:
            return ObservedTrialDifficulty(level=int(match.group(1)), text=text)
    return None
