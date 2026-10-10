"""Pure policy and real durable ledger contracts; no game simulation."""
from concurrent.futures import ThreadPoolExecutor
import json
import subprocess
import sys
import time

import pytest
from backend.core.fanxiu import storage_bag_unknown_research as research
from backend.core.fanxiu.data_annotation import ai_assistance
from backend.core.codex import CodexDispatch


def inputs():
    atlas = {'items': [{'base_id': 1, 'auto_claim': False, 'operation_template': 'unsupported',
                        'item': {'name': 'unresolved item', 'description': 'configuration v1'}},
                       {'base_id': 2, 'operation_template': 'unsupported', 'item': {'name': 'historic'}}]}
    snapshot = {'complete': True, 'source': 'active_backpack_panel_item_info_list',
                'captured_at_epoch': 1000, 'items': [{'base_id': 1, 'num': 3}]}
    return atlas, snapshot


def discover(path):
    atlas, snapshot = inputs()
    return research.discover_storage_bag_unknown_items(atlas, snapshot, rules={}, path=path, now=1000)[0]


def test_inventory_observation_retains_provider_evidence_identity(tmp_path):
    atlas, snapshot = inputs()
    snapshot['evidence'] = {'pid': 321, 'process_start_ticks': 7654, 'read_only': True}
    path = tmp_path / 'ledger.json'
    research.discover_storage_bag_unknown_items(atlas, snapshot, rules={}, path=path, now=1000)
    status = research.read_storage_bag_unknown_research_status(path=path)
    observation = status['last_discovery']['inventory_observation']
    assert (observation['pid'], observation['process_start_ticks']) == (321, 7654)
    assert status['inventory_process_identity_bound'] is True
    changed = {**snapshot, 'evidence': {'pid': 322, 'process_start_ticks': 7655}}
    assert research._inventory_observation(changed) != observation
    assert research._inventory_observation(changed)['inventory_fingerprint'] == observation['inventory_fingerprint']


def test_inventory_identity_missing_legacy_and_conflict_are_explicit(tmp_path):
    _, snapshot = inputs()
    assert research._inventory_observation(snapshot)['pid'] is None
    assert not research.read_storage_bag_unknown_research_status(path=tmp_path / 'empty.json')['inventory_process_identity_bound']
    legacy = {**snapshot, 'pid': 321, 'process_start_ticks': 7654}
    assert research._inventory_observation(legacy)['pid'] == 321
    with pytest.raises(ValueError, match='Conflicting inventory process identity'):
        research._inventory_observation({**legacy, 'evidence': {'pid': 322}})


def test_new_catalog_material_uses_existing_purpose_rule_without_ai_or_consumption(tmp_path):
    path = tmp_path / 'ledger.json'
    atlas, snapshot = inputs()
    atlas['items'][0]['item'] = {'id': 1, 'name': '诛首秘法残页', 'type': 5,
                               'sub_type': 7, 'description': '当集齐80页后可将其合为秘传诛首秘法！'}
    signature = research.discover_storage_bag_unknown_items(atlas, snapshot, rules={}, path=path, now=1000)[0]
    state = research.read_storage_bag_unknown_research(path=path)
    item = state['items'][signature]
    assert item['status'] == 'resolved' and item['purpose_status'] == 'reserve'
    assert item['catalog_route'] == 'collection_conversion_material'
    assert item['classification_complete'] is False and item['operation_supported'] is False
    assert atlas['items'][0]['auto_claim'] is False
    assert research.plan_storage_bag_unknown_research_sync(state, [signature])[signature]['action'] == 'skip'
    atlas['items'][0]['item']['description'] = '用途资料尚未明确'
    unresolved = research.discover_storage_bag_unknown_items(atlas, snapshot, rules={}, path=path, now=1000)[0]
    assert research.read_storage_bag_unknown_research(path=path)['items'][unresolved]['status'] == 'discovered'


def test_scope_and_stable_identity_exclude_quantity_time_selection(tmp_path):
    path = tmp_path / 'ledger.json'
    signature = discover(path)
    atlas, snapshot = inputs()
    snapshot['items'][0]['num'] = 90
    snapshot['captured_at_epoch'] = 1010
    assert research.discover_storage_bag_unknown_items(atlas, snapshot, rules={}, path=path, now=1010) == [signature]
    state = research.read_storage_bag_unknown_research(path=path)
    assert len(state['items']) == 1
    assert state['items'][signature]['base_id'] == 1
    assert atlas['items'][0]['auto_claim'] is False
    atlas['items'][0]['item']['description'] = 'configuration v2'
    assert research.discover_storage_bag_unknown_items(atlas, snapshot, rules={}, path=path, now=1010) != [signature]


@pytest.mark.parametrize('patch', [{'complete': False}, {'captured_at_epoch': 700},
                                    {'source': 'historical_atlas'}, {'captured_at_epoch': 1001}])
def test_nonfresh_or_incomplete_rejected(tmp_path, patch):
    atlas, snapshot = inputs()
    with pytest.raises(ValueError):
        research.discover_storage_bag_unknown_items(atlas, {**snapshot, **patch}, rules={},
                                                   path=tmp_path / 'ledger.json', now=1000)


def test_concurrent_launch_suppression_retry_and_restart(tmp_path, monkeypatch):
    path = tmp_path / 'ledger.json'
    signature = discover(path)
    calls = []
    def suppressed(request, **kwargs):
        calls.append(kwargs['mode'])
        return None
    monkeypatch.setattr(ai_assistance, 'request_ai_assistance', suppressed)
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(lambda _: research.dispatch_storage_bag_unknown_research(signature, path=path, now=1000), range(6)))
    assert calls == ['read_only_research']
    code = ('from backend.core.fanxiu.storage_bag_unknown_research import read_storage_bag_unknown_research; '
            'import json,sys; print(json.dumps(read_storage_bag_unknown_research(path=sys.argv[1])))')
    restarted = subprocess.run([sys.executable, '-c', code, str(path)], check=True, capture_output=True, text=True)
    assert json.loads(restarted.stdout)['items'][signature]['status'] == 'suppressed'
    monkeypatch.setattr(ai_assistance, 'request_ai_assistance', lambda *a, **k:
                        CodexDispatch('dispatch1', 1, '', '', '', '', '', 'now'))
    assert research.dispatch_storage_bag_unknown_research(signature, path=path, now=1061)['status'] == 'dispatched'


def test_owner_rule_roundtrip_does_not_enable_consumption(tmp_path):
    path = tmp_path / 'ledger.json'
    atlas, snapshot = inputs()
    atlas['items'][0]['operation_template'] = 'random_box'
    signature = research.discover_storage_bag_unknown_items(atlas, snapshot,
        rules={'1': {'classification_complete': False}}, path=path, now=1000)[0]
    research.record_storage_bag_research_conclusion(signature, evidence='unresolved usage evidence',
        purpose='await_owner', classification_complete=False, path=path)
    projected = research.project_storage_bag_research_policy(atlas, current_signatures=[signature], path=path)
    assert projected['items'][0]['research_hold'] == 'await_owner'
    research.confirm_storage_bag_item_purpose(signature, purpose='use_existing_policy', owner_evidence='owner seq123', path=path)
    # Late AI result may not undo the owner's purpose or require a complete reward pool.
    result = research.record_storage_bag_research_conclusion(signature, evidence='late result', purpose='await_owner',
                                                            classification_complete=False, path=path)
    assert result['status'] == 'resolved' and result['classification_complete'] is False
    projected = research.project_storage_bag_research_policy(atlas, current_signatures=[signature], path=path)
    assert 'research_hold' not in projected['items'][0]
    assert projected['items'][0]['auto_claim'] is False


@pytest.mark.parametrize('purpose,catalog_status,expected', [
    ('reserve', 'await_owner', 'resolved'),
    ('independent_lifecycle', 'await_owner', 'resolved'),
    ('use_existing_policy', 'reserve', 'discovered'),
])
def test_owner_purpose_wins_rediscovery_without_hiding_unsupported_operation(
        tmp_path, purpose, catalog_status, expected):
    from backend.core.fanxiu.storage_bag_catalog_purpose import storage_bag_catalog_purpose_fingerprint
    from backend.core.fanxiu.data_annotation.state import write_data_annotation_json
    path = tmp_path / 'ledger.json'
    atlas, snapshot = inputs()
    card = atlas['items'][0]['item']
    card['id'] = 1
    fingerprint = storage_bag_catalog_purpose_fingerprint(card)
    write_data_annotation_json(path, {'version': 1, 'items': {},
        'catalog_findings': {fingerprint: {'purpose_status': catalog_status,
            'evidence': 'catalog suggestion', 'route': 'ordinary_box_candidate',
            'fingerprint': fingerprint}}})
    research.configure_storage_bag_owner_purpose(base_id=1, purpose=purpose,
        owner_evidence='explicit owner decision', path=path)
    signature = research.discover_storage_bag_unknown_items(
        atlas, snapshot, rules={}, path=path, now=1000)[0]
    item = research.read_storage_bag_unknown_research(path=path)['items'][signature]
    assert item['purpose_status'] == purpose and item['status'] == expected
    plan = research.plan_storage_bag_unknown_research_sync(
        research.read_storage_bag_unknown_research(path=path), [signature])
    assert plan[signature]['action'] == ('dispatch_research' if expected == 'discovered' else 'skip')
    projected = research.project_storage_bag_research_policy(
        atlas, current_signatures=[signature], path=path)['items'][0]
    assert projected['operation_template'] == 'unsupported'
    assert projected['auto_claim'] is False


def test_unknown_is_held_immediately_and_classification_is_projected(tmp_path):
    from backend.core.fanxiu.data_annotation.tasks.storage_bag_auto_claim_policy import decide_storage_bag_auto_claim_item
    path = tmp_path / 'ledger.json'
    signature = discover(path)
    atlas, _ = inputs()
    atlas['items'][0].update(auto_claim=True, num=3)
    projected = research.project_storage_bag_research_policy(atlas, current_signatures=[signature], path=path)
    assert decide_storage_bag_auto_claim_item(projected['items'][0]).action == 'defer'
    research.record_storage_bag_research_conclusion(signature, evidence='authoritative structure', purpose='await_owner',
        classification_complete=True, classification={'operation_template': 'fixed_box', 'yield_mode': 'fixed'}, path=path)
    research.confirm_storage_bag_item_purpose(signature, purpose='use_existing_policy', owner_evidence='owner confirmed usage', path=path)
    projected = research.project_storage_bag_research_policy(atlas, current_signatures=[signature], path=path)
    assert projected['items'][0]['operation_template'] == 'fixed_box'
    assert decide_storage_bag_auto_claim_item(projected['items'][0]).action == 'execute'
    assert research.project_storage_bag_research_policy(atlas, current_signatures=[], path=path)['items'][0]['operation_template'] == 'unsupported'


def test_owner_confirmed_box_does_not_wait_for_full_pool_or_redispatch(tmp_path, monkeypatch):
    path = tmp_path / 'ledger.json'
    research.configure_storage_bag_owner_purpose(base_id=1, purpose='use_existing_policy',
                                               owner_evidence='explicit owner non-prayer all-open', path=path)
    atlas, snapshot = inputs()
    atlas['items'][0]['operation_template'] = 'random_box'
    signature = research.discover_storage_bag_unknown_items(atlas, snapshot,
        rules={'1': {'classification_complete': False}}, path=path, now=1000)[0]
    assert research.read_storage_bag_unknown_research(path=path)['items'][signature]['status'] == 'resolved'
    monkeypatch.setattr(ai_assistance, 'request_ai_assistance', lambda *a, **k: pytest.fail('must not dispatch'))
    assert research.dispatch_storage_bag_unknown_research(signature, path=path, now=1000)['status'] == 'resolved'
    assert 'research_hold' not in research.project_storage_bag_research_policy(atlas,
        current_signatures=[signature], path=path)['items'][0]


def test_metadata_only_box_is_held_without_a_persisted_reward_rule(tmp_path):
    path = tmp_path / 'ledger.json'
    atlas, snapshot = inputs()
    atlas['items'][0]['operation_template'] = 'random_box'
    atlas['items'][0]['item'].update(type=2, metadata_only=True)
    signature = research.discover_storage_bag_unknown_items(atlas, snapshot, rules={}, path=path, now=1000)[0]
    assert research.read_storage_bag_unknown_research(path=path)['items'][signature]['classification_complete'] is False
    projected = research.project_storage_bag_research_policy(atlas, current_signatures=[signature], path=path)
    assert projected['items'][0]['research_hold'] == 'unknown'


def test_confirmation_outbox_is_durable_and_deduplicated(tmp_path):
    from backend.core.messaging.wechat_agent_store import AgentStore
    path = tmp_path / 'ledger.json'
    signature = discover(path)
    research.record_storage_bag_research_conclusion(signature, evidence='purpose not established',
        purpose='await_owner', classification_complete=False, path=path)
    store = AgentStore(tmp_path / 'outbox.sqlite')
    store.ensure_hook('group', 'account', 'chat')
    store.subscribe_codex_thread('thread', 'group', [])
    research.configure_storage_bag_research_confirmation(thread_id='thread', path=path, store=store)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: research.queue_storage_bag_purpose_confirmation(signature,
                  thread_id='thread', path=path, store=store), range(4)))
    restarted = AgentStore(tmp_path / 'outbox.sqlite')
    notifications = restarted.codex_notifications_status()['notifications']
    assert len(notifications) == 1
    assert notifications[0]['status'] == 'pending'
    assert research.read_storage_bag_unknown_research(path=path)['confirmation_thread_id'] == 'thread'


def _awaiting_owner(path):
    signature = discover(path)
    research.record_storage_bag_research_conclusion(signature, evidence='purpose unknown',
        purpose='await_owner', classification_complete=False, path=path)
    return signature


def test_sync_reports_unconfigured_confirmation_route_without_invalid_dispatch(tmp_path, monkeypatch):
    from backend.core.jobs import executor
    path = tmp_path / 'ledger.json'
    signature = _awaiting_owner(path)

    class _EmptyResult:
        def all(self):
            return []

    class _Session:
        def exec(self, statement):
            return _EmptyResult()

    enqueued = []
    monkeypatch.setattr(executor.background_task_queue, 'enqueue_once',
                        lambda *a, **k: (enqueued.append(a) or ('task', True)))

    atlas, snapshot = inputs()
    snapshot['captured_at_epoch'] = time.time()
    report = research.sync_storage_bag_unknown_research(atlas, snapshot, session=_Session(), path=path)

    assert enqueued == []
    assert report == [signature]  # Legacy list contract preserved.
    detail = report.diagnostics[signature]
    assert detail['action'] == 'awaiting_confirmation_route'
    assert detail['reason'] == research.WAITING_CONFIRMATION_ROUTE
    assert detail['resolve_by'] == research.CONFIGURE_CONFIRMATION_ROUTE
    assert report.waiting == {signature: detail}
    item = research.read_storage_bag_unknown_research(path=path)['items'][signature]
    assert item['status'] == 'awaiting_owner'
    assert item['waiting_reason'] == research.WAITING_CONFIRMATION_ROUTE
    audit = research.read_storage_bag_unknown_research_status(path=path)
    assert audit['latest_sync_evidence_complete'] is True
    assert audit['last_discovery']['inventory_observation']['captured_at_epoch'] == snapshot['captured_at_epoch']
    assert audit['last_sync']['diagnostics'][signature] == detail
    assert audit['formal_policy_plan_acceptance'] == 'requires_formal_job_receipt'


def test_formal_discovery_receipt_exists_even_without_unknown_items(tmp_path):
    path = tmp_path / 'ledger.json'
    atlas, snapshot = inputs()
    assert research.discover_storage_bag_unknown_items({'items': []}, snapshot,
        rules={}, path=path, now=1000) == []
    status = research.read_storage_bag_unknown_research_status(path=path)
    assert status['last_discovery']['inventory_observation']['current_type_count'] == 1
    assert status['last_discovery']['signatures'] == []
    assert status['latest_sync_evidence_complete'] is False


def test_legacy_ledger_and_newer_discovery_do_not_claim_old_sync_verified(tmp_path):
    path = tmp_path / 'ledger.json'
    discover(path)
    assert research.read_storage_bag_unknown_research_status(path=path)['latest_sync_evidence_complete'] is False
    state = research.read_storage_bag_unknown_research(path=path)
    state['last_sync'] = dict(state['last_discovery'])
    from backend.core.fanxiu.data_annotation.state import write_data_annotation_json
    write_data_annotation_json(path, state)
    assert research.read_storage_bag_unknown_research_status(path=path)['latest_sync_evidence_complete'] is False
    atlas, snapshot = inputs()
    snapshot['captured_at_epoch'] = 1010
    research.discover_storage_bag_unknown_items(atlas, snapshot, rules={}, path=path, now=1010)
    assert research.read_storage_bag_unknown_research_status(path=path)['latest_sync_evidence_complete'] is False


@pytest.mark.parametrize('change', ['quantity', 'pid', 'process_start_ticks'])
def test_same_epoch_concurrent_discovery_cannot_rebind_sync_receipt(tmp_path, monkeypatch, change):
    from copy import deepcopy
    from sqlmodel import Session, create_engine
    from backend.models import FanxiuPacketBusinessRecord
    from backend.core.fanxiu.data_annotation.state import write_data_annotation_json
    path = tmp_path / 'ledger.json'
    atlas, snapshot = inputs()
    snapshot.update(captured_at_epoch=time.time(), pid=10, process_start_ticks=20)
    signature = research.discover_storage_bag_unknown_items(atlas, snapshot, rules={}, path=path)[0]
    research.record_storage_bag_research_conclusion(signature, evidence='purpose unknown',
        purpose='await_owner', classification_complete=False, path=path)
    concurrent = deepcopy(snapshot)
    if change == 'quantity':
        concurrent['items'][0]['num'] += 1
    else:
        concurrent[change] += 1
    original = research._apply_storage_bag_waiting_reasons
    def interleave(signatures, *, path=None):
        original(signatures, path=path)
        assert research.discover_storage_bag_unknown_items(atlas, concurrent, rules={}, path=path) == [signature]
    monkeypatch.setattr(research, '_apply_storage_bag_waiting_reasons', interleave)
    engine = create_engine('sqlite:///' + str(tmp_path / 'rules.sqlite'))
    FanxiuPacketBusinessRecord.__table__.create(engine)
    try:
        with Session(engine) as session:
            research.sync_storage_bag_unknown_research(atlas, snapshot, session=session, path=path)
    finally:
        engine.dispose()
    status = research.read_storage_bag_unknown_research_status(path=path)
    assert status['last_sync'] is None and not status['latest_sync_evidence_complete']
    # Even an exact manual copy of discovery is not a sync disposition receipt.
    state = research.read_storage_bag_unknown_research(path=path)
    state['last_sync'] = {**state['last_discovery'], 'record_kind': 'formal_unknown_sync', 'diagnostics': {}}
    write_data_annotation_json(path, state)
    assert not research.read_storage_bag_unknown_research_status(path=path)['latest_sync_evidence_complete']


def test_sync_plan_queues_confirmation_once_route_is_bound(tmp_path):
    path = tmp_path / 'ledger.json'
    signature = _awaiting_owner(path)
    state = research.read_storage_bag_unknown_research(path=path)
    state['confirmation_thread_id'] = 'thread'
    plan = research.plan_storage_bag_unknown_research_sync(state, [signature])
    assert plan[signature] == {'action': 'queue_confirmation', 'reason': 'awaiting_owner'}


def test_waiting_reason_reconciles_current_state_not_stale_snapshot(tmp_path):
    path = tmp_path / 'ledger.json'
    signature = _awaiting_owner(path)
    # A stale sync snapshot planned a wait while the route was still unbound.
    research._apply_storage_bag_waiting_reasons([signature], path=path)
    assert research.read_storage_bag_unknown_research(path=path)['items'][signature]['waiting_reason'] == research.WAITING_CONFIRMATION_ROUTE
    # Concurrent binder binds the route before the waiting-reason transaction.
    with research._edit(path) as state:
        state['confirmation_thread_id'] = 'thread'
    research._apply_storage_bag_waiting_reasons([signature], path=path)
    assert 'waiting_reason' not in research.read_storage_bag_unknown_research(path=path)['items'][signature]
    # A concurrent resolve must also clear a stale wait, regardless of the route.
    with research._edit(path) as state:
        state.pop('confirmation_thread_id', None)
        state['items'][signature]['status'] = 'resolved'
    research._apply_storage_bag_waiting_reasons([signature], path=path)
    assert 'waiting_reason' not in research.read_storage_bag_unknown_research(path=path)['items'][signature]
