from __future__ import annotations

import time
from typing import Any

from sqlmodel import Session, func, select

from backend.models import DeviceFile, DocumentAsset, NoteNode, PdfDocument, ResourceIdentity, SheetDocument, WorkbookDocument


RESOURCE_TYPE_SHEET = "sheet"
RESOURCE_TYPE_WORKBOOK = "workbook"
RESOURCE_TYPE_PDF = "pdf"
RESOURCE_TYPE_NOTE = "note"
RESOURCE_TYPE_DOCUMENT_ASSET = "document_asset"
RESOURCE_TYPE_DEVICE_FILE = "device_file"

RESOURCE_TYPES = {
    RESOURCE_TYPE_SHEET,
    RESOURCE_TYPE_WORKBOOK,
    RESOURCE_TYPE_PDF,
    RESOURCE_TYPE_NOTE,
    RESOURCE_TYPE_DOCUMENT_ASSET,
    RESOURCE_TYPE_DEVICE_FILE,
}


def _normalize_resource_type(resource_type: str) -> str:
    normalized = str(resource_type or "").strip()
    if normalized not in RESOURCE_TYPES:
        raise ValueError(f"unsupported resource type: {resource_type}")
    return normalized


def _normalize_legacy_pk(legacy_pk: Any) -> str:
    normalized = str(legacy_pk or "").strip()
    if not normalized:
        raise ValueError("resource legacy_pk is required")
    return normalized


def _first_scalar(value: Any) -> Any:
    if value is None:
        return None
    mapping = getattr(value, "_mapping", None)
    if mapping:
        values = list(mapping.values())
        return values[0] if values else None
    if isinstance(value, (list, tuple)):
        return value[0] if value else None
    try:
        return value[0]
    except (TypeError, KeyError, IndexError):
        return value


def _table_numeric_max(session: Session, model: Any) -> int:
    try:
        row = session.exec(select(func.coalesce(func.max(model.numeric_id), 0))).first()
    except Exception:
        return 0
    return max(int(_first_scalar(row) or 0), 0)


def get_current_global_resource_id_max(session: Session) -> int:
    identity_row = session.exec(select(func.coalesce(func.max(ResourceIdentity.id), 0))).first()
    return max(
        int(_first_scalar(identity_row) or 0),
        _table_numeric_max(session, SheetDocument),
        _table_numeric_max(session, PdfDocument),
        _table_numeric_max(session, NoteNode),
        _table_numeric_max(session, DocumentAsset),
        _table_numeric_max(session, DeviceFile),
    )


RESOURCE_NUMERIC_MODELS = {
    RESOURCE_TYPE_SHEET: SheetDocument,
    RESOURCE_TYPE_PDF: PdfDocument,
    RESOURCE_TYPE_NOTE: NoteNode,
    RESOURCE_TYPE_DOCUMENT_ASSET: DocumentAsset,
    RESOURCE_TYPE_DEVICE_FILE: DeviceFile,
}


def _resource_id_is_available(
    session: Session,
    resource_id: int,
    *,
    resource_type: str | None = None,
    legacy_pk: str | None = None,
) -> bool:
    normalized_id = int(resource_id)
    if session.get(ResourceIdentity, normalized_id) is not None:
        return False
    owner_model = RESOURCE_NUMERIC_MODELS.get(str(resource_type or ""))
    normalized_legacy_pk = str(legacy_pk or "").strip()
    for model in (SheetDocument, PdfDocument, NoteNode, DocumentAsset, DeviceFile):
        try:
            existing = session.exec(select(model.id).where(model.numeric_id == normalized_id).limit(1)).first()
        except Exception:
            continue
        if existing is not None:
            if model is owner_model and str(existing or "").strip() == normalized_legacy_pk:
                continue
            if model is owner_model and hasattr(model, "legacy_id"):
                try:
                    legacy_pk_row = session.exec(
                        select(model.legacy_id).where(model.numeric_id == normalized_id).limit(1)
                    ).first()
                except Exception:
                    legacy_pk_row = None
                if str(_first_scalar(legacy_pk_row) or "").strip() == normalized_legacy_pk:
                    continue
            return False
    return True


def allocate_resource_id(
    session: Session,
    resource_type: str,
    legacy_pk: Any,
    *,
    preferred_id: int | None = None,
) -> int:
    normalized_type = _normalize_resource_type(resource_type)
    normalized_legacy_pk = _normalize_legacy_pk(legacy_pk)

    existing = session.exec(
        select(ResourceIdentity)
        .where(ResourceIdentity.resource_type == normalized_type)
        .where(ResourceIdentity.legacy_pk == normalized_legacy_pk)
    ).first()
    if existing is not None:
        return int(existing.id)

    resource_id = int(preferred_id or 0)
    if resource_id <= 0 or not _resource_id_is_available(
        session,
        resource_id,
        resource_type=normalized_type,
        legacy_pk=normalized_legacy_pk,
    ):
        resource_id = get_current_global_resource_id_max(session) + 1

    now = time.time()
    identity = ResourceIdentity(
        id=resource_id,
        resource_type=normalized_type,
        legacy_pk=normalized_legacy_pk,
        created_at=now,
        updated_at=now,
    )
    session.add(identity)
    return resource_id


def ensure_resource_identity(
    session: Session,
    resource_type: str,
    legacy_pk: Any,
    public_id: int | None,
) -> int:
    return allocate_resource_id(
        session,
        resource_type,
        legacy_pk,
        preferred_id=int(public_id or 0) if public_id else None,
    )


def get_public_resource_id(session: Session, resource_type: str, legacy_pk: Any, numeric_id: int | None = None) -> int:
    return ensure_resource_identity(session, resource_type, legacy_pk, numeric_id)
