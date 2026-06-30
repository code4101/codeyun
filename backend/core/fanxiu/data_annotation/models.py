from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class FanxiuDataAnnotationRuntimeLogEntry(BaseModel):
    id: str = ""
    time: str = ""
    kind: str = ""
    scope: str = ""
    item_id: str = ""
    message: str = ""
    action: str = ""
    source_file: str = ""
    source_path: str = ""
    source_line: int | None = None
    source_expr: str = ""
    ts: str = ""


class FanxiuDataAnnotationRuntimeLogResponse(BaseModel):
    entries: list[FanxiuDataAnnotationRuntimeLogEntry] = Field(default_factory=list)
    path: str = ""


class FanxiuDataAnnotationRuntimeCellLog(BaseModel):
    id: str = ""
    title: str = ""
    source_kind: str = "command"
    source: str = ""
    started_at: str = ""
    ended_at: str = ""
    entries: list[FanxiuDataAnnotationRuntimeLogEntry] = Field(default_factory=list)


class FanxiuDataAnnotationRuntimeCellLogResponse(BaseModel):
    cells: list[FanxiuDataAnnotationRuntimeCellLog] = Field(default_factory=list)
    path: str = ""


class FanxiuDataAnnotationWorldFactsResponse(BaseModel):
    ok: bool = True
    facts: dict[str, Any] = Field(default_factory=dict)
    path: str = ""


class FanxiuDataAnnotationDoctorWatchLatestResponse(BaseModel):
    ok: bool = True
    exists: bool = False
    path: str = ""
    message: str = ""
    snapshot: dict[str, Any] = Field(default_factory=dict)
    heartbeat: dict[str, Any] = Field(default_factory=dict)


class FanxiuDataAnnotationDoctorWatchEnsureResponse(BaseModel):
    ok: bool = True
    started: bool = False
    pid: Optional[int] = None
    reason: str = ""
    heartbeat: dict[str, Any] = Field(default_factory=dict)
    previous_heartbeat: dict[str, Any] = Field(default_factory=dict)
    latest: dict[str, Any] = Field(default_factory=dict)
    output_path: str = ""
    stdout_path: str = ""
    stderr_path: str = ""
    command: list[str] = Field(default_factory=list)


class FanxiuDataAnnotationRuntimeStatus(BaseModel):
    ok: bool = True
    behavior_tree_enabled: bool = True
    service_running: bool = False
    running: bool = False
    guard_group_enabled: bool = True
    guard_group_running: bool = False
    guard_enabled: bool = True
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
    kernel_status: dict[str, Any] = Field(default_factory=dict)
    cell_status: dict[str, Any] = Field(default_factory=dict)
    scheduler_status: dict[str, Any] = Field(default_factory=dict)
    orchestration_status: dict[str, Any] = Field(default_factory=dict)
    cell_tick: dict[str, Any] = Field(default_factory=dict)
    kernel_restart: dict[str, Any] = Field(default_factory=dict)
    isolation: dict[str, Any] = Field(default_factory=dict)
    started_at: float = 0
    updated_at: float = 0
    finished_at: float = 0
    error: str = ""
    logs: list[dict[str, Any]] = Field(default_factory=list)
    cell_logs: list[dict[str, Any]] = Field(default_factory=list)
    queued_job: dict[str, Any] = Field(default_factory=dict)


class FanxiuDataAnnotationRuntimeTaskRequest(BaseModel):
    entry_id: str
    task_type: str
    payload: dict[str, Any] = Field(default_factory=dict)


class FanxiuDataAnnotationRuntimeCellTickRequest(BaseModel):
    entry_id: str
    guard: bool = True
    manual_job: bool = True
    scheduled_job: bool = True
    run_mode: Literal["tick_once", "until_idle", "current_job"] = "tick_once"
    max_ticks: int = Field(10, ge=1, le=100)
    timeout_seconds: float = Field(30.0, ge=0.1, le=300.0)


class FanxiuDataAnnotationRuntimeKernelRestartRequest(BaseModel):
    entry_id: str
    timeout_seconds: float = Field(5.0, ge=0.1, le=60.0)


class FanxiuDataAnnotationRuntimeStopRequest(BaseModel):
    entry_id: Optional[str] = None


class FanxiuDataAnnotationRuntimeBehaviorTreeRequest(BaseModel):
    entry_id: str
    enabled: bool


class FanxiuDataAnnotationRuntimeGuardRequest(BaseModel):
    entry_id: str
    guard_id: str = "close_popups"
    enabled: bool
    interval_seconds: float = Field(2.0, ge=0.5, le=30)


class FanxiuDataAnnotationRuntimeGuardGroupRequest(BaseModel):
    entry_id: str
    enabled: bool


class FanxiuDataAnnotationRuntimeIsolationRequest(BaseModel):
    entry_id: str
    enabled: bool
    token: str = ""
    ttl_seconds: float = Field(21600.0, ge=60.0, le=86400.0)


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
    weekdays: list[int] = Field(default_factory=list)
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
    job_group_enabled: bool = True
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
    job_group_enabled: bool = True
    blocking_overlays: list[dict[str, Any]] = Field(default_factory=list)
    runtime: dict[str, Any] = Field(default_factory=dict)
    facts_summary: dict[str, Any] = Field(default_factory=dict)
    due_tasks: list[FanxiuDataAnnotationSchedulerPlanItem] = Field(default_factory=list)
    tasks: list[FanxiuDataAnnotationSchedulerPlanItem] = Field(default_factory=list)
    path: str = ""


class FanxiuDataAnnotationSchedulerRunDueRequest(BaseModel):
    entry_id: str


class FanxiuDataAnnotationSchedulerAdvanceNextRequest(BaseModel):
    entry_id: str
    task_id: str


class FanxiuDataAnnotationSchedulerSettingsRequest(BaseModel):
    job_group_enabled: bool


class FanxiuDataAnnotationSchedulerRunNowRequest(BaseModel):
    entry_id: str
    task_id: str
    payload: dict[str, Any] = Field(default_factory=dict)
    interrupt_same_group: bool = True
