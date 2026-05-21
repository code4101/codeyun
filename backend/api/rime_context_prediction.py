from __future__ import annotations

from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from backend.core.auth import verify_api_token
from backend.core.device import BaseDevice
from backend.core.rime_context_prediction import (
    DEFAULT_HISTORY_ARTICLE_PAGE_SIZE,
    RimeContextPredictionError,
    adjust_rime_context_weight_compare_candidate,
    collect_rime_runtime_config,
    collect_rime_context_weight_compare,
    collect_rime_context_prediction_article_content,
    collect_rime_context_prediction_articles,
    collect_rime_context_prediction_history_article,
    collect_rime_context_prediction_lint,
    collect_rime_context_prediction_tree,
    delete_rime_context_prediction_article,
    delete_rime_context_prediction_candidate,
    import_rime_context_prediction_article,
    rebuild_rime_context_prediction_snapshot,
    refresh_rime_context_prediction_tree,
    save_rime_context_prediction_article_content,
    save_rime_context_prediction_history_article,
    update_rime_runtime_config,
    update_rime_context_prediction_candidate,
    update_rime_context_prediction_article,
)


router = APIRouter()


class RimeArticleImportRequest(BaseModel):
    title: str | None = None
    content: str = Field(min_length=1)
    enabled: bool = True
    source_type: str | None = None
    weight_multiplier: float | None = Field(default=None, ge=1, le=100)


class RimeArticleUpdateRequest(BaseModel):
    title: str | None = None
    enabled: bool | None = None


class RimeArticleContentSaveRequest(BaseModel):
    content: str
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=DEFAULT_HISTORY_ARTICLE_PAGE_SIZE, ge=1, le=20000)


class RimeHistoryArticleSaveRequest(BaseModel):
    content: str = Field(min_length=1)


class RimeCandidateDeleteRequest(BaseModel):
    context: str = Field(min_length=1)
    prefix: str = Field(min_length=1)
    candidate: str = Field(min_length=1)


class RimeCandidateUpdateRequest(BaseModel):
    original_context: str | None = None
    original_prefix: str | None = None
    original_candidate: str | None = None
    context: str = Field(min_length=1)
    prefix: str = Field(min_length=1)
    candidate: str = Field(min_length=1)
    weight: float = Field(gt=0)


class RimeRuntimeConfigUpdateRequest(BaseModel):
    config: dict[str, Any] = Field(default_factory=dict)


class RimeWeightCompareRequest(BaseModel):
    candidates: list[str] = Field(default_factory=list)
    source: str = "snapshot"
    limit: int = Field(default=20, ge=1, le=100)


class RimeWeightCompareAdjustRequest(BaseModel):
    prefix: str = Field(min_length=1)
    candidate: str = Field(min_length=1)
    weight: float = Field(gt=0)
    candidates: list[str] = Field(default_factory=list)
    source: str = "snapshot"
    limit: int = Field(default=20, ge=1, le=100)


def _raise_rime_error(exc: RimeContextPredictionError) -> None:
    raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/context-prediction/tree")
def get_rime_context_prediction_tree(
    source: str = Query("snapshot"),
    limit: int = Query(50000, ge=1, le=50000),
    _: BaseDevice = Depends(verify_api_token),
):
    return collect_rime_context_prediction_tree(limit=limit, source=source)


@router.post("/context-prediction/tree/refresh")
def post_rime_context_prediction_tree_refresh(
    source: str = Query("snapshot"),
    limit: int = Query(50000, ge=1, le=50000),
    _: BaseDevice = Depends(verify_api_token),
):
    try:
        return refresh_rime_context_prediction_tree(limit=limit, source=source)
    except RimeContextPredictionError as exc:
        _raise_rime_error(exc)


@router.post("/context-prediction/weight-compare")
def post_rime_context_weight_compare(
    req: RimeWeightCompareRequest,
    _: BaseDevice = Depends(verify_api_token),
):
    return collect_rime_context_weight_compare(
        req.candidates,
        source=req.source,
        limit=req.limit,
    )


@router.post("/context-prediction/weight-compare/adjust")
def post_rime_context_weight_compare_adjust(
    req: RimeWeightCompareAdjustRequest,
    background_tasks: BackgroundTasks,
    _: BaseDevice = Depends(verify_api_token),
):
    try:
        payload = adjust_rime_context_weight_compare_candidate(
            prefix=req.prefix,
            candidate=req.candidate,
            weight=req.weight,
            candidates=req.candidates,
            source=req.source,
            limit=req.limit,
        )
        background_tasks.add_task(rebuild_rime_context_prediction_snapshot, None, allow_snapshot_fallback=True)
        return payload
    except RimeContextPredictionError as exc:
        _raise_rime_error(exc)


@router.get("/context-prediction/runtime-config")
def get_rime_runtime_config(
    _: BaseDevice = Depends(verify_api_token),
):
    return collect_rime_runtime_config()


@router.patch("/context-prediction/runtime-config")
def patch_rime_runtime_config(
    req: RimeRuntimeConfigUpdateRequest,
    _: BaseDevice = Depends(verify_api_token),
):
    try:
        return update_rime_runtime_config(req.config)
    except RimeContextPredictionError as exc:
        _raise_rime_error(exc)


@router.get("/context-prediction/history-article")
def get_rime_context_prediction_history_article(
    limit: int = Query(20000, ge=1, le=200000),
    page: int | None = Query(None, ge=1),
    page_size: int | None = Query(None, ge=1, le=5000),
    _: BaseDevice = Depends(verify_api_token),
):
    return collect_rime_context_prediction_history_article(limit=limit, page=page, page_size=page_size)


@router.put("/context-prediction/history-article")
def put_rime_context_prediction_history_article(
    req: RimeHistoryArticleSaveRequest,
    _: BaseDevice = Depends(verify_api_token),
):
    try:
        return save_rime_context_prediction_history_article(req.content)
    except RimeContextPredictionError as exc:
        _raise_rime_error(exc)


@router.get("/context-prediction/articles")
def get_rime_context_prediction_articles(
    _: BaseDevice = Depends(verify_api_token),
):
    return collect_rime_context_prediction_articles()


@router.get("/context-prediction/articles/{article_id}/content")
def get_rime_context_prediction_article_content(
    article_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(DEFAULT_HISTORY_ARTICLE_PAGE_SIZE, ge=1, le=20000),
    _: BaseDevice = Depends(verify_api_token),
):
    return collect_rime_context_prediction_article_content(article_id, page=page, page_size=page_size)


@router.get("/context-prediction/lint")
def get_rime_context_prediction_lint(
    source: str = Query("all"),
    mode: str = Query("rules"),
    limit: int = Query(200, ge=1, le=1000),
    history_limit: int = Query(20000, ge=1, le=200000),
    _: BaseDevice = Depends(verify_api_token),
):
    return collect_rime_context_prediction_lint(
        source=source,
        mode=mode,
        limit=limit,
        history_limit=history_limit,
    )


@router.delete("/context-prediction/candidates")
def delete_rime_context_prediction_candidate_endpoint(
    req: RimeCandidateDeleteRequest,
    _: BaseDevice = Depends(verify_api_token),
):
    try:
        return delete_rime_context_prediction_candidate(
            context=req.context,
            prefix=req.prefix,
            candidate=req.candidate,
        )
    except RimeContextPredictionError as exc:
        _raise_rime_error(exc)


@router.patch("/context-prediction/candidates")
def patch_rime_context_prediction_candidate_endpoint(
    req: RimeCandidateUpdateRequest,
    _: BaseDevice = Depends(verify_api_token),
):
    try:
        return update_rime_context_prediction_candidate(
            original_context=req.original_context,
            original_prefix=req.original_prefix,
            original_candidate=req.original_candidate,
            context=req.context,
            prefix=req.prefix,
            candidate=req.candidate,
            weight=req.weight,
        )
    except RimeContextPredictionError as exc:
        _raise_rime_error(exc)


@router.post("/context-prediction/articles")
def post_rime_context_prediction_article(
    req: RimeArticleImportRequest,
    _: BaseDevice = Depends(verify_api_token),
):
    try:
        return import_rime_context_prediction_article(
            title=req.title,
            content=req.content,
            enabled=req.enabled,
            source_type=req.source_type or "imported_article",
            weight_multiplier=req.weight_multiplier,
        )
    except RimeContextPredictionError as exc:
        _raise_rime_error(exc)


@router.patch("/context-prediction/articles/{article_id}")
def patch_rime_context_prediction_article(
    article_id: str,
    req: RimeArticleUpdateRequest,
    _: BaseDevice = Depends(verify_api_token),
):
    try:
        return update_rime_context_prediction_article(
            article_id,
            title=req.title,
            enabled=req.enabled,
        )
    except RimeContextPredictionError as exc:
        _raise_rime_error(exc)


@router.put("/context-prediction/articles/{article_id}/content")
def put_rime_context_prediction_article_content(
    article_id: str,
    req: RimeArticleContentSaveRequest,
    _: BaseDevice = Depends(verify_api_token),
):
    try:
        return save_rime_context_prediction_article_content(
            article_id,
            req.content,
            page=req.page,
            page_size=req.page_size,
        )
    except RimeContextPredictionError as exc:
        _raise_rime_error(exc)


@router.delete("/context-prediction/articles/{article_id}")
def delete_rime_context_prediction_article_endpoint(
    article_id: str,
    _: BaseDevice = Depends(verify_api_token),
):
    try:
        return delete_rime_context_prediction_article(article_id)
    except RimeContextPredictionError as exc:
        _raise_rime_error(exc)
