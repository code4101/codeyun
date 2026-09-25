import assert from 'node:assert/strict';
import test from 'node:test';
import { createLatestRequest } from '../src/utils/latestRequest.ts';

test('replacement and closure revoke old writes without reviving them on reopen', () => {
  const requests = createLatestRequest();
  const first = requests.begin();
  assert.equal(first(), true);
  const second = requests.begin();
  assert.equal(first(), false);
  assert.equal(second(), true);
  requests.invalidate();
  assert.equal(second(), false);
  const reopened = requests.begin();
  assert.equal(first(), false);
  assert.equal(second(), false);
  assert.equal(reopened(), true);
});
