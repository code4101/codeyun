from __future__ import annotations

import time
import anyio
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from sqlmodel import Session, func, or_, select

from backend.api.notes import (
    _append_note_history,
    _prepare_note_update_data,
    _record_note_metadata_feedback_safely,
    _serialize_note_read,
)
from backend.core.access.auth import get_current_active_user, get_optional_current_user_from_token
from backend.core.notes.refs import load_notes_by_refs, note_public_id, note_ref_aliases
from backend.core.notes.progress import is_note_system_custom_field_key
from backend.core.optimistic_mutation import changed_fields_from_request, stale_field_conflicts
from backend.db import get_session
from backend.models import NoteEdge, NoteNode, ResourceAccessGrant, User
from backend.schemas import NoteRead
from backend.core.resources.identity import RESOURCE_TYPE_NOTE
from backend.api.websocket_manager import manager as ws_manager


router = APIRouter()

NOTE_DOC_RESOURCE_TYPE = RESOURCE_TYPE_NOTE
RESOURCE_ACCESS_ROLES = ("deny", "viewer", "editor", "manager")
RESOURCE_ACCESS_ROLE_RANK = {"deny": 0, "viewer": 1, "editor": 2, "manager": 3}
RESOURCE_ACCESS_SUBJECT_ANONYMOUS = "anonymous"
RESOURCE_ACCESS_SUBJECT_USER = "user"


def _note_resource_update_room(note_ref: str) -> str:
    return f"resource:note:{note_ref}"


def _broadcast_note_resource_update(
    note: NoteNode,
    *,
    updated_by_user_id: int | None = None,
    mutation_id: str | None = None,
    client_instance_id: str | None = None,
    source_kind: str = "system",
) -> None:
    public_ref = _public_note_ref(note)
    message = {
        "type": "resource-updated",
        "resource_type": "note",
        "resource_id": public_ref,
        "version": int(note.version or 1),
        "updated_at": float(note.updated_at or time.time()),
        "updated_by_user_id": updated_by_user_id,
        "mutation_id": mutation_id,
        "client_instance_id": client_instance_id,
        "source_kind": source_kind,
    }
    try:
        anyio.from_thread.run(ws_manager.broadcast, _note_resource_update_room(public_ref), message)
    except RuntimeError:
        pass


class NoteDocAccessCapabilities(BaseModel):
    can_read: bool = False
    can_edit_content: bool = False
    can_manage_access: bool = False


class NoteDocResourceAccess(BaseModel):
    role: Literal["none", "deny", "viewer", "editor", "manager"] = "none"
    capabilities: NoteDocAccessCapabilities = Field(default_factory=NoteDocAccessCapabilities)


class NoteDocDetail(NoteRead):
    access: NoteDocResourceAccess


class NoteDocUpdateRequest(BaseModel):
    base_version: Optional[int] = Field(default=None, ge=1)
    expected_fields: Optional[dict[str, Any]] = None
    mutation_id: Optional[str] = Field(default=None, max_length=128)
    client_instance_id: Optional[str] = Field(default=None, max_length=128)
    title: Optional[str] = None
    content: Optional[str] = None
    weight: Optional[int] = None
    start_at: Optional[float] = None
    note_categories: Optional[list[dict[str, Any]]] = None
    primary_category: Optional[str] = None
    note_form: Optional[str] = None
    note_scene: Optional[str] = None
    lifecycle_stage: Optional[str] = None
    color: Optional[str] = None
    private_level: Optional[int] = None
    custom_fields: Optional[list[list[Any]]] = None
    completion_progress_expr: Optional[str] = None


class NoteDocResourceAccessGrantItem(BaseModel):
    subject_type: Literal["anonymous", "user"]
    subject_key: str
    subject_user_id: Optional[int] = None
    username: str = ""
    nickname: str = ""
    role: Literal["deny", "viewer", "editor", "manager"]


class NoteDocResourceAccessGrantUpdate(BaseModel):
    subject_type: Literal["anonymous", "user"]
    username: Optional[str] = None
    subject_user_id: Optional[int] = None
    role: Literal["none", "deny", "viewer", "editor", "manager"]


class NoteDocResourceAccessUpdateRequest(BaseModel):
    grants: list[NoteDocResourceAccessGrantUpdate] = Field(default_factory=list)


class NoteDocResourceAccessResponse(BaseModel):
    resource_type: Literal["note"] = NOTE_DOC_RESOURCE_TYPE
    resource_id: int
    public_id: int
    access: NoteDocResourceAccess
    grants: list[NoteDocResourceAccessGrantItem] = Field(default_factory=list)


def _is_numeric_note_ref(value: str) -> bool:
    text = str(value or "").strip()
    return bool(text) and text.isdecimal()


def _get_note_by_ref_or_404(session: Session, note_ref: str) -> NoteNode:
    normalized_ref = str(note_ref or "").strip()
    if not _is_numeric_note_ref(normalized_ref):
        raise HTTPException(status_code=404, detail="文档不存在")

    query = (
        select(NoteNode)
        .where(NoteNode.numeric_id == int(normalized_ref))
        .where(or_(NoteNode.deleted_at.is_(None), NoteNode.deleted_at <= 0))
    )
    note = session.exec(query).first()
    if note is None:
        raise HTTPException(status_code=404, detail="文档不存在")
    return note


def _require_note_resource_id(note: NoteNode) -> str:
    return _public_note_ref(note)


def _require_note_public_id(note: NoteNode) -> int:
    numeric_id = int(note.numeric_id or 0)
    if numeric_id <= 0:
        raise HTTPException(status_code=500, detail="文档资源编号缺失")
    return numeric_id


def _require_note_legacy_id(note: NoteNode) -> str:
    note_id = str(getattr(note, "legacy_id", None) or note.id or "").strip()
    if not note_id:
        raise HTTPException(status_code=500, detail="文档内部 ID 缺失")
    return note_id


def _public_note_ref(note: NoteNode) -> str:
    if note.numeric_id is not None and int(note.numeric_id) > 0:
        return str(note.numeric_id)
    note_id = str(getattr(note, "legacy_id", None) or note.id or "").strip()
    if not note_id:
        raise HTTPException(status_code=500, detail="文档资源 ID 缺失")
    return note_id


def _normalize_resource_role(value: Any) -> Literal["deny", "viewer", "editor", "manager"] | None:
    role = str(value or "").strip()
    if role in RESOURCE_ACCESS_ROLES:
        return role  # type: ignore[return-value]
    return None


def _build_resource_access(role: str | None, current_user: User | None) -> NoteDocResourceAccess:
    normalized_role = _normalize_resource_role(role) if role else None
    if normalized_role is None:
        return NoteDocResourceAccess(role="none")

    rank = RESOURCE_ACCESS_ROLE_RANK[normalized_role]
    can_read = rank >= RESOURCE_ACCESS_ROLE_RANK["viewer"]
    can_edit_content = (
        current_user is not None
        and rank >= RESOURCE_ACCESS_ROLE_RANK["editor"]
    )
    return NoteDocResourceAccess(
        role=normalized_role,
        capabilities=NoteDocAccessCapabilities(
            can_read=can_read,
            can_edit_content=can_edit_content,
            can_manage_access=rank >= RESOURCE_ACCESS_ROLE_RANK["manager"],
        ),
    )


def _resource_role_allows(
    role: str | None,
    required_role: Literal["viewer", "editor", "manager"],
) -> bool:
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


def _fetch_resource_grants(session: Session, note: NoteNode) -> list[ResourceAccessGrant]:
    return list(session.exec(
        select(ResourceAccessGrant)
        .where(ResourceAccessGrant.resource_type == NOTE_DOC_RESOURCE_TYPE)
        .where(ResourceAccessGrant.resource_id == _require_note_resource_id(note))
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


def _resolve_doc_resource_access(
    session: Session,
    note: NoteNode,
    current_user: User | None,
) -> NoteDocResourceAccess:
    if _is_superuser_or_owner(current_user, note.user_id):
        return _build_resource_access("manager", current_user)
    role = _resolve_subject_grant_role(_fetch_resource_grants(session, note), current_user)
    return _build_resource_access(role, current_user)


def _require_resource_access(
    access: NoteDocResourceAccess,
    required_role: Literal["viewer", "editor", "manager"],
) -> None:
    if not _resource_role_allows(access.role, required_role):
        raise HTTPException(status_code=403, detail="没有该文档权限")


def _collect_custom_fields_from_note(note: NoteNode) -> dict[str, list[Any]]:
    result: dict[str, list[Any]] = {}
    custom_fields = note.custom_fields
    if isinstance(custom_fields, list):
        for field_item in custom_fields:
            if isinstance(field_item, list) and len(field_item) >= 3:
                key = str(field_item[0] or "").strip()
                if not key or is_note_system_custom_field_key(key):
                    continue
                result[key] = [key, str(field_item[1] or "string"), field_item[2]]
    elif isinstance(custom_fields, dict):
        for key, value in custom_fields.items():
            normalized_key = str(key or "").strip()
            if not normalized_key or is_note_system_custom_field_key(normalized_key):
                continue
            result[normalized_key] = [normalized_key, "string", value]
    return result


def _build_inherited_fields(session: Session, note: NoteNode) -> dict[str, list[list[Any]]]:
    note_refs = note_ref_aliases(note)
    direct_parent_edges = session.exec(
        select(NoteEdge).where(
            NoteEdge.user_id == note.user_id,
            NoteEdge.target_id.in_(note_refs),
        )
    ).all()
    parent_ids = [edge.source_id for edge in direct_parent_edges]

    parent_nodes: list[NoteNode] = list({note_public_id(parent): parent for parent in load_notes_by_refs(session, note.user_id, parent_ids).values()}.values())

    direct_parent_fields: dict[str, list[Any]] = {}
    for parent_node in parent_nodes:
        direct_parent_fields.update(_collect_custom_fields_from_note(parent_node))

    ancestor_fields: dict[str, list[Any]] = {}
    visited_ancestors = {note_public_id(parent) for parent in parent_nodes}
    queue = sorted({ref for parent in parent_nodes for ref in note_ref_aliases(parent)})
    max_depth = 3
    current_depth = 0
    while queue and current_depth < max_depth:
        upstream_edges = session.exec(
            select(NoteEdge).where(
                NoteEdge.user_id == note.user_id,
                NoteEdge.target_id.in_(queue),
            )
        ).all()
        source_ref_map = load_notes_by_refs(session, note.user_id, [edge.source_id for edge in upstream_edges])
        new_ancestor_nodes: list[NoteNode] = []
        for edge in upstream_edges:
            source_note = source_ref_map.get(str(edge.source_id))
            if source_note is None:
                continue
            source_id = note_public_id(source_note)
            if source_id not in visited_ancestors and source_id != note_public_id(note):
                visited_ancestors.add(source_id)
                new_ancestor_nodes.append(source_note)

        if not new_ancestor_nodes:
            queue = []
            current_depth += 1
            continue

        for ancestor_node in new_ancestor_nodes:
            ancestor_fields.update(_collect_custom_fields_from_note(ancestor_node))
        queue = sorted({ref for ancestor_node in new_ancestor_nodes for ref in note_ref_aliases(ancestor_node)})
        current_depth += 1

    for key in list(ancestor_fields.keys()):
        if key in direct_parent_fields:
            del ancestor_fields[key]

    return {
        "direct": list(direct_parent_fields.values()),
        "ancestors": list(ancestor_fields.values()),
    }


def _serialize_doc_note_detail(
    session: Session,
    note: NoteNode,
    *,
    current_user: User | None,
    access: NoteDocResourceAccess,
) -> dict[str, Any]:
    note_refs = note_ref_aliases(note)
    edge_count = session.exec(
        select(func.count()).select_from(NoteEdge).where(
            NoteEdge.user_id == note.user_id,
            or_(NoteEdge.source_id.in_(note_refs), NoteEdge.target_id.in_(note_refs)),
        )
    ).one()
    out_degree = session.exec(
        select(func.count()).select_from(NoteEdge).where(
            NoteEdge.user_id == note.user_id,
            NoteEdge.source_id.in_(note_refs),
        )
    ).one()
    return _serialize_note_read(
        note,
        current_user,
        can_edit=access.capabilities.can_edit_content,
        edge_count=edge_count,
        out_degree=out_degree,
        inherited_fields=_build_inherited_fields(session, note),
        access=access.model_dump(),
    )


def _serialize_access_grants(session: Session, note: NoteNode) -> list[NoteDocResourceAccessGrantItem]:
    grants = _fetch_resource_grants(session, note)
    user_ids = sorted({
        int(grant.subject_user_id)
        for grant in grants
        if grant.subject_user_id is not None
    })
    users = session.exec(select(User).where(User.id.in_(user_ids))).all() if user_ids else []
    user_map = {user.id: user for user in users}
    result: list[NoteDocResourceAccessGrantItem] = []

    for grant in sorted(grants, key=lambda item: (item.subject_type, item.subject_key)):
        role = _normalize_resource_role(grant.role)
        if role is None:
            continue
        user = user_map.get(grant.subject_user_id) if grant.subject_user_id is not None else None
        result.append(NoteDocResourceAccessGrantItem(
            subject_type=grant.subject_type,  # type: ignore[arg-type]
            subject_key=grant.subject_key,
            subject_user_id=grant.subject_user_id,
            username=user.username if user is not None else "",
            nickname=user.nickname if user is not None else "",
            role=role,
        ))
    return result


def _build_resource_access_response(
    session: Session,
    note: NoteNode,
    access: NoteDocResourceAccess,
) -> NoteDocResourceAccessResponse:
    public_id = _require_note_public_id(note)
    return NoteDocResourceAccessResponse(
        resource_id=public_id,
        public_id=public_id,
        access=access,
        grants=_serialize_access_grants(session, note),
    )


def _save_resource_access_grants(
    session: Session,
    *,
    note: NoteNode,
    payload: NoteDocResourceAccessUpdateRequest,
    current_user: User,
) -> None:
    now = time.time()
    existing = {
        grant.subject_key: grant
        for grant in _fetch_resource_grants(session, note)
    }
    normalized_items: dict[str, tuple[str, int | None, str]] = {}

    for item in payload.grants:
        if item.subject_type == RESOURCE_ACCESS_SUBJECT_ANONYMOUS:
            subject_type = RESOURCE_ACCESS_SUBJECT_ANONYMOUS
            subject_user_id = None
            subject_key = RESOURCE_ACCESS_SUBJECT_ANONYMOUS
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

    resource_id = _require_note_resource_id(note)
    for subject_key, (subject_type, subject_user_id, role) in normalized_items.items():
        grant = existing.get(subject_key)
        if grant is None:
            grant = ResourceAccessGrant(
                resource_type=NOTE_DOC_RESOURCE_TYPE,
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


@router.get("/{note_ref}", response_model=NoteDocDetail)
async def read_note_doc(
    note_ref: str,
    current_user: User | None = Depends(get_optional_current_user_from_token),
    session: Session = Depends(get_session),
):
    note = _get_note_by_ref_or_404(session, note_ref)
    access = _resolve_doc_resource_access(session, note, current_user)
    _require_resource_access(access, "viewer")
    return _serialize_doc_note_detail(session, note, current_user=current_user, access=access)


@router.put("/{note_ref}", response_model=NoteDocDetail)
def update_note_doc(
    note_ref: str,
    note_in: NoteDocUpdateRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    note = _get_note_by_ref_or_404(session, note_ref)
    access = _resolve_doc_resource_access(session, note, current_user)
    _require_resource_access(access, "editor")
    raw_request = note_in.model_dump(exclude_unset=True)
    requested_updates = changed_fields_from_request(raw_request)
    if note_in.base_version is not None and int(note.version or 1) != int(note_in.base_version):
        conflicts = stale_field_conflicts(note, requested_updates, note_in.expected_fields)
        if conflicts:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "文档中本次编辑的字段已发生变化，请合并后重试",
                    "conflicting_fields": conflicts,
                    "current_version": int(note.version or 1),
                },
            )

    note_data = _prepare_note_update_data(note, requested_updates)
    _append_note_history(note, note_data, int(time.time()))
    _record_note_metadata_feedback_safely(
        session,
        note=note,
        updates=note_data,
        source_kind="doc_resource_update",
    )
    for key, value in note_data.items():
        setattr(note, key, value)
    if note_data:
        note.version = max(int(note.version or 1), 1) + 1
    note.updated_at = time.time()
    session.add(note)
    session.commit()
    session.refresh(note)
    _broadcast_note_resource_update(
        note,
        updated_by_user_id=current_user.id,
        mutation_id=note_in.mutation_id,
        client_instance_id=note_in.client_instance_id,
        source_kind="user",
    )

    next_access = _resolve_doc_resource_access(session, note, current_user)
    return _serialize_doc_note_detail(session, note, current_user=current_user, access=next_access)


@router.websocket("/ws/resources/note/{note_ref}")
async def websocket_note_resource_updates(websocket: WebSocket, note_ref: str):
    room = _note_resource_update_room(str(note_ref or "").strip())
    await ws_manager.connect(websocket, room)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket, room)
    except Exception:
        ws_manager.disconnect(websocket, room)


@router.get("/{note_ref}/access", response_model=NoteDocResourceAccessResponse)
def read_note_doc_access(
    note_ref: str,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    note = _get_note_by_ref_or_404(session, note_ref)
    access = _resolve_doc_resource_access(session, note, current_user)
    _require_resource_access(access, "manager")
    return _build_resource_access_response(session, note, access)


@router.put("/{note_ref}/access", response_model=NoteDocResourceAccessResponse)
def update_note_doc_access(
    note_ref: str,
    payload: NoteDocResourceAccessUpdateRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    note = _get_note_by_ref_or_404(session, note_ref)
    access = _resolve_doc_resource_access(session, note, current_user)
    _require_resource_access(access, "manager")
    _save_resource_access_grants(session, note=note, payload=payload, current_user=current_user)
    session.commit()
    next_access = _resolve_doc_resource_access(session, note, current_user)
    return _build_resource_access_response(session, note, next_access)
