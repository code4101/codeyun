"""A provider wait cannot make preparation evidence authorize the final click."""
import time
from copy import deepcopy
from types import SimpleNamespace

import pytest

from backend.core.fanxiu.data_annotation.tasks import xianyuan_gift_gui as gifts
from backend.core.fanxiu.data_annotation import kernel_scheduler_control as scheduler


@pytest.mark.parametrize(('change', 'error'), [
    ('recipient', '收礼人物'),
    ('process', '进程'),
    ('extra_gift', '唯一授权批次'),
])
def test_waiting_submit_rechecks_changed_selection_without_recording_action(monkeypatch, change, error):
    original = {'complete': True, 'npc_id': 4005, 'native_npc_id': 4005,
                'quantity_modal': None, 'captured_at': time.time(),
                'evidence': {'pid': 1, 'process_start_ticks': 2},
                'selected_items': [{'base_id': 37092001, 'quantity': 1}]}
    visible = deepcopy(original)
    events = []

    class Context:
        def wait_click(self, scene, shape, *, before_click=None):
            assert (scene, shape) == (759, '赠送')
            yield 'provider_wait'
            # A popup/recovery during the provider's wait changed the target.
            if change == 'recipient':
                visible['npc_id'] = visible['native_npc_id'] = 4013
            elif change == 'process':
                visible['evidence']['process_start_ticks'] = 3
            else:
                visible['selected_items'].append({'base_id': 19070153, 'quantity': 1})
            events.append('provider_ready')
            assert callable(before_click)
            before_click()
            events.append('consumed')

    context = Context()
    runner = SimpleNamespace(_behavior_tree_context=lambda *_args, **_kwargs: context)
    monkeypatch.setattr(gifts, 'read_xianyuan_gift_actions', lambda: [])
    monkeypatch.setattr(scheduler, 'read_scheduler_batch_admission', lambda **_: {'admitted': True})
    monkeypatch.setattr(scheduler, 'read_scheduler_batch_admission', lambda **_: {'admitted': True})
    monkeypatch.setattr(gifts, 'read_xianyuan_atlas_runtime', lambda **_: {
        'runtime_complete': True, 'people': [{'npc_id': 4005}],
        'runtime_debug': {'pid': 1, 'process_start_ticks': 2}})
    monkeypatch.setattr(gifts, 'read_backpack_item_counts', lambda *_args, **_kwargs: ({37092001: 2}, {}))
    monkeypatch.setattr(gifts, 'plan_xianyuan_gift_batch', lambda *_: {
        'action': 'gift', 'npc_id': 4005, 'base_id': 37092001, 'quantity': 1})

    def prepare(*_args, **_kwargs):
        if False:
            yield
        return deepcopy(original)

    monkeypatch.setattr(gifts, 'open_selected_npc_gift_page', prepare)
    monkeypatch.setattr(gifts, 'select_single_npc_gift', prepare)
    monkeypatch.setattr(gifts, 'read_xianyuan_gift_page_snapshot', lambda: deepcopy(visible))
    monkeypatch.setattr(gifts, 'begin_xianyuan_gift_action', lambda *_args, **_kwargs: events.append('pending'))
    plan = {'items': [{'base_id': 37092001, 'eligible_targets': [{'npc_id': 4005, 'name': '炎帝萧炎'}]}]}
    task = gifts.execute_xianyuan_selected_gift_adapter(runner, {}, {}, None, plan)
    assert next(task) == 'provider_wait'
    with pytest.raises(RuntimeError, match=error):
        next(task)
    assert events == ['provider_ready']
