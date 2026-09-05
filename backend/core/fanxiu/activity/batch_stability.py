from __future__ import annotations

"""Activity-neutral stability rule for adjacent positive batch samples."""

from fractions import Fraction
from typing import SupportsFloat


def positive_relative_change_is_stable(
    previous: SupportsFloat,
    current: SupportsFloat,
    *,
    maximum_change: Fraction = Fraction(1, 2),
) -> bool:
    """Use the established adjacent-sample relative-change convention.

    The denominator is deliberately the previous sample.  Keeping this rule
    here prevents activities from silently inventing different meanings for a
    "50% fluctuation".
    """

    before = Fraction(str(float(previous)))
    after = Fraction(str(float(current)))
    if before <= 0 or after <= 0:
        raise ValueError("批次稳定性比较要求两个正数样本")
    if maximum_change < 0:
        raise ValueError("批次稳定性阈值无效")
    return abs(after - before) / before <= maximum_change


__all__ = ["positive_relative_change_is_stable"]
