from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


NotebookKernelStatus = Literal["stopped", "starting", "idle", "busy", "error"]
NotebookRunStatus = Literal["success", "error", "interrupted"]


class NotebookBinding(BaseModel):
    entry_id: str
    device_id: str
    notebook_path: str
    workdir: str
    exists: bool
    updated_at: float | None = None


class NotebookCell(BaseModel):
    cell_id: str
    index: int
    cell_type: str
    source: str
    execution_count: int | None = None
    outputs_summary: list[str] = Field(default_factory=list)
    stale: bool = False
    last_run_status: str | None = None
    last_run_at: float | None = None


class NotebookState(BaseModel):
    session_id: str
    entry_id: str
    device_id: str
    binding: NotebookBinding
    notebook_path: str
    notebook_hash: str
    kernel_status: NotebookKernelStatus
    cells: list[NotebookCell] = Field(default_factory=list)
    stale_cell_ids: list[str] = Field(default_factory=list)
    last_error: str | None = None
    dirty: bool = False


class NotebookBindingUpdateRequest(BaseModel):
    notebook_path: str | None = None


class UpdateCellRequest(BaseModel):
    notebook_hash: str
    source: str


class SaveNotebookRequest(BaseModel):
    notebook_hash: str


class RunCellRequest(BaseModel):
    notebook_hash: str
    cell_id: str


class RunCodeRequest(BaseModel):
    code: str


class NotebookRunResponse(BaseModel):
    status: NotebookRunStatus
    outputs_summary: list[str] = Field(default_factory=list)
    state: NotebookState
