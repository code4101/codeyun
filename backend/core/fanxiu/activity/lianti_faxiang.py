from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from sqlmodel import Session, col, select

from backend.core.fanxiu.activity.exchange_event import (
    is_exchange_activity_active,
    list_exchange_activity_snapshot,
    replace_exchange_rankings,
    upsert_exchange_activity_snapshot,
)
from backend.core.fanxiu.activity.lingchong_jingwu import (
    project_lingchong_jingwu_rank_rows,
)
from backend.core.fanxiu.activity.standard_observation import read_activity_rank_fact
from backend.core.fanxiu.catalog.resources import resolve_fanxiu_export_root
from backend.core.fanxiu.instrumentation.backpack import read_backpack_item_counts
from backend.models import (
    FanxiuExchangeActivity,
    FanxiuPacketBusinessRecord,
)


LIANTI_FAXIANG_ACTIVITY_TYPE = "lianti-faxiang"
LIANTI_FAXIANG_OFFICIAL_NAME = "炼体法相"
LIANTI_FAXIANG_ACTIVITY_ID = 1043011
LIANTI_ESSENCE_ITEM_ID = 5030001
LIANTI_BREAKTHROUGH_ITEM_ID = 5030002
_SCORE_CONDITION = re.compile(r"PhysicalFightScore\|(\d+)")


class LiantiFaxiangResourceItem(BaseModel):
    item_id: int
    name: str
    quality: int
    count: int
    role: str
    score_per_item: int = 0


class LiantiFaxiangResourceSnapshot(BaseModel):
    activity_id: str
    captured_at: str
    source_kind: str = "readonly_backpack_runtime"
    complete: bool = True
    items: list[LiantiFaxiangResourceItem] = Field(default_factory=list)
    primary_resource_count: int = 0
    breakthrough_resource_count: int = 0
    maximum_score_gain: int = 0
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


def _event_date(worldline: dict[str, Any], key: str) -> str:
    text = str(worldline.get(f"{key}Text") or "").strip()
    if text:
        return text[:10]
    timestamp = int(worldline.get(key) or 0)
    if timestamp <= 0:
        raise ValueError("炼体法相世界线时间事实不完整")
    return datetime.fromtimestamp(timestamp / 1000).astimezone().date().isoformat()


def ensure_lianti_faxiang_activity(session: Session) -> str:
    """Project today's exact same-server preliminary into the shared store."""

    record = session.exec(
        select(FanxiuPacketBusinessRecord)
        .where(
            FanxiuPacketBusinessRecord.domain == "worldline_activity",
            FanxiuPacketBusinessRecord.entity_id == str(LIANTI_FAXIANG_ACTIVITY_ID),
        )
        .order_by(
            col(FanxiuPacketBusinessRecord.captured_at).desc(),
            col(FanxiuPacketBusinessRecord.updated_at).desc(),
        )
    ).first()
    if record is None:
        raise ValueError("尚未采集到炼体法相预赛活动实例")
    payload = dict(record.payload or {})
    worldline = payload.get("item") if isinstance(payload.get("item"), dict) else payload
    if int(worldline.get("activityId") or 0) != LIANTI_FAXIANG_ACTIVITY_ID:
        raise ValueError("世界线事实不是当前炼体法相预赛")
    if str(worldline.get("name") or "") != LIANTI_FAXIANG_OFFICIAL_NAME:
        raise ValueError("炼体法相世界线名称与静态身份不一致")

    root = resolve_fanxiu_export_root()
    config = next(
        (
            row
            for row in _load_config_rows(root, "Activity")
            if int(row.get("id") or 0) == LIANTI_FAXIANG_ACTIVITY_ID
        ),
        None,
    )
    if config is None or str(config.get("littleName_plain") or "") != "(预赛)":
        raise ValueError("炼体法相 1043011 静态配置不是本服预赛")

    activity_payload = {
        "activity_type": LIANTI_FAXIANG_ACTIVITY_TYPE,
        "cross_count": 1,
        "start_date": _event_date(worldline, "startTime"),
        "end_date": _event_date(worldline, "endTime"),
        "game_rank_activity_id": LIANTI_FAXIANG_ACTIVITY_ID,
        "currency_name": "炼体积分",
        "captured_at": record.captured_at,
        "source_kind": "standard_runtime_facts",
        "resource_strategy": {
            "resource_metric": "淬体精魄库存",
            "score_metric": "每使用 1 个淬体精魄增加 100 炼体积分",
            "task_metric": "本期服务器下发的 PhysicalFightScore 任务进度",
            "primary_resource_item_id": LIANTI_ESSENCE_ITEM_ID,
            "breakthrough_resource_item_id": LIANTI_BREAKTHROUGH_ITEM_ID,
        },
        "evidence": {
            "official_name": LIANTI_FAXIANG_OFFICIAL_NAME,
            "phase": "预赛",
            "same_server": True,
            "rank_scope_activity_ids": {"personal": LIANTI_FAXIANG_ACTIVITY_ID},
            "worldline": dict(worldline),
            "worldline_fact": dict(record.evidence or {}),
        },
    }
    return upsert_exchange_activity_snapshot(session, activity_payload)


def _quest_items(parsed: dict[str, Any], field: str) -> list[dict[str, Any]]:
    raw = parsed.get(field)
    if isinstance(raw, dict):
        raw = raw.get("items")
    return [dict(item) for item in (raw or []) if isinstance(item, dict)]


def _task_milestones(
    observed_tasks: list[dict[str, Any]],
    *,
    export_root: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Join only server-declared QuestEntryVO IDs; never choose a static ladder."""

    if not observed_tasks:
        raise ValueError("炼体法相本期任务尚未加载，拒绝从多套静态梯度猜测")
    root = resolve_fanxiu_export_root(export_root)
    configs = {
        int(row.get("id") or 0): row
        for row in _load_config_rows(root, "ActiveTask")
        if int(row.get("activityId") or 0) == LIANTI_FAXIANG_ACTIVITY_ID
    }
    result: list[dict[str, Any]] = []
    seen: set[int] = set()
    for observed in observed_tasks:
        task_id = int(observed.get("taskId") or observed.get("task_id") or 0)
        if task_id <= 0 or task_id in seen:
            raise ValueError("炼体法相本期任务 ID 缺失或重复")
        seen.add(task_id)
        config = configs.get(task_id)
        if config is None:
            raise ValueError(f"炼体法相本期任务缺少静态配置：{task_id}")
        target = 0
        for condition in config.get("finishCondition") or []:
            match = _SCORE_CONDITION.fullmatch(str(condition or ""))
            if match is not None:
                target = int(match.group(1))
                break
        progress_rows = observed.get("progressList") or observed.get("progress_list") or []
        if isinstance(progress_rows, dict):
            progress_rows = progress_rows.get("items") or []
        runtime_progress = next(
            (row for row in progress_rows if isinstance(row, dict)), {}
        )
        runtime_target = int(runtime_progress.get("target") or 0)
        if target <= 0 or runtime_target != target:
            raise ValueError(f"炼体法相任务 {task_id} 的 Runtime/配置目标不一致")
        progress = int(runtime_progress.get("progress") or 0)
        result.append(
            {
                "task_id": task_id,
                "order": int(config.get("sort") or 0),
                "name": str(config.get("name_plain") or config.get("name") or ""),
                "target": target,
                "progress": progress,
                "status": int(observed.get("status") or 0),
                "finished": bool(runtime_progress.get("finish")) or progress >= target,
                "must_get": str(config.get("corner_plain") or config.get("corner") or "") == "必拿",
                "rewards": [str(value) for value in (config.get("reward") or [])],
            }
        )
    result.sort(key=lambda row: (row["target"], row["order"], row["task_id"]))
    return result


def load_lianti_faxiang_observed_tasks(
    session: Session,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    """Report the missing Runtime task capability without reading raw history."""

    del session, export_root, start_date, end_date
    raise ValueError(
        "炼体法相本期任务 Runtime 读取尚未实现；已禁止使用抓包 raw JSON 兜底"
    )

def _resource_definitions(
    *, export_root: str | Path | None = None
) -> list[dict[str, Any]]:
    root = resolve_fanxiu_export_root(export_root)
    rows = {
        int(row.get("id") or 0): row for row in _load_config_rows(root, "Item")
    }
    definitions = []
    for item_id, role, score in (
        (LIANTI_ESSENCE_ITEM_ID, "score", 100),
        (LIANTI_BREAKTHROUGH_ITEM_ID, "breakthrough", 0),
    ):
        row = rows.get(item_id)
        if row is None:
            raise ValueError(f"炼体法相资源配置不存在：{item_id}")
        definitions.append(
            {
                "item_id": item_id,
                "name": str(row.get("name_plain") or row.get("name") or ""),
                "quality": int(row.get("quality") or 0),
                "role": role,
                "score_per_item": score,
            }
        )
    return definitions



def collect_lianti_faxiang_resource_snapshot(
    *,
    activity_id: str,
    export_root: str | Path | None = None,
) -> LiantiFaxiangResourceSnapshot:
    definitions = _resource_definitions(export_root=export_root)
    counts, runtime_evidence = read_backpack_item_counts(
        [row["item_id"] for row in definitions],
        manager_key="lianti-faxiang-resources",
    )
    items = [
        LiantiFaxiangResourceItem(
            **row,
            count=max(0, int(counts.get(int(row["item_id"]), 0))),
        )
        for row in definitions
    ]
    primary_count = next(
        (row.count for row in items if row.item_id == LIANTI_ESSENCE_ITEM_ID), 0
    )
    breakthrough_count = next(
        (row.count for row in items if row.item_id == LIANTI_BREAKTHROUGH_ITEM_ID), 0
    )
    return LiantiFaxiangResourceSnapshot(
        activity_id=activity_id,
        captured_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        items=items,
        primary_resource_count=primary_count,
        breakthrough_resource_count=breakthrough_count,
        maximum_score_gain=primary_count * 100,
        evidence={
            **runtime_evidence,
            "read_only": True,
            "score_per_primary_resource": 100,
            "breakthrough_resource_not_counted_as_score": True,
        },
    )



def store_lianti_faxiang_resource_snapshot(
    session: Session,
    snapshot: LiantiFaxiangResourceSnapshot,
) -> LiantiFaxiangResourceSnapshot:
    from backend.core.fanxiu.business_data import (
        upsert_fanxiu_business_records,
    )

    payload = snapshot.model_dump(mode="json")
    upsert_fanxiu_business_records(
        session,
        [
            {
                "domain": "resource_ranking_resource_snapshot",
                "record_key": f"{LIANTI_FAXIANG_ACTIVITY_TYPE}:{snapshot.activity_id}",
                "protocol": "runtime_memory_backpack",
                "source_kind": snapshot.source_kind,
                "entity_id": snapshot.activity_id,
                "entity_name": LIANTI_FAXIANG_OFFICIAL_NAME,
                "captured_at": snapshot.captured_at.replace("T", " ", 1),
                "payload": payload,
                "evidence": dict(snapshot.evidence),
            }
        ],
    )
    return snapshot




def load_lianti_faxiang_resource_snapshot(
    session: Session,
    *,
    activity_id: str,
) -> LiantiFaxiangResourceSnapshot:
    record = session.exec(
        select(FanxiuPacketBusinessRecord).where(
            FanxiuPacketBusinessRecord.domain == "resource_ranking_resource_snapshot",
            FanxiuPacketBusinessRecord.record_key
            == f"{LIANTI_FAXIANG_ACTIVITY_TYPE}:{activity_id}",
        )
    ).first()
    if record is not None:
        return LiantiFaxiangResourceSnapshot.model_validate(record.payload)
    definitions = _resource_definitions()
    return LiantiFaxiangResourceSnapshot(
        activity_id=activity_id,
        captured_at="",
        complete=False,
        items=[LiantiFaxiangResourceItem(**row, count=0) for row in definitions],
        reason="尚未从游戏采集炼体资源库存",
        evidence={"read_only": True, "score_per_primary_resource": 100},
    )


def collect_and_store_lianti_faxiang_activity(
    session: Session,
    *,
    activity_id: str | None = None,
    today: date | None = None,
) -> Any:
    current_id = ensure_lianti_faxiang_activity(session)
    selected_id = activity_id or current_id
    activity = session.get(FanxiuExchangeActivity, selected_id)
    if activity is None or activity.activity_type != LIANTI_FAXIANG_ACTIVITY_TYPE:
        raise ValueError("炼体法相活动不存在")
    if not is_exchange_activity_active(activity, today=today):
        raise ValueError("炼体法相活动不在有效日期内")
    fact = read_activity_rank_fact(session, LIANTI_FAXIANG_ACTIVITY_ID)
    captured_date = str(fact.get("captured_at") or "")[:10]
    if not captured_date or not activity.start_date <= captured_date <= activity.end_date:
        raise ValueError("炼体法相个人榜事实不属于所选活动周期")
    rows = project_lingchong_jingwu_rank_rows(fact, scope="personal")
    activity.captured_at = str(fact["captured_at"])
    activity.source_kind = "standard_runtime_facts"
    evidence = dict(activity.evidence or {})
    evidence["rank_scope_completeness"] = {
        "personal": {
            "declared": int(fact.get("rank_list_size") or 0),
            "loaded": len(fact.get("items") or []),
        }
    }
    activity.evidence = evidence
    session.add(activity)
    replace_exchange_rankings(
        session,
        activity_type=LIANTI_FAXIANG_ACTIVITY_TYPE,
        activity_id=activity.id,
        rows=rows,
        captured_at=activity.captured_at,
    )
    return list_exchange_activity_snapshot(
        session,
        activity_type=LIANTI_FAXIANG_ACTIVITY_TYPE,
        activity_id=activity.id,
    ).selected_activity


__all__ = [
    "LIANTI_FAXIANG_ACTIVITY_TYPE",
    "LIANTI_FAXIANG_OFFICIAL_NAME",
    "LIANTI_FAXIANG_ACTIVITY_ID",
    "ensure_lianti_faxiang_activity",
    "load_lianti_faxiang_observed_tasks",
    "LiantiFaxiangResourceSnapshot",
    "collect_lianti_faxiang_resource_snapshot",
    "store_lianti_faxiang_resource_snapshot",
    "load_lianti_faxiang_resource_snapshot",
    "collect_and_store_lianti_faxiang_activity",
]
