import axios from 'axios'

import api from '@/api'

export interface WorkbookRefItem {
  id: number
  title: string
}

export type NoteSheetResourceRole = 'none' | 'deny' | 'viewer' | 'editor' | 'manager'
export type NoteSheetResourceType = 'workbook' | 'sheet'
export type NoteSheetAccessSubjectType = 'anonymous' | 'user'

export interface NoteSheetAccessCapabilities {
  can_read: boolean
  can_use_local_view: boolean
  can_edit_data: boolean
  editable_data_columns?: number[]
  can_edit_config: boolean
  can_run_sheet_actions: boolean
  can_manage_access: boolean
}

export interface NoteSheetResourceAccess {
  role: NoteSheetResourceRole
  capabilities: NoteSheetAccessCapabilities
}

export interface NoteSheetResourceAccessGrantItem {
  subject_type: NoteSheetAccessSubjectType
  subject_key: string
  subject_user_id?: number | null
  username: string
  nickname: string
  role: Exclude<NoteSheetResourceRole, 'none'>
}

export interface NoteSheetResourceAccessGrantUpdate {
  subject_type: NoteSheetAccessSubjectType
  username?: string
  subject_user_id?: number | null
  role: NoteSheetResourceRole
}

export interface NoteSheetResourceAccessResponse {
  resource_type: NoteSheetResourceType
  resource_id: number
  access: NoteSheetResourceAccess
  grants: NoteSheetResourceAccessGrantItem[]
}

export interface NoteSheetSummary {
  id: number
  title: string
  engine: string
  scope: string
  owner_user_id?: number | null
  created_by_user_id?: number | null
  updated_by_user_id?: number | null
  created_at: number
  updated_at: number
  workbook_items: WorkbookRefItem[]
  access?: NoteSheetResourceAccess | null
}

export interface NoteSheetDetail extends NoteSheetSummary {
  owner_type: string
  owner_key: string
  sheet_key: string
  version: number
  document_json: Record<string, unknown>
  pagination?: NoteSheetPaginationState | null
}

export interface NoteSheetPaginationState {
  page: number
  page_size: number
  total_rows: number
  page_count: number
  row_offset: number
  loaded_row_count: number
}

export interface WorkbookSummary {
  id: number
  title: string
  owner_user_id?: number | null
  created_by_user_id?: number | null
  updated_by_user_id?: number | null
  created_at: number
  updated_at: number
  sheet_count: number
  access?: NoteSheetResourceAccess | null
}

export interface WorkbookDetail extends WorkbookSummary {
  sheets: NoteSheetSummary[]
}

export interface NoteSheetCreateRequest {
  title?: string
  workbook_id?: number | null
  document_json?: Record<string, unknown>
}

export interface NoteSheetUpdateRequest {
  title?: string
  document_json?: Record<string, unknown>
  page_patch?: {
    page: number
    page_size: number
    row_offset: number
    loaded_row_count: number
  }
}

export interface NoteSheetSortRequest {
  column_index: number
  direction?: 'asc' | 'desc'
}

export interface NoteSheetExcelImportResponse {
  sheet: NoteSheetDetail
  imported_count: number
  preserved_row_count: number
  extra_columns: string[]
  warnings: string[]
  mapping_notes: string[]
}

const NOTE_SHEET_EXCEL_IMPORT_TIMEOUT_MS = 930_000
const NOTE_SHEET_ACTION_TIMEOUT_MS = 180_000

export interface NoteSheetRegistrationMatchResponse {
  sheet: NoteSheetDetail
  action: string
  updated_count: number
  skipped_count: number
  error_count: number
  message: string
}

export type NoteSheetRegistrationMatchRunStatus =
  | 'idle'
  | 'pending'
  | 'running'
  | 'completed'
  | 'failed'
  | 'cancelled'

export interface NoteSheetRegistrationMatchRunResponse {
  run_id: string
  action: string
  sheet_id: number
  workbook_id?: number | null
  status: NoteSheetRegistrationMatchRunStatus
  use_browser_fallback: boolean
  already_running: boolean
  cancel_requested: boolean
  queued_at?: number | null
  started_at?: number | null
  finished_at?: number | null
  total_count: number
  processed_count: number
  updated_count: number
  skipped_count: number
  error_count: number
  message: string
  error_message?: string | null
  sheet?: NoteSheetDetail | null
}

export interface AttendanceTemplateActionItem {
  course_type: string
  course_name: string
  target_date: string
  row_index?: number | null
  reason: string
}

export interface AttendanceTemplateGenerationResponse {
  sheet: NoteSheetDetail
  generated: AttendanceTemplateActionItem[]
  skipped: AttendanceTemplateActionItem[]
}

export interface AttendanceCourseTemplateGenerationRequest {
  row_index?: number
  course_type?: string
  target_date?: string
  target_year?: number
  target_month?: number
}

export interface AttendanceCompletionRequest {
  row_index: number
  completion_date?: string
}

export interface AttendanceCompletionResponse {
  sheet: NoteSheetDetail
  row_index: number
}

export interface AttendanceCourseScriptStatusItem {
  row_index: number
  course_type: string
  course_name: string
  online_sheet: string
  url: string
  target_stem: string
  target_filename: string
  exists: boolean
  existing_path: string
  can_generate: boolean
  reason: string
}

export interface AttendanceCourseScriptStatusesResponse {
  statuses: AttendanceCourseScriptStatusItem[]
}

export interface AttendanceCourseScriptGenerationRequest {
  row_index: number
}

export interface AttendanceCourseScriptGenerationResponse {
  status: AttendanceCourseScriptStatusItem
  source_filename: string
  source_path: string
  created_path: string
}

export interface AttendanceCourseScriptOrganizeItem {
  row_index: number
  course_type: string
  online_sheet: string
  target_filename: string
  completed: boolean
  source_path: string
  target_path: string
  reason: string
}

export interface AttendanceCourseScriptOrganizeResponse {
  moved: AttendanceCourseScriptOrganizeItem[]
  skipped: AttendanceCourseScriptOrganizeItem[]
}

export type AttendanceLinkCountFieldKey = 'lesson_links' | 'clockin_links'

export interface AttendanceLinkCountUpdateRequest {
  field_key: AttendanceLinkCountFieldKey
  row_index?: number
}

export interface AttendanceLinkCountUpdateItem {
  row_index: number
  course_name: string
  lookup_name: string
  value: string
  total_count: number
  linked_count: number
  reason: string
}

export interface AttendanceLinkCountUpdateResponse {
  sheet: NoteSheetDetail
  updated: AttendanceLinkCountUpdateItem[]
  skipped: AttendanceLinkCountUpdateItem[]
}

type NoteSheetResourceRequestOptions = {
  workbookId?: number | null
}

type NoteSheetRegistrationUserMatchOptions = NoteSheetResourceRequestOptions & {
  useBrowserFallback?: boolean
}

export async function fetchNoteSheets() {
  const response = await api.get<NoteSheetSummary[]>('/note-sheets/sheets')
  return response.data
}

export async function createNoteSheet(payload: NoteSheetCreateRequest) {
  const response = await api.post<NoteSheetDetail>('/note-sheets/sheets', payload)
  return response.data
}

export async function fetchNoteSheet(
  sheetId: number,
  options?: { page?: number; pageSize?: number; paginate?: boolean; workbookId?: number | null },
) {
  try {
    const response = await api.get<NoteSheetDetail>(`/note-sheets/sheets/${sheetId}`, {
      params: {
        page: options?.page,
        page_size: options?.pageSize,
        paginate: options?.paginate,
        workbook_id: options?.workbookId ?? undefined,
      },
    })
    return response.data
  } catch (error) {
    if (axios.isAxiosError(error) && error.response?.status === 404) {
      return null
    }
    throw error
  }
}

export async function updateNoteSheet(
  sheetId: number,
  payload: NoteSheetUpdateRequest,
  options?: NoteSheetResourceRequestOptions,
) {
  const response = await api.put<NoteSheetDetail>(`/note-sheets/sheets/${sheetId}`, payload, {
    params: {
      workbook_id: options?.workbookId ?? undefined,
    },
  })
  return response.data
}

export async function sortNoteSheet(
  sheetId: number,
  payload: NoteSheetSortRequest,
  options?: NoteSheetResourceRequestOptions,
) {
  const response = await api.post<NoteSheetDetail>(`/note-sheets/sheets/${sheetId}/sort`, payload, {
    params: {
      workbook_id: options?.workbookId ?? undefined,
    },
  })
  return response.data
}

export async function importNoteSheetFromExcelReset(
  sheetId: number,
  payload: { file: File; instruction?: string; actionCell?: { documentRow: number; column: number } },
  options?: NoteSheetResourceRequestOptions,
) {
  const formData = new FormData()
  formData.append('file', payload.file)
  formData.append('instruction', payload.instruction ?? '')
  if (payload.actionCell) {
    formData.append('action_document_row', String(payload.actionCell.documentRow))
    formData.append('action_column', String(payload.actionCell.column))
  }
  const response = await api.post<NoteSheetExcelImportResponse>(
    `/note-sheets/sheets/${sheetId}/import-excel-reset`,
    formData,
    {
      params: {
        workbook_id: options?.workbookId ?? undefined,
      },
      headers: {
        'Content-Type': 'multipart/form-data',
      },
      timeout: NOTE_SHEET_EXCEL_IMPORT_TIMEOUT_MS,
    },
  )
  return response.data
}

export async function updateNoteSheetRegistrationOrderMatch(
  sheetId: number,
  options?: NoteSheetResourceRequestOptions,
) {
  const response = await api.post<NoteSheetRegistrationMatchResponse>(
    `/note-sheets/sheets/${sheetId}/registration/update-order-match`,
    {},
    {
      params: {
        workbook_id: options?.workbookId ?? undefined,
      },
      timeout: NOTE_SHEET_ACTION_TIMEOUT_MS,
    },
  )
  return response.data
}

export async function updateNoteSheetRegistrationUserMatch(
  sheetId: number,
  options?: NoteSheetRegistrationUserMatchOptions,
) {
  const response = await api.post<NoteSheetRegistrationMatchResponse>(
    `/note-sheets/sheets/${sheetId}/registration/update-user-match`,
    {},
    {
      params: {
        workbook_id: options?.workbookId ?? undefined,
        use_browser_fallback: options?.useBrowserFallback ?? false,
      },
      timeout: NOTE_SHEET_ACTION_TIMEOUT_MS,
    },
  )
  return response.data
}

export async function startNoteSheetRegistrationMatchRun(
  sheetId: number,
  payload: {
    action: string
    useBrowserFallback?: boolean
    forceRestart?: boolean
  },
  options?: NoteSheetResourceRequestOptions,
) {
  const response = await api.post<NoteSheetRegistrationMatchRunResponse>(
    `/note-sheets/sheets/${sheetId}/registration/match-runs`,
    {
      action: payload.action,
      use_browser_fallback: payload.useBrowserFallback ?? false,
      force_restart: payload.forceRestart ?? false,
    },
    {
      params: {
        workbook_id: options?.workbookId ?? undefined,
      },
      timeout: NOTE_SHEET_ACTION_TIMEOUT_MS,
    },
  )
  return response.data
}

export async function fetchNoteSheetActiveRegistrationMatchRun(
  sheetId: number,
  action: string,
  options?: NoteSheetResourceRequestOptions,
) {
  const response = await api.get<NoteSheetRegistrationMatchRunResponse>(
    `/note-sheets/sheets/${sheetId}/registration/match-runs/active`,
    {
      params: {
        workbook_id: options?.workbookId ?? undefined,
        action,
      },
    },
  )
  return response.data
}

export async function fetchNoteSheetRegistrationMatchRun(
  sheetId: number,
  runId: string,
  options?: NoteSheetResourceRequestOptions,
) {
  const response = await api.get<NoteSheetRegistrationMatchRunResponse>(
    `/note-sheets/sheets/${sheetId}/registration/match-runs/${runId}`,
    {
      params: {
        workbook_id: options?.workbookId ?? undefined,
      },
    },
  )
  return response.data
}

export async function generateAttendanceNextMonthTemplates(sheetId: number, options?: NoteSheetResourceRequestOptions) {
  const response = await api.post<AttendanceTemplateGenerationResponse>(
    `/note-sheets/sheets/${sheetId}/attendance-summary/generate-next-month-templates`,
    {},
    {
      params: {
        workbook_id: options?.workbookId ?? undefined,
      },
    },
  )
  return response.data
}

export async function generateAttendanceCourseTemplate(
  sheetId: number,
  payload: AttendanceCourseTemplateGenerationRequest,
  options?: NoteSheetResourceRequestOptions,
) {
  const response = await api.post<AttendanceTemplateGenerationResponse>(
    `/note-sheets/sheets/${sheetId}/attendance-summary/generate-course-template`,
    payload,
    {
      params: {
        workbook_id: options?.workbookId ?? undefined,
      },
    },
  )
  return response.data
}

export async function setAttendanceRowCompleted(
  sheetId: number,
  payload: AttendanceCompletionRequest,
  options?: NoteSheetResourceRequestOptions,
) {
  const response = await api.post<AttendanceCompletionResponse>(
    `/note-sheets/sheets/${sheetId}/attendance-summary/set-completed`,
    payload,
    {
      params: {
        workbook_id: options?.workbookId ?? undefined,
      },
    },
  )
  return response.data
}

export async function fetchAttendanceCourseScriptStatuses(
  sheetId: number,
  options?: NoteSheetResourceRequestOptions,
) {
  const response = await api.get<AttendanceCourseScriptStatusesResponse>(
    `/note-sheets/sheets/${sheetId}/attendance-summary/course-script-statuses`,
    {
      params: {
        workbook_id: options?.workbookId ?? undefined,
      },
    },
  )
  return response.data
}

export async function generateAttendanceCourseScript(
  sheetId: number,
  payload: AttendanceCourseScriptGenerationRequest,
  options?: NoteSheetResourceRequestOptions,
) {
  const response = await api.post<AttendanceCourseScriptGenerationResponse>(
    `/note-sheets/sheets/${sheetId}/attendance-summary/generate-course-script`,
    payload,
    {
      params: {
        workbook_id: options?.workbookId ?? undefined,
      },
    },
  )
  return response.data
}

export async function organizeAttendanceCourseScripts(
  sheetId: number,
  options?: NoteSheetResourceRequestOptions,
) {
  const response = await api.post<AttendanceCourseScriptOrganizeResponse>(
    `/note-sheets/sheets/${sheetId}/attendance-summary/organize-course-scripts`,
    {},
    {
      params: {
        workbook_id: options?.workbookId ?? undefined,
      },
    },
  )
  return response.data
}

export async function updateAttendanceLinkCounts(
  sheetId: number,
  payload: AttendanceLinkCountUpdateRequest,
  options?: NoteSheetResourceRequestOptions,
) {
  const response = await api.post<AttendanceLinkCountUpdateResponse>(
    `/note-sheets/sheets/${sheetId}/attendance-summary/update-link-counts`,
    payload,
    {
      params: {
        workbook_id: options?.workbookId ?? undefined,
      },
    },
  )
  return response.data
}

export async function deleteNoteSheet(sheetId: number) {
  await api.delete(`/note-sheets/sheets/${sheetId}`)
}

export async function fetchWorkbooks() {
  const response = await api.get<WorkbookSummary[]>('/note-sheets/workbooks')
  return response.data
}

export async function createWorkbook(payload: { title?: string }) {
  const response = await api.post<WorkbookDetail>('/note-sheets/workbooks', payload)
  return response.data
}

export async function updateWorkbook(workbookId: number, payload: { title?: string }) {
  const response = await api.put<WorkbookDetail>(`/note-sheets/workbooks/${workbookId}`, payload)
  return response.data
}

export async function saveAsWorkbook(
  workbookId: number,
  payload: {
    mode: 'template' | 'duplicate'
    title?: string
  },
) {
  const response = await api.post<WorkbookDetail>(`/note-sheets/workbooks/${workbookId}/save-as`, payload)
  return response.data
}

export async function fetchWorkbook(workbookId: number) {
  try {
    const response = await api.get<WorkbookDetail>(`/note-sheets/workbooks/${workbookId}`)
    return response.data
  } catch (error) {
    if (axios.isAxiosError(error) && error.response?.status === 404) {
      return null
    }
    throw error
  }
}

export async function fetchWorkbookAccess(workbookId: number) {
  const response = await api.get<NoteSheetResourceAccessResponse>(`/note-sheets/workbooks/${workbookId}/access`)
  return response.data
}

export async function updateWorkbookAccess(
  workbookId: number,
  grants: NoteSheetResourceAccessGrantUpdate[],
) {
  const response = await api.put<NoteSheetResourceAccessResponse>(`/note-sheets/workbooks/${workbookId}/access`, {
    grants,
  })
  return response.data
}

export async function fetchSheetAccess(sheetId: number) {
  const response = await api.get<NoteSheetResourceAccessResponse>(`/note-sheets/sheets/${sheetId}/access`)
  return response.data
}

export async function updateSheetAccess(
  sheetId: number,
  grants: NoteSheetResourceAccessGrantUpdate[],
) {
  const response = await api.put<NoteSheetResourceAccessResponse>(`/note-sheets/sheets/${sheetId}/access`, {
    grants,
  })
  return response.data
}

export async function attachSheetToWorkbook(workbookId: number, sheetId: number) {
  const response = await api.post<WorkbookDetail>(`/note-sheets/workbooks/${workbookId}/sheets`, {
    sheet_id: sheetId,
  })
  return response.data
}

export async function removeSheetFromWorkbook(workbookId: number, sheetId: number) {
  const response = await api.delete<WorkbookDetail>(`/note-sheets/workbooks/${workbookId}/sheets/${sheetId}`)
  return response.data
}

export async function deleteWorkbook(workbookId: number) {
  await api.delete(`/note-sheets/workbooks/${workbookId}`)
}
