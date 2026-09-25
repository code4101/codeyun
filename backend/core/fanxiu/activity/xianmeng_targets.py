"""仙盟阵营资格、排序与已加载战场的目标查询。

目标规则依赖服务端关系配置；不依赖 Task/Executor，也不负责进场或攻击。
读 Runtime 前须由调用方通过正式业务入口加载战场，事实不完整会明确失败。
"""
from __future__ import annotations

import time
from typing import Any


def plan_xianmeng_targets(
    snapshot: dict[str, Any], *, now_ms: int | None = None,
) -> dict[str, Any]:
    """Rank non-friendly camps: no CD first, unshielded before shielded.

    Pillar damage alone does not mean attackable: an almost-destroyed camp
    can still have several minutes of immunity. A live shield lowers rank
    but does not make the camp ineligible. The focused UI immunity check
    remains the final authority before spending stamina.
    """

    from backend.core.fanxiu.catalog.server_relations import (
        classify_fanxiu_target_relation,
    )

    camps = snapshot.get("camps") if isinstance(snapshot.get("camps"), list) else []
    current_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
    rows: list[dict[str, Any]] = []
    own_rows: list[dict[str, Any]] = []
    prepared: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    def exclude(row: dict[str, Any], code: str, reason: str) -> None:
        skipped.append({"id": row.get("id"), "name": row.get("name"), "reason_code": code, "reason": reason})

    for raw in camps:
        if not isinstance(raw, dict):
            continue
        server_id = raw.get("server_id")
        relation = classify_fanxiu_target_relation(
            is_npc=False,
            server_id=server_id,
        )
        row = {**raw, "relation": relation}
        if relation.get("relation") == "same_server":
            own_rows.append(row)
        prepared.append(row)

    own_ids = {int(row.get("id") or 0) for row in own_rows}
    battlefield_ally_ids = {
        int(row.get("ally_camp_id") or 0)
        for row in own_rows
        if int(row.get("ally_camp_id") or 0) > 0
    }
    battlefield_ally_ids.update(
        int(row.get("id") or 0)
        for row in prepared
        if int(row.get("ally_camp_id") or 0) in own_ids
    )
    for row in prepared:
        relation = row["relation"]
        if relation.get("camp") != "non_friendly":
            if relation.get("camp") == "friendly":
                exclude(row, "friendly", "本服或友军，禁止攻击")
            else:
                exclude(row, "unknown_relation", "阵营关系未确认，禁止攻击")
            continue
        if int(row.get("id") or 0) in battlefield_ally_ids:
            exclude(row, "battlefield_ally", "战场盟友，禁止攻击")
            continue
        cur_hp = row.get("pillar_cur_hp")
        max_hp = row.get("pillar_max_hp")
        if not isinstance(cur_hp, (int, float)) or not isinstance(max_hp, (int, float)):
            exclude(row, "incomplete_score", "阵柱积分事实不完整")
            continue
        # A visible button and an advancing client input timer do not prove
        # this camp remains attackable. Live zero-pillar probes dispatched
        # clicks but produced neither results nor stamina consumption.
        if float(max_hp) <= 0 or float(cur_hp) <= 0:
            if float(max_hp) <= 0:
                exclude(row, "invalid_max_score", "阵柱总积分无效，无法确认可攻击")
            else:
                exclude(row, "depleted_score", "阵柱积分已耗尽，无法攻击")
            continue
        protect_end_time = row.get("protect_end_time")
        if isinstance(protect_end_time, (int, float)) and protect_end_time > current_ms:
            exclude(row, "immune", "目标处于免战期，暂不可攻击")
            continue
        row["shielded"] = (
            row.get("has_super_mirror_hp_protect") is True
            or row.get("has_xiaoyan_mirror") is True
        )
        row["pillar_ratio"] = max(0.0, min(1.0, float(cur_hp) / float(max_hp)))
        rows.append(row)

    opponents = [row for row in prepared
                 if row["relation"].get("camp") == "non_friendly"
                 and int(row.get("id") or 0) not in battlefield_ally_ids]
    all_opponents_defeated = bool(
        own_rows and opponents
        and len(prepared) == len(camps) == int(snapshot.get("camp_count") or 0)
        and all(row["relation"].get("camp") in {"friendly", "non_friendly"} for row in prepared)
        and all(isinstance(row.get("pillar_cur_hp"), (int, float))
                and row["pillar_cur_hp"] == 0
                and isinstance(row.get("pillar_max_hp"), (int, float))
                and row["pillar_max_hp"] > 0 for row in opponents)
    )

    own_pillar_destroyed = any(
        isinstance(row.get("pillar_cur_hp"), (int, float))
        and float(row["pillar_cur_hp"]) <= 0
        for row in own_rows
    )
    rows.sort(
        key=lambda row: (
            bool(row["shielded"]),
            float(row["pillar_cur_hp"]),
            float(row["pillar_ratio"]),
            int(row.get("id") or 0),
        )
    )
    return {
        "all_opponents_defeated": all_opponents_defeated,
        "opponents": opponents,
        "own_pillar_destroyed": own_pillar_destroyed,
        "own_camps": own_rows,
        "battlefield_ally_ids": sorted(battlefield_ally_ids),
        "candidates": rows,
        "skipped_targets": skipped,
    }


def describe_xianmeng_attackable_targets(
    snapshot: dict[str, Any], *, now_ms: int | None = None,
) -> dict[str, Any]:
    """用同一目标规则解释给定快照；不修改输入，不读取 Runtime。"""
    if not snapshot.get("ok") or not snapshot.get("complete"):
        raise RuntimeError("仙盟阵营事实未完整加载，不能当作无目标")
    fallback = plan_xianmeng_targets(snapshot, now_ms=now_ms)
    return {**snapshot, "fallback_plan": fallback, "skipped_targets": fallback["skipped_targets"]}


def read_xianmeng_attackable_targets() -> dict[str, Any]:
    """读取已加载战场的 Runtime 事实，不导航、点击或写调度状态。"""
    from backend.core.fanxiu.instrumentation.landcontend import (
        read_landcontend_command_target_snapshot,
    )

    return describe_xianmeng_attackable_targets(read_landcontend_command_target_snapshot())
