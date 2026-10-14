"""Real SQLite lock contention must not stop the account listener."""
import sqlite3

import pytest

from backend.core.messaging.wechat_agent import WechatAgentService
from backend.core.messaging.wechat_agent_store import AgentStore


def test_readers_see_committed_state_while_writer_holds_lock(tmp_path):
    store = AgentStore(tmp_path / 'events.sqlite')
    store.ensure_hook('group', 'bot', '123@chatroom')
    store.set_meta('checkpoint', 'committed')
    with store.transaction() as writer:
        writer.execute('UPDATE meta SET value=? WHERE key=?', ('"uncommitted"', 'checkpoint'))
        assert store.get_meta('checkpoint') == 'committed'
        assert store.hook('group')['status'] == 'idle'
        assert store.pending_messages('group') == []
        assert store.status()['event_count'] == 0
        assert store.uncertain_replies('group') == []
    assert store.get_meta('checkpoint') == 'uncommitted'


def test_read_transaction_rejects_accidental_mutation(tmp_path):
    store = AgentStore(tmp_path / 'events.sqlite')
    with store.transaction(write=False) as reader:
        with pytest.raises(sqlite3.OperationalError, match='readonly'):
            reader.execute('DELETE FROM meta')


def test_locked_heartbeat_recovers_after_writer_releases(tmp_path, monkeypatch):
    store = AgentStore(tmp_path / 'events.sqlite')
    service = WechatAgentService({'accounts': [], 'hooks': []}, store=store)
    connect = sqlite3.connect
    def quick_timeout(*args, **kwargs):
        kwargs['timeout'] = 0.05
        return connect(*args, **kwargs)
    monkeypatch.setattr(sqlite3, 'connect', quick_timeout)
    with store.transaction():
        assert service.publish_heartbeat() is False
        assert store.get_meta('service') is None
    assert service.publish_heartbeat() is True
    assert store.get_meta('service')['stopped'] is False


def test_health_does_not_hide_unrelated_database_failure(tmp_path, monkeypatch):
    store = AgentStore(tmp_path / 'events.sqlite')
    service = WechatAgentService({'accounts': [], 'hooks': []}, store=store)
    def broken(*args, **kwargs):
        raise sqlite3.OperationalError('no such table: meta')
    monkeypatch.setattr(store, 'set_meta', broken)
    with pytest.raises(sqlite3.OperationalError, match='no such table'):
        service.publish_heartbeat()


@pytest.mark.parametrize('code', [sqlite3.SQLITE_IOERR, sqlite3.SQLITE_IOERR_WRITE])
def test_io_failed_heartbeat_can_recover_without_advancing_business(tmp_path, monkeypatch, code):
    store = AgentStore(tmp_path / 'events.sqlite')
    store.ensure_hook('group', 'bot', '123@chatroom')
    store.update_hook('group', consumed_seq=42)
    service = WechatAgentService({'accounts': [], 'hooks': []}, store=store)
    original = store.set_meta
    def broken(*args, **kwargs):
        exc = sqlite3.OperationalError('disk I/O error')
        exc.sqlite_errorcode = code
        raise exc
    monkeypatch.setattr(store, 'set_meta', broken)
    assert service.publish_heartbeat() is False
    assert store.hook('group')['consumed_seq'] == 42
    monkeypatch.setattr(store, 'set_meta', original)
    assert service.publish_heartbeat() is True


def test_legacy_hook_lock_failure_recovers_without_replaying_messages(tmp_path):
    store = AgentStore(tmp_path / 'events.sqlite')
    store.ensure_hook('group', 'bot', '123@chatroom')
    store.update_hook('group', consumed_seq=42, summary='unfinished business',
                      status='failed', last_error='database is locked', retry_at=1)
    assert store.recover_hook_lock_failure('group') is True
    hook = store.hook('group')
    assert (hook['status'], hook['last_error'], hook['retry_at']) == ('idle', None, 0)
    assert hook['consumed_seq'] == 42
    assert hook['summary'] == 'unfinished business'
    assert store.recover_hook_lock_failure('group') is False


@pytest.mark.parametrize('fields', [
    {'last_error': 'no such table: hooks'},
    {'turn_id': 'live-turn'},
    {'retry_at': 10**12},
    {'status': 'uncertain'},
])
def test_lock_recovery_preserves_other_failures_and_owners(tmp_path, fields):
    store = AgentStore(tmp_path / 'events.sqlite')
    store.ensure_hook('group', 'bot', '123@chatroom')
    store.update_hook('group', **{'status': 'failed', 'last_error': 'database is locked',
                                'retry_at': 1, **fields})
    before = store.hook('group')
    assert store.recover_hook_lock_failure('group') is False
    assert store.hook('group') == before


def test_lock_recovery_does_not_clear_uncertain_delivery(tmp_path):
    store = AgentStore(tmp_path / 'events.sqlite')
    store.ensure_hook('group', 'bot', '123@chatroom')
    reply_id = store.reserve_reply('group', 1, 'reserved reply')
    store.finish_reply(reply_id, 'uncertain', {'error': 'delivery not confirmed'})
    store.update_hook('group', status='failed', last_error='database is locked', retry_at=1)
    before = store.hook('group')
    assert store.recover_hook_lock_failure('group') is False
    assert store.hook('group') == before
    assert len(store.uncertain_replies('group')) == 1
