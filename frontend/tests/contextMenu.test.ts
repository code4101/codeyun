import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import test, { type TestContext } from 'node:test'
import { registerContextMenuSurface, CONTEXT_MENU_HOLD_MS } from '../src/utils/contextMenu.ts'
import { contextMenuDirective } from '../src/directives/contextMenu.ts'

const require = createRequire(import.meta.url)
const { JSDOM } = require('jsdom')

function fixture(t: TestContext) {
  t.mock.timers.enable({ apis: ['setTimeout', 'Date'] })
  const dom = new JSDOM('<div id="surface"><span id="target">row</span></div><button id="other">other</button>')
  const win = dom.window
  const doc = win.document as Document
  const surface = doc.getElementById('surface')!
  const target = doc.getElementById('target')!
  const disposers: Array<() => void> = []
  t.after(() => { disposers.forEach(dispose => dispose()); win.close(); t.mock.timers.reset() })
  function register(el = surface, options = {}) {
    const dispose = registerContextMenuSurface(el, options)
    disposers.push(dispose)
    return dispose
  }
  function pointer(type: string, el = target, values: Record<string, unknown> = {}) {
    const event = new win.Event(type, { bubbles: true, cancelable: true, composed: true })
    Object.assign(event, { pointerId: 1, pointerType: 'touch', isPrimary: true, clientX: 40, clientY: 50 }, values)
    el.dispatchEvent(event)
    return event
  }
  function mouse(type = 'contextmenu', el = target) {
    const event = new win.MouseEvent(type, { bubbles: true, cancelable: true, detail: 1, clientX: 40, clientY: 50, button: type === 'click' ? 0 : 2 })
    el.dispatchEvent(event)
    return event
  }
  return { win, doc, surface, target, register, pointer, mouse, hold: () => t.mock.timers.tick(CONTEXT_MENU_HOLD_MS) }
}

test('touch hold and mouse right click share target, coordinates and capture/bubble path', t => {
  const f = fixture(t)
  f.register()
  const calls: string[] = []
  f.surface.addEventListener('contextmenu', event => {
    assert.equal(event.target, f.target)
    assert.equal(event.button, 2)
    assert.equal(event.clientX, 40)
    assert.equal(event.clientY, 50)
    calls.push('capture')
  }, true)
  f.target.addEventListener('contextmenu', () => calls.push('target'))
  f.surface.addEventListener('contextmenu', event => { calls.push('bubble'); event.preventDefault() })
  f.pointer('pointerdown')
  t.mock.timers.tick(CONTEXT_MENU_HOLD_MS - 1)
  assert.deepEqual(calls, [])
  t.mock.timers.tick(1)
  assert.deepEqual(calls, ['capture', 'target', 'bubble'])
  f.pointer('pointerup')
  f.pointer('pointerdown', f.target, { pointerType: 'mouse' })
  assert(f.mouse().defaultPrevented)
  assert.equal(calls.length, 6)
})

for (const reason of ['tap', 'move', 'scroll', 'cancel', 'multitouch', 'drag', 'blur', 'hidden', 'removed', 'unregister']) {
  test(`long press cancels on ${reason}`, t => {
    const f = fixture(t)
    const dispose = f.register()
    let count = 0
    f.surface.addEventListener('contextmenu', () => count++)
    f.pointer('pointerdown')
    if (reason === 'tap') f.pointer('pointerup')
    if (reason === 'move') f.pointer('pointermove', f.target, { clientX: 51 })
    if (reason === 'scroll') f.surface.dispatchEvent(new f.win.Event('scroll'))
    if (reason === 'cancel') f.pointer('pointercancel')
    if (reason === 'multitouch') f.pointer('pointerdown', f.target, { pointerId: 2, isPrimary: false })
    if (reason === 'drag') f.target.dispatchEvent(new f.win.Event('dragstart', { bubbles: true }))
    if (reason === 'blur') f.win.dispatchEvent(new f.win.Event('blur'))
    if (reason === 'hidden') f.doc.dispatchEvent(new f.win.Event('visibilitychange'))
    if (reason === 'removed') f.target.remove()
    if (reason === 'unregister') dispose()
    f.hold()
    assert.equal(count, 0)
  })
}

test('small finger drift is tolerated and nested surfaces open just once', t => {
  const f = fixture(t)
  f.register()
  f.register(f.target)
  let parent = 0, child = 0
  f.surface.addEventListener('contextmenu', () => parent++)
  f.target.addEventListener('contextmenu', event => { child++; event.stopPropagation() })
  f.pointer('pointerdown')
  f.pointer('pointermove', f.target, { clientX: 45, clientY: 54 })
  f.hold()
  assert.equal(child, 1)
  assert.equal(parent, 0)
})

test('browser contextmenu arriving before or after the timer opens only once', t => {
  const f = fixture(t)
  f.register()
  let count = 0
  f.target.addEventListener('contextmenu', () => count++)
  f.pointer('pointerdown')
  f.mouse()
  f.hold()
  assert.equal(count, 1)
  assert(f.mouse().defaultPrevented)
  f.pointer('pointerup')
  f.pointer('pointerdown')
  f.hold()
  assert.equal(count, 2)
  assert(f.mouse().defaultPrevented)
  assert.equal(count, 2)
})

test('release click is consumed; a subsequent deliberate tap is unaffected', t => {
  const f = fixture(t)
  f.register()
  let clicks = 0
  f.target.addEventListener('click', () => clicks++)
  f.pointer('pointerdown')
  f.hold()
  f.pointer('pointerup')
  assert(f.mouse('click').defaultPrevented)
  assert.equal(clicks, 0)
  f.pointer('pointerdown')
  f.pointer('pointerup')
  assert(!f.mouse('click').defaultPrevented)
  assert.equal(clicks, 1)
})

test('late release cannot click through when the menu removes its source', t => {
  const f = fixture(t)
  const dispose = f.register()
  f.target.addEventListener('contextmenu', () => { dispose(); f.surface.remove() })
  f.pointer('pointerdown')
  f.hold()
  const other = f.doc.getElementById('other')!
  f.pointer('pointerup', other)
  assert(f.mouse('click', other).defaultPrevented)
  t.mock.timers.tick(801)
  assert(!f.mouse('click', other).defaultPrevented)
})

for (const markup of ['<input>', '<textarea></textarea>', '<select></select>', '<div contenteditable="true"><span>edit</span></div>', '<article data-context-menu-native><span>text</span></article>', '<button disabled>disabled</button>']) {
  test(`native text/editing/disabled target is not claimed: ${markup}`, t => {
    const f = fixture(t)
    f.register()
    f.surface.innerHTML = markup
    const target = f.surface.querySelector('span') || f.surface.firstElementChild!
    let count = 0
    f.surface.addEventListener('contextmenu', () => count++)
    f.pointer('pointerdown', target as HTMLElement)
    f.hold()
    assert.equal(count, 0)
  })
}

test('table/tree adapter only claims declared rows and preserves nested target', t => {
  const f = fixture(t)
  f.register(f.surface, { selector: '.row' })
  let count = 0
  f.surface.addEventListener('contextmenu', event => {
    assert.equal((event.target as Element).closest('.row'), f.target)
    count++
  })
  f.pointer('pointerdown')
  f.hold()
  assert.equal(count, 0)
  f.pointer('pointerup')
  f.target.className = 'row'
  f.pointer('pointerdown')
  f.hold()
  assert.equal(count, 1)
})

test('directive updates closures, preserves modifiers and unregisters on unmount', t => {
  const f = fixture(t)
  let value = 'old', seen = ''
  const binding = { value: (event: MouseEvent) => { assert(event.defaultPrevented); seen = value }, modifiers: { prevent: true, stop: true, capture: true } } as any
  let bubbled = false
  f.target.addEventListener('contextmenu', () => { bubbled = true })
  contextMenuDirective.mounted!(f.surface, binding, null as never, null as never)
  value = 'new'
  contextMenuDirective.updated!(f.surface, { ...binding, value: (event: MouseEvent) => { assert(event.defaultPrevented); seen = value } }, null as never, null as never)
  f.pointer('pointerdown')
  f.hold()
  assert.equal(seen, 'new')
  assert.equal(bubbled, false)
  f.pointer('pointerup')
  contextMenuDirective.beforeUnmount!(f.surface, binding, null as never, null as never)
  assert(!f.surface.hasAttribute('data-context-menu-surface'))
  f.pointer('pointerdown')
  seen = ''
  f.hold()
  assert.equal(seen, '')
})

test('Vue mount, reactive callback update and unmount use the same directive contract', async t => {
  const f = fixture(t)
  const names = ['window', 'document', 'Element', 'SVGElement', 'HTMLElement', 'Node']
  const descriptors = names.map(name => Object.getOwnPropertyDescriptor(globalThis, name))
  names.forEach(name => Object.defineProperty(globalThis, name, { configurable: true, value: name === 'window' ? f.win : f.win[name] }))
  t.after(() => names.forEach((name, i) => {
    if (descriptors[i]) Object.defineProperty(globalThis, name, descriptors[i]!)
    else Reflect.deleteProperty(globalThis, name)
  }))
  const { createApp, h, ref, withDirectives, nextTick } = require('vue')
  const selected = ref('first')
  const seen: string[] = []
  let clicks = 0
  const app = createApp({
    setup: () => () => {
      const current = selected.value
      return withDirectives(h('button', { onClick: () => clicks++ }, current), [
        [contextMenuDirective, (event: MouseEvent) => {
          assert(event.defaultPrevented)
          seen.push(current)
        }, undefined, { prevent: true }],
      ])
    },
  })
  app.mount(f.surface)
  const button = f.surface.firstElementChild! as HTMLElement
  f.pointer('pointerdown', button)
  f.hold()
  f.pointer('pointerup', button)
  f.mouse('click', button)
  assert.equal(clicks, 0)
  selected.value = 'second'
  await nextTick()
  f.pointer('pointerdown', button)
  f.hold()
  f.pointer('pointerup', button)
  assert.deepEqual(seen, ['first', 'second'])
  app.unmount()
  assert(!button.hasAttribute('data-context-menu-surface'))
})
