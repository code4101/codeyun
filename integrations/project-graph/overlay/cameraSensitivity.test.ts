import assert from 'node:assert/strict';
import test from 'node:test';
import { clampSensitivity, sensitivityDefaults, wheelPixels } from './cameraSensitivity.ts';

test('sensitivity is clamped to its range; invalid or empty input restores the default', () => {
  assert.equal(clampSensitivity('scroll', 100), 4);
  assert.equal(clampSensitivity('scroll', 0), 0.25);
  assert.equal(clampSensitivity('zoom', '0.555'), 0.56);
  assert.equal(clampSensitivity('zoom', 'abc'), sensitivityDefaults.zoom);
  assert.equal(clampSensitivity('zoom', ''), sensitivityDefaults.zoom);
});
test('non-Mac defaults keep upstream behaviour', () => {
  assert.deepEqual(sensitivityDefaults, { scroll: 1, zoom: 1 });
});
test('deltaMode lines and pages are normalised to pixels, sign preserved', () => {
  assert.equal(wheelPixels(5, 0), 5);
  assert.equal(wheelPixels(-3, 1), -48);
  assert.equal(wheelPixels(1, 2, 700), 700);
});
