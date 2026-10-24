from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.core.baidu_netdisk import client as cm
from backend.core.baidu_netdisk.ledger import TransferLedger
from backend.core.baidu_netdisk.operations import OperationStore, OperationConflict
from backend.core.baidu_netdisk.download_batch import run_batch
from backend.core.baidu_netdisk.automation import NetdiskAutomation
from backend.core.baidu_netdisk.automation_config import AutomationConfig, AutomationPaths


def test_deferred_submit_never_walks_or_collects_and_repeat_never_clicks(tmp_path, monkeypatch):
    c = object.__new__(cm.NetdiskClient)
    c.ledger = TransferLedger(tmp_path / 'ledger.json')
    c.canonical_listing = None
    c.remote_tree = lambda *a: pytest.fail('must not walk subtree')
    c.collect_dir = lambda *a: pytest.fail('must not collect without inventory')
    screen = cm.Screen({'width': 1000, 'height': 800}, [])
    c.open_remote_dir = lambda *a: ('main', screen)
    c._find_in_list = lambda *a: (screen, {'name': 'pack'})
    monkeypatch.setattr(cm, 'list_rows', lambda s: [{'name': 'pack', 'kind': 'dir'}])
    c._select_for_download = lambda *a: ({'x': 10, 'y': 10, 'w': 10, 'h': 10}, screen)
    c.lease_check = lambda: None
    calls = []
    c.driver = SimpleNamespace(input=lambda *a, **k: calls.append(k))
    (tmp_path / 'dl').mkdir()
    out = c.download_dir('/pack', tmp_path / 'out', client_download_dir=tmp_path / 'dl', defer_inventory=True)
    assert out.status == 'submitted' and len(calls) == 1
    assert c.ledger.get(out.key)['facts']['expected_files'] is None
    assert c.download_dir('/pack', tmp_path / 'out', client_download_dir=tmp_path / 'dl', defer_inventory=True).status == 'submitted'
    assert len(calls) == 1


def submitted(tmp_path):
    ledger = TransferLedger(tmp_path / 'ledger.json')
    store = OperationStore(tmp_path / 'ops', ledger)
    request = {'kind': 'download_dir', 'remote_path': '/pack', 'resource_id': '123',
               'resource_version': None, 'local_dir': str(tmp_path / 'out'), 'expected_manifest': None}
    store.begin('op', request)
    native, _ = ledger.begin('download_dir', remote_path='/pack', local_dir=str(tmp_path / 'out'))
    ledger.update(native['key'], 'submitted', inventory_deferred=True, expected_files=None)
    store.record('op', {'status': 'submitted', 'key': native['key']})
    return store, ledger, native['key']


def test_binding_is_one_way_idempotent_and_preserves_original_identity(tmp_path):
    store, ledger, key = submitted(tmp_path)
    manifest = {'resource_id': '123', 'resource_version': None, 'files': [{'rel': 'a', 'size': 3}]}
    opts = dict(remote_path='/pack', resource_id='123')
    op = store.bind_download_manifest('op', manifest, **opts)
    assert op['initial_request']['expected_manifest'] is None
    assert ledger.get(key)['facts']['expected_files'] == {'a': '3'}
    assert store.bind_download_manifest('op', manifest, **opts) == op
    with pytest.raises(OperationConflict):
        store.bind_download_manifest('op', {**manifest, 'files': [{'rel': 'a', 'size': 4}]}, **opts)
    with pytest.raises(OperationConflict):
        store.bind_download_manifest('op', manifest, remote_path='/other', resource_id='123')


def test_unknown_manifest_is_never_ready(tmp_path):
    store, _, _ = submitted(tmp_path)
    assert store.reconcile('op')['status'] != 'ready'


def job(index):
    return {'material_id': str(index), 'favorite_order': index, 'material_cloud_path': f'/p{index}',
            'inventory_complete': False, 'resource_total_bytes': None,
            'request': {'operation_id': f'op{index}', 'remote_path': f'/p{index}', 'expected_manifest': None}}


def test_queue_first_preserves_favorite_order_and_unknown_reservations(tmp_path):
    calls = []
    service = SimpleNamespace(config=SimpleNamespace(local_root=tmp_path),
        enqueue_dir=lambda **kw: (calls.append(kw['operation_id']) or {'status': 'submitted'}))
    result = run_batch(service, [job(2), job(1)], tmp_path / 'batch', limit=50, max_queued=50,
                       queue_first=True, disk_guard=lambda: True, free_bytes=lambda: 10**15)
    assert calls == ['op1', 'op2']
    assert [j['resource_total_bytes'] for j in result['inflight_jobs']] == [None, None]


def test_guard_failure_prevents_any_submit(tmp_path):
    service = SimpleNamespace(config=SimpleNamespace(local_root=tmp_path),
        enqueue_dir=lambda **kw: pytest.fail('guard must prevent submission'))
    out = run_batch(service, [job(1)], tmp_path / 'batch', queue_first=True,
                    disk_guard=lambda: False, free_bytes=lambda: 10**15)
    assert out['status'] == 'paused_disk_budget' and not out['receipts']
    with pytest.raises(ValueError, match='live disk guard'):
        run_batch(service, [job(1)], tmp_path / 'other', queue_first=True)


def test_pause_refuses_foreign_or_partial_material_identity():
    c = object.__new__(cm.NetdiskClient)
    c.observe_transfers = lambda: pytest.fail('invalid input cannot touch GUI')
    with pytest.raises(ValueError):
        c.pause_download_roots(['foreign'])
    with pytest.raises(ValueError):
        c.pause_download_roots(['sd-123'])


def test_pause_never_clicks_unproven_transfer_row():
    c = object.__new__(cm.NetdiskClient)
    screen = cm.Screen({'width': 1000, 'height': 800}, [
        {'text': '下载中(1)', 'x': 100, 'y': 80, 'w': 100, 'h': 20},
        {'text': 'foreign_sd-ffffffffffff', 'x': 100, 'y': 300, 'w': 300, 'h': 20}])
    c.observe_transfers = lambda: ('main', screen)
    calls = []
    c.click_box = lambda s, main, box, **kw: (calls.append((box['text'], kw)) or screen)
    out = c.pause_download_roots(['pack_sd-123456789abc'])
    assert out['paused'] == [] and out['unproven'] == ['sd-123456789abc']
    assert calls == [('下载中(1)', {})]


def test_public_enqueue_repeated_identity_never_enters_native_twice(tmp_path, monkeypatch):
    config = AutomationConfig(profile='main', scene_ids=['home'], client_download_dir=tmp_path / 'dl',
                              local_root=tmp_path / 'out', paths=AutomationPaths(tmp_path / 'auto'))
    service = NetdiskAutomation(config)
    calls = []
    monkeypatch.setattr(service, '_execute_download_dir', lambda *a: (calls.append(a) or {'status': 'submitted'}))
    monkeypatch.setattr(service.operations, 'reconcile', lambda oid: {'status': 'needs_manifest',
        'next': 'supply_trusted_expected_manifest', 'recoverable': False})
    args = dict(remote_path='/pack', resource_id='123', destination='data')
    service.enqueue_dir('op', **args)
    service.enqueue_dir('op', **args)
    assert len(calls) == 1 and service.inspect('op')['request']['expected_manifest'] is None


def test_public_pause_refuses_unsubmitted_intent_before_gui(tmp_path, monkeypatch):
    config = AutomationConfig(profile='main', scene_ids=['home'], client_download_dir=tmp_path / 'dl',
                              local_root=tmp_path / 'out', paths=AutomationPaths(tmp_path / 'auto'))
    service = NetdiskAutomation(config)
    monkeypatch.setattr(service, 'native', lambda *a, **k: pytest.fail('unsubmitted op cannot pause'))
    from backend.core.baidu_netdisk.automation import AutomationFailure
    with pytest.raises(AutomationFailure) as err:
        service.pause_download_operations(['unknown'])
    assert err.value.code == 'pause_operation_not_proven'


def test_complete_listing_clears_previous_download_selection_before_count(monkeypatch):
    c = object.__new__(cm.NetdiskClient)
    c.max_scrolls = 0
    selected, cleared = object(), object()
    monkeypatch.setattr(cm, 'selection_evidence', lambda s: ['selected'] if s is selected else [])
    monkeypatch.setattr(cm, 'loaded_count', lambda s: 2 if s is cleared else None)
    monkeypatch.setattr(cm, 'list_rows', lambda s: [
        {'name': n, 'kind': 'dir', 'line': {'y': i * 20, 'h': 10}} for i, n in enumerate(['a', 'b'])])
    calls = []
    c._clear_selection = lambda main, s: (calls.append(s) or cleared)
    screen, rows, complete = c._list_dir_entries('main', selected)
    assert calls == [selected] and screen is cleared and complete and len(rows) == 2


def test_incomplete_listing_still_refuses_without_positive_loaded_count(monkeypatch):
    c = object.__new__(cm.NetdiskClient)
    c.max_scrolls = 0
    monkeypatch.setattr(cm, 'selection_evidence', lambda s: [])
    monkeypatch.setattr(cm, 'loaded_count', lambda s: None)
    monkeypatch.setattr(cm, 'list_rows', lambda s: [])
    c._clear_selection = lambda *a: pytest.fail('empty selection must not click')
    assert c._list_dir_entries('main', object())[2] is False
