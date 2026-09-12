/** Screen-space layout: reserve lanes in weight order, so small notes disappear
 * first as dates converge when zooming out. Input dates use store milliseconds. */
export interface TimelineItem {
  id: number;
  start_at: number;
  weight: number;
  weight_mode?: string | null;
}

/** Context appears only at the first tick or when its calendar unit changes.
 * Keep the full local date available to tooltips and assistive technology. */
export function labelTimelineTicks(times: number[], span: number) {
  const day = 86400000;
  let previousContext = '';
  let previousLabel = '';
  return times.map(time => {
    const date = new Date(time);
    const year = `${date.getFullYear()}年`;
    const month = `${date.getMonth() + 1}月`;
    const dateLabel = `${month}${date.getDate()}日`;
    const context = span < 3 * day ? `${year}${dateLabel}` : span < 730 * day ? year : '';
    const label = span < 3 * day
      ? `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`
      : span < 90 * day ? dateLabel : span < 730 * day ? month : year;
    const result = {
      context: context !== previousContext ? context : '',
      label: context === previousContext && label === previousLabel ? '' : label,
      fullLabel: date.toLocaleString('zh-CN'),
    };
    previousContext = context;
    previousLabel = label;
    return result;
  });
}

/** Split the loaded set, not the visible time window, to keep tracks stable while
 * panning. Reserve the last track for remaining groups so no notes are dropped. */
export function splitTimelineTracks<T>(notes: T[], count: number, groupKey: (note: T) => string) {
  const size = Math.max(1, Math.min(3, Math.trunc(count) || 1));
  if (size === 1) return [{ keys: [] as string[], notes: [...notes], remainder: false }];
  const groups = new Map<string, T[]>();
  for (const note of notes) {
    const key = groupKey(note);
    const group = groups.get(key) ?? [];
    group.push(note);
    groups.set(key, group);
  }
  const sorted = [...groups].sort((a, b) => b[1].length - a[1].length || a[0].localeCompare(b[0]));
  return Array.from({ length: size }, (_, index) => {
    const entries = index === size - 1 ? sorted.slice(index) : sorted.slice(index, index + 1);
    return { keys: entries.map(([key]) => key), notes: entries.flatMap(([, group]) => group), remainder: entries.length > 1 };
  });
}

export function timelineImportance(note: TimelineItem): number {
  const weight = Number.isFinite(note.weight) ? note.weight : 0;
  return note.weight_mode === 'linear' ? Math.log2(Math.max(0.1, weight / 100)) : weight;
}

/** Find the most populated fixed-duration window; ties favor recent notes.
 * Isolated future reminders must not pull the initial viewport away from content. */
export function denseTimelineWindow(notes: TimelineItem[], span: number) {
  const times = notes.map(note => note.start_at).filter(Number.isFinite).sort((a, b) => a - b);
  if (!times.length) return null;
  let left = 0, bestLeft = 0, bestRight = 0;
  for (let right = 0; right < times.length; right++) {
    while (times[right]! - times[left]! > span * 0.9) left++;
    if (right - left >= bestRight - bestLeft) { bestLeft = left; bestRight = right; }
  }
  return { center: (times[bestLeft]! + times[bestRight]!) / 2, span };
}

/** Track spacing follows occupied card rows, never the browser's height. */
export function timelineTrackHeight(lanes: number[], rowHeight: number) {
  return Math.max(90, 64 + (lanes.length ? Math.max(...lanes) + 1 : 0) * rowHeight);
}

/** Divide available vertical space between populated tracks. Empty tracks retain
 * only their label band; density controls occupancy rather than a fixed row cap. */
export function adaptiveTimelineLanes(availableHeight: number, counts: number[], rowHeight: number, density: number) {
  const populated = counts.filter(count => count > 0).length;
  if (!populated) return counts.map(() => 0);
  const share = (availableHeight - (counts.length - populated) * timelineTrackHeight([], rowHeight)) / populated;
  const capacity = Math.max(1, Math.floor((share - 64) / rowHeight));
  const occupancy = density === 1 ? 0.5 : density === 2 ? 0.75 : 1;
  const rows = Math.max(1, Math.floor(capacity * occupancy));
  return counts.map(count => Math.min(count, rows));
}

export function layoutTimeline<T extends TimelineItem>(
  notes: T[], start: number, span: number, width: number, lanes: number, cardWidth = 176,
) {
  if (!(span > 0) || width < cardWidth || lanes < 1) return [];
  const occupied: number[][] = Array.from({ length: lanes }, () => []);
  const candidates = notes.filter(n => Number.isFinite(n.start_at) && n.start_at >= start && n.start_at <= start + span)
    .sort((a, b) => timelineImportance(b) - timelineImportance(a) || a.start_at - b.start_at || a.id - b.id);
  const result: { note: T; x: number; left: number; lane: number; importance: number }[] = [];
  for (const note of candidates) {
    const x = (note.start_at - start) / span * width;
    const left = Math.max(0, Math.min(width - cardWidth, x - cardWidth / 2));
    const lane = occupied.findIndex(row => row.every(other => Math.abs(other - left) >= cardWidth + 14));
    if (lane < 0) continue;
    occupied[lane]!.push(left);
    result.push({ note, x, left, lane, importance: timelineImportance(note) });
  }
  return result;
}
