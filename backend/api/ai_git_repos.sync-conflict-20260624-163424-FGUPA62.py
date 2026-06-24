from __future__ import annotations

from typing import Optional

import requests
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlmodel import Session

from backend.api.git_tools import GitToolInspectRequest, GitToolInspectResponse
from backend.core.ai_git_repos import (
    list_user_ai_git_repos,
    save_user_ai_git_repos,
    touch_user_ai_git_repo,
)
from backend.core.auth import get_current_user_from_token
from backend.core.git_tools import GitToolError, inspect_git_repository
from backend.db import get_session
from backend.models import User, UserDevice


router = APIRouter()


class AiGitSavedRepo(BaseModel):
    id: str
    name: str
    entry_id: str
    cwd: str
    pinned: bool = False
    order_index: int = 0
    created_at: Optional[float] = None
    updated_at: Optional[float] = None
    last_used_at: Optional[float] = None


class AiGitSavedRepoInput(BaseModel):
    id: str = ""
    name: str = ""
    entry_id: str
    cwd: str
    pinned: bool = False
    order_index: int = 0
    created_at: Optional[float] = None
    updated_at: Optional[float] = None
    last_used_at: Optional[float] = None


class AiGitSavedReposResponse(BaseModel):
    items: list[AiGitSavedRepo] = Field(default_factory=list)


class AiGitSavedReposUpdateRequest(BaseModel):
    items: list[AiGitSavedRepoInput] = Field(default_factory=list)


class AiGitSavedRepoTouchResponse(BaseModel):
    ok: bool = True
    item: Optional[AiGitSavedRepo] = None


class AiGitRepoStatusesRequest(BaseModel):
    repo_ids: list[str] = Field(default_factory=list)


class AiGitRepoStatusItem(BaseModel):
    repo_id: str
    name: str
    entry_id: str
    cwd: str
    ok: bool
    clean: Optional[bool] = None
    branch: str = ""
    branch_status: str = ""
    repo_root: Optional[str] = None
    changed_file_count: int = 0
    estimated_changed_line_count: int = 0
    changed_paths: list[str] = Field(default_factory=list)
    split_recommended: bool = False
    split_reason: str = ""
    oversized: bool = False
    suggested_split_groups: list[dict[str, object]] = Field(default_factory=list)
    error: Optional[str] = None


class AiGitRepoStatusesResponse(BaseModel):
    items: list[AiGitRepoStatusItem] = Field(default_factory=list)


def _find_owned_entry(session: Session, current_user: User, entry_id: str) -> UserDevice | None:
    entry = session.get(UserDevice, entry_id)
    if entry is None or entry.user_id != current_user.id or not entry.is_active:
        return None
    return entry


def _remote_base_url(entry: UserDevice) -> str:
    if entry.mode != "remote":
        raise RuntimeError("This entry is not a remote entry")
    if not entry.server_url:
        raise RuntimeError("Remote entry has no server_url configured")
    return entry.server_url.rstrip("/")


def _proxy_headers(entry: UserDevice) -> dict[str, str]:
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
        raw_detail = payload.get("detail") or payload.get("message") or payload.get("error")
        if isinstance(raw_detail, str) and raw_detail.strip():
            return raw_detail.strip()

    return resp.text.strip() or f"Remote request failed with HTTP {resp.status_code}"


def _inspect_repo_for_entry(entry: UserDevice, cwd: str) -> dict[str, object]:
    if entry.mode == "local":
        return inspect_git_repository(cwd)

    target_url = f"{_remote_base_url(entry)}/api/git-tools/inspect"
    try:
        resp = requests.request(
            method="POST",
            url=target_url,
            headers=_proxy_headers(entry),
            json=GitToolInspectRequest(cwd=cwd).model_dump(),
            timeout=20,
        )
    except requests.RequestException as exc:
        raise RuntimeError(f"Failed to reach remote device: {exc}") from exc

    if resp.status_code >= 400:
        raise RuntimeError(_extract_remote_error(resp))

    try:
        payload = resp.json()
    except ValueError as exc:
        raise RuntimeError("远程设备返回了无效的 JSON") from exc

    return GitToolInspectResponse.model_validate(payload).model_dump()


def _build_status_item(repo: dict[str, object], inspect_payload: dict[str, object] | None, error: str | None) -> AiGitRepoStatusItem:
    changed_files = inspect_payload.get("changed_files", []) if isinstance(inspect_payload, dict) else []
    changed_paths = [
        str(item.get("path") or "")
        for item in changed_files
        if isinstance(item, dict) and isinstance(item.get("path"), str) and item.get("path")
    ]
    return AiGitRepoStatusItem(
        repo_id=str(repo["id"]),
        name=str(repo["name"]),
        entry_id=str(repo["entry_id"]),
        cwd=str(repo["cwd"]),
        ok=error is None and inspect_payload is not None,
        clean=bool(inspect_payload["clean"]) if inspect_payload is not None else None,
        branch=str(inspect_payload.get("branch") or "") if inspect_payload is not None else "",
        branch_status=str(inspect_payload.get("branch_status") or "") if inspect_payload is not None else "",
        repo_root=str(inspect_payload.get("repo_root") or "") if inspect_payload is not None else None,
        changed_file_count=len(changed_paths),
        estimated_changed_line_count=int(inspect_payload.get("estimated_changed_line_count") or 0) if inspect_payload is not None else 0,
        changed_paths=changed_paths,
        split_recommended=bool(inspect_payload.get("split_recommended")) if inspect_payload is not None else False,
        split_reason=str(inspect_payload.get("split_reason") or "") if inspect_payload is not None else "",
        oversized=bool(inspect_payload.get("oversized")) if inspect_payload is not None else False,
        suggested_split_groups=list(inspect_payload.get("suggested_split_groups") or []) if inspect_payload is not None else [],
        error=error,
    )


@router.get("", response_model=AiGitSavedReposResponse)
def get_ai_git_saved_repos(
    current_user: User = Depends(get_current_user_from_token),
    session: Session = Depends(get_session),
):
    payload = list_user_ai_git_repos(session, current_user.id)
    return AiGitSavedReposResponse(
        items=[AiGitSavedRepo.model_validate(item) for item in payload["items"]],
    )


@router.put("", response_model=AiGitSavedReposResponse)
def put_ai_git_saved_repos(
    payload: AiGitSavedReposUpdateRequest,
    current_user: User = Depends(get_current_user_from_token),
    session: Session = Depends(get_session),
):
    saved = save_user_ai_git_repos(
        session,
        current_user.id,
        items=[item.model_dump() for item in payload.items],
    )
    return AiGitSavedReposResponse(
        items=[AiGitSavedRepo.model_validate(item) for item in saved["items"]],
    )


@router.post("/{repo_id}/touch", response_model=AiGitSavedRepoTouchResponse)
def post_ai_git_saved_repo_touch(
    repo_id: str,
    current_user: User = Depends(get_current_user_from_token),
    session: Session = Depends(get_session),
):
    item = touch_user_ai_git_repo(session, current_user.id, repo_id)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="项目不存在")
    return AiGitSavedRepoTouchResponse(
        ok=True,
        item=AiGitSavedRepo.model_validate(item),
    )


@router.post("/statuses", response_model=AiGitRepoStatusesResponse)
def post_ai_git_repo_statuses(
    payload: AiGitRepoStatusesRequest,
    current_user: User = Depends(get_current_user_from_token),
    session: Session = Depends(get_session),
):
    saved = list_user_ai_git_repos(session, current_user.id)
    repo_id_filter = {item for item in payload.repo_ids if item}
    repos = [
        item
        for item in saved["items"]
        if not repo_id_filter or item["id"] in repo_id_filter
    ]

    results: list[AiGitRepoStatusItem] = []
    for repo in repos:
        entry = _find_owned_entry(session, current_user, str(repo["entry_id"]))
        if entry is None:
            results.append(_build_status_item(repo, None, "关联设备不存在或已停用"))
            continue

        try:
            inspect_payload = _inspect_repo_for_entry(entry, str(repo["cwd"]))
            results.append(_build_status_item(repo, inspect_payload, None))
        except (GitToolError, RuntimeError) as exc:
            results.append(_build_status_item(repo, None, str(exc)))

    return AiGitRepoStatusesResponse(items=results)
