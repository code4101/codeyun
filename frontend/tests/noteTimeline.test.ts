import assert from 'node:assert/strict';
import test from 'node:test';
import { layoutTimeline, timelineImportance } from '../src/utils/noteTimeline.ts';

test('zooming out hides low-weight neighbors; zooming in reveals them', () => {
  const notes = [{ id: 1, start_at: 400, weight: 0 }, { id: 2, start_at: 500, weight: 5 }];
  assert.deepEqual(layoutTimeline(notes, 0, 1000, 800, 1).map(x => x.note.id), [2]);
  assert.deepEqual(layoutTimeline(notes, 350, 200, 800, 1).map(x => x.note.id), [2, 1]);
});

test('cards stay inside the viewport without overlap in each lane', () => {
  const notes = Array.from({ length: 100 }, (_, id) => ({ id, start_at: id * 10, weight: id % 7 }));
  const layout = layoutTimeline(notes, 0, 990, 800, 3);
  assert.ok(layout.length > 3);
  for (const item of layout) {
    assert.ok(item.left >= 0 && item.left + 176 <= 800);
    for (const other of layout) {
      if (other !== item && other.lane === item.lane) assert.ok(Math.abs(other.left - item.left) >= 190);
    }
  }
});

test('equal dates use additional lanes with stable weight priority', () => {
  const notes = [0, 1, 2, 3].map(id => ({ id, start_at: 10, weight: id }));
  assert.deepEqual(layoutTimeline(notes, 0, 20, 800, 2).map(x => x.note.id), [3, 2]);
  assert.deepEqual(layoutTimeline([...notes].reverse(), 0, 20, 800, 2), layoutTimeline(notes, 0, 20, 800, 2));
});

test('invalid dates and out-of-range nodes are excluded; linear legacy weights share the area scale', () => {
  assert.deepEqual(layoutTimeline([{ id: 1, start_at: NaN, weight: 2 }, { id: 2, start_at: 25, weight: 2 }], 0, 20, 800, 2), []);
  assert.equal(timelineImportance({ id: 1, start_at: 0, weight: 400, weight_mode: 'linear' }), 2);
});
