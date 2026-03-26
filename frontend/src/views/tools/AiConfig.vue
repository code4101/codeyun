<template>
  <div class="ai-config-page">
    <section class="hero-panel">
      <div class="hero-copy">
        <div class="eyebrow">AI工具 / 配置</div>
        <h1>AI配置</h1>
      </div>
    </section>

    <div class="workspace-grid">
      <aside class="provider-panel">
        <section class="panel-card">
          <div class="panel-header">
            <div>
              <p class="panel-kicker">来源资产</p>
              <h2>来源列表</h2>
            </div>
            <div class="panel-header-actions">
              <el-tag v-if="!isAuthenticated" type="info" effect="plain">
                本地保存
              </el-tag>
              <el-tag v-else type="success" effect="plain">
                账号资产
              </el-tag>
              <el-button
                v-if="isAuthenticated"
                size="small"
                @click="openCustomProviderDialog"
              >
                新增来源
              </el-button>
            </div>
          </div>

          <div class="provider-list">
            <button
              v-for="provider in providers"
              :key="provider.id"
              type="button"
              class="provider-item"
              :class="{ active: selectedProviderId === provider.id }"
              @click="handleProviderChange(provider.id)"
            >
              <div class="provider-item-main">
                <span class="provider-item-label">{{ provider.label }}</span>
                <span class="provider-item-model">{{ getProviderSummaryModel(provider.id) }}</span>
              </div>
              <div class="provider-item-meta">
                <span class="provider-item-state" :class="getProviderStateClass(provider.id)">
                  {{ getProviderStateLabel(provider.id) }}
                </span>
              </div>
            </button>
          </div>
        </section>
      </aside>

      <section class="editor-panel">
        <section class="panel-card">
          <div class="panel-header">
            <div>
              <p class="panel-kicker">连接与模型</p>
              <h2>{{ currentProvider?.label || '来源配置' }}</h2>
            </div>
            <div class="panel-header-actions">
              <el-tag :type="status.available ? 'success' : (statusLoading ? 'info' : 'warning')" effect="light">
                {{ statusLoading ? '检查中' : (status.available ? '已连接' : '未连接') }}
              </el-tag>
              <el-tag v-if="savingProviderConfig" type="info" effect="plain">
                保存中
              </el-tag>
              <el-tag v-if="isAuthenticated && currentProviderHasSavedConfig" type="info" effect="plain">
                账号已保存
              </el-tag>
              <el-button
                text
                :icon="RefreshRight"
                :loading="statusLoading"
                @click="refreshStatus()"
              >
                检查连接
              </el-button>
              <el-button
                v-if="isAuthenticated && currentProvider?.is_custom"
                text
                type="danger"
                @click="deleteCurrentCustomProvider"
              >
                删除当前来源
              </el-button>
            </div>
          </div>

          <el-form label-position="top" class="settings-form">
            <el-form-item label="地址">
              <el-input
                v-model="currentBaseUrl"
                clearable
                placeholder="例如 http://127.0.0.1:11434 或 https://api.deepseek.com/v1"
                @change="handleProviderBaseUrlChange"
              />
            </el-form-item>

            <el-form-item label="API Key">
              <el-input
                v-if="isAuthenticated"
                v-model="currentApiKeyLabelInput"
                clearable
                class="api-key-label-input"
                placeholder="Key 名称（可选，不填则自动编号）"
              />
              <div class="api-key-input-row">
                <el-input
                  v-model="currentApiKeyInput"
                  type="password"
                  show-password
                  clearable
                  :placeholder="currentProviderRequiresApiKey ? '输入新 Key 后点击保存' : 'Ollama 可留空'"
                />
                <el-button
                  class="api-key-save-button"
                  type="primary"
                  :disabled="!currentApiKeyInput.trim()"
                  :loading="savingProviderConfig"
                  @click="saveCurrentProviderApiKey"
                >
                  保存
                </el-button>
              </div>
              <div v-if="isAuthenticated && currentProviderHasSavedConfig" class="account-config-row">
                <el-button
                  text
                  size="small"
                  :loading="removingProviderConfig"
                  @click="removeCurrentProviderConfig"
                >
                  清除账号保存
                </el-button>
              </div>
              <div v-if="isAuthenticated && currentProviderSavedKeys.length" class="saved-key-section">
                <div class="saved-key-header">
                  <span class="saved-key-title">已保存 API Key</span>
                  <span class="saved-key-note">每次只会使用其中一把激活项</span>
                </div>
                <div class="saved-key-list">
                  <div
                    v-for="savedKey in currentProviderSavedKeys"
                    :key="savedKey.id"
                    class="saved-key-item"
                  >
                    <div class="saved-key-meta">
                      <span class="saved-key-label">{{ savedKey.label }}</span>
                      <span class="saved-key-mask">{{ savedKey.masked_value }}</span>
                      <el-tag v-if="savedKey.is_active" size="small" type="success" effect="plain">
                        已激活
                      </el-tag>
                    </div>
                    <div class="saved-key-actions">
                      <el-button
                        v-if="!savedKey.is_active"
                        text
                        size="small"
                        :loading="activatingProviderKeyId === savedKey.id"
                        @click="activateCurrentProviderKey(savedKey.id)"
                      >
                        设为激活
                      </el-button>
                      <el-button
                        text
                        size="small"
                        type="danger"
                        :loading="deletingProviderKeyId === savedKey.id"
                        @click="deleteCurrentProviderKey(savedKey.id)"
                      >
                        删除
                      </el-button>
                    </div>
                  </div>
                </div>
              </div>
            </el-form-item>

            <el-form-item label="预设模型列表">
              <div class="model-list-editor">
                <div v-if="currentPreferredModels.length" ref="modelListRef" class="model-list">
                  <div
                    v-for="(modelName, index) in currentPreferredModels"
                    :key="`${selectedProviderId}-${index}-${modelName}`"
                    class="model-list-row"
                  >
                    <SortableOrderHandle
                      :index="index"
                      :total="currentPreferredModels.length"
                    />
                    <el-input
                      :model-value="modelName"
                      placeholder="输入模型名"
                      @change="value => updatePreferredModel(index, value)"
                    />
                    <div class="model-list-actions">
                      <el-button text size="small" type="danger" @click="removePreferredModel(index)">
                        删除
                      </el-button>
                    </div>
                  </div>
                </div>
                <div v-else class="model-list-empty">
                  还没有预设模型，先新增一个。
                </div>

                <div class="model-add-row">
                  <el-input
                    v-model="currentNewModelInput"
                    clearable
                    placeholder="新增一个模型名，例如 deepseek-chat"
                    @keyup.enter="addPreferredModel"
                  />
                  <el-button type="primary" plain :disabled="!currentNewModelInput.trim()" @click="addPreferredModel">
                    新增
                  </el-button>
                </div>
              </div>
            </el-form-item>
          </el-form>

          <el-alert
            v-if="status.error"
            :title="status.error"
            type="warning"
            :closable="false"
            class="status-alert"
          />
        </section>
      </section>
    </div>

    <el-dialog
      v-model="customProviderDialogVisible"
      title="新增自定义来源"
      width="460px"
    >
      <el-form label-position="top">
        <el-form-item label="名称">
          <el-input v-model="customProviderDraft.label" placeholder="例如我的中转站" />
        </el-form-item>
        <el-form-item label="地址">
          <el-input v-model="customProviderDraft.baseUrl" placeholder="例如 https://example.com/v1" />
        </el-form-item>
        <el-form-item label="默认模型">
          <el-input v-model="customProviderDraft.defaultModel" placeholder="可选，留空后再在配置页单独填写" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="customProviderDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="creatingCustomProvider" @click="createCustomProvider">
          创建
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { RefreshRight } from '@element-plus/icons-vue'

import { fetchAiChatStatus, type AiChatStatusResponse } from '@/api/aiChat'
import SortableOrderHandle from '@/components/SortableOrderHandle.vue'
import { useAiProviderStore } from '@/store/aiProviderStore'
import { useUserStore } from '@/store/userStore'
import { useSortableList } from '@/utils/useSortableList'

interface CustomProviderDraft {
  label: string
  baseUrl: string
  defaultModel: string
}

const aiProviderStore = useAiProviderStore()
const userStore = useUserStore()

const selectedProviderId = ref('')
const status = reactive<AiChatStatusResponse>({
  provider: 'ollama',
  label: 'Ollama',
  kind: 'ollama',
  is_custom: false,
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
const savingProviderConfig = ref(false)
const removingProviderConfig = ref(false)
const activatingProviderKeyId = ref('')
const deletingProviderKeyId = ref('')
const customProviderDialogVisible = ref(false)
const creatingCustomProvider = ref(false)
const apiKeyDrafts = reactive<Record<string, string>>({})
const apiKeyLabelDrafts = reactive<Record<string, string>>({})
const newModelDrafts = reactive<Record<string, string>>({})
const modelListRef = ref<HTMLElement | null>(null)

const customProviderDraft = reactive<CustomProviderDraft>({
  label: '',
  baseUrl: '',
  defaultModel: '',
})

const isAuthenticated = computed(() => userStore.isAuthenticated)
const providers = computed(() => aiProviderStore.providers)
const currentProvider = computed(() => aiProviderStore.getProviderById(selectedProviderId.value))
const currentProviderConfig = computed(() => aiProviderStore.getProviderConfig(selectedProviderId.value))
const currentProviderRequiresApiKey = computed(() => currentProvider.value?.requires_api_key ?? status.requires_api_key)
const currentProviderHasSavedConfig = computed(() => currentProviderConfig.value.hasAccountConfig)
const currentProviderSavedKeys = computed(() => currentProviderConfig.value.savedKeys)

const currentBaseUrl = computed({
  get: () => aiProviderStore.getProviderConfig(selectedProviderId.value).baseUrl,
  set: (value: string) => {
    aiProviderStore.updateProviderConfig(selectedProviderId.value, { baseUrl: value })
  },
})

const currentApiKeyInput = computed({
  get: () => getApiKeyDraft(selectedProviderId.value),
  set: (value: string) => {
    setApiKeyDraft(selectedProviderId.value, value)
  },
})

const currentApiKeyLabelInput = computed({
  get: () => getApiKeyLabelDraft(selectedProviderId.value),
  set: (value: string) => {
    setApiKeyLabelDraft(selectedProviderId.value, value)
  },
})

const currentPreferredModels = computed(() => aiProviderStore.getProviderConfig(selectedProviderId.value).preferredModels)
const currentNewModelInput = computed({
  get: () => getNewModelDraft(selectedProviderId.value),
  set: (value: string) => {
    setNewModelDraft(selectedProviderId.value, value)
  },
})

watch(
  () => isAuthenticated.value,
  async () => {
    await loadProvidersAndStatus()
  }
)

onMounted(async () => {
  await loadProvidersAndStatus()
})

useSortableList({
  listRef: modelListRef,
  getDeps: () => [selectedProviderId.value, currentPreferredModels.value.length] as const,
  isEnabled: () => currentPreferredModels.value.length > 1,
  ghostClass: 'model-list-ghost',
  onReorder: (oldIndex, newIndex) => reorderPreferredModel(oldIndex, newIndex),
})

function ensureSelectedProvider() {
  const knownProviderIds = new Set(providers.value.map(provider => provider.id))
  if (selectedProviderId.value && knownProviderIds.has(selectedProviderId.value)) {
    return
  }

  selectedProviderId.value = providers.value[0]?.id || aiProviderStore.defaultProviderId || 'ollama'
}

function getApiKeyDraft(providerId: string) {
  if (!providerId) {
    return ''
  }
  if (!(providerId in apiKeyDrafts)) {
    apiKeyDrafts[providerId] = aiProviderStore.getProviderConfig(providerId).apiKey || ''
  }
  return apiKeyDrafts[providerId]
}

function setApiKeyDraft(providerId: string, value: string) {
  if (!providerId) {
    return
  }
  apiKeyDrafts[providerId] = value
}

function getApiKeyLabelDraft(providerId: string) {
  if (!providerId) {
    return ''
  }
  if (!(providerId in apiKeyLabelDrafts)) {
    apiKeyLabelDrafts[providerId] = ''
  }
  return apiKeyLabelDrafts[providerId]
}

function setApiKeyLabelDraft(providerId: string, value: string) {
  if (!providerId) {
    return
  }
  apiKeyLabelDrafts[providerId] = value
}

function getNewModelDraft(providerId: string) {
  if (!providerId) {
    return ''
  }
  if (!(providerId in newModelDrafts)) {
    newModelDrafts[providerId] = ''
  }
  return newModelDrafts[providerId]
}

function setNewModelDraft(providerId: string, value: string) {
  if (!providerId) {
    return
  }
  newModelDrafts[providerId] = value
}

async function loadProvidersAndStatus() {
  try {
    await aiProviderStore.loadProviders(isAuthenticated.value)
    ensureSelectedProvider()
    getApiKeyDraft(selectedProviderId.value)
    getApiKeyLabelDraft(selectedProviderId.value)
    getNewModelDraft(selectedProviderId.value)
    await refreshStatus(selectedProviderId.value)
  } catch (error) {
    const message = getErrorMessage(error)
    status.available = false
    status.error = message
  }
}

function getProviderSummaryModel(providerId: string) {
  const models = aiProviderStore.getEffectiveModels(providerId)
  if (!models.length) {
    return '未预设模型'
  }
  if (models.length === 1) {
    return models[0]
  }
  return `${models[0]} 等 ${models.length} 个`
}

function getProviderStateLabel(providerId: string) {
  if (currentProvider.value?.id === providerId && status.available) {
    return '已连接'
  }
  if (aiProviderStore.hasEffectiveConnection(providerId) && aiProviderStore.getEffectiveModels(providerId).length) {
    return '可用'
  }
  return '待配置'
}

function getProviderStateClass(providerId: string) {
  const label = getProviderStateLabel(providerId)
  if (label === '已连接') {
    return 'is-connected'
  }
  if (label === '可用') {
    return 'is-ready'
  }
  return 'is-pending'
}

async function handleProviderChange(providerId: string) {
  selectedProviderId.value = providerId
  getApiKeyDraft(providerId)
  getApiKeyLabelDraft(providerId)
  getNewModelDraft(providerId)
  await refreshStatus(providerId)
}

async function saveCurrentProviderConfig(options: { includeApiKey?: boolean; silent?: boolean; apiKey?: string; apiKeyLabel?: string } = {}) {
  if (!isAuthenticated.value || !selectedProviderId.value) {
    return
  }

  const includeApiKey = options.includeApiKey ?? true
  const silent = options.silent ?? false
  const normalizedApiKey = includeApiKey ? (options.apiKey ?? '').trim() : ''
  const normalizedApiKeyLabel = includeApiKey ? (options.apiKeyLabel ?? '').trim() : ''
  if (includeApiKey) {
    aiProviderStore.updateProviderConfig(selectedProviderId.value, { apiKey: normalizedApiKey })
  }
  savingProviderConfig.value = true
  try {
    const hadDraftApiKey = includeApiKey && Boolean(normalizedApiKey)
    await aiProviderStore.saveProviderConfig(selectedProviderId.value, {
      includeApiKey,
      apiKeyLabel: normalizedApiKeyLabel,
    })
    if (!silent) {
      ElMessage.success(hadDraftApiKey ? '已保存新 Key 到账号，并设为激活' : '已自动保存')
    }
    await refreshStatus(selectedProviderId.value)
  } catch (error) {
    ElMessage.error(getErrorMessage(error))
  } finally {
    savingProviderConfig.value = false
  }
}

async function handleProviderBaseUrlChange() {
  if (isAuthenticated.value) {
    await saveCurrentProviderConfig({
      includeApiKey: false,
      silent: true,
    })
    return
  }
  await refreshStatus(selectedProviderId.value)
}

async function saveCurrentProviderApiKey() {
  if (!selectedProviderId.value) {
    return
  }

  const normalizedApiKey = currentApiKeyInput.value.trim()
  if (!normalizedApiKey) {
    return
  }

  if (isAuthenticated.value) {
    await saveCurrentProviderConfig({
      includeApiKey: true,
      silent: false,
      apiKey: normalizedApiKey,
      apiKeyLabel: currentApiKeyLabelInput.value,
    })
    setApiKeyDraft(selectedProviderId.value, '')
    setApiKeyLabelDraft(selectedProviderId.value, '')
    return
  }

  aiProviderStore.updateProviderConfig(selectedProviderId.value, { apiKey: normalizedApiKey })
  ElMessage.success('已保存到本地')
  await refreshStatus(selectedProviderId.value)
}

async function handlePreferredModelsChange() {
  if (!selectedProviderId.value) {
    return
  }
  if (isAuthenticated.value) {
    await saveCurrentProviderConfig({
      includeApiKey: false,
      silent: true,
    })
    return
  }
  await refreshStatus(selectedProviderId.value)
}

async function updatePreferredModel(index: number, value: string) {
  const nextModels = [...currentPreferredModels.value]
  nextModels[index] = value
  aiProviderStore.updateProviderConfig(selectedProviderId.value, {
    preferredModels: nextModels,
  })
  await handlePreferredModelsChange()
}

async function addPreferredModel() {
  const modelName = currentNewModelInput.value.trim()
  if (!selectedProviderId.value || !modelName) {
    return
  }

  aiProviderStore.updateProviderConfig(selectedProviderId.value, {
    preferredModels: [...currentPreferredModels.value, modelName],
  })
  setNewModelDraft(selectedProviderId.value, '')
  await handlePreferredModelsChange()
}

async function removePreferredModel(index: number) {
  if (!selectedProviderId.value) {
    return
  }
  aiProviderStore.updateProviderConfig(selectedProviderId.value, {
    preferredModels: currentPreferredModels.value.filter((_, itemIndex) => itemIndex !== index),
  })
  await handlePreferredModelsChange()
}

async function reorderPreferredModel(oldIndex: number, newIndex: number) {
  if (
    !selectedProviderId.value
    || oldIndex < 0
    || newIndex < 0
    || oldIndex >= currentPreferredModels.value.length
    || newIndex >= currentPreferredModels.value.length
    || oldIndex === newIndex
  ) {
    return
  }

  const nextModels = [...currentPreferredModels.value]
  const [movedModel] = nextModels.splice(oldIndex, 1)
  if (!movedModel) {
    return
  }
  nextModels.splice(newIndex, 0, movedModel)
  aiProviderStore.updateProviderConfig(selectedProviderId.value, {
    preferredModels: nextModels,
  })
  await handlePreferredModelsChange()
}

async function removeCurrentProviderConfig() {
  if (!isAuthenticated.value || !selectedProviderId.value) {
    return
  }

  removingProviderConfig.value = true
  try {
    await aiProviderStore.deleteProviderConfig(selectedProviderId.value)
    setApiKeyDraft(selectedProviderId.value, '')
    setApiKeyLabelDraft(selectedProviderId.value, '')
    ElMessage.success('已清除账号中的来源配置')
    await refreshStatus(selectedProviderId.value)
  } catch (error) {
    ElMessage.error(getErrorMessage(error))
  } finally {
    removingProviderConfig.value = false
  }
}

async function activateCurrentProviderKey(keyId: string) {
  if (!isAuthenticated.value || !selectedProviderId.value) {
    return
  }

  activatingProviderKeyId.value = keyId
  try {
    await aiProviderStore.activateProviderKey(selectedProviderId.value, keyId)
    ElMessage.success('已切换激活 Key')
    await refreshStatus(selectedProviderId.value)
  } catch (error) {
    ElMessage.error(getErrorMessage(error))
  } finally {
    activatingProviderKeyId.value = ''
  }
}

async function deleteCurrentProviderKey(keyId: string) {
  if (!isAuthenticated.value || !selectedProviderId.value) {
    return
  }

  try {
    await ElMessageBox.confirm('将删除这把已保存的 API Key。', '删除 API Key', {
      confirmButtonText: '删除',
      cancelButtonText: '取消',
      type: 'warning',
    })
  } catch {
    return
  }

  deletingProviderKeyId.value = keyId
  try {
    await aiProviderStore.deleteProviderKey(selectedProviderId.value, keyId)
    ElMessage.success('已删除保存的 API Key')
    await refreshStatus(selectedProviderId.value)
  } catch (error) {
    ElMessage.error(getErrorMessage(error))
  } finally {
    deletingProviderKeyId.value = ''
  }
}

async function refreshStatus(providerId = selectedProviderId.value) {
  if (!providerId) {
    return
  }

  statusLoading.value = true
  status.error = ''
  const connectionPayload = aiProviderStore.buildConnectionPayload(providerId)

  try {
    const nextStatus = await fetchAiChatStatus(connectionPayload)
    await aiProviderStore.syncDiscoveredModelsFromStatus(nextStatus)
    Object.assign(status, nextStatus)
    const effectiveModels = aiProviderStore.getEffectiveModels(providerId)
    if (effectiveModels.length) {
      status.default_model = effectiveModels[0]
      status.models = effectiveModels
    }
  } catch (error) {
    const message = getErrorMessage(error)
    const providerMeta = aiProviderStore.getProviderById(providerId)
    status.provider = providerId
    status.label = providerMeta?.label || providerId || '当前来源'
    status.kind = providerMeta?.kind || 'unknown'
    status.is_custom = providerMeta?.is_custom ?? false
    status.available = false
    status.configured = false
    status.supports_stream = providerMeta?.supports_stream ?? true
    status.supports_vision = providerMeta?.supports_vision ?? false
    status.requires_api_key = providerMeta?.requires_api_key ?? false
    status.base_url = connectionPayload.base_url
    status.default_model = aiProviderStore.getEffectiveModel(providerId)
    status.models = aiProviderStore.getEffectiveModels(providerId)
    status.error = message
  } finally {
    statusLoading.value = false
  }
}

function openCustomProviderDialog() {
  if (!isAuthenticated.value) {
    ElMessage.warning('登录后才能新增自定义来源')
    return
  }

  customProviderDraft.label = ''
  customProviderDraft.baseUrl = ''
  customProviderDraft.defaultModel = ''
  customProviderDialogVisible.value = true
}

async function createCustomProvider() {
  if (!isAuthenticated.value) {
    ElMessage.warning('登录后才能新增自定义来源')
    return
  }
  if (!customProviderDraft.label.trim()) {
    ElMessage.warning('请先填写自定义来源名称')
    return
  }
  if (!customProviderDraft.baseUrl.trim()) {
    ElMessage.warning('请先填写自定义来源地址')
    return
  }

  creatingCustomProvider.value = true
  try {
    const created = await aiProviderStore.createCustomProvider({
      label: customProviderDraft.label.trim(),
      base_url: customProviderDraft.baseUrl.trim(),
      default_model: customProviderDraft.defaultModel.trim() || undefined,
      models: [],
    })
    customProviderDialogVisible.value = false
    await aiProviderStore.loadProviders(isAuthenticated.value)
    selectedProviderId.value = created.id
    setApiKeyDraft(created.id, '')
    setApiKeyLabelDraft(created.id, '')
    setNewModelDraft(created.id, '')
    await refreshStatus(created.id)
    ElMessage.success('已新增自定义来源')
  } catch (error) {
    ElMessage.error(getErrorMessage(error))
  } finally {
    creatingCustomProvider.value = false
  }
}

async function deleteCurrentCustomProvider() {
  if (!isAuthenticated.value || !currentProvider.value?.is_custom) {
    return
  }

  try {
    await ElMessageBox.confirm('将删除当前自定义来源，以及它对应的账号连接配置。', '删除自定义来源', {
      confirmButtonText: '删除',
      cancelButtonText: '取消',
      type: 'warning',
    })
  } catch {
    return
  }

  try {
    await aiProviderStore.deleteCustomProvider(currentProvider.value.id)
    await aiProviderStore.loadProviders(isAuthenticated.value)
    ensureSelectedProvider()
    await refreshStatus(selectedProviderId.value)
    ElMessage.success('已删除自定义来源')
  } catch (error) {
    ElMessage.error(getErrorMessage(error))
  }
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
.ai-config-page {
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
.panel-card {
  border-radius: 28px;
  border: 1px solid rgba(191, 219, 254, 0.58);
  background: rgba(255, 255, 255, 0.9);
  box-shadow: 0 18px 45px rgba(15, 23, 42, 0.08);
  backdrop-filter: blur(10px);
}

.hero-panel {
  padding: 28px 32px;
}

.eyebrow,
.panel-kicker {
  margin: 0;
  font-size: 13px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: #64748b;
}

.hero-copy h1,
.panel-header h2 {
  margin: 6px 0 0;
  font-size: 24px;
  line-height: 1.15;
  color: #0f172a;
}

.workspace-grid {
  display: grid;
  grid-template-columns: minmax(260px, 320px) minmax(0, 1fr);
  gap: 18px;
  min-height: 0;
  flex: 1;
}

.provider-panel,
.editor-panel {
  min-height: 0;
}

.panel-card {
  padding: 22px;
  height: 100%;
  box-sizing: border-box;
}

.panel-header {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  align-items: flex-start;
  margin-bottom: 18px;
}

.panel-header-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  flex-wrap: wrap;
}

.provider-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.provider-item {
  width: 100%;
  border: 1px solid rgba(191, 219, 254, 0.9);
  background: linear-gradient(180deg, rgba(240, 249, 255, 0.95), rgba(255, 255, 255, 0.96));
  border-radius: 18px;
  padding: 14px 16px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  text-align: left;
  cursor: pointer;
  transition: transform 0.16s ease, border-color 0.16s ease, box-shadow 0.16s ease;
}

.provider-item:hover {
  transform: translateY(-1px);
  border-color: rgba(59, 130, 246, 0.65);
  box-shadow: 0 12px 24px rgba(37, 99, 235, 0.1);
}

.provider-item.active {
  border-color: rgba(14, 116, 144, 0.72);
  box-shadow: 0 12px 24px rgba(14, 116, 144, 0.14);
  background: linear-gradient(180deg, rgba(224, 242, 254, 0.96), rgba(248, 250, 252, 0.98));
}

.provider-item-main {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.provider-item-label {
  font-size: 16px;
  font-weight: 600;
  color: #0f172a;
}

.provider-item-model {
  font-size: 12px;
  color: #64748b;
  word-break: break-all;
}

.provider-item-state {
  display: inline-flex;
  align-items: center;
  padding: 4px 10px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 600;
}

.provider-item-state.is-connected {
  color: #0f766e;
  background: rgba(204, 251, 241, 0.95);
}

.provider-item-state.is-ready {
  color: #1d4ed8;
  background: rgba(219, 234, 254, 0.95);
}

.provider-item-state.is-pending {
  color: #92400e;
  background: rgba(254, 243, 199, 0.95);
}

.settings-form :deep(.el-form-item) {
  margin-bottom: 18px;
}

.api-key-label-input {
  margin-bottom: 10px;
}

.api-key-input-row {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
}

.api-key-input-row :deep(.el-input) {
  flex: 1;
}

.api-key-save-button {
  flex: 0 0 auto;
  min-width: 88px;
}

.model-list-editor {
  display: flex;
  flex-direction: column;
  gap: 10px;
  width: 100%;
}

.model-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.model-list-row {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 8px 10px;
  border: 1px solid rgba(226, 232, 240, 0.9);
  border-radius: 16px;
  background: rgba(248, 250, 252, 0.95);
}

.model-list-row :deep(.el-input) {
  flex: 1;
}

.model-list-actions {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.model-list-empty {
  padding: 14px 16px;
  border: 1px dashed rgba(191, 219, 254, 0.9);
  border-radius: 16px;
  background: rgba(248, 250, 252, 0.7);
  color: #64748b;
  font-size: 13px;
}

.model-add-row {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
}

.model-add-row :deep(.el-input) {
  flex: 1;
}

.account-config-row {
  display: flex;
  justify-content: flex-end;
  margin-top: 8px;
}

.saved-key-section {
  margin-top: 12px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  width: 100%;
}

.saved-key-header {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  flex-wrap: wrap;
  font-size: 12px;
  color: #64748b;
}

.saved-key-title {
  color: #0f172a;
  font-weight: 600;
}

.saved-key-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  width: 100%;
}

.saved-key-item {
  border-radius: 16px;
  border: 1px solid rgba(191, 219, 254, 0.78);
  background: rgba(248, 250, 252, 0.95);
  padding: 12px 14px;
  width: 100%;
  box-sizing: border-box;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.saved-key-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.saved-key-label {
  font-weight: 600;
  color: #0f172a;
}

.saved-key-mask {
  font-family: Consolas, 'Courier New', monospace;
  font-size: 12px;
  color: #475569;
}

.saved-key-actions {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.status-alert {
  margin-top: 4px;
}

@media (max-width: 980px) {
  .workspace-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 640px) {
  .ai-config-page {
    padding: 16px;
  }

  .hero-panel,
  .panel-card {
    border-radius: 22px;
  }

  .hero-panel {
    padding: 22px;
  }

  .panel-card {
    padding: 18px;
  }

  .api-key-input-row {
    flex-wrap: wrap;
  }

  .api-key-save-button {
    width: 100%;
  }

  .model-list-row,
  .model-add-row {
    flex-wrap: wrap;
  }

  .model-add-row .el-button {
    width: 100%;
  }
}
</style>
