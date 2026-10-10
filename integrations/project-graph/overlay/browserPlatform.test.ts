import { test } from 'node:test';
import assert from 'node:assert/strict';
import { classifyMacWheel, detectDesktopMac } from './browserPlatform.ts';

const mac = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/130.0.0.0 Safari/537.36';
test('desktop Mac is detected; iPadOS and Windows are not', () => {
  assert.equal(detectDesktopMac(mac, 0), true);
  assert.equal(detectDesktopMac(mac, 5), false);
  assert.equal(detectDesktopMac('Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/130', 0), false);
});
test('Mac wheel: pinch by ctrlKey, notches stay wheel, other scrolls pan', () => {
  assert.equal(classifyMacWheel({ ctrlKey: true, deltaX: 0, wheelDeltaY: -3 }, false), 'pinch');
  assert.equal(classifyMacWheel({ ctrlKey: true, deltaX: 0, wheelDeltaY: -3.5 }, false), 'pinch');
  assert.equal(classifyMacWheel({ ctrlKey: true, deltaX: 0, wheelDeltaY: -120 }, true), 'wheel');
  assert.equal(classifyMacWheel({ ctrlKey: false, deltaX: 0, wheelDeltaY: -240 }, false), 'wheel');
  assert.equal(classifyMacWheel({ ctrlKey: false, deltaX: 0, wheelDeltaY: -4.5 }, false), 'pan');
  assert.equal(classifyMacWheel({ ctrlKey: false, deltaX: 3, wheelDeltaY: -120 }, false), 'pan');
  assert.equal(classifyMacWheel({ ctrlKey: false, deltaX: 0 }, false), 'pan');
});
test('explicit device overrides the heuristic; pinch and line mode are fixed', () => {
  const collision = { ctrlKey: false, deltaX: 0, deltaY: 40, wheelDeltaY: -120 };
  assert.equal(classifyMacWheel(collision, false, 'auto'), 'wheel');
  assert.equal(classifyMacWheel(collision, false, 'trackpad'), 'pan');
  assert.equal(classifyMacWheel({ ctrlKey: false, deltaX: 0, wheelDeltaY: -4.5 }, false, 'mouse'), 'wheel');
  assert.equal(classifyMacWheel({ ctrlKey: false, deltaX: 0, deltaMode: 1, wheelDeltaY: -3 }, false, 'auto'), 'wheel');
  for (const device of ['auto', 'trackpad', 'mouse'] as const) assert.equal(classifyMacWheel({ ctrlKey: true, deltaX: 0 }, false, device), 'pinch');
});
