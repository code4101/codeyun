"""Preparation-only API. There is deliberately no business execution endpoint."""
from typing import Literal
import io
import json
import zipfile
import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlmodel import Session

from backend.core.access.auth import get_current_active_user
from backend.core.access.feature_access_guard import require_feature_access_dependency
from backend.core.note_preparation import PreparationStore, collect_sources
from backend.db import get_session
from backend.models import User

router = APIRouter(dependencies=[Depends(require_feature_access_dependency("notes.preparation"))])


class ReviewLabel(BaseModel):
    decision: Literal["pending", "interested", "deferred", "dismissed"]
    human_note: str = Field(default="", max_length=10000)


@router.get("")
def overview(user: User = Depends(get_current_active_user)):
    return PreparationStore(user.id).overview()


@router.post("/snapshot")
def snapshot(session: Session = Depends(get_session), user: User = Depends(get_current_active_user)):
    # FastAPI runs this synchronous handler in its pool, keeping long scans off
    # the live application's event loop.
    sources, coverage = asyncio.run(collect_sources(session, user))
    result = PreparationStore(user.id).cache_snapshot(sources, coverage)
    return {"id": result["id"], "coverage": coverage, "source_count": len(sources)}


@router.get("/snapshots/{snapshot_id}")
def read_snapshot(snapshot_id: str, evidence_ids: list[str] | None = Query(default=None), user: User = Depends(get_current_active_user)):
    try:
        result = PreparationStore(user.id).snapshot(snapshot_id)
    except ValueError:
        result = None
    if result is None:
        raise HTTPException(404, "快照不存在")
    if evidence_ids is not None:
        result["sources"] = [source for source in result["sources"] if source["id"] in set(evidence_ids)]
    return result


@router.patch("/packets/{packet_id}")
def review(packet_id: str, body: ReviewLabel, user: User = Depends(get_current_active_user)):
    try:
        return PreparationStore(user.id).decision(packet_id, body.decision, body.human_note)
    except KeyError:
        raise HTTPException(404, "筹备包不存在")


@router.get("/export")
def export(user: User = Depends(get_current_active_user)):
    store = PreparationStore(user.id)
    result = store.overview()
    snapshot_ids = {item["snapshot_id"] for item in result["packets"]}
    snapshot_ids.update(revision["snapshot_id"] for item in result["packets"]
                        for revision in item.get("history", []) if revision.get("snapshot_id"))
    if result["snapshot"]:
        snapshot_ids.add(result["snapshot"]["id"])
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("preparation.json", json.dumps(result, ensure_ascii=False))
        for identity in sorted(snapshot_ids):
            archive.writestr(f"snapshots/{identity}.json", json.dumps(store.snapshot(identity), ensure_ascii=False))
    return Response(output.getvalue(), media_type="application/zip", headers={"Content-Disposition": 'attachment; filename="note-preparation.zip"'})
