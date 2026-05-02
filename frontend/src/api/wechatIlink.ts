import api from '@/api'

const WECHAT_ILINK_LONG_REQUEST_TIMEOUT_MS = 45 * 1000

export interface WechatIlinkAccountSummary {
  account_id: string
  user_id: string
  base_url: string
  token_masked: string
  created_at?: number | null
  updated_at?: number | null
  last_poll_at?: number | null
  last_message_at?: number | null
  has_cursor: boolean
  context_user_count: number
  codex_bridge: WechatIlinkCodexBridgeStatus
}

export interface WechatIlinkCodexBridgeStatus {
  enabled?: boolean
  running?: boolean
  model?: string
  command?: string
  started_at?: number | null
  handled_count?: number
  last_poll_at?: number | null
  last_message_at?: number | null
  last_reply_at?: number | null
  last_error?: string
}

export interface WechatIlinkStatusResponse {
  base_url: string
  bot_type: string
  channel_version: string
  accounts: WechatIlinkAccountSummary[]
  active_logins: Array<{
    session_key: string
    qrcode_url: string
    status: string
    started_at?: number | null
  }>
}

export interface WechatIlinkAccountsResponse {
  items: WechatIlinkAccountSummary[]
}

export interface WechatIlinkLoginStartRequest {
  account_id?: string | null
  force?: boolean
}

export interface WechatIlinkLoginStartResponse {
  session_key: string
  qrcode_url: string
  status: string
  message: string
}

export interface WechatIlinkLoginWaitRequest {
  session_key: string
  timeout_ms?: number
}

export interface WechatIlinkLoginWaitResponse {
  connected: boolean
  status: string
  message: string
  account?: WechatIlinkAccountSummary | null
}

export interface WechatIlinkMessageSummary {
  seq?: number | string | null
  message_id?: number | string | null
  from_user_id: string
  to_user_id: string
  create_time_ms?: number | null
  session_id: string
  message_type?: number | null
  message_state?: number | null
  context_token: string
  text: string
  images: WechatIlinkImageSummary[]
  item_types: number[]
  raw: Record<string, unknown>
}

export interface WechatIlinkImageSummary {
  id: string
  mime_type: string
  size: number
  download_url?: string
  data_url?: string
  download_error?: string
  preview_url?: string
}

export interface WechatIlinkUpdatesResponse {
  ret?: number | null
  errcode?: number | null
  errmsg?: string | null
  messages: WechatIlinkMessageSummary[]
  timed_out: boolean
  longpolling_timeout_ms?: number | null
}

export interface WechatIlinkSendTextRequest {
  to_user_id: string
  text: string
  context_token?: string | null
  timeout_ms?: number
}

export interface WechatIlinkSendTextResponse {
  message_id: string
  to_user_id: string
  used_context_token: boolean
}

export interface WechatIlinkSendImageRequest {
  to_user_id: string
  image: File
  text?: string
  context_token?: string | null
  timeout_ms?: number
}

export interface WechatIlinkSendImageResponse {
  message_id: string
  to_user_id: string
  used_context_token: boolean
  image: WechatIlinkImageSummary
}

export interface WechatIlinkCodexBridgeStartRequest {
  model?: string | null
  command?: string | null
  system_prompt?: string | null
}

export interface WechatIlinkCodexBridgeResponse {
  account: WechatIlinkAccountSummary
}

export async function fetchWechatIlinkStatus(): Promise<WechatIlinkStatusResponse> {
  const response = await api.get<WechatIlinkStatusResponse>('/wechat-ilink/status')
  return response.data
}

export async function fetchWechatIlinkAccounts(): Promise<WechatIlinkAccountsResponse> {
  const response = await api.get<WechatIlinkAccountsResponse>('/wechat-ilink/accounts')
  return response.data
}

export async function startWechatIlinkLogin(
  payload: WechatIlinkLoginStartRequest = {},
): Promise<WechatIlinkLoginStartResponse> {
  const response = await api.post<WechatIlinkLoginStartResponse>('/wechat-ilink/login/start', payload)
  return response.data
}

export async function waitWechatIlinkLogin(
  payload: WechatIlinkLoginWaitRequest,
): Promise<WechatIlinkLoginWaitResponse> {
  const response = await api.post<WechatIlinkLoginWaitResponse>('/wechat-ilink/login/wait', payload, {
    timeout: WECHAT_ILINK_LONG_REQUEST_TIMEOUT_MS,
  })
  return response.data
}

export async function deleteWechatIlinkAccount(accountId: string): Promise<void> {
  await api.delete(`/wechat-ilink/accounts/${encodeURIComponent(accountId)}`)
}

export async function pullWechatIlinkUpdates(
  accountId: string,
  timeoutMs = 35_000,
): Promise<WechatIlinkUpdatesResponse> {
  const response = await api.post<WechatIlinkUpdatesResponse>(
    `/wechat-ilink/accounts/${encodeURIComponent(accountId)}/updates`,
    { timeout_ms: timeoutMs },
    { timeout: Math.max(WECHAT_ILINK_LONG_REQUEST_TIMEOUT_MS, timeoutMs + 10_000) },
  )
  return response.data
}

export async function sendWechatIlinkText(
  accountId: string,
  payload: WechatIlinkSendTextRequest,
): Promise<WechatIlinkSendTextResponse> {
  const response = await api.post<WechatIlinkSendTextResponse>(
    `/wechat-ilink/accounts/${encodeURIComponent(accountId)}/messages`,
    payload,
    { timeout: Math.max(15_000, (payload.timeout_ms ?? 15_000) + 5_000) },
  )
  return response.data
}

export async function sendWechatIlinkImage(
  accountId: string,
  payload: WechatIlinkSendImageRequest,
): Promise<WechatIlinkSendImageResponse> {
  const form = new FormData()
  form.append('to_user_id', payload.to_user_id)
  form.append('image', payload.image)
  form.append('text', payload.text ?? '')
  if (payload.context_token) form.append('context_token', payload.context_token)
  form.append('timeout_ms', String(payload.timeout_ms ?? 15_000))
  const response = await api.post<WechatIlinkSendImageResponse>(
    `/wechat-ilink/accounts/${encodeURIComponent(accountId)}/images`,
    form,
    { timeout: Math.max(15_000, (payload.timeout_ms ?? 15_000) + 10_000) },
  )
  return response.data
}

export async function startWechatIlinkCodexBridge(
  accountId: string,
  payload: WechatIlinkCodexBridgeStartRequest = {},
): Promise<WechatIlinkCodexBridgeResponse> {
  const response = await api.post<WechatIlinkCodexBridgeResponse>(
    `/wechat-ilink/accounts/${encodeURIComponent(accountId)}/codex-bridge/start`,
    payload,
  )
  return response.data
}

export async function stopWechatIlinkCodexBridge(accountId: string): Promise<WechatIlinkCodexBridgeResponse> {
  const response = await api.post<WechatIlinkCodexBridgeResponse>(
    `/wechat-ilink/accounts/${encodeURIComponent(accountId)}/codex-bridge/stop`,
  )
  return response.data
}
