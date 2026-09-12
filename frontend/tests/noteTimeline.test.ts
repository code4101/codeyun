import assert from 'node:assert/strict';
import test from 'node:test';
import { adaptiveTimelineLanes, denseTimelineWindow, labelTimelineTicks, layoutTimeline, splitTimelineTracks, timelineImportance, timelineTrackHeight } from '../src/utils/noteTimeline.ts';

test('date ruler prints a year once and restores it across a year boundary', () => {
  const labels = labelTimelineTicks([new Date(2022, 11, 20).getTime(), new Date(2022, 11, 25).getTime(), new Date(2023, 0, 2).getTime()], 30 * 86400000);
  assert.deepEqual(labels.map(item => item.context), ['2022年', '', '2023年']);
  assert.deepEqual(labels.map(item => item.label), ['12月20日', '12月25日', '1月2日']);
});

test('zoomed-in ruler shares date context, and zoomed-out ruler uses months or years', () => {
  const times = [new Date(2022, 11, 31, 22).getTime(), new Date(2022, 11, 31, 23).getTime(), new Date(2023, 0, 1, 0).getTime()];
  assert.deepEqual(labelTimelineTicks(times, 86400000).map(item => [item.context, item.label]), [
    ['2022年12月31日', '22:00'], ['', '23:00'], ['2023年1月1日', '00:00'],
  ]);
  assert.deepEqual(labelTimelineTicks(times, 365 * 86400000).map(item => item.label), ['12月', '', '1月']);
  assert.deepEqual(labelTimelineTicks(times, 1000 * 86400000).map(item => item.label), ['2022年', '', '2023年']);
});

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

test('one track includes everything; multiple tracks partition without losing or duplicating notes', () => {
  const notes = ['a', 'b', 'a', 'c', 'd', 'b', 'a'].map((category, id) => ({ id, category }));
  const group = (note: typeof notes[number]) => note.category;
  assert.deepEqual(splitTimelineTracks(notes, 1, group)[0]!.notes, notes);
  for (const count of [2, 3]) {
    const tracks = splitTimelineTracks(notes, count, group);
    assert.equal(tracks.length, count);
    assert.deepEqual(tracks.flatMap(track => track.notes.map(note => note.id)).sort(), notes.map(note => note.id).sort());
    assert.deepEqual(tracks[0]!.keys, ['a']);
    assert.equal(tracks[count - 1]!.remainder, true);
  }
});

test('group assignments are stable across input order and empty tracks are preserved', () => {
  const notes = [{ id: 1, category: 'a' }, { id: 2, category: 'b' }];
  const group = (note: typeof notes[number]) => note.category;
  const tracks = splitTimelineTracks(notes, 3, group);
  assert.deepEqual(tracks.map(track => track.keys), splitTimelineTracks([...notes].reverse(), 3, group).map(track => track.keys));
  assert.equal(tracks[2]!.notes.length, 0);
  assert.equal(splitTimelineTracks([], 3, () => '').length, 3);
});

test('parallel axes use the same date coordinates but have independent visibility budgets', () => {
  const notes = [
    { id: 1, category: 'a', start_at: 10, weight: 5 },
    { id: 2, category: 'b', start_at: 10, weight: 0 },
  ];
  assert.equal(layoutTimeline(notes, 0, 20, 800, 1).length, 1);
  const layouts = splitTimelineTracks(notes, 2, note => note.category).map(track => layoutTimeline(track.notes, 0, 20, 800, 1));
  assert.equal(layouts.flat().length, 2);
  assert.equal(layouts[0]![0]!.x, layouts[1]![0]!.x);
});

test('initial content window stays on populated dates despite distant reminders', () => {
  const day = 86400000;
  const notes = [0, 1, 2, 3, 4, 300, 600].map((time, id) => ({ id, start_at: time * day, weight: 0 }));
  const viewport = denseTimelineWindow(notes, 30 * day)!;
  assert.equal(viewport.center, 2 * day);
  assert.equal(notes.filter(note => Math.abs(note.start_at - viewport.center) <= viewport.span / 2).length, 5);
  assert.equal(denseTimelineWindow([], 30 * day), null);
  assert.equal(denseTimelineWindow([{ id: 0, start_at: NaN, weight: 0 }], 30 * day), null);
});

test('track spacing collapses empty rows and grows only for occupied lanes', () => {
  assert.equal(timelineTrackHeight([], 96), 90);
  assert.equal(timelineTrackHeight([0], 96), 160);
  assert.equal(timelineTrackHeight([0, 1, 2], 96), 352);
});

test('compact cards reveal more notes in the same time window while preserving priority', () => {
  const notes = Array.from({ length: 80 }, (_, id) => ({ id, start_at: id * 10, weight: id % 4 }));
  const regular = layoutTimeline(notes, 0, 800, 1200, 3, 176);
  const compact = layoutTimeline(notes, 0, 800, 1200, 4, 132);
  assert.ok(compact.length > regular.length);
  assert.equal(compact[0]!.note.weight, 3);
});

test('taller viewports reveal more rows instead of adding empty track spacing', () => {
  assert.deepEqual(adaptiveTimelineLanes(500, [100], 96, 3), [4]);
  assert.deepEqual(adaptiveTimelineLanes(900, [100], 96, 3), [8]);
  const notes = Array.from({ length: 100 }, (_, id) => ({ id, start_at: 10, weight: id }));
  const displayed = (height: number) => layoutTimeline(notes, 0, 20, 800, adaptiveTimelineLanes(height, [100], 96, 3)[0]!).length;
  assert.ok(displayed(900) > displayed(500));
});

test('multiple axes share height, empty axes stay compact, small screens retain a row', () => {
  assert.deepEqual(adaptiveTimelineLanes(900, [100, 100, 100], 96, 3), [2, 2, 2]);
  assert.deepEqual(adaptiveTimelineLanes(900, [0, 100, 0], 96, 3), [0, 6, 0]);
  assert.deepEqual(adaptiveTimelineLanes(100, [100, 100, 100], 96, 3), [1, 1, 1]);
  assert.deepEqual(adaptiveTimelineLanes(900, [0, 0], 96, 3), [0, 0]);
  assert.deepEqual(adaptiveTimelineLanes(900, [1], 96, 3), [1]);
  assert.deepEqual(adaptiveTimelineLanes(900, [100], 96, 1), [4]);
});
