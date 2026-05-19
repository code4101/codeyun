from __future__ import annotations

from typing import Iterable

from sqlalchemy import or_
from sqlmodel import Session, select

from backend.models import SheetDocument, WorkbookDocument


def sheet_public_id(document: SheetDocument) -> str:
    numeric_id = int(document.numeric_id or 0)
    if numeric_id > 0:
        return str(numeric_id)
    return str(document.id or "")


def workbook_public_id(workbook: WorkbookDocument) -> str:
    numeric_id = int(workbook.numeric_id or 0)
    if numeric_id > 0:
        return str(numeric_id)
    return str(workbook.id or "")


def sheet_ref_aliases(document: SheetDocument) -> set[str]:
    refs = {
        str(document.id or "").strip(),
        str(getattr(document, "legacy_id", None) or "").strip(),
        sheet_public_id(document),
    }
    return {ref for ref in refs if ref}


def workbook_ref_aliases(workbook: WorkbookDocument) -> set[str]:
    refs = {
        str(workbook.id or "").strip(),
        str(getattr(workbook, "legacy_id", None) or "").strip(),
        workbook_public_id(workbook),
    }
    return {ref for ref in refs if ref}


def build_sheet_ref_map(documents: Iterable[SheetDocument]) -> dict[str, SheetDocument]:
    result: dict[str, SheetDocument] = {}
    for document in documents:
        for ref in sheet_ref_aliases(document):
            result[ref] = document
    return result


def build_workbook_ref_map(workbooks: Iterable[WorkbookDocument]) -> dict[str, WorkbookDocument]:
    result: dict[str, WorkbookDocument] = {}
    for workbook in workbooks:
        for ref in workbook_ref_aliases(workbook):
            result[ref] = workbook
    return result


def load_sheets_by_refs(session: Session, refs: Iterable[str]) -> dict[str, SheetDocument]:
    normalized_refs = {str(ref or "").strip() for ref in refs}
    normalized_refs.discard("")
    if not normalized_refs:
        return {}
    legacy_refs = [ref for ref in normalized_refs if not ref.isdecimal()]
    numeric_refs = [int(ref) for ref in normalized_refs if ref.isdecimal()]
    conditions = []
    if legacy_refs:
        conditions.append(SheetDocument.id.in_(legacy_refs))
        conditions.append(SheetDocument.legacy_id.in_(legacy_refs))
    if numeric_refs:
        conditions.append(SheetDocument.numeric_id.in_(numeric_refs))
    if not conditions:
        return {}
    query = select(SheetDocument)
    query = query.where(or_(*conditions) if len(conditions) > 1 else conditions[0])
    return build_sheet_ref_map(session.exec(query).all())


def load_workbooks_by_refs(session: Session, refs: Iterable[str]) -> dict[str, WorkbookDocument]:
    normalized_refs = {str(ref or "").strip() for ref in refs}
    normalized_refs.discard("")
    if not normalized_refs:
        return {}
    legacy_refs = [ref for ref in normalized_refs if not ref.isdecimal()]
    numeric_refs = [int(ref) for ref in normalized_refs if ref.isdecimal()]
    conditions = []
    if legacy_refs:
        conditions.append(WorkbookDocument.id.in_(legacy_refs))
        conditions.append(WorkbookDocument.legacy_id.in_(legacy_refs))
    if numeric_refs:
        conditions.append(WorkbookDocument.numeric_id.in_(numeric_refs))
    if not conditions:
        return {}
    query = select(WorkbookDocument)
    query = query.where(or_(*conditions) if len(conditions) > 1 else conditions[0])
    return build_workbook_ref_map(session.exec(query).all())
