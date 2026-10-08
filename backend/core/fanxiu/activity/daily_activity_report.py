from __future__ import annotations

"""Pure daily group projection of authoritative activity periods.

Gameplay dates exclude preparation and claim grace; this report says which
activities exist, not whether their challenges or rewards remain unfinished.
"""

from datetime import date, datetime, time, timedelta
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

from backend.core.fanxiu.activity.ranking_lifecycle import (
    RankingActivityIdentity,
    ranking_activity_identities,
)
from backend.core.fanxiu.activity.theme_collection import theme_member_for_row

GAMEPLAY_FAMILY = "gameplay_rank"
RESOURCE_FAMILY = "resource_rank"

_MISSING = object()


def resolve_ranking_identity(
    raw: Mapping[str, Any],
    identities: Sequence[RankingActivityIdentity] | None = None,
) -> RankingActivityIdentity | None:
    rows = tuple(identities) if identities is not None else ranking_activity_identities()
    best_priority = 0
    winners: list[RankingActivityIdentity] = []
    for identity in rows:
        priority = identity.match_priority(raw)
        if priority <= 0:
            continue
        if priority > best_priority:
            best_priority = priority
            winners = [identity]
        elif priority == best_priority:
            winners.append(identity)
    if best_priority <= 0 or len(winners) != 1:
        return None
    return winners[0]


def build_daily_activity_report(
    plan: Mapping[str, Any],
    theme_plan: Mapping[str, Any],
    *,
    business_date: date | None = None,
) -> dict[str, Any]:
    resolved_date = _resolve_business_date(plan, business_date)

    if not isinstance(plan, Mapping) or plan.get("status") != "ready":
        return _rejected(resolved_date, "每日发现计划未就绪")
    if not isinstance(theme_plan, Mapping) or theme_plan.get("plan_ready") is not True:
        return _rejected(resolved_date, "主题计划未就绪")
    if theme_plan.get("diagnostics"):
        return _rejected(resolved_date, "主题计划诊断信息非空")
    if not isinstance(plan.get("occurrences"), (list, tuple)) or not isinstance(theme_plan.get("occurrences"), (list, tuple)):
        return _rejected(resolved_date, "活动实例清单缺失")
    if business_date is None and _coerce_date(plan.get("target_date")) is None:
        return _rejected(resolved_date, "活动业务日期缺失")

    timezone = _plan_timezone(plan)
    if timezone is None:
        return _rejected(resolved_date, "活动时区缺失或无效")

    gameplay: list[str] = []
    resource: list[str] = []
    seen_rank: set[str] = set()
    for occurrence in plan.get("occurrences") or ():
        if not isinstance(occurrence, Mapping):
            return _rejected(resolved_date, "榜单实例证据缺失")
        start_at = _coerce_datetime(occurrence.get("start_at"), timezone)
        end_at = _coerce_datetime(occurrence.get("end_at"), timezone)
        if start_at is None or end_at is None:
            return _rejected(resolved_date, "榜单时间证据缺失")
        if not _covers(start_at, end_at, resolved_date):
            continue
        raw = _merged_raw(occurrence)
        identities = ranking_activity_identities()
        priorities = [row.match_priority(raw) for row in identities]
        identity = resolve_ranking_identity(raw, identities)
        if identity is None:
            # A complete worldline also contains ordinary weekly/daily events.
            # They are not members of these two ranking families. An unknown
            # scheduled ranking or an ambiguous registered identity is unsafe.
            schedule_id = raw.get("scheduleId", occurrence.get("schedule_id"))
            if not any(priorities) and (
                (type(schedule_id) is int and schedule_id == 0)
                or theme_member_for_row(raw) is not None
            ):
                continue
            return _rejected(resolved_date, "榜单身份证据缺失或冲突")
        if occurrence.get("identity_complete") is False or occurrence.get("identity_conflict"):
            return _rejected(resolved_date, "榜单身份未核验")
        instance = str(occurrence.get("key") or (identity.activity_type, raw.get("id"), start_at, end_at))
        if instance in seen_rank:
            continue
        seen_rank.add(instance)
        item = _format_ranking_item(occurrence, raw, identity, start_at, end_at, resolved_date)
        if identity.family == GAMEPLAY_FAMILY:
            gameplay.append(item)
        elif identity.family == RESOURCE_FAMILY:
            resource.append(item)

    themes: list[str] = []
    seen_theme: set[Any] = set()
    for occurrence in theme_plan.get("occurrences") or ():
        if not isinstance(occurrence, Mapping):
            return _rejected(resolved_date, "主题实例证据缺失")
        name = occurrence.get("name")
        start_at = _coerce_datetime(occurrence.get("start_at"), timezone)
        end_at = _coerce_datetime(occurrence.get("end_at"), timezone)
        if not name or start_at is None or end_at is None:
            return _rejected(resolved_date, "主题实例证据缺失")
        if not _covers(start_at, end_at, resolved_date):
            continue
        key = occurrence.get("instance_key") or (occurrence.get("activity_id"), name, start_at, end_at)
        if key in seen_theme:
            continue
        seen_theme.add(key)
        themes.append(_format_theme_item(occurrence, start_at, end_at, resolved_date))

    text = "\n".join(
        (
            "玩法榜：" + ("、".join(gameplay) if gameplay else "无"),
            "资源榜：" + ("、".join(resource) if resource else "无"),
            "主题集：" + ("、".join(themes) if themes else "无"),
        )
    )
    return {
        "ready": True,
        "text": text,
        "business_date": resolved_date,
        "reason": None,
    }


def _rejected(business_date: date, reason: str) -> dict[str, Any]:
    return {
        "ready": False,
        "text": "",
        "business_date": business_date,
        "reason": reason,
    }


def _resolve_business_date(plan: Mapping[str, Any], business_date: date | None) -> date:
    if isinstance(business_date, datetime):
        return business_date.date()
    if isinstance(business_date, date):
        return business_date
    target = plan.get("target_date") if isinstance(plan, Mapping) else None
    parsed = _coerce_date(target)
    return parsed if parsed is not None else date.today()


def _coerce_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        text = value.strip()
        if len(text) >= 10:
            try:
                return date.fromisoformat(text[:10])
            except ValueError:
                return None
    return None


def _plan_timezone(plan: Mapping[str, Any]) -> ZoneInfo | None:
    name = plan.get("timezone") if isinstance(plan, Mapping) else None
    if isinstance(name, str) and name.strip():
        try:
            return ZoneInfo(name.strip())
        except Exception:
            return None
    return None


def _coerce_datetime(value: Any, timezone: ZoneInfo | None) -> datetime | None:
    if value is None or value is _MISSING:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone) if timezone and value.tzinfo else value.replace(tzinfo=timezone)
    if isinstance(value, date):
        return datetime.combine(value, time.min, tzinfo=timezone)
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return _from_epoch(float(value), timezone)
    text = str(value).strip()
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        number = None
    if number is not None:
        return _from_epoch(number, timezone)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None and timezone is not None:
        parsed = parsed.replace(tzinfo=timezone)
    elif parsed.tzinfo is not None and timezone is not None:
        parsed = parsed.astimezone(timezone)
    return parsed


def _from_epoch(value: float, timezone: ZoneInfo | None) -> datetime:
    if value > 1e11:
        value = value / 1000.0
    return datetime.fromtimestamp(value, tz=timezone)


def _merged_raw(occurrence: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(occurrence)
    raw = merged.pop("raw", None)
    if isinstance(raw, Mapping):
        merged.update(raw)
    return merged


def _covers(start_at: datetime, end_at: datetime, business_date: date) -> bool:
    return start_at.date() <= business_date <= _last_playable_date(end_at)


def _last_playable_date(end_at: datetime) -> date:
    last_day = end_at.date()
    if end_at.timetz().replace(tzinfo=None) == time.min:
        last_day -= timedelta(days=1)
    return last_day


def _progress(start_at: datetime, end_at: datetime, business_date: date) -> str | None:
    total = (_last_playable_date(end_at) - start_at.date()).days + 1
    if total <= 1:
        return None
    day = (business_date - start_at.date()).days + 1
    return f"{day}/{total}"


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    return None


def _cross_count(occurrence: Mapping[str, Any], raw: Mapping[str, Any]) -> int | None:
    if "serverCount" in raw:
        return _positive_int(raw.get("serverCount"))
    if "server_count" in raw:
        return _positive_int(raw.get("server_count"))
    return _positive_int(occurrence.get("cross_count"))


def _display_name(
    occurrence: Mapping[str, Any],
    raw: Mapping[str, Any],
    identity: RankingActivityIdentity,
) -> str:
    name = occurrence.get("display_name") or occurrence.get("name")
    if not name:
        name = identity.names[0] if identity.names else identity.activity_type
    name = str(name).strip()
    little_name = str(raw.get("littleName") or raw.get("little_name") or "")
    if "预赛" in little_name and "预赛" not in name:
        name = f"{name}（预赛）"
    return name


def _format_ranking_item(
    occurrence: Mapping[str, Any],
    raw: Mapping[str, Any],
    identity: RankingActivityIdentity,
    start_at: datetime,
    end_at: datetime,
    business_date: date,
) -> str:
    parts = [_display_name(occurrence, raw, identity)]
    cross = _cross_count(occurrence, raw)
    if cross is not None:
        parts.append(f"{cross}跨")
    progress = _progress(start_at, end_at, business_date)
    if progress is not None:
        parts.append(progress)
    return " ".join(parts)


def _format_theme_item(
    occurrence: Mapping[str, Any],
    start_at: datetime,
    end_at: datetime,
    business_date: date,
) -> str:
    parts = [str(occurrence.get("name")).strip()]
    cross = _positive_int(occurrence.get("cross_count"))
    if cross is not None:
        parts.append(f"{cross}跨")
    progress = _progress(start_at, end_at, business_date)
    if progress is not None:
        parts.append(progress)
    return " ".join(parts)
