import type { PdfPageOcr } from '@/api/pdfDocuments';

interface SelectionRun { text: string; x: number; y: number; w: number; h: number; endOfLine: boolean; lineId: string }

export interface OcrTextBlock {
  id: string;
  kind: 'paragraph' | 'heading' | 'marginal' | 'unclassified';
  lines: Array<{ id: string; runs: SelectionRun[] }>;
}

/** One semantic reading model for the image-aligned layer and clipboard.
 * Geometry belongs to runs; paragraph membership belongs to persisted layout.
 * Unclassified lines stay separate rather than being guessed by consumers.
 */
export function ocrTextBlocks(result: PdfPageOcr): OcrTextBlock[] {
  const runs = ocrSelectionRuns(result);
  const byLine = new Map(result.lines.map(line => [line.line_id,
    { id: line.line_id, runs: runs.filter(run => run.lineId === line.line_id) }]));
  const assigned = new Set<string>();
  const blocks: OcrTextBlock[] = [];
  for (const block of result.layout?.blocks || []) {
    const lines = block.line_ids.flatMap(id => {
      const line = byLine.get(id);
      if (!line || assigned.has(id)) return [];
      assigned.add(id);
      return [line];
    });
    if (lines.length) blocks.push({id: block.id, kind: block.kind, lines});
  }
  for (const [id, line] of byLine) {
    if (!assigned.has(id)) blocks.push({id, kind: 'unclassified', lines: [line]});
  }
  return blocks;
}

/** Native OCR boxes in reading order. Retain word boxes without inventing character bounds.
 * If tokenization lost punctuation, keep the entire source line selectable instead.
 */
export function ocrSelectionRuns(result: PdfPageOcr) {
  return result.lines.flatMap<SelectionRun>(line => {
    const tokens = result.tokens.filter(token => token.parent_line_id === line.line_id)
      .sort((a, b) => a.order - b.order);
    const complete = tokens.length && tokens.map(token => token.text).join('').replace(/\s/g, '') === line.text.replace(/\s/g, '');
    if (!complete) return [{ ...line, endOfLine: true, lineId: line.line_id }];
    let cursor = 0;
    return tokens.map((token, index) => {
      const position = line.text.indexOf(token.text, cursor);
      const prefix = position >= cursor ? line.text.slice(cursor, position) : '';
      cursor = position + token.text.length;
      return { ...token, text: prefix + token.text, endOfLine: index === tokens.length - 1, lineId: line.line_id };
    });
  });
}

/** Clipboard presentation only: keep annotation offsets anchored to original DOM text.
 * Slice the selection before joining lines, so partial paragraphs never copy extra text.
 */
export function formatOcrSelection(root: HTMLElement, start: number, end: number, result: PdfPageOcr): string {
  const blocks = new Map<string, number>();
  ocrTextBlocks(result).forEach((block, index) => {
    if (block.kind !== 'unclassified') block.lines.forEach(line => blocks.set(line.id, index));
  });
  const lines: Array<{id: string; text: string}> = [];
  let cursor = 0;
  for (const span of root.querySelectorAll<HTMLElement>('.ocr-selectable-text')) {
    const text = span.textContent || '';
    const selected = text.slice(Math.max(0, start - cursor), Math.max(0, Math.min(text.length, end - cursor)));
    cursor += text.length;
    if (!selected) continue;
    const id = span.dataset.ocrLineId || '';
    const last = lines[lines.length - 1];
    if (last?.id === id) last.text += selected;
    else lines.push({id, text: selected});
  }
  return lines.reduce((text, line, index) => {
    if (!index) return line.text;
    const previous = lines[index - 1];
    const sameParagraph = blocks.has(line.id) && blocks.get(line.id) === blocks.get(previous.id);
    // Unknown structure keeps line breaks; only evidenced paragraph wraps are removed.
    const separator = sameParagraph
      ? (/[A-Za-z0-9,;:]$/.test(text) && /^[A-Za-z0-9]/.test(line.text) ? ' ' : '')
      : blocks.has(line.id) && blocks.has(previous.id) ? '\n\n' : '\n';
    return text + separator + line.text;
  }, '');
}

/** Transform OCR image pixels to CSS viewport pixels, including quarter-turn rotation.
 * Both spaces already use top-left origin. Device pixel ratio belongs to the canvas only.
 */
export function ocrViewportMatrix(geometry: PdfPageOcr['geometry'], viewport: {width: number; height: number; rotation: number}) {
  const turn = ((viewport.rotation - geometry.rotation) % 360 + 360) % 360;
  const w = geometry.width, h = geometry.height;
  const sx = viewport.width / (turn % 180 ? h : w);
  const sy = viewport.height / (turn % 180 ? w : h);
  if (turn === 90) return [0, sy, -sx, 0, viewport.width, 0];
  if (turn === 180) return [-sx, 0, 0, -sy, viewport.width, viewport.height];
  if (turn === 270) return [0, -sy, sx, 0, 0, viewport.height];
  return [sx, 0, 0, sy, 0, 0];
}

export function renderOcrSelection(root: HTMLElement, result: PdfPageOcr, viewport: {width: number; height: number; rotation: number}) {
  const context = document.createElement('canvas').getContext('2d');
  if (!context) throw new Error('无法创建文字测量画布');
  const [a, b, c, d, e, f] = ocrViewportMatrix(result.geometry, viewport);
  const fragment = document.createDocumentFragment();
  for (const block of ocrTextBlocks(result)) {
    const blockElement = document.createElement('div');
    blockElement.className = 'ocr-text-block';
    blockElement.dataset.ocrBlockId = block.id;
    blockElement.dataset.ocrBlockKind = block.kind;
    blockElement.style.display = 'contents';
    blockElement.setAttribute('role', block.kind === 'heading' ? 'heading' : 'paragraph');
    if (block.kind === 'heading') blockElement.setAttribute('aria-level', '2');
    for (const line of block.lines) {
      const lineElement = document.createElement('div');
      lineElement.className = 'ocr-text-line';
      lineElement.dataset.ocrLineId = line.id;
      lineElement.style.display = 'contents';
      for (const run of line.runs) {
    if (run.w <= 0 || run.h <= 0) continue;
    const span = document.createElement('span');
    span.textContent = run.text;
    span.className = 'ocr-selectable-text';
    span.dataset.ocrLineId = run.lineId;
    const font = `${run.h}px sans-serif`;
    context.font = font;
    const stretch = run.w / Math.max(1, context.measureText(run.text).width);
    Object.assign(span.style, {font, lineHeight: '1', left: `${a * run.x + c * run.y + e}px`,
      top: `${b * run.x + d * run.y + f}px`,
      transform: `matrix(${a * stretch},${b * stretch},${c},${d},0,0)`});
    lineElement.append(span);
    if (run.endOfLine) {
      const br = document.createElement('br');
      br.setAttribute('role', 'presentation');
      br.style.left = `${a * (run.x + run.w) + c * run.y + e}px`;
      br.style.top = `${b * (run.x + run.w) + d * run.y + f}px`;
      lineElement.append(br);
    }
      }
      blockElement.append(lineElement);
    }
    fragment.append(blockElement);
  }
  root.replaceChildren(fragment);
}
