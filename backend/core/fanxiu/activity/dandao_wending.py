from __future__ import annotations

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
from backend.core.fanxiu.instrumentation.activity_rank_runtime import (
    prepare_activity_rank_runtime,
    read_activity_rank_runtime_snapshot,
)
from backend.models import FanxiuExchangeActivity, FanxiuExchangeRanking


# Public compatibility exports; the catalog owns all static interpretation.
from backend.core.fanxiu.catalog.dandao_wending import (
    DANDAO_WENDING_ACTIVITY_TYPE,
    DANDAO_WENDING_OFFICIAL_NAME,
    DANDAO_WENDING_METRIC,
    DANDAO_WENDING_METRIC_LABEL,
    DANDAO_WENDING_PRELIMINARY_ACTIVITY_ID,
    DANDAO_WENDING_FOUR_CROSS_ACTIVITY_ID,
    DANDAO_WENDING_OBSERVED_PRELIMINARY_TASK_IDS,
    DandaoRankRequest,
    DandaoStaticPlan,
    DandaoTaskMilestone,
    resolve_dandao_static_plan,
    load_dandao_observed_task_milestones,
    resolve_dandao_task_targets,
    resolve_dandao_live_task_ids,
    project_dandao_task_score,
)


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
                "rank_scope_identities": {"personal": {
                    "runtime_rank_activity_id": request.rank_activity_id,
                    "reward_activity_id": request.rank_activity_id,
                }},
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
    rows = project_dandao_task_score(task_ids, current_score, export_root=export_root)
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
    total_players = int(snapshot.get("rank_list_size") or 0)
    # rankListSize counts all participants; rankVOS is the game's bounded
    # leaderboard (typically the top 50).  Its own declared count is the
    # completeness contract for the rows we can actually observe.
    declared = int(snapshot.get("declared_rank_count") if snapshot.get("declared_rank_count") is not None else total_players)
    items = [dict(row) for row in snapshot.get("rankings") or [] if isinstance(row, dict)]
    # A freshly opened leaderboard may legitimately contain zero players before
    # anyone scores. The Runtime snapshot above proves the page was loaded;
    # zero declared rows and zero observed rows are a complete empty ranking.
    if declared < 0 or total_players < declared or len(items) != declared:
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
            "is_last_player": int(row["rank"]) == total_players and declared == total_players,
            "has_player": True,
            "raw_data": {
                "reported_rank_list_size": total_players,
                "loaded_player_count": len(items),
                "declared_rank_count": declared,
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
            "raw_data": {
                "unranked": True,
                "reported_rank_list_size": total_players,
                "loaded_player_count": len(items),
                "declared_rank_count": declared,
                "scope_complete": True,
                "source": "read_only_runtime_memory",
            },
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
            "total_players": int(snapshot.get("rank_list_size") or 0),
            "declared": int(snapshot.get("declared_rank_count") if snapshot.get("declared_rank_count") is not None else snapshot.get("rank_list_size") or 0),
            "loaded": int(snapshot.get("loaded_rank_count") if snapshot.get("loaded_rank_count") is not None else len(snapshot.get("rankings") or [])),
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
