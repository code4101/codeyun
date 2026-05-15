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
  source: string | null;
  source_path: string | null;
  updated_at: number | null;
  files: RimeContextPredictionFileInfo[];
  summary: RimeContextPredictionSummary;
  rows: RimeContextPredictionRow[];
}

export interface RimeContextArticle {
  id: string;
  title: string;
  enabled: boolean;
  source_type: string;
  source_key?: string;
  source_label: string;
  status: string;
  row_count: number;
  char_count: number;
  content_hash: string;
  extractor_version: number;
  created_at: number;
  updated_at: number;
  processed_at: number;
}

export interface RimeContextArticleSummary {
  article_count: number;
  enabled_count: number;
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

export interface RimeContextArticleImportPayload {
  title?: string;
  content: string;
  enabled: boolean;
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

export interface RimeContextHistoryArticleSavePayload {
  content: string;
}

export interface RimeContextHistoryArticleQuery {
  limit?: number;
  page?: number;
  page_size?: number;
}

export interface RimeContextLintQuery {
  source?: 'all' | 'history' | 'articles' | string;
  mode?: 'rules' | 'ai' | string;
  limit?: number;
  history_limit?: number;
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

export async function fetchRimeContextPredictionTree(
  entryId: string,
  limit = 5000,
): Promise<RimeContextPredictionTree> {
  const response = await api.get<RimeContextPredictionTree>(
    getDeviceEntryPath(entryId, '/rime/context-prediction/tree'),
    { params: { limit } },
  );
  return response.data;
}

export async function refreshRimeContextPredictionTree(
  entryId: string,
): Promise<RimeContextPredictionTree> {
  const response = await api.post<RimeContextPredictionTree>(
    getDeviceEntryPath(entryId, '/rime/context-prediction/tree/refresh'),
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

export async function deleteRimeContextArticle(
  entryId: string,
  articleId: string,
): Promise<RimeContextArticlesResponse> {
  const response = await api.delete<RimeContextArticlesResponse>(
    getDeviceEntryPath(entryId, `/rime/context-prediction/articles/${articleId}`),
  );
  return response.data;
}
