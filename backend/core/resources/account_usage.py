"""Read-only storage accounting by resource owner, never by viewer or grant.

SQLite variable-length fields are measured in SQL as BLOB bytes (UTF-8 for text,
the persisted representation for JSON); rows and large content are not loaded
into Python. Indexes, fixed-width fields, free pages and WAL are shared database
overhead and deliberately excluded. Soft-deleted payload remains chargeable.
Workbook links and sharing grants do not duplicate underlying resource payloads.
Unowned legacy attachments cannot be attributed from references alone.
"""
from __future__ import annotations

import time
from sqlalchemy import JSON, LargeBinary, String, case, cast, func, literal
from sqlmodel import Session, select
from sqlmodel.sql.sqltypes import AutoString
from pydantic import BaseModel, Field

from backend.core.library.storage import PDF_HOSTED_ENTRY_ID, library_owner_roots
from backend.core.resources.storage_usage import collect_directory_usage
from backend.models import (GraphResource, LibraryBookAsset, NoteEdge, NoteNode,
                            PdfDocument, SheetDocument, SheetPageSnapshot, WorkbookDocument)


class UsagePart(BaseModel):
    label: str
    count: int = 0
    bytes: int = 0
    retained_bytes: int = 0


class UsageCategory(BaseModel):
    key: str
    title: str
    resource_count: int = 0
    data_bytes: int = 0
    file_bytes: int = 0
    total_bytes: int = 0
    retained_bytes: int = 0
    external_reference_bytes: int = 0
    unknown_external_size_count: int = 0
    parts: list[UsagePart] = Field(default_factory=list)
    unavailable_count: int = 0


class AccountUsage(BaseModel):
    owner_id: int
    generated_at: float
    total_bytes: int
    categories: list[UsageCategory]
    notes: list[str]


def _payload(model):
    return sum((func.coalesce(func.length(cast(column, LargeBinary)), 0)
                for column in model.__table__.columns
                if isinstance(column.type, (String, AutoString, JSON, LargeBinary))), literal(0))


def _part(session: Session, model, condition, label: str) -> UsagePart:
    size = _payload(model)
    deleted = (model.deleted_at.is_not(None) if hasattr(model, 'deleted_at')
               else model.deleted == True if model is GraphResource else literal(False))
    count, total, retained = session.exec(select(
        func.count(), func.coalesce(func.sum(size), 0),
        func.coalesce(func.sum(case((deleted, size), else_=0)), 0)
    ).select_from(model).where(condition)).one()
    return UsagePart(label=label, count=count, bytes=total, retained_bytes=retained)


def collect_account_usage(session: Session, owner_id: int) -> AccountUsage:
    """Return logical storage for one owner, including retained data and originals.

    Files are measured only under provider-owned user directories; source paths
    from documents are never used for arbitrary filesystem access. External PDF
    sizes are registry metadata, reported separately from hosted storage.
    """
    if owner_id <= 0:
        raise ValueError('owner_id must be positive')
    note = UsageCategory(key='notes', title='星图笔记', parts=[
        _part(session, NoteNode, NoteNode.user_id == owner_id, '笔记正文、历史与元数据'),
        _part(session, NoteEdge, NoteEdge.user_id == owner_id, '笔记关系'),
    ])
    note.resource_count = note.parts[0].count
    sheets = UsageCategory(key='sheets', title='星云表格', parts=[
        _part(session, SheetDocument, SheetDocument.owner_user_id == owner_id, '表格数据与元数据'),
        _part(session, WorkbookDocument, WorkbookDocument.owner_user_id == owner_id, '工作簿'),
        _part(session, SheetPageSnapshot, SheetPageSnapshot.sheet_id.in_(
            select(SheetDocument.id).where(SheetDocument.owner_user_id == owner_id)), '表格分页缓存'),
    ])
    sheets.resource_count = sheets.parts[0].count + sheets.parts[1].count
    library = UsageCategory(key='library', title='图书馆', parts=[
        _part(session, PdfDocument, PdfDocument.owner_user_id == owner_id, 'PDF 文档元数据'),
        _part(session, LibraryBookAsset, LibraryBookAsset.owner_user_id == owner_id, '电子书元数据'),
    ])
    library.resource_count = sum(part.count for part in library.parts)
    for root in library_owner_roots(owner_id):
        # Missing owner directories are normal for a new account. Links are not
        # followed: another storage namespace must never be charged to this user.
        if root.is_symlink() or root.is_junction():
            library.unavailable_count += 1
            continue
        if not root.exists():
            continue
        usage = collect_directory_usage(root, top_limit=0, prefer_treesize=False)
        library.file_bytes += usage.logical_size_bytes
        library.unavailable_count += usage.inaccessible_count + usage.symlink_count
    external = PdfDocument.source_entry_id != PDF_HOSTED_ENTRY_ID
    total, unknown = session.exec(select(
        func.coalesce(func.sum(PdfDocument.size_bytes), 0),
        func.coalesce(func.sum(case((PdfDocument.size_bytes.is_(None), 1), else_=0)), 0),
    ).where(PdfDocument.owner_user_id == owner_id, external)).one()
    library.external_reference_bytes = max(0, total)
    library.unknown_external_size_count = unknown
    graph = UsageCategory(key='project_graph', title='ProjectGraph', parts=[
        _part(session, GraphResource, GraphResource.owner_id == owner_id, '图文件、原始导入副本与目录元数据'),
    ])
    graph.resource_count = session.exec(select(func.count()).select_from(GraphResource).where(
        GraphResource.owner_id == owner_id, GraphResource.kind == 'document')).one()
    categories = [note, sheets, library, graph]
    for category in categories:
        category.data_bytes = sum(part.bytes for part in category.parts)
        category.retained_bytes = sum(part.retained_bytes for part in category.parts)
        category.total_bytes = category.data_bytes + category.file_bytes
    return AccountUsage(owner_id=owner_id, generated_at=time.time(),
        total_bytes=sum(item.total_bytes for item in categories), categories=categories, notes=[
            '按唯一拥有者统计；分享、书架摆放和工作簿引用不重复计算资源。',
            '统计数据库中的文本、JSON、二进制内容及图书馆账号目录文件；包含回收站、原始导入副本和表格分页缓存。',
            '这是可归属内容的逻辑字节数，不包含数据库索引、固定字段、空闲页、日志及系统备份。',
            '旧版全局图片附件尚无拥有者记录，暂未计入；个人阅读状态、批注及系统技能源文件暂未计入。',
            '外部设备 PDF 仅列出登记大小，不计入服务器占用。目录扫描失败或链接跳过时，总量为已统计部分。',
        ])
