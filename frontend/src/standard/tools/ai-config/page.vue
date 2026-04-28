<template>
  <div class="ai-config-page">
    <section class="hero-panel">
      <div class="hero-copy">
        <h1>AI配置</h1>
      </div>
    </section>

    <div class="workspace-grid">
      <aside class="provider-panel">
        <section class="panel-card">
          <div class="provider-list">
            <button
              v-for="provider in providers"
              :key="provider.id"
              type="button"
              class="provider-item"
              :class="{ active: assetMode === 'providers' && selectedProviderId === provider.id }"
              @click="handleProviderAssetChange(provider.id)"
            >
              <div class="provider-item-main">
                <span class="provider-item-label">{{ provider.label }}</span>
                <span class="provider-item-model">
                  {{ getProviderSummaryModel(provider.id) }}
                </span>
              </div>
              <div class="provider-item-meta">
                <span
                  class="provider-item-state"
                  :class="getProviderStateClass(provider.id)"
                >
                  {{ getProviderStateLabel(provider.id) }}
                </span>
              </div>
            </button>
          </div>
          <div v-if="isAuthenticated" class="provider-footer">
            <el-button
              class="asset-add-button"
              size="large"
              @click="openCustomProviderDialog"
            >
              新增
            </el-button>
          </div>
          <div v-if="aiAppStore.appDefinitions.length" class="provider-list app-list">
            <button
              v-for="appDefinition in aiAppStore.appDefinitions"
              :key="appDefinition.id"
              type="button"
              class="provider-item"
              :class="{ active: assetMode === 'apps' && selectedAppId === appDefinition.id }"
              @click="handleAppAssetChange(appDefinition.id)"
            >
              <div class="provider-item-main">
                <span class="provider-item-label">{{ appDefinition.label }}</span>
                <span class="provider-item-model">
                  {{ getAppSummaryModel(appDefinition.id) }}
                </span>
              </div>
              <div class="provider-item-meta">
                <span
                  class="provider-item-state"
                  :class="getAppStateClass(appDefinition.id)"
                >
                  {{ getAppStateLabel(appDefinition.id) }}
                </span>
              </div>
            </button>
          </div>
        </section>
      </aside>

      <section class="editor-panel">
        <section v-if="assetMode === 'providers'" class="panel-card">
          <div
            v-if="savingProviderConfig || currentProviderSharingLabel || (isAuthenticated && currentProvider?.is_custom && currentProviderCanManage)"
            class="panel-header editor-header"
          >
            <div class="panel-header-actions">
              <el-tag v-if="savingProviderConfig" type="info" effect="plain">
                保存中
              </el-tag>
              <el-tag v-if="currentProviderSharingLabel" type="info" effect="plain">
                {{ currentProviderSharingLabel }}
              </el-tag>
              <el-button
                v-if="isAuthenticated && currentProvider?.is_custom && currentProviderCanManage"
                text
                type="danger"
                @click="deleteCurrentCustomProvider"
              >
                删除当前来源
              </el-button>
            </div>
          </div>

          <el-form label-position="top" class="settings-form">
            <el-form-item>
              <template #label>
                <div class="form-section-label">
                  <div class="form-section-label-main">
                    <span>{{ currentProviderConnectionFieldLabel }}</span>
                    <el-button
                      v-if="isAuthenticated && !currentProviderConnectionReadonly"
                      class="section-add-button"
                      text
                      :icon="Plus"
                      :disabled="savingProviderConfig"
                      :title="`新增${currentProviderConnectionFieldLabel}`"
                      @click="openAddCurrentProviderBaseUrlDialog"
                    />
                  </div>
                  <el-button
                    class="section-check-button"
                    text
                    :icon="RefreshRight"
                    :loading="statusLoading"
                    @click="refreshStatus()"
                  >
                    检查连接
                  </el-button>
                </div>
              </template>
              <div
                v-if="isAuthenticated && !currentProviderConnectionReadonly"
                class="base-url-editor"
              >
                <div v-if="currentProviderSavedBaseUrls.length" class="saved-key-section saved-base-url-section">
                  <div class="saved-key-list">
                    <div
                      v-for="savedBaseUrl in currentProviderSavedBaseUrls"
                      :key="savedBaseUrl.id"
                      class="saved-key-item saved-choice-item"
                      :class="{ active: savedBaseUrl.is_active }"
                      role="radio"
                      :aria-checked="savedBaseUrl.is_active"
                      tabindex="0"
                      @click="!savedBaseUrl.is_active && activateCurrentProviderBaseUrl(savedBaseUrl.id)"
                      @keydown.enter.prevent="!savedBaseUrl.is_active && activateCurrentProviderBaseUrl(savedBaseUrl.id)"
                      @keydown.space.prevent="!savedBaseUrl.is_active && activateCurrentProviderBaseUrl(savedBaseUrl.id)"
                    >
                      <input
                        class="saved-choice-radio"
                        type="radio"
                        :name="`provider-base-url-${selectedProviderId}`"
                        :checked="savedBaseUrl.is_active"
                        :disabled="activatingProviderBaseUrlId === savedBaseUrl.id || deletingProviderBaseUrlId === savedBaseUrl.id || savingProviderBaseUrlId === savedBaseUrl.id"
                        @click.stop
                        @change="activateCurrentProviderBaseUrl(savedBaseUrl.id)"
                      >
                      <div class="saved-key-meta saved-base-url-meta">
                        <el-input
                          v-if="isEditingCurrentProviderBaseUrl(savedBaseUrl.id)"
                          v-model="providerBaseUrlEditDraft"
                          class="saved-inline-edit"
                          autofocus
                          clearable
                          :disabled="savingProviderBaseUrlId === savedBaseUrl.id"
                          @click.stop
                          @dblclick.stop
                          @keyup.enter="finishEditCurrentProviderBaseUrl(savedBaseUrl.id)"
                          @keyup.esc="cancelEditCurrentProviderBaseUrl(savedBaseUrl.id)"
                          @blur="finishEditCurrentProviderBaseUrl(savedBaseUrl.id)"
                        />
                        <span
                          v-else
                          class="saved-base-url-value editable-list-value"
                          @dblclick.stop="startEditCurrentProviderBaseUrl(savedBaseUrl)"
                        >
                          {{ savedBaseUrl.value }}
                        </span>
                      </div>
                      <div class="saved-key-actions">
                        <el-button
                          class="row-remove-button"
                          text
                          size="small"
                          type="danger"
                          :icon="Minus"
                          :disabled="savingProviderBaseUrlId === savedBaseUrl.id"
                          :loading="deletingProviderBaseUrlId === savedBaseUrl.id"
                          title="删除地址"
                          aria-label="删除地址"
                          @click.stop="deleteCurrentProviderBaseUrl(savedBaseUrl.id)"
                        />
                      </div>
                    </div>
                  </div>
                </div>
              </div>
              <el-input
                v-else
                v-model="currentBaseUrl"
                clearable
                :disabled="currentProviderConnectionReadonly"
                :placeholder="currentProviderConnectionPlaceholder"
                @change="handleProviderBaseUrlChange"
              />
            </el-form-item>
            <div
              v-if="isAuthenticated && currentProviderHasSavedConfig && !currentProviderRequiresApiKey && !currentProviderUsesSystemAccessKeys"
              class="account-config-row"
            >
              <el-button
                text
                size="small"
                :loading="removingProviderConfig"
                @click="removeCurrentProviderConfig"
              >
                清除账号保存
              </el-button>
            </div>

            <el-form-item v-if="currentProviderHasKeySection">
              <template #label>
                <div class="form-section-label">
                  <div class="form-section-label-main">
                    <span>{{ currentProviderKeyFieldLabel }}</span>
                    <el-popover
                      v-if="currentProviderKeyHelpVisible"
                      trigger="click"
                      placement="right-start"
                      :width="360"
                      popper-class="ai-config-key-help-popover"
                    >
                      <template #reference>
                        <el-button
                          class="section-help-button"
                          text
                          :icon="QuestionFilled"
                          title="访问 Key 说明"
                          aria-label="访问 Key 说明"
                        />
                      </template>
                      <div class="key-help-content">
                        <div class="key-help-title">{{ currentProviderKeyHelpTitle }}</div>
                        <p>{{ currentProviderNativeKeyHelpText }}</p>
                        <p>{{ currentProviderAccessPolicyHelpText }}</p>
                      </div>
                    </el-popover>
                    <el-button
                      class="section-add-button"
                      text
                      :icon="Plus"
                      :disabled="savingProviderConfig || generatingOllamaAccessKey || generatingCodexAccessKey"
                      :title="`新增${currentProviderKeyFieldLabel}`"
                      @click="handleAddCurrentProviderKey"
                    />
                  </div>
                </div>
              </template>
              <div
                v-if="!currentProviderUsesSystemAccessKeys && isAuthenticated && currentProviderHasSavedConfig && !currentProviderSavedKeys.length"
                class="account-config-row"
              >
                <el-button
                  text
                  size="small"
                  :loading="removingProviderConfig"
                  @click="removeCurrentProviderConfig"
                >
                  清除账号保存
                </el-button>
              </div>
              <div v-if="currentProviderUsesSystemAccessKeys" class="saved-key-section">
                <div v-if="currentProviderSystemAccessKeys.length" class="saved-key-list">
                  <div
                    v-for="accessKey in currentProviderSystemAccessKeys"
                    :key="accessKey.id"
                    class="saved-key-item saved-choice-item"
                    :class="{ active: isCurrentSystemAccessKeyActive(accessKey) }"
                    role="radio"
                    :aria-checked="isCurrentSystemAccessKeyActive(accessKey)"
                    tabindex="0"
                    @click="!isCurrentSystemAccessKeyActive(accessKey) && activateCurrentSystemAccessKeyForCurrentProvider(accessKey)"
                    @keydown.enter.prevent="!isCurrentSystemAccessKeyActive(accessKey) && activateCurrentSystemAccessKeyForCurrentProvider(accessKey)"
                    @keydown.space.prevent="!isCurrentSystemAccessKeyActive(accessKey) && activateCurrentSystemAccessKeyForCurrentProvider(accessKey)"
                  >
                    <input
                      class="saved-choice-radio"
                      type="radio"
                      :name="`provider-key-${selectedProviderId}`"
                      :checked="isCurrentSystemAccessKeyActive(accessKey)"
                      :disabled="activatingProviderKeyId === accessKey.id || isCurrentSystemAccessKeyDeleting(accessKey.id)"
                      @click.stop
                      @change="activateCurrentSystemAccessKeyForCurrentProvider(accessKey)"
                      >
                    <div class="saved-key-meta">
                      <span class="saved-key-mask">
                        {{ getCurrentSystemAccessKeyDisplayValue(accessKey.id, accessKey.masked_value) }}
                      </span>
                      <el-button
                        class="saved-key-eye-button"
                        text
                        size="small"
                        :icon="isCurrentSystemAccessKeyPlaintextVisible(accessKey.id) ? View : Hide"
                        :loading="isCurrentSystemAccessKeyRevealing(accessKey.id)"
                        :title="isCurrentSystemAccessKeyPlaintextVisible(accessKey.id) ? '隐藏明文' : '查看明文'"
                        @click.stop="toggleCurrentSystemAccessKeyPlaintext(accessKey.id)"
                      />
                    </div>
                    <div class="saved-key-actions">
                      <el-button
                        class="row-remove-button"
                        text
                        size="small"
                        type="danger"
                        :icon="Minus"
                        :loading="isCurrentSystemAccessKeyDeleting(accessKey.id)"
                        :title="`删除${currentProviderKeyFieldLabel}`"
                        :aria-label="`删除${currentProviderKeyFieldLabel}`"
                        @click.stop="deleteCurrentSystemAccessKey(accessKey)"
                      />
                    </div>
                  </div>
                </div>
                <div v-else-if="currentProviderSystemAccessKeysLoading" class="model-list-empty">
                  加载中
                </div>
              </div>
              <div v-else-if="isAuthenticated && currentProviderSavedKeys.length" class="saved-key-section">
                <div class="saved-key-list">
                  <div
                    v-for="savedKey in currentProviderSavedKeys"
                    :key="savedKey.id"
                    class="saved-key-item saved-choice-item"
                    :class="{ active: savedKey.is_active }"
                    role="radio"
                    :aria-checked="savedKey.is_active"
                    tabindex="0"
                    @click="!savedKey.is_active && activateCurrentProviderKey(savedKey.id)"
                    @keydown.enter.prevent="!savedKey.is_active && activateCurrentProviderKey(savedKey.id)"
                    @keydown.space.prevent="!savedKey.is_active && activateCurrentProviderKey(savedKey.id)"
                  >
                    <input
                      class="saved-choice-radio"
                      type="radio"
                      :name="`provider-key-${selectedProviderId}`"
                      :checked="savedKey.is_active"
                      :disabled="activatingProviderKeyId === savedKey.id || deletingProviderKeyId === savedKey.id || savingProviderKeyId === savedKey.id"
                      @click.stop
                      @change="activateCurrentProviderKey(savedKey.id)"
                      >
                    <div class="saved-key-meta">
                      <el-input
                        v-if="isEditingCurrentProviderKey(savedKey.id)"
                        v-model="providerKeyEditDraft"
                        class="saved-inline-edit saved-key-edit-input"
                        type="password"
                        show-password
                        autofocus
                        clearable
                        :disabled="savingProviderKeyId === savedKey.id"
                        @click.stop
                        @dblclick.stop
                        @keyup.enter="finishEditCurrentProviderKey(savedKey.id)"
                        @keyup.esc="cancelEditCurrentProviderKey(savedKey.id)"
                        @blur="finishEditCurrentProviderKey(savedKey.id)"
                      />
                      <template v-else>
                        <span
                          class="saved-key-mask editable-list-value"
                          @dblclick.stop="startEditCurrentProviderKey(savedKey)"
                        >
                          {{ getCurrentProviderKeyDisplayValue(savedKey.id, savedKey.masked_value) }}
                        </span>
                        <el-button
                          class="saved-key-eye-button"
                          text
                          size="small"
                          :icon="isCurrentProviderKeyPlaintextVisible(savedKey.id) ? View : Hide"
                          :loading="revealingProviderKeyId === savedKey.id"
                          :title="isCurrentProviderKeyPlaintextVisible(savedKey.id) ? '隐藏明文' : '查看明文'"
                          @click.stop="toggleCurrentProviderKeyPlaintext(savedKey.id)"
                        />
                      </template>
                    </div>
                    <div class="saved-key-actions">
                      <el-button
                        class="row-remove-button"
                        text
                        size="small"
                        type="danger"
                        :icon="Minus"
                        :disabled="savingProviderKeyId === savedKey.id"
                        :loading="deletingProviderKeyId === savedKey.id"
                        title="删除 API Key"
                        aria-label="删除 API Key"
                        @click.stop="deleteCurrentProviderKey(savedKey.id)"
                      />
                    </div>
                  </div>
                </div>
              </div>
              <div v-if="!isAuthenticated" class="api-key-input-row">
                <el-input
                  v-model="currentApiKeyInput"
                  class="api-key-secret-input"
                  type="password"
                  show-password
                  clearable
                  :placeholder="currentProviderKeyInputPlaceholder"
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
            </el-form-item>

            <el-form-item>
              <template #label>
                <div class="form-section-label">
                  <div class="form-section-label-main">
                    <span>预设模型</span>
                    <el-button
                      class="section-add-button"
                      text
                      :icon="Plus"
                      :disabled="savingProviderConfig"
                      title="新增模型"
                      @click="openAddPreferredModelDialog"
                    />
                  </div>
                </div>
              </template>
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
                      size="xs"
                    />
                    <el-input
                      v-if="isEditingPreferredModel(index)"
                      v-model="preferredModelEditDraft"
                      class="model-inline-edit"
                      autofocus
                      clearable
                      placeholder="输入模型名"
                      :disabled="savingPreferredModelIndex === index"
                      @click.stop
                      @dblclick.stop
                      @keyup.enter="finishEditPreferredModel(index)"
                      @keyup.esc="cancelEditPreferredModel(index)"
                      @blur="finishEditPreferredModel(index)"
                    />
                    <span
                      v-else
                      class="model-name-text editable-list-value"
                      @dblclick.stop="startEditPreferredModel(index, modelName)"
                    >
                      {{ modelName }}
                    </span>
                    <div class="model-list-actions">
                      <el-button
                        class="row-remove-button"
                        text
                        size="small"
                        type="danger"
                        :icon="Minus"
                        title="删除模型"
                        aria-label="删除模型"
                        @click="removePreferredModel(index)"
                      />
                    </div>
                  </div>
                </div>
                <div v-else class="model-list-empty">
                  暂无预设模型
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

        <section v-else class="panel-card">
          <div class="panel-header">
            <div>
              <p class="panel-kicker">应用设置</p>
              <h2>{{ currentAppDefinition?.label || '应用配置' }}</h2>
            </div>
            <div class="panel-header-actions">
              <el-tag :type="getCurrentAppStatusType()" effect="light">
                {{ getCurrentAppStatusLabel() }}
              </el-tag>
              <el-tag type="info" effect="plain">
                本地设置
              </el-tag>
            </div>
          </div>

          <el-form label-position="top" class="settings-form">
            <el-form-item label="启用">
              <div class="app-toggle-row">
                <el-switch v-model="currentAppEnabled" />
                <el-tag size="small" effect="plain" :type="currentAppEnabled ? 'success' : 'info'">
                  {{ currentAppEnabled ? '节点与详情页可直接调用' : '当前不执行' }}
                </el-tag>
              </div>
            </el-form-item>

            <el-form-item label="AI来源">
              <el-select
                v-model="currentAppProviderId"
                filterable
                placeholder="选择一个已配置来源"
                class="app-provider-select"
              >
                <el-option
                  v-for="provider in providers"
                  :key="provider.id"
                  :label="provider.label"
                  :value="provider.id"
                />
              </el-select>
            </el-form-item>

            <el-form-item label="模型">
              <div class="app-model-row">
                <el-select
                  v-model="currentAppModel"
                  filterable
                  allow-create
                  default-first-option
                  clearable
                  placeholder="留空则跟随来源首选模型"
                  class="app-model-select"
                >
                  <el-option
                    v-for="modelName in currentAppModelOptions"
                    :key="`${resolvedCurrentAppProviderId}-${modelName}`"
                    :label="modelName"
                    :value="modelName"
                  />
                </el-select>
                <el-button text @click="clearCurrentAppModel">
                  跟随首选
                </el-button>
              </div>
            </el-form-item>
          </el-form>

          <el-alert
            title="会读取当前节点标题，并参考已有条目的标题、分类、形态、阶段，自动回写分类、形态、阶段。"
            type="info"
            :closable="false"
            class="status-alert"
          />
          <el-alert
            v-if="currentAppEnabled && !currentAppIsReady"
            title="请先为这个应用绑定一个已可用的来源和模型。"
            type="warning"
            :closable="false"
            class="status-alert"
          />
        </section>
      </section>
    </div>

    <el-dialog
      v-model="customProviderDialogVisible"
      title="新增来源"
      width="460px"
    >
      <el-form label-position="top">
        <el-form-item label="类型">
          <el-segmented
            v-model="customProviderDraft.kind"
            :options="customProviderKindOptions"
          />
        </el-form-item>
        <el-form-item label="名称">
          <el-input v-model="customProviderDraft.label" :placeholder="customProviderNamePlaceholder" />
        </el-form-item>
        <el-form-item :label="customProviderConnectionLabel">
          <el-input v-model="customProviderDraft.baseUrl" :placeholder="customProviderConnectionPlaceholder" />
        </el-form-item>
        <el-form-item label="默认模型">
          <el-input v-model="customProviderDraft.defaultModel" :placeholder="customProviderDefaultModelPlaceholder" />
        </el-form-item>
        <el-form-item label="权限">
          <el-segmented
            v-model="customProviderDraft.visibility"
            :options="customProviderVisibilityOptions"
          />
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
import { Hide, Minus, Plus, QuestionFilled, RefreshRight, View } from '@element-plus/icons-vue'

import {
  createAiChatCodexAccessKey,
  createAiChatOllamaAccessKey,
  deleteAiChatCodexAccessKey,
  deleteAiChatOllamaAccessKey,
  fetchAiChatCodexAccessKeys,
  fetchAiChatOllamaAccessKeys,
  fetchAiChatStatus,
  revealAiChatCodexAccessKey,
  revealAiChatOllamaAccessKey,
  revealAiChatProviderKey,
  type AiChatCodexAccessKeySummary,
  type AiChatOllamaAccessKeySummary,
  type AiChatSavedApiKeySummary,
  type AiChatSavedBaseUrlSummary,
  type AiChatStatusResponse,
} from '@/api/aiChat'
import SortableOrderHandle from '@/components/SortableOrderHandle.vue'
import { useAiAppStore } from '@/store/aiAppStore'
import { useAiProviderStore } from '@/store/aiProviderStore'
import { useUserStore } from '@/store/userStore'
import { useSortableList } from '@/utils/useSortableList'

interface CustomProviderDraft {
  label: string
  kind: 'openai_compatible' | 'codex_cli'
  visibility: 'private' | 'public'
  baseUrl: string
  defaultModel: string
}

type AiChatSystemAccessKeySummary = AiChatOllamaAccessKeySummary | AiChatCodexAccessKeySummary

const aiProviderStore = useAiProviderStore()
const aiAppStore = useAiAppStore()
const userStore = useUserStore()

const assetMode = ref<'providers' | 'apps'>('providers')
const selectedProviderId = ref('')
const selectedAppId = ref<'note-taxonomy'>('note-taxonomy')
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
  requires_api_key: true,
  base_url: '',
  default_model: 'qwen3-vl:4b',
  models: [],
  error: '',
})
const statusLoading = ref(false)
const savingProviderConfig = ref(false)
const removingProviderConfig = ref(false)
const activatingProviderBaseUrlId = ref('')
const deletingProviderBaseUrlId = ref('')
const editingProviderBaseUrlId = ref('')
const savingProviderBaseUrlId = ref('')
const providerBaseUrlEditDraft = ref('')
const providerBaseUrlEditOriginal = ref('')
const activatingProviderKeyId = ref('')
const revealingProviderKeyId = ref('')
const deletingProviderKeyId = ref('')
const editingProviderKeyId = ref('')
const savingProviderKeyId = ref('')
const providerKeyEditDraft = ref('')
const providerKeyEditOriginal = ref('')
const providerApiKeyPlaintexts = reactive<Record<string, string>>({})
const providerApiKeyPlaintextVisible = reactive<Record<string, boolean>>({})
const codexAccessKeys = ref<AiChatCodexAccessKeySummary[]>([])
const codexAccessKeyPlaintexts = reactive<Record<string, string>>({})
const codexAccessKeyPlaintextVisible = reactive<Record<string, boolean>>({})
const codexAccessKeysLoading = ref(false)
const generatingCodexAccessKey = ref(false)
const revealingCodexAccessKeyId = ref('')
const deletingCodexAccessKeyId = ref('')
const ollamaAccessKeys = ref<AiChatOllamaAccessKeySummary[]>([])
const ollamaAccessKeyPlaintexts = reactive<Record<string, string>>({})
const ollamaAccessKeyPlaintextVisible = reactive<Record<string, boolean>>({})
const ollamaAccessKeysLoading = ref(false)
const generatingOllamaAccessKey = ref(false)
const revealingOllamaAccessKeyId = ref('')
const deletingOllamaAccessKeyId = ref('')
const customProviderDialogVisible = ref(false)
const creatingCustomProvider = ref(false)
const baseUrlDrafts = reactive<Record<string, string>>({})
const apiKeyDrafts = reactive<Record<string, string>>({})
const newModelDrafts = reactive<Record<string, string>>({})
const modelListRef = ref<HTMLElement | null>(null)
const editingPreferredModelIndex = ref<number | null>(null)
const savingPreferredModelIndex = ref<number | null>(null)
const preferredModelEditDraft = ref('')
const preferredModelEditOriginal = ref('')

const customProviderDraft = reactive<CustomProviderDraft>({
  label: '',
  kind: 'openai_compatible',
  visibility: 'private',
  baseUrl: '',
  defaultModel: '',
})

const DEFAULT_CODEX_COMMAND = 'codex'
const DEFAULT_CODEX_MODEL = 'gpt-5.4'

const isAuthenticated = computed(() => userStore.isAuthenticated)
const isAdmin = computed(() => userStore.isAdmin)
const providers = computed(() => aiProviderStore.providers)
const currentProvider = computed(() => aiProviderStore.getProviderById(selectedProviderId.value))
const currentProviderConfig = computed(() => aiProviderStore.getProviderConfig(selectedProviderId.value))
const currentProviderCanManage = computed(() => currentProvider.value?.can_manage ?? false)
const currentProviderSharingMode = computed(() => currentProvider.value?.sharing_mode || 'builtin')
const currentProviderRequiresApiKey = computed(() => currentProvider.value?.requires_api_key ?? status.requires_api_key)
const currentProviderHasSavedConfig = computed(() => currentProviderConfig.value.hasAccountConfig)
const currentProviderSavedBaseUrls = computed(() => currentProviderConfig.value.savedBaseUrls)
const currentProviderSavedKeys = computed(() => currentProviderConfig.value.savedKeys)
const currentProviderIsOllama = computed(() => (selectedProviderId.value || '').trim().toLowerCase() === 'ollama')
const currentProviderUsesSystemOllamaKeys = computed(() => currentProviderIsOllama.value && isAdmin.value)
const currentProviderIsCodex = computed(() => (currentProvider.value?.kind || '').trim().toLowerCase() === 'codex_cli')
const currentProviderUsesSystemCodexKeys = computed(() => currentProviderIsCodex.value && isAdmin.value && currentProviderCanManage.value)
const currentProviderUsesSystemAccessKeys = computed(() => currentProviderUsesSystemOllamaKeys.value || currentProviderUsesSystemCodexKeys.value)
const currentProviderSystemAccessKeys = computed<AiChatSystemAccessKeySummary[]>(() => {
  if (currentProviderUsesSystemCodexKeys.value) {
    return codexAccessKeys.value
  }
  if (currentProviderUsesSystemOllamaKeys.value) {
    return ollamaAccessKeys.value
  }
  return []
})
const currentProviderSystemAccessKeysLoading = computed(() => (
  currentProviderUsesSystemCodexKeys.value
    ? codexAccessKeysLoading.value
    : ollamaAccessKeysLoading.value
))
const currentProviderHasKeySection = computed(() => currentProviderRequiresApiKey.value || currentProviderUsesSystemAccessKeys.value)
const currentProviderConnectionReadonly = computed(() => currentProviderIsCodex.value && !currentProviderCanManage.value)
const currentProviderConnectionFieldLabel = computed(() => currentProviderIsCodex.value ? '命令' : '地址')
const currentProviderConnectionPlaceholder = computed(() => {
  if (currentProviderIsCodex.value) {
    return '例如 codex 或 codex -p myprofile'
  }
  return '例如 http://127.0.0.1:11434 或 https://api.deepseek.com/v1'
})
const currentProviderKeyFieldLabel = computed(() => {
  if (currentProviderIsCodex.value) {
    return '访问 Token'
  }
  return currentProviderIsOllama.value ? '访问 Key' : 'API Key'
})
const currentProviderKeyInputPlaceholder = computed(() => {
  if (currentProviderIsOllama.value) {
    return '输入管理员分发的 CodeYun Ollama 访问 Key'
  }
  if (currentProviderIsCodex.value) {
    return '输入管理员分发的 CodeYun Codex 访问 Token'
  }
  return currentProviderRequiresApiKey.value ? '输入新 Key 后点击保存' : '可留空'
})
const currentProviderKeyHelpVisible = computed(() => currentProviderIsOllama.value || currentProviderIsCodex.value)
const currentProviderKeyHelpTitle = computed(() => currentProviderIsCodex.value ? 'Codex CLI 访问 Token' : 'Ollama 访问 Key')
const currentProviderNativeKeyHelpText = computed(() => (
  currentProviderIsCodex.value
    ? 'Codex CLI 本身不需要这里的 Token。'
    : 'Ollama 原生接口本身不需要这里的 Key。'
))
const currentProviderAccessPolicyHelpText = computed(() => (
  currentProviderIsCodex.value
    ? '这是 CodeYun 在分发调用本机 Codex CLI 时增加的一层权限控制。管理员维护有效 Token，用户保存分发到自己的 Token。'
    : '这是 CodeYun 在调用本机 Ollama 前增加的一层权限控制。管理员维护有效 Key，用户保存分发到自己的 Key。'
))
const currentProviderSharingLabel = computed(() => {
  if (currentProviderSharingMode.value === 'public') {
    return '向所有登录用户开放'
  }
  if (currentProvider.value?.is_custom) {
    return '仅自己'
  }
  return ''
})
const customProviderKindOptions = computed(() => {
  const items = [{ label: 'OpenAI兼容', value: 'openai_compatible' }]
  if (isAdmin.value) {
    items.push({ label: 'Codex CLI', value: 'codex_cli' })
  }
  return items
})
const customProviderVisibilityOptions = [
  { label: '仅自己', value: 'private' },
  { label: '全开放', value: 'public' },
]
const customProviderConnectionLabel = computed(() => customProviderDraft.kind === 'codex_cli' ? '命令' : '地址')
const customProviderConnectionPlaceholder = computed(() => {
  if (customProviderDraft.kind === 'codex_cli') {
    return `默认 ${DEFAULT_CODEX_COMMAND}，也可以填 codex -p myprofile`
  }
  return '例如 https://example.com/v1'
})
const customProviderNamePlaceholder = computed(() => (
  customProviderDraft.kind === 'codex_cli' ? '例如我的 Codex' : '例如我的中转站'
))
const customProviderDefaultModelPlaceholder = computed(() => (
  customProviderDraft.kind === 'codex_cli'
    ? `默认已填 ${DEFAULT_CODEX_MODEL}`
    : '可选，留空后再在配置页单独填写'
))
const currentAppDefinition = computed(() => aiAppStore.getDefinition(selectedAppId.value))
const currentAppConfig = computed(() => aiAppStore.getAppConfig(selectedAppId.value))
const resolvedCurrentAppProviderId = computed(() =>
  currentAppConfig.value.provider.trim()
  || aiProviderStore.defaultProviderId
  || providers.value[0]?.id
  || ''
)
const currentAppEnabled = computed({
  get: () => currentAppConfig.value.enabled,
  set: (value: boolean) => {
    aiAppStore.updateAppConfig(selectedAppId.value, { enabled: value })
  },
})
const currentAppProviderId = computed({
  get: () => currentAppConfig.value.provider.trim() || resolvedCurrentAppProviderId.value,
  set: (value: string) => {
    aiAppStore.updateAppConfig(selectedAppId.value, {
      provider: value,
      model: currentAppConfig.value.provider.trim() === value ? currentAppConfig.value.model : '',
    })
  },
})
const currentAppModel = computed({
  get: () => currentAppConfig.value.model,
  set: (value: string) => {
    aiAppStore.updateAppConfig(selectedAppId.value, { model: value })
  },
})
const currentAppProvider = computed(() => aiProviderStore.getProviderById(resolvedCurrentAppProviderId.value))
const currentAppModelOptions = computed(() => (
  resolvedCurrentAppProviderId.value
    ? aiProviderStore.getEffectiveModels(resolvedCurrentAppProviderId.value)
    : []
))
const currentAppEffectiveModel = computed(() => currentAppConfig.value.model.trim() || currentAppModelOptions.value[0] || '')
const currentAppIsReady = computed(() => (
  currentAppEnabled.value
  && Boolean(resolvedCurrentAppProviderId.value)
  && Boolean(currentAppEffectiveModel.value)
  && aiProviderStore.hasEffectiveConnection(resolvedCurrentAppProviderId.value)
))

const currentBaseUrl = computed({
  get: () => aiProviderStore.getProviderConfig(selectedProviderId.value).baseUrl,
  set: (value: string) => {
    aiProviderStore.updateProviderConfig(selectedProviderId.value, { baseUrl: value })
  },
})

const currentBaseUrlInput = computed({
  get: () => getBaseUrlDraft(selectedProviderId.value),
  set: (value: string) => {
    setBaseUrlDraft(selectedProviderId.value, value)
  },
})

const currentApiKeyInput = computed({
  get: () => getApiKeyDraft(selectedProviderId.value),
  set: (value: string) => {
    setApiKeyDraft(selectedProviderId.value, value)
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
  () => [isAuthenticated.value, isAdmin.value],
  async () => {
    await loadProvidersAndStatus()
  }
)

watch(
  () => customProviderDraft.kind,
  kind => {
    if (kind !== 'codex_cli') {
      return
    }
    if (!customProviderDraft.baseUrl.trim()) {
      customProviderDraft.baseUrl = DEFAULT_CODEX_COMMAND
    }
    if (!customProviderDraft.defaultModel.trim()) {
      customProviderDraft.defaultModel = DEFAULT_CODEX_MODEL
    }
  }
)

onMounted(async () => {
  aiAppStore.ensureLoaded()
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

function handleProviderAssetChange(id: string) {
  assetMode.value = 'providers'
  void handleProviderChange(id)
}

function handleAppAssetChange(id: string) {
  assetMode.value = 'apps'
  selectedAppId.value = id as 'note-taxonomy'
}

function getBaseUrlDraft(providerId: string) {
  if (!providerId) {
    return ''
  }
  if (!(providerId in baseUrlDrafts)) {
    baseUrlDrafts[providerId] = ''
  }
  return baseUrlDrafts[providerId]
}

function setBaseUrlDraft(providerId: string, value: string) {
  if (!providerId) {
    return
  }
  baseUrlDrafts[providerId] = value
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
    getBaseUrlDraft(selectedProviderId.value)
    getApiKeyDraft(selectedProviderId.value)
    getNewModelDraft(selectedProviderId.value)
    await syncSystemAccessKeysForSelection()
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

function getAppSummaryModel(appId: string) {
  const config = aiAppStore.getAppConfig(appId as 'note-taxonomy')
  const providerId = config.provider.trim() || aiProviderStore.defaultProviderId || providers.value[0]?.id || ''
  const provider = aiProviderStore.getProviderById(providerId)
  const resolvedModel = config.model.trim() || (providerId ? aiProviderStore.getEffectiveModel(providerId) : '')
  if (!provider && !resolvedModel) {
    return '未绑定来源'
  }
  if (!provider) {
    return resolvedModel || '未绑定来源'
  }
  if (!resolvedModel) {
    return `${provider.label} / 未选模型`
  }
  return `${provider.label} / ${resolvedModel}`
}

function getProviderStateLabel(providerId: string) {
  if (currentProvider.value?.id === providerId && status.available) {
    return '可用'
  }
  if (aiProviderStore.hasEffectiveConnection(providerId) && aiProviderStore.getEffectiveModels(providerId).length) {
    return '可用'
  }
  return '待配置'
}

function getProviderStateClass(providerId: string) {
  const label = getProviderStateLabel(providerId)
  if (label === '可用') {
    return 'is-ready'
  }
  return 'is-pending'
}

function getAppStateLabel(appId: string) {
  const config = aiAppStore.getAppConfig(appId as 'note-taxonomy')
  if (!config.enabled) {
    return '已停用'
  }
  const providerId = config.provider.trim() || aiProviderStore.defaultProviderId || providers.value[0]?.id || ''
  const modelName = config.model.trim() || (providerId ? aiProviderStore.getEffectiveModel(providerId) : '')
  if (providerId && modelName && aiProviderStore.hasEffectiveConnection(providerId)) {
    return '已就绪'
  }
  return '待配置'
}

function getAppStateClass(appId: string) {
  const label = getAppStateLabel(appId)
  if (label === '已就绪') {
    return 'is-connected'
  }
  if (label === '已停用') {
    return 'is-neutral'
  }
  return 'is-pending'
}

function getCurrentAppStatusLabel() {
  return getAppStateLabel(selectedAppId.value)
}

function getCurrentAppStatusType() {
  const label = getCurrentAppStatusLabel()
  if (label === '已就绪') {
    return 'success'
  }
  if (label === '已停用') {
    return 'info'
  }
  return 'warning'
}

function clearCurrentAppModel() {
  aiAppStore.updateAppConfig(selectedAppId.value, { model: '' })
}

async function handleProviderChange(providerId: string) {
  clearInlineEditState()
  selectedProviderId.value = providerId
  getBaseUrlDraft(providerId)
  getApiKeyDraft(providerId)
  getNewModelDraft(providerId)
  await syncSystemAccessKeysForSelection()
  await refreshStatus(providerId)
}

function clearOllamaAccessKeyState() {
  ollamaAccessKeys.value = []
  for (const keyId of Object.keys(ollamaAccessKeyPlaintexts)) {
    delete ollamaAccessKeyPlaintexts[keyId]
  }
  for (const keyId of Object.keys(ollamaAccessKeyPlaintextVisible)) {
    delete ollamaAccessKeyPlaintextVisible[keyId]
  }
}

function clearCodexAccessKeyState() {
  codexAccessKeys.value = []
  for (const keyId of Object.keys(codexAccessKeyPlaintexts)) {
    delete codexAccessKeyPlaintexts[keyId]
  }
  for (const keyId of Object.keys(codexAccessKeyPlaintextVisible)) {
    delete codexAccessKeyPlaintextVisible[keyId]
  }
}

async function syncSystemAccessKeysForSelection() {
  await syncOllamaAccessKeysForSelection()
  await syncCodexAccessKeysForSelection()
}

async function syncOllamaAccessKeysForSelection() {
  if (!isAdmin.value || !currentProviderIsOllama.value) {
    clearOllamaAccessKeyState()
    return
  }
  await loadOllamaAccessKeys()
}

async function syncCodexAccessKeysForSelection() {
  if (!isAdmin.value || !currentProviderIsCodex.value || !currentProviderCanManage.value) {
    clearCodexAccessKeyState()
    return
  }
  await loadCodexAccessKeys()
}

async function loadOllamaAccessKeys() {
  if (!isAdmin.value || !currentProviderIsOllama.value) {
    clearOllamaAccessKeyState()
    return
  }

  ollamaAccessKeysLoading.value = true
  try {
    const payload = await fetchAiChatOllamaAccessKeys()
    ollamaAccessKeys.value = payload.items
  } catch (error) {
    clearOllamaAccessKeyState()
    ElMessage.error(getErrorMessage(error))
  } finally {
    ollamaAccessKeysLoading.value = false
  }
}

async function loadCodexAccessKeys() {
  if (!isAdmin.value || !currentProviderIsCodex.value || !currentProviderCanManage.value) {
    clearCodexAccessKeyState()
    return
  }

  codexAccessKeysLoading.value = true
  try {
    const payload = await fetchAiChatCodexAccessKeys()
    codexAccessKeys.value = payload.items
  } catch (error) {
    clearCodexAccessKeyState()
    ElMessage.error(getErrorMessage(error))
  } finally {
    codexAccessKeysLoading.value = false
  }
}

function getOllamaAccessKeyPlaintext(keyId: string) {
  return ollamaAccessKeyPlaintexts[keyId] || ''
}

function isOllamaAccessKeyPlaintextVisible(keyId: string) {
  return Boolean(ollamaAccessKeyPlaintextVisible[keyId])
}

function isCodexAccessKeyPlaintextVisible(keyId: string) {
  return Boolean(codexAccessKeyPlaintextVisible[keyId])
}

function getOllamaAccessKeyDisplayValue(keyId: string, maskedValue: string) {
  return isOllamaAccessKeyPlaintextVisible(keyId)
    ? getOllamaAccessKeyPlaintext(keyId) || maskedValue
    : maskedValue
}

function getCodexAccessKeyDisplayValue(keyId: string, maskedValue: string) {
  return isCodexAccessKeyPlaintextVisible(keyId)
    ? getCodexAccessKeyPlaintext(keyId) || maskedValue
    : maskedValue
}

function getCurrentSystemAccessKeyDisplayValue(keyId: string, maskedValue: string) {
  if (currentProviderUsesSystemCodexKeys.value) {
    return getCodexAccessKeyDisplayValue(keyId, maskedValue)
  }
  return getOllamaAccessKeyDisplayValue(keyId, maskedValue)
}

function getCurrentProviderActiveSavedKey() {
  return currentProviderSavedKeys.value.find(savedKey => savedKey.is_active) || null
}

function findCurrentProviderSavedKeyByMask(maskedValue: string) {
  const normalizedMask = maskedValue.trim()
  if (!normalizedMask) {
    return null
  }
  const matches = currentProviderSavedKeys.value.filter(savedKey => savedKey.masked_value === normalizedMask)
  if (!matches.length) {
    return null
  }
  return [...matches].sort((left, right) => (right.updated_at || 0) - (left.updated_at || 0))[0] || null
}

function isOllamaAccessKeyActive(accessKey: AiChatOllamaAccessKeySummary) {
  return getCurrentProviderActiveSavedKey()?.masked_value === accessKey.masked_value
}

function isCodexAccessKeyActive(accessKey: AiChatCodexAccessKeySummary) {
  return getCurrentProviderActiveSavedKey()?.masked_value === accessKey.masked_value
}

function isCurrentSystemAccessKeyActive(accessKey: AiChatSystemAccessKeySummary) {
  return getCurrentProviderActiveSavedKey()?.masked_value === accessKey.masked_value
}

function isCurrentSystemAccessKeyPlaintextVisible(keyId: string) {
  if (currentProviderUsesSystemCodexKeys.value) {
    return isCodexAccessKeyPlaintextVisible(keyId)
  }
  return isOllamaAccessKeyPlaintextVisible(keyId)
}

function isCurrentSystemAccessKeyRevealing(keyId: string) {
  return currentProviderUsesSystemCodexKeys.value
    ? revealingCodexAccessKeyId.value === keyId
    : revealingOllamaAccessKeyId.value === keyId
}

function isCurrentSystemAccessKeyDeleting(keyId: string) {
  return currentProviderUsesSystemCodexKeys.value
    ? deletingCodexAccessKeyId.value === keyId
    : deletingOllamaAccessKeyId.value === keyId
}

function getCodexAccessKeyPlaintext(keyId: string) {
  return codexAccessKeyPlaintexts[keyId] || ''
}

function buildProviderApiKeyPlaintextKey(providerId: string, keyId: string) {
  return `${providerId}::${keyId}`
}

function getProviderApiKeyPlaintext(providerId: string, keyId: string) {
  return providerApiKeyPlaintexts[buildProviderApiKeyPlaintextKey(providerId, keyId)] || ''
}

function getCurrentProviderKeyPlaintext(keyId: string) {
  return getProviderApiKeyPlaintext(selectedProviderId.value, keyId)
}

function isProviderKeyPlaintextVisible(providerId: string, keyId: string) {
  return Boolean(providerApiKeyPlaintextVisible[buildProviderApiKeyPlaintextKey(providerId, keyId)])
}

function isCurrentProviderKeyPlaintextVisible(keyId: string) {
  return isProviderKeyPlaintextVisible(selectedProviderId.value, keyId)
}

function getCurrentProviderKeyDisplayValue(keyId: string, maskedValue: string) {
  return isCurrentProviderKeyPlaintextVisible(keyId)
    ? getCurrentProviderKeyPlaintext(keyId) || maskedValue
    : maskedValue
}

function clearCurrentProviderApiKeyPlaintexts() {
  const prefix = `${selectedProviderId.value}::`
  for (const key of Object.keys(providerApiKeyPlaintexts)) {
    if (key.startsWith(prefix)) {
      delete providerApiKeyPlaintexts[key]
    }
  }
  for (const key of Object.keys(providerApiKeyPlaintextVisible)) {
    if (key.startsWith(prefix)) {
      delete providerApiKeyPlaintextVisible[key]
    }
  }
}

function clearInlineEditState() {
  editingProviderBaseUrlId.value = ''
  savingProviderBaseUrlId.value = ''
  providerBaseUrlEditDraft.value = ''
  providerBaseUrlEditOriginal.value = ''
  editingProviderKeyId.value = ''
  savingProviderKeyId.value = ''
  providerKeyEditDraft.value = ''
  providerKeyEditOriginal.value = ''
  editingPreferredModelIndex.value = null
  savingPreferredModelIndex.value = null
  preferredModelEditDraft.value = ''
  preferredModelEditOriginal.value = ''
}

function isEditingCurrentProviderBaseUrl(baseUrlId: string) {
  return editingProviderBaseUrlId.value === baseUrlId
}

function startEditCurrentProviderBaseUrl(savedBaseUrl: AiChatSavedBaseUrlSummary) {
  if (!isAuthenticated.value || currentProviderConnectionReadonly.value || !savedBaseUrl.id) {
    return
  }

  editingProviderKeyId.value = ''
  editingPreferredModelIndex.value = null
  providerBaseUrlEditOriginal.value = savedBaseUrl.value
  providerBaseUrlEditDraft.value = savedBaseUrl.value
  editingProviderBaseUrlId.value = savedBaseUrl.id
}

function cancelEditCurrentProviderBaseUrl(baseUrlId?: string) {
  if (baseUrlId && editingProviderBaseUrlId.value !== baseUrlId) {
    return
  }
  editingProviderBaseUrlId.value = ''
  providerBaseUrlEditDraft.value = ''
  providerBaseUrlEditOriginal.value = ''
}

async function finishEditCurrentProviderBaseUrl(baseUrlId: string) {
  if (!isAuthenticated.value || !selectedProviderId.value || editingProviderBaseUrlId.value !== baseUrlId) {
    return
  }
  if (savingProviderBaseUrlId.value === baseUrlId) {
    return
  }

  const normalizedBaseUrl = providerBaseUrlEditDraft.value.trim()
  if (!normalizedBaseUrl) {
    ElMessage.warning('地址不能为空')
    return
  }
  if (normalizedBaseUrl === providerBaseUrlEditOriginal.value.trim()) {
    cancelEditCurrentProviderBaseUrl(baseUrlId)
    return
  }

  const providerId = selectedProviderId.value
  let saved = false
  savingProviderBaseUrlId.value = baseUrlId
  try {
    await aiProviderStore.updateProviderBaseUrl(providerId, baseUrlId, normalizedBaseUrl)
    saved = true
    ElMessage.success('已更新地址')
    await refreshStatus(providerId)
  } catch (error) {
    ElMessage.error(getErrorMessage(error))
  } finally {
    savingProviderBaseUrlId.value = ''
    if (saved) {
      cancelEditCurrentProviderBaseUrl(baseUrlId)
    }
  }
}

function isEditingCurrentProviderKey(keyId: string) {
  return editingProviderKeyId.value === keyId
}

async function startEditCurrentProviderKey(savedKey: AiChatSavedApiKeySummary) {
  const providerId = selectedProviderId.value
  if (!isAuthenticated.value || !providerId || !savedKey.id) {
    return
  }
  if (revealingProviderKeyId.value === savedKey.id) {
    return
  }

  editingProviderBaseUrlId.value = ''
  editingPreferredModelIndex.value = null
  revealingProviderKeyId.value = savedKey.id
  try {
    const detail = await revealAiChatProviderKey(providerId, savedKey.id)
    if (selectedProviderId.value !== providerId) {
      return
    }
    const plaintextKey = buildProviderApiKeyPlaintextKey(providerId, savedKey.id)
    providerApiKeyPlaintexts[plaintextKey] = detail.plaintext_value
    providerKeyEditOriginal.value = detail.plaintext_value
    providerKeyEditDraft.value = detail.plaintext_value
    editingProviderKeyId.value = savedKey.id
  } catch (error) {
    ElMessage.error(getErrorMessage(error))
  } finally {
    revealingProviderKeyId.value = ''
  }
}

function cancelEditCurrentProviderKey(keyId?: string) {
  if (keyId && editingProviderKeyId.value !== keyId) {
    return
  }
  editingProviderKeyId.value = ''
  providerKeyEditDraft.value = ''
  providerKeyEditOriginal.value = ''
}

async function finishEditCurrentProviderKey(keyId: string) {
  if (!isAuthenticated.value || !selectedProviderId.value || editingProviderKeyId.value !== keyId) {
    return
  }
  if (savingProviderKeyId.value === keyId) {
    return
  }

  const normalizedApiKey = providerKeyEditDraft.value.trim()
  if (!normalizedApiKey) {
    ElMessage.warning('API Key 不能为空')
    return
  }
  if (normalizedApiKey === providerKeyEditOriginal.value.trim()) {
    cancelEditCurrentProviderKey(keyId)
    return
  }

  const providerId = selectedProviderId.value
  let saved = false
  savingProviderKeyId.value = keyId
  try {
    await aiProviderStore.updateProviderKey(providerId, keyId, normalizedApiKey)
    const plaintextKey = buildProviderApiKeyPlaintextKey(providerId, keyId)
    delete providerApiKeyPlaintexts[plaintextKey]
    delete providerApiKeyPlaintextVisible[plaintextKey]
    saved = true
    ElMessage.success('已更新 API Key')
    await refreshStatus(providerId)
  } catch (error) {
    ElMessage.error(getErrorMessage(error))
  } finally {
    savingProviderKeyId.value = ''
    if (saved) {
      cancelEditCurrentProviderKey(keyId)
    }
  }
}

function isEditingPreferredModel(index: number) {
  return editingPreferredModelIndex.value === index
}

function startEditPreferredModel(index: number, modelName: string) {
  if (!selectedProviderId.value) {
    return
  }

  editingProviderBaseUrlId.value = ''
  editingProviderKeyId.value = ''
  preferredModelEditOriginal.value = modelName
  preferredModelEditDraft.value = modelName
  editingPreferredModelIndex.value = index
}

function cancelEditPreferredModel(index?: number) {
  if (typeof index === 'number' && editingPreferredModelIndex.value !== index) {
    return
  }
  editingPreferredModelIndex.value = null
  preferredModelEditDraft.value = ''
  preferredModelEditOriginal.value = ''
}

async function finishEditPreferredModel(index: number) {
  if (!selectedProviderId.value || editingPreferredModelIndex.value !== index) {
    return
  }
  if (savingPreferredModelIndex.value === index) {
    return
  }

  const normalizedModelName = preferredModelEditDraft.value.trim()
  if (!normalizedModelName) {
    ElMessage.warning('模型名不能为空')
    return
  }
  if (normalizedModelName === preferredModelEditOriginal.value.trim()) {
    cancelEditPreferredModel(index)
    return
  }

  let saved = false
  savingPreferredModelIndex.value = index
  try {
    await updatePreferredModel(index, normalizedModelName)
    saved = true
  } finally {
    savingPreferredModelIndex.value = null
    if (saved) {
      cancelEditPreferredModel(index)
    }
  }
}

async function createOllamaAccessKeyWithPrompt() {
  if (!isAdmin.value) {
    return
  }

  generatingOllamaAccessKey.value = true
  try {
    const created = await createAiChatOllamaAccessKey()
    ollamaAccessKeyPlaintexts[created.id] = created.plaintext_value
    ollamaAccessKeyPlaintextVisible[created.id] = false
    await loadOllamaAccessKeys()
    ElMessage.success('已生成新的访问 Key')
  } catch (error) {
    ElMessage.error(getErrorMessage(error))
  } finally {
    generatingOllamaAccessKey.value = false
  }
}

async function createCodexAccessKeyWithPrompt() {
  if (!isAdmin.value || !currentProviderCanManage.value) {
    return
  }

  generatingCodexAccessKey.value = true
  try {
    const created = await createAiChatCodexAccessKey()
    codexAccessKeyPlaintexts[created.id] = created.plaintext_value
    codexAccessKeyPlaintextVisible[created.id] = false
    await loadCodexAccessKeys()
    ElMessage.success('已生成新的访问 Token')
  } catch (error) {
    ElMessage.error(getErrorMessage(error))
  } finally {
    generatingCodexAccessKey.value = false
  }
}

async function revealCurrentOllamaAccessKey(keyId: string) {
  if (!isAdmin.value || !keyId) {
    return ''
  }
  if (ollamaAccessKeyPlaintexts[keyId]) {
    return ollamaAccessKeyPlaintexts[keyId]
  }

  revealingOllamaAccessKeyId.value = keyId
  try {
    const detail = await revealAiChatOllamaAccessKey(keyId)
    ollamaAccessKeyPlaintexts[keyId] = detail.plaintext_value
    return detail.plaintext_value
  } catch (error) {
    ElMessage.error(getErrorMessage(error))
    return ''
  } finally {
    revealingOllamaAccessKeyId.value = ''
  }
}

async function toggleCurrentOllamaAccessKeyPlaintext(keyId: string) {
  if (!isAdmin.value || !keyId) {
    return
  }
  if (ollamaAccessKeyPlaintextVisible[keyId]) {
    ollamaAccessKeyPlaintextVisible[keyId] = false
    return
  }
  const plaintext = await revealCurrentOllamaAccessKey(keyId)
  if (plaintext) {
    ollamaAccessKeyPlaintextVisible[keyId] = true
  }
}

async function revealCurrentCodexAccessKey(keyId: string) {
  if (!isAdmin.value || !keyId) {
    return ''
  }
  if (codexAccessKeyPlaintexts[keyId]) {
    return codexAccessKeyPlaintexts[keyId]
  }

  revealingCodexAccessKeyId.value = keyId
  try {
    const detail = await revealAiChatCodexAccessKey(keyId)
    codexAccessKeyPlaintexts[keyId] = detail.plaintext_value
    return detail.plaintext_value
  } catch (error) {
    ElMessage.error(getErrorMessage(error))
    return ''
  } finally {
    revealingCodexAccessKeyId.value = ''
  }
}

async function toggleCurrentCodexAccessKeyPlaintext(keyId: string) {
  if (!isAdmin.value || !keyId) {
    return
  }
  if (codexAccessKeyPlaintextVisible[keyId]) {
    codexAccessKeyPlaintextVisible[keyId] = false
    return
  }
  const plaintext = await revealCurrentCodexAccessKey(keyId)
  if (plaintext) {
    codexAccessKeyPlaintextVisible[keyId] = true
  }
}

async function toggleCurrentSystemAccessKeyPlaintext(keyId: string) {
  if (currentProviderUsesSystemCodexKeys.value) {
    await toggleCurrentCodexAccessKeyPlaintext(keyId)
    return
  }
  await toggleCurrentOllamaAccessKeyPlaintext(keyId)
}

async function toggleCurrentProviderKeyPlaintext(keyId: string) {
  const providerId = selectedProviderId.value
  if (!isAuthenticated.value || !providerId || !keyId) {
    return
  }

  const plaintextKey = buildProviderApiKeyPlaintextKey(providerId, keyId)
  if (providerApiKeyPlaintextVisible[plaintextKey]) {
    providerApiKeyPlaintextVisible[plaintextKey] = false
    return
  }

  if (providerApiKeyPlaintexts[plaintextKey]) {
    providerApiKeyPlaintextVisible[plaintextKey] = true
    return
  }

  revealingProviderKeyId.value = keyId
  try {
    const detail = await revealAiChatProviderKey(providerId, keyId)
    providerApiKeyPlaintexts[plaintextKey] = detail.plaintext_value
    providerApiKeyPlaintextVisible[plaintextKey] = true
  } catch (error) {
    ElMessage.error(getErrorMessage(error))
  } finally {
    revealingProviderKeyId.value = ''
  }
}

async function deleteCurrentOllamaAccessKey(accessKey: AiChatOllamaAccessKeySummary) {
  if (!isAdmin.value || !accessKey.id) {
    return
  }

  try {
    await ElMessageBox.confirm('将删除这把系统访问 Key。已经分发出去的用户将无法继续使用它。', '删除系统访问 Key', {
      confirmButtonText: '删除',
      cancelButtonText: '取消',
      type: 'warning',
    })
  } catch {
    return
  }

  const providerId = selectedProviderId.value
  deletingOllamaAccessKeyId.value = accessKey.id
  try {
    await deleteAiChatOllamaAccessKey(accessKey.id)
    delete ollamaAccessKeyPlaintexts[accessKey.id]
    delete ollamaAccessKeyPlaintextVisible[accessKey.id]
    if (providerId && currentProviderIsOllama.value) {
      const savedKeys = currentProviderSavedKeys.value.filter(savedKey => savedKey.masked_value === accessKey.masked_value)
      for (const savedKey of savedKeys) {
        await aiProviderStore.deleteProviderKey(providerId, savedKey.id)
      }
    }
    await loadOllamaAccessKeys()
    ElMessage.success('已删除系统访问 Key')
    await refreshStatus(providerId)
  } catch (error) {
    ElMessage.error(getErrorMessage(error))
  } finally {
    deletingOllamaAccessKeyId.value = ''
  }
}

async function deleteCurrentCodexAccessKey(accessKey: AiChatCodexAccessKeySummary) {
  if (!isAdmin.value || !accessKey.id) {
    return
  }

  try {
    await ElMessageBox.confirm('将删除这把系统访问 Token。已经分发出去的用户将无法继续使用它。', '删除系统访问 Token', {
      confirmButtonText: '删除',
      cancelButtonText: '取消',
      type: 'warning',
    })
  } catch {
    return
  }

  const providerId = selectedProviderId.value
  deletingCodexAccessKeyId.value = accessKey.id
  try {
    await deleteAiChatCodexAccessKey(accessKey.id)
    delete codexAccessKeyPlaintexts[accessKey.id]
    delete codexAccessKeyPlaintextVisible[accessKey.id]
    if (providerId && currentProviderIsCodex.value) {
      const savedKeys = currentProviderSavedKeys.value.filter(savedKey => savedKey.masked_value === accessKey.masked_value)
      for (const savedKey of savedKeys) {
        await aiProviderStore.deleteProviderKey(providerId, savedKey.id)
      }
    }
    await loadCodexAccessKeys()
    ElMessage.success('已删除系统访问 Token')
    await refreshStatus(providerId)
  } catch (error) {
    ElMessage.error(getErrorMessage(error))
  } finally {
    deletingCodexAccessKeyId.value = ''
  }
}

async function deleteCurrentSystemAccessKey(accessKey: AiChatSystemAccessKeySummary) {
  if (currentProviderUsesSystemCodexKeys.value) {
    await deleteCurrentCodexAccessKey(accessKey as AiChatCodexAccessKeySummary)
    return
  }
  await deleteCurrentOllamaAccessKey(accessKey as AiChatOllamaAccessKeySummary)
}

async function saveCurrentProviderConfig(options: { includeApiKey?: boolean; silent?: boolean; apiKey?: string; baseUrl?: string | null } = {}) {
  if (!isAuthenticated.value || !selectedProviderId.value) {
    return
  }

  const includeApiKey = options.includeApiKey ?? true
  const silent = options.silent ?? false
  const normalizedApiKey = includeApiKey ? (options.apiKey ?? '').trim() : ''
  if (includeApiKey) {
    aiProviderStore.updateProviderConfig(selectedProviderId.value, { apiKey: normalizedApiKey })
  }
  savingProviderConfig.value = true
  try {
    const hadDraftApiKey = includeApiKey && Boolean(normalizedApiKey)
    await aiProviderStore.saveProviderConfig(selectedProviderId.value, {
      includeApiKey,
      baseUrl: options.baseUrl,
    })
    if (!silent) {
      ElMessage.success(hadDraftApiKey ? '已保存新 Key 到账号' : '已自动保存')
    }
    await refreshStatus(selectedProviderId.value)
  } catch (error) {
    ElMessage.error(getErrorMessage(error))
  } finally {
    savingProviderConfig.value = false
  }
}

async function saveCurrentProviderBaseUrl() {
  if (!isAuthenticated.value || !selectedProviderId.value || currentProviderConnectionReadonly.value) {
    return
  }

  const normalizedBaseUrl = currentBaseUrlInput.value.trim()
  if (!normalizedBaseUrl) {
    return
  }

  savingProviderConfig.value = true
  try {
    await aiProviderStore.saveProviderConfig(selectedProviderId.value, {
      includeApiKey: false,
      baseUrl: normalizedBaseUrl,
    })
    setBaseUrlDraft(selectedProviderId.value, '')
    ElMessage.success('已保存地址')
    await refreshStatus(selectedProviderId.value)
  } catch (error) {
    ElMessage.error(getErrorMessage(error))
  } finally {
    savingProviderConfig.value = false
  }
}

async function openAddCurrentProviderBaseUrlDialog() {
  if (!isAuthenticated.value || !selectedProviderId.value || currentProviderConnectionReadonly.value) {
    return
  }

  try {
    const result = await ElMessageBox.prompt(
      '',
      `新增${currentProviderConnectionFieldLabel.value}`,
      {
        confirmButtonText: '保存',
        cancelButtonText: '取消',
        customClass: 'ai-config-compact-prompt',
        inputValue: currentBaseUrlInput.value,
        inputPlaceholder: currentProviderConnectionPlaceholder.value,
        inputValidator: (value: string) => Boolean((value || '').trim()) || `${currentProviderConnectionFieldLabel.value}不能为空`,
      },
    )
    currentBaseUrlInput.value = result.value || ''
    await saveCurrentProviderBaseUrl()
  } catch {
    return
  }
}

async function handleProviderBaseUrlChange() {
  if (currentProviderConnectionReadonly.value) {
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
    })
    setApiKeyDraft(selectedProviderId.value, '')
    return
  }

  aiProviderStore.updateProviderConfig(selectedProviderId.value, { apiKey: normalizedApiKey })
  ElMessage.success('已保存到本地')
  await refreshStatus(selectedProviderId.value)
}

async function openAddCurrentProviderApiKeyDialog() {
  if (!selectedProviderId.value) {
    return
  }

  try {
    const result = await ElMessageBox.prompt(
      '',
      `新增${currentProviderKeyFieldLabel.value}`,
      {
        confirmButtonText: '保存',
        cancelButtonText: '取消',
        customClass: 'ai-config-compact-prompt',
        inputType: 'password',
        inputValue: currentApiKeyInput.value,
        inputPlaceholder: currentProviderKeyInputPlaceholder.value,
        inputValidator: (value: string) => Boolean((value || '').trim()) || `${currentProviderKeyFieldLabel.value}不能为空`,
      },
    )
    currentApiKeyInput.value = result.value || ''
    await saveCurrentProviderApiKey()
  } catch {
    return
  }
}

async function handleAddCurrentProviderKey() {
  if (currentProviderUsesSystemOllamaKeys.value) {
    await createOllamaAccessKeyWithPrompt()
    return
  }
  if (currentProviderUsesSystemCodexKeys.value) {
    await createCodexAccessKeyWithPrompt()
    return
  }
  await openAddCurrentProviderApiKeyDialog()
}

async function activateOllamaAccessKeyForCurrentProvider(accessKey: AiChatOllamaAccessKeySummary) {
  const providerId = selectedProviderId.value
  if (!currentProviderUsesSystemOllamaKeys.value || !isAuthenticated.value || !providerId || !accessKey.id) {
    return
  }
  if (isOllamaAccessKeyActive(accessKey)) {
    return
  }

  activatingProviderKeyId.value = accessKey.id
  try {
    const existingSavedKey = findCurrentProviderSavedKeyByMask(accessKey.masked_value)
    if (existingSavedKey) {
      await aiProviderStore.activateProviderKey(providerId, existingSavedKey.id)
      ElMessage.success('已切换访问 Key')
      await refreshStatus(providerId)
      return
    }

    const plaintext = await revealCurrentOllamaAccessKey(accessKey.id)
    if (!plaintext || selectedProviderId.value !== providerId) {
      return
    }

    aiProviderStore.updateProviderConfig(providerId, { apiKey: plaintext })
    await aiProviderStore.saveProviderConfig(providerId, { includeApiKey: true })
    setApiKeyDraft(providerId, '')
    if (selectedProviderId.value !== providerId) {
      return
    }
    const savedKey = findCurrentProviderSavedKeyByMask(accessKey.masked_value)
    if (savedKey) {
      await aiProviderStore.activateProviderKey(providerId, savedKey.id)
    }
    ElMessage.success('已切换访问 Key')
    await refreshStatus(providerId)
  } catch (error) {
    ElMessage.error(getErrorMessage(error))
  } finally {
    activatingProviderKeyId.value = ''
  }
}

async function activateCodexAccessKeyForCurrentProvider(accessKey: AiChatCodexAccessKeySummary) {
  const providerId = selectedProviderId.value
  if (!currentProviderUsesSystemCodexKeys.value || !isAuthenticated.value || !providerId || !accessKey.id) {
    return
  }
  if (isCodexAccessKeyActive(accessKey)) {
    return
  }

  activatingProviderKeyId.value = accessKey.id
  try {
    const existingSavedKey = findCurrentProviderSavedKeyByMask(accessKey.masked_value)
    if (existingSavedKey) {
      await aiProviderStore.activateProviderKey(providerId, existingSavedKey.id)
      ElMessage.success('已切换访问 Token')
      await refreshStatus(providerId)
      return
    }

    const plaintext = await revealCurrentCodexAccessKey(accessKey.id)
    if (!plaintext || selectedProviderId.value !== providerId) {
      return
    }

    aiProviderStore.updateProviderConfig(providerId, { apiKey: plaintext })
    await aiProviderStore.saveProviderConfig(providerId, { includeApiKey: true })
    setApiKeyDraft(providerId, '')
    if (selectedProviderId.value !== providerId) {
      return
    }
    const savedKey = findCurrentProviderSavedKeyByMask(accessKey.masked_value)
    if (savedKey) {
      await aiProviderStore.activateProviderKey(providerId, savedKey.id)
    }
    ElMessage.success('已切换访问 Token')
    await refreshStatus(providerId)
  } catch (error) {
    ElMessage.error(getErrorMessage(error))
  } finally {
    activatingProviderKeyId.value = ''
  }
}

async function activateCurrentSystemAccessKeyForCurrentProvider(accessKey: AiChatSystemAccessKeySummary) {
  if (currentProviderUsesSystemCodexKeys.value) {
    await activateCodexAccessKeyForCurrentProvider(accessKey as AiChatCodexAccessKeySummary)
    return
  }
  await activateOllamaAccessKeyForCurrentProvider(accessKey as AiChatOllamaAccessKeySummary)
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
  if (!selectedProviderId.value) {
    return
  }

  const normalizedModelName = value.trim()
  if (!normalizedModelName) {
    ElMessage.warning('模型名不能为空')
    return
  }
  const currentModelName = (currentPreferredModels.value[index] || '').trim()
  if (normalizedModelName === currentModelName) {
    return
  }

  const nextModels = [...currentPreferredModels.value]
  nextModels[index] = normalizedModelName
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

  cancelEditPreferredModel()
  aiProviderStore.updateProviderConfig(selectedProviderId.value, {
    preferredModels: [...currentPreferredModels.value, modelName],
  })
  setNewModelDraft(selectedProviderId.value, '')
  await handlePreferredModelsChange()
}

async function openAddPreferredModelDialog() {
  if (!selectedProviderId.value) {
    return
  }

  try {
    const result = await ElMessageBox.prompt('', '新增模型', {
      confirmButtonText: '新增',
      cancelButtonText: '取消',
      customClass: 'ai-config-compact-prompt',
      inputValue: currentNewModelInput.value,
      inputPlaceholder: '例如 deepseek-chat',
      inputValidator: (value: string) => Boolean((value || '').trim()) || '模型名不能为空',
    })
    currentNewModelInput.value = result.value || ''
    await addPreferredModel()
  } catch {
    return
  }
}

async function removePreferredModel(index: number) {
  if (!selectedProviderId.value) {
    return
  }
  cancelEditPreferredModel()
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

  cancelEditPreferredModel()
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
    setBaseUrlDraft(selectedProviderId.value, '')
    setApiKeyDraft(selectedProviderId.value, '')
    clearCurrentProviderApiKeyPlaintexts()
    clearInlineEditState()
    ElMessage.success('已清除账号中的来源配置')
    await refreshStatus(selectedProviderId.value)
  } catch (error) {
    ElMessage.error(getErrorMessage(error))
  } finally {
    removingProviderConfig.value = false
  }
}

async function activateCurrentProviderBaseUrl(baseUrlId: string) {
  if (!isAuthenticated.value || !selectedProviderId.value) {
    return
  }
  if (currentProviderSavedBaseUrls.value.some(item => item.id === baseUrlId && item.is_active)) {
    return
  }

  activatingProviderBaseUrlId.value = baseUrlId
  try {
    await aiProviderStore.activateProviderBaseUrl(selectedProviderId.value, baseUrlId)
    ElMessage.success('已切换激活地址')
    await refreshStatus(selectedProviderId.value)
  } catch (error) {
    ElMessage.error(getErrorMessage(error))
  } finally {
    activatingProviderBaseUrlId.value = ''
  }
}

async function deleteCurrentProviderBaseUrl(baseUrlId: string) {
  if (!isAuthenticated.value || !selectedProviderId.value) {
    return
  }

  try {
    await ElMessageBox.confirm('将删除这个已保存的地址。', '删除地址', {
      confirmButtonText: '删除',
      cancelButtonText: '取消',
      type: 'warning',
    })
  } catch {
    return
  }

  deletingProviderBaseUrlId.value = baseUrlId
  try {
    await aiProviderStore.deleteProviderBaseUrl(selectedProviderId.value, baseUrlId)
    cancelEditCurrentProviderBaseUrl(baseUrlId)
    ElMessage.success('已删除保存的地址')
    await refreshStatus(selectedProviderId.value)
  } catch (error) {
    ElMessage.error(getErrorMessage(error))
  } finally {
    deletingProviderBaseUrlId.value = ''
  }
}

async function activateCurrentProviderKey(keyId: string) {
  if (!isAuthenticated.value || !selectedProviderId.value) {
    return
  }
  if (currentProviderSavedKeys.value.some(item => item.id === keyId && item.is_active)) {
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
    const plaintextKey = buildProviderApiKeyPlaintextKey(selectedProviderId.value, keyId)
    delete providerApiKeyPlaintexts[plaintextKey]
    delete providerApiKeyPlaintextVisible[plaintextKey]
    cancelEditCurrentProviderKey(keyId)
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
    status.sharing_mode = providerMeta?.sharing_mode || 'builtin'
    status.can_manage = providerMeta?.can_manage ?? false
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
  customProviderDraft.kind = 'openai_compatible'
  customProviderDraft.visibility = 'private'
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
    ElMessage.warning(customProviderDraft.kind === 'codex_cli' ? '请先填写 Codex 命令' : '请先填写自定义来源地址')
    return
  }

  creatingCustomProvider.value = true
  try {
    const created = await aiProviderStore.createCustomProvider({
      label: customProviderDraft.label.trim(),
      kind: customProviderDraft.kind,
      visibility: customProviderDraft.visibility,
      base_url: customProviderDraft.baseUrl.trim(),
      default_model: customProviderDraft.defaultModel.trim() || undefined,
      models: [],
    })
    customProviderDialogVisible.value = false
    await aiProviderStore.loadProviders(isAuthenticated.value)
    selectedProviderId.value = created.id
    setApiKeyDraft(created.id, '')
    setNewModelDraft(created.id, '')
    await refreshStatus(created.id)
    ElMessage.success('已新增来源')
  } catch (error) {
    ElMessage.error(getErrorMessage(error))
  } finally {
    creatingCustomProvider.value = false
  }
}

async function deleteCurrentCustomProvider() {
  if (!isAuthenticated.value || !currentProvider.value?.is_custom || !currentProviderCanManage.value) {
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
  padding: 20px;
  box-sizing: border-box;
  background: linear-gradient(180deg, #f7fafc 0%, #eef3f6 100%);
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.panel-card {
  border-radius: 8px;
  border: 1px solid rgba(203, 213, 225, 0.82);
  background: rgba(255, 255, 255, 0.94);
  box-shadow: 0 12px 30px rgba(15, 23, 42, 0.06);
}

.hero-panel {
  padding: 2px 2px 4px;
}

.eyebrow,
.panel-kicker {
  margin: 0;
  font-size: 12px;
  letter-spacing: 0;
  color: #64748b;
}

.hero-copy h1,
.panel-header h2 {
  margin: 4px 0 0;
  font-size: 22px;
  line-height: 1.15;
  color: #0f172a;
}

.workspace-grid {
  display: grid;
  grid-template-columns: minmax(260px, 320px) minmax(0, 1fr);
  gap: 14px;
  min-height: 0;
  flex: 1;
}

.provider-panel,
.editor-panel {
  min-height: 0;
}

.panel-card {
  padding: 18px;
  height: 100%;
  box-sizing: border-box;
}

.provider-panel .panel-card {
  display: flex;
  flex-direction: column;
}

.panel-header {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: flex-start;
  margin-bottom: 14px;
}

.editor-header {
  justify-content: flex-end;
}

.panel-header-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 6px;
  flex-wrap: wrap;
}

.asset-add-button {
  height: 36px;
  padding: 0 16px;
  border-radius: 8px;
  font-weight: 600;
}

.provider-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.app-list {
  margin-top: 8px;
}

.provider-footer {
  padding: 10px 0 4px;
  display: flex;
  justify-content: flex-start;
}

.provider-item {
  width: 100%;
  border: 1px solid rgba(203, 213, 225, 0.86);
  background: #fff;
  border-radius: 8px;
  padding: 12px 14px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  text-align: left;
  cursor: pointer;
  transition: border-color 0.16s ease, box-shadow 0.16s ease, background-color 0.16s ease;
}

.provider-item:hover {
  border-color: rgba(59, 130, 246, 0.58);
  box-shadow: 0 8px 18px rgba(37, 99, 235, 0.08);
}

.provider-item.active {
  border-color: rgba(14, 116, 144, 0.66);
  box-shadow: inset 3px 0 0 rgba(14, 116, 144, 0.75);
  background: #f0f9ff;
}

.provider-item-main {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.provider-item-label {
  font-size: 15px;
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
  padding: 3px 8px;
  border-radius: 6px;
  font-size: 12px;
  font-weight: 600;
}

.provider-item-state.is-connected {
  color: #0f766e;
  background: #ccfbf1;
}

.provider-item-state.is-ready {
  color: #15803d;
  background: #dcfce7;
}

.provider-item-state.is-pending {
  color: #92400e;
  background: #fef3c7;
}

.provider-item-state.is-neutral {
  color: #475569;
  background: #e2e8f0;
}

.settings-form :deep(.el-form-item) {
  margin-bottom: 16px;
}

.settings-form :deep(.el-form-item__label) {
  width: 100%;
}

.form-section-label {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  width: 100%;
  color: #475569;
}

.form-section-label-main {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  min-width: 0;
}

.section-add-button {
  width: 22px;
  height: 22px;
  min-height: 22px;
  padding: 0;
  color: #2563eb;
}

.section-add-button :deep(.el-icon) {
  font-size: 15px;
}

.section-help-button {
  width: 22px;
  height: 22px;
  min-height: 22px;
  padding: 0;
  color: #64748b;
}

.section-help-button:hover,
.section-help-button:focus {
  color: #2563eb;
}

.section-check-button {
  min-height: 24px;
  padding: 0 4px;
  color: #334155;
}

.base-url-editor {
  display: flex;
  flex-direction: column;
  gap: 8px;
  width: 100%;
}

.saved-base-url-section {
  margin-top: 0;
}

.saved-base-url-meta {
  flex: 1;
}

.saved-base-url-value {
  color: #475569;
  font-size: 13px;
  word-break: break-all;
}

.editable-list-value {
  min-width: 0;
  cursor: text;
}

.editable-list-value:hover {
  color: #1d4ed8;
}

.saved-inline-edit {
  flex: 1;
  min-width: min(100%, 360px);
}

.saved-key-edit-input {
  min-width: min(100%, 420px);
}

.api-key-input-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  gap: 8px;
  width: 100%;
}

.api-key-secret-input {
  min-width: 0;
}

.api-key-save-button {
  min-width: 76px;
}

.app-toggle-row,
.app-model-row {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  flex-wrap: wrap;
}

.app-provider-select,
.app-model-select {
  width: 100%;
}

.app-model-row :deep(.el-select) {
  flex: 1;
  min-width: 220px;
}

.model-list-editor {
  display: flex;
  flex-direction: column;
  gap: 8px;
  width: 100%;
}

.model-list {
  display: flex;
  flex-direction: column;
  gap: 0;
}

.model-list-row {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 7px 0;
  background: transparent;
}

.model-list-row + .model-list-row {
  border-top: 1px solid rgba(226, 232, 240, 0.9);
}

.model-list-row :deep(.el-input) {
  flex: 1;
}

.model-name-text {
  flex: 1;
  min-width: 0;
  padding: 3px 0;
  color: #334155;
  font-size: 14px;
  line-height: 24px;
  word-break: break-all;
}

.model-inline-edit {
  flex: 1;
  min-width: 0;
}

.model-list-actions {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.row-remove-button {
  width: 22px;
  height: 22px;
  min-height: 22px;
  padding: 0;
  color: #ef4444;
}

.row-remove-button:hover,
.row-remove-button:focus {
  background: rgba(239, 68, 68, 0.08);
  color: #dc2626;
}

.row-remove-button :deep(.el-icon) {
  font-size: 14px;
}

.model-list-empty {
  padding: 10px 12px;
  border: 1px dashed rgba(203, 213, 225, 0.95);
  border-radius: 8px;
  background: #f8fafc;
  color: #64748b;
  font-size: 13px;
}

.account-config-row {
  display: flex;
  justify-content: flex-end;
  margin-top: 6px;
}

.saved-key-section {
  margin-top: 6px;
  display: flex;
  flex-direction: column;
  gap: 0;
  width: 100%;
}

.saved-key-list {
  display: flex;
  flex-direction: column;
  gap: 0;
  width: 100%;
}

.saved-key-item {
  border: 0;
  border-radius: 0;
  background: transparent;
  padding: 7px 0;
  width: 100%;
  box-sizing: border-box;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.saved-key-item + .saved-key-item {
  border-top: 1px solid rgba(226, 232, 240, 0.9);
}

.saved-choice-item {
  cursor: pointer;
  transition: background-color 0.16s ease;
}

.saved-choice-item:hover {
  background: #f8fafc;
}

.saved-choice-item:focus-visible {
  outline: 2px solid rgba(37, 99, 235, 0.42);
  outline-offset: 2px;
}

.saved-choice-item.active {
  background: transparent;
}

.saved-choice-item.active .saved-key-label {
  color: #1d4ed8;
}

.saved-choice-radio {
  flex: 0 0 auto;
  width: 15px;
  height: 15px;
  margin: 0;
  cursor: pointer;
  accent-color: #2563eb;
}

.saved-choice-radio:disabled {
  cursor: default;
}

.saved-key-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  min-width: 0;
}

.saved-key-label {
  font-weight: 600;
  color: #0f172a;
}

.saved-key-mask {
  font-family: Consolas, 'Courier New', monospace;
  font-size: 12px;
  color: #475569;
  padding: 2px 5px;
  border-radius: 6px;
  background: #f8fafc;
  word-break: break-all;
}

.saved-key-actions {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  margin-left: auto;
}

.saved-key-eye-button {
  color: #64748b;
  padding: 0 4px;
}

.key-help-content {
  display: flex;
  flex-direction: column;
  gap: 8px;
  color: #475569;
  font-size: 13px;
  line-height: 1.55;
}

.key-help-content p {
  margin: 0;
}

.key-help-title {
  color: #0f172a;
  font-weight: 600;
}

.status-alert {
  margin-top: 4px;
}

:global(.ai-config-compact-prompt .el-message-box__message) {
  display: none;
}

:global(.ai-config-compact-prompt .el-message-box__input) {
  padding-top: 0;
}

:global(.ai-config-key-help-popover) {
  padding: 12px;
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

  .hero-panel {
    padding: 0;
  }

  .panel-card {
    border-radius: 8px;
  }

  .panel-card {
    padding: 14px;
  }

  .api-key-input-row {
    grid-template-columns: 1fr;
  }

  .api-key-save-button {
    width: 100%;
  }

  .model-list-row {
    flex-wrap: wrap;
  }
}
</style>
