"""Exercise the real adapter: a claim handoff must retain a consumed gift."""
from types import SimpleNamespace

from backend.core.fanxiu.data_annotation.tasks import xianyuan_gift_gui as gifts
from backend.core.fanxiu.data_annotation.tasks import theme_npc_gift as rewards
from backend.core.fanxiu.data_annotation import kernel_scheduler_control as scheduler


def test_entry_admission_handoff_preserves_selected_scope(monkeypatch):
    admission = {'admitted': False}
    exits = []

    def leave(context):
        exits.append(34)
        yield from ()

    monkeypatch.setattr(gifts, 'read_xianyuan_gift_actions', lambda: [])
    monkeypatch.setattr(scheduler, 'read_scheduler_batch_admission', lambda **kwargs: admission)
    monkeypatch.setattr(gifts, 'leave_selected_npc_without_gifting', leave)
    runner = SimpleNamespace(_behavior_tree_context=lambda *args, **kwargs: object())
    plan = {'items': [
        {'base_id': 37092001, 'quantity': 914, 'eligible_targets': [{'npc_id': 4005}]},
        {'base_id': 37130904, 'quantity': 300, 'eligible_targets': [{'npc_id': 4013}]},
    ]}
    generator = gifts.execute_xianyuan_selected_gift_adapter(runner, {}, {}, None, plan)
    while True:
        try:
            next(generator)
        except StopIteration as done:
            result = done.value
            break
    assert result['actions'] == []
    assert result['remaining_items'] == [
        {'base_id': 37092001, 'quantity': 914}, {'base_id': 37130904, 'quantity': 300}]
    assert result['yield_for_due_job'] is admission
    assert exits == [34]


def test_consumed_gift_survives_claim_handoff(monkeypatch):
    clicks, exits = [], []
    admission = {'admitted': False, 'reason': 'due job'}
    receipt = {'action_id': 'original-action', 'status': 'verified'}
    person = {'npc_id': 4005, 'rewards': []}
    snapshot = {'runtime_complete': True, 'people': [person], 'runtime_debug': {'pid': 1}}
    stocks = iter([2, 1])

    def empty(*args, **kwargs):
        yield from ()

    def page(*args, **kwargs):
        yield from ()
        return {'complete': True, 'npc_id': 4005}

    def claim(*args, **kwargs):
        yield from ()
        raise gifts.XianyuanDueJobYield(admission)

    def leave(*args):
        exits.append(34)
        yield from ()

    class Context:
        def wait_click(self, scene, label, **kwargs):
            if kwargs.get('before_click'):
                kwargs['before_click']()
            clicks.append((scene, label))
            yield from ()

        def wait_scene(self, scenes, **kwargs):
            yield from ()
            return scenes[-1]

    context = Context()
    runner = SimpleNamespace(_behavior_tree_context=lambda *args, **kwargs: context)
    monkeypatch.setattr(gifts, 'read_xianyuan_gift_actions', lambda: [])
    monkeypatch.setattr(scheduler, 'read_scheduler_batch_admission', lambda **kwargs: {'admitted': True})
    monkeypatch.setattr(gifts, 'read_xianyuan_atlas_runtime', lambda **kwargs: snapshot)
    monkeypatch.setattr(gifts, 'read_backpack_item_counts', lambda ids, **kwargs: ({ids[0]: next(stocks)}, {}))
    monkeypatch.setattr(gifts, 'plan_xianyuan_gift_batch', lambda item, person: {'action': 'gift'})
    monkeypatch.setattr(gifts, 'open_selected_npc_gift_page', page)
    monkeypatch.setattr(gifts, 'select_single_npc_gift', empty)
    monkeypatch.setattr(gifts, 'require_xianyuan_navigation_budget', lambda **kwargs: None)
    monkeypatch.setattr(gifts, 'read_xianyuan_gift_page_snapshot', lambda: {})
    monkeypatch.setattr(gifts, 'require_xianyuan_gift_selection', lambda *args, **kwargs: None)
    monkeypatch.setattr(gifts, 'begin_xianyuan_gift_action', lambda *args, **kwargs: {'action_id': 'original-action'})
    monkeypatch.setattr(gifts, 'settle_xianyuan_gift_action', lambda *args, **kwargs: receipt)
    monkeypatch.setattr(rewards, 'claim_npc_return_gifts', claim)
    monkeypatch.setattr(gifts, 'leave_selected_npc_without_gifting', leave)
    plan = {'items': [
        {'base_id': 37092001, 'quantity': 2, 'eligible_targets': [{'npc_id': 4005, 'name': '炎帝'}]},
        {'base_id': 37130904, 'quantity': 3, 'eligible_targets': [{'npc_id': 4013, 'name': '雅妃'}]},
    ]}
    generator = gifts.execute_xianyuan_selected_gift_adapter(runner, {}, {}, None, plan)
    while True:
        try:
            next(generator)
        except StopIteration as done:
            result = done.value
            break
    assert clicks.count((759, '赠送')) == 1
    assert result['actions'] == [receipt]
    assert result['remaining_items'] == [
        {'base_id': 37092001, 'quantity': 1}, {'base_id': 37130904, 'quantity': 3}]
    assert result['outcome'] == 'partial'
    assert result['yield_for_due_job'] is admission
    assert exits == [34]
