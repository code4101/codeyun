from __future__ import annotations

"""Strict read-only facts for the currently loaded activity sign-in panel."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from backend.core.fanxiu.instrumentation.activity_menu import (
    active_ui_component_objects,
)
from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
    LuaRef,
    as_int,
    table_ref,
)
from backend.core.fanxiu.instrumentation.ui_runtime_context import (
    acquire_ui_runtime_context_fast,
)


_MILESTONE_DAYS = (3, 7, 14, 21, 28)
_PANEL_REQUIRED_FIELDS = frozenset(
    {
        "activityId",
        "activitySignInGetReward",
        "curSelectTurn",
        "dayInfoDic",
        "processScrollView",
        "signInInfo",
    }
)


@dataclass(frozen=True)
class ActivitySigninMilestone:
    day: int
    can_get_reward: bool


@dataclass(frozen=True)
class ActivitySigninSnapshot:
    activity_id: int
    turn_id: int
    total_days: int
    signed_days: tuple[int, ...]
    signed_day_count: int
    got_reward_ids: tuple[int, ...]
    milestones: tuple[ActivitySigninMilestone, ...]
    captured_at: str
    pid: int
    process_start_ticks: int


def _fields(reader: Any, value: Any) -> dict[Any, Any]:
    if not isinstance(value, LuaRef) or value.kind != "table":
        return {}
    return reader.fields(value)


def _list_values(reader: Any, value: Any, label: str) -> tuple[Any, ...]:
    ref = table_ref(value)
    if ref is None:
        raise FanxiuRuntimeMemoryError(f"每日签到 {label} 不是列表")
    rows, count = reader.indexed_list_items(ref)
    if count is None or count < 0 or len(rows) != count:
        raise FanxiuRuntimeMemoryError(f"每日签到 {label} 列表不完整")
    return tuple(item for _index, item in rows)


def _locate_panel(ctx: Any) -> LuaRef:
    candidates = []
    for component in active_ui_component_objects(ctx, include_descendants=True):
        fields = _fields(ctx.reader, component)
        if _PANEL_REQUIRED_FIELDS <= set(fields):
            candidates.append(component)
    if len(candidates) != 1:
        raise FanxiuRuntimeMemoryError(
            f"每日签到活动面板对象不唯一：{len(candidates)} 个候选",
            code="data_not_loaded" if not candidates else "runtime_incomplete",
        )
    return candidates[0]


def read_activity_signin_snapshot() -> ActivitySigninSnapshot:
    """Read milestone eligibility without calling Lua or changing game state."""

    ctx = acquire_ui_runtime_context_fast(frozenset())
    panel = _fields(ctx.reader, _locate_panel(ctx))
    activity_id = as_int(panel.get("activityId"))
    turn = _fields(ctx.reader, panel.get("curSelectTurn"))
    turn_id = as_int(turn.get("id"))
    total_days = as_int(turn.get("totalDays"))
    sign_in_days = _list_values(ctx.reader, turn.get("signInDays"), "已签到日期")
    got_reward_values = _list_values(ctx.reader, turn.get("gotRewardIds"), "已领累签奖励")

    scroll = _fields(ctx.reader, panel.get("processScrollView"))
    configs = _list_values(ctx.reader, scroll.get("ItemInfoList"), "累签奖励配置")
    config_addresses = [
        ref.address
        for value in configs
        if (ref := table_ref(value)) is not None
    ]
    if len(config_addresses) != len(_MILESTONE_DAYS) or len(set(config_addresses)) != len(config_addresses):
        raise FanxiuRuntimeMemoryError(
            f"每日签到累签奖励配置异常：{len(config_addresses)} 项",
            code="runtime_incomplete",
        )

    controller_map = table_ref(scroll.get("ItemClassDic"))
    if controller_map is None:
        raise FanxiuRuntimeMemoryError(
            "每日签到累签奖励控件表尚未物化",
            code="data_not_loaded",
        )
    controllers = ctx.reader.dictionary_fields(controller_map)
    state_by_config: dict[int, bool] = {}
    for value in controllers.values():
        fields = _fields(ctx.reader, value)
        config = table_ref(fields.get("cfg"))
        can_get_reward = fields.get("canGetReward")
        if config is None or not isinstance(can_get_reward, bool):
            continue
        if config.address in state_by_config:
            raise FanxiuRuntimeMemoryError(
                "每日签到累签奖励控件重复绑定配置",
                code="runtime_incomplete",
            )
        state_by_config[config.address] = can_get_reward
    if set(state_by_config) != set(config_addresses):
        raise FanxiuRuntimeMemoryError(
            "每日签到累签奖励控件尚未完整物化",
            code="data_not_loaded",
        )

    got_reward_ids = tuple(
        reward_id
        for value in got_reward_values
        if (reward_id := as_int(value)) is not None and reward_id > 0
    )
    if len(got_reward_ids) != len(got_reward_values) or len(set(got_reward_ids)) != len(got_reward_ids):
        raise FanxiuRuntimeMemoryError(
            "每日签到已领累签奖励 ID 列表异常",
            code="runtime_incomplete",
        )
    if activity_id is None or turn_id is None or total_days is None:
        raise FanxiuRuntimeMemoryError("每日签到活动身份或期次不完整", code="runtime_incomplete")
    signed_days_list: list[int] = []
    for value in sign_in_days:
        # The live controller stores each signed date as
        # ``{day=<number>, supplementary=<bool>}``, not as a bare integer.
        # Keep a bare-int fallback for older controller generations while
        # validating the current structured contract exactly.
        fields = _fields(ctx.reader, value)
        day = as_int(fields.get("day")) if fields else as_int(value)
        supplementary = fields.get("supplementary") if fields else False
        if day is None or not 1 <= day <= total_days or not isinstance(supplementary, bool):
            raise FanxiuRuntimeMemoryError(
                "每日签到已签到日期项异常",
                code="runtime_incomplete",
            )
        signed_days_list.append(day)
    signed_days = tuple(signed_days_list)
    if len(signed_days) != len(sign_in_days) or len(set(signed_days)) != len(signed_days):
        raise FanxiuRuntimeMemoryError(
            "每日签到已签到日期列表异常",
            code="runtime_incomplete",
        )

    return ActivitySigninSnapshot(
        activity_id=activity_id,
        turn_id=turn_id,
        total_days=total_days,
        signed_days=signed_days,
        signed_day_count=len(signed_days),
        got_reward_ids=got_reward_ids,
        milestones=tuple(
            ActivitySigninMilestone(day=day, can_get_reward=state_by_config[address])
            for day, address in zip(_MILESTONE_DAYS, config_addresses)
        ),
        captured_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        pid=ctx.binding.pid,
        process_start_ticks=ctx.binding.process_start_ticks,
    )


__all__ = [
    "ActivitySigninMilestone",
    "ActivitySigninSnapshot",
    "read_activity_signin_snapshot",
]
