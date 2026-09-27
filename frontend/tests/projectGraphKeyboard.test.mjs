import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import { transform } from 'esbuild'

const source = await readFile(new URL('../../integrations/project-graph/overlay/keyboardLifecycle.ts', import.meta.url), 'utf8')
const { code } = await transform(source, { loader: 'ts', format: 'esm' })
const { installKeyboardLifecycle } = await import(`data:text/javascript;base64,${Buffer.from(code).toString('base64')}`)

test('key release and focus loss clear held state and repaint even while the renderer is idle', () => {
  const win = new EventTarget(), doc = new EventTarget()
  doc.activeElement = null
  doc.hidden = false
  const held = new Set()
  let resets = 0, paints = 0, starts = 0
  const dispose = installKeyboardLifecycle({
    controller: { pressingKeySet: held, resetCountdownTimer() { resets++ } },
    renderer: { tick() { paints++ } }, loop() { starts++ },
  }, win, doc)
  const send = (target, type, props = {}) => {
    const event = Object.assign(new Event(type, { cancelable: true }), props)
    target.dispatchEvent(event)
    return event
  }
  held.add('alt')
  send(win, 'keyup', { key: 'Alt', altKey: false })
  assert.equal(held.size, 0)
  assert.ok(paints > 0)
  assert.equal(resets, paints)
  for (const action of [() => send(win, 'blur'), () => { doc.hidden = true; send(doc, 'visibilitychange') },
    () => { doc.activeElement = { closest: () => ({}) }; send(doc, 'focusin') }]) {
    held.add('alt'); held.add('a')
    const previous = paints
    action()
    assert.equal(held.size, 0)
    assert.ok(paints > previous)
  }
  doc.activeElement = null
  held.add('alt'); held.add('control')
  send(win, 'pointermove', { altKey: false, ctrlKey: true })
  assert.deepEqual([...held], ['control'], 'correct stale modifiers without releasing keys still held')
  assert.equal(send(win, 'keydown', { key: 'Alt', altKey: true }).defaultPrevented, true)
  assert.equal(starts, 1)
  doc.activeElement = { closest: () => ({}) }
  assert.equal(send(win, 'keydown', { key: 'Alt', altKey: true }).defaultPrevented, false)
  dispose()
  held.add('alt')
  send(win, 'keyup', { key: 'Alt' })
  assert.ok(held.has('alt'), 'dispose removes all handlers')
})
