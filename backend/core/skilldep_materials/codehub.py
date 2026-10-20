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
RETRIES = 3


class CodeHubError(RuntimeError):
    pass


class CodeHubSkillDep:
    def __init__(self, peer_name: str = 'CodeHub'):
        from lingnet.config import ConfigStore

        store = ConfigStore(get_settings().data_dir / 'lingnet')
        peer = store.peer(peer_name)
        if not peer.get('node_id') or not peer.get('token') or not peer['url'].rstrip('/').endswith('/lingnet'):
            raise CodeHubError('CodeHub 灵网对端未核验或地址不是 .../api/lingnet')
        self._peer = peer
        self.api_base = peer['url'].rstrip('/')[:-len('/lingnet')]
        node = store.node()
        self.device_id = node['node_id']
        self.device_name = node.get('display_name') or platform.node() or node['node_id']

    def _request(self, method: str, path: str, **kwargs) -> Any:
        headers = {'Authorization': 'Bearer ' + self._peer['token']}
        last = None
        # Every call here is a read or an idempotent upsert, so a lost response may be retried.
        for attempt in range(RETRIES):
            try:
                response = requests.request(method, self.api_base + path, headers=headers, timeout=60,
                                            proxies=REMOTE_DEVICE_DIRECT_PROXIES, **kwargs)
            except requests.RequestException as exc:
                last = CodeHubError(f'{method} {path} 连接失败：{type(exc).__name__}')
                time.sleep(2 * (attempt + 1))
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

    def register(self, material_id: str, record: dict[str, Any]) -> dict[str, Any]:
        path = f'{SKILLDEP_PREFIX}/{quote(material_id)}/acquisitions/{quote(self.device_id)}'
        return self._request('PUT', path, json=record)

    def acquisitions(self, material_id: str) -> list[dict[str, Any]]:
        data = self._request('GET', f'{SKILLDEP_PREFIX}/{quote(material_id)}/acquisitions')
        return data['items'] if isinstance(data, dict) else data
