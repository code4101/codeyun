"""Public HTTP client for a registered remote device's Device Agent.

Connection credentials stay with the device provider, never in group prompts.
An uncertain POST must be reconciled through session/turn reads, never retried.
"""
from __future__ import annotations

import requests
from sqlmodel import Session

from backend.core.devices.http_proxy import REMOTE_DEVICE_DIRECT_PROXIES


class RemoteDeviceAgentClient:
    def __init__(self, entry_id: str):
        self.entry_id = entry_id

    def connection(self):
        """Resolve current registered credentials and physical device identity."""
        from backend.db import engine
        from backend.models import UserDevice
        from backend.core.devices.device import get_device_id

        with Session(engine) as session:
            entry = session.get(UserDevice, self.entry_id)
            if (not entry or not entry.is_active or entry.mode != 'remote'
                    or entry.device_id == get_device_id() or not entry.server_url or not entry.token):
                raise ValueError('微信中转需要有效的远端设备连接')
            url = entry.server_url.rstrip('/') + '/api/device-agent'
            headers = {'X-Device-Token': entry.token}
            return url, headers, entry.device_id

    def validate_target(self):
        """Verify the endpoint actually represents the configured remote device."""
        _, _, device_id = self.connection()
        manifest = self.request('GET', '/manifest')
        if manifest.get('device_id') != device_id or not manifest.get('enabled'):
            raise ValueError('远端设备身份不匹配或 Device Agent 未启用')
        return manifest

    def request(self, method: str, path: str, **kwargs):
        base_url, headers, _ = self.connection()
        url = base_url + path
        response = requests.request(method, url, headers=headers, timeout=20,
                                    proxies=REMOTE_DEVICE_DIRECT_PROXIES, **kwargs)
        # Do not expose credentials, remote response bodies or request URLs in logs.
        if not response.ok:
            raise RuntimeError(f'远端 Device Agent HTTP {response.status_code}')
        return response.json()

    def close(self):
        pass
