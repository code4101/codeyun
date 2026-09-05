from __future__ import annotations

"""Pure policy and difference planning for 魔道入侵 native auto-exorcism."""

from collections.abc import Mapping
from typing import Any


QUALITY_HALL_MASTER = "quality_hall_master"
QUALITY_ELDER = "quality_elder"
QUALITY_SECT_MASTER = "quality_sect_master"
QUALITY_SUPREME_ELDER = "quality_supreme_elder"
QUALITY_SAINT_MASTER = "quality_saint_master"
QUALITY_EXTRATERRESTRIAL_DEMON = "quality_extraterrestrial_demon"

ELDER_QUADRUPLE_MERIT = "elder_quadruple_merit"
ELDER_CHASE_CHAIN = "elder_chase_chain"
SECT_MASTER_QUADRUPLE_MERIT = "sect_master_quadruple_merit"
SECT_MASTER_CHASE_CHAIN = "sect_master_chase_chain"
SUPREME_ELDER_QUADRUPLE_MERIT = "supreme_elder_quadruple_merit"
SUPREME_ELDER_CHASE_CHAIN = "supreme_elder_chase_chain"
SAINT_MASTER_QUADRUPLE_MERIT = "saint_master_quadruple_merit"
SAINT_MASTER_CHASE_CHAIN = "saint_master_chase_chain"
EXTRATERRESTRIAL_DEMON_QUADRUPLE_MERIT = (
    "extraterrestrial_demon_quadruple_merit"
)
EXTRATERRESTRIAL_DEMON_CHASE_CHAIN = "extraterrestrial_demon_chase_chain"

AUTO_USE_EXORCISM_ORDER = "auto_use_exorcism_order"
SKIP_ANIMATION = "skip_animation"
FAST_EXORCISM = "fast_exorcism"
TENFOLD_EXORCISM = "tenfold_exorcism"


# Stable semantic order: the upper quality pane from low to high, followed by
# the lower global switches.  GUI code owns the mapping from these keys to
# formally annotated Shapes; this pure module never guesses coordinates.
MAGIC_INVASION_AUTO_CONFIG_OPTIONS = (
    {"key": QUALITY_HALL_MASTER, "section": "quality", "label": "堂主"},
    {"key": QUALITY_ELDER, "section": "quality", "label": "长老"},
    {
        "key": ELDER_QUADRUPLE_MERIT,
        "section": "quality_boost",
        "quality": QUALITY_ELDER,
        "label": "长老·四倍功勋符",
    },
    {
        "key": ELDER_CHASE_CHAIN,
        "section": "quality_boost",
        "quality": QUALITY_ELDER,
        "label": "长老·追命索",
    },
    {"key": QUALITY_SECT_MASTER, "section": "quality", "label": "宗主"},
    {
        "key": SECT_MASTER_QUADRUPLE_MERIT,
        "section": "quality_boost",
        "quality": QUALITY_SECT_MASTER,
        "label": "宗主·四倍功勋符",
    },
    {
        "key": SECT_MASTER_CHASE_CHAIN,
        "section": "quality_boost",
        "quality": QUALITY_SECT_MASTER,
        "label": "宗主·追命索",
    },
    {
        "key": QUALITY_SUPREME_ELDER,
        "section": "quality",
        "label": "太上长老",
    },
    {
        "key": SUPREME_ELDER_QUADRUPLE_MERIT,
        "section": "quality_boost",
        "quality": QUALITY_SUPREME_ELDER,
        "label": "太上长老·四倍功勋符",
    },
    {
        "key": SUPREME_ELDER_CHASE_CHAIN,
        "section": "quality_boost",
        "quality": QUALITY_SUPREME_ELDER,
        "label": "太上长老·追命索",
    },
    {
        "key": QUALITY_SAINT_MASTER,
        "section": "quality",
        "label": "圣主",
    },
    {
        "key": SAINT_MASTER_QUADRUPLE_MERIT,
        "section": "quality_boost",
        "quality": QUALITY_SAINT_MASTER,
        "label": "圣主·四倍功勋符",
    },
    {
        "key": SAINT_MASTER_CHASE_CHAIN,
        "section": "quality_boost",
        "quality": QUALITY_SAINT_MASTER,
        "label": "圣主·追命索",
    },
    {
        "key": QUALITY_EXTRATERRESTRIAL_DEMON,
        "section": "quality",
        "label": "天外魔神",
    },
    {
        "key": EXTRATERRESTRIAL_DEMON_QUADRUPLE_MERIT,
        "section": "quality_boost",
        "quality": QUALITY_EXTRATERRESTRIAL_DEMON,
        "label": "天外魔神·四倍功勋符",
    },
    {
        "key": EXTRATERRESTRIAL_DEMON_CHASE_CHAIN,
        "section": "quality_boost",
        "quality": QUALITY_EXTRATERRESTRIAL_DEMON,
        "label": "天外魔神·追命索",
    },
    {
        "key": AUTO_USE_EXORCISM_ORDER,
        "section": "global",
        "label": "自动使用除魔令",
    },
    {"key": SKIP_ANIMATION, "section": "global", "label": "跳过动画"},
    {"key": FAST_EXORCISM, "section": "global", "label": "快速除魔"},
    {"key": TENFOLD_EXORCISM, "section": "global", "label": "十连除魔"},
)

MAGIC_INVASION_PARTICIPATING_QUALITIES = frozenset(
    {
        QUALITY_SECT_MASTER,
        QUALITY_SUPREME_ELDER,
        QUALITY_SAINT_MASTER,
        QUALITY_EXTRATERRESTRIAL_DEMON,
    }
)
MAGIC_INVASION_AUGMENTED_QUALITIES = frozenset(
    {
        QUALITY_SUPREME_ELDER,
        QUALITY_SAINT_MASTER,
        QUALITY_EXTRATERRESTRIAL_DEMON,
    }
)

_QUALITY_POLICY_FIELDS = (
    (QUALITY_HALL_MASTER, ()),
    (QUALITY_ELDER, (ELDER_QUADRUPLE_MERIT, ELDER_CHASE_CHAIN)),
    (
        QUALITY_SECT_MASTER,
        (SECT_MASTER_QUADRUPLE_MERIT, SECT_MASTER_CHASE_CHAIN),
    ),
    (
        QUALITY_SUPREME_ELDER,
        (SUPREME_ELDER_QUADRUPLE_MERIT, SUPREME_ELDER_CHASE_CHAIN),
    ),
    (
        QUALITY_SAINT_MASTER,
        (SAINT_MASTER_QUADRUPLE_MERIT, SAINT_MASTER_CHASE_CHAIN),
    ),
    (
        QUALITY_EXTRATERRESTRIAL_DEMON,
        (
            EXTRATERRESTRIAL_DEMON_QUADRUPLE_MERIT,
            EXTRATERRESTRIAL_DEMON_CHASE_CHAIN,
        ),
    ),
)


def desired_magic_invasion_auto_choices() -> dict[str, bool]:
    """Return the single user-approved policy for both local and cross modes.

    Participation starts at 宗主, while consumable augments start one tier
    higher at 太上长老.  Keeping the two threshold sets independent prevents
    enabling 宗主's fourfold-merit or pursuit-chain switches by implication.
    """

    desired: dict[str, bool] = {}
    for quality_key, augment_keys in _QUALITY_POLICY_FIELDS:
        desired[quality_key] = quality_key in MAGIC_INVASION_PARTICIPATING_QUALITIES
        for augment_key in augment_keys:
            desired[augment_key] = quality_key in MAGIC_INVASION_AUGMENTED_QUALITIES
    desired.update({
        AUTO_USE_EXORCISM_ORDER: True,
        SKIP_ANIMATION: True,
        FAST_EXORCISM: True,
        TENFOLD_EXORCISM: False,
    })
    return desired


def plan_magic_invasion_auto_configuration(
    current_choices: Mapping[str, Any],
) -> dict[str, Any]:
    """Build an idempotent semantic toggle plan without operating the game."""

    if not isinstance(current_choices, Mapping):
        raise RuntimeError("魔道自动除魔 Runtime 缺少配置状态")

    desired = desired_magic_invasion_auto_choices()
    missing = [key for key in desired if key not in current_choices]
    if missing:
        raise RuntimeError(
            "魔道自动除魔 Runtime 缺少配置字段：" + ", ".join(missing)
        )

    current: dict[str, bool] = {}
    for key in desired:
        value = current_choices[key]
        if not isinstance(value, bool):
            raise RuntimeError(f"魔道自动除魔 Runtime 配置字段不是布尔值：{key}")
        current[key] = value

    actions = [
        {
            **option,
            "current": current[option["key"]],
            "desired": desired[option["key"]],
        }
        for option in MAGIC_INVASION_AUTO_CONFIG_OPTIONS
        if current[option["key"]] != desired[option["key"]]
    ]
    return {
        "current": current,
        "desired": desired,
        "actions": actions,
        "already_configured": not actions,
    }


def plan_magic_invasion_auto_from_runtime(
    snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate a complete semantic Runtime projection and plan its delta."""

    if not isinstance(snapshot, Mapping) or not all(
        snapshot.get(key) is True for key in ("ok", "available", "complete")
    ):
        raise RuntimeError("魔道自动除魔 Runtime 配置事实不完整")
    return plan_magic_invasion_auto_configuration(
        snapshot.get("auto_exorcism_choices")
    )


__all__ = [
    "AUTO_USE_EXORCISM_ORDER",
    "ELDER_CHASE_CHAIN",
    "ELDER_QUADRUPLE_MERIT",
    "EXTRATERRESTRIAL_DEMON_CHASE_CHAIN",
    "EXTRATERRESTRIAL_DEMON_QUADRUPLE_MERIT",
    "FAST_EXORCISM",
    "MAGIC_INVASION_AUTO_CONFIG_OPTIONS",
    "MAGIC_INVASION_AUGMENTED_QUALITIES",
    "MAGIC_INVASION_PARTICIPATING_QUALITIES",
    "QUALITY_ELDER",
    "QUALITY_EXTRATERRESTRIAL_DEMON",
    "QUALITY_HALL_MASTER",
    "QUALITY_SAINT_MASTER",
    "QUALITY_SECT_MASTER",
    "QUALITY_SUPREME_ELDER",
    "SKIP_ANIMATION",
    "SAINT_MASTER_CHASE_CHAIN",
    "SAINT_MASTER_QUADRUPLE_MERIT",
    "SECT_MASTER_CHASE_CHAIN",
    "SECT_MASTER_QUADRUPLE_MERIT",
    "SUPREME_ELDER_CHASE_CHAIN",
    "SUPREME_ELDER_QUADRUPLE_MERIT",
    "TENFOLD_EXORCISM",
    "desired_magic_invasion_auto_choices",
    "plan_magic_invasion_auto_configuration",
    "plan_magic_invasion_auto_from_runtime",
]
