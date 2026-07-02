import api, { getDeviceEntryPath } from '@/api'

export type DeviceAgentRequesterKind = 'device' | 'user' | 'system'
export type DeviceAgentRequestType = 'ask' | 'diagnose' | 'delegate' | 'repair'

export interface DeviceAgentConfig {
  enabled: boolean
  display_name: string
  device_role: string
  local_context: string
  responsibilities: string
  default_provider: string
  default_model: string
  updated_at?: number | null
}

export interface DeviceAgentManifest extends DeviceAgentConfig {
  device_id: string
  hostname: string
  configured_provider?: string
  configured_model?: string
  status: string
  ai_provider?: {
    provider: string
    model: string
    available: boolean
    configured: boolean
    kind?: string
    label?: string
    error?: string | null
  }
  agent: Record<string, any>
}

export interface DeviceAgentRequester {
  kind: DeviceAgentRequesterKind
  id: string
  display_name: string
}

export interface DeviceAgentTurn {
  id: string
  session_id: string
  role: string
  requester: DeviceAgentRequester
  request_type: DeviceAgentRequestType
  instruction: string
  context: Record<string, any>
  status: string
  stage: string
  stage_label: string
  queue_task_id?: string | null
  heartbeat_at?: number | null
  result_report: Record<string, any>
  error_message?: string | null
  created_at: number
  started_at?: number | null
  finished_at?: number | null
  updated_at: number
}

export interface DeviceAgentSession {
  id: string
  local_device_id: string
  peer_device_id: string
  peer_name: string
  requester_kind: DeviceAgentRequesterKind
  title: string
  status: string
  last_turn_id?: string | null
  created_at: number
  updated_at: number
  turns?: DeviceAgentTurn[]
}

export interface DeviceAgentSessionListResponse {
  items: DeviceAgentSession[]
}

export interface DeviceAgentSessionPayload {
  requester: DeviceAgentRequester
  request_type: DeviceAgentRequestType
  instruction: string
  context?: Record<string, any>
  title?: string
}

export const fetchDeviceAgentConfig = async (entryId: string): Promise<DeviceAgentConfig> => {
  const response = await api.get(getDeviceEntryPath(entryId, '/agent/config'))
  return response.data
}

export const saveDeviceAgentConfig = async (
  entryId: string,
  payload: Partial<DeviceAgentConfig>
): Promise<DeviceAgentConfig> => {
  const response = await api.put(getDeviceEntryPath(entryId, '/agent/config'), payload)
  return response.data
}

export const fetchDeviceAgentManifest = async (entryId: string): Promise<DeviceAgentManifest> => {
  const response = await api.get(getDeviceEntryPath(entryId, '/agent/manifest'))
  return response.data
}

export const fetchDeviceAgentSessions = async (
  entryId: string,
  limit = 10
): Promise<DeviceAgentSessionListResponse> => {
  const response = await api.get(getDeviceEntryPath(entryId, '/agent/sessions'), { params: { limit } })
  return response.data
}

export const createDeviceAgentSession = async (
  entryId: string,
  payload: DeviceAgentSessionPayload
): Promise<DeviceAgentSession> => {
  const response = await api.post(getDeviceEntryPath(entryId, '/agent/sessions'), payload)
  return response.data
}

export const appendDeviceAgentTurn = async (
  entryId: string,
  sessionId: string,
  payload: DeviceAgentSessionPayload
): Promise<DeviceAgentTurn> => {
  const response = await api.post(getDeviceEntryPath(entryId, `/agent/sessions/${encodeURIComponent(sessionId)}/turns`), payload)
  return response.data
}

export const fetchDeviceAgentSession = async (
  entryId: string,
  sessionId: string
): Promise<DeviceAgentSession> => {
  const response = await api.get(getDeviceEntryPath(entryId, `/agent/sessions/${encodeURIComponent(sessionId)}`))
  return response.data
}

export const fetchDeviceAgentTurn = async (
  entryId: string,
  turnId: string
): Promise<DeviceAgentTurn> => {
  const response = await api.get(getDeviceEntryPath(entryId, `/agent/turns/${encodeURIComponent(turnId)}`))
  return response.data
}
