import assert from 'node:assert/strict'
import test from 'node:test'

import {
  shouldAttemptDeviceMediaSync,
} from '../src/utils/deviceMediaListPolicy.ts'

const indexedRecursiveRequest = (scanLimit: number) => ({
  recursive: true,
  scan_limit: scanLimit,
  limit: 50,
  sort_program: {
    rules: [
      { field: 'weight' },
      { field: 'modified_at' },
    ],
  },
})

test('indexed recursive media pages are attempted synchronously regardless of scan limit', () => {
  assert.equal(shouldAttemptDeviceMediaSync(indexedRecursiveRequest(2_000)), true)
  assert.equal(shouldAttemptDeviceMediaSync(indexedRecursiveRequest(5_000)), true)
  assert.equal(shouldAttemptDeviceMediaSync(indexedRecursiveRequest(50_000)), true)
})

test('filesystem and expensive media queries retain background-task guardrails', () => {
  assert.equal(shouldAttemptDeviceMediaSync({
    recursive: false,
    scan_limit: 5_000,
    limit: 50,
  }), false)

  assert.equal(shouldAttemptDeviceMediaSync({
    ...indexedRecursiveRequest(5_000),
    sort_program: { rules: [{ field: 'duplicate_cluster' }] },
  }), false)

  assert.equal(shouldAttemptDeviceMediaSync({
    ...indexedRecursiveRequest(5_000),
    sort_program: { rules: [{ field: 'visual_hash' }] },
  }), false)

  assert.equal(shouldAttemptDeviceMediaSync({
    ...indexedRecursiveRequest(5_000),
    snapshot_id: 'existing-snapshot',
  }), false)

  assert.equal(shouldAttemptDeviceMediaSync({
    ...indexedRecursiveRequest(5_000),
    limit: 200,
  }), false)
})
