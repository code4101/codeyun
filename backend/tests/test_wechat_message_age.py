"""Queue age limits preserve history, active work and explicit recovery."""
import time

from backend.tests.test_wechat_agent import ingest, message
from backend.core.messaging.wechat_agent_store import AgentStore


def make_store(tmp_path):
    store = AgentStore(tmp_path / 'events.sqlite')
    store.ensure_hook('test', 'wxid_gxgjjgft1oj722', '123@chatroom')
    store.set_message_age_policy(max_age_seconds=21600, reason='Owner: skip old queued input')
    return store


def test_old_queue_is_skipped_but_history_and_new_references_survive(tmp_path):
    store = make_store(tmp_path)
    old = message(1, '旧请求')
    old['timestamp'] = time.time() - 21601
    fresh = message(2, '继续处理我昨天的请求')
    ingest(store, old, fresh)
    assert [m['seq'] for m in store.pending_messages('test')] == [2]
    assert [m['seq'] for m in store.unrouted_messages('test')] == [2]
    assert store.defer_unrouted_messages('test', reason='Recovery') == 1
    assert [m['seq'] for m in store.messages('test')] == [1, 2]
    assert store.status()['outbox'] == []
    assert not store.question_answered('test', 1)
    # Policy is durable across restart, not a one-time sweep.
    assert [m['seq'] for m in AgentStore(store.path).pending_messages('test')] == [2]


def test_active_submitted_input_is_not_cancelled_by_age(tmp_path):
    store = make_store(tmp_path)
    old = message(1, '正在执行的旧请求')
    old['timestamp'] = time.time() - 30000
    ingest(store, old)
    store.update_hook('test', status='running')
    store.set_meta('context_delivery:test', {'submitted_seq': 1})
    assert [m['seq'] for m in store.pending_messages('test')] == [1]
    store.update_hook('test', status='idle')
    assert store.pending_messages('test') == []


def test_explicit_reopening_can_restore_old_ignored_input(tmp_path):
    store = make_store(tmp_path)
    old = message(1, '旧问题')
    old['timestamp'] = time.time() - 30000
    ingest(store, old)
    store.ignore_unaddressed_message('test', 1)
    assert store.reopen_ignored_message('test', 1, reason='Owner explicitly asked again')
    assert [m['seq'] for m in store.pending_messages('test')] == [1]
