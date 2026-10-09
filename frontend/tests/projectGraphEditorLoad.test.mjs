import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import test from 'node:test'
import { build } from 'esbuild'
import { parse, compileScript } from '@vue/compiler-sfc'
import { JSDOM } from 'jsdom'

const dom = new JSDOM('<body><div id="app"></div></body>', { url: 'http://localhost/' })
for (const key of ['window', 'document', 'Element', 'HTMLElement', 'SVGElement', 'Node', 'MutationObserver', 'MessageEvent', 'location', 'getComputedStyle']) globalThis[key] = dom.window[key]
const { createApp, nextTick, h } = await import('vue')
const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const compiled = await build({
  entryPoints: [path.join(frontend, 'src/plugins/modules/project-graph/ProjectGraphEditor.vue')], bundle: true, write: false, format: 'esm', platform: 'node',
  plugins: [{ name: 'vue', setup(b) {
    b.onResolve({ filter: /^vue$/ }, () => ({ path: pathToFileURL(path.join(frontend, 'node_modules/vue/index.mjs')).href, external: true }))
    b.onLoad({ filter: /\.vue$/ }, async ({ path: filename }) => {
      const { descriptor, errors } = parse(await readFile(filename, 'utf8'), { filename }); assert.deepEqual(errors, [])
      return { contents: compileScript(descriptor, { id: filename, inlineTemplate: true }).content, loader: 'ts' }
    })
  } }],
})
const Editor = (await import(`data:text/javascript;base64,${Buffer.from(compiled.outputFiles[0].text).toString('base64')}`)).default
const tick = (ms = 0) => new Promise(resolve => setTimeout(resolve, ms))

/** Mount the editor and speak the iframe protocol from the frame's window. */
async function open(storage) {
  const events = [], replies = []
  const app = createApp({ render: () => h(Editor, { documentId: '7', title: 'T', storage,
    onError: m => events.push(['error', m]), onLoadFailed: () => events.push(['loadFailed']), onSaved: () => events.push(['saved']) }) })
  const host = document.body.appendChild(document.createElement('div')); app.mount(host); await nextTick()
  const frame = document.querySelector('iframe'), win = frame.contentWindow
  win.postMessage = data => { replies.push(data) }   // jsdom cannot deliver to an unloaded frame
  const session = new URL(frame.src).searchParams.get('session')
  const say = (type, payload, id) => window.dispatchEvent(new window.MessageEvent('message', { origin: location.origin, source: win,
    data: { channel: 'codeyun.project-graph', version: 1, session, type, payload, id } }))
  return { app, events, replies, say }
}
const doc = (extra = {}) => ({ id: '7', title: 'T', revision: 4, bytes: new Uint8Array([1, 2]), ...extra })

test('file download starts at mount, in parallel with editor boot, and is reused once', async () => {
  let reads = 0, release
  const storage = { read: () => (reads++, new Promise(resolve => { release = () => resolve(doc()) })), write: async () => doc() }
  const { app, replies, say } = await open(storage)
  assert.equal(reads, 1, 'read begins before the iframe says ready')
  say('ready', {}, 'r1'); await tick()
  assert.equal(reads, 1, 'ready waits for the preloaded read instead of starting another')
  release(); await tick(10)
  const answer = replies.find(m => m.type === 'response' && m.id === 'r1')
  assert.deepEqual([...answer.payload.bytes], [1, 2]); assert.equal(answer.error, undefined)
  app.unmount()
})

test('startup failures request a reopen; reopening a fresh instance really loads', async () => {
  let reads = 0
  const storage = { read: async () => { if (++reads === 1) throw new Error('网络连接失败'); return doc() }, write: async () => doc() }
  const first = await open(storage)
  first.say('ready', {}, 'r1'); await tick(10)
  assert.deepEqual(first.events.map(e => e[0]), ['loadFailed', 'error'])
  assert.match(first.replies.find(m => m.id === 'r1').error, /网络连接失败/)
  first.app.unmount()
  const second = await open(storage)   // GraphWorkspace.mountDocument remounts the keyed editor
  second.say('ready', {}, 'r2'); await tick(10)
  assert.equal(second.events.length, 0)
  assert.equal(second.replies.find(m => m.id === 'r2').error, undefined)
  second.app.unmount()
})

test('collaboration credential failure during ready is a load failure', async () => {
  const storage = { read: async () => doc({ collaborative: true }), collaborationCredentials: async () => { throw new Error('登录已过期') }, write: async () => doc() }
  const { app, events, replies, say } = await open(storage)
  say('ready', {}, 'r1'); await tick(10)
  assert.deepEqual(events.map(e => e[0]), ['loadFailed', 'error'])
  assert.match(replies.find(m => m.id === 'r1').error, /登录已过期/)
  app.unmount()
})

test('iframe startup error is a load failure, but errors after boot are save errors', async () => {
  const storage = { read: async () => doc(), write: async () => { throw new Error('revision conflict') } }
  const early = await open(storage)
  early.say('error', { message: '启动失败' }); await tick()
  assert.deepEqual(early.events, [['loadFailed'], ['error', '启动失败']])
  early.app.unmount()
  const booted = await open(storage)
  booted.say('ready', {}, 'r'); await tick(10)
  booted.say('status', { state: 'loaded' }); booted.say('presented', {})
  booted.say('write', { bytes: new Uint8Array([9]) }, 'w'); await tick(10)
  assert.deepEqual(booted.events.map(e => e[0]), ['error'], 'save failure must not ask to reopen')
  booted.say('error', { message: '保存失败' }); await tick()
  assert.equal(booted.events.some(e => e[0] === 'loadFailed'), false)
  booted.app.unmount()
})

test('a read finishing after the editor closed neither answers nor reports', async () => {
  let release
  const storage = { read: () => new Promise(resolve => { release = () => resolve(doc()) }), write: async () => doc() }
  const { app, events, replies, say } = await open(storage)
  say('ready', {}, 'r1'); await tick()
  app.unmount(); release(); await tick(10)
  assert.equal(replies.some(m => m.id === 'r1'), false)
  assert.deepEqual(events, [])
})
