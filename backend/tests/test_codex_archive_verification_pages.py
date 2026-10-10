"""Real TEMP AgentStore receipts; bounded archive-source contract, no sends."""
import threading
from types import SimpleNamespace

import pytest

from backend.core.messaging.wechat_agent_store import AgentStore
from backend.core.messaging.codex_results import sync_codex_results

HOOK = {'key': 'archive-test', 'account_id': 'wxid_archive_test', 'chat_id': 'test@chatroom'}


@pytest.fixture
def store(tmp_path):
    s = AgentStore(tmp_path / 'events.sqlite')
    s.ensure_hook(**HOOK)
    s.subscribe_codex_thread('thread', HOOK['key'], [])
    s.configure_codex_reporting('thread', mode='stages_only')
    return s


def sent(s, number):
    turn = f'stage:archive-{number}'
    assert s.enqueue_codex_completion('thread', turn, f'archive-result-{number}')
    row = s.codex_notification('thread', turn)
    reply_id = s.reserve_codex_notification(row['id'])
    s.finish_reply(reply_id, 'sent', {'result': 1, 'test_receipt': True})
    return s.codex_notification('thread', turn)


def echo(row, *, local_id=None):
    return {'local_id': local_id or row['id'], 'sender_username': HOOK['account_id'],
            'create_time': row['created_at'] + 1, 'message_text': row['text']}


def service(s, rows, calls, fail_q=None):
    def forbidden_send(*a, **k):
        raise AssertionError('sent archive verification must never send')
    def query(chat, *, q, limit, include_resources):
        assert chat == HOOK['chat_id'] and limit == 100 and include_resources is False
        calls.append(q)
        if q == fail_q:
            raise OSError('archive temporarily unavailable')
        return {'items': [r for r in reversed(rows) if q in r['message_text']][:limit]}
    return SimpleNamespace(store=s, config={'hooks': [HOOK]}, stop_event=threading.Event(),
        source=lambda account: SimpleNamespace(list_messages=query), sender=forbidden_send,
        image_sender=forbidden_send, file_sender=forbidden_send, poll_once=lambda: None)


def test_old_sent_late_archive_reenters_after_pages_without_resend(store):
    receipts = [sent(store, n) for n in range(25)]
    oldest = receipts[0]
    assert oldest['id'] not in {n['id'] for n in store.codex_notifications_status()['notifications']}
    rows = [echo(r) for r in receipts[1:]]
    calls = []
    # Reopen the actual persistent store between sweeps.
    sync_codex_results(service(AgentStore(store.path), rows, calls))
    marker = f"codex_result_verification:{oldest['id']}"
    assert store.get_meta(marker)['status'] == 'awaiting_archive'
    assert len(calls) == 20 and store.get_meta('codex_result_archive_scan_cursor') is not None
    sync_codex_results(service(AgentStore(store.path), rows, calls))
    assert len(calls) == 25 and store.get_meta('codex_result_archive_scan_cursor') is None
    rows.append(echo(oldest, local_id=999))
    # The matching reply lies beyond 100 unrelated group messages.
    rows.extend({'message_text': f'unrelated {i}', 'local_id': i+1000,
                 'sender_username': 'other', 'create_time': oldest['created_at']+2} for i in range(150))
    sync_codex_results(service(AgentStore(store.path), rows, calls))
    assert store.get_meta(marker)['status'] == 'verified'
    assert store.get_meta(marker)['local_ids'] == [999]
    assert len(calls) == 26  # all 24 verified notifications were excluded
    sync_codex_results(service(AgentStore(store.path), rows, calls))
    assert len(calls) == 26
    assert all(store.codex_notification('thread', r['turn_id'])['status'] == 'sent' for r in receipts)


def test_round_horizon_excludes_new_sends_and_returns_to_old_waiters(store):
    old = [sent(store, n) for n in range(23)]
    first = store.codex_notifications_awaiting_archive(limit=20)
    new = [sent(store, n+100) for n in range(30)]
    second = AgentStore(store.path).codex_notifications_awaiting_archive(cursor=first['next_cursor'])
    assert [r['id'] for r in second['notifications']] == [r['id'] for r in old[20:]]
    assert second['next_cursor'] is None
    restart = store.codex_notifications_awaiting_archive(limit=20)
    assert restart['notifications'][0]['id'] == old[0]['id']
    assert restart['next_cursor']['through_id'] == new[-1]['id']


def test_source_failure_does_not_starve_later_notifications(store):
    first, second = sent(store, 1), sent(store, 2)
    calls = []
    sync_codex_results(service(store, [echo(second)], calls, fail_q=first['text']))
    assert store.get_meta(f"codex_result_verification:{first['id']}")['status'] == 'awaiting_archive'
    assert store.get_meta(f"codex_result_verification:{second['id']}")['status'] == 'verified'
    assert calls == [first['text'], second['text']]
    assert store.get_meta('codex_results_worker')['error'] is not None


def test_wrong_sender_and_old_matching_text_do_not_verify(store):
    row = sent(store, 1)
    wrong = {**echo(row), 'sender_username': 'other'}
    old = {**echo(row), 'create_time': row['created_at']-4}
    calls = []
    sync_codex_results(service(store, [wrong, old], calls))
    assert store.get_meta(f"codex_result_verification:{row['id']}")['status'] == 'awaiting_archive'


def test_literal_full_text_query_keeps_reserved_characters_and_exact_text(store, tmp_path):
    import sqlite3
    from pyxllib.autogui.wechat_db import _literal_like_clause, _safe_like
    from backend.core.messaging.codex_results import read_codex_notification_archive
    text = '【进度】\n原线程 thread-id Goal blocked writer inactive_owner；完成率100%。'
    assert store.enqueue_codex_completion('thread', 'stage:reserved', text)
    n = store.codex_notification('thread', 'stage:reserved')
    store.finish_reply(store.reserve_codex_notification(n['id']), 'sent', {'test_receipt': True})
    n = store.codex_notification('thread', 'stage:reserved')
    with sqlite3.connect(tmp_path / 'archive.sqlite') as conn:
        conn.execute('CREATE TABLE archive(text TEXT)')
        conn.execute('INSERT INTO archive VALUES(?)', (text,))
        queries = []
        def query(chat, *, q, limit, include_resources):
            queries.append(q)
            safe = _safe_like(q)
            rows = conn.execute('SELECT text FROM archive WHERE '+_literal_like_clause('text'), ('%'+safe+'%',)).fetchall()
            return {'items': [{**echo(n), 'message_text': r[0]} for r in rows]}
        source = SimpleNamespace(list_messages=query)
        # Preserve the first-fault contrast using actual SQLite, not a fake q.
        assert conn.execute('SELECT text FROM archive WHERE text LIKE ?', ('%'+_safe_like(text)+'%',)).fetchall() == []
        result = read_codex_notification_archive(n, hook=HOOK, source=source)
    assert result['status'] == 'verified'
    assert queries[-1] == text


@pytest.mark.parametrize('cursor', [{}, {'after_id': True, 'through_id': 1},
    {'after_id': 2, 'through_id': 1}, {'after_id': -1, 'through_id': 1}])
def test_invalid_public_page_cursor_is_rejected(store, cursor):
    with pytest.raises(ValueError):
        store.codex_notifications_awaiting_archive(cursor=cursor)
