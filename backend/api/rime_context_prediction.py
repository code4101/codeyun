from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from backend.core.auth import verify_api_token
from backend.core.device import BaseDevice
from backend.core.rime_context_prediction import (
    RimeContextPredictionError,
    collect_rime_context_prediction_articles,
    collect_rime_context_prediction_history_article,
    collect_rime_context_prediction_lint,
    collect_rime_context_prediction_tree,
    delete_rime_context_prediction_article,
    delete_rime_context_prediction_candidate,
    import_rime_context_prediction_article,
    refresh_rime_context_prediction_tree,
    save_rime_context_prediction_history_article,
    update_rime_context_prediction_candidate,
    update_rime_context_prediction_article,
)


router = APIRouter()


class RimeArticleImportRequest(BaseModel):
    title: str | None = None
    content: str = Field(min_length=1)
    enabled: bool = True


class RimeArticleUpdateRequest(BaseModel):
    title: str | None = None
    enabled: bool | None = None


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


@router.delete("/context-prediction/articles/{article_id}")
def delete_rime_context_prediction_article_endpoint(
    article_id: str,
    _: BaseDevice = Depends(verify_api_token),
):
    try:
        return delete_rime_context_prediction_article(article_id)
    except RimeContextPredictionError as exc:
        _raise_rime_error(exc)
