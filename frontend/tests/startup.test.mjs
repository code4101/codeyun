import assert from 'node:assert/strict'
import { readFile, readdir } from 'node:fs/promises'
import { createRequire } from 'node:module'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import test from 'node:test'
import { build } from 'esbuild'
import { compileScript, parse } from '@vue/compiler-sfc'
import { createSSRApp } from 'vue'
import { renderToString } from '@vue/server-renderer'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')

async function loadRouter({ access } = {}) {
  const result = await build({
    stdin: { contents: "export { default } from './src/router/index.ts'; export { default as App } from './src/App.vue'", resolveDir: root },
    alias: { '@': path.join(root, 'src') },
    bundle: true, write: false, platform: 'node', format: 'cjs', packages: 'external',
    plugins: [{ name: 'startup-test', setup(builder) {
      // Exercise router construction without rendering components or touching browser history.
      if (access) {
        builder.onResolve({ filter: /^element-plus\/es\/locale\/lang\/zh-cn$/ }, () => ({ path: 'locale', namespace: 'test-locale' }))
        builder.onLoad({ filter: /.*/, namespace: 'test-locale' }, () => ({ contents: 'export default {}', loader: 'js' }))
        builder.onLoad({ filter: /[/\\]store[/\\](userStore|featureAccessStore)\.ts$/ }, ({ path: file }) => ({
          contents: file.endsWith('userStore.ts')
            ? 'export const useUserStore = () => globalThis.__routeAccessTest.user'
            : 'export const useFeatureAccessStore = () => globalThis.__routeAccessTest.feature',
          loader: 'js',
        }))
        builder.onLoad({ filter: /[/\\]router[/\\]routeLoadRecovery\.ts$/ }, () => ({
          contents: 'export const installRouteLoadRecovery = () => {}; export const routeLoadError = null; export const reloadCurrentPage = () => {}', loader: 'js',
        }))
      }
      builder.onLoad({ filter: /\.vue$/ }, async ({ path: file }) => {
        if (access && /[/\\](App|Forbidden)\.vue$/.test(file)) {
          const { descriptor } = parse(await readFile(file, 'utf8'), { filename: file })
          return { contents: compileScript(descriptor, { id: file, inlineTemplate: true }).content, loader: 'ts' }
        }
        return { contents: 'export default { setup() { globalThis.__routeAccessTest?.mounted.push("business"); return () => "business-page" } }', loader: 'js' }
      })
      builder.onLoad({ filter: /[/\\]router[/\\]index\.ts$/ }, async ({ path: file }) => ({
        contents: (await readFile(file, 'utf8')).replaceAll('createWebHistory', 'createMemoryHistory'), loader: 'ts',
      }))
      builder.onLoad({ filter: /[/\\]plugins[/\\]index\.ts$/ }, async ({ path: file }) => {
        let source = await readFile(file, 'utf8')
        const imports = []
        for (const [variable, filename] of [['pluginModuleFiles', 'index.ts'], ['pluginPermissionFiles', 'permissionRegistry.json']]) {
          const entries = []
          for (const entry of await readdir(path.join(root, 'src/plugins/modules'), { withFileTypes: true })) {
            if (!entry.isDirectory()) continue
            const relative = `./modules/${entry.name}/${filename}`
            try { await readFile(path.resolve(path.dirname(file), relative)) } catch (error) {
              if (error.code === 'ENOENT') continue
              throw error
            }
            const name = `pluginFile${imports.length}`
            imports.push(`import * as ${name} from ${JSON.stringify(relative)}`)
            entries.push(`${JSON.stringify(relative)}: ${name}`)
          }
          source = source.replace(new RegExp(`const ${variable} = import\\.meta\\.glob[\\s\\S]*?\\n\\)`), `const ${variable} = {${entries.join(',')}}`)
        }
        return { contents: `${imports.join('\n')}\n${source}`, loader: 'ts' }
      })
    } }],
  })
  const module = { exports: {} }
  new Function('require', 'module', 'exports', result.outputFiles[0].text)(createRequire(path.join(root, 'package.json')), module, module.exports)
  return module.exports
}

test('all registered routes pass the real startup permission check', async () => {
  const module = { exports: await loadRouter() }
  const router = module.exports.default
  assert.ok(router.resolve('/tools/globe').matched.length)
  for (const prefix of ['', '/standalone']) {
    for (const path of ['/system/accounts', '/system/my-account', '/system/storage']) {
      assert.ok(router.getRoutes().some(route => route.path === `${prefix}${path}`), `missing system page: ${prefix}${path}`)
    }
  }
  assert.equal(router.getRoutes().some(route => /^\/(standalone\/)?admin\//.test(route.path)), false, 'old admin page URLs are removed without redirects')
  for (const route of router.getRoutes()) {
    if (typeof route.redirect !== 'string') continue
    const target = router.resolve(route.redirect)
    assert.ok(target.matched.length, `redirect target missing: ${route.redirect}`)
  }
})

test('denied routes render 403 at the requested URL without mounting business pages', async () => {
  const access = {
    user: { isAuthenticated: true, user: {}, isAdmin: false },
    feature: { isReady: true, loading: false, isAllowed: () => false },
    mounted: [],
  }
  globalThis.__routeAccessTest = access
  globalThis.document = { title: '' }
  try {
    const { default: router, App } = await loadRouter({ access })
    for (const [legacy, canonical] of [
      ['/attendance/workbook/22?sheet=62623&view=lookup#row7', '/kq5034/workbook/22?sheet=62623&view=lookup#row7'],
      ['/attendance/sheet/62623?view=lookup', '/kq5034/sheet/62623?view=lookup'],
      ['/attendance-feedback?course=5034#form', '/kq5034/feedback?course=5034#form'],
      ['/attendance/configs?tab=account', '/kq5034/configs?tab=account'],
      ['/standalone/attendance/orders?order=12#detail', '/standalone/kq5034/orders?order=12#detail'],
    ]) {
      await router.push(legacy)
      assert.equal(router.currentRoute.value.fullPath, canonical)
      assert.ok(router.currentRoute.value.matched.length)
      assert.equal(router.currentRoute.value.meta.accessDenied, canonical.includes('/configs') || canonical.includes('/orders'))
    }
    for (const prefix of ['', '/standalone']) {
      await router.push(`${prefix}/system/my-account?tab=storage#usage`)
      assert.equal(router.currentRoute.value.fullPath, `${prefix}/system/my-account?tab=storage#usage`)
      assert.equal(router.currentRoute.value.meta.accessDenied, true, 'system pages enforce feature permissions')
    }
    async function render() {
      const app = createSSRApp(App)
      app.use(router)
      app.component('el-config-provider', { template: '<slot />' })
      app.component('el-button', { template: '<button><slot /></button>' })
      return renderToString(app)
    }
    for (const url of ['/tools/globe?view=china#map', '/standalone/tools/globe?view=china#map']) {
      await router.push(url)
      assert.equal(router.currentRoute.value.fullPath, url)
      assert.equal(router.currentRoute.value.meta.accessDenied, true)
      assert.match(await render(), /当前账号无权访问该功能/)
      assert.deepEqual(access.mounted, [])
    }
    router.addRoute({ path: '/test-admin', component: {}, meta: { requiresAdmin: true, skipFeatureAccess: true } })
    await router.push('/test-admin?tab=users#list')
    assert.equal(router.currentRoute.value.fullPath, '/test-admin?tab=users#list')
    assert.equal(router.currentRoute.value.meta.accessDenied, true)
    router.addRoute({ path: '/test-unmapped', component: {} })
    await router.push('/test-unmapped')
    assert.equal(router.currentRoute.value.meta.accessDenied, true)
    await router.push('/doc/public-note')
    assert.equal(router.currentRoute.value.meta.accessDenied, false)
    access.feature.isAllowed = () => true
    await router.push('/tools/globe?view=china#map')
    assert.equal(router.currentRoute.value.meta.accessDenied, false)
    assert.doesNotMatch(await render(), /当前账号无权访问该功能/)
    assert.ok(access.mounted.length > 0)
    access.feature.isAllowed = () => false
    await router.push('/tools/globe?view=world#map')
    assert.equal(router.currentRoute.value.meta.accessDenied, true)
    const returned = new Promise(resolve => { const stop = router.afterEach(() => { stop(); resolve() }) })
    router.back()
    await returned
    assert.equal(router.currentRoute.value.fullPath, '/tools/globe?view=china#map')
    assert.equal(router.currentRoute.value.meta.accessDenied, true)
  } finally {
    delete globalThis.__routeAccessTest
    delete globalThis.document
  }
})

test('boot errors remain visible even after the module load event', async () => {
  const html = await readFile(path.join(root, 'index.html'), 'utf8')
  const source = [...html.matchAll(/<script>\s*([\s\S]*?)<\/script>/g)]
    .map(match => match[1]).find(script => script.includes('const resourceState'))
  function harness() {
    const handlers = new Map()
    const elements = new Map()
    const window = { addEventListener(name, callback) {
      handlers.set(name, [...(handlers.get(name) ?? []), callback])
    } }
    const document = { getElementById(id) {
      if (!elements.has(id)) elements.set(id, { textContent: '' })
      return elements.get(id)
    } }
    new Function('window', 'document', 'Element', source)(window, document, class {})
    return {
      emit: (name, event = {}) => handlers.get(name)?.forEach(callback => callback(event)),
      detail: () => elements.get('app-shell-loading-detail').textContent,
    }
  }
  const sync = harness()
  sync.emit('error', { message: 'startup failed' })
  sync.emit('codeyun:module-loaded')
  assert.equal(sync.detail(), 'startup failed')
  const async = harness()
  async.emit('unhandledrejection', { reason: new Error('async startup failed') })
  assert.equal(async.detail(), 'async startup failed')
  const mounted = harness()
  mounted.emit('codeyun:app-mounted')
  mounted.emit('error', { message: 'later unrelated error' })
  assert.equal(mounted.detail(), '')
})
