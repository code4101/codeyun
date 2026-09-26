import assert from 'node:assert/strict'
import test from 'node:test'
import { detectPageCrop, FULL_PAGE } from '../src/standard/pdf/resource-view/pdfPageCrop.ts'

test('crop keeps blank pages and full-bleed artwork intact', () => {
  const white = new Uint8ClampedArray(200 * 300 * 4).fill(255)
  assert.deepEqual(detectPageCrop(white, 200, 300), FULL_PAGE)
  for (let i = 0; i < white.length; i += 4) white[i] = white[i + 1] = white[i + 2] = 0
  assert.deepEqual(detectPageCrop(white, 200, 300), FULL_PAGE)
})
test('crop removes paper margins, ignores specks and retains ink with padding', () => {
  const pixels = new Uint8ClampedArray(200 * 300 * 4).fill(255)
  const ink = (x: number, y: number) => { const i = (y * 200 + x) * 4; pixels[i] = pixels[i + 1] = pixels[i + 2] = 0 }
  for (let y = 60; y < 240; y++) for (let x = 40; x < 160; x++) ink(x, y)
  ink(2, 2); ink(198, 295)
  for (let x = 0; x < 200; x++) ink(x, 0)
  const crop = detectPageCrop(pixels, 200, 300)
  assert.deepEqual(crop, { left: .17, top: .18, right: .17, bottom: .18 })
  assert.ok(crop.left * 200 < 40 && crop.top * 300 < 60)
})
