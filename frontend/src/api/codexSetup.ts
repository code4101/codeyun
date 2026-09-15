import api from './index'

export type CodexSetupMode = 'deepseek-flash' | 'deepseek-v4-pro' | 'gpt'

export interface CodexSetupModeInfo {
  id: CodexSetupMode
  label: string
  description: string
}

export interface CodexSetupStatus {
  mode: string
  model: string
  model_provider: string
  codex_home: string
  config_path: string
  config_exists: boolean
  models_json_exists: boolean
  backup_exists: boolean
  deepseek_configured: boolean
  deepseek_api_key_present: boolean
  deepseek_key_available: boolean
  modes: CodexSetupModeInfo[]
}

export interface CodexSetupSwitchResult {
  ok: boolean
  mode: string
  changed: boolean
  message: string
  output: string
  status: CodexSetupStatus
}

export interface CodexQuotaWindow {
  label: string
  remaining_percent: number
  reset_at: string
}

export interface CodexQuotaGroup {
  id: string
  name: string
  windows: CodexQuotaWindow[]
}

export interface CodexQuotaPoint {
  at: string
  remaining_percent: number
}

export interface CodexQuotaWindowHistory {
  name: string
  window_start: string
  window_end: string
  reset_at: string
  remaining_percent: number | null
  points: CodexQuotaPoint[]
}

export interface CodexQuotaResponse {
  groups: CodexQuotaGroup[]
  general_window: CodexQuotaWindowHistory | null
  observed_at: string
  error: string
}

const CODEX_SETUP_SWITCH_TIMEOUT_MS = 5 * 60 * 1000
const CODEX_QUOTA_TIMEOUT_MS = 60 * 1000

export async function fetchCodexSetupStatus() {
  const response = await api.get<CodexSetupStatus>('/codex-setup/status')
  return response.data
}

export async function fetchCodexQuota() {
  const response = await api.get<CodexQuotaResponse>('/codex-setup/quota', {
    timeout: CODEX_QUOTA_TIMEOUT_MS,
  })
  return response.data
}

export async function refreshCodexQuota() {
  const response = await api.post<CodexQuotaResponse>(
    '/codex-setup/quota/refresh',
    {},
    { timeout: CODEX_QUOTA_TIMEOUT_MS },
  )
  return response.data
}

export async function switchCodexSetup(mode: CodexSetupMode, apiKey?: string) {
  const response = await api.post<CodexSetupSwitchResult>(
    '/codex-setup/switch',
    { mode, api_key: apiKey ?? null },
    { timeout: CODEX_SETUP_SWITCH_TIMEOUT_MS },
  )
  return response.data
}
