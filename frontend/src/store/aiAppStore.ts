import { defineStore } from 'pinia'

import {
  fetchAiChatAppConfigs,
  saveAiChatAppConfig,
  type AiChatAppConfig,
} from '@/api/aiChat'

const LOCAL_AI_APP_CONFIG_STORAGE_KEY = 'codeyun_ai_app_configs_v1'

export type AiAppId = string

export interface AiAppDefinition {
  id: AiAppId
  group: string
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

export const FALLBACK_AI_APP_DEFINITIONS: AiAppDefinition[] = [
  {
    id: 'note-taxonomy',
    group: '星图笔记',
    label: '笔记分类',
    description: '仅分析当前标题，并参考已有条目的标题、分类、形态、阶段后回写结果。',
  },
  {
    id: 'ai-git-commit',
    group: '开发工具',
    label: 'AI提交',
    description: '生成 Git 提交信息，GitHub 项目自动提交和分层归纳提交共用这一组模型配置。',
  },
  {
    id: 'codex-diary',
    group: '星图笔记',
    label: 'Codex 星图日记',
    description: '按日期读取 Codex 会话，按主题拆分后写入星图笔记节点。',
  },
  {
    id: 'codeclaw',
    group: '开发工具',
    label: 'CodeClaw 微信接入',
    description: '微信消息入口使用的 AI 模型，默认交给本机 Codex CLI 处理并回复微信。',
  },
]

function canUseLocalStorage() {
  return typeof window !== 'undefined' && typeof window.localStorage !== 'undefined'
}

function buildDefaultAppConfig(appId?: AiAppId): AiAppRuntimeConfig {
  if (appId === 'ai-git-commit') {
    return {
      enabled: true,
      provider: 'codex-cli',
      model: 'gpt-5.3-codex-spark',
      updatedAt: null,
    }
  }

  return {
    enabled: true,
    provider: appId === 'codex-diary' ? 'deepseek' : appId === 'note-taxonomy' || appId === 'codeclaw' ? 'codex-cli' : '',
    model: appId === 'codex-diary' ? 'deepseek-v4-pro' : appId === 'note-taxonomy' || appId === 'codeclaw' ? 'gpt-5.3-codex-spark' : '',
    updatedAt: null,
  }
}

function normalizeAppConfigItem(value: unknown, appId?: AiAppId): AiAppRuntimeConfig {
  const item = value && typeof value === 'object'
    ? value as LocalAiAppConfigItem
    : {}
  const fallback = buildDefaultAppConfig(appId)
  return {
    enabled: item.enabled !== false,
    provider: typeof item.provider === 'string' ? item.provider.trim() : fallback.provider,
    model: typeof item.model === 'string' ? item.model.trim() : fallback.model,
    updatedAt: typeof item.updated_at === 'number' && Number.isFinite(item.updated_at)
      ? item.updated_at
      : fallback.updatedAt,
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

    return FALLBACK_AI_APP_DEFINITIONS.reduce<Partial<Record<AiAppId, AiAppRuntimeConfig>>>((result, definition) => {
      if (payload.apps?.[definition.id]) {
        result[definition.id] = normalizeAppConfigItem(payload.apps[definition.id], definition.id)
      }
      return result
    }, {})
  } catch {
    return {}
  }
}

function clearLocalAppConfigs() {
  if (canUseLocalStorage()) {
    window.localStorage.removeItem(LOCAL_AI_APP_CONFIG_STORAGE_KEY)
  }
}

function persistLocalAppConfigs(configs: Partial<Record<AiAppId, AiAppRuntimeConfig>>) {
  if (!canUseLocalStorage()) {
    return
  }

  const apps = FALLBACK_AI_APP_DEFINITIONS.reduce<Partial<Record<AiAppId, LocalAiAppConfigItem>>>((result, definition) => {
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

function appConfigFromApi(item: AiChatAppConfig): AiAppRuntimeConfig {
  return {
    enabled: item.enabled !== false,
    provider: item.provider.trim(),
    model: item.model.trim(),
    updatedAt: item.updated_at ?? null,
  }
}

export const useAiAppStore = defineStore('aiApp', {
  state: () => ({
    loadedForAuthState: null as boolean | null,
    definitions: [...FALLBACK_AI_APP_DEFINITIONS] as AiAppDefinition[],
    appConfigs: {} as Partial<Record<AiAppId, AiAppRuntimeConfig>>,
  }),

  getters: {
    appDefinitions: state => state.definitions,
  },

  actions: {
    getDefinition(appId: AiAppId) {
      return this.definitions.find(item => item.id === appId) ?? null
    },

    getAppConfig(appId: AiAppId): AiAppRuntimeConfig {
      return this.appConfigs[appId] ?? buildDefaultAppConfig(appId)
    },

    applyAppConfig(item: AiChatAppConfig) {
      if (!this.definitions.some(definition => definition.id === item.id)) {
        this.definitions.push({
          id: item.id,
          group: item.group || '',
          label: item.label || item.id,
          description: item.description || '',
        })
      }
      this.appConfigs[item.id] = appConfigFromApi(item)
    },

    async loadAppConfigs(isAuthenticated: boolean) {
      const localConfigs = loadLocalAppConfigs()
      if (!isAuthenticated) {
        this.appConfigs = localConfigs
        this.loadedForAuthState = false
        return
      }

      const payload = await fetchAiChatAppConfigs()
      this.definitions = payload.items.map(item => ({
        id: item.id,
        group: item.group || '',
        label: item.label || item.id,
        description: item.description || '',
      }))
      const nextConfigs: Partial<Record<AiAppId, AiAppRuntimeConfig>> = {}
      for (const item of payload.items) {
        nextConfigs[item.id] = appConfigFromApi(item)
      }
      this.appConfigs = nextConfigs

      for (const definition of this.definitions) {
        const localConfig = localConfigs[definition.id]
        const remoteConfig = this.appConfigs[definition.id]
        if (!localConfig || remoteConfig?.updatedAt) {
          continue
        }
        await this.saveAppConfig(definition.id, localConfig)
      }
      clearLocalAppConfigs()
      this.loadedForAuthState = true
    },

    async updateAppConfig(appId: AiAppId, patch: Partial<AiAppRuntimeConfig>) {
      const nextConfig = {
        ...this.getAppConfig(appId),
        ...patch,
        updatedAt: Date.now(),
      }
      this.appConfigs[appId] = nextConfig

      if (this.loadedForAuthState === true) {
        await this.saveAppConfig(appId, nextConfig)
        return
      }
      persistLocalAppConfigs(this.appConfigs)
    },

    async saveAppConfig(appId: AiAppId, config = this.getAppConfig(appId)) {
      const saved = await saveAiChatAppConfig(appId, {
        enabled: config.enabled,
        provider: config.provider.trim(),
        model: config.model.trim(),
      })
      this.applyAppConfig(saved)
      return saved
    },

    async resetAppConfig(appId: AiAppId) {
      const nextConfig = buildDefaultAppConfig(appId)
      this.appConfigs[appId] = nextConfig
      if (this.loadedForAuthState === true) {
        await this.saveAppConfig(appId, nextConfig)
        return
      }
      persistLocalAppConfigs(this.appConfigs)
    },
  },
})
