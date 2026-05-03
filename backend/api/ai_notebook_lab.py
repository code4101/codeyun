from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from backend.core.auth import verify_api_token
from backend.core.device import BaseDevice
from backend.core.notebook_lab import (
    NotebookBindingUpdateRequest,
    NotebookLabError,
    NotebookRunResponse,
    NotebookState,
    RunCellRequest,
    RunCodeRequest,
    SaveNotebookRequest,
    UpdateCellRequest,
    get_notebook_state,
    interrupt_notebook_kernel,
    run_notebook_cell,
    run_temporary_code,
    update_notebook_binding,
    update_notebook_cell,
)
from backend.core.notebook_lab.service import save_notebook
from backend.db import get_session


router = APIRouter()


def _raise_notebook_http_error(exc: NotebookLabError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.get("/state", response_model=NotebookState)
def get_ai_notebook_state(
    session: Session = Depends(get_session),
    device: BaseDevice = Depends(verify_api_token),
):
    try:
        return get_notebook_state(session, entry_id="", device_id=device.device_id)
    except NotebookLabError as exc:
        _raise_notebook_http_error(exc)


@router.put("/binding", response_model=NotebookState)
def put_ai_notebook_binding(
    payload: NotebookBindingUpdateRequest,
    session: Session = Depends(get_session),
    device: BaseDevice = Depends(verify_api_token),
):
    try:
        return update_notebook_binding(
            session,
            entry_id="",
            device_id=device.device_id,
            notebook_path=payload.notebook_path,
        )
    except NotebookLabError as exc:
        _raise_notebook_http_error(exc)


@router.put("/cells/{cell_id}", response_model=NotebookState)
def put_ai_notebook_cell(
    cell_id: str,
    payload: UpdateCellRequest,
    session: Session = Depends(get_session),
    device: BaseDevice = Depends(verify_api_token),
):
    try:
        return update_notebook_cell(
            session,
            entry_id="",
            device_id=device.device_id,
            cell_id=cell_id,
            notebook_hash=payload.notebook_hash,
            source=payload.source,
        )
    except NotebookLabError as exc:
        _raise_notebook_http_error(exc)


@router.post("/save", response_model=NotebookState)
def post_ai_notebook_save(
    payload: SaveNotebookRequest,
    session: Session = Depends(get_session),
    device: BaseDevice = Depends(verify_api_token),
):
    try:
        return save_notebook(
            session,
            entry_id="",
            device_id=device.device_id,
            notebook_hash=payload.notebook_hash,
        )
    except NotebookLabError as exc:
        _raise_notebook_http_error(exc)


@router.post("/run-cell", response_model=NotebookRunResponse)
def post_ai_notebook_run_cell(
    payload: RunCellRequest,
    session: Session = Depends(get_session),
    device: BaseDevice = Depends(verify_api_token),
):
    try:
        return run_notebook_cell(
            session,
            entry_id="",
            device_id=device.device_id,
            notebook_hash=payload.notebook_hash,
            cell_id=payload.cell_id,
        )
    except NotebookLabError as exc:
        _raise_notebook_http_error(exc)


@router.post("/run-code", response_model=NotebookRunResponse)
def post_ai_notebook_run_code(
    payload: RunCodeRequest,
    session: Session = Depends(get_session),
    device: BaseDevice = Depends(verify_api_token),
):
    try:
        return run_temporary_code(
            session,
            entry_id="",
            device_id=device.device_id,
            code=payload.code,
        )
    except NotebookLabError as exc:
        _raise_notebook_http_error(exc)


@router.post("/interrupt", response_model=NotebookState)
def post_ai_notebook_interrupt(
    session: Session = Depends(get_session),
    device: BaseDevice = Depends(verify_api_token),
):
    try:
        return interrupt_notebook_kernel(session, entry_id="", device_id=device.device_id)
    except NotebookLabError as exc:
        _raise_notebook_http_error(exc)
