from __future__ import annotations

import mimetypes
import os
import re
import time
import html as html_module
import hashlib
import shutil
from datetime import timedelta
from pathlib import Path
from typing import Any, Literal

import requests
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from jose import JWTError, jwt
from pydantic import BaseModel, Field
from sqlmodel import Session, select
from starlette.background import BackgroundTask

from backend.api.filesystem import resolve_request_path
from backend.core.access.auth import (
    ALGORITHM,
    SECRET_KEY,
    create_access_token,
    get_current_active_user,
    get_optional_current_user_from_token,
)
from backend.core.devices.files import ensure_device_file_resource_identity
from backend.core.settings import get_settings
from backend.db import get_session
from backend.models import (
    DeviceFile,
    PdfDocument,
    PdfPageNote,
    PdfUserState,
    ResourceAccessGrant,
    User,
    UserDevice,
    generate_sheet_document_id,
)
from backend.core.resources.identity import RESOURCE_TYPE_PDF, allocate_resource_id


router = APIRouter()

PDF_RESOURCE_TYPE = "pdf"
PDF_HOSTED_ENTRY_ID = "codeyun-pdf-store"
PDF_HOSTED_DEVICE_ID = "codeyun-pdf-store"
PDF_CONTENT_TOKEN_SCOPE = "pdf-document-content"
PDF_CONTENT_TOKEN_EXPIRE_MINUTES = 15
RESOURCE_ACCESS_SUBJECT_ANONYMOUS = "anonymous"
RESOURCE_ACCESS_SUBJECT_USER = "user"
RESOURCE_ACCESS_ROLE_RANK = {
    "deny": 0,
    "viewer": 1,
    "editor": 2,
    "manager": 3,
}


class PdfFileSelector(BaseModel):
    entry_id: str
    root: str | None = None
    path: str = ""
    absolute_path: str = ""


class PdfLocalImportRequest(BaseModel):
    absolute_path: str


class PdfAccessCapabilities(BaseModel):
    can_read: bool = False
    can_update_state: bool = False
    can_update_page_notes: bool = False
    can_manage_access: bool = False


class PdfResourceAccess(BaseModel):
    role: Literal["none", "deny", "viewer", "editor", "manager"] = "none"
    capabilities: PdfAccessCapabilities = Field(default_factory=PdfAccessCapabilities)


class PdfUserStatePayload(BaseModel):
    current_page: int = 1
    zoom: str = "auto"
    sidebar_open: bool = True
    state_json: dict[str, Any] = Field(default_factory=dict)
    updated_at: float | None = None


class PdfUserStateUpdateRequest(BaseModel):
    current_page: int = Field(default=1, ge=1)
    zoom: str = "auto"
    sidebar_open: bool = True
    state_json: dict[str, Any] = Field(default_factory=dict)


class PdfDocumentDetail(BaseModel):
    id: int
    title: str
    mime_type: str
    size_bytes: int | None = None
    content_hash: str | None = None
    created_at: float
    updated_at: float
    access: PdfResourceAccess
    my_state: PdfUserStatePayload | None = None


class PdfDocumentSummary(PdfDocumentDetail):
    pass


class PdfContentUrlResponse(BaseModel):
    url: str
    expires_in: int


class PdfAccessGrantItem(BaseModel):
    subject_type: Literal["anonymous", "user"]
    subject_key: str
    subject_user_id: int | None = None
    username: str = ""
    nickname: str = ""
    role: Literal["deny", "viewer", "editor", "manager"]


class PdfAccessGrantUpdate(BaseModel):
    subject_type: Literal["anonymous", "user"]
    username: str | None = None
    subject_user_id: int | None = None
    role: Literal["none", "deny", "viewer", "editor", "manager"]


class PdfAccessUpdateRequest(BaseModel):
    grants: list[PdfAccessGrantUpdate] = Field(default_factory=list)


class PdfAccessResponse(BaseModel):
    resource_type: Literal["pdf"] = "pdf"
    resource_id: int
    access: PdfResourceAccess
    grants: list[PdfAccessGrantItem] = Field(default_factory=list)


class PdfPageNotePayload(BaseModel):
    id: str | None = None
    pdf_id: int
    page_number: int
    content_html: str = ""
    exists: bool = False
    can_edit: bool = False
    created_at: float | None = None
    updated_at: float | None = None


class PdfPageNoteUpdateRequest(BaseModel):
    content_html: str = ""


def _get_next_pdf_numeric_id(session: Session, legacy_pk: str) -> int:
    return allocate_resource_id(session, RESOURCE_TYPE_PDF, legacy_pk)


def _require_pdf_numeric_id(document: PdfDocument) -> int:
    numeric_id = int(document.numeric_id or 0)
    if numeric_id <= 0:
        raise HTTPException(status_code=500, detail="PDF 编号缺失")
    return numeric_id


def _pdf_resource_id(document: PdfDocument) -> str:
    return str(_require_pdf_numeric_id(document))


def _pdf_document_ref_candidates(document: PdfDocument) -> list[str]:
    refs = [_pdf_resource_id(document)]
    legacy_id = str(getattr(document, "legacy_id", None) or "").strip()
    if legacy_id and legacy_id not in refs:
        refs.append(legacy_id)
    return refs


def _normalize_resource_role(value: Any) -> Literal["deny", "viewer", "editor", "manager"] | None:
    role = str(value or "").strip()
    if role in RESOURCE_ACCESS_ROLE_RANK:
        return role  # type: ignore[return-value]
    return None


def _build_resource_access(role: str | None, current_user: User | None) -> PdfResourceAccess:
    normalized_role = _normalize_resource_role(role) if role else None
    if normalized_role is None:
        return PdfResourceAccess(role="none")
    rank = RESOURCE_ACCESS_ROLE_RANK[normalized_role]
    can_read = rank >= RESOURCE_ACCESS_ROLE_RANK["viewer"]
    return PdfResourceAccess(
        role=normalized_role,
        capabilities=PdfAccessCapabilities(
            can_read=can_read,
            can_update_state=can_read and current_user is not None,
            can_update_page_notes=can_read and current_user is not None,
            can_manage_access=rank >= RESOURCE_ACCESS_ROLE_RANK["manager"],
        ),
    )


def _resource_role_allows(role: str | None, required_role: Literal["viewer", "editor", "manager"]) -> bool:
    normalized_role = _normalize_resource_role(role)
    if normalized_role is None:
        return False
    return RESOURCE_ACCESS_ROLE_RANK[normalized_role] >= RESOURCE_ACCESS_ROLE_RANK[required_role]


def _build_resource_subject_key(subject_type: str, subject_user_id: int | None = None) -> str:
    if subject_type == RESOURCE_ACCESS_SUBJECT_ANONYMOUS:
        return RESOURCE_ACCESS_SUBJECT_ANONYMOUS
    if subject_type == RESOURCE_ACCESS_SUBJECT_USER and subject_user_id is not None:
        return f"user:{subject_user_id}"
    raise HTTPException(status_code=400, detail="非法权限主体")


def _current_user_subject_keys(current_user: User | None) -> list[str]:
    if current_user is None:
        return [RESOURCE_ACCESS_SUBJECT_ANONYMOUS]
    return [
        _build_resource_subject_key(RESOURCE_ACCESS_SUBJECT_USER, current_user.id),
        RESOURCE_ACCESS_SUBJECT_ANONYMOUS,
    ]


def _fetch_resource_grants(session: Session, resource_id: str) -> list[ResourceAccessGrant]:
    return list(session.exec(
        select(ResourceAccessGrant)
        .where(ResourceAccessGrant.resource_type == PDF_RESOURCE_TYPE)
        .where(ResourceAccessGrant.resource_id == resource_id)
    ).all())


def _resolve_subject_grant_role(
    grants: list[ResourceAccessGrant],
    current_user: User | None,
) -> str | None:
    grant_map = {grant.subject_key: grant for grant in grants}
    for subject_key in _current_user_subject_keys(current_user):
        grant = grant_map.get(subject_key)
        if grant is not None:
            return _normalize_resource_role(grant.role)
    return None


def _is_superuser_or_owner(current_user: User | None, owner_user_id: int | None) -> bool:
    return bool(
        current_user is not None
        and (current_user.is_superuser or owner_user_id == current_user.id)
    )


def _resolve_pdf_resource_access(
    session: Session,
    document: PdfDocument,
    current_user: User | None,
) -> PdfResourceAccess:
    if _is_superuser_or_owner(current_user, document.owner_user_id):
        return _build_resource_access("manager", current_user)
    role = _resolve_subject_grant_role(_fetch_resource_grants(session, _pdf_resource_id(document)), current_user)
    return _build_resource_access(role, current_user)


def _require_resource_access(
    access: PdfResourceAccess,
    required_role: Literal["viewer", "editor", "manager"],
) -> None:
    if not _resource_role_allows(access.role, required_role):
        raise HTTPException(status_code=403, detail="没有该资源权限")


def _get_pdf_by_numeric_id_or_404(session: Session, pdf_id: int) -> PdfDocument:
    document = session.exec(
        select(PdfDocument).where(PdfDocument.numeric_id == pdf_id)
    ).first()
    if document is None:
        raise HTTPException(status_code=404, detail="PDF 不存在")
    return document


def _get_pdf_document_or_404(
    session: Session,
    current_user: User | None,
    pdf_id: int,
    *,
    required_role: Literal["viewer", "editor", "manager"] = "viewer",
) -> tuple[PdfDocument, PdfResourceAccess]:
    document = _get_pdf_by_numeric_id_or_404(session, pdf_id)
    access = _resolve_pdf_resource_access(session, document, current_user)
    _require_resource_access(access, required_role)
    return document, access


def _serialize_user_state(state: PdfUserState | None) -> PdfUserStatePayload | None:
    if state is None:
        return None
    return PdfUserStatePayload(
        current_page=max(int(state.current_page or 1), 1),
        zoom=state.zoom or "auto",
        sidebar_open=bool(state.sidebar_open),
        state_json=dict(state.state_json or {}),
        updated_at=state.updated_at,
    )


def _get_user_state(session: Session, document: PdfDocument, current_user: User | None) -> PdfUserState | None:
    if current_user is None:
        return None
    state = session.exec(
        select(PdfUserState)
        .where(PdfUserState.pdf_document_id.in_(_pdf_document_ref_candidates(document)))
        .where(PdfUserState.user_id == current_user.id)
    ).first()
    public_ref = _pdf_resource_id(document)
    if state is not None and state.pdf_document_id != public_ref:
        state.pdf_document_id = public_ref
        session.add(state)
        session.commit()
        session.refresh(state)
    return state


def _serialize_pdf_detail(
    session: Session,
    document: PdfDocument,
    *,
    current_user: User | None,
    access: PdfResourceAccess,
) -> PdfDocumentDetail:
    return PdfDocumentDetail(
        id=_require_pdf_numeric_id(document),
        title=document.title or "未命名 PDF",
        mime_type=document.mime_type or "application/pdf",
        size_bytes=document.size_bytes,
        content_hash=document.content_hash,
        created_at=document.created_at,
        updated_at=document.updated_at,
        access=access,
        my_state=_serialize_user_state(_get_user_state(session, document, current_user)),
    )


def _serialize_pdf_summary(
    session: Session,
    document: PdfDocument,
    *,
    current_user: User,
    access: PdfResourceAccess,
) -> PdfDocumentSummary:
    detail = _serialize_pdf_detail(session, document, current_user=current_user, access=access)
    return PdfDocumentSummary(**detail.model_dump())


def _get_page_note(
    session: Session,
    document: PdfDocument,
    current_user: User,
    page_number: int,
) -> PdfPageNote | None:
    note = session.exec(
        select(PdfPageNote)
        .where(PdfPageNote.pdf_document_id.in_(_pdf_document_ref_candidates(document)))
        .where(PdfPageNote.user_id == current_user.id)
        .where(PdfPageNote.page_number == page_number)
    ).first()
    public_ref = _pdf_resource_id(document)
    if note is not None and note.pdf_document_id != public_ref:
        note.pdf_document_id = public_ref
        session.add(note)
        session.commit()
        session.refresh(note)
    return note


def _page_note_has_meaningful_content(content_html: str) -> bool:
    raw = str(content_html or "").strip()
    if not raw:
        return False
    if re.search(r"<(img|video|audio|iframe)\b", raw, flags=re.IGNORECASE):
        return True
    text = re.sub(r"<[^>]+>", "", raw)
    text = html_module.unescape(text).replace("\u200b", "").replace("\xa0", " ")
    return bool(text.strip())


def _serialize_page_note(
    document: PdfDocument,
    *,
    page_number: int,
    note: PdfPageNote | None,
    can_edit: bool,
) -> PdfPageNotePayload:
    return PdfPageNotePayload(
        id=note.id if note is not None else None,
        pdf_id=_require_pdf_numeric_id(document),
        page_number=page_number,
        content_html=note.content_html if note is not None else "",
        exists=note is not None,
        can_edit=can_edit,
        created_at=note.created_at if note is not None else None,
        updated_at=note.updated_at if note is not None else None,
    )


def _basename(path: str) -> str:
    value = str(path or "").strip()
    return re.split(r"[\\/]", value)[-1] or "未命名 PDF"


def _looks_like_pdf(path: str, mime_type: str | None = None) -> bool:
    normalized_mime = (mime_type or "").strip().lower()
    return normalized_mime == "application/pdf" or _basename(path).lower().endswith(".pdf")


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _hosted_pdf_root(user_id: int) -> Path:
    root = get_settings().data_dir / "pdf-documents" / f"user_{user_id}"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_pdf_to_hosted_storage(source_path: Path, current_user: User) -> tuple[str, int, str]:
    if not source_path.exists():
        raise HTTPException(status_code=404, detail="PDF 文件不存在")
    if not source_path.is_file():
        raise HTTPException(status_code=400, detail="PDF 来源不是文件")
    if not _looks_like_pdf(os.fspath(source_path)):
        raise HTTPException(status_code=400, detail="只能导入 PDF 文件")

    resolved_source = source_path.resolve(strict=True)
    content_hash = _sha256_file(resolved_source)
    target_path = _hosted_pdf_root(current_user.id) / f"{content_hash}.pdf"
    if not target_path.exists():
        temp_path = target_path.with_suffix(".pdf.tmp")
        shutil.copy2(os.fspath(resolved_source), os.fspath(temp_path))
        temp_path.replace(target_path)
    return os.fspath(target_path.resolve(strict=False)), target_path.stat().st_size, content_hash


def _resolve_hosted_pdf_path(document: PdfDocument) -> Path:
    data_dir = get_settings().data_dir.resolve(strict=False)
    target_path = Path(document.source_absolute_path).expanduser().resolve(strict=False)
    if not _is_relative_to(target_path, data_dir):
        raise HTTPException(status_code=400, detail="PDF 托管路径不在数据目录内")
    return target_path


def _load_device_file(session: Session, device_id: str, absolute_path: str) -> DeviceFile | None:
    return session.exec(
        select(DeviceFile)
        .where(DeviceFile.device_id == device_id)
        .where(DeviceFile.absolute_path == absolute_path)
    ).first()


def _upsert_device_file_from_local_path(
    session: Session,
    *,
    device_id: str,
    absolute_path: str,
    mime_type: str,
) -> DeviceFile:
    target_path, _resolved = resolve_request_path(None, "", absolute_path=absolute_path)
    if not target_path.exists():
        raise HTTPException(status_code=404, detail="文件不存在")
    if not target_path.is_file():
        raise HTTPException(status_code=400, detail="路径不是文件")

    stat_result = target_path.stat()
    resolved_path = os.fspath(target_path.resolve(strict=False))
    record = _load_device_file(session, device_id, resolved_path)
    now = time.time()
    if record is None:
        record = DeviceFile(device_id=device_id, absolute_path=resolved_path, created_at=now)
    record.last_known_path = resolved_path
    record.file_size = stat_result.st_size
    record.modified_at_ms = int(stat_result.st_mtime * 1000)
    record.media_kind = "pdf"
    record.mime_type = mime_type
    record.match_status = "matched"
    record.updated_at = now
    record.last_seen_at = now
    session.add(record)
    session.flush()
    ensure_device_file_resource_identity(session, record)
    return record


def _upsert_device_file_from_remote_path(
    session: Session,
    *,
    device_id: str,
    absolute_path: str,
    mime_type: str,
) -> DeviceFile:
    record = _load_device_file(session, device_id, absolute_path)
    now = time.time()
    if record is None:
        record = DeviceFile(
            device_id=device_id,
            absolute_path=absolute_path,
            last_known_path=absolute_path,
            media_kind="pdf",
            mime_type=mime_type,
            match_status="matched",
            created_at=now,
            updated_at=now,
            last_seen_at=now,
        )
    else:
        record.last_known_path = absolute_path
        record.media_kind = "pdf"
        record.mime_type = mime_type or record.mime_type
        record.match_status = "matched"
        record.updated_at = now
        record.last_seen_at = now
    session.add(record)
    session.flush()
    ensure_device_file_resource_identity(session, record)
    return record


def _resolve_pdf_source(
    session: Session,
    payload: PdfFileSelector,
    current_user: User,
) -> tuple[UserDevice, DeviceFile, str, str, int | None, str | None, str]:
    entry = session.get(UserDevice, payload.entry_id)
    if entry is None or entry.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="设备入口不存在")
    if not entry.is_active:
        raise HTTPException(status_code=400, detail="设备入口已停用")

    raw_path = (payload.absolute_path or payload.path or "").strip()
    if not raw_path:
        raise HTTPException(status_code=400, detail="PDF 路径不能为空")

    guessed_mime = mimetypes.guess_type(_basename(raw_path))[0]
    if not _looks_like_pdf(raw_path, guessed_mime):
        raise HTTPException(status_code=400, detail="只能打开 PDF 文件")

    if entry.mode == "local":
        target_path, _resolved = resolve_request_path(payload.root, payload.path, absolute_path=payload.absolute_path)
        resolved_path = os.fspath(target_path.resolve(strict=False))
        mime_type = mimetypes.guess_type(resolved_path)[0] or guessed_mime
        if not _looks_like_pdf(resolved_path, mime_type):
            raise HTTPException(status_code=400, detail="只能打开 PDF 文件")
        record = _upsert_device_file_from_local_path(
            session,
            device_id=entry.device_id,
            absolute_path=resolved_path,
            mime_type=mime_type or "application/pdf",
        )
        return entry, record, resolved_path, record.mime_type or "application/pdf", record.file_size, record.content_hash, record.hash_algorithm

    record = _upsert_device_file_from_remote_path(
        session,
        device_id=entry.device_id,
        absolute_path=raw_path,
        mime_type=guessed_mime or "application/pdf",
    )
    return entry, record, raw_path, record.mime_type or "application/pdf", record.file_size, record.content_hash, record.hash_algorithm


def _create_or_update_hosted_pdf_document(
    session: Session,
    *,
    current_user: User,
    title: str,
    hosted_path: str,
    size_bytes: int,
    content_hash: str,
    source_device_file_id: int | None = None,
) -> PdfDocument:
    now = time.time()
    document = session.exec(
        select(PdfDocument)
        .where(PdfDocument.owner_user_id == current_user.id)
        .where(PdfDocument.content_hash == content_hash)
    ).first()
    if document is None:
        document = session.exec(
            select(PdfDocument)
            .where(PdfDocument.owner_user_id == current_user.id)
            .where(PdfDocument.source_device_id == PDF_HOSTED_DEVICE_ID)
            .where(PdfDocument.source_absolute_path == hosted_path)
        ).first()

    if document is None:
        legacy_id = generate_sheet_document_id()
        numeric_id = _get_next_pdf_numeric_id(session, legacy_id)
        document = PdfDocument(
            id=numeric_id,
            numeric_id=numeric_id,
            legacy_id=legacy_id,
            owner_user_id=current_user.id,
            created_by_user_id=current_user.id,
            created_at=now,
        )

    document.title = title or document.title or "未命名 PDF"
    document.source_device_file_id = source_device_file_id
    document.source_entry_id = PDF_HOSTED_ENTRY_ID
    document.source_device_id = PDF_HOSTED_DEVICE_ID
    document.source_absolute_path = hosted_path
    document.mime_type = "application/pdf"
    document.size_bytes = size_bytes
    document.content_hash = content_hash
    document.hash_algorithm = "sha256"
    document.updated_by_user_id = current_user.id
    document.updated_at = now
    session.add(document)
    return document


def _save_resource_access_grants(
    session: Session,
    *,
    resource_id: str,
    payload: PdfAccessUpdateRequest,
    current_user: User,
) -> None:
    now = time.time()
    existing = {
        grant.subject_key: grant
        for grant in _fetch_resource_grants(session, resource_id)
    }
    normalized_items: dict[str, tuple[str, int | None, str]] = {}

    for item in payload.grants:
        if item.subject_type == RESOURCE_ACCESS_SUBJECT_ANONYMOUS:
            subject_type = RESOURCE_ACCESS_SUBJECT_ANONYMOUS
            subject_key = RESOURCE_ACCESS_SUBJECT_ANONYMOUS
            subject_user_id = None
        else:
            user: User | None = None
            if item.subject_user_id is not None:
                user = session.get(User, item.subject_user_id)
            if user is None and item.username:
                user = session.exec(select(User).where(User.username == item.username.strip())).first()
            if user is None:
                raise HTTPException(status_code=400, detail="用户不存在")
            subject_type = RESOURCE_ACCESS_SUBJECT_USER
            subject_user_id = user.id
            subject_key = _build_resource_subject_key(subject_type, subject_user_id)

        if item.role == "none":
            continue
        role = _normalize_resource_role(item.role)
        if role is None:
            raise HTTPException(status_code=400, detail="非法权限角色")
        if subject_type == RESOURCE_ACCESS_SUBJECT_ANONYMOUS and role in {"editor", "manager"}:
            raise HTTPException(status_code=400, detail="游客不能拥有编辑权限")
        normalized_items[subject_key] = (subject_type, subject_user_id, role)

    for subject_key, grant in list(existing.items()):
        if subject_key not in normalized_items:
            session.delete(grant)

    for subject_key, (subject_type, subject_user_id, role) in normalized_items.items():
        grant = existing.get(subject_key)
        if grant is None:
            grant = ResourceAccessGrant(
                resource_type=PDF_RESOURCE_TYPE,
                resource_id=resource_id,
                subject_key=subject_key,
                subject_type=subject_type,
                subject_user_id=subject_user_id,
                role=role,
                created_at=now,
                updated_at=now,
                updated_by_user_id=current_user.id,
            )
        else:
            grant.subject_type = subject_type
            grant.subject_user_id = subject_user_id
            grant.role = role
            grant.updated_at = now
            grant.updated_by_user_id = current_user.id
        session.add(grant)


def _serialize_access_grants(session: Session, resource_id: str) -> list[PdfAccessGrantItem]:
    grants = _fetch_resource_grants(session, resource_id)
    user_ids = sorted({
        int(grant.subject_user_id)
        for grant in grants
        if grant.subject_user_id is not None
    })
    users = session.exec(select(User).where(User.id.in_(user_ids))).all() if user_ids else []
    user_map = {user.id: user for user in users}
    result: list[PdfAccessGrantItem] = []
    for grant in sorted(grants, key=lambda item: (item.subject_type, item.subject_key)):
        role = _normalize_resource_role(grant.role)
        if role is None:
            continue
        user = user_map.get(grant.subject_user_id) if grant.subject_user_id is not None else None
        result.append(PdfAccessGrantItem(
            subject_type=grant.subject_type,  # type: ignore[arg-type]
            subject_key=grant.subject_key,
            subject_user_id=grant.subject_user_id,
            username=user.username if user is not None else "",
            nickname=user.nickname if user is not None else "",
            role=role,
        ))
    return result


def _build_access_response(session: Session, document: PdfDocument, access: PdfResourceAccess) -> PdfAccessResponse:
    return PdfAccessResponse(
        resource_id=_require_pdf_numeric_id(document),
        access=access,
        grants=_serialize_access_grants(session, _pdf_resource_id(document)),
    )


def _create_pdf_content_token(document: PdfDocument) -> str:
    return create_access_token(
        {
            "sub": PDF_CONTENT_TOKEN_SCOPE,
            "scope": PDF_CONTENT_TOKEN_SCOPE,
            "pdf_document_id": document.id,
        },
        expires_delta=timedelta(minutes=PDF_CONTENT_TOKEN_EXPIRE_MINUTES),
    )


def _decode_pdf_content_token(session: Session, pdf_id: int, token: str) -> PdfDocument:
    credentials_exception = HTTPException(status_code=401, detail="PDF 内容链接无效")
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError as exc:
        raise credentials_exception from exc
    if payload.get("scope") != PDF_CONTENT_TOKEN_SCOPE or payload.get("sub") != PDF_CONTENT_TOKEN_SCOPE:
        raise credentials_exception
    document = _get_pdf_by_numeric_id_or_404(session, pdf_id)
    if payload.get("pdf_document_id") != document.id:
        raise credentials_exception
    return document


def _content_disposition_filename(filename: str) -> str:
    safe = _basename(filename).replace('"', "")
    return f'inline; filename="{safe}"'


def _stream_remote_pdf_content(entry: UserDevice, document: PdfDocument, request: Request) -> StreamingResponse:
    if entry.mode != "remote":
        raise HTTPException(status_code=400, detail="设备入口不是远程入口")
    if not entry.server_url:
        raise HTTPException(status_code=400, detail="远程设备入口没有地址")

    headers = {
        "Authorization": f"Bearer {entry.token}",
        "X-Device-Token": entry.token,
    }
    if request.headers.get("range"):
        headers["Range"] = request.headers["range"]
    if request.headers.get("if-range"):
        headers["If-Range"] = request.headers["if-range"]

    try:
        response = requests.get(
            f"{entry.server_url.rstrip('/')}/api/fs/content",
            params={"absolute_path": document.source_absolute_path},
            headers=headers,
            timeout=30,
            stream=True,
        )
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"远程设备不可达：{exc}") from exc

    if response.status_code >= 400:
        detail = response.text.strip() or f"远程设备返回 HTTP {response.status_code}"
        response.close()
        raise HTTPException(status_code=response.status_code, detail=detail)

    forwarded_headers = {
        key: value
        for key, value in response.headers.items()
        if key.lower() in {"accept-ranges", "cache-control", "content-length", "content-range", "etag", "last-modified"}
    }
    forwarded_headers["content-disposition"] = _content_disposition_filename(document.title or "document.pdf")
    return StreamingResponse(
        response.iter_content(chunk_size=64 * 1024),
        status_code=response.status_code,
        media_type=document.mime_type or "application/pdf",
        headers=forwarded_headers,
        background=BackgroundTask(response.close),
    )


@router.post("/from-device-file", response_model=PdfDocumentDetail)
def create_pdf_document_from_device_file(
    payload: PdfFileSelector,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
):
    entry, device_file, absolute_path, mime_type, size_bytes, content_hash, hash_algorithm = _resolve_pdf_source(
        session,
        payload,
        current_user,
    )
    if entry.mode == "local":
        hosted_path, hosted_size_bytes, hosted_content_hash = _copy_pdf_to_hosted_storage(Path(absolute_path), current_user)
        document = _create_or_update_hosted_pdf_document(
            session,
            current_user=current_user,
            title=_basename(absolute_path),
            hosted_path=hosted_path,
            size_bytes=hosted_size_bytes,
            content_hash=hosted_content_hash,
            source_device_file_id=device_file.id,
        )
        session.commit()
        session.refresh(document)
        access = _resolve_pdf_resource_access(session, document, current_user)
        return _serialize_pdf_detail(session, document, current_user=current_user, access=access)

    now = time.time()
    document = session.exec(
        select(PdfDocument)
        .where(PdfDocument.owner_user_id == current_user.id)
        .where(PdfDocument.source_device_id == entry.device_id)
        .where(PdfDocument.source_absolute_path == absolute_path)
    ).first()

    title = _basename(absolute_path)
    if document is None:
        legacy_id = generate_sheet_document_id()
        numeric_id = _get_next_pdf_numeric_id(session, legacy_id)
        document = PdfDocument(
            id=numeric_id,
            numeric_id=numeric_id,
            legacy_id=legacy_id,
            title=title,
            source_device_file_id=device_file.id,
            source_entry_id=entry.entry_id,
            source_device_id=entry.device_id,
            source_absolute_path=absolute_path,
            mime_type=mime_type or "application/pdf",
            size_bytes=size_bytes,
            content_hash=content_hash,
            hash_algorithm=hash_algorithm or "sha256",
            owner_user_id=current_user.id,
            created_by_user_id=current_user.id,
            updated_by_user_id=current_user.id,
            created_at=now,
            updated_at=now,
        )
    else:
        document.source_device_file_id = device_file.id
        document.source_entry_id = entry.entry_id
        document.title = document.title or title
        document.mime_type = mime_type or document.mime_type or "application/pdf"
        document.size_bytes = size_bytes if size_bytes is not None else document.size_bytes
        document.content_hash = content_hash or document.content_hash
        document.hash_algorithm = hash_algorithm or document.hash_algorithm or "sha256"
        document.updated_by_user_id = current_user.id
        document.updated_at = now

    session.add(document)
    session.commit()
    session.refresh(document)
    access = _resolve_pdf_resource_access(session, document, current_user)
    return _serialize_pdf_detail(session, document, current_user=current_user, access=access)


@router.post("/import-local-path", response_model=PdfDocumentDetail)
def import_pdf_document_from_local_path(
    payload: PdfLocalImportRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
):
    raw_path = str(payload.absolute_path or "").strip()
    if not raw_path:
        raise HTTPException(status_code=400, detail="PDF 路径不能为空")
    source_path = Path(raw_path).expanduser()
    if not source_path.is_absolute():
        raise HTTPException(status_code=400, detail="必须使用本机绝对路径")
    hosted_path, size_bytes, content_hash = _copy_pdf_to_hosted_storage(source_path, current_user)
    document = _create_or_update_hosted_pdf_document(
        session,
        current_user=current_user,
        title=_basename(os.fspath(source_path)),
        hosted_path=hosted_path,
        size_bytes=size_bytes,
        content_hash=content_hash,
    )
    session.commit()
    session.refresh(document)
    access = _resolve_pdf_resource_access(session, document, current_user)
    return _serialize_pdf_detail(session, document, current_user=current_user, access=access)


@router.get("", response_model=list[PdfDocumentSummary])
def list_pdf_documents(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
):
    if current_user.is_superuser:
        candidate_documents = session.exec(select(PdfDocument)).all()
    else:
        owned_documents = session.exec(
            select(PdfDocument).where(PdfDocument.owner_user_id == current_user.id)
        ).all()
        subject_keys = _current_user_subject_keys(current_user)
        grants = session.exec(
            select(ResourceAccessGrant)
            .where(ResourceAccessGrant.resource_type == PDF_RESOURCE_TYPE)
            .where(ResourceAccessGrant.subject_key.in_(subject_keys))
        ).all()
        granted_pdf_ids = sorted({int(grant.resource_id) for grant in grants if str(grant.resource_id).isdigit()})
        granted_documents = session.exec(
            select(PdfDocument).where(PdfDocument.numeric_id.in_(granted_pdf_ids))
        ).all() if granted_pdf_ids else []
        document_map = {document.id: document for document in [*owned_documents, *granted_documents]}
        candidate_documents = list(document_map.values())

    document_access_items = [
        (document, _resolve_pdf_resource_access(session, document, current_user))
        for document in candidate_documents
    ]
    document_access_items = [
        (document, access)
        for document, access in document_access_items
        if access.capabilities.can_read
    ]
    document_access_items.sort(
        key=lambda item: (float(item[0].updated_at or 0.0), float(item[0].created_at or 0.0)),
        reverse=True,
    )
    return [
        _serialize_pdf_summary(session, document, current_user=current_user, access=access)
        for document, access in document_access_items
    ]


@router.get("/{pdf_id}", response_model=PdfDocumentDetail)
def get_pdf_document(
    pdf_id: int,
    session: Session = Depends(get_session),
    current_user: User | None = Depends(get_optional_current_user_from_token),
):
    document, access = _get_pdf_document_or_404(session, current_user, pdf_id, required_role="viewer")
    return _serialize_pdf_detail(session, document, current_user=current_user, access=access)


@router.post("/{pdf_id}/content-url", response_model=PdfContentUrlResponse)
def get_pdf_content_url(
    pdf_id: int,
    session: Session = Depends(get_session),
    current_user: User | None = Depends(get_optional_current_user_from_token),
):
    document, _access = _get_pdf_document_or_404(session, current_user, pdf_id, required_role="viewer")
    token = _create_pdf_content_token(document)
    return PdfContentUrlResponse(
        url=f"/api/pdf-documents/{_require_pdf_numeric_id(document)}/content?token={token}",
        expires_in=PDF_CONTENT_TOKEN_EXPIRE_MINUTES * 60,
    )


@router.get("/{pdf_id}/content")
def get_pdf_content(
    pdf_id: int,
    request: Request,
    token: str = Query(...),
    session: Session = Depends(get_session),
):
    document = _decode_pdf_content_token(session, pdf_id, token)
    if document.source_entry_id == PDF_HOSTED_ENTRY_ID:
        target_path = _resolve_hosted_pdf_path(document)
        if not target_path.exists():
            raise HTTPException(status_code=404, detail="PDF 托管文件不存在")
        if not target_path.is_file():
            raise HTTPException(status_code=400, detail="PDF 托管路径不是文件")
        return FileResponse(
            path=os.fspath(target_path),
            media_type=document.mime_type or "application/pdf",
            filename=document.title or target_path.name,
            content_disposition_type="inline",
        )

    entry = session.get(UserDevice, document.source_entry_id)
    if entry is None or entry.user_id != document.owner_user_id or not entry.is_active:
        raise HTTPException(status_code=404, detail="PDF 来源设备入口不可用")

    if entry.mode == "remote":
        return _stream_remote_pdf_content(entry, document, request)

    target_path, _resolved = resolve_request_path(None, "", absolute_path=document.source_absolute_path)
    if not target_path.exists():
        raise HTTPException(status_code=404, detail="PDF 文件不存在")
    if not target_path.is_file():
        raise HTTPException(status_code=400, detail="PDF 来源不是文件")
    return FileResponse(
        path=os.fspath(target_path),
        media_type=document.mime_type or "application/pdf",
        filename=document.title or target_path.name,
        content_disposition_type="inline",
    )


@router.put("/{pdf_id}/my-state", response_model=PdfUserStatePayload)
def update_pdf_user_state(
    pdf_id: int,
    payload: PdfUserStateUpdateRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
):
    document, _access = _get_pdf_document_or_404(session, current_user, pdf_id, required_role="viewer")
    state = _get_user_state(session, document, current_user)
    now = time.time()
    if state is None:
        state = PdfUserState(
            pdf_document_id=_pdf_resource_id(document),
            user_id=current_user.id,
            created_at=now,
        )
    state.current_page = max(int(payload.current_page or 1), 1)
    state.zoom = (payload.zoom or "auto").strip() or "auto"
    state.sidebar_open = bool(payload.sidebar_open)
    state.state_json = dict(payload.state_json or {})
    state.updated_at = now
    session.add(state)
    session.commit()
    session.refresh(state)
    return _serialize_user_state(state)


@router.get("/{pdf_id}/page-notes/{page_number}", response_model=PdfPageNotePayload)
def get_pdf_page_note(
    pdf_id: int,
    page_number: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
):
    document, access = _get_pdf_document_or_404(session, current_user, pdf_id, required_role="viewer")
    normalized_page = max(int(page_number or 1), 1)
    note = _get_page_note(session, document, current_user, normalized_page)
    return _serialize_page_note(
        document,
        page_number=normalized_page,
        note=note,
        can_edit=access.capabilities.can_update_page_notes,
    )


@router.put("/{pdf_id}/page-notes/{page_number}", response_model=PdfPageNotePayload)
def update_pdf_page_note(
    pdf_id: int,
    page_number: int,
    payload: PdfPageNoteUpdateRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
):
    document, access = _get_pdf_document_or_404(session, current_user, pdf_id, required_role="viewer")
    normalized_page = max(int(page_number or 1), 1)
    content_html = str(payload.content_html or "")
    note = _get_page_note(session, document, current_user, normalized_page)

    if not _page_note_has_meaningful_content(content_html):
        if note is not None:
            session.delete(note)
            session.commit()
        return _serialize_page_note(
            document,
            page_number=normalized_page,
            note=None,
            can_edit=access.capabilities.can_update_page_notes,
        )

    now = time.time()
    if note is None:
        note = PdfPageNote(
            pdf_document_id=_pdf_resource_id(document),
            user_id=current_user.id,
            page_number=normalized_page,
            created_at=now,
        )
    note.content_html = content_html
    note.updated_at = now
    session.add(note)
    session.commit()
    session.refresh(note)
    return _serialize_page_note(
        document,
        page_number=normalized_page,
        note=note,
        can_edit=access.capabilities.can_update_page_notes,
    )


@router.get("/{pdf_id}/access", response_model=PdfAccessResponse)
def get_pdf_access(
    pdf_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
):
    document, access = _get_pdf_document_or_404(session, current_user, pdf_id, required_role="manager")
    return _build_access_response(session, document, access)


@router.put("/{pdf_id}/access", response_model=PdfAccessResponse)
def update_pdf_access(
    pdf_id: int,
    payload: PdfAccessUpdateRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
):
    document, _access = _get_pdf_document_or_404(session, current_user, pdf_id, required_role="manager")
    _save_resource_access_grants(
        session,
        resource_id=_pdf_resource_id(document),
        payload=payload,
        current_user=current_user,
    )
    session.commit()
    access = _resolve_pdf_resource_access(session, document, current_user)
    return _build_access_response(session, document, access)
