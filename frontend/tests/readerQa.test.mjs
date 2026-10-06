import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import test from 'node:test'
import { build } from 'esbuild'
import { compileScript, compileTemplate, parse } from '@vue/compiler-sfc'
import { createRenderer, defineComponent, h, nextTick, reactive } from 'vue'

const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const vueURL = pathToFileURL(path.join(frontend, 'node_modules/vue/dist/vue.runtime.esm-bundler.js')).href
const compiled = await build({
  stdin: { contents: "export { default as Panel } from './src/standard/pdf/library/ReaderQaPanel.vue'", resolveDir: frontend },
  bundle: true, write: false, format: 'esm', plugins: [{ name: 'qa-test', setup(builder) {
    builder.onResolve({ filter: /^vue$/ }, () => ({ path: vueURL, external: true }))
    builder.onResolve({ filter: /^@\/api\/libraryQa$|^@\/store\/userStore$|^axios$/ }, args => ({ path: args.path, namespace: 'mock' }))
    builder.onLoad({ filter: /.*/, namespace: 'mock' }, ({ path: filename }) => ({ contents:
      filename === 'axios' ? 'export default { isCancel: e => e?.name === "CanceledError", isAxiosError: () => false }' :
      filename.includes('userStore') ? 'export const useUserStore = () => globalThis.__qaUser' :
      'export const askReadingQuestion = (...args) => globalThis.__qaAsk(...args); export const fetchQaConversation = (...args) => globalThis.__qaLoad(...args); export const saveQaConversation = (...args) => globalThis.__qaSave(...args)' }))
    builder.onLoad({ filter: /\.vue$/ }, async ({ path: filename }) => {
      const { descriptor, errors } = parse(await readFile(filename, 'utf8'), { filename })
      assert.deepEqual(errors, [])
      const template = compileTemplate({ source: descriptor.template.content, filename, id: 'qa-test' })
      assert.deepEqual(template.errors, [])
      return { contents: compileScript(descriptor, { id: 'qa-test' }).content, loader: 'ts' }
    })
  } }],
})
const { Panel } = await import(`data:text/javascript;base64,${Buffer.from(compiled.outputFiles[0].text).toString('base64')}`)
Panel.render = () => null

async function mount({ persisted = new Map(), storage = new Map(), userId = 1, saveFailure = false } = {}) {
  globalThis.__qaUser = reactive({ user: { id: userId }, isAuthenticated: true })
  globalThis.localStorage = { getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value) }
  const key = scope => JSON.stringify([globalThis.__qaUser.user.id, scope.resource_type, scope.resource_id])
  globalThis.__qaLoad = async scope => structuredClone(persisted.get(key(scope)) ?? { ...scope, turns: [], revision: 0 })
  globalThis.__qaSave = async page => {
    if (saveFailure) throw new Error('save failed')
    const saved = { ...page, revision: page.revision + 1 }
    persisted.set(key(page), structuredClone(saved))
    return saved
  }
  const pending = []
  globalThis.__qaAsk = (payload, signal) => new Promise((resolve, reject) => pending.push({ payload, signal, resolve, reject }))
  const props = reactive({ resourceType: 'rich-text', resourceId: 'a', bookTitle: '书A', chapterId: 'one', chapterTitle: '第一章' })
  const renderer = createRenderer({ createComment: () => ({}), insert() {}, remove() {}, parentNode: () => null,
    nextSibling: () => null, createElement: () => ({}), createText: () => ({}), setText() {}, setElementText() {}, patchProp() {} })
  const app = renderer.createApp(defineComponent({ render: () => h(Panel, props) }))
  app.mount({})
  await nextTick()
  return { app, props, pending, state: app._instance.subTree.component.setupState }
}
const answer = { answer: '回答[1]', model: 'flash', sources: [{ number: 1, title: '第一章', chapter_id: 'one', page: null }],
  context: { location: '第一章', warnings: [], characters: 10 } }

test('uses latest chapter, keeps completed same-book history, blocks duplicate sends', async () => {
  const { app, props, state, pending } = await mount()
  state.question = '问题1'
  const first = state.send()
  await state.send()
  assert.equal(pending.length, 1)
  assert.equal(pending[0].payload.chapter_id, 'one')
  pending[0].resolve(answer); await first
  props.chapterId = 'two'; props.chapterTitle = '第二章'; await nextTick()
  assert.equal(state.turns.length, 2)
  state.question = '问题2'
  const second = state.send()
  assert.equal(pending[1].payload.chapter_id, 'two')
  assert.equal(pending[1].payload.history.length, 2)
  pending[1].resolve(answer); await second
  app.unmount()
})

test('changing chapter cancels pending answer and changing books clears history', async () => {
  const { app, props, state, pending } = await mount()
  state.question = '旧问题'
  const first = state.send()
  props.chapterId = 'two'; await nextTick()
  assert.equal(pending[0].signal.aborted, true)
  pending[0].resolve(answer); await first
  assert.equal(state.turns.length, 0)
  assert.equal(state.question, '旧问题')
  state.question = '新问题'
  const second = state.send()
  props.resourceId = 'b'; await nextTick()
  pending[1].resolve(answer); await second
  assert.equal(state.turns.length, 0)
  assert.equal(state.question, '')
  app.unmount()
})

test('account changes and clearing discard pending replies; failure retry does not duplicate question', async () => {
  const { app, state, pending } = await mount()
  state.question = '问题'
  const first = state.send()
  pending[0].reject(new Error('network')); await first
  assert.equal(state.turns.length, 0)
  assert.equal(state.question, '问题')
  const second = state.send(true)
  assert.equal(pending[1].payload.history.length, 0)
  globalThis.__qaUser.user.id = 2; await nextTick()
  assert.equal(pending[1].signal.aborted, true)
  pending[1].resolve(answer); await second
  assert.equal(state.turns.length, 0)
  state.question = '再问'
  const third = state.send()
  state.clear()
  pending[2].resolve(answer); await third
  assert.equal(state.turns.length, 0)
  app.unmount()
})

test('completed questions, sources and locations survive reopening; clear is persistent', async () => {
  const persisted = new Map(), storage = new Map()
  const first = await mount({ persisted, storage })
  first.state.question = '持久化问题'
  const sent = first.state.send()
  first.pending[0].resolve(answer); await sent; await nextTick()
  first.app.unmount()
  const remote = await mount({ persisted })
  assert.equal(remote.state.turns.length, 2, '另一设备没有本地副本，也可从服务器恢复')
  remote.app.unmount()
  const second = await mount({ persisted, storage })
  assert.equal(second.state.turns.length, 2)
  assert.equal(second.state.turns[0].content, '持久化问题')
  assert.equal(second.state.turns[1].result.sources[0].chapter_id, 'one')
  assert.equal(second.state.turns[1].location, '第一章')
  second.state.clear(); await nextTick(); second.app.unmount()
  const third = await mount({ persisted, storage })
  assert.equal(third.state.turns.length, 0)
  third.app.unmount()
})

test('draft survives failed save and is isolated by book and account', async () => {
  const storage = new Map()
  const first = await mount({ storage, saveFailure: true })
  first.state.question = '保留问题'
  const sent = first.state.send()
  first.pending[0].resolve(answer); await sent; await nextTick()
  assert.match(first.state.historyError, /保存失败/)
  first.state.question = '未发送草稿'
  first.app.unmount()
  const second = await mount({ storage })
  assert.equal(second.state.turns.length, 2)
  assert.equal(second.state.question, '未发送草稿')
  second.props.resourceId = 'b'; await nextTick()
  assert.equal(second.state.turns.length, 0)
  second.app.unmount()
  const third = await mount({ storage, userId: 2 })
  assert.equal(third.state.turns.length, 0)
  assert.equal(third.state.question, '')
  third.app.unmount()
})

test('conflicting local draft is preserved until user explicitly adopts server history', async () => {
  const scope = { resource_type: 'rich-text', resource_id: 'a' }
  const persisted = new Map([[JSON.stringify([1, 'rich-text', 'a']), { ...scope, revision: 2,
    turns: [{ role: 'user', content: '服务器问题', location: '章节' }] }]])
  const storage = new Map([[`codeyun.reader-qa:${JSON.stringify([1, 'rich-text', 'a'])}`, JSON.stringify({ revision: 1,
    turns: [{ role: 'user', content: '本地问题', location: '章节' }], question: '' })]])
  const { app, state, pending } = await mount({ persisted, storage })
  assert.equal(state.turns[0].content, '本地问题')
  assert.match(state.historyError, /其他窗口/)
  state.question = '不能带着冲突继续发送'
  await state.send()
  assert.equal(pending.length, 0)
  await state.historyStore.reload()
  assert.equal(state.turns[0].content, '服务器问题')
  assert.equal(state.historyError, '')
  app.unmount()
})
