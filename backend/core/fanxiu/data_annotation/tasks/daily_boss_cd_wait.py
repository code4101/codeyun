"""Pure time policy for the watched-boss refresh countdown.

The normal challenge flow schedules the next check ``lead_seconds`` before a
known refresh so the follow-up run can arrive inside a bounded in-list wait
instead of naturally landing after the refresh.  These helpers are pure so the
time decisions can be tested without any game/Kernel interaction.
"""

from __future__ import annotations

DAILY_BOSS_CD_LEAD_SECONDS = 150
DAILY_BOSS_CD_WAIT_CAP_SECONDS = 180.0
DAILY_BOSS_CD_MIN_RECHECK_SECONDS = 60
DAILY_BOSS_CD_UNREADABLE_TIMEOUT_SECONDS = 30.0
DAILY_BOSS_CD_UNREADABLE_RECHECK_SECONDS = 1800
DAILY_BOSS_CD_READY_STREAK = 2


def daily_boss_cd_early_recheck_seconds(
    cd_seconds: int,
    *,
    lead_seconds: int = DAILY_BOSS_CD_LEAD_SECONDS,
    min_recheck_seconds: int = DAILY_BOSS_CD_MIN_RECHECK_SECONDS,
) -> int:
    """Schedule a recheck ``lead_seconds`` before a known refresh countdown."""

    return max(int(min_recheck_seconds), int(cd_seconds) - int(lead_seconds))


def daily_boss_cd_wait_decision(
    *,
    refresh_identifier_present: bool,
    missing_refresh_streak: int,
    cd_seconds: int | None,
    wait_elapsed_seconds: float,
    unreadable_elapsed_seconds: float | None,
    wait_cap_seconds: float = DAILY_BOSS_CD_WAIT_CAP_SECONDS,
    unreadable_timeout_seconds: float = DAILY_BOSS_CD_UNREADABLE_TIMEOUT_SECONDS,
    unreadable_recheck_seconds: int = DAILY_BOSS_CD_UNREADABLE_RECHECK_SECONDS,
    lead_seconds: int = DAILY_BOSS_CD_LEAD_SECONDS,
    min_recheck_seconds: int = DAILY_BOSS_CD_MIN_RECHECK_SECONDS,
    ready_streak: int = DAILY_BOSS_CD_READY_STREAK,
) -> tuple[str, int | None]:
    """Pure decision for the bounded in-list wait on a watched boss CD.

    ``action`` is one of ``ready`` (the refresh field disappeared on the
    required number of verified frames), ``wait`` (keep waiting inside the one
    absolute cap), ``recheck_early`` (a known countdown still lies beyond the
    lead window, so schedule ``lead_seconds`` early), ``recheck_unreadable``
    (the original 30-second unparsable policy) or ``recheck_floor`` (the
    absolute cap was reached with no readable countdown, so fall back to the
    minimum deferral).  Scheduled actions return the deferral in seconds.
    """

    if (
        not refresh_identifier_present
        and int(missing_refresh_streak) >= int(ready_streak)
    ):
        return "ready", None
    if (
        refresh_identifier_present
        and cd_seconds is not None
        and int(cd_seconds) > int(lead_seconds)
    ):
        return "recheck_early", daily_boss_cd_early_recheck_seconds(
            int(cd_seconds),
            lead_seconds=lead_seconds,
            min_recheck_seconds=min_recheck_seconds,
        )
    if (
        refresh_identifier_present
        and cd_seconds is None
        and unreadable_elapsed_seconds is not None
        and float(unreadable_elapsed_seconds) >= float(unreadable_timeout_seconds)
    ):
        return "recheck_unreadable", int(unreadable_recheck_seconds)
    if float(wait_elapsed_seconds) >= float(wait_cap_seconds):
        if cd_seconds is not None:
            return "recheck_early", daily_boss_cd_early_recheck_seconds(
                int(cd_seconds),
                lead_seconds=lead_seconds,
                min_recheck_seconds=min_recheck_seconds,
            )
        return "recheck_floor", int(min_recheck_seconds)
    return "wait", None
