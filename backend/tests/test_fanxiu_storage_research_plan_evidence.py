"""Pure inventory provenance and real TEMP receipt persistence, no game simulation."""
import copy
import json
from pathlib import Path

from backend.core.fanxiu import storage_bag_unknown_research as research
from backend.core.fanxiu.data_annotation.subtask_execution import job_log_context


def inputs():
    snapshot = dict(captured_at_epoch=1000, source='active_backpack_panel_item_info_list',
        complete=True, fingerprint='source-fingerprint', evidence=dict(pid=321, process_start_ticks=7654), items=[dict(base_id=1, num=3)])
    state = dict(version=1, items={})
    observation = research.project_storage_bag_research_plan_evidence(state, snapshot, {})['inventory_observation']
    state['last_discovery'] = dict(inventory_observation=observation, signatures=['catalog-identity'])
    state['last_sync'] = dict(record_kind='formal_unknown_sync', inventory_observation=copy.deepcopy(observation),
        signatures=['catalog-identity'], diagnostics={'catalog-identity': dict(action='skip', reason='resolved purpose')})
    return state, snapshot


def test_exact_inventory_source_bound_without_consumption_claim():
    state, snapshot = inputs()
    row = research.project_storage_bag_research_plan_evidence(state, snapshot, {'deferred': []})
    assert row['snapshot_source_binding_verified'] is True
    assert row['business_complete'] is False and row['consumption_performed'] is False


def test_same_inventory_foreign_plan_fingerprint_never_verifies_plan():
    state, snapshot = inputs()
    row = research.project_storage_bag_research_plan_evidence(state, snapshot,
        {'runtime_fingerprint': 'foreign-snapshot'})
    assert row['snapshot_source_binding_verified']
    assert not row['plan_runtime_fingerprint_matches_snapshot']
    assert not row['plan_source_binding_verified']
    row = research.project_storage_bag_research_plan_evidence(state, snapshot,
        {'runtime_fingerprint': snapshot['fingerprint']})
    assert row['plan_source_binding_verified']


def test_identical_counts_different_process_is_not_bound():
    state, snapshot = inputs()
    snapshot['evidence']['process_start_ticks'] += 1
    row = research.project_storage_bag_research_plan_evidence(state, snapshot, {})
    assert not row['plan_inventory_matches_sync'] and not row['snapshot_source_binding_verified']


def test_missing_legacy_identity_never_backfilled():
    state, snapshot = inputs()
    snapshot.pop('evidence')
    observation = research.project_storage_bag_research_plan_evidence(state, snapshot, {})['inventory_observation']
    for key in ('last_discovery', 'last_sync'):
        state[key]['inventory_observation'] = copy.deepcopy(observation)
    row = research.project_storage_bag_research_plan_evidence(state, snapshot, {})
    assert row['plan_inventory_matches_sync'] and not row['snapshot_source_binding_verified']
    assert row['inventory_observation']['pid'] is None


def test_new_snapshot_or_malformed_sync_diagnostics_cannot_cover_old_plan():
    state, snapshot = inputs()
    snapshot['captured_at_epoch'] += 1
    assert not research.project_storage_bag_research_plan_evidence(state, snapshot, {})['snapshot_source_binding_verified']
    snapshot['captured_at_epoch'] -= 1
    state['last_sync']['diagnostics'] = {}
    assert not research.project_storage_bag_research_plan_evidence(state, snapshot, {})['snapshot_source_binding_verified']


def test_formal_replans_append_under_exact_attempt_and_keep_history(tmp_path, monkeypatch):
    state, snapshot = inputs()
    ledger = tmp_path/'ledger.json'
    ledger.write_text(json.dumps(state), encoding='utf-8')
    monkeypatch.setattr('backend.core.temp_paths.codeyun_temp_root', lambda *args, **kwargs: tmp_path)
    assert research.record_storage_bag_research_plan_evidence(snapshot, {}, path=ledger) is None
    with job_log_context('storage-bag-auto-claim', 'test-evidence-attempt'):
        path = research.record_storage_bag_research_plan_evidence(snapshot, {'deferred': []}, path=ledger)
        snapshot['evidence']['pid'] += 1
        research.record_storage_bag_research_plan_evidence(snapshot, {}, path=ledger)
    receipt = json.loads(Path(path).read_text(encoding='utf-8'))
    assert [r['sequence'] for r in receipt['observations']] == [1, 2]
    assert [r['snapshot_source_binding_verified'] for r in receipt['observations']] == [True, False]
    assert receipt['attempt_id'] == 'test-evidence-attempt'
    assert json.loads(ledger.read_text(encoding='utf-8')) == state


def test_public_read_missing_and_different_attempt_without_reconstruction(tmp_path):
    from backend.core.messaging.wechat_agent_store import AgentStore
    store = AgentStore(tmp_path/'events.sqlite')
    target = tmp_path/'missing'
    result = research.read_storage_bag_research_plan_evidence('task', 'different-attempt',
        directory=target, store=store)
    assert result['status'] == 'missing' and result['receipt'] is None
    assert not target.exists()


def test_public_failure_is_newer_than_old_receipt_and_recovery_retains_history(tmp_path, monkeypatch):
    from backend.core.messaging.wechat_agent_store import AgentStore
    store = AgentStore(tmp_path/'events.sqlite')
    state, snapshot = inputs()
    ledger = tmp_path/'ledger.json'
    ledger.write_text(json.dumps(state), encoding='utf-8')
    clock = [10.0]
    monkeypatch.setattr(research.time, 'time', lambda: clock[0])
    plan = {'runtime_fingerprint': snapshot['fingerprint']}
    with job_log_context('task', 'attempt'):
        research.record_storage_bag_research_plan_evidence(snapshot, plan, path=ledger, directory=tmp_path)
        assert research.read_storage_bag_research_plan_evidence('task', 'attempt', directory=tmp_path,
            store=store)['latest_plan_source_binding_verified']
        clock[0] = 11
        research.record_storage_bag_research_plan_diagnostic_failure(OSError('unavailable'), store=store)
        read = research.read_storage_bag_research_plan_evidence('task', 'attempt', directory=tmp_path, store=store)
        assert read['status'] == 'diagnostic_failed' and not read['latest_plan_source_binding_verified']
        assert len(read['receipt']['observations']) == 1
        assert research.read_storage_bag_research_plan_evidence('task', 'foreign', directory=tmp_path,
            store=store)['status'] == 'missing'
        clock[0] = 12
        research.record_storage_bag_research_plan_evidence(snapshot, plan, path=ledger, directory=tmp_path)
    read = research.read_storage_bag_research_plan_evidence('task', 'attempt', directory=tmp_path, store=store)
    assert read['status'] == 'recorded' and read['latest_plan_source_binding_verified']
    assert read['diagnostic_failure']['recorded_at'] == 11
    assert len(read['receipt']['observations']) == 2
