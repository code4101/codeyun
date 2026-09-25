"""丹道问鼎静态规则与观测映射，不依赖数据库或游戏执行器。

榜单身份、任务梯度和条件解析在这里维护。Runtime 提供本期成员与进度，
活动层负责保存实例和榜单；静态配置不能自行选定本期梯度。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from backend.core.fanxiu.catalog.resources import resolve_fanxiu_export_root


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


def resolve_dandao_task_targets(
    activity_id: int, task_ids: tuple[int, ...], *,
    export_root: str | Path | None = None,
) -> tuple[int, ...]:
    """Resolve thresholds for the already-authorized live task membership."""
    configs = {
        int(row.get("id") or 0): row
        for row in _load_config_rows(resolve_fanxiu_export_root(export_root), "ActiveTask")
        if int(row.get("activityId") or 0) == int(activity_id)
    }
    targets = []
    for task_id in task_ids:
        row = configs.get(task_id)
        if row is None:
            raise ValueError(f"丹道任务配置缺失：{task_id}")
        values = [int(m.group(1)) for condition in row.get("finishCondition") or []
                  if (m := _MEDICAL_EXP_CONDITION.fullmatch(str(condition or "")))]
        if len(values) != 1 or values[0] <= 0:
            raise ValueError(f"丹道任务熟练度目标无效：{task_id}")
        targets.append(values[0])
    if not targets or targets != sorted(set(targets)):
        raise ValueError("丹道任务梯度必须严格递增")
    return tuple(targets)


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


def project_dandao_task_score(
    task_ids: list[int], current_score: int, *,
    export_root: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Project already-identified preliminary tasks from a shared metric.

    These rows are score-based estimates, not proof of QuestMgr completion.
    The activity API retains that distinction in its completeness/evidence.
    """
    root = resolve_fanxiu_export_root(export_root)
    configs = {
        int(row.get("id") or 0): row
        for row in _load_config_rows(root, "ActiveTask")
        if int(row.get("activityId") or 0) == DANDAO_WENDING_PRELIMINARY_ACTIVITY_ID
    }
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
    return rows
