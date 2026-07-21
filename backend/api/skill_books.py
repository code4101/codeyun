from __future__ import annotations

import base64
import hashlib
import math
import os
import re
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

from backend.core.access.auth import get_current_active_user
from backend.models import User


router = APIRouter()

SKILL_BOOK_ID = "local-skills"
SKILL_BOOK_TITLE = "本地 Skill 手册"
SKILL_BOOK_AUTHOR = "CodeYun"
MAX_SKILL_MARKDOWN_BYTES = 2 * 1024 * 1024


class SkillBookChapter(BaseModel):
    id: str
    skill_id: str
    title: str
    relative_path: str
    kind: Literal["main", "reference"]
    revision: str
    character_count: int
    updated_at: float


class SkillBookSkill(BaseModel):
    id: str
    name: str
    description: str = ""
    chapters: list[SkillBookChapter]
    updated_at: float


class SkillBookCatalog(BaseModel):
    id: str = SKILL_BOOK_ID
    title: str = SKILL_BOOK_TITLE
    author: str = SKILL_BOOK_AUTHOR
    cover_color: str = "#315f53"
    revision: str
    skill_count: int
    chapter_count: int
    estimated_page_count: int
    updated_at: float
    skills: list[SkillBookSkill]


class SkillBookChapterContent(BaseModel):
    book_id: str = SKILL_BOOK_ID
    chapter: SkillBookChapter
    markdown: str


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


def _chapter_payload(
    *,
    root: Path,
    skill_id: str,
    path: Path,
    kind: Literal["main", "reference"],
) -> tuple[SkillBookChapter, str, dict[str, str]]:
    raw_markdown = _read_markdown(path)
    metadata, body = _split_frontmatter(raw_markdown)
    relative_path = path.relative_to(root).as_posix()
    stat = path.stat()
    fallback_title = skill_id if kind == "main" else path.stem
    title = metadata.get("name", "").strip() if kind == "main" else ""
    title = title or _markdown_title(body, fallback_title)
    revision = hashlib.sha256(raw_markdown.encode("utf-8")).hexdigest()
    return (
        SkillBookChapter(
            id=_chapter_id(relative_path),
            skill_id=skill_id,
            title=title,
            relative_path=relative_path,
            kind=kind,
            revision=revision,
            character_count=len(body),
            updated_at=stat.st_mtime,
        ),
        body,
        metadata,
    )


def _scan_skill_book(root: Path | None = None) -> tuple[SkillBookCatalog, dict[str, tuple[SkillBookChapter, Path]]]:
    resolved_root = (root or _skills_root()).resolve(strict=False)
    if not resolved_root.is_dir():
        raise HTTPException(status_code=404, detail="Local Skill directory not found")

    skills: list[SkillBookSkill] = []
    chapter_lookup: dict[str, tuple[SkillBookChapter, Path]] = {}
    revision_parts: list[str] = []
    total_characters = 0
    latest_updated_at = 0.0

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
            )
            chapter_rows.append(chapter)

        for chapter in chapter_rows:
            chapter_lookup[chapter.id] = (chapter, resolved_root / chapter.relative_path)
            revision_parts.append(f"{chapter.relative_path}\0{chapter.revision}")
            total_characters += chapter.character_count
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
        estimated_page_count=max(1, math.ceil(total_characters / 900)),
        updated_at=latest_updated_at,
        skills=skills,
    )
    return catalog, chapter_lookup


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"


@router.get("/local/catalog", response_model=SkillBookCatalog)
def get_local_skill_book_catalog(
    response: Response,
    _current_user: User = Depends(get_current_active_user),
) -> SkillBookCatalog:
    _no_store(response)
    catalog, _lookup = _scan_skill_book()
    return catalog


@router.get("/local/chapters/{chapter_id}", response_model=SkillBookChapterContent)
def get_local_skill_book_chapter(
    chapter_id: str,
    response: Response,
    _current_user: User = Depends(get_current_active_user),
) -> SkillBookChapterContent:
    _no_store(response)
    _catalog, lookup = _scan_skill_book()
    row = lookup.get(chapter_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Skill chapter not found")
    chapter, path = row
    raw_markdown = _read_markdown(path)
    _metadata, body = _split_frontmatter(raw_markdown)
    current_revision = hashlib.sha256(raw_markdown.encode("utf-8")).hexdigest()
    if current_revision != chapter.revision:
        chapter = chapter.model_copy(update={
            "revision": current_revision,
            "character_count": len(body),
            "updated_at": path.stat().st_mtime,
        })
    return SkillBookChapterContent(chapter=chapter, markdown=body)
