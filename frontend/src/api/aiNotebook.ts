import api, { getDeviceEntryPath } from '@/api'

export interface NotebookBinding {
  entry_id: string
  device_id: string
  notebook_path: string
  workdir: string
  exists: boolean
  updated_at?: number | null
}

export interface NotebookCell {
  cell_id: string
  index: number
  cell_type: string
  source: string
  execution_count?: number | null
  outputs_summary: string[]
  stale: boolean
  last_run_status?: string | null
  last_run_at?: number | null
}

export interface NotebookState {
  session_id: string
  entry_id: string
  device_id: string
  binding: NotebookBinding
  notebook_path: string
  notebook_hash: string
  kernel_status: 'stopped' | 'starting' | 'idle' | 'busy' | 'error'
  cells: NotebookCell[]
  stale_cell_ids: string[]
  last_error?: string | null
  dirty: boolean
}

export interface NotebookRunResponse {
  status: 'success' | 'error' | 'interrupted'
  outputs_summary: string[]
  state: NotebookState
}

export interface NotebookBindingUpdateRequest {
  notebook_path?: string | null
}

export interface UpdateCellRequest {
  notebook_hash: string
  source: string
}

export interface SaveNotebookRequest {
  notebook_hash: string
}

export interface RunCellRequest {
  notebook_hash: string
  cell_id: string
}

export interface RunCodeRequest {
  code: string
}

export async function fetchAiNotebookState(entryId: string) {
  const response = await api.get<NotebookState>(getDeviceEntryPath(entryId, '/ai-notebook/state'))
  return response.data
}

export async function updateAiNotebookBinding(entryId: string, payload: NotebookBindingUpdateRequest) {
  const response = await api.put<NotebookState>(getDeviceEntryPath(entryId, '/ai-notebook/binding'), payload)
  return response.data
}

export async function updateAiNotebookCell(entryId: string, cellId: string, payload: UpdateCellRequest) {
  const response = await api.put<NotebookState>(
    getDeviceEntryPath(entryId, `/ai-notebook/cells/${encodeURIComponent(cellId)}`),
    payload,
  )
  return response.data
}

export async function saveAiNotebook(entryId: string, payload: SaveNotebookRequest) {
  const response = await api.post<NotebookState>(getDeviceEntryPath(entryId, '/ai-notebook/save'), payload)
  return response.data
}

export async function runAiNotebookCell(entryId: string, payload: RunCellRequest) {
  const response = await api.post<NotebookRunResponse>(getDeviceEntryPath(entryId, '/ai-notebook/run-cell'), payload, {
    timeout: 180000,
  })
  return response.data
}

export async function runAiNotebookCode(entryId: string, payload: RunCodeRequest) {
  const response = await api.post<NotebookRunResponse>(getDeviceEntryPath(entryId, '/ai-notebook/run-code'), payload, {
    timeout: 180000,
  })
  return response.data
}

export async function interruptAiNotebook(entryId: string) {
  const response = await api.post<NotebookState>(getDeviceEntryPath(entryId, '/ai-notebook/interrupt'))
  return response.data
}
