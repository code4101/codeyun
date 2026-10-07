/** 后端诊断响应头触发采集；关闭时不安装监听、不创建计时器、不发请求。 */
const explicitBrowser = typeof window !== 'undefined' && new URLSearchParams(window.location.search).get('browserDebug') === 'qianxin'
export const browserDiagnosticTag = explicitBrowser ? 'qianxin' : ''
type EventKind = 'snapshot' | 'resize' | 'error' | 'rejection' | 'request'
type DiagnosticEvent = { kind: EventKind; path: string; detail: string; values: Record<string, number | boolean | null> }
let runId = ''
let blockedRunId = ''
let expiresAt = 0
let timer: ReturnType<typeof setTimeout> | undefined
let expiryTimer: ReturnType<typeof setTimeout> | undefined
let queue: DiagnosticEvent[] = []
let batches = 0
let started = false
const requestStarts = new WeakMap<object, number>()

export function browserDiagnosticRequestStarted(request: object) {
  if (runId) requestStarts.set(request, performance.now())
}
export function browserDiagnosticRequestDuration(request?: object) {
  if (!runId) return null
  const start = request && requestStarts.get(request)
  if (request) requestStarts.delete(request)
  return start == null ? null : Math.round(performance.now() - start)
}

function pathOnly(value: string) {
  try { return new URL(value, window.location.href).pathname.slice(0, 500) } catch { return '' }
}
function stop(remember = false) {
  if (remember && runId) blockedRunId = runId
  runId = ''
  queue = []
  clearTimeout(timer)
  clearTimeout(expiryTimer)
  timer = expiryTimer = undefined
  if (!started) return
  window.removeEventListener('resize', resized)
  window.removeEventListener('error', failed)
  window.removeEventListener('unhandledrejection', rejected)
  started = false
}
function snapshot(): DiagnosticEvent['values'] {
  const height = (selector: string) => document.querySelector(selector)?.getBoundingClientRect().height ?? null
  return { width: innerWidth, height: innerHeight, outerWidth, outerHeight,
    dpr: devicePixelRatio, screenWidth: screen.width, screenHeight: screen.height,
    visualWidth: window.visualViewport?.width ?? null, visualHeight: window.visualViewport?.height ?? null,
    visualScale: window.visualViewport?.scale ?? null,
    supportsDvh: typeof CSS !== 'undefined' && CSS.supports('height', '100dvh'),
    appHeight: height('#app'), layoutHeight: height('.common-layout'),
    navigationHeight: height('.main-aside'), readerHeight: height('.reader-content'),
    documentHeight: document.documentElement.scrollHeight }
}
function add(kind: EventKind, detail = '', values: DiagnosticEvent['values'] = {}) {
  if (!runId || Date.now() >= expiresAt || batches >= 25) { stop(true); return }
  if (queue.length < 10) queue.push({ kind, path: location.pathname.slice(0, 500), detail: detail.slice(0, 1000), values })
  if (!timer) timer = setTimeout(() => { timer = undefined; void flush() }, 2000)
}
async function flush() {
  if (!runId || Date.now() >= expiresAt) { stop(true); return }
  const events = queue.splice(0, 10)
  if (!events.length) return
  const token = localStorage.getItem('token')
  if (!token) { stop(true); return }
  const sendingRunId = runId
  batches++
  try {
    const response = await fetch('/api/browser-diagnostics/events', { method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
      body: JSON.stringify({ run_id: sendingRunId, events }) })
    if (sendingRunId === runId && (!response.ok || batches >= 25)) stop(true)
  } catch { if (sendingRunId === runId) stop(true) }
}
function resized() { add('resize', '', snapshot()) }
function failed(event: ErrorEvent) {
  // 不上传异常正文或任意 rejection 值，避免夹带页面内容或凭证。
  add('error', `${event.error?.name || 'Error'} ${pathOnly(event.filename)}:${event.lineno}:${event.colno}`, snapshot())
}
function rejected(event: PromiseRejectionEvent) { add('rejection', event.reason?.name || 'Promise rejection', snapshot()) }

export function browserDiagnosticResponse(header?: string, request?: { path: string; status?: number; code?: string; durationMs?: number | null }) {
  if (!header) { if (runId) stop(true); return }
  const [nextRunId, expiry] = header.split(';')
  const nextExpiry = Number(expiry) * 1000
  if (nextRunId === blockedRunId) return
  if (!/^[a-f0-9]{32}$/.test(nextRunId || '') || !Number.isFinite(nextExpiry) || nextExpiry <= Date.now()) { stop(); return }
  if (nextRunId !== runId) {
    stop()
    runId = nextRunId!
    expiresAt = nextExpiry
    batches = 0
    started = true
    window.addEventListener('resize', resized)
    window.addEventListener('error', failed)
    window.addEventListener('unhandledrejection', rejected)
    expiryTimer = setTimeout(() => stop(true), Math.min(expiresAt - Date.now(), 1800_000))
    add('snapshot', '', snapshot())
  }
  if (request) add('request', `${pathOnly(request.path)} ${request.code || ''}`, { status: request.status ?? null, durationMs: request.durationMs ?? null, ...snapshot() })
}

export function browserDiagnosticRequestFailure(path: string, code = '', status?: number, durationMs?: number | null) {
  if (runId) add('request', `${pathOnly(path)} ${code}`, { status: status ?? null, durationMs: durationMs ?? null, ...snapshot() })
}
