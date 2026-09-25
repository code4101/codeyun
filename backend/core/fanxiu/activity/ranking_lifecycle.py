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
    "take-medicine-batch",
})
RETIRED_RESOURCE_RANKING_TASK_TYPES = frozenset({
    "resource_rank_daily_free_gift",
    "dandao_task_rewards",
    "yuanding_sansheng_daily_gift",
    "take_medicine_batch",
})
DAILY_RECONCILE_KIND = "daily_reconcile"
EXCHANGE_TAIL_KIND = "exchange_tail_0030"
MAGIC_INITIALIZATION_KIND = "magic_initialization_0030"
MAGIC_ACTIVE_KIND = "magic_active_1900"
MAGIC_MAIL_KIND = "magic_mail_1200"
XUTIAN_ACTIVE_KIND = "xutian_active_1000"
XUTIAN_OPEN_COLLECTION_KIND = "xutian_open_collection_1005"
BEAST_ABYSS_INITIALIZATION_KIND = "beast_abyss_initialization_1000"
BEAST_ABYSS_FORMAL_KIND = "beast_abyss_formal_1005"
BEAST_ABYSS_AUTO_CLEAR_KIND = "beast_abyss_auto_clear_2045"
BEAST_ABYSS_MANUAL_CLEAR_KIND = "beast_abyss_manual_clear_2150"
XIANMENG_ACTIVE_KIND = "xianmeng_active_1000"
TIANDI_YIJU_ACTIVE_KIND = "tiandi_yiju_active_1005"
# 云梦在 10:00 才真正开启，00:10 的日常对账只能拿到日程身份；活动期间必须由
# 一次真实的页面加载把兑换宝阁/钱包/榜单事实带进 Runtime，否则事实永远为空。
YUNMENG_ACTIVE_KIND = "yunmeng_active_1005"
# 云梦论剑玉来自挑战：开启后先按当日可用次数推一次，20:45 再用恢复出来的次数补一次。
YUNMENG_CHALLENGE_KIND = "yunmeng_challenge_1010"
YUNMENG_CHALLENGE_EVENING_KIND = "yunmeng_challenge_2045"
RESOURCE_FREE_GIFT_KIND = "resource_free_gift_0510"
LINGZHUANG_STRENGTHENING_KIND = "lingzhuang_tier12_0515"
DANDAO_REWARDS_KIND = "dandao_rewards_1810"
DANDAO_RESOURCE_USE_KIND = "dandao_resource_use_0500"
DANDAO_TAKE_MEDICINE_KIND = "dandao_take_medicine_once"
YUANDING_GIFT_KIND = "yuanding_gift_0500"
DAILY_RECONCILE_TIME = time(0, 10)
EXCHANGE_TAIL_CLOSE_SAFETY_MARGIN = timedelta(minutes=5)
MAGIC_INITIALIZATION_TIME = time(0, 30)
XIANYUAN_EXCHANGE_TAIL_TIME = time(0, 0)
MAGIC_ACTIVE_TIME = time(19, 0)
MAGIC_MAIL_TIME = time(12, 0)
XUTIAN_ACTIVE_TIME = time(10, 0)
XUTIAN_OPEN_COLLECTION_TIME = time(10, 5)
YUNMENG_ACTIVE_TIME = time(10, 5)
YUNMENG_CHALLENGE_TIME = time(10, 10)
YUNMENG_CHALLENGE_EVENING_TIME = time(20, 45)
BEAST_ABYSS_INITIALIZATION_TIME = time(10, 0)
BEAST_ABYSS_FORMAL_TIME = time(10, 5)
BEAST_ABYSS_AUTO_CLEAR_TIME = time(20, 45)
BEAST_ABYSS_MANUAL_CLEAR_TIME = time(21, 50)
BEAST_ABYSS_AUTO_WINDOW_END = time(21, 30)
# Beast Abyss active execution is still under live R&D.  Keep its checkpoint
# identities and executors available for explicit AI validation, but do not
# publish them into the engineering Scheduler until the full branches pass.
PRODUCTION_BEAST_ABYSS_ACTIVE_KINDS: frozenset[str] = frozenset()
XIANMENG_ACTIVE_TIME = time(10, 0)
TIANDI_YIJU_ACTIVE_TIME = time(10, 5)
RESOURCE_FREE_GIFT_TIME = time(5, 10)
DANDAO_REWARDS_TIME = time(18, 10)
DANDAO_RESOURCE_USE_TIME = time(5, 0)
YUANDING_GIFT_TIME = time(5, 0)
# 缘定三生「正式运行」单元：任务奖励领取 + 自动联姻使用资源，最后回 #34。
# 活动期间每天固定 5 个时点各跑一次；两个模块连跑，中间不回世界。
YUANDING_RESOURCE_UNIT_KINDS = (
    ("yuanding_resource_1000", time(10, 0)),
    ("yuanding_resource_1200", time(12, 0)),
    ("yuanding_resource_1500", time(15, 0)),
    ("yuanding_resource_1800", time(18, 0)),
    ("yuanding_resource_2000", time(20, 0)),
)
YUANDING_RESOURCE_UNIT_KIND_SET = frozenset(kind for kind, _unit_time in YUANDING_RESOURCE_UNIT_KINDS)

# Only resource ranks with a real activity page, shared #605 landing and
# ChargeMgr idempotency proof may receive this side-effectful checkpoint.
# lianti-faxiang enables the whole activity type, but only the 8-server
# instance 8043001 has passed the real 754/755/#605 page acceptance; every
# other variant stays unregistered in the gift adapter and is rejected
# fail-closed by the occurrence-scoped filter.
RESOURCE_FREE_GIFT_ACTIVITY_TYPES = frozenset({
    "shequn-lingchong",
    "dandao-wending",
    "lingzhuang-huadao",
    "yaochi-flower-festival",
    "lianti-faxiang",
})

RANKING_CAPABILITY_STATUS = {
    "beast-abyss": "implemented_exchange_tail_active_still_rnd",
    "tiandi-yiju": "implemented_active_and_idempotent_exchange_tail",
    # 日程发现与实例化已接入；实时社团榜、任务和资源采集仍待研发。
    "shequn-lingchong": "observed_unhandled",
}

# Production Scheduler allowlist.  Checkpoint definitions outside this list
# remain available to explicit R&D Cells, but the unified gameplay-ranking Job
# must never discover or wake for them until live idempotent acceptance has
# promoted the capability here.
PRODUCTION_GAMEPLAY_EXCHANGE_TAIL_ACTIVITY_TYPES = frozenset({
    "xutian-palace",
    "beast-abyss",
    "magic-invasion",
    "yunmeng-trial",
    "xianyuan-duokui",
    "tiandi-yiju",
})
PRODUCTION_GAMEPLAY_CHECKPOINT_KINDS = {
    # 19:00 supply -> 3x500 (+one reward-miss batch) -> task rewards has
    # occurrence-scoped consumption evidence and passed live replay.
    "magic-invasion": frozenset({MAGIC_INITIALIZATION_KIND, MAGIC_ACTIVE_KIND}),
    "xutian-palace": frozenset({XUTIAN_OPEN_COLLECTION_KIND}),
    # 仙盟争霸只有 10:00 的正式挑战是已验收的生产动作；00:10 的日常对账在该活动
    # 上是 no-op retained 标记，不纳入准入，避免每天多一次无动作唤醒。
    "xianmeng-competition": frozenset({XIANMENG_ACTIVE_KIND}),
    "tiandi-yiju": frozenset({DAILY_RECONCILE_KIND, TIANDI_YIJU_ACTIVE_KIND}),
    "yunmeng-trial": frozenset({
        YUNMENG_ACTIVE_KIND,
        YUNMENG_CHALLENGE_KIND,
        YUNMENG_CHALLENGE_EVENING_KIND,
    }),
}


def ranking_checkpoint_is_production(checkpoint: "RankingCheckpoint") -> bool:
    if checkpoint.family == "resource_rank":
        return True
    if checkpoint.checkpoint_kind == EXCHANGE_TAIL_KIND:
        # A known shop's closing obligation must wake the owner even when its
        # executor is missing. The owner escalates that capability gap to AI;
        # filtering here used to silently sleep past the redemption window.
        return True
    return checkpoint.checkpoint_kind in PRODUCTION_GAMEPLAY_CHECKPOINT_KINDS.get(
        checkpoint.activity_type,
        frozenset(),
    )

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
        if self.activity_type == "magic-invasion":
            from backend.core.fanxiu.activity.magic_occurrence_identity import magic_occurrence_key
            return magic_occurrence_key(self.activity_id, self.cross_count,
                                        self.start_at.isoformat(), self.end_at.isoformat())
        # Xutian's Runtime row id is not a stable occurrence identity.  The
        # same open activity has been observed changing from the early
        # cross-server suffix to the settled server suffix without changing
        # its activity id or interval.  Keep the checkpoint row stable across
        # that refresh while leaving every other activity's established key
        # contract untouched.
        if self.activity_type == "xutian-palace":
            return (
                f"activity:xutian-palace:{self.activity_id}:"
                f"{self.start_at.isoformat(timespec='seconds')}:"
                f"{self.end_at.isoformat(timespec='seconds')}"
            )
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
        "xiling-zhengwu": (1043801, 2043801, 4043801, 8043801, 16043801, 32043801),
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
        # 社团灵宠只有跨服父活动进入世界线日程；其唯一 follow
        # 指向社团榜，不得套用灵宠竞武的个人榜/位面榜双榜身份。
        "shequn-lingchong": (2043501, 4043501, 8043501, 16043501),
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


def occurrence_has_exchange_shop(occurrence: RankingOccurrence) -> bool:
    """Return the registered page capability for this Runtime occurrence.

    Runtime owns the exact open interval (``end_at`` to ``close_at``), while
    the public activity spec owns whether that page actually has an exchange
    shop.  Executor maturity is deliberately not part of this business fact.
    """

    from backend.core.fanxiu.activity.exchange_activity_registry import (
        get_exchange_activity_spec,
    )

    try:
        spec = get_exchange_activity_spec(occurrence.activity_type)
    except ValueError:
        return False
    return bool(spec.page.has_shop and spec.shop is not None)


def occurrence_exchange_tail_window_contains(
    occurrence: RankingOccurrence,
    value: datetime,
) -> bool:
    """Whether ``value`` is inside the occurrence's exchange-tail window.

    Runtime ``closePanelTime`` is minute-granular for some activities (Magic
    Invasion currently reports exactly ``00:30:00``).  That labelled closing
    minute is still the scheduled 00:30 exchange slot, so admit the remainder
    of that minute for normal Scheduler dispatch jitter.  All later work still
    has to prove the exact Runtime occurrence before any physical action.
    """

    local_value = value.astimezone(occurrence.start_at.tzinfo)
    closing_minute_end = occurrence.close_at.replace(
        second=0,
        microsecond=0,
    ) + timedelta(minutes=1)
    return occurrence.end_at < local_value < closing_minute_end


def checkpoints_for_occurrence(
    occurrence: RankingOccurrence,
    *,
    business_day: date,
) -> tuple[RankingCheckpoint, ...]:
    """Build common and activity-specific checkpoints for one business day."""

    if not occurrence_relevant_on(occurrence, business_day):
        return ()
    checkpoints = []
    daily_reconcile_enabled = not (
        occurrence.activity_type == "tiandi-yiju"
        and occurrence.activity_id not in TIANDI_YIJU_PLAYABLE_ACTIVITY_IDS
    ) and not (
        occurrence.activity_type == "beast-abyss"
        and business_day != occurrence.start_at.date()
    ) and occurrence.activity_type != "magic-invasion"
    if daily_reconcile_enabled:
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
    default_tail_at = _at(tail_day, tail_time, occurrence.start_at.tzinfo)
    # Some Runtime occurrences close their panel exactly at 00:30.  Waiting
    # for that labelled boundary lets #66 rotate to the next occurrence before
    # the old calendar row can be entered.  Keep the ordinary 00:30 slot when
    # there is ample room, otherwise enter five minutes before the exact
    # Runtime close while the same occurrence is still provably available.
    tail_at = min(
        default_tail_at,
        occurrence.close_at - EXCHANGE_TAIL_CLOSE_SAFETY_MARGIN,
    )
    if (
        occurrence_has_exchange_shop(occurrence)
        and not (
            occurrence.activity_type == "tiandi-yiju"
            and occurrence.activity_id not in TIANDI_YIJU_PLAYABLE_ACTIVITY_IDS
        )
        and business_day == tail_at.date()
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
    magic_mail_at = _at(business_day, MAGIC_MAIL_TIME, occurrence.start_at.tzinfo)
    # The calendar can show an upcoming Magic occurrence before its entry is
    # open.  Its 00:30 card then only opens a preview, so initialization must
    # wait for the Runtime start rather than treating the preview as a page.
    magic_initialization_at = max(
        _at(business_day, MAGIC_INITIALIZATION_TIME, occurrence.start_at.tzinfo),
        occurrence.start_at + timedelta(minutes=1),
    )
    if (
        occurrence.activity_type == "magic-invasion"
        and business_day == occurrence.start_at.date()
    ):
        checkpoints.append(
            RankingCheckpoint(
                instance_key=occurrence.instance_key,
                activity_type=occurrence.activity_type,
                family=occurrence.family,
                runtime_id=occurrence.runtime_id,
                activity_id=occurrence.activity_id,
                checkpoint_kind=MAGIC_INITIALIZATION_KIND,
                business_date=business_day.isoformat(),
                due_at=magic_initialization_at,
            )
        )
    if (
        occurrence.activity_type == "magic-invasion"
        and occurrence.start_at.date() <= business_day <= occurrence.end_at.date()
    ):
        checkpoints.append(
            RankingCheckpoint(
                # The mail box belongs to the account, not to either of the
                # consecutive server/cross-server activity occurrences.  A
                # stable account key makes the 12:00 action idempotent even
                # when Runtime later drops the server occurrence.
                instance_key="account:magic-invasion-mail",
                activity_type=occurrence.activity_type,
                family=occurrence.family,
                runtime_id=occurrence.runtime_id,
                activity_id=occurrence.activity_id,
                checkpoint_kind=MAGIC_MAIL_KIND,
                business_date=business_day.isoformat(),
                due_at=magic_mail_at,
            )
        )
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
    xutian_at = _at(business_day, XUTIAN_ACTIVE_TIME, occurrence.start_at.tzinfo)
    xutian_collection_at = _at(
        business_day, XUTIAN_OPEN_COLLECTION_TIME, occurrence.start_at.tzinfo,
    )
    if (
        occurrence.activity_type == "xutian-palace"
        and occurrence.start_at <= xutian_collection_at <= occurrence.end_at
    ):
        checkpoints.append(RankingCheckpoint(
            instance_key=occurrence.instance_key,
            activity_type=occurrence.activity_type,
            family=occurrence.family,
            runtime_id=occurrence.runtime_id,
            activity_id=occurrence.activity_id,
            checkpoint_kind=XUTIAN_OPEN_COLLECTION_KIND,
            business_date=business_day.isoformat(),
            due_at=xutian_collection_at,
        ))
    if (
        occurrence.activity_type == "xutian-palace"
        and occurrence.start_at <= xutian_at <= occurrence.end_at
    ):
        checkpoints.append(
            RankingCheckpoint(
                instance_key=occurrence.instance_key,
                activity_type=occurrence.activity_type,
                family=occurrence.family,
                runtime_id=occurrence.runtime_id,
                activity_id=occurrence.activity_id,
                checkpoint_kind=XUTIAN_ACTIVE_KIND,
                business_date=business_day.isoformat(),
                due_at=xutian_at,
            )
        )
    beast_checkpoints = (
        (BEAST_ABYSS_INITIALIZATION_KIND, BEAST_ABYSS_INITIALIZATION_TIME),
        (BEAST_ABYSS_FORMAL_KIND, BEAST_ABYSS_FORMAL_TIME),
        (BEAST_ABYSS_AUTO_CLEAR_KIND, BEAST_ABYSS_AUTO_CLEAR_TIME),
        (BEAST_ABYSS_MANUAL_CLEAR_KIND, BEAST_ABYSS_MANUAL_CLEAR_TIME),
    )
    if occurrence.activity_type == "beast-abyss":
        for checkpoint_kind, checkpoint_time in beast_checkpoints:
            if checkpoint_kind not in PRODUCTION_BEAST_ABYSS_ACTIVE_KINDS:
                continue
            due_at = _at(
                business_day,
                checkpoint_time,
                occurrence.start_at.tzinfo,
            )
            if occurrence.start_at <= due_at <= occurrence.end_at:
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
    yunmeng_active_at = _at(
        business_day, YUNMENG_ACTIVE_TIME, occurrence.start_at.tzinfo
    )
    if (
        occurrence.activity_type == "yunmeng-trial"
        and occurrence.start_at <= yunmeng_active_at <= occurrence.end_at
    ):
        checkpoints.append(
            RankingCheckpoint(
                instance_key=occurrence.instance_key,
                activity_type=occurrence.activity_type,
                family=occurrence.family,
                runtime_id=occurrence.runtime_id,
                activity_id=occurrence.activity_id,
                checkpoint_kind=YUNMENG_ACTIVE_KIND,
                business_date=business_day.isoformat(),
                due_at=yunmeng_active_at,
            )
        )
    yunmeng_challenge_slots = (
        (YUNMENG_CHALLENGE_KIND, YUNMENG_CHALLENGE_TIME),
        (YUNMENG_CHALLENGE_EVENING_KIND, YUNMENG_CHALLENGE_EVENING_TIME),
    )
    if occurrence.activity_type == "yunmeng-trial":
        for checkpoint_kind, checkpoint_time in yunmeng_challenge_slots:
            due_at = _at(business_day, checkpoint_time, occurrence.start_at.tzinfo)
            if occurrence.start_at <= due_at <= occurrence.end_at:
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
    resource_kinds = (
        (LINGZHUANG_STRENGTHENING_KIND, time(5, 15),
         occurrence.activity_type == "lingzhuang-huadao" and occurrence.cross_count == 1),
        (
            RESOURCE_FREE_GIFT_KIND,
            RESOURCE_FREE_GIFT_TIME,
            occurrence.activity_type in RESOURCE_FREE_GIFT_ACTIVITY_TYPES,
        ),
        (DANDAO_REWARDS_KIND, DANDAO_REWARDS_TIME, occurrence.activity_type == "dandao-wending"),
        (DANDAO_RESOURCE_USE_KIND, DANDAO_RESOURCE_USE_TIME, occurrence.activity_type == "dandao-wending"),
        (DANDAO_TAKE_MEDICINE_KIND, DANDAO_RESOURCE_USE_TIME, occurrence.activity_type == "dandao-wending"),
        (YUANDING_GIFT_KIND, YUANDING_GIFT_TIME, occurrence.activity_type == "yuanding-sansheng"),
        *(
            (kind, unit_time, occurrence.activity_type == "yuanding-sansheng")
            for kind, unit_time in YUANDING_RESOURCE_UNIT_KINDS
        ),
    )
    if occurrence.family == "resource_rank":
        for checkpoint_kind, checkpoint_time, enabled in resource_kinds:
            if not enabled:
                continue
            # 资源榜的每日业务按「业务日」归属，不按开服那一秒做瞬时比较：
            # 2026-09-21 缘定三生开盘时间是 05:00:05，而礼包时间是 05:00:00，
            # 旧实现因差 5 秒把开盘当天的免费礼包整档丢掉（当天不会生成 checkpoint，
            # 永远不会补跑）。这里改为先按日期归属当天，再把 due_at 抬到活动实际开盘，
            # 保证不会在活动尚未开启时被判定到期。
            if not occurrence.start_at.date() <= business_day <= occurrence.end_at.date():
                continue
            # Medicine belongs to the occurrence, not each business day.
            checkpoint_day = (occurrence.start_at.date()
                              if checkpoint_kind == DANDAO_TAKE_MEDICINE_KIND else business_day)
            due_at = max(
                _at(checkpoint_day, checkpoint_time, occurrence.start_at.tzinfo),
                occurrence.start_at,
            )
            if due_at <= occurrence.end_at:
                checkpoints.append(
                    RankingCheckpoint(
                        instance_key=occurrence.instance_key,
                        activity_type=occurrence.activity_type,
                        family=occurrence.family,
                        runtime_id=occurrence.runtime_id,
                        activity_id=occurrence.activity_id,
                        checkpoint_kind=checkpoint_kind,
                        business_date=checkpoint_day.isoformat(),
                        due_at=due_at,
                    )
                )
    return tuple(checkpoints)


def due_ranking_checkpoints(
    occurrences: Iterable[RankingOccurrence],
    *,
    now: datetime,
    completed_keys: Iterable[tuple[str, str, str]] = (),
    production_only: bool = False,
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
                if checkpoint.checkpoint_kind in {
                    DAILY_RECONCILE_KIND,
                    MAGIC_INITIALIZATION_KIND,
                }
                or (
                    checkpoint.checkpoint_kind == MAGIC_MAIL_KIND
                    and business_day == local_now.date()
                )
                or (
                    checkpoint.checkpoint_kind == EXCHANGE_TAIL_KIND
                    and occurrence_exchange_tail_window_contains(
                        occurrence,
                        local_now,
                    )
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
                    XUTIAN_ACTIVE_KIND,
                    XUTIAN_OPEN_COLLECTION_KIND,
                    BEAST_ABYSS_FORMAL_KIND,
                    BEAST_ABYSS_INITIALIZATION_KIND,
                    BEAST_ABYSS_AUTO_CLEAR_KIND,
                    BEAST_ABYSS_MANUAL_CLEAR_KIND,
                    XIANMENG_ACTIVE_KIND,
                    TIANDI_YIJU_ACTIVE_KIND,
                    YUNMENG_ACTIVE_KIND,
                    YUNMENG_CHALLENGE_KIND,
                    YUNMENG_CHALLENGE_EVENING_KIND,
                    RESOURCE_FREE_GIFT_KIND,
                    DANDAO_REWARDS_KIND,
                    DANDAO_RESOURCE_USE_KIND,
                    DANDAO_TAKE_MEDICINE_KIND,
                    YUANDING_GIFT_KIND,
                    *YUANDING_RESOURCE_UNIT_KIND_SET,
                }
                and not (
                    checkpoint.checkpoint_kind
                    in {
                        BEAST_ABYSS_INITIALIZATION_KIND,
                        BEAST_ABYSS_FORMAL_KIND,
                        BEAST_ABYSS_AUTO_CLEAR_KIND,
                    }
                    and local_now.timetz().replace(tzinfo=None)
                    >= BEAST_ABYSS_AUTO_WINDOW_END
                )
            )
    ordered = sorted(
        (
            item
            for item in candidates
            if item.key not in completed and item.due_at <= now
            and (not production_only or ranking_checkpoint_is_production(item))
        ),
        key=lambda item: (
            item.due_at,
            item.activity_type,
            item.instance_key,
            item.checkpoint_kind,
            item.runtime_id,
        ),
    )
    # Several consecutive magic occurrences can describe the same account
    # mailbox on one business day.  Keep exactly one durable checkpoint key.
    unique: dict[tuple[str, str, str], RankingCheckpoint] = {}
    for item in ordered:
        unique.setdefault(item.key, item)
    return tuple(unique.values())


def next_ranking_lifecycle_time(
    occurrences: Iterable[RankingOccurrence],
    *,
    now: datetime,
    completed_keys: Iterable[tuple[str, str, str]] = (),
    retry_times: Iterable[datetime] = (),
    production_only: bool = False,
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
    # The game publishes new occurrences at 05:00. A midnight discovery with
    # no actionable checkpoint must still revisit today's newly opened list.
    publication_time = _at(local_now.date(), time(5, 0), local_now.tzinfo)
    if publication_time > local_now:
        candidates.append(publication_time)
    for occurrence in occurrences:
        local_day = now.astimezone(occurrence.start_at.tzinfo).date()
        for day in (local_day, local_day + timedelta(days=1)):
            for checkpoint in checkpoints_for_occurrence(
                occurrence,
                business_day=day,
            ):
                if (
                    checkpoint.key not in completed
                    and checkpoint.due_at > now
                    and (not production_only or ranking_checkpoint_is_production(checkpoint))
                ):
                    candidates.append(checkpoint.due_at)
    candidates.extend(value for value in retry_times if value > now)
    return min(candidates)


__all__ = [
    "BEAST_ABYSS_AUTO_CLEAR_KIND",
    "BEAST_ABYSS_FORMAL_KIND",
    "BEAST_ABYSS_INITIALIZATION_KIND",
    "BEAST_ABYSS_MANUAL_CLEAR_KIND",
    "DAILY_RECONCILE_KIND",
    "EXCHANGE_TAIL_KIND",
    "MAGIC_INITIALIZATION_KIND",
    "MAGIC_ACTIVE_KIND",
    "MAGIC_MAIL_KIND",
    "XUTIAN_ACTIVE_KIND",
    "XUTIAN_OPEN_COLLECTION_KIND",
    "XIANMENG_ACTIVE_KIND",
    "YUNMENG_ACTIVE_KIND",
    "YUNMENG_CHALLENGE_EVENING_KIND",
    "YUNMENG_CHALLENGE_KIND",
    "TIANDI_YIJU_ACTIVE_KIND",
    "TIANDI_YIJU_PLAYABLE_ACTIVITY_IDS",
    "RESOURCE_FREE_GIFT_KIND",
    "RESOURCE_FREE_GIFT_ACTIVITY_TYPES",
    "DANDAO_REWARDS_KIND",
    "DANDAO_RESOURCE_USE_KIND",
    "DANDAO_TAKE_MEDICINE_KIND",
    "YUANDING_GIFT_KIND",
    "RANKING_CAPABILITY_STATUS",
    "PRODUCTION_GAMEPLAY_CHECKPOINT_KINDS",
    "PRODUCTION_GAMEPLAY_EXCHANGE_TAIL_ACTIVITY_TYPES",
    "ranking_checkpoint_is_production",
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
    "occurrence_exchange_tail_window_contains",
    "occurrence_has_exchange_shop",
    "occurrence_relevant_on",
    "ranking_activity_identities",
]
