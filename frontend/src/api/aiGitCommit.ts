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
