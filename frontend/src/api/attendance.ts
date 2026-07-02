import axios from 'axios'

import api from '@/api'

export type AttendanceOrderLookupMode = 'hybrid' | 'db_only' | 'browser_only'
const ATTENDANCE_ORDER_REQUEST_TIMEOUT_MS = 620000
const ATTENDANCE_REFUNDED_CHECK_TIMEOUT_MS = 60000

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

export type AttendanceCourseDataStepDefaultRole = 'browser_device' | 'data_host'
export type AttendanceCourseDataStepEffectiveRole = AttendanceCourseDataStepDefaultRole | 'custom_device'

export interface AttendanceCourseDataStepRunnerConfig {
  step: number
  title: string
  default_role: AttendanceCourseDataStepDefaultRole
  effective_role: AttendanceCourseDataStepEffectiveRole
  configured_device_entry_id?: string | null
  effective_device_entry_id?: string | null
  device?: AttendanceExecutionDeviceSummary | null
  device_missing?: boolean
  device_inactive?: boolean
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
}

export interface AttendanceCourseDataFlowPayload {
  browser_device_entry_id?: string | null
  fallback_browser_device_entry_id?: string | null
  effective_browser_device_entry_id?: string | null
  data_device_entry_id?: string | null
  step_device_entry_ids?: Record<string, string | null>
  step_runners?: AttendanceCourseDataStepRunnerConfig[]
}

export interface AttendanceCourseDataFlowConfigResponse {
  course_data_flow: AttendanceCourseDataFlowPayload
  current_browser_device?: AttendanceExecutionDeviceSummary | null
  current_data_device?: AttendanceExecutionDeviceSummary | null
}

export interface AttendanceFeedbackCourseOption {
  name: string
  attendance_sheet_url?: string | null
}

export interface AttendanceFeedbackFormMeta {
  course_names: string[]
  course_options?: AttendanceFeedbackCourseOption[]
  course_names_updated_at?: number | null
  data_sheet_url?: string | null
}

export interface AttendanceConfigUpdateRequest {
  current_wjx_account_id?: string | null
  execution_device_entry_id?: string | null
  scan_reminder_users?: string[]
  order_lookup_mode?: AttendanceOrderLookupMode
  order_operation_password?: string | null
  clear_order_operation_password?: boolean
}

export interface AttendanceCourseDataFlowConfigUpdateRequest {
  browser_device_entry_id?: string | null
  data_device_entry_id?: string | null
  step_device_entry_ids?: Record<string, string | null>
}

export interface AttendanceAccountCreateRequest {
  login_username: string
  password: string
}

export interface AttendanceAccountUpdateRequest {
  login_username?: string
  password?: string
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

export type AttendanceOrderRefundQueryType = 'auto' | 'pay_order' | 'merchant_order' | 'refund_id'

export interface AttendanceOrderRefundDetailRequest {
  order_id: string
  query_type?: AttendanceOrderRefundQueryType
  execution_device_entry_id?: string | null
  login_users?: string[]
  persist_global_selection?: boolean
}

export interface AttendanceOrderRefundDetailItem {
  wechat_order_id: string
  merchant_order_id: string
  refund_id: string
  refund_amount: number
  refund_status: string
  applicant: string
  submitted_at: string
  completed_at: string
}

export interface AttendanceOrderRefundDetailSummary {
  order_id: string
  matched_order_id: string
  query_type: AttendanceOrderRefundQueryType
  row_count: number
  refund_amount_total: number
  wechat_order_id: string
  merchant_order_id: string
  refund_statuses: string[]
}

export interface AttendanceOrderRefundDetailResponse {
  execution_device_entry_id: string
  summary: AttendanceOrderRefundDetailSummary
  rows: AttendanceOrderRefundDetailItem[]
}

export interface AttendanceSheetRefundedCheckRequest {
  workbook_id?: number | null
  execution_device_entry_id?: string | null
  login_users?: string[]
  order_lookup_mode?: AttendanceOrderLookupMode
  persist_global_selection?: boolean
}

export type AttendanceSheetRefundedCheckStatus =
  | 'matched'
  | 'mismatch'
  | 'missing_registration'
  | 'missing_order'
  | 'missing_payment_refunded'

export interface AttendanceSheetRefundedCheckRow {
  row_number: number
  student_no: string
  student_name: string
  wechat_order_id: string
  merchant_order_id: string
  sheet_refunded_amount: string
  payment_refunded_amount: string
  order_amount: string
  status: AttendanceSheetRefundedCheckStatus
  message: string
}

export interface AttendanceSheetRefundedCheckSummary {
  total_count: number
  checked_count: number
  matched_count: number
  mismatch_count: number
  warning_count: number
}

export interface AttendanceSheetRefundedCheckResponse {
  execution_device_entry_id: string
  attendance_sheet_id: number
  registration_sheet_id?: number | null
  workbook_id?: number | null
  summary: AttendanceSheetRefundedCheckSummary
  rows: AttendanceSheetRefundedCheckRow[]
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

export type AttendanceCourseUpdateDataCourseType = 'fanbei' | 'nianzhu'

export interface AttendanceCourseUpdateDataRequest {
  course_type: AttendanceCourseUpdateDataCourseType
  attendance_sheet_id: number
  course_name: string
  include_frozen?: boolean
  workbook_id?: number | null
}

export interface AttendanceCourseUpdateDataResponse {
  step2: unknown
  step3: {
    message?: string
    updated_rows?: number
    updated_cells?: number
    styled_cells?: number
    rows?: number
  }
}

export async function runAttendanceCourseUpdateData(payload: AttendanceCourseUpdateDataRequest) {
  const response = await api.post(
    `/note-sheets/sheets/${payload.attendance_sheet_id}/attendance/course-update-data`,
    {
      course_type: payload.course_type,
      course_name: payload.course_name,
      include_frozen: payload.include_frozen === true,
    },
    {
      params: {
        workbook_id: payload.workbook_id ?? undefined,
      },
      timeout: ATTENDANCE_ORDER_REQUEST_TIMEOUT_MS,
    },
  )
  return response.data as AttendanceCourseUpdateDataResponse
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

export interface AttendanceFeedbackHistoryResponse {
  items: AttendanceWjxDataItem[]
  total: number
}

export interface AttendanceWjxDataSheetLocation {
  workbook_id: number
  sheet_id: number
  path: string
}

export interface AttendanceFeedbackSubmitRequest {
  course_name: string
  student_id_text: string
  student_name: string
  correction_request: string
  extra_note?: string
  workbook_id?: number | null
  sheet_id?: number | null
}

export interface AttendanceWjxDataUpdateRequest {
  process_status?: string
  process_note?: string
  match_result?: Record<string, unknown>
  revision_result?: Record<string, unknown>
}

export interface AttendanceWjxAiPrecheckRequest {
  persist?: boolean
  use_codex_cli?: boolean
  auto_repair?: boolean
  repair_with_remote_browser?: boolean
}

export interface AttendanceWjxAiPrecheckResponse {
  item: AttendanceWjxDataItem
  precheck: Record<string, unknown>
}

export interface AttendanceSheetDocument {
  id: number
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

export interface AttendanceHeaderToolGroup {
  label: string
  kind: 'clockin' | 'week'
  start_column: number
  colspan: number
  background_color: string
  child_background_color: string
  week_index?: number | null
}

export interface AttendanceHeaderToolCell {
  label: string
  url: string
  kind: 'clockin' | 'lesson'
  column_index: number
  group_label: string
  background_color: string
  source_id?: number | null
  lesson_id2: string
  week_index?: number | null
}

export interface AttendanceHeaderToolResponse {
  course_name: string
  course_type: string
  groups: AttendanceHeaderToolGroup[]
  cells: AttendanceHeaderToolCell[]
  rows: string[][]
  plain_text: string
  document_json: Record<string, unknown>
}

export async function fetchAttendanceConfig() {
  const response = await api.get<AttendanceConfigResponse>('/attendance/config')
  return response.data
}

export async function fetchAttendanceCourseDataFlowConfig() {
  const response = await api.get<AttendanceCourseDataFlowConfigResponse>('/attendance/course-data-flow/config')
  return response.data
}

export async function generateAttendanceHeaderTool(courseName: string) {
  const response = await api.post<AttendanceHeaderToolResponse>('/attendance/header-tool/generate', {
    course_name: courseName,
  })
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

export async function fetchAttendanceSheetDocumentById(sheetId: number) {
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

export async function executeAttendanceOrder(payload: AttendanceOrderExecuteRequest) {
  const response = await api.post<AttendanceOrderExecuteResponse>('/attendance/order-execute', payload, {
    timeout: ATTENDANCE_ORDER_REQUEST_TIMEOUT_MS,
  })
  return response.data
}

export async function fetchAttendanceOrderRefundDetails(payload: AttendanceOrderRefundDetailRequest) {
  const response = await api.post<AttendanceOrderRefundDetailResponse>('/attendance/order-refund-details', payload, {
    timeout: ATTENDANCE_ORDER_REQUEST_TIMEOUT_MS,
  })
  return {
    ...response.data,
    summary: {
      ...response.data.summary,
      order_id: normalizeAttendanceOrderId(response.data.summary?.order_id),
      matched_order_id: normalizeAttendanceOrderId(response.data.summary?.matched_order_id),
      wechat_order_id: normalizeAttendanceOrderId(response.data.summary?.wechat_order_id),
      merchant_order_id: normalizeAttendanceOrderId(response.data.summary?.merchant_order_id),
      refund_amount_total: Number(response.data.summary?.refund_amount_total || 0),
      row_count: Number(response.data.summary?.row_count || 0),
      refund_statuses: Array.isArray(response.data.summary?.refund_statuses)
        ? response.data.summary.refund_statuses.filter((item): item is string => typeof item === 'string' && !!item.trim())
        : [],
    },
    rows: (response.data.rows || []).map((item) => ({
      ...item,
      wechat_order_id: normalizeAttendanceOrderId(item.wechat_order_id),
      merchant_order_id: normalizeAttendanceOrderId(item.merchant_order_id),
      refund_id: normalizeAttendanceOrderId(item.refund_id),
      refund_amount: Number(item.refund_amount || 0),
      refund_status: String(item.refund_status || ''),
      applicant: String(item.applicant || ''),
      submitted_at: String(item.submitted_at || ''),
      completed_at: String(item.completed_at || ''),
    })),
  }
}

export async function checkAttendanceSheetRefundedAmounts(sheetId: number, payload: AttendanceSheetRefundedCheckRequest = {}) {
  const response = await api.post<AttendanceSheetRefundedCheckResponse>(
    `/attendance/sheets/${sheetId}/check-refunded`,
    payload,
    { timeout: ATTENDANCE_REFUNDED_CHECK_TIMEOUT_MS },
  )
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

export async function submitAttendanceFeedback(payload: AttendanceFeedbackSubmitRequest) {
  const response = await api.post<AttendanceWjxDataItem>('/attendance/wjx-feedback/submissions', payload)
  return response.data
}

export async function fetchAttendanceFeedbackHistory(params: {
  course_name: string
  student_id_text?: string
  student_name?: string
  limit?: number
}) {
  const response = await api.get<AttendanceFeedbackHistoryResponse>('/attendance/wjx-feedback/history', { params })
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

export async function fetchAttendanceWjxDataSheetLocation() {
  const response = await api.get<AttendanceWjxDataSheetLocation>('/attendance/wjx-data/sheet')
  return response.data
}

export async function updateAttendanceWjxData(entryId: number, payload: AttendanceWjxDataUpdateRequest) {
  const response = await api.patch<AttendanceWjxDataItem>(`/attendance/wjx-data/${entryId}`, payload)
  return response.data
}

export async function runAttendanceWjxDataAiPrecheck(entryId: number, payload?: AttendanceWjxAiPrecheckRequest) {
  const response = await api.post<AttendanceWjxAiPrecheckResponse>(
    `/attendance/wjx-data/${entryId}/ai-precheck`,
    payload ?? {},
    { timeout: 180_000 },
  )
  return response.data
}

export async function deleteAttendanceWjxData(entryId: number) {
  await api.delete(`/attendance/wjx-data/${entryId}`)
}
