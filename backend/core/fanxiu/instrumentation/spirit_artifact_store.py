from __future__ import annotations

"""Persist the latest equipped spirit-artifact projection as a database fact."""

import time
from datetime import datetime
from typing import Any

from sqlmodel import Session, select

from backend.models import FanxiuPacketBusinessRecord


SPIRIT_ARTIFACT_SNAPSHOT_DOMAIN = "spirit_artifact_equipped_snapshot"
SPIRIT_ARTIFACT_SNAPSHOT_KEY = "current"


def load_spirit_artifact_runtime_snapshot(session: Session) -> dict[str, Any] | None:
    row = session.exec(
        select(FanxiuPacketBusinessRecord).where(
            FanxiuPacketBusinessRecord.domain == SPIRIT_ARTIFACT_SNAPSHOT_DOMAIN,
            FanxiuPacketBusinessRecord.record_key == SPIRIT_ARTIFACT_SNAPSHOT_KEY,
        )
    ).first()
    return dict(row.payload) if row and isinstance(row.payload, dict) else None


def upsert_spirit_artifact_runtime_snapshot(
    session: Session,
    snapshot: dict[str, Any],
) -> FanxiuPacketBusinessRecord:
    """Replace the current fact only with a complete, non-empty runtime snapshot."""

    payload = dict(snapshot or {})
    if not payload.get("runtime_complete") or int(payload.get("runtime_equipped_count") or 0) <= 0:
        raise ValueError("拒绝用不完整的灵器运行态快照覆盖数据库")
    captured_timestamp = float(payload.get("runtime_updated_at") or time.time())
    captured_at = datetime.fromtimestamp(captured_timestamp).isoformat(timespec="seconds")
    row = session.exec(
        select(FanxiuPacketBusinessRecord).where(
            FanxiuPacketBusinessRecord.domain == SPIRIT_ARTIFACT_SNAPSHOT_DOMAIN,
            FanxiuPacketBusinessRecord.record_key == SPIRIT_ARTIFACT_SNAPSHOT_KEY,
        )
    ).first()
    now = time.time()
    if row is None:
        row = FanxiuPacketBusinessRecord(
            domain=SPIRIT_ARTIFACT_SNAPSHOT_DOMAIN,
            record_key=SPIRIT_ARTIFACT_SNAPSHOT_KEY,
            source_kind="dynamic_instrumentation",
            entity_name="当前装配灵器",
            captured_at=captured_at,
            captured_date=captured_at[:10],
            payload=payload,
            evidence=dict(payload.get("runtime_debug") or {}),
            created_at=now,
            updated_at=now,
        )
        session.add(row)
    else:
        row.source_kind = "dynamic_instrumentation"
        row.captured_at = captured_at
        row.captured_date = captured_at[:10]
        row.payload = payload
        row.evidence = dict(payload.get("runtime_debug") or {})
        row.updated_at = now
    session.commit()
    session.refresh(row)
    return row


def project_spirit_artifact_part_update(
    snapshot: dict[str, Any], part: dict[str, Any], *, observed_at: float,
) -> dict[str, Any]:
    """纯投影局部更新：只覆盖同一装配实例，保留全馆原观测时间和其他行。

    part 为调用方已取得的新鲜 enriched Runtime 部件，必须包含六条完整词条、
    pid/start、无候选以及明确 is_break；不在此读取游戏或猜测当前装配。
    observed_at 是这次部件观察时间，不能用写入时间冒充。原快照须已绑定该
    ware/part/item_id；换本体须先由完整装配同步更新引用，不接受局部偷换。
    """
    import copy
    import math
    from .spirit_artifact import project_spirit_artifact_part_row
    from .spirit_artifact_wash_observation import (
        SpiritArtifactWashTarget, validate_spirit_artifact_wash_snapshot,
    )
    if not isinstance(observed_at, (int, float)) or not math.isfinite(observed_at) or observed_at <= 0:
        raise ValueError('局部观察时间必须为有效时间戳')
    for key in ('ware_id', 'part', 'base_id', 'pid', 'process_start_ticks'):
        if type(part.get(key)) is not int or part[key] <= 0:
            raise ValueError(f'局部部件缺少有效 {key}')
    target = SpiritArtifactWashTarget(str(part.get('item_id') or ''), part['ware_id'], part['part'],
                                      (part['pid'], part['process_start_ticks']), part['base_id'])
    validate_spirit_artifact_wash_snapshot(part, target, verify_ui=False)
    if type(part.get('is_break')) is not bool or part['pending_effects'] or len(part['effects']) != 6:
        raise ValueError('局部同步要求明确突破状态、六条属性且无候选')
    for effect in part['effects']:
        if (not (effect.get('code') or effect.get('type') == 3)
                or not effect.get('name') or 'affix' not in effect
                or type(effect.get('normal_max')) is not int or effect['normal_max'] <= 0):
            raise ValueError('局部同步词条尚未完整 enrich')
    for key in ('grade', 'realm', 'refine_num'):
        if type(part.get(key)) is not int or part[key] < 0:
            raise ValueError(f'局部部件缺少有效 {key}')
    payload = copy.deepcopy(snapshot)
    matches = [(artifact, row) for artifact in payload.get('artifacts', []) for row in artifact.get('rows', [])
               if row.get('runtime_ware_id') == target.ware_id and row.get('runtime_part') == target.part]
    if len(matches) != 1 or matches[0][1].get('runtime_item_id') != target.item_id:
        raise ValueError('局部同步必须匹配现有唯一装配部件；不可创建或替换装配引用')
    artifact, row = matches[0]
    if row.get('runtime_base_id') != target.base_id:
        raise ValueError('同一装配实例的 base_id 与已有快照不符')
    prior_time = row.get('runtime_observation', {}).get('observed_at', payload.get('runtime_updated_at', 0))
    if observed_at < (prior_time or 0):
        raise ValueError('拒绝用较旧部件观察覆盖新事实')
    projected = project_spirit_artifact_part_row(part, artifact_name=artifact['name'], part_name=row['part_name'])
    row.update(projected)
    row['runtime_observation'] = dict(observed_at=observed_at, pid=part['pid'],
        process_start_ticks=part['process_start_ticks'], scope='part', source='runtime_item_enriched')
    # 顶层全馆时刻、process/debug、完整覆盖标记均保留；仅显式声明现在混合观察。
    payload['runtime_observation_scope'] = 'mixed'
    payload['runtime_partial_updated_at'] = max(observed_at, payload.get('runtime_partial_updated_at', 0))
    return payload


def update_spirit_artifact_runtime_part(
    session: Session, part: dict[str, Any], *, observed_at: float,
) -> dict[str, Any]:
    """原子更新已存在馆快照的一行并提交；不读游戏，不标记全馆新鲜。

    与完整同步并发时采用 compare-and-swap：读后快照有变则拒绝并回滚，
    调用方重新核对，不自动重放。返回保存后的馆快照，含行级观测元数据。
    """
    from sqlalchemy import update
    row = session.exec(select(FanxiuPacketBusinessRecord).where(
        FanxiuPacketBusinessRecord.domain == SPIRIT_ARTIFACT_SNAPSHOT_DOMAIN,
        FanxiuPacketBusinessRecord.record_key == SPIRIT_ARTIFACT_SNAPSHOT_KEY,
    )).first()
    if row is None or not isinstance(row.payload, dict):
        raise ValueError('不存在可局部更新的装配快照')
    original = row.payload
    payload = project_spirit_artifact_part_update(original, part, observed_at=observed_at)
    result = session.execute(update(FanxiuPacketBusinessRecord).where(
        FanxiuPacketBusinessRecord.id == row.id,
        FanxiuPacketBusinessRecord.updated_at == row.updated_at,
        FanxiuPacketBusinessRecord.payload == original,
    ).values(payload=payload, updated_at=time.time()).execution_options(synchronize_session=False))
    if result.rowcount != 1:
        session.rollback()
        raise RuntimeError('灵器快照已并发变化，未写入局部更新')
    session.commit()
    session.expire(row)
    return dict(row.payload)
