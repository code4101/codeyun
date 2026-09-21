import assert from 'node:assert/strict'
import { test } from 'node:test'
import { buildPaceSegments } from './chartPaces.ts'

const day = (n: number) => `2026-09-${String(n).padStart(2, '0')}T00:00:00Z`
const pace = (start: number, end: number) => ({
  startAt: day(start), endAt: day(end), startValue: 100, endValue: 0, label: '',
})

test('early reset clips the old reference without changing its slope or source', () => {
  const original = pace(20, 27)
  const result = buildPaceSegments([pace(22, 29), original])
  assert.equal(result.length, 2)
  assert.equal(result[0]![1]![0], Date.parse(day(22)))
  assert.ok(Math.abs(result[0]![1]![1] - 100 * 5 / 7) < 1e-10)
  assert.deepEqual(result[1], [[Date.parse(day(22)), 100], [Date.parse(day(29)), 0]])
  assert.equal(original.endAt, day(27))
})

test('normal and delayed resets retain the full old reference', () => {
  for (const start of [27, 28]) {
    assert.deepEqual(buildPaceSegments([pace(20, 27), pace(start, 30)])[0], [
      [Date.parse(day(20)), 100], [Date.parse(day(27)), 0],
    ])
  }
})

test('each successive early reset truncates only its preceding segment', () => {
  const result = buildPaceSegments([pace(20, 27), pace(21, 28), pace(22, 29)])
  assert.deepEqual(result.map(segment => segment[1]![0]), [21, 22, 29].map(n => Date.parse(day(n))))
  assert.ok(result.slice(0, 2).every(segment => Math.abs(segment[1]![1] - 100 * 6 / 7) < 1e-10))
})
