/** Kernel、Cell、作业调度与运行观测的前端契约。
 * 页面只组合这些业务接口；HTTP 传输仍统一使用 api 客户端。
 */
import api from '@/api';
/** 状态快照中的原始日志；独立日志接口会额外分配稳定 id。 */
export interface FanxiuKernelSchedulerLogData {
  id?: string;
  time: string;
  kind: string;
  scope?: string;
  item_id?: string;
  message: string;
  action?: string;
  source_file?: string;
  source_path?: string;
  source_line?: number | null;
  source_expr?: string;
  ts?: string;
}

export interface FanxiuKernelSchedulerLogEntry extends FanxiuKernelSchedulerLogData {
  id: string;
}

export interface FanxiuKernelSchedulerLogResponse {
  entries: FanxiuKernelSchedulerLogEntry[];
  path: string;
}

export interface FanxiuKernelSchedulerCellLog {
  id: string;
  title: string;
  source_kind: string;
  source: string;
  started_at: string;
  ended_at: string;
  entries: FanxiuKernelSchedulerLogEntry[];
}

export interface FanxiuKernelSchedulerCellLogResponse {
  cells: FanxiuKernelSchedulerCellLog[];
  path: string;
}

export interface FanxiuKernelSchedulerGuardItem {
  id: string;
  label: string;
  message?: string;
  enabled?: boolean;
  running?: boolean;
  entry_id?: string;
  updated_at?: number;
}

export interface FanxiuDataAnnotationWorldFactsResponse {
  ok: boolean;
  facts: Record<string, unknown>;
  path: string;
}

export interface FanxiuDataAnnotationDoctorWatchLatestResponse {
  ok: boolean;
  exists: boolean;
  path: string;
  message: string;
  heartbeat?: {
    active?: boolean;
    age_seconds?: number | null;
    stale_after_seconds?: number;
    scheduler_consistent?: boolean;
    updated_at_text?: string;
    pid?: number;
    message?: string;
  } & Record<string, unknown>;
  snapshot: {
    checked_at?: string;
    severity?: string;
    summary?: string;
    owner_active?: boolean;
    execution_status?: string;
    execution_phase?: string;
    scheduler_next_action?: string;
    due_task_count?: number;
    due_task_ids?: string[];
    stale_due_count?: number;
    stale_due_success_count?: number;
    blocked_due_count?: number;
    blocked_due_ids?: string[];
    automation_safe?: boolean;
    needs_human_annotation?: boolean;
    blocked_by?: Array<Record<string, unknown>>;
    action_required?: string[];
    annotation_targets?: Array<{
      title?: string;
      path?: string;
      query?: Record<string, string>;
      url?: string;
      acceptable_shapes?: string[];
      existing_shapes?: string[];
      all_shapes?: string[];
      missing_shapes?: string[];
      required_shapes?: string[];
      description?: string;
    }>;
    retry_condition?: string;
    screenshot_path?: string;
    screenshot_error?: string;
  };
}

export interface FanxiuDataAnnotationDoctorWatchEnsureResponse {
  ok: boolean;
  started: boolean;
  pid?: number | null;
  reason: string;
  heartbeat?: Record<string, unknown>;
  previous_heartbeat?: Record<string, unknown>;
  latest?: Record<string, unknown>;
  output_path?: string;
  stdout_path?: string;
  stderr_path?: string;
  command?: string[];
}

export interface FanxiuKernelSchedulerStatus {
  ok: boolean;
  behavior_tree_enabled?: boolean;
  running: boolean;
  guard_group_enabled?: boolean;
  guard_group_running?: boolean;
  guard_enabled?: boolean;
  guard_running?: boolean;
  guard_entry_id?: string;
  guard_interval_seconds?: number;
  guard_items?: Record<string, FanxiuKernelSchedulerGuardItem>;
  last_guard_event?: Record<string, unknown>;
  status: 'idle' | 'running' | 'stopping' | 'stopped' | 'success' | 'error' | string;
  entry_id: string;
  task_type?: string;
  current_task?: string;
  phase?: string;
  current_scene?: number | null;
  message: string;
  current_index: number;
  total: number;
  current_code: string;
  current_task_id?: string;
  interruptible?: boolean;
  kernel?: Record<string, unknown>;
  kernel_restart?: Record<string, unknown>;
  started_at: number;
  updated_at: number;
  finished_at: number;
  error: string;
  logs: FanxiuKernelSchedulerLogData[];
  cell_logs?: FanxiuKernelSchedulerCellLog[];
}

export interface FanxiuInfoWindowSettings {
  enabled: boolean;
  auto_refresh: boolean;
  show_scene_id: boolean;
  show_scene_score: boolean;
  show_scene_identity_shapes: boolean;
  show_all_shapes: boolean;
  show_magic_crystal: boolean;
  show_xutian_currency: boolean;
}

export interface FanxiuInfoWindowControlStatus {
  ok: boolean;
  settings: FanxiuInfoWindowSettings;
  renderer: {
    running?: boolean;
    available?: boolean;
    visible?: boolean;
    pid?: number;
    updated_at?: number;
  };
  scene: {
    scene_id?: number | null;
    score?: number;
    asset_directory?: string;
    boxes?: Array<Record<string, number>>;
    all_shape_boxes?: Array<Record<string, number>>;
    observed_at?: number;
  };
}

export interface FanxiuKernelSchedulerDeviceRestartResponse {
  ok: boolean;
  recovered: boolean;
  status: string;
  message: string;
  device: Record<string, unknown>;
  scheduler: FanxiuKernelSchedulerStatus;
}

export interface FanxiuKernelSchedulerTaskItem {
  id: string;
  task_type: string;
  label: string;
  supported?: boolean;
  template_id?: string;
  template_label?: string;
  template_source?: string;
  trigger_description: string;
  source: string;
  legacy_name: string;
  interruptible: boolean;
  dispatch_level: number;
  dispatch_order: number;
  next_time?: string | null;
  original_next_time?: string | null;
  schedule_bias_minutes?: number;
  last_run_at?: string | null;
  last_result?: string;
  last_message?: string;
  error_retry_delay_seconds: number;
  payload: Record<string, unknown>;
  checkpoint?: Record<string, unknown> | null;
}

export interface FanxiuKernelSchedulerTasksResponse {
  ok: boolean;
  tasks: FanxiuKernelSchedulerTaskItem[];
  job_group_enabled?: boolean;
  path: string;
}

export interface FanxiuGameStateInspectionStatus {
  ok: boolean;
  name: string;
  description: string;
  enabled: boolean;
  status: 'running' | 'paused' | 'starting' | 'error' | string;
  interval_seconds: number;
  probe_count: number;
  probes: Array<{ id: string; label: string; source: string }>;
  sources: string[];
  service_pid?: number | null;
  last_checked_at?: string | null;
  next_check_at?: string | null;
  last_result: string;
  last_message: string;
  last_duration_ms?: number | null;
}

export interface FanxiuKernelSchedulerTimeSequenceItem {
  task_id: string;
  task_label: string;
  original_next_time?: string | null;
  effective_next_time?: string | null;
  bias_minutes: number;
}

export interface FanxiuKernelSchedulerTimeSequenceGroup {
  key: string;
  original_time: string;
  task_ids: string[];
  items: FanxiuKernelSchedulerTimeSequenceItem[];
}

export interface FanxiuKernelSchedulerTimeSequenceResponse {
  ok: boolean;
  groups: FanxiuKernelSchedulerTimeSequenceGroup[];
}

export interface FanxiuKernelSchedulerPlanItem {
  id: string;
  task_type: string;
  label: string;
  supported?: boolean;
  template_id?: string;
  template_label?: string;
  template_source?: string;
  trigger_description?: string;
  due: boolean;
  runnable: boolean;
  reason: string;
  next_time?: string | null;
  fact: Record<string, unknown>;
}

export interface FanxiuKernelSchedulerPlanResponse {
  ok: boolean;
  next_action: string;
  message: string;
  job_group_enabled?: boolean;
  blocking_overlays?: Array<Record<string, unknown>>;
  context: Record<string, unknown>;
  facts_summary: Record<string, unknown>;
  due_tasks: FanxiuKernelSchedulerPlanItem[];
  tasks: FanxiuKernelSchedulerPlanItem[];
  path: string;
}

export const getFanxiuKernelSchedulerStatus = (
  entryId = '',
  options: {
    includeCellLogs?: boolean;
    includeLogs?: boolean;
  } = {},
) => {
  const params: Record<string, string | boolean> = {};
  if (entryId) params.entry_id = entryId;
  if (options.includeCellLogs !== undefined) params.include_cell_logs = options.includeCellLogs;
  if (options.includeLogs !== undefined) params.include_logs = options.includeLogs;
  return api
    .get<FanxiuKernelSchedulerStatus>('/fanxiu/kernel-scheduler/status', { params })
    .then(res => res.data);
};

export const getFanxiuInfoWindowStatus = (entryId = '') => {
  return api
    .get<FanxiuInfoWindowControlStatus>('/fanxiu/kernel-scheduler/info-window', {
      params: entryId ? { entry_id: entryId } : {},
    })
    .then(res => res.data);
};

export const setFanxiuInfoWindowSettings = (
  entryId: string,
  settings: FanxiuInfoWindowSettings,
) => {
  return api
    .post<FanxiuInfoWindowControlStatus>(
      '/fanxiu/kernel-scheduler/info-window/settings',
      { entry_id: entryId, ...settings },
      { timeout: 10000 },
    )
    .then(res => res.data);
};

const FANXIU_DATA_ANNOTATION_CONTROL_TIMEOUT = 60000;

export const submitFanxiuDataAnnotationTaskCell = (
  entryId: string,
  taskType: string,
  payload: Record<string, unknown> = {},
  options: { timeoutSeconds?: number } = {},
) => {
  return api
    .post<FanxiuKernelSchedulerStatus>(
      '/fanxiu/kernel-scheduler/cells/task',
      { entry_id: entryId, task_type: taskType, payload, timeout_seconds: options.timeoutSeconds ?? null },
      { timeout: Math.max(FANXIU_DATA_ANNOTATION_CONTROL_TIMEOUT, ((options.timeoutSeconds ?? 30) * 1000) + 5000) },
    )
    .then(res => res.data);
};

export const submitFanxiuDataAnnotationCodeCell = (
  entryId: string,
  code: string,
  options: { timeoutSeconds?: number; maxOutputChars?: number } = {},
) => {
  const timeoutSeconds = options.timeoutSeconds ?? 120;
  return api
    .post<FanxiuKernelSchedulerStatus>(
      '/fanxiu/kernel-scheduler/cells/code',
      {
        entry_id: entryId,
        code,
        timeout_seconds: timeoutSeconds,
        max_output_chars: options.maxOutputChars ?? 4000,
      },
      { timeout: Math.max(FANXIU_DATA_ANNOTATION_CONTROL_TIMEOUT, (timeoutSeconds * 1000) + 5000) },
    )
    .then(res => res.data);
};

export const stopFanxiuKernelSchedulerCurrentTask = (entryId?: string) => {
  return api
    .post<FanxiuKernelSchedulerStatus>('/fanxiu/kernel-scheduler/task/stop', { entry_id: entryId || null }, { timeout: FANXIU_DATA_ANNOTATION_CONTROL_TIMEOUT })
    .then(res => res.data);
};

export const setFanxiuKernelSchedulerBehaviorTree = (entryId: string, enabled: boolean) => {
  return api
    .post<FanxiuKernelSchedulerStatus>(
      '/fanxiu/kernel-scheduler/behavior-tree/set',
      { entry_id: entryId, enabled },
      { timeout: FANXIU_DATA_ANNOTATION_CONTROL_TIMEOUT },
    )
    .then(res => res.data);
};

export const restartFanxiuKernelSchedulerKernel = (entryId: string, timeoutSeconds = 5) => {
  return api
    .post<FanxiuKernelSchedulerStatus>(
      '/fanxiu/kernel-scheduler/kernel/restart',
      { entry_id: entryId, timeout_seconds: timeoutSeconds },
      { timeout: Math.max(FANXIU_DATA_ANNOTATION_CONTROL_TIMEOUT, (timeoutSeconds * 1000) + 10000) },
    )
    .then(res => res.data);
};

export const restartFanxiuKernelSchedulerDevice = (entryId: string) => {
  return api
    .post<FanxiuKernelSchedulerDeviceRestartResponse>(
      '/fanxiu/kernel-scheduler/device/restart',
      { entry_id: entryId },
      { timeout: 240000 },
    )
    .then(res => res.data);
};

export const setFanxiuKernelSchedulerGuard = (entryId: string, enabled: boolean, intervalSeconds = 2, guardId = 'device_health') => {
  return api
    .post<FanxiuKernelSchedulerStatus>(
      '/fanxiu/kernel-scheduler/guard/set',
      { entry_id: entryId, guard_id: guardId, enabled, interval_seconds: intervalSeconds },
      { timeout: FANXIU_DATA_ANNOTATION_CONTROL_TIMEOUT },
    )
    .then(res => res.data);
};

export const setFanxiuKernelSchedulerGuardGroup = (entryId: string, enabled: boolean) => {
  return api
    .post<FanxiuKernelSchedulerStatus>(
      '/fanxiu/kernel-scheduler/guard/group/set',
      { entry_id: entryId, enabled },
      { timeout: FANXIU_DATA_ANNOTATION_CONTROL_TIMEOUT },
    )
    .then(res => res.data);
};

export const getFanxiuKernelSchedulerLogs = (limit = 80, scope = '', itemId = '') => {
  return api
    .get<FanxiuKernelSchedulerLogResponse>('/fanxiu/kernel-scheduler/logs', { params: { limit, scope, item_id: itemId } })
    .then(res => res.data);
};

export const getFanxiuKernelSchedulerCellLogs = (limit = 20, logLimit = 1000) => {
  return api
    .get<FanxiuKernelSchedulerCellLogResponse>('/fanxiu/kernel-scheduler/cell-logs', { params: { limit, log_limit: logLimit } })
    .then(res => res.data);
};

export const getFanxiuDataAnnotationWorldFacts = () => {
  return api
    .get<FanxiuDataAnnotationWorldFactsResponse>('/fanxiu/kernel-scheduler/world-facts')
    .then(res => res.data);
};

export const getFanxiuDataAnnotationDoctorWatchLatest = () => {
  return api
    .get<FanxiuDataAnnotationDoctorWatchLatestResponse>('/fanxiu/kernel-scheduler/doctor-watch/latest')
    .then(res => res.data);
};

export const ensureFanxiuDataAnnotationDoctorWatch = () => {
  return api
    .post<FanxiuDataAnnotationDoctorWatchEnsureResponse>('/fanxiu/kernel-scheduler/doctor-watch/ensure')
    .then(res => res.data);
};

export const clearFanxiuKernelSchedulerLogs = () => {
  return api.delete<FanxiuKernelSchedulerLogResponse>('/fanxiu/kernel-scheduler/logs').then(res => res.data);
};

export const getFanxiuKernelSchedulerTasks = () => {
  return api.get<FanxiuKernelSchedulerTasksResponse>('/fanxiu/kernel-scheduler/tasks').then(res => res.data);
};

export const getFanxiuGameStateInspectionStatus = () => {
  return api
    .get<FanxiuGameStateInspectionStatus>('/fanxiu/kernel-scheduler/state-inspection')
    .then(res => res.data);
};

export const getFanxiuKernelSchedulerPlan = () => {
  return api.get<FanxiuKernelSchedulerPlanResponse>('/fanxiu/kernel-scheduler/plan').then(res => res.data);
};

export const getFanxiuKernelSchedulerTimeSequence = () => {
  return api
    .get<FanxiuKernelSchedulerTimeSequenceResponse>('/fanxiu/kernel-scheduler/time-sequence')
    .then(res => res.data);
};

export const saveFanxiuKernelSchedulerTimeSequence = (
  groups: Array<{ key: string; task_ids: string[] }>,
) => {
  return api
    .put<FanxiuKernelSchedulerTimeSequenceResponse>(
      '/fanxiu/kernel-scheduler/time-sequence',
      { groups },
    )
    .then(res => res.data);
};

export const saveFanxiuKernelSchedulerTasks = (
  tasks: Array<Pick<FanxiuKernelSchedulerTaskItem, 'id'> & Partial<Pick<
    FanxiuKernelSchedulerTaskItem,
    'dispatch_level' | 'dispatch_order' | 'trigger_description' | 'error_retry_delay_seconds'
  >>>,
) => {
  return api.put<FanxiuKernelSchedulerTasksResponse>('/fanxiu/kernel-scheduler/tasks', tasks).then(res => res.data);
};

export const setFanxiuKernelSchedulerSettings = (jobGroupEnabled: boolean, entryId = '') => {
  return api
    .put<FanxiuKernelSchedulerTasksResponse>('/fanxiu/kernel-scheduler/settings', { job_group_enabled: jobGroupEnabled, entry_id: entryId })
    .then(res => res.data);
};

export type FanxiuSchedulerBusinessTimeMode = 'planned' | 'current';

export const runNowFanxiuKernelSchedulerTask = (
  entryId: string,
  taskId: string,
  payload: Record<string, unknown> = {},
  interruptSameGroup = true,
  businessTimeMode: FanxiuSchedulerBusinessTimeMode = 'planned',
) => {
  return api
    .post<FanxiuKernelSchedulerStatus>(
      '/fanxiu/kernel-scheduler/task/run-now',
      {
        entry_id: entryId,
        task_id: taskId,
        payload,
        interrupt_same_group: interruptSameGroup,
        business_time_mode: businessTimeMode,
      },
      { timeout: FANXIU_DATA_ANNOTATION_CONTROL_TIMEOUT },
    )
    .then(res => res.data);
};

export const runDueFanxiuKernelSchedulerTasks = (entryId: string) => {
  return api
    .post<FanxiuKernelSchedulerStatus>('/fanxiu/kernel-scheduler/run-due', { entry_id: entryId }, { timeout: FANXIU_DATA_ANNOTATION_CONTROL_TIMEOUT })
    .then(res => res.data);
};

export const triggerOnceFanxiuKernelSchedulerTask = (entryId: string, taskId: string) => {
  return api
    .post<{ ok: boolean; task_id: string; next_time: string }>(
      '/fanxiu/kernel-scheduler/task/trigger-once',
      { entry_id: entryId, task_id: taskId },
    )
    .then(res => res.data);
};

export const setFanxiuKernelSchedulerTaskNextTime = (
  entryId: string,
  taskId: string,
  nextTime: string | null,
) => {
  return api
    .put<{ ok: boolean; task_id: string; next_time: string | null }>(
      '/fanxiu/kernel-scheduler/task/next-time',
      { entry_id: entryId, task_id: taskId, next_time: nextTime },
    )
    .then(res => res.data);
};
