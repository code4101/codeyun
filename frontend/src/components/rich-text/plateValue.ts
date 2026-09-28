/** An editor needs a paragraph to accept input; persistence does not. */
export const emptyPlateValue = () => [{ type: 'p', children: [{ text: '' }] }]

/** Formatting and blank paragraphs are placeholders; media/math and explicit
 * containers are content, including a newly inserted empty collapse block. */
export function hasPlateContent(value: unknown): boolean {
  if (Array.isArray(value)) return value.some(hasPlateContent)
  if (!value || typeof value !== 'object') return false
  const node = value as Record<string, unknown>
  if (node.type === 'codeyun-collapse') return true
  if (typeof node.text === 'string' && node.text.trim()) return true
  if (['img', 'image', 'video', 'audio', 'file', 'media_embed'].includes(String(node.type)) &&
      typeof node.url === 'string' && node.url.trim()) return true
  if (['equation', 'inline_equation'].includes(String(node.type)) &&
      typeof node.texExpression === 'string' && node.texExpression.trim()) return true
  return hasPlateContent(node.children)
}

/** No-content is represented by absence, never by an empty editor document. */
export function storedPlateValue<T>(value: T[]): T[] {
  return hasPlateContent(value) ? value : []
}
