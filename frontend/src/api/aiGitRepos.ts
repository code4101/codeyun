import api from '@/api'

export interface AiGitSavedRepo {
  id: string
  name: string
  entry_id: string
  cwd: string
  pinned: boolean
  order_index: number
  created_at?: number | null
  updated_at?: number | null
  last_used_at?: number | null
}

export interface AiGitSavedReposResponse {
  items: AiGitSavedRepo[]
}

export interface AiGitRepoStatusesRequest {
  repo_ids?: string[]
}

export interface AiGitRepoStatusItem {
  repo_id: string
  name: string
  entry_id: string
  cwd: string
  ok: boolean
  clean?: boolean | null
  branch: string
  branch_status: string
  repo_root?: string | null
  changed_file_count: number
  estimated_changed_line_count: number
  changed_paths: string[]
  split_recommended: boolean
  split_reason: string
  oversized: boolean
  suggested_split_groups: Array<{
    label: string
    file_count: number
    sample_paths: string[]
  }>
  error?: string | null
}

export interface AiGitRepoStatusesResponse {
  items: AiGitRepoStatusItem[]
}

export async function fetchAiGitSavedRepos() {
  const response = await api.get<AiGitSavedReposResponse>('/ai-git-repos')
  return response.data
}

export async function saveAiGitSavedRepos(items: AiGitSavedRepo[]) {
  const response = await api.put<AiGitSavedReposResponse>('/ai-git-repos', { items })
  return response.data
}

export async function touchAiGitSavedRepo(repoId: string) {
  const response = await api.post<{ ok: boolean; item: AiGitSavedRepo | null }>(`/ai-git-repos/${repoId}/touch`)
  return response.data
}

export async function fetchAiGitRepoStatuses(payload: AiGitRepoStatusesRequest = {}) {
  const response = await api.post<AiGitRepoStatusesResponse>('/ai-git-repos/statuses', payload)
  return response.data
}
