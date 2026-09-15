export type AiPortalEmbedStatus = 'ok' | 'unknown' | 'blocked'

export interface AiPortalSite {
  id: string
  name: string
  url: string
  group: string
  embed: AiPortalEmbedStatus
  note?: string
  custom?: boolean
}

export const AI_PORTAL_GROUP_INTERNATIONAL = '国际站点'
export const AI_PORTAL_GROUP_DOMESTIC = '国内站点'
export const AI_PORTAL_GROUP_CUSTOM = '我的站点'

const PRESET_SITES: AiPortalSite[] = [
  {
    id: 'arena',
    name: 'Arena',
    url: 'https://arena.ai/',
    group: AI_PORTAL_GROUP_INTERNATIONAL,
    embed: 'blocked',
    note: '响应头 X-Frame-Options: SAMEORIGIN',
  },
  {
    id: 'lmarena',
    name: 'LMArena',
    url: 'https://lmarena.ai/',
    group: AI_PORTAL_GROUP_INTERNATIONAL,
    embed: 'unknown',
  },
  {
    id: 'chatgpt',
    name: 'ChatGPT',
    url: 'https://chatgpt.com/',
    group: AI_PORTAL_GROUP_INTERNATIONAL,
    embed: 'unknown',
  },
  {
    id: 'claude',
    name: 'Claude',
    url: 'https://claude.ai/',
    group: AI_PORTAL_GROUP_INTERNATIONAL,
    embed: 'blocked',
    note: '响应头 X-Frame-Options 与 CSP frame-ancestors 均限制为自身',
  },
  {
    id: 'gemini',
    name: 'Gemini',
    url: 'https://gemini.google.com/app',
    group: AI_PORTAL_GROUP_INTERNATIONAL,
    embed: 'unknown',
  },
  {
    id: 'grok',
    name: 'Grok',
    url: 'https://grok.com/',
    group: AI_PORTAL_GROUP_INTERNATIONAL,
    embed: 'unknown',
  },
  {
    id: 'perplexity',
    name: 'Perplexity',
    url: 'https://www.perplexity.ai/',
    group: AI_PORTAL_GROUP_INTERNATIONAL,
    embed: 'unknown',
  },
  {
    id: 'deepseek',
    name: 'DeepSeek',
    url: 'https://chat.deepseek.com/',
    group: AI_PORTAL_GROUP_DOMESTIC,
    embed: 'blocked',
    note: '响应头 CSP frame-ancestors: none',
  },
  {
    id: 'kimi',
    name: 'Kimi',
    url: 'https://kimi.moonshot.cn/',
    group: AI_PORTAL_GROUP_DOMESTIC,
    embed: 'unknown',
  },
  {
    id: 'doubao',
    name: '豆包',
    url: 'https://www.doubao.com/chat/',
    group: AI_PORTAL_GROUP_DOMESTIC,
    embed: 'blocked',
    note: '响应头 CSP frame-ancestors 未包含本域',
  },
  {
    id: 'qianwen',
    name: '通义千问',
    url: 'https://tongyi.aliyun.com/qianwen/',
    group: AI_PORTAL_GROUP_DOMESTIC,
    embed: 'blocked',
    note: '响应头 X-Frame-Options 与 CSP frame-ancestors 均限制为阿里域内',
  },
  {
    id: 'chatglm',
    name: '智谱清言',
    url: 'https://chatglm.cn/',
    group: AI_PORTAL_GROUP_DOMESTIC,
    embed: 'ok',
  },
]

export const AI_PORTAL_PRESET_SITES: readonly AiPortalSite[] = PRESET_SITES

const CUSTOM_SITES_STORAGE_KEY = 'ai_portal_custom_sites_v1'
const ACTIVE_SITE_STORAGE_KEY = 'ai_portal_active_site_v1'
const VIEW_MODE_STORAGE_KEY = 'ai_portal_view_mode_v1'

const KNOWN_EMBED_STATUS: AiPortalEmbedStatus[] = ['ok', 'unknown', 'blocked']

export function normalizeSiteUrl(raw: string): string | null {
  const trimmed = raw.trim()
  if (!trimmed) {
    return null
  }
  const candidate = /^[a-z][a-z0-9+.-]*:\/\//i.test(trimmed) ? trimmed : `https://${trimmed}`
  try {
    const parsed = new URL(candidate)
    if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
      return null
    }
    return parsed.toString()
  } catch {
    return null
  }
}

export function siteHostname(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, '')
  } catch {
    return url
  }
}

export function siteInitials(name: string): string {
  const latin = name.replace(/[^a-zA-Z0-9]/g, '')
  if (latin) {
    return latin.slice(0, 2).toUpperCase()
  }
  return name.slice(0, 2)
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

function normalizeStoredSite(raw: unknown, index: number): AiPortalSite | null {
  if (!isRecord(raw)) {
    return null
  }
  const name = typeof raw.name === 'string' ? raw.name.trim() : ''
  const url = typeof raw.url === 'string' ? normalizeSiteUrl(raw.url) : null
  if (!name || !url) {
    return null
  }
  const embed = typeof raw.embed === 'string' && KNOWN_EMBED_STATUS.includes(raw.embed as AiPortalEmbedStatus)
    ? raw.embed as AiPortalEmbedStatus
    : 'unknown'
  return {
    id: typeof raw.id === 'string' && raw.id ? raw.id : `custom-${index}-${url}`,
    name,
    url,
    group: AI_PORTAL_GROUP_CUSTOM,
    embed,
    custom: true,
  }
}

function readStorage(key: string): string | null {
  if (typeof window === 'undefined' || typeof window.localStorage === 'undefined') {
    return null
  }
  try {
    return window.localStorage.getItem(key)
  } catch {
    return null
  }
}

function writeStorage(key: string, value: string): void {
  if (typeof window === 'undefined' || typeof window.localStorage === 'undefined') {
    return
  }
  try {
    window.localStorage.setItem(key, value)
  } catch {
    // 忽略隐私模式或配额导致的写入失败
  }
}

export function loadCustomSites(): AiPortalSite[] {
  const raw = readStorage(CUSTOM_SITES_STORAGE_KEY)
  if (!raw) {
    return []
  }
  try {
    const parsed = JSON.parse(raw) as unknown
    if (!Array.isArray(parsed)) {
      return []
    }
    return parsed
      .map((item, index) => normalizeStoredSite(item, index))
      .filter((item): item is AiPortalSite => item !== null)
  } catch {
    return []
  }
}

export function saveCustomSites(sites: AiPortalSite[]): void {
  const payload = sites.map((site) => ({
    id: site.id,
    name: site.name,
    url: site.url,
    embed: site.embed,
  }))
  writeStorage(CUSTOM_SITES_STORAGE_KEY, JSON.stringify(payload))
}

export function loadActiveSiteId(): string | null {
  return readStorage(ACTIVE_SITE_STORAGE_KEY)
}

export function saveActiveSiteId(id: string): void {
  writeStorage(ACTIVE_SITE_STORAGE_KEY, id)
}

export type AiPortalViewMode = 'embed' | 'external'

export function loadViewMode(): AiPortalViewMode {
  return readStorage(VIEW_MODE_STORAGE_KEY) === 'external' ? 'external' : 'embed'
}

export function saveViewMode(mode: AiPortalViewMode): void {
  writeStorage(VIEW_MODE_STORAGE_KEY, mode)
}

export function createCustomSite(name: string, url: string): AiPortalSite {
  const normalized = normalizeSiteUrl(url)
  if (!normalized) {
    throw new Error('网址无效')
  }
  return {
    id: `custom-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`,
    name: name.trim() || siteHostname(normalized),
    url: normalized,
    group: AI_PORTAL_GROUP_CUSTOM,
    embed: 'unknown',
    custom: true,
  }
}
