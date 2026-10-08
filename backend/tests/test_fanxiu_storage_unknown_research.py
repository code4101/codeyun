"""Pure policy and real durable ledger contracts; no game simulation."""
from concurrent.futures import ThreadPoolExecutor
import json
import subprocess
import sys

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
