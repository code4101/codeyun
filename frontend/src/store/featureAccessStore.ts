import { defineStore } from 'pinia'

import { fetchAccessContext, type FeatureAccessContext, type FeatureAccessFlatItem } from '@/api/access'
import { useUserStore } from '@/store/userStore'

let pendingContextRequest: Promise<FeatureAccessContext> | null = null
const CONTEXT_CACHE_KEY = 'codeyun:feature-access-context:v1'
const CONTEXT_CACHE_TTL_MS = 60 * 1000

interface FeatureAccessContextCache {
  authKey: string
  savedAt: number
  context: FeatureAccessContext
}

interface FeatureAccessState {
  context: FeatureAccessContext | null
  loading: boolean
  loaded: boolean
  error: string | null
}

function hashToken(value: string | null): string {
  if (!value) {
    return 'anonymous'
  }
  let hash = 2166136261
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index)
    hash = Math.imul(hash, 16777619)
  }
  return `token:${(hash >>> 0).toString(36)}:${value.length}`
}

function readCachedContext(authKey: string): FeatureAccessContext | null {
  if (typeof window === 'undefined') {
    return null
  }
  try {
    const raw = window.localStorage.getItem(CONTEXT_CACHE_KEY)
    if (!raw) {
      return null
    }
    const payload = JSON.parse(raw) as Partial<FeatureAccessContextCache>
    if (
      payload.authKey !== authKey
      || !payload.context
      || typeof payload.savedAt !== 'number'
      || Date.now() - payload.savedAt > CONTEXT_CACHE_TTL_MS
    ) {
      return null
    }
    return payload.context
  } catch {
    return null
  }
}

function writeCachedContext(authKey: string, context: FeatureAccessContext) {
  if (typeof window === 'undefined') {
    return
  }
  try {
    const payload: FeatureAccessContextCache = {
      authKey,
      savedAt: Date.now(),
      context,
    }
    window.localStorage.setItem(CONTEXT_CACHE_KEY, JSON.stringify(payload))
  } catch {
    // localStorage may be unavailable or full; the network path remains the source of truth.
  }
}

function clearCachedContext() {
  if (typeof window === 'undefined') {
    return
  }
  window.localStorage.removeItem(CONTEXT_CACHE_KEY)
}

export const useFeatureAccessStore = defineStore('feature-access', {
  state: (): FeatureAccessState => ({
    context: null,
    loading: false,
    loaded: false,
    error: null,
  }),

  getters: {
    flatItems: (state): Record<string, FeatureAccessFlatItem> => state.context?.flat_items ?? {},
    isReady: (state) => state.loaded && !!state.context,
    isAllowed: (state) => (key: string | null | undefined) => {
      if (!key || !state.context) {
        return false
      }
      if (state.context.subject.is_superuser) {
        return true
      }
      const item = state.context.flat_items[key]
      return Boolean(item?.effective_value)
    },
    getItem: (state) => (key: string | null | undefined) => {
      if (!key) {
        return null
      }
      return state.context?.flat_items[key] ?? null
    },
  },

  actions: {
    async loadContext(force = false) {
      if (this.loading && pendingContextRequest) {
        return pendingContextRequest
      }
      if (this.loaded && !force) {
        return this.context
      }

      const userStore = useUserStore()
      const authKey = hashToken(userStore.token)
      if (!force) {
        const cachedContext = readCachedContext(authKey)
        if (cachedContext) {
          this.context = cachedContext
          this.loaded = true
          this.error = null
          return cachedContext
        }
      }

      this.loading = true
      this.error = null
      const request = (async () => {
        let nextContext = await fetchAccessContext()

        // If a stale/expired access token made the optional-auth endpoint fall back to
        // anonymous context, try repairing the session through /auth/me first and then
        // fetch the context again with the refreshed token.
        if (userStore.isAuthenticated && !nextContext.subject.is_authenticated) {
          await userStore.fetchUserProfile()
          if (userStore.isAuthenticated) {
            nextContext = await fetchAccessContext()
          }
        }

        this.context = nextContext
        this.loaded = true
        writeCachedContext(hashToken(userStore.token), nextContext)
        return nextContext
      })()
      pendingContextRequest = request

      try {
        return await request
      } catch (error: any) {
        this.error = error?.response?.data?.detail || error?.message || '加载功能权限失败'
        if (!this.context) {
          this.loaded = false
        }
        throw error
      } finally {
        if (pendingContextRequest === request) {
          pendingContextRequest = null
        }
        this.loading = false
      }
    },

    async refreshContext() {
      return this.loadContext(true)
    },

    async ensureLoaded() {
      return this.loadContext(false)
    },

    clearContext() {
      this.context = null
      this.loaded = false
      this.loading = false
      this.error = null
      clearCachedContext()
    },
  },
})
