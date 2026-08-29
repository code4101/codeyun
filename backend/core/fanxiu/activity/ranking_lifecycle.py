from __future__ import annotations

"""Pure lifecycle planning shared by the two ranking Scheduler Jobs.

The module knows activity occurrences and checkpoint rules, but never opens a
database session, operates the game, or writes Scheduler state.  One caller
may therefore plan gameplay rankings and resource rankings together without
coupling their activity-specific adapters.
"""

from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Iterable, Mapping

from backend.core.fanxiu.activity.exchange_activity_spec import RankingFamily


RANKING_LIFECYCLE_TASK_ID = "ranking-lifecycle"
RANKING_LIFECYCLE_TASK_TYPE = "ranking_lifecycle"
RESOURCE_RANKING_TASK_ID = "resource-ranking"
RESOURCE_RANKING_TASK_TYPE = "resource_ranking"
RETIRED_GAMEPLAY_RANKING_TASK_IDS = frozenset({
    "magic-invasion-explore",
    "xutian-palace-rankings",
    "xutian-palace-native-auto",
    "yunmeng-trial-auto-challenge",
    "yunmeng-tail",
    "legacy-daily-xianmeng",
})
RETIRED_GAMEPLAY_RANKING_TASK_TYPES = frozenset({
    "magic_invasion_explore",
    "xutian_palace_rankings",
    "xutian_palace_native_auto",
    "yunmeng_trial_auto_challenge",
    "yunmeng_tail",
    "daily_xianmeng",
})
RETIRED_RESOURCE_RANKING_TASK_IDS = frozenset({
    "resource-rank-daily-free-gift",
    "dandao-task-rewards",
    "yuanding-sansheng-daily-gift",
})
RETIRED_RESOURCE_RANKING_TASK_TYPES = frozenset({
    "resource_rank_daily_free_gift",
    "dandao_task_rewards",
    "yuanding_sansheng_daily_gift",
})
DAILY_RECONCILE_KIND = "daily_reconcile"
EXCHANGE_TAIL_KIND = "exchange_tail_0030"
MAGIC_ACTIVE_KIND = "magic_active_1900"
XIANMENG_ACTIVE_KIND = "xianmeng_active_1000"
TIANDI_YIJU_ACTIVE_KIND = "tiandi_yiju_active_1005"
RESOURCE_FREE_GIFT_KIND = "resource_free_gift_0510"
DANDAO_REWARDS_KIND = "dandao_rewards_1810"
YUANDING_GIFT_KIND = "yuanding_gift_0500"
DAILY_RECONCILE_TIME = time(0, 30)
XIANYUAN_EXCHANGE_TAIL_TIME = time(0, 0)
MAGIC_ACTIVE_TIME = time(19, 0)
XIANMENG_ACTIVE_TIME = time(10, 0)
TIANDI_YIJU_ACTIVE_TIME = time(10, 5)
RESOURCE_FREE_GIFT_TIME = time(5, 10)
DANDAO_REWARDS_TIME = time(18, 10)
YUANDING_GIFT_TIME = time(5, 0)

# An exchange-tail checkpoint is side-effectful, so it is enabled only for
# activity adapters that have a proven, idempotent executor.  The common
# lifecycle owns the timing; each adapter still owns navigation and purchase
# verification for its page family.
EXCHANGE_TAIL_ACTIVITY_TYPES = frozenset({
    "magic-invasion",
    "yunmeng-trial",
    "xianyuan-duokui",
    "tiandi-yiju",
})

# Only resource ranks with a real activity page, shared #605 landing and
# ChargeMgr idempotency proof may receive this side-effectful checkpoint.
RESOURCE_FREE_GIFT_ACTIVITY_TYPES = frozenset({
    "dandao-wending",
    "lingzhuang-huadao",
})

RANKING_CAPABILITY_STATUS = {
    "beast-abyss": "observed_reconcile_only",
    "tiandi-yiju": "implemented_active_and_idempotent_exchange_tail",
}

# 8090002 is the cross-server group-selection/schedule surface.  It overlaps
# the real 8090004 board interval, so giving both occurrences an action
# checkpoint would spend the same account stamina twice under two identities.
TIANDI_YIJU_PLAYABLE_ACTIVITY_IDS = frozenset({8090001, 8090004})


@dataclass(frozen=True)
class RankingOccurrence:
    activity_type: str
    family: RankingFamily
    runtime_id: str
    activity_id: int
    start_at: datetime
    end_at: datetime
    prepare_at: datetime
    close_at: datetime
    cross_count: int
    world_level: int = 0
    base_id: int = 0

    @property
    def instance_key(self) -> str:
        return (
            f"runtime:{self.runtime_id}:activity:{self.activity_id}:"
            f"{self.start_at.isoformat(timespec='seconds')}:"
            f"{self.end_at.isoformat(timespec='seconds')}"
        )


@dataclass(frozen=True)
class RankingCheckpoint:
    instance_key: str
    activity_type: str
    family: RankingFamily
    runtime_id: str
    activity_id: int
    checkpoint_kind: str
    business_date: str
    due_at: datetime

    @property
    def key(self) -> tuple[str, str, str]:
        return self.instance_key, self.checkpoint_kind, self.business_date

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["due_at"] = self.due_at.isoformat(timespec="seconds")
        return payload


@dataclass(frozen=True)
class RankingActivityIdentity:
    activity_type: str
    family: RankingFamily
    vo_types: tuple[str, ...]
    runtime_activity_types: tuple[int, ...] = ()
    activity_ids: tuple[int, ...] = ()
    base_ids: tuple[int, ...] = ()
    names: tuple[str, ...] = ()

    def match_priority(self, raw: Mapping[str, Any]) -> int:
        """Rank stable normalized facts above compatibility-only class names."""
        vo_type = str(raw.get("class") or raw.get("voType") or raw.get("vo_type") or "")
        try:
            runtime_type = int(raw.get("activityType") or raw.get("activity_type") or 0)
            activity_id = int(raw.get("activityId") or raw.get("activity_id") or 0)
            base_id = int(raw.get("baseId") or raw.get("base_id") or 0)
            name = str(raw.get("name") or raw.get("activityName") or "").strip()
        except (TypeError, ValueError):
            return 0
        if (activity_id and activity_id in self.activity_ids) or (
            base_id and base_id in self.base_ids
        ):
            return 3
        if runtime_type and runtime_type in self.runtime_activity_types:
            return 2
        if vo_type and vo_type in self.vo_types:
            return 1
        if name and name in self.names:
            return 1
        return 0

    def matches(self, raw: Mapping[str, Any]) -> bool:
        return self.match_priority(raw) > 0


def _timestamp(value: Any, *, fallback: datetime | None = None) -> datetime | None:
    try:
        raw = int(value or 0)
    except (TypeError, ValueError):
        raw = 0
    if raw > 0:
        parsed = datetime.fromtimestamp(raw / 1000).astimezone()
        return parsed.replace(microsecond=0)
    return fallback


def ranking_activity_identities() -> tuple[RankingActivityIdentity, ...]:
    """Project the public Exchange registry into lifecycle identities."""

    from backend.core.fanxiu.activity.exchange_activity_registry import (
        EXCHANGE_ACTIVITY_SPECS,
    )
    runtime_types = {
        "yunmeng-trial": (21,),
        "xianyuan-duokui": (129,),
        "xutian-palace": (8,),
        "magic-invasion": (7,),
        "beast-abyss": (15,),
        "tiandi-yiju": (9, 13, 17),
    }
    activity_ids = {
        "xianyuan-duokui": (846001,),
        # Version-specific Activity ids belong only in this adapter boundary.
        # The normalized Runtime schedule deliberately does not retain raw VO
        # classes, so every retained server-count variant must be explicit.
        "lingzhuang-huadao": (
            1044501, 1044301, 2044301, 4044301, 1044311,
            8044301, 16044301, 32044301,
        ),
        "yaochi-flower-festival": (
            1042801, 2042801, 4042801, 1042811,
            8042801, 16042801, 32042801,
        ),
        "yuanding-sansheng": (16045101, 32045101, 64045101),
        "lingchong-jingwu": (
            1042001, 1042901, 2042901, 4042901, 1042911,
            8042901, 16042901, 32042901,
        ),
        "lianti-faxiang": (
            1041701, 1043001, 2043001, 4043001, 1043011,
            8043001, 16043001, 32043001,
        ),
        "dandao-wending": (
            1041401, 1043101, 2043101, 4043101, 1043111,
            8043101, 16043101, 32043101,
        ),
        "tiandi-yiju": (8090001, 8090002, 8090004),
    }
    identities: list[RankingActivityIdentity] = []
    for activity_type, spec in EXCHANGE_ACTIVITY_SPECS.items():
        identities.append(
            RankingActivityIdentity(
                activity_type=activity_type,
                family=spec.family,
                vo_types=tuple(spec.worldline_vo_types),
                runtime_activity_types=runtime_types.get(activity_type, ()),
                activity_ids=activity_ids.get(activity_type, ()),
            )
        )
    identities.extend(
        (
            RankingActivityIdentity(
                activity_type="xianmeng-competition",
                family="gameplay_rank",
                vo_types=(),
                runtime_activity_types=(42, 43),
                base_ids=(28000, 28100, 28200),
            ),
        )
    )
    return tuple(identities)


def discover_ranking_occurrences(
    schedule: Mapping[str, Any],
    *,
    identities: Iterable[RankingActivityIdentity] | None = None,
    family: RankingFamily | None = None,
) -> tuple[RankingOccurrence, ...]:
    """Return every complete registered ranking occurrence in one Runtime list."""

    identity_rows = tuple(identities or ranking_activity_identities())
    result: list[RankingOccurrence] = []
    seen: set[str] = set()
    for raw in schedule.get("items") or ():
        if not isinstance(raw, Mapping):
            continue
        priorities = [
            (identity.match_priority(raw), identity) for identity in identity_rows
        ]
        best_priority = max((priority for priority, _identity in priorities), default=0)
        matches = [
            identity
            for priority, identity in priorities
            if priority == best_priority and priority > 0
        ]
        if len(matches) != 1:
            continue
        identity = matches[0]
        if family is not None and identity.family != family:
            continue
        runtime_id = str(raw.get("id") or "").strip()
        try:
            activity_id = int(raw.get("activityId") or 0)
            base_id = int(raw.get("baseId") or 0)
            cross_count = max(1, int(raw.get("serverCount") or 1))
            world_level = max(0, int(raw.get("avgWorldLevel") or 0))
        except (TypeError, ValueError):
            continue
        start_at = _timestamp(raw.get("startTime"))
        end_at = _timestamp(raw.get("endTime"))
        if not runtime_id or activity_id <= 0 or start_at is None or end_at is None:
            continue
        prepare_at = _timestamp(raw.get("prepareEndTime"), fallback=start_at)
        close_at = _timestamp(raw.get("closePanelTime"), fallback=end_at)
        if (
            prepare_at is None
            or close_at is None
            or end_at < start_at
            or close_at < end_at
        ):
            continue
        occurrence = RankingOccurrence(
            activity_type=identity.activity_type,
            family=identity.family,
            runtime_id=runtime_id,
            activity_id=activity_id,
            base_id=base_id,
            start_at=start_at,
            end_at=end_at,
            prepare_at=prepare_at,
            close_at=close_at,
            cross_count=cross_count,
            world_level=world_level,
        )
        if occurrence.instance_key in seen:
            continue
        seen.add(occurrence.instance_key)
        result.append(occurrence)
    return tuple(
        sorted(result, key=lambda item: (item.prepare_at, item.activity_type, item.runtime_id))
    )


def occurrence_relevant_on(occurrence: RankingOccurrence, business_day: date) -> bool:
    """Whether a daily reconciliation must retain this occurrence."""

    return occurrence.prepare_at.date() <= business_day <= occurrence.close_at.date()


def _at(day: date, value: time, timezone: Any) -> datetime:
    return datetime.combine(day, value, tzinfo=timezone)


def checkpoints_for_occurrence(
    occurrence: RankingOccurrence,
    *,
    business_day: date,
) -> tuple[RankingCheckpoint, ...]:
    """Build common and activity-specific checkpoints for one business day."""

    if not occurrence_relevant_on(occurrence, business_day):
        return ()
    checkpoints = []
    if not (
        occurrence.activity_type == "tiandi-yiju"
        and occurrence.activity_id not in TIANDI_YIJU_PLAYABLE_ACTIVITY_IDS
    ):
        checkpoints.append(RankingCheckpoint(
            instance_key=occurrence.instance_key,
            activity_type=occurrence.activity_type,
            family=occurrence.family,
            runtime_id=occurrence.runtime_id,
            activity_id=occurrence.activity_id,
            checkpoint_kind=DAILY_RECONCILE_KIND,
            business_date=business_day.isoformat(),
            due_at=_at(business_day, DAILY_RECONCILE_TIME, occurrence.start_at.tzinfo),
        ))
    tail_day = occurrence.end_at.date() + timedelta(days=1)
    tail_time = (
        XIANYUAN_EXCHANGE_TAIL_TIME
        if occurrence.activity_type == "xianyuan-duokui"
        else DAILY_RECONCILE_TIME
    )
    tail_at = _at(tail_day, tail_time, occurrence.start_at.tzinfo)
    if (
        occurrence.family == "gameplay_rank"
        and occurrence.activity_type in EXCHANGE_TAIL_ACTIVITY_TYPES
        and not (
            occurrence.activity_type == "tiandi-yiju"
            and occurrence.activity_id not in TIANDI_YIJU_PLAYABLE_ACTIVITY_IDS
        )
        and business_day == tail_day
        and occurrence.end_at < tail_at < occurrence.close_at
    ):
        checkpoints.append(
            RankingCheckpoint(
                instance_key=occurrence.instance_key,
                activity_type=occurrence.activity_type,
                family=occurrence.family,
                runtime_id=occurrence.runtime_id,
                activity_id=occurrence.activity_id,
                checkpoint_kind=EXCHANGE_TAIL_KIND,
                business_date=business_day.isoformat(),
                due_at=tail_at,
            )
        )
    magic_at = _at(business_day, MAGIC_ACTIVE_TIME, occurrence.start_at.tzinfo)
    if (
        occurrence.activity_type == "magic-invasion"
        and occurrence.start_at <= magic_at <= occurrence.end_at
    ):
        checkpoints.append(
            RankingCheckpoint(
                instance_key=occurrence.instance_key,
                activity_type=occurrence.activity_type,
                family=occurrence.family,
                runtime_id=occurrence.runtime_id,
                activity_id=occurrence.activity_id,
                checkpoint_kind=MAGIC_ACTIVE_KIND,
                business_date=business_day.isoformat(),
                due_at=magic_at,
            )
        )
    xianmeng_at = _at(business_day, XIANMENG_ACTIVE_TIME, occurrence.start_at.tzinfo)
    if (
        occurrence.activity_type == "xianmeng-competition"
        and business_day in {occurrence.start_at.date(), occurrence.end_at.date()}
        and occurrence.start_at <= xianmeng_at <= occurrence.end_at
    ):
        checkpoints.append(
            RankingCheckpoint(
                instance_key=occurrence.instance_key,
                activity_type=occurrence.activity_type,
                family=occurrence.family,
                runtime_id=occurrence.runtime_id,
                activity_id=occurrence.activity_id,
                checkpoint_kind=XIANMENG_ACTIVE_KIND,
                business_date=business_day.isoformat(),
                due_at=xianmeng_at,
            )
        )
    tiandi_yiju_at = _at(
        business_day, TIANDI_YIJU_ACTIVE_TIME, occurrence.start_at.tzinfo
    )
    if (
        occurrence.activity_type == "tiandi-yiju"
        and occurrence.activity_id in TIANDI_YIJU_PLAYABLE_ACTIVITY_IDS
        and occurrence.start_at <= tiandi_yiju_at <= occurrence.end_at
    ):
        checkpoints.append(
            RankingCheckpoint(
                instance_key=occurrence.instance_key,
                activity_type=occurrence.activity_type,
                family=occurrence.family,
                runtime_id=occurrence.runtime_id,
                activity_id=occurrence.activity_id,
                checkpoint_kind=TIANDI_YIJU_ACTIVE_KIND,
                business_date=business_day.isoformat(),
                due_at=tiandi_yiju_at,
            )
        )
    resource_kinds = (
        (
            RESOURCE_FREE_GIFT_KIND,
            RESOURCE_FREE_GIFT_TIME,
            occurrence.activity_type in RESOURCE_FREE_GIFT_ACTIVITY_TYPES,
        ),
        (DANDAO_REWARDS_KIND, DANDAO_REWARDS_TIME, occurrence.activity_type == "dandao-wending"),
        (YUANDING_GIFT_KIND, YUANDING_GIFT_TIME, occurrence.activity_type == "yuanding-sansheng"),
    )
    if occurrence.family == "resource_rank":
        for checkpoint_kind, checkpoint_time, enabled in resource_kinds:
            due_at = _at(business_day, checkpoint_time, occurrence.start_at.tzinfo)
            if enabled and occurrence.start_at <= due_at <= occurrence.end_at:
                checkpoints.append(
                    RankingCheckpoint(
                        instance_key=occurrence.instance_key,
                        activity_type=occurrence.activity_type,
                        family=occurrence.family,
                        runtime_id=occurrence.runtime_id,
                        activity_id=occurrence.activity_id,
                        checkpoint_kind=checkpoint_kind,
                        business_date=business_day.isoformat(),
                        due_at=due_at,
                    )
                )
    return tuple(checkpoints)


def due_ranking_checkpoints(
    occurrences: Iterable[RankingOccurrence],
    *,
    now: datetime,
    completed_keys: Iterable[tuple[str, str, str]] = (),
) -> tuple[RankingCheckpoint, ...]:
    """Return all incomplete checkpoints due by ``now`` in stable order."""

    if now.tzinfo is None:
        raise ValueError("榜单生命周期时钟必须带时区")
    completed = set(completed_keys)
    candidates: list[RankingCheckpoint] = []
    for occurrence in occurrences:
        local_now = now.astimezone(occurrence.start_at.tzinfo)
        last_day = min(local_now.date(), occurrence.close_at.date())
        business_day = occurrence.prepare_at.date()
        while business_day <= last_day:
            candidates.extend(
                checkpoint
                for checkpoint in checkpoints_for_occurrence(
                    occurrence,
                    business_day=business_day,
                )
                if checkpoint.checkpoint_kind == DAILY_RECONCILE_KIND
                or (
                    checkpoint.checkpoint_kind == EXCHANGE_TAIL_KIND
                    and occurrence.end_at < local_now < occurrence.close_at
                )
            )
            business_day += timedelta(days=1)

        # Stateful gameplay is safe to catch up only while this exact Runtime
        # occurrence is still open.  An expired 19:00 action is never replayed.
        if occurrence.start_at <= local_now <= occurrence.end_at:
            candidates.extend(
                checkpoint
                for checkpoint in checkpoints_for_occurrence(
                    occurrence,
                    business_day=local_now.date(),
                )
                if checkpoint.checkpoint_kind
                in {
                    MAGIC_ACTIVE_KIND,
                    XIANMENG_ACTIVE_KIND,
                    TIANDI_YIJU_ACTIVE_KIND,
                    RESOURCE_FREE_GIFT_KIND,
                    DANDAO_REWARDS_KIND,
                    YUANDING_GIFT_KIND,
                }
            )
    return tuple(
        sorted(
            (
                item
                for item in candidates
                if item.key not in completed and item.due_at <= now
            ),
            key=lambda item: (
                item.due_at,
                item.activity_type,
                item.instance_key,
                item.checkpoint_kind,
            ),
        )
    )


def next_ranking_lifecycle_time(
    occurrences: Iterable[RankingOccurrence],
    *,
    now: datetime,
    completed_keys: Iterable[tuple[str, str, str]] = (),
    retry_times: Iterable[datetime] = (),
) -> datetime:
    """Return the next absolute wake-up for the sole lifecycle Job."""

    if now.tzinfo is None:
        raise ValueError("榜单生命周期时钟必须带时区")
    completed = set(completed_keys)
    local_now = now.astimezone()
    next_daily = _at(local_now.date(), DAILY_RECONCILE_TIME, local_now.tzinfo)
    if next_daily <= local_now:
        next_daily += timedelta(days=1)
    candidates: list[datetime] = [next_daily]
    for occurrence in occurrences:
        local_day = now.astimezone(occurrence.start_at.tzinfo).date()
        for day in (local_day, local_day + timedelta(days=1)):
            for checkpoint in checkpoints_for_occurrence(
                occurrence,
                business_day=day,
            ):
                if checkpoint.key not in completed and checkpoint.due_at > now:
                    candidates.append(checkpoint.due_at)
    candidates.extend(value for value in retry_times if value > now)
    return min(candidates)


__all__ = [
    "DAILY_RECONCILE_KIND",
    "EXCHANGE_TAIL_ACTIVITY_TYPES",
    "EXCHANGE_TAIL_KIND",
    "MAGIC_ACTIVE_KIND",
    "XIANMENG_ACTIVE_KIND",
    "TIANDI_YIJU_ACTIVE_KIND",
    "TIANDI_YIJU_PLAYABLE_ACTIVITY_IDS",
    "RESOURCE_FREE_GIFT_KIND",
    "RESOURCE_FREE_GIFT_ACTIVITY_TYPES",
    "DANDAO_REWARDS_KIND",
    "YUANDING_GIFT_KIND",
    "RANKING_CAPABILITY_STATUS",
    "RANKING_LIFECYCLE_TASK_ID",
    "RANKING_LIFECYCLE_TASK_TYPE",
    "RESOURCE_RANKING_TASK_ID",
    "RESOURCE_RANKING_TASK_TYPE",
    "RETIRED_GAMEPLAY_RANKING_TASK_IDS",
    "RETIRED_GAMEPLAY_RANKING_TASK_TYPES",
    "RETIRED_RESOURCE_RANKING_TASK_IDS",
    "RETIRED_RESOURCE_RANKING_TASK_TYPES",
    "RankingActivityIdentity",
    "RankingFamily",
    "RankingCheckpoint",
    "RankingOccurrence",
    "checkpoints_for_occurrence",
    "discover_ranking_occurrences",
    "due_ranking_checkpoints",
    "next_ranking_lifecycle_time",
    "occurrence_relevant_on",
    "ranking_activity_identities",
]
