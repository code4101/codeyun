import pytest

from backend.core.fanxiu.data_annotation.tasks.spirit_artifact_protected_locks import (
    plan_spirit_artifact_protected_locks,
)


def plan(names):
    rules = {i: {'code': name if name in ('A', 'B') else '', 'name': name}
             for i, name in enumerate(names, 1)}
    return plan_spirit_artifact_protected_locks(
        [{'cleanse_id': i} for i in rules], rules, {'A'})


@pytest.mark.parametrize('special', ['灵器无双', '混沌灵威', '混沌道威'])
def test_existing_s_is_protected_with_a(special):
    result = plan(['A', special, 'B'])
    assert result['desired_lock_ids'] == [1, 2]
    assert result['washable_ids'] == [3]


def test_all_protected_is_skip_not_lock_all():
    result = plan(['A', '灵器无双', '混沌道威'])
    assert result['should_wash'] is False
    assert result['desired_lock_ids'] is None


def test_unknown_classification_is_not_unlocked():
    with pytest.raises(ValueError, match='分类不明'):
        plan(['A', '新特殊属性'])
