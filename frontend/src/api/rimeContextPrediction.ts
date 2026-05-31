import api, { getDeviceEntryPath } from '@/api';

export interface RimeContextPredictionRow {
  context: string;
  prefix: string;
  candidate: string;
  weight: number;
  comment?: string;
}

export interface RimeContextPredictionFileInfo {
  key: string;
  path: string | null;
  exists: boolean;
  size: number;
  modified_at: number | null;
}

export interface RimeContextPredictionSummary {
  row_count: number;
  context_count: number;
  prefix_count: number;
  candidate_count: number;
}

export interface RimeContextPredictionTree {
  available: boolean;
  status: string;
  message: string;
  rime_dir: string | null;
  source_kind?: string;
  source: string | null;
  source_path: string | null;
  updated_at: number | null;
  files: RimeContextPredictionFileInfo[];
  summary: RimeContextPredictionSummary;
  rows: RimeContextPredictionRow[];
}

export type RimeContextPredictionSource = 'snapshot' | 'hot' | 'context_hot' | 'seed';

export interface RimeContextArticle {
  id: string;
  title: string;
  enabled: boolean;
  source_type: string;
  source_key?: string;
  source_label: string;
  weight_multiplier?: number;
  status: string;
  row_count: number;
  char_count: number;
  content_hash: string;
  extractor_version: number;
  created_at: number;
  updated_at: number;
  processed_at: number;
  readonly?: boolean;
}

export interface RimeContextArticleSummary {
  article_count: number;
  enabled_count: number;
  lexicon_count?: number;
  negative_lexicon_count?: number;
  contribution_count: number;
}

export interface RimeContextArticlesResponse {
  available: boolean;
  status: string;
  message: string;
  rime_dir: string | null;
  files: RimeContextPredictionFileInfo[];
  summary: RimeContextArticleSummary;
  articles: RimeContextArticle[];
}

export interface RimeContextHistorySummary {
  entry_count: number;
  char_count: number;
  paragraph_count: number;
  first_seen: string;
  last_seen: string;
  pending_row_count: number;
  model_count_row_count: number;
  truncated: boolean;
  limit: number;
  edited: boolean;
  saved_at: number;
  base_event_count: number;
  appended_event_count: number;
}

export interface RimeContextHistoryPagination {
  page: number;
  page_size: number;
  total: number;
  total_pages: number;
  start_index: number;
  end_index: number;
  has_prev: boolean;
  has_next: boolean;
}

export interface RimeContextHistoryArticleResponse {
  available: boolean;
  status: string;
  message: string;
  rime_dir: string | null;
  source: string | null;
  source_path: string | null;
  updated_at: number | null;
  files: RimeContextPredictionFileInfo[];
  summary: RimeContextHistorySummary;
  pagination: RimeContextHistoryPagination | null;
  content: string;
}

export interface RimeContextArticleContentResponse {
  available: boolean;
  status: string;
  message: string;
  rime_dir: string | null;
  files: RimeContextPredictionFileInfo[];
  article: RimeContextArticle | null;
  pagination: RimeContextHistoryPagination | null;
  content: string;
}

export interface RimeContextLintSummary {
  source_count: number;
  issue_count: number;
  high_count: number;
  medium_count: number;
  low_count: number;
  rule_count: number;
  ai_count: number;
}

export interface RimeContextLintIssue {
  id: string;
  source_type: string;
  source_id: string;
  source_title: string;
  source_enabled: boolean;
  rule: string;
  type: string;
  severity: 'high' | 'medium' | 'low' | string;
  line: number;
  column: number;
  span_start: number;
  span_end: number;
  text: string;
  message: string;
  suggestion: string;
  confidence: number;
  excerpt: string;
}

export interface RimeContextLintResponse {
  available: boolean;
  status: string;
  message: string;
  rime_dir: string | null;
  files: RimeContextPredictionFileInfo[];
  summary: RimeContextLintSummary;
  issues: RimeContextLintIssue[];
}

export type RimeRuntimeConfigValue = string | number | boolean | null;

export interface RimeRuntimeConfigField {
  type: 'int' | 'float' | 'bool' | 'enum' | string;
  label?: string;
  min?: number;
  max?: number;
  values?: string[];
}

export interface RimeRuntimeConfigResponse {
  available: boolean;
  status: string;
  message: string;
  rime_dir: string | null;
  source: string | null;
  source_path: string | null;
  updated_at: number | null;
  files: RimeContextPredictionFileInfo[];
  config: Record<string, RimeRuntimeConfigValue>;
  fields: Record<string, RimeRuntimeConfigField>;
  missing_keys: string[];
  requires_reload: boolean;
  deploy?: {
    ok: boolean;
    status: string;
    message: string;
    deployer?: string | null;
    returncode?: number;
  } | null;
}

export interface RimePerformanceSection {
  count: number;
  total_ms: number;
  avg_ms: number;
  max_ms: number;
  last_ms: number;
}

export interface RimePerformanceQueryTrace {
  seq: number;
  input: string;
  full_input: string;
  seg_start: number;
  seg_end: number;
  gap_ms: number;
  duration_ms: number;
  candidate_count: number;
  yielded_count: number;
  heap_kb: number;
  reason: string;
}

export interface RimePerformanceResponse {
  available: boolean;
  status: string;
  message: string;
  rime_dir: string | null;
  source: string | null;
  source_path: string | null;
  updated_at: number | null;
  files: RimeContextPredictionFileInfo[];
  config: Record<string, RimeRuntimeConfigValue>;
  runtime: Record<string, number | string | boolean | null>;
  sections: Record<string, RimePerformanceSection>;
  recent_queries: RimePerformanceQueryTrace[];
  started_at?: number | null;
  clock_ms?: number | null;
  version?: number;
}

export interface RimeWeightCompareGroup {
  key: string;
  weight: number;
  row_count: number;
}

export interface RimeWeightCompareItem {
  input: string;
  candidate: string;
  pinyin: string;
  default_pinyin: string;
  total_weight: number;
  exact_prefix_weight: number;
  row_count: number;
  prefixes: RimeWeightCompareGroup[];
  contexts: RimeWeightCompareGroup[];
  comments: RimeWeightCompareGroup[];
  rows: RimeContextPredictionRow[];
}

export interface RimeWeightCompareResponse {
  available: boolean;
  status: string;
  message: string;
  rime_dir: string | null;
  source_kind?: string;
  source: string | null;
  source_path: string | null;
  updated_at: number | null;
  files: RimeContextPredictionFileInfo[];
  summary: {
    candidate_count: number;
    matched_count: number;
    row_count: number;
  };
  items: RimeWeightCompareItem[];
}

export interface RimeContextArticleImportPayload {
  title?: string;
  content: string;
  enabled: boolean;
  source_type?: 'imported_article' | 'lexicon' | 'negative_lexicon' | string;
  weight_multiplier?: number;
}

export interface RimeContextDeviceHistoryImportPayload {
  source_entry_id: string;
  enabled: boolean;
  limit?: number;
}

export interface RimeContextArticleUpdatePayload {
  title?: string;
  enabled?: boolean;
}

export interface RimeContextArticleContentSavePayload {
  content: string;
  page: number;
  page_size: number;
}

export interface RimeContextHistoryArticleSavePayload {
  content: string;
}

export interface RimeContextHistoryArticleQuery {
  limit?: number;
  page?: number;
  page_size?: number;
}

export interface RimeContextArticleContentQuery {
  page?: number;
  page_size?: number;
}

export interface RimeContextLintQuery {
  source?: 'all' | 'history' | 'articles' | string;
  mode?: 'rules' | 'ai' | string;
  limit?: number;
  history_limit?: number;
}

export interface RimeContextPredictionTreeQuery {
  source?: RimeContextPredictionSource | string;
  limit?: number;
}

export interface RimeContextCandidateDeletePayload {
  context: string;
  prefix: string;
  candidate: string;
}

export interface RimeContextCandidateUpdatePayload {
  original_context?: string;
  original_prefix?: string;
  original_candidate?: string;
  context: string;
  prefix: string;
  candidate: string;
  weight: number;
}

export interface RimeRuntimeConfigUpdatePayload {
  config: Record<string, RimeRuntimeConfigValue>;
}

export interface RimeWeightComparePayload {
  candidates: string[];
  source?: RimeContextPredictionSource | string;
  limit?: number;
}

export interface RimeWeightCompareAdjustPayload {
  prefix: string;
  candidate: string;
  weight: number;
  candidates: string[];
  source?: RimeContextPredictionSource | string;
  limit?: number;
}

export async function fetchRimeContextPredictionTree(
  entryId: string,
  query: RimeContextPredictionTreeQuery = {},
): Promise<RimeContextPredictionTree> {
  const response = await api.get<RimeContextPredictionTree>(
    getDeviceEntryPath(entryId, '/rime/context-prediction/tree'),
    { params: { source: query.source || 'snapshot', limit: query.limit || 50000 } },
  );
  return response.data;
}

export async function refreshRimeContextPredictionTree(
  entryId: string,
  query: RimeContextPredictionTreeQuery = {},
): Promise<RimeContextPredictionTree> {
  const response = await api.post<RimeContextPredictionTree>(
    getDeviceEntryPath(entryId, '/rime/context-prediction/tree/refresh'),
    undefined,
    { params: { source: query.source || 'snapshot', limit: query.limit || 50000 } },
  );
  return response.data;
}

export async function fetchRimeRuntimeConfig(
  entryId: string,
): Promise<RimeRuntimeConfigResponse> {
  const response = await api.get<RimeRuntimeConfigResponse>(
    getDeviceEntryPath(entryId, '/rime/context-prediction/runtime-config'),
  );
  return response.data;
}

export async function updateRimeRuntimeConfig(
  entryId: string,
  payload: RimeRuntimeConfigUpdatePayload,
): Promise<RimeRuntimeConfigResponse> {
  const response = await api.patch<RimeRuntimeConfigResponse>(
    getDeviceEntryPath(entryId, '/rime/context-prediction/runtime-config'),
    payload,
  );
  return response.data;
}

export async function fetchRimePerformanceStats(
  entryId: string,
): Promise<RimePerformanceResponse> {
  const response = await api.get<RimePerformanceResponse>(
    getDeviceEntryPath(entryId, '/rime/context-prediction/performance'),
  );
  return response.data;
}

export async function resetRimePerformanceStats(
  entryId: string,
): Promise<RimePerformanceResponse> {
  const response = await api.post<RimePerformanceResponse>(
    getDeviceEntryPath(entryId, '/rime/context-prediction/performance/reset'),
  );
  return response.data;
}

export async function compareRimeContextWeights(
  entryId: string,
  payload: RimeWeightComparePayload,
): Promise<RimeWeightCompareResponse> {
  const response = await api.post<RimeWeightCompareResponse>(
    getDeviceEntryPath(entryId, '/rime/context-prediction/weight-compare'),
    payload,
  );
  return response.data;
}

export async function adjustRimeContextWeightCompare(
  entryId: string,
  payload: RimeWeightCompareAdjustPayload,
): Promise<RimeWeightCompareResponse> {
  const response = await api.post<RimeWeightCompareResponse>(
    getDeviceEntryPath(entryId, '/rime/context-prediction/weight-compare/adjust'),
    payload,
  );
  return response.data;
}

export async function fetchRimeContextArticles(
  entryId: string,
): Promise<RimeContextArticlesResponse> {
  const response = await api.get<RimeContextArticlesResponse>(
    getDeviceEntryPath(entryId, '/rime/context-prediction/articles'),
  );
  return response.data;
}

export async function fetchRimeContextHistoryArticle(
  entryId: string,
  query: RimeContextHistoryArticleQuery = { page: 1, page_size: 2000 },
): Promise<RimeContextHistoryArticleResponse> {
  const response = await api.get<RimeContextHistoryArticleResponse>(
    getDeviceEntryPath(entryId, '/rime/context-prediction/history-article'),
    { params: query },
  );
  return response.data;
}

export async function fetchRimeContextArticleContent(
  entryId: string,
  articleId: string,
  query: RimeContextArticleContentQuery = { page: 1, page_size: 2000 },
): Promise<RimeContextArticleContentResponse> {
  const response = await api.get<RimeContextArticleContentResponse>(
    getDeviceEntryPath(entryId, `/rime/context-prediction/articles/${articleId}/content`),
    { params: query },
  );
  return response.data;
}

export async function saveRimeContextHistoryArticle(
  entryId: string,
  payload: RimeContextHistoryArticleSavePayload,
): Promise<RimeContextHistoryArticleResponse> {
  const response = await api.put<RimeContextHistoryArticleResponse>(
    getDeviceEntryPath(entryId, '/rime/context-prediction/history-article'),
    payload,
  );
  return response.data;
}

export async function fetchRimeContextLint(
  entryId: string,
  query: RimeContextLintQuery = { source: 'all', mode: 'rules', limit: 200 },
): Promise<RimeContextLintResponse> {
  const response = await api.get<RimeContextLintResponse>(
    getDeviceEntryPath(entryId, '/rime/context-prediction/lint'),
    { params: query },
  );
  return response.data;
}

export async function deleteRimeContextCandidate(
  entryId: string,
  payload: RimeContextCandidateDeletePayload,
): Promise<RimeContextPredictionTree> {
  const response = await api.delete<RimeContextPredictionTree>(
    getDeviceEntryPath(entryId, '/rime/context-prediction/candidates'),
    { data: payload },
  );
  return response.data;
}

export async function updateRimeContextCandidate(
  entryId: string,
  payload: RimeContextCandidateUpdatePayload,
): Promise<RimeContextPredictionTree> {
  const response = await api.patch<RimeContextPredictionTree>(
    getDeviceEntryPath(entryId, '/rime/context-prediction/candidates'),
    payload,
  );
  return response.data;
}

export async function importRimeContextArticle(
  entryId: string,
  payload: RimeContextArticleImportPayload,
): Promise<RimeContextArticlesResponse> {
  const response = await api.post<RimeContextArticlesResponse>(
    getDeviceEntryPath(entryId, '/rime/context-prediction/articles'),
    payload,
  );
  return response.data;
}

export async function importRimeContextDeviceHistory(
  entryId: string,
  payload: RimeContextDeviceHistoryImportPayload,
): Promise<RimeContextArticlesResponse> {
  const response = await api.post<RimeContextArticlesResponse>(
    getDeviceEntryPath(entryId, '/rime/context-prediction/articles/from-device-history'),
    payload,
  );
  return response.data;
}

export async function updateRimeContextArticle(
  entryId: string,
  articleId: string,
  payload: RimeContextArticleUpdatePayload,
): Promise<RimeContextArticlesResponse> {
  const response = await api.patch<RimeContextArticlesResponse>(
    getDeviceEntryPath(entryId, `/rime/context-prediction/articles/${articleId}`),
    payload,
  );
  return response.data;
}

export async function saveRimeContextArticleContent(
  entryId: string,
  articleId: string,
  payload: RimeContextArticleContentSavePayload,
): Promise<RimeContextArticleContentResponse> {
  const response = await api.put<RimeContextArticleContentResponse>(
    getDeviceEntryPath(entryId, `/rime/context-prediction/articles/${articleId}/content`),
    payload,
  );
  return response.data;
}

export async function deleteRimeContextArticle(
  entryId: string,
  articleId: string,
): Promise<RimeContextArticlesResponse> {
  const response = await api.delete<RimeContextArticlesResponse>(
    getDeviceEntryPath(entryId, `/rime/context-prediction/articles/${articleId}`),
  );
  return response.data;
}
