import assert from 'node:assert/strict'
import test from 'node:test'
import { createRenderer, defineComponent } from 'vue'
import { useAutoSave } from '../src/utils/useAutoSave.ts'

test('offline drafts retry in the background and reconnect saves the latest edit serially', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  const browser = new EventTarget()
  const storage = new Map<string, string>()
  Object.assign(browser, { localStorage: {
    getItem: (key: string) => storage.get(key) ?? null,
    setItem: (key: string, value: string) => storage.set(key, value),
    removeItem: (key: string) => storage.delete(key),
  } })
  const oldWindow = Object.getOwnPropertyDescriptor(globalThis, 'window')
  const oldDocument = Object.getOwnPropertyDescriptor(globalThis, 'document')
  Object.defineProperty(globalThis, 'window', { configurable: true, value: browser })
  Object.defineProperty(globalThis, 'document', { configurable: true, value: new EventTarget() })
  t.after(() => {
    for (const [key, descriptor] of [['window', oldWindow], ['document', oldDocument]] as const) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor)
      else Reflect.deleteProperty(globalThis, key)
    }
  })
  let offline = true
  let release: (() => void) | undefined
  const attempts: string[] = []
  let autoSave!: ReturnType<typeof useAutoSave<{ text: string }>>
  const renderer = createRenderer<any, any>({
    patchProp() {}, insert() {}, remove() {}, setText() {}, setElementText() {},
    createElement: () => ({}), createText: () => ({}), createComment: () => ({}),
    parentNode: () => null, nextSibling: () => null,
  })
  const app = renderer.createApp(defineComponent({ setup() {
    autoSave = useAutoSave({
      debounceMs: 2000, retryDelayMs: 1500, storageKey: () => 'draft',
      equals: (a: { text: string }, b) => a.text === b.text,
      save: async snapshot => {
        attempts.push(snapshot.text)
        if (offline) throw new Error('offline')
        await new Promise<void>(resolve => { release = resolve })
        return snapshot
      },
    })
    return () => null
  } }))
  app.mount({})
  t.after(() => app.unmount())
  const settle = async () => { for (let i = 0; i < 12; i++) await Promise.resolve() }
  autoSave.loadSnapshot({ text: '' })
  autoSave.markDirty({ text: 'first' })
  assert.equal(JSON.parse(storage.get('draft')!).snapshot.text, 'first')
  t.mock.timers.tick(2000)
  await settle()
  assert.equal(autoSave.saveStatus.value, 'unsaved')
  t.mock.timers.tick(1500)
  await settle()
  assert.deepEqual(attempts, ['first', 'first'])
  offline = false
  browser.dispatchEvent(new Event('online'))
  await settle()
  autoSave.markDirty({ text: 'latest' })
  release!()
  await settle()
  assert.equal(attempts.at(-1), 'latest')
  assert.equal(JSON.parse(storage.get('draft')!).snapshot.text, 'latest')
  release!()
  await settle()
  assert.equal(autoSave.saveStatus.value, 'saved')
  assert.equal(storage.has('draft'), false)
  browser.dispatchEvent(new Event('online'))
  await settle()
  assert.equal(attempts.length, 4)
})
