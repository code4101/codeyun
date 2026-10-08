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
    result = drain(gifts.execute_xianyuan_selected_gift_adapter(runner, {}, {}, None, {'items': []}))
    assert result['outcome'] == 'partial'
    assert result['yield_for_due_job'] is admission
    assert [r['action_id'] for r in result['actions']] == [str(i) for i in range(finished_cycles + 1)]
    assert result['remaining_items'] == [{'base_id': 37092001, 'quantity': 10 - finished_cycles}]
    assert navigation == [34]


def test_real_cycle_publishes_settled_receipt_before_navigation_yield(monkeypatch):
    from backend.core.fanxiu.data_annotation import kernel_scheduler_control as scheduler
    from backend.core.fanxiu.data_annotation.tasks import theme_npc_gift

    admission = {'admitted': False, 'retry_at': '2026-10-08T12:00:00+08:00'}
    receipt = {'action_id': 'confirmed-gift', 'status': 'verified'}
    navigation = []

    def empty(*args, **kwargs):
        yield from ()

    class Context:
        def wait_click(self, scene, shape, *, before_click=None):
            if before_click:
                before_click()
            yield from ()

        def wait_scene(self, scenes, **kwargs):
            yield from ()
            return scenes[0]

        def go_scene(self, scene):
            navigation.append(scene)
            if len(navigation) == 1:
                raise gifts.XianyuanDueJobYield(admission)
            yield from ()

    runtime = {'runtime_complete': True, 'runtime_debug': {'pid': 1, 'process_start_ticks': 2},
               'people': [{'npc_id': 4005, 'rewards': []}]}
    counts = iter([2, 1])
    monkeypatch.setattr(gifts, 'read_xianyuan_gift_actions', lambda: [])
    monkeypatch.setattr(scheduler, 'read_scheduler_batch_admission', lambda **kwargs: {'admitted': True})
    monkeypatch.setattr(scheduler, 'read_scheduler_batch_admission', lambda **kwargs: {'admitted': True})
    monkeypatch.setattr(gifts, 'read_xianyuan_atlas_runtime', lambda **kwargs: runtime)
    monkeypatch.setattr(gifts, 'read_backpack_item_counts', lambda *args, **kwargs: ({37092001: next(counts)}, {}))
    monkeypatch.setattr(gifts, 'plan_xianyuan_gift_batch', lambda *args: {'action': 'gift', 'quantity': 1})
    monkeypatch.setattr(gifts, 'open_selected_npc_gift_page', empty)
    monkeypatch.setattr(gifts, 'select_single_npc_gift', empty)
    monkeypatch.setattr(gifts, 'read_xianyuan_gift_page_snapshot', lambda: {})
    monkeypatch.setattr(gifts, 'require_xianyuan_gift_selection', lambda *args, **kwargs: None)
    monkeypatch.setattr(gifts, 'begin_xianyuan_gift_action', lambda *args, **kwargs: {'action_id': 'confirmed-gift'})
    monkeypatch.setattr(gifts, 'settle_xianyuan_gift_action', lambda *args, **kwargs: receipt)
    monkeypatch.setattr(theme_npc_gift, 'claim_npc_return_gifts', empty)
    runner = SimpleNamespace(_behavior_tree_context=lambda *args, **kwargs: Context())
    plan = {'items': [{'base_id': 37092001, 'eligible_targets': [{'npc_id': 4005, 'name': '炎帝'}]}]}
    result = drain(gifts.execute_xianyuan_selected_gift_adapter(runner, {}, {}, None, plan))
    assert result['outcome'] == 'partial'
    assert result['actions'] == [receipt]
    assert result['remaining_items'] == [{'base_id': 37092001, 'quantity': 1}]
    assert navigation == [34, 34]
