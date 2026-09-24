from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class FanxiuKernelSchedulerLogEntry(BaseModel):
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


class FanxiuKernelSchedulerLogResponse(BaseModel):
    entries: list[FanxiuKernelSchedulerLogEntry] = Field(default_factory=list)
    path: str = ""


class FanxiuKernelSchedulerCellLog(BaseModel):
    id: str = ""
    title: str = ""
    source_kind: str = "command"
    source: str = ""
    started_at: str = ""
    ended_at: str = ""
    entries: list[FanxiuKernelSchedulerLogEntry] = Field(default_factory=list)


class FanxiuKernelSchedulerCellLogResponse(BaseModel):
    cells: list[FanxiuKernelSchedulerCellLog] = Field(default_factory=list)
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


class FanxiuInfoWindowSettings(BaseModel):
    enabled: bool = True
    auto_refresh: bool = False
    show_scene_id: bool = True
    show_scene_score: bool = True
    # #699 shows the live 累计魔晶 instead of the scene number. The field must
    # live here too: this model is both the POST body and the GET response, so
    # an unlisted key would be silently dropped in both directions and the
    # switch would look wired while changing nothing.
    show_magic_crystal: bool = True
    show_xutian_currency: bool = True
    show_scene_identity_shapes: bool = True
    show_all_shapes: bool = False


class FanxiuInfoWindowSettingsRequest(FanxiuInfoWindowSettings):
    entry_id: str = ""


class FanxiuInfoWindowControlStatus(BaseModel):
    ok: bool = True
    settings: FanxiuInfoWindowSettings = Field(default_factory=FanxiuInfoWindowSettings)
    renderer: dict[str, Any] = Field(default_factory=dict)
    scene: dict[str, Any] = Field(default_factory=dict)


class FanxiuKernelSchedulerStatus(BaseModel):
    ok: bool = True
    behavior_tree_enabled: bool = True
    running: bool = False
    guard_group_enabled: bool = True
    guard_group_running: bool = False
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
    kernel: dict[str, Any] = Field(default_factory=dict)
    kernel_restart: dict[str, Any] = Field(default_factory=dict)
    started_at: float = 0
    updated_at: float = 0
    finished_at: Optional[float] = None
    error: str = ""
    output: str = ""
    execution_count: Optional[int] = None
    logs: list[dict[str, Any]] = Field(default_factory=list)
    cell_logs: list[dict[str, Any]] = Field(default_factory=list)


class FanxiuKernelSchedulerCodeCellRequest(BaseModel):
    entry_id: str
    code: str = Field(min_length=1)
    timeout_seconds: float = Field(120.0, ge=1.0, le=21600.0)
    max_output_chars: int = Field(4000, ge=200, le=20000)


class FanxiuKernelSchedulerTaskCellRequest(BaseModel):
    entry_id: str
    task_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: Optional[float] = Field(default=None, ge=1.0, le=21600.0)
    effective_now: Optional[datetime] = None


class FanxiuKernelSchedulerKernelRestartRequest(BaseModel):
    entry_id: str
    timeout_seconds: float = Field(5.0, ge=0.1, le=60.0)


class FanxiuKernelSchedulerDeviceRestartRequest(BaseModel):
    entry_id: str


class FanxiuKernelSchedulerDeviceRestartResponse(BaseModel):
    ok: bool = True
    recovered: bool = False
    status: str = ""
    message: str = ""
    device: dict[str, Any] = Field(default_factory=dict)
    scheduler: FanxiuKernelSchedulerStatus = Field(default_factory=FanxiuKernelSchedulerStatus)


class FanxiuKernelSchedulerStopRequest(BaseModel):
    entry_id: Optional[str] = None


class FanxiuKernelSchedulerBehaviorTreeRequest(BaseModel):
    entry_id: str
    enabled: bool


class FanxiuKernelSchedulerGuardRequest(BaseModel):
    entry_id: str
    guard_id: str = "device_health"
    enabled: bool
    interval_seconds: float = Field(2.0, ge=0.5, le=30)


class FanxiuKernelSchedulerGuardGroupRequest(BaseModel):
    entry_id: str
    enabled: bool


class FanxiuKernelSchedulerIsolationRequest(BaseModel):
    entry_id: str
    enabled: bool
    token: str = ""
    ttl_seconds: float = Field(21600.0, ge=60.0, le=86400.0)


class FanxiuKernelSchedulerTaskItem(BaseModel):
    id: str
    task_type: str
    label: str = ""
    supported: bool = False
    template_id: str = ""
    template_label: str = ""
    template_source: str = "preset"
    trigger_description: str = ""
    # Persisted compatibility value.  It predates the Kernel Scheduler
    # terminology and must not be used as a new Python/module name.
    source: str = "kernel_scheduler"
    legacy_name: str = ""
    interruptible: bool = True
    dispatch_level: int = Field(0, ge=0, le=5)
    dispatch_order: int = Field(0, ge=0, le=9999)
    next_time: Optional[str] = None
    original_next_time: Optional[str] = None
    schedule_bias_minutes: int = Field(0, ge=0)
    last_run_at: Optional[str] = None
    last_result: str = ""
    error_retry_delay_seconds: int = Field(600, ge=0)
    payload: dict[str, Any] = Field(default_factory=dict)
    scheduler_meta: Optional[dict[str, Any]] = None
    attempt_id: Optional[str] = None
    attempt_original_trigger: Optional[str] = None
    attempt_kernel_generation: Optional[int] = None
    attempt_kernel_idle_since: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None


class FanxiuKernelSchedulerTasksResponse(BaseModel):
    ok: bool = True
    tasks: list[FanxiuKernelSchedulerTaskItem] = Field(default_factory=list)
    job_group_enabled: bool = True
    path: str = ""


class FanxiuKernelSchedulerTaskUpdate(BaseModel):
    id: str
    dispatch_level: Optional[int] = Field(default=None, ge=0, le=5)
    dispatch_order: Optional[int] = Field(default=None, ge=0, le=9999)
    trigger_description: Optional[str] = None
    error_retry_delay_seconds: Optional[int] = Field(default=None, ge=0)


class FanxiuGameStateInspectionStatus(BaseModel):
    ok: bool = True
    name: str = "游戏状态巡检"
    description: str = ""
    enabled: bool = False
    status: str = "paused"
    interval_seconds: float = 60.0
    probe_count: int = 0
    probes: list[dict[str, Any]] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    service_pid: Optional[int] = None
    last_checked_at: Optional[str] = None
    next_check_at: Optional[str] = None
    last_result: str = ""
    last_message: str = ""
    last_duration_ms: Optional[int] = None
    facts: dict[str, Any] = Field(default_factory=dict)
    errors: list[dict[str, Any]] = Field(default_factory=list)
    recoveries: dict[str, dict[str, Any]] = Field(default_factory=dict)
    due_task_ids: list[str] = Field(default_factory=list)
    updated_at: Optional[float] = None


class FanxiuKernelSchedulerTimeSequenceItem(BaseModel):
    task_id: str
    task_label: str
    original_next_time: Optional[str] = None
    effective_next_time: Optional[str] = None
    bias_minutes: int = 0


class FanxiuKernelSchedulerTimeSequenceGroup(BaseModel):
    key: str
    original_time: str
    task_ids: list[str] = Field(default_factory=list)
    items: list[FanxiuKernelSchedulerTimeSequenceItem] = Field(default_factory=list)


class FanxiuKernelSchedulerTimeSequenceResponse(BaseModel):
    ok: bool = True
    groups: list[FanxiuKernelSchedulerTimeSequenceGroup] = Field(default_factory=list)


class FanxiuKernelSchedulerTimeSequenceUpdateGroup(BaseModel):
    key: str
    task_ids: list[str] = Field(default_factory=list)


class FanxiuKernelSchedulerTimeSequenceUpdateRequest(BaseModel):
    groups: list[FanxiuKernelSchedulerTimeSequenceUpdateGroup] = Field(default_factory=list)


class FanxiuKernelSchedulerPlanItem(BaseModel):
    id: str
    task_type: str
    label: str = ""
    supported: bool = False
    template_id: str = ""
    template_label: str = ""
    template_source: str = "preset"
    trigger_description: str = ""
    due: bool = False
    runnable: bool = False
    reason: str = ""
    next_time: Optional[str] = None
    original_next_time: Optional[str] = None
    schedule_bias_minutes: int = 0
    last_result: str = ""
    fact: dict[str, Any] = Field(default_factory=dict)


class FanxiuKernelSchedulerPlanResponse(BaseModel):
    ok: bool = True
    next_action: str = "idle"
    message: str = ""
    job_group_enabled: bool = True
    blocking_overlays: list[dict[str, Any]] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)
    facts_summary: dict[str, Any] = Field(default_factory=dict)
    due_tasks: list[FanxiuKernelSchedulerPlanItem] = Field(default_factory=list)
    tasks: list[FanxiuKernelSchedulerPlanItem] = Field(default_factory=list)
    path: str = ""


class FanxiuKernelSchedulerRunDueRequest(BaseModel):
    entry_id: str


class FanxiuKernelSchedulerSettingsRequest(BaseModel):
    job_group_enabled: bool
    entry_id: str = ""


class FanxiuKernelSchedulerRunNowRequest(BaseModel):
    entry_id: str
    task_id: str
    payload: dict[str, Any] = Field(default_factory=dict)
    interrupt_same_group: bool = True
    effective_now: Optional[datetime] = None
    business_time_mode: Literal["planned", "current"] = "planned"


class FanxiuKernelSchedulerTriggerOnceRequest(BaseModel):
    entry_id: str
    task_id: str


class FanxiuKernelSchedulerTriggerOnceResponse(BaseModel):
    ok: bool = True
    task_id: str
    next_time: str


class FanxiuKernelSchedulerNextTimeRequest(BaseModel):
    entry_id: str
    task_id: str
    next_time: Optional[str] = None


class FanxiuKernelSchedulerNextTimeResponse(BaseModel):
    ok: bool = True
    task_id: str
    next_time: Optional[str] = None
