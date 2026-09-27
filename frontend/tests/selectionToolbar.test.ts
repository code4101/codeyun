import assert from 'node:assert/strict'
import test from 'node:test'
import { createRequire } from 'node:module'
import { selectionToolbarPosition } from '../src/components/rich-text/selectionToolbarPosition.ts'

const viewport = { left: 0, top: 0, right: 640, bottom: 480 }
const size = { width: 100, height: 32 }
const line = { left: 100, top: 120, right: 300, bottom: 144 }

test('selection toolbar stays immediately above the selection', () => {
  assert.deepEqual(selectionToolbarPosition([line], viewport, size), { left: 150, top: 80 })
})
test('screen edge flips toolbar below the selection and clamps horizontally', () => {
  assert.deepEqual(selectionToolbarPosition([{ left: 0, top: 4, right: 30, bottom: 28 }], viewport, size), { left: 8, top: 36 })
  assert.deepEqual(selectionToolbarPosition([{ ...line, left: 610, right: 640 }], viewport, size), { left: 532, top: 80 })
})
test('multi-column selection uses a visible focus line instead of a union midpoint', () => {
  const offscreen = { left: 700, right: 900, top: 50, bottom: 74 }
  assert.deepEqual(selectionToolbarPosition([line, offscreen], viewport, size), { left: 150, top: 80 })
  assert.equal(selectionToolbarPosition([offscreen], viewport, size), null)
  const second = { ...line, top: 150, bottom: 174 }
  assert.equal(selectionToolbarPosition([line, second], viewport, size)?.top, 110)
  assert.equal(selectionToolbarPosition([line, second], viewport, size, true)?.top, 80)
})
test('visual viewport offsets and dock clipping constrain placement', () => {
  assert.deepEqual(selectionToolbarPosition([line], { left: 120, top: 90, right: 320, bottom: 400 }, size), { left: 160, top: 152 })
})

test('native selectionchange opens, repositions and clears toolbar without mouseup/contextmenu', async t => {
  const { JSDOM } = createRequire(import.meta.url)('jsdom')
  const dom = new JSDOM('<div id="app"></div>')
  const win = dom.window
  const names = ['window', 'document', 'Element', 'HTMLElement', 'SVGElement', 'Node', 'getComputedStyle']
  const descriptors = names.map(name => Object.getOwnPropertyDescriptor(globalThis, name))
  names.forEach(name => Object.defineProperty(globalThis, name, { configurable: true, value: name === 'window' ? win : name === 'getComputedStyle' ? win.getComputedStyle.bind(win) : win[name] }))
  t.after(() => {
    dom.window.close()
    names.forEach((name, i) => descriptors[i] ? Object.defineProperty(globalThis, name, descriptors[i]!) : Reflect.deleteProperty(globalThis, name))
  })
  const { createApp, h, ref, Teleport, nextTick } = await import('vue')
  const { useSelectionToolbar } = await import('../src/components/rich-text/useSelectionToolbar.ts')
  let visible: ReturnType<typeof ref<boolean>>
  let refreshes = 0
  let touchMouse: () => boolean
  const app = createApp({ setup() {
    const root = ref<HTMLElement | null>(null), toolbar = ref<HTMLElement | null>(null)
    visible = ref(false)
    const controller = useSelectionToolbar(root, toolbar, () => visible.value, () => {
      refreshes++
      visible.value = !!win.getSelection()?.toString()
    })
    touchMouse = controller.isTouchMouseEvent
    return () => h('section', { style: 'transform:translate(180px,40px);overflow:hidden' }, [
      h('article', { ref: root }, 'selected words'),
      h(Teleport, { to: 'body' }, visible.value ? h('div', { ref: toolbar, id: 'toolbar', style: controller.style.value }, '高亮 批注') : []),
    ])
  } })
  app.mount('#app')
  const doc = win.document
  const range = doc.createRange()
  range.selectNodeContents(doc.querySelector('article')!)
  let rect = line
  range.getClientRects = () => [rect] as any
  // jsdom has no layout engine: supply measured toolbar dimensions explicitly.
  win.HTMLElement.prototype.getBoundingClientRect = function () {
    return this.id === 'toolbar' ? { width: 100, height: 32, left: 0, top: 0, right: 100, bottom: 32 }
      : { width: 640, height: 480, ...viewport }
  }
  win.getSelection()!.addRange(range)
  const settle = async () => { await new Promise(resolve => setTimeout(resolve, 140)); await nextTick() }
  doc.dispatchEvent(new win.Event('selectionchange'))
  await settle()
  const toolbar = doc.getElementById('toolbar')!
  assert.equal(toolbar.parentElement, doc.body)
  assert.equal(toolbar.style.left, '150px')
  assert.equal(toolbar.style.top, '80px')
  rect = { ...line, top: 200, bottom: 224 }
  doc.dispatchEvent(new win.Event('selectionchange'))
  await settle()
  assert.equal(toolbar.style.top, '160px')
  doc.dispatchEvent(new win.Event('touchstart'))
  assert(touchMouse!())
  win.getSelection()!.removeAllRanges()
  doc.dispatchEvent(new win.Event('selectionchange'))
  await settle()
  assert.equal(doc.getElementById('toolbar'), null)
  app.unmount()
  const before = refreshes
  doc.dispatchEvent(new win.Event('selectionchange'))
  await settle()
  assert.equal(refreshes, before)
})
