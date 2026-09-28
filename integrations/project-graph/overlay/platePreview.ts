/** Canvas previews are a bounded projection, never Markdown/JSON serialization.
 * Encoding media into text makes the upstream line wrapper measure enormous
 * suffixes repeatedly, even when the UI ultimately shows only a few lines.
 * Limits cover traversal as well as output; URLs and binary data are never read.
 */
export function platePreviewText(value: unknown[], maxChars = 1000, maxNodes = 256): string {
  if (maxChars <= 0 || maxNodes <= 0) return '';
  const stack = [{ children: value, index: 0, separator: '' }];
  let result = '', visited = 0, truncated = false;
  const append = (text: string) => {
    const room = maxChars - result.length;
    result += text.slice(0, room);
    if (text.length > room) truncated = true;
  };
  while (stack.length && result.length < maxChars && visited < maxNodes) {
    const frame = stack[stack.length - 1];
    if (frame.index >= frame.children.length) {
      append(frame.separator);
      stack.pop();
      continue;
    }
    const candidate = frame.children[frame.index++];
    visited++;
    if (!candidate || typeof candidate !== 'object') continue;
    const node = candidate as Record<string, unknown>;
    if (typeof node.text === 'string') { append(node.text); continue; }
    const type = node.type;
    if (type === 'img' || type === 'image') { append('[图片]\n'); continue; }
    if (['video', 'audio', 'file', 'media_embed'].includes(String(type))) { append('[附件]\n'); continue; }
    if (type === 'equation' || type === 'inline_equation') {
      append(typeof node.texExpression === 'string' ? node.texExpression : '[公式]');
      if (type === 'equation') append('\n');
      continue;
    }
    if (type === 'codeyun-collapse' && typeof node.title === 'string') {
      append(node.title);
      append('\n');
    }
    if (Array.isArray(node.children)) stack.push({
      children: node.children, index: 0,
      separator: ['a', 'link', 'mention', 'inline_equation'].includes(String(type)) ? '' : '\n',
    });
  }
  truncated ||= stack.some(frame => frame.index < frame.children.length);
  const text = result.trim();
  return truncated ? text.slice(0, Math.max(0, maxChars - 1)) + '…' : text;
}

/** Plate changes replace the value. Weak keys let edits/undo snapshots expire
 * instead of retaining every historical image-containing document forever. */
export function createPlatePreviewCache() {
  const cache = new WeakMap<unknown[], string>();
  return (value: unknown[]) => {
    let text = cache.get(value);
    if (text === undefined) { text = platePreviewText(value); cache.set(value, text); }
    return text;
  };
}
