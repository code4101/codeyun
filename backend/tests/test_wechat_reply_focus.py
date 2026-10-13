"""Delivery evidence is background; a short current answer keeps other work pending."""
import json
import time

from backend.core.messaging.wechat_agent import model_delivery_receipt
from backend.tests.test_wechat_agent import ingest, owner_event, private_service, PlainClient, store
from backend.tests.test_wechat_agent import HOOK, message, service, CandidateClient
import pytest


@pytest.mark.parametrize('receipt_status', ['not_sent', 'sent', 'uncertain', 'frozen'])
def test_unchanged_review_recovers_only_confirmed_unsent_source(receipt_status):
    from backend.core.messaging.wechat_reply_groups import preserve_unsent_reply_links
    result = {'action':'reply','text':'原答复','question_seqs':[]}
    receipt = {'status':receipt_status,'text':'原答复',
               'reply_groups':[{'question_seqs':[2914]}]}
    restored = preserve_unsent_reply_links(result, None, [{'seq':2914},{'seq':2918}], [receipt])
    assert restored['question_seqs'] == ([2914] if receipt_status == 'not_sent' else [])
    assert result['question_seqs'] == []


def test_unsent_review_requires_exact_text_and_pending_identity():
    from backend.core.messaging.wechat_reply_groups import preserve_unsent_reply_links
    previous = {'action':'reply','text':'原答复','question_seqs':[1]}
    result = {'action':'reply','text':'原答复','question_seqs':[]}
    assert preserve_unsent_reply_links(result, previous, [{'seq':1}], [])['question_seqs'] == [1]
    assert preserve_unsent_reply_links({**result,'text':'另一答复'}, previous, [{'seq':1}], [])['question_seqs'] == []
    assert preserve_unsent_reply_links(result, previous, [{'seq':2}], [])['question_seqs'] == []


def test_routed_review_delivers_original_without_consuming_unrelated_new_message(store):
    hook = {**HOOK, 'message_routing':True}
    store.enable_message_routing('test')
    ingest(store, message(1, '@考勤返款 问题'), message(2, '@考勤返款 睡觉去了'))
    for seq in (1,2):
        store.record_message_route('test', seq, 'main')
    svc = service(store, sender=lambda *a, **k: {})
    progress = svc.context_delivery('test','daily',CandidateClient())
    progress.update(submitted_seq=2,candidate={'action':'reply','text':'初稿',
                                               'summary':'原答复','question_seqs':[1]},candidate_through=2)
    store.set_meta('context_delivery:test',progress)
    store.update_hook('test',thread_id='daily',session_day='2026-10-13',last_question_at=time.time())
    class ReviewClient(CandidateClient):
        def run(self,*a,**kw):
            return {**super().run(*a,**kw),'question_seqs':[]}
    assert svc.process_hook(hook,ReviewClient())
    assert store.deliveries('test')[0]['reply_groups'][0]['question_seqs'] == [1]
    assert [m['seq'] for m in store.pending_messages('test')] == [2]


def test_routed_group_partial_result_keeps_other_main_requests_pending(store):
    hook = {**HOOK, 'message_routing': True}
    store.enable_message_routing('test')
    ingest(store, message(1, '@考勤返款 修复重复汇报'), message(2, '研究弟子寻访'))
    for seq in (1, 2):
        store.record_message_route('test', seq, 'main')
    sent = []
    svc = service(store, sender=lambda *a, **k: sent.append(a) or {})
    assert svc.process_hook(hook, CandidateClient())
    assert [m['seq'] for m in store.pending_messages('test')] == [2]
    assert store.deliveries('test')[0]['reply_groups'][0]['question_seqs'] == [1]
    assert len(sent) == 1
    class RemainingClient(CandidateClient):
        def run(self, *args, **kwargs):
            return {**super().run(*args, **kwargs), 'question_seqs': [2]}
    assert svc.process_hook(hook, RemainingClient())
    assert store.pending_messages('test') == []
    assert len(sent) == 2


@pytest.mark.parametrize('answered', [[], [999]])
def test_routed_group_rejects_absent_or_foreign_completion_before_send(store, answered):
    hook = {**HOOK, 'message_routing': True}
    store.enable_message_routing('test')
    ingest(store, message(1, '@考勤返款 修复'))
    store.record_message_route('test', 1, 'main')
    class InvalidClient(CandidateClient):
        def run(self, *args, **kwargs):
            return {**super().run(*args, **kwargs), 'question_seqs': answered}
    sent = []
    with pytest.raises(ValueError, match='回复须明确关联'):
        service(store, sender=lambda *a, **k: sent.append(a) or {}).process_hook(hook, InvalidClient())
    assert sent == []
    assert [m['seq'] for m in store.pending_messages('test')] == [1]


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
