import axios from 'axios'

import api from '@/api'

export interface WorkbookRefItem {
  id: number
  title: string
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

export async function fetchNoteSheets() {
  const response = await api.get<NoteSheetSummary[]>('/note-sheets/sheets')
  return response.data
}

export async function createNoteSheet(payload: NoteSheetCreateRequest) {
  const response = await api.post<NoteSheetDetail>('/note-sheets/sheets', payload)
  return response.data
}

export async function fetchNoteSheet(sheetId: number, options?: { page?: number; pageSize?: number; paginate?: boolean }) {
  try {
    const response = await api.get<NoteSheetDetail>(`/note-sheets/sheets/${sheetId}`, {
      params: {
        page: options?.page,
        page_size: options?.pageSize,
        paginate: options?.paginate,
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

export async function updateNoteSheet(sheetId: number, payload: NoteSheetUpdateRequest) {
  const response = await api.put<NoteSheetDetail>(`/note-sheets/sheets/${sheetId}`, payload)
  return response.data
}

export async function sortNoteSheet(sheetId: number, payload: NoteSheetSortRequest) {
  const response = await api.post<NoteSheetDetail>(`/note-sheets/sheets/${sheetId}/sort`, payload)
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
