"""Pure wall-stage accounting only; no game/context/purchase simulation."""
import pytest
from backend.core.fanxiu.data_annotation.tasks import magic_invasion_tail as tail


def test_nonoverlapping_stages_and_repeated_goods_aggregation(monkeypatch):
    clock = iter([0., 1., 3., 4., 7., 8.])
    monkeypatch.setattr(tail.time, 'perf_counter', lambda: next(clock))
    identity = {'scheduler_attempt_id': 'formal-attempt', 'instance_key': 'one-occurrence'}
    timing = tail._MagicTailTiming(identity=identity)
    identity['scheduler_attempt_id'] = 'changed-external-value'
    timing.mark('product_navigation', goods_id=1)
    timing.mark('quantity_verification', goods_id=1)
    timing.mark('product_navigation', goods_id=2)
    timing.mark('return_to_world')
    result = timing.snapshot(completed=True)
    assert result['identity']['scheduler_attempt_id'] == 'formal-attempt'
    assert result['started_at'] and result['observed_at']
    assert sum(r['seconds'] for r in result['samples']) == result['elapsed_seconds'] == 8
    assert result['aggregates']['product_navigation'] == {
        'count': 2, 'total_seconds': 5., 'mean_seconds': 2.5, 'std_seconds': .5, 'completed_count': 2}
    assert [r['goods_id'] for r in result['samples'] if r['stage'] == 'product_navigation'] == [1, 2]


def test_incomplete_stage_remains_explicit_and_snapshot_does_not_complete_it(monkeypatch):
    clock = iter([0., 2., 5., 6.])
    monkeypatch.setattr(tail.time, 'perf_counter', lambda: next(clock))
    timing = tail._MagicTailTiming()
    timing.mark('purchase_and_shop_return', goods_id=7)
    first = timing.snapshot(completed=False)
    second = timing.snapshot(completed=False)
    assert not first['completed'] and not first['samples'][-1]['completed']
    assert first['aggregates']['purchase_and_shop_return']['completed_count'] == 0
    assert sum(s['seconds'] for s in second['samples']) == pytest.approx(6)
    assert len(first['samples']) == len(second['samples']) == 2
