interface SpatialRun { text: string; x: number; w: number; h: number }

/** Recover layout gaps in headings/marginalia, not prose wraps.
 * A gap must exceed both ordinary token spacing and a fraction of glyph height.
 * Preserve existing whitespace; never use book-specific text or numbering rules.
 */
export function spatialRunText(runs: SpatialRun[]): string {
  const gaps = runs.slice(1).map((run, i) => Math.max(0, run.x - runs[i].x - runs[i].w));
  const sorted = [...gaps].sort((a, b) => a - b);
  const ordinary = sorted.length >= 3 ? sorted[Math.floor((sorted.length - 1) / 2)] : 0;
  return runs.reduce((text, run, i) => {
    if (!i) return run.text;
    const em = Math.max(1, Math.min(run.h, runs[i - 1].h));
    const gap = gaps[i - 1];
    const separated = gap > Math.max(em * .35, ordinary * 2.5);
    const spacer = separated && !/\s$/.test(text) && !/^\s/.test(run.text)
      ? '\u2002'.repeat(Math.min(4, Math.max(1, Math.round(gap / (em * .5))))) : '';
    return text + spacer + run.text;
  }, '');
}
