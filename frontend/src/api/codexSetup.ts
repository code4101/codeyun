import api from './index'
import { isAxiosError } from 'axios'

export interface CodexProviderModel {
  id: string
  label: string
}

export interface CodexProviderInfo {
  id: string
  label: string
  default_model?: string
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
  opencode_proxy_running: boolean
  opencode_proxy_url: string
  providers: CodexProviderInfo[]
}

export interface CodexSetupSwitchResult {
  ok: boolean
  provider: string
  model: string
  changed: boolean
  message: string
  output: string
  notice: string
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
  /** None marks a reset break; the chart must not connect across it. */
  remaining_percent: number | null
}

export interface CodexQuotaPeriod {
  /** First-use trigger that starts this cycle; equals reset_at minus one period. */
  start_at: string
  reset_at: string
}

export interface CodexQuotaWindowHistory {
  name: string
  window_start: string
  window_end: string
  reset_at: string
  /** Reset period in minutes; 0 when the window has no reset concept. */
  period_minutes: number
  /** Each reset cycle's own start/reset pair; the previous cycle's end is not the next start. */
  periods: CodexQuotaPeriod[]
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

export interface DeepSeekBalanceItem {
  currency: string
  total_balance: string
  granted_balance: string
  topped_up_balance: string
}

export interface BalancePoint {
  at: string
  value: number
}

export interface BalanceWindow {
  window_start: string
  window_end: string
  points: BalancePoint[]
}

export interface DeepSeekBalanceResponse {
  available: boolean
  is_available: boolean
  balances: DeepSeekBalanceItem[]
  total_window: BalanceWindow | null
  observed_at: string
  error: string
}

export interface OpenCodeUsageResponse {
  available: boolean
  windows: OpenCodeUsageWindow[]
  monthly_window: CodexQuotaWindowHistory | null
  observed_at: string
  error: string
}

export interface HahaModelWeekPeriod {
  start_at: string
  reset_at: string
  /** Estimated weekly quota (USD) this week's remaining line starts from. */
  start_value: number
}

export interface HahaModelWeek {
  week_start: string
  week_end: string
  used: number
  /** Finished week: actual spend; running week: spend scaled by elapsed share. */
  quota: number
  complete: boolean
}

export interface HahaModelWeeklyWindow {
  window_start: string
  window_end: string
  /** Largest estimated weekly quota over every recorded week (USD). */
  max: number
  periods: HahaModelWeekPeriod[]
  weeks: HahaModelWeek[]
  /** None marks the break between weeks. */
  points: { at: string; value: number | null }[]
}

export interface HahaModelUsageResponse {
  configured: boolean
  available: boolean
  balance: number | null
  used_total: number
  request_count: number | null
  source?: 'account' | 'token'
  token_name?: string
  active_key?: string
  stale?: boolean
  weekly_window: HahaModelWeeklyWindow | null
  observed_at: string
  error: string
}

const CODEX_SETUP_SWITCH_TIMEOUT_MS = 5 * 60 * 1000
const CODEX_QUOTA_TIMEOUT_MS = 60 * 1000

// The local backend briefly goes offline during a supervised reload. Retry only
// reads so opening this page during that gap recovers without repeating writes.
async function readSetup<T>(path: string, timeout?: number) {
  const delays = [1000, 3000, 8000, 15000]
  for (let attempt = 0; ; attempt++) {
    try {
      const response = await api.get<T>(path, { timeout })
      return response.data
    } catch (error) {
      const transient = isAxiosError(error) && (
        [502, 503, 504].includes(error.response?.status ?? 0)
        || (!error.response && ['ERR_NETWORK', 'ECONNABORTED', 'ETIMEDOUT'].includes(error.code ?? ''))
      )
      if (!transient || attempt >= delays.length) throw error
      await new Promise(resolve => setTimeout(resolve, delays[attempt]))
    }
  }
}

export async function fetchCodexSetupStatus() {
  return readSetup<CodexSetupStatus>('/codex-setup/status')
}

export async function fetchCodexQuota() {
  return readSetup<CodexQuotaResponse>('/codex-setup/quota', CODEX_QUOTA_TIMEOUT_MS)
}

export async function fetchOpenCodeUsage() {
  return readSetup<OpenCodeUsageResponse>('/codex-setup/opencode-usage', CODEX_QUOTA_TIMEOUT_MS)
}

export async function fetchDeepSeekBalance() {
  return readSetup<DeepSeekBalanceResponse>('/codex-setup/deepseek-balance', CODEX_QUOTA_TIMEOUT_MS)
}

export async function refreshDeepSeekBalance() {
  const response = await api.post<DeepSeekBalanceResponse>(
    '/codex-setup/deepseek-balance/refresh',
    {},
    { timeout: CODEX_QUOTA_TIMEOUT_MS },
  )
  return response.data
}

export async function refreshOpenCodeUsage() {
  const response = await api.post<OpenCodeUsageResponse>(
    '/codex-setup/opencode-usage/refresh',
    {},
    { timeout: CODEX_QUOTA_TIMEOUT_MS },
  )
  return response.data
}

export async function fetchHahaModelUsage() {
  return readSetup<HahaModelUsageResponse>('/codex-setup/hahamodel-usage', CODEX_QUOTA_TIMEOUT_MS)
}

export async function refreshHahaModelUsage() {
  const response = await api.post<HahaModelUsageResponse>(
    '/codex-setup/hahamodel-usage/refresh',
    {},
    { timeout: CODEX_QUOTA_TIMEOUT_MS },
  )
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
