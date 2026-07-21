from __future__ import annotations

import base64
import hashlib
import math
import os
import re
import time
import unicodedata
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from backend.core.access.auth import get_current_active_user
from backend.db import get_session
from backend.models import (
    LibraryBookAsset,
    LibraryBookPlacement,
    LibraryReadingState,
    PdfBookshelfPlacement,
    PdfLibraryBookshelf,
    User,
)
from backend.api.pdf_documents import _resolve_bookshelf_resource_access


router = APIRouter()

SKILL_BOOK_ID = "local-skills"
SKILL_BOOK_ASSET_ID = "skill-book:local-skills"
SKILL_BOOK_TITLE = "本地 Skill 手册"
SKILL_BOOK_AUTHOR = "CodeYun"
LOCAL_SKILL_BOOK_OWNER_USERNAME = "code4101"
MAX_SKILL_MARKDOWN_BYTES = 2 * 1024 * 1024
SKILL_BOOK_PAGINATION_VERSION = 2
SKILL_BOOK_LINE_CAPACITY_UNITS = 44
SKILL_BOOK_LINES_PER_PAGE = 30
SKILL_BOOK_PAGE_CAPACITY_UNITS = SKILL_BOOK_LINE_CAPACITY_UNITS * SKILL_BOOK_LINES_PER_PAGE
DEFAULT_SKILL_BOOK_PAGE_FORMAT = "A4"
SKILL_BOOK_PAGE_FORMATS: dict[str, tuple[str, float, float]] = {
    "A3": ("A3", 297.0, 420.0),
    "A4": ("A4", 210.0, 297.0),
    "A5": ("A5", 148.0, 210.0),
    "B5": ("B5", 176.0, 250.0),
    "LETTER": ("Letter", 215.9, 279.4),
}


def _ensure_local_skill_book_asset(
    session: Session,
) -> tuple[LibraryBookAsset, LibraryBookPlacement, PdfLibraryBookshelf, User]:
    owner = session.exec(
        select(User).where(User.username == LOCAL_SKILL_BOOK_OWNER_USERNAME)
    ).first()
    if owner is None or owner.id is None:
        raise HTTPException(status_code=404, detail="Local skill book not found")

    asset = session.get(LibraryBookAsset, SKILL_BOOK_ASSET_ID)
    now = time.time()
    if asset is None:
        asset = LibraryBookAsset(
            id=SKILL_BOOK_ASSET_ID,
            resource_type="skill-book",
            owner_user_id=owner.id,
            source_kind="local-skills",
            title=SKILL_BOOK_TITLE,
            author=SKILL_BOOK_AUTHOR,
            cover_color="#315f53",
            metadata_json={"page_format": DEFAULT_SKILL_BOOK_PAGE_FORMAT},
            created_at=now,
            updated_at=now,
        )
        session.add(asset)

    shelves = list(session.exec(
        select(PdfLibraryBookshelf)
        .where(PdfLibraryBookshelf.user_id == owner.id)
        .order_by(PdfLibraryBookshelf.sort_index, PdfLibraryBookshelf.created_at)
    ).all())
    if not shelves:
        for sort_index, name in enumerate(("1", "2", "4", "5")):
            session.add(PdfLibraryBookshelf(
                user_id=owner.id,
                name=name,
                sort_index=sort_index,
                created_at=now,
                updated_at=now,
            ))
        session.commit()
        shelves = list(session.exec(
            select(PdfLibraryBookshelf)
            .where(PdfLibraryBookshelf.user_id == owner.id)
            .order_by(PdfLibraryBookshelf.sort_index, PdfLibraryBookshelf.created_at)
        ).all())
    default_shelf = shelves[0]

    placement = session.exec(
        select(LibraryBookPlacement)
        .where(LibraryBookPlacement.book_asset_id == asset.id)
        .where(LibraryBookPlacement.user_id == owner.id)
    ).first()
    if placement is None:
        pdf_positions = session.exec(
            select(PdfBookshelfPlacement.position_index)
            .where(PdfBookshelfPlacement.user_id == owner.id)
            .where(PdfBookshelfPlacement.bookshelf_id == default_shelf.id)
            .where(PdfBookshelfPlacement.shelf_index == 0)
        ).all()
        dynamic_positions = session.exec(
            select(LibraryBookPlacement.position_index)
            .where(LibraryBookPlacement.user_id == owner.id)
            .where(LibraryBookPlacement.bookshelf_id == default_shelf.id)
            .where(LibraryBookPlacement.shelf_index == 0)
        ).all()
        placement = LibraryBookPlacement(
            book_asset_id=asset.id,
            user_id=owner.id,
            bookshelf_id=default_shelf.id,
            shelf_index=0,
            position_index=max([*pdf_positions, *dynamic_positions], default=-1) + 1,
            orientation="spine_vertical",
            created_at=now,
            updated_at=now,
        )
        session.add(placement)
    session.commit()
    session.refresh(asset)
    session.refresh(placement)
    shelf = session.get(PdfLibraryBookshelf, placement.bookshelf_id) or default_shelf
    return asset, placement, shelf, owner


def _get_local_skill_book_context(
    session: Session,
    current_user: User,
    *,
    bookshelf_id: str | None = None,
    required_role: Literal["viewer", "manager"] = "viewer",
) -> tuple[LibraryBookAsset, LibraryBookPlacement, PdfLibraryBookshelf, User, Literal["viewer", "manager"]]:
    asset, placement, shelf, owner = _ensure_local_skill_book_asset(session)
    if bookshelf_id is not None and placement.bookshelf_id != bookshelf_id:
        raise HTTPException(status_code=404, detail="Local skill book not found")
    access = _resolve_bookshelf_resource_access(session, shelf, current_user)
    role: Literal["viewer", "manager"] = "manager" if current_user.id == owner.id else "viewer"
    if current_user.id != owner.id and not access.capabilities.can_read:
        raise HTTPException(status_code=404, detail="Local skill book not found")
    if required_role == "manager" and current_user.id != owner.id:
        raise HTTPException(status_code=403, detail="没有该动态书本的管理权限")
    return asset, placement, shelf, owner, role


def _asset_page_format(asset: LibraryBookAsset) -> str:
    return _normalize_page_format((asset.metadata_json or {}).get("page_format"))


class SkillBookPageFormatOption(BaseModel):
    value: str
    label: str
    width_mm: float
    height_mm: float


class SkillBookChapter(BaseModel):
    id: str
    skill_id: str
    title: str
    relative_path: str
    kind: Literal["main", "reference"]
    revision: str
    character_count: int
    reading_unit_count: int
    estimated_page_count: int
    page_start: int
    page_end: int
    updated_at: float


class SkillBookSkill(BaseModel):
    id: str
    name: str
    description: str = ""
    chapters: list[SkillBookChapter]
    updated_at: float


class SkillBookPlacementPayload(BaseModel):
    book_id: str = SKILL_BOOK_ID
    bookshelf_id: str
    shelf_index: int = 0
    position_index: int = 0
    orientation: Literal["spine_vertical", "spine_horizontal", "cover_front"] = "spine_vertical"


class SkillBookCatalog(BaseModel):
    id: str = SKILL_BOOK_ID
    title: str = SKILL_BOOK_TITLE
    author: str = SKILL_BOOK_AUTHOR
    cover_color: str = "#315f53"
    revision: str
    skill_count: int
    chapter_count: int
    estimated_page_count: int
    page_format: str = DEFAULT_SKILL_BOOK_PAGE_FORMAT
    page_width_mm: float = 210.0
    page_height_mm: float = 297.0
    page_format_options: list[SkillBookPageFormatOption] = Field(default_factory=list)
    pagination_version: int = SKILL_BOOK_PAGINATION_VERSION
    page_capacity_units: int = SKILL_BOOK_PAGE_CAPACITY_UNITS
    updated_at: float
    skills: list[SkillBookSkill]
    owner_user_id: int
    owner_username: str
    is_owned: bool
    access_role: Literal["viewer", "manager"]
    bookshelf_placement: SkillBookPlacementPayload


class SkillBookPlacementUpdate(BaseModel):
    bookshelf_id: str
    shelf_index: int = Field(default=0, ge=0)
    position_index: int = Field(default=0, ge=0)
    orientation: Literal["spine_vertical", "spine_horizontal", "cover_front"] = "spine_vertical"


class SkillBookChapterContent(BaseModel):
    book_id: str = SKILL_BOOK_ID
    chapter: SkillBookChapter
    markdown: str


class SkillBookReadingStatePayload(BaseModel):
    book_id: str = SKILL_BOOK_ID
    chapter_id: str = ""
    character_offset: int = 0
    chapter_revision: str = ""
    current_page: int = 1
    pagination_version: int = SKILL_BOOK_PAGINATION_VERSION
    page_format: str = DEFAULT_SKILL_BOOK_PAGE_FORMAT
    updated_at: float | None = None


class SkillBookReadingStateUpdate(BaseModel):
    chapter_id: str
    character_offset: int = Field(default=0, ge=0)
    chapter_revision: str = ""


class SkillBookMetadataUpdate(BaseModel):
    page_format: str = DEFAULT_SKILL_BOOK_PAGE_FORMAT


def _normalize_page_format(value: str | None) -> str:
    normalized = (value or "").strip().upper()
    return normalized if normalized in SKILL_BOOK_PAGE_FORMATS else DEFAULT_SKILL_BOOK_PAGE_FORMAT


def _page_format_options() -> list[SkillBookPageFormatOption]:
    return [
        SkillBookPageFormatOption(value=value, label=label, width_mm=width, height_mm=height)
        for value, (label, width, height) in SKILL_BOOK_PAGE_FORMATS.items()
    ]


def _page_capacity(page_format: str) -> tuple[int, int, int]:
    _label, width_mm, height_mm = SKILL_BOOK_PAGE_FORMATS[_normalize_page_format(page_format)]
    line_capacity = max(24, round(SKILL_BOOK_LINE_CAPACITY_UNITS * width_mm / 210.0))
    lines_per_page = max(18, round(SKILL_BOOK_LINES_PER_PAGE * height_mm / 297.0))
    return line_capacity, lines_per_page, line_capacity * lines_per_page


def _skills_root() -> Path:
    configured = (os.environ.get("CODEYUN_SKILLS_ROOT") or "").strip()
    if configured:
        return Path(configured).expanduser().resolve(strict=False)
    return (Path(__file__).resolve().parents[3] / "skills").resolve(strict=False)


def _read_markdown(path: Path) -> str:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise HTTPException(status_code=404, detail="Skill chapter not found") from exc
    if size > MAX_SKILL_MARKDOWN_BYTES:
        raise HTTPException(status_code=413, detail="Skill chapter is too large")
    try:
        return path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError as exc:
        raise HTTPException(status_code=404, detail="Skill chapter not found") from exc


def _split_frontmatter(markdown: str) -> tuple[dict[str, str], str]:
    if not markdown.startswith("---"):
        return {}, markdown
    match = re.match(r"^---\s*\r?\n(.*?)\r?\n---\s*(?:\r?\n|$)", markdown, re.DOTALL)
    if match is None:
        return {}, markdown
    metadata: dict[str, str] = {}
    for line in match.group(1).splitlines():
        field = re.match(r"^([A-Za-z_][A-Za-z0-9_-]*)\s*:\s*(.*?)\s*$", line)
        if field is None:
            continue
        value = field.group(2).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        metadata[field.group(1)] = value
    return metadata, markdown[match.end():]


def _markdown_title(markdown: str, fallback: str) -> str:
    for line in markdown.splitlines():
        match = re.match(r"^#\s+(.+?)\s*$", line)
        if match:
            return re.sub(r"\s+#+\s*$", "", match.group(1)).strip() or fallback
    return fallback


def _chapter_id(relative_path: str) -> str:
    return base64.urlsafe_b64encode(relative_path.encode("utf-8")).decode("ascii").rstrip("=")


def _character_width_units(text: str) -> float:
    units = 0.0
    for character in text:
        if character.isspace():
            units += 0.25
        elif unicodedata.east_asian_width(character) in {"W", "F"}:
            units += 1.0
        else:
            units += 0.55
    return units


def _estimate_markdown_reading_units(
    markdown: str,
    line_capacity_units: int = SKILL_BOOK_LINE_CAPACITY_UNITS,
) -> int:
    """Estimate occupied layout cells for the standard rich-text reading page.

    The standard page assumes roughly 44 CJK cells per line and 30 body lines.
    Structural Markdown consumes vertical space even when its raw character count
    is small, so the estimate works line-by-line instead of dividing raw bytes.
    """
    total_units = 0.0
    in_fenced_code = False
    for raw_line in markdown.splitlines():
        stripped = raw_line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fenced_code = not in_fenced_code
            total_units += line_capacity_units * 0.5
            continue
        if not stripped:
            total_units += line_capacity_units * 0.45
            continue
        if re.search(r"!\[[^\]]*\]\([^)]*\)", stripped):
            total_units += line_capacity_units * 10
            continue

        visible = re.sub(r"^\s{0,3}(?:#{1,6}|[-+*]|\d+[.)]|>)\s+", "", stripped)
        visible = re.sub(r"[`*_~]", "", visible)
        width_units = max(1.0, _character_width_units(visible))
        occupied_lines = max(1, math.ceil(width_units / line_capacity_units))
        if not in_fenced_code and re.match(r"^\s{0,3}#{1,6}\s+", raw_line):
            occupied_lines += 1
        total_units += occupied_lines * line_capacity_units
    return max(1, math.ceil(total_units))


def _chapter_payload(
    *,
    root: Path,
    skill_id: str,
    path: Path,
    kind: Literal["main", "reference"],
    line_capacity_units: int = SKILL_BOOK_LINE_CAPACITY_UNITS,
    page_capacity_units: int = SKILL_BOOK_PAGE_CAPACITY_UNITS,
) -> tuple[SkillBookChapter, str, dict[str, str]]:
    raw_markdown = _read_markdown(path)
    metadata, body = _split_frontmatter(raw_markdown)
    relative_path = path.relative_to(root).as_posix()
    stat = path.stat()
    fallback_title = skill_id if kind == "main" else path.stem
    title = metadata.get("name", "").strip() if kind == "main" else ""
    title = title or _markdown_title(body, fallback_title)
    revision = hashlib.sha256(raw_markdown.encode("utf-8")).hexdigest()
    reading_unit_count = _estimate_markdown_reading_units(body, line_capacity_units)
    return (
        SkillBookChapter(
            id=_chapter_id(relative_path),
            skill_id=skill_id,
            title=title,
            relative_path=relative_path,
            kind=kind,
            revision=revision,
            character_count=len(body),
            reading_unit_count=reading_unit_count,
            estimated_page_count=max(1, math.ceil(reading_unit_count / page_capacity_units)),
            page_start=1,
            page_end=1,
            updated_at=stat.st_mtime,
        ),
        body,
        metadata,
    )


def _scan_skill_book(
    root: Path | None = None,
    page_format: str = DEFAULT_SKILL_BOOK_PAGE_FORMAT,
) -> tuple[SkillBookCatalog, dict[str, tuple[SkillBookChapter, Path]]]:
    resolved_root = (root or _skills_root()).resolve(strict=False)
    if not resolved_root.is_dir():
        raise HTTPException(status_code=404, detail="Local Skill directory not found")

    normalized_page_format = _normalize_page_format(page_format)
    _page_label, page_width_mm, page_height_mm = SKILL_BOOK_PAGE_FORMATS[normalized_page_format]
    line_capacity_units, _lines_per_page, page_capacity_units = _page_capacity(normalized_page_format)
    skills: list[SkillBookSkill] = []
    chapter_lookup: dict[str, tuple[SkillBookChapter, Path]] = {}
    revision_parts: list[str] = []
    latest_updated_at = 0.0
    next_page = 1

    for skill_dir in sorted(resolved_root.iterdir(), key=lambda item: item.name.casefold()):
        main_path = skill_dir / "SKILL.md"
        if not skill_dir.is_dir() or not main_path.is_file():
            continue

        chapter_rows: list[SkillBookChapter] = []
        main_chapter, _main_body, metadata = _chapter_payload(
            root=resolved_root,
            skill_id=skill_dir.name,
            path=main_path,
            kind="main",
            line_capacity_units=line_capacity_units,
            page_capacity_units=page_capacity_units,
        )
        chapter_rows.append(main_chapter)

        reference_paths = sorted(
            (
                path
                for path in skill_dir.rglob("*.md")
                if path != main_path and not any(part.startswith(".") for part in path.relative_to(skill_dir).parts)
            ),
            key=lambda item: item.relative_to(skill_dir).as_posix().casefold(),
        )
        for reference_path in reference_paths:
            chapter, _body, _metadata = _chapter_payload(
                root=resolved_root,
                skill_id=skill_dir.name,
                path=reference_path,
                kind="reference",
                line_capacity_units=line_capacity_units,
                page_capacity_units=page_capacity_units,
            )
            chapter_rows.append(chapter)

        for chapter in chapter_rows:
            chapter.page_start = next_page
            chapter.page_end = next_page + chapter.estimated_page_count - 1
            next_page = chapter.page_end + 1
            chapter_lookup[chapter.id] = (chapter, resolved_root / chapter.relative_path)
            revision_parts.append(f"{chapter.relative_path}\0{chapter.revision}")
            latest_updated_at = max(latest_updated_at, chapter.updated_at)

        skills.append(
            SkillBookSkill(
                id=skill_dir.name,
                name=metadata.get("name", "").strip() or skill_dir.name,
                description=metadata.get("description", "").strip(),
                chapters=chapter_rows,
                updated_at=max(chapter.updated_at for chapter in chapter_rows),
            )
        )

    revision = hashlib.sha256("\n".join(revision_parts).encode("utf-8")).hexdigest()
    chapter_count = sum(len(skill.chapters) for skill in skills)
    catalog = SkillBookCatalog(
        revision=revision,
        skill_count=len(skills),
        chapter_count=chapter_count,
        estimated_page_count=max(1, next_page - 1),
        page_format=normalized_page_format,
        page_width_mm=page_width_mm,
        page_height_mm=page_height_mm,
        page_format_options=_page_format_options(),
        page_capacity_units=page_capacity_units,
        updated_at=latest_updated_at,
        skills=skills,
        owner_user_id=0,
        owner_username="",
        is_owned=False,
        access_role="viewer",
        bookshelf_placement=SkillBookPlacementPayload(bookshelf_id=""),
    )
    return catalog, chapter_lookup


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"


def _reading_page(chapter: SkillBookChapter | None, character_offset: int) -> int:
    if chapter is None or chapter.character_count <= 0:
        return 1
    bounded_offset = min(chapter.character_count, max(0, character_offset))
    progress = bounded_offset / chapter.character_count
    chapter_page = min(
        chapter.estimated_page_count,
        math.floor(progress * chapter.estimated_page_count) + 1,
    )
    return chapter.page_start + chapter_page - 1


def _get_reading_state(session: Session, user_id: int) -> LibraryReadingState | None:
    return session.exec(
        select(LibraryReadingState)
        .where(LibraryReadingState.resource_type == "skill-book")
        .where(LibraryReadingState.resource_id == SKILL_BOOK_ID)
        .where(LibraryReadingState.user_id == user_id)
    ).first()


def _serialize_reading_state(
    state: LibraryReadingState | None,
    lookup: dict[str, tuple[SkillBookChapter, Path]],
    *,
    page_format: str = DEFAULT_SKILL_BOOK_PAGE_FORMAT,
) -> SkillBookReadingStatePayload:
    chapter = lookup.get(state.chapter_id)[0] if state and state.chapter_id in lookup else None
    offset = min(chapter.character_count, max(0, state.character_offset)) if chapter and state else 0
    return SkillBookReadingStatePayload(
        chapter_id=chapter.id if chapter else "",
        character_offset=offset,
        chapter_revision=state.chapter_revision if state and chapter else "",
        current_page=_reading_page(chapter, offset),
        page_format=page_format,
        updated_at=state.updated_at if state and chapter else None,
    )


@router.get("/local/catalog", response_model=SkillBookCatalog)
def get_local_skill_book_catalog(
    response: Response,
    bookshelf_id: str = Query(...),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
) -> SkillBookCatalog:
    asset, placement, _shelf, owner, role = _get_local_skill_book_context(
        session, current_user, bookshelf_id=bookshelf_id
    )
    _no_store(response)
    catalog, _lookup = _scan_skill_book(page_format=_asset_page_format(asset))
    return catalog.model_copy(update={
        "title": asset.title,
        "author": asset.author,
        "cover_color": asset.cover_color,
        "owner_user_id": owner.id,
        "owner_username": owner.username,
        "is_owned": current_user.id == owner.id,
        "access_role": role,
        "bookshelf_placement": SkillBookPlacementPayload(
            bookshelf_id=placement.bookshelf_id or "",
            shelf_index=placement.shelf_index,
            position_index=placement.position_index,
            orientation=placement.orientation,
        ),
    })


@router.get("/local/chapters/{chapter_id}", response_model=SkillBookChapterContent)
def get_local_skill_book_chapter(
    chapter_id: str,
    response: Response,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
) -> SkillBookChapterContent:
    asset, _placement, _shelf, _owner, _role = _get_local_skill_book_context(
        session, current_user
    )
    _no_store(response)
    page_format = _asset_page_format(asset)
    _catalog, lookup = _scan_skill_book(page_format=page_format)
    row = lookup.get(chapter_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Skill chapter not found")
    chapter, path = row
    raw_markdown = _read_markdown(path)
    _metadata, body = _split_frontmatter(raw_markdown)
    current_revision = hashlib.sha256(raw_markdown.encode("utf-8")).hexdigest()
    if current_revision != chapter.revision:
        line_capacity_units, _lines_per_page, page_capacity_units = _page_capacity(page_format)
        reading_unit_count = _estimate_markdown_reading_units(body, line_capacity_units)
        estimated_page_count = max(1, math.ceil(reading_unit_count / page_capacity_units))
        chapter = chapter.model_copy(update={
            "revision": current_revision,
            "character_count": len(body),
            "reading_unit_count": reading_unit_count,
            "estimated_page_count": estimated_page_count,
            "page_end": chapter.page_start + estimated_page_count - 1,
            "updated_at": path.stat().st_mtime,
        })
    return SkillBookChapterContent(chapter=chapter, markdown=body)


@router.get("/local/my-state", response_model=SkillBookReadingStatePayload)
def get_local_skill_book_reading_state(
    response: Response,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
) -> SkillBookReadingStatePayload:
    asset, _placement, _shelf, _owner, _role = _get_local_skill_book_context(
        session, current_user
    )
    _no_store(response)
    state = _get_reading_state(session, current_user.id)
    page_format = _asset_page_format(asset)
    _catalog, lookup = _scan_skill_book(page_format=page_format)
    return _serialize_reading_state(state, lookup, page_format=page_format)


@router.put("/local/my-state", response_model=SkillBookReadingStatePayload)
def update_local_skill_book_reading_state(
    payload: SkillBookReadingStateUpdate,
    response: Response,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
) -> SkillBookReadingStatePayload:
    asset, _placement, _shelf, _owner, _role = _get_local_skill_book_context(
        session, current_user
    )
    _no_store(response)
    state = _get_reading_state(session, current_user.id)
    page_format = _asset_page_format(asset)
    _catalog, lookup = _scan_skill_book(page_format=page_format)
    row = lookup.get(payload.chapter_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Skill chapter not found")
    chapter = row[0]
    now = time.time()
    if state is None:
        state = LibraryReadingState(
            resource_type="skill-book",
            resource_id=SKILL_BOOK_ID,
            user_id=current_user.id,
            created_at=now,
        )
    state.chapter_id = chapter.id
    state.character_offset = min(chapter.character_count, max(0, payload.character_offset))
    state.chapter_revision = payload.chapter_revision.strip() or chapter.revision
    state.updated_at = now
    session.add(state)
    session.commit()
    session.refresh(state)
    return _serialize_reading_state(state, lookup, page_format=page_format)


@router.put("/local/metadata", response_model=SkillBookCatalog)
def update_local_skill_book_metadata(
    payload: SkillBookMetadataUpdate,
    response: Response,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
) -> SkillBookCatalog:
    asset, placement, _shelf, owner, role = _get_local_skill_book_context(
        session, current_user, required_role="manager"
    )
    _no_store(response)
    normalized_page_format = _normalize_page_format(payload.page_format)
    if payload.page_format.strip().upper() not in SKILL_BOOK_PAGE_FORMATS:
        raise HTTPException(status_code=422, detail="Unsupported page format")
    now = time.time()
    asset.metadata_json = {**(asset.metadata_json or {}), "page_format": normalized_page_format}
    asset.updated_at = now
    session.add(asset)
    session.commit()
    catalog, _lookup = _scan_skill_book(page_format=normalized_page_format)
    return catalog.model_copy(update={
        "title": asset.title,
        "author": asset.author,
        "cover_color": asset.cover_color,
        "owner_user_id": owner.id,
        "owner_username": owner.username,
        "is_owned": True,
        "access_role": role,
        "bookshelf_placement": SkillBookPlacementPayload(
            bookshelf_id=placement.bookshelf_id or "",
            shelf_index=placement.shelf_index,
            position_index=placement.position_index,
            orientation=placement.orientation,
        ),
    })


@router.put("/local/placement", response_model=SkillBookPlacementPayload)
def update_local_skill_book_placement(
    payload: SkillBookPlacementUpdate,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
) -> SkillBookPlacementPayload:
    _asset, placement, _shelf, owner, _role = _get_local_skill_book_context(
        session, current_user, required_role="manager"
    )
    target_shelf = session.get(PdfLibraryBookshelf, payload.bookshelf_id)
    if target_shelf is None or target_shelf.user_id != owner.id:
        raise HTTPException(status_code=404, detail="Bookshelf not found")
    placement.bookshelf_id = target_shelf.id
    placement.shelf_index = payload.shelf_index
    placement.position_index = payload.position_index
    placement.orientation = payload.orientation
    placement.updated_at = time.time()
    session.add(placement)
    session.commit()
    session.refresh(placement)
    return SkillBookPlacementPayload(
        bookshelf_id=placement.bookshelf_id or "",
        shelf_index=placement.shelf_index,
        position_index=placement.position_index,
        orientation=placement.orientation,
    )
