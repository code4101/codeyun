/** 游戏窗口控制、帧采集、截图与匹配的公共前端接口。 */
import api from '@/api';


export interface FanxiuGameWindow2StreamToken {
  token: string;
  expires_in_seconds: number;
}

export interface FanxiuGameWindow2ServiceStatus {
  key: string;
  title: string;
  running: boolean;
  state: string;
  state_label: string;
  target_title?: string;
  url?: string;
  host?: string;
  port?: number;
  process_count?: number;
  pids?: number[];
  last_error?: string;
}

export interface FanxiuGameWindow2ServiceStartResponse {
  status: string;
  service: FanxiuGameWindow2ServiceStatus;
}

export interface FanxiuGameWindow2ClickPayload {
  entry_id: string;
  x: number;
  y: number;
  title?: string;
  title_match?: 'contains' | 'exact';
  mode?: 'auto' | 'printwindow' | 'screen';
  area?: 'outer' | 'client';
  crop?: string;
  trim_border?: string;
  rotate?: '0' | '90' | '180' | '270' | 'ccw' | 'cw' | 'none';
  fixed_width?: number;
  fixed_height?: number;
  frame_width?: number;
  frame_height?: number;
  input_backend?: 'desktop' | 'adb';
}

export interface FanxiuGameWindow2DragPayload {
  entry_id: string;
  start_x: number;
  start_y: number;
  end_x: number;
  end_y: number;
  duration_ms?: number;
  title?: string;
  title_match?: 'contains' | 'exact';
  mode?: 'auto' | 'printwindow' | 'screen';
  area?: 'outer' | 'client';
  crop?: string;
  trim_border?: string;
  rotate?: '0' | '90' | '180' | '270' | 'ccw' | 'cw' | 'none';
  fixed_width?: number;
  fixed_height?: number;
  frame_width?: number;
  frame_height?: number;
  input_backend?: 'desktop' | 'adb';
}

export interface FanxiuGameWindow2KeyeventPayload {
  entry_id: string;
  key?: string;
  keys?: string[];
}

export interface FanxiuGameWindow2TextPayload {
  entry_id: string;
  text: string;
}

export interface FanxiuGameWindow2SaveFramePayload {
  entry_id: string;
  title?: string;
  title_match?: 'contains' | 'exact';
  mode?: 'auto' | 'printwindow' | 'screen';
  area?: 'outer' | 'client';
  crop?: string;
  trim_border?: string;
  rotate?: '0' | '90' | '180' | '270' | 'ccw' | 'cw' | 'none';
  fixed_width?: number;
  fixed_height?: number;
  quality?: number;
  current_frame_data_url?: string;
  overwrite_filename?: string;
}

export interface FanxiuGameWindow2SaveFrameResponse {
  ok: boolean;
  index: number;
  filename: string;
  path: string;
  directory: string;
  width: number;
  height: number;
}

export interface FanxiuGameWindow2BurstSaveResponse {
  ok: boolean;
  saved: boolean;
  skipped: boolean;
  reason?: string;
  phash?: string;
  index: number;
  filename: string;
  path?: string;
  directory: string;
  width: number;
  height: number;
}

export interface FanxiuGameWindow2BurstFrameItem {
  filename: string;
  stem: string;
  size: number;
  created_at: string;
  modified_at: string;
  width: number;
  height: number;
}

export interface FanxiuGameWindow2BurstListResponse {
  ok: boolean;
  directory: string;
  page: number;
  page_size: number;
  total: number;
  items: FanxiuGameWindow2BurstFrameItem[];
}

export interface FanxiuGameWindow2BurstClearResponse {
  ok: boolean;
  cleared: number;
  directory: string;
}

export interface FanxiuGameWindow2BurstImportItem {
  index: number;
  filename: string;
  source_filename: string;
  path: string;
  directory: string;
  width: number;
  height: number;
}

export interface FanxiuGameWindow2BurstImportResponse {
  ok: boolean;
  directory: string;
  source_directory: string;
  imported: FanxiuGameWindow2BurstImportItem[];
  imported_count: number;
}

export interface FanxiuGameWindow2MatchBox {
  name: string;
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface FanxiuGameWindow2MatchPayload extends FanxiuGameWindow2SaveFramePayload {
  filename: string;
  box: FanxiuGameWindow2MatchBox;
  scan?: boolean;
  scan_box?: FanxiuGameWindow2MatchBox;
  pixel_tolerance?: number;
  alpha_mask_data_url?: string;
  ocr_mask_mode?: 'inherit-envelope' | 'custom' | 'off' | 'raw-alpha';
  ocr_mask_data_url?: string;
  tolerance_min_data_url?: string;
  tolerance_max_data_url?: string;
  current_frame_data_url?: string;
  prefer_cached?: boolean;
  match_strategy?: 'auto' | 'anchor_pixel';
  match_search_radius?: number;
  ocr_enabled?: boolean;
  ocr_text?: string;
  ocr_match_mode?: 'contains' | 'exact' | 'wildcard' | 'regex';
  read_only_cache?: boolean;
  save_match_frame?: boolean;
  debug_match?: boolean;
}

export interface FanxiuGameWindow2MatchDebug {
  width: number;
  height: number;
  pixel_tolerance: number;
  effective_pixel_count: number;
  matched_pixel_count: number;
  unmatched_pixel_count: number;
  score: number;
  similarity: number;
  mask_coverage: number;
  reference_masked_data_url?: string;
  current_masked_data_url?: string;
  mismatch_heatmap_data_url?: string;
}

export interface FanxiuGameWindow2MatchResponse {
  ok: boolean;
  index: number;
  source_filename: string;
  match_filename: string;
  path: string;
  directory: string;
  similarity: number;
  score: number;
  fixed_similarity?: number;
  fixed_score?: number;
  fixed_pixel_similarity?: number;
  fixed_pixel_score?: number;
  fixed_exact_similarity?: number;
  fixed_exact_score?: number;
  fixed_exact_pixel_similarity?: number;
  fixed_exact_pixel_score?: number;
  fixed_search_radius?: number;
  pixel_tolerance?: number;
  match_strategy?: string;
  ocr_text?: string;
  ocr_target?: string;
  ocr_match_mode?: string;
  ocr_min_confidence?: number;
  box: FanxiuGameWindow2MatchBox;
  current_box: FanxiuGameWindow2MatchBox;
  fixed_box?: FanxiuGameWindow2MatchBox;
  matches?: Array<{
    box: FanxiuGameWindow2MatchBox;
    similarity: number;
    score: number;
    crop_similarity?: number;
    crop_score?: number;
    ocr_text?: string;
    ocr_confidence?: number;
  }>;
  source_width: number;
  source_height: number;
  width: number;
  height: number;
  match_debug?: FanxiuGameWindow2MatchDebug;
}

export interface FanxiuGameWindow2FrameStatus {
  ok: boolean;
  entry_id: string;
  sequence: number;
  captured_at: number;
  age_seconds: number | null;
  consecutive_failures: number;
  last_error: string;
  ready: boolean;
}

export interface FanxiuGameWindow2ScreenshotItem {
  filename: string;
  stem: string;
  pre_label_filename: string;
  pre_label_exists: boolean;
  label_filename: string;
  label_exists: boolean;
  size: number;
  modified_at: string;
  width: number;
  height: number;
}

export interface FanxiuGameWindow2ScreenshotListResponse {
  directory: string;
  items: FanxiuGameWindow2ScreenshotItem[];
}

export interface FanxiuGameWindow2PreLabelBox {
  name: string;
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface FanxiuGameWindow2PreLabelPayload {
  version: number;
  image: string;
  size: {
    width: number;
    height: number;
  };
  boxes: FanxiuGameWindow2PreLabelBox[];
}

export interface FanxiuGameWindow2PreLabelResponse {
  exists: boolean;
  filename: string;
  payload: FanxiuGameWindow2PreLabelPayload;
}

export interface FanxiuGameWindow2ScreenshotDeleteResponse {
  filename: string;
  deleted: string[];
}

export const createFanxiuGameWindow2StreamToken = (entryId: string) => {
  return api
    .post<FanxiuGameWindow2StreamToken>('/fanxiu/game-window2/stream-token', { entry_id: entryId })
    .then(res => res.data);
};

export const getFanxiuGameWindow2ServiceStatus = () => {
  return api.get<FanxiuGameWindow2ServiceStatus>('/fanxiu/game-window2/service-status').then(res => res.data);
};

export const getFanxiuGameWindow2FrameStatus = (entryId: string) => {
  return api
    .get<FanxiuGameWindow2FrameStatus>('/fanxiu/game-window2/frame-status', { params: { entry_id: entryId } })
    .then(res => res.data);
};

export const startFanxiuGameWindow2Service = () => {
  return api.post<FanxiuGameWindow2ServiceStartResponse>('/fanxiu/game-window2/service-start').then(res => res.data);
};

export const clickFanxiuGameWindow2 = (payload: FanxiuGameWindow2ClickPayload) => {
  return api.post<Record<string, unknown>>('/fanxiu/game-window2/input/click', payload, { timeout: 30000 }).then(res => res.data);
};

export const dragFanxiuGameWindow2 = (payload: FanxiuGameWindow2DragPayload) => {
  return api.post<Record<string, unknown>>('/fanxiu/game-window2/input/drag', payload, { timeout: 30000 }).then(res => res.data);
};

export const keyeventFanxiuGameWindow2 = (payload: FanxiuGameWindow2KeyeventPayload) => {
  return api.post<Record<string, unknown>>('/fanxiu/game-window2/input/keyevent', payload, { timeout: 30000 }).then(res => res.data);
};

export const textFanxiuGameWindow2 = (payload: FanxiuGameWindow2TextPayload) => {
  return api.post<Record<string, unknown>>('/fanxiu/game-window2/input/text', payload, { timeout: 30000 }).then(res => res.data);
};

export const screencapFanxiuGameWindow2 = (
  entryId: string,
  options?: {
    signal?: AbortSignal;
    timeout?: number;
    preferCached?: boolean;
    cachedOnly?: boolean;
    title?: string;
    titleMatch?: 'contains' | 'exact';
    mode?: 'auto' | 'printwindow' | 'screen';
    area?: 'outer' | 'client';
    crop?: string;
    trimBorder?: string;
    rotate?: string;
    fixedWidth?: number;
    fixedHeight?: number;
  },
) => {
  return api
    .post<Blob>('/fanxiu/game-window2/screencap', {
      entry_id: entryId,
      prefer_cached: options?.preferCached ?? false,
      cached_only: options?.cachedOnly ?? false,
      title: options?.title,
      title_match: options?.titleMatch,
      mode: options?.mode,
      area: options?.area,
      crop: options?.crop,
      trim_border: options?.trimBorder,
      rotate: options?.rotate,
      fixed_width: options?.fixedWidth,
      fixed_height: options?.fixedHeight,
    }, {
      responseType: 'blob',
      timeout: options?.timeout ?? 60000,
      signal: options?.signal,
    })
    .then(res => res.data);
};

export const saveFanxiuGameWindow2Frame = (payload: FanxiuGameWindow2SaveFramePayload) => {
  return api.post<FanxiuGameWindow2SaveFrameResponse>('/fanxiu/game-window2/save-frame', payload).then(res => res.data);
};

export const saveFanxiuGameWindow2BurstFrame = (payload: FanxiuGameWindow2SaveFramePayload) => {
  return api.post<FanxiuGameWindow2BurstSaveResponse>('/fanxiu/game-window2/burst/save', payload).then(res => res.data);
};

export const listFanxiuGameWindow2BurstFrames = (entryId: string, page = 1, pageSize = 24) => {
  return api
    .post<FanxiuGameWindow2BurstListResponse>('/fanxiu/game-window2/burst/list', {
      entry_id: entryId,
      page,
      page_size: pageSize,
    })
    .then(res => res.data);
};

export const getFanxiuGameWindow2BurstFrameImage = (entryId: string, filename: string) => {
  return api
    .get<Blob>('/fanxiu/game-window2/burst/image', {
      params: { entry_id: entryId, filename },
      responseType: 'blob',
    })
    .then(res => res.data);
};

export const clearFanxiuGameWindow2BurstFrames = (entryId: string) => {
  return api
    .post<FanxiuGameWindow2BurstClearResponse>('/fanxiu/game-window2/burst/clear', { entry_id: entryId })
    .then(res => res.data);
};

export const importFanxiuGameWindow2BurstFrames = (entryId: string, filenames: string[]) => {
  return api
    .post<FanxiuGameWindow2BurstImportResponse>('/fanxiu/game-window2/burst/import', { entry_id: entryId, filenames })
    .then(res => res.data);
};

export const matchFanxiuGameWindow2Screenshot = (
  payload: FanxiuGameWindow2MatchPayload,
  options: { signal?: AbortSignal; timeout?: number } = {},
) => {
  return api.post<FanxiuGameWindow2MatchResponse>('/fanxiu/game-window2/match', payload, {
    timeout: options.timeout ?? 60000,
    signal: options.signal,
  }).then(res => res.data);
};

export const getFanxiuGameWindow2MatchImage = (entryId: string, filename: string) => {
  return api
    .get<Blob>('/fanxiu/game-window2/match/image', {
      params: { entry_id: entryId, filename },
      responseType: 'blob',
    })
    .then(res => res.data);
};

export const listFanxiuGameWindow2Screenshots = (entryId: string) => {
  return api
    .post<FanxiuGameWindow2ScreenshotListResponse>('/fanxiu/game-window2/screenshot/list', { entry_id: entryId })
    .then(res => res.data);
};

export const deleteFanxiuGameWindow2Screenshot = (entryId: string, filename: string) => {
  return api
    .post<FanxiuGameWindow2ScreenshotDeleteResponse>('/fanxiu/game-window2/screenshot/delete', { entry_id: entryId, filename })
    .then(res => res.data);
};

export const getFanxiuGameWindow2Screenshot = (entryId: string, filename: string) => {
  return api
    .get<Blob>('/fanxiu/game-window2/screenshot/image', {
      params: { entry_id: entryId, filename },
      responseType: 'blob',
    })
    .then(res => res.data);
};

export const getFanxiuGameWindow2PreLabel = (entryId: string, filename: string) => {
  return api
    .post<FanxiuGameWindow2PreLabelResponse>('/fanxiu/game-window2/screenshot/pre-label', { entry_id: entryId, filename })
    .then(res => res.data);
};

export const saveFanxiuGameWindow2PreLabel = (
  entryId: string,
  filename: string,
  payload: FanxiuGameWindow2PreLabelPayload,
) => {
  return api
    .put<FanxiuGameWindow2PreLabelResponse>('/fanxiu/game-window2/screenshot/pre-label', { entry_id: entryId, filename, payload })
    .then(res => res.data);
};
