import ipaddress
import socket
import time
from typing import List, Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from backend.core.auth import get_current_user_from_token
from backend.core.device import get_device_id
from backend.db import get_session
from backend.models import User, UserDevice
from backend.schemas import DeviceRead, UserDeviceCreate, UserDeviceRead, UserDeviceUpdate

router = APIRouter()


def _get_next_order_index(session: Session, user_id: int) -> int:
    last_link = session.exec(
        select(UserDevice)
        .where(UserDevice.user_id == user_id)
        .order_by(UserDevice.order_index.desc(), UserDevice.created_at.desc())
    ).first()
    if not last_link or last_link.order_index is None:
        return 0
    return last_link.order_index + 1


def _entry_type(user_device: UserDevice) -> str:
    return "LocalDevice" if user_device.mode == "local" else "RemoteDevice"


def _client_visible_server_url(user_device: UserDevice) -> Optional[str]:
    if user_device.mode == "local":
        return None
    return user_device.server_url


def _device_read(user_device: UserDevice) -> DeviceRead:
    return DeviceRead(
        id=user_device.device_id,
        name=user_device.name,
        type=_entry_type(user_device),
        server_url=_client_visible_server_url(user_device),
        order_index=user_device.order_index,
        created_at=user_device.created_at,
        updated_at=user_device.updated_at,
    )


def _entry_read(user_device: UserDevice) -> UserDeviceRead:
    return UserDeviceRead(
        id=user_device.entry_id,
        user_id=user_device.user_id,
        device_id=user_device.device_id,
        mode=user_device.mode,
        alias=user_device.name,
        name=user_device.name,
        server_url=_client_visible_server_url(user_device),
        token=user_device.token,
        is_active=user_device.is_active,
        created_at=user_device.created_at,
        updated_at=user_device.updated_at,
        device=_device_read(user_device),
    )


def _normalize_remote_server_url(raw_url: Optional[str]) -> str:
    if not raw_url or not raw_url.strip():
        raise HTTPException(status_code=400, detail="远程设备模式必须填写后端地址")

    url = raw_url.strip()
    if not url.startswith(("http://", "https://")):
        url = "http://" + url

    parsed = urlparse(url)
    if not parsed.hostname:
        raise HTTPException(status_code=400, detail="设备后端地址格式无效")

    host = parsed.hostname.strip().lower()
    if host == "localhost":
        raise HTTPException(status_code=400, detail="localhost 无法作为远程设备后端地址，请改用本地设备模式")

    try:
        ip = ipaddress.ip_address(host)
        if ip.is_loopback:
            raise HTTPException(status_code=400, detail="回环地址不可作为远程设备后端地址，请改用本地设备模式")
    except ValueError:
        pass

    normalized = url.rstrip("/")
    return normalized


@router.get("/", response_model=List[UserDeviceRead])
def read_user_devices(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
):
    statement = (
        select(UserDevice)
        .where(UserDevice.user_id == current_user.id)
        .order_by(UserDevice.order_index, UserDevice.created_at)
    )
    user_devices = session.exec(statement).all()
    return [_entry_read(entry) for entry in user_devices]


@router.post("/add", response_model=UserDeviceRead)
def add_user_device(
    device_in: UserDeviceCreate,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
):
    mode = device_in.mode
    token = (device_in.token or "").strip()
    if not token:
        raise HTTPException(status_code=400, detail="Token 不能为空")

    if mode == "local":
        if device_in.server_url and device_in.server_url.strip():
            raise HTTPException(status_code=400, detail="本地设备模式不支持后端地址")
        if device_in.device_id and device_in.device_id.strip():
            raise HTTPException(status_code=400, detail="本地设备模式无需填写设备 ID")
        device_id = get_device_id()
        local_name = socket.gethostname()
        name = (device_in.name or device_in.alias or local_name).strip() or local_name
        server_url = None
    else:
        device_id = (device_in.device_id or "").strip()
        if not device_id:
            raise HTTPException(status_code=400, detail="远程设备模式必须填写设备 ID")
        name = (device_in.name or device_in.alias or device_id).strip() or device_id
        server_url = _normalize_remote_server_url(device_in.server_url)

    new_link = UserDevice(
        user_id=current_user.id,
        device_id=device_id,
        mode=mode,
        token=token,
        name=name,
        server_url=server_url,
        is_active=True,
        order_index=_get_next_order_index(session, current_user.id),
    )
    session.add(new_link)
    session.commit()
    session.refresh(new_link)
    return _entry_read(new_link)


@router.put("/{entry_id}", response_model=UserDeviceRead)
def update_user_device(
    entry_id: str,
    device_in: UserDeviceUpdate,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
):
    link = session.get(UserDevice, entry_id)
    if not link or link.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Device entry not found")

    if device_in.token is not None:
        token = device_in.token.strip()
        if not token:
            raise HTTPException(status_code=400, detail="Token 不能为空")
        link.token = token
    if device_in.alias is not None:
        link.name = device_in.alias.strip() or link.name
    if device_in.name is not None:
        link.name = device_in.name.strip() or link.name
    if device_in.server_url is not None:
        if link.mode != "remote":
            raise HTTPException(status_code=400, detail="本地设备入口不支持后端地址")
        link.server_url = _normalize_remote_server_url(device_in.server_url)
    if device_in.is_active is not None:
        link.is_active = device_in.is_active

    link.updated_at = time.time()
    session.add(link)
    session.commit()
    session.refresh(link)
    return _entry_read(link)


@router.delete("/{entry_id}")
def remove_user_device(
    entry_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
):
    link = session.get(UserDevice, entry_id)
    if not link or link.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Device entry not found")

    session.delete(link)
    session.commit()
    return {"ok": True}


@router.post("/reorder")
def reorder_user_devices(
    entry_ids: List[str],
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
):
    for idx, entry_id in enumerate(entry_ids):
        link = session.get(UserDevice, entry_id)
        if link and link.user_id == current_user.id:
            link.order_index = idx
            session.add(link)
    session.commit()
    return {"status": "reordered"}
