from __future__ import annotations

import time
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field as PydanticField
from sqlalchemy import func, or_
from sqlmodel import Session, select

from backend.core.access.auth import get_current_active_user
from backend.db import get_session
from backend.models import GithubProject, User


router = APIRouter()


class GithubProjectSourceRef(BaseModel):
    source_type: str = "manual"
    source_key: str = ""
    source_label: str = ""
    seen_at: Optional[float] = None


class GithubProjectUpsertRequest(BaseModel):
    github_repo_id: int
    full_name: str
    html_url: str = ""
    default_branch: str = ""
    description: str = ""
    homepage: str = ""
    language: str = ""
    license_spdx_id: str = ""
    topics: list[str] = PydanticField(default_factory=list)
    stars: int = 0
    forks: int = 0
    open_issues: int = 0
    archived: bool = False
    disabled: bool = False
    private: bool = False
    created_at: str = ""
    pushed_at: str = ""
    updated_at: str = ""
    analysis_note: Optional[str] = None
    source: Optional[GithubProjectSourceRef] = None


class GithubProjectPatchRequest(BaseModel):
    analysis_note: Optional[str] = None
    needs_review: Optional[bool] = None


class GithubProjectRead(BaseModel):
    id: int
    github_repo_id: int
    full_name: str
    html_url: str
    default_branch: str
    description: str
    homepage: str
    language: str
    license_spdx_id: str
    topics: list[str]
    stars: int
    forks: int
    open_issues: int
    archived: bool
    disabled: bool
    private: bool
    created_at_github: str
    pushed_at: str
    updated_at: str
    last_seen_at: float
    last_checked_at: Optional[float]
    needs_review: bool
    analysis_note: str
    source_refs: list[dict[str, Any]]
    update_notes: list[dict[str, Any]]
    created_at: float
    updated_at_local: float


class GithubProjectListResponse(BaseModel):
    items: list[GithubProjectRead]
    total: int


class GithubProjectUpsertResponse(BaseModel):
    item: GithubProjectRead
    created: bool
    changed: bool


def _normalize_text(value: str | None) -> str:
    return str(value or "").strip()


def _normalize_topics(values: list[str] | None) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        topic = _normalize_text(value).lower()
        if not topic or topic in seen:
            continue
        result.append(topic)
        seen.add(topic)
    return result


def _source_ref_key(source: dict[str, Any]) -> tuple[str, str, str]:
    return (
        _normalize_text(source.get("source_type")),
        _normalize_text(source.get("source_key")),
        _normalize_text(source.get("source_label")),
    )


def _append_source_ref(project: GithubProject, source: GithubProjectSourceRef | None, *, now: float) -> None:
    if source is None:
        return
    row = {
        "source_type": _normalize_text(source.source_type) or "manual",
        "source_key": _normalize_text(source.source_key),
        "source_label": _normalize_text(source.source_label),
        "seen_at": float(source.seen_at or now),
    }
    refs = list(project.source_refs or [])
    row_key = _source_ref_key(row)
    for index, existing in enumerate(refs):
        if _source_ref_key(existing) == row_key:
            refs[index] = {**existing, "seen_at": row["seen_at"]}
            project.source_refs = refs
            return
    refs.append(row)
    project.source_refs = refs


def _serialize_project(project: GithubProject) -> GithubProjectRead:
    if project.id is None:
        raise ValueError("project id is required")
    return GithubProjectRead(
        id=int(project.id),
        github_repo_id=int(project.github_repo_id),
        full_name=project.full_name,
        html_url=project.html_url,
        default_branch=project.default_branch,
        description=project.description,
        homepage=project.homepage,
        language=project.language,
        license_spdx_id=project.license_spdx_id,
        topics=list(project.topics or []),
        stars=int(project.stars or 0),
        forks=int(project.forks or 0),
        open_issues=int(project.open_issues or 0),
        archived=bool(project.archived),
        disabled=bool(project.disabled),
        private=bool(project.private),
        created_at_github=project.created_at_github,
        pushed_at=project.pushed_at,
        updated_at=project.updated_at_github,
        last_seen_at=float(project.last_seen_at or 0),
        last_checked_at=project.last_checked_at,
        needs_review=bool(project.needs_review),
        analysis_note=project.analysis_note,
        source_refs=list(project.source_refs or []),
        update_notes=list(project.update_notes or []),
        created_at=float(project.created_at or 0),
        updated_at_local=float(project.updated_at or 0),
    )


@router.get("", response_model=GithubProjectListResponse)
def list_github_projects(
    q: str = "",
    needs_review: Optional[bool] = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_session),
    _current_user: User = Depends(get_current_active_user),
) -> GithubProjectListResponse:
    filters = []
    keyword = _normalize_text(q)
    if keyword:
        pattern = f"%{keyword}%"
        filters.append(
            or_(
                GithubProject.full_name.ilike(pattern),
                GithubProject.description.ilike(pattern),
                GithubProject.language.ilike(pattern),
            )
        )
    if needs_review is not None:
        filters.append(GithubProject.needs_review == needs_review)

    total = int(session.exec(select(func.count()).select_from(GithubProject).where(*filters)).one() or 0)
    rows = session.exec(
        select(GithubProject)
        .where(*filters)
        .order_by(GithubProject.needs_review.desc(), GithubProject.pushed_at.desc(), GithubProject.stars.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    return GithubProjectListResponse(items=[_serialize_project(row) for row in rows], total=total)


@router.get("/{project_id}", response_model=GithubProjectRead)
def get_github_project(
    project_id: int,
    session: Session = Depends(get_session),
    _current_user: User = Depends(get_current_active_user),
) -> GithubProjectRead:
    project = session.get(GithubProject, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="GitHub project not found")
    return _serialize_project(project)


@router.post("/upsert", response_model=GithubProjectUpsertResponse)
def upsert_github_project(
    req: GithubProjectUpsertRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
) -> GithubProjectUpsertResponse:
    full_name = _normalize_text(req.full_name)
    if not full_name or "/" not in full_name:
        raise HTTPException(status_code=400, detail="full_name must look like owner/repo")

    now = time.time()
    project = session.exec(select(GithubProject).where(GithubProject.github_repo_id == req.github_repo_id)).first()
    created = project is None
    changed = False
    if project is None:
        project = GithubProject(
            github_repo_id=req.github_repo_id,
            full_name=full_name,
            created_at=now,
            created_by_user_id=current_user.id,
            needs_review=True,
        )
        changed = True
    else:
        changed = project.pushed_at != _normalize_text(req.pushed_at) or project.updated_at_github != _normalize_text(req.updated_at)
        if changed:
            notes = list(project.update_notes or [])
            notes.append(
                {
                    "seen_at": now,
                    "old_pushed_at": project.pushed_at,
                    "new_pushed_at": _normalize_text(req.pushed_at),
                    "old_updated_at": project.updated_at_github,
                    "new_updated_at": _normalize_text(req.updated_at),
                }
            )
            project.update_notes = notes
            project.needs_review = True

    project.full_name = full_name
    project.html_url = _normalize_text(req.html_url)
    project.default_branch = _normalize_text(req.default_branch)
    project.description = _normalize_text(req.description)
    project.homepage = _normalize_text(req.homepage)
    project.language = _normalize_text(req.language)
    project.license_spdx_id = _normalize_text(req.license_spdx_id)
    project.topics = _normalize_topics(req.topics)
    project.stars = max(0, int(req.stars or 0))
    project.forks = max(0, int(req.forks or 0))
    project.open_issues = max(0, int(req.open_issues or 0))
    project.archived = bool(req.archived)
    project.disabled = bool(req.disabled)
    project.private = bool(req.private)
    project.created_at_github = _normalize_text(req.created_at)
    project.pushed_at = _normalize_text(req.pushed_at)
    project.updated_at_github = _normalize_text(req.updated_at)
    project.last_seen_at = now
    project.last_checked_at = now
    project.updated_at = now
    project.updated_by_user_id = current_user.id
    if req.analysis_note is not None:
        project.analysis_note = _normalize_text(req.analysis_note)
    _append_source_ref(project, req.source, now=now)

    session.add(project)
    session.commit()
    session.refresh(project)
    return GithubProjectUpsertResponse(item=_serialize_project(project), created=created, changed=changed)


@router.patch("/{project_id}", response_model=GithubProjectRead)
def patch_github_project(
    project_id: int,
    req: GithubProjectPatchRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
) -> GithubProjectRead:
    project = session.get(GithubProject, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="GitHub project not found")
    if req.analysis_note is not None:
        project.analysis_note = _normalize_text(req.analysis_note)
    if req.needs_review is not None:
        project.needs_review = bool(req.needs_review)
    project.updated_at = time.time()
    project.updated_by_user_id = current_user.id
    session.add(project)
    session.commit()
    session.refresh(project)
    return _serialize_project(project)
