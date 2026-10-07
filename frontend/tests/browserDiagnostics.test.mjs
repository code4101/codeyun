import assert from 'node:assert/strict'
import { fileURLToPath } from 'node:url'
import test from 'node:test'
import { build } from 'esbuild'

const compiled = await build({ entryPoints: [fileURLToPath(new URL('../src/utils/browserDiagnostics.ts', import.meta.url))], bundle: true, write: false, platform: 'node', format: 'esm' })
let iteration = 0
async function fixture() {
  const listeners = new Map(), timers = new Map(), reports = []
  let sequence = 0
  const location = { href: 'https://code4101.com/reader?token=hidden', search: '', pathname: '/reader' }
  Object.assign(globalThis, {
    window: { location, visualViewport: { width: 960, height: 540, scale: 1 },
      addEventListener: (type, fn) => listeners.set(type, fn), removeEventListener: type => listeners.delete(type) },
    location, document: { querySelector: () => ({ getBoundingClientRect: () => ({ height: 540 }) }), documentElement: { scrollHeight: 540 } },
    innerWidth: 960, innerHeight: 540, outerWidth: 960, outerHeight: 600, devicePixelRatio: 1.25,
    screen: { width: 1920, height: 1080 }, CSS: { supports: () => false }, localStorage: { getItem: () => 'test-session' },
    setTimeout: (fn, delay) => { const id = ++sequence; timers.set(id, { fn, delay }); return id },
    clearTimeout: id => timers.delete(id),
    fetch: async (url, request) => { reports.push({ url, payload: JSON.parse(request.body) }); return { ok: true } },
  })
  const source = compiled.outputFiles[0].text + `\n// fixture ${++iteration}`
  const api = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`)
  const flush = async () => {
    for (const [id, timer] of [...timers]) if (timer.delay === 2000) { timers.delete(id); timer.fn() }
    await Promise.resolve(); await Promise.resolve()
  }
  const header = 'a'.repeat(32) + ';' + Math.floor(Date.now() / 1000 + 60)
  return { api, listeners, timers, reports, flush, header }
}

test('off mode has no event listeners, timers or reporting requests', async () => {
  const f = await fixture()
  f.api.browserDiagnosticResponse(undefined, { path: '/linux-do-books', status: 200 })
  f.api.browserDiagnosticRequestFailure('/linux-do-books', 'ECONNABORTED')
  assert.equal(f.listeners.size, 0)
  assert.equal(f.timers.size, 0)
  assert.equal(f.reports.length, 0)
})

test('debug snapshots batch resize bursts and stop when the backend disables the run', async () => {
  const f = await fixture()
  f.api.browserDiagnosticResponse(f.header, { path: '/api/book?token=hidden', status: 200 })
  for (let i = 0; i < 100; i++) f.listeners.get('resize')()
  await f.flush()
  assert.equal(f.reports.length, 1)
  assert.equal(f.reports[0].payload.events.length, 10)
  assert.equal(f.reports[0].payload.events[0].values.supportsDvh, false)
  assert.ok(!JSON.stringify(f.reports).includes('hidden'))
  f.api.browserDiagnosticResponse(undefined)
  assert.equal(f.listeners.size, 0)
  assert.equal(f.timers.size, 0)
  f.api.browserDiagnosticResponse(f.header) // 迟到响应不能重新启动已关闭的会话。
  assert.equal(f.timers.size, 0)
})

test('report quota ends collection without restarting on later responses', async () => {
  const f = await fixture()
  for (let i = 0; i < 30; i++) {
    f.api.browserDiagnosticResponse(f.header, { path: '/api/book', status: 200 })
    await f.flush()
  }
  assert.equal(f.reports.length, 25)
  assert.equal(f.listeners.size, 0)
  assert.equal(f.timers.size, 0)
})
