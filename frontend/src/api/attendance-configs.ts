import api from '@/api'

import type {
  AttendanceAccount,
  AttendanceConfigResponse,
  AttendanceConfigUpdateRequest,
  AttendanceCourseDataFlowConfigResponse,
  AttendanceCourseDataFlowConfigUpdateRequest,
  AttendanceCourseDataStepRunnerConfig,
  AttendanceOrderLookupMode,
} from './attendance'

export type {
  AttendanceAccount,
  AttendanceConfigResponse,
  AttendanceConfigUpdateRequest,
  AttendanceCourseDataFlowConfigResponse,
  AttendanceCourseDataFlowConfigUpdateRequest,
  AttendanceCourseDataStepRunnerConfig,
  AttendanceOrderLookupMode,
}

export interface AttendanceConfigBootstrapDevice {
  id: string
  user_id: number
  device_id: string
  mode: 'local' | 'remote'
  alias: string
  name: string
  server_url?: string | null
  is_active: boolean
  created_at: number
  updated_at: number
  device?: {
    id: string
    name: string
    type: string
    server_url?: string | null
    order_index: number
    created_at: number
    updated_at: number
  }
}

export interface AttendanceConfigsBootstrapResponse {
  config: AttendanceConfigResponse
  course_data_flow_config: AttendanceCourseDataFlowConfigResponse
  accounts: AttendanceAccount[]
  devices: AttendanceConfigBootstrapDevice[]
}

export async function fetchAttendanceConfigsBootstrap() {
  const response = await api.get<AttendanceConfigsBootstrapResponse>('/attendance/config/bootstrap')
  return response.data
}

export async function fetchAttendanceConfig() {
  const response = await api.get<AttendanceConfigResponse>('/attendance/config')
  return response.data
}

export async function fetchAttendanceCourseDataFlowConfig() {
  const response = await api.get<AttendanceCourseDataFlowConfigResponse>('/attendance/course-data-flow/config')
  return response.data
}

export async function updateAttendanceConfig(payload: AttendanceConfigUpdateRequest) {
  const response = await api.put<AttendanceConfigResponse>('/attendance/config', payload)
  return response.data
}

export async function updateAttendanceCourseDataFlowConfig(payload: AttendanceCourseDataFlowConfigUpdateRequest) {
  const response = await api.put<AttendanceCourseDataFlowConfigResponse>('/attendance/course-data-flow/config', payload)
  return response.data
}

export async function fetchAttendanceAccounts() {
  const response = await api.get<{ items: AttendanceAccount[] }>('/attendance/accounts')
  return response.data.items
}

export async function createAttendanceAccount(payload: {
  login_username: string
  password: string
}) {
  const response = await api.post<AttendanceAccount>('/attendance/accounts', payload)
  return response.data
}

export async function updateAttendanceAccount(accountId: string, payload: {
  login_username?: string
  password?: string
}) {
  const response = await api.put<AttendanceAccount>(`/attendance/accounts/${accountId}`, payload)
  return response.data
}

export async function deleteAttendanceAccount(accountId: string) {
  await api.delete(`/attendance/accounts/${accountId}`)
}
