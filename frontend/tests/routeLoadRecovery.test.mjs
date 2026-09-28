import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import test from 'node:test'
import { build } from 'esbuild'

test('failed navigation retries the target URL and bounds repeated reloads', async () => {
  const result = await build({
    entryPoints: [fileURLToPath(new URL('../src/router/routeLoadRecovery.ts', import.meta.url))],
    bundle: true, write: false, platform: 'node', format: 'cjs', packages: 'external',
  })
  const module = { exports: {} }
  new Function('require', 'module', 'exports', result.outputFiles[0].text)(createRequire(import.meta.url), module, module.exports)
  const { installRouteLoadRecovery, routeLoadError } = module.exports
  const callbacks = {}, storage = new Map(), replacements = []
  const previousWindow = globalThis.window
  globalThis.window = {
    location: { href: 'http://localhost:5173/tools/open-score-study', replace: url => replacements.push(url) },
    sessionStorage: { getItem: key => storage.get(key), setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key) },
  }
  try {
    installRouteLoadRecovery({
      beforeEach: fn => { callbacks.before = fn },
      afterEach: fn => { callbacks.after = fn },
      onError: fn => { callbacks.error = fn },
    })
    const failure = new Error('Failed to fetch dynamically imported module')
    const target = { fullPath: '/tools/color-tools?mode=rgb#palette' }
    callbacks.error(failure, target)
    const url = new URL(replacements[0])
    assert.equal(url.pathname, '/tools/color-tools')
    assert.equal(url.searchParams.get('mode'), 'rgb')
    assert.equal(url.hash, '#palette')
    assert.ok(url.searchParams.get('_route_retry'))
    callbacks.error(failure, target)
    assert.equal(replacements.length, 1)
    assert.match(routeLoadError.value, /页面资源加载失败/)
    callbacks.before()
    assert.equal(routeLoadError.value, '')
    callbacks.error(new Error('business failure'), target)
    assert.equal(routeLoadError.value, 'business failure')
    assert.equal(replacements.length, 1)
  } finally {
    globalThis.window = previousWindow
  }
})
