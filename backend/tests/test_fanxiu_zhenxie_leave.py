from backend.core.fanxiu.data_annotation.tasks.zhenxie import (
    _ZHENXIE_LEAVE_REDISCOVER_WAIT_SECONDS,
    zhenxie_landing_wait_seconds,
)


def test_zhenxie_landing_wait_capped_by_constant():
    # 剩余预算很大时，单次等待必须被上限截断（此前会拖满整段 deadline 造成 133s 卡顿）。
    assert zhenxie_landing_wait_seconds(deadline=1e9, now=0.0) == _ZHENXIE_LEAVE_REDISCOVER_WAIT_SECONDS


def test_zhenxie_landing_wait_uses_remaining_deadline_when_smaller():
    assert zhenxie_landing_wait_seconds(deadline=105.0, now=100.0) == 5.0


def test_zhenxie_landing_wait_floor_is_one_second():
    assert zhenxie_landing_wait_seconds(deadline=100.0, now=250.0) == 1.0
