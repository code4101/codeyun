import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import test from 'node:test'
import { createRouter, createMemoryHistory } from 'vue-router'
import { build } from 'esbuild'

test('inventory reads fixed redirects, pattern redirects, aliases and HTML without navigating', async () => {
  const result = await build({ entryPoints: [fileURLToPath(new URL('../src/router/urlCompatibility.ts', import.meta.url))], bundle: true, write: false, platform: 'node', format: 'cjs', packages: 'external' })
  const module = { exports: {} }
  new Function('require', 'module', 'exports', result.outputFiles[0].text)(createRequire(import.meta.url), module, module.exports)
  const { collectUrlCompatibility, collectHtmlCompatibility } = module.exports
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/current', component: {}, alias: '/alias' },
    { path: '/old', redirect: '/current?tab=calendar' },
    { path: '/attendance/:pathMatch(.*)*', redirect: to => ({ path: to.path.replace('/attendance', '/kq5034'), query: to.query, hash: to.hash }) },
  ] })
  const before = router.currentRoute.value
  const rules = collectUrlCompatibility(router)
  assert.equal(rules.length, 3)
  assert.equal(rules.find(rule => rule.from === '/alias').kind, '路由别名')
  assert.equal(rules.find(rule => rule.from === '/old').to, '/current?tab=calendar')
  assert.equal(rules.find(rule => rule.from.includes('attendance')).to, '/kq5034/:pathMatch(.*)*')
  assert.equal(router.currentRoute.value, before)
  const html = '<script>location.replace("/kq5034/feedback" + location.search + location.hash)</script>'
  assert.equal(collectHtmlCompatibility('/attendance-feedback/index.html', html)[0].to, '/kq5034/feedback')
  assert.deepEqual(collectHtmlCompatibility('/index.html', '<html></html>'), [])
})
