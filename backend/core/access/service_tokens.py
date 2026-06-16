from __future__ import annotations

import ast
import hashlib
import secrets
import time
from pathlib import Path
from typing import Any

from fastapi import Depends, Header, HTTPException, status
from sqlmodel import Session, select

from backend.core.ai.chat_user_config import (
    AiChatUserConfigError,
    _decrypt_secret,
    _encrypt_secret,
)
from backend.core.settings import ROOT_DIR
from backend.db import get_session
from backend.models import ServiceAccessToken


SERVICE_SCOPE_OCR_PREDICT = "services.ocr:predict"
SERVICE_SCOPE_OCR_STATUS = "services.ocr:status"
SERVICE_SCOPE_FANXIU_RUNTIME_CONTROL = "fanxiu.runtime:control"
SERVICE_SCOPE_MOBILE_SMS_UPLOAD = "mobile.sms:upload"
DEFAULT_OCR_SERVICE_SCOPES = (SERVICE_SCOPE_OCR_PREDICT, SERVICE_SCOPE_OCR_STATUS)
SERVICE_TOKEN_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"


class ServiceTokenError(RuntimeError):
    pass


def _hash_service_token(value: str) -> str:
    return hashlib.sha256(value.strip().encode("utf-8")).hexdigest()


def _generate_service_token_plaintext() -> str:
    chars = [secrets.choice(SERVICE_TOKEN_ALPHABET) for _ in range(32)]
    groups = ["".join(chars[index:index + 4]) for index in range(0, len(chars), 4)]
    return f"cys-{'-'.join(groups)}"


def _mask_service_token(value: str) -> str:
    payload = value.strip()
    if not payload:
        return ""
    visible_length = max(1, (len(payload) + 1) // 2)
    hidden_length = len(payload) - visible_length
    if hidden_length <= 0:
        return payload
    return f"{payload[:visible_length]}{'*' * hidden_length}"


def _get_service_token_masked_value(token: ServiceAccessToken) -> str:
    try:
        plaintext = _decrypt_secret(token.secret_encrypted)
    except AiChatUserConfigError:
        return token.masked_value
    return _mask_service_token(plaintext)


def _normalize_scopes(scopes: list[str] | tuple[str, ...] | None) -> list[str]:
    normalized: list[str] = []
    for raw_scope in scopes or DEFAULT_OCR_SERVICE_SCOPES:
        scope = str(raw_scope or "").strip()
        if scope and scope not in normalized:
            normalized.append(scope)
    return normalized or list(DEFAULT_OCR_SERVICE_SCOPES)


def _serialize_service_token(token: ServiceAccessToken, *, plaintext_value: str | None = None) -> dict[str, Any]:
    payload = {
        "id": token.id,
        "label": token.label,
        "masked_value": _get_service_token_masked_value(token),
        "scopes": list(token.scopes or []),
        "enabled": token.enabled,
        "is_legacy": token.is_legacy,
        "notes": token.notes,
        "call_count": token.call_count,
        "last_used_at": token.last_used_at,
        "created_by_user_id": token.created_by_user_id,
        "created_at": token.created_at,
        "updated_at": token.updated_at,
    }
    if plaintext_value is not None:
        payload["plaintext_value"] = plaintext_value
    return payload


def list_service_access_tokens(session: Session) -> list[dict[str, Any]]:
    tokens = session.exec(
        select(ServiceAccessToken).order_by(
            ServiceAccessToken.is_legacy,
            ServiceAccessToken.created_at,
            ServiceAccessToken.label,
        )
    ).all()
    return [_serialize_service_token(token) for token in tokens]


def create_service_access_token(
    session: Session,
    *,
    label: str | None = None,
    plaintext_value: str | None = None,
    scopes: list[str] | tuple[str, ...] | None = None,
    enabled: bool = True,
    is_legacy: bool = False,
    notes: str | None = None,
    created_by_user_id: int | None = None,
) -> dict[str, Any]:
    plaintext = (plaintext_value or _generate_service_token_plaintext()).strip()
    if not plaintext:
        raise ServiceTokenError("服务 Token 不能为空")

    secret_hash = _hash_service_token(plaintext)
    existing = session.exec(
        select(ServiceAccessToken).where(ServiceAccessToken.secret_hash == secret_hash)
    ).first()
    if existing:
        return _serialize_service_token(existing, plaintext_value=plaintext if not is_legacy else None)

    now = time.time()
    token = ServiceAccessToken(
        label=(label or "").strip() or ("legacy service token" if is_legacy else "服务 Token"),
        secret_hash=secret_hash,
        secret_encrypted=_encrypt_secret(plaintext),
        masked_value=_mask_service_token(plaintext),
        scopes=_normalize_scopes(scopes),
        enabled=enabled,
        is_legacy=is_legacy,
        notes=(notes or "").strip(),
        created_by_user_id=created_by_user_id,
        created_at=now,
        updated_at=now,
    )
    session.add(token)
    session.commit()
    session.refresh(token)
    return _serialize_service_token(token, plaintext_value=plaintext if not is_legacy else None)


def reveal_service_access_token(session: Session, token_id: str) -> dict[str, Any]:
    token = session.get(ServiceAccessToken, token_id)
    if not token:
        raise ServiceTokenError("指定的服务 Token 不存在")
    try:
        plaintext = _decrypt_secret(token.secret_encrypted)
    except AiChatUserConfigError as exc:
        raise ServiceTokenError(str(exc)) from exc
    return _serialize_service_token(token, plaintext_value=plaintext)


def update_service_access_token(
    session: Session,
    token_id: str,
    *,
    label: str | None = None,
    scopes: list[str] | tuple[str, ...] | None = None,
    enabled: bool | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    token = session.get(ServiceAccessToken, token_id)
    if not token:
        raise ServiceTokenError("指定的服务 Token 不存在")

    if label is not None:
        token.label = label.strip() or token.label
    if scopes is not None:
        token.scopes = _normalize_scopes(scopes)
    if enabled is not None:
        token.enabled = bool(enabled)
    if notes is not None:
        token.notes = notes.strip()
    token.updated_at = time.time()
    session.add(token)
    session.commit()
    session.refresh(token)
    return _serialize_service_token(token)


def delete_service_access_token(session: Session, token_id: str) -> None:
    token = session.get(ServiceAccessToken, token_id)
    if not token:
        raise ServiceTokenError("指定的服务 Token 不存在")
    session.delete(token)
    session.commit()


def _service_token_has_scope(token: ServiceAccessToken, required_scope: str) -> bool:
    scopes = set(token.scopes or [])
    return required_scope in scopes or "services.*" in scopes or "*" in scopes


def validate_service_token_value(
    session: Session,
    raw_token: str | None,
    *,
    required_scope: str,
) -> ServiceAccessToken:
    token_value = (raw_token or "").strip()
    if not token_value:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing service token",
        )

    token = session.exec(
        select(ServiceAccessToken).where(ServiceAccessToken.secret_hash == _hash_service_token(token_value))
    ).first()
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid service token",
        )
    if not token.enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Service token is disabled",
        )
    if not _service_token_has_scope(token, required_scope):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Service token does not have the required scope",
        )

    token.call_count = int(token.call_count or 0) + 1
    token.last_used_at = time.time()
    token.updated_at = token.updated_at or token.last_used_at
    session.add(token)
    session.commit()
    session.refresh(token)
    return token


def extract_service_token(
    *,
    authorization: str | None = None,
    legacy_token: str | None = None,
) -> str | None:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization.split(" ", 1)[1].strip()
    if legacy_token:
        return legacy_token.strip()
    return None


def require_service_scope(required_scope: str):
    async def dependency(
        authorization: str | None = Header(None),
        legacy_token: str | None = Header(None, alias="Token"),
        session: Session = Depends(get_session),
    ) -> ServiceAccessToken:
        return validate_service_token_value(
            session,
            extract_service_token(authorization=authorization, legacy_token=legacy_token),
            required_scope=required_scope,
        )

    return dependency


def _extract_string_set_assignment(path: Path, assignment_names: set[str]) -> list[str]:
    try:
        module = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return []

    values: list[str] = []
    for node in ast.walk(module):
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id in assignment_names for target in node.targets):
            continue
        if not isinstance(node.value, (ast.Set, ast.List, ast.Tuple)):
            continue
        for item in node.value.elts:
            if isinstance(item, ast.Constant) and isinstance(item.value, str) and item.value.strip():
                values.append(item.value.strip())
    return values


def discover_legacy_service_tokens() -> list[tuple[str, str]]:
    xlproject_root = ROOT_DIR.parent / "xlproject"
    sources = [
        (xlproject_root / "src" / "xlserver" / "init.py", {"super_tokens"}, "xlserver common_ocr"),
        (xlproject_root / "src" / "xlserver" / "host_common.py", {"api_keys"}, "xlserver host_common"),
    ]

    discovered: list[tuple[str, str]] = []
    seen: set[str] = set()
    for path, names, source_label in sources:
        for token in _extract_string_set_assignment(path, names):
            if token in seen:
                continue
            seen.add(token)
            discovered.append((token, source_label))
    return discovered


def ensure_legacy_service_tokens(session: Session) -> int:
    imported_count = 0
    for index, (plaintext, source_label) in enumerate(discover_legacy_service_tokens(), start=1):
        secret_hash = _hash_service_token(plaintext)
        existing = session.exec(
            select(ServiceAccessToken).where(ServiceAccessToken.secret_hash == secret_hash)
        ).first()
        if existing:
            continue
        create_service_access_token(
            session,
            label=f"legacy {source_label} #{index}",
            plaintext_value=plaintext,
            scopes=DEFAULT_OCR_SERVICE_SCOPES,
            is_legacy=True,
            notes=f"从 {source_label} 旧服务白名单导入",
        )
        imported_count += 1
    return imported_count
