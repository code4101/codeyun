from __future__ import annotations

"""Occurrence-scoped journal for variable-size reward batches.

Pending identity and before-facts survive a failed Cell. A completed result
must be settled before its confirmation is dismissed; a pending batch never
licenses a second start click.
"""
import time
from uuid import uuid4
from collections.abc import Mapping
from .magic_invasion import load_magic_invasion_occurrence_evidence, store_magic_invasion_occurrence_evidence

KEY = "magic_invasion_reward_batches"


def load_magic_invasion_reward_state(occurrence):
    raw = load_magic_invasion_occurrence_evidence(occurrence).get(KEY)
    if raw is None:
        return {"occurrence_id": occurrence.occurrence_id, "status": "collecting",
                "measurements": [], "pending_batch": None}
    if not isinstance(raw, Mapping) or raw.get("occurrence_id") != occurrence.occurrence_id:
        raise RuntimeError("魔道奖励批次实例不一致")
    return dict(raw)


def arm_magic_invasion_reward_batch(occurrence, *, count, baseline):
    state = load_magic_invasion_reward_state(occurrence)
    if state.get("pending_batch"):
        return {**state, "recovered_pending": True}
    if count <= 0:
        raise ValueError("魔道奖励批次必须为正整数")
    state["pending_batch"] = {"batch_id": uuid4().hex, "requested_exorcisms": count,
                              "armed_at_epoch": time.time(), "baseline": dict(baseline)}
    store_magic_invasion_occurrence_evidence(occurrence, {KEY: state}, message=f"魔道奖励批次授权 {count} 次")
    return {**state, "recovered_pending": False}


def settle_magic_invasion_reward_batch(occurrence, *, batch_id, completed_exorcisms,
                                      magic_crystal_delta, ranking_score_delta, duration_seconds):
    state = load_magic_invasion_reward_state(occurrence)
    row = dict(batch_id=batch_id, completed_exorcisms=completed_exorcisms,
               magic_crystal_delta=magic_crystal_delta, ranking_score_delta=ranking_score_delta,
               duration_seconds=duration_seconds)
    for previous in state["measurements"]:
        if previous["batch_id"] == batch_id:
            if previous != row:
                raise RuntimeError("魔道奖励重复结算内容冲突")
            return state
    pending = state.get("pending_batch")
    if not pending or pending["batch_id"] != batch_id or pending["requested_exorcisms"] != completed_exorcisms:
        raise RuntimeError("魔道奖励结算未命中授权次数")
    if min(magic_crystal_delta, ranking_score_delta) < 0 or duration_seconds <= 0:
        raise ValueError("魔道奖励批次观测无效")
    state["measurements"] = [*state["measurements"], row]
    state["pending_batch"] = None
    store_magic_invasion_occurrence_evidence(occurrence, {KEY: state}, message=f"魔道奖励批次完成 {completed_exorcisms} 次")
    return state
