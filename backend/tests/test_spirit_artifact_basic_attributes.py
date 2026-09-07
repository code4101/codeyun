from dataclasses import replace

import pytest

from backend.core.fanxiu.data_annotation.tasks.spirit_artifact_basic_attributes import (
    WashAttribute, plan_basic_attributes,
)


def rows(names, locked=()):
    return [WashAttribute(i + 1, name, 100, 4, i in locked) for i, name in enumerate(names)]


def test_lock_only_existing_targets():
    current = rows(['灵力', '攻击', '守御', '气血恢复', '暴击', '灵力恢复'], (0,))
    assert plan_basic_attributes(current).lock_ids == (2, 3)


def test_gain_type_despite_lower_value_and_quality():
    current = rows(['灵力', '攻击', '守御', '气血恢复', '暴击', '灵力恢复'], (0, 1, 2))
    candidate = list(current)
    candidate[3] = WashAttribute(7, '气血', 1, 1, False)
    assert plan_basic_attributes(current, candidate).action == 'retain'
    assert plan_basic_attributes(candidate).lock_ids == (7,)
    candidate[3] = replace(candidate[3], locked=True)
    assert plan_basic_attributes(candidate).action == 'complete'


def test_numeric_improvement_is_not_new_type():
    current = rows(['灵力', '攻击', '守御', '气血恢复', '暴击', '灵力恢复'], (0, 1, 2))
    candidate = list(current)
    candidate[3] = replace(candidate[3], value=9999, quality=6)
    assert plan_basic_attributes(current, candidate).action == 'wash'


def test_changed_lock_is_not_accepted():
    current = rows(['灵力', '攻击', '守御', '气血恢复', '暴击', '灵力恢复'], (0, 1, 2))
    candidate = list(current)
    candidate[0] = replace(candidate[0], value=1)
    assert plan_basic_attributes(current, candidate).action == 'blocked'


def test_defense_alias_and_map_order_do_not_change_completion():
    current = rows(['灵力', '攻击', '防御', '气血', '暴击', '灵力恢复'], (0, 1, 2, 3))
    assert plan_basic_attributes(list(reversed(current))).action == 'complete'


def test_unrelated_lock_requires_undefined_policy():
    current = rows(['灵力', '攻击', '守御', '气血恢复', '暴击', '灵力恢复'], (0, 1, 2, 4))
    assert plan_basic_attributes(current).action == 'blocked'


def test_incomplete_input_rejected():
    with pytest.raises(ValueError):
        plan_basic_attributes(rows(['灵力']))
