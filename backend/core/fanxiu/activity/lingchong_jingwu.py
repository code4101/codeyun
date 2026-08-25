from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlmodel import Session, col, select

from backend.core.fanxiu.activity.exchange_event import (
    is_exchange_activity_active,
    list_exchange_activity_snapshot,
    replace_exchange_rankings,
    upsert_exchange_activity_snapshot,
)
from backend.core.fanxiu.activity.standard_observation import read_activity_rank_fact
from backend.core.fanxiu.catalog.resources import resolve_fanxiu_export_root
from backend.core.fanxiu.instrumentation.backpack import read_backpack_item_counts
from backend.models import (
    FanxiuExchangeActivity,
    FanxiuPacketBusinessRecord,
)


LINGCHONG_JINGWU_ACTIVITY_TYPE = "lingchong-jingwu"
LINGCHONG_JINGWU_OFFICIAL_NAME = "灵宠竞武"
LINGCHONG_JINGWU_USER_ALIAS = "灵武竞宠"
LINGCHONG_JINGWU_PARENT_ACTIVITY_ID = 8042901
LINGCHONG_JINGWU_PERSONAL_RANK_ID = 42905
LINGCHONG_JINGWU_PLANE_RANK_ID = 42906
TALENT_PILL_ITEM_ID = 9070095
_PET_TALENT_CONDITION = re.compile(r"PetTalent\|(\d+)")


class LingchongJingwuReferences(BaseModel):
    activity_type: Literal["lingchong-jingwu"] = LINGCHONG_JINGWU_ACTIVITY_TYPE
    official_name: str = LINGCHONG_JINGWU_OFFICIAL_NAME
    user_alias: str = LINGCHONG_JINGWU_USER_ALIAS
    parent_activity_id: int
    cross_count: int
    personal_rank_activity_id: int
    plane_rank_activity_id: int


class LingchongJingwuTaskMilestone(BaseModel):
    task_id: int
    order: int
    name: str
    target: int
    progress: int
    status: int
    finished: bool
    talent_pill_count: int
    rewards: list[str] = Field(default_factory=list)


class LingchongJingwuResourceItem(BaseModel):
    item_id: int
    name: str
    quality: int
    count: int
    aptitude_gain_by_pet_type: dict[int, int] = Field(default_factory=dict)
    minimum_aptitude_gain: int
    maximum_aptitude_gain: int


class LingchongJingwuResourceSnapshot(BaseModel):
    activity_id: str
    captured_at: str
    source_kind: str = "readonly_backpack_runtime"
    complete: bool = True
    items: list[LingchongJingwuResourceItem]
    total_count: int
    reason: str = ""
    evidence: dict[str, Any] = Field(default_factory=dict)


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
    return [dict(row) for row in rows if isinstance(row, dict)]


def resolve_lingchong_jingwu_references(
    *,
    parent_activity_id: int = LINGCHONG_JINGWU_PARENT_ACTIVITY_ID,
    export_root: str | Path | None = None,
) -> LingchongJingwuReferences:
    """Resolve the 8-server event from the game's Activity graph.

    The public alias is intentionally not used as a config identity.  The
    authoritative current client and packet facts call the activity
    ``灵宠竞武``; ``灵武竞宠`` remains a user-facing search alias.
    """

    root = resolve_fanxiu_export_root(export_root)
    rows = _load_config_rows(root, "Activity")
    rows_by_id = {int(row.get("id") or 0): row for row in rows}
    parent = rows_by_id.get(int(parent_activity_id))
    if parent is None:
        raise ValueError(f"灵宠竞武活动配置不存在：{int(parent_activity_id)}")
    official_name = str(parent.get("name_plain") or parent.get("name") or "")
    if official_name != LINGCHONG_JINGWU_OFFICIAL_NAME:
        raise ValueError(
            f"活动 {int(parent_activity_id)} 身份不符：期望{LINGCHONG_JINGWU_OFFICIAL_NAME}，实际{official_name or '空'}"
        )
    cross_count = int(parent.get("crossGroup") or 0)
    if cross_count != 8:
        raise ValueError(f"灵宠竞武活动不是 8 跨：{cross_count}")
    follow = [int(value) for value in (parent.get("follow") or [])]
    if len(follow) != 2:
        raise ValueError("8跨灵宠竞武配置缺少个人榜/位面榜引用")

    personal_id: int | None = None
    plane_id: int | None = None
    for rank_id in follow:
        rank = rows_by_id.get(rank_id)
        if rank is None:
            raise ValueError(f"8跨灵宠竞武引用了不存在的榜单：{rank_id}")
        base_id = int(rank.get("baseId") or 0)
        if base_id == 42901:
            personal_id = rank_id
        elif base_id == 42902:
            plane_id = rank_id
    if personal_id is None or plane_id is None:
        raise ValueError("8跨灵宠竞武的个人榜/位面榜身份不完整")
    return LingchongJingwuReferences(
        parent_activity_id=int(parent_activity_id),
        cross_count=cross_count,
        personal_rank_activity_id=personal_id,
        plane_rank_activity_id=plane_id,
    )


def build_lingchong_jingwu_activity_payload(
    worldline: dict[str, Any],
    *,
    references: LingchongJingwuReferences,
    captured_at: str,
) -> dict[str, Any]:
    """Build the generic activity-store payload from one exact worldline fact."""

    if int(worldline.get("activityId") or 0) != references.parent_activity_id:
        raise ValueError("世界线事实不是当前 8跨灵宠竞武父活动")
    if str(worldline.get("name") or "") != references.official_name:
        raise ValueError("世界线活动名称与静态配置不一致")
    if int(worldline.get("serverCount") or 0) != references.cross_count:
        raise ValueError("世界线活动跨数与静态配置不一致")

    def event_date(key: str) -> str:
        timestamp = int(worldline.get(key) or 0)
        if timestamp <= 0:
            raise ValueError("8跨灵宠竞武世界线时间事实不完整")
        return datetime.fromtimestamp(timestamp / 1000).astimezone().date().isoformat()

    return {
        "activity_type": references.activity_type,
        "cross_count": references.cross_count,
        "start_date": event_date("startTime"),
        "end_date": event_date("endTime"),
        "game_rank_activity_id": references.personal_rank_activity_id,
        "currency_name": "灵兽资质积分",
        "captured_at": str(captured_at),
        "source_kind": "standard_runtime_facts",
        "resource_strategy": {
            "resource_metric": "饲灵丸库存与各灵兽类型资质增量",
            "task_metric": "本期已下发 PetTalent 任务进度",
        },
        "evidence": {
            "official_name": references.official_name,
            "user_alias": references.user_alias,
            "parent_activity_id": references.parent_activity_id,
            "rank_scope_activity_ids": {
                "personal": references.personal_rank_activity_id,
                "plane": references.plane_rank_activity_id,
            },
            "worldline": dict(worldline),
        },
    }


def _reward_item_count(rewards: list[str], item_id: int) -> int:
    total = 0
    for reward in rewards:
        match = re.fullmatch(r"Item\|(\d+)_(-?\d+)(?:_.*)?", reward)
        if match is not None and int(match.group(1)) == int(item_id):
            total += int(match.group(2))
    return total


def load_lingchong_jingwu_task_milestones(
    observed_tasks: list[dict[str, Any]],
    *,
    parent_activity_id: int = LINGCHONG_JINGWU_PARENT_ACTIVITY_ID,
    export_root: str | Path | None = None,
) -> list[LingchongJingwuTaskMilestone]:
    """Join this instance's exact QuestEntryVO rows to ActiveTask config.

    ActiveTask retains multiple ladders for the same parent activity.  Picking
    the longest or newest ladder can silently display tasks from another
    server tier.  Therefore the current packet/Runtime task IDs are mandatory.
    """

    if not observed_tasks:
        raise ValueError("灵宠竞武本期任务尚未加载，拒绝从多套静态梯度猜测")
    root = resolve_fanxiu_export_root(export_root)
    rows_by_id = {
        int(row.get("id") or 0): row
        for row in _load_config_rows(root, "ActiveTask")
        if int(row.get("activityId") or 0) == int(parent_activity_id)
    }
    milestones: list[LingchongJingwuTaskMilestone] = []
    seen: set[int] = set()
    for observed in observed_tasks:
        task_id = int(observed.get("taskId") or observed.get("task_id") or 0)
        if task_id <= 0 or task_id in seen:
            raise ValueError("灵宠竞武本期任务 ID 缺失或重复")
        seen.add(task_id)
        config = rows_by_id.get(task_id)
        if config is None:
            raise ValueError(f"灵宠竞武本期任务缺少静态配置：{task_id}")
        target = 0
        for condition in config.get("finishCondition") or []:
            match = _PET_TALENT_CONDITION.fullmatch(str(condition or ""))
            if match is not None:
                target = int(match.group(1))
                break
        progress_rows = observed.get("progressList") or observed.get("progress_list") or []
        if isinstance(progress_rows, dict):
            progress_rows = progress_rows.get("items") or []
        runtime_progress = next(
            (row for row in progress_rows if isinstance(row, dict)),
            {},
        )
        runtime_target = int(runtime_progress.get("target") or 0)
        if target <= 0 or runtime_target != target:
            raise ValueError(f"灵宠竞武任务 {task_id} 的 Runtime/配置目标不一致")
        rewards = [str(value) for value in (config.get("reward") or [])]
        status = int(observed.get("status") or 0)
        progress = int(runtime_progress.get("progress") or 0)
        milestones.append(
            LingchongJingwuTaskMilestone(
                task_id=task_id,
                order=int(config.get("sort") or 0),
                name=str(config.get("name_plain") or config.get("name") or ""),
                target=target,
                progress=progress,
                status=status,
                finished=bool(runtime_progress.get("finish")) or progress >= target,
                talent_pill_count=_reward_item_count(rewards, TALENT_PILL_ITEM_ID),
                rewards=rewards,
            )
        )
    milestones.sort(key=lambda row: (row.target, row.order, row.task_id))
    return milestones


def load_lingchong_jingwu_resource_definitions(
    *,
    export_root: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Load usable feeding pills and preserve their pet-type-specific gains."""

    root = resolve_fanxiu_export_root(export_root)
    definitions: list[dict[str, Any]] = []
    for row in _load_config_rows(root, "Item"):
        name = str(row.get("name_plain") or row.get("name") or "")
        if not name.endswith("饲灵丸"):
            continue
        gains: dict[int, int] = {}
        raw_effect = str(row.get("effectValue") or "")
        for token in raw_effect.split(","):
            match = re.fullmatch(r"(\d+):(\d+)", token.strip())
            if match is not None:
                gains[int(match.group(1))] = int(match.group(2))
        if not gains:
            continue
        definitions.append(
            {
                "item_id": int(row.get("id") or 0),
                "name": name,
                "quality": int(row.get("quality") or 0),
                "aptitude_gain_by_pet_type": gains,
            }
        )
    definitions.sort(key=lambda row: (row["quality"], row["item_id"]))
    if not definitions:
        raise ValueError("没有找到可计算资质增量的饲灵丸配置")
    return definitions


def collect_lingchong_jingwu_resource_snapshot(
    *,
    activity_id: str,
    export_root: str | Path | None = None,
) -> LingchongJingwuResourceSnapshot:
    """Read current feeding-pill counts from the already-loaded backpack."""

    definitions = load_lingchong_jingwu_resource_definitions(export_root=export_root)
    counts, runtime_evidence = read_backpack_item_counts(
        [row["item_id"] for row in definitions],
        manager_key="lingchong-jingwu-resources",
    )
    captured_at = datetime.now().astimezone().isoformat(timespec="seconds")
    items = []
    for row in definitions:
        gains = dict(row["aptitude_gain_by_pet_type"])
        items.append(
            LingchongJingwuResourceItem(
                **row,
                count=max(0, int(counts.get(int(row["item_id"]), 0))),
                minimum_aptitude_gain=min(gains.values()),
                maximum_aptitude_gain=max(gains.values()),
            )
        )
    return LingchongJingwuResourceSnapshot(
        activity_id=str(activity_id),
        captured_at=captured_at,
        items=items,
        total_count=sum(row.count for row in items),
        evidence={
            **runtime_evidence,
            "official_name": LINGCHONG_JINGWU_OFFICIAL_NAME,
            "user_alias": LINGCHONG_JINGWU_USER_ALIAS,
            "item_count": len(items),
            "score_requires_pet_type": True,
        },
    )


def ensure_lingchong_jingwu_activity(session: Session) -> str:
    """Project the exact 8-server worldline instance into the shared store."""

    record = session.exec(
        select(FanxiuPacketBusinessRecord)
        .where(
            FanxiuPacketBusinessRecord.domain == "worldline_activity",
            FanxiuPacketBusinessRecord.entity_id
            == str(LINGCHONG_JINGWU_PARENT_ACTIVITY_ID),
        )
        .order_by(
            col(FanxiuPacketBusinessRecord.captured_at).desc(),
            col(FanxiuPacketBusinessRecord.updated_at).desc(),
        )
    ).first()
    if record is None:
        raise ValueError("尚未采集到8跨灵宠竞武活动实例")
    payload = dict(record.payload or {})
    worldline = payload.get("item") if isinstance(payload.get("item"), dict) else payload
    references = resolve_lingchong_jingwu_references()
    activity_payload = build_lingchong_jingwu_activity_payload(
        dict(worldline),
        references=references,
        captured_at=record.captured_at,
    )
    existing = session.exec(
        select(FanxiuExchangeActivity).where(
            FanxiuExchangeActivity.activity_type == LINGCHONG_JINGWU_ACTIVITY_TYPE,
            FanxiuExchangeActivity.cross_count == references.cross_count,
            FanxiuExchangeActivity.start_date == activity_payload["start_date"],
            FanxiuExchangeActivity.end_date == activity_payload["end_date"],
        )
    ).first()
    existing_evidence = dict(existing.evidence or {}) if existing is not None else {}
    activity_payload["evidence"] = {
        **existing_evidence,
        **dict(activity_payload.get("evidence") or {}),
        "worldline_fact": dict(record.evidence or {}),
    }
    if existing is not None and existing.captured_at:
        def captured_time(value: str) -> datetime:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))

        try:
            if captured_time(existing.captured_at) > captured_time(record.captured_at):
                activity_payload["captured_at"] = existing.captured_at
                activity_payload["source_kind"] = existing.source_kind
        except ValueError:
            # Keep a non-empty materialized timestamp rather than replacing it
            # with an older worldline projection whose format cannot be ordered.
            activity_payload["captured_at"] = existing.captured_at
            activity_payload["source_kind"] = existing.source_kind
    return upsert_exchange_activity_snapshot(session, activity_payload)


def _quest_items(parsed: dict[str, Any], field: str) -> list[dict[str, Any]]:
    raw = parsed.get(field)
    if isinstance(raw, dict):
        raw = raw.get("items")
    return [dict(item) for item in (raw or []) if isinstance(item, dict)]


def load_lingchong_jingwu_observed_tasks(
    session: Session,
    *,
    export_root: str | Path | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, Any]:
    """Report the missing Runtime task capability without reading raw history."""

    del session, export_root, start_date, end_date
    raise ValueError(
        "灵宠竞武本期任务 Runtime 读取尚未实现；已禁止使用抓包 raw JSON 兜底"
    )

def store_lingchong_jingwu_resource_snapshot(
    session: Session,
    snapshot: LingchongJingwuResourceSnapshot,
) -> LingchongJingwuResourceSnapshot:
    from backend.core.fanxiu.business_data import (
        upsert_fanxiu_business_records,
    )

    payload = snapshot.model_dump(mode="json")
    upsert_fanxiu_business_records(
        session,
        [
            {
                "domain": "lingchong_jingwu_resource_snapshot",
                "record_key": f"lingchong-jingwu-resources:{snapshot.activity_id}",
                "protocol": "runtime_memory_backpack",
                "source_kind": snapshot.source_kind,
                "entity_id": snapshot.activity_id,
                "entity_name": LINGCHONG_JINGWU_OFFICIAL_NAME,
                "captured_at": snapshot.captured_at.replace("T", " ", 1),
                "payload": payload,
                "evidence": dict(snapshot.evidence),
            }
        ],
    )
    return snapshot




def load_lingchong_jingwu_resource_snapshot(
    session: Session,
    *,
    activity_id: str,
) -> LingchongJingwuResourceSnapshot:
    record = session.exec(
        select(FanxiuPacketBusinessRecord).where(
            FanxiuPacketBusinessRecord.domain
            == "lingchong_jingwu_resource_snapshot",
            FanxiuPacketBusinessRecord.record_key
            == f"lingchong-jingwu-resources:{activity_id}",
        )
    ).first()
    if record is not None:
        return LingchongJingwuResourceSnapshot.model_validate(record.payload)
    definitions = load_lingchong_jingwu_resource_definitions()
    items = [
        LingchongJingwuResourceItem(
            **row,
            count=0,
            minimum_aptitude_gain=min(row["aptitude_gain_by_pet_type"].values()),
            maximum_aptitude_gain=max(row["aptitude_gain_by_pet_type"].values()),
        )
        for row in definitions
    ]
    return LingchongJingwuResourceSnapshot(
        activity_id=activity_id,
        captured_at="",
        source_kind="readonly_backpack_runtime",
        complete=False,
        items=items,
        total_count=0,
        reason="尚未从游戏采集饲灵丸库存",
        evidence={"item_count": len(items), "score_requires_pet_type": True},
    )


def collect_and_store_lingchong_jingwu_activity(
    session: Session,
    *,
    activity_id: str | None = None,
    today: date | None = None,
) -> Any:
    """Refresh both complete ranking scopes from standard packet facts."""

    current_id = ensure_lingchong_jingwu_activity(session)
    selected_id = activity_id or current_id
    activity = session.get(FanxiuExchangeActivity, selected_id)
    if activity is None or activity.activity_type != LINGCHONG_JINGWU_ACTIVITY_TYPE:
        raise ValueError("8跨灵宠竞武活动不存在")
    if not is_exchange_activity_active(activity, today=today):
        raise ValueError("8跨灵宠竞武活动不在有效日期内")
    references = resolve_lingchong_jingwu_references()
    personal = read_activity_rank_fact(
        session, references.personal_rank_activity_id
    )
    plane = read_activity_rank_fact(session, references.plane_rank_activity_id)
    _require_fact_in_activity_period(activity, personal, label="个人榜")
    _require_fact_in_activity_period(activity, plane, label="位面榜")
    rows = project_lingchong_jingwu_rank_rows(personal, scope="personal")
    rows.extend(project_lingchong_jingwu_rank_rows(plane, scope="plane"))
    captured_at = max(str(personal["captured_at"]), str(plane["captured_at"]))
    evidence = dict(activity.evidence or {})
    evidence.update(
        {
            "rank_scope_activity_ids": {
                "personal": references.personal_rank_activity_id,
                "plane": references.plane_rank_activity_id,
            },
            "rank_scope_completeness": {
                "personal": {
                    "declared": int(personal.get("rank_list_size") or 0),
                    "loaded": len(personal.get("items") or []),
                },
                "plane": {
                    "declared": int(plane.get("rank_list_size") or 0),
                    "loaded": len(plane.get("items") or []),
                },
            },
        }
    )
    activity.evidence = evidence
    activity.captured_at = captured_at
    activity.source_kind = "standard_runtime_facts"
    session.add(activity)
    replace_exchange_rankings(
        session,
        activity_type=LINGCHONG_JINGWU_ACTIVITY_TYPE,
        activity_id=activity.id,
        rows=rows,
        captured_at=captured_at,
    )
    return list_exchange_activity_snapshot(
        session,
        activity_type=LINGCHONG_JINGWU_ACTIVITY_TYPE,
        activity_id=activity.id,
    ).selected_activity


def _require_fact_in_activity_period(
    activity: FanxiuExchangeActivity,
    fact: dict[str, Any],
    *,
    label: str,
) -> None:
    captured_date = str(fact.get("captured_at") or "")[:10]
    if not captured_date or not (activity.start_date <= captured_date <= activity.end_date):
        raise ValueError(
            f"灵宠竞武{label}事实不属于所选活动周期：{captured_date or '缺少时间'}"
        )


def project_lingchong_jingwu_rank_rows(
    rank_fact: dict[str, Any],
    *,
    scope: Literal["personal", "plane"],
) -> list[dict[str, Any]]:
    """Validate and project a complete standard rank fact."""

    expected_vo = (
        "ActivityRankPersonalVO" if scope == "personal" else "ActivityRankCrossServerVO"
    )
    if str(rank_fact.get("rank_vo_type") or "") != expected_vo:
        raise ValueError(f"灵宠竞武{scope}榜 VO 类型不匹配")
    declared = int(rank_fact.get("rank_list_size") or 0)
    items = [dict(item) for item in (rank_fact.get("items") or []) if isinstance(item, dict)]
    if declared <= 0 or len(items) != declared:
        raise ValueError(f"灵宠竞武{scope}榜不完整：{len(items)}/{declared}")
    ranks = [int(item.get("rank") or 0) for item in items]
    if ranks != list(range(1, declared + 1)):
        raise ValueError(f"灵宠竞武{scope}榜排名不连续")
    self_rank = int(rank_fact.get("rank") or 0)
    rows: list[dict[str, Any]] = []
    for item in items:
        rank = int(item["rank"])
        role_key = str(item.get("key") or item.get("id") or f"{scope}:{rank}")
        rows.append(
            {
                "ranking_scope": scope,
                "rank": rank,
                "score": int(item.get("score") or 0),
                "role_key": role_key,
                "name": str(item.get("name") or ""),
                "server_id": item.get("server_id") or (item.get("id") if scope == "plane" else None),
                "server_name": str(item.get("server_name") or ""),
                "club_name": str(item.get("club_name") or ""),
                "is_self": rank == self_rank,
                "is_reward_guard": False,
                "is_last_player": rank == declared,
                "has_player": True,
                "raw_data": {
                    "reported_rank_list_size": declared,
                    "loaded_player_count": len(items),
                    "scope_complete": True,
                    "rank_vo_type": expected_vo,
                },
            }
        )
    return rows


__all__ = [
    "LINGCHONG_JINGWU_ACTIVITY_TYPE",
    "LINGCHONG_JINGWU_OFFICIAL_NAME",
    "LINGCHONG_JINGWU_USER_ALIAS",
    "LINGCHONG_JINGWU_PARENT_ACTIVITY_ID",
    "LINGCHONG_JINGWU_PERSONAL_RANK_ID",
    "LINGCHONG_JINGWU_PLANE_RANK_ID",
    "LingchongJingwuReferences",
    "LingchongJingwuTaskMilestone",
    "LingchongJingwuResourceItem",
    "LingchongJingwuResourceSnapshot",
    "resolve_lingchong_jingwu_references",
    "build_lingchong_jingwu_activity_payload",
    "load_lingchong_jingwu_task_milestones",
    "load_lingchong_jingwu_resource_definitions",
    "collect_lingchong_jingwu_resource_snapshot",
    "ensure_lingchong_jingwu_activity",
    "load_lingchong_jingwu_observed_tasks",
    "store_lingchong_jingwu_resource_snapshot",
    "load_lingchong_jingwu_resource_snapshot",
    "collect_and_store_lingchong_jingwu_activity",
    "project_lingchong_jingwu_rank_rows",
]
