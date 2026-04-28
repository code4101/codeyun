import { defineStore } from 'pinia'

const LOCAL_AI_APP_CONFIG_STORAGE_KEY = 'codeyun_ai_app_configs_v1'

export type AiAppId = 'note-taxonomy'

export interface AiAppDefinition {
  id: AiAppId
  label: string
  description: string
}

export interface AiAppRuntimeConfig {
  enabled: boolean
  provider: string
  model: string
  updatedAt: number | null
}

interface LocalAiAppConfigItem {
  enabled?: boolean
  provider?: string
  model?: string
  updated_at?: number | null
}

interface LocalAiAppConfigsPayload {
  version: 1
  apps: Partial<Record<AiAppId, LocalAiAppConfigItem>>
}

export const AI_APP_DEFINITIONS: AiAppDefinition[] = [
  {
    id: 'note-taxonomy',
    label: '笔记分类',
    description: '仅分析当前标题，并参考已有条目的标题、分类、形态、阶段后回写结果。',
  },
]

function canUseLocalStorage() {
  return typeof window !== 'undefined' && typeof window.localStorage !== 'undefined'
}

function buildDefaultAppConfig(): AiAppRuntimeConfig {
  return {
    enabled: true,
    provider: '',
    model: '',
    updatedAt: null,
  }
}

function normalizeAppConfigItem(value: unknown): AiAppRuntimeConfig {
  const item = value && typeof value === 'object'
    ? value as LocalAiAppConfigItem
    : {}
  return {
    enabled: item.enabled !== false,
    provider: typeof item.provider === 'string' ? item.provider.trim() : '',
    model: typeof item.model === 'string' ? item.model.trim() : '',
    updatedAt: typeof item.updated_at === 'number' && Number.isFinite(item.updated_at)
      ? item.updated_at
      : null,
  }
}

function loadLocalAppConfigs(): Partial<Record<AiAppId, AiAppRuntimeConfig>> {
  if (!canUseLocalStorage()) {
    return {}
  }

  try {
    const raw = window.localStorage.getItem(LOCAL_AI_APP_CONFIG_STORAGE_KEY)
    if (!raw) {
      return {}
    }

    const payload = JSON.parse(raw) as Partial<LocalAiAppConfigsPayload>
    if (!payload || typeof payload !== 'object' || !payload.apps || typeof payload.apps !== 'object') {
      return {}
    }

    return AI_APP_DEFINITIONS.reduce<Partial<Record<AiAppId, AiAppRuntimeConfig>>>((result, definition) => {
      result[definition.id] = normalizeAppConfigItem(payload.apps?.[definition.id])
      return result
    }, {})
  } catch {
    return {}
  }
}

function persistLocalAppConfigs(configs: Partial<Record<AiAppId, AiAppRuntimeConfig>>) {
  if (!canUseLocalStorage()) {
    return
  }

  const apps = AI_APP_DEFINITIONS.reduce<Partial<Record<AiAppId, LocalAiAppConfigItem>>>((result, definition) => {
    const config = configs[definition.id]
    if (!config) {
      return result
    }

    result[definition.id] = {
      enabled: config.enabled,
      provider: config.provider.trim(),
      model: config.model.trim(),
      updated_at: config.updatedAt,
    }
    return result
  }, {})

  const payload: LocalAiAppConfigsPayload = {
    version: 1,
    apps,
  }

  window.localStorage.setItem(LOCAL_AI_APP_CONFIG_STORAGE_KEY, JSON.stringify(payload))
}

export const useAiAppStore = defineStore('aiApp', {
  state: () => ({
    loaded: false,
    appConfigs: {} as Partial<Record<AiAppId, AiAppRuntimeConfig>>,
  }),

  getters: {
    appDefinitions: () => AI_APP_DEFINITIONS,
  },

  actions: {
    ensureLoaded() {
      if (this.loaded) {
        return
      }
      this.appConfigs = loadLocalAppConfigs()
      this.loaded = true
    },

    getDefinition(appId: AiAppId) {
      return AI_APP_DEFINITIONS.find(item => item.id === appId) ?? null
    },

    getAppConfig(appId: AiAppId): AiAppRuntimeConfig {
      this.ensureLoaded()
      return this.appConfigs[appId] ?? buildDefaultAppConfig()
    },

    updateAppConfig(appId: AiAppId, patch: Partial<AiAppRuntimeConfig>) {
      this.ensureLoaded()
      this.appConfigs[appId] = {
        ...this.getAppConfig(appId),
        ...patch,
        updatedAt: Date.now(),
      }
      persistLocalAppConfigs(this.appConfigs)
    },

    resetAppConfig(appId: AiAppId) {
      this.ensureLoaded()
      this.appConfigs[appId] = buildDefaultAppConfig()
      persistLocalAppConfigs(this.appConfigs)
    },
  },
})
