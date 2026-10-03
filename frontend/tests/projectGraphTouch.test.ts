import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import test, { type TestContext } from 'node:test';
import { installTouchGestures } from '../../integrations/project-graph/overlay/touchGestures.ts';
const { JSDOM } = createRequire(import.meta.url)('jsdom');

function fixture(t: TestContext, edit = false) {
  t.mock.timers.enable({ apis: ['setTimeout', 'Date'] });
  const dom = new JSDOM('<canvas></canvas>');
  const canvas = dom.window.document.querySelector('canvas') as HTMLCanvasElement;
  const calls: Array<{ action: string; value?: unknown }> = [];
  const record = (action: string, value?: unknown) => calls.push({ action, value });
  const gesture = installTouchGestures(canvas, {
    focus: p => record('focus', p), target: p => p.x < 100 ? 'node' : null,
    tap: (p, double) => record('tap', { p, double }), hold: p => record('hold', p),
    start: p => { record('start', p); return edit ? 'edit' : 'pan'; },
    move: p => record('move', p), end: (p, cancelled) => record('end', { p, cancelled }),
    navigate: (previous, current, scale) => record('navigate', { previous, current, scale }),
  });
  function pointer(type: string, x = 20, y = 20, id = 1, pointerType = 'touch') {
    const event = new dom.window.Event(type, { bubbles: true, cancelable: true });
    Object.assign(event, { pointerType, pointerId: id, clientX: x, clientY: y });
    canvas.dispatchEvent(event); return event;
  }
  t.after(() => { gesture.dispose(); dom.window.close(); t.mock.timers.reset(); });
  return { calls, canvas, pointer, tick: (ms: number) => t.mock.timers.tick(ms), gesture, dom };
}

test('pointer and legacy touch releases from swipes never create tap/double-tap', t => {
  const f = fixture(t);
  let native = 0;
  for (const type of ['pointerdown', 'pointerup', 'touchstart', 'touchend']) f.canvas.addEventListener(type, () => native++);
  for (let i = 0; i < 4; i++) {
    f.pointer('pointerdown'); f.pointer('pointermove', 80); f.pointer('pointermove', 20);
    f.pointer('pointerup');
    f.canvas.dispatchEvent(new f.dom.window.Event('touchend', { bubbles: true, cancelable: true }));
    f.tick(50);
  }
  assert.equal(native, 0);
  assert.equal(f.calls.filter(c => c.action === 'tap').length, 0);
  assert.equal(f.calls.filter(c => c.action === 'navigate').length, 8);
});

test('only two stationary taps on the same target are a double tap', t => {
  const f = fixture(t);
  const tap = (x: number) => { f.pointer('pointerdown', x); f.pointer('pointerup', x); f.tick(100); };
  tap(90); tap(110); tap(110);
  assert.deepEqual(f.calls.filter(c => c.action === 'tap').map(c => (c.value as { double: boolean }).double), [false, false, true]);
});

test('second finger cancels editing; lifting one finger cannot resume edit or tap', t => {
  const f = fixture(t, true);
  f.pointer('pointerdown'); f.pointer('pointermove', 40);
  f.pointer('pointerdown', 140, 20, 2); f.pointer('pointermove', 180, 20, 2);
  f.pointer('pointerup', 180, 20, 2); f.pointer('pointermove', 60); f.pointer('pointerup', 60);
  assert.equal(f.calls.filter(c => c.action === 'end').length, 1);
  assert.equal((f.calls.find(c => c.action === 'end')!.value as { cancelled: boolean }).cancelled, true);
  assert.equal(f.calls.filter(c => c.action === 'move').length, 1);
  assert.equal(f.calls.filter(c => c.action === 'tap').length, 0);
  assert.equal(f.calls.filter(c => c.action === 'navigate').length, 1);
});

for (const reason of ['move', 'second-finger', 'pointercancel', 'blur', 'escape']) {
  test(`long press cancels on ${reason}`, t => {
    const f = fixture(t);
    f.pointer('pointerdown');
    if (reason === 'move') f.pointer('pointermove', 40);
    if (reason === 'second-finger') f.pointer('pointerdown', 120, 20, 2);
    if (reason === 'pointercancel') f.pointer('pointercancel');
    if (reason === 'blur') f.dom.window.dispatchEvent(new f.dom.window.Event('blur'));
    if (reason === 'escape') f.dom.window.dispatchEvent(new f.dom.window.KeyboardEvent('keydown', { key: 'Escape' }));
    f.tick(600);
    assert.equal(f.calls.filter(c => c.action === 'hold').length, 0);
  });
}

test('long press opens once and release is not a tap', t => {
  const f = fixture(t); f.pointer('pointerdown'); f.tick(600); f.pointer('pointerup');
  assert.equal(f.calls.filter(c => c.action === 'hold').length, 1);
  assert.equal(f.calls.filter(c => c.action === 'tap').length, 0);
});

test('mouse and Pencil keep native pointer behavior', t => {
  const f = fixture(t);
  for (const kind of ['mouse', 'pen', 'codeyun-touch']) {
    assert.equal(f.pointer('pointerdown', 20, 20, 1, kind).defaultPrevented, false);
    f.pointer('pointerup', 20, 20, 1, kind);
  }
  assert.equal(f.calls.length, 0);
});
