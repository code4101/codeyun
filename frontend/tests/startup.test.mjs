import assert from 'node:assert/strict'
import { readFile, readdir } from 'node:fs/promises'
import { createRequire } from 'node:module'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import test from 'node:test'
import { build } from 'esbuild'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')

test('all registered routes pass the real startup permission check', async () => {
  const result = await build({
    entryPoints: [path.join(root, 'src/router/index.ts')],
    alias: { '@': path.join(root, 'src') },
    bundle: true, write: false, platform: 'node', format: 'cjs', packages: 'external',
    plugins: [{ name: 'startup-test', setup(builder) {
      // Exercise router construction without rendering components or touching browser history.
      builder.onLoad({ filter: /\.vue$/ }, () => ({ contents: 'export default {}', loader: 'js' }))
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
  const router = module.exports.default
  assert.ok(router.resolve('/tools/globe').matched.length)
  for (const route of router.getRoutes()) {
    if (typeof route.redirect !== 'string') continue
    const target = router.resolve(route.redirect)
    assert.ok(target.matched.length, `redirect target missing: ${route.redirect}`)
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
