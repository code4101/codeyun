"""CodeHub SkillDep material API through the paired LingNet peer.

The connection (URL, token, node identity) is resolved from the LingNet peer
directory at call time and checked against the peer's manifest; credentials
never leave this object and are not included in errors.
"""
from __future__ import annotations

import platform
import time
from typing import Any
from urllib.parse import quote

import requests

from backend.core.devices.http_proxy import REMOTE_DEVICE_DIRECT_PROXIES
from backend.core.settings import get_settings

SKILLDEP_PREFIX = '/data-platform/skill-dependency/plan/materials'
SNAPSHOT_PATH = '/data-platform/skill-dependency/plan/snapshot'
RETRIES = 3
TIMEOUT = 60


class CodeHubError(RuntimeError):
    pass


def _lingnet_store():
    from lingnet.config import ConfigStore

    return ConfigStore(get_settings().data_dir / 'lingnet')


def local_device() -> dict[str, str]:
    """This device's LingNet identity, resolved locally (no CodeHub round trip)."""
    node = _lingnet_store().node()
    if not node.get('node_id'):
        raise CodeHubError('本机灵网节点尚无 node_id')
    return {'device_id': node['node_id'], 'device_name': node.get('display_name') or platform.node() or node['node_id']}


class CodeHubSkillDep:
    # Class defaults keep instances built without ``__init__`` (tests) on the old 60s x 3 budget.
    timeout: float = TIMEOUT
    retries: int = RETRIES
    backoff: float = 2

    def __init__(self, peer_name: str = 'CodeHub', *, timeout: float = TIMEOUT, retries: int = RETRIES,
                 backoff: float = 2):
        """``timeout`` (seconds per attempt), ``retries`` (attempts on connection errors, >= 1) and
        ``backoff`` (seconds, grows linearly between attempts) bound every request of this client."""
        if timeout <= 0 or retries < 1 or backoff < 0:
            raise ValueError('timeout 必须 > 0，retries >= 1，backoff >= 0')
        self.timeout, self.retries, self.backoff = timeout, int(retries), backoff
        store = _lingnet_store()
        peer = store.peer(peer_name)
        if not peer.get('node_id') or not peer.get('token') or not peer['url'].rstrip('/').endswith('/lingnet'):
            raise CodeHubError('CodeHub 灵网对端未核验或地址不是 .../api/lingnet')
        self._peer = peer
        self.api_base = peer['url'].rstrip('/')[:-len('/lingnet')]
        device = local_device()
        self.device_id = device['device_id']
        self.device_name = device['device_name']

    def _request(self, method: str, path: str, **kwargs) -> Any:
        headers = {'Authorization': 'Bearer ' + self._peer['token']}
        last = None
        # Every call here is a read or an idempotent upsert, so a lost response may be retried.
        for attempt in range(self.retries):
            try:
                response = requests.request(method, self.api_base + path, headers=headers, timeout=self.timeout,
                                            proxies=REMOTE_DEVICE_DIRECT_PROXIES, **kwargs)
            except requests.RequestException as exc:
                last = CodeHubError(f'{method} {path} 连接失败：{type(exc).__name__}')
                if attempt + 1 < self.retries:
                    time.sleep(self.backoff * (attempt + 1))
                continue
            if not response.ok:
                detail = response.text[:300] if response.status_code < 500 else ''
                raise CodeHubError(f'{method} {path} HTTP {response.status_code} {detail}')
            return response.json()
        raise last

    def verify_peer(self) -> dict[str, Any]:
        manifest = self._request('GET', '/lingnet/manifest')
        if manifest.get('device_id') != self._peer['node_id'] or not manifest.get('enabled'):
            raise CodeHubError('CodeHub manifest 身份不匹配或未开启')
        return {'device_id': manifest['device_id'], 'display_name': manifest.get('display_name')}

    def materials(self) -> list[dict[str, Any]]:
        data = self._request('GET', SKILLDEP_PREFIX)
        return data['items'] if isinstance(data, dict) else data

    def snapshot(self, include_secrets: bool = False) -> dict[str, Any]:
        """Whole plan snapshot (materials, acquisitions, deliveries, cloud transfers); secrets only on request."""
        return self._request('GET', SNAPSHOT_PATH, params={'include_secrets': str(bool(include_secrets)).lower()})

    def register(self, material_id: str, record: dict[str, Any]) -> dict[str, Any]:
        path = f'{SKILLDEP_PREFIX}/{quote(material_id)}/acquisitions/{quote(self.device_id)}'
        return self._request('PUT', path, json=record)

    def acquisitions(self, material_id: str) -> list[dict[str, Any]]:
        data = self._request('GET', f'{SKILLDEP_PREFIX}/{quote(material_id)}/acquisitions')
        return data['items'] if isinstance(data, dict) else data
