from __future__ import annotations

"""Pure Theme Collection (主题集) planning for one first-level Scheduler Job.

The daily activity reader owns Runtime facts.  This module is the execution
authority projection: it maps the actual activity list occurrences of every
non-ranking theme activity into one canonical ``theme-collection`` Job and
reduces their coexisting instances/stages to the earliest valid pending
``next_time``.

Design constraints (see the module tests):

* One theme may own several simultaneously open instances.
* One instance may own several coexisting stages (``prepare``/``active``/
  ``tail``); a stage is only ever a plan, never a guessed weekday.
* Completion is isolated by ``instance_key + member_id + stage_kind +
  business_date`` so a new instance or a new business day never reuses an old
  completion.
* No stage executes until its kind is explicitly promoted into
  :data:`PRODUCTION_THEME_STAGE_KINDS`. Unlisted stages remain disabled.
* Stage timing lives here; existing member components still own gameplay.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

from backend.core.fanxiu.activity.authorized_activity_lifecycle import (
    AUTHORIZED_ACTIVITY_LIFECYCLE_SPECS,
    build_activity_lifecycle_scheduler_migration,
)
from backend.core.fanxiu.activity.daily_activity_discovery import DEFAULT_TIMEZONE


THEME_COLLECTION_TASK_ID = "theme-collection"
THEME_COLLECTION_TASK_TYPE = "theme_collection"
THEME_COLLECTION_LABEL = "主题集"

STAGE_PREPARE = "prepare"
STAGE_ACTIVE = "active"
STAGE_TAIL = "tail"
THEME_STAGES = (STAGE_PREPARE, STAGE_ACTIVE, STAGE_TAIL)

DAY_SCOPE_START_DAY = "start_day"
DAY_SCOPE_DAILY = "daily"
DAY_SCOPE_LAST_DAY = "last_day"
DAY_SCOPES = (DAY_SCOPE_START_DAY, DAY_SCOPE_DAILY, DAY_SCOPE_LAST_DAY)

# Stage kinds whose live gameplay has passed acceptance and may therefore run.
# 仙缘送礼 (``visit_xianzun_2200``) is the only ending whose cover stays usable
# after the mother instance's authoritative ``end_at`` (until the last playable
# day's midnight).  It therefore opts into the explicit after-end strategy below
# instead of the normal ``start_at <= due_at < end_at`` window.
HOLY_WOOD_DAILY_KIND = "holy_wood_prayer_0000"
HOLY_WOOD_TAIL_KIND = "holy_wood_prayer_2100"
GARDEN_BANQUET_DAILY_KINDS = (
    "garden_banquet_1000",
    "garden_banquet_1200",
    "garden_banquet_1400",
    "garden_banquet_1600",
    "garden_banquet_1800",
    "garden_banquet_2000",
)
GARDEN_BANQUET_TAIL_KIND = "garden_banquet_2100"
VISIT_XIANZUN_KIND = "visit_xianzun_2200"

PRODUCTION_THEME_STAGE_KINDS: frozenset[str] = frozenset(
    {
        HOLY_WOOD_DAILY_KIND,
        HOLY_WOOD_TAIL_KIND,
        *GARDEN_BANQUET_DAILY_KINDS,
        GARDEN_BANQUET_TAIL_KIND,
        VISIT_XIANZUN_KIND,
        "xianzang_config_0005",
        "xianzang_lottery_2110",
        "kunlun_config_0005",
        "kunlun_lottery_2110",
        "lingxiao_start_0005",
        "lingxiao_tail_2110",
        "wanbao_start_0005",
        "wanbao_tail_2110",
    }
)

# Legacy first-level entries that the canonical theme Job subsumes.  They may
# only be removed through the atomic Scheduler migration after all three gates
# in ``build_activity_lifecycle_scheduler_migration`` pass.  This module never
# writes Scheduler membership itself.
THEME_COLLECTION_INTERNALIZED_TASK_IDS = frozenset(
    {
        "penglai-xianzang",
        "penglai-xianzang-config",
        "penglai-xianzang-lottery",
        "kunlun-secret",
        "kunlun-secret-config",
        "kunlun-secret-lottery",
        "lingxiao-xianhui",
        "wanbao-zhenbao",
        "holy-wood-prayer",
        "xianyan-host-baihua",
        "xianyan-participation",
        "xianyan-rewards",
    }
)
THEME_COLLECTION_INTERNALIZED_TASK_TYPES = frozenset(
    {
        "penglai_xianzang",
        "penglai_xianzang_config",
        "penglai_xianzang_lottery",
        "kunlun_secret",
        "kunlun_secret_config",
        "kunlun_secret_lottery",
        "lingxiao_xianhui",
        "wanbao_zhenbao",
        "holy_wood_prayer",
        "xianyan_host_baihua",
        "xianyan_participation",
        "xianyan_rewards",
    }
)


@dataclass(frozen=True)
class ThemeStageRule:
    """One declarative stage of one theme member.

    ``trigger`` is an absolute local wall-clock time combined with the actual
    occurrence dates; it is never a weekday recurrence.

    ``recurrence_group`` marks rules that share one intraday recurrence family
    (for example the 12:00/14:00/16:00 garden slots).  When a whole family has
    been missed on the current day, only the latest member of the group runs;
    earlier missed rounds are never replayed.
    """

    member_id: str
    stage: str
    kind: str
    day_scope: str
    trigger: time
    recurrence_group: str = ""
    deadline: time | None = None
    # Explicit strategy: the cover may still be finished after the mother
    # instance's authoritative ``end_at``.  Only rules that opt in survive the
    # per-instance expiry filter, and they end at the last playable day's
    # midnight.  General stages never revive.
    allow_after_end: bool = False

    def __post_init__(self) -> None:
        if self.stage not in THEME_STAGES:
            raise ValueError(f"主题集未知阶段：{self.stage}")
        if self.day_scope not in DAY_SCOPES:
            raise ValueError(f"主题集未知日期范围：{self.day_scope}")
        if not self.member_id or not self.kind:
            raise ValueError("主题集阶段缺少成员或阶段身份")
        if self.recurrence_group and self.day_scope not in (DAY_SCOPE_DAILY, DAY_SCOPE_LAST_DAY):
            raise ValueError("主题集重复组只能用于每日或最后日阶段")
        if self.allow_after_end and self.day_scope != DAY_SCOPE_LAST_DAY:
            raise ValueError("主题集 after-end 阶段只能用于最后日阶段")


@dataclass(frozen=True)
class ThemeMemberSpec:
    member_id: str
    names: frozenset[str]
    executor_task_id: str
    stage_rules: tuple[ThemeStageRule, ...]
    implemented: bool = False
    delegated_lifecycle: bool = False
    activity_ids: tuple[int, ...] = ()
    base_ids: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if not self.member_id or not self.names or not self.executor_task_id:
            raise ValueError("主题集成员身份不完整")

    def matches(self, raw: Mapping[str, Any]) -> bool:
        name = str(raw.get("name") or "").strip()
        if name and name in self.names:
            return True
        activity_id = _as_int(raw.get("activity_id"))
        if activity_id is not None and activity_id in self.activity_ids:
            return True
        base_id = _as_int(raw.get("base_id"))
        if base_id is not None and base_id in self.base_ids:
            return True
        return False


@dataclass(frozen=True)
class ThemeOccurrence:
    member_id: str
    activity_id: int
    name: str
    instance_key: str
    start_at: datetime
    end_at: datetime
    close_at: datetime

    @property
    def last_playable_date(self) -> date:
        last_day = self.end_at.date()
        if self.end_at.timetz().replace(tzinfo=None) == time.min:
            last_day -= timedelta(days=1)
        return last_day


@dataclass(frozen=True)
class ThemeCheckpoint:
    instance_key: str
    member_id: str
    stage: str
    kind: str
    business_date: str
    due_at: datetime
    recurrence_group: str = ""
    deadline_at: datetime | None = None

    @property
    def key(self) -> tuple[str, str, str, str]:
        return (self.instance_key, self.member_id, self.kind, self.business_date)


# The four already-authorized activities stop being separate Jobs and become
# stage rules of the single canonical theme Job.  Their trigger times are the
# existing daily-binding semantics (config/start at 00:05, evening tail/lottery
# at 21:10); existence still comes only from a real occurrence or authority row.
_DELEGATED_STAGE_RULES: dict[str, tuple[ThemeStageRule, ...]] = {
    "penglai-xianzang": (
        ThemeStageRule(
            member_id="penglai-xianzang",
            stage=STAGE_ACTIVE,
            kind="xianzang_config_0005",
            day_scope=DAY_SCOPE_DAILY,
            trigger=time(0, 5),
        ),
        ThemeStageRule(
            member_id="penglai-xianzang",
            stage=STAGE_TAIL,
            kind="xianzang_lottery_2110",
            day_scope=DAY_SCOPE_LAST_DAY,
            trigger=time(21, 10),
        ),
    ),
    "kunlun-secret": (
        ThemeStageRule(
            member_id="kunlun-secret",
            stage=STAGE_ACTIVE,
            kind="kunlun_config_0005",
            day_scope=DAY_SCOPE_DAILY,
            trigger=time(0, 5),
        ),
        ThemeStageRule(
            member_id="kunlun-secret",
            stage=STAGE_TAIL,
            kind="kunlun_lottery_2110",
            day_scope=DAY_SCOPE_LAST_DAY,
            trigger=time(21, 10),
        ),
    ),
    "lingxiao-xianhui": (
        ThemeStageRule(
            member_id="lingxiao-xianhui",
            stage=STAGE_ACTIVE,
            kind="lingxiao_start_0005",
            day_scope=DAY_SCOPE_START_DAY,
            trigger=time(0, 5),
        ),
        ThemeStageRule(
            member_id="lingxiao-xianhui",
            stage=STAGE_TAIL,
            kind="lingxiao_tail_2110",
            day_scope=DAY_SCOPE_LAST_DAY,
            trigger=time(21, 10),
        ),
    ),
    "wanbao-zhenbao": (
        ThemeStageRule(
            member_id="wanbao-zhenbao",
            stage=STAGE_ACTIVE,
            kind="wanbao_start_0005",
            day_scope=DAY_SCOPE_START_DAY,
            trigger=time(0, 5),
        ),
        ThemeStageRule(
            member_id="wanbao-zhenbao",
            stage=STAGE_TAIL,
            kind="wanbao_tail_2110",
            day_scope=DAY_SCOPE_LAST_DAY,
            trigger=time(21, 10),
        ),
    ),
}


def _delegated_members() -> tuple[ThemeMemberSpec, ...]:
    """Internalize the authorized activities as stage rules of the one Job."""

    return tuple(
        ThemeMemberSpec(
            member_id=spec.task_id,
            names=spec.names,
            executor_task_id=spec.task_id,
            stage_rules=_DELEGATED_STAGE_RULES.get(spec.task_id, ()),
            implemented=True,
            delegated_lifecycle=True,
        )
        for spec in AUTHORIZED_ACTIVITY_LIFECYCLE_SPECS
    )


# The 仙园游宴 main instance owns 圣木祈愿 / 园中仙宴 / 寻访仙尊 as three
# stages of one instance.  It is published from ``ActivityMgr``'s activation
# dictionary (activity 304, base 118000), not as three separate #66 rows.
XIANYUAN_BANQUET_ACTIVITY_ID = 304
XIANYUAN_BANQUET_BASE_ID = 118000


THEME_COLLECTION_MEMBERS: tuple[ThemeMemberSpec, ...] = (
    ThemeMemberSpec(
        member_id="xianyuan-banquet",
        names=frozenset({"仙园游宴", "仙宴"}),
        executor_task_id="xianyuan-banquet",
        activity_ids=(XIANYUAN_BANQUET_ACTIVITY_ID,),
        base_ids=(XIANYUAN_BANQUET_BASE_ID,),
        stage_rules=(
            ThemeStageRule(
                member_id="xianyuan-banquet",
                stage=STAGE_ACTIVE,
                kind=HOLY_WOOD_DAILY_KIND,
                day_scope=DAY_SCOPE_DAILY,
                trigger=time(0, 0),
            ),
            ThemeStageRule(
                member_id="xianyuan-banquet",
                stage=STAGE_TAIL,
                kind=HOLY_WOOD_TAIL_KIND,
                day_scope=DAY_SCOPE_LAST_DAY,
                trigger=time(21, 0),
            ),
            ThemeStageRule(
                member_id="xianyuan-banquet",
                stage=STAGE_ACTIVE,
                kind="garden_banquet_1000",
                day_scope=DAY_SCOPE_DAILY,
                trigger=time(10, 0),
                recurrence_group="garden_daily",
                deadline=time(22, 0),
            ),
            ThemeStageRule(
                member_id="xianyuan-banquet",
                stage=STAGE_ACTIVE,
                kind="garden_banquet_1200",
                day_scope=DAY_SCOPE_DAILY,
                trigger=time(12, 0),
                recurrence_group="garden_daily",
                deadline=time(22, 0),
            ),
            ThemeStageRule(
                member_id="xianyuan-banquet",
                stage=STAGE_ACTIVE,
                kind="garden_banquet_1400",
                day_scope=DAY_SCOPE_DAILY,
                trigger=time(14, 0),
                recurrence_group="garden_daily",
                deadline=time(22, 0),
            ),
            ThemeStageRule(
                member_id="xianyuan-banquet",
                stage=STAGE_ACTIVE,
                kind="garden_banquet_1600",
                day_scope=DAY_SCOPE_DAILY,
                trigger=time(16, 0),
                recurrence_group="garden_daily",
                deadline=time(22, 0),
            ),
            ThemeStageRule(
                member_id="xianyuan-banquet",
                stage=STAGE_ACTIVE,
                kind="garden_banquet_1800",
                day_scope=DAY_SCOPE_DAILY,
                trigger=time(18, 0),
                recurrence_group="garden_daily",
                deadline=time(22, 0),
            ),
            ThemeStageRule(
                member_id="xianyuan-banquet",
                stage=STAGE_ACTIVE,
                kind="garden_banquet_2000",
                day_scope=DAY_SCOPE_DAILY,
                trigger=time(20, 0),
                recurrence_group="garden_daily",
                deadline=time(22, 0),
            ),
            # 最后日 21 点收尾包含常规整轮，替代当天尚未运行的较早轮次。
            # 独立 kind 保证已完成 20 点轮次仍会在 21 点再执行。
            ThemeStageRule(
                member_id="xianyuan-banquet",
                stage=STAGE_TAIL,
                kind=GARDEN_BANQUET_TAIL_KIND,
                day_scope=DAY_SCOPE_LAST_DAY,
                trigger=time(21, 0),
                recurrence_group="garden_daily",
                deadline=time(22, 0),
            ),
            # 寻访仙尊 / 仙缘送礼：母活动 end_at（如 22:00:01）之后封面仍可在当日
            # 24 点前收尾。仅此规则显式启用 after-end，且以真实实例为前提。
            ThemeStageRule(
                member_id="xianyuan-banquet",
                stage=STAGE_TAIL,
                kind=VISIT_XIANZUN_KIND,
                day_scope=DAY_SCOPE_LAST_DAY,
                trigger=time(22, 0),
                allow_after_end=True,
            ),
        ),
        implemented=True,
    ),
    ThemeMemberSpec(
        member_id="tianhe-xianhui",
        names=frozenset({"天河仙会"}),
        executor_task_id="tianhe-xianhui",
        stage_rules=(),
        implemented=False,
    ),
    ThemeMemberSpec(
        member_id="wanxiang-baoge",
        names=frozenset({"万象宝阁"}),
        executor_task_id="daily_activity_list_sync",
        stage_rules=(),
        implemented=False,
    ),
    *_delegated_members(),
)


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def theme_stage_is_production(kind: str) -> bool:
    return str(kind) in PRODUCTION_THEME_STAGE_KINDS


def _production_set(
    production_kinds: Iterable[str] | None,
) -> frozenset[str]:
    if production_kinds is None:
        return PRODUCTION_THEME_STAGE_KINDS
    return frozenset(str(value) for value in production_kinds if str(value or ""))


def _member_index() -> dict[str, ThemeMemberSpec]:
    return {spec.member_id: spec for spec in THEME_COLLECTION_MEMBERS}


def _stage_rule(spec: ThemeMemberSpec, kind: str) -> ThemeStageRule | None:
    for rule in spec.stage_rules:
        if rule.kind == kind:
            return rule
    return None


def _member_for_row(row: Mapping[str, Any]) -> ThemeMemberSpec | None:
    for spec in THEME_COLLECTION_MEMBERS:
        if spec.matches(row):
            return spec
    return None


def theme_member_for_row(row: Mapping[str, Any]) -> ThemeMemberSpec | None:
    """Public read-only lookup of the member a plan/authority row belongs to."""

    return _member_for_row(row)


def _epoch_ms_datetime(value: Any, tz: ZoneInfo) -> datetime | None:
    millis = _as_int(value)
    if millis is None or millis <= 0:
        return None
    return datetime.fromtimestamp(millis / 1000, tz)


def _runtime_ids(raw: Mapping[str, Any]) -> list[int]:
    return [
        parsed
        for raw_id in raw.get("runtime_ids") or []
        if (parsed := _as_int(raw_id)) is not None and parsed > 0
    ]


def _normalize_candidate(
    raw: Mapping[str, Any],
    *,
    source_kind: str,
    tz: ZoneInfo,
    certified: bool,
) -> dict[str, Any] | None:
    """Normalize one authority row without assuming a single Runtime source.

    Time authority may arrive as ISO strings (#66 / Revenue period) or as epoch
    milliseconds (``ActivityMgr`` activation dictionary).  A row without a
    positive activity id and both boundaries is retained only for diagnostics.
    """

    if not isinstance(raw, Mapping):
        return None
    activity_id = _as_int(raw.get("activity_id"))
    base_id = _as_int(raw.get("base_id"))
    start_at = _parse_datetime(raw.get("start_at")) or _epoch_ms_datetime(
        raw.get("start_time_ms"), tz
    )
    end_at = _parse_datetime(raw.get("end_at")) or _epoch_ms_datetime(
        raw.get("end_time_ms"), tz
    )
    close_at = (
        _parse_datetime(raw.get("close_panel_at"))
        or _epoch_ms_datetime(raw.get("close_panel_time_ms"), tz)
        or end_at
    )
    complete = bool(
        raw.get("identity_complete", certified)
        and activity_id is not None
        and activity_id > 0
        and start_at is not None
        and end_at is not None
    )
    return {
        "identity_complete": complete,
        "source_kind": str(raw.get("source_kind") or source_kind or "").strip(),
        "name": str(raw.get("name") or "").strip(),
        "activity_id": activity_id,
        "base_id": base_id,
        "start_at": start_at,
        "end_at": end_at,
        "close_at": close_at,
        "runtime_ids": _runtime_ids(raw),
    }


def iter_theme_source_candidates(
    plan: Mapping[str, Any],
    *,
    activation_occurrences: Iterable[Mapping[str, Any]] = (),
    authority_occurrences: Iterable[Mapping[str, Any]] = (),
    timezone_name: str = DEFAULT_TIMEZONE,
) -> tuple[dict[str, Any], ...]:
    """Normalize every authority-bearing row regardless of its Runtime source.

    The worldline ``#66`` list is one caller, not the only one: the 仙园游宴
    family is published from ``ActivityMgr``'s activation dictionary, and other
    themes may be observed through the Revenue activity observation list.
    Callers pass those already-read rows in; this module never opens the game.
    """

    tz = ZoneInfo(timezone_name)
    plan_source_kind = str(plan.get("source_kind") or "").strip()
    rows: list[dict[str, Any]] = []
    for raw in plan.get("occurrences") or []:
        candidate = _normalize_candidate(
            raw, source_kind=plan_source_kind, tz=tz, certified=False
        )
        if candidate is not None:
            rows.append(candidate)
    for raw in activation_occurrences or ():
        candidate = _normalize_candidate(
            raw,
            source_kind="activity_activation_runtime_memory",
            tz=tz,
            certified=True,
        )
        if candidate is not None:
            rows.append(candidate)
    for raw in authority_occurrences or ():
        candidate = _normalize_candidate(
            raw,
            source_kind=str(raw.get("source_kind") or "authority_occurrence"),
            tz=tz,
            certified=True,
        )
        if candidate is not None:
            rows.append(candidate)
    return tuple(rows)


def discover_theme_occurrences(
    plan: Mapping[str, Any],
    *,
    activation_occurrences: Iterable[Mapping[str, Any]] = (),
    authority_occurrences: Iterable[Mapping[str, Any]] = (),
    timezone_name: str = DEFAULT_TIMEZONE,
) -> tuple[ThemeOccurrence, ...]:
    """Map complete authority rows to the members they actually belong to."""

    result: list[ThemeOccurrence] = []
    seen: set[str] = set()
    candidates = iter_theme_source_candidates(
        plan,
        activation_occurrences=activation_occurrences,
        authority_occurrences=authority_occurrences,
        timezone_name=timezone_name,
    )
    # A period-only source is evidence for the identified instances, not an
    # additional instance with synthetic runtime ID 0. Preserve distinct real
    # runtime IDs even when they share a period.
    identified_periods = {
        (row["activity_id"], row["start_at"], row["end_at"])
        for row in candidates
        if row["identity_complete"] and any(row["runtime_ids"])
    }
    for candidate in candidates:
        if not candidate["identity_complete"]:
            continue
        if not any(candidate["runtime_ids"]) and (
            candidate["activity_id"], candidate["start_at"], candidate["end_at"]
        ) in identified_periods:
            continue
        spec = _member_for_row(candidate)
        if spec is None:
            continue
        activity_id = candidate["activity_id"]
        start_at = candidate["start_at"]
        end_at = candidate["end_at"]
        runtime_id = candidate["runtime_ids"][0] if candidate["runtime_ids"] else 0
        instance_key = (
            f"theme:{spec.member_id}:{activity_id}:"
            f"{start_at.isoformat(timespec='seconds')}:"
            f"{end_at.isoformat(timespec='seconds')}:{runtime_id}"
        )
        if instance_key in seen:
            continue
        seen.add(instance_key)
        result.append(
            ThemeOccurrence(
                member_id=spec.member_id,
                activity_id=activity_id,
                name=candidate["name"] or spec.member_id,
                instance_key=instance_key,
                start_at=start_at,
                end_at=end_at,
                close_at=candidate["close_at"] or end_at,
            )
        )
    return tuple(result)


def theme_occurrence_diagnostics(
    plan: Mapping[str, Any],
    *,
    activation_occurrences: Iterable[Mapping[str, Any]] = (),
    authority_occurrences: Iterable[Mapping[str, Any]] = (),
    timezone_name: str = DEFAULT_TIMEZONE,
) -> list[dict[str, Any]]:
    """Non-executable evidence for members that lack complete time authority."""

    complete_members = {
        occurrence.member_id
        for occurrence in discover_theme_occurrences(
            plan,
            activation_occurrences=activation_occurrences,
            authority_occurrences=authority_occurrences,
            timezone_name=timezone_name,
        )
    }
    diagnostics: list[dict[str, Any]] = []
    seen: set[tuple[str, int | None]] = set()

    def record(member_id: str, raw: Mapping[str, Any], reason: str) -> None:
        activity_id = _as_int(raw.get("activity_id"))
        key = (member_id, activity_id)
        if key in seen:
            return
        seen.add(key)
        diagnostics.append(
            {
                "member_id": member_id,
                "name": str(raw.get("name") or "").strip(),
                "activity_id": activity_id,
                "status": "non_executable",
                "reason": reason,
            }
        )

    for raw in plan.get("activity_observations") or []:
        if not isinstance(raw, Mapping):
            continue
        spec = _member_for_row(raw)
        if spec is None or spec.member_id in complete_members:
            continue
        record(spec.member_id, raw, "missing_time_authority")
    for candidate in iter_theme_source_candidates(
        plan,
        activation_occurrences=activation_occurrences,
        authority_occurrences=authority_occurrences,
        timezone_name=timezone_name,
    ):
        spec = _member_for_row(candidate)
        if (
            spec is None
            or candidate["identity_complete"]
            or spec.member_id in complete_members
        ):
            continue
        record(spec.member_id, candidate, "incomplete_time_authority")
    return diagnostics


def _at(day: date, value: time, tz: Any) -> datetime:
    return datetime.combine(day, value, tzinfo=tz)


def checkpoints_for_occurrence(
    occurrence: ThemeOccurrence,
    spec: ThemeMemberSpec,
    *,
    production_kinds: Iterable[str] | None = None,
) -> tuple[ThemeCheckpoint, ...]:
    """Enumerate every stage candidate, including coexisting stages."""

    production = _production_set(production_kinds)
    tz = occurrence.start_at.tzinfo
    if spec.member_id != occurrence.member_id:
        return ()
    last_day = occurrence.last_playable_date
    candidates: list[ThemeCheckpoint] = []
    for rule in spec.stage_rules:
        if rule.kind not in production:
            continue
        if rule.day_scope == DAY_SCOPE_START_DAY:
            days = (occurrence.start_at.date(),)
        elif rule.day_scope == DAY_SCOPE_LAST_DAY:
            days = (last_day,)
        else:
            span = max(0, (last_day - occurrence.start_at.date()).days)
            days = tuple(
                occurrence.start_at.date() + timedelta(days=offset)
                for offset in range(span + 1)
            )
        for day in days:
            if rule.allow_after_end:
                # The cover stays usable once the authoritative period closed,
                # but only until the last playable day's midnight.  Take
                # ``max(trigger, end_at)``; when ``end_at`` already sits on that
                # midnight the window would collapse, so fall back to the
                # trigger and keep the hard deadline one instant later.
                hard_deadline = _at(day + timedelta(days=1), time.min, tz)
                due_at = max(_at(day, rule.trigger, tz), occurrence.end_at)
                if due_at >= hard_deadline:
                    due_at = _at(day, rule.trigger, tz)
                if not (occurrence.start_at <= due_at < hard_deadline):
                    continue
                deadline_at = hard_deadline
            else:
                due_at = max(_at(day, rule.trigger, tz), occurrence.start_at)
                if not (occurrence.start_at <= due_at < occurrence.end_at):
                    continue
                deadline_at = _at(day, rule.deadline, tz) if rule.deadline else None
            candidates.append(
                ThemeCheckpoint(
                    instance_key=occurrence.instance_key,
                    member_id=spec.member_id,
                    stage=rule.stage,
                    kind=rule.kind,
                    business_date=day.isoformat(),
                    due_at=due_at,
                    recurrence_group=rule.recurrence_group,
                    deadline_at=deadline_at,
                )
            )
    return tuple(candidates)


def _occurrence_is_expired(occurrence: ThemeOccurrence, now: datetime) -> bool:
    """An instance whose authoritative period already closed never runs again."""

    return now >= occurrence.end_at


def _checkpoint_is_historical(checkpoint: ThemeCheckpoint, now: datetime) -> bool:
    """A missed slot from an earlier business day is not replayed."""

    spec = _member_index().get(checkpoint.member_id)
    if spec is not None and any(rule.kind == checkpoint.kind and rule.day_scope == DAY_SCOPE_START_DAY
                                for rule in spec.stage_rules):
        # Initial setup is one per instance and remains due if discovered late.
        return False
    try:
        business_day = date.fromisoformat(checkpoint.business_date)
    except ValueError:
        return True
    return business_day < now.date()


def _collapse_latest_round(
    checkpoints: Iterable[ThemeCheckpoint],
) -> list[ThemeCheckpoint]:
    """Keep only the latest due checkpoint per intraday recurrence group.

    Rules without a group are independent and always retained.  This is what
    stops a Job that starts at 15:00 from replaying the missed 10:00/12:00
    garden rounds; only the most recent 14:00 round is executed.
    """

    latest: dict[tuple[str, str, str, str], ThemeCheckpoint] = {}
    independent: list[ThemeCheckpoint] = []
    for checkpoint in checkpoints:
        if not checkpoint.recurrence_group:
            independent.append(checkpoint)
            continue
        group_key = (
            checkpoint.instance_key,
            checkpoint.member_id,
            checkpoint.business_date,
            checkpoint.recurrence_group,
        )
        current = latest.get(group_key)
        if current is None or checkpoint.due_at > current.due_at:
            latest[group_key] = checkpoint
    independent.extend(latest.values())
    return independent


def active_theme_checkpoints(
    occurrences: Iterable[ThemeOccurrence],
    *,
    now: datetime,
    production_only: bool = True,
    production_kinds: Iterable[str] | None = None,
) -> tuple[ThemeCheckpoint, ...]:
    """Every still-executable stage candidate, including future ones.

    Expired instances and historical business dates are removed here so both
    the due selector and the next-time selector share one definition of what
    may still run.
    """

    if now.tzinfo is None:
        raise ValueError("主题集时钟必须带时区")
    production = _production_set(production_kinds)
    member_index = _member_index()
    candidates: list[ThemeCheckpoint] = []
    for occurrence in occurrences:
        spec = member_index.get(occurrence.member_id)
        if spec is None:
            continue
        instance_ended = _occurrence_is_expired(occurrence, now)
        for checkpoint in checkpoints_for_occurrence(
            occurrence, spec, production_kinds=production
        ):
            if production_only and checkpoint.kind not in production:
                continue
            rule = _stage_rule(spec, checkpoint.kind)
            # An ended instance stops every normal stage.  Only a rule that
            # explicitly opts into after-end finishing survives it, and then
            # only until its own hard deadline (checked below).
            if rule is None or not rule.allow_after_end:
                if instance_ended:
                    continue
            if _checkpoint_is_historical(checkpoint, now):
                continue
            if checkpoint.deadline_at is not None and now >= checkpoint.deadline_at:
                continue
            candidates.append(checkpoint)
    return tuple(candidates)


def due_theme_checkpoints(
    occurrences: Iterable[ThemeOccurrence],
    *,
    now: datetime,
    completed_keys: Iterable[tuple[str, str, str, str]] = (),
    production_only: bool = True,
    production_kinds: Iterable[str] | None = None,
) -> tuple[ThemeCheckpoint, ...]:
    if now.tzinfo is None:
        raise ValueError("主题集时钟必须带时区")
    completed = set(completed_keys)
    due = [
        checkpoint
        for checkpoint in active_theme_checkpoints(
            occurrences,
            now=now,
            production_only=production_only,
            production_kinds=production_kinds,
        )
        if checkpoint.due_at <= now
    ]
    # Collapse the missed intraday rounds first, then drop completed keys: a
    # finished latest round must not fall back to replaying an earlier slot.
    collapsed = _collapse_latest_round(due)
    # 同一时点依成员声明顺序执行：圣木可产出随礼，必须先于仙宴。
    stage_order = {(spec.member_id, rule.kind): index
                   for spec in THEME_COLLECTION_MEMBERS
                   for index, rule in enumerate(spec.stage_rules)}
    ordered = sorted(
        collapsed,
        key=lambda item: (
            item.due_at,
            item.member_id,
            item.instance_key,
            stage_order.get((item.member_id, item.kind), 0),
            item.kind,
            item.business_date,
        ),
    )
    unique: dict[tuple[str, str, str, str], ThemeCheckpoint] = {}
    for item in ordered:
        if item.key in completed:
            continue
        unique.setdefault(item.key, item)
    return tuple(unique.values())


def theme_reconcile_time(
    occurrences: Iterable[ThemeOccurrence],
    *,
    now: datetime,
    reconcile_time: time = time(0, 5),
) -> datetime:
    del occurrences
    candidate = _at(now.date(), reconcile_time, now.tzinfo)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


def next_theme_collection_time(
    occurrences: Iterable[ThemeOccurrence],
    *,
    now: datetime,
    completed_keys: Iterable[tuple[str, str, str, str]] = (),
    retry_times: Iterable[datetime] = (),
    production_only: bool = True,
    production_kinds: Iterable[str] | None = None,
) -> datetime:
    """Return the next absolute wake-up: earliest valid pending or reconcile."""

    if now.tzinfo is None:
        raise ValueError("主题集时钟必须带时区")
    completed = set(completed_keys)
    production = _production_set(production_kinds)
    candidates: list[datetime] = [
        theme_reconcile_time(occurrences, now=now),
    ]
    for checkpoint in active_theme_checkpoints(
        occurrences,
        now=now,
        production_only=production_only,
        production_kinds=production,
    ):
        if checkpoint.key in completed:
            continue
        if checkpoint.due_at > now:
            candidates.append(checkpoint.due_at)
    candidates.extend(value for value in retry_times if value > now)
    return min(candidates)


def _deferred_stage_evidence(
    occurrences: Iterable[ThemeOccurrence],
    *,
    production_kinds: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    production = _production_set(production_kinds)
    member_index = _member_index()
    deferred: list[dict[str, Any]] = []
    for occurrence in occurrences:
        spec = member_index.get(occurrence.member_id)
        if spec is None:
            continue
        for rule in spec.stage_rules:
            if rule.kind in production:
                continue
            deferred.append(
                {
                    "member_id": spec.member_id,
                    "instance_key": occurrence.instance_key,
                    "stage": rule.stage,
                    "kind": rule.kind,
                    "day_scope": rule.day_scope,
                    "trigger": rule.trigger.strftime("%H:%M"),
                    "reason": "theme_stage_not_production",
                }
            )
    return deferred


def project_theme_collection(
    plan: Mapping[str, Any],
    *,
    previous_completions: Mapping[str, Mapping[str, Any]] | None = None,
    resource_counts: Mapping[str, int | None] | None = None,
    completed_stage_keys: Iterable[tuple[str, str, str, str]] = (),
    activation_occurrences: Iterable[Mapping[str, Any]] = (),
    authority_occurrences: Iterable[Mapping[str, Any]] = (),
    now: datetime | None = None,
    timezone_name: str = DEFAULT_TIMEZONE,
    production_kinds: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Project the daily plan into the single canonical theme Job state.

    Every theme member - the internalized authorized activities included - is
    driven by this module's own stage rules.  Existence still comes only from a
    real occurrence or an independently authoritative period; a member without
    one is reported as a diagnostic and is never guessed into a run.
    """

    timezone = ZoneInfo(timezone_name)
    current = now or datetime.now(timezone)
    if current.tzinfo is None:
        raise ValueError("主题集时钟必须带时区")
    current = current.astimezone(timezone)
    production = _production_set(production_kinds)
    del previous_completions, resource_counts

    occurrences = discover_theme_occurrences(
        plan,
        activation_occurrences=activation_occurrences,
        authority_occurrences=authority_occurrences,
        timezone_name=timezone_name,
    )
    diagnostics = theme_occurrence_diagnostics(
        plan,
        activation_occurrences=activation_occurrences,
        authority_occurrences=authority_occurrences,
        timezone_name=timezone_name,
    )
    deferred = _deferred_stage_evidence(occurrences, production_kinds=production)
    decisions: list[dict[str, Any]] = []
    for item in deferred:
        decisions.append({**item, "status": "pending_validation"})
    decisions.extend(
        {**item, "binding_id": "theme-collection-diagnostic"}
        for item in diagnostics
    )
    if not occurrences and not diagnostics:
        decisions.append(
            {
                "binding_id": "theme-collection-no-occurrence",
                "task_id": THEME_COLLECTION_TASK_ID,
                "status": "blocked",
                "reason": "当前活动清单没有可归属的主题集实例",
                "next_time": None,
            }
        )

    completed_stages = set(completed_stage_keys)
    active_stages = active_theme_checkpoints(
        occurrences, now=current, production_kinds=production
    )
    pending_stages = [
        checkpoint
        for checkpoint in active_stages
        if checkpoint.key not in completed_stages
    ]
    due_now = due_theme_checkpoints(
        occurrences,
        now=current,
        completed_keys=completed_stages,
        production_kinds=production,
    )
    # Missed recurrence slots are superseded by the latest due round, not
    # pending work that can drag next_time back into the past after completion.
    pending_stages = list(due_now) + [c for c in pending_stages if c.due_at > current]
    candidate_times = [checkpoint.due_at for checkpoint in pending_stages]
    next_time = min(candidate_times) if candidate_times else None

    if next_time is not None:
        status = "ready"
    elif deferred or diagnostics:
        # Declared-but-unaccepted stages are a validation state, not a missing
        # observation.  Never report a disabled model as unavailable.
        status = "pending_validation"
    else:
        status = "observation_unavailable"

    desired = {THEME_COLLECTION_TASK_ID: None}
    if next_time is not None:
        desired[THEME_COLLECTION_TASK_ID] = next_time.strftime(
            "%Y-%m-%d %H:%M:%S"
        )

    return {
        "status": status,
        "desired_next_times": desired,
        "decisions": decisions,
        "deferred_stages": deferred,
        "diagnostics": diagnostics,
        "pending_stage_count": len(pending_stages),
        "due_stage_keys": [list(checkpoint.key) for checkpoint in due_now],
        "due_stages": [
            {
                "instance_key": checkpoint.instance_key,
                "member_id": checkpoint.member_id,
                "stage": checkpoint.stage,
                "kind": checkpoint.kind,
                "business_date": checkpoint.business_date,
                "due_at": checkpoint.due_at.isoformat(timespec="seconds"),
            }
            for checkpoint in due_now
        ],
        "occurrences": [
            {
                "member_id": occurrence.member_id,
                "activity_id": occurrence.activity_id,
                "name": occurrence.name,
                "instance_key": occurrence.instance_key,
                "start_at": occurrence.start_at.isoformat(timespec="seconds"),
                "end_at": occurrence.end_at.isoformat(timespec="seconds"),
            }
            for occurrence in occurrences
        ],
        "migration": {
            "canonical_task_ids": [THEME_COLLECTION_TASK_ID],
            "retirement_candidates": sorted(THEME_COLLECTION_INTERNALIZED_TASK_IDS),
            "fact_ready": bool(candidate_times),
            # Membership deletion still needs the explicit three-gate builder.
            "ready": False,
            "removed_task_ids": [],
        },
    }


def theme_collection_job_descriptor() -> dict[str, Any]:
    """The one canonical Job record shape for the Scheduler defaults.

    Registration is a code change the maintainer applies deliberately; this
    helper exists so the shape is shared instead of duplicated in the
    Scheduler defaults.
    """

    return {
        "id": THEME_COLLECTION_TASK_ID,
        "task_type": THEME_COLLECTION_TASK_TYPE,
        "label": THEME_COLLECTION_LABEL,
        "description": "动态",
        "retired_task_ids": sorted(THEME_COLLECTION_INTERNALIZED_TASK_IDS),
        "retired_task_types": sorted(THEME_COLLECTION_INTERNALIZED_TASK_TYPES),
    }


def theme_collection_migration_contract(
    projection: Mapping[str, Any],
    *,
    registered_standard_task_ids: Sequence[str],
    completion_store_ready: bool,
    daily_sync_adapter_ready: bool,
) -> dict[str, Any]:
    """Reuse the shared three-gate migration authority; never writes state."""

    return build_activity_lifecycle_scheduler_migration(
        projection,
        registered_standard_task_ids=registered_standard_task_ids,
        completion_store_ready=completion_store_ready,
        daily_sync_adapter_ready=daily_sync_adapter_ready,
    )


def iter_theme_checkpoints(
    checkpoints: Sequence[ThemeCheckpoint],
    *,
    execute: Any,
    completed_keys: Iterable[tuple[str, str, str, str]] = (),
) -> list[dict[str, Any]]:
    """Run due checkpoints in order; first failure propagates and stops.

    ``execute`` is the member executor lookup and must raise on any member
    failure.  A completion is never recorded here; the caller owns the durable
    store and may only persist after ``execute`` returns successfully.
    """

    completed = set(completed_keys)
    results: list[dict[str, Any]] = []
    for checkpoint in checkpoints:
        if checkpoint.key in completed:
            continue
        result = execute(checkpoint)
        results.append({"checkpoint": checkpoint, "result": result})
    return results


__all__ = [
    "DAY_SCOPE_DAILY",
    "DAY_SCOPE_LAST_DAY",
    "DAY_SCOPE_START_DAY",
    "GARDEN_BANQUET_DAILY_KINDS",
    "GARDEN_BANQUET_TAIL_KIND",
    "HOLY_WOOD_DAILY_KIND",
    "HOLY_WOOD_TAIL_KIND",
    "PRODUCTION_THEME_STAGE_KINDS",
    "STAGE_ACTIVE",
    "STAGE_PREPARE",
    "STAGE_TAIL",
    "THEME_COLLECTION_INTERNALIZED_TASK_IDS",
    "THEME_COLLECTION_INTERNALIZED_TASK_TYPES",
    "THEME_COLLECTION_LABEL",
    "THEME_COLLECTION_MEMBERS",
    "THEME_COLLECTION_TASK_ID",
    "THEME_COLLECTION_TASK_TYPE",
    "THEME_STAGES",
    "ThemeCheckpoint",
    "ThemeMemberSpec",
    "ThemeOccurrence",
    "ThemeStageRule",
    "VISIT_XIANZUN_KIND",
    "XIANYUAN_BANQUET_ACTIVITY_ID",
    "XIANYUAN_BANQUET_BASE_ID",
    "active_theme_checkpoints",
    "checkpoints_for_occurrence",
    "discover_theme_occurrences",
    "due_theme_checkpoints",
    "iter_theme_checkpoints",
    "iter_theme_source_candidates",
    "next_theme_collection_time",
    "project_theme_collection",
    "theme_collection_job_descriptor",
    "theme_collection_migration_contract",
    "theme_member_for_row",
    "theme_occurrence_diagnostics",
    "theme_reconcile_time",
    "theme_stage_is_production",
]
