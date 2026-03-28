import api, { getDeviceEntryPath } from '@/api'

const AI_GIT_GENERATE_TIMEOUT_MS = 5 * 60 * 1000
const AI_GIT_COMMIT_TIMEOUT_MS = 2 * 60 * 1000

export type GitCommitStyle = 'summary' | 'conventional'

export interface GitChangedFile {
  path: string
  status: string
  staged: boolean
  unstaged: boolean
  untracked: boolean
}

export interface GitSuggestedSplitGroup {
  label: string
  file_count: number
  sample_paths: string[]
}

export interface GitInspectResponse {
  cwd: string
  repo_root: string
  branch: string
  branch_status: string
  clean: boolean
  status_lines: string[]
  diff_stat: string
  staged_diff_stat: string
  changed_files: GitChangedFile[]
  changed_file_count: number
  estimated_changed_line_count: number
  split_recommended: boolean
  split_reason: string
  oversized: boolean
  suggested_split_groups: GitSuggestedSplitGroup[]
}

export interface GitInspectRequest {
  cwd: string
}

export interface GitGenerateMessageRequest extends GitInspectRequest {
  provider?: string | null
  base_url?: string | null
  api_key?: string | null
  model?: string | null
  style?: GitCommitStyle
  include_body?: boolean
  max_files?: number
}

export interface GitGenerateMessageResponse {
  inspect: GitInspectResponse
  subject: string
  body: string[]
  full_message: string
  needs_split: boolean
  reason: string
  model: string
  raw_content: string
}

export interface GitGenerateAndCommitRequest extends GitGenerateMessageRequest {
  add_all?: boolean
}

export interface GitReductionLevel {
  level: number
  input_kind: 'source' | 'summary'
  chunk_count: number
  node_count: number
  preview_nodes: {
    node_id: string
    topic: string
    summary: string
    candidate_subject: string
    source_ref_count: number
  }[]
}

export interface GitReductionMeta {
  run_id: string
  profile_id: string
  level_count: number
  source_unit_count: number
  source_unit_truncated_count: number
  node_count: number
  leaf_chunk_count: number
  levels: GitReductionLevel[]
}

export interface GitReduceRequest extends GitInspectRequest {
  provider?: string | null
  base_url?: string | null
  api_key?: string | null
  model?: string | null
  style?: GitCommitStyle
  include_body?: boolean
  branch_factor?: number
}

export interface GitReduceAndCommitRequest extends GitReduceRequest {
  add_all?: boolean
}

export interface GitReduceResponse {
  inspect: GitInspectResponse
  subject: string
  body: string[]
  full_message: string
  needs_split: boolean
  reason: string
  model: string
  raw_content: string
  topic: string
  summary: string
  key_points: string[]
  risk_points: string[]
  reduction: GitReductionMeta
}

export interface GitReduceAndCommitResponse extends GitReduceResponse {
  commit: GitCommitResponse
}

export interface GitReductionRunRead {
  id: string
  entry_id: string
  cwd: string
  provider: string
  model: string
  style: GitCommitStyle
  include_body: boolean
  branch_factor: number
  auto_commit: boolean
  add_all: boolean
  status: 'running' | 'completed' | 'failed'
  repo_root: string
  branch: string
  source_unit_count: number
  source_unit_truncated_count: number
  estimated_level_count: number
  current_level_index: number
  current_level_chunk_count: number
  current_level_completed_chunk_count: number
  completed_chunk_count: number
  level_count: number
  node_count: number
  error_message?: string | null
  result?: GitReduceResponse | null
  commit?: GitCommitResponse | null
  created_at: number
  finished_at?: number | null
  updated_at: number
}

export interface GitStartReductionRunRequest {
  cwd: string
  provider?: string | null
  base_url?: string | null
  api_key?: string | null
  model?: string | null
  style?: GitCommitStyle
  include_body?: boolean
  branch_factor?: number
  auto_commit?: boolean
  add_all?: boolean
}

export interface GitCommitRequest extends GitInspectRequest {
  subject: string
  body: string[]
  add_all?: boolean
}

export interface GitCommitResponse {
  cwd: string
  repo_root: string
  branch: string
  commit_hash: string
  short_hash: string
  summary: string
  full_message: string
  clean: boolean
  status_lines: string[]
}

export interface GitGenerateAndCommitResponse extends GitGenerateMessageResponse {
  commit: GitCommitResponse
}

export async function inspectDeviceEntryGit(entryId: string, payload: GitInspectRequest) {
  const response = await api.post<GitInspectResponse>(
    getDeviceEntryPath(entryId, '/git/inspect'),
    payload,
  )
  return response.data
}

export async function generateDeviceEntryGitMessage(entryId: string, payload: GitGenerateMessageRequest) {
  const response = await api.post<GitGenerateMessageResponse>(
    getDeviceEntryPath(entryId, '/git/generate-message'),
    payload,
    {
      timeout: AI_GIT_GENERATE_TIMEOUT_MS,
    },
  )
  return response.data
}

export async function generateAndCommitDeviceEntryGit(entryId: string, payload: GitGenerateAndCommitRequest) {
  const response = await api.post<GitGenerateAndCommitResponse>(
    getDeviceEntryPath(entryId, '/git/generate-and-commit'),
    payload,
    {
      timeout: AI_GIT_GENERATE_TIMEOUT_MS + AI_GIT_COMMIT_TIMEOUT_MS,
    },
  )
  return response.data
}

export async function reduceDeviceEntryGit(entryId: string, payload: GitReduceRequest) {
  const response = await api.post<GitReduceResponse>(
    getDeviceEntryPath(entryId, '/git/reduce'),
    payload,
    {
      timeout: AI_GIT_GENERATE_TIMEOUT_MS,
    },
  )
  return response.data
}

export async function reduceAndCommitDeviceEntryGit(entryId: string, payload: GitReduceAndCommitRequest) {
  const response = await api.post<GitReduceAndCommitResponse>(
    getDeviceEntryPath(entryId, '/git/reduce-and-commit'),
    payload,
    {
      timeout: AI_GIT_GENERATE_TIMEOUT_MS + AI_GIT_COMMIT_TIMEOUT_MS,
    },
  )
  return response.data
}

export async function startDeviceEntryGitReductionRun(entryId: string, payload: GitStartReductionRunRequest) {
  const response = await api.post<GitReductionRunRead>(
    getDeviceEntryPath(entryId, '/git/reduce-runs'),
    payload,
  )
  return response.data
}

export async function fetchDeviceEntryGitReductionRun(entryId: string, runId: string) {
  const response = await api.get<GitReductionRunRead>(
    getDeviceEntryPath(entryId, `/git/reduce-runs/${runId}`),
  )
  return response.data
}

export async function commitDeviceEntryGit(entryId: string, payload: GitCommitRequest) {
  const response = await api.post<GitCommitResponse>(
    getDeviceEntryPath(entryId, '/git/commit'),
    payload,
    {
      timeout: AI_GIT_COMMIT_TIMEOUT_MS,
    },
  )
  return response.data
}
