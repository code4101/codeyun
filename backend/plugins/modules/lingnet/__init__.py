"""灵网插件：本机 AI 主体由独立的 lingnet 包提供，CodeYun 只做宿主适配。

节点接口同时挂在 /api/lingnet 与 device-agent v1 的 /api/device-agent，
其他设备和微信中转仍可带 CodeYun 设备 token 访问本机。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, HTTPException
from lingnet import Host, Lingnet

# 旧 Device Agent 的节点配置；灵网首次启动时沿用为默认值，之后以灵网配置页为准。
LEGACY_DEVICE_AGENT_CONFIG_KEY = "system:device_agent:config"
LEGACY_CONFIG_FIELDS = ("enabled", "display_name", "device_role", "local_context", "responsibilities")


class CodeYunHost(Host):
    name = "CodeYun"

    def data_dir(self) -> Path:
        from backend.core.settings import get_settings

        return get_settings().data_dir

    def admin_dependency(self) -> Callable[..., Any]:
        from backend.core.access.auth import get_current_active_superuser

        return get_current_active_superuser

    def node_id(self) -> str | None:
        from backend.core.devices.device import get_device_id

        return get_device_id()

    def verify_peer_token(self, token: str) -> bool:
        from backend.core.access.auth import validate_api_token_value

        try:
            validate_api_token_value(token)
        except HTTPException:
            return False
        return True

    def node_defaults(self) -> dict[str, Any]:
        from backend.core.settings import ROOT_DIR

        return {"cwd": str(ROOT_DIR), "device_role": "CodeYun 设备节点", **_legacy_device_agent_config()}


def _legacy_device_agent_config() -> dict[str, Any]:
    from sqlalchemy.exc import OperationalError
    from sqlmodel import Session

    from backend.db import engine
    from backend.models import AppSetting

    # 插件在应用导入时注册，早于迁移；新库还没有 appsetting 表时没有旧配置可沿用。
    try:
        with Session(engine) as session:
            row = session.get(AppSetting, LEGACY_DEVICE_AGENT_CONFIG_KEY)
    except OperationalError:
        return {}
    value = row.value if row is not None and isinstance(row.value, dict) else {}
    return {key: value[key] for key in LEGACY_CONFIG_FIELDS if value.get(key) not in (None, "")}


def register(app: FastAPI) -> None:
    lingnet = Lingnet(CodeYunHost())
    for router in lingnet.routers():
        app.include_router(router)
    lingnet.startup()
    app.state.lingnet = lingnet
