import { test } from 'node:test'
import assert from 'node:assert/strict'
import { constrainGlobeCamera } from './cameraConstraints'

test('high latitude navigation preserves requested center and zoom at every scale', () => {
  for (const latitude of [-85.05, -80, -60, -31, 0, 31, 60, 80, 85.05]) {
    for (const zoom of [-1, 1.2, 5, 12, 18]) {
      const result = constrainGlobeCamera(latitude, zoom)
      assert.equal(result.latitude, latitude)
      assert.equal(result.zoom, zoom)
      assert.equal(result.bearing, 0)
      assert.equal(result.roll, 0)
      assert.equal(result.pitch, 0)
    }
  }
})
