export interface HighlightRect { x: number; y: number; width: number; height: number }

/** Bridge small inter-character gaps within a line, never across lines or columns.
 * Work in CSS pixels so the gap threshold follows the current zoom.
 */
export function joinHighlightRects(rects: HighlightRect[]): HighlightRect[] {
  const rows: HighlightRect[][] = [];
  for (const rect of [...rects].sort((a, b) => (a.y + a.height / 2) - (b.y + b.height / 2))) {
    const row = rows[rows.length - 1];
    const anchor = row?.[0];
    if (anchor && Math.abs(rect.y + rect.height / 2 - anchor.y - anchor.height / 2) < Math.min(rect.height, anchor.height) * .45) row.push(rect);
    else rows.push([rect]);
  }
  return rows.flatMap(row => {
    const merged: HighlightRect[] = [];
    for (const rect of row.sort((a, b) => a.x - b.x)) {
      const last = merged[merged.length - 1];
      if (last && rect.x - last.x - last.width <= Math.max(2, Math.min(rect.height, last.height) * .65)) {
        const right = Math.max(last.x + last.width, rect.x + rect.width);
        const bottom = Math.max(last.y + last.height, rect.y + rect.height);
        last.y = Math.min(last.y, rect.y);
        last.width = right - last.x;
        last.height = bottom - last.y;
      } else merged.push({...rect});
    }
    return merged;
  });
}

/** Current-page index only; paragraph boundaries remain hard search boundaries.
 * Normalized offsets map back to DOM text nodes, including annotation wrappers.
 */
export function indexPageText(root: HTMLElement) {
  const blocks = [...root.querySelectorAll<HTMLElement>('.ocr-text-block')];
  return (blocks.length ? blocks : [root]).map(block => {
    const points: Array<{node: Text; start: number; end: number}> = [];
    let text = '';
    const walker = document.createTreeWalker(block, NodeFilter.SHOW_TEXT);
    while (walker.nextNode()) {
      const node = walker.currentNode as Text;
      let offset = 0;
      for (const char of node.data) {
        const normalized = char.toLowerCase().replace(/\s/g, '');
        text += normalized;
        for (let i = 0; i < normalized.length; i++) points.push({node, start: offset, end: offset + char.length});
        offset += char.length;
      }
    }
    return {text, points};
  });
}

export function findPageText(index: ReturnType<typeof indexPageText>, query: string) {
  const term = query.toLowerCase().replace(/\s/g, '');
  const hits: Array<{start: {node: Text; start: number}; end: {node: Text; end: number}}> = [];
  if (!term) return hits;
  for (const block of index) {
    let offset = block.text.indexOf(term);
    while (offset >= 0) {
      hits.push({start: block.points[offset], end: block.points[offset + term.length - 1]});
      offset = block.text.indexOf(term, offset + term.length);
    }
  }
  return hits;
}
