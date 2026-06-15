import api from '@/api'

export interface GithubProjectSourceRef {
  source_type?: string
  source_key?: string
  source_label?: string
  seen_at?: number
}

export interface GithubProject {
  id: number
  github_repo_id: number
  full_name: string
  html_url: string
  default_branch: string
  description: string
  homepage: string
  language: string
  license_spdx_id: string
  topics: string[]
  stars: number
  forks: number
  open_issues: number
  archived: boolean
  disabled: boolean
  private: boolean
  created_at_github: string
  pushed_at: string
  updated_at: string
  last_seen_at: number
  last_checked_at: number | null
  needs_review: boolean
  analysis_note: string
  source_refs: GithubProjectSourceRef[]
  update_notes: Array<Record<string, unknown>>
  created_at: number
  updated_at_local: number
}

export interface GithubProjectUpsertRequest {
  github_repo_id: number
  full_name: string
  html_url?: string
  default_branch?: string
  description?: string
  homepage?: string
  language?: string
  license_spdx_id?: string
  topics?: string[]
  stars?: number
  forks?: number
  open_issues?: number
  archived?: boolean
  disabled?: boolean
  private?: boolean
  created_at?: string
  pushed_at?: string
  updated_at?: string
  analysis_note?: string
  source?: GithubProjectSourceRef
}

export interface GithubProjectListResponse {
  items: GithubProject[]
  total: number
}

export interface GithubProjectUpsertResponse {
  item: GithubProject
  created: boolean
  changed: boolean
}

export async function listGithubProjects(params: {
  q?: string
  needs_review?: boolean | null
  limit?: number
  offset?: number
} = {}): Promise<GithubProjectListResponse> {
  const query = Object.fromEntries(
    Object.entries(params).filter(([, value]) => value !== null && value !== undefined && value !== ''),
  )
  const response = await api.get<GithubProjectListResponse>('/github-projects', { params: query })
  return response.data
}

export async function upsertGithubProject(payload: GithubProjectUpsertRequest): Promise<GithubProjectUpsertResponse> {
  const response = await api.post<GithubProjectUpsertResponse>('/github-projects/upsert', payload)
  return response.data
}

export async function patchGithubProject(
  id: number,
  payload: { analysis_note?: string; needs_review?: boolean },
): Promise<GithubProject> {
  const response = await api.patch<GithubProject>(`/github-projects/${id}`, payload)
  return response.data
}
