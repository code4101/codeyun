from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class FanxiuDataAnnotationRuntimeLogEntry(BaseModel):
    id: str = ""
    time: str = ""
    kind: str = ""
    scope: str = ""
    item_id: str = ""
    message: str = ""
    ts: str = ""


class FanxiuDataAnnotationRuntimeLogResponse(BaseModel):
    entries: list[FanxiuDataAnnotationRuntimeLogEntry] = Field(default_factory=list)
    path: str = ""


class FanxiuDataAnnotationWorldFactsResponse(BaseModel):
    ok: bool = True
    facts: dict[str, Any] = Field(default_factory=dict)
    path: str = ""


class FanxiuDataAnnotationRuntimeStatus(BaseModel):
    ok: bool = True
    service_running: bool = False
    running: bool = False
    guard_enabled: bool = False
    guard_running: bool = False
    guard_entry_id: str = ""
    guard_interval_seconds: float = 2.0
    guard_items: dict[str, Any] = Field(default_factory=dict)
    status: str = "idle"
    entry_id: str = ""
    task_type: str = ""
    current_task: str = ""
    phase: str = ""
    current_scene: Optional[int] = None
    message: str = ""
    current_index: int = 0
    total: int = 0
    current_code: str = ""
    current_task_id: str = ""
    interruptible: bool = True
    last_guard_event: dict[str, Any] = Field(default_factory=dict)
    started_at: float = 0
    updated_at: float = 0
    finished_at: float = 0
    error: str = ""
    logs: list[dict[str, Any]] = Field(default_factory=list)
    queued_job: dict[str, Any] = Field(default_factory=dict)


class FanxiuDataAnnotationRuntimeTaskRequest(BaseModel):
    entry_id: str
    task_type: str
    payload: dict[str, Any] = Field(default_factory=dict)


class FanxiuDataAnnotationRuntimeStopRequest(BaseModel):
    entry_id: Optional[str] = None


class FanxiuDataAnnotationRuntimeGuardRequest(BaseModel):
    entry_id: str
    guard_id: str = "close_popups"
    enabled: bool
    interval_seconds: float = Field(2.0, ge=0.5, le=30)


class FanxiuDataAnnotationSchedulerTaskItem(BaseModel):
    id: str
    task_type: str
    label: str = ""
    supported: bool = False
    source: str = "manual"
    schedule_kind: str = "manual"
    legacy_name: str = ""
    enabled: bool = False
    interruptible: bool = True
    next_time: Optional[str] = None
    schedule_times: list[str] = Field(default_factory=list)
    window: Optional[list[str]] = None
    last_run_at: Optional[str] = None
    last_result: str = ""
    retry_after: Optional[str] = None
    cooldown_seconds: int = 0
    payload: dict[str, Any] = Field(default_factory=dict)
    checkpoint: Optional[dict[str, Any]] = None


class FanxiuDataAnnotationSchedulerTasksResponse(BaseModel):
    ok: bool = True
    tasks: list[FanxiuDataAnnotationSchedulerTaskItem] = Field(default_factory=list)
    path: str = ""


class FanxiuDataAnnotationSchedulerPlanItem(BaseModel):
    id: str
    task_type: str
    label: str = ""
    supported: bool = False
    enabled: bool = False
    due: bool = False
    runnable: bool = False
    reason: str = ""
    next_time: Optional[str] = None
    retry_after: Optional[str] = None
    last_result: str = ""
    fact: dict[str, Any] = Field(default_factory=dict)


class FanxiuDataAnnotationSchedulerPlanResponse(BaseModel):
    ok: bool = True
    next_action: str = "idle"
    message: str = ""
    runtime: dict[str, Any] = Field(default_factory=dict)
    facts_summary: dict[str, Any] = Field(default_factory=dict)
    due_tasks: list[FanxiuDataAnnotationSchedulerPlanItem] = Field(default_factory=list)
    tasks: list[FanxiuDataAnnotationSchedulerPlanItem] = Field(default_factory=list)
    path: str = ""


class FanxiuDataAnnotationSchedulerRunDueRequest(BaseModel):
    entry_id: str


class FanxiuDataAnnotationSchedulerRunNowRequest(BaseModel):
    entry_id: str
    task_id: str
    payload: dict[str, Any] = Field(default_factory=dict)
