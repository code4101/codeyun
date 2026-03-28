from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from backend.core.ai_git_commit import resolve_ai_runtime_config
from backend.core.document_reduction import (
    DOCUMENT_REDUCTION_BRANCH_FACTOR,
    DocumentReductionError,
    answer_document_question,
    generate_document_index,
)
from backend.core.document_reduction_cache import (
    delete_document_nodes,
    replace_document_run_nodes,
    search_document_run_nodes,
)
from backend.core.document_reduction_storage import (
    DocumentReductionStorageError,
    delete_document_asset_dir,
    load_document_run_result,
    load_document_run_source_units,
    load_document_run_tree_nodes,
    load_document_source_text,
    save_document_run_artifacts,
    save_document_source_text,
    sha256_hexdigest,
)
from backend.core.auth import get_current_user_from_token
from backend.db import get_session
from backend.models import DocumentAsset, DocumentQueryHistory, DocumentReductionRun, User


router = APIRouter()

MAX_DOCUMENT_UPLOAD_BYTES = 20 * 1024 * 1024
ALLOWED_TEXT_FILE_EXTENSIONS = {
    ".csv",
    ".json",
    ".jsonl",
    ".log",
    ".markdown",
    ".md",
    ".py",
    ".rst",
    ".text",
    ".toml",
    ".tsv",
    ".txt",
    ".yaml",
    ".yml",
}


class ReductionDocumentRead(BaseModel):
    id: str
    title: str
    original_filename: str
    media_type: str
    file_ext: str
    size_bytes: int
    sha256: str
    source_char_count: int
    status: str
    latest_run_id: Optional[str] = None
    latest_summary: str = ""
    latest_query_at: Optional[float] = None
    run_count: int
    created_at: float
    updated_at: float


class ReductionDocumentRunRead(BaseModel):
    id: str
    document_id: str
    provider: str
    model: str
    task_type: str
    status: str
    branch_factor: int
    source_unit_count: int
    source_unit_truncated_count: int
    estimated_level_count: int
    current_level_index: int
    current_level_chunk_count: int
    current_level_completed_chunk_count: int
    completed_chunk_count: int
    level_count: int
    node_count: int
    top_summary: str
    error_message: Optional[str] = None
    created_at: float
    finished_at: Optional[float] = None
    updated_at: float


class ReductionDocumentListResponse(BaseModel):
    items: list[ReductionDocumentRead] = Field(default_factory=list)


class ReductionDocumentDetailResponse(BaseModel):
    document: ReductionDocumentRead
    active_run: Optional[ReductionDocumentRunRead] = None
    latest_run: Optional[ReductionDocumentRunRead] = None


class ReductionDocumentIndexRequest(BaseModel):
    provider: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    model: Optional[str] = None
    branch_factor: int = Field(default=DOCUMENT_REDUCTION_BRANCH_FACTOR, ge=2, le=20)


class ReductionDocumentIndexResponse(BaseModel):
    document: ReductionDocumentRead
    run: ReductionDocumentRunRead
    result: dict[str, Any]
    reduction: dict[str, Any]


class ReductionDocumentQueryRequest(BaseModel):
    query: str
    provider: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    model: Optional[str] = None
    run_id: Optional[str] = None


class ReductionDocumentQueryResponse(BaseModel):
    query_id: str
    document_id: str
    run_id: str
    model: str
    answer: str
    summary: str
    needs_more_context: bool = False
    matched_node_ids: list[str] = Field(default_factory=list)
    matched_source_refs: list[str] = Field(default_factory=list)
    follow_up_questions: list[str] = Field(default_factory=list)
    matched_nodes: list[dict[str, Any]] = Field(default_factory=list)


class ReductionDocumentDeleteResponse(BaseModel):
    ok: bool = True
    document_id: str


@router.get("", response_model=ReductionDocumentListResponse)
def list_reduction_documents(
    current_user: User = Depends(get_current_user_from_token),
    session: Session = Depends(get_session),
):
    items = session.exec(
        select(DocumentAsset)
        .where(DocumentAsset.user_id == current_user.id)
        .order_by(DocumentAsset.updated_at.desc(), DocumentAsset.created_at.desc())
    ).all()
    return ReductionDocumentListResponse(items=[_serialize_document(item) for item in items])


@router.post("/upload", response_model=ReductionDocumentRead)
async def upload_reduction_document(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user_from_token),
    session: Session = Depends(get_session),
):
    raw_bytes = await file.read()
    if not raw_bytes:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="上传文件不能为空")
    if len(raw_bytes) > MAX_DOCUMENT_UPLOAD_BYTES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="上传文件过大，请先拆分后再上传")

    try:
        text_content = _decode_text_upload(
            filename=file.filename or "untitled.txt",
            content_type=file.content_type or "",
            raw_bytes=raw_bytes,
        )
    except DocumentReductionError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    title = Path(file.filename or "untitled.txt").stem.strip() or "未命名文档"
    now = time.time()
    document = DocumentAsset(
        user_id=current_user.id,
        title=title,
        original_filename=file.filename or "untitled.txt",
        media_type=(file.content_type or "text/plain").strip() or "text/plain",
        file_ext=(Path(file.filename or "").suffix or "").lower(),
        size_bytes=len(raw_bytes),
        sha256=sha256_hexdigest(raw_bytes),
        source_char_count=len(text_content),
        status="uploaded",
        created_at=now,
        updated_at=now,
    )
    session.add(document)
    session.commit()
    session.refresh(document)

    save_document_source_text(
        user_id=current_user.id,
        document_id=document.id,
        original_filename=document.original_filename,
        media_type=document.media_type,
        raw_bytes=raw_bytes,
        text_content=text_content,
    )
    return _serialize_document(document)


@router.get("/{document_id}", response_model=ReductionDocumentDetailResponse)
def get_reduction_document_detail(
    document_id: str,
    current_user: User = Depends(get_current_user_from_token),
    session: Session = Depends(get_session),
):
    document = _get_user_document(session, current_user, document_id)
    active_run = _get_document_run(session, current_user, document.latest_run_id) if document.latest_run_id else None
    latest_run = _get_latest_document_run(session, current_user, document.id)
    return ReductionDocumentDetailResponse(
        document=_serialize_document(document),
        active_run=_serialize_run(active_run) if active_run else None,
        latest_run=_serialize_run(latest_run) if latest_run else None,
    )


@router.post("/{document_id}/index", response_model=ReductionDocumentIndexResponse)
def index_reduction_document(
    document_id: str,
    payload: ReductionDocumentIndexRequest,
    current_user: User = Depends(get_current_user_from_token),
    session: Session = Depends(get_session),
):
    document = _get_user_document(session, current_user, document_id)
    provider_id = ""
    base_url = None
    api_key = None
    extra_providers = ()

    run = DocumentReductionRun(
        document_id=document.id,
        user_id=current_user.id,
        provider=(payload.provider or "").strip(),
        model=(payload.model or "").strip(),
        status="running",
        branch_factor=payload.branch_factor,
        created_at=time.time(),
        updated_at=time.time(),
    )
    session.add(run)
    session.commit()
    session.refresh(run)

    try:
        document.status = "running"
        document.updated_at = time.time()
        session.add(document)
        session.commit()
        source_text = load_document_source_text(user_id=current_user.id, document_id=document.id)
        provider_id, base_url, api_key, extra_providers = resolve_ai_runtime_config(
            session=session,
            current_user=current_user,
            provider=payload.provider,
            base_url=payload.base_url,
            api_key=payload.api_key,
        )
        run.provider = provider_id
        run.updated_at = time.time()
        session.add(run)
        session.commit()

        def update_progress(event: dict[str, Any]) -> None:
            nonlocal run
            event_type = str(event.get("event") or "").strip()
            if event_type == "prepared":
                run.source_unit_count = int(event.get("source_unit_count") or 0)
                run.estimated_level_count = int(event.get("estimated_level_count") or 0)
            elif event_type in {"level_started", "chunk_completed"}:
                run.current_level_index = int(event.get("level") or 0)
                run.current_level_chunk_count = int(event.get("chunk_count") or 0)
                run.completed_chunk_count = int(event.get("completed_chunk_count") or 0)
                run.current_level_completed_chunk_count = int(event.get("completed_level_chunk_count") or 0)
            elif event_type == "completed":
                run.completed_chunk_count = int(event.get("completed_chunk_count") or run.completed_chunk_count or 0)
            run.updated_at = time.time()
            session.add(run)
            session.commit()

        reduction_payload = generate_document_index(
            document_id=document.id,
            document_title=document.title,
            text=source_text,
            provider_id=provider_id,
            base_url=base_url,
            api_key=api_key,
            model=payload.model,
            extra_providers=extra_providers,
            branch_factor=payload.branch_factor,
            progress_callback=update_progress,
        )
        save_document_run_artifacts(
            user_id=current_user.id,
            document_id=document.id,
            run_id=run.id,
            source_units=list(reduction_payload["source_units"]),
            levels=list(reduction_payload["reduction"]["levels"]),
            final_result=dict(reduction_payload["result"]),
        )

        all_nodes = [
            node
            for level in reduction_payload["reduction"]["levels"]
            for node in level.get("nodes") or []
        ]
        replace_document_run_nodes(
            user_id=current_user.id,
            document_id=document.id,
            run_id=run.id,
            nodes=all_nodes,
        )

        run.model = str(reduction_payload["result"].get("model") or payload.model or provider_id)
        run.status = "completed"
        run.source_unit_count = int(reduction_payload["source_unit_count"] or 0)
        run.source_unit_truncated_count = sum(
            1
            for item in reduction_payload["source_units"]
            if bool((item.get("metadata") or {}).get("truncated"))
        )
        run.level_count = int(reduction_payload["reduction"]["level_count"] or 0)
        run.node_count = int(reduction_payload["reduction"]["node_count"] or 0)
        run.current_level_completed_chunk_count = run.current_level_chunk_count
        run.top_summary = str(reduction_payload["result"].get("summary") or "").strip()
        run.finished_at = time.time()
        run.updated_at = run.finished_at
        session.add(run)

        document.status = "indexed"
        document.latest_run_id = run.id
        document.latest_summary = run.top_summary
        document.run_count = int(document.run_count or 0) + 1
        document.updated_at = time.time()
        session.add(document)
        session.commit()
        session.refresh(run)
        session.refresh(document)
    except (DocumentReductionError, DocumentReductionStorageError) as exc:
        run.status = "failed"
        run.error_message = str(exc)
        run.finished_at = time.time()
        run.updated_at = run.finished_at
        session.add(run)
        document.status = "error"
        document.updated_at = time.time()
        session.add(document)
        session.commit()
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except Exception as exc:
        run.status = "failed"
        run.error_message = str(exc)
        run.finished_at = time.time()
        run.updated_at = run.finished_at
        session.add(run)
        document.status = "error"
        document.updated_at = time.time()
        session.add(document)
        session.commit()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"文档归纳失败：{exc}") from exc

    return ReductionDocumentIndexResponse(
        document=_serialize_document(document),
        run=_serialize_run(run),
        result=dict(reduction_payload["result"]),
        reduction=dict(reduction_payload["reduction"]),
    )


@router.post("/{document_id}/query", response_model=ReductionDocumentQueryResponse)
def query_reduction_document(
    document_id: str,
    payload: ReductionDocumentQueryRequest,
    current_user: User = Depends(get_current_user_from_token),
    session: Session = Depends(get_session),
):
    document = _get_user_document(session, current_user, document_id)
    run = _resolve_query_run(session, current_user, document, payload.run_id)

    provider_id = ""
    base_url = None
    api_key = None
    extra_providers = ()

    query_row = DocumentQueryHistory(
        document_id=document.id,
        run_id=run.id,
        user_id=current_user.id,
        provider=(payload.provider or "").strip(),
        model=(payload.model or "").strip(),
        query_text=(payload.query or "").strip(),
        status="running",
        created_at=time.time(),
        updated_at=time.time(),
    )
    session.add(query_row)
    session.commit()
    session.refresh(query_row)

    try:
        provider_id, base_url, api_key, extra_providers = resolve_ai_runtime_config(
            session=session,
            current_user=current_user,
            provider=payload.provider,
            base_url=payload.base_url,
            api_key=payload.api_key,
        )
        query_row.provider = provider_id
        run_result = load_document_run_result(user_id=current_user.id, document_id=document.id, run_id=run.id)
        matched_nodes = search_document_run_nodes(
            user_id=current_user.id,
            document_id=document.id,
            run_id=run.id,
            query_text=payload.query,
            limit=6,
        )
        if not matched_nodes:
            matched_nodes = _fallback_match_nodes(
                user_id=current_user.id,
                document_id=document.id,
                run_id=run.id,
            )

        source_units = load_document_run_source_units(
            user_id=current_user.id,
            document_id=document.id,
            run_id=run.id,
        )
        answer_payload = answer_document_question(
            question=payload.query,
            root_summary=run_result,
            matched_nodes=matched_nodes,
            source_units=source_units,
            provider_id=provider_id,
            base_url=base_url,
            api_key=api_key,
            model=payload.model,
            extra_providers=extra_providers,
        )

        query_row.model = str(answer_payload["model"] or payload.model or provider_id)
        query_row.answer_text = str(answer_payload["answer"] or "").strip()
        query_row.status = "completed"
        query_row.matched_node_count = len(matched_nodes)
        query_row.matched_source_count = len(answer_payload["matched_source_refs"])
        query_row.finished_at = time.time()
        query_row.updated_at = query_row.finished_at
        session.add(query_row)

        document.latest_query_at = time.time()
        document.updated_at = document.latest_query_at
        session.add(document)
        session.commit()
    except (DocumentReductionError, DocumentReductionStorageError) as exc:
        query_row.status = "failed"
        query_row.error_message = str(exc)
        query_row.finished_at = time.time()
        query_row.updated_at = query_row.finished_at
        session.add(query_row)
        session.commit()
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except Exception as exc:
        query_row.status = "failed"
        query_row.error_message = str(exc)
        query_row.finished_at = time.time()
        query_row.updated_at = query_row.finished_at
        session.add(query_row)
        session.commit()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"文档提问失败：{exc}") from exc

    return ReductionDocumentQueryResponse(
        query_id=query_row.id,
        document_id=document.id,
        run_id=run.id,
        model=str(answer_payload["model"]),
        answer=str(answer_payload["answer"]),
        summary=str(answer_payload["summary"]),
        needs_more_context=bool(answer_payload["needs_more_context"]),
        matched_node_ids=list(answer_payload["matched_node_ids"]),
        matched_source_refs=list(answer_payload["matched_source_refs"]),
        follow_up_questions=list(answer_payload["follow_up_questions"]),
        matched_nodes=[
            {
                "node_id": str(item.get("node_id") or ""),
                "topic": str(item.get("topic") or ""),
                "summary": str(item.get("summary") or ""),
                "source_refs": list(item.get("source_refs") or []),
                "score": int(item.get("score") or 0),
            }
            for item in matched_nodes
        ],
    )


@router.delete("/{document_id}", response_model=ReductionDocumentDeleteResponse)
def delete_reduction_document(
    document_id: str,
    current_user: User = Depends(get_current_user_from_token),
    session: Session = Depends(get_session),
):
    document = _get_user_document(session, current_user, document_id)
    runs = session.exec(
        select(DocumentReductionRun)
        .where(
            DocumentReductionRun.user_id == current_user.id,
            DocumentReductionRun.document_id == document.id,
        )
    ).all()
    queries = session.exec(
        select(DocumentQueryHistory)
        .where(
            DocumentQueryHistory.user_id == current_user.id,
            DocumentQueryHistory.document_id == document.id,
        )
    ).all()

    for item in queries:
        session.delete(item)
    for item in runs:
        session.delete(item)
    session.delete(document)
    session.commit()

    delete_document_nodes(user_id=current_user.id, document_id=document.id)
    delete_document_asset_dir(user_id=current_user.id, document_id=document.id)
    return ReductionDocumentDeleteResponse(document_id=document.id)


def _get_user_document(session: Session, current_user: User, document_id: str) -> DocumentAsset:
    document = session.get(DocumentAsset, document_id)
    if not document or document.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="文档不存在")
    return document


def _get_document_run(session: Session, current_user: User, run_id: Optional[str]) -> Optional[DocumentReductionRun]:
    if not run_id:
        return None
    run = session.get(DocumentReductionRun, run_id)
    if not run or run.user_id != current_user.id:
        return None
    return run


def _get_latest_document_run(
    session: Session,
    current_user: User,
    document_id: str,
) -> Optional[DocumentReductionRun]:
    return session.exec(
        select(DocumentReductionRun)
        .where(
            DocumentReductionRun.user_id == current_user.id,
            DocumentReductionRun.document_id == document_id,
        )
        .order_by(DocumentReductionRun.created_at.desc(), DocumentReductionRun.updated_at.desc())
    ).first()


def _resolve_query_run(
    session: Session,
    current_user: User,
    document: DocumentAsset,
    requested_run_id: Optional[str],
) -> DocumentReductionRun:
    run = _get_document_run(session, current_user, requested_run_id or document.latest_run_id)
    if run is None or run.document_id != document.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="当前文档还没有可用的归纳结果")
    if run.status != "completed":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="当前归纳任务未完成，暂时不能提问")
    return run


def _fallback_match_nodes(*, user_id: int, document_id: str, run_id: str) -> list[dict[str, Any]]:
    nodes = load_document_run_tree_nodes(user_id=user_id, document_id=document_id, run_id=run_id)
    if not nodes:
        return []
    nodes.sort(key=lambda item: (-int(item.get("level") or 0), str(item.get("node_id") or "")))
    top = nodes[:3]
    for item in top:
        item.setdefault("payload", {})
    return [
        {
            "node_id": str(item.get("node_id") or ""),
            "level": int(item.get("level") or 0),
            "topic": str((item.get("payload") or {}).get("topic") or ""),
            "summary": str((item.get("payload") or {}).get("summary") or ""),
            "keywords": list((item.get("payload") or {}).get("keywords") or []),
            "possible_questions": list((item.get("payload") or {}).get("possible_questions") or []),
            "importance": str((item.get("payload") or {}).get("importance") or ""),
            "source_refs": list(item.get("source_refs") or []),
            "child_node_ids": list(item.get("child_node_ids") or []),
            "payload": dict(item.get("payload") or {}),
            "score": 0,
        }
        for item in top
    ]


def _serialize_document(document: DocumentAsset) -> ReductionDocumentRead:
    return ReductionDocumentRead.model_validate(document.model_dump())


def _serialize_run(run: DocumentReductionRun) -> ReductionDocumentRunRead:
    return ReductionDocumentRunRead.model_validate(run.model_dump())


def _decode_text_upload(*, filename: str, content_type: str, raw_bytes: bytes) -> str:
    suffix = (Path(filename).suffix or "").lower()
    normalized_type = (content_type or "").strip().lower()
    is_text_type = normalized_type.startswith("text/") or normalized_type in {
        "application/json",
        "application/x-ndjson",
        "application/yaml",
    }
    if not is_text_type and suffix not in ALLOWED_TEXT_FILE_EXTENSIONS:
        raise DocumentReductionError("当前仅支持上传文本类文件")
    if b"\x00" in raw_bytes:
        raise DocumentReductionError("检测到二进制内容，当前仅支持纯文本文件")

    for encoding in ("utf-8-sig", "utf-8", "utf-16"):
        try:
            return raw_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw_bytes.decode("utf-8", errors="replace")
