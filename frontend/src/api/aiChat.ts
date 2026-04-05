import api from '@/api'

const AI_CHAT_REQUEST_TIMEOUT_MS = 5 * 60 * 1000

type StreamEventType = 'delta' | 'done' | 'error'

export interface AiChatImageInput {
  name?: string | null
  mime_type?: string | null
  data_base64: string
}

export interface AiChatMessageInput {
  role: 'user' | 'assistant'
  content: string
  images?: AiChatImageInput[]
}

export interface AiChatRequest {
  provider?: string | null
  base_url?: string | null
  api_key?: string | null
  messages: AiChatMessageInput[]
  model?: string | null
  system_prompt?: string | null
  temperature?: number | null
  stream?: boolean | null
}

export interface AiChatResponse {
  model: string
  content: string
  created_at?: string | null
  done_reason?: string | null
  prompt_eval_count?: number | null
  eval_count?: number | null
  total_duration?: number | null
}

export interface AiChatStatusResponse {
  provider: string
  label: string
  kind: string
  is_custom: boolean
  available: boolean
  requires_auth: boolean
  configured: boolean
  supports_stream: boolean
  supports_vision: boolean
  requires_api_key: boolean
  base_url: string
  default_model: string
  models: string[]
  error?: string | null
}

export interface AiChatProviderSummary {
  id: string
  label: string
  kind: string
  is_custom: boolean
  configured: boolean
  requires_api_key: boolean
  base_url: string
  default_model: string
  models: string[]
  supports_stream: boolean
  supports_vision: boolean
}

export interface AiChatProvidersResponse {
  default_provider: string
  items: AiChatProviderSummary[]
}

export interface AiChatStatusRequest {
  provider?: string | null
  base_url?: string | null
  api_key?: string | null
}

export interface AiChatSavedProviderConfig {
  provider: string
  base_url: string
  preferred_model: string
  preferred_models: string[]
  has_api_key: boolean
  active_key_id?: string | null
  key_count: number
  keys: AiChatSavedApiKeySummary[]
  updated_at?: number | null
}

export interface AiChatSavedConfigsResponse {
  signed_in: boolean
  items: AiChatSavedProviderConfig[]
}

export interface AiChatOllamaAccessKeySummary {
  id: string
  label: string
  masked_value: string
  created_at?: number | null
  updated_at?: number | null
  created_by_user_id?: number | null
}

export interface AiChatOllamaAccessKeysResponse {
  items: AiChatOllamaAccessKeySummary[]
}

export interface AiChatCreateOllamaAccessKeyRequest {
  label?: string | null
}

export interface AiChatOllamaAccessKeyDetail extends AiChatOllamaAccessKeySummary {
  plaintext_value: string
}

export interface AiChatPromptCard {
  id: string
  title: string
  content: string
  updated_at?: number | null
}

export interface AiChatPromptCardsResponse {
  signed_in: boolean
  selected_id?: string | null
  items: AiChatPromptCard[]
}

export interface AiChatPromptCardsUpdateRequest {
  selected_id?: string | null
  items: AiChatPromptCard[]
}

export interface AiChatSessionImage {
  id: string
  name: string
  mime_type: string
  data_base64: string
}

export interface AiChatSessionMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  images: AiChatSessionImage[]
  target_model_option_ids: string[]
  provider_id: string
  model_option_id: string
  model: string
  display_model: string
  created_at?: string | null
  total_duration?: number | null
  error: boolean
}

export interface AiChatSessionItem {
  id: string
  title: string
  preview: string
  provider_id: string
  model: string
  selected_model_option_ids: string[]
  selected_assistant_message_id?: string | null
  draft: string
  messages: AiChatSessionMessage[]
  updated_at?: number | null
}

export interface AiChatSessionsResponse {
  signed_in: boolean
  active_session_id?: string | null
  items: AiChatSessionItem[]
}

export interface AiChatSessionsUpdateRequest {
  active_session_id?: string | null
  items: AiChatSessionItem[]
}

export interface AiChatSaveProviderConfigRequest {
  base_url?: string | null
  preferred_model?: string | null
  preferred_models?: string[] | null
  api_key?: string | null
  api_key_label?: string | null
  clear_api_key?: boolean
}

export interface AiChatSavedApiKeySummary {
  id: string
  label: string
  masked_value: string
  is_active: boolean
  updated_at?: number | null
}

export interface AiChatCreateCustomProviderRequest {
  label: string
  base_url: string
  default_model?: string | null
  models?: string[]
}

export interface AiChatStreamDeltaEvent {
  type: 'delta'
  delta: string
  model?: string | null
  created_at?: string | null
}

export interface AiChatStreamDoneEvent extends AiChatResponse {
  type: 'done'
}

export interface AiChatStreamErrorEvent {
  type: 'error'
  detail: string
}

export type AiChatStreamEvent =
  | AiChatStreamDeltaEvent
  | AiChatStreamDoneEvent
  | AiChatStreamErrorEvent

export async function fetchAiChatProviders() {
  const response = await api.get<AiChatProvidersResponse>('/ai-chat/providers')
  return response.data
}

export async function fetchAiChatStatus(payload: AiChatStatusRequest) {
  const response = await api.post<AiChatStatusResponse>('/ai-chat/status', payload)
  return response.data
}

export async function fetchAiChatSavedConfigs() {
  const response = await api.get<AiChatSavedConfigsResponse>('/ai-chat/saved-configs')
  return response.data
}

export async function fetchAiChatOllamaAccessKeys() {
  const response = await api.get<AiChatOllamaAccessKeysResponse>('/ai-chat/ollama-access-keys')
  return response.data
}

export async function createAiChatOllamaAccessKey(payload: AiChatCreateOllamaAccessKeyRequest = {}) {
  const response = await api.post<AiChatOllamaAccessKeyDetail>('/ai-chat/ollama-access-keys', payload)
  return response.data
}

export async function revealAiChatOllamaAccessKey(keyId: string) {
  const response = await api.get<AiChatOllamaAccessKeyDetail>(`/ai-chat/ollama-access-keys/${keyId}`)
  return response.data
}

export async function deleteAiChatOllamaAccessKey(keyId: string) {
  await api.delete(`/ai-chat/ollama-access-keys/${keyId}`)
}

export async function fetchAiChatPromptCards() {
  const response = await api.get<AiChatPromptCardsResponse>('/ai-chat/prompt-cards')
  return response.data
}

export async function saveAiChatPromptCards(payload: AiChatPromptCardsUpdateRequest) {
  const response = await api.put<AiChatPromptCardsResponse>('/ai-chat/prompt-cards', payload)
  return response.data
}

export async function fetchAiChatSessions() {
  const response = await api.get<AiChatSessionsResponse>('/ai-chat/sessions')
  return response.data
}

export async function saveAiChatSessions(payload: AiChatSessionsUpdateRequest) {
  const response = await api.put<AiChatSessionsResponse>('/ai-chat/sessions', payload)
  return response.data
}

export async function saveAiChatProviderConfig(
  providerId: string,
  payload: AiChatSaveProviderConfigRequest,
) {
  const response = await api.put<AiChatSavedProviderConfig>(`/ai-chat/saved-configs/${providerId}`, payload)
  return response.data
}

export async function deleteAiChatProviderConfig(providerId: string) {
  await api.delete(`/ai-chat/saved-configs/${providerId}`)
}

export async function activateAiChatProviderKey(providerId: string, keyId: string) {
  const response = await api.post<AiChatSavedProviderConfig>(`/ai-chat/saved-configs/${providerId}/keys/${keyId}/activate`)
  return response.data
}

export async function deleteAiChatProviderKey(providerId: string, keyId: string) {
  const response = await api.delete<AiChatSavedProviderConfig>(`/ai-chat/saved-configs/${providerId}/keys/${keyId}`)
  return response.data
}

export async function createAiChatCustomProvider(payload: AiChatCreateCustomProviderRequest) {
  const response = await api.post<AiChatProviderSummary>('/ai-chat/custom-providers', payload)
  return response.data
}

export async function deleteAiChatCustomProvider(providerId: string) {
  await api.delete(`/ai-chat/custom-providers/${providerId}`)
}

export async function sendAiChatMessage(payload: AiChatRequest) {
  const response = await api.post<AiChatResponse>(
    '/ai-chat/chat',
    payload,
    {
      timeout: AI_CHAT_REQUEST_TIMEOUT_MS,
    }
  )
  return response.data
}

export async function streamAiChatMessage(
  payload: AiChatRequest,
  onEvent: (event: AiChatStreamEvent) => void,
) {
  const token = localStorage.getItem('token')
  const response = await fetch('/api/ai-chat/chat-stream', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify(payload),
  })

  if (!response.ok) {
    throw new Error(await readErrorResponse(response))
  }

  if (!response.body) {
    throw new Error('当前浏览器不支持流式响应')
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { value, done } = await reader.read()
    buffer += decoder.decode(value ?? new Uint8Array(), { stream: !done })

    const lines = buffer.split('\n')
    buffer = lines.pop() ?? ''

    for (const line of lines) {
      const trimmed = line.trim()
      if (!trimmed) {
        continue
      }

      const event = JSON.parse(trimmed) as AiChatStreamEvent & { type?: StreamEventType }
      onEvent(event)

      if (event.type === 'error') {
        throw new Error(event.detail || '流式请求失败')
      }
    }

    if (done) {
      break
    }
  }

  const tail = buffer.trim()
  if (!tail) {
    return
  }

  const event = JSON.parse(tail) as AiChatStreamEvent & { type?: StreamEventType }
  onEvent(event)
  if (event.type === 'error') {
    throw new Error(event.detail || '流式请求失败')
  }
}

async function readErrorResponse(response: Response) {
  const raw = await response.text()
  if (!raw) {
    return `请求失败 (${response.status})`
  }

  try {
    const payload = JSON.parse(raw) as { detail?: string; message?: string }
    return payload.detail || payload.message || `请求失败 (${response.status})`
  } catch {
    return raw || `请求失败 (${response.status})`
  }
}
