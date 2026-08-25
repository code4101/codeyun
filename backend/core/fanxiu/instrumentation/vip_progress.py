from __future__ import annotations

"""Pure normalization for VIP progress across level transitions.

The game defines cumulative VIP experience in ``ChargeModel:GetAcumulateVipExp``
as the sum of ``Vip.Vip[i].vipExp`` for every completed level ``1..vipLevel``
plus the current ``ChargeModel.vipExp`` remainder.  No Runtime access belongs
in this module; callers must supply one coherent Role/Charge/config snapshot.
"""

from collections.abc import Mapping
from typing import Any


class VipProgressError(ValueError):
    """The supplied Role, Charge and VIP configuration facts are incoherent."""


def _strict_non_negative_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise VipProgressError(f"{field} 必须是非负整数")
    return value


def _canonical_exp_table(vip_exp_by_level: Mapping[int, int]) -> dict[int, int]:
    if not isinstance(vip_exp_by_level, Mapping) or not vip_exp_by_level:
        raise VipProgressError("Vip.Vip 经验配置为空")
    result: dict[int, int] = {}
    for raw_level, raw_exp in vip_exp_by_level.items():
        if (
            isinstance(raw_level, bool)
            or not isinstance(raw_level, int)
            or raw_level <= 0
        ):
            raise VipProgressError("Vip.Vip 等级键必须是正整数")
        if isinstance(raw_exp, bool) or not isinstance(raw_exp, int) or raw_exp <= 0:
            raise VipProgressError(f"Vip.Vip[{raw_level}].vipExp 必须是正整数")
        if raw_level in result:
            raise VipProgressError(f"Vip.Vip 等级 {raw_level} 重复")
        result[raw_level] = raw_exp
    return result


def normalize_vip_progress(
    *,
    vip_level: int,
    current_vip_exp: int,
    vip_exp_by_level: Mapping[int, int],
) -> dict[str, int | bool | None]:
    """Return the game's cumulative VIP progress on a monotonic scale.

    ``vip_exp_by_level[n]`` is the amount required to complete VIP level ``n``
    (the ``vipExp`` field of the ``Vip.Vip`` row indexed by ``n``).  A missing
    completed row is always incomplete.  A missing next row means the supplied
    level is the maximum known level; the function does not invent a cap.
    """

    level = _strict_non_negative_int(vip_level, "vip_level")
    current = _strict_non_negative_int(current_vip_exp, "current_vip_exp")
    table = _canonical_exp_table(vip_exp_by_level)

    missing_completed = [index for index in range(1, level + 1) if index not in table]
    if missing_completed:
        raise VipProgressError(
            "Vip.Vip 缺少已完成等级配置："
            + ",".join(str(value) for value in missing_completed)
        )

    completed_exp = sum(table[index] for index in range(1, level + 1))
    next_level = level + 1 if level + 1 in table else None
    next_required_exp = table.get(level + 1)
    if next_required_exp is not None and current > next_required_exp:
        raise VipProgressError(
            f"当前 VIP 经验 {current} 超过下一等级阈值 {next_required_exp}"
        )

    return {
        "vip_level": level,
        "current_vip_exp": current,
        "completed_level_exp": completed_exp,
        "cumulative_vip_exp": completed_exp + current,
        "next_vip_level": next_level,
        "next_level_required_exp": next_required_exp,
        "remaining_to_next_level": (
            next_required_exp - current if next_required_exp is not None else None
        ),
        "at_max_known_level": next_level is None,
    }


def vip_progress_delta(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
) -> int:
    """Compare two normalized snapshots without breaking at level-up."""

    before_total = _strict_non_negative_int(
        before.get("cumulative_vip_exp"),
        "before.cumulative_vip_exp",
    )
    after_total = _strict_non_negative_int(
        after.get("cumulative_vip_exp"),
        "after.cumulative_vip_exp",
    )
    if after_total < before_total:
        raise VipProgressError(
            f"VIP 累计经验倒退：{before_total} -> {after_total}"
        )
    return after_total - before_total


def compose_vip_progress_snapshot(
    *,
    role_snapshot: Mapping[str, Any],
    charge_snapshot: Mapping[str, Any],
    vip_exp_by_level: Mapping[int, int],
) -> dict[str, Any]:
    """Combine Role level, Charge remainder and static VIP thresholds.

    This is intentionally a pure composition gate.  It does not discover a
    Manager, open a GUI, or authorize using a VIP item.  Callers must supply
    two complete same-process Runtime projections; the result is suitable as
    a future before/after verifier once the Role-side ``vip_level`` reader has
    independently been proven.
    """

    if role_snapshot.get("complete") is not True:
        raise VipProgressError("Role VIP 等级快照不完整")
    if charge_snapshot.get("complete") is not True:
        raise VipProgressError("Charge VIP 经验快照不完整")
    if role_snapshot.get("source") != "runtime_memory.role.vip_level":
        raise VipProgressError("Role VIP 等级快照来源无效")
    if charge_snapshot.get("source") != "runtime_memory.charge.vip_exp":
        raise VipProgressError("Charge VIP 经验快照来源无效")

    def process_identity(snapshot: Mapping[str, Any], label: str) -> tuple[int, int]:
        evidence = snapshot.get("evidence")
        if not isinstance(evidence, Mapping):
            raise VipProgressError(f"{label} 快照缺少进程证据")
        pid = evidence.get("pid")
        start_ticks = evidence.get("process_start_ticks")
        if (
            isinstance(pid, bool)
            or not isinstance(pid, int)
            or pid <= 0
            or isinstance(start_ticks, bool)
            or not isinstance(start_ticks, int)
            or start_ticks <= 0
        ):
            raise VipProgressError(f"{label} 快照进程证据无效")
        return pid, start_ticks

    role_identity = process_identity(role_snapshot, "Role")
    if process_identity(charge_snapshot, "Charge") != role_identity:
        raise VipProgressError("Role 与 Charge 快照不是同一游戏进程")

    normalized = normalize_vip_progress(
        vip_level=role_snapshot.get("vip_level"),
        current_vip_exp=charge_snapshot.get("vip_exp"),
        vip_exp_by_level=vip_exp_by_level,
    )
    return {
        "ok": True,
        "available": True,
        "complete": True,
        "source": "runtime_memory.role_charge+static_config.Vip.Vip",
        **normalized,
        "captured_at": charge_snapshot.get("captured_at"),
        "evidence": {
            "pid": role_identity[0],
            "process_start_ticks": role_identity[1],
            "role_source": role_snapshot.get("source"),
            "charge_source": charge_snapshot.get("source"),
            "read_only": True,
        },
    }


__all__ = [
    "VipProgressError",
    "compose_vip_progress_snapshot",
    "normalize_vip_progress",
    "vip_progress_delta",
]
