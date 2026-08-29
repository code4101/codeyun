from __future__ import annotations

"""Pure configuration planning for the 天地弈局 auto-challenge dialog."""

from collections.abc import Mapping
from typing import Any


AUTO_USE_STRENGTH_ITEM = "auto_use_strength_item"
CONTINUE_AFTER_DEFEAT = "continue_after_defeat"
SKIP_ANIMATION = "skip_animation"
MASTER_SKILL_ITEM = "master_skill_item"
QUADRUPLE_CHESS_TOKEN_ITEM = "quadruple_chess_token_item"

# Keep the order identical to the dialog: the three common switches first,
# followed by the two cross-server item switches.
TIANDI_YIJU_AUTO_CONFIG_OPTIONS = (
    {
        "key": AUTO_USE_STRENGTH_ITEM,
        "label": "自动使用仙弈盒",
        "shape": "自动使用仙弈盒开关",
    },
    {
        "key": CONTINUE_AFTER_DEFEAT,
        "label": "对弈失败时不中断自动对弈",
        "shape": "对弈失败时不中断自动对弈开关",
    },
    {
        "key": SKIP_ANIMATION,
        "label": "跳过动画",
        "shape": "跳过动画开关",
    },
    {
        "key": MASTER_SKILL_ITEM,
        "label": "妙手技",
        # This is the current formal #680 Shape title.  The business name is
        # 妙手技; the planner does not infer a different control from OCR.
        "shape": "妙手珠开关",
    },
    {
        "key": QUADRUPLE_CHESS_TOKEN_ITEM,
        "label": "四倍棋符",
        "shape": "四倍棋符开关",
    },
)


def _positive_cross_count(value: Any) -> int:
    if isinstance(value, bool):
        raise RuntimeError("天地弈局配置缺少有效活动跨数")
    try:
        cross_count = int(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("天地弈局配置缺少有效活动跨数") from exc
    if cross_count <= 0:
        raise RuntimeError("天地弈局配置缺少有效活动跨数")
    return cross_count


def desired_tiandi_yiju_auto_challenge_choices(
    cross_count: int,
    *,
    feature_item_available: Mapping[str, bool] | None = None,
) -> dict[str, bool]:
    """Return the five desired switches for a Runtime-proven occurrence."""

    use_cross_server_items = _positive_cross_count(cross_count) > 1
    availability = feature_item_available or {}
    return {
        AUTO_USE_STRENGTH_ITEM: True,
        CONTINUE_AFTER_DEFEAT: True,
        SKIP_ANIMATION: True,
        MASTER_SKILL_ITEM: use_cross_server_items
        and bool(availability.get(MASTER_SKILL_ITEM, False)),
        QUADRUPLE_CHESS_TOKEN_ITEM: use_cross_server_items
        and bool(availability.get(QUADRUPLE_CHESS_TOKEN_ITEM, False)),
    }


def plan_tiandi_yiju_auto_challenge_configuration(
    current_choices: Mapping[str, Any],
    *,
    cross_count: int,
    feature_item_available: Mapping[str, bool] | None = None,
) -> dict[str, Any]:
    """Build a deterministic click plan without performing GUI operations."""

    if not isinstance(current_choices, Mapping):
        raise RuntimeError("天地弈局 Runtime 缺少自动挑战配置状态")
    desired = desired_tiandi_yiju_auto_challenge_choices(
        cross_count,
        feature_item_available=feature_item_available,
    )
    missing = [key for key in desired if key not in current_choices]
    if missing:
        raise RuntimeError(
            "天地弈局 Runtime 缺少自动挑战配置字段：" + ", ".join(missing)
        )

    current: dict[str, bool] = {}
    for key in desired:
        value = current_choices[key]
        if not isinstance(value, bool):
            raise RuntimeError(f"天地弈局 Runtime 配置字段不是布尔值：{key}")
        current[key] = value

    actions = [
        {
            "key": option["key"],
            "label": option["label"],
            "shape": option["shape"],
            "current": current[option["key"]],
            "desired": desired[option["key"]],
        }
        for option in TIANDI_YIJU_AUTO_CONFIG_OPTIONS
        if current[option["key"]] != desired[option["key"]]
    ]
    resolved_cross_count = _positive_cross_count(cross_count)
    return {
        "cross_count": resolved_cross_count,
        "mode": "cross_server" if resolved_cross_count > 1 else "local_server",
        "current": current,
        "desired": desired,
        "actions": actions,
        "already_configured": not actions,
    }


def plan_tiandi_yiju_auto_challenge_from_runtime(
    snapshot: Mapping[str, Any],
    *,
    cross_count: int | None = None,
    feature_item_available: Mapping[str, bool] | None = None,
) -> dict[str, Any]:
    """Validate a Runtime projection and build its GUI-only difference plan.

    ``auto_challenge_choices`` must be a semantic Runtime projection.  This
    function intentionally has no OCR or screenshot fallback: until all five
    fields are decoded authoritatively, configuration fails closed.
    """

    if not isinstance(snapshot, Mapping) or not all(
        bool(snapshot.get(key)) for key in ("ok", "available", "complete")
    ):
        raise RuntimeError("天地弈局 Runtime 自动挑战配置事实不完整")

    snapshot_cross_count = snapshot.get("cross_count")
    if cross_count is None:
        resolved_cross_count = _positive_cross_count(snapshot_cross_count)
    else:
        resolved_cross_count = _positive_cross_count(cross_count)
        if snapshot_cross_count is not None and (
            _positive_cross_count(snapshot_cross_count) != resolved_cross_count
        ):
            raise RuntimeError("天地弈局 Runtime 活动跨数与当前 occurrence 不一致")

    return plan_tiandi_yiju_auto_challenge_configuration(
        snapshot.get("auto_challenge_choices"),
        cross_count=resolved_cross_count,
        feature_item_available=feature_item_available,
    )


__all__ = [
    "AUTO_USE_STRENGTH_ITEM",
    "CONTINUE_AFTER_DEFEAT",
    "MASTER_SKILL_ITEM",
    "QUADRUPLE_CHESS_TOKEN_ITEM",
    "SKIP_ANIMATION",
    "TIANDI_YIJU_AUTO_CONFIG_OPTIONS",
    "desired_tiandi_yiju_auto_challenge_choices",
    "plan_tiandi_yiju_auto_challenge_configuration",
    "plan_tiandi_yiju_auto_challenge_from_runtime",
]
