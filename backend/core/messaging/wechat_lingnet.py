"""微信中转使用灵网对端名录；连接身份与凭据始终由灵网提供。"""
from __future__ import annotations

from lingnet.config import ConfigStore
from backend.core.devices.agent_client import RemoteDeviceAgentClient
from backend.core.settings import get_settings


class LingnetWechatClient(RemoteDeviceAgentClient):
    def __init__(self, peer_id: str):
        self.peer_id = peer_id

    def connection(self):
        """每次调用解析最新连接；只接受已核验身份的精确对端 ID。"""
        peer = ConfigStore(get_settings().data_dir / 'lingnet').peer(self.peer_id)
        if peer['id'] != self.peer_id or not peer.get('node_id') or not peer.get('token'):
            raise ValueError('微信中转需要已核验身份的灵网对端 ID')
        # 复用设备协议客户端的直连、身份核验、错误脱敏与单次 POST 策略。
        return peer['url'], {'Authorization': 'Bearer ' + peer['token'],
                             'X-Device-Token': peer['token']}, peer['node_id']
