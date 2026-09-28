/** Calendar arithmetic uses local noon, so UTC offsets and DST never change the day. */
export function localDay(date = new Date()): string {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
}
export function shiftDay(day: string, amount: number): string {
  const date = new Date(`${day}T12:00:00`)
  date.setDate(date.getDate() + amount)
  return localDay(date)
}
export function journalWeek(day: string): string[] {
  const weekday = new Date(`${day}T12:00:00`).getDay()
  const monday = shiftDay(day, -((weekday + 6) % 7))
  return Array.from({ length: 7 }, (_, index) => shiftDay(monday, index))
}
export function dayLabel(day: string): string {
  return new Date(`${day}T12:00:00`).toLocaleDateString('zh-CN', { month: 'long', day: 'numeric', weekday: 'short' })
}
