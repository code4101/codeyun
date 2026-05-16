import api from '@/api'

export interface CodeYunServiceStatus {
  key: string
  title: string
  engine: string
  device: string
  lang: string
  loaded: boolean
  state: 'cold' | 'idle' | 'running' | string
  instance_count: number
  idle_instance_count: number
  active_instance_count: number
  max_instances: number
  idle_timeout_seconds: number
  idle_expires_at?: number | null
  idle_remaining_seconds?: number | null
  acquire_timeout_seconds: number
  call_count: number
  error_count: number
  last_loaded_at?: number | null
  last_used_at?: number | null
  last_error?: string | null
  options?: Record<string, unknown>
}

export interface ServiceSummary {
  ok: boolean
  device: {
    id: string
    hostname: string
  }
  services: CodeYunServiceStatus[]
  token_count: number
  enabled_token_count: number
}

export interface ServiceResetResponse {
  ok: boolean
  service: CodeYunServiceStatus
}

export interface ServiceAccessToken {
  id: string
  label: string
  masked_value: string
  plaintext_value?: string
  scopes: string[]
  enabled: boolean
  is_legacy: boolean
  notes: string
  call_count: number
  last_used_at?: number | null
  created_at: number
  updated_at: number
}

export interface ServiceConnectionDoc {
  kind: 'local' | 'lan' | 'public' | string
  label: string
  base_url: string
  url: string
  status: 'available' | 'unconfigured' | string
  source?: string
}

export interface ServiceDocs {
  ok: boolean
  connections: ServiceConnectionDoc[]
  services: Array<{
    key: string
    title: string
    endpoint: string
    method: string
    scopes: string[]
  }>
  examples: {
    curl: string
    python: string
    javascript: string
  }
}

export interface ServiceTokenCreatePayload {
  label?: string
  scopes?: string[]
  notes?: string
  enabled?: boolean
}

export interface ServiceTokenUpdatePayload {
  label?: string
  scopes?: string[]
  notes?: string
  enabled?: boolean
}

export const fetchClusterServiceSummary = async (entryId: string): Promise<ServiceSummary> => {
  const response = await api.get(`/cluster/services/${entryId}`)
  return response.data
}

export const resetClusterOcrService = async (entryId: string): Promise<ServiceResetResponse> => {
  const response = await api.post(`/cluster/services/${entryId}/ocr/reset`)
  return response.data
}

export const fetchClusterServiceDocs = async (entryId: string): Promise<ServiceDocs> => {
  const response = await api.get(`/cluster/services/${entryId}/docs`)
  return response.data
}

export const fetchClusterServiceTokens = async (entryId: string): Promise<ServiceAccessToken[]> => {
  const response = await api.get(`/cluster/services/${entryId}/tokens`)
  return response.data.tokens || []
}

export const createClusterServiceToken = async (
  entryId: string,
  payload: ServiceTokenCreatePayload,
): Promise<ServiceAccessToken> => {
  const response = await api.post(`/cluster/services/${entryId}/tokens`, payload)
  return response.data.token
}

export const revealClusterServiceToken = async (
  entryId: string,
  tokenId: string,
): Promise<ServiceAccessToken> => {
  const response = await api.get(`/cluster/services/${entryId}/tokens/${tokenId}/reveal`)
  return response.data.token
}

export const updateClusterServiceToken = async (
  entryId: string,
  tokenId: string,
  payload: ServiceTokenUpdatePayload,
): Promise<ServiceAccessToken> => {
  const response = await api.patch(`/cluster/services/${entryId}/tokens/${tokenId}`, payload)
  return response.data.token
}

export const deleteClusterServiceToken = async (entryId: string, tokenId: string): Promise<void> => {
  await api.delete(`/cluster/services/${entryId}/tokens/${tokenId}`)
}
