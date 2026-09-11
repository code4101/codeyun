"""Pure authorization parser tests; no simulated game execution."""
import pytest

from backend.core.fanxiu.data_annotation.tasks.xinghai import parse_charge_state


def test_first_charge_is_exactly_one_hundred():
    assert parse_charge_state('今日已汇聚真元 0/4 次', '247349/100', 1)['cost'] == 100


def test_existing_charge_never_authorizes_spending():
    assert parse_charge_state('今日已汇聚真元 1/4 次', '', 4)['purchase_count'] == 0


@pytest.mark.parametrize('today,cost,count', [
    ('今日已汇聚真元 0/4 次', '247349/200', 1),
    ('今日已汇聚真元 0/4 次', '247349/100', 2),
    ('今日已汇聚真元 0/4 次', '', 1),
    ('', '247349/100', 1),
    ('今日已汇聚真元 5/4 次', '247349/100', 1),
])
def test_unverified_purchase_is_rejected(today, cost, count):
    with pytest.raises(ValueError):
        parse_charge_state(today, cost, count)
