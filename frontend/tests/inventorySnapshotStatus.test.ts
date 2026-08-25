import assert from 'node:assert/strict'
import test from 'node:test'

import {
  INVENTORY_SNAPSHOT_AUTO_COLLECT_STALE_MS,
  shouldAutoCollectInventorySnapshot,
} from '../src/standard/fanxiu/components/inventorySnapshotStatus.ts'

const now = new Date('2026-08-06T12:00:00+08:00')

test('inventory snapshot becomes due when it reaches 24 hours old', () => {
  const exactlyOneDayAgo = (now.getTime() - INVENTORY_SNAPSHOT_AUTO_COLLECT_STALE_MS) / 1000
  assert.equal(shouldAutoCollectInventorySnapshot(exactlyOneDayAgo, now), true)
  assert.equal(shouldAutoCollectInventorySnapshot(exactlyOneDayAgo + 1, now), false)
})

test('missing or invalid inventory snapshot time is due', () => {
  assert.equal(shouldAutoCollectInventorySnapshot(undefined, now), true)
  assert.equal(shouldAutoCollectInventorySnapshot(0, now), true)
  assert.equal(shouldAutoCollectInventorySnapshot(Number.NaN, now), true)
})

test('future inventory snapshot time is not due', () => {
  assert.equal(shouldAutoCollectInventorySnapshot(now.getTime() / 1000 + 60, now), false)
})
