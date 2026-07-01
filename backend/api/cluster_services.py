from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

import requests
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session

from backend.api.services import (
    ServiceTokenCreateRequest,
    ServiceTokenUpdateRequest,
    build_service_docs_response,
    build_service_summary_response,
    control_create_token,
    control_delete_token,
    control_list_tokens,
    control_reveal_token,
    control_reset_ocr_service,
    control_update_token,
)
from backend.core.access.auth import get_current_user_from_token
from backend.core.access.feature_access_guard import ensure_feature_access
from backend.core.devices.http_proxy import REMOTE_DEVICE_DIRECT_PROXIES
from backend.db import get_session
from backend.models import User, UserDevice


router = APIRouter()
CLUSTER_SERVICES_FEATURE_KEY = "cluster.services"


def _normalize_service_keys(keys: list[str] | None) -> list[str] | None:
    if not keys:
        return None
    normalized: list[str] = []
    for raw_key in keys:
        for part in str(raw_key or "").split(","):
            key = part.strip()
            if key and key not in normalized:
                normalized.append(key)
    return normalized or None


def _get_entry_or_404(session: Session, current_user: User, entry_id: str) -> UserDevice:
    ensure_feature_access(
        session,
        feature_key=CLUSTER_SERVICES_FEATURE_KEY,
        current_user=current_user,
    )
    entry = session.get(UserDevice, entry_id)
    if not entry or entry.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Device entry not found")
    if not entry.is_active:
        raise HTTPException(status_code=400, detail="Device entry is inactive")
    return entry


def _require_admin(current_user: User) -> None:
    if not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="只有管理员可以管理服务 Token")


def _remote_base_url(entry: UserDevice) -> str:
    if entry.mode != "remote" or not entry.server_url:
        raise HTTPException(status_code=400, detail="远程设备入口未配置后端地址")
    return entry.server_url.rstrip("/")


def _remote_headers(entry: UserDevice) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {entry.token}",
        "X-Device-Token": entry.token,
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


def _proxy_service_control(
    entry: UserDevice,
    method: str,
    path: str,
    *,
    json_body: Any | None = None,
    timeout: int = 20,
) -> Any:
    target_url = f"{_remote_base_url(entry)}/api/service-control{path}"
    try:
        resp = requests.request(
            method=method,
            url=target_url,
            headers=_remote_headers(entry),
            json=json_body,
            proxies=REMOTE_DEVICE_DIRECT_PROXIES.copy(),
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"远程服务管理不可达：{exc}") from exc

    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=_extract_remote_error(resp))

    try:
        return resp.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="远程服务管理返回了无效 JSON") from exc


@router.get("/{entry_id}")
def get_entry_service_summary(
    entry_id: str,
    keys: list[str] | None = Query(None),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
):
    entry = _get_entry_or_404(session, current_user, entry_id)
    service_keys = _normalize_service_keys(keys)
    if entry.mode == "local":
        return build_service_summary_response(session, service_keys=service_keys)
    path = "/summary"
    if service_keys:
        path = f"{path}?{urlencode([('keys', key) for key in service_keys])}"
    return _proxy_service_control(entry, "GET", path)


@router.post("/{entry_id}/ocr/reset")
def reset_entry_ocr_service(
    entry_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
):
    entry = _get_entry_or_404(session, current_user, entry_id)
    _require_admin(current_user)
    if entry.mode == "local":
        return control_reset_ocr_service()
    return _proxy_service_control(entry, "POST", "/ocr/reset")


@router.get("/{entry_id}/docs")
def get_entry_service_docs(
    entry_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
):
    entry = _get_entry_or_404(session, current_user, entry_id)
    if entry.mode == "local":
        return build_service_docs_response()
    return _proxy_service_control(entry, "GET", "/docs")


@router.get("/{entry_id}/tokens")
def list_entry_service_tokens(
    entry_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
):
    entry = _get_entry_or_404(session, current_user, entry_id)
    _require_admin(current_user)
    if entry.mode == "local":
        return control_list_tokens(session)
    return _proxy_service_control(entry, "GET", "/tokens")


@router.post("/{entry_id}/tokens")
def create_entry_service_token(
    entry_id: str,
    req: ServiceTokenCreateRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
):
    entry = _get_entry_or_404(session, current_user, entry_id)
    _require_admin(current_user)
    if entry.mode == "local":
        return control_create_token(req, session)
    return _proxy_service_control(entry, "POST", "/tokens", json_body=req.model_dump())


@router.get("/{entry_id}/tokens/{token_id}/reveal")
def reveal_entry_service_token(
    entry_id: str,
    token_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
):
    entry = _get_entry_or_404(session, current_user, entry_id)
    _require_admin(current_user)
    if entry.mode == "local":
        return control_reveal_token(token_id, session)
    return _proxy_service_control(entry, "GET", f"/tokens/{token_id}/reveal")


@router.patch("/{entry_id}/tokens/{token_id}")
def update_entry_service_token(
    entry_id: str,
    token_id: str,
    req: ServiceTokenUpdateRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
):
    entry = _get_entry_or_404(session, current_user, entry_id)
    _require_admin(current_user)
    if entry.mode == "local":
        return control_update_token(token_id, req, session)
    return _proxy_service_control(entry, "PATCH", f"/tokens/{token_id}", json_body=req.model_dump(exclude_unset=True))


@router.delete("/{entry_id}/tokens/{token_id}")
def delete_entry_service_token(
    entry_id: str,
    token_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token),
):
    entry = _get_entry_or_404(session, current_user, entry_id)
    _require_admin(current_user)
    if entry.mode == "local":
        return control_delete_token(token_id, session)
    return _proxy_service_control(entry, "DELETE", f"/tokens/{token_id}")
