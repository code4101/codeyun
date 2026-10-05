import pytest

from backend.core.fanxiu.instrumentation.item_reward_graph import index_loaded_optional_gift_rows
from backend.core.fanxiu.instrumentation.runtime_memory import FanxiuRuntimeMemoryError


def test_runtime_string_group_and_unallocated_row():
    rows = [
        dict(id=54575, groupID='24166', giftID=10000003, number=20, showCondition=''),
        dict(id=54526, groupID='', giftID=0, number=0, showCondition=''),
    ]
    assert index_loaded_optional_gift_rows(rows) == {
        24166: [dict(id=10000003, count=20, config_id=54575, show_condition='')]
    }


@pytest.mark.parametrize('field,value', [('number', 0), ('giftID', None), ('showCondition', None)])
def test_incomplete_real_reward_is_not_placeholder(field, value):
    row = dict(id=54575, groupID='24166', giftID=10000003, number=20, showCondition='')
    row[field] = value
    with pytest.raises(FanxiuRuntimeMemoryError):
        index_loaded_optional_gift_rows([row])


def test_duplicate_identity_rejected():
    row = dict(id=54575, groupID='24166', giftID=10000003, number=20, showCondition='')
    with pytest.raises(FanxiuRuntimeMemoryError):
        index_loaded_optional_gift_rows([row, row])


def test_config_order_independent_of_runtime_hash_order():
    rows = [dict(id=i, groupID='24166', giftID=10000003, number=20, showCondition='')
            for i in (54576, 54575)]
    assert index_loaded_optional_gift_rows(rows) == index_loaded_optional_gift_rows(reversed(rows))
