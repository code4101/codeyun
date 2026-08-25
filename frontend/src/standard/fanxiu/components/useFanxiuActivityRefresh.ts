import { computed, toValue, type MaybeRefOrGetter } from 'vue'

import { useUserStore } from '@/store/userStore'

import {
  isActivityActiveToday,
  isActivityCollectibleToday,
  shouldAutoCollectActivity,
} from './activityStatus'

type RefreshableActivity = {
  start_date: string
  end_date: string
  close_panel_date?: string | null
  close_panel_at?: string | null
  captured_at?: string | null
}

type ActivityRefreshOptions = {
  activity: MaybeRefOrGetter<RefreshableActivity | null | undefined>
  capturedAts?: () => readonly (string | null | undefined)[]
  collectSilently: () => Promise<void> | void
}

/** Shared refresh contract for every Fanxiu activity page with game collection. */
export function useFanxiuActivityRefresh(options: ActivityRefreshOptions) {
  const userStore = useUserStore()
  const canEdit = computed(() => (
    userStore.user?.username === '凡修手游' || userStore.isAdmin
  ))
  const isActivityActive = computed(() => isActivityActiveToday(toValue(options.activity)))
  const isActivityCollectible = computed(() => isActivityCollectibleToday(toValue(options.activity)))
  const canCollect = computed(() => canEdit.value && isActivityCollectible.value)

  function maybeAutoCollect(): boolean {
    const activity = toValue(options.activity)
    const capturedAts = options.capturedAts?.() ?? [activity?.captured_at]
    if (!canEdit.value || !shouldAutoCollectActivity(activity, capturedAts)) return false

    try {
      void Promise.resolve(options.collectSilently()).catch(() => undefined)
    } catch {
      // Page refresh collection is best-effort and must never interrupt rendering.
    }
    return true
  }

  return {
    canEdit,
    canCollect,
    isActivityActive,
    isActivityCollectible,
    maybeAutoCollect,
  }
}
