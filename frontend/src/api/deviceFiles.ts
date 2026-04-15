import api, { getDeviceEntryPath } from '@/api';
import type { GalleryItemKind, GallerySortProgram } from '@/utils/imageGallery';

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
  id: string;
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

const DEVICE_MEDIA_LIST_DEFAULT_TIMEOUT_MS = 10_000;
const DEVICE_MEDIA_LIST_DUPLICATE_CLUSTER_TIMEOUT_MS = 120_000;

const usesDuplicateClusterSort = (payload: DeviceMediaListRequest) =>
  Array.isArray(payload.sort_program?.rules)
  && payload.sort_program.rules.some((rule) => rule?.field === 'duplicate_cluster');

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
  recursive_total_bytes?: number | null;
  recursive_file_count?: number | null;
  latest_descendant_modified_at?: number | null;
  max_weight?: number | null;
  weighted_file_count?: number | null;
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
  const response = await api.post(getDeviceEntryPath(entryId, '/files/media/list'), payload, {
    timeout: usesDuplicateClusterSort(payload)
      ? DEVICE_MEDIA_LIST_DUPLICATE_CLUSTER_TIMEOUT_MS
      : DEVICE_MEDIA_LIST_DEFAULT_TIMEOUT_MS,
  });
  return {
    root: response.data.root ?? null,
    path: response.data.path ?? '',
    absolute_path: response.data.absolute_path ?? '',
    sort_mode: response.data.sort_mode ?? 'path',
    sort_program: response.data.sort_program ?? null,
    snapshot_id: response.data.snapshot_id ?? null,
    total_count: response.data.total_count ?? 0,
    total_bytes: response.data.total_bytes ?? 0,
    visual_hash_status: response.data.visual_hash_status ?? null,
    offset: response.data.offset ?? 0,
    limit: response.data.limit ?? 0,
    has_more: Boolean(response.data.has_more),
    next_offset: response.data.next_offset ?? null,
    layout: response.data.layout ?? null,
    media: response.data.media ?? [],
  };
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
  const response = await api.post(getDeviceEntryPath(entryId, '/files/delete'), payload);
  return response.data;
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
