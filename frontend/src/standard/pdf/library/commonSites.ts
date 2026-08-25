import api from '@/api'

export interface CommonSite {
  id: string
  title: string
  url: string
  description?: string
  logo_size?: number
}

export const COMMON_SITES_STORAGE_KEY = 'codeyun.notes.commonSites.v1'
export const COMMON_SITES_DEFAULTS_VERSION_KEY = 'codeyun.notes.commonSites.defaultsVersion'
export const COMMON_SITES_DEFAULTS_VERSION = 4
const COMMON_SITE_LOGO_DB_NAME = 'codeyun-common-site-logo-cache'
const COMMON_SITE_LOGO_STORE_NAME = 'logos'
const COMMON_SITE_LOGO_DB_VERSION = 1

interface CachedCommonSiteLogo {
  key: string
  blob: Blob
  cachedAt: number
}

export const DEFAULT_COMMON_SITES: CommonSite[] = [
  {
    id: 'codex-usage',
    title: 'codex余额',
    url: 'https://chatgpt.com/codex/cloud/settings/analytics#usage',
    description: 'ChatGPT Codex 云端用量页面',
  },
  {
    id: 'z-library',
    title: 'Z-Library',
    url: 'https://zh.z-library.sk/',
    description: '电子书网站',
  },
  {
    id: 'linux-do-ai-thinking-framework',
    title: 'AI 时代的思维框架',
    url: 'https://linux.do/t/topic/2538870',
    description: 'LINUX DO 上的 AI 思维框架文章',
  },
  {
    id: 'ruanyf-weekly-latest',
    title: 'Weekly',
    url: 'https://github.com/ruanyf/weekly/blob/master/docs/issue-405.md',
    description: '科技爱好者周刊最新一期',
  },
]

const cloneDefaults = () => DEFAULT_COMMON_SITES.map((site) => ({ ...site }))

export function loadCommonSites(): CommonSite[] {
  if (typeof window === 'undefined' || !window.localStorage) {
    return cloneDefaults()
  }
  const raw = window.localStorage.getItem(COMMON_SITES_STORAGE_KEY)
  if (!raw) {
    window.localStorage.setItem(
      COMMON_SITES_DEFAULTS_VERSION_KEY,
      String(COMMON_SITES_DEFAULTS_VERSION),
    )
    return cloneDefaults()
  }
  try {
    const parsed = JSON.parse(raw) as CommonSite[]
    if (!Array.isArray(parsed)) return cloneDefaults()
    const appliedVersion = Number(
      window.localStorage.getItem(COMMON_SITES_DEFAULTS_VERSION_KEY) ?? 0,
    )
    if (appliedVersion >= COMMON_SITES_DEFAULTS_VERSION) return parsed
    const existingIds = new Set(parsed.map((site) => site.id))
    const migrated = [
      ...parsed,
      ...DEFAULT_COMMON_SITES
        .filter((site) => !existingIds.has(site.id))
        .map((site) => ({ ...site })),
    ]
    window.localStorage.setItem(COMMON_SITES_STORAGE_KEY, JSON.stringify(migrated))
    window.localStorage.setItem(
      COMMON_SITES_DEFAULTS_VERSION_KEY,
      String(COMMON_SITES_DEFAULTS_VERSION),
    )
    return migrated
  } catch {
    return cloneDefaults()
  }
}

export function saveCommonSites(sites: CommonSite[]) {
  if (typeof window === 'undefined' || !window.localStorage) return
  window.localStorage.setItem(COMMON_SITES_STORAGE_KEY, JSON.stringify(sites))
}

function commonSiteLogoCacheKey(siteUrl: string) {
  return new URL(siteUrl).origin.toLowerCase()
}

function openCommonSiteLogoDatabase() {
  return new Promise<IDBDatabase>((resolve, reject) => {
    const request = indexedDB.open(COMMON_SITE_LOGO_DB_NAME, COMMON_SITE_LOGO_DB_VERSION)
    request.onupgradeneeded = () => {
      const database = request.result
      if (!database.objectStoreNames.contains(COMMON_SITE_LOGO_STORE_NAME)) {
        database.createObjectStore(COMMON_SITE_LOGO_STORE_NAME, { keyPath: 'key' })
      }
    }
    request.onsuccess = () => resolve(request.result)
    request.onerror = () => reject(request.error)
  })
}

async function readCommonSiteLogoCache(key: string) {
  if (typeof indexedDB === 'undefined') return null
  const database = await openCommonSiteLogoDatabase()
  try {
    return await new Promise<Blob | null>((resolve, reject) => {
      const request = database
        .transaction(COMMON_SITE_LOGO_STORE_NAME, 'readonly')
        .objectStore(COMMON_SITE_LOGO_STORE_NAME)
        .get(key)
      request.onsuccess = () => resolve((request.result as CachedCommonSiteLogo | undefined)?.blob ?? null)
      request.onerror = () => reject(request.error)
    })
  } finally {
    database.close()
  }
}

async function writeCommonSiteLogoCache(key: string, blob: Blob) {
  if (typeof indexedDB === 'undefined') return
  const database = await openCommonSiteLogoDatabase()
  try {
    await new Promise<void>((resolve, reject) => {
      const transaction = database.transaction(COMMON_SITE_LOGO_STORE_NAME, 'readwrite')
      transaction.objectStore(COMMON_SITE_LOGO_STORE_NAME).put({ key, blob, cachedAt: Date.now() })
      transaction.oncomplete = () => resolve()
      transaction.onerror = () => reject(transaction.error)
      transaction.onabort = () => reject(transaction.error)
    })
  } finally {
    database.close()
  }
}

async function fetchCommonSiteLogo(siteUrl: string, refresh: boolean) {
  const response = await api.request<Blob>({
    method: refresh ? 'POST' : 'GET',
    url: refresh ? '/common-sites/logo/refresh' : '/common-sites/logo',
    params: { site_url: siteUrl },
    responseType: 'blob',
  })
  return response.data
}

export async function loadCommonSiteLogoBlob(siteUrl: string, options: { refresh?: boolean } = {}) {
  const key = commonSiteLogoCacheKey(siteUrl)
  if (!options.refresh) {
    try {
      const cached = await readCommonSiteLogoCache(key)
      if (cached) return cached
    } catch {
      // IndexedDB may be disabled by browser policy; the server cache remains available.
    }
  }
  const blob = await fetchCommonSiteLogo(siteUrl, Boolean(options.refresh))
  try {
    await writeCommonSiteLogoCache(key, blob)
  } catch {
    // A persistent browser cache is an optimization, not a prerequisite for showing the logo.
  }
  return blob
}
