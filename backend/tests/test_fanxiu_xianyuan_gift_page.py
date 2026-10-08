from types import SimpleNamespace

import pytest

from backend.core.fanxiu.instrumentation import xianyuan_gift_page as module
from backend.core.fanxiu.instrumentation.runtime_memory import LuaRef, FanxiuRuntimeMemoryError


def fixture(monkeypatch, *, modal=True):
    objects = {
        1: {'_CurNpcId': 4005, 'npcInfoVo': LuaRef('table', 2),
            '_CurSelectItemList': LuaRef('table', 3), 'hobbyList': LuaRef('table', 4),
            '_CurWillAddValue': 0},
        2: {'id': 4005},
        5: {'baseId': 37092001, 'num': 1111},
        6: {'data': LuaRef('table', 5), 'useNum': 80, 'useMax': 80, 'perValue': 100},
    }
    lists = {3: ([], 0), 4: ([(1, LuaRef('table', 5))], 1)}
    windows = {'NpcGiftGivingView': (LuaRef('table', 1),),
               'NpcGiftSelectedNumView': (LuaRef('table', 6),) if modal else ()}
    ctx = SimpleNamespace(binding=SimpleNamespace(pid=1, process_start_ticks=2),
                          reader=SimpleNamespace(indexed_list_items=lambda ref: lists[ref.address]))
    monkeypatch.setattr(module, 'read_ui_runtime_snapshot', lambda keys, callback, **kw: callback(ctx))
    monkeypatch.setattr(module, 'read_active_ui_window_panels', lambda ctx, name: windows[name])
    monkeypatch.setattr(module, 'read_ui_object_field', lambda ctx, address, name: objects[address].get(name))
    return objects, lists, windows


def test_quantity_selection_does_not_claim_gift_submitted(monkeypatch):
    fixture(monkeypatch)
    result = module.read_xianyuan_gift_page_snapshot()
    assert result['npc_id'] == result['native_npc_id'] == 4005
    assert result['selected_items'] == []
    assert result['quantity_modal']['quantity'] == 80
    assert result['quantity_modal']['base_id'] == 37092001
    assert result['available_items'][0]['runtime_index'] == 1


def test_missing_list_cannot_be_treated_as_empty_selection(monkeypatch):
    _, lists, _ = fixture(monkeypatch)
    lists[3] = ([], None)
    with pytest.raises(FanxiuRuntimeMemoryError, match='列表不完整'):
        module.read_xianyuan_gift_page_snapshot()


def test_duplicate_panel_and_excess_quantity_fail_closed(monkeypatch):
    objects, _, windows = fixture(monkeypatch)
    objects[6]['useNum'] = 81
    with pytest.raises(FanxiuRuntimeMemoryError, match='超出'):
        module.read_xianyuan_gift_page_snapshot()
    objects[6]['useNum'] = 80
    windows['NpcGiftGivingView'] *= 2
    with pytest.raises(FanxiuRuntimeMemoryError, match='不唯一'):
        module.read_xianyuan_gift_page_snapshot()


def test_closed_page_is_not_completed_gift(monkeypatch):
    _, _, windows = fixture(monkeypatch)
    windows.update(NpcGiftGivingView=(), NpcGiftSelectedNumView=())
    assert module.read_xianyuan_gift_page_snapshot()['complete'] is False


def test_submit_guard_rejects_other_gifts_modal_and_stale_process():
    import time
    from copy import deepcopy
    batch = {'action': 'gift', 'npc_id': 4005, 'base_id': 37092001, 'quantity': 80}
    process = {'pid': 1, 'process_start_ticks': 2}
    snap = {'complete': True, 'quantity_modal': None, 'npc_id': 4005, 'native_npc_id': 4005,
            'captured_at': time.time(), 'evidence': process,
            'selected_items': [{'base_id': 37092001, 'quantity': 80}]}
    assert module.require_xianyuan_gift_selection(snap, batch, expected_process=process) is snap
    for alteration in [
        lambda x: x['selected_items'].append({'base_id': 7020013, 'quantity': 1}),
        lambda x: x.update(quantity_modal={'quantity': 80}),
        lambda x: x['evidence'].update(pid=2),
        lambda x: x.update(native_npc_id=4013),
        lambda x: x.update(captured_at=time.time()-6),
        lambda x: x['selected_items'][0].update(quantity=81),
    ]:
        changed = deepcopy(snap)
        alteration(changed)
        with pytest.raises(RuntimeError):
            module.require_xianyuan_gift_selection(changed, batch, expected_process=process)
