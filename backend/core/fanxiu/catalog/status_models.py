from __future__ import annotations

from typing import Any, List, Optional

from pydantic import BaseModel, Field


class FanxiuProcessItem(BaseModel):
    pid: int
    parent_pid: Optional[int] = None
    name: str
    command_line: str
    created_at: Optional[str] = None
    matched_reason: str


class FanxiuProcessListResponse(BaseModel):
    items: List[FanxiuProcessItem] = Field(default_factory=list)


























































class FanxiuPlayerProfileRecordListResponse(BaseModel):
    ok: bool = True
    count: int = 0
    records: list[dict[str, Any]] = Field(default_factory=list)
    daily_count: int = 0
    daily_records: list[dict[str, Any]] = Field(default_factory=list)
    xianlv_team_count: int = 0
    xianlv_team_records: list[dict[str, Any]] = Field(default_factory=list)
    xianlv_team_daily_count: int = 0
    xianlv_team_daily_records: list[dict[str, Any]] = Field(default_factory=list)


class FanxiuServerRelationTreeResponse(BaseModel):
    ok: bool = True
    version: int = 1
    ordering: str = "protection_desc"
    groups: list[dict[str, Any]] = Field(default_factory=list)


class FanxiuServerRelationTreeUpdateRequest(BaseModel):
    version: int = 1
    ordering: str = "protection_desc"
    groups: list[dict[str, Any]] = Field(default_factory=list)


class FanxiuMailRecordListResponse(BaseModel):
    ok: bool = True
    count: int = 0
    total: int = 0
    offset: int = 0
    limit: int = 0
    records: list[dict[str, Any]] = Field(default_factory=list)


class FanxiuMailRecordUpdateRequest(BaseModel):
    status: str


class FanxiuMailRecordUpdateResponse(BaseModel):
    ok: bool = True
    record: dict[str, Any] = Field(default_factory=dict)


class FanxiuMailRuntimeSyncResponse(BaseModel):
    ok: bool = True
    complete: bool = False
    source: str = "runtime_memory"
    reason: str = ""
    inserted: int = 0
    updated: int = 0
    absent: int = 0
    record_count: int = 0
    captured_at: str = ""


class FanxiuStorageBagAutoClaimUpdateRequest(BaseModel):
    auto_claim: bool


class FanxiuStorageBagAutoClaimUpdateResponse(BaseModel):
    ok: bool = True
    base_id: int
    auto_claim: bool


class FanxiuStorageBagNoteUpdateRequest(BaseModel):
    note: str = Field(default="", max_length=2000)


class FanxiuStorageBagNoteUpdateResponse(BaseModel):
    ok: bool = True
    base_id: int
    note: str




































class LocalScriptProcessItem(BaseModel):
    pid: int
    parent_pid: Optional[int] = None
    name: str
    kind: str
    script: str
    script_path: Optional[str] = None
    command_line: str
    cwd: Optional[str] = None
    created_at: Optional[str] = None
    runtime_seconds: Optional[int] = None
    project_hint: str = ""
    is_fanxiu: bool = False


class LocalScriptProcessListResponse(BaseModel):
    items: List[LocalScriptProcessItem] = Field(default_factory=list)


class FanxiuProcessTerminateError(BaseModel):
    pid: int
    error: str


class FanxiuProcessTerminateResponse(BaseModel):
    matched: List[FanxiuProcessItem] = Field(default_factory=list)
    terminated: List[FanxiuProcessItem] = Field(default_factory=list)
    remaining: List[FanxiuProcessItem] = Field(default_factory=list)
    errors: List[FanxiuProcessTerminateError] = Field(default_factory=list)
