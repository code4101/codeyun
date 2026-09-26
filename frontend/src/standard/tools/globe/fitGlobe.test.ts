import { test } from 'node:test'
import assert from 'node:assert/strict'
import { fitGlobeZoom } from './fitGlobe'

test('full globe occupies 92% of the shorter viewport side across screens and latitudes', () => {
  for (const [width, height] of [[1200, 1000], [360, 640], [1920, 600], [600, 600]]) {
    for (const latitude of [0, 25, -60, 80]) {
      const zoom = fitGlobeZoom(width!, height!, latitude, 36.87)!
      const radius = 512 * 2 ** zoom / (2 * Math.PI * Math.cos(latitude * Math.PI / 180))
      const distance = height! / (2 * Math.tan(36.87 * Math.PI / 360))
      const angularRadius = Math.asin(radius / (distance + radius))
      const diameter = 2 * distance * Math.tan(angularRadius)
      assert.ok(Math.abs(diameter - Math.min(width!, height!) * 0.92) < 1e-8)
    }
  }
  assert.equal(fitGlobeZoom(0, 600, 25, 36.87), null)
  assert.equal(fitGlobeZoom(600, 0, 25, 36.87), null)
})
