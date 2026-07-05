type BootPerfMark = {
  name: string
  time: number
  delta: number
  detail?: unknown
}

declare global {
  interface Window {
    __codeyunBootPerf?: {
      enabled: boolean
      startedAt: number
      marks: BootPerfMark[]
    }
  }
}

function readBootPerfEnabled() {
  if (typeof window === 'undefined') {
    return false
  }
  try {
    const params = new URLSearchParams(window.location.search)
    const value = params.get('bootPerf') ?? params.get('sheetPerf') ?? params.get('perf')
    return value === '1' || value === 'true'
  } catch {
    return false
  }
}

function ensureBootPerfState() {
  if (typeof window === 'undefined') {
    return null
  }
  const existing = window.__codeyunBootPerf
  if (existing) {
    return existing
  }
  const startedAt = typeof performance !== 'undefined' ? performance.now() : Date.now()
  const state = {
    enabled: readBootPerfEnabled(),
    startedAt,
    marks: [] as BootPerfMark[],
  }
  window.__codeyunBootPerf = state
  return state
}

export function isBootPerfEnabled() {
  return ensureBootPerfState()?.enabled === true
}

export function markBootPerf(name: string, detail?: unknown) {
  const state = ensureBootPerfState()
  if (!state?.enabled) {
    return
  }
  const now = typeof performance !== 'undefined' ? performance.now() : Date.now()
  const previous = state.marks[state.marks.length - 1]
  const mark = {
    name,
    time: Math.round(now * 10) / 10,
    delta: Math.round((now - (previous?.time ?? state.startedAt)) * 10) / 10,
    ...(detail === undefined ? {} : { detail }),
  }
  state.marks.push(mark)
  if (typeof console !== 'undefined') {
    console.info(`[CodeYun bootPerf] ${JSON.stringify(mark)}`)
  }
}

export async function markBootPerfAsync<T>(name: string, run: () => Promise<T>): Promise<T> {
  markBootPerf(`${name}.start`)
  try {
    const result = await run()
    markBootPerf(`${name}.end`)
    return result
  } catch (error) {
    markBootPerf(`${name}.error`, {
      message: error instanceof Error ? error.message : String(error),
    })
    throw error
  }
}
