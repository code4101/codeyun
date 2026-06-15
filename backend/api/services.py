from __future__ import annotations

import base64
import binascii
import ipaddress
import os
import socket
import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from backend.core.access.auth import verify_api_token
from backend.core.devices.device import get_device_id
from backend.core.runtime.game_window_service import get_game_window_service_status
from backend.core.ocr.preview import OcrPreviewError, OcrShapeType, run_paddle_ocr_preview
from backend.core.runtime.ocr_service import get_ocr_service_status, reset_ocr_service
from backend.core.access.service_tokens import (
    SERVICE_SCOPE_OCR_PREDICT,
    SERVICE_SCOPE_OCR_STATUS,
    ServiceTokenError,
    create_service_access_token,
    delete_service_access_token,
    ensure_legacy_service_tokens,
    list_service_access_tokens,
    require_service_scope,
    reveal_service_access_token,
    update_service_access_token,
)
from backend.core.settings import get_settings
from backend.db import get_session
from backend.models import ServiceAccessToken


router = APIRouter()
control_router = APIRouter(dependencies=[Depends(verify_api_token)])
_RFC1918_LAN_NETWORKS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
)


class OcrPredictRequest(BaseModel):
    image: str = Field(min_length=1)
    shape_type: OcrShapeType = "rectangle"
    options: dict[str, Any] = Field(default_factory=dict)


class ServiceTokenCreateRequest(BaseModel):
    label: str = ""
    scopes: list[str] = Field(default_factory=lambda: [SERVICE_SCOPE_OCR_PREDICT, SERVICE_SCOPE_OCR_STATUS])
    notes: str = ""
    enabled: bool = True


class ServiceTokenUpdateRequest(BaseModel):
    label: str | None = None
    scopes: list[str] | None = None
    notes: str | None = None
    enabled: bool | None = None


def _decode_request_image(value: str) -> bytes:
    payload = (value or "").strip()
    if "," in payload and payload.split(",", 1)[0].lower().startswith("data:"):
        payload = payload.split(",", 1)[1]
    try:
        image_bytes = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=400, detail="image 必须是 base64 图片字符串") from exc

    max_bytes = get_settings().service_request_max_image_bytes
    if len(image_bytes) > max_bytes:
        raise HTTPException(status_code=413, detail=f"图片超过服务限制：{max_bytes} bytes")
    if not image_bytes:
        raise HTTPException(status_code=400, detail="image 不能为空")
    return image_bytes


def _predict_ocr_from_base64(req: OcrPredictRequest) -> dict[str, Any]:
    image_bytes = _decode_request_image(req.image)
    temp_path = ""
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as temp_file:
            temp_file.write(image_bytes)
            temp_path = temp_file.name
        preview = run_paddle_ocr_preview(Path(temp_path), shape_type=req.shape_type, options=req.options)
    except OcrPreviewError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    finally:
        if temp_path:
            try:
                os.unlink(temp_path)
            except OSError:
                pass

    return {
        "ok": True,
        "engine": preview["engine"],
        "shape_type": preview["shape_type"],
        "shape_count": preview["shape_count"],
        "document": preview["document"],
    }


def _is_lan_ipv4(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return ip.version == 4 and any(ip in network for network in _RFC1918_LAN_NETWORKS)


def _get_primary_route_ip_address() -> str | None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return None
    finally:
        sock.close()


def _get_lan_ip_addresses() -> list[str]:
    addresses: list[str] = []

    def add_address(value: str | None) -> None:
        if not value:
            return
        try:
            ip = ipaddress.ip_address(value)
        except ValueError:
            return
        if not _is_lan_ipv4(ip):
            return
        text = str(ip)
        if text not in addresses:
            addresses.append(text)

    add_address(_get_primary_route_ip_address())
    if addresses:
        return addresses

    try:
        hostname = socket.gethostname()
        for item in socket.getaddrinfo(hostname, None, family=socket.AF_INET):
            add_address(item[4][0])
    except OSError:
        pass

    return addresses


def _resolve_public_base_url() -> tuple[str | None, str]:
    settings = get_settings()
    if settings.public_base_url:
        return settings.public_base_url, "configured"

    names = {socket.gethostname().strip().lower(), get_device_id().strip().lower()}
    if names & {"codepc_mf", "codepc-mf"}:
        return "https://code4101.com", "default_codepc_mf"
    return None, "unconfigured"


def build_service_docs_response() -> dict[str, Any]:
    settings = get_settings()
    local_base = f"http://127.0.0.1:{settings.backend_port}"
    connections: list[dict[str, Any]] = [
        {
            "kind": "local",
            "label": "本机",
            "base_url": local_base,
            "url": f"{local_base}/api/services/ocr/predict",
            "status": "available",
        }
    ]
    for ip in _get_lan_ip_addresses():
        base_url = f"http://{ip}:{settings.backend_port}"
        connections.append(
            {
                "kind": "lan",
                "label": "局域网",
                "base_url": base_url,
                "url": f"{base_url}/api/services/ocr/predict",
                "status": "available",
            }
        )

    public_base_url, public_source = _resolve_public_base_url()
    connections.append(
        {
            "kind": "public",
            "label": "公网",
            "base_url": public_base_url or "",
            "url": f"{public_base_url}/api/services/ocr/predict" if public_base_url else "",
            "status": "available" if public_base_url else "unconfigured",
            "source": public_source,
        }
    )

    example_base_url = public_base_url or local_base
    endpoint = f"{example_base_url}/api/services/ocr/predict"
    return {
        "ok": True,
        "services": [
            {
                "key": "ocr",
                "title": "OCR",
                "endpoint": "/api/services/ocr/predict",
                "method": "POST",
                "scopes": [SERVICE_SCOPE_OCR_PREDICT, SERVICE_SCOPE_OCR_STATUS],
            }
        ],
        "connections": connections,
        "examples": {
            "curl": (
                f"curl -X POST \"{endpoint}\" "
                "-H \"Authorization: Bearer <service-token>\" "
                "-H \"Content-Type: application/json\" "
                "-d \"{\\\"image\\\":\\\"<base64-image>\\\",\\\"shape_type\\\":\\\"rectangle\\\",\\\"options\\\":{}}\""
            ),
            "python": (
                "import base64\n"
                "import requests\n\n"
                "with open('image.png', 'rb') as f:\n"
                "    image = base64.b64encode(f.read()).decode('utf-8')\n\n"
                f"resp = requests.post('{endpoint}',\n"
                "    headers={'Authorization': 'Bearer <service-token>'},\n"
                "    json={'image': image, 'shape_type': 'rectangle', 'options': {}})\n"
                "resp.raise_for_status()\n"
                "labelme_doc = resp.json()['document']"
            ),
            "javascript": (
                "const file = document.querySelector('input[type=file]').files[0];\n"
                "const image = await new Promise((resolve) => {\n"
                "  const reader = new FileReader();\n"
                "  reader.onload = () => resolve(String(reader.result).split(',')[1]);\n"
                "  reader.readAsDataURL(file);\n"
                "});\n\n"
                f"const resp = await fetch('{endpoint}', {{\n"
                "  method: 'POST',\n"
                "  headers: {\n"
                "    'Authorization': 'Bearer <service-token>',\n"
                "    'Content-Type': 'application/json',\n"
                "  },\n"
                "  body: JSON.stringify({ image, shape_type: 'rectangle', options: {} }),\n"
                "});\n"
                "const { document } = await resp.json();"
            ),
        },
    }


def build_service_summary_response(session: Session) -> dict[str, Any]:
    ensure_legacy_service_tokens(session)
    token_count = session.exec(select(ServiceAccessToken)).all()
    enabled_token_count = [token for token in token_count if token.enabled]
    return {
        "ok": True,
        "device": {
            "id": get_device_id(),
            "hostname": socket.gethostname(),
        },
        "services": [get_ocr_service_status(), get_game_window_service_status()],
        "token_count": len(token_count),
        "enabled_token_count": len(enabled_token_count),
    }


def _map_service_token_error(exc: ServiceTokenError) -> HTTPException:
    return HTTPException(status_code=404, detail=str(exc))


@router.post(
    "/ocr/predict",
    dependencies=[Depends(require_service_scope(SERVICE_SCOPE_OCR_PREDICT))],
)
def predict_ocr(req: OcrPredictRequest):
    return _predict_ocr_from_base64(req)


@router.get(
    "/ocr/status",
    dependencies=[Depends(require_service_scope(SERVICE_SCOPE_OCR_STATUS))],
)
def get_ocr_status():
    return {"ok": True, "service": get_ocr_service_status()}


@control_router.get("/summary")
def control_get_service_summary(session: Session = Depends(get_session)):
    return build_service_summary_response(session)


@control_router.post("/ocr/reset")
def control_reset_ocr_service():
    return {"ok": True, "service": reset_ocr_service()}


@control_router.get("/docs")
def control_get_service_docs():
    return build_service_docs_response()


@control_router.get("/tokens")
def control_list_tokens(session: Session = Depends(get_session)):
    ensure_legacy_service_tokens(session)
    return {"ok": True, "tokens": list_service_access_tokens(session)}


@control_router.post("/tokens")
def control_create_token(req: ServiceTokenCreateRequest, session: Session = Depends(get_session)):
    try:
        token = create_service_access_token(
            session,
            label=req.label,
            scopes=req.scopes,
            enabled=req.enabled,
            notes=req.notes,
        )
    except ServiceTokenError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "token": token}


@control_router.get("/tokens/{token_id}/reveal")
def control_reveal_token(token_id: str, session: Session = Depends(get_session)):
    try:
        return {"ok": True, "token": reveal_service_access_token(session, token_id)}
    except ServiceTokenError as exc:
        raise _map_service_token_error(exc) from exc


@control_router.patch("/tokens/{token_id}")
def control_update_token(token_id: str, req: ServiceTokenUpdateRequest, session: Session = Depends(get_session)):
    try:
        return {
            "ok": True,
            "token": update_service_access_token(
                session,
                token_id,
                label=req.label,
                scopes=req.scopes,
                enabled=req.enabled,
                notes=req.notes,
            ),
        }
    except ServiceTokenError as exc:
        raise _map_service_token_error(exc) from exc


@control_router.delete("/tokens/{token_id}")
def control_delete_token(token_id: str, session: Session = Depends(get_session)):
    try:
        delete_service_access_token(session, token_id)
    except ServiceTokenError as exc:
        raise _map_service_token_error(exc) from exc
    return {"ok": True}
