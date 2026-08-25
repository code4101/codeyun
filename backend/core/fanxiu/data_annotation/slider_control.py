"""离散滑杆和增量点数控件的通用规则。

本模块是无设备副作用的控件模型层：

* ``DiscreteSliderScale`` 负责百分比、档位与拖拽横坐标换算；
* ``find_labeled_percentage`` 负责从实时 OCR 文本解析某一滑杆的当前值；
* ``BalancedPointState`` 负责两个增量属性的累计均衡选择。

截图、OCR 缓存、点击、拖拽和动作后复核属于 ``BehaviorTreeRuntime``。仙窍试炼
“当前难度 + 1”等业务含义属于 ``trial_difficulty``，不要反向塞进控件层。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable


_FULLWIDTH_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")


@dataclass(frozen=True)
class BalancedPointState:
    """两个属性的剩余点数与累计增量状态。

    比较的是从共同最小值开始已经投入的档位数，不是只把“本次剩余点数”
    对半分。较少的一项优先；相同则优先第一项。
    """

    remaining: int
    first_value: int
    second_value: int
    minimum: int = 10
    step: int = 10

    def __post_init__(self) -> None:
        if self.remaining < 0:
            raise ValueError("剩余点数不能为负数")
        if self.step <= 0:
            raise ValueError("属性步长必须为正数")
        for label, value in (("第一项", self.first_value), ("第二项", self.second_value)):
            if value < self.minimum or (value - self.minimum) % self.step:
                raise ValueError(
                    f"{label}当前值 {value}% 无效；应不小于 {self.minimum}% 且步长为 {self.step}%"
                )

    @property
    def first_points(self) -> int:
        return (self.first_value - self.minimum) // self.step

    @property
    def second_points(self) -> int:
        return (self.second_value - self.minimum) // self.step

    def next_target(self) -> str | None:
        """Choose the less-filled item; ties favor the first item."""

        if self.remaining == 0:
            return None
        return "first" if self.first_points <= self.second_points else "second"


@dataclass(frozen=True)
class DiscreteSliderScale:
    minimum: int
    maximum: int
    step: int

    def __post_init__(self) -> None:
        if self.step <= 0 or self.maximum < self.minimum:
            raise ValueError("滑杆范围无效")
        if (self.maximum - self.minimum) % self.step:
            raise ValueError("滑杆最大值必须落在步长网格上")

    @property
    def position_count(self) -> int:
        return (self.maximum - self.minimum) // self.step + 1

    def index(self, value: int) -> int:
        value = int(value)
        if value < self.minimum or value > self.maximum or (value - self.minimum) % self.step:
            raise ValueError(
                f"滑杆值 {value} 无效；应在 {self.minimum}..{self.maximum} 且步长为 {self.step}"
            )
        return (value - self.minimum) // self.step

    def center_x(self, box: dict[str, Any], value: int) -> float:
        left = float(box.get("x") or 0)
        width = float(box.get("w") or 0)
        height = float(box.get("h") or 0)
        if width <= 0 or height <= 0:
            raise ValueError("滑杆标注框无效")
        # The shape encloses the thumb at both extremes.  Its half-height is a
        # stable estimate of the thumb radius and therefore of the center inset.
        inset = min(width / 2, height / 2)
        start = left + inset
        end = left + width - inset
        if self.position_count == 1:
            return (start + end) / 2
        return start + (end - start) * self.index(value) / (self.position_count - 1)

    def drag_points(
        self,
        box: dict[str, Any],
        current: int,
        target: int,
    ) -> tuple[float, float, float, float]:
        center_y = float(box.get("y") or 0) + float(box.get("h") or 0) / 2
        return self.center_x(box, current), center_y, self.center_x(box, target), center_y


@dataclass(frozen=True)
class LabeledPercentage:
    value: int
    text: str
    line: dict[str, Any]


def find_labeled_percentage(lines: Iterable[dict[str, Any]], label: str) -> LabeledPercentage | None:
    """Locate ``label ... [N%]`` and retain its OCR geometry."""

    compact_label = re.sub(r"\s+", "", str(label or "")).translate(_FULLWIDTH_DIGITS)
    if not compact_label:
        raise ValueError("滑杆标题不能为空")
    for line in lines:
        text = re.sub(r"\s+", "", str(line.get("text") or "")).translate(_FULLWIDTH_DIGITS)
        label_at = text.find(compact_label)
        if label_at < 0:
            continue
        tail = text[label_at + len(compact_label):]
        match = re.search(r"[【\[]?\s*(\d{1,4})\s*%\s*[】\]]?", tail)
        if match:
            return LabeledPercentage(value=int(match.group(1)), text=text, line=line)
    return None


def read_labeled_percentage(lines: Iterable[dict[str, Any]], label: str) -> tuple[int, str] | None:
    observation = find_labeled_percentage(lines, label)
    return (observation.value, observation.text) if observation is not None else None
