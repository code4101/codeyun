"""Magic occurrence identity and explicit repair of Runtime-id-split roots.

Runtime row IDs are observation metadata. An activity definition, server scope
and exact business interval identify one occurrence. Repair is a transaction;
the caller must save its dry-run report before committing destructive cleanup.
"""
from datetime import datetime
from zoneinfo import ZoneInfo
from sqlmodel import Session, select

from backend.models import (
    FanxiuExchangeActivity, FanxiuExchangeShopItem, FanxiuExchangeRanking,
    FanxiuExchangeActivityObservation, FanxiuRankingLifecycleCheckpoint,
)


def magic_occurrence_key(activity_id: int, cross_count: int, start_at: str, end_at: str) -> str:
    def boundary(value: str) -> str:
        parsed = datetime.fromisoformat(value)
        zone = ZoneInfo("Asia/Shanghai")
        return (parsed.replace(tzinfo=zone) if parsed.tzinfo is None else parsed.astimezone(zone)).isoformat(timespec="seconds")
    if int(activity_id) <= 0 or int(cross_count) <= 0:
        raise ValueError("魔道业务身份缺少活动 ID 或跨数")
    start, end = boundary(start_at), boundary(end_at)
    if start > end:
        raise ValueError("魔道业务时间区间倒置")
    return f"activity:magic-invasion:{int(activity_id)}:cross:{int(cross_count)}:{start}:{end}"


def repair_magic_occurrence_roots(session: Session, *, apply: bool = False) -> dict:
    """Preview or merge exact magic occurrences; never read or operate the game.

    Keeps the richest root, unions child identities and retains the newer
    conflicting observation. Shop locks are retained and bought counts never
    decrease. Completed checkpoints win over failed attempts. Full original
    rows are returned for an external recovery artifact. Caller owns commit.
    """
    roots = list(session.exec(select(FanxiuExchangeActivity).where(
        FanxiuExchangeActivity.activity_type == "magic-invasion")).all())
    groups = {}
    for row in roots:
        if row.game_activity_id and row.start_at and row.end_at and not row.instance_key.startswith("legacy:"):
            key = magic_occurrence_key(row.game_activity_id, row.cross_count, row.start_at, row.end_at)
            groups.setdefault(key, []).append(row)
    specs = ((FanxiuExchangeShopItem, ("goods_id",)),
             (FanxiuExchangeRanking, ("ranking_scope", "rank", "role_key")),
             (FanxiuExchangeActivityObservation, ("fingerprint",)))
    report = {"groups": [], "deleted_roots": 0}
    for key, rows in groups.items():
        children = {model: list(session.exec(select(model).where(model.activity_id.in_([r.id for r in rows]))).all())
                    for model, _ in specs}
        canonical = max(rows, key=lambda r: (
            sum(c.activity_id == r.id for cs in children.values() for c in cs),
            r.captured_at, r.updated_at, r.id))
        old_keys = {r.instance_key for r in rows} | {key}
        # Older explorer Cells used context: while the scheduler used runtime:.
        old_keys |= {k.replace("runtime:", "context:", 1) for k in old_keys if k.startswith("runtime:")}
        checkpoints = list(session.exec(select(FanxiuRankingLifecycleCheckpoint).where(
            FanxiuRankingLifecycleCheckpoint.instance_key.in_(old_keys))).all())
        if len(rows) == 1 and canonical.instance_key == key and all(c.instance_key == key for c in checkpoints):
            continue
        if any(c.status == "running" for c in checkpoints):
            raise ValueError("魔道 checkpoint 正在运行，不能合并")
        report["groups"].append({"key": key, "keep": canonical.id,
            "roots": [r.model_dump(mode="json") for r in rows],
            "children": {m.__tablename__: [c.model_dump(mode="json") for c in cs] for m, cs in children.items()},
            "checkpoints": [c.model_dump(mode="json") for c in checkpoints]})
        report["deleted_roots"] += len(rows) - 1
        if not apply:
            continue
        for model, fields in specs:
            by_identity = {}
            for child in children[model]:
                by_identity.setdefault(tuple(getattr(child, f) for f in fields), []).append(child)
            for cohort in by_identity.values():
                winner = max(cohort, key=lambda c: (getattr(c, "updated_at", c.created_at), c.id))
                if model is FanxiuExchangeShopItem:
                    winner.locked = any(c.locked for c in cohort)
                    winner.purchased_count = max(c.purchased_count for c in cohort)
                for child in cohort:
                    if child is not winner:
                        session.delete(child)
                session.flush()
                winner.activity_id = canonical.id
                session.add(winner)
        by_checkpoint = {}
        for cp in checkpoints:
            by_checkpoint.setdefault((cp.checkpoint_kind, cp.business_date), []).append(cp)
        for cohort in by_checkpoint.values():
            winner = max(cohort, key=lambda c: (c.status == "completed", c.updated_at))
            evidence = {}
            for cp in sorted(cohort, key=lambda c: c is winner):
                evidence.update(cp.evidence or {})
            winner.evidence = evidence
            winner.attempt_count = sum(c.attempt_count for c in cohort)
            for cp in cohort:
                if cp is not winner:
                    session.delete(cp)
            session.flush()
            winner.instance_key = key
            session.add(winner)
        session.flush()
        for row in rows:
            if row is not canonical:
                session.delete(row)
        session.flush()
        canonical.instance_key = key
        canonical.evidence = {**(canonical.evidence or {}), "instance_key": key}
        session.add(canonical)
        session.flush()
    return report
