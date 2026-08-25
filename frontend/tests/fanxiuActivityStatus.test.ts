import assert from 'node:assert/strict'
import test from 'node:test'

import {
  formatActivityUpdatedAt,
  isActivityActiveToday,
  isActivityCollectibleToday,
  shouldAutoCollectActivity,
} from '../src/standard/fanxiu/components/activityStatus.ts'

const augustFourth = new Date(2026, 7, 4, 12)

test('activity update time is compact while preserving dates when needed', () => {
  assert.equal(formatActivityUpdatedAt('2026-08-04T10:15:29', augustFourth), '10:15:29')
  assert.equal(formatActivityUpdatedAt('2026-08-03T20:21:46', augustFourth), '8/3 20:21:46')
  assert.equal(formatActivityUpdatedAt('2025-08-03T20:21:46', augustFourth), '2025/8/3 20:21:46')
})

test('finished activity is inactive', () => {
  assert.equal(isActivityActiveToday({ start_date: '2026-08-02', end_date: '2026-08-02' }, augustFourth), false)
})

test('activity currently within its date range is active', () => {
  assert.equal(isActivityActiveToday({ start_date: '2026-08-03', end_date: '2026-08-06' }, augustFourth), true)
})

test('start and end dates are both inclusive', () => {
  const activity = { start_date: '2026-08-03', end_date: '2026-08-06' }
  assert.equal(isActivityActiveToday(activity, new Date(2026, 7, 3, 0)), true)
  assert.equal(isActivityActiveToday(activity, new Date(2026, 7, 6, 23, 59)), true)
})

test('active activity becomes due only after its data is older than one hour', () => {
  const activity = {
    start_date: '2026-08-03',
    end_date: '2026-08-06',
    captured_at: '2026-08-04T10:59:59',
  }
  assert.equal(shouldAutoCollectActivity(activity, undefined, new Date(2026, 7, 4, 12)), true)
  assert.equal(
    shouldAutoCollectActivity(
      { ...activity, captured_at: '2026-08-04T11:00:00' },
      undefined,
      new Date(2026, 7, 4, 12),
    ),
    false,
  )
})

test('finished activities never auto collect even when their data is stale', () => {
  assert.equal(shouldAutoCollectActivity({
    start_date: '2026-08-02',
    end_date: '2026-08-02',
    captured_at: '2026-08-02T08:00:00',
  }, undefined, augustFourth), false)
})

test('settlement window stays collectible after formal activity end', () => {
  const activity = {
    start_date: '2026-08-18',
    end_date: '2026-08-20',
    close_panel_date: '2026-08-21',
    close_panel_at: '2026-08-21T23:58:59+08:00',
    captured_at: '2026-08-20T18:39:13',
  }
  const settlementDay = new Date(2026, 7, 21, 12)
  assert.equal(isActivityActiveToday(activity, settlementDay), false)
  assert.equal(isActivityCollectibleToday(activity, settlementDay), true)
  assert.equal(shouldAutoCollectActivity(activity, undefined, settlementDay), true)
  assert.equal(isActivityCollectibleToday(activity, new Date('2026-08-21T23:59:00+08:00')), false)
})

test('missing data and any stale related snapshot make an active activity due', () => {
  const activity = {
    start_date: '2026-08-03',
    end_date: '2026-08-06',
    captured_at: '2026-08-04T11:30:00',
  }
  assert.equal(shouldAutoCollectActivity(activity, ['2026-08-04T11:30:00', ''], augustFourth), true)
  assert.equal(
    shouldAutoCollectActivity(activity, ['2026-08-04T11:30:00', '2026-08-04T11:15:00'], augustFourth),
    false,
  )
})
