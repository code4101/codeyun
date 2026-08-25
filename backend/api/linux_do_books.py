from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Literal

from bs4 import BeautifulSoup, Tag
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile, status
from fastapi.responses import FileResponse
from PIL import Image
from pydantic import BaseModel, Field, HttpUrl, field_validator
from sqlmodel import Session, select

from backend.core.access.auth import get_current_active_user
from backend.core.settings import get_settings
from backend.core.library.linux_do_book import (
    LinuxDoBookDocument,
    LinuxDoBookError,
    LinuxDoTocItem,
    import_linux_do_book,
    normalize_discussion_numbering,
)
from backend.core.library.dynamic_book_pagination import (
    dynamic_book_html_page_count,
    dynamic_book_page_count,
)
from backend.core.library.book_metadata import normalize_book_start_date
from backend.core.library.ebook_import import EbookImportError, ImportedEbook, import_ebook, supported_ebook_filename
from backend.core.temp_paths import codeyun_temp_root
from backend.db import get_session
from backend.models import (
    LibraryBookAsset,
    LibraryAnnotation,
    LibraryBookPlacement,
    LibraryReadingState,
    LibraryFolder,
    PdfBookshelfPlacement,
    PdfLibraryBookshelf,
    User,
)


router = APIRouter()
BOOK_RESOURCE_TYPE = "linux-do-book"
STORAGE_VERSION = 1
EBOOK_UPLOAD_MAX_BYTES = 1024 * 1024 * 1024


class LinuxDoBookImportRequest(BaseModel):
    url: HttpUrl
    bookshelf_id: str | None = None


class LinuxDoBookPlacementPayload(BaseModel):
    bookshelf_id: str
    shelf_index: int = 0
    position_index: int = 0
    orientation: Literal["spine_vertical", "spine_horizontal", "cover_front"] = "spine_vertical"
    folder_id: str | None = None
    article_reading_mode: Literal["scroll", "paginated"] | None = None


class LinuxDoBookPlacementUpdate(LinuxDoBookPlacementPayload):
    pass


class LinuxDoBookMetadataUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=240)
    author: str = Field(default="", max_length=160)
    start_date: str = Field(default="", max_length=10)
    cover_color: str = Field(default="#294f6d", min_length=1, max_length=32)

    @field_validator("start_date")
    @classmethod
    def validate_start_date(cls, value: str) -> str:
        return normalize_book_start_date(value)


class LinuxDoBookCapabilities(BaseModel):
    can_annotate: bool = True
    can_edit_content: bool = False
    edit_mode: Literal["html", "source"] | None = None
    source_policy: Literal["owned", "derived", "external"] = "derived"


class LinuxDoBookReadingState(BaseModel):
    book_id: str
    chapter_id: str = ""
    character_offset: int = 0
    chapter_revision: str = ""
    current_page: int = 1
    page_count: int = 1
    updated_at: float | None = None


class LinuxDoBookSummary(BaseModel):
    id: str
    topic_id: int
    title: str
    author: str
    start_date: str = ""
    source_url: str
    book_kind: str = "linux-do"
    format: str = "html"
    original_filename: str = ""
    cover_color: str
    revision: str
    toc_count: int
    post_count: int
    selected_reply_count: int
    estimated_page_count: int
    imported_at: float
    updated_at: float
    latest_issue: int | None = None
    capabilities: LinuxDoBookCapabilities = Field(default_factory=LinuxDoBookCapabilities)
    bookshelf_placement: LinuxDoBookPlacementPayload
    reading_state: LinuxDoBookReadingState | None = None


class LinuxDoBookContent(LinuxDoBookSummary):
    content_html: str
    content_markdown: str
    toc: list[LinuxDoTocItem]


class LinuxDoBookReadingStateUpdate(BaseModel):
    chapter_id: str = ""
    character_offset: int = Field(default=0, ge=0)
    chapter_revision: str = ""
    current_page: int = Field(default=1, ge=1)
    page_count: int = Field(default=1, ge=1)


class HtmlBookArticleUpdate(BaseModel):
    content_html: str = Field(min_length=1, max_length=2_000_000)
    revision: str = Field(min_length=1, max_length=128)


class EbookSourceContent(BaseModel):
    content: str
    revision: str
    format: Literal["html", "markdown", "text"]
    filename: str


class EbookSourceUpdate(BaseModel):
    content: str = Field(max_length=10_000_000)
    revision: str = Field(min_length=1, max_length=128)


def _storage_path(owner_user_id: int, topic_id: int) -> Path:
    return (
        get_settings().data_dir
        / "library-books"
        / f"user_{owner_user_id}"
        / "linux-do"
        / str(topic_id)
        / "book.json"
    )


def _asset_storage_dir(asset: LibraryBookAsset) -> Path:
    topic_id = int((asset.metadata_json or {}).get("topic_id") or 0)
    return _storage_path(asset.owner_user_id, topic_id).parent


def _write_imported_ebook(asset: LibraryBookAsset, document: LinuxDoBookDocument, imported: ImportedEbook, source_path: Path) -> None:
    storage_dir = _asset_storage_dir(asset)
    resources_dir = storage_dir / "resources"
    shutil.rmtree(resources_dir, ignore_errors=True)
    resources_dir.mkdir(parents=True, exist_ok=True)
    for resource_name, (payload, _media_type) in imported.resources.items():
        (resources_dir / Path(resource_name).name).write_bytes(payload)
    source_extension = Path(str((asset.metadata_json or {}).get("original_filename") or source_path.name)).suffix.lower()
    shutil.copyfile(source_path, storage_dir / f"source{source_extension}")
    _write_document(asset.owner_user_id, document)


def _cover_average_color(imported: ImportedEbook) -> str:
    if not imported.cover_resource_name or imported.cover_resource_name not in imported.resources:
        return "#315f53"
    try:
        payload, _media_type = imported.resources[imported.cover_resource_name]
        with Image.open(io.BytesIO(payload)) as image:
            red, green, blue = image.convert("RGB").resize((1, 1), Image.Resampling.BOX).getpixel((0, 0))
        return f"#{int(red):02x}{int(green):02x}{int(blue):02x}"
    except Exception:
        return "#315f53"


def _write_document(owner_user_id: int, document: LinuxDoBookDocument) -> None:
    path = _storage_path(owner_user_id, document.topic_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f".{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(document.to_dict(), ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _read_document(asset: LibraryBookAsset) -> LinuxDoBookDocument:
    topic_id = int((asset.metadata_json or {}).get("topic_id") or 0)
    path = _storage_path(asset.owner_user_id, topic_id)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["toc"] = [LinuxDoTocItem(**item) for item in payload.get("toc") or []]
        document = normalize_discussion_numbering(LinuxDoBookDocument(**payload))
        document.estimated_page_count = (
            dynamic_book_html_page_count(document.content_html)
            if document.content_html
            else dynamic_book_page_count(document.content_markdown)
        )
        return document
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise HTTPException(status_code=404, detail="电子书内容文件不存在或已损坏") from exc


def _read_summary_document(asset: LibraryBookAsset) -> LinuxDoBookDocument:
    metadata = dict(asset.metadata_json or {})
    estimated_page_count = int(
        metadata.get("estimated_page_count")
        or metadata.get("virtual_page_count")
        or 1
    )
    return LinuxDoBookDocument(
        topic_id=int(metadata.get("topic_id") or 0),
        title=asset.title,
        author=asset.author,
        source_url=str(metadata.get("source_url") or ""),
        content_html="",
        content_markdown="",
        toc=[],
        revision=str(metadata.get("revision") or ""),
        post_count=int(metadata.get("post_count") or 0),
        selected_reply_count=int(metadata.get("selected_reply_count") or 0),
        imported_at=float(metadata.get("imported_at") or asset.created_at),
        estimated_page_count=max(1, estimated_page_count),
    )


def _ensure_bookshelf(session: Session, user: User, requested_id: str | None) -> PdfLibraryBookshelf:
    if user.id is None:
        raise HTTPException(status_code=403, detail="当前用户无效")
    shelves = list(session.exec(
        select(PdfLibraryBookshelf)
        .where(PdfLibraryBookshelf.user_id == user.id)
        .order_by(PdfLibraryBookshelf.sort_index, PdfLibraryBookshelf.created_at)
    ).all())
    if requested_id:
        requested = next((shelf for shelf in shelves if shelf.id == requested_id), None)
        if requested is None:
            raise HTTPException(status_code=404, detail="目标书柜不存在")
        return requested
    if shelves:
        return shelves[0]
    now = time.time()
    for sort_index, name in enumerate(("1", "2", "4", "5")):
        session.add(PdfLibraryBookshelf(
            user_id=user.id,
            name=name,
            sort_index=sort_index,
            created_at=now,
            updated_at=now,
        ))
    session.commit()
    return session.exec(
        select(PdfLibraryBookshelf)
        .where(PdfLibraryBookshelf.user_id == user.id)
        .order_by(PdfLibraryBookshelf.sort_index, PdfLibraryBookshelf.created_at)
    ).first()


def _next_position(session: Session, user_id: int, bookshelf_id: str) -> int:
    positions = [
        *session.exec(
            select(PdfBookshelfPlacement.position_index)
            .where(PdfBookshelfPlacement.user_id == user_id)
            .where(PdfBookshelfPlacement.bookshelf_id == bookshelf_id)
            .where(PdfBookshelfPlacement.shelf_index == 0)
        ).all(),
        *session.exec(
            select(LibraryBookPlacement.position_index)
            .where(LibraryBookPlacement.user_id == user_id)
            .where(LibraryBookPlacement.bookshelf_id == bookshelf_id)
            .where(LibraryBookPlacement.shelf_index == 0)
        ).all(),
    ]
    return max([int(value) for value in positions], default=-1) + 1


def _get_owned_asset(session: Session, current_user: User, book_id: str) -> LibraryBookAsset:
    asset = session.get(LibraryBookAsset, book_id)
    if asset is None or asset.resource_type != BOOK_RESOURCE_TYPE or asset.owner_user_id != current_user.id:
        raise HTTPException(status_code=404, detail="电子书不存在")
    return asset


def _placement_for(session: Session, asset: LibraryBookAsset) -> LibraryBookPlacement:
    placement = session.exec(
        select(LibraryBookPlacement)
        .where(LibraryBookPlacement.book_asset_id == asset.id)
        .where(LibraryBookPlacement.user_id == asset.owner_user_id)
    ).first()
    if placement is None or placement.bookshelf_id is None:
        raise HTTPException(status_code=404, detail="电子书尚未放入书柜")
    return placement


def _reading_state_payload(
    book_id: str,
    state: LibraryReadingState | None,
) -> LinuxDoBookReadingState | None:
    if state is None:
        return None
    state_json = state.state_json or {}
    return LinuxDoBookReadingState(
        book_id=book_id,
        chapter_id=state.chapter_id,
        character_offset=state.character_offset,
        chapter_revision=state.chapter_revision,
        current_page=max(1, int(state_json.get("current_page") or 1)),
        page_count=max(1, int(state_json.get("page_count") or 1)),
        updated_at=state.updated_at,
    )


def _summary(
    asset: LibraryBookAsset,
    placement: LibraryBookPlacement,
    document: LinuxDoBookDocument,
    reading_state: LibraryReadingState | None = None,
) -> LinuxDoBookSummary:
    metadata = asset.metadata_json or {}
    book_kind = str(metadata.get("book_kind") or "linux-do")
    source_format = str(metadata.get("format") or "html")
    can_edit_rich_text = book_kind in {"brainstorm", "article-collection"}
    can_edit_source = book_kind == "ebook" and source_format in {"html", "markdown", "text"}
    can_edit_content = can_edit_rich_text or can_edit_source
    return LinuxDoBookSummary(
        id=asset.id,
        topic_id=document.topic_id,
        title=asset.title.strip() or document.title,
        author=asset.author.strip(),
        start_date=str(metadata.get("start_date") or ""),
        source_url=document.source_url,
        book_kind=book_kind,
        format=source_format,
        original_filename=str(metadata.get("original_filename") or ""),
        cover_color=asset.cover_color,
        revision=document.revision,
        toc_count=int(metadata.get("toc_count") or len(document.toc)),
        post_count=document.post_count,
        selected_reply_count=document.selected_reply_count,
        estimated_page_count=document.estimated_page_count,
        imported_at=document.imported_at,
        updated_at=asset.updated_at,
        latest_issue=(
            int(metadata["latest_issue"])
            if metadata.get("latest_issue") is not None
            else None
        ),
        capabilities=LinuxDoBookCapabilities(
            can_annotate=True,
            can_edit_content=can_edit_content,
            edit_mode="html" if can_edit_rich_text else "source" if can_edit_source else None,
            source_policy="owned" if can_edit_content else "derived",
        ),
        bookshelf_placement=LinuxDoBookPlacementPayload(
            bookshelf_id=str(placement.bookshelf_id),
            shelf_index=placement.shelf_index,
            position_index=placement.position_index,
            orientation=placement.orientation,
            folder_id=placement.folder_id,
            article_reading_mode=placement.article_reading_mode,
        ),
        reading_state=_reading_state_payload(asset.id, reading_state),
    )


def upsert_derived_rich_text_book(
    *,
    session: Session,
    current_user: User,
    document: LinuxDoBookDocument,
    source_kind: str,
    book_kind: str,
    bookshelf_id: str | None = None,
    cover_color: str = "#315f53",
    metadata: dict[str, Any] | None = None,
) -> LinuxDoBookSummary:
    """Persist a generated HTML book through the same asset model used by the library."""
    if current_user.id is None:
        raise HTTPException(status_code=403, detail="当前用户无效")
    normalized_source_kind = source_kind.strip()
    if not normalized_source_kind or len(normalized_source_kind) > 80:
        raise ValueError("source_kind 必须为 1-80 个字符")
    bookshelf = _ensure_bookshelf(session, current_user, bookshelf_id)
    source_digest = hashlib.sha256(normalized_source_kind.encode("utf-8")).hexdigest()[:24]
    asset_id = f"derived:{current_user.id}:{source_digest}"
    now = time.time()
    asset = session.exec(
        select(LibraryBookAsset)
        .where(LibraryBookAsset.owner_user_id == current_user.id)
        .where(LibraryBookAsset.source_kind == normalized_source_kind)
    ).first()
    if asset is None:
        asset = session.get(LibraryBookAsset, asset_id)
    if asset is None:
        asset = LibraryBookAsset(
            id=asset_id,
            resource_type=BOOK_RESOURCE_TYPE,
            owner_user_id=current_user.id,
            source_kind=normalized_source_kind,
            created_at=now,
        )
    asset.title = document.title
    asset.author = document.author
    asset.cover_color = cover_color
    existing_metadata = dict(asset.metadata_json or {})
    asset.metadata_json = {
        **existing_metadata,
        **dict(metadata or {}),
        "storage_version": STORAGE_VERSION,
        "topic_id": document.topic_id,
        "book_kind": book_kind,
        "format": "html",
        "source_url": document.source_url,
        "revision": document.revision,
        "toc_count": len(document.toc),
        "post_count": document.post_count,
        "selected_reply_count": document.selected_reply_count,
        "estimated_page_count": document.estimated_page_count,
        "imported_at": document.imported_at,
    }
    asset.updated_at = now
    session.add(asset)
    placement = session.exec(
        select(LibraryBookPlacement)
        .where(LibraryBookPlacement.book_asset_id == asset_id)
        .where(LibraryBookPlacement.user_id == current_user.id)
    ).first()
    if placement is None:
        placement = LibraryBookPlacement(
            book_asset_id=asset_id,
            user_id=current_user.id,
            bookshelf_id=bookshelf.id,
            shelf_index=0,
            position_index=_next_position(session, current_user.id, bookshelf.id),
            orientation="spine_vertical",
            created_at=now,
            updated_at=now,
        )
    elif bookshelf_id:
        placement.bookshelf_id = bookshelf.id
        placement.folder_id = None
        placement.updated_at = now
    session.add(placement)
    _write_document(current_user.id, document)
    session.commit()
    session.refresh(asset)
    session.refresh(placement)
    return _summary(asset, placement, document)


@router.post("/upload", response_model=LinuxDoBookSummary, status_code=status.HTTP_201_CREATED)
async def upload_ebook(
    file: UploadFile = File(...),
    bookshelf_id: str | None = Form(default=None),
    shelf_index: int = Form(default=0, ge=0),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
):
    if current_user.id is None:
        raise HTTPException(status_code=403, detail="当前用户无效")
    filename = Path(file.filename or "").name
    if not supported_ebook_filename(filename):
        raise HTTPException(status_code=400, detail="支持 EPUB、HTML、Markdown 和 TXT 电子书")
    suffix = Path(filename).suffix.lower()
    temp_path = codeyun_temp_root("ebook_uploads") / f"{uuid.uuid4().hex}{suffix}"
    total_bytes = 0
    digest = hashlib.sha256()
    try:
        with temp_path.open("wb") as output:
            while chunk := await file.read(1024 * 1024):
                total_bytes += len(chunk)
                if total_bytes > EBOOK_UPLOAD_MAX_BYTES:
                    raise HTTPException(status_code=413, detail="电子书不能超过 1GB")
                digest.update(chunk)
                output.write(chunk)
        if total_bytes == 0:
            raise HTTPException(status_code=400, detail="上传文件不能为空")

        content_hash = digest.hexdigest()
        asset_id = f"ebook:{current_user.id}:{content_hash[:24]}"
        try:
            imported = import_ebook(temp_path, book_id=asset_id, filename=filename)
        except EbookImportError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        bookshelf = _ensure_bookshelf(session, current_user, bookshelf_id)
        topic_id = -int(content_hash[:14], 16)
        now = time.time()
        asset = session.get(LibraryBookAsset, asset_id)
        created = asset is None
        if created:
            asset = LibraryBookAsset(
                id=asset_id,
                resource_type=BOOK_RESOURCE_TYPE,
                owner_user_id=current_user.id,
                source_kind=f"ebook:{content_hash}",
                created_at=now,
            )
        asset.title = imported.title
        asset.author = imported.author[:160]
        asset.cover_color = _cover_average_color(imported)
        estimated_page_count = dynamic_book_html_page_count(imported.content_html)
        metadata = dict(asset.metadata_json or {})
        asset.metadata_json = {
            **metadata,
            **dict(asset.metadata_json or {}),
            "storage_version": STORAGE_VERSION,
            "topic_id": topic_id,
            "book_kind": "ebook",
            "format": imported.format,
            "original_filename": filename,
            "content_hash": content_hash,
            "revision": imported.revision,
            "cover_resource_name": imported.cover_resource_name,
            "toc_count": len(imported.toc),
            "post_count": len(imported.toc),
            "estimated_page_count": estimated_page_count,
            "imported_at": now,
        }
        asset.updated_at = now
        session.add(asset)

        placement = session.exec(
            select(LibraryBookPlacement)
            .where(LibraryBookPlacement.book_asset_id == asset_id)
            .where(LibraryBookPlacement.user_id == current_user.id)
        ).first()
        if placement is None:
            placement = LibraryBookPlacement(
                book_asset_id=asset_id,
                user_id=current_user.id,
                bookshelf_id=bookshelf.id,
                shelf_index=shelf_index,
                position_index=_next_position(session, current_user.id, bookshelf.id),
                orientation="spine_vertical",
                created_at=now,
                updated_at=now,
            )
        else:
            placement.bookshelf_id = bookshelf.id
            placement.folder_id = None
            placement.shelf_index = shelf_index
            placement.updated_at = now
        session.add(placement)

        document = LinuxDoBookDocument(
            topic_id=topic_id,
            title=imported.title,
            author=imported.author,
            source_url="",
            content_html=imported.content_html,
            content_markdown=imported.content_text,
            toc=imported.toc,
            revision=imported.revision,
            post_count=len(imported.toc),
            selected_reply_count=0,
            imported_at=now,
            estimated_page_count=estimated_page_count,
        )
        _write_imported_ebook(asset, document, imported, temp_path)
        session.commit()
        session.refresh(asset)
        session.refresh(placement)
        return _summary(asset, placement, document)
    finally:
        await file.close()
        temp_path.unlink(missing_ok=True)


@router.get("/{book_id}/resources/{resource_name}")
def get_ebook_resource(
    book_id: str,
    resource_name: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
):
    asset = _get_owned_asset(session, current_user, book_id)
    safe_name = Path(resource_name).name
    if safe_name != resource_name:
        raise HTTPException(status_code=404, detail="电子书资源不存在")
    resource_path = _asset_storage_dir(asset) / "resources" / safe_name
    if not resource_path.is_file():
        raise HTTPException(status_code=404, detail="电子书资源不存在")
    return FileResponse(resource_path)


def _editable_ebook_source_path(asset: LibraryBookAsset) -> tuple[Path, str, str]:
    metadata = dict(asset.metadata_json or {})
    if str(metadata.get("book_kind") or "") != "ebook":
        raise HTTPException(status_code=403, detail="这本书没有可编辑的源文件")
    source_format = str(metadata.get("format") or "")
    if source_format not in {"html", "markdown", "text"}:
        raise HTTPException(status_code=403, detail="这种电子书暂不支持修改正文")
    filename = Path(str(metadata.get("original_filename") or "")).name
    suffix = Path(filename).suffix.lower()
    source_path = _asset_storage_dir(asset) / f"source{suffix}"
    if not suffix or not source_path.is_file():
        raise HTTPException(status_code=404, detail="电子书源文件不存在")
    if source_path.stat().st_size > 10_000_000:
        raise HTTPException(status_code=413, detail="源文件超过 10MB，不适合在浏览器中直接编辑")
    return source_path, source_format, filename


def _read_editable_source(source_path: Path) -> str:
    payload = source_path.read_bytes()
    if payload.startswith((b"\xff\xfe", b"\xfe\xff")):
        return payload.decode("utf-16")
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    return payload.decode("utf-8", errors="replace")


@router.get("/{book_id}/source", response_model=EbookSourceContent)
def get_ebook_source(
    book_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
):
    asset = _get_owned_asset(session, current_user, book_id)
    source_path, source_format, filename = _editable_ebook_source_path(asset)
    document = _read_document(asset)
    return EbookSourceContent(
        content=_read_editable_source(source_path),
        revision=document.revision,
        format=source_format,
        filename=filename,
    )


@router.put("/{book_id}/source", response_model=LinuxDoBookContent)
def update_ebook_source(
    book_id: str,
    payload: EbookSourceUpdate,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
):
    asset = _get_owned_asset(session, current_user, book_id)
    source_path, _source_format, filename = _editable_ebook_source_path(asset)
    placement = _placement_for(session, asset)
    current_document = _read_document(asset)
    if payload.revision != current_document.revision:
        raise HTTPException(status_code=409, detail="正文已在其他位置更新，请重新打开后再编辑")

    temporary = codeyun_temp_root("ebook_source_edits") / f"{uuid.uuid4().hex}{source_path.suffix}"
    try:
        temporary.write_text(payload.content, encoding="utf-8")
        try:
            imported = import_ebook(temporary, book_id=asset.id, filename=filename)
        except EbookImportError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        now = time.time()
        document = LinuxDoBookDocument(
            topic_id=current_document.topic_id,
            title=imported.title,
            author=current_document.author,
            source_url=current_document.source_url,
            content_html=imported.content_html,
            content_markdown=imported.content_text,
            toc=imported.toc,
            revision=imported.revision,
            post_count=len(imported.toc),
            selected_reply_count=current_document.selected_reply_count,
            imported_at=current_document.imported_at,
            estimated_page_count=dynamic_book_html_page_count(imported.content_html),
        )
        metadata = dict(asset.metadata_json or {})
        metadata.update({
            "format": imported.format,
            "revision": imported.revision,
            "toc_count": len(imported.toc),
            "post_count": len(imported.toc),
            "estimated_page_count": document.estimated_page_count,
        })
        asset.title = imported.title
        asset.metadata_json = metadata
        asset.updated_at = now
        session.add(asset)
        _write_imported_ebook(asset, document, imported, temporary)
        session.commit()
        session.refresh(asset)
        return _content_payload(asset, placement, document)
    finally:
        temporary.unlink(missing_ok=True)


def _content_payload(
    asset: LibraryBookAsset,
    placement: LibraryBookPlacement,
    document: LinuxDoBookDocument,
) -> LinuxDoBookContent:
    return LinuxDoBookContent(
        **_summary(asset, placement, document).model_dump(),
        content_html=document.content_html,
        content_markdown=document.content_markdown,
        toc=document.toc,
    )


def _sanitize_html_book_article(content_html: str) -> BeautifulSoup:
    soup = BeautifulSoup(content_html, "html.parser")
    for node in soup.select("script, style, iframe, object, embed, form"):
        node.decompose()
    for node in soup.find_all(True):
        for attribute in list(node.attrs):
            if attribute.lower().startswith("on"):
                node.attrs.pop(attribute, None)
        for attribute in ("href", "src"):
            value = str(node.get(attribute) or "")
            if re.match(r"^\s*javascript:", value, re.IGNORECASE):
                node.attrs.pop(attribute, None)
    return soup


def _rebuild_html_book_toc(content_soup: BeautifulSoup) -> list[LinuxDoTocItem]:
    toc: list[LinuxDoTocItem] = []
    for index, article in enumerate(content_soup.select("article[data-article-id]"), start=1):
        article_id = str(article.get("data-article-id") or "").strip()
        parent_anchor = str(article.get("data-parent-article-id") or "").strip() or None
        heading = article.find("h1")
        title = heading.get_text(" ", strip=True) if isinstance(heading, Tag) else ""
        if not article_id or not title:
            continue
        heading["id"] = article_id
        toc.append(LinuxDoTocItem(
            title=title,
            number="",
            level=2 if parent_anchor else 1,
            anchor=article_id,
            parent_anchor=parent_anchor,
            source_post_number=index,
        ))
    return toc


def upsert_article_collection_entry(
    *,
    session: Session,
    current_user: User,
    collection_source_kind: str,
    collection_title: str,
    article_id: str,
    article_title: str,
    article_content_html: str,
    article_author: str = "",
    article_source_url: str = "",
    article_date: str = "",
    collection_author: str = "多人作者",
    bookshelf_id: str | None = None,
    cover_color: str = "#315f53",
) -> LinuxDoBookSummary:
    """Add or replace one article in a stable, multi-article library collection."""
    if current_user.id is None:
        raise HTTPException(status_code=403, detail="当前用户无效")
    normalized_article_id = article_id.strip()
    normalized_article_title = article_title.strip()
    if not normalized_article_id or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,119}", normalized_article_id):
        raise ValueError("article_id 必须为 1-120 个安全字符")
    if not normalized_article_title:
        raise ValueError("article_title 不能为空")

    normalized_source_kind = collection_source_kind.strip()
    existing_asset = session.exec(
        select(LibraryBookAsset)
        .where(LibraryBookAsset.owner_user_id == current_user.id)
        .where(LibraryBookAsset.source_kind == normalized_source_kind)
    ).first()
    now = time.time()
    if existing_asset is None:
        digest = hashlib.sha256(normalized_source_kind.encode("utf-8")).hexdigest()
        document = LinuxDoBookDocument(
            topic_id=-int(digest[:15], 16),
            title=collection_title.strip(),
            author=collection_author.strip(),
            source_url="",
            content_html="",
            content_markdown="",
            toc=[],
            revision="",
            post_count=0,
            selected_reply_count=0,
            imported_at=now,
        )
        metadata: dict[str, Any] = {}
    else:
        document = _read_document(existing_asset)
        metadata = dict(existing_asset.metadata_json or {})

    content_soup = BeautifulSoup(document.content_html, "html.parser")
    body_soup = _sanitize_html_book_article(article_content_html)
    outer_article = body_soup.find("article")
    if isinstance(outer_article, Tag):
        body_soup = BeautifulSoup(outer_article.decode_contents(), "html.parser")
    for heading in list(body_soup.find_all("h1")):
        if heading.get_text(" ", strip=True) == normalized_article_title:
            heading.decompose()
        else:
            heading.name = "h2"

    replacement = content_soup.new_tag("article")
    replacement["data-article-id"] = normalized_article_id
    title_heading = content_soup.new_tag("h1", id=normalized_article_id)
    title_heading.string = normalized_article_title
    replacement.append(title_heading)
    for node in list(body_soup.contents):
        replacement.append(node.extract())

    existing_article = next((
        article
        for article in content_soup.select("article[data-article-id]")
        if str(article.get("data-article-id") or "") == normalized_article_id
    ), None)
    if existing_article is None:
        content_soup.append(replacement)
    else:
        existing_article.replace_with(replacement)

    toc = _rebuild_html_book_toc(content_soup)
    content_html = str(content_soup)
    revision = hashlib.sha256(content_html.encode("utf-8")).hexdigest()
    document.title = collection_title.strip()
    document.author = collection_author.strip()
    document.content_html = content_html
    document.content_markdown = ""
    document.toc = toc
    document.revision = revision
    document.post_count = len(toc)
    document.estimated_page_count = dynamic_book_html_page_count(content_html)

    articles = [dict(item) for item in metadata.get("articles") or [] if isinstance(item, dict)]
    article_metadata = {
        "id": normalized_article_id,
        "title": normalized_article_title,
        "author": article_author.strip(),
        "source_url": article_source_url.strip(),
        "date": article_date.strip(),
        "updated_at": now,
    }
    for index, item in enumerate(articles):
        if str(item.get("id") or "") == normalized_article_id:
            articles[index] = {**item, **article_metadata}
            break
    else:
        articles.append(article_metadata)

    return upsert_derived_rich_text_book(
        session=session,
        current_user=current_user,
        document=document,
        source_kind=normalized_source_kind,
        book_kind="article-collection",
        bookshelf_id=bookshelf_id,
        cover_color=cover_color,
        metadata={
            **metadata,
            "article_count": len(toc),
            "articles": articles,
        },
    )


@router.post("/import", response_model=LinuxDoBookSummary, status_code=status.HTTP_201_CREATED)
def import_book(
    payload: LinuxDoBookImportRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
):
    if current_user.id is None:
        raise HTTPException(status_code=403, detail="当前用户无效")
    try:
        document = import_linux_do_book(str(payload.url))
    except LinuxDoBookError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    bookshelf = _ensure_bookshelf(session, current_user, payload.bookshelf_id)
    asset_id = f"linux-do:{current_user.id}:{document.topic_id}"
    source_kind = f"linux-do-topic:{document.topic_id}"
    now = time.time()
    asset = session.get(LibraryBookAsset, asset_id)
    created = asset is None
    if created:
        asset = LibraryBookAsset(
            id=asset_id,
            resource_type=BOOK_RESOURCE_TYPE,
            owner_user_id=current_user.id,
            source_kind=source_kind,
            created_at=now,
        )
    asset.title = document.title
    asset.author = document.author
    asset.cover_color = "#294f6d"
    metadata = dict(asset.metadata_json or {})
    asset.metadata_json = {
        **metadata,
        "storage_version": STORAGE_VERSION,
        "topic_id": document.topic_id,
        "book_kind": "linux-do",
        "format": "html",
        "source_url": document.source_url,
        "revision": document.revision,
        "toc_count": len(document.toc),
        "post_count": document.post_count,
        "selected_reply_count": document.selected_reply_count,
        "estimated_page_count": document.estimated_page_count,
        "imported_at": document.imported_at,
    }
    asset.updated_at = now
    session.add(asset)
    placement = session.exec(
        select(LibraryBookPlacement)
        .where(LibraryBookPlacement.book_asset_id == asset_id)
        .where(LibraryBookPlacement.user_id == current_user.id)
    ).first()
    if placement is None:
        placement = LibraryBookPlacement(
            book_asset_id=asset_id,
            user_id=current_user.id,
            bookshelf_id=bookshelf.id,
            shelf_index=0,
            position_index=_next_position(session, current_user.id, bookshelf.id),
            orientation="spine_vertical",
            created_at=now,
            updated_at=now,
        )
    elif payload.bookshelf_id:
        placement.bookshelf_id = bookshelf.id
        placement.folder_id = None
        placement.updated_at = now
    session.add(placement)
    _write_document(current_user.id, document)
    session.commit()
    session.refresh(asset); session.refresh(placement)
    return _summary(asset, placement, document)


@router.get("", response_model=list[LinuxDoBookSummary])
def list_books(
    bookshelf_id: str | None = Query(default=None),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
):
    statement = (
        select(LibraryBookAsset, LibraryBookPlacement)
        .join(
            LibraryBookPlacement,
            LibraryBookPlacement.book_asset_id == LibraryBookAsset.id,
        )
        .where(LibraryBookAsset.owner_user_id == current_user.id)
        .where(LibraryBookAsset.resource_type == BOOK_RESOURCE_TYPE)
        .where(LibraryBookPlacement.user_id == current_user.id)
        .order_by(LibraryBookAsset.updated_at.desc())
    )
    if bookshelf_id:
        statement = statement.where(LibraryBookPlacement.bookshelf_id == bookshelf_id)
    rows = session.exec(statement).all()
    asset_ids = [asset.id for asset, _placement in rows]
    reading_states = {
        state.resource_id: state
        for state in session.exec(
            select(LibraryReadingState)
            .where(LibraryReadingState.resource_type == BOOK_RESOURCE_TYPE)
            .where(LibraryReadingState.resource_id.in_(asset_ids))
            .where(LibraryReadingState.user_id == current_user.id)
        ).all()
    } if asset_ids else {}
    return [
        _summary(
            asset,
            placement,
            _read_summary_document(asset),
            reading_states.get(asset.id),
        )
        for asset, placement in rows
    ]


@router.get("/{book_id}", response_model=LinuxDoBookContent)
def get_book(
    book_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
):
    asset = _get_owned_asset(session, current_user, book_id)
    placement = _placement_for(session, asset)
    document = _read_document(asset)
    return _content_payload(asset, placement, document)


@router.delete("/{book_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_book(
    book_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
) -> Response:
    asset = _get_owned_asset(session, current_user, book_id)
    storage_dir = _asset_storage_dir(asset)
    related_rows = [
        *session.exec(
            select(LibraryBookPlacement).where(LibraryBookPlacement.book_asset_id == asset.id)
        ).all(),
        *session.exec(
            select(LibraryReadingState)
            .where(LibraryReadingState.resource_type == BOOK_RESOURCE_TYPE)
            .where(LibraryReadingState.resource_id == asset.id)
        ).all(),
        *session.exec(
            select(LibraryAnnotation)
            .where(LibraryAnnotation.resource_type == BOOK_RESOURCE_TYPE)
            .where(LibraryAnnotation.resource_id == asset.id)
        ).all(),
    ]
    for row in related_rows:
        session.delete(row)
    session.delete(asset)
    session.commit()
    try:
        shutil.rmtree(storage_dir, ignore_errors=True)
    except OSError:
        # The database is the source of truth. A stale imported-book cache is
        # safer than rolling a user-requested deletion back into the library.
        pass
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/{book_id}/metadata", response_model=LinuxDoBookSummary)
def update_book_metadata(
    book_id: str,
    payload: LinuxDoBookMetadataUpdate,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
):
    asset = _get_owned_asset(session, current_user, book_id)
    placement = _placement_for(session, asset)
    document = _read_document(asset)
    asset.title = payload.title.strip()
    asset.author = payload.author.strip()
    metadata = dict(asset.metadata_json or {})
    metadata["start_date"] = payload.start_date
    asset.metadata_json = metadata
    asset.cover_color = payload.cover_color.strip()
    asset.updated_at = time.time()
    session.add(asset)
    session.commit()
    session.refresh(asset)
    return _summary(asset, placement, document)


@router.put("/{book_id}/articles/{article_id}", response_model=LinuxDoBookContent)
def update_html_book_article(
    book_id: str,
    article_id: str,
    payload: HtmlBookArticleUpdate,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
):
    asset = _get_owned_asset(session, current_user, book_id)
    metadata = dict(asset.metadata_json or {})
    book_kind = str(metadata.get("book_kind") or "")
    if book_kind not in {"brainstorm", "article-collection"}:
        raise HTTPException(status_code=403, detail="这本书不支持直接编辑")

    placement = _placement_for(session, asset)
    document = _read_document(asset)
    if payload.revision != document.revision:
        raise HTTPException(status_code=409, detail="文章已在其他位置更新，请重新打开后再编辑")

    content_soup = BeautifulSoup(document.content_html, "html.parser")
    target = next((
        article
        for article in content_soup.select("article[data-article-id]")
        if str(article.get("data-article-id") or "") == article_id
    ), None)
    if target is None:
        raise HTTPException(status_code=404, detail="文章不存在")

    article_soup = _sanitize_html_book_article(payload.content_html)
    heading = article_soup.find("h1")
    if not isinstance(heading, Tag) or not heading.get_text(" ", strip=True):
        raise HTTPException(status_code=422, detail="文章需要保留一个标题")
    heading["id"] = article_id

    target.clear()
    for node in list(article_soup.contents):
        target.append(node.extract())

    toc = _rebuild_html_book_toc(content_soup)
    if not toc:
        raise HTTPException(status_code=422, detail="书中至少需要保留一篇有标题的文章")

    now = time.time()
    content_html = str(content_soup)
    revision = hashlib.sha256(content_html.encode("utf-8")).hexdigest()
    document.content_html = content_html
    document.content_markdown = ""
    document.toc = toc
    document.revision = revision
    document.estimated_page_count = dynamic_book_html_page_count(content_html)

    articles = [dict(item) for item in metadata.get("articles") or [] if isinstance(item, dict)]
    article_title = heading.get_text(" ", strip=True)
    matched_article = False
    for item in articles:
        if str(item.get("id") or "") == article_id:
            item["title"] = article_title
            item["updated_at"] = now
            matched_article = True
            break
    if not matched_article:
        articles.append({"id": article_id, "title": article_title, "updated_at": now})
    metadata.update({
        "revision": revision,
        "toc_count": len(toc),
        "post_count": document.post_count,
        "selected_reply_count": document.selected_reply_count,
        "estimated_page_count": document.estimated_page_count,
        "imported_at": document.imported_at,
        "article_count": len(toc),
        "articles": articles,
    })
    asset.metadata_json = metadata
    asset.updated_at = now
    session.add(asset)
    _write_document(current_user.id, document)
    session.commit()
    session.refresh(asset)
    return _content_payload(asset, placement, document)


@router.put("/{book_id}/placement", response_model=LinuxDoBookPlacementPayload)
def update_book_placement(
    book_id: str,
    payload: LinuxDoBookPlacementUpdate,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
):
    asset = _get_owned_asset(session, current_user, book_id)
    placement = _placement_for(session, asset)
    target_bookshelf = session.get(PdfLibraryBookshelf, payload.bookshelf_id)
    if target_bookshelf is None or target_bookshelf.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Bookshelf not found")
    target_folder = None
    if payload.folder_id:
        target_folder = session.get(LibraryFolder, payload.folder_id)
        if (
            target_folder is None
            or target_folder.owner_user_id != current_user.id
            or target_folder.bookshelf_id != target_bookshelf.id
        ):
            raise HTTPException(status_code=404, detail="Library folder not found")
    placement.bookshelf_id = target_bookshelf.id
    placement.folder_id = target_folder.id if target_folder else None
    placement.shelf_index = target_folder.shelf_index if target_folder else payload.shelf_index
    placement.position_index = payload.position_index
    placement.orientation = payload.orientation
    if "article_reading_mode" in payload.model_fields_set:
        placement.article_reading_mode = payload.article_reading_mode
    placement.updated_at = time.time()
    session.add(placement)
    session.commit()
    session.refresh(placement)
    return LinuxDoBookPlacementPayload(
        bookshelf_id=placement.bookshelf_id or "",
        shelf_index=placement.shelf_index,
        position_index=placement.position_index,
        orientation=placement.orientation,
        folder_id=placement.folder_id,
        article_reading_mode=placement.article_reading_mode,
    )


@router.get("/{book_id}/reading-state", response_model=LinuxDoBookReadingState)
def get_reading_state(
    book_id: str,
    response: Response,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
):
    _get_owned_asset(session, current_user, book_id)
    state = session.exec(
        select(LibraryReadingState)
        .where(LibraryReadingState.resource_type == BOOK_RESOURCE_TYPE)
        .where(LibraryReadingState.resource_id == book_id)
        .where(LibraryReadingState.user_id == current_user.id)
    ).first()
    response.headers["Cache-Control"] = "no-store"
    if state is None:
        return LinuxDoBookReadingState(book_id=book_id)
    return _reading_state_payload(book_id, state)


@router.put("/{book_id}/reading-state", response_model=LinuxDoBookReadingState)
def update_reading_state(
    book_id: str,
    payload: LinuxDoBookReadingStateUpdate,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
):
    _get_owned_asset(session, current_user, book_id)
    state = session.exec(
        select(LibraryReadingState)
        .where(LibraryReadingState.resource_type == BOOK_RESOURCE_TYPE)
        .where(LibraryReadingState.resource_id == book_id)
        .where(LibraryReadingState.user_id == current_user.id)
    ).first()
    now = time.time()
    if state is None:
        state = LibraryReadingState(
            resource_type=BOOK_RESOURCE_TYPE,
            resource_id=book_id,
            user_id=current_user.id,
            created_at=now,
        )
    state.chapter_id = payload.chapter_id
    state.character_offset = payload.character_offset
    state.chapter_revision = payload.chapter_revision
    state.state_json = {
        **(state.state_json or {}),
        "current_page": payload.current_page,
        "page_count": payload.page_count,
    }
    state.updated_at = now
    session.add(state); session.commit(); session.refresh(state)
    return _reading_state_payload(book_id, state)
