import assert from 'node:assert/strict'
import test from 'node:test'

import { defaultExchangeShopSort } from '../src/standard/fanxiu/components/exchangeShopSort.ts'

test('exchange shop defaults to ascending priority when any priority is configured', () => {
  assert.deepEqual(
    defaultExchangeShopSort([
      { priority_order: null },
      { priority_order: 2 },
      { priority_order: 1 },
    ]),
    { key: 'priority_order', direction: 'asc' },
  )
})

test('exchange shop keeps source order when no priority is configured', () => {
  assert.deepEqual(
    defaultExchangeShopSort([{ priority_order: null }, {}]),
    { key: 'source_order', direction: 'asc' },
  )
})
