"""部署主机连接的账号边界，与远程设备入口所有权正交。"""

from fastapi import HTTPException

from backend.models import User


def ensure_host_device_access(current_user: User, *, mode: str, device_id: str = "") -> None:
    """本机连接、执行和凭据仅供管理员；remote 标签不能掩盖本机 ID。"""
    from backend.core.devices.device import get_device_id

    if not current_user.is_superuser and (mode == "local" or device_id == get_device_id()):
        raise HTTPException(status_code=403, detail="仅管理员可访问部署主机设备连接或令牌")
