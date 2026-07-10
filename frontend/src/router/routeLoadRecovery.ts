import { ref } from 'vue'
import type { Router } from 'vue-router'

const RETRY_STORAGE_KEY = 'codeyun_route_load_retry'
const RETRY_MAX_AGE_MS = 60_000

export const routeLoadError = ref('')

function isResourceLoadError(error: unknown): boolean {
  const message = error instanceof Error ? error.message : String(error || '')
  return /failed to fetch dynamically imported module|error loading dynamically imported module|importing a module script failed|load failed/i.test(message)
}

function readLastRetryAt(): number {
  try {
    return Number(window.sessionStorage.getItem(RETRY_STORAGE_KEY) || 0)
  } catch {
    return 0
  }
}

function rememberRetry(now: number) {
  try {
    window.sessionStorage.setItem(RETRY_STORAGE_KEY, String(now))
  } catch {
    // sessionStorage may be unavailable in private or restricted browser contexts.
  }
}

function reloadWithCacheBuster(now: number) {
  const url = new URL(window.location.href)
  url.searchParams.set('_route_retry', String(now))
  window.location.replace(url.toString())
}

export function installRouteLoadRecovery(router: Router) {
  router.onError((error) => {
    if (!isResourceLoadError(error)) {
      routeLoadError.value = error instanceof Error ? error.message : String(error || '页面启动失败')
      return
    }

    const now = Date.now()
    if (now - readLastRetryAt() > RETRY_MAX_AGE_MS) {
      rememberRetry(now)
      reloadWithCacheBuster(now)
      return
    }

    routeLoadError.value = '页面资源加载失败。开发服务可能处于异常状态，请重新加载；若仍失败，请重启 CodeYun。'
  })
}

export function reloadCurrentPage() {
  window.location.reload()
}
