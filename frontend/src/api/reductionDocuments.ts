import api from '@/api'

const DOCUMENT_REDUCTION_TIMEOUT_MS = 10 * 60 * 1000

export interface ReductionDocumentRead {
  id: string
  title: string
  original_filename: string
  media_type: string
  file_ext: string
  size_bytes: number
  sha256: string
  source_char_count: number
  status: string
  latest_run_id?: string | null
  latest_summary: string
  latest_query_at?: number | null
  run_count: number
  created_at: number
  updated_at: number
}

export interface ReductionDocumentRunRead {
  id: string
  document_id: string
  provider: string
  model: string
  task_type: string
  status: string
  branch_factor: number
  source_unit_count: number
  source_unit_truncated_count: number
  estimated_level_count: number
  current_level_index: number
  current_level_chunk_count: number
  current_level_completed_chunk_count: number
  completed_chunk_count: number
  level_count: number
  node_count: number
  top_summary: string
  error_message?: string | null
  created_at: number
  finished_at?: number | null
  updated_at: number
}

export interface ReductionDocumentDetailResponse {
  document: ReductionDocumentRead
  active_run?: ReductionDocumentRunRead | null
  latest_run?: ReductionDocumentRunRead | null
}

export interface ReductionDocumentIndexRequest {
  provider?: string | null
  base_url?: string | null
  api_key?: string | null
  model?: string | null
  branch_factor?: number
}

export interface ReductionDocumentIndexResponse {
  document: ReductionDocumentRead
  run: ReductionDocumentRunRead
  result: {
    topic?: string
    summary?: string
    keywords?: string[]
    possible_questions?: string[]
    importance?: string
    importance_reason?: string
    reason?: string
    model?: string
  }
  reduction: {
    run_id: string
    profile_id: string
    level_count: number
    node_count: number
    levels: Array<{
      level: number
      input_kind: 'source' | 'summary'
      chunk_count: number
      node_count: number
      nodes: Array<{
        node_id: string
        level: number
        chunk_id: string
        payload: Record<string, any>
        source_refs: string[]
        child_node_ids: string[]
        model: string
        metadata: Record<string, any>
      }>
    }>
  }
}

export interface ReductionDocumentQueryRequest {
  query: string
  provider?: string | null
  base_url?: string | null
  api_key?: string | null
  model?: string | null
  run_id?: string | null
}

export interface ReductionDocumentQueryResponse {
  query_id: string
  document_id: string
  run_id: string
  model: string
  answer: string
  summary: string
  needs_more_context: boolean
  matched_node_ids: string[]
  matched_source_refs: string[]
  follow_up_questions: string[]
  matched_nodes: Array<{
    node_id: string
    topic: string
    summary: string
    source_refs: string[]
    score: number
  }>
}

export interface ReductionDocumentDeleteResponse {
  ok: boolean
  document_id: string
}

export async function fetchReductionDocuments() {
  const response = await api.get<{ items: ReductionDocumentRead[] }>('/reduction-documents')
  return response.data.items
}

export async function uploadReductionDocument(file: File) {
  const formData = new FormData()
  formData.append('file', file)
  const response = await api.post<ReductionDocumentRead>(
    '/reduction-documents/upload',
    formData,
    {
      timeout: DOCUMENT_REDUCTION_TIMEOUT_MS,
    },
  )
  return response.data
}

export async function fetchReductionDocumentDetail(documentId: string) {
  const response = await api.get<ReductionDocumentDetailResponse>(`/reduction-documents/${documentId}`)
  return response.data
}

export async function indexReductionDocument(documentId: string, payload: ReductionDocumentIndexRequest) {
  const response = await api.post<ReductionDocumentIndexResponse>(
    `/reduction-documents/${documentId}/index`,
    payload,
    {
      timeout: DOCUMENT_REDUCTION_TIMEOUT_MS,
    },
  )
  return response.data
}

export async function queryReductionDocument(documentId: string, payload: ReductionDocumentQueryRequest) {
  const response = await api.post<ReductionDocumentQueryResponse>(
    `/reduction-documents/${documentId}/query`,
    payload,
    {
      timeout: DOCUMENT_REDUCTION_TIMEOUT_MS,
    },
  )
  return response.data
}

export async function deleteReductionDocument(documentId: string) {
  const response = await api.delete<ReductionDocumentDeleteResponse>(`/reduction-documents/${documentId}`)
  return response.data
}
