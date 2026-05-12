import ipaddress
import socket
import time
from typing import Any, List, Optional
from urllib.parse import urlparse

import requests
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from backend.core.auth import get_current_user_from_token
from backend.core.device import get_device_id, get_device_token
from backend.db import get_session
from backend.models import User, UserDevice
from backend.schemas import DeviceRead, UserDeviceCreate, UserDeviceRead, UserDeviceTokenRead, UserDeviceUpdate

router = APIRouter()
REMOTE_DEVICE_DIRECT_PROXIES = {"http": "", "https": "", "all": "", "no_proxy": "*"}


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


def _effective_entry_token(user_device: UserDevice) -> str:
    if user_device.mode == "local":
        return get_device_token() or ""
    return user_device.token or ""


def _sync_local_entry_token(session: Session, user_device: UserDevice) -> None:
    if user_device.mode != "local":
        return

    token = get_device_token()
    if not token or user_device.token == token:
        return

    user_device.token = token
    user_device.updated_at = time.time()
    session.add(user_device)
    session.commit()
    session.refresh(user_device)


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


def _remote_device_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "X-Device-Token": token,
    }


def _extract_remote_error(resp: requests.Response) -> str:
    try:
        payload = resp.json()
    except ValueError:
        payload = None

    if isinstance(payload, dict):
        detail = payload.get("detail") or payload.get("message") or payload.get("error")
        if isinstance(detail, str) and detail.strip():
            return detail.strip()

    return resp.text.strip() or f"远程设备返回 HTTP {resp.status_code}"


def _fetch_remote_device_identity(server_url: str, token: str) -> tuple[str, str]:
    target_url = f"{server_url.rstrip('/')}/api/device-control/status"
    try:
        resp = requests.get(
            target_url,
            headers=_remote_device_headers(token),
            proxies=REMOTE_DEVICE_DIRECT_PROXIES.copy(),
            timeout=10,
        )
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"远程设备不可达：{exc}") from exc

    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=_extract_remote_error(resp))

    try:
        payload: Any = resp.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="远程设备身份接口返回了无效 JSON") from exc

    if not isinstance(payload, dict):
        raise HTTPException(status_code=502, detail="远程设备身份接口返回格式无效")

    device_id = str(payload.get("id") or payload.get("device_id") or "").strip()
    if not device_id:
        raise HTTPException(status_code=502, detail="远程设备未返回 device_id")

    hostname = str(payload.get("hostname") or "").strip()
    return device_id, hostname


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


@router.get("/{entry_id}/token", response_model=UserDeviceTokenRead)
def read_user_device_token(
    entry_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
):
    link = session.get(UserDevice, entry_id)
    if not link or link.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Device entry not found")
    _sync_local_entry_token(session, link)
    token = _effective_entry_token(link)
    if not token:
        raise HTTPException(status_code=400, detail="本机设备 Token 未配置")
    return UserDeviceTokenRead(token=token)


@router.post("/add", response_model=UserDeviceRead)
def add_user_device(
    device_in: UserDeviceCreate,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
):
    mode = device_in.mode
    token = (device_in.token or "").strip()

    if mode == "local":
        token = get_device_token() or token
        if not token:
            raise HTTPException(status_code=400, detail="本机设备 Token 未配置")
        if device_in.server_url and device_in.server_url.strip():
            raise HTTPException(status_code=400, detail="本地设备模式不支持后端地址")
        if device_in.device_id and device_in.device_id.strip():
            raise HTTPException(status_code=400, detail="本地设备模式无需填写设备 ID")
        device_id = get_device_id()
        local_name = socket.gethostname()
        name = (device_in.name or device_in.alias or local_name).strip() or local_name
        server_url = None
    else:
        if not token:
            raise HTTPException(status_code=400, detail="Token 不能为空")
        server_url = _normalize_remote_server_url(device_in.server_url)
        device_id = (device_in.device_id or "").strip()
        detected_name = ""
        if not device_id:
            device_id, detected_name = _fetch_remote_device_identity(server_url, token)
        name = (device_in.name or device_in.alias or detected_name or device_id).strip() or device_id

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

    next_token = link.token
    next_server_url = link.server_url
    should_refresh_remote_identity = False

    if device_in.token is not None:
        if link.mode == "local":
            _sync_local_entry_token(session, link)
            raise HTTPException(status_code=400, detail="本地入口 Token 来自本机设备配置，不能在连接入口中手动修改")
        token = device_in.token.strip()
        if not token:
            raise HTTPException(status_code=400, detail="Token 不能为空")
        next_token = token
        should_refresh_remote_identity = True
    if device_in.server_url is not None:
        if link.mode != "remote":
            raise HTTPException(status_code=400, detail="本地设备入口不支持后端地址")
        next_server_url = _normalize_remote_server_url(device_in.server_url)
        should_refresh_remote_identity = True

    detected_name = ""
    if link.mode == "remote" and should_refresh_remote_identity:
        if not next_token or not next_server_url:
            raise HTTPException(status_code=400, detail="远程执行设备缺少后端地址或访问令牌")
        device_id, detected_name = _fetch_remote_device_identity(next_server_url, next_token)
        link.device_id = device_id
        link.token = next_token
        link.server_url = next_server_url

    if device_in.alias is not None:
        link.name = device_in.alias.strip() or link.name
    if device_in.name is not None:
        link.name = device_in.name.strip() or link.name
    elif detected_name and not link.name:
        link.name = detected_name
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
