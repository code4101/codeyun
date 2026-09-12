/** Screen-space layout: reserve lanes in weight order, so small notes disappear
 * first as dates converge when zooming out. Input dates use store milliseconds. */
export interface TimelineItem {
  id: number;
  start_at: number;
  weight: number;
  weight_mode?: string | null;
}

export function timelineImportance(note: TimelineItem): number {
  const weight = Number.isFinite(note.weight) ? note.weight : 0;
  return note.weight_mode === 'linear' ? Math.log2(Math.max(0.1, weight / 100)) : weight;
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
