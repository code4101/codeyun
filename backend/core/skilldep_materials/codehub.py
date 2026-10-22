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
CLOUD_TRANSFERS_PATH = '/data-platform/skill-dependency/plan/cloud-transfers'
STORAGE_NAMING_PATH = '/data-platform/skill-dependency/plan/storage-naming'
STORAGE_LAYOUT_PATH = '/data-platform/skill-dependency/plan/storage-layout'
FINALIZATIONS_PATH = '/data-platform/skill-dependency/plan/finalizations'
FIRST_BATCH_PATH = '/data-platform/skill-dependency/plan/first-batch'
SITE_BLOCKS_PATH = '/data-platform/skill-dependency/plan/site-blocks'
FINALIZATION_ROOTS = ('cloud', 'local')
RELOCATE_FIELDS = {'cloud_path', 'expected_resource_version', 'listing_verified', 'observed_at', 'note'}
RETRIES = 3
TIMEOUT = 60


class CodeHubError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def _segment(value: str, name: str) -> str:
    """One URL path segment; empty, dot-only or non-string identifiers are rejected, ``/`` is escaped."""
    if not isinstance(value, str) or not value.strip() or value.strip('.') == '':
        raise ValueError(f'{name} 必须是非空标识符')
    return quote(value, safe='')


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

    def _request(self, method: str, path: str, *, attempts: int | None = None, **kwargs) -> Any:
        headers = {'Authorization': 'Bearer ' + self._peer['token']}
        last = None
        # Reads and idempotent upserts may be retried after a lost response; non-idempotent
        # calls pass ``attempts=1`` and leave confirmation to a read-back by the caller.
        attempts = self.retries if attempts is None else attempts
        for attempt in range(attempts):
            try:
                response = requests.request(method, self.api_base + path, headers=headers, timeout=self.timeout,
                                            proxies=REMOTE_DEVICE_DIRECT_PROXIES, **kwargs)
            except requests.RequestException as exc:
                last = CodeHubError(f'{method} {path} 连接失败：{type(exc).__name__}')
                if attempt + 1 < attempts:
                    time.sleep(self.backoff * (attempt + 1))
                continue
            if not response.ok:
                detail = response.text[:300] if response.status_code < 500 else ''
                raise CodeHubError(f'{method} {path} HTTP {response.status_code} {detail}', response.status_code)
            return response.json()
        if attempts == 1:
            raise CodeHubError(f'{last}（未重试，结果未知，请回读确认）')
        raise last

    def verify_peer(self) -> dict[str, Any]:
        manifest = self._request('GET', '/lingnet/manifest')
        if manifest.get('device_id') != self._peer['node_id'] or not manifest.get('enabled'):
            raise CodeHubError('CodeHub manifest 身份不匹配或未开启')
        return {'device_id': manifest['device_id'], 'display_name': manifest.get('display_name')}

    def materials(self) -> list[dict[str, Any]]:
        data = self._request('GET', SKILLDEP_PREFIX)
        return data['items'] if isinstance(data, dict) else data

    def first_batch(self) -> dict[str, Any]:
        """The first-release selection: chosen third-level points and the materials each one uses."""
        return self._request('GET', FIRST_BATCH_PATH)

    def site_blocks(self) -> dict[str, dict[str, Any]]:
        """Site circuit breakers by short site name; ``active=true`` sites are off limits to every device."""
        data = self._request('GET', SITE_BLOCKS_PATH)
        return data.get('items', data) if isinstance(data, dict) else {b['site']: b for b in data}

    def put_site_block(self, site: str, reason: str) -> dict[str, Any]:
        """Trip the breaker of ``site`` (main-domain short name, e.g. ``cninfo``); server default cooldown 24h."""
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError('reason 必填')
        return self._request('PUT', f'{SITE_BLOCKS_PATH}/{_segment(site, "site")}', json={'reason': reason[:500]})

    def snapshot(self, include_secrets: bool = False) -> dict[str, Any]:
        """Whole plan snapshot (materials, acquisitions, deliveries, cloud transfers); secrets only on request."""
        return self._request('GET', SNAPSHOT_PATH, params={'include_secrets': str(bool(include_secrets)).lower()})

    def register(self, material_id: str, record: dict[str, Any]) -> dict[str, Any]:
        path = f'{SKILLDEP_PREFIX}/{quote(material_id)}/acquisitions/{quote(self.device_id)}'
        return self._request('PUT', path, json=record)

    def cloud_transfers(self) -> list[dict[str, Any]]:
        """Every netdisk transfer record of the plan."""
        data = self._request('GET', CLOUD_TRANSFERS_PATH)
        return data['items'] if isinstance(data, dict) else data

    def put_cloud_transfer(self, material_id: str, account_id: str, record: dict[str, Any]) -> dict[str, Any]:
        """Upsert one transfer record; ``record['expected_resource_version']`` guards the material version."""
        path = f'{SKILLDEP_PREFIX}/{quote(material_id)}/cloud-transfers/{quote(account_id)}'
        return self._request('PUT', path, json=record)

    def acquisitions(self, material_id: str) -> list[dict[str, Any]]:
        data = self._request('GET', f'{SKILLDEP_PREFIX}/{quote(material_id)}/acquisitions')
        return data['items'] if isinstance(data, dict) else data

    def storage_naming(self) -> dict[str, Any]:
        """Central storage naming: layout rule, ``cloud``/``local``/``staging`` roots and historical prefixes."""
        return self._request('GET', STORAGE_NAMING_PATH)

    def storage_layout(self) -> dict[str, Any]:
        """Desired cloud/local dirs per material as computed by the rule; not proof of physical placement."""
        return self._request('GET', STORAGE_LAYOUT_PATH)

    def finalizations(self) -> dict[str, Any]:
        """All final clean-output records: ``{version, updated_at, count, items}``."""
        return self._request('GET', FINALIZATIONS_PATH)

    def put_finalization(self, material_id: str, root: str, record: dict[str, Any]) -> dict[str, Any]:
        """Upsert the (material, root) finalization; ``root`` is ``cloud`` or ``local`` (staging is never final).

        Idempotent overwrite on the server, so the shared retry policy applies; HTTP 409/422 surface as
        ``CodeHubError.status_code``."""
        if root not in FINALIZATION_ROOTS:
            raise ValueError(f'root 只能是 {"/".join(FINALIZATION_ROOTS)}')
        if not isinstance(record, dict):
            raise ValueError('record 必须是 dict')
        path = f'{SKILLDEP_PREFIX}/{_segment(material_id, "material_id")}/finalizations/{root}'
        return self._request('PUT', path, json=record)

    def relocate_cloud_transfer(self, material_id: str, account_id: str, record: dict[str, Any]) -> dict[str, Any]:
        """Register the verified new ``cloud_path`` of an existing transferred record (no re-transfer).

        ``record``: ``cloud_path``, ``expected_resource_version``, ``listing_verified=True`` and optional
        ``observed_at``/``note``. The POST appends path history, so it is sent exactly once: a lost response
        raises ``CodeHubError`` and the caller confirms via ``cloud_transfers()`` before any resend."""
        if not isinstance(record, dict):
            raise ValueError('record 必须是 dict')
        unknown = set(record) - RELOCATE_FIELDS
        if unknown:
            raise ValueError(f'relocate 不支持字段：{sorted(unknown)}')
        if not isinstance(record.get('cloud_path'), str) or not record['cloud_path'].strip():
            raise ValueError('cloud_path 必须是非空字符串')
        if record.get('expected_resource_version') in (None, ''):
            raise ValueError('expected_resource_version 必填')
        if record.get('listing_verified') is not True:
            raise ValueError('listing_verified 必须为 True（先回读网盘列表确认）')
        path = (f'{SKILLDEP_PREFIX}/{_segment(material_id, "material_id")}'
                f'/cloud-transfers/{_segment(account_id, "account_id")}/relocate')
        return self._request('POST', path, json=record, attempts=1)
