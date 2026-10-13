"""A monitor recovers dead workers without replay or killing live owners."""
import pytest

from backend.core.messaging import wechat_agent as agent
from backend.core.messaging.wechat_agent_store import AgentStore


@pytest.mark.parametrize('condition', ['dead', 'live_stale', 'running', 'stopped', 'reload', 'disabled'])
def test_monitor_only_restarts_dead_enabled_worker(tmp_path, monkeypatch, condition):
    monkeypatch.setattr(agent, 'agent_root', lambda: tmp_path)
    monkeypatch.setattr(agent, 'load_config', lambda: {'enabled': condition != 'disabled'})
    store = AgentStore(tmp_path / 'events.sqlite')
    if condition == 'stopped':
        store.set_meta('stop_requested', 123)
    if condition == 'reload':
        store.set_meta('reload', {'status': 'waiting_for_idle'})
    monkeypatch.setattr(agent, 'wechat_agent_status', lambda: {
        'running': condition == 'running', 'external_worker': {'pid': 12345},
    })
    monkeypatch.setattr('psutil.pid_exists', lambda pid: condition == 'live_stale')
    starts = []
    monkeypatch.setattr(agent, 'start_wechat_agent_service', lambda: starts.append(True) or {'running': True})
    result = agent.ensure_wechat_agent_service_for_monitor()
    assert len(starts) == (1 if condition == 'dead' else 0)
    if condition == 'live_stale':
        assert result['status'] == 'error'
    if condition == 'dead':
        assert result['recovered']
