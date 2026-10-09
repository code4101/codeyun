import assert from 'node:assert/strict'
import test, { type TestContext } from 'node:test'
import { presentFrame } from '../src/components/window-projection/framePresentation.ts'

function fixture(t: TestContext) {
  const pending: { resolve: () => void; reject: (error: Error) => void }[] = []
  class MockImage {
    src = ''
    decode() { return new Promise<void>((resolve, reject) => pending.push({ resolve, reject })) }
  }
  const previous = globalThis.Image
  globalThis.Image = MockImage as unknown as typeof Image
  t.after(() => { globalThis.Image = previous })
  const writes: unknown[] = []
  let width = 100, height = 100
  const target = {
    get width() { return width }, set width(value: number) { width = value; writes.push(['width', value]) },
    get height() { return height }, set height(value: number) { height = value; writes.push(['height', value]) },
    getContext: () => ({ drawImage: (image: MockImage, x: number, y: number) => writes.push([image.src, x, y]) }),
  } as unknown as HTMLCanvasElement
  return { pending, writes, target }
}

// Node has no native Image; each test installs and restores its decoder.
Object.defineProperty(globalThis, 'Image', { value: class {}, configurable: true, writable: true })
const frame = { width: 200, height: 150, patches: [{ x: 0, y: 0, image: 'a' }, { x: 100, y: 0, image: 'b' }] }

test('slow tiles and resizing preserve the previous frame until the complete update is ready', async t => {
  const { pending, writes, target } = fixture(t)
  const update = presentFrame(target, frame, () => true)
  pending[1]!.resolve()
  await new Promise(resolve => setImmediate(resolve))
  assert.deepEqual(writes, [], 'no blank canvas, resize, or partial tiles during decoding')
  pending[0]!.resolve()
  assert.equal(await update, true)
  assert.deepEqual(writes, [['width', 200], ['height', 150], ['a', 0, 0], ['b', 100, 0]])
})

test('switching windows during decoding discards all pixels and geometry from the stale frame', async t => {
  const { pending, writes, target } = fixture(t)
  let current = true
  const update = presentFrame(target, frame, () => current)
  current = false
  pending.forEach(image => image.resolve())
  assert.equal(await update, false)
  assert.deepEqual(writes, [])
})

test('a broken tile preserves the complete previous frame for a later retry', async t => {
  const { pending, writes, target } = fixture(t)
  const update = presentFrame(target, frame, () => true)
  pending[0]!.resolve()
  pending[1]!.reject(new Error('decode failed'))
  await assert.rejects(update, /decode failed/)
  assert.deepEqual(writes, [])
})
