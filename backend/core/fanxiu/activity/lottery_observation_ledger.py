from __future__ import annotations

"""Pure validation for paired lottery scatter observations.

Persistence adapters remain free to choose SQL models and record keys.  This
module owns the business invariant: a consumptive draw is represented by one
``before_draw`` and one ``after_draw`` observation with the same action id.
"""

from dataclasses import dataclass
from typing import Any, Iterable, Literal, Mapping


PAIRED_DRAW_LEDGER_PROTOCOL = "paired_draw_v1"
DrawMode = Literal["ten_draw", "single_draw"]


class LotteryObservationConflict(ValueError):
    """An observation conflicts with the monotonic activity ledger."""


@dataclass(frozen=True)
class LotteryObservationAppend:
    idempotent: bool
    dx: int
    dy: int


def build_paired_draw_observations(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    *,
    action_id: str,
    draw_mode: DrawMode,
    requested_batch_size: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Normalize and validate one atomic draw's before/after scatter pair."""

    identity = str(action_id or "").strip()
    if not identity:
        raise LotteryObservationConflict("抽奖散点缺少动作 identity")
    requested = _positive_int(requested_batch_size, "requested_batch_size")
    if draw_mode == "single_draw" and requested != 1:
        raise LotteryObservationConflict("单抽请求批次必须为 1")
    if draw_mode == "ten_draw" and requested != 10:
        raise LotteryObservationConflict("十连请求批次必须为 10")

    before_point = _normalize_point(
        before,
        action_id=identity,
        phase="before_draw",
        draw_mode=draw_mode,
        requested_batch_size=requested,
    )
    after_point = _normalize_point(
        after,
        action_id=identity,
        phase="after_draw",
        draw_mode=draw_mode,
        requested_batch_size=requested,
    )
    _validate_pair(before_point, after_point)
    dx = int(after_point["x"]) - int(before_point["x"])
    dy = int(after_point["y"]) - int(before_point["y"])
    after_point["batch_size"] = dx
    after_point["dx"] = dx
    after_point["dy"] = dy
    before_point["batch_size"] = 0
    before_point["dx"] = 0
    before_point["dy"] = 0
    return before_point, after_point


def build_draw_before_observation(
    before: Mapping[str, Any],
    *,
    action_id: str,
    draw_mode: DrawMode,
    requested_batch_size: int,
) -> dict[str, Any]:
    """Build the durable pre-action half before the GUI click is issued."""

    identity = str(action_id or "").strip()
    if not identity:
        raise LotteryObservationConflict("抽奖散点缺少动作 identity")
    requested = _positive_int(requested_batch_size, "requested_batch_size")
    if (draw_mode, requested) not in {("single_draw", 1), ("ten_draw", 10)}:
        raise LotteryObservationConflict("抽奖模式与请求批次不一致")
    point = _normalize_point(
        before,
        action_id=identity,
        phase="before_draw",
        draw_mode=draw_mode,
        requested_batch_size=requested,
    )
    point.update({"batch_size": 0, "dx": 0, "dy": 0})
    return point


def validate_lottery_observation_append(
    existing: Iterable[Mapping[str, Any]],
    snapshot: Mapping[str, Any],
) -> LotteryObservationAppend:
    """Validate one paired-protocol append without mutating the sequence."""

    point = dict(snapshot)
    if point.get("ledger_protocol") != PAIRED_DRAW_LEDGER_PROTOCOL:
        return LotteryObservationAppend(idempotent=False, dx=0, dy=0)
    phase = str(point.get("action_phase") or point.get("observation_kind") or "")
    action_id = str(point.get("action_id") or "")
    if phase not in {"before_draw", "after_draw"} or not action_id:
        raise LotteryObservationConflict("paired_draw_v1 必须提供 draw phase 与 action_id")
    normalized = _normalize_point(
        point,
        action_id=action_id,
        phase=phase,
        draw_mode=_draw_mode(point.get("draw_mode")),
        requested_batch_size=_positive_int(
            point.get("requested_batch_size"), "requested_batch_size"
        ),
    )
    history = [
        dict(item)
        for item in existing
        if item.get("ledger_protocol") == PAIRED_DRAW_LEDGER_PROTOCOL
    ]
    exact = next(
        (
            item
            for item in history
            if str(item.get("action_id") or "") == action_id
            and str(item.get("action_phase") or item.get("observation_kind") or "")
            == phase
        ),
        None,
    )
    if exact is not None:
        comparable = _normalize_point(
            exact,
            action_id=action_id,
            phase=phase,
            draw_mode=_draw_mode(exact.get("draw_mode")),
            requested_batch_size=_positive_int(
                exact.get("requested_batch_size"), "requested_batch_size"
            ),
        )
        if _point_signature(comparable) != _point_signature(normalized):
            raise LotteryObservationConflict(
                f"同一动作阶段发生冲突：action_id={action_id}, phase={phase}"
            )
        return LotteryObservationAppend(idempotent=True, dx=0, dy=0)

    _validate_global_identity(history, normalized)
    if phase == "before_draw":
        dangling = [
            item
            for item in history
            if str(item.get("action_phase") or "") == "before_draw"
            and not any(
                str(candidate.get("action_id") or "")
                == str(item.get("action_id") or "")
                and str(candidate.get("action_phase") or "") == "after_draw"
                for candidate in history
            )
        ]
        if dangling:
            raise LotteryObservationConflict("上一批抽奖只有 before 散点，拒绝开启新批次")
        latest = _latest(history)
        if latest is not None and (
            int(normalized["x"]) != int(latest["x"])
            or int(normalized["y"]) != int(latest["y"])
        ):
            raise LotteryObservationConflict("新批次 before 散点未承接上一累计点")
        return LotteryObservationAppend(idempotent=False, dx=0, dy=0)

    matching_before = next(
        (
            item
            for item in history
            if str(item.get("action_id") or "") == action_id
            and str(item.get("action_phase") or "") == "before_draw"
        ),
        None,
    )
    if matching_before is None:
        raise LotteryObservationConflict(
            f"after_draw 缺少匹配的 before_draw：action_id={action_id}"
        )
    _validate_pair(matching_before, normalized)
    dx = int(normalized["x"]) - int(matching_before["x"])
    dy = int(normalized["y"]) - int(matching_before["y"])
    return LotteryObservationAppend(idempotent=False, dx=dx, dy=dy)


def _normalize_point(
    point: Mapping[str, Any],
    *,
    action_id: str,
    phase: str,
    draw_mode: DrawMode,
    requested_batch_size: int,
) -> dict[str, Any]:
    if point.get("complete") is False:
        raise LotteryObservationConflict(str(point.get("reason") or "抽奖散点不完整"))
    activity_id = _positive_int(point.get("activity_id"), "activity_id")
    x = _nonnegative_int(point.get("x"), "x")
    y = _nonnegative_int(point.get("y"), "y")
    if y > x:
        raise LotteryObservationConflict(f"大奖累计不能超过抽数：x={x}, y={y}")
    selected = point.get("selected_big_reward")
    target_id = point.get("selected_library_id")
    if target_id is None and isinstance(selected, Mapping):
        target_id = selected.get("library_id")
    target_id = _positive_int(target_id, "selected_library_id")
    result = {
        **point,
        "ledger_protocol": PAIRED_DRAW_LEDGER_PROTOCOL,
        "activity_id": activity_id,
        "x": x,
        "y": y,
        "selected_library_id": target_id,
        "action_id": action_id,
        "observation_kind": phase,
        "action_phase": phase,
        "draw_mode": draw_mode,
        "requested_batch_size": requested_batch_size,
    }
    for field in ("available_draws", "progress"):
        if result.get(field) is not None:
            result[field] = _nonnegative_int(result[field], field)
    return result


def _validate_pair(before: Mapping[str, Any], after: Mapping[str, Any]) -> None:
    for field in (
        "activity_id",
        "selected_library_id",
        "action_id",
        "draw_mode",
        "requested_batch_size",
    ):
        if before.get(field) != after.get(field):
            raise LotteryObservationConflict(f"抽奖 before/after 字段冲突：{field}")
    dx = int(after["x"]) - int(before["x"])
    dy = int(after["y"]) - int(before["y"])
    requested = int(before["requested_batch_size"])
    if dx <= 0 or dx > requested or dy < 0 or dy > dx:
        raise LotteryObservationConflict(f"抽奖增量非法：dx={dx}, dy={dy}, request={requested}")
    if before.get("draw_mode") == "single_draw" and dx != 1:
        raise LotteryObservationConflict(f"单抽实际增量必须为 1：dx={dx}")
    if after.get("batch_size") is not None and int(after["batch_size"]) != dx:
        raise LotteryObservationConflict("after_draw.batch_size 与累计抽数差不一致")
    if before.get("progress") is not None and after.get("progress") is not None:
        if int(after["progress"]) - int(before["progress"]) != dx:
            raise LotteryObservationConflict("累抽进度差与实际抽数不一致")
    if before.get("available_draws") is not None and after.get("available_draws") is not None:
        if int(before["available_draws"]) - int(after["available_draws"]) != dx:
            raise LotteryObservationConflict("抽奖前后可用次数差与实际抽数不一致")


def _validate_global_identity(
    history: list[dict[str, Any]], point: Mapping[str, Any]
) -> None:
    for item in history:
        if int(item.get("activity_id") or 0) != int(point["activity_id"]):
            raise LotteryObservationConflict("同一账本的活动实例身份发生变化")
        target_id = item.get("selected_library_id")
        if target_id is None and isinstance(item.get("selected_big_reward"), Mapping):
            target_id = item["selected_big_reward"].get("library_id")
        if int(target_id or 0) != int(point["selected_library_id"]):
            raise LotteryObservationConflict("同一账本的大奖目标发生变化")
        if int(point["x"]) < int(item.get("x") or 0) or int(point["y"]) < int(
            item.get("y") or 0
        ):
            raise LotteryObservationConflict("抽奖累计散点发生倒退")
        if int(point["x"]) == int(item.get("x") or 0) and int(point["y"]) != int(
            item.get("y") or 0
        ):
            raise LotteryObservationConflict("同一累计抽数对应不同大奖累计")


def _latest(history: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not history:
        return None
    return max(
        history,
        key=lambda item: (
            int(item.get("x") or 0),
            1 if str(item.get("action_phase") or "") == "after_draw" else 0,
        ),
    )


def _point_signature(point: Mapping[str, Any]) -> tuple[Any, ...]:
    return tuple(
        point.get(field)
        for field in (
            "activity_id",
            "x",
            "y",
            "selected_library_id",
            "action_id",
            "action_phase",
            "draw_mode",
            "requested_batch_size",
            "batch_size",
            "available_draws",
            "progress",
        )
    )


def _draw_mode(value: Any) -> DrawMode:
    if value not in {"ten_draw", "single_draw"}:
        raise LotteryObservationConflict(f"抽奖模式无效：{value!r}")
    return value


def _nonnegative_int(value: Any, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise LotteryObservationConflict(f"{field} 必须是非负整数：{value!r}")
    return value


def _positive_int(value: Any, field: str) -> int:
    result = _nonnegative_int(value, field)
    if result <= 0:
        raise LotteryObservationConflict(f"{field} 必须是正整数：{value!r}")
    return result


__all__ = [
    "PAIRED_DRAW_LEDGER_PROTOCOL",
    "LotteryObservationAppend",
    "LotteryObservationConflict",
    "build_draw_before_observation",
    "build_paired_draw_observations",
    "validate_lottery_observation_append",
]
