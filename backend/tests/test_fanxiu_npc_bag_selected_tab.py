"""NPC bags are selected detail tabs, not direct UIShowMgr components."""
from types import SimpleNamespace

import pytest

from backend.core.fanxiu.instrumentation import xianyuan_gift_page as page
from backend.core.fanxiu.instrumentation import ui_runtime_context as ui
from backend.core.fanxiu.instrumentation import resource_auto_use as config_reader
from backend.core.fanxiu.instrumentation.runtime_memory import LuaRef, FanxiuRuntimeMemoryError


def setup_bag(monkeypatch):
    objects = {
        1: {'npcInfoVo': LuaRef('table', 2)},
        2: {'id': 4005},
        3: {'ResetBtn': True, 'GiftBtn': True, 'npcInfoVo': LuaRef('table', 2),
            'rewardList': LuaRef('table', 4), 'resetStepList': LuaRef('table', 5),
            'resetOverList': LuaRef('table', 6), 'isEnough': True},
        99: {'package': LuaRef('table', 7)},
        7: {'loaded': LuaRef('table', 8)},
        8: {'Generate.Cfg.Npc.ConfigValue': LuaRef('table', 9)},
        9: {'NPC_GIFT_RESET_ITEM': LuaRef('table', 10)},
    }
    lists = {5: ([1], 1), 6: ([], 0)}
    state = {'hosts': [LuaRef('table', 1)], 'tab': (3, 1),
             'config': {'id': 'NPC_GIFT_RESET_ITEM', 'content': 'Item|30000001_0'}}
    ctx = SimpleNamespace(
        binding=SimpleNamespace(pid=21822, process_start_ticks=12589765,
                                environment_address=99, state_address=100),
        reader=SimpleNamespace(list_items=lambda ref: lists[ref.address]),
    )
    def hosts(context, name):
        assert name == 'NpcLookMainView'
        return state['hosts']
    monkeypatch.setattr(page, 'read_ui_runtime_snapshot', lambda keys, observe, **kw: observe(ctx))
    monkeypatch.setattr(page, 'read_active_ui_window_panels', hosts)
    monkeypatch.setattr(page, 'read_ui_object_field', lambda ctx, address, name: objects[address].get(name))
    monkeypatch.setattr(ui, 'read_ui_selected_tab_panel', lambda ctx, address: state['tab'])
    monkeypatch.setattr(ui, 'has_ui_object_fields', lambda ctx, address, fields: fields <= objects[address].keys())
    monkeypatch.setattr(config_reader, 'read_runtime_config_rows', lambda *args, **kwargs: [state['config']])
    return state, objects, lists


def test_reads_the_selected_second_tab_and_native_free_cost(monkeypatch):
    setup_bag(monkeypatch)
    result = page.read_xianyuan_gift_bag_snapshot()
    assert result['complete'] is True
    assert result['npc_id'] == 4005
    assert result['reset_steps'] == [1]
    assert result['remaining_reset_rewards'] == 0
    assert result['reset_cost_quantity'] == 0
    assert result['evidence'] == {'pid': 21822, 'process_start_ticks': 12589765}


@pytest.mark.parametrize('fault', ['duplicate_host', 'wrong_tab', 'wrong_person', 'partial_rewards', 'bad_cost'])
def test_incomplete_or_wrong_identity_cannot_prove_reset_eligibility(monkeypatch, fault):
    state, objects, lists = setup_bag(monkeypatch)
    if fault == 'duplicate_host':
        state['hosts'] *= 2
    elif fault == 'wrong_tab':
        state['tab'] = (3, 0)
    elif fault == 'wrong_person':
        objects[11] = {'id': 4013}
        objects[1]['npcInfoVo'] = LuaRef('table', 11)
    elif fault == 'partial_rewards':
        lists[6] = ([], 1)
    else:
        state['config']['content'] = 'unloaded'
    with pytest.raises(FanxiuRuntimeMemoryError):
        page.read_xianyuan_gift_bag_snapshot()
