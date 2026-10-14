from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from backend.core.access import auth
from backend.plugins.modules import lingnet as plugin


def test_lingnet_plugin_serves_device_agent_v1_with_codeyun_identity(tmp_path, monkeypatch) -> None:
    def validate(token):
        if token != "device-token":
            raise HTTPException(status_code=401, detail="Invalid authentication token")

    monkeypatch.setattr(plugin.CodeYunHost, "data_dir", lambda self: tmp_path)
    monkeypatch.setattr(plugin.CodeYunHost, "node_id", lambda self: "codeyun-device")
    monkeypatch.setattr(plugin, "_legacy_device_agent_config", lambda: {"enabled": True, "display_name": "旧节点名"})
    monkeypatch.setattr(auth, "validate_api_token_value", validate)

    app = FastAPI()
    plugin.register(app)
    client = TestClient(app)

    # 微信中转与其他设备沿用 /api/device-agent + CodeYun 设备 token。
    manifest = client.get("/api/device-agent/manifest", headers={"X-Device-Token": "device-token"}).json()
    assert manifest["device_id"] == "codeyun-device"
    assert manifest["enabled"] is True and manifest["display_name"] == "旧节点名"
    assert client.get("/api/lingnet/manifest", headers={"X-Device-Token": "wrong"}).status_code == 401
    # 管理接口走 CodeYun 管理员登录。
    assert client.get("/api/lingnet/admin/node").status_code == 401
