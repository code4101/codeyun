import api, { getDeviceEntryPath } from '@/api';

export type RuntimeKind = 'service' | 'job';
export type RuntimeSource = 'command' | 'builtin';

export interface RuntimeGroup {
  id: string;
  kind: RuntimeKind;
  title: string;
  queue_key?: string | null;
  is_default?: boolean;
}

export interface RuntimeItem {
  id: string;
  key: string;
  kind: RuntimeKind;
  source: RuntimeSource;
  group_id: string;
  group_title: string;
  title: string;
  description?: string | null;
  command?: string;
  cwd?: string | null;
  runtime_kind?: RuntimeKind | null;
  schedule?: string | null;
  schedule_label?: string;
  next_run_at?: string | null;
  timeout?: number | null;
  timeout_policy?: 'none' | 'terminate' | string;
  timeout_seconds?: number | null;
  schedule_kind?: 'manual' | 'cron' | 'dynamic' | string;
  concurrency_scope?: 'unit' | 'group' | string;
  concurrency_key?: string | null;
  overlap_policy?: 'replace' | 'queue' | 'skip' | string;
  queue_key?: string | null;
  policy?: Record<string, any>;
  order?: number;
  enabled?: boolean;
  active: boolean;
  status: Record<string, any>;
  actions: string[];
  raw: Record<string, any>;
  actionLoading?: boolean;
  toggleLoading?: boolean;
}

export interface RuntimeQueueSnapshot {
  is_idle: boolean;
  running: Record<string, any> | null;
  pending: Record<string, any>[];
  recent: Record<string, any>[];
}

export interface RuntimeStatusResponse {
  device_id: string;
  device: Record<string, any>;
  groups: RuntimeGroup[];
  items: RuntimeItem[];
  queue: RuntimeQueueSnapshot | null;
  runner_running: boolean;
  next_wake_at: string | null;
  runner_error?: string | null;
}

export const fetchRuntimeStatus = async (entryId: string): Promise<RuntimeStatusResponse> => {
  const response = await api.get(getDeviceEntryPath(entryId, '/runtime/status'));
  return response.data;
};

export const triggerRuntimeJob = async (entryId: string, jobKey: string) => {
  const response = await api.post(getDeviceEntryPath(entryId, `/runtime/jobs/${encodeURIComponent(jobKey)}/trigger`));
  return response.data;
};

export const triggerRuntimeItem = async (entryId: string, source: RuntimeSource, itemKey: string) => {
  const response = await api.post(
    getDeviceEntryPath(entryId, `/runtime/items/${encodeURIComponent(source)}/${encodeURIComponent(itemKey)}/trigger`)
  );
  return response.data;
};

export const toggleRuntimeJob = async (entryId: string, jobKey: string, enabled: boolean) => {
  const response = await api.post(getDeviceEntryPath(entryId, `/runtime/jobs/${encodeURIComponent(jobKey)}/toggle`), { enabled });
  return response.data;
};

export const deleteRuntimeJob = async (entryId: string, jobKey: string) => {
  const response = await api.delete(getDeviceEntryPath(entryId, `/runtime/jobs/${encodeURIComponent(jobKey)}`));
  return response.data;
};

export const resetRuntimeJobSchedule = async (entryId: string, jobKey: string) => {
  const response = await api.post(getDeviceEntryPath(entryId, `/runtime/jobs/${encodeURIComponent(jobKey)}/reset-schedule`));
  return response.data;
};
