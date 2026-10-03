import { OFFLINE_LIBRARY_URL } from './store'

/** Wait for this scoped registration, not serviceWorker.ready: the main app
 * intentionally lies outside the offline reader's service-worker scope. */
export async function ensureOfflineRuntime(): Promise<void> {
  if (!window.isSecureContext || !navigator.serviceWorker) {
    throw new Error('离线阅读需要 HTTPS（或本机 localhost），请使用 https://code4101.com')
  }
  const existing = await navigator.serviceWorker.getRegistration(OFFLINE_LIBRARY_URL)
  if (existing?.active) { void existing.update().catch(() => undefined); return }
  const registration = await navigator.serviceWorker.register(`${OFFLINE_LIBRARY_URL}sw.js`, { scope: OFFLINE_LIBRARY_URL })
  // An existing complete runtime is sufficient while an update installs.
  if (registration.active) return
  const worker = registration.installing ?? registration.waiting
  if (!worker) throw new Error('离线阅读器未安装，请重试')
  await new Promise<void>((resolve, reject) => {
    const timer = setTimeout(() => { cleanup(); reject(new Error('离线阅读器下载超时，请重试')) }, 120000)
    function cleanup() { clearTimeout(timer); worker!.removeEventListener('statechange', changed) }
    function changed() {
      if (worker!.state === 'activated') { cleanup(); resolve() }
      if (worker!.state === 'redundant') { cleanup(); reject(new Error('离线阅读器下载失败，请重试')) }
    }
    worker.addEventListener('statechange', changed)
    changed()
  })
}
