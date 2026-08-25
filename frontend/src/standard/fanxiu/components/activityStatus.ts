type ActivityDateRange = {
  start_date: string
  end_date: string
  close_panel_date?: string | null
  close_panel_at?: string | null
}

type RefreshableActivity = ActivityDateRange & {
  captured_at?: string | null
}

export const ACTIVITY_AUTO_COLLECT_STALE_MS = 60 * 60 * 1000

function localDateKey(now: Date): string {
  return [
    now.getFullYear(),
    String(now.getMonth() + 1).padStart(2, '0'),
    String(now.getDate()).padStart(2, '0'),
  ].join('-')
}

function localTimeKey(value: Date): string {
  return [value.getHours(), value.getMinutes(), value.getSeconds()]
    .map(part => String(part).padStart(2, '0'))
    .join(':')
}

export function formatActivityUpdatedAt(value: string, now = new Date()): string {
  const updatedAt = new Date(value)
  if (Number.isNaN(updatedAt.getTime())) return value
  const time = localTimeKey(updatedAt)
  if (localDateKey(updatedAt) === localDateKey(now)) return time
  const monthDay = `${updatedAt.getMonth() + 1}/${updatedAt.getDate()}`
  return updatedAt.getFullYear() === now.getFullYear()
    ? `${monthDay} ${time}`
    : `${updatedAt.getFullYear()}/${monthDay} ${time}`
}

export function isActivityActiveToday(
  activity: ActivityDateRange | null | undefined,
  now = new Date(),
): boolean {
  if (!activity) return false
  const today = localDateKey(now)
  return activity.start_date <= today && today <= activity.end_date
}

export function isActivityCollectibleToday(
  activity: ActivityDateRange | null | undefined,
  now = new Date(),
): boolean {
  if (!activity) return false
  const today = localDateKey(now)
  const closeDate = activity.close_panel_date || activity.end_date
  if (!(activity.start_date <= today && today <= closeDate)) return false
  const closeAt = Date.parse(String(activity.close_panel_at || ''))
  return !Number.isFinite(closeAt) || now.getTime() <= closeAt
}

export function shouldAutoCollectActivity(
  activity: RefreshableActivity | null | undefined,
  capturedAts: readonly (string | null | undefined)[] = [activity?.captured_at],
  now = new Date(),
  staleAfterMs = ACTIVITY_AUTO_COLLECT_STALE_MS,
): boolean {
  if (!isActivityCollectibleToday(activity, now)) return false
  return capturedAts.some(value => {
    const capturedAt = Date.parse(String(value || ''))
    return !Number.isFinite(capturedAt) || now.getTime() - capturedAt > staleAfterMs
  })
}
