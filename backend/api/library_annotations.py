from __future__ import annotations

import time
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field, model_validator
from sqlmodel import Session, select

from backend.core.access.auth import get_current_active_user
from backend.db import get_session
from backend.models import LibraryAnnotation, LibraryBookAsset, User


router = APIRouter()
RICH_TEXT_RESOURCE_TYPE = "rich-text"
SKILL_BOOK_RESOURCE_TYPE = "skill-book"
PDF_RESOURCE_TYPE = "pdf"
SUPPORTED_RESOURCE_TYPES = {
    RICH_TEXT_RESOURCE_TYPE,
    SKILL_BOOK_RESOURCE_TYPE,
    PDF_RESOURCE_TYPE,
}
ANNOTATION_COLORS = {"yellow", "green", "blue", "pink"}


class LibraryAnnotationCreate(BaseModel):
    resource_type: str = Field(default=RICH_TEXT_RESOURCE_TYPE, max_length=40)
    resource_id: str = Field(min_length=1, max_length=160)
    chapter_id: str = Field(default="", max_length=512)
    kind: Literal["highlight", "comment"] = "highlight"
    color: str = Field(default="yellow", max_length=32)
    quote_text: str = Field(min_length=1, max_length=10_000)
    prefix_text: str = Field(default="", max_length=500)
    suffix_text: str = Field(default="", max_length=500)
    start_offset: int = Field(default=0, ge=0)
    end_offset: int = Field(default=0, ge=0)
    source_revision: str = Field(default="", max_length=128)
    comment_text: str = Field(default="", max_length=50_000)
    position_json: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_offsets(self):
        if self.end_offset < self.start_offset:
            raise ValueError("end_offset must be greater than or equal to start_offset")
        return self


class LibraryAnnotationUpdate(BaseModel):
    color: str | None = Field(default=None, max_length=32)
    comment_text: str | None = Field(default=None, max_length=50_000)


class LibraryAnnotationPayload(BaseModel):
    id: str
    resource_type: str
    resource_id: str
    chapter_id: str
    kind: Literal["highlight", "comment"]
    color: str
    quote_text: str
    prefix_text: str
    suffix_text: str
    start_offset: int
    end_offset: int
    source_revision: str
    comment_text: str
    position_json: dict[str, Any]
    created_at: float
    updated_at: float


def _require_user_id(current_user: User) -> int:
    if current_user.id is None:
        raise HTTPException(status_code=403, detail="当前用户无效")
    return current_user.id


def _require_annotatable_resource(
    session: Session,
    current_user: User,
    resource_type: str,
    resource_id: str,
) -> None:
    if resource_type not in SUPPORTED_RESOURCE_TYPES:
        raise HTTPException(status_code=400, detail="当前资源类型暂不支持批注")
    if resource_type == SKILL_BOOK_RESOURCE_TYPE:
        from backend.api.skill_books import (
            SKILL_BOOK_ASSET_ID,
            _get_local_skill_book_context,
        )

        if resource_id != SKILL_BOOK_ASSET_ID:
            raise HTTPException(status_code=404, detail="图书不存在")
        _get_local_skill_book_context(session, current_user)
        return
    if resource_type == PDF_RESOURCE_TYPE:
        from backend.api.pdf_documents import _resolve_pdf_resource_access
        from backend.models import PdfDocument

        try:
            pdf_id = int(resource_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="PDF 不存在") from exc
        document = session.get(PdfDocument, pdf_id)
        if (
            document is None
            or not _resolve_pdf_resource_access(
                session,
                document,
                current_user,
            ).capabilities.can_read
        ):
            raise HTTPException(status_code=404, detail="PDF 不存在")
        return
    asset = session.get(LibraryBookAsset, resource_id)
    if asset is None or asset.owner_user_id != _require_user_id(current_user):
        raise HTTPException(status_code=404, detail="图书不存在")


def _normalize_color(value: str) -> str:
    color = value.strip().lower()
    return color if color in ANNOTATION_COLORS else "yellow"


def _payload(annotation: LibraryAnnotation) -> LibraryAnnotationPayload:
    return LibraryAnnotationPayload(
        id=annotation.id,
        resource_type=annotation.resource_type,
        resource_id=annotation.resource_id,
        chapter_id=annotation.chapter_id,
        kind="comment" if annotation.kind == "comment" else "highlight",
        color=annotation.color,
        quote_text=annotation.quote_text,
        prefix_text=annotation.prefix_text,
        suffix_text=annotation.suffix_text,
        start_offset=annotation.start_offset,
        end_offset=annotation.end_offset,
        source_revision=annotation.source_revision,
        comment_text=annotation.comment_text,
        position_json=dict(annotation.position_json or {}),
        created_at=annotation.created_at,
        updated_at=annotation.updated_at,
    )


def _owned_annotation(
    session: Session,
    current_user: User,
    annotation_id: str,
) -> LibraryAnnotation:
    annotation = session.get(LibraryAnnotation, annotation_id)
    if annotation is None or annotation.user_id != _require_user_id(current_user):
        raise HTTPException(status_code=404, detail="批注不存在")
    return annotation


@router.get("", response_model=list[LibraryAnnotationPayload])
def list_annotations(
    resource_type: str = Query(default=RICH_TEXT_RESOURCE_TYPE, max_length=40),
    resource_id: str = Query(min_length=1, max_length=160),
    chapter_id: str | None = Query(default=None, max_length=512),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
):
    _require_annotatable_resource(session, current_user, resource_type, resource_id)
    statement = (
        select(LibraryAnnotation)
        .where(LibraryAnnotation.user_id == _require_user_id(current_user))
        .where(LibraryAnnotation.resource_type == resource_type)
        .where(LibraryAnnotation.resource_id == resource_id)
        .order_by(LibraryAnnotation.chapter_id, LibraryAnnotation.start_offset, LibraryAnnotation.created_at)
    )
    if chapter_id is not None:
        statement = statement.where(LibraryAnnotation.chapter_id == chapter_id)
    return [_payload(item) for item in session.exec(statement).all()]


@router.post("", response_model=LibraryAnnotationPayload, status_code=201)
def create_annotation(
    payload: LibraryAnnotationCreate,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
):
    _require_annotatable_resource(
        session,
        current_user,
        payload.resource_type,
        payload.resource_id,
    )
    now = time.time()
    annotation = LibraryAnnotation(
        user_id=_require_user_id(current_user),
        resource_type=payload.resource_type,
        resource_id=payload.resource_id,
        chapter_id=payload.chapter_id,
        kind=payload.kind,
        color=_normalize_color(payload.color),
        quote_text=payload.quote_text,
        prefix_text=payload.prefix_text,
        suffix_text=payload.suffix_text,
        start_offset=payload.start_offset,
        end_offset=payload.end_offset,
        source_revision=payload.source_revision,
        comment_text=payload.comment_text.strip(),
        position_json=payload.position_json,
        created_at=now,
        updated_at=now,
    )
    session.add(annotation)
    session.commit()
    session.refresh(annotation)
    return _payload(annotation)


@router.patch("/{annotation_id}", response_model=LibraryAnnotationPayload)
def update_annotation(
    annotation_id: str,
    payload: LibraryAnnotationUpdate,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
):
    annotation = _owned_annotation(session, current_user, annotation_id)
    if payload.color is not None:
        annotation.color = _normalize_color(payload.color)
    if payload.comment_text is not None:
        annotation.comment_text = payload.comment_text.strip()
        annotation.kind = "comment" if annotation.comment_text else "highlight"
    annotation.updated_at = time.time()
    session.add(annotation)
    session.commit()
    session.refresh(annotation)
    return _payload(annotation)


@router.delete("/{annotation_id}", status_code=204)
def delete_annotation(
    annotation_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
):
    annotation = _owned_annotation(session, current_user, annotation_id)
    session.delete(annotation)
    session.commit()
    return Response(status_code=204)
