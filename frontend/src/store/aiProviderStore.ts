import { defineStore } from 'pinia'

import {
  activateAiChatProviderKey,
  createAiChatCustomProvider,
  deleteAiChatCustomProvider,
  deleteAiChatProviderConfig,
  deleteAiChatProviderKey,
  fetchAiChatProviders,
  fetchAiChatSavedConfigs,
  saveAiChatProviderConfig,
  type AiChatCreateCustomProviderRequest,
  type AiChatProviderSummary,
  type AiChatProvidersResponse,
  type AiChatSavedApiKeySummary,
  type AiChatSavedProviderConfig,
  type AiChatStatusResponse,
} from '@/api/aiChat'

const LOCAL_PROVIDER_CONFIG_STORAGE_KEY = 'codeyun_ai_provider_configs_v1'
const OLLAMA_MODEL_ALIASES = [
  {
    alias: 'qwen3.5:4b-instruct',
    runtimeModel: 'qwen3.5:4b',
  },
]

export interface AiProviderRuntimeConfig {
  baseUrl: string
  apiKey: string
  preferredModels: string[]
  hasSavedApiKey: boolean
  hasAccountConfig: boolean
  activeKeyId?: string | null
  savedKeys: AiChatSavedApiKeySummary[]
  updatedAt?: number | null
}

interface LocalProviderConfigItem {
  base_url?: string
  api_key?: string
  preferred_model?: string
  preferred_models?: string[]
}

interface LocalProviderConfigsPayload {
  version: 1
  providers: Record<string, LocalProviderConfigItem>
}

function buildEmptyProviderConfig(): AiProviderRuntimeConfig {
  return {
    baseUrl: '',
    apiKey: '',
    preferredModels: [],
    hasSavedApiKey: false,
    hasAccountConfig: false,
    activeKeyId: null,
    savedKeys: [],
    updatedAt: null,
  }
}

function canUseLocalStorage() {
  return typeof window !== 'undefined' && typeof window.localStorage !== 'undefined'
}

function dedupeModelNames(items: string[]) {
  const seen = new Set<string>()
  const normalized: string[] = []
  for (const item of items) {
    const modelName = item.trim()
    if (!modelName || seen.has(modelName)) {
      continue
    }
    seen.add(modelName)
    normalized.push(modelName)
  }
  return normalized
}

function areModelListsEqual(left: string[], right: string[]) {
  return left.length === right.length && left.every((item, index) => item === right[index])
}

function decorateProviderModelList(providerId: string | null | undefined, items: string[]) {
  const normalized = dedupeModelNames(items)
  if ((providerId || '').trim().toLowerCase() !== 'ollama') {
    return normalized
  }

  for (const item of OLLAMA_MODEL_ALIASES) {
    const aliasIndex = normalized.indexOf(item.alias)
    if (aliasIndex >= 0) {
      continue
    }
    const runtimeIndex = normalized.indexOf(item.runtimeModel)
    if (runtimeIndex < 0) {
      continue
    }
    normalized.splice(runtimeIndex, 0, item.alias)
  }

  return normalized
}

function getProviderDefaultModelList(provider: AiChatProviderSummary | null) {
  if (!provider) {
    return []
  }
  return decorateProviderModelList(provider.id, [
    provider.default_model,
    ...provider.models,
  ].filter((item): item is string => typeof item === 'string'))
}

function loadLocalProviderConfigs(): Record<string, LocalProviderConfigItem> {
  if (!canUseLocalStorage()) {
    return {}
  }

  try {
    const raw = window.localStorage.getItem(LOCAL_PROVIDER_CONFIG_STORAGE_KEY)
    if (!raw) {
      return {}
    }
    const payload = JSON.parse(raw) as Partial<LocalProviderConfigsPayload>
    if (!payload || typeof payload !== 'object' || typeof payload.providers !== 'object' || !payload.providers) {
      return {}
    }
    return payload.providers
  } catch {
    return {}
  }
}

function persistLocalProviderConfigs(payload: Record<string, LocalProviderConfigItem>) {
  if (!canUseLocalStorage()) {
    return
  }

  const normalizedEntries = Object.entries(payload)
    .map(([providerId, item]) => {
      if (!providerId.trim() || !item || typeof item !== 'object') {
        return null
      }

      const normalizedItem: LocalProviderConfigItem = {}
      const baseUrl = typeof item.base_url === 'string' ? item.base_url.trim() : ''
      const apiKey = typeof item.api_key === 'string' ? item.api_key.trim() : ''
      const preferredModels = Array.isArray(item.preferred_models)
        ? dedupeModelNames(item.preferred_models.filter((model): model is string => typeof model === 'string'))
        : []

      if (baseUrl) {
        normalizedItem.base_url = baseUrl
      }
      if (apiKey) {
        normalizedItem.api_key = apiKey
      }
      if (preferredModels.length) {
        normalizedItem.preferred_models = preferredModels
      }

      if (!Object.keys(normalizedItem).length) {
        return null
      }

      return [providerId.trim(), normalizedItem] as const
    })
    .filter((entry): entry is readonly [string, LocalProviderConfigItem] => Boolean(entry))

  if (!normalizedEntries.length) {
    window.localStorage.removeItem(LOCAL_PROVIDER_CONFIG_STORAGE_KEY)
    return
  }

  const normalizedPayload: LocalProviderConfigsPayload = {
    version: 1,
    providers: Object.fromEntries(normalizedEntries),
  }
  window.localStorage.setItem(LOCAL_PROVIDER_CONFIG_STORAGE_KEY, JSON.stringify(normalizedPayload))
}

export const useAiProviderStore = defineStore('aiProvider', {
  state: () => ({
    defaultProviderId: 'ollama',
    providers: [] as AiChatProviderSummary[],
    providerConfigs: {} as Record<string, AiProviderRuntimeConfig>,
    loadedForAuthState: null as boolean | null,
  }),

  getters: {
    providerMap: state => Object.fromEntries(state.providers.map(provider => [provider.id, provider])),
  },

  actions: {
    getProviderById(providerId: string) {
      return this.providers.find(provider => provider.id === providerId) ?? null
    },

    getProviderConfig(providerId: string): AiProviderRuntimeConfig {
      return this.providerConfigs[providerId] ?? buildEmptyProviderConfig()
    },

    updateProviderConfig(providerId: string, patch: Partial<AiProviderRuntimeConfig>) {
      if (!providerId) {
        return
      }

      const nextConfig = {
        ...this.getProviderConfig(providerId),
        ...patch,
      }
      nextConfig.preferredModels = dedupeModelNames(nextConfig.preferredModels ?? [])

      this.providerConfigs[providerId] = nextConfig

      if (this.loadedForAuthState === false) {
        this.persistAnonymousProviderConfigs()
      }
    },

    applyProviders(payload: AiChatProvidersResponse) {
      this.defaultProviderId = payload.default_provider
      this.providers = payload.items

      const knownProviderIds = new Set(payload.items.map(item => item.id))
      for (const providerId of Object.keys(this.providerConfigs)) {
        if (!knownProviderIds.has(providerId)) {
          delete this.providerConfigs[providerId]
        }
      }

      for (const provider of payload.items) {
        const current = this.getProviderConfig(provider.id)
        this.providerConfigs[provider.id] = {
          ...current,
          baseUrl: current.baseUrl.trim() || provider.base_url || '',
          preferredModels: current.preferredModels.length ? dedupeModelNames(current.preferredModels) : getProviderDefaultModelList(provider),
        }
      }
    },

    clearSavedProviderMetadata() {
      for (const provider of this.providers) {
        const current = this.getProviderConfig(provider.id)
        this.providerConfigs[provider.id] = {
          ...current,
          hasSavedApiKey: false,
          hasAccountConfig: false,
          activeKeyId: null,
          savedKeys: [],
          updatedAt: null,
        }
      }
    },

    applyLocalProviderConfigs() {
      const localConfigs = loadLocalProviderConfigs()
      for (const [providerId, item] of Object.entries(localConfigs)) {
        if (!this.providers.some(provider => provider.id === providerId)) {
          continue
        }
        const current = this.getProviderConfig(providerId)
        this.providerConfigs[providerId] = {
          ...current,
          baseUrl: typeof item.base_url === 'string' ? item.base_url.trim() : current.baseUrl,
          apiKey: typeof item.api_key === 'string' ? item.api_key.trim() : current.apiKey,
          preferredModels: Array.isArray(item.preferred_models)
            ? dedupeModelNames(item.preferred_models.filter((model): model is string => typeof model === 'string'))
            : (typeof item.preferred_model === 'string' && item.preferred_model.trim()
                ? [item.preferred_model.trim()]
                : current.preferredModels),
        }
      }
    },

    persistAnonymousProviderConfigs() {
      const payload: Record<string, LocalProviderConfigItem> = {}
      for (const provider of this.providers) {
        const current = this.getProviderConfig(provider.id)
        const localItem: LocalProviderConfigItem = {}
        const baseUrl = current.baseUrl.trim()
        const apiKey = current.apiKey.trim()
        const preferredModels = dedupeModelNames(current.preferredModels)
        const defaultModelList = getProviderDefaultModelList(provider)

        if (baseUrl && baseUrl !== provider.base_url.trim()) {
          localItem.base_url = baseUrl
        }
        if (apiKey) {
          localItem.api_key = apiKey
        }
        if (JSON.stringify(preferredModels) !== JSON.stringify(defaultModelList)) {
          localItem.preferred_models = preferredModels
        }

        if (Object.keys(localItem).length) {
          payload[provider.id] = localItem
        }
      }
      persistLocalProviderConfigs(payload)
    },

    applySavedProviderConfig(item: AiChatSavedProviderConfig) {
      const providerMeta = this.getProviderById(item.provider)
      const current = this.getProviderConfig(item.provider)
      this.providerConfigs[item.provider] = {
        ...current,
        baseUrl: item.base_url || providerMeta?.base_url || current.baseUrl,
        preferredModels: dedupeModelNames(
          item.preferred_models?.length
            ? item.preferred_models
            : (item.preferred_model ? [item.preferred_model] : getProviderDefaultModelList(providerMeta))
        ),
        apiKey: '',
        hasSavedApiKey: item.has_api_key,
        hasAccountConfig: Boolean(item.base_url || item.preferred_models?.length || item.preferred_model || item.has_api_key),
        activeKeyId: item.active_key_id ?? null,
        savedKeys: item.keys ?? [],
        updatedAt: item.updated_at ?? null,
      }
    },

    applySavedProviderConfigs(items: AiChatSavedProviderConfig[]) {
      this.clearSavedProviderMetadata()
      for (const item of items) {
        this.applySavedProviderConfig(item)
      }
    },

    async loadProviders(isAuthenticated: boolean) {
      const providerPayload = await fetchAiChatProviders()
      this.applyProviders(providerPayload)

      if (isAuthenticated) {
        const savedPayload = await fetchAiChatSavedConfigs()
        this.applySavedProviderConfigs(savedPayload.items)
      } else {
        this.clearSavedProviderMetadata()
        this.applyLocalProviderConfigs()
      }

      this.loadedForAuthState = isAuthenticated
    },

    buildConnectionPayload(providerId: string) {
      const providerConfig = this.getProviderConfig(providerId)
      const payload: {
        provider: string
        base_url: string
        api_key?: string
      } = {
        provider: providerId,
        base_url: providerConfig.baseUrl.trim(),
      }
      if (providerConfig.apiKey.trim()) {
        payload.api_key = providerConfig.apiKey.trim()
      }
      return payload
    },

    getEffectiveModel(providerId: string) {
      const providerConfig = this.getProviderConfig(providerId)
      if (providerConfig.preferredModels.length) {
        return providerConfig.preferredModels[0]
      }
      return this.getProviderById(providerId)?.default_model?.trim() || ''
    },

    getEffectiveModels(providerId: string) {
      const providerConfig = this.getProviderConfig(providerId)
      if (providerConfig.preferredModels.length) {
        return decorateProviderModelList(providerId, providerConfig.preferredModels)
      }
      return getProviderDefaultModelList(this.getProviderById(providerId))
    },

    applyProviderStatus(status: AiChatStatusResponse) {
      const providerIndex = this.providers.findIndex(provider => provider.id === status.provider)
      if (providerIndex < 0) {
        return
      }

      const current = this.providers[providerIndex]
      const nextModels = decorateProviderModelList(status.provider, status.models ?? [])
      this.providers.splice(providerIndex, 1, {
        ...current,
        label: status.label || current.label,
        kind: status.kind || current.kind,
        is_custom: status.is_custom,
        configured: status.configured,
        requires_api_key: status.requires_api_key,
        base_url: status.base_url || current.base_url,
        default_model: status.default_model || nextModels[0] || current.default_model,
        models: nextModels,
        supports_stream: status.supports_stream,
        supports_vision: status.supports_vision,
      })
    },

    async syncDiscoveredModelsFromStatus(status: AiChatStatusResponse) {
      this.applyProviderStatus(status)

      if (status.kind !== 'ollama' || !status.available) {
        return false
      }

      const discoveredModels = decorateProviderModelList(status.provider, status.models ?? [])
      if (!discoveredModels.length) {
        return false
      }

      const currentConfig = this.getProviderConfig(status.provider)
      const currentModels = dedupeModelNames(currentConfig.preferredModels)
      const preservedModels = currentModels.filter(model => discoveredModels.includes(model))
      const appendedModels = discoveredModels.filter(model => !preservedModels.includes(model))
      const nextModels = [...preservedModels, ...appendedModels]

      if (areModelListsEqual(currentModels, nextModels)) {
        return false
      }

      this.updateProviderConfig(status.provider, {
        preferredModels: nextModels,
      })

      if (this.loadedForAuthState === true) {
        await this.saveProviderConfig(status.provider, {
          includeApiKey: false,
        })
      }

      return true
    },

    hasEffectiveApiKey(providerId: string) {
      const provider = this.getProviderById(providerId)
      if (!provider?.requires_api_key) {
        return true
      }

      const providerConfig = this.getProviderConfig(providerId)
      return Boolean(
        providerConfig.apiKey.trim()
        || providerConfig.savedKeys.length
        || provider.configured
      )
    },

    hasEffectiveConnection(providerId: string) {
      const provider = this.getProviderById(providerId)
      if (!provider) {
        return false
      }

      const providerConfig = this.getProviderConfig(providerId)
      const effectiveBaseUrl = providerConfig.baseUrl.trim() || provider.base_url.trim()
      return Boolean(effectiveBaseUrl) && this.hasEffectiveApiKey(providerId)
    },

    async saveProviderConfig(
      providerId: string,
      options: {
        includeApiKey?: boolean
        clearApiKey?: boolean
        apiKeyLabel?: string
      } = {},
    ) {
      const includeApiKey = options.includeApiKey ?? true
      const clearApiKey = options.clearApiKey ?? false
      const providerConfig = this.getProviderConfig(providerId)
      const saved = await saveAiChatProviderConfig(providerId, {
        base_url: providerConfig.baseUrl.trim(),
        preferred_model: providerConfig.preferredModels[0] ?? null,
        preferred_models: providerConfig.preferredModels,
        api_key: includeApiKey && providerConfig.apiKey.trim() ? providerConfig.apiKey.trim() : undefined,
        api_key_label: includeApiKey && providerConfig.apiKey.trim() ? (options.apiKeyLabel ?? '').trim() || undefined : undefined,
        clear_api_key: clearApiKey,
      })
      this.applySavedProviderConfig(saved)
      return saved
    },

    async deleteProviderConfig(providerId: string) {
      await deleteAiChatProviderConfig(providerId)
      const providerMeta = this.getProviderById(providerId)
      this.providerConfigs[providerId] = {
        ...buildEmptyProviderConfig(),
        baseUrl: providerMeta?.base_url || '',
        preferredModels: getProviderDefaultModelList(providerMeta),
        apiKey: '',
        hasSavedApiKey: false,
        hasAccountConfig: false,
        activeKeyId: null,
        savedKeys: [],
        updatedAt: null,
      }
    },

    async activateProviderKey(providerId: string, keyId: string) {
      const saved = await activateAiChatProviderKey(providerId, keyId)
      this.applySavedProviderConfig(saved)
      return saved
    },

    async deleteProviderKey(providerId: string, keyId: string) {
      const saved = await deleteAiChatProviderKey(providerId, keyId)
      this.applySavedProviderConfig(saved)
      return saved
    },

    async createCustomProvider(payload: AiChatCreateCustomProviderRequest) {
      const created = await createAiChatCustomProvider(payload)
      this.updateProviderConfig(created.id, {
        baseUrl: created.base_url,
        preferredModels: getProviderDefaultModelList(created),
        apiKey: '',
        hasSavedApiKey: false,
        hasAccountConfig: false,
        activeKeyId: null,
        savedKeys: [],
        updatedAt: null,
      })
      return created
    },

    async deleteCustomProvider(providerId: string) {
      await deleteAiChatCustomProvider(providerId)
      delete this.providerConfigs[providerId]
    },
  },
})
