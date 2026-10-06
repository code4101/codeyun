"""Delivery evidence is background; a short current answer keeps other work pending."""
import json

from backend.core.messaging.wechat_agent import model_delivery_receipt
from backend.tests.test_wechat_agent import ingest, owner_event, private_service, PlainClient, store


def test_sent_receipt_retains_coverage_without_old_answer_or_native_details():
    original = {'through_seq': 12, 'status': 'sent', 'created_at': 1,
                'text': '旧工程结果' * 1000, 'result': {'sender': {'pid': 123}},
                'reply_groups': [{'question_seqs': [10, 11], 'text': '旧答案', 'quote_text': '旧问题'}]}
    compact = model_delivery_receipt(original)
    assert compact == {'through_seq': 12, 'status': 'sent', 'created_at': 1,
                       'reply_groups': [{'question_seqs': [10, 11]}]}
    assert len(json.dumps(compact)) < len(json.dumps(original)) / 10
    assert original['text']  # Provider receipt remains unchanged.


def test_uncertain_receipt_preserves_evidence_for_reconciliation():
    original = {'through_seq': 12, 'status': 'uncertain', 'text': '部分发送',
                'result': {'instruction': '禁止重发'}, 'reply_groups': [{'question_seqs': [10]}]}
    assert model_delivery_receipt(original) == original


def test_latest_short_answer_does_not_complete_other_pending_task(store):
    sent = []
    svc, hook = private_service(store, lambda _, text, **kw: sent.append(text) or {})
    ingest(store, owner_event(1, '长任务还要继续'), owner_event(2, '你还在吗？'))
    client = PlainClient('CODECLAW_REPLY_TO: 2\n在，长任务继续处理。')
    assert svc.process_hook(hook, client)
    assert '本轮答复范围：当前新增消息 [1, 2]' in client.prompts[0]
    assert [m['seq'] for m in store.pending_messages('owner')] == [1]
    assert len(sent) == 1 and '长任务还要继续' not in sent[0]


def test_new_thread_baselines_completed_receipts_instead_of_replaying_history(store):
    svc, hook = private_service(store, lambda *a, **k: {})
    ingest(store, owner_event(1, '旧问题'))
    assert svc.process_hook(hook, PlainClient('旧答案'))
    progress = svc.context_delivery('owner', 'new-daily-thread', PlainClient(''), new_thread=True)
    assert progress['receipts']['1']['status'] == 'sent'
    assert 'text' not in progress['receipts']['1']
    assert progress['seeded'] is False


def test_legacy_full_receipt_snapshot_migrates_without_resending_answer(store):
    svc, hook = private_service(store, lambda *a, **k: {})
    ingest(store, owner_event(1, '旧问题'))
    client = PlainClient('旧答案')
    assert svc.process_hook(hook, client)
    progress = store.get_meta('context_delivery:owner')
    progress['receipts'] = {'1': store.deliveries('owner')[0]}
    store.set_meta('context_delivery:owner', progress)
    ingest(store, owner_event(2, '新问题'))
    newer = PlainClient('新答案')
    assert svc.process_hook(hook, newer)
    assert '旧答案' not in newer.prompts[0]
    assert '[工程发送结果' not in newer.prompts[0]
    assert 'text' not in store.get_meta('context_delivery:owner')['receipts']['1']
