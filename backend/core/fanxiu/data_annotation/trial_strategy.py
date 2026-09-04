"""Pure business decisions for the two regular Xianqiao trial tracks."""

from __future__ import annotations

from typing import Literal


TrialTrack = Literal["higher", "lower"]


def choose_xianqiao_trial_sweep_track(
    *,
    higher_level: int | None,
    lower_level: int | None,
) -> dict[str, int | str | None]:
    """Choose the more valuable verified sweepable track.

    The higher track (A/黑凤王) has weight 1.  The lower track (B/血光)
    currently has weight 2.  A track without a verified sweepable level is
    ineligible.  Strict ``2B > A`` selects B; ties deliberately select A.
    """

    higher = None if higher_level is None else int(higher_level)
    lower = None if lower_level is None else int(lower_level)
    if higher is not None and higher < 1:
        raise ValueError("A 线路可扫荡等级必须为正数")
    if lower is not None and lower < 1:
        raise ValueError("B 线路可扫荡等级必须为正数")
    if higher is None and lower is None:
        raise ValueError("A、B 均无已验证可扫荡等级")

    higher_value = higher
    lower_value = None if lower is None else 2 * lower
    if higher is None:
        selected: TrialTrack = "lower"
    elif lower is None:
        selected = "higher"
    elif int(lower_value) > int(higher_value):
        selected = "lower"
    else:
        selected = "higher"
    return {
        "track": selected,
        "higher_level": higher,
        "lower_level": lower,
        "higher_value": higher_value,
        "lower_value": lower_value,
    }


__all__ = ["TrialTrack", "choose_xianqiao_trial_sweep_track"]
