import assert from 'node:assert/strict'
import test from 'node:test'
import { build } from 'esbuild'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..')
const compiled = await build({
  stdin: { contents: `export * from './integrations/project-graph/overlay/bodyPreviewSettings'; export * from './integrations/project-graph/overlay/bodyPreviewRenderer'; export {Settings} from '@/core/service/Settings';`, resolveDir: root },
  bundle: true, write: false, format: 'esm', platform: 'node',
  plugins: [{ name: 'canvas-fixture', setup(builder) {
    builder.onResolve({ filter: /^@\// }, args => ({ path: args.path, namespace: 'fixture' }))
    builder.onResolve({ filter: /^@graphif\// }, args => ({ path: args.path, namespace: 'fixture' }))
    builder.onLoad({ filter: /.*/, namespace: 'fixture' }, ({ path }) => ({ contents:
      path.endsWith('/Settings') ? 'export const Settings={alwaysShowDetails:true,entityDetailsFontSize:12,entityDetailsLinesLimit:3,entityDetailsWidthLimit:200};' :
      path.endsWith('/EntityRenderer') ? 'export class EntityRenderer {}' :
      path.startsWith('@graphif/') ? 'export class Vector {constructor(x,y){this.x=x;this.y=y}}' :
      'export class Project {}; export class Entity {};'
    }))
  }}],
})
const bundle = compiled.outputFiles[0].text
const stored = new Map(), storageListeners = []
globalThis.localStorage = { getItem: key => stored.get(key) ?? null, setItem: (key, value) => stored.set(key, value) }
globalThis.window = { addEventListener: (name, listener) => { if (name === 'storage') storageListeners.push(listener) } }
const load = id => import(`data:text/javascript;base64,${Buffer.from(bundle + '\n//' + id).toString('base64')}`)

test('default off, opt-in rendering, persisted application scope and live cross-frame updates', async () => {
  const api = await load('first')
  let reads = 0, renders = 0
  const entity = { isMouseHover: true, isSelected: true,
    get details() { reads++; return [{ children: [{ text: '正文' }] }] },
    collisionBox: { getRectangle: () => ({ location: { add: () => ({}) }, size: { x: 100, y: 50 } }) } }
  const graph = { camera: { currentScale: 1 }, renderer: { transformWorld2View: x => x },
    textRenderer: { renderMultiLineText: () => renders++ }, stageStyleManager: { currentStyle: { NodeDetailsText: {} } } }
  const renderer = new api.BodyPreviewRenderer(graph)
  renderer.renderEntityDetails(entity)
  assert.equal(reads, 0, 'disabled previews skip body traversal even with legacy always-on and selection')
  assert.equal(renders, 0)
  api.setBodyPreviewEnabled(true)
  renderer.renderEntityDetails(entity)
  assert.equal(renders, 1)
  assert.equal(stored.get(api.bodyPreviewStorageKey), 'true')
  const other = await load('other-graph')
  assert.equal(other.bodyPreviewEnabled(), true, 'new graphs/reloads inherit the application preference')
  api.Settings.alwaysShowDetails = false
  entity.isMouseHover = false
  renderer.renderEntityDetails(entity)
  assert.equal(renders, 1, 'hover mode still requires hovering')
  let notified = 0
  const unsubscribe = api.subscribeBodyPreview(() => notified++)
  api.setBodyPreviewEnabled(false)
  for (const listener of storageListeners) listener({ storageArea: localStorage, key: api.bodyPreviewStorageKey, newValue: 'false' })
  assert.equal(other.bodyPreviewEnabled(), false, 'already open graphs follow the shared preference')
  entity.isMouseHover = true
  renderer.renderEntityDetails(entity)
  assert.equal(renders, 1)
  assert.ok(notified > 0)
  unsubscribe()
  stored.set(api.bodyPreviewStorageKey, 'invalid')
  assert.equal((await load('invalid')).bodyPreviewEnabled(), false)
})
