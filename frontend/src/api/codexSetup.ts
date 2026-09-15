import api from './index'

export interface CodexProviderModel {
  id: string
  label: string
}

export interface CodexProviderInfo {
  id: string
  label: string
  models: CodexProviderModel[]
}

export interface CodexSetupStatus {
  provider: string
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
  providers: CodexProviderInfo[]
}

export interface CodexSetupSwitchResult {
  ok: boolean
  provider: string
  model: string
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

export interface OpenCodeUsageWindow {
  label: string
  remaining_percent: number
  reset_at: string
  status: string
}

export interface OpenCodeUsageResponse {
  available: boolean
  windows: OpenCodeUsageWindow[]
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

export async function fetchOpenCodeUsage() {
  const response = await api.get<OpenCodeUsageResponse>('/codex-setup/opencode-usage', {
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

export async function switchCodexSetup(provider: string, model?: string, apiKey?: string) {
  const response = await api.post<CodexSetupSwitchResult>(
    '/codex-setup/switch',
    { provider, model: model ?? null, api_key: apiKey ?? null },
    { timeout: CODEX_SETUP_SWITCH_TIMEOUT_MS },
  )
  return response.data
}
