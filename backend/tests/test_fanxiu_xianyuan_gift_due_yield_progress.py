"""A due-job handoff preserves verified gifts from earlier and current cycles."""
from types import SimpleNamespace

import pytest

from backend.core.fanxiu.data_annotation.tasks import xianyuan_gift_gui as gifts


def drain(generator):
    while True:
        try:
            next(generator)
        except StopIteration as exc:
            return exc.value


def leave(context):
    """Keep these tests focused on receipt preservation at the exit boundary."""
    yield from context.go_scene(34)


@pytest.mark.parametrize('finished_cycles', [0, 1, 2])
def test_due_yield_retains_all_verified_actions_including_interrupted_cycle(monkeypatch, finished_cycles):
    cycles = []
    navigation = []
    admission = {'admitted': False, 'retry_at': '2026-10-08T12:00:00+08:00'}

    def cycle(*args, progress):
        index = len(cycles)
        cycles.append(index)
        # Represents an already-settled effect, before subsequent navigation.
        progress['actions'].append({'action_id': str(index), 'status': 'verified'})
        progress['remaining_items'][37092001] = 10 - index
        yield 'verified_effect'
        if index == finished_cycles:
            raise gifts.XianyuanDueJobYield(admission)
        return {'outcome': 'partial', 'actions': [progress['actions'][-1]]}

    class Context:
        def go_scene(self, scene):
            navigation.append(scene)
            yield from ()

    runner = SimpleNamespace(_behavior_tree_context=lambda *args, **kwargs: Context())
    monkeypatch.setattr(gifts, 'run_xianyuan_selected_gift_adapter', cycle)
    monkeypatch.setattr(gifts, 'leave_selected_npc_without_gifting', leave)
    result = drain(gifts.execute_xianyuan_selected_gift_adapter(runner, {}, {}, None, {'items': []}))
    assert result['outcome'] == 'partial'
    assert result['yield_for_due_job'] is admission
    assert [r['action_id'] for r in result['actions']] == [str(i) for i in range(finished_cycles + 1)]
    assert result['remaining_items'] == [{'base_id': 37092001, 'quantity': 10 - finished_cycles}]
    assert navigation == [34]

