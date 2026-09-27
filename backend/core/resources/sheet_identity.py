from __future__ import annotations

from dataclasses import dataclass

from sqlmodel import Session

from backend.core.resources.identity import RESOURCE_TYPE_SHEET, RESOURCE_TYPE_WORKBOOK, allocate_resource_id
from backend.models import SheetDocument, WorkbookDocument, generate_sheet_document_id


@dataclass(frozen=True)
class NewSheetIdentity:
    primary_id: str
    numeric_id: int
    legacy_id: str


@dataclass(frozen=True)
class NewWorkbookIdentity:
    primary_id: str
    numeric_id: int
    legacy_id: str
    resource_identity_id: int


def allocate_new_sheet_identity(session: Session) -> NewSheetIdentity:
    legacy_id = generate_sheet_document_id()
    numeric_id = allocate_resource_id(session, RESOURCE_TYPE_SHEET, legacy_id)
    primary_id = str(numeric_id)
    if session.get(SheetDocument, primary_id) is not None:
        raise RuntimeError(f"allocated sheet resource id conflicts with existing sheet primary key: {primary_id}")
    return NewSheetIdentity(primary_id=primary_id, numeric_id=numeric_id, legacy_id=legacy_id)


def allocate_new_workbook_identity(session: Session) -> NewWorkbookIdentity:
    legacy_id = generate_sheet_document_id()
    # 已有工作簿/工作表编号保持不变；新建资源统一从全局编号池分配。
    numeric_id = allocate_resource_id(session, RESOURCE_TYPE_WORKBOOK, legacy_id)
    primary_id = str(numeric_id)
    if session.get(WorkbookDocument, primary_id) is not None:
        raise RuntimeError(f"allocated workbook route id conflicts with existing workbook primary key: {primary_id}")
    return NewWorkbookIdentity(
        primary_id=primary_id,
        numeric_id=numeric_id,
        legacy_id=legacy_id,
        resource_identity_id=numeric_id,
    )
