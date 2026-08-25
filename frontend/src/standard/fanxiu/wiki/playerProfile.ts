export type PlayerProfileRow = Record<string, any>

export function playerProfileUserKey(row: PlayerProfileRow): string {
  const roleId = String(row?.role_id_text || row?.role_id || '').trim()
  return roleId ? `id:${roleId}` : `name:${String(row?.name || '').trim()}`
}

export function playerProfileTimestamp(row: PlayerProfileRow, field = 'observed_at'): number {
  const parsed = Date.parse(String(row?.[field] || '').replace(' ', 'T'))
  return Number.isFinite(parsed) ? parsed : 0
}

function shanghaiDateKey(value: string | number | Date): string {
  const date = value instanceof Date ? value : new Date(value)
  if (!Number.isFinite(date.getTime())) return ''
  const parts = new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(date)
  const byType = Object.fromEntries(parts.map(part => [part.type, part.value]))
  return `${byType.year}-${byType.month}-${byType.day}`
}

function playerProfileObservedDateKey(row: PlayerProfileRow, field: string): string {
  const raw = String(row?.[field] || '').trim()
  if (!raw) return ''
  const normalized = raw.replace(' ', 'T')
  const timestamp = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(normalized)
    ? normalized
    : `${normalized}+08:00`
  return shanghaiDateKey(timestamp)
}

export function isPlayerProfileMetricFreshToday(
  row: PlayerProfileRow,
  field: string,
  now: string | number | Date = Date.now(),
): boolean {
  const observedDate = playerProfileObservedDateKey(row, field)
  return Boolean(observedDate) && observedDate === shanghaiDateKey(now)
}

export function playerProfileBattleValue(row: PlayerProfileRow): number | null {
  const value = Number(row?.battle_score)
  return Number.isFinite(value) && value > 0 ? value : null
}

export function comparePlayerProfileBattleDesc(
  left: PlayerProfileRow,
  right: PlayerProfileRow,
): number {
  const leftBattle = playerProfileBattleValue(left)
  const rightBattle = playerProfileBattleValue(right)
  if (leftBattle === null) return rightBattle === null ? 0 : 1
  if (rightBattle === null) return -1
  return (
    rightBattle - leftBattle
    || playerProfileTimestamp(right) - playerProfileTimestamp(left)
    || playerProfileUserKey(left).localeCompare(playerProfileUserKey(right), 'zh-Hans-CN')
    || String(left?.name || '').localeCompare(String(right?.name || ''), 'zh-Hans-CN')
  )
}

export function comparePlayerProfileBattleAsc(
  left: PlayerProfileRow,
  right: PlayerProfileRow,
): number {
  const leftBattle = playerProfileBattleValue(left)
  const rightBattle = playerProfileBattleValue(right)
  if (leftBattle === null) return rightBattle === null ? comparePlayerProfileBattleDesc(left, right) : 1
  if (rightBattle === null) return -1
  return leftBattle - rightBattle || comparePlayerProfileBattleDesc(left, right)
}

export function selectDailyPlayerProfileRepresentatives(rows: PlayerProfileRow[]): PlayerProfileRow[] {
  const byDay = new Map<string, PlayerProfileRow>()
  for (const row of rows) {
    if (playerProfileBattleValue(row) === null) continue
    const userKey = playerProfileUserKey(row)
    const dateKey = String(row?.observed_date || row?.observed_at || '').slice(0, 10)
    if (!userKey || !dateKey) continue
    const key = `${userKey}::${dateKey}`
    const current = byDay.get(key)
    if (!current || comparePlayerProfileBattleDesc(row, current) < 0) byDay.set(key, row)
  }
  return [...byDay.values()].sort((left, right) => (
    playerProfileTimestamp(right) - playerProfileTimestamp(left)
    || comparePlayerProfileBattleDesc(left, right)
  ))
}

export function selectLatestPlayerProfiles(rows: PlayerProfileRow[]): PlayerProfileRow[] {
  const byUser = new Map<string, PlayerProfileRow>()
  for (const row of selectDailyPlayerProfileRepresentatives(rows)) {
    const key = playerProfileUserKey(row)
    const current = byUser.get(key)
    if (!current || playerProfileTimestamp(row) > playerProfileTimestamp(current)) byUser.set(key, row)
  }
  return [...byUser.values()]
}

export function attachLatestXianlvTeamObservations(
  rows: PlayerProfileRow[],
  xianlvRows: PlayerProfileRow[],
): PlayerProfileRow[] {
  const byUser = new Map<string, PlayerProfileRow>()
  for (const candidate of xianlvRows) {
    const score = Number(candidate?.xianlv_team_fight_score_max)
    if (!Number.isFinite(score) || score <= 0) continue
    const key = playerProfileUserKey(candidate)
    if (!key) continue
    const current = byUser.get(key)
    const candidateTime = playerProfileTimestamp(candidate, 'xianlv_team_observed_at')
    const currentTime = current ? playerProfileTimestamp(current, 'xianlv_team_observed_at') : -1
    if (!current || candidateTime > currentTime || (
      candidateTime === currentTime
      && score > Number(current?.xianlv_team_fight_score_max || 0)
    )) {
      byUser.set(key, candidate)
    }
  }
  return rows.map(row => {
    const xianlv = byUser.get(playerProfileUserKey(row))
    if (!xianlv) return row
    return {
      ...row,
      xianlv_team_fight_score_max: xianlv.xianlv_team_fight_score_max,
      xianlv_team_fight_score_text: xianlv.xianlv_team_fight_score_text,
      xianlv_team_observed_at: xianlv.xianlv_team_observed_at || xianlv.observed_at || '',
    }
  })
}
