import { test } from 'node:test'
import assert from 'node:assert/strict'
import { chooseDragAxis, constrainGlobeCamera, preserveGlobeScale } from './cameraConstraints'

test('vertical browsing is restored, with polar limits and an upright camera at every zoom', () => {
  for (const latitude of [-85.05, -80, -60, -31, 0, 31, 60, 80, 85.05]) {
    for (const zoom of [-1, 1.2, 5, 12, 18]) {
      const result = constrainGlobeCamera(latitude, zoom)
      assert.equal(result.latitude, Math.max(-85, Math.min(85, latitude)))
      assert.equal(result.zoom, zoom)
      assert.equal(result.bearing, 0)
      assert.equal(result.roll, 0)
      assert.equal(result.pitch, 0)
    }
  }
})

test('screen gesture selects one dominant axis while ignoring click jitter', () => {
  assert.equal(chooseDragAxis(2, -2), null)
  assert.equal(chooseDragAxis(20, 6), 'horizontal')
  assert.equal(chooseDragAxis(-20, 6), 'horizontal')
  assert.equal(chooseDragAxis(6, 20), 'vertical')
  assert.equal(chooseDragAxis(6, -20), 'vertical')
})

test('axis locking does not change apparent globe scale during dragging', () => {
  for (const latitude of [-85.05, -60, 0, 40, 85.05]) {
    const corrected = preserveGlobeScale(3, latitude, 25)
    const before = 2 ** 3 / Math.cos(latitude * Math.PI / 180)
    const after = 2 ** corrected / Math.cos(25 * Math.PI / 180)
    assert.ok(Math.abs(before - after) < 1e-9)
  }
  assert.equal(preserveGlobeScale(3, 25, 25), 3)
})
