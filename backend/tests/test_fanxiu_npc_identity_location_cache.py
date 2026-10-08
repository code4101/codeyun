"""Structural identity caching must never retain favor or reward observations."""
from types import SimpleNamespace

import pytest

from backend.core.fanxiu.instrumentation import xianyuan_atlas as atlas


@pytest.fixture
def native(monkeypatch):
    state = SimpleNamespace(
        memory=SimpleNamespace(device_scope_id='device-a', pid=1, process_start_ticks=2),
        root=100, order=[101, 102, 103], identity_reads=[], full_reads=[],
        tables={101: {'id': 7, 'isOpen': 1},
                102: {'id': 4005, 'isOpen': 1, 'favor': 12000, 'resetFavor': 0, 'reward_state': 0},
                103: {'id': 4013, 'isOpen': 0, 'favor': 12000, 'resetFavor': 0, 'reward_state': 0}},
    )

    class Reader:
        def fields(self, ref):
            if ref.address == state.root:
                return {'_NpcInfoList': [atlas.LuaRef('table', a) for a in state.order]}
            state.full_reads.append(ref.address)
            return dict(state.tables[ref.address])

        def string_fields(self, address, names):
            state.identity_reads.append(address)
            return {k: state.tables[address][k] for k in names}

        def list_items(self, values):
            return values, len(values)

    monkeypatch.setattr(atlas, '_NPC_IDENTITY_CACHE', None)
    monkeypatch.setattr(atlas.MumuProcessMemory, 'discover_cached', lambda **kw: state.memory)
    monkeypatch.setattr(atlas, 'LuaJitReader', lambda m: Reader())
    monkeypatch.setattr(atlas, '_data_root', lambda m: (state.root, True))
    monkeypatch.setattr(atlas, '_name_index', lambda *a, **kw: {})
    monkeypatch.setattr(atlas, '_npc_gift_options', lambda n: {'can_send_config': True})
    monkeypatch.setattr(atlas, '_npc_favor_thresholds', lambda: {})
    monkeypatch.setattr(atlas, '_rewards', lambda reader, fields: [{
        'level': 1, 'reward_key': 1, 'kind': '物资', 'state': fields.get('reward_state', 0)}])
    return state


def test_cached_identity_reads_new_favor_rewards_and_openness(native):
    first = atlas.read_xianyuan_atlas_runtime(npc_ids=[4005])
    assert first['runtime_debug']['npc_identity_cache_hit'] is False
    native.identity_reads.clear()
    native.tables[102].update(resetFavor=12000, reward_state=2)
    current = atlas.read_xianyuan_atlas_runtime(npc_ids=[4005])
    assert current['runtime_debug']['npc_identity_cache_hit'] is True
    assert native.identity_reads == []
    assert current['people'][0]['reset_favor'] == 12000
    assert current['people'][0]['rewards'][0]['state'] == 2
    assert first['people'][0]['reset_favor'] == 0
    native.tables[102]['isOpen'] = 0
    assert atlas.read_xianyuan_atlas_runtime(npc_ids=[4005])['missing_npc_ids'] == [4005]
    native.tables[103]['isOpen'] = 1
    assert atlas.read_xianyuan_atlas_runtime(npc_ids=[4013])['runtime_complete'] is True


@pytest.mark.parametrize('change', ['device', 'pid', 'start', 'root', 'order'])
def test_identity_cache_invalidates_on_each_native_scope_boundary(native, change):
    atlas.read_xianyuan_atlas_runtime(npc_ids=[4005])
    native.identity_reads.clear()
    if change == 'device':
        native.memory.device_scope_id = 'device-b'
    elif change == 'pid':
        native.memory.pid = 2
    elif change == 'start':
        native.memory.process_start_ticks = 3
    elif change == 'root':
        native.root = 200
    else:
        native.order = [102, 101, 103]
    result = atlas.read_xianyuan_atlas_runtime(npc_ids=[4005])
    assert result['runtime_debug']['npc_identity_cache_hit'] is False
    assert len(native.identity_reads) == 3
    assert result['people'][0]['runtime_index'] == (0 if change == 'order' else 1)


def test_changed_cached_target_rebuilds_mapping_instead_of_substitution(native):
    atlas.read_xianyuan_atlas_runtime(npc_ids=[4005])
    native.tables[102]['id'] = 8
    native.tables[101]['id'] = 4005
    result = atlas.read_xianyuan_atlas_runtime(npc_ids=[4005])
    assert result['runtime_debug']['npc_identity_cache_hit'] is False
    assert result['people'][0]['runtime_index'] == 0


def test_missing_identity_is_rediscovered_and_duplicates_remain_visible(native):
    atlas.read_xianyuan_atlas_runtime(npc_ids=[4005])
    assert atlas.read_xianyuan_atlas_runtime(npc_ids=[999])['runtime_complete'] is False
    native.tables[101]['id'] = 999
    assert atlas.read_xianyuan_atlas_runtime(npc_ids=[999])['people'][0]['runtime_index'] == 0
    native.tables[102]['id'] = 999
    native.root = 200
    result = atlas.read_xianyuan_atlas_runtime(npc_ids=[999])
    assert len(result['people']) == 2
    assert len(atlas.read_xianyuan_atlas_runtime(npc_ids=[999])['people']) == 2
