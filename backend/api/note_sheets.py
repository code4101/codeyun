from __future__ import annotations

from copy import deepcopy
import time
import re
from math import ceil
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlmodel import Session, delete, select

from backend.core.auth import get_current_active_user
from backend.core.feature_access_guard import require_feature_access_dependency
from backend.db import get_session
from backend.models import (
    SheetDocument,
    User,
    WorkbookDocument,
    WorkbookSheetLink,
)


router = APIRouter(
    dependencies=[Depends(require_feature_access_dependency("notes.sheets"))],
)

DEFAULT_NOTE_SHEET_PAGE_SIZE = 100
MAX_NOTE_SHEET_PAGE_SIZE = 1000
NATURAL_SORT_SPLIT_RE = re.compile(r"(\d+)")


class WorkbookRefItem(BaseModel):
    id: int
    title: str


class NoteSheetSummaryResponse(BaseModel):
    id: int
    title: str
    engine: str
    scope: str
    owner_user_id: Optional[int] = None
    created_by_user_id: Optional[int] = None
    updated_by_user_id: Optional[int] = None
    created_at: float
    updated_at: float
    workbook_items: list[WorkbookRefItem] = Field(default_factory=list)


class NoteSheetDetailResponse(NoteSheetSummaryResponse):
    owner_type: str
    owner_key: str
    sheet_key: str
    version: int
    document_json: dict[str, Any] = Field(default_factory=dict)
    pagination: Optional["NoteSheetPaginationResponse"] = None


class NoteSheetPaginationResponse(BaseModel):
    page: int = 1
    page_size: int = DEFAULT_NOTE_SHEET_PAGE_SIZE
    total_rows: int = 0
    page_count: int = 1
    row_offset: int = 0
    loaded_row_count: int = 0


class NoteSheetPagePatchRequest(BaseModel):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=DEFAULT_NOTE_SHEET_PAGE_SIZE, ge=1, le=MAX_NOTE_SHEET_PAGE_SIZE)
    row_offset: int = Field(default=0, ge=0)
    loaded_row_count: int = Field(default=0, ge=0)


class NoteSheetCreateRequest(BaseModel):
    title: str = ""
    workbook_id: Optional[int] = None
    document_json: dict[str, Any] = Field(default_factory=dict)


class NoteSheetUpdateRequest(BaseModel):
    title: Optional[str] = None
    document_json: Optional[dict[str, Any]] = None
    page_patch: Optional[NoteSheetPagePatchRequest] = None


class NoteSheetSortRequest(BaseModel):
    column_index: int = Field(ge=0)
    direction: Literal["asc", "desc"] = "asc"


class WorkbookSummaryResponse(BaseModel):
    id: int
    title: str
    owner_user_id: Optional[int] = None
    created_by_user_id: Optional[int] = None
    updated_by_user_id: Optional[int] = None
    created_at: float
    updated_at: float
    sheet_count: int = 0


class WorkbookDetailResponse(WorkbookSummaryResponse):
    sheets: list[NoteSheetSummaryResponse] = Field(default_factory=list)


class WorkbookCreateRequest(BaseModel):
    title: str = ""


class WorkbookAttachSheetRequest(BaseModel):
    sheet_id: int


class WorkbookSaveAsRequest(BaseModel):
    mode: Literal["template", "duplicate"] = "duplicate"
    title: str = ""


def _create_default_sheet_document() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "columns": ["列1", "列2", "列3"],
        "rows": [],
        "view_settings": {
            "show_row_numbers": True,
            "show_column_markers": True,
            "column_marker_style": "letters",
            "pagination": {
                "enabled": False,
                "page_size": DEFAULT_NOTE_SHEET_PAGE_SIZE,
            },
        },
    }


def _normalize_document_json(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return _create_default_sheet_document()
    return dict(value)


def _normalize_page_size(value: int | None) -> int:
    numeric = int(value or DEFAULT_NOTE_SHEET_PAGE_SIZE)
    return min(max(numeric, 1), MAX_NOTE_SHEET_PAGE_SIZE)


def _extract_document_rows(document_json: dict[str, Any]) -> list[Any]:
    rows = document_json.get("rows")
    return list(rows) if isinstance(rows, list) else []


def _get_document_pagination_settings(document_json: dict[str, Any]) -> tuple[bool, int]:
    normalized = _normalize_document_json(document_json)
    view_settings = normalized.get("view_settings")
    if not isinstance(view_settings, dict):
        return False, DEFAULT_NOTE_SHEET_PAGE_SIZE

    pagination = view_settings.get("pagination")
    if not isinstance(pagination, dict):
        return False, DEFAULT_NOTE_SHEET_PAGE_SIZE

    enabled = pagination.get("enabled") is True
    page_size = _normalize_page_size(pagination.get("page_size"))
    return enabled, page_size


def _build_paged_document(
    document_json: dict[str, Any],
    *,
    page: int,
    page_size: int,
) -> tuple[dict[str, Any], NoteSheetPaginationResponse]:
    normalized = _normalize_document_json(document_json)
    all_rows = _extract_document_rows(normalized)
    safe_page_size = _normalize_page_size(page_size)
    actual_page_count = max(1, ceil(len(all_rows) / safe_page_size) if all_rows else 1)
    safe_page = min(max(int(page or 1), 1), actual_page_count)
    row_offset = min((safe_page - 1) * safe_page_size, len(all_rows))
    page_rows = all_rows[row_offset: row_offset + safe_page_size]

    return (
        {
            **normalized,
            "rows": page_rows,
        },
        NoteSheetPaginationResponse(
            page=safe_page,
            page_size=safe_page_size,
            total_rows=len(all_rows),
            page_count=actual_page_count,
            row_offset=row_offset,
            loaded_row_count=len(page_rows),
        ),
    )


def _build_workspace_pagination(
    *,
    page_patch: NoteSheetPagePatchRequest,
    total_rows: int,
    current_row_count: int,
) -> NoteSheetPaginationResponse:
    safe_page_size = _normalize_page_size(page_patch.page_size)
    actual_page_count = max(1, ceil(total_rows / safe_page_size) if total_rows else 1)
    display_page = max(int(page_patch.page or 1), 1)
    return NoteSheetPaginationResponse(
        page=display_page,
        page_size=safe_page_size,
        total_rows=total_rows,
        page_count=max(actual_page_count, display_page),
        row_offset=min(int(page_patch.row_offset or 0), total_rows),
        loaded_row_count=max(current_row_count, 0),
    )


def _merge_paged_document(
    current_document: dict[str, Any],
    incoming_document: dict[str, Any],
    page_patch: NoteSheetPagePatchRequest,
) -> dict[str, Any]:
    normalized_current = _normalize_document_json(current_document)
    normalized_incoming = _normalize_document_json(incoming_document)

    current_rows = _extract_document_rows(normalized_current)
    incoming_rows = _extract_document_rows(normalized_incoming)
    row_offset = min(max(int(page_patch.row_offset or 0), 0), len(current_rows))
    loaded_row_count = max(int(page_patch.loaded_row_count or 0), 0)
    tail_start = min(row_offset + loaded_row_count, len(current_rows))

    return {
        **normalized_current,
        **{
            key: value
            for key, value in normalized_incoming.items()
            if key != "rows"
        },
        "rows": [
            *current_rows[:row_offset],
            *incoming_rows,
            *current_rows[tail_start:],
        ],
    }


def _normalize_title(value: str | None, *, default_value: str) -> str:
    normalized = str(value or "").strip()
    return normalized or default_value


def _extract_sort_cell_text(row: Any, column_index: int, columns: list[Any]) -> str:
    if isinstance(row, list):
        raw_value = row[column_index] if column_index < len(row) else ""
    elif isinstance(row, dict):
        column_key = str(columns[column_index]) if column_index < len(columns) else ""
        raw_value = row.get(column_key, "")
    else:
        raw_value = ""
    return "" if raw_value is None else str(raw_value).strip()


def _natural_sort_key(value: str) -> tuple[tuple[int, Any], ...]:
    normalized = value.casefold()
    parts = NATURAL_SORT_SPLIT_RE.split(normalized)
    key: list[tuple[int, Any]] = []
    for part in parts:
        if not part:
            continue
        if part.isdigit():
            key.append((0, int(part)))
        else:
            key.append((1, part))
    return tuple(key)


def _sort_sheet_document_rows(
    document_json: dict[str, Any],
    *,
    column_index: int,
    direction: Literal["asc", "desc"],
) -> dict[str, Any]:
    normalized = _normalize_document_json(document_json)
    all_rows = _extract_document_rows(normalized)
    columns = list(normalized.get("columns") or [])

    sortable_rows: list[Any] = []
    empty_value_rows: list[Any] = []
    blank_rows: list[Any] = []

    for row in all_rows:
        if isinstance(row, list):
            row_values = row
        elif isinstance(row, dict):
            row_values = list(row.values())
        else:
            row_values = []
        if not any(str(cell or "").strip() for cell in row_values):
            blank_rows.append(row)
            continue

        cell_text = _extract_sort_cell_text(row, column_index, columns)
        if cell_text == "":
            empty_value_rows.append(row)
        else:
            sortable_rows.append(row)

    sorted_rows = sorted(
        sortable_rows,
        key=lambda row: _natural_sort_key(_extract_sort_cell_text(row, column_index, columns)),
        reverse=direction == "desc",
    )

    return {
        **normalized,
        "rows": [
            *sorted_rows,
            *empty_value_rows,
            *blank_rows,
        ],
    }


def _get_next_sheet_numeric_id(session: Session) -> int:
    current_max = session.exec(
        select(SheetDocument.numeric_id)
        .where(SheetDocument.numeric_id.is_not(None))
        .order_by(SheetDocument.numeric_id.desc())
    ).first()
    return max(int(current_max or 0), 0) + 1


def _get_next_workbook_numeric_id(session: Session) -> int:
    current_max = session.exec(
        select(WorkbookDocument.numeric_id)
        .where(WorkbookDocument.numeric_id.is_not(None))
        .order_by(WorkbookDocument.numeric_id.desc())
    ).first()
    return max(int(current_max or 0), 0) + 1


def _require_sheet_numeric_id(document: SheetDocument) -> int:
    numeric_id = int(document.numeric_id or 0)
    if numeric_id <= 0:
        raise HTTPException(status_code=500, detail="表格编号缺失")
    return numeric_id


def _require_workbook_numeric_id(workbook: WorkbookDocument) -> int:
    numeric_id = int(workbook.numeric_id or 0)
    if numeric_id <= 0:
        raise HTTPException(status_code=500, detail="工作簿编号缺失")
    return numeric_id


def _is_sheet_owner(document: SheetDocument, current_user: User) -> bool:
    return current_user.is_superuser or document.owner_user_id == current_user.id


def _is_workbook_owner(workbook: WorkbookDocument, current_user: User) -> bool:
    return current_user.is_superuser or workbook.owner_user_id == current_user.id


def _get_note_sheet_or_404(session: Session, current_user: User, sheet_id: int) -> SheetDocument:
    document = session.exec(
        select(SheetDocument).where(SheetDocument.numeric_id == sheet_id)
    ).first()
    if document is None or document.scope != "notes":
        raise HTTPException(status_code=404, detail="表格不存在")
    if not _is_sheet_owner(document, current_user):
        raise HTTPException(status_code=403, detail="没有该表格权限")
    return document


def _get_workbook_or_404(session: Session, current_user: User, workbook_id: int) -> WorkbookDocument:
    workbook = session.exec(
        select(WorkbookDocument).where(WorkbookDocument.numeric_id == workbook_id)
    ).first()
    if workbook is None:
        raise HTTPException(status_code=404, detail="工作簿不存在")
    if not _is_workbook_owner(workbook, current_user):
        raise HTTPException(status_code=403, detail="没有该工作簿权限")
    return workbook


def _list_workbook_refs_for_sheet_ids(session: Session, sheet_ids: list[str]) -> dict[str, list[WorkbookRefItem]]:
    if not sheet_ids:
        return {}

    links = session.exec(
        select(WorkbookSheetLink)
        .where(WorkbookSheetLink.sheet_id.in_(sheet_ids))
        .order_by(WorkbookSheetLink.order_index, WorkbookSheetLink.created_at)
    ).all()
    if not links:
        return {}

    workbook_ids = sorted({link.workbook_id for link in links})
    workbooks = session.exec(
        select(WorkbookDocument)
        .where(WorkbookDocument.id.in_(workbook_ids))
    ).all()
    workbook_map = {workbook.id: workbook for workbook in workbooks}

    result: dict[str, list[WorkbookRefItem]] = {sheet_id: [] for sheet_id in sheet_ids}
    for link in links:
        workbook = workbook_map.get(link.workbook_id)
        if workbook is None:
            continue
        result.setdefault(link.sheet_id, []).append(
            WorkbookRefItem(id=_require_workbook_numeric_id(workbook), title=workbook.title),
        )
    return result


def _serialize_sheet_summary(
    document: SheetDocument,
    *,
    workbook_items: list[WorkbookRefItem] | None = None,
) -> dict[str, Any]:
    return {
        "id": _require_sheet_numeric_id(document),
        "title": document.title,
        "engine": document.engine,
        "scope": document.scope,
        "owner_user_id": document.owner_user_id,
        "created_by_user_id": document.created_by_user_id,
        "updated_by_user_id": document.updated_by_user_id,
        "created_at": float(document.created_at or 0.0),
        "updated_at": float(document.updated_at or 0.0),
        "workbook_items": workbook_items or [],
    }


def _serialize_sheet_detail(
    document: SheetDocument,
    *,
    workbook_items: list[WorkbookRefItem] | None = None,
    document_json: dict[str, Any] | None = None,
    pagination: NoteSheetPaginationResponse | None = None,
) -> dict[str, Any]:
    return {
        **_serialize_sheet_summary(document, workbook_items=workbook_items),
        "owner_type": document.owner_type,
        "owner_key": document.owner_key,
        "sheet_key": document.sheet_key,
        "version": int(document.version or 1),
        "document_json": dict(document_json if document_json is not None else (document.document_json or {})),
        "pagination": pagination,
    }


def _serialize_workbook_summary(workbook: WorkbookDocument, *, sheet_count: int = 0) -> dict[str, Any]:
    return {
        "id": _require_workbook_numeric_id(workbook),
        "title": workbook.title,
        "owner_user_id": workbook.owner_user_id,
        "created_by_user_id": workbook.created_by_user_id,
        "updated_by_user_id": workbook.updated_by_user_id,
        "created_at": float(workbook.created_at or 0.0),
        "updated_at": float(workbook.updated_at or 0.0),
        "sheet_count": sheet_count,
    }


def _serialize_workbook_detail(session: Session, workbook: WorkbookDocument) -> dict[str, Any]:
    links = session.exec(
        select(WorkbookSheetLink)
        .where(WorkbookSheetLink.workbook_id == workbook.id)
        .order_by(WorkbookSheetLink.order_index, WorkbookSheetLink.created_at)
    ).all()
    sheet_ids = [link.sheet_id for link in links]
    sheets = session.exec(
        select(SheetDocument)
        .where(SheetDocument.id.in_(sheet_ids) if sheet_ids else False)
    ).all() if sheet_ids else []
    sheet_map = {sheet.id: sheet for sheet in sheets}
    sheet_workbook_refs = _list_workbook_refs_for_sheet_ids(session, sheet_ids)
    ordered_sheets = [
        _serialize_sheet_summary(sheet_map[link.sheet_id], workbook_items=sheet_workbook_refs.get(link.sheet_id, []))
        for link in links
        if link.sheet_id in sheet_map
    ]
    return {
        **_serialize_workbook_summary(workbook, sheet_count=len(ordered_sheets)),
        "sheets": ordered_sheets,
    }


def _clone_sheet_document_json(
    document_json: dict[str, Any],
    *,
    mode: Literal["template", "duplicate"],
) -> dict[str, Any]:
    cloned = deepcopy(_normalize_document_json(document_json))
    if mode == "template":
        cloned["rows"] = []
    return cloned


def _get_next_workbook_link_order(session: Session, workbook_id: str) -> int:
    links = session.exec(
        select(WorkbookSheetLink)
        .where(WorkbookSheetLink.workbook_id == workbook_id)
        .order_by(WorkbookSheetLink.order_index.desc(), WorkbookSheetLink.created_at.desc())
    ).all()
    if not links:
        return 10
    return max(int(links[0].order_index or 0), 0) + 10


@router.get("/sheets", response_model=list[NoteSheetSummaryResponse])
def list_note_sheets(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
):
    documents = session.exec(
        select(SheetDocument)
        .where(SheetDocument.scope == "notes")
        .where(SheetDocument.owner_user_id == current_user.id)
        .order_by(SheetDocument.updated_at.desc(), SheetDocument.created_at.desc())
    ).all()
    workbook_refs = _list_workbook_refs_for_sheet_ids(session, [document.id for document in documents])
    return [
        NoteSheetSummaryResponse.model_validate(
            _serialize_sheet_summary(document, workbook_items=workbook_refs.get(document.id, [])),
        )
        for document in documents
    ]


@router.post("/sheets", response_model=NoteSheetDetailResponse)
def create_note_sheet(
    payload: NoteSheetCreateRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
):
    workbook: WorkbookDocument | None = None
    if payload.workbook_id is not None:
        workbook = _get_workbook_or_404(session, current_user, payload.workbook_id)

    now = time.time()
    document = SheetDocument(
        numeric_id=_get_next_sheet_numeric_id(session),
        scope="notes",
        owner_type="user",
        owner_key=str(current_user.id),
        sheet_key="pending",
        title=_normalize_title(payload.title, default_value="未命名表格"),
        engine="handsontable",
        document_json=_normalize_document_json(payload.document_json),
        version=1,
        owner_user_id=current_user.id,
        created_by_user_id=current_user.id,
        updated_by_user_id=current_user.id,
        created_at=now,
        updated_at=now,
    )
    session.add(document)
    session.flush()
    document.sheet_key = str(document.numeric_id or document.id)
    session.add(document)
    if workbook is not None:
        session.add(
            WorkbookSheetLink(
                workbook_id=workbook.id,
                sheet_id=document.id,
                order_index=_get_next_workbook_link_order(session, workbook.id),
                created_at=now,
            ),
        )
        workbook.updated_by_user_id = current_user.id
        workbook.updated_at = now
        session.add(workbook)
    session.commit()
    session.refresh(document)
    workbook_items = _list_workbook_refs_for_sheet_ids(session, [document.id]).get(document.id, [])
    return NoteSheetDetailResponse.model_validate(
        _serialize_sheet_detail(document, workbook_items=workbook_items),
    )


@router.get("/sheets/{sheet_id}", response_model=NoteSheetDetailResponse)
def get_note_sheet(
    sheet_id: int,
    page: int = Query(default=1, ge=1),
    page_size: int | None = Query(default=None, ge=1, le=MAX_NOTE_SHEET_PAGE_SIZE),
    paginate: bool | None = Query(default=None),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
):
    document = _get_note_sheet_or_404(session, current_user, sheet_id)
    workbook_items = _list_workbook_refs_for_sheet_ids(session, [document.id]).get(document.id, [])
    full_document = dict(document.document_json or {})
    document_paginate_enabled, document_page_size = _get_document_pagination_settings(full_document)
    effective_paginate = document_paginate_enabled if paginate is None else paginate

    if effective_paginate:
        page_document, pagination = _build_paged_document(
            full_document,
            page=page,
            page_size=page_size if page_size is not None else document_page_size,
        )
    else:
        page_document = _normalize_document_json(full_document)
        pagination = None

    return NoteSheetDetailResponse.model_validate(
        _serialize_sheet_detail(
            document,
            workbook_items=workbook_items,
            document_json=page_document,
            pagination=pagination,
        ),
    )


@router.put("/sheets/{sheet_id}", response_model=NoteSheetDetailResponse)
def update_note_sheet(
    sheet_id: int,
    payload: NoteSheetUpdateRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
):
    document = _get_note_sheet_or_404(session, current_user, sheet_id)
    next_title = _normalize_title(payload.title, default_value=document.title or "未命名表格") if payload.title is not None else document.title
    current_document = dict(document.document_json or {})
    if payload.document_json is None:
        next_document = current_document
    elif payload.page_patch is None:
        next_document = _normalize_document_json(payload.document_json)
    else:
        next_document = _merge_paged_document(current_document, payload.document_json, payload.page_patch)

    if document.title != next_title or current_document != next_document:
        document.title = next_title
        document.document_json = next_document
        document.version = max(int(document.version or 1), 1) + 1
        document.updated_by_user_id = current_user.id
        document.updated_at = time.time()
        session.add(document)
        session.commit()
        session.refresh(document)

    workbook_items = _list_workbook_refs_for_sheet_ids(session, [document.id]).get(document.id, [])
    response_document = dict(document.document_json or {})
    response_pagination: NoteSheetPaginationResponse | None = None
    response_paginate_enabled, _response_page_size = _get_document_pagination_settings(response_document)
    if payload.document_json is not None and payload.page_patch is not None:
        response_document = _normalize_document_json(payload.document_json)
        if response_paginate_enabled:
            response_pagination = _build_workspace_pagination(
                page_patch=payload.page_patch,
                total_rows=len(_extract_document_rows(dict(document.document_json or {}))),
                current_row_count=len(_extract_document_rows(response_document)),
            )

    return NoteSheetDetailResponse.model_validate(
        _serialize_sheet_detail(
            document,
            workbook_items=workbook_items,
            document_json=response_document,
            pagination=response_pagination,
        ),
    )


@router.post("/sheets/{sheet_id}/sort", response_model=NoteSheetDetailResponse)
def sort_note_sheet(
    sheet_id: int,
    payload: NoteSheetSortRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
):
    document = _get_note_sheet_or_404(session, current_user, sheet_id)
    current_document = _normalize_document_json(dict(document.document_json or {}))
    columns = list(current_document.get("columns") or [])
    if payload.column_index >= len(columns):
        raise HTTPException(status_code=400, detail="排序字段不存在")

    next_document = _sort_sheet_document_rows(
        current_document,
        column_index=payload.column_index,
        direction=payload.direction,
    )

    if current_document != next_document:
        document.document_json = next_document
        document.version = max(int(document.version or 1), 1) + 1
        document.updated_by_user_id = current_user.id
        document.updated_at = time.time()
        session.add(document)
        session.commit()
        session.refresh(document)

    workbook_items = _list_workbook_refs_for_sheet_ids(session, [document.id]).get(document.id, [])
    paginate_enabled, page_size = _get_document_pagination_settings(next_document)
    if paginate_enabled:
        response_document, pagination = _build_paged_document(next_document, page=1, page_size=page_size)
    else:
        response_document = _normalize_document_json(next_document)
        pagination = None

    return NoteSheetDetailResponse.model_validate(
        _serialize_sheet_detail(
            document,
            workbook_items=workbook_items,
            document_json=response_document,
            pagination=pagination,
        ),
    )


@router.delete("/sheets/{sheet_id}")
def delete_note_sheet(
    sheet_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
):
    document = _get_note_sheet_or_404(session, current_user, sheet_id)
    session.exec(delete(WorkbookSheetLink).where(WorkbookSheetLink.sheet_id == document.id))
    session.delete(document)
    session.commit()
    return {"ok": True}


@router.get("/workbooks", response_model=list[WorkbookSummaryResponse])
def list_workbooks(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
):
    workbooks = session.exec(
        select(WorkbookDocument)
        .where(WorkbookDocument.owner_user_id == current_user.id)
        .order_by(WorkbookDocument.updated_at.desc(), WorkbookDocument.created_at.desc())
    ).all()
    if not workbooks:
        return []

    workbook_ids = [workbook.id for workbook in workbooks]
    links = session.exec(
        select(WorkbookSheetLink)
        .where(WorkbookSheetLink.workbook_id.in_(workbook_ids))
    ).all()
    counts: dict[str, int] = {}
    for link in links:
        counts[link.workbook_id] = counts.get(link.workbook_id, 0) + 1

    return [
        WorkbookSummaryResponse.model_validate(
            _serialize_workbook_summary(workbook, sheet_count=counts.get(workbook.id, 0)),
        )
        for workbook in workbooks
    ]


@router.post("/workbooks", response_model=WorkbookDetailResponse)
def create_workbook(
    payload: WorkbookCreateRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
):
    now = time.time()
    workbook = WorkbookDocument(
        numeric_id=_get_next_workbook_numeric_id(session),
        title=_normalize_title(payload.title, default_value="未命名工作簿"),
        owner_user_id=current_user.id,
        created_by_user_id=current_user.id,
        updated_by_user_id=current_user.id,
        created_at=now,
        updated_at=now,
    )
    session.add(workbook)
    session.commit()
    session.refresh(workbook)
    return WorkbookDetailResponse.model_validate(_serialize_workbook_detail(session, workbook))


@router.get("/workbooks/{workbook_id}", response_model=WorkbookDetailResponse)
def get_workbook(
    workbook_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
):
    workbook = _get_workbook_or_404(session, current_user, workbook_id)
    return WorkbookDetailResponse.model_validate(_serialize_workbook_detail(session, workbook))


@router.post("/workbooks/{workbook_id}/save-as", response_model=WorkbookDetailResponse)
def save_as_workbook(
    workbook_id: int,
    payload: WorkbookSaveAsRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
):
    source_workbook = _get_workbook_or_404(session, current_user, workbook_id)
    links = session.exec(
        select(WorkbookSheetLink)
        .where(WorkbookSheetLink.workbook_id == source_workbook.id)
        .order_by(WorkbookSheetLink.order_index, WorkbookSheetLink.created_at)
    ).all()
    source_sheet_ids = [link.sheet_id for link in links]
    source_sheets = session.exec(
        select(SheetDocument)
        .where(SheetDocument.id.in_(source_sheet_ids) if source_sheet_ids else False)
    ).all() if source_sheet_ids else []
    source_sheet_map = {sheet.id: sheet for sheet in source_sheets}

    now = time.time()
    workbook = WorkbookDocument(
        numeric_id=_get_next_workbook_numeric_id(session),
        title=_normalize_title(payload.title, default_value="未命名工作簿"),
        owner_user_id=current_user.id,
        created_by_user_id=current_user.id,
        updated_by_user_id=current_user.id,
        created_at=now,
        updated_at=now,
    )
    session.add(workbook)
    session.flush()

    for link in links:
        source_sheet = source_sheet_map.get(link.sheet_id)
        if source_sheet is None:
            continue

        document = SheetDocument(
            numeric_id=_get_next_sheet_numeric_id(session),
            scope=source_sheet.scope,
            owner_type=source_sheet.owner_type,
            owner_key=source_sheet.owner_key,
            sheet_key="pending",
            title=source_sheet.title,
            engine=source_sheet.engine,
            document_json=_clone_sheet_document_json(
                dict(source_sheet.document_json or {}),
                mode=payload.mode,
            ),
            version=1,
            owner_user_id=current_user.id,
            created_by_user_id=current_user.id,
            updated_by_user_id=current_user.id,
            created_at=now,
            updated_at=now,
        )
        session.add(document)
        session.flush()
        document.sheet_key = str(document.numeric_id or document.id)
        session.add(document)
        session.add(
            WorkbookSheetLink(
                workbook_id=workbook.id,
                sheet_id=document.id,
                order_index=link.order_index,
                created_at=now,
            ),
        )

    session.commit()
    session.refresh(workbook)
    return WorkbookDetailResponse.model_validate(_serialize_workbook_detail(session, workbook))


@router.post("/workbooks/{workbook_id}/sheets", response_model=WorkbookDetailResponse)
def attach_sheet_to_workbook(
    workbook_id: int,
    payload: WorkbookAttachSheetRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
):
    workbook = _get_workbook_or_404(session, current_user, workbook_id)
    document = _get_note_sheet_or_404(session, current_user, payload.sheet_id)

    existing = session.exec(
        select(WorkbookSheetLink)
        .where(WorkbookSheetLink.workbook_id == workbook.id)
        .where(WorkbookSheetLink.sheet_id == document.id)
    ).first()

    if existing is None:
        session.add(
            WorkbookSheetLink(
                workbook_id=workbook.id,
                sheet_id=document.id,
                order_index=_get_next_workbook_link_order(session, workbook.id),
            ),
        )
        workbook.updated_by_user_id = current_user.id
        workbook.updated_at = time.time()
        session.add(workbook)
        session.commit()
        session.refresh(workbook)

    return WorkbookDetailResponse.model_validate(_serialize_workbook_detail(session, workbook))


@router.delete("/workbooks/{workbook_id}/sheets/{sheet_id}", response_model=WorkbookDetailResponse)
def remove_sheet_from_workbook(
    workbook_id: int,
    sheet_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
):
    workbook = _get_workbook_or_404(session, current_user, workbook_id)
    _get_note_sheet_or_404(session, current_user, sheet_id)

    link = session.exec(
        select(WorkbookSheetLink)
        .where(WorkbookSheetLink.workbook_id == workbook.id)
        .where(WorkbookSheetLink.sheet_id == sheet_id)
    ).first()
    if link is not None:
        session.delete(link)
        workbook.updated_by_user_id = current_user.id
        workbook.updated_at = time.time()
        session.add(workbook)
        session.commit()
        session.refresh(workbook)

    return WorkbookDetailResponse.model_validate(_serialize_workbook_detail(session, workbook))


@router.delete("/workbooks/{workbook_id}")
def delete_workbook(
    workbook_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
):
    workbook = _get_workbook_or_404(session, current_user, workbook_id)
    session.exec(delete(WorkbookSheetLink).where(WorkbookSheetLink.workbook_id == workbook.id))
    session.delete(workbook)
    session.commit()
    return {"ok": True}
