from __future__ import annotations

"""仙花榜期人物绑定；写一次、跨 attempt 复用，榜期变化才重新择人。"""

from copy import deepcopy
from typing import Any, Mapping

from sqlalchemy.dialects.sqlite import insert
from sqlmodel import Session, select

from backend.models import FanxiuPacketBusinessRecord
from backend.core.fanxiu.activity.yaochi_flower_strategy import plan_yaochi_flower_recipient


RECIPIENT_BINDING_DOMAIN = "yaochi_flower_recipient_binding"


def load_yaochi_flower_recipient_binding(
    session: Session, *, instance_key: str,
) -> dict[str, Any] | None:
    """只读已持久化绑定；不访问游戏，不隐式择人或写库。"""
    if not instance_key.strip():
        raise ValueError("仙花榜期 instance_key 不能为空")
    row = session.exec(select(FanxiuPacketBusinessRecord).where(
        FanxiuPacketBusinessRecord.domain == RECIPIENT_BINDING_DOMAIN,
        FanxiuPacketBusinessRecord.record_key == instance_key,
    )).first()
    return deepcopy(row.payload) if row else None


def bind_yaochi_flower_recipient(
    session: Session, *, instance_key: str, plan: Mapping[str, Any],
) -> dict[str, Any]:
    """首次绑定人物、功法目标及重置阶段，重复/并发调用返回首次结果。

    原子 INSERT ON CONFLICT DO NOTHING 使用业务表 domain/key 唯一约束，
    后到计划不能覆盖先到者。调用方只可执行本函数返回的实际绑定。
    Runtime index 不持久化；本期功法优先级改变或库存增加不解锁人物。
    此操作提交 Session；不送花、不重置，也不授权库存全部消耗。
    """
    if not instance_key.strip() or plan.get("instance_key") != instance_key:
        raise ValueError("绑定要求明确且一致的仙花榜期")
    recommendation = deepcopy(dict(plan.get("recommendation") or {}))
    npc_id = int(recommendation.get("npc_id") or 0)
    if plan.get("status") != "ready" or npc_id <= 0 or npc_id != int((plan.get("chosen_npc") or {}).get("npc_id") or 0):
        raise ValueError("仙花人物计划无效")
    payload = {
        "instance_key": instance_key,
        "strategy": "single_recipient_all_flowers",
        "npc_id": npc_id,
        "target_gongfa": deepcopy(dict(plan.get("target_gongfa") or {})),
        "recommendation": recommendation,
    }
    row = FanxiuPacketBusinessRecord(
        domain=RECIPIENT_BINDING_DOMAIN, record_key=instance_key,
        entity_id=str(npc_id), entity_name=str(recommendation.get("name") or npc_id),
        source_kind="yaochi_flower_strategy", payload=payload,
    )
    session.execute(insert(FanxiuPacketBusinessRecord).values(**row.model_dump()).on_conflict_do_nothing(
        index_elements=["domain", "record_key"],
    ))
    session.commit()
    binding = load_yaochi_flower_recipient_binding(session, instance_key=instance_key)
    if binding is None:
        raise RuntimeError("仙花榜期绑定写入后不可读")
    return binding


def prepare_yaochi_flower_recipient(
    session: Session, *, instance_key: str,
    gongfa_snapshot: Mapping[str, Any], xianyuan_snapshot: Mapping[str, Any],
    flower_snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    """正式策略入口：有绑定直接复用；无绑定择人并原子固定，返回最新计划。

    instance_key 使用 RankingOccurrence 的完整键（运行时活动实例、模板、
    起止时间），不能只用跨数或活动名称。首次调用写库，不操作游戏；
    已绑定人物不可用时报错，绝不回退重选。执行器须另行取得运行权及预算。
    """
    binding = load_yaochi_flower_recipient_binding(session, instance_key=instance_key)
    if binding is None:
        candidate = plan_yaochi_flower_recipient(
            gongfa_snapshot, xianyuan_snapshot, flower_snapshot, instance_key=instance_key,
        )
        binding = bind_yaochi_flower_recipient(session, instance_key=instance_key, plan=candidate)
    return plan_yaochi_flower_recipient(
        gongfa_snapshot, xianyuan_snapshot, flower_snapshot,
        instance_key=instance_key, binding=binding,
    )


__all__ = [
    "load_yaochi_flower_recipient_binding", "bind_yaochi_flower_recipient",
    "prepare_yaochi_flower_recipient",
]
