import type {PdfPageOcr} from '@/api/pdfDocuments';
import {ocrViewportMatrix} from './ocrTextLayer';

export interface TextCaret { node: Text; offset: number }

/** Freeze geometry once per gesture. Hit testing first chooses a source line,
 * then a character boundary, so whitespace never resolves to a paragraph end.
 * OCR coordinates and DOM selection remain separate; annotations may split nodes.
 */
export function createOcrHitTest(root: HTMLElement, geometry: PdfPageOcr['geometry'], viewport: {width:number; height:number; rotation:number}) {
  const context = document.createElement('canvas').getContext('2d');
  const lines = [...root.querySelectorAll<HTMLElement>('.ocr-text-line')].map(line => {
    const runs = [...line.querySelectorAll<HTMLElement>('.ocr-selectable-text')].map(span => {
      const text = span.textContent || '';
      const x = Number(span.dataset.ocrX), y = Number(span.dataset.ocrY);
      const width = Number(span.dataset.ocrWidth), height = Number(span.dataset.ocrHeight);
      const boundaries = [0];
      for (const char of text) boundaries.push(boundaries[boundaries.length - 1] + char.length);
      if (context) context.font = `${height}px sans-serif`;
      const total = context?.measureText(text).width || text.length || 1;
      const carets = boundaries.map(offset => ({offset, x:x + width * (context?.measureText(text.slice(0, offset)).width ?? offset) / total}));
      return {span, x, y, width, height, carets};
    }).filter(r => r.width > 0 && r.height > 0);
    return {runs, x:Math.min(...runs.map(r => r.x)), y:Math.min(...runs.map(r => r.y)),
      right:Math.max(...runs.map(r => r.x+r.width)), bottom:Math.max(...runs.map(r => r.y+r.height))};
  }).filter(line => line.runs.length);
  const [a,b,c,d,e,f] = ocrViewportMatrix(geometry, viewport);
  const determinant = a*d-b*c;
  return (clientX: number, clientY: number): TextCaret | null => {
    if (!lines.length || !determinant) return null;
    // Only one layout read per movement; this also follows scrolling while dragging.
    const bounds = root.getBoundingClientRect();
    const px = clientX-bounds.left-e, py = clientY-bounds.top-f;
    const x = (d*px-c*py)/determinant, y = (-b*px+a*py)/determinant;
    const distance = (value:number, low:number, high:number) => Math.max(low-value, value-high, 0);
    let line = lines[0], score = Infinity;
    for (const candidate of lines) {
      const vertical = distance(y,candidate.y,candidate.bottom);
      const horizontal = distance(x,candidate.x,candidate.right);
      const nextScore = vertical * 10000 + horizontal;
      if (nextScore < score) {line = candidate; score = nextScore;}
    }
    let run = line.runs[0], caret = run.carets[0], best = Infinity;
    for (const candidate of line.runs) {
      for (const boundary of candidate.carets) {
        const dist = Math.abs(boundary.x-x);
        if (dist < best) {best=dist; run=candidate; caret=boundary;}
      }
    }
    let remaining = caret.offset;
    const walker = document.createTreeWalker(run.span, NodeFilter.SHOW_TEXT);
    while (walker.nextNode()) {
      const node = walker.currentNode as Text;
      if (remaining <= node.length) return {node, offset:remaining};
      remaining -= node.length;
    }
    return null;
  };
}
