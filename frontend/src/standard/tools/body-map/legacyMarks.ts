/** 仅用于迁移初版专用字段。模型、通用标注和新存储均不依赖这些业务字段。 */
export function legacyMarkNote(mark: Record<string, unknown>): string {
  const parts: string[] = []
  if (typeof mark.feeling === 'string' && mark.feeling) parts.push(mark.feeling)
  if (typeof mark.severity === 'number' && Number.isInteger(mark.severity) && mark.severity >= 0 && mark.severity <= 10) {
    parts.push(`不适程度 ${mark.severity}/10`)
  }
  if (typeof mark.note === 'string' && mark.note) parts.push(mark.note)
  return parts.join('；')
}
