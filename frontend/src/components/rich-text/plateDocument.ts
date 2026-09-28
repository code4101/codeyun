/** Plate is an independent body format, not a graph file or a Markdown string. */
import { emptyPlateValue } from './plateValue'
export type NoteBodyFormat = 'html' | 'markdown' | 'plate'
export const emptyPlateContent = () => JSON.stringify({ schema: 'codeyun.plate', version: 1, value: emptyPlateValue() })
export function readPlateContent(content: string): unknown[] {
  const body = JSON.parse(content)
  if (body?.schema !== 'codeyun.plate' || body.version !== 1 || !Array.isArray(body.value) || !body.value.length) throw new Error('无法识别 Plate 正文，请检查文档格式或版本')
  return body.value
}
export function writePlateContent(value: unknown[]): string {
  return JSON.stringify({ schema: 'codeyun.plate', version: 1, value })
}
export function platePlainText(content: string): string {
  try {
    const visit = (node: any): string => {
      if (typeof node.text === 'string') return node.text
      const children = Array.isArray(node.children) ? node.children.map(visit).join(node.type === 'codeyun-collapse' ? '\n' : '') : ''
      return node.type === 'codeyun-collapse' && typeof node.title === 'string' ? `${node.title}\n${children}` : children
    }
    return readPlateContent(content).map(visit).join('\n')
  } catch { return '无法读取的 Plate 正文' }
}
