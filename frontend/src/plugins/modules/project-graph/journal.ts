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

export function shiftMonth(month: string, amount: number): string {
  const date = new Date(`${month}-01T12:00:00`)
  date.setMonth(date.getMonth() + amount)
  return localDay(date).slice(0, 7)
}
/** Complete Monday-first weeks, including clickable neighboring-month dates. */
export function journalMonth(month: string): string[] {
  const first = new Date(`${month}-01T12:00:00`)
  const last = new Date(first); last.setMonth(last.getMonth() + 1, 0)
  const offset = (first.getDay() + 6) % 7
  const count = last.getDate()
  return Array.from({ length: Math.ceil((offset + count) / 7) * 7 }, (_, index) =>
    shiftDay(`${month}-01`, index - offset))
}
