from types import SimpleNamespace

import pytest

from backend.core.baidu_netdisk.client import NetdiskClient, Screen


def test_transfer_observation_activates_owned_window_before_fresh_capture():
    client = object.__new__(NetdiskClient)
    events = []
    client.main_window = lambda: 'owned-main'
    client.lease_check = lambda: events.append('lease')
    client.driver = SimpleNamespace(activate=lambda window: events.append(('activate', window)))
    screen = Screen({'width': 1000, 'height': 800}, [
        {'text': '下载中(0)', 'x': 100, 'y': 80, 'w': 100, 'h': 20}])
    client.observe = lambda window: (events.append(('capture', window)) or screen)
    client.click_box = lambda *args, **kwargs: pytest.fail('already on transfer page')
    assert client.observe_transfers() == ('owned-main', screen)
    assert events == ['lease', ('activate', 'owned-main'), ('capture', 'owned-main')]


def test_expired_lease_prevents_transfer_activation_and_capture():
    client = object.__new__(NetdiskClient)
    client.main_window = lambda: 'owned-main'
    def expired():
        raise RuntimeError('lease expired')
    client.lease_check = expired
    client.driver = SimpleNamespace(activate=lambda *args: pytest.fail('expired lease'))
    client.observe = lambda *args: pytest.fail('expired lease')
    with pytest.raises(RuntimeError, match='lease expired'):
        client.observe_transfers()
