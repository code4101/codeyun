import api, { getDeviceEntryPath } from '@/api';

const CODEX_DAILY_SUMMARY_TIMEOUT_MS = 15 * 60 * 1000;

export interface CodexThreadSummary {
  id: string;
  title: string;
  preview?: string | null;
  cwd?: string | null;
  original_cwd?: string | null;
  rollout_path?: string | null;
  created_at?: number | null;
  updated_at?: number | null;
  archived: boolean;
  project_label: string;
  project_secondary_label?: string | null;
  workspace_root?: string | null;
}

export interface CodexProjectGroup {
  key: string;
  label: string;
  secondary_label?: string | null;
  cwd?: string | null;
  workspace_root?: string | null;
  thread_count: number;
  archived_thread_count: number;
  latest_updated_at?: number | null;
  threads: CodexThreadSummary[];
}

export interface CodexOverviewResponse {
  root_dir: string;
  default_root_dir: string;
  state_db_path: string;
  session_index_path: string;
  global_state_path: string;
  total_groups: number;
  total_threads: number;
  archived_threads: number;
  groups: CodexProjectGroup[];
}

export interface CodexThreadMessage {
  seq: number;
  timestamp?: string | null;
  role: 'user' | 'assistant';
  phase?: string | null;
  text: string;
}

export interface CodexThreadDetailThread extends CodexThreadSummary {
  group_key: string;
  group_label: string;
  group_secondary_label?: string | null;
}

export interface CodexThreadDetailResponse {
  root_dir: string;
  thread: CodexThreadDetailThread;
  message_count: number;
  user_message_count: number;
  assistant_message_count: number;
  messages: CodexThreadMessage[];
}

export interface CodexThreadMessageImage {
  index: number;
  type: string;
  image_url: string;
}

export interface CodexThreadMessageImagesResponse {
  root_dir: string;
  thread_id: string;
  message_seq: number;
  images: CodexThreadMessageImage[];
}

export interface CodexWorkloadTurn {
  id: string;
  thread_id: string;
  turn_index: number;
  thread_title: string;
  project_label: string;
  project_secondary_label?: string | null;
  workspace_root?: string | null;
  group_key: string;
  group_label: string;
  user_seq: number;
  assistant_seq?: number | null;
  start_at: number;
  end_at: number;
  duration_seconds: number;
  completed: boolean;
  preview?: string | null;
}

export interface CodexWorkloadSegment {
  start_at: number;
  end_at: number;
  duration_seconds: number;
  concurrency: number;
}

export interface CodexWorkloadResponse {
  root_dir: string;
  total_threads: number;
  total_turns: number;
  skipped_threads: number;
  max_concurrency: number;
  time_range_start?: number | null;
  time_range_end?: number | null;
  turns: CodexWorkloadTurn[];
  segments: CodexWorkloadSegment[];
}

export interface CodexDailySummaryThread {
  thread_id: string;
  title: string;
  project_label: string;
  project_secondary_label?: string | null;
  workspace_root?: string | null;
  start_at: number;
  end_at: number;
  turn_count: number;
  user_message_count: number;
  assistant_message_count: number;
  preview?: string | null;
}

export interface CodexDailySummaryTypeItem {
  key: string;
  label: string;
  color?: string | null;
  order: number;
  builtin: boolean;
}

export interface CodexDailySummaryResponse {
  root_dir: string;
  date: string;
  timezone: string;
  generated_at?: string | null;
  generated_by: 'codex_cli' | 'empty';
  model?: string | null;
  prompt_version: string;
  summary_text: string;
  thread_count: number;
  turn_count: number;
  user_message_count: number;
  assistant_message_count: number;
  threads: CodexDailySummaryThread[];
  type_items: CodexDailySummaryTypeItem[];
}

export interface CodexDailySummaryRunRead {
  id: string;
  root_dir: string;
  date: string;
  timezone: string;
  provider: string;
  generated_by: string;
  model?: string | null;
  prompt_version: string;
  force_requested: boolean;
  reused_existing_run: boolean;
  status: string;
  stage: string;
  stage_label: string;
  thread_count: number;
  turn_count: number;
  user_message_count: number;
  assistant_message_count: number;
  summary_text: string;
  error_message?: string | null;
  heartbeat_at?: number | null;
  result?: CodexDailySummaryResponse | null;
  created_at: number;
  finished_at?: number | null;
  updated_at: number;
}

export async function fetchCodexOverviewForEntry(entryId: string, rootDir?: string) {
  const response = await api.get<CodexOverviewResponse>(getDeviceEntryPath(entryId, '/codex/overview'), {
    params: rootDir?.trim() ? { root_dir: rootDir.trim() } : undefined,
  });
  return response.data;
}

export async function fetchCodexThreadDetailForEntry(entryId: string, threadId: string, rootDir?: string) {
  const response = await api.get<CodexThreadDetailResponse>(
    getDeviceEntryPath(entryId, `/codex/threads/${threadId}`),
    {
      params: rootDir?.trim() ? { root_dir: rootDir.trim() } : undefined,
    },
  );
  return response.data;
}

export async function fetchCodexThreadMessageImagesForEntry(
  entryId: string,
  threadId: string,
  messageSeq: number,
  rootDir?: string,
) {
  const response = await api.get<CodexThreadMessageImagesResponse>(
    getDeviceEntryPath(entryId, `/codex/threads/${threadId}/messages/${messageSeq}/images`),
    {
      params: rootDir?.trim() ? { root_dir: rootDir.trim() } : undefined,
    },
  );
  return response.data;
}

export async function fetchCodexWorkloadForEntry(entryId: string, rootDir?: string) {
  const response = await api.get<CodexWorkloadResponse>(getDeviceEntryPath(entryId, '/codex/workload'), {
    params: rootDir?.trim() ? { root_dir: rootDir.trim() } : undefined,
  });
  return response.data;
}

export async function generateCodexDailySummaryForEntry(
  entryId: string,
  payload: {
    date: string;
    root_dir?: string | null;
    model?: string | null;
  },
) {
  const response = await api.post<CodexDailySummaryResponse>(
    getDeviceEntryPath(entryId, '/codex/daily-summary/generate'),
    payload,
    {
      timeout: CODEX_DAILY_SUMMARY_TIMEOUT_MS,
    },
  );
  return response.data;
}

export async function fetchCodexDailySummaryLatestForEntry(
  entryId: string,
  params: {
    date: string;
    root_dir?: string | null;
  },
) {
  const response = await api.get<CodexDailySummaryRunRead>(
    getDeviceEntryPath(entryId, '/codex/daily-summary/latest'),
    {
      params: params.root_dir?.trim()
        ? { date: params.date, root_dir: params.root_dir.trim() }
        : { date: params.date },
    },
  );
  return response.data;
}

export async function startCodexDailySummaryRunForEntry(
  entryId: string,
  payload: {
    date: string;
    root_dir?: string | null;
    model?: string | null;
    force?: boolean;
  },
) {
  const response = await api.post<CodexDailySummaryRunRead>(
    getDeviceEntryPath(entryId, '/codex/daily-summary/runs'),
    payload,
  );
  return response.data;
}

export async function fetchCodexDailySummaryRunForEntry(entryId: string, runId: string) {
  const response = await api.get<CodexDailySummaryRunRead>(
    getDeviceEntryPath(entryId, `/codex/daily-summary/runs/${runId}`),
  );
  return response.data;
}
