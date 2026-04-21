import axios from 'axios'

import api from '@/api'

export type AttendanceOrderLookupMode = 'hybrid' | 'db_only' | 'browser_only'
const ATTENDANCE_ORDER_REQUEST_TIMEOUT_MS = 620000
const ATTENDANCE_WJX_DATA_REQUEST_TIMEOUT_MS = 620000

function normalizeAttendanceTimestamp(value: unknown): number {
  const numeric = typeof value === 'number' ? value : Number(value)
  if (!Number.isFinite(numeric) || numeric <= 0) return 0
  return numeric < 10000000000 ? numeric * 1000 : numeric
}

function normalizeAttendanceOrderId(value: unknown): string {
  return String(value ?? '').replace(/^[`']+/, '').trim()
}

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
  scan_reminder_users: string[]
  order_lookup_mode: AttendanceOrderLookupMode
  order_operation_password_configured: boolean
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
    view_url: string
    fill_url: string
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

export interface AttendanceFeedbackFormMeta {
  course_names: string[]
  course_names_updated_at?: number | null
  template: {
    id: string
    name: string
    activity_id: string
    design_url: string
    view_url: string
    fill_url: string
  }
}

export interface AttendanceFeedbackFormMetaUpdateRequest {
  course_names: string[]
}

export interface AttendanceConfigUpdateRequest {
  current_wjx_account_id?: string | null
  execution_device_entry_id?: string | null
  scan_reminder_users?: string[]
  order_lookup_mode?: AttendanceOrderLookupMode
  order_operation_password?: string | null
  clear_order_operation_password?: boolean
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

export interface AttendanceOrderRow {
  编号?: string
  学员名称?: string
  微信支付订单号?: string
  商户订单号?: string
  订单金额?: string | number
  已返款?: string | number
  退款原因?: string
  退款额度?: string | number
  执行退款?: string
}

export interface AttendanceOrderSummary {
  input_count: number
  processed_count: number
  refunded_count: number
  error_count: number
  skipped_blank_count: number
}

export interface AttendanceOrderExecuteRequest {
  action: 'inspect' | 'refund'
  rows: AttendanceOrderRow[]
  execution_device_entry_id?: string | null
  login_users?: string[]
  order_lookup_mode?: AttendanceOrderLookupMode
  persist_global_selection?: boolean
}

export interface AttendanceOrderExecuteResponse {
  execution_device_entry_id: string
  action: 'inspect' | 'refund'
  rows: AttendanceOrderRow[]
  summary: AttendanceOrderSummary
}

export interface AttendanceOrderRefundHistoryForegroundColors {
  created_day?: string | null
  operator?: string | null
}

export interface AttendanceOrderRefundHistoryItem {
  id: string
  requested_by_user_id?: number | null
  operator_username: string
  operator_nickname: string
  operator_name: string
  execution_device_entry_id?: string | null
  student_name: string
  wechat_order_id: string
  merchant_order_id: string
  order_amount: string
  refunded_amount: string
  remaining_amount: string
  refund_amount: string
  refund_reason: string
  result_text: string
  created_at: number
  foreground_colors: AttendanceOrderRefundHistoryForegroundColors
}

export interface AttendanceOrderRefundHistoryPage {
  items: AttendanceOrderRefundHistoryItem[]
  total: number
  page: number
  page_size: number
}

export interface AttendanceWjxDataSyncState {
  activity_id: string
  template_id: string
  last_max_seq: number
  last_incremental_count: number
  stored_count: number
  last_used_all_pages: boolean
  last_sync_at?: number | null
  last_success_at?: number | null
  last_error?: string | null
  execution_device_entry_id?: string | null
  created_by_user_id?: number | null
  updated_by_user_id?: number | null
  created_at: number
  updated_at: number
}

export interface AttendanceWjxDataForegroundColors {
  submitted?: string | null
  course?: string | null
  student?: string | null
}

export interface AttendanceWjxDataItem {
  id: number
  activity_id: string
  seq: number
  submitted_at_text: string
  duration_text: string
  source: string
  source_detail: string
  source_ip: string
  course_name: string
  student_id_text: string
  student_name: string
  foreground_colors: AttendanceWjxDataForegroundColors
  correction_request: string
  extra_note: string
  process_status: string
  process_note: string
  match_result: Record<string, unknown>
  revision_result: Record<string, unknown>
  raw_row: Record<string, unknown>
  synced_at: number
  created_at: number
  updated_at: number
}

export interface AttendanceWjxDataPage {
  items: AttendanceWjxDataItem[]
  total: number
  page: number
  page_size: number
  sync_state?: AttendanceWjxDataSyncState | null
  template: {
    id: string
    name: string
    activity_id: string
    design_url: string
    view_url: string
    fill_url: string
  }
}

export interface AttendanceWjxDataSyncRequest {
  template_id?: string | null
  account_id?: string | null
  execution_device_entry_id?: string | null
  persist_global_selection?: boolean
}

export interface AttendanceFeedbackSubmitRequest {
  course_name: string
  student_id_text: string
  student_name: string
  correction_request: string
  extra_note?: string
}

export interface AttendanceWjxDataSyncResponse {
  template: AttendanceWjxDataPage['template']
  execution_device_entry_id: string
  inserted_count: number
  updated_count: number
  latest_max_seq: number
  recent_count: number
  fetched_count: number
  incremental_count: number
  used_all_pages: boolean
  sync_state?: AttendanceWjxDataSyncState | null
}

export interface AttendanceWjxDataUpdateRequest {
  process_status?: string
  process_note?: string
  match_result?: Record<string, unknown>
  revision_result?: Record<string, unknown>
}

export interface AttendanceSheetDocument {
  id: string
  scope: string
  owner_type: string
  owner_key: string
  sheet_key: string
  title: string
  engine: string
  document_json: Record<string, unknown>
  version: number
  created_by_user_id?: number | null
  updated_by_user_id?: number | null
  created_at: number
  updated_at: number
}

export interface AttendanceSheetDocumentUpsertRequest {
  owner_type: string
  owner_key: string
  sheet_key: string
  title?: string
  engine?: 'handsontable'
  document_json: Record<string, unknown>
}

export async function fetchAttendanceConfig() {
  const response = await api.get<AttendanceConfigResponse>('/attendance/config')
  return response.data
}

export async function fetchAttendanceSheetDocumentByOwner(params: {
  owner_type: string
  owner_key: string
  sheet_key: string
}) {
  try {
    const response = await api.get<AttendanceSheetDocument>('/attendance/sheets/by-owner', {
      params,
    })
    return response.data
  } catch (error) {
    if (axios.isAxiosError(error) && error.response?.status === 404) {
      return null
    }
    throw error
  }
}

export async function upsertAttendanceSheetDocument(payload: AttendanceSheetDocumentUpsertRequest) {
  const response = await api.put<AttendanceSheetDocument>('/attendance/sheets', payload)
  return response.data
}

export async function fetchAttendanceSheetDocumentById(sheetId: string) {
  try {
    const response = await api.get<AttendanceSheetDocument>(`/attendance/sheets/${sheetId}`)
    return response.data
  } catch (error) {
    if (axios.isAxiosError(error) && error.response?.status === 404) {
      return null
    }
    throw error
  }
}

export async function fetchAttendanceFeedbackFormMeta() {
  const response = await api.get<AttendanceFeedbackFormMeta>('/attendance/wjx-feedback-form')
  return response.data
}

export async function updateAttendanceFeedbackFormMeta(payload: AttendanceFeedbackFormMetaUpdateRequest) {
  const response = await api.put<AttendanceFeedbackFormMeta>('/attendance/wjx-feedback-form', payload)
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

export async function executeAttendanceOrder(payload: AttendanceOrderExecuteRequest) {
  const response = await api.post<AttendanceOrderExecuteResponse>('/attendance/order-execute', payload, {
    timeout: ATTENDANCE_ORDER_REQUEST_TIMEOUT_MS,
  })
  return response.data
}

export async function fetchAttendanceOrderRefundHistory(params?: { page?: number; page_size?: number }) {
  const response = await api.get<AttendanceOrderRefundHistoryPage>('/attendance/order-refund-history', {
    params,
  })
  return {
    ...response.data,
    items: (response.data.items || []).map((item) => ({
      ...item,
      wechat_order_id: normalizeAttendanceOrderId(item.wechat_order_id),
      merchant_order_id: normalizeAttendanceOrderId(item.merchant_order_id),
      created_at: normalizeAttendanceTimestamp(item.created_at),
      foreground_colors: item.foreground_colors || {},
    })),
  }
}

export async function syncAttendanceWjxData(payload: AttendanceWjxDataSyncRequest = {}) {
  const response = await api.post<AttendanceWjxDataSyncResponse>('/attendance/wjx-data/sync', payload, {
    timeout: ATTENDANCE_WJX_DATA_REQUEST_TIMEOUT_MS,
  })
  return response.data
}

export async function submitAttendanceFeedback(payload: AttendanceFeedbackSubmitRequest) {
  const response = await api.post<AttendanceWjxDataItem>('/attendance/wjx-feedback/submissions', payload)
  return response.data
}

export async function fetchAttendanceWjxData(params?: {
  page?: number
  page_size?: number
  process_status?: string
  keyword?: string
  template_id?: string | null
}) {
  const response = await api.get<AttendanceWjxDataPage>('/attendance/wjx-data', { params })
  return response.data
}

export async function updateAttendanceWjxData(entryId: number, payload: AttendanceWjxDataUpdateRequest) {
  const response = await api.patch<AttendanceWjxDataItem>(`/attendance/wjx-data/${entryId}`, payload)
  return response.data
}

export async function deleteAttendanceWjxData(entryId: number) {
  await api.delete(`/attendance/wjx-data/${entryId}`)
}
