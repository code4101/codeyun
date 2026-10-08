import threading
from types import SimpleNamespace

import pytest

from backend.core.fanxiu.data_annotation.tasks import xianyuan_auto_gift as gifts


def execute(monkeypatch, result):
    monkeypatch.setattr(gifts, 'routed_npc_gift_items', lambda _: [SimpleNamespace(base_id=37092001)])
    monkeypatch.setattr(gifts, 'build_xianyuan_auto_gift_plan', lambda *_: {'items': []})

    def adapter(*_):
        yield 'observing'
        return result

    task = gifts.execute_xianyuan_auto_gift_task(None, {}, {}, threading.Event(),
        storage_reader=lambda: {}, xianyuan_reader=lambda: {}, gui_adapter=adapter)
    assert next(task) == 'observing'
    try:
        next(task)
    except StopIteration as done:
        return done.value


@pytest.mark.parametrize('result', [
    {'ok': True, 'verified': True, 'outcome': 'partial', 'remaining_items': [{'base_id': 37092001, 'quantity': 1031}]},
    {'ok': True, 'verified': True, 'outcome': 'partial', 'yield_for_due_job': {'admitted': False}},
    {'ok': True, 'verified': True, 'outcome': 'complete', 'remaining_items': []},
])
def test_parent_preserves_verified_adapter_completion_scope(monkeypatch, result):
    outcome = execute(monkeypatch, result)
    assert outcome['outcome'] == result['outcome']
    assert outcome['action_result'] == result


@pytest.mark.parametrize('reported', [None, 'success', ''])
def test_verified_effect_without_explicit_scope_is_not_full_completion(monkeypatch, reported):
    with pytest.raises(RuntimeError, match='完成范围'):
        execute(monkeypatch, {'ok': True, 'verified': True, 'outcome': reported})
