from __future__ import annotations

import pytest

from backend.core.fanxiu.data_annotation.tasks.magic_invasion_auto_config import (
    AUTO_USE_EXORCISM_ORDER,
    ELDER_CHASE_CHAIN,
    ELDER_QUADRUPLE_MERIT,
    EXTRATERRESTRIAL_DEMON_CHASE_CHAIN,
    EXTRATERRESTRIAL_DEMON_QUADRUPLE_MERIT,
    FAST_EXORCISM,
    MAGIC_INVASION_AUGMENTED_QUALITIES,
    MAGIC_INVASION_PARTICIPATING_QUALITIES,
    QUALITY_ELDER,
    QUALITY_EXTRATERRESTRIAL_DEMON,
    QUALITY_HALL_MASTER,
    QUALITY_SAINT_MASTER,
    QUALITY_SECT_MASTER,
    QUALITY_SUPREME_ELDER,
    SKIP_ANIMATION,
    SAINT_MASTER_CHASE_CHAIN,
    SAINT_MASTER_QUADRUPLE_MERIT,
    SECT_MASTER_CHASE_CHAIN,
    SECT_MASTER_QUADRUPLE_MERIT,
    SUPREME_ELDER_CHASE_CHAIN,
    SUPREME_ELDER_QUADRUPLE_MERIT,
    TENFOLD_EXORCISM,
    desired_magic_invasion_auto_choices,
    plan_magic_invasion_auto_configuration,
    plan_magic_invasion_auto_from_runtime,
)


def test_magic_auto_policy_is_identical_for_local_and_cross_business_modes() -> None:
    assert desired_magic_invasion_auto_choices() == {
        QUALITY_HALL_MASTER: False,
        QUALITY_ELDER: False,
        ELDER_QUADRUPLE_MERIT: False,
        ELDER_CHASE_CHAIN: False,
        QUALITY_SECT_MASTER: True,
        SECT_MASTER_QUADRUPLE_MERIT: False,
        SECT_MASTER_CHASE_CHAIN: False,
        QUALITY_SUPREME_ELDER: True,
        SUPREME_ELDER_QUADRUPLE_MERIT: True,
        SUPREME_ELDER_CHASE_CHAIN: True,
        QUALITY_SAINT_MASTER: True,
        SAINT_MASTER_QUADRUPLE_MERIT: True,
        SAINT_MASTER_CHASE_CHAIN: True,
        QUALITY_EXTRATERRESTRIAL_DEMON: True,
        EXTRATERRESTRIAL_DEMON_QUADRUPLE_MERIT: True,
        EXTRATERRESTRIAL_DEMON_CHASE_CHAIN: True,
        AUTO_USE_EXORCISM_ORDER: True,
        SKIP_ANIMATION: True,
        FAST_EXORCISM: True,
        TENFOLD_EXORCISM: False,
    }
    assert MAGIC_INVASION_PARTICIPATING_QUALITIES == {
        QUALITY_SECT_MASTER,
        QUALITY_SUPREME_ELDER,
        QUALITY_SAINT_MASTER,
        QUALITY_EXTRATERRESTRIAL_DEMON,
    }
    assert MAGIC_INVASION_AUGMENTED_QUALITIES == {
        QUALITY_SUPREME_ELDER,
        QUALITY_SAINT_MASTER,
        QUALITY_EXTRATERRESTRIAL_DEMON,
    }


def test_magic_auto_difference_plan_changes_only_mismatched_controls() -> None:
    current = desired_magic_invasion_auto_choices()
    current[QUALITY_ELDER] = True
    current[SUPREME_ELDER_CHASE_CHAIN] = False
    current[TENFOLD_EXORCISM] = True

    plan = plan_magic_invasion_auto_configuration(current)

    assert [action["key"] for action in plan["actions"]] == [
        QUALITY_ELDER,
        SUPREME_ELDER_CHASE_CHAIN,
        TENFOLD_EXORCISM,
    ]
    assert [action["desired"] for action in plan["actions"]] == [False, True, False]
    assert plan["already_configured"] is False


def test_magic_auto_runtime_plan_is_idempotent_and_fail_closed() -> None:
    desired = desired_magic_invasion_auto_choices()
    plan = plan_magic_invasion_auto_from_runtime(
        {
            "ok": True,
            "available": True,
            "complete": True,
            "auto_exorcism_choices": desired,
        }
    )
    assert plan["already_configured"] is True
    assert plan["actions"] == []

    with pytest.raises(RuntimeError, match="事实不完整"):
        plan_magic_invasion_auto_from_runtime(
            {
                "ok": True,
                "available": True,
                "complete": False,
                "auto_exorcism_choices": desired,
            }
        )
    with pytest.raises(RuntimeError, match="缺少配置字段"):
        plan_magic_invasion_auto_configuration({QUALITY_HALL_MASTER: False})
