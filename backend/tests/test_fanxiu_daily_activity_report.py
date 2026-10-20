from __future__ import annotations

from datetime import date, datetime

from backend.core.fanxiu.activity.daily_activity_report import (
    build_daily_activity_report,
    resolve_ranking_identity,
)
from backend.core.fanxiu.activity.ranking_lifecycle import (
    RankingActivityIdentity,
    ranking_activity_identities,
)


def test_ordinary_worldline_events_are_not_ranking_members():
    plan = _plan([{"name": "宗门镇邪", "schedule_id": 0,
                   "start_at": "2026-01-01T21:00:00+08:00", "end_at": "2026-01-01T21:30:00+08:00",
                   "raw": {"name": "宗门镇邪", "scheduleId": 0, "activityType": 6}}])
    result = _report(plan, _theme_plan(), date(2026, 1, 1))
    assert result["ready"]
    assert result["text"] == "玩法榜：无\n资源榜：无\n主题集：无"


def test_unknown_scheduled_identity_is_not_reported_as_absent():
    plan = _plan([{"name": "新活动", "schedule_id": 6400002,
                   "start_at": "2026-01-01T10:00:00+08:00", "end_at": "2026-01-01T22:00:00+08:00",
                   "raw": {"name": "新活动", "scheduleId": 6400002, "activityType": 99999}}])
    result = _report(plan, _theme_plan(), date(2026, 1, 1))
    assert not result["ready"]
    assert not result["text"]
    # Unverified row without a positive ranking match: catalog gap, still pending.
    assert result["diagnostics"][0]["kind"] == "activity_catalog_gap"
    assert result["diagnostics"][0]["activity"]["name"] == "新活动"
    assert result["diagnostics"][0]["activity"]["scheduleId"] == 6400002
    assert result["diagnostics"][0]["matching_identities"] == []


def test_real_worldline_schema_keeps_instances_and_excludes_grace_and_ordinary_events():
    rank = {"name": "灵宠竞武", "cross_count": 16, "schedule_id": 6400002,
            "start_at": "2026-10-08T05:00:05+08:00", "end_at": "2026-10-09T22:00:00+08:00",
            "close_panel_at": "2026-10-10T23:58:59+08:00",
            "raw": {"id": 16042901400004, "activityId": 16042901, "baseId": 42900,
                    "activityType": 12, "scheduleId": 6400002, "name": "灵宠竞武", "serverCount": 16}}
    second = {**rank, "key": "second-instance", "raw": {**rank["raw"], "id": 80429014000004, "serverCount": 8}}
    grace = {**rank, "start_at": "2026-10-07T05:00:05+08:00", "end_at": "2026-10-07T22:00:00+08:00"}
    ordinary = {"name": "联盟灵脉争夺", "schedule_id": 0,
                "start_at": "2026-10-05T01:00:15+08:00", "end_at": "2026-10-11T23:59:50+08:00",
                "raw": {"activityId": 2005, "activityType": 167, "scheduleId": 0, "name": "联盟灵脉争夺"}}
    result = _report(_plan([ordinary, grace, rank, dict(rank), second]), _theme_plan(), date(2026, 10, 9))
    assert result["ready"]
    assert result["text"] == "玩法榜：无\n资源榜：灵宠竞武 16跨 2/2、灵宠竞武 8跨 2/2\n主题集：无"


def _identity(family: str) -> RankingActivityIdentity:
    for identity in ranking_activity_identities():
        if identity.family == family and identity.names:
            return identity
    raise AssertionError(f"缺少 {family} 身份")


def _occurrence(
    identity: RankingActivityIdentity,
    *,
    name: str,
    start_at: datetime,
    end_at: datetime,
    cross: int = 1,
    server_count=None,
) -> dict:
    raw: dict = {"identityComplete": True, "name": identity.names[0]}
    if server_count == "omit":
        raw["serverCount"] = None
    else:
        raw["serverCount"] = cross
    occurrence = {
        "name": name,
        "display_name": name,
        "cross_count": cross,
        "start_at": start_at,
        "end_at": end_at,
        "raw": raw,
    }
    if server_count is not None and server_count != "omit":
        occurrence["raw"]["serverCount"] = server_count
    return occurrence


def _plan(occurrences, *, status: str = "ready") -> dict:
    return {
        "target_date": "2026-01-01",
        "timezone": "Asia/Shanghai",
        "status": status,
        "occurrences": list(occurrences),
    }


def _theme_plan(occurrences=(), *, plan_ready: bool = True, diagnostics=()) -> dict:
    return {
        "plan_ready": plan_ready,
        "occurrences": list(occurrences),
        "diagnostics": list(diagnostics),
    }


def _report(plan, theme_plan, business_date: date) -> dict:
    return build_daily_activity_report(plan, theme_plan, business_date=business_date)


def test_two_day_ranking_reports_second_of_two() -> None:
    identity = _identity("gameplay_rank")
    occurrence = _occurrence(
        identity,
        name="测试玩法",
        start_at=datetime(2026, 1, 1, 10, 0),
        end_at=datetime(2026, 1, 2, 23, 0),
        cross=8,
    )
    result = _report(_plan([occurrence]), _theme_plan(), date(2026, 1, 2))
    assert result["ready"] is True
    assert result["text"] == "玩法榜：测试玩法 8跨 2/2\n资源榜：无\n主题集：无"


def test_single_day_omits_one_of_one() -> None:
    identity = _identity("gameplay_rank")
    occurrence = _occurrence(
        identity,
        name="测试玩法",
        start_at=datetime(2026, 1, 1, 10, 0),
        end_at=datetime(2026, 1, 1, 23, 0),
        cross=4,
    )
    result = _report(_plan([occurrence]), _theme_plan(), date(2026, 1, 1))
    assert result["text"] == "玩法榜：测试玩法 4跨\n资源榜：无\n主题集：无"
    assert "1/1" not in result["text"]


def test_unknown_cross_is_not_fabricated() -> None:
    identity = _identity("gameplay_rank")
    occurrence = _occurrence(
        identity,
        name="测试玩法",
        start_at=datetime(2026, 1, 1, 10, 0),
        end_at=datetime(2026, 1, 1, 23, 0),
        cross=1,
        server_count="omit",
    )
    result = _report(_plan([occurrence]), _theme_plan(), date(2026, 1, 1))
    assert result["text"] == "玩法榜：测试玩法\n资源榜：无\n主题集：无"
    assert "跨" not in result["text"]


def test_reward_retention_period_is_excluded() -> None:
    identity = _identity("gameplay_rank")
    occurrence = _occurrence(
        identity,
        name="测试玩法",
        start_at=datetime(2026, 1, 1, 10, 0),
        end_at=datetime(2026, 1, 2, 23, 0),
        cross=8,
    )
    occurrence["raw"]["closePanelTime"] = datetime(2026, 1, 4, 0, 30)
    result = _report(_plan([occurrence]), _theme_plan(), date(2026, 1, 3))
    assert result["ready"] is True
    assert result["text"] == "玩法榜：无\n资源榜：无\n主题集：无"


def test_end_midnight_boundary_uses_previous_day() -> None:
    identity = _identity("gameplay_rank")
    occurrence = _occurrence(
        identity,
        name="测试玩法",
        start_at=datetime(2026, 1, 1, 10, 0),
        end_at=datetime(2026, 1, 3, 0, 0),
        cross=8,
    )
    plan = _plan([occurrence])
    inside = _report(plan, _theme_plan(), date(2026, 1, 2))
    assert inside["text"] == "玩法榜：测试玩法 8跨 2/2\n资源榜：无\n主题集：无"
    after = _report(plan, _theme_plan(), date(2026, 1, 3))
    assert after["text"] == "玩法榜：无\n资源榜：无\n主题集：无"


def test_empty_and_complete_writes_none() -> None:
    result = _report(_plan([]), _theme_plan(), date(2026, 1, 1))
    assert result["ready"] is True
    assert result["text"] == "玩法榜：无\n资源榜：无\n主题集：无"
    assert result["business_date"] == date(2026, 1, 1)


def test_missing_or_conflicting_evidence_is_rejected() -> None:
    identity = _identity("gameplay_rank")
    unknown = _occurrence(
        identity,
        name="测试玩法",
        start_at=datetime(2026, 1, 1, 10, 0),
        end_at=datetime(2026, 1, 1, 23, 0),
        cross=8,
    )
    unknown["raw"] = {}
    missing = _report(_plan([unknown]), _theme_plan(), date(2026, 1, 1))
    assert missing["ready"] is False
    assert missing["text"] == ""
    assert missing["reason"]

    first = RankingActivityIdentity(
        activity_type="first",
        family="gameplay_rank",
        vo_types=("SharedVo",),
    )
    second = RankingActivityIdentity(
        activity_type="second",
        family="gameplay_rank",
        vo_types=("SharedVo",),
    )
    assert resolve_ranking_identity({"class": "SharedVo"}, (first, second)) is None

    unready_theme = _report(_plan([]), _theme_plan(diagnostics=["缺失"]), date(2026, 1, 1))
    assert unready_theme["ready"] is False
    assert unready_theme["text"] == ""


def test_unverified_type4_without_ranking_match_is_catalog_gap_pending():
    from backend.core.fanxiu.activity.daily_activity_report import pending_report_retry_minutes
    plan = _plan([{"name": "", "schedule_id": 6400002, "identity_complete": False, "start_at": "2026-01-01T10:00:00+08:00", "end_at": "2026-01-01T22:00:00+08:00",
                   "raw": {"activityId": 47008, "activityType": 4, "scheduleId": 6400002, "name": ""}}])
    result = _report(plan, _theme_plan(), date(2026, 1, 1))
    assert result["ready"] is False and result["text"] == ""
    assert result["diagnostics"][0]["kind"] == "activity_catalog_gap"
    assert result["diagnostics"][0]["matching_identities"] == []
    assert pending_report_retry_minutes(result) == 30


def test_registered_name_complete_but_unregistered_stays_ranking_unresolved():
    plan = _plan([{"name": "新活动", "schedule_id": 6400002, "identity_complete": True, "start_at": "2026-01-01T10:00:00+08:00", "end_at": "2026-01-01T22:00:00+08:00",
                   "raw": {"name": "新活动", "scheduleId": 6400002, "activityType": 99999}}])
    result = _report(plan, _theme_plan(), date(2026, 1, 1))
    assert not result["ready"]
    assert result["diagnostics"][0]["kind"] == "ranking_identity_unresolved"


def test_registered_identity_tie_fails_closed():
    rows = tuple(RankingActivityIdentity(activity_type=f"t{i}", family=f, vo_types=(), runtime_activity_types=(4242,))
                 for i, f in enumerate(("gameplay_rank", "resource_rank")))
    assert resolve_ranking_identity({"activityType": 4242}, rows) is None
    assert resolve_ranking_identity({"activityType": 4242}, rows[:1]) is rows[0]


def test_pending_retry_split_static_gap_and_loading():
    from backend.core.fanxiu.activity.daily_activity_report import pending_report_retry_minutes
    assert pending_report_retry_minutes({"status": "pending_evidence", "reason": "主题计划未就绪"}) == 5
    assert pending_report_retry_minutes({"diagnostics": [{"kind": "ranking_identity_unresolved"}]}) == 30
    assert pending_report_retry_minutes({"diagnostics": [{"kind": "activity_catalog_gap"}, {"kind": "other"}]}) == 5
