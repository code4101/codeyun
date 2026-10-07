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
