import assert from 'node:assert/strict';
import test from 'node:test';
import { resolveNodeProgressStyle } from '../src/utils/nodeProgressStyle.ts';

test('September 19 diary durations fill cards even while work is ongoing', () => {
  const full = resolveNodeProgressStyle('doing', 814 / 814, '#67c23a', '#FFFFFF');
  assert.equal(full?.backgroundColor, '#67c23a');
  for (const minutes of [28, 35, 6, 26]) {
    const style = resolveNodeProgressStyle('doing', minutes / 814, '#67c23a', '#FFFFFF');
    assert.equal(style?.partialFillRatio, minutes / 814);
    assert.ok(style?.backgroundImage.includes(`${(minutes / 814 * 100).toFixed(2)}%`));
    assert.deepEqual(style, resolveNodeProgressStyle('done', minutes / 814, '#67c23a', '#FFFFFF'));
  }
});

test('missing progress preserves stage styling and explicit zero stays empty', () => {
  assert.equal(resolveNodeProgressStyle('doing', null, '#67c23a', '#FFFFFF'), null);
  assert.equal(resolveNodeProgressStyle('doing', Number.NaN, '#67c23a', '#FFFFFF'), null);
  assert.equal(resolveNodeProgressStyle('done', null, '#67c23a', '#FFFFFF')?.backgroundColor, '#67c23a');
  assert.equal(resolveNodeProgressStyle('done', 0, '#67c23a', '#FFFFFF')?.partialFillRatio, 0);
});
