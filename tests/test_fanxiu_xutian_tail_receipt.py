from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from backend.core.fanxiu.data_annotation.tasks.xutian_tail import validated_xutian_tail_receipt


def test_only_exact_ended_finite_complete_receipt_can_suppress_consumption():
    end = datetime(2026, 10, 6, 22, tzinfo=timezone(timedelta(hours=8)))
    occurrence = SimpleNamespace(instance_key='exact-instance', end_at=end,
                                 close_at=end + timedelta(days=2))
    receipt = dict(status='completed', instance_key='exact-instance', activity_id='stored-id',
                   captured_at=(end + timedelta(hours=1)).isoformat(),
                   planning=dict(complete=True, closing_goods_reached=True),
                   rankings=dict(personal=78, plane=4))
    validate = lambda value: validated_xutian_tail_receipt(
        value, occurrence=occurrence, activity_id='stored-id')
    assert validate(receipt) == receipt
    assert validated_xutian_tail_receipt(receipt, occurrence=occurrence,
        activity_id='stored-id', require_overflow=True) is None
    settled = {**receipt, 'overflow_settled': True}
    assert validated_xutian_tail_receipt(settled, occurrence=occurrence,
        activity_id='stored-id', require_overflow=True) == settled
    for mutation in (
        dict(instance_key='another-instance'), dict(activity_id='another-id'),
        dict(status='pending'), dict(rankings=dict(personal=78, plane=0)),
        dict(planning=dict(complete=True, closing_goods_reached=False)),
        dict(captured_at=(end - timedelta(seconds=1)).isoformat()),
        dict(captured_at=occurrence.close_at.isoformat()), dict(captured_at='unknown'),
    ):
        assert validate({**receipt, **mutation}) is None
