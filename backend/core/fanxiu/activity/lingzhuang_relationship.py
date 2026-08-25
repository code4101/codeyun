from __future__ import annotations

"""Persist derived Lingzhuang material-to-score relationship samples."""

import time
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from backend.core.fanxiu.activity.lingzhuang_strengthening import (
    LingzhuangStrengtheningSnapshot,
    load_lingzhuang_strengthening_snapshot,
)
from backend.models import FanxiuExchangeRanking, FanxiuPacketBusinessRecord


RELATIONSHIP_SAMPLE_DOMAIN = "relationship_sample"
RELATIONSHIP_NAMESPACE = "lingzhuang-huadao-material-score"


class RelationshipSample(BaseModel):
    id: str
    captured_at: str
    x: float
    values: dict[str, float] = Field(default_factory=dict)


class RelationshipDataset(BaseModel):
    namespace: str
    entity_id: str
    samples: list[RelationshipSample] = Field(default_factory=list)


def _record_key(activity_id: str, x: int) -> str:
    return f"{RELATIONSHIP_NAMESPACE}:{activity_id}:{x}"


def _cumulative_task_score(snapshot: LingzhuangStrengtheningSnapshot) -> int:
    if snapshot.score_current is None:
        raise ValueError("灵装积分进度缺失")
    completed = sum(
        round_item.target
        for round_item in snapshot.score_rounds
        if snapshot.score_round is not None and round_item.round < snapshot.score_round
    )
    return completed + int(snapshot.score_current)


def _material_count(snapshot: LingzhuangStrengtheningSnapshot, part: str) -> int:
    for row in snapshot.rows:
        if row.part == part:
            # 初灵、洞玄使用同一部位玄铁；快照的两列故意共享物品 id。
            count = row.initial.material_count
            if count is None:
                raise ValueError(f"{part}玄铁存量缺失")
            return int(count)
    raise ValueError(f"强化快照中没有装备部位：{part}")


def record_lingzhuang_strengthening_action_sample(
    session: Session,
    *,
    activity_id: str,
    before: LingzhuangStrengtheningSnapshot | dict,
    after: LingzhuangStrengtheningSnapshot | dict,
    part: str,
    category: str,
) -> RelationshipDataset:
    """Persist one exact click sample from structured before/after Runtime data."""

    def coerce(value: LingzhuangStrengtheningSnapshot | dict) -> LingzhuangStrengtheningSnapshot:
        # Long-lived Jupyter kernels can hot-reload this module and the snapshot
        # module independently.  Normalize an older Pydantic class by payload.
        if not isinstance(value, dict) and hasattr(value, "model_dump"):
            value = value.model_dump(mode="python")
        return LingzhuangStrengtheningSnapshot.model_validate(value)

    before_snapshot = coerce(before)
    after_snapshot = coerce(after)
    for label, snapshot in (("点击前", before_snapshot), ("点击后", after_snapshot)):
        if snapshot.equipment_current is None:
            raise ValueError(f"{label}装备任务进度缺失，拒绝记录关系样本")
        if snapshot.score_current is None or snapshot.score_round is None or not snapshot.score_rounds:
            raise ValueError(f"{label}积分任务进度缺失，拒绝记录关系样本")
    consumed = _material_count(before_snapshot, part) - _material_count(after_snapshot, part)
    if consumed <= 0:
        raise ValueError(f"{part}玄铁没有减少，拒绝把本次点击记成消耗")
    existing = list_lingzhuang_relationship_samples(session, activity_id=activity_id)
    previous_x = max((int(sample.x) for sample in existing.samples), default=0)
    x = previous_x + consumed
    captured_at = after_snapshot.captured_at
    payload = {
        "x": x,
        "values": {
            "equipment_task_progress": int(after_snapshot.equipment_current),
            "task_score": _cumulative_task_score(after_snapshot),
        },
        "action": {
            "part": part,
            "category": category,
            "consumed": consumed,
            "material_before": _material_count(before_snapshot, part),
            "material_after": _material_count(after_snapshot, part),
            "equipment_task_before": before_snapshot.equipment_current,
            "equipment_task_after": after_snapshot.equipment_current,
            "task_score_before": _cumulative_task_score(before_snapshot),
            "task_score_after": _cumulative_task_score(after_snapshot),
        },
    }
    key = _record_key(activity_id, x)
    row = session.exec(
        select(FanxiuPacketBusinessRecord).where(
            FanxiuPacketBusinessRecord.domain == RELATIONSHIP_SAMPLE_DOMAIN,
            FanxiuPacketBusinessRecord.record_key == key,
        )
    ).first()
    now = time.time()
    if row is None:
        row = FanxiuPacketBusinessRecord(
            domain=RELATIONSHIP_SAMPLE_DOMAIN,
            record_key=key,
            source_kind="read_only_runtime_before_after_action",
            entity_id=activity_id,
            entity_name="灵装化道消耗与积分",
            captured_at=captured_at,
            captured_date=captured_at[:10],
            payload=payload,
            updated_at=now,
        )
    else:
        row.source_kind = "read_only_runtime_before_after_action"
        row.captured_at = captured_at
        row.captured_date = captured_at[:10]
        row.payload = payload
        row.updated_at = now
    session.add(row)
    session.commit()
    return list_lingzhuang_relationship_samples(session, activity_id=activity_id)


def list_lingzhuang_relationship_samples(
    session: Session,
    *,
    activity_id: str,
) -> RelationshipDataset:
    prefix = f"{RELATIONSHIP_NAMESPACE}:{activity_id}:"
    rows = session.exec(
        select(FanxiuPacketBusinessRecord).where(
            FanxiuPacketBusinessRecord.domain == RELATIONSHIP_SAMPLE_DOMAIN,
            FanxiuPacketBusinessRecord.entity_id == activity_id,
            FanxiuPacketBusinessRecord.record_key.startswith(prefix),
        )
    ).all()
    samples: list[RelationshipSample] = []
    for row in rows:
        if not isinstance(row.payload, dict):
            continue
        try:
            samples.append(
                RelationshipSample(
                    id=row.id,
                    captured_at=row.captured_at,
                    x=float(row.payload["x"]),
                    values={
                        str(key): float(value)
                        for key, value in dict(row.payload.get("values") or {}).items()
                    },
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    samples.sort(key=lambda item: (item.x, item.captured_at))
    return RelationshipDataset(
        namespace=RELATIONSHIP_NAMESPACE,
        entity_id=activity_id,
        samples=samples,
    )


def record_lingzhuang_relationship_sample(
    session: Session,
    *,
    activity_id: str,
) -> RelationshipDataset:
    strengthening = load_lingzhuang_strengthening_snapshot(session)
    if strengthening.activity_id != activity_id or not strengthening.complete:
        raise ValueError("强化与任务数据未完整更新，不能生成关系样本")
    if strengthening.equipment_current is None or strengthening.score_current is None:
        raise ValueError("累计玄铁消耗或灵装积分缺失，不能生成关系样本")

    completed_round_score = sum(
        round_item.target
        for round_item in strengthening.score_rounds
        if strengthening.score_round is not None and round_item.round < strengthening.score_round
    )
    cumulative_task_score = completed_round_score + strengthening.score_current

    self_ranking = session.exec(
        select(FanxiuExchangeRanking).where(
            FanxiuExchangeRanking.activity_id == activity_id,
            FanxiuExchangeRanking.ranking_scope == "personal",
            FanxiuExchangeRanking.is_self.is_(True),
            FanxiuExchangeRanking.has_player.is_(True),
        )
    ).first()
    if self_ranking is None:
        raise ValueError("本次榜单中没有我的积分，不能生成关系样本")

    x = int(strengthening.equipment_current)
    captured_at = max(
        value
        for value in (strengthening.captured_at, self_ranking.captured_at)
        if value
    )
    payload = {
        "x": x,
        "values": {
            "ranking_score": int(self_ranking.score),
            "task_score": int(cumulative_task_score),
        },
    }
    key = _record_key(activity_id, x)
    row = session.exec(
        select(FanxiuPacketBusinessRecord).where(
            FanxiuPacketBusinessRecord.domain == RELATIONSHIP_SAMPLE_DOMAIN,
            FanxiuPacketBusinessRecord.record_key == key,
        )
    ).first()
    now = time.time()
    if row is None:
        row = FanxiuPacketBusinessRecord(
            domain=RELATIONSHIP_SAMPLE_DOMAIN,
            record_key=key,
            source_kind="derived_from_persisted_activity_snapshots",
            entity_id=activity_id,
            entity_name="灵装化道消耗与积分",
            captured_at=captured_at,
            captured_date=captured_at[:10],
            payload=payload,
            updated_at=now,
        )
    else:
        row.captured_at = captured_at
        row.captured_date = captured_at[:10]
        row.payload = payload
        row.updated_at = now
    session.add(row)
    session.commit()
    return list_lingzhuang_relationship_samples(session, activity_id=activity_id)
