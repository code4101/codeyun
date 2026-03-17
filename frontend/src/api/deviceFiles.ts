import api, { getDeviceEntryPath } from '@/api';
import type { GalleryItemKind, GallerySortProgram } from '@/utils/imageGallery';

export type DeviceMediaSortMode = 'path' | 'modified-desc' | 'size-desc' | 'weight-desc';

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
  modified_at: number;
  width?: number | null;
  height?: number | null;
  aspect_ratio?: number | null;
  duration_ms?: number | null;
  kind?: GalleryItemKind;
  mime_type?: string | null;
  weight?: number | null;
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

export interface DeviceMediaListing {
  root: string | null;
  path: string;
  absolute_path: string;
  sort_mode: DeviceMediaSortMode;
  sort_program?: GallerySortProgram | null;
  snapshot_id: string | null;
  total_count: number;
  total_bytes: number;
  offset: number;
  limit: number;
  has_more: boolean;
  next_offset: number | null;
  layout: DeviceMediaLayout | null;
  media: DeviceImageRecord[];
}

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
}

export interface DeviceDirectoryListing {
  root: string | null;
  current_path: string;
  absolute_path: string;
  items: DeviceDirectoryItem[];
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
  const response = await api.post(getDeviceEntryPath(entryId, '/files/media/list'), payload);
  return {
    root: response.data.root ?? null,
    path: response.data.path ?? '',
    absolute_path: response.data.absolute_path ?? '',
    sort_mode: response.data.sort_mode ?? 'path',
    sort_program: response.data.sort_program ?? null,
    snapshot_id: response.data.snapshot_id ?? null,
    total_count: response.data.total_count ?? 0,
    total_bytes: response.data.total_bytes ?? 0,
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
  payload: DeviceFileSelector
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
