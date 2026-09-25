/** 资产标注、识别诊断、OCR 和图像处理的公共前端接口。 */
import api from '@/api';
import type { FanxiuGameWindow2MatchBox } from './gameWindow';


export interface FanxiuDataAnnotationAssetTreeResponse {
  ok: boolean;
  entry_id: string;
  exists: boolean;
  tree: unknown[];
  revision: string;
  updated_at: number;
}

export interface FanxiuDataAnnotationRecognitionOpsEdge {
  source_id: number;
  target_id: number;
  score?: number | string | null;
  threshold?: number | string | null;
  matched?: boolean;
}

export interface FanxiuDataAnnotationRecognitionOpsIssue {
  id: string;
  category: string;
  severity: 'error' | 'warning' | 'info' | string;
  label: string;
  node_ids: number[];
  edges: FanxiuDataAnnotationRecognitionOpsEdge[];
  incident?: FanxiuDataAnnotationNavigationIncidentSummary | null;
  ambiguity?: FanxiuDataAnnotationRecognitionAmbiguitySummary | null;
}

export interface FanxiuDataAnnotationRecognitionAmbiguityFrame {
  sha256: string;
  path: string;
  width: number;
  height: number;
  captured_at?: string;
  fallback_scene_id?: number | null;
}

export interface FanxiuDataAnnotationRecognitionAmbiguitySummary {
  id: string;
  signature: string;
  review_status?: string | null;
  layer: number;
  tied_scene_ids: number[];
  occurrence_count: number;
  distinct_frame_count: number;
  first_seen_at: string;
  last_seen_at: string;
  selected_scene_counts: Record<string, number>;
  sample_frames: FanxiuDataAnnotationRecognitionAmbiguityFrame[];
  latest_event_id?: string;
  latest_similarities?: Array<{ scene_id: number; score?: number | null }>;
  asset_tree_sha256?: string;
  recognizer_version?: string;
  frame_data_urls?: Record<string, string>;
  recompute?: {
    scene_id?: number | null;
    score: number;
    status: string;
    calculated_at: string;
    asset_tree_sha256: string;
    trace: Array<Record<string, unknown>>;
  };
}

export interface FanxiuDataAnnotationRecognitionAmbiguityResponse {
  ok: boolean;
  entry_id: string;
  ambiguity: FanxiuDataAnnotationRecognitionAmbiguitySummary;
}

export interface FanxiuDataAnnotationNavigationIncidentSummary {
  id: string;
  status: string;
  review_status?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  elapsed_seconds?: number | null;
  target_scene_id?: number | null;
  current_scene_id?: number | null;
  fallback_used?: boolean;
  trigger?: Record<string, unknown>;
  context?: Record<string, unknown>;
  resolution?: Record<string, unknown> | null;
  timeline_count?: number;
}

export interface FanxiuDataAnnotationNavigationIncidentTimelineItem {
  index: number;
  time?: string;
  kind: string;
  source_scene_id?: number | null;
  recognized_scene_id?: number | null;
  recognized_score?: number | null;
  shape_id?: string;
  shape_title?: string;
  point?: [number, number] | null;
  reason?: string;
  landing_scene_id?: number | null;
  landing_score?: number | null;
  frame_similarity?: number | null;
  progressed?: boolean;
  navigation_state_key?: string;
  attempt?: number | null;
  before_frame?: string | null;
  after_frame?: string | null;
}

export interface FanxiuDataAnnotationNavigationIncident {
  id: string;
  status: string;
  review_status?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  elapsed_seconds?: number | null;
  target_scene_id?: number | null;
  current_scene_id?: number | null;
  fallback_used?: boolean;
  trigger?: { type?: string; label?: string; threshold?: Record<string, unknown> };
  policy?: Record<string, unknown>;
  context?: {
    task?: string | null;
    task_id?: string | null;
    task_type?: string | null;
    phase?: string | null;
    cell_id?: string | null;
    kernel_generation?: number | null;
  };
  asset_tree?: Record<string, unknown>;
  diagnostic?: Record<string, unknown> | null;
  resolution?: Record<string, unknown> | null;
  timeline: FanxiuDataAnnotationNavigationIncidentTimelineItem[];
  frames?: Array<{
    path: string;
    role: string;
    timeline_index?: number | null;
    scene_id?: number | null;
    shape_id?: string | null;
    shape_title?: string | null;
    captured_at?: string;
  }>;
  frame_data_urls?: Record<string, string>;
}

export interface FanxiuDataAnnotationNavigationIncidentResponse {
  ok: boolean;
  entry_id: string;
  incident: FanxiuDataAnnotationNavigationIncident;
}

export interface FanxiuDataAnnotationRecognitionOpsCategory {
  id: string;
  label: string;
  count: number;
}

export interface FanxiuDataAnnotationRecognitionOpsResponse {
  ok: boolean;
  entry_id: string;
  asset_tree_updated_at: number;
  matrix: {
    cache_key?: string | null;
    cache_path?: string | null;
    cache_hit: boolean;
    cache_missing?: boolean;
    cache_partial?: boolean;
    cache_stale?: boolean;
    score_mode?: string | null;
    layer?: number | string | null;
    threshold?: number | string | null;
    updated_at?: number | null;
    node_count: number;
    expected_node_count?: number | null;
    covered_node_count?: number | null;
    skipped_node_ids?: number[];
    edge_count: number;
    ignored_self_loop_count: number;
  };
  recompute?: {
    cache_key?: string | null;
    running: boolean;
    started_at?: number | null;
    finished_at?: number | null;
    error?: string | null;
  } | null;
  edges?: FanxiuDataAnnotationRecognitionOpsEdge[];
  categories: FanxiuDataAnnotationRecognitionOpsCategory[];
  issues: FanxiuDataAnnotationRecognitionOpsIssue[];
  summary: {
    node_count: number;
    edge_count: number;
    issue_count: number;
    category_counts: Record<string, number>;
  };
}

export interface FanxiuDataAnnotationSaveFramePayload {
  entry_id: string;
  current_frame_data_url?: string;
  fresh_capture?: boolean;
  title?: string;
  same_level_as_scene_id?: number;
  filename?: string;
  asset_node?: Record<string, unknown>;
  parent_id?: string;
  after_node_id?: string;
  base_revision?: string;
}

export interface FanxiuDataAnnotationSaveFrameResponse {
  ok: boolean;
  entry_id: string;
  filename: string;
  path: string;
  directory: string;
  width: number;
  height: number;
  fresh_capture: boolean;
  frame_sequence: number;
  captured_at: number;
  tree?: unknown[] | null;
  revision?: string | null;
  updated_at?: number | null;
}

export interface FanxiuDataAnnotationOcrFrameToken {
  text: string;
  x: number;
  y: number;
  w: number;
  h: number;
  parent_line_id?: string | null;
  line_order?: number | null;
  order?: number | null;
}

export interface FanxiuDataAnnotationOcrFrameLine {
  line_id: string;
  order: number;
  text: string;
  x: number;
  y: number;
  w: number;
  h: number;
  score?: number | null;
  source: 'paddle' | string;
}

export interface FanxiuDataAnnotationOcrFrameResponse {
  lines: FanxiuDataAnnotationOcrFrameLine[];
  tokens: FanxiuDataAnnotationOcrFrameToken[];
}

export interface FanxiuDataAnnotationRemoveBackgroundPayload {
  image_data_url: string;
  model?: string;
  alpha_matting?: boolean;
  post_process_mask?: boolean;
}

export interface FanxiuDataAnnotationRemoveBackgroundResponse {
  ok: boolean;
  model: string;
  width: number;
  height: number;
  alpha_mask_data_url: string;
  result_data_url: string;
}

export interface FanxiuDataAnnotationMacroPoint {
  x: number;
  y: number;
}

export interface FanxiuDataAnnotationMacroAnnotatePayload {
  image_data_url: string;
  action: 'click' | 'drag';
  start: FanxiuDataAnnotationMacroPoint;
  end?: FanxiuDataAnnotationMacroPoint | null;
  fallback_box: FanxiuGameWindow2MatchBox;
  frame_width: number;
  frame_height: number;
  duration_ms?: number;
  direction?: 'up' | 'down' | 'left' | 'right' | 'none' | null;
}

export interface FanxiuDataAnnotationMacroAnnotateResponse {
  ok: boolean;
  used_ai: boolean;
  box: FanxiuGameWindow2MatchBox;
  confidence: number;
  label: string;
  reason: string;
  raw: string;
}

export const getFanxiuDataAnnotationAssetTree = (entryId: string) => {
  return api
    .get<FanxiuDataAnnotationAssetTreeResponse>('/fanxiu/data-annotation/asset-tree', { params: { entry_id: entryId } })
    .then(res => res.data);
};

export const getFanxiuDataAnnotationRecognitionOps = (entryId: string, recompute = false) => {
  return api
    .get<FanxiuDataAnnotationRecognitionOpsResponse>('/fanxiu/data-annotation/recognition-ops', {
      params: { entry_id: entryId, layer: 2, recompute },
      timeout: 600000,
    })
    .then(res => res.data);
};

export const getFanxiuDataAnnotationNavigationIncident = (entryId: string, incidentId: string) => {
  return api
    .get<FanxiuDataAnnotationNavigationIncidentResponse>(
      `/fanxiu/data-annotation/recognition-ops/incidents/${encodeURIComponent(incidentId)}`,
      { params: { entry_id: entryId }, timeout: 120000 },
    )
    .then(res => res.data);
};

export const getFanxiuDataAnnotationRecognitionAmbiguity = (entryId: string, signature: string, recompute = false) => {
  return api
    .get<FanxiuDataAnnotationRecognitionAmbiguityResponse>(
      `/fanxiu/data-annotation/recognition-ops/ambiguities/${encodeURIComponent(signature)}`,
      { params: { entry_id: entryId, recompute }, timeout: 120000 },
    )
    .then(res => res.data);
};

export const saveFanxiuDataAnnotationAssetTree = (entryId: string, tree: unknown[], baseRevision?: string) => {
  return api
    .put<FanxiuDataAnnotationAssetTreeResponse>('/fanxiu/data-annotation/asset-tree', { entry_id: entryId, tree, base_revision: baseRevision ?? '' })
    .then(res => res.data);
};

export const saveFanxiuDataAnnotationFrame = (payload: FanxiuDataAnnotationSaveFramePayload) => {
  return api
    .post<FanxiuDataAnnotationSaveFrameResponse>('/fanxiu/data-annotation/save-frame', payload, { timeout: 60000 })
    .then(res => res.data);
};

export const getFanxiuDataAnnotationImage = (entryId: string, filename: string, cacheBust?: number) => {
  return api
    .get<Blob>('/fanxiu/data-annotation/image', {
      params: { entry_id: entryId, filename, ...(cacheBust ? { _: cacheBust } : {}) },
      responseType: 'blob',
    })
    .then(res => res.data);
};

export const recognizeFanxiuDataAnnotationOcrFrame = (imageDataUrl: string) => {
  return api
    .post<FanxiuDataAnnotationOcrFrameResponse>('/fanxiu/data-annotation/ocr-frame', { image_data_url: imageDataUrl }, {
      timeout: 180000,
    })
    .then(res => res.data);
};

export const removeFanxiuDataAnnotationBackground = (payload: FanxiuDataAnnotationRemoveBackgroundPayload) => {
  return api
    .post<FanxiuDataAnnotationRemoveBackgroundResponse>('/fanxiu/data-annotation/remove-background', payload, {
      timeout: 300000,
    })
    .then(res => res.data);
};

export const annotateFanxiuDataAnnotationMacroShape = (payload: FanxiuDataAnnotationMacroAnnotatePayload) => {
  return api
    .post<FanxiuDataAnnotationMacroAnnotateResponse>('/fanxiu/data-annotation/macro/annotate', payload, {
      timeout: 180000,
    })
    .then(res => res.data);
};
