import api, { getDeviceEntryPath } from '@/api';
import type { GalleryItemKind, GallerySortProgram } from '@/utils/imageGallery';
import { monitorPolledTask, runLongTask, type LongTaskSnapshot } from '@/utils/longTask';

export type DeviceMediaSortMode = 'path' | 'modified-desc' | 'size-desc' | 'weight-desc';
export type DeviceDirectorySortField =
  | 'name'
  | 'modified_at'
  | 'recursive_total_bytes'
  | 'recursive_file_count'
  | 'latest_descendant_modified_at'
  | 'max_weight'
  | 'weighted_file_count';
export type DeviceDirectorySortDirection = 'asc' | 'desc';
export type DeviceDirectorySortNulls = 'first' | 'last';

export interface DeviceDirectorySortRule {
  field: DeviceDirectorySortField;
  direction: DeviceDirectorySortDirection;
  nulls: DeviceDirectorySortNulls;
}

export interface DeviceDirectorySortProgram {
  rules: DeviceDirectorySortRule[];
}

export interface DeviceFilesystemRoot {
  key: string;
  label: string;
  path: string;
  preferred: boolean;
  writable: boolean;
}

export interface DeviceImageRecord {
  id: number;
  name: string;
  path: string;
  absolute_path: string;
  relative_path: string;
  folder_path: string;
  size: number;
  created_at?: number | null;
  modified_at: number;
  width?: number | null;
  height?: number | null;
  aspect_ratio?: number | null;
  duration_ms?: number | null;
  kind?: GalleryItemKind;
  mime_type?: string | null;
  weight?: number | null;
  content_hash?: string | null;
  hash_algorithm?: string | null;
  visual_hash?: string | null;
  visual_hash_algorithm?: string | null;
  duplicate_cluster_order?: number | null;
  duplicate_cluster_distance?: number | null;
  duplicate_cluster_member_order?: number | null;
  duplicate_cluster_size?: number | null;
}

export interface DeviceFileSelector {
  root?: string;
  path?: string;
  absolute_path?: string;
}

export interface DeviceMediaLayout {
  mode: 'none' | 'masonry';
  column_count: number;
  column_width: number;
  gap: number;
  columns: string[][];
  column_heights: number[];
}

export interface DeviceMediaVisualHashStatus {
  requested: boolean;
  total_image_count: number;
  indexed_count: number;
  missing_count: number;
  computed_count: number;
  reused_content_hash_count: number;
  prewarm_scheduled_count: number;
  complete: boolean;
}

export interface DeviceMediaListRequest extends DeviceFileSelector {
  recursive?: boolean;
  scan_limit?: number;
  sort_mode?: DeviceMediaSortMode;
  sort_program?: GallerySortProgram | null;
  snapshot_id?: string;
  offset?: number;
  limit?: number;
  layout_mode?: 'none' | 'masonry';
  layout_columns?: number;
  layout_column_width?: number;
  layout_gap?: number;
  layout_column_heights?: number[];
}

export interface DeviceDirectoryListRequest extends DeviceFileSelector {
  sort_program?: DeviceDirectorySortProgram | null;
}

export interface DeviceMediaListing {
  root: string | null;
  path: string;
  absolute_path: string;
  sort_mode: DeviceMediaSortMode;
  sort_program?: GallerySortProgram | null;
  snapshot_id: string | null;
  total_count: number;
  total_bytes: number;
  visual_hash_status: DeviceMediaVisualHashStatus | null;
  offset: number;
  limit: number;
  has_more: boolean;
  next_offset: number | null;
  layout: DeviceMediaLayout | null;
  media: DeviceImageRecord[];
}

const DEVICE_MEDIA_LIST_IDLE_TIMEOUT_MS = 30_000;
const DEVICE_MEDIA_LIST_FAST_PATH_TIMEOUT_MS = 2_500;
const DEVICE_MEDIA_LIST_DEFAULT_TIMEOUT_MS = 10_000;
const DEVICE_MEDIA_LIST_DUPLICATE_CLUSTER_TIMEOUT_MS = 120_000;
const DEVICE_MEDIA_LIST_FAST_PATH_SCAN_LIMIT = 2_000;
const DEVICE_MEDIA_LIST_FAST_PATH_LIMIT = 100;

const usesDuplicateClusterSort = (payload: DeviceMediaListRequest) =>
  Array.isArray(payload.sort_program?.rules)
  && payload.sort_program.rules.some((rule) => rule?.field === 'duplicate_cluster');

const canUseFastMediaList = (payload: DeviceMediaListRequest) =>
  !usesDuplicateClusterSort(payload)
  && payload.recursive !== true
  && Number(payload.scan_limit ?? 0) <= DEVICE_MEDIA_LIST_FAST_PATH_SCAN_LIMIT
  && Number(payload.limit ?? 0) <= DEVICE_MEDIA_LIST_FAST_PATH_LIMIT
  && !payload.snapshot_id;

const isTimeoutError = (error: any) =>
  error?.code === 'ECONNABORTED'
  || String(error?.message || '').toLowerCase().includes('timeout');

export interface DeviceThumbnailOptions {
  max_edge?: number;
  quality?: number;
  cache_key?: string | number;
}

export interface DeviceDirectoryItem {
  name: string;
  path: string;
  is_dir: boolean;
  size: number | null;
  modified_at: number | null;
  direct_file_bytes?: number | null;
  direct_file_count?: number | null;
  recursive_total_bytes?: number | null;
  recursive_file_count?: number | null;
  latest_descendant_modified_at?: number | null;
  max_weight?: number | null;
  weighted_file_count?: number | null;
  disk_total_bytes?: number | null;
  disk_free_bytes?: number | null;
  disk_used_bytes?: number | null;
}

export interface DeviceDirectoryListing {
  root: string | null;
  current_path: string;
  absolute_path: string;
  items: DeviceDirectoryItem[];
}

export interface DeviceRevealResult {
  ok: boolean;
  supported: boolean;
  launched: boolean;
  method: string;
  detail: string;
  root: string | null;
  path: string;
  absolute_path: string;
  target_path: string;
  directory_path: string;
}

export interface DeviceTextFilePayload extends DeviceFileSelector {
  encoding?: string;
}

export interface DeviceTextFileResult {
  ok: boolean;
  root: string | null;
  path: string;
  absolute_path: string;
  encoding: string;
  size: number;
  modified_at: number;
  text: string;
}

export interface DeviceLabelmeRenameRequest extends DeviceFileSelector {
  base_root?: string;
  base_path?: string;
  base_absolute_path?: string;
  target_relative_path: string;
  overwrite?: boolean;
  encoding?: string;
}

export interface DeviceLabelmeRenameResult {
  ok: boolean;
  root: string | null;
  path: string;
  absolute_path: string;
  source_image_absolute_path: string;
  target_image_absolute_path: string;
  source_json_absolute_path: string;
  target_json_absolute_path: string;
  target_relative_path: string;
  target_name: string;
  json_moved: boolean;
  json_updated: boolean;
  overwritten: boolean;
}

export type DeviceOcrShapeType = 'polygon' | 'rectangle';

export interface DeviceOcrPreviewRequest extends DeviceFileSelector {
  shape_type?: DeviceOcrShapeType;
}

export interface DeviceOcrPreviewResponse {
  ok: boolean;
  root: string | null;
  path: string;
  absolute_path: string;
  engine: string;
  shape_type: DeviceOcrShapeType;
  shape_count: number;
  document: Record<string, unknown>;
}

export type DeviceDeleteTaskStatus = 'pending' | 'running' | 'completed' | 'partial_failed' | 'failed' | 'unknown';
export type DeviceDuplicateRule = 'size' | 'name' | 'extension' | 'modified_at' | 'sha256';
export type DeviceDuplicateSortMode = 'file_size' | 'group_total' | 'reclaimable';
export type DeviceDuplicateSource = 'auto' | 'everything' | 'filesystem';
export type DeviceDuplicateFilterAction = 'include' | 'exclude';
export type DeviceDuplicateFilterMatch = 'contains' | 'prefix' | 'suffix' | 'equals' | 'glob';

export interface DeviceDeleteTask {
  id: string;
  task_id: string;
  name: string;
  status: DeviceDeleteTaskStatus;
  queued_at: number | null;
  started_at: number | null;
  updated_at: number | null;
  finished_at: number | null;
  pid: number | null;
  pid_started_at: number | null;
  return_code: number | null;
  skipped_count: number;
  skipped_paths: Array<{ path: string; error: string }>;
  error_message: string | null;
  metadata: Record<string, unknown>;
  target_path: string;
  entry_name: string;
}

export interface DeviceDeleteTaskStartResult {
  ok: boolean;
  queued: boolean;
  task_id: string;
  pid: number | null;
  task: DeviceDeleteTask;
}

export interface DeviceDuplicateFile {
  name: string;
  path: string;
  absolute_path: string;
  size: number;
  modified_at: number | null;
}

export interface DeviceDuplicateGroup {
  id: string;
  key_label: string;
  rules: DeviceDuplicateRule[];
  file_count: number;
  file_size: number;
  group_total_bytes: number;
  reclaimable_bytes: number;
  files: DeviceDuplicateFile[];
}

export interface DeviceDuplicateFilterRule {
  enabled: boolean;
  action: DeviceDuplicateFilterAction;
  match: DeviceDuplicateFilterMatch;
  value: string;
}

export interface DeviceDuplicateListRequest extends DeviceFileSelector {
  recursive?: boolean;
  rules?: DeviceDuplicateRule[];
  filter_rules?: DeviceDuplicateFilterRule[];
  sort_mode?: DeviceDuplicateSortMode;
  source?: DeviceDuplicateSource;
  min_size?: number;
  scan_limit?: number;
  snapshot_id?: string;
  page?: number;
  page_size?: number;
}

export interface DeviceDuplicateListing {
  ok: boolean;
  root: string | null;
  path: string;
  absolute_path: string;
  snapshot_id: string;
  page: number;
  page_size: number;
  has_previous: boolean;
  has_next: boolean;
  total_groups: number;
  total_reclaimable_bytes: number;
  duplicate_file_count: number;
  scanned_file_count: number;
  candidate_file_count: number;
  hash_computed_count: number;
  source: string;
  source_detail: string;
  complete: boolean;
  groups: DeviceDuplicateGroup[];
}

export interface DeviceDuplicateAnalysis extends DeviceDuplicateListing {
  task_id: string;
  status: 'queued' | 'running' | 'completed' | 'failed' | string;
  stage: string;
  message: string;
  running: boolean;
  error: string | null;
  scan_limit: number;
  hit_scan_limit: boolean;
  created_at: number | null;
  started_at: number | null;
  updated_at: number | null;
  finished_at: number | null;
  elapsed_ms: number;
}

const normalizeNullableNumber = (value: unknown): number | null => {
  if (value == null || value === '') {
    return null;
  }
  const numberValue = Number(value);
  return Number.isFinite(numberValue) ? numberValue : null;
};

const normalizeDeleteTask = (raw: any): DeviceDeleteTask => ({
  id: raw?.id ?? raw?.task_id ?? '',
  task_id: raw?.task_id ?? raw?.id ?? '',
  name: raw?.name ?? '',
  status: raw?.status ?? 'unknown',
  queued_at: raw?.queued_at ?? null,
  started_at: raw?.started_at ?? null,
  updated_at: raw?.updated_at ?? null,
  finished_at: raw?.finished_at ?? null,
  pid: normalizeNullableNumber(raw?.pid),
  pid_started_at: normalizeNullableNumber(raw?.pid_started_at),
  return_code: normalizeNullableNumber(raw?.return_code),
  skipped_count: Number(raw?.skipped_count ?? 0),
  skipped_paths: Array.isArray(raw?.skipped_paths) ? raw.skipped_paths : [],
  error_message: raw?.error_message ?? null,
  metadata: raw?.metadata ?? {},
  target_path: raw?.target_path ?? '',
  entry_name: raw?.entry_name ?? '',
});

const normalizeDuplicateListing = (raw: any): DeviceDuplicateListing => ({
  ok: Boolean(raw?.ok),
  root: raw?.root ?? null,
  path: raw?.path ?? '',
  absolute_path: raw?.absolute_path ?? '',
  snapshot_id: raw?.snapshot_id ?? '',
  page: Number(raw?.page ?? 1),
  page_size: Number(raw?.page_size ?? 10),
  has_previous: Boolean(raw?.has_previous),
  has_next: Boolean(raw?.has_next),
  total_groups: Number(raw?.total_groups ?? 0),
  total_reclaimable_bytes: Number(raw?.total_reclaimable_bytes ?? 0),
  duplicate_file_count: Number(raw?.duplicate_file_count ?? 0),
  scanned_file_count: Number(raw?.scanned_file_count ?? 0),
  candidate_file_count: Number(raw?.candidate_file_count ?? 0),
  hash_computed_count: Number(raw?.hash_computed_count ?? 0),
  source: raw?.source ?? '',
  source_detail: raw?.source_detail ?? '',
  complete: Boolean(raw?.complete),
  groups: Array.isArray(raw?.groups) ? raw.groups : [],
});

const normalizeDuplicateAnalysis = (raw: any): DeviceDuplicateAnalysis => ({
  ...normalizeDuplicateListing(raw),
  task_id: raw?.task_id ?? '',
  status: raw?.status ?? '',
  stage: raw?.stage ?? '',
  message: raw?.message ?? '',
  running: Boolean(raw?.running),
  error: raw?.error ?? null,
  scan_limit: Number(raw?.scan_limit ?? 0),
  hit_scan_limit: Boolean(raw?.hit_scan_limit),
  created_at: normalizeNullableNumber(raw?.created_at),
  started_at: normalizeNullableNumber(raw?.started_at),
  updated_at: normalizeNullableNumber(raw?.updated_at),
  finished_at: normalizeNullableNumber(raw?.finished_at),
  elapsed_ms: Number(raw?.elapsed_ms ?? 0),
});

export const fetchDeviceRoots = async (entryId: string): Promise<DeviceFilesystemRoot[]> => {
  const response = await api.get(getDeviceEntryPath(entryId, '/files/roots'));
  return response.data.roots ?? [];
};

export const fetchDeviceImages = async (
  entryId: string,
  payload: DeviceFileSelector
): Promise<DeviceImageRecord[]> => {
  const response = await api.post(getDeviceEntryPath(entryId, '/files/images/list'), payload);
  return response.data.images ?? [];
};

export const fetchDeviceMedia = async (
  entryId: string,
  payload: DeviceMediaListRequest
): Promise<DeviceMediaListing> => {
  if (canUseFastMediaList(payload)) {
    try {
      return await fetchDeviceMediaSync(entryId, payload, {
        timeoutMs: DEVICE_MEDIA_LIST_FAST_PATH_TIMEOUT_MS,
      });
    } catch (error: any) {
      if (!isTimeoutError(error)) {
        throw error;
      }
    }
  }

  return runLongTask<DeviceMediaListing>({
    start: () => startDeviceMediaListTask(entryId, payload),
    poll: (taskId) => fetchDeviceMediaListTask(entryId, taskId),
    idleTimeoutMs: usesDuplicateClusterSort(payload)
      ? DEVICE_MEDIA_LIST_DUPLICATE_CLUSTER_TIMEOUT_MS
      : DEVICE_MEDIA_LIST_IDLE_TIMEOUT_MS,
  });
};

const normalizeDeviceMediaListing = (raw: any): DeviceMediaListing => ({
  root: raw?.root ?? null,
  path: raw?.path ?? '',
  absolute_path: raw?.absolute_path ?? '',
  sort_mode: raw?.sort_mode ?? 'path',
  sort_program: raw?.sort_program ?? null,
  snapshot_id: raw?.snapshot_id ?? null,
  total_count: raw?.total_count ?? 0,
  total_bytes: raw?.total_bytes ?? 0,
  visual_hash_status: raw?.visual_hash_status ?? null,
  offset: raw?.offset ?? 0,
  limit: raw?.limit ?? 0,
  has_more: Boolean(raw?.has_more),
  next_offset: raw?.next_offset ?? null,
  layout: raw?.layout ?? null,
  media: raw?.media ?? [],
});

export const fetchDeviceMediaSync = async (
  entryId: string,
  payload: DeviceMediaListRequest,
  options?: { timeoutMs?: number }
): Promise<DeviceMediaListing> => {
  const response = await api.post(getDeviceEntryPath(entryId, '/files/media/list'), payload, {
    timeout: options?.timeoutMs ?? (usesDuplicateClusterSort(payload)
      ? DEVICE_MEDIA_LIST_DUPLICATE_CLUSTER_TIMEOUT_MS
      : DEVICE_MEDIA_LIST_DEFAULT_TIMEOUT_MS),
  });
  return normalizeDeviceMediaListing(response.data);
};

export const startDeviceMediaListTask = async (
  entryId: string,
  payload: DeviceMediaListRequest
): Promise<LongTaskSnapshot<DeviceMediaListing>> => {
  const response = await api.post(getDeviceEntryPath(entryId, '/files/media/list/tasks'), payload);
  return response.data;
};

export const fetchDeviceMediaListTask = async (
  entryId: string,
  taskId: string
): Promise<LongTaskSnapshot<DeviceMediaListing>> => {
  const response = await api.get(getDeviceEntryPath(entryId, `/files/media/list/tasks/${encodeURIComponent(taskId)}`));
  const snapshot = response.data as LongTaskSnapshot<any>;
  if (snapshot.status === 'completed' && snapshot.result) {
    return {
      ...snapshot,
      result: normalizeDeviceMediaListing(snapshot.result),
    };
  }
  return snapshot;
};

export const fetchDeviceDirectoryItems = async (
  entryId: string,
  payload: DeviceDirectoryListRequest
): Promise<DeviceDirectoryListing> => {
  const response = await api.post(getDeviceEntryPath(entryId, '/files/list_dir'), payload);
  return {
    root: response.data.root ?? null,
    current_path: response.data.current_path ?? '',
    absolute_path: response.data.absolute_path ?? '',
    items: response.data.items ?? [],
  };
};

export const fetchDeviceDuplicateFiles = async (
  entryId: string,
  payload: DeviceDuplicateListRequest
): Promise<DeviceDuplicateListing> => {
  const initial = await startDeviceDuplicateAnalysis(entryId, payload);
  return monitorPolledTask<DeviceDuplicateAnalysis>({
    initial,
    poll: (task) => fetchDeviceDuplicateAnalysis(entryId, task.task_id, {
      page: task.page,
      page_size: task.page_size,
    }),
    isRunning: (task) => task.running,
    getUpdatedAt: (task) => task.updated_at,
    getError: (task) => task.status === 'failed' ? (task.error || task.message || '重复文件分析失败') : '',
    idleTimeoutMs: payload.rules?.includes('sha256') ? 120_000 : 30_000,
  });
};

export const startDeviceDuplicateAnalysis = async (
  entryId: string,
  payload: DeviceDuplicateListRequest
): Promise<DeviceDuplicateAnalysis> => {
  const response = await api.post(getDeviceEntryPath(entryId, '/files/duplicates/tasks'), payload, {
    timeout: 30000,
  });
  return normalizeDuplicateAnalysis(response.data);
};

export const fetchDeviceDuplicateAnalysis = async (
  entryId: string,
  taskId: string,
  params: { page?: number; page_size?: number } = {}
): Promise<DeviceDuplicateAnalysis> => {
  const response = await api.get(getDeviceEntryPath(entryId, `/files/duplicates/tasks/${encodeURIComponent(taskId)}`), {
    params,
    timeout: 30000,
  });
  return normalizeDuplicateAnalysis(response.data);
};

export const fetchDeviceFileBlob = async (
  entryId: string,
  payload: DeviceFileSelector
): Promise<Blob> => {
  const response = await api.get(getDeviceEntryPath(entryId, '/files/content'), {
    params: payload,
    responseType: 'blob',
  });
  return response.data;
};

export const fetchDeviceFileText = async (
  entryId: string,
  payload: DeviceTextFilePayload
): Promise<DeviceTextFileResult> => {
  const response = await api.get(getDeviceEntryPath(entryId, '/files/text'), {
    params: payload,
  });
  return {
    ok: Boolean(response.data.ok),
    root: response.data.root ?? null,
    path: response.data.path ?? '',
    absolute_path: response.data.absolute_path ?? '',
    encoding: response.data.encoding ?? 'utf-8',
    size: Number(response.data.size ?? 0),
    modified_at: Number(response.data.modified_at ?? 0),
    text: typeof response.data.text === 'string' ? response.data.text : '',
  };
};

export const saveDeviceFileText = async (
  entryId: string,
  payload: DeviceTextFilePayload & { text: string }
) => {
  const response = await api.post(getDeviceEntryPath(entryId, '/files/text'), {
    ...payload,
    encoding: payload.encoding ?? 'utf-8',
  });
  return response.data;
};

export const renameDeviceLabelmeAnnotation = async (
  entryId: string,
  payload: DeviceLabelmeRenameRequest
): Promise<DeviceLabelmeRenameResult> => {
  const response = await api.post(getDeviceEntryPath(entryId, '/files/labelme/rename'), {
    ...payload,
    encoding: payload.encoding ?? 'utf-8',
    overwrite: Boolean(payload.overwrite),
  });
  return {
    ok: Boolean(response.data.ok),
    root: response.data.root ?? null,
    path: response.data.path ?? '',
    absolute_path: response.data.absolute_path ?? '',
    source_image_absolute_path: response.data.source_image_absolute_path ?? '',
    target_image_absolute_path: response.data.target_image_absolute_path ?? '',
    source_json_absolute_path: response.data.source_json_absolute_path ?? '',
    target_json_absolute_path: response.data.target_json_absolute_path ?? '',
    target_relative_path: response.data.target_relative_path ?? '',
    target_name: response.data.target_name ?? '',
    json_moved: Boolean(response.data.json_moved),
    json_updated: Boolean(response.data.json_updated),
    overwritten: Boolean(response.data.overwritten),
  };
};

export const fetchDeviceFileOcrPreview = async (
  entryId: string,
  payload: DeviceOcrPreviewRequest
): Promise<DeviceOcrPreviewResponse> => {
  const response = await api.post(getDeviceEntryPath(entryId, '/files/ocr'), {
    ...payload,
    shape_type: payload.shape_type ?? 'polygon',
  }, {
    timeout: 120000,
  });
  return {
    ok: Boolean(response.data.ok),
    root: response.data.root ?? null,
    path: response.data.path ?? '',
    absolute_path: response.data.absolute_path ?? '',
    engine: response.data.engine ?? 'paddleocr',
    shape_type: response.data.shape_type ?? 'polygon',
    shape_count: Number(response.data.shape_count ?? 0),
    document: response.data.document ?? {},
  };
};

export const fetchDeviceFileStreamUrl = async (
  entryId: string,
  payload: DeviceFileSelector
): Promise<string> => {
  const response = await api.post(getDeviceEntryPath(entryId, '/files/stream-url'), payload);
  return response.data.url ?? '';
};

export const fetchDeviceImageBlob = fetchDeviceFileBlob;
export const fetchDeviceMediaBlob = fetchDeviceFileBlob;

export const fetchDeviceThumbnailBlob = async (
  entryId: string,
  payload: DeviceFileSelector,
  options: DeviceThumbnailOptions = {}
): Promise<Blob> => {
  const response = await api.get(getDeviceEntryPath(entryId, '/files/thumbnail'), {
    params: {
      ...payload,
      max_edge: options.max_edge ?? 360,
      quality: options.quality ?? 82,
      cache_key: options.cache_key,
    },
    responseType: 'blob',
  });
  return response.data;
};

export const setDeviceFileCover = async (
  entryId: string,
  payload: DeviceFileSelector,
  cover: Blob,
  filename = 'cover.jpg'
) => {
  const formData = new FormData();
  if (payload.root) {
    formData.append('root', payload.root);
  }
  if (payload.path) {
    formData.append('path', payload.path);
  }
  if (payload.absolute_path) {
    formData.append('absolute_path', payload.absolute_path);
  }
  formData.append('cover', cover, filename);

  const response = await api.post(getDeviceEntryPath(entryId, '/files/cover'), formData, {
    headers: {
      'Content-Type': 'multipart/form-data',
    },
  });
  return response.data;
};

export const setDeviceFileWeight = async (
  entryId: string,
  payload: DeviceFileSelector,
  weight: number
) => {
  const response = await api.post(getDeviceEntryPath(entryId, '/files/weight'), {
    ...payload,
    weight,
  });
  return response.data;
};

export const deleteDeviceEntry = async (
  entryId: string,
  payload: DeviceFileSelector & { recursive?: boolean }
) => {
  const response = await api.post(getDeviceEntryPath(entryId, '/files/delete'), payload, {
    timeout: 0,
  });
  return response.data;
};

export const startDeviceEntryDelete = async (
  entryId: string,
  payload: DeviceFileSelector & { recursive?: boolean }
): Promise<DeviceDeleteTaskStartResult> => {
  const response = await api.post(getDeviceEntryPath(entryId, '/files/delete/async'), payload);
  return {
    ok: Boolean(response.data.ok),
    queued: Boolean(response.data.queued),
    task_id: response.data.task_id ?? response.data.task?.task_id ?? '',
    pid: normalizeNullableNumber(response.data.pid ?? response.data.task?.pid),
    task: normalizeDeleteTask(response.data.task ?? {}),
  };
};

export const fetchDeviceEntryDeleteTasks = async (
  entryId: string
): Promise<DeviceDeleteTask[]> => {
  const response = await api.get(getDeviceEntryPath(entryId, '/files/delete-tasks'));
  return (response.data.tasks ?? []).map(normalizeDeleteTask);
};

export const fetchDeviceEntryDeleteTask = async (
  entryId: string,
  taskId: string
): Promise<DeviceDeleteTask> => {
  const response = await api.get(getDeviceEntryPath(entryId, `/files/delete-tasks/${encodeURIComponent(taskId)}`));
  return normalizeDeleteTask(response.data);
};

export const revealDeviceEntryInFolder = async (
  entryId: string,
  payload: DeviceFileSelector
): Promise<DeviceRevealResult> => {
  const response = await api.post(getDeviceEntryPath(entryId, '/files/reveal'), payload);
  return {
    ok: Boolean(response.data.ok),
    supported: Boolean(response.data.supported),
    launched: Boolean(response.data.launched),
    method: response.data.method ?? '',
    detail: response.data.detail ?? '',
    root: response.data.root ?? null,
    path: response.data.path ?? '',
    absolute_path: response.data.absolute_path ?? '',
    target_path: response.data.target_path ?? '',
    directory_path: response.data.directory_path ?? '',
  };
};
