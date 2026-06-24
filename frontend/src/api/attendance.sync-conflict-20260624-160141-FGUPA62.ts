import api from '@/api'

export interface AttendanceExecutionDeviceSummary {
  entry_id: string
  user_id: number
  device_id: string
  name: string
  mode: 'local' | 'remote'
  server_url?: string | null
  is_active: boolean
  order_index: number
  created_at: number
  updated_at: number
}

export interface AttendanceAccount {
  id: string
  provider: string
  name: string
  login_username: string
  password?: string
  created_by_user_id?: number | null
  updated_by_user_id?: number | null
  created_at: number
  updated_at: number
}

export interface AttendanceTemplate {
  id: string
  provider: string
  name: string
  activity_id: string
  is_active: boolean
  created_by_user_id?: number | null
  updated_by_user_id?: number | null
  created_at: number
  updated_at: number
}

export interface AttendanceServicePayload {
  current_wjx_account_id?: string | null
  execution_device_entry_id?: string | null
  granted_user_ids: number[]
  created_by_user_id?: number | null
  updated_by_user_id?: number | null
  created_at: number
  updated_at: number
}

export interface AttendanceConfigResponse {
  service: AttendanceServicePayload
  current_account?: AttendanceAccount | null
  current_execution_device?: AttendanceExecutionDeviceSummary | null
  fixed_wjx_template: {
    id: string
    name: string
    activity_id: string
    design_url: string
  }
}

export interface AttendanceRun {
  id: string
  template_id: string
  account_id: string
  execution_device_entry_id: string
  requested_by_user_id?: number | null
  action: 'inspect' | 'apply'
  status: 'pending' | 'running' | 'completed' | 'failed'
  request: Record<string, unknown>
  result: Record<string, unknown>
  error_message?: string | null
  created_at: number
  finished_at?: number | null
  updated_at: number
}

export interface AttendanceConfigUpdateRequest {
  current_wjx_account_id?: string | null
  execution_device_entry_id?: string | null
}

export interface AttendanceAccountCreateRequest {
  login_username: string
  password: string
}

export interface AttendanceAccountUpdateRequest {
  login_username?: string
  password?: string
}

export interface AttendanceTemplateCreateRequest {
  name: string
  activity_id: string
  is_active?: boolean
}

export interface AttendanceTemplateUpdateRequest {
  name?: string
  activity_id?: string
  is_active?: boolean
}

export interface AttendanceRunCreateRequest {
  template_id?: string | null
  action: 'inspect' | 'apply'
  account_id?: string | null
  execution_device_entry_id?: string | null
  hide?: string[]
  add?: string[]
  persist_global_selection?: boolean
}

export async function fetchAttendanceConfig() {
  const response = await api.get<AttendanceConfigResponse>('/attendance/config')
  return response.data
}

export async function updateAttendanceConfig(payload: AttendanceConfigUpdateRequest) {
  const response = await api.put<AttendanceConfigResponse>('/attendance/config', payload)
  return response.data
}

export async function fetchAttendanceAccounts() {
  const response = await api.get<{ items: AttendanceAccount[] }>('/attendance/accounts')
  return response.data.items
}

export async function createAttendanceAccount(payload: AttendanceAccountCreateRequest) {
  const response = await api.post<AttendanceAccount>('/attendance/accounts', payload)
  return response.data
}

export async function updateAttendanceAccount(accountId: string, payload: AttendanceAccountUpdateRequest) {
  const response = await api.put<AttendanceAccount>(`/attendance/accounts/${accountId}`, payload)
  return response.data
}

export async function deleteAttendanceAccount(accountId: string) {
  await api.delete(`/attendance/accounts/${accountId}`)
}

export async function fetchAttendanceTemplates() {
  const response = await api.get<{ items: AttendanceTemplate[] }>('/attendance/templates')
  return response.data.items
}

export async function createAttendanceTemplate(payload: AttendanceTemplateCreateRequest) {
  const response = await api.post<AttendanceTemplate>('/attendance/templates', payload)
  return response.data
}

export async function updateAttendanceTemplate(templateId: string, payload: AttendanceTemplateUpdateRequest) {
  const response = await api.put<AttendanceTemplate>(`/attendance/templates/${templateId}`, payload)
  return response.data
}

export async function deleteAttendanceTemplate(templateId: string) {
  await api.delete(`/attendance/templates/${templateId}`)
}

export async function startAttendanceRun(payload: AttendanceRunCreateRequest) {
  const response = await api.post<AttendanceRun>('/attendance/wjx-runs', payload)
  return response.data
}

export async function fetchAttendanceRun(runId: string) {
  const response = await api.get<AttendanceRun>(`/attendance/wjx-runs/${runId}`)
  return response.data
}
