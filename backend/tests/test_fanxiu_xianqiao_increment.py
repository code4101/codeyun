import pytest

from backend.core.fanxiu.data_annotation.tasks.resource_daily_plan import RESOURCE_COMPONENTS, increment_cycle
from backend.core.fanxiu.data_annotation.tasks.xianqiao_upgrade import xianqiao_stock_increment_key


def stock(count):
    return dict(complete=True, available_exp=count * 50,
                items=[dict(item_id=20100001, quantity=0, exp_per_item=10),
                       dict(item_id=20100003, quantity=count, exp_per_item=50)])


def worn(sum_exp):
    return dict(complete=True, equipped=[dict(part_id=1, equip_id=7, sum_exp=sum_exp)])


def test_exhausted_stock_admits_no_increment():
    assert xianqiao_stock_increment_key(stock(0), worn(512000)) is None


def test_increment_identity_is_stable_for_leftovers_and_new_after_consumption():
    first = xianqiao_stock_increment_key(stock(237), worn(512000))
    assert first == xianqiao_stock_increment_key(stock(237), worn(512000))
    assert first != xianqiao_stock_increment_key(stock(237), worn(523850))
    assert increment_cycle('2026-10-16', first) != '2026-10-16'


def test_incomplete_snapshot_refuses_admission():
    with pytest.raises(RuntimeError):
        xianqiao_stock_increment_key({**stock(1), 'complete': False}, worn(0))


def test_only_dedicated_upgrade_declares_increment():
    assert [c.module for c in RESOURCE_COMPONENTS if c.increment] == ['xianqiao_upgrade']
