"""An operator can recover an ignored input without replaying delivered work."""
import time

import pytest

from backend.tests.test_wechat_message_routing import fixture_store, ingest


def ignored_store(tmp_path):
    store = fixture_store(tmp_path)
    ingest(store, 1, '国家图书馆附近有什么好吃的？')
    ingest(store, 2, '后续无关聊天')
    for seq in (1, 2):
        store.record_message_route('group', seq, 'main')
    store.resolve_routed_messages('group', [1, 2])
    store.set_meta('context_delivery:group', {'submitted_seq': 2, 'seeded': True, 'candidate': None, 'receipts': {}})
    return store


def test_reopen_resubmits_original_only_and_survives_restart(tmp_path):
    store = ignored_store(tmp_path)
    wait = time.time() + 300
    assert store.reopen_ignored_message('group', 1, reason='群用途误作问答白名单', not_before=wait)
    assert store.hook('group')['consumed_seq'] == 0
    assert store.hook('group')['retry_at'] == wait
    assert store.get_meta('context_delivery:group')['submitted_seq'] == 0
    restarted = type(store)(store.path)
    assert [m['seq'] for m in restarted.pending_messages('group', restarted.hook('group')['consumed_seq'])] == [1]
    assert not restarted.reopen_ignored_message('group', 1, reason='重复恢复')
    assert restarted.messages('group')[0]['text'] == '国家图书馆附近有什么好吃的？'


@pytest.mark.parametrize('status', ['sent', 'sending', 'uncertain'])
def test_reopen_cannot_replay_grouped_delivery_with_later_anchor(tmp_path, status):
    store = ignored_store(tmp_path)
    # Question 1 was covered by a reply anchored on 2, even if a stale ignore
    # ledger entry still exists. Delivery identity takes precedence.
    reply_id = store.reserve_reply('group', 2, '答复')
    store.set_meta(f'reply_groups:{reply_id}', [{'question_seqs': [1], 'text': '答复'}])
    if status != 'sending':
        store.finish_reply(reply_id, status, {})
    store.update_hook('group', status='idle', turn_id=None)
    with pytest.raises(ValueError, match='sent or reserved'):
        store.reopen_ignored_message('group', 1, reason='误忽略')
    assert store.pending_messages('group') == []


@pytest.mark.parametrize('busy', ['writer', 'draft'])
def test_reopen_does_not_steal_writer_or_draft(tmp_path, busy):
    store = ignored_store(tmp_path)
    if busy == 'writer':
        store.update_hook('group', status='running', turn_id='turn')
    else:
        store.set_meta('context_delivery:group', {'submitted_seq': 2, 'candidate': {'text': '待发送'}})
    with pytest.raises(ValueError, match='active writer'):
        store.reopen_ignored_message('group', 1, reason='误忽略')


def test_reopen_rejects_foreign_source_and_missing_diagnosis(tmp_path):
    store = ignored_store(tmp_path)
    with pytest.raises(ValueError, match='Foreign'):
        store.reopen_ignored_message('group', 99, reason='误忽略')
    with pytest.raises(ValueError, match='diagnosis'):
        store.reopen_ignored_message('group', 1, reason=' ')
