import { defineStore } from 'pinia'

import { fetchAccessContext, type FeatureAccessContext, type FeatureAccessFlatItem } from '@/api/access'

interface FeatureAccessState {
  context: FeatureAccessContext | null
  loading: boolean
  loaded: boolean
  error: string | null
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
        return true
      }
      if (state.context.subject.is_superuser) {
        return true
      }
      const item = state.context.flat_items[key]
      if (!item) {
        return true
      }
      return Boolean(item.effective_value)
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
      if (this.loading) {
        return this.context
      }
      if (this.loaded && !force) {
        return this.context
      }

      this.loading = true
      this.error = null
      try {
        const nextContext = await fetchAccessContext()
        this.context = nextContext
        this.loaded = true
        return nextContext
      } catch (error: any) {
        this.error = error?.response?.data?.detail || error?.message || '加载功能权限失败'
        if (!this.context) {
          this.loaded = false
        }
        throw error
      } finally {
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
    },
  },
})
