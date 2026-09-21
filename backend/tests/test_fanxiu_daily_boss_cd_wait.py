from backend.core.fanxiu.data_annotation.tasks.daily_boss_cd_wait import (
    DAILY_BOSS_CD_LEAD_SECONDS,
    DAILY_BOSS_CD_MIN_RECHECK_SECONDS,
    DAILY_BOSS_CD_READY_STREAK,
    DAILY_BOSS_CD_UNREADABLE_RECHECK_SECONDS,
    daily_boss_cd_early_recheck_seconds,
    daily_boss_cd_wait_decision,
)


def test_daily_boss_cd_early_recheck_leads_and_keeps_floor() -> None:
    assert daily_boss_cd_early_recheck_seconds(527) == 527 - DAILY_BOSS_CD_LEAD_SECONDS
    assert daily_boss_cd_early_recheck_seconds(1800) == 1650
    assert daily_boss_cd_early_recheck_seconds(200) == 60
    assert daily_boss_cd_early_recheck_seconds(151) == DAILY_BOSS_CD_MIN_RECHECK_SECONDS
    assert daily_boss_cd_early_recheck_seconds(150) == DAILY_BOSS_CD_MIN_RECHECK_SECONDS


def test_daily_boss_cd_wait_decision_short_cd_waits() -> None:
    assert daily_boss_cd_wait_decision(
        refresh_identifier_present=True,
        missing_refresh_streak=0,
        cd_seconds=120,
        wait_elapsed_seconds=10.0,
        unreadable_elapsed_seconds=None,
    ) == ("wait", None)
    assert daily_boss_cd_wait_decision(
        refresh_identifier_present=True,
        missing_refresh_streak=0,
        cd_seconds=1,
        wait_elapsed_seconds=179.9,
        unreadable_elapsed_seconds=None,
    ) == ("wait", None)


def test_daily_boss_cd_wait_decision_long_cd_schedules_early() -> None:
    assert daily_boss_cd_wait_decision(
        refresh_identifier_present=True,
        missing_refresh_streak=0,
        cd_seconds=527,
        wait_elapsed_seconds=0.0,
        unreadable_elapsed_seconds=None,
    ) == ("recheck_early", 377)


def test_daily_boss_cd_wait_decision_reset_round_schedules_early() -> None:
    assert daily_boss_cd_wait_decision(
        refresh_identifier_present=True,
        missing_refresh_streak=0,
        cd_seconds=1800,
        wait_elapsed_seconds=12.0,
        unreadable_elapsed_seconds=None,
    ) == ("recheck_early", 1650)


def test_daily_boss_cd_wait_decision_hard_cap_is_absolute() -> None:
    assert daily_boss_cd_wait_decision(
        refresh_identifier_present=True,
        missing_refresh_streak=0,
        cd_seconds=100,
        wait_elapsed_seconds=180.0,
        unreadable_elapsed_seconds=None,
    ) == ("recheck_early", 60)
    assert daily_boss_cd_wait_decision(
        refresh_identifier_present=True,
        missing_refresh_streak=1,
        cd_seconds=None,
        wait_elapsed_seconds=180.0,
        unreadable_elapsed_seconds=None,
    ) == ("recheck_floor", 60)
    assert daily_boss_cd_wait_decision(
        refresh_identifier_present=False,
        missing_refresh_streak=1,
        cd_seconds=None,
        wait_elapsed_seconds=999.0,
        unreadable_elapsed_seconds=None,
    ) == ("recheck_floor", 60)


def test_daily_boss_cd_wait_decision_requires_two_verified_frames() -> None:
    assert daily_boss_cd_wait_decision(
        refresh_identifier_present=False,
        missing_refresh_streak=1,
        cd_seconds=None,
        wait_elapsed_seconds=5.0,
        unreadable_elapsed_seconds=None,
    ) == ("wait", None)
    assert daily_boss_cd_wait_decision(
        refresh_identifier_present=False,
        missing_refresh_streak=DAILY_BOSS_CD_READY_STREAK,
        cd_seconds=None,
        wait_elapsed_seconds=5.0,
        unreadable_elapsed_seconds=None,
    ) == ("ready", None)


def test_daily_boss_cd_wait_decision_blank_ocr_is_not_ready() -> None:
    assert daily_boss_cd_wait_decision(
        refresh_identifier_present=False,
        missing_refresh_streak=0,
        cd_seconds=None,
        wait_elapsed_seconds=5.0,
        unreadable_elapsed_seconds=None,
    ) == ("wait", None)


def test_daily_boss_cd_wait_decision_keeps_unreadable_policy() -> None:
    assert daily_boss_cd_wait_decision(
        refresh_identifier_present=True,
        missing_refresh_streak=0,
        cd_seconds=None,
        wait_elapsed_seconds=10.0,
        unreadable_elapsed_seconds=29.9,
    ) == ("wait", None)
    assert daily_boss_cd_wait_decision(
        refresh_identifier_present=True,
        missing_refresh_streak=0,
        cd_seconds=None,
        wait_elapsed_seconds=10.0,
        unreadable_elapsed_seconds=30.0,
    ) == ("recheck_unreadable", DAILY_BOSS_CD_UNREADABLE_RECHECK_SECONDS)
