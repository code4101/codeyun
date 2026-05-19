import api, { getDeviceEntryPath } from '@/api';

const CODEX_SESSION_READ_TIMEOUT_MS = 120 * 1000;
const CODEX_WORKLOAD_READ_TIMEOUT_MS = 180 * 1000;

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
  thread_offset?: number;
  thread_limit?: number | null;
  returned_threads?: number;
  has_more?: boolean;
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

export async function fetchCodexOverviewForEntry(
  entryId: string,
  params?: {
    rootDir?: string;
    threadOffset?: number;
    threadLimit?: number;
  },
) {
  const requestParams: Record<string, string | number> = {};
  if (params?.rootDir?.trim()) {
    requestParams.root_dir = params.rootDir.trim();
  }
  if (params?.threadOffset) {
    requestParams.thread_offset = params.threadOffset;
  }
  if (params?.threadLimit) {
    requestParams.thread_limit = params.threadLimit;
  }
  const response = await api.get<CodexOverviewResponse>(getDeviceEntryPath(entryId, '/codex/overview'), {
    params: Object.keys(requestParams).length ? requestParams : undefined,
    timeout: CODEX_SESSION_READ_TIMEOUT_MS,
  });
  return response.data;
}

export async function fetchCodexThreadDetailForEntry(entryId: string, threadId: string, rootDir?: string) {
  const response = await api.get<CodexThreadDetailResponse>(
    getDeviceEntryPath(entryId, `/codex/threads/${threadId}`),
    {
      params: rootDir?.trim() ? { root_dir: rootDir.trim() } : undefined,
      timeout: CODEX_SESSION_READ_TIMEOUT_MS,
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
      timeout: CODEX_SESSION_READ_TIMEOUT_MS,
    },
  );
  return response.data;
}

export async function fetchCodexWorkloadForEntry(
  entryId: string,
  options?: string | {
    rootDir?: string;
    startAt?: number;
    endAt?: number;
  },
) {
  const rootDir = typeof options === 'string' ? options : options?.rootDir;
  const requestParams: Record<string, string | number> = {};
  if (rootDir?.trim()) {
    requestParams.root_dir = rootDir.trim();
  }
  if (typeof options !== 'string') {
    if (Number.isFinite(options?.startAt)) {
      requestParams.start_at = Number(options?.startAt);
    }
    if (Number.isFinite(options?.endAt)) {
      requestParams.end_at = Number(options?.endAt);
    }
  }
  const response = await api.get<CodexWorkloadResponse>(getDeviceEntryPath(entryId, '/codex/workload'), {
    params: Object.keys(requestParams).length ? requestParams : undefined,
    timeout: CODEX_WORKLOAD_READ_TIMEOUT_MS,
  });
  return response.data;
}
