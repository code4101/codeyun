from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Literal

from sqlmodel import Session, col, select

from backend.core.fanxiu.activity.daily_activity_sync import (
    load_worldline_activity_schedule_snapshot,
)
from backend.core.fanxiu.activity.exchange_event import (
    is_exchange_activity_active,
    list_exchange_activity_snapshot,
    replace_exchange_rankings,
    upsert_exchange_activity_snapshot,
)
from backend.core.fanxiu.catalog.resources import resolve_fanxiu_export_root
from backend.core.fanxiu.instrumentation.activity_rank_runtime import (
    prepare_activity_rank_runtime,
    read_activity_rank_runtime_snapshot,
)
from backend.models import FanxiuExchangeActivity, FanxiuExchangeRanking


DANDAO_WENDING_ACTIVITY_TYPE = "dandao-wending"
DANDAO_WENDING_OFFICIAL_NAME = "丹道问鼎"
DANDAO_WENDING_METRIC = "MedicalExp"
DANDAO_WENDING_METRIC_LABEL = "炼丹熟练度"
DANDAO_WENDING_PRELIMINARY_ACTIVITY_ID = 1043111
DANDAO_WENDING_FOUR_CROSS_ACTIVITY_ID = 4043101

_PRELIMINARY_VIEW = "ActivityRankMainView"
_CROSS_VIEW = "ActivityRankServerMainView"
_MEDICAL_EXP_CONDITION = re.compile(r"MedicalExp\|(\d+)")
# #598 on 2026-08-19 showed fourteen current task rows.  ActiveTask contains
# exactly one fourteen-row ladder for this activity; retain the inferred IDs as
# explicit evidence while the generic QuestEntryVO Runtime reader is absent.
DANDAO_WENDING_OBSERVED_PRELIMINARY_TASK_IDS = tuple(range(104311151, 104311165))


@dataclass(frozen=True)
class DandaoRankRequest:
    """One exact rank cache that the native page asks the server to populate."""

    scope: Literal["personal", "plane"]
    role: Literal["primary", "comparative"]
    subject: Literal["role", "server"]
    rank_activity_id: int
    activity_list_subtype: int
    reward_group: int
    expected_vo_types: tuple[str, ...]


@dataclass(frozen=True)
class DandaoStaticPlan:
    """Configuration-proven read plan; it does not open a page or load data."""

    activity_id: int
    phase: Literal["preliminary", "cross"]
    page_view: str
    task_activity_id: int
    metric: str
    metric_label: str
    rank_requests: tuple[DandaoRankRequest, ...]


@dataclass(frozen=True)
class DandaoTaskMilestone:
    task_id: int
    order: int
    name: str
    target: int
    progress: int
    status: int
    finished: bool
    rewards: tuple[str, ...]


def _load_config_rows(root: Path, table: str) -> list[dict[str, Any]]:
    path = root / "parsed_configs" / table / "rows.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError) as exc:
        raise ValueError(f"无法读取凡修 {table} 配置") from exc
    rows: Any = payload if isinstance(payload, list) else payload.get("rows", payload)
    if isinstance(rows, dict):
        rows = list(rows.values())
    if not isinstance(rows, list):
        raise ValueError(f"凡修 {table} 配置格式无效")
    return [row for row in rows if isinstance(row, dict)]


def _jump_view(row: dict[str, Any]) -> str:
    prefix, separator, view = str(row.get("jump") or "").partition("|")
    if prefix != "OpenWin" or not separator or not view:
        raise ValueError("丹道问鼎活动缺少可验证的原生页面入口")
    return view


def resolve_dandao_static_plan(
    activity_id: int,
    *,
    export_root: str | Path | None = None,
) -> DandaoStaticPlan:
    """Resolve the exact native rank requests from Activity/ActivityList config.

    The result is intentionally limited to request identities.  VO types remain
    expectations until a live standard observation proves the current payload.
    """

    root = resolve_fanxiu_export_root(export_root)
    activities = {
        int(row.get("id") or 0): row for row in _load_config_rows(root, "Activity")
    }
    activity_lists = {
        int(row.get("id") or 0): row
        for row in _load_config_rows(root, "ActivityList")
    }
    activity = activities.get(int(activity_id))
    if activity is None or int(activity.get("sameActGroup") or 0) != 12:
        raise ValueError(f"活动 {int(activity_id)} 不是可验证的丹道问鼎活动")
    page_view = _jump_view(activity)

    if page_view == _PRELIMINARY_VIEW:
        subtype = int(activity.get("subType") or 0)
        list_row = activity_lists.get(subtype, {})
        if (
            int(activity.get("baseId") or 0) != 43110
            or subtype != 31
            or int(list_row.get("subtype") or 0) != 1
        ):
            raise ValueError("丹道问鼎预赛模板身份不一致")
        reward_group = int(activity.get("rewardGroup") or 0)
        if reward_group <= 0:
            raise ValueError("丹道问鼎预赛缺少排名奖励组")
        requests = (
            DandaoRankRequest(
                scope="personal",
                role="primary",
                subject="role",
                rank_activity_id=int(activity_id),
                activity_list_subtype=subtype,
                reward_group=reward_group,
                expected_vo_types=("ActivityRankPersonalVO",),
            ),
        )
        return DandaoStaticPlan(
            activity_id=int(activity_id),
            phase="preliminary",
            page_view=page_view,
            task_activity_id=int(activity_id),
            metric=DANDAO_WENDING_METRIC,
            metric_label=DANDAO_WENDING_METRIC_LABEL,
            rank_requests=requests,
        )

    if page_view != _CROSS_VIEW:
        raise ValueError(f"丹道问鼎使用了未知原生页面：{page_view}")
    if int(activity.get("baseId") or 0) != 43100:
        raise ValueError("丹道问鼎跨服父模板身份不一致")
    cross_count = int(activity.get("crossGroup") or 0)
    if cross_count <= 1:
        raise ValueError("丹道问鼎跨服父模板缺少有效跨服组")

    requests_by_scope: dict[str, DandaoRankRequest] = {}
    for raw_follow_id in activity.get("follow") or []:
        follow_id = int(raw_follow_id or 0)
        follow = activities.get(follow_id)
        if follow is None:
            raise ValueError(f"丹道问鼎跨服榜单引用不存在：{follow_id}")
        if (
            int(follow.get("sameActGroup") or 0) != 12
            or int(follow.get("crossGroup") or 0) != cross_count
        ):
            raise ValueError(f"丹道问鼎跨服榜单引用身份不一致：{follow_id}")
        list_subtype = int(follow.get("subType") or 0)
        list_row = activity_lists.get(list_subtype, {})
        rank_kind = int(list_row.get("subtype") or 0)
        if rank_kind == 1:
            scope, role, subject = "personal", "primary", "role"
            expected_vo_types = ("ActivityRankPersonalVO",)
        elif rank_kind == 4:
            scope, role, subject = "plane", "comparative", "server"
            expected_vo_types = ("ActivityRankCrossServerVO",)
        else:
            raise ValueError(f"丹道问鼎榜单 {follow_id} 的榜单类型未知")
        if scope in requests_by_scope:
            raise ValueError(f"丹道问鼎跨服模板重复声明 {scope} 榜")
        reward_group = int(follow.get("rewardGroup") or 0)
        if reward_group <= 0:
            raise ValueError(f"丹道问鼎榜单 {follow_id} 缺少排名奖励组")
        requests_by_scope[scope] = DandaoRankRequest(
            scope=scope,
            role=role,
            subject=subject,
            rank_activity_id=follow_id,
            activity_list_subtype=list_subtype,
            reward_group=reward_group,
            expected_vo_types=expected_vo_types,
        )
    if set(requests_by_scope) != {"personal", "plane"}:
        raise ValueError("丹道问鼎跨服模板必须同时声明个人榜和位面榜")
    return DandaoStaticPlan(
        activity_id=int(activity_id),
        phase="cross",
        page_view=page_view,
        task_activity_id=int(activity_id),
        metric=DANDAO_WENDING_METRIC,
        metric_label=DANDAO_WENDING_METRIC_LABEL,
        rank_requests=(requests_by_scope["personal"], requests_by_scope["plane"]),
    )


def load_dandao_observed_task_milestones(
    observed_tasks: list[dict[str, Any]],
    *,
    task_activity_id: int,
    export_root: str | Path | None = None,
) -> list[DandaoTaskMilestone]:
    """Join only task IDs declared by the current Runtime occurrence.

    ActiveTask contains several ladders for one parent activity.  Static rows
    alone cannot identify the live server tier, so missing observations fail
    closed instead of selecting the longest or newest ladder.
    """

    if not observed_tasks:
        raise ValueError("丹道问鼎本期任务尚未加载，拒绝从多套静态梯度猜测")
    root = resolve_fanxiu_export_root(export_root)
    configs = {
        int(row.get("id") or 0): row
        for row in _load_config_rows(root, "ActiveTask")
        if int(row.get("activityId") or 0) == int(task_activity_id)
    }
    milestones: list[DandaoTaskMilestone] = []
    seen: set[int] = set()
    for observed in observed_tasks:
        task_id = int(observed.get("taskId") or observed.get("task_id") or 0)
        if task_id <= 0 or task_id in seen:
            raise ValueError("丹道问鼎本期任务 ID 缺失或重复")
        seen.add(task_id)
        config = configs.get(task_id)
        if config is None:
            raise ValueError(f"丹道问鼎本期任务缺少静态配置：{task_id}")
        targets = []
        for condition in config.get("finishCondition") or []:
            match = _MEDICAL_EXP_CONDITION.fullmatch(str(condition or ""))
            if match is not None:
                targets.append(int(match.group(1)))
        if len(targets) != 1 or targets[0] <= 0:
            raise ValueError(f"丹道问鼎任务 {task_id} 的炼丹熟练度条件无效")
        progress_rows = observed.get("progressList") or observed.get("progress_list") or []
        if isinstance(progress_rows, dict):
            progress_rows = progress_rows.get("items") or []
        runtime_progress = next(
            (row for row in progress_rows if isinstance(row, dict)), {}
        )
        target = targets[0]
        if int(runtime_progress.get("target") or 0) != target:
            raise ValueError(f"丹道问鼎任务 {task_id} 的 Runtime/配置目标不一致")
        progress = int(runtime_progress.get("progress") or 0)
        milestones.append(
            DandaoTaskMilestone(
                task_id=task_id,
                order=int(config.get("sort") or 0),
                name=str(config.get("name_plain") or config.get("name") or ""),
                target=target,
                progress=progress,
                status=int(observed.get("status") or 0),
                finished=bool(runtime_progress.get("finish")) or progress >= target,
                rewards=tuple(str(value) for value in (config.get("reward") or [])),
            )
        )
    milestones.sort(key=lambda row: (row.target, row.order, row.task_id))
    return milestones


def resolve_dandao_live_task_ids(
    activity_id: int,
    *,
    task_entries: list[dict[str, Any]],
    finished_task_ids: list[int],
    export_root: str | Path | None = None,
) -> tuple[int, ...]:
    """Resolve this occurrence's exact ladder from live QuestMgr membership.

    ``ActiveTask`` retains mutually exclusive ladders for the same parent
    activity.  Current ``taskEntryVOs`` plus ``finishTasks`` are the only
    membership authority; static rows merely validate their order and metric.
    """

    root = resolve_fanxiu_export_root(export_root)
    configs = {
        int(row.get("id") or 0): row
        for row in _load_config_rows(root, "ActiveTask")
        if int(row.get("activityId") or 0) == int(activity_id)
    }
    if not configs:
        raise ValueError(f"丹道问鼎活动 {int(activity_id)} 缺少任务配置")
    represented: set[int] = set()
    for row in task_entries:
        if not isinstance(row, dict):
            continue
        task_id = int(row.get("taskId") or row.get("task_id") or 0)
        if task_id in configs:
            represented.add(task_id)
    represented.update(
        task_id
        for value in finished_task_ids
        if (task_id := int(value or 0)) in configs
    )
    if not represented:
        raise ValueError("丹道问鼎本期 QuestMgr 任务尚未加载")

    ordered: list[tuple[int, int]] = []
    seen_orders: set[int] = set()
    for task_id in represented:
        row = configs[task_id]
        order = int(row.get("sort") or 0)
        targets = [
            int(match.group(1))
            for value in row.get("finishCondition") or []
            if (match := _MEDICAL_EXP_CONDITION.fullmatch(str(value or "")))
            is not None
        ]
        if order <= 0 or len(targets) != 1 or targets[0] <= 0:
            raise ValueError(f"丹道问鼎任务 {task_id} 的顺序或熟练度条件无效")
        if order in seen_orders:
            raise ValueError(
                f"丹道问鼎 Runtime 同时出现互斥梯度的第 {order} 档任务"
            )
        seen_orders.add(order)
        ordered.append((order, task_id))
    ordered.sort()
    actual_orders = [order for order, _task_id in ordered]
    if actual_orders != list(range(1, len(ordered) + 1)):
        raise ValueError(
            f"丹道问鼎本期任务梯度不完整：顺序 {actual_orders}"
        )
    return tuple(task_id for _order, task_id in ordered)


def _current_preliminary_occurrence() -> tuple[dict[str, Any], dict[str, Any]]:
    snapshot = load_worldline_activity_schedule_snapshot()
    occurrences = [
        dict(item)
        for item in snapshot.get("occurrences") or []
        if isinstance(item, dict)
        and int(item.get("activity_id") or 0) == DANDAO_WENDING_PRELIMINARY_ACTIVITY_ID
    ]
    if not occurrences:
        raise ValueError("尚未采集到丹道问鼎预赛活动实例")
    occurrence = max(
        occurrences,
        key=lambda item: (
            str(item.get("start_at") or ""),
            int(item.get("state") or 0),
        ),
    )
    if str(occurrence.get("name") or "") != DANDAO_WENDING_OFFICIAL_NAME:
        raise ValueError("丹道问鼎世界线名称与静态身份不一致")
    if not occurrence.get("identity_complete"):
        raise ValueError("丹道问鼎世界线身份尚未完整解析")
    return occurrence, snapshot


def ensure_dandao_wending_activity(session: Session) -> str:
    """Project the latest saved, lossless Runtime occurrence into the page DB."""

    occurrence, snapshot = _current_preliminary_occurrence()
    plan = resolve_dandao_static_plan(DANDAO_WENDING_PRELIMINARY_ACTIVITY_ID)
    request = plan.rank_requests[0]
    raw_occurrence = (
        dict(occurrence.get("raw") or {})
        if isinstance(occurrence.get("raw"), dict)
        else {}
    )
    start_date = str(occurrence.get("start_date") or "")
    end_date = str(occurrence.get("end_date") or start_date)
    date.fromisoformat(start_date)
    date.fromisoformat(end_date)
    existing = session.exec(
        select(FanxiuExchangeActivity).where(
            FanxiuExchangeActivity.activity_type == DANDAO_WENDING_ACTIVITY_TYPE,
            FanxiuExchangeActivity.cross_count == 1,
            FanxiuExchangeActivity.start_date == start_date,
            FanxiuExchangeActivity.end_date == end_date,
        )
    ).first()
    # A GET materialization must never replace a newer collected ranking's
    # captured_at/source_kind with the older schedule discovery timestamp.
    if existing is not None:
        return existing.id
    return upsert_exchange_activity_snapshot(
        session,
        {
            "activity_type": DANDAO_WENDING_ACTIVITY_TYPE,
            "cross_count": 1,
            "start_date": start_date,
            "end_date": end_date,
            "game_rank_activity_id": request.rank_activity_id,
            "currency_name": DANDAO_WENDING_METRIC_LABEL,
            "captured_at": str(snapshot.get("captured_at") or ""),
            "source_kind": "saved_worldline_runtime_facts",
            "resource_strategy": {
                "score_metric": DANDAO_WENDING_METRIC_LABEL,
                "task_metric": "本期炼丹熟练度里程碑",
                "phase": "预赛",
                "native_page": plan.page_view,
            },
            "evidence": {
                "official_name": DANDAO_WENDING_OFFICIAL_NAME,
                "phase": plan.phase,
                "same_server": True,
                "rank_scope_activity_ids": {"personal": request.rank_activity_id},
                "rank_reward_group": request.reward_group,
                "game_activity_id": DANDAO_WENDING_PRELIMINARY_ACTIVITY_ID,
                "period_start_time_ms": int(raw_occurrence.get("startTime") or 0),
                # The real #599 reward page selected the open-day >=31 tier.
                # Only the tier boundary is persisted; no fabricated exact
                # server age is claimed.
                "server_day": 31,
                "server_day_evidence": "#599 selected ActivityListReward serverDay [31,9999]",
                "worldline_occurrence": occurrence,
                "worldline_snapshot": {
                    "captured_at": snapshot.get("captured_at"),
                    "source_kind": snapshot.get("source_kind"),
                    "source_evidence": snapshot.get("source_evidence") or {},
                },
                "observed_task_ids": list(DANDAO_WENDING_OBSERVED_PRELIMINARY_TASK_IDS),
                "task_membership_evidence": "#598 visible row count + unique 14-row ActiveTask ladder",
            },
        },
    )


def load_dandao_wending_tasks(
    session: Session,
    *,
    activity_id: str,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    """Expose the observed ladder without pretending QuestEntryVO was decoded."""

    activity = session.get(FanxiuExchangeActivity, activity_id)
    if activity is None or activity.activity_type != DANDAO_WENDING_ACTIVITY_TYPE:
        raise ValueError("丹道问鼎活动不存在")
    evidence = dict(activity.evidence or {})
    task_ids = [int(value) for value in evidence.get("observed_task_ids") or []]
    if task_ids != list(DANDAO_WENDING_OBSERVED_PRELIMINARY_TASK_IDS):
        raise ValueError("丹道问鼎本期任务成员证据不完整")
    root = resolve_fanxiu_export_root(export_root)
    configs = {
        int(row.get("id") or 0): row
        for row in _load_config_rows(root, "ActiveTask")
        if int(row.get("activityId") or 0) == DANDAO_WENDING_PRELIMINARY_ACTIVITY_ID
    }
    self_row = session.exec(
        select(FanxiuExchangeRanking)
        .where(
            FanxiuExchangeRanking.activity_id == activity.id,
            FanxiuExchangeRanking.ranking_scope == "personal",
            FanxiuExchangeRanking.is_self == True,  # noqa: E712
        )
        .order_by(col(FanxiuExchangeRanking.captured_at).desc())
    ).first()
    current_score = max(0, int(self_row.score if self_row is not None else 0))
    rows: list[dict[str, Any]] = []
    for task_id in task_ids:
        config = configs.get(task_id)
        if config is None:
            raise ValueError(f"丹道问鼎本期任务缺少静态配置：{task_id}")
        targets = [
            int(match.group(1))
            for value in config.get("finishCondition") or []
            if (match := _MEDICAL_EXP_CONDITION.fullmatch(str(value or ""))) is not None
        ]
        if len(targets) != 1 or targets[0] <= 0:
            raise ValueError(f"丹道问鼎任务 {task_id} 的炼丹熟练度条件无效")
        target = targets[0]
        rows.append(
            {
                "task_id": task_id,
                "order": int(config.get("sort") or 0),
                "name": str(config.get("name_plain") or config.get("name") or ""),
                "target": target,
                "progress": current_score,
                "status": 4 if current_score >= target else 3,
                "finished": current_score >= target,
                "must_get": str(config.get("corner_plain") or config.get("corner") or "") == "必拿",
                "rewards": [str(value) for value in config.get("reward") or []],
            }
        )
    rows.sort(key=lambda row: (row["target"], row["order"], row["task_id"]))
    return {
        "captured_at": str(self_row.captured_at if self_row is not None else activity.captured_at),
        "complete": False,
        "reason": "任务成员已由真实页面14条记录与唯一静态梯度对齐；进度按同指标榜单分数投影，QuestEntryVO Runtime 尚未接入",
        "items": rows,
        "evidence": {
            "membership": evidence.get("task_membership_evidence"),
            "progress_source": "personal_rank_score",
        },
    }


def _runtime_rank_rows(
    snapshot: dict[str, Any],
    *,
    scope: Literal["personal", "plane"] = "personal",
) -> list[dict[str, Any]]:
    if not snapshot.get("ok") or not snapshot.get("complete"):
        raise ValueError(str(snapshot.get("reason") or "丹道问鼎榜单尚未加载"))
    declared = int(snapshot.get("rank_list_size") or 0)
    items = [dict(row) for row in snapshot.get("rankings") or [] if isinstance(row, dict)]
    if declared <= 0 or len(items) != declared:
        raise ValueError(f"丹道问鼎{scope}榜不完整：{len(items)}/{declared}")
    ranks = [int(row.get("rank") or 0) for row in items]
    if ranks != list(range(1, declared + 1)):
        raise ValueError(f"丹道问鼎{scope}榜排名不连续")
    self_row = dict(snapshot.get("self_ranking") or {})
    self_rank = int(self_row.get("rank") or 0)
    rows = [
        {
            "ranking_scope": scope,
            "rank": int(row["rank"]),
            "score": int(row.get("score") or 0),
            "role_key": str(row.get("role_key") or f"{scope}:{row['rank']}"),
            "name": str(row.get("name") or ""),
            "server_id": row.get("server_id"),
            "server_name": str(row.get("server_name") or ""),
            "club_name": str(row.get("club_name") or ""),
            "is_self": int(row["rank"]) == self_rank and self_rank > 0,
            "is_reward_guard": False,
            "is_last_player": int(row["rank"]) == declared,
            "has_player": True,
            "raw_data": {
                "reported_rank_list_size": declared,
                "loaded_player_count": len(items),
                "scope_complete": True,
                "source": "read_only_runtime_memory",
            },
        }
        for row in items
    ]
    if self_rank <= 0:
        rows.append(
            {
                "ranking_scope": scope,
                "rank": 0,
                "score": int(self_row.get("score") or 0),
                "role_key": str(self_row.get("role_key") or f"{scope}:self-unranked"),
                "name": str(self_row.get("name") or ""),
                "server_id": self_row.get("server_id"),
                "server_name": str(self_row.get("server_name") or ""),
                "club_name": str(self_row.get("club_name") or ""),
                "is_self": True,
                "is_reward_guard": False,
                "is_last_player": False,
                "has_player": False,
                "raw_data": {"unranked": True, "source": "read_only_runtime_memory"},
            }
        )
    return rows


def collect_and_store_dandao_wending_activity(
    session: Session,
    *,
    activity_id: str | None = None,
    today: date | None = None,
) -> Any:
    selected_id = activity_id or ensure_dandao_wending_activity(session)
    activity = session.get(FanxiuExchangeActivity, selected_id)
    if activity is None or activity.activity_type != DANDAO_WENDING_ACTIVITY_TYPE:
        raise ValueError("丹道问鼎活动不存在")
    if not is_exchange_activity_active(activity, today=today):
        raise ValueError("丹道问鼎活动不在有效日期内")
    evidence = dict(activity.evidence or {})
    worldline_occurrence = evidence.get("worldline_occurrence")
    if not isinstance(worldline_occurrence, dict):
        worldline_occurrence = {}
    game_activity_id = int(
        evidence.get("game_activity_id")
        or worldline_occurrence.get("activity_id")
        or 0
    )
    plan = resolve_dandao_static_plan(game_activity_id)
    rank_ids = [request.rank_activity_id for request in plan.rank_requests]
    snapshots = {
        request.scope: read_activity_rank_runtime_snapshot(request.rank_activity_id)
        for request in plan.rank_requests
    }
    if any(
        not snapshot.get("ok")
        and snapshot.get("error_code") in {"process_cache_miss", "root_cache_miss"}
        for snapshot in snapshots.values()
    ):
        recovery = prepare_activity_rank_runtime(rank_ids)
        if not recovery.get("ok"):
            raise ValueError(str(recovery.get("reason") or "丹道问鼎榜单 Runtime 恢复失败"))
        snapshots = {
            request.scope: read_activity_rank_runtime_snapshot(request.rank_activity_id)
            for request in plan.rank_requests
        }
    captured_values: list[str] = []
    rows: list[dict[str, Any]] = []
    completeness: dict[str, dict[str, int]] = {}
    runtime_evidence: dict[str, dict[str, Any]] = {}
    for request in plan.rank_requests:
        snapshot = snapshots[request.scope]
        captured_at = str(snapshot.get("captured_at") or "")
        if not (
            captured_at[:10]
            and activity.start_date <= captured_at[:10] <= activity.end_date
        ):
            raise ValueError(
                f"丹道问鼎{request.scope}榜 Runtime 事实不属于所选活动周期"
            )
        captured_values.append(captured_at)
        rows.extend(_runtime_rank_rows(snapshot, scope=request.scope))
        completeness[request.scope] = {
            "declared": int(snapshot.get("rank_list_size") or 0),
            "loaded": int(snapshot.get("loaded_rank_count") or 0),
        }
        runtime_evidence[request.scope] = dict(snapshot.get("evidence") or {})
    captured_at = max(captured_values)
    activity.captured_at = captured_at
    activity.source_kind = "read_only_runtime_memory"
    activity.evidence = {
        **evidence,
        "rank_scope_completeness": completeness,
        "rank_runtime": runtime_evidence,
    }
    session.add(activity)
    replace_exchange_rankings(
        session,
        activity_type=DANDAO_WENDING_ACTIVITY_TYPE,
        activity_id=activity.id,
        rows=rows,
        captured_at=captured_at,
    )
    return list_exchange_activity_snapshot(
        session,
        activity_type=DANDAO_WENDING_ACTIVITY_TYPE,
        activity_id=activity.id,
    ).selected_activity


__all__ = [
    "DANDAO_WENDING_ACTIVITY_TYPE",
    "DANDAO_WENDING_OFFICIAL_NAME",
    "DANDAO_WENDING_PRELIMINARY_ACTIVITY_ID",
    "DANDAO_WENDING_FOUR_CROSS_ACTIVITY_ID",
    "resolve_dandao_static_plan",
    "resolve_dandao_live_task_ids",
    "load_dandao_observed_task_milestones",
    "ensure_dandao_wending_activity",
    "load_dandao_wending_tasks",
    "collect_and_store_dandao_wending_activity",
]
