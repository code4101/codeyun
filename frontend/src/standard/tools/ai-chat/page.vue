<template>
  <div class="ai-chat-page">
    <section class="hero-panel">
      <div class="hero-copy">
        <h1>AI聊天</h1>
      </div>
    </section>

    <div class="workspace-grid">
      <aside class="control-panel">
        <section class="panel-card">
          <div v-if="chatModelOptions.length" class="chat-model-panel">
            <div class="selected-model-editor">
              <div
                v-if="selectedModelOptions.length"
                ref="selectedModelListRef"
                class="selected-model-list"
              >
                <div
                  v-for="(option, index) in selectedModelOptions"
                  :key="option.id"
                  class="selected-model-row"
                >
                  <SortableOrderHandle
                    :index="index"
                    :total="selectedModelOptions.length"
                    size="sm"
                  />
                  <div class="selected-model-meta">
                    <span class="selected-model-label">{{ option.providerLabel }}</span>
                    <span class="selected-model-separator">/</span>
                    <span class="selected-model-name">{{ option.model }}</span>
                  </div>
                  <div class="selected-model-actions">
                    <el-tag v-if="!option.supportsVision" size="small" effect="plain">
                      仅文本
                    </el-tag>
                    <el-tag size="small" effect="plain" :type="option.supportsStream ? 'success' : 'info'">
                      {{ option.supportsStream ? '流式输出' : '整段返回' }}
                    </el-tag>
                    <el-button
                      text
                      size="small"
                      type="danger"
                      @click="removeSelectedModelOption(option.id)"
                    >
                      移除
                    </el-button>
                  </div>
                </div>
              </div>
              <div v-else class="selected-model-empty">
                还没有加入模型。点击添加，再从模型池里选一个或多个。
              </div>

              <div v-if="showAddModelPicker" class="selected-model-add-row">
                <el-select
                  v-model="pendingModelOptionId"
                  filterable
                  placeholder="选择一个模型，例如 DeepSeek / deepseek-chat"
                  :disabled="!addableModelOptions.length"
                >
                  <el-option
                    v-for="option in addableModelOptions"
                    :key="option.id"
                    :label="option.label"
                    :value="option.id"
                  />
                </el-select>
                <el-button
                  type="primary"
                  plain
                  :disabled="!pendingModelOptionId"
                  @click="addSelectedModelOption"
                >
                  添加
                </el-button>
                <el-button text @click="closeAddModelPicker">
                  取消
                </el-button>
              </div>
              <div v-else class="selected-model-add-trigger">
                <el-button
                  type="primary"
                  plain
                  :disabled="!addableModelOptions.length"
                  @click="openAddModelPicker"
                >
                  添加
                </el-button>
              </div>
            </div>
          </div>

          <div v-else class="chat-model-empty">
            <p>还没有可用的聊天模型。</p>
            <el-button type="primary" @click="goToConfigPage">
              前往配置
            </el-button>
          </div>

          <el-alert
            v-if="status.error"
            :title="status.error"
            type="warning"
            :closable="false"
            class="status-alert"
          />
        </section>
      </aside>

      <section class="chat-panel">
        <div class="chat-surface">
          <div ref="messagesViewportRef" class="messages-viewport">
            <div v-if="conversationRounds.length" class="conversation-workspace">
              <div class="conversation-round-list">
                <article
                  v-for="(round, roundIndex) in conversationRounds"
                  :key="round.id"
                  class="conversation-round-card"
                  :class="{ active: round.assistantMessages.some(message => message.id === selectedAssistantMessageId) }"
                >
                  <div class="conversation-round-topline">
                    <div class="conversation-round-meta">
                      <span class="conversation-round-index">第 {{ roundIndex + 1 }} 轮</span>
                      <span v-if="round.userMessage.created_at" class="conversation-round-time">
                        {{ formatTime(round.userMessage.created_at) }}
                      </span>
                    </div>
                    <el-button
                      v-if="round.userMessage.content"
                      text
                      size="small"
                      :icon="CopyDocument"
                      @click="copyText(round.userMessage.content)"
                    >
                      复制提问
                    </el-button>
                  </div>

                  <div v-if="round.userMessage.images.length" class="message-image-list round-image-list">
                    <figure v-for="image in round.userMessage.images" :key="image.id" class="message-image-card">
                      <img :src="image.preview_url" :alt="image.name || '上传图片'" />
                      <figcaption>{{ image.name || '图片' }}</figcaption>
                    </figure>
                  </div>

                  <div class="conversation-round-question">
                    {{ round.userMessage.content || (round.userMessage.images.length ? '（仅发送图片）' : ' ') }}
                  </div>

                  <div v-if="round.assistantMessages.length" class="response-node-list">
                    <button
                      v-for="(assistantMessage, assistantIndex) in round.assistantMessages"
                      :key="assistantMessage.id"
                      type="button"
                      class="response-node"
                      :class="{
                        active: assistantMessage.id === selectedAssistantMessageId,
                        pending: assistantMessage.pending,
                        error: assistantMessage.error,
                      }"
                      @click="selectAssistantMessage(assistantMessage.id)"
                    >
                      <span class="response-node-index">
                        {{ formatCompactIndex(assistantIndex, round.assistantMessages.length) }}
                      </span>
                      <div class="response-node-main">
                        <span class="response-node-label">
                          {{ assistantMessage.display_model || assistantMessage.model || 'AI 回复' }}
                        </span>
                        <span class="response-node-state">
                          {{ getAssistantMessageStateText(assistantMessage) }}
                        </span>
                      </div>
                    </button>
                  </div>

                  <div v-else class="response-node-empty">
                    当前轮次还没有模型回复节点。
                  </div>
                </article>
              </div>
            </div>

            <el-empty
              v-else
              description="还没有会话内容。先选一个模型，再发一条文本或加一张图片试试。"
            />
          </div>

          <div class="composer-panel">
            <div v-if="attachments.length" class="attachment-strip">
              <div
                v-for="image in attachments"
                :key="image.id"
                class="attachment-card"
              >
                <img :src="image.preview_url" :alt="image.name || '待发送图片'" />
                <div class="attachment-meta">
                  <span class="attachment-name">{{ image.name }}</span>
                  <span class="attachment-size">{{ formatFileSize(image.size) }}</span>
                </div>
                <button type="button" class="attachment-remove" @click="removeAttachment(image.id)">
                  <el-icon><Close /></el-icon>
                </button>
              </div>
            </div>

            <div class="composer-toolbar">
              <div class="toolbar-left">
                <el-button :icon="Picture" :disabled="!selectedModelOptions.length || !selectedSupportsVision" @click="triggerImagePicker">
                  添加图片
                </el-button>
              </div>
              <div class="toolbar-right">
                <span class="toolbar-counter">
                  {{ draft.trim().length }} 字
                </span>
              </div>
            </div>

            <el-input
              v-model="draft"
              type="textarea"
              resize="none"
              :autosize="{ minRows: 4, maxRows: 10 }"
              placeholder="输入问题；Enter 发送，Shift+Enter 换行"
              @paste="handleComposerPaste"
              @keydown="handleComposerKeydown"
            />

            <div class="composer-actions">
              <div class="composer-note-block">
                <div class="composer-note">
                  {{ composerNote }}
                </div>
                <div v-if="responseCapabilityNote" class="composer-capability-note">
                  {{ responseCapabilityNote }}
                </div>
              </div>
              <el-button
                type="primary"
                size="large"
                :loading="sending"
                :disabled="!canSend"
                @click="sendMessage"
              >
                发送
              </el-button>
            </div>

            <input
              ref="fileInputRef"
              type="file"
              accept="image/*"
              multiple
              class="hidden-file-input"
              @change="handleFileChange"
            />
          </div>

          <div v-if="conversationRounds.length" class="response-detail-panel">
            <div class="response-detail-shell">
              <article
                v-if="selectedAssistantMessage"
                class="response-detail-card"
                :class="{ pending: selectedAssistantMessage.pending, error: selectedAssistantMessage.error }"
              >
                <div class="message-topline">
                  <div class="message-meta">
                    <span class="message-role">AI</span>
                    <span class="message-model">
                      {{ selectedAssistantMessage.display_model || selectedAssistantMessage.model || 'AI 回复' }}
                    </span>
                    <el-tag
                      v-if="selectedAssistantMessage.pending"
                      size="small"
                      effect="plain"
                      :type="selectedAssistantMessage.supports_stream ? 'success' : 'info'"
                      class="message-state-tag"
                    >
                      {{ getAssistantMessageStateText(selectedAssistantMessage) }}
                    </el-tag>
                    <span v-if="selectedAssistantRoundIndex >= 0" class="message-time">
                      第 {{ selectedAssistantRoundIndex + 1 }} 轮
                    </span>
                    <span v-if="selectedAssistantMessage.created_at" class="message-time">
                      {{ formatTime(selectedAssistantMessage.created_at) }}
                    </span>
                    <span v-if="selectedAssistantMessage.total_duration" class="message-time">
                      {{ formatDuration(selectedAssistantMessage.total_duration) }}
                    </span>
                  </div>
                  <el-button
                    v-if="selectedAssistantMessage.content"
                    text
                    size="small"
                    :icon="CopyDocument"
                    @click="copyText(selectedAssistantMessage.content)"
                  >
                    复制
                  </el-button>
                </div>

                <div class="response-detail-body">
                  <div v-if="selectedAssistantMessage.content" class="message-live-shell">
                    <div
                      class="message-content message-markdown"
                      :class="{ 'message-markdown-live': selectedAssistantMessage.pending }"
                      v-html="renderedSelectedAssistantHtml"
                    ></div>
                  </div>
                  <div v-else-if="selectedAssistantMessage.pending" class="message-empty-state">
                    <span class="message-empty-title">
                      {{ selectedAssistantMessage.supports_stream ? '正在思考...' : '正在生成完整回复...' }}
                    </span>
                    <span class="message-empty-caption">
                      {{ getAssistantMessagePendingHint(selectedAssistantMessage) }}
                    </span>
                  </div>
                  <div v-else class="message-content">
                    该模型当前还没有可展示的正文内容。
                  </div>
                </div>
              </article>

              <div v-else class="response-detail-empty">
                <el-empty description="点击上方模型节点后，在这里查看完整回复。" />
              </div>
            </div>
          </div>
        </div>
      </section>
    </div>

    <section class="panel-card history-panel-card">
      <div class="chat-history-section">
        <div class="chat-history-header">
          <span class="chat-history-title">历史会话</span>
          <el-button text size="small" @click="startNewSession">
            新建
          </el-button>
        </div>

        <div v-if="historySessionItems.length" class="chat-history-list">
          <article
            v-for="session in historySessionItems"
            :key="session.id"
            class="chat-history-card"
            :class="{ active: session.id === activeSessionId }"
          >
            <button
              type="button"
              class="chat-history-main"
              @click="switchSession(session.id)"
            >
              <div class="chat-history-main-topline">
                <span class="chat-history-item-title">{{ session.title || '未命名会话' }}</span>
                <span v-if="session.updated_at" class="chat-history-item-time">
                  {{ formatSessionUpdateTime(session.updated_at) }}
                </span>
              </div>
              <div class="chat-history-item-preview">
                {{ session.preview || '没有正文内容' }}
              </div>
            </button>
            <el-button
              text
              size="small"
              type="danger"
              class="chat-history-delete"
              @click.stop="removeSession(session.id)"
            >
              删除
            </el-button>
          </article>
        </div>
        <div v-else class="chat-history-empty">
          暂无历史会话。发出第一条消息后会自动进入这里。
        </div>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Close, CopyDocument, Picture } from '@element-plus/icons-vue'
import DOMPurify from 'dompurify'
import { marked } from 'marked'

import {
  fetchAiChatStatus,
  fetchAiChatSessions,
  sendAiChatMessage,
  saveAiChatSessions,
  streamAiChatMessage,
  type AiChatProviderSummary,
  type AiChatResponse,
  type AiChatSessionImage,
  type AiChatSessionItem,
  type AiChatSessionMessage,
  type AiChatSessionsResponse,
  type AiChatSessionsUpdateRequest,
  type AiChatStatusResponse,
} from '@/api/aiChat'
import SortableOrderHandle from '@/components/SortableOrderHandle.vue'
import { useAiProviderStore } from '@/store/aiProviderStore'
import { useUserStore } from '@/store/userStore'
import { useAutoSave } from '@/utils/useAutoSave'
import { useSortableList } from '@/utils/useSortableList'

interface LocalChatImage {
  id: string
  name: string
  mime_type: string
  data_base64: string
  preview_url: string
  size: number
}

interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  images: LocalChatImage[]
  target_model_option_ids?: string[]
  provider_id?: string
  model_option_id?: string
  model?: string
  display_model?: string
  created_at?: string
  total_duration?: number
  pending?: boolean
  error?: boolean
  supports_stream?: boolean
}

interface PersistedAiChatSettings {
  version: 8
  providerId: string
  model: string
  selectedModelOptionIds: string[]
}

interface ChatModelOption {
  id: string
  providerId: string
  providerLabel: string
  model: string
  label: string
  supportsVision: boolean
  supportsStream: boolean
}

interface ConversationRound {
  id: string
  userMessage: ChatMessage
  assistantMessages: ChatMessage[]
}

const SETTINGS_STORAGE_KEY = 'codeyun_ai_chat_settings_v1'
const CHAT_SESSION_DRAFT_STORAGE_KEY_PREFIX = 'codeyun_ai_chat_session_draft_v1'
const MAX_IMAGE_SIZE_BYTES = 6 * 1024 * 1024
const MAX_ATTACHMENT_COUNT = 4

const providers = computed<AiChatProviderSummary[]>(() => aiProviderStore.providers)
const status = reactive<AiChatStatusResponse>({
  provider: 'ollama',
  label: 'Ollama',
  kind: 'ollama',
  is_custom: false,
  sharing_mode: 'builtin',
  can_manage: false,
  available: false,
  requires_auth: false,
  configured: true,
  supports_stream: true,
  supports_vision: true,
  requires_api_key: false,
  base_url: '',
  default_model: 'qwen3-vl:4b',
  models: [],
  error: '',
})
const statusLoading = ref(false)
const sending = ref(false)
const draft = ref('')
const messages = ref<ChatMessage[]>([])
const attachments = ref<LocalChatImage[]>([])
const fileInputRef = ref<HTMLInputElement | null>(null)
const messagesViewportRef = ref<HTMLElement | null>(null)
const selectedModelListRef = ref<HTMLElement | null>(null)
const pendingModelOptionId = ref('')
const showAddModelPicker = ref(false)
const selectedAssistantMessageId = ref('')
const chatModelSelectionHydrated = ref(false)
const chatSessionHydrated = ref(false)
const sessionItems = ref<AiChatSessionItem[]>([])
const activeSessionId = ref('')

let localIdSeed = 0

const router = useRouter()
const aiProviderStore = useAiProviderStore()
const userStore = useUserStore()
const settings = reactive(loadInitialSettings())
const isAuthenticated = computed(() => userStore.isAuthenticated)
const chatSessionAutoSave = useAutoSave<AiChatSessionsUpdateRequest>({
  debounceMs: 1200,
  equals: areAiChatSessionsSnapshotsEqual,
  storageKey: () => buildChatSessionDraftStorageKey(),
  save: async snapshot => {
    if (!isAuthenticated.value) {
      sessionItems.value = snapshot.items
      return snapshot
    }
    const saved = await saveAiChatSessions(snapshot)
    sessionItems.value = saved.items
    return chatSessionsResponseToSnapshot(saved)
  },
  onError: error => {
    console.error('Failed to autosave AI chat session', error)
  },
})
const chatModelOptions = computed<ChatModelOption[]>(() => (
  providers.value
    .flatMap(provider => {
      if (!aiProviderStore.hasEffectiveConnection(provider.id)) {
        return []
      }
      return aiProviderStore.getEffectiveModels(provider.id).map(model => ({
        id: `${provider.id}::${model}`,
        providerId: provider.id,
        providerLabel: provider.label,
        model,
        label: `${provider.label} / ${model}`,
        supportsVision: provider.supports_vision,
        supportsStream: provider.supports_stream,
      } satisfies ChatModelOption))
    })
    .filter((option): option is ChatModelOption => Boolean(option))
))
const selectedModelOptionIds = computed({
  get: () => settings.selectedModelOptionIds,
  set: (value: string[]) => {
    settings.selectedModelOptionIds = Array.from(new Set(value.filter(Boolean)))
    syncSelectedChatModelOptions()
  },
})
const selectedModelOptions = computed(() => {
  const optionMap = new Map(chatModelOptions.value.map(option => [option.id, option]))
  return settings.selectedModelOptionIds
    .map(optionId => optionMap.get(optionId) ?? null)
    .filter((option): option is ChatModelOption => Boolean(option))
})
const addableModelOptions = computed(() => {
  const selectedIds = new Set(settings.selectedModelOptionIds)
  return chatModelOptions.value.filter(option => !selectedIds.has(option.id))
})
const assistantMessages = computed(() => messages.value.filter(message => message.role === 'assistant'))
const conversationRounds = computed<ConversationRound[]>(() => {
  const rounds: ConversationRound[] = []
  let currentRound: ConversationRound | null = null

  for (const message of messages.value) {
    if (message.role === 'user') {
      currentRound = {
        id: message.id,
        userMessage: message,
        assistantMessages: [],
      }
      rounds.push(currentRound)
      continue
    }

    if (!currentRound) {
      continue
    }
    currentRound.assistantMessages.push(message)
  }

  return rounds
})
const selectedAssistantMessage = computed(() => (
  assistantMessages.value.find(message => message.id === selectedAssistantMessageId.value) ?? null
))
const renderedSelectedAssistantHtml = computed(() => {
  const content = selectedAssistantMessage.value?.content?.trim() || ''
  if (!content) {
    return ''
  }
  return renderMarkdown(content)
})
const selectedAssistantRoundIndex = computed(() => (
  conversationRounds.value.findIndex(round => round.assistantMessages.some(message => message.id === selectedAssistantMessageId.value))
))
const primarySelectedModelOption = computed(() => selectedModelOptions.value[0] ?? null)
const currentProvider = computed(() => providers.value.find(item => item.id === primarySelectedModelOption.value?.providerId) ?? null)
const currentProviderLabel = computed(() => {
  if (!selectedModelOptions.value.length) {
    return status.label || '当前来源'
  }
  if (selectedModelOptions.value.length === 1) {
    return selectedModelOptions.value[0].providerLabel
  }
  return `${selectedModelOptions.value.length} 个模型`
})
const resolvedModelName = computed(() => primarySelectedModelOption.value?.model || settings.model.trim() || aiProviderStore.getEffectiveModel(settings.providerId) || status.default_model.trim())
const selectedSupportsVision = computed(() => (
  selectedModelOptions.value.length > 0
  && selectedModelOptions.value.every(option => option.supportsVision)
))

const canSend = computed(() => {
  if (sending.value || statusLoading.value) {
    return false
  }
  if (!selectedModelOptions.value.length) {
    return false
  }
  if (attachments.value.length && !selectedSupportsVision.value) {
    return false
  }
  return Boolean(draft.value.trim() || attachments.value.length)
})

const composerNote = computed(() => {
  if (!chatModelOptions.value.length) {
    return '请先到配置页完成来源、连接信息和模型列表。'
  }
  if (!selectedModelOptions.value.length) {
    return '请先添加至少一个模型'
  }
  if (!status.configured) {
    return status.error || `${status.label} 尚未配置`
  }
  if (attachments.value.length && !selectedSupportsVision.value) {
    return '当前选中的模型里包含不支持图片输入的项，请调整模型选择或移除图片'
  }
  if (canSend.value) {
    if (selectedModelOptions.value.length === 1) {
      if (!status.available) {
        return `将直接尝试连接 ${currentProviderLabel.value}。`
      }
      return `准备就绪，将通过 ${currentProviderLabel.value} / ${resolvedModelName.value} 发送。`
    }
    return `准备就绪，将并发请求 ${selectedModelOptions.value.length} 个模型，并分别维护各自上下文。`
  }
  return '请选择模型并输入文本，或在支持视觉的模型下添加图片。'
})
const responseCapabilityNote = computed(() => {
  if (!selectedModelOptions.value.length) {
    return ''
  }

  const streamCount = selectedModelOptions.value.filter(option => option.supportsStream).length
  if (streamCount === selectedModelOptions.value.length) {
    return '当前模型支持流式输出正文；不会展示内部思考链。'
  }
  if (streamCount === 0) {
    return '当前模型会整段返回结果；不会逐段流式输出，也不会展示内部思考。'
  }
  return `${streamCount} 个模型支持流式输出，其余模型会整段返回；不会展示内部思考。`
})
const historySessionItems = computed(() => {
  const activeWorkspaceItem = buildActiveWorkspaceSessionItem()
  const merged = sessionItems.value
    .filter(item => item.id !== activeSessionId.value)
    .map(item => ({ ...item }))

  if (activeWorkspaceItem) {
    merged.push(activeWorkspaceItem)
  }

  return merged
    .sort((left, right) => (Number(right.updated_at || 0) - Number(left.updated_at || 0)) || left.id.localeCompare(right.id))
})

watch(settings, persistSettings, { deep: true })
watch(
  () => assistantMessages.value.map(message => message.id),
  assistantMessageIds => {
    if (!assistantMessageIds.length) {
      selectedAssistantMessageId.value = ''
      return
    }
    if (selectedAssistantMessageId.value && assistantMessageIds.includes(selectedAssistantMessageId.value)) {
      return
    }
    selectedAssistantMessageId.value = assistantMessageIds[assistantMessageIds.length - 1] ?? ''
  }
)
watch(chatModelOptions, () => {
  syncSelectedChatModelOptions()
})
watch(addableModelOptions, options => {
  if (!options.length) {
    showAddModelPicker.value = false
    pendingModelOptionId.value = ''
    return
  }
  if (pendingModelOptionId.value && options.some(option => option.id === pendingModelOptionId.value)) {
    return
  }
  pendingModelOptionId.value = options[0]?.id ?? ''
}, { immediate: true })
watch(
  () => isAuthenticated.value,
  async () => {
    await initializeAiChatPage()
  }
)
watch(
  [
    () => settings.providerId,
    () => settings.model,
    () => [...settings.selectedModelOptionIds],
    () => draft.value,
    () => selectedAssistantMessageId.value,
    messages,
  ],
  () => {
    if (!chatSessionHydrated.value) {
      return
    }
    chatSessionAutoSave.markDirty(buildChatSessionsSnapshot())
  },
  { deep: true }
)

useSortableList({
  listRef: selectedModelListRef,
  getDeps: () => [selectedModelOptions.value.length, ...selectedModelOptions.value.map(option => option.id)] as const,
  isEnabled: () => selectedModelOptions.value.length > 1,
  ghostClass: 'selected-model-sortable-ghost',
  onReorder: (oldIndex, newIndex) => reorderSelectedModelOptions(oldIndex, newIndex),
})

onMounted(async () => {
  await initializeAiChatPage()
})

onBeforeUnmount(() => {
  revokeImages(attachments.value)
  for (const message of messages.value) {
    revokeImages(message.images)
  }
})

function loadInitialSettings(): PersistedAiChatSettings {
  const fallback: PersistedAiChatSettings = {
    version: 8,
    providerId: '',
    model: '',
    selectedModelOptionIds: [],
  }

  const raw = localStorage.getItem(SETTINGS_STORAGE_KEY)
  if (!raw) {
    return fallback
  }

  try {
    const parsed = JSON.parse(raw) as Partial<PersistedAiChatSettings>
    const selectedModelOptionIds = Array.isArray(parsed.selectedModelOptionIds)
      ? parsed.selectedModelOptionIds.filter((item): item is string => typeof item === 'string' && Boolean(item.trim()))
      : []
    return {
      version: 8,
      providerId: typeof parsed.providerId === 'string' ? parsed.providerId : fallback.providerId,
      model: typeof parsed.model === 'string' ? parsed.model : fallback.model,
      selectedModelOptionIds,
    }
  } catch (error) {
    console.warn('Failed to load AI chat settings', error)
    return fallback
  }
}

function persistSettings() {
  localStorage.setItem(
    SETTINGS_STORAGE_KEY,
    JSON.stringify({
      version: 8,
      providerId: settings.providerId,
      model: settings.model,
      selectedModelOptionIds: settings.selectedModelOptionIds,
    } satisfies PersistedAiChatSettings)
  )
}

function buildChatSessionDraftStorageKey() {
  const scope = userStore.user?.id ?? 'anonymous'
  return `${CHAT_SESSION_DRAFT_STORAGE_KEY_PREFIX}:${scope}`
}

function buildEmptyChatSessionsSnapshot(): AiChatSessionsUpdateRequest {
  return {
    active_session_id: null,
    items: [],
  }
}

function chatSessionsResponseToSnapshot(response: AiChatSessionsResponse): AiChatSessionsUpdateRequest {
  return {
    active_session_id: response.active_session_id ?? null,
    items: [...(response.items || [])],
  }
}

function areAiChatSessionsSnapshotsEqual(
  left: AiChatSessionsUpdateRequest,
  right: AiChatSessionsUpdateRequest,
) {
  return JSON.stringify(left) === JSON.stringify(right)
}

function getSessionTitle(messages: ChatMessage[]) {
  for (const message of messages) {
    if (message.role !== 'user') {
      continue
    }
    const content = message.content.trim()
    if (content) {
      return content.replace(/\s+/g, ' ').slice(0, 40)
    }
    if (message.images.length) {
      return '图片对话'
    }
  }
  return '新会话'
}

function getSessionPreview(messages: ChatMessage[], draftText: string) {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index]
    const content = message.content.trim()
    if (content) {
      return content.replace(/\s+/g, ' ').slice(0, 80)
    }
    if (message.images.length) {
      return '包含图片'
    }
  }
  if (draftText.trim()) {
    return `草稿：${draftText.trim().replace(/\s+/g, ' ').slice(0, 76)}`
  }
  return ''
}

function estimateBase64Size(dataBase64: string) {
  const normalized = (dataBase64 || '').trim()
  if (!normalized) {
    return 0
  }
  const padding = normalized.endsWith('==') ? 2 : (normalized.endsWith('=') ? 1 : 0)
  return Math.max(0, Math.floor((normalized.length * 3) / 4) - padding)
}

function buildImagePreviewUrl(image: AiChatSessionImage) {
  const mimeType = image.mime_type || 'image/png'
  return `data:${mimeType};base64,${image.data_base64}`
}

function serializeLocalImage(image: LocalChatImage): AiChatSessionImage {
  return {
    id: image.id,
    name: image.name || '',
    mime_type: image.mime_type || '',
    data_base64: image.data_base64,
  }
}

function restoreSessionImage(image: AiChatSessionImage): LocalChatImage {
  return {
    id: image.id,
    name: image.name || '',
    mime_type: image.mime_type || 'image/png',
    data_base64: image.data_base64,
    preview_url: buildImagePreviewUrl(image),
    size: estimateBase64Size(image.data_base64),
  }
}

function buildSessionMessagesSnapshot(messagesValue: ChatMessage[]): AiChatSessionMessage[] {
  return messagesValue.map(message => ({
    id: message.id,
    role: message.role,
    content: message.content,
    images: message.images.map(serializeLocalImage),
    target_model_option_ids: [...(message.target_model_option_ids ?? [])],
    provider_id: message.provider_id ?? '',
    model_option_id: message.model_option_id ?? '',
    model: message.model ?? '',
    display_model: message.display_model ?? '',
    created_at: message.created_at ?? null,
    total_duration: message.total_duration ?? null,
    error: Boolean(message.error || message.pending),
  }))
}

function shouldPersistSessionItem(item: AiChatSessionItem) {
  return Boolean(item.messages.length || item.draft.trim())
}

function buildActiveWorkspaceSessionItem(): AiChatSessionItem | null {
  if (!activeSessionId.value) {
    return null
  }

  const snapshotMessages = buildSessionMessagesSnapshot(messages.value)
  const item: AiChatSessionItem = {
    id: activeSessionId.value,
    title: getSessionTitle(messages.value),
    preview: getSessionPreview(messages.value, draft.value),
    provider_id: settings.providerId,
    model: settings.model,
    selected_model_option_ids: [...settings.selectedModelOptionIds],
    selected_assistant_message_id: selectedAssistantMessageId.value || null,
    draft: draft.value,
    messages: snapshotMessages,
    updated_at: Date.now() / 1000,
  }

  return shouldPersistSessionItem(item) ? item : null
}

function buildChatSessionsSnapshot(): AiChatSessionsUpdateRequest {
  const items = historySessionItems.value.map(item => ({ ...item }))
  const activeItem = buildActiveWorkspaceSessionItem()
  const activeSessionIds = new Set(items.map(item => item.id))
  const resolvedActiveSessionId = activeItem?.id ?? (activeSessionIds.has(activeSessionId.value) ? activeSessionId.value : null)

  return {
    active_session_id: resolvedActiveSessionId,
    items,
  }
}

function applySessionItemToWorkspace(
  item: AiChatSessionItem | null,
  options: { hydrateSelection?: boolean } = {},
) {
  revokeImages(attachments.value)
  attachments.value = []
  for (const message of messages.value) {
    revokeImages(message.images)
  }

  if (!item) {
    draft.value = ''
    messages.value = []
    selectedAssistantMessageId.value = ''
    return
  }

  if (options.hydrateSelection && item.selected_model_option_ids.length) {
    settings.providerId = item.provider_id || settings.providerId
    settings.model = item.model || settings.model
    settings.selectedModelOptionIds = [...item.selected_model_option_ids]
  }

  draft.value = item.draft || ''
  messages.value = item.messages.map(message => ({
    id: message.id,
    role: message.role,
    content: message.content,
    images: message.images.map(restoreSessionImage),
    target_model_option_ids: [...(message.target_model_option_ids || [])],
    provider_id: message.provider_id || undefined,
    model_option_id: message.model_option_id || undefined,
    model: message.model || undefined,
    display_model: message.display_model || undefined,
    created_at: message.created_at ?? undefined,
    total_duration: message.total_duration ?? undefined,
    pending: false,
    error: Boolean(message.error),
  }))
  selectedAssistantMessageId.value = item.selected_assistant_message_id || ''
}

function formatCompactIndex(index: number, total: number) {
  const normalizedIndex = Math.max(0, index) + 1
  const padLength = total >= 100 ? 3 : (total >= 10 ? 2 : 1)
  return String(normalizedIndex).padStart(padLength, '0')
}

function renderMarkdown(content: string) {
  const html = marked.parse(content, {
    async: false,
    breaks: true,
    gfm: true,
  }) as string
  return DOMPurify.sanitize(html)
}

function buildConnectionPayload(providerId = settings.providerId) {
  if (!providerId) {
    return {
      provider: '',
      base_url: '',
    }
  }
  return aiProviderStore.buildConnectionPayload(providerId)
}

async function initializeAiChatPage() {
  chatSessionHydrated.value = false
  await loadProvidersAndStatus()
  await loadPersistedChatSession()
  await scrollConversationToEnd()
  chatSessionHydrated.value = true
}

async function loadProvidersAndStatus() {
  chatModelSelectionHydrated.value = false
  try {
    await aiProviderStore.loadProviders(isAuthenticated.value)
    syncSelectedChatModelOptions()
  } catch (error) {
    const message = getErrorMessage(error)
    status.available = false
    status.error = message
    chatModelSelectionHydrated.value = true
    return
  }

  await refreshStatus(settings.providerId || aiProviderStore.defaultProviderId, true)
  chatModelSelectionHydrated.value = true
  syncSelectedChatModelOptions()
}

async function loadPersistedChatSession() {
  const emptySnapshot = buildEmptyChatSessionsSnapshot()
  let baseSnapshot = emptySnapshot

  try {
    if (isAuthenticated.value) {
      const response = await fetchAiChatSessions()
      baseSnapshot = chatSessionsResponseToSnapshot(response)
    }
  } catch (error) {
    console.error('Failed to load AI chat session', error)
  }

  const { snapshot, restored } = chatSessionAutoSave.loadSnapshot(baseSnapshot, { draftStrategy: 'auto' })
  const effectiveSnapshot = snapshot ?? baseSnapshot
  sessionItems.value = effectiveSnapshot.items
  activeSessionId.value = effectiveSnapshot.active_session_id || effectiveSnapshot.items[0]?.id || createLocalId('session')

  const activeItem = effectiveSnapshot.items.find(item => item.id === activeSessionId.value) ?? null
  const providerIdBeforeSessionRestore = settings.providerId
  applySessionItemToWorkspace(activeItem, {
    hydrateSelection: Boolean(activeItem?.selected_model_option_ids?.length),
  })

  if (activeItem?.selected_model_option_ids?.length) {
    syncSelectedChatModelOptions()
    if (settings.providerId !== providerIdBeforeSessionRestore) {
      await refreshStatus(settings.providerId || aiProviderStore.defaultProviderId, true)
    }
  }

  if (restored) {
    chatSessionAutoSave.markDirty(effectiveSnapshot)
  }
}

async function startNewSession() {
  if (chatSessionHydrated.value) {
    await chatSessionAutoSave.flush()
  }
  activeSessionId.value = createLocalId('session')
  applySessionItemToWorkspace(null)
  await scrollConversationToEnd()
}

async function switchSession(sessionId: string) {
  if (!sessionId || sessionId === activeSessionId.value) {
    return
  }

  if (chatSessionHydrated.value) {
    await chatSessionAutoSave.flush()
  }

  sessionItems.value = buildChatSessionsSnapshot().items
  activeSessionId.value = sessionId
  const targetSession = sessionItems.value.find(item => item.id === sessionId) ?? null
  applySessionItemToWorkspace(targetSession, {
    hydrateSelection: Boolean(targetSession?.selected_model_option_ids?.length),
  })
  if (targetSession?.selected_model_option_ids?.length) {
    syncSelectedChatModelOptions()
    await refreshStatus(settings.providerId || aiProviderStore.defaultProviderId, true)
  }
  await scrollConversationToEnd()
}

async function removeSession(sessionId: string) {
  const targetSession = historySessionItems.value.find(item => item.id === sessionId)
  if (!targetSession) {
    return
  }

  try {
    await ElMessageBox.confirm(`将删除会话“${targetSession.title || '未命名会话'}”。`, '删除历史会话', {
      confirmButtonText: '删除',
      cancelButtonText: '取消',
      type: 'warning',
    })
  } catch {
    return
  }

  if (chatSessionHydrated.value) {
    await chatSessionAutoSave.flush()
  }

  const remainingItems = buildChatSessionsSnapshot().items.filter(item => item.id !== sessionId)
  sessionItems.value = remainingItems

  if (activeSessionId.value === sessionId) {
    const nextActive = remainingItems[0] ?? null
    activeSessionId.value = nextActive?.id || createLocalId('session')
    applySessionItemToWorkspace(nextActive)
  }

  chatSessionAutoSave.markDirty({
    active_session_id: remainingItems.some(item => item.id === activeSessionId.value) ? activeSessionId.value : (remainingItems[0]?.id ?? null),
    items: remainingItems,
  }, { immediate: true })
}

function syncSelectedChatModelOptions(forceReset = false) {
  const availableOptionIds = new Set(chatModelOptions.value.map(option => option.id))
  const normalizedSelectedIds = settings.selectedModelOptionIds.filter(optionId => availableOptionIds.has(optionId))

  if (!availableOptionIds.size) {
    if (!settings.providerId) {
      settings.providerId = aiProviderStore.defaultProviderId || providers.value[0]?.id || ''
    }
    if (!settings.model) {
      settings.model = aiProviderStore.getEffectiveModel(settings.providerId) || ''
    }
    return
  }

  if (normalizedSelectedIds.length) {
    settings.selectedModelOptionIds = normalizedSelectedIds
  } else if (!chatModelSelectionHydrated.value && settings.selectedModelOptionIds.length) {
    return
  } else {
    settings.selectedModelOptionIds = []
    if (!settings.providerId) {
      settings.providerId = aiProviderStore.defaultProviderId || providers.value[0]?.id || ''
    }
    if (!settings.model) {
      settings.model = aiProviderStore.getEffectiveModel(settings.providerId) || ''
    }
    return
  }

  const primaryOption = selectedModelOptions.value[0] ?? chatModelOptions.value.find(option => option.id === settings.selectedModelOptionIds[0]) ?? null
  if (primaryOption) {
    settings.providerId = primaryOption.providerId
    settings.model = primaryOption.model
    return
  }

  settings.providerId = aiProviderStore.defaultProviderId || providers.value[0]?.id || ''
  settings.model = aiProviderStore.getEffectiveModel(settings.providerId) || ''
}

async function refreshStatus(providerId = settings.providerId, forceModel = false) {
  if (!providerId) {
    status.available = false
    status.configured = false
    status.error = '请先到配置页预设一个可用模型'
    return
  }

  statusLoading.value = true
  status.error = ''
  const connectionPayload = buildConnectionPayload(providerId)

  try {
    const nextStatus = await fetchAiChatStatus(connectionPayload)
    await aiProviderStore.syncDiscoveredModelsFromStatus(nextStatus)
    Object.assign(status, nextStatus)
    const effectiveModels = aiProviderStore.getEffectiveModels(nextStatus.provider)
    if (effectiveModels.length) {
      status.default_model = effectiveModels[0]
      status.models = effectiveModels
    }
    settings.providerId = nextStatus.provider
    if (forceModel && !settings.model.trim()) {
      settings.model = aiProviderStore.getEffectiveModel(nextStatus.provider) || nextStatus.default_model || ''
    }
  } catch (error) {
    const message = getErrorMessage(error)
    const providerMeta = aiProviderStore.getProviderById(providerId)
    status.provider = providerId
    status.label = providerMeta?.label || providerId || '当前来源'
    status.kind = providerMeta?.kind || 'unknown'
    status.is_custom = providerMeta?.is_custom ?? false
    status.sharing_mode = providerMeta?.sharing_mode || 'builtin'
    status.can_manage = providerMeta?.can_manage ?? false
    status.available = false
    status.configured = false
    status.supports_stream = providerMeta?.supports_stream ?? true
    status.supports_vision = providerMeta?.supports_vision ?? false
    status.requires_api_key = providerMeta?.requires_api_key ?? false
    status.base_url = connectionPayload.base_url
    status.default_model = aiProviderStore.getEffectiveModel(providerId) || (providerMeta?.default_model ?? '')
    status.models = status.default_model ? [status.default_model] : []
    status.error = message
  } finally {
    statusLoading.value = false
  }
}

async function syncAfterModelSelectionChange() {
  syncSelectedChatModelOptions()
  await refreshStatus(settings.providerId, true)

  if (attachments.value.length && !selectedSupportsVision.value) {
    ElMessage.warning('当前选中的模型里包含不支持图片输入的项，请调整模型选择或移除图片后再发送')
  }
}

function backfillUserHistoryForModelOption(optionId: string) {
  if (!optionId) {
    return
  }

  messages.value = messages.value.map(message => {
    if (message.role !== 'user') {
      return message
    }

    const nextTargetIds = Array.from(new Set([...(message.target_model_option_ids ?? []), optionId]))
    if (
      message.target_model_option_ids?.length
      && nextTargetIds.length === message.target_model_option_ids.length
    ) {
      return message
    }

    return {
      ...message,
      target_model_option_ids: nextTargetIds,
    }
  })
}

function openAddModelPicker() {
  if (!addableModelOptions.value.length) {
    return
  }
  showAddModelPicker.value = true
  pendingModelOptionId.value = pendingModelOptionId.value || addableModelOptions.value[0]?.id || ''
}

function closeAddModelPicker() {
  showAddModelPicker.value = false
}

async function addSelectedModelOption() {
  if (!pendingModelOptionId.value) {
    return
  }
  const optionId = pendingModelOptionId.value
  if (settings.selectedModelOptionIds.includes(optionId)) {
    return
  }

  backfillUserHistoryForModelOption(optionId)
  settings.selectedModelOptionIds = [...settings.selectedModelOptionIds, optionId]
  showAddModelPicker.value = false
  await syncAfterModelSelectionChange()
}

async function removeSelectedModelOption(optionId: string) {
  settings.selectedModelOptionIds = settings.selectedModelOptionIds.filter(id => id !== optionId)
  await syncAfterModelSelectionChange()
}

async function reorderSelectedModelOptions(oldIndex: number, newIndex: number) {
  if (
    oldIndex < 0
    || newIndex < 0
    || oldIndex >= settings.selectedModelOptionIds.length
    || newIndex >= settings.selectedModelOptionIds.length
    || oldIndex === newIndex
  ) {
    return
  }

  const reorderedIds = [...settings.selectedModelOptionIds]
  const [movedId] = reorderedIds.splice(oldIndex, 1)
  if (!movedId) {
    return
  }
  reorderedIds.splice(newIndex, 0, movedId)
  settings.selectedModelOptionIds = reorderedIds
  await syncAfterModelSelectionChange()
}

function goToConfigPage() {
  void router.push('/tools/ai-config')
}

function triggerImagePicker() {
  fileInputRef.value?.click()
}

async function handleFileChange(event: Event) {
  const input = event.target as HTMLInputElement
  const files = Array.from(input.files ?? [])
  input.value = ''

  if (!files.length) {
    return
  }

  await appendImageFiles(files)
}

async function handleComposerPaste(event: ClipboardEvent) {
  if (!selectedSupportsVision.value) {
    return
  }

  const clipboard = event.clipboardData
  if (!clipboard) {
    return
  }

  const filesFromItems = Array.from(clipboard.items ?? [])
    .map(item => item.kind === 'file' ? item.getAsFile() : null)
    .filter((file): file is File => Boolean(file))
  const files = filesFromItems.length ? filesFromItems : Array.from(clipboard.files ?? [])

  if (!files.length) {
    return
  }

  const imageFiles = files.filter(file => file.type.startsWith('image/'))
  if (!imageFiles.length) {
    return
  }

  await appendImageFiles(imageFiles)
}

function handleComposerKeydown(event: KeyboardEvent) {
  if (event.key !== 'Enter') {
    return
  }
  if (event.shiftKey || event.isComposing) {
    return
  }

  event.preventDefault()
  void sendMessage()
}

async function appendImageFiles(files: File[]) {
  const remainingSlots = MAX_ATTACHMENT_COUNT - attachments.value.length
  if (remainingSlots <= 0) {
    ElMessage.warning(`当前最多附带 ${MAX_ATTACHMENT_COUNT} 张图片`)
    return
  }

  const pickedFiles = files.slice(0, remainingSlots)
  for (const file of pickedFiles) {
    if (!file.type.startsWith('image/')) {
      ElMessage.warning(`${file.name} 不是图片，已跳过`)
      continue
    }
    if (file.size > MAX_IMAGE_SIZE_BYTES) {
      ElMessage.warning(`${file.name} 超过 ${formatFileSize(MAX_IMAGE_SIZE_BYTES)}，已跳过`)
      continue
    }

    try {
      attachments.value.push(await createAttachmentFromFile(file))
    } catch (error) {
      console.error('Failed to prepare image attachment', error)
      ElMessage.error(`读取图片失败：${file.name}`)
    }
  }

  if (files.length > pickedFiles.length) {
    ElMessage.info(`已截断为前 ${MAX_ATTACHMENT_COUNT} 张图片`)
  }
}

function removeAttachment(imageId: string) {
  const target = attachments.value.find(item => item.id === imageId)
  if (!target) {
    return
  }
  revokeImages([target])
  attachments.value = attachments.value.filter(item => item.id !== imageId)
}

async function sendMessage() {
  if (!canSend.value) {
    return
  }
  if (!activeSessionId.value) {
    activeSessionId.value = createLocalId('session')
  }

  const targetModelOptions = selectedModelOptions.value
  if (!targetModelOptions.length) {
    ElMessage.warning('请先添加至少一个模型')
    return
  }

  const outgoingImages = attachments.value
  attachments.value = []

  const userMessage: ChatMessage = {
    id: createLocalId('user'),
    role: 'user',
    content: draft.value.trim(),
    images: outgoingImages,
    target_model_option_ids: targetModelOptions.map(option => option.id),
    created_at: new Date().toISOString(),
  }
  messages.value.push(userMessage)

  const placeholders = targetModelOptions.map(option => {
    const placeholder: ChatMessage = {
      id: createLocalId('assistant'),
      role: 'assistant',
      content: '',
      images: [],
      provider_id: option.providerId,
      model_option_id: option.id,
      model: option.model,
      display_model: option.label,
      pending: true,
      supports_stream: option.supportsStream,
    }
    messages.value.push(placeholder)
    return {
      option,
      placeholder,
    }
  })
  selectedAssistantMessageId.value = placeholders[0]?.placeholder.id ?? ''
  draft.value = ''
  sending.value = true
  await scrollConversationToEnd()

  try {
    await Promise.allSettled(
      placeholders.map(({ option, placeholder }) => sendMessageToModel(option, placeholder.id))
    )
  } finally {
    sending.value = false
    await scrollConversationToEnd()
  }
}

function selectAssistantMessage(messageId: string) {
  selectedAssistantMessageId.value = messageId
}

function getAssistantMessageStateText(message: ChatMessage) {
  if (message.error) {
    return '生成异常'
  }
  if (message.pending) {
    if (message.supports_stream) {
      return message.content.trim() ? '流式输出中' : '思考中'
    }
    return '整段生成中'
  }
  return message.created_at ? formatTime(message.created_at) : '已完成'
}

function getAssistantMessagePendingHint(message: ChatMessage) {
  if (message.supports_stream) {
    return message.content.trim()
      ? '支持流式的模型会在这里持续追加正文。'
      : '正在等待首段正文返回。'
  }
  return '当前来源不支持流式输出，会在生成完成后一次性显示结果。'
}

function buildConversationHistoryForModel(modelOptionId: string) {
  return messages.value
    .filter(message => !message.pending && !message.error)
    .filter(message => {
      if (message.role === 'user') {
        return !message.target_model_option_ids?.length || message.target_model_option_ids.includes(modelOptionId)
      }
      return message.model_option_id === modelOptionId
    })
    .map(message => ({
      role: message.role,
      content: message.content,
      images: message.images.map(image => ({
        name: image.name,
        mime_type: image.mime_type,
        data_base64: image.data_base64,
      })),
    }))
}

async function sendMessageToModel(option: ChatModelOption, placeholderId: string) {
  const requestPayload = {
    ...buildConnectionPayload(option.providerId),
    model: option.model,
    stream: option.supportsStream,
    messages: buildConversationHistoryForModel(option.id),
  }

  try {
    if (option.supportsStream) {
      await streamAiChatMessage(requestPayload, event => {
        if (event.type === 'delta') {
          appendAssistantDelta(
            placeholderId,
            event.delta,
            event.model ?? undefined,
            event.created_at ?? undefined,
            option,
          )
          return
        }

        if (event.type === 'done') {
          fillAssistantMessage(placeholderId, event, option)
        }
      })
    } else {
      const response = await sendAiChatMessage(requestPayload)
      fillAssistantMessage(placeholderId, response, option)
    }
  } catch (error) {
    const target = messages.value.find(message => message.id === placeholderId)
    if (!target) {
      return
    }
    target.pending = false
    target.error = true
    const errorMessage = getErrorMessage(error)
    target.content = target.content.trim()
      ? `${target.content}\n\n[流式中断] ${errorMessage}`
      : errorMessage
  }
}

function fillAssistantMessage(messageId: string, response: AiChatResponse, option?: ChatModelOption) {
  const target = messages.value.find(message => message.id === messageId)
  if (!target) {
    return
  }

  target.pending = false
  target.error = false
  target.content = response.content
  target.model = response.model
  target.display_model = option ? `${option.providerLabel} / ${response.model}` : target.display_model
  target.supports_stream = option?.supportsStream ?? target.supports_stream
  target.created_at = response.created_at ?? new Date().toISOString()
  target.total_duration = response.total_duration ?? undefined
}

function appendAssistantDelta(
  messageId: string,
  delta: string,
  model?: string,
  createdAt?: string,
  option?: ChatModelOption,
) {
  const target = messages.value.find(message => message.id === messageId)
  if (!target) {
    return
  }

  target.pending = true
  target.error = false
  target.content += delta
  target.supports_stream = option?.supportsStream ?? target.supports_stream
  if (model) {
    target.model = model
    if (option) {
      target.display_model = `${option.providerLabel} / ${model}`
    }
  }
  if (createdAt && !target.created_at) {
    target.created_at = createdAt
  }
  void scrollConversationToEnd()
}

async function createAttachmentFromFile(file: File): Promise<LocalChatImage> {
  const dataUrl = await readFileAsDataUrl(file)
  const commaIndex = dataUrl.indexOf(',')
  const dataBase64 = commaIndex >= 0 ? dataUrl.slice(commaIndex + 1) : dataUrl

  return {
    id: createLocalId('image'),
    name: file.name,
    mime_type: file.type || 'image/*',
    data_base64: dataBase64 || dataUrl,
    preview_url: URL.createObjectURL(file),
    size: file.size,
  }
}

function readFileAsDataUrl(file: File) {
  return new Promise<string>((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(typeof reader.result === 'string' ? reader.result : '')
    reader.onerror = () => reject(reader.error ?? new Error('Failed to read file'))
    reader.readAsDataURL(file)
  })
}

function revokeImages(images: LocalChatImage[]) {
  for (const image of images) {
    if (image.preview_url && image.preview_url.startsWith('blob:')) {
      URL.revokeObjectURL(image.preview_url)
    }
  }
}

function createLocalId(prefix: string) {
  localIdSeed += 1
  return `${prefix}-${Date.now()}-${localIdSeed}`
}

async function scrollConversationToEnd() {
  await nextTick()
  const viewport = messagesViewportRef.value
  if (!viewport) {
    return
  }
  viewport.scrollTop = viewport.scrollHeight
}

async function copyText(content: string) {
  try {
    await navigator.clipboard.writeText(content)
    ElMessage.success('已复制到剪贴板')
  } catch (error) {
    console.error('Failed to copy content', error)
    ElMessage.error('复制失败，请检查浏览器权限')
  }
}

function formatTime(value: string) {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return value
  }
  return date.toLocaleString()
}

function formatSessionUpdateTime(value: number) {
  const date = new Date(value * 1000)
  if (Number.isNaN(date.getTime())) {
    return ''
  }
  return date.toLocaleString()
}

function formatDuration(value: number) {
  const milliseconds = value / 1_000_000
  if (milliseconds < 1000) {
    return `${milliseconds.toFixed(0)} ms`
  }
  return `${(milliseconds / 1000).toFixed(2)} s`
}

function formatFileSize(bytes: number) {
  if (bytes < 1024) {
    return `${bytes} B`
  }
  if (bytes < 1024 * 1024) {
    return `${(bytes / 1024).toFixed(1)} KB`
  }
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function getErrorMessage(error: unknown) {
  if (typeof error === 'object' && error && 'response' in error) {
    const maybeError = error as {
      response?: {
        data?: {
          detail?: string
          message?: string
        }
      }
      message?: string
    }
    return maybeError.response?.data?.detail
      || maybeError.response?.data?.message
      || maybeError.message
      || '请求失败'
  }

  if (error instanceof Error) {
    return error.message
  }

  return '请求失败'
}
</script>

<style scoped>
.ai-chat-page {
  min-height: 100%;
  padding: 24px;
  box-sizing: border-box;
  background:
    radial-gradient(circle at top left, rgba(14, 116, 144, 0.16), transparent 28%),
    radial-gradient(circle at bottom right, rgba(15, 118, 110, 0.14), transparent 26%),
    linear-gradient(180deg, #f2f7f7 0%, #eef3f6 100%);
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.hero-panel,
.panel-card,
.chat-surface {
  background: rgba(255, 255, 255, 0.94);
  border: 1px solid rgba(148, 163, 184, 0.18);
  border-radius: 24px;
  box-shadow: 0 18px 40px rgba(15, 23, 42, 0.08);
}

.hero-panel {
  padding: 24px 28px;
  display: flex;
  justify-content: space-between;
  gap: 24px;
  align-items: center;
  flex-wrap: wrap;
}

.hero-copy {
  max-width: 820px;
}

.hero-copy h1 {
  margin: 6px 0 10px;
  color: #0f172a;
}

.hero-copy h1 {
  font-size: 34px;
  line-height: 1.08;
}

.hero-copy p {
  margin: 0;
  color: #475569;
  font-size: 15px;
  max-width: 760px;
}

.workspace-grid {
  display: flex;
  flex-direction: column;
  gap: 18px;
  min-height: 0;
  flex: 1;
}

.control-panel {
  display: flex;
  flex-direction: column;
  gap: 18px;
  min-height: 0;
}

.panel-card {
  padding: 22px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.chat-model-panel {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.chat-history-section {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding-top: 8px;
  border-top: 1px solid rgba(148, 163, 184, 0.18);
}

.history-panel-card .chat-history-section {
  gap: 12px;
  padding-top: 0;
  border-top: 0;
}

.chat-history-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.chat-history-title {
  font-size: 13px;
  font-weight: 700;
  color: #334155;
  letter-spacing: 0.04em;
}

.chat-history-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 320px;
  overflow: auto;
  padding-right: 4px;
}

.chat-history-card {
  display: flex;
  align-items: stretch;
  gap: 8px;
  border: 1px solid rgba(148, 163, 184, 0.18);
  border-radius: 16px;
  background: rgba(248, 250, 252, 0.78);
  transition: border-color 0.2s ease, box-shadow 0.2s ease, background-color 0.2s ease;
}

.chat-history-card.active {
  border-color: rgba(14, 165, 233, 0.4);
  box-shadow: 0 10px 24px rgba(14, 165, 233, 0.12);
  background: linear-gradient(135deg, rgba(239, 246, 255, 0.98), rgba(248, 250, 252, 0.94));
}

.chat-history-main {
  flex: 1;
  min-width: 0;
  border: 0;
  background: transparent;
  text-align: left;
  padding: 12px 0 12px 14px;
  cursor: pointer;
}

.chat-history-main-topline {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 4px;
}

.chat-history-item-title {
  min-width: 0;
  font-size: 14px;
  font-weight: 700;
  color: #0f172a;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.chat-history-item-time {
  flex-shrink: 0;
  font-size: 11px;
  color: #64748b;
}

.chat-history-item-preview {
  font-size: 12px;
  color: #475569;
  line-height: 1.5;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  word-break: break-word;
}

.chat-history-delete {
  align-self: center;
  margin-right: 10px;
}

.chat-history-empty {
  font-size: 13px;
  color: #64748b;
  line-height: 1.6;
}

.selected-model-editor {
  display: flex;
  flex-direction: column;
  gap: 10px;
  width: 100%;
}

.selected-model-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  width: 100%;
}

.selected-model-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  width: 100%;
  padding: 10px 12px;
  box-sizing: border-box;
  border-radius: 16px;
  border: 1px solid rgba(148, 163, 184, 0.18);
  background: rgba(248, 250, 252, 0.84);
}

.selected-model-meta {
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  flex: 1;
}

.selected-model-label {
  font-size: 13px;
  font-weight: 600;
  color: #0f172a;
  white-space: nowrap;
}

.selected-model-separator {
  color: #94a3b8;
  font-size: 12px;
  line-height: 1;
}

.selected-model-name {
  font-size: 13px;
  color: #475569;
  word-break: break-all;
}

.selected-model-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-left: auto;
  flex-shrink: 0;
}

.selected-model-empty {
  padding: 14px 16px;
  border-radius: 16px;
  border: 1px dashed rgba(148, 163, 184, 0.35);
  background: rgba(248, 250, 252, 0.7);
  color: #64748b;
  font-size: 13px;
  line-height: 1.6;
}

.selected-model-add-row {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
}

.selected-model-add-trigger {
  display: flex;
  justify-content: flex-end;
  width: 100%;
}

.selected-model-add-row :deep(.el-select) {
  flex: 1;
  min-width: 0;
}

.chat-model-empty {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 12px;
  padding: 18px;
  border-radius: 20px;
  border: 1px dashed rgba(148, 163, 184, 0.65);
  background: rgba(248, 250, 252, 0.75);
  margin-bottom: 12px;
}

.chat-model-empty p {
  margin: 0;
  color: #475569;
  line-height: 1.6;
}

.custom-provider-row {
  margin-top: 10px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.custom-provider-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.custom-provider-note {
  font-size: 12px;
  line-height: 1.5;
  color: #64748b;
}

.account-config-row {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  flex-wrap: wrap;
}

.account-config-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.saved-key-section {
  margin-top: 12px;
  padding: 12px 14px;
  border-radius: 16px;
  background: rgba(248, 250, 252, 0.82);
  border: 1px solid rgba(148, 163, 184, 0.18);
}

.saved-key-header {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  flex-wrap: wrap;
  margin-bottom: 10px;
}

.saved-key-title {
  font-size: 13px;
  font-weight: 600;
  color: #0f172a;
}

.saved-key-note {
  font-size: 12px;
  color: #64748b;
}

.saved-key-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.saved-key-item {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: center;
  flex-wrap: wrap;
  padding: 10px 12px;
  border-radius: 14px;
  background: rgba(255, 255, 255, 0.9);
  border: 1px solid rgba(148, 163, 184, 0.14);
}

.saved-key-meta,
.saved-key-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.saved-key-label {
  font-size: 13px;
  font-weight: 600;
  color: #0f172a;
}

.saved-key-mask {
  font-size: 12px;
  color: #64748b;
}

.status-list {
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.status-row {
  display: grid;
  grid-template-columns: 88px minmax(0, 1fr);
  gap: 12px;
  align-items: start;
}

.status-row dt {
  color: #64748b;
  font-size: 13px;
}

.status-row dd {
  margin: 0;
  color: #0f172a;
  word-break: break-all;
}

.status-alert {
  margin: 0 0 18px;
}

.chat-panel,
.chat-surface {
  min-height: 0;
}

.chat-surface {
  min-height: 720px;
  display: grid;
  grid-template-rows: minmax(0, 1fr) auto auto;
  overflow: hidden;
}

.messages-viewport {
  min-height: 0;
  overflow: auto;
  padding: 22px 22px 8px;
}

.conversation-workspace {
  display: flex;
  flex-direction: column;
  gap: 18px;
  align-items: stretch;
}

.conversation-round-list {
  display: flex;
  flex-direction: column;
  gap: 14px;
  width: 100%;
}

.conversation-round-card,
.response-detail-card,
.response-detail-empty {
  border-radius: 22px;
  border: 1px solid rgba(148, 163, 184, 0.18);
  background: linear-gradient(135deg, rgba(255, 255, 255, 0.98), rgba(248, 250, 252, 0.94));
  box-shadow: 0 12px 28px rgba(15, 23, 42, 0.06);
}

.conversation-round-card {
  padding: 16px 18px;
}

.conversation-round-card.active {
  border-color: rgba(56, 189, 248, 0.35);
  box-shadow: 0 16px 32px rgba(56, 189, 248, 0.12);
}

.conversation-round-topline {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
}

.conversation-round-meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}

.conversation-round-index {
  font-size: 13px;
  font-weight: 700;
  color: #0f172a;
}

.conversation-round-time {
  font-size: 12px;
  color: #64748b;
}

.conversation-round-question {
  color: #0f172a;
  font-size: 14px;
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-word;
}

.round-image-list {
  margin-bottom: 12px;
}

.response-node-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-top: 14px;
}

.response-node {
  width: 100%;
  padding: 12px 14px;
  border: 1px solid rgba(148, 163, 184, 0.2);
  border-radius: 18px;
  background: rgba(248, 250, 252, 0.88);
  display: flex;
  align-items: center;
  gap: 12px;
  text-align: left;
  cursor: pointer;
  transition: border-color 0.2s ease, transform 0.2s ease, background 0.2s ease, box-shadow 0.2s ease;
}

.response-node:hover {
  border-color: rgba(56, 189, 248, 0.32);
  transform: translateY(-1px);
}

.response-node.active {
  border-color: rgba(59, 130, 246, 0.4);
  background: linear-gradient(135deg, rgba(239, 246, 255, 0.98), rgba(248, 250, 252, 0.96));
  box-shadow: 0 12px 24px rgba(59, 130, 246, 0.12);
}

.response-node.pending {
  opacity: 0.86;
}

.response-node.error {
  border-color: rgba(239, 68, 68, 0.26);
  background: linear-gradient(135deg, rgba(254, 242, 242, 0.96), rgba(255, 255, 255, 0.96));
}

.response-node-index {
  flex-shrink: 0;
  min-width: 32px;
  height: 32px;
  padding: 0 8px;
  border-radius: 999px;
  background: rgba(226, 232, 240, 0.95);
  color: #334155;
  font-size: 12px;
  font-weight: 700;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

.response-node-main {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.response-node-label {
  font-size: 14px;
  font-weight: 600;
  color: #0f172a;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.response-node-state {
  font-size: 12px;
  color: #64748b;
}

.response-node-empty {
  margin-top: 14px;
  padding: 12px 14px;
  border-radius: 18px;
  border: 1px dashed rgba(148, 163, 184, 0.35);
  background: rgba(248, 250, 252, 0.7);
  color: #64748b;
  font-size: 13px;
}

.response-detail-shell {
  width: 100%;
  min-height: 0;
}

.response-detail-card {
  min-height: 0;
  padding: 18px 20px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.response-detail-card.pending {
  opacity: 0.9;
}

.response-detail-card.error {
  border-color: rgba(239, 68, 68, 0.22);
  background: linear-gradient(135deg, rgba(254, 242, 242, 0.98), rgba(255, 255, 255, 0.96));
}

.response-detail-empty {
  min-height: 220px;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
}

.response-detail-body {
  min-height: 0;
}

.message-topline {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: center;
  margin-bottom: 10px;
}

.message-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}

.message-role {
  font-weight: 700;
  color: #0f172a;
}

.message-state-tag {
  margin-right: 2px;
}

.message-model,
.message-time {
  color: #64748b;
  font-size: 12px;
}

.message-live-shell {
  min-height: 0;
}

.message-content {
  white-space: pre-wrap;
  word-break: break-word;
  color: #0f172a;
  line-height: 1;
  font-size: 15px;
}

.message-markdown-live::after {
  content: '';
  display: inline-block;
  width: 0.6em;
  height: 1.1em;
  margin-left: 2px;
  border-radius: 999px;
  background: rgba(14, 116, 144, 0.72);
  vertical-align: text-bottom;
  animation: ai-chat-blink 1s ease-in-out infinite;
}

.message-empty-state {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 12px 14px;
  border-radius: 18px;
  border: 1px dashed rgba(14, 116, 144, 0.22);
  background: rgba(240, 249, 255, 0.7);
}

.message-empty-title {
  font-size: 14px;
  font-weight: 600;
  color: #0f172a;
}

.message-empty-caption {
  font-size: 12px;
  line-height: 1.6;
  color: #64748b;
}

:deep(.message-markdown > :first-child) {
  margin-top: 0;
}

:deep(.message-markdown > :last-child) {
  margin-bottom: 0;
}

:deep(.message-markdown p) {
  margin: 0 0 0.55em;
}

:deep(.message-markdown h1),
:deep(.message-markdown h2),
:deep(.message-markdown h3),
:deep(.message-markdown h4) {
  margin: 0.9em 0 0.4em;
  line-height: 1.3;
  color: #0f172a;
}

:deep(.message-markdown h1) {
  font-size: 1.75em;
}

:deep(.message-markdown h2) {
  font-size: 1.45em;
}

:deep(.message-markdown h3) {
  font-size: 1.2em;
}

:deep(.message-markdown ul),
:deep(.message-markdown ol) {
  margin: 0 0 0.65em 1.25em;
  padding: 0;
}

:deep(.message-markdown li + li) {
  margin-top: 0.15em;
}

:deep(.message-markdown blockquote) {
  margin: 0.7em 0;
  padding: 0.65em 0.9em;
  border-left: 4px solid rgba(59, 130, 246, 0.35);
  background: rgba(239, 246, 255, 0.7);
  color: #334155;
  border-radius: 0 14px 14px 0;
}

:deep(.message-markdown hr) {
  margin: 0.9em 0;
  border: none;
  border-top: 1px solid rgba(148, 163, 184, 0.28);
}

:deep(.message-markdown code) {
  font-family: Consolas, 'Courier New', monospace;
  font-size: 0.92em;
  background: rgba(226, 232, 240, 0.55);
  border-radius: 8px;
  padding: 0.15em 0.4em;
}

:deep(.message-markdown pre) {
  margin: 0.75em 0;
  padding: 14px 16px;
  border-radius: 16px;
  overflow: auto;
  background: #0f172a;
  color: #e2e8f0;
  box-shadow: inset 0 0 0 1px rgba(148, 163, 184, 0.12);
}

:deep(.message-markdown pre code) {
  background: transparent;
  color: inherit;
  padding: 0;
  border-radius: 0;
  font-size: 0.92em;
  line-height: 1.65;
}

:deep(.message-markdown table) {
  width: 100%;
  border-collapse: collapse;
  margin: 0.75em 0;
}

:deep(.message-markdown th),
:deep(.message-markdown td) {
  border: 1px solid rgba(148, 163, 184, 0.24);
  padding: 8px 10px;
  text-align: left;
  vertical-align: top;
}

:deep(.message-markdown thead th) {
  background: rgba(241, 245, 249, 0.9);
}

:deep(.message-markdown a) {
  color: #0f766e;
  text-decoration: underline;
}

.message-image-list {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-bottom: 12px;
}

.message-image-card {
  margin: 0;
  width: 164px;
  border-radius: 16px;
  overflow: hidden;
  border: 1px solid rgba(148, 163, 184, 0.2);
  background: #fff;
}

.message-image-card img {
  display: block;
  width: 100%;
  height: 132px;
  object-fit: cover;
}

.message-image-card figcaption {
  padding: 8px 10px;
  font-size: 12px;
  color: #475569;
}

.composer-panel {
  border-top: 1px solid rgba(148, 163, 184, 0.16);
  padding: 16px 22px 22px;
  background: rgba(248, 250, 252, 0.72);
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.response-detail-panel {
  border-top: 1px solid rgba(148, 163, 184, 0.16);
  padding: 0 22px 22px;
  background: rgba(248, 250, 252, 0.72);
}

.attachment-strip {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}

.attachment-card {
  position: relative;
  width: 124px;
  border-radius: 18px;
  overflow: hidden;
  border: 1px solid rgba(148, 163, 184, 0.2);
  background: #fff;
}

.attachment-card img {
  width: 100%;
  height: 96px;
  object-fit: cover;
  display: block;
}

.attachment-meta {
  padding: 10px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.attachment-name,
.attachment-size {
  font-size: 12px;
  color: #475569;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.attachment-remove {
  position: absolute;
  top: 8px;
  right: 8px;
  width: 28px;
  height: 28px;
  border: none;
  border-radius: 999px;
  background: rgba(15, 23, 42, 0.72);
  color: #fff;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
}

.composer-toolbar,
.composer-actions {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: center;
  flex-wrap: wrap;
}

.toolbar-left {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
}

.composer-note-block {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.composer-capability-note {
  font-size: 12px;
  line-height: 1.5;
  color: #7c8da1;
}

.toolbar-counter,
.composer-note {
  font-size: 13px;
  color: #64748b;
}

@keyframes ai-chat-blink {
  0%,
  100% {
    opacity: 0.18;
  }

  50% {
    opacity: 1;
  }
}

.hidden-file-input {
  display: none;
}

@media (max-width: 1100px) {
  .chat-surface {
    min-height: 640px;
  }

  .conversation-workspace {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 780px) {
  .ai-chat-page {
    padding: 16px;
  }

  .hero-panel,
  .panel-card,
  .chat-surface {
    border-radius: 20px;
  }

  .hero-panel {
    padding: 18px;
  }

  .hero-copy h1 {
    font-size: 28px;
  }

  .messages-viewport,
  .composer-panel,
  .response-detail-panel {
    padding-left: 16px;
    padding-right: 16px;
  }

  .conversation-round-topline,
  .message-topline {
    align-items: flex-start;
    flex-wrap: wrap;
  }

  .response-node {
    padding-left: 12px;
    padding-right: 12px;
  }

  .response-node-label {
    white-space: normal;
  }

  .selected-model-row,
  .selected-model-add-row {
    flex-wrap: wrap;
  }

  .selected-model-add-trigger {
    justify-content: stretch;
  }

  .selected-model-actions {
    width: 100%;
    justify-content: flex-end;
  }

  .selected-model-add-row .el-button,
  .selected-model-add-trigger .el-button {
    width: 100%;
  }
}
</style>
