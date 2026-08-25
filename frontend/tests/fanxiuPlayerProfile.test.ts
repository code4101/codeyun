import assert from 'node:assert/strict'
import test from 'node:test'

import {
  attachLatestXianlvTeamObservations,
  comparePlayerProfileBattleDesc,
  selectDailyPlayerProfileRepresentatives,
} from '../src/standard/fanxiu/wiki/playerProfile.ts'

test('player profiles rank by battle score even when attack is missing', () => {
  const rows = [
    { role_id_text: '1', name: '甲', battle_score: 100, observed_at: '2026-08-19 12:00:00' },
    { role_id_text: '2', name: '乙', battle_score: 200, attack_value: 1, observed_at: '2026-08-19 11:00:00' },
  ]

  assert.deepEqual([...rows].sort(comparePlayerProfileBattleDesc).map(row => row.name), ['乙', '甲'])
})

test('battle sort keeps null last and does not let attack override battle score', () => {
  const rows = [
    { role_id_text: '1', name: '低战高攻', battle_score: 100, attack_value: 9999, observed_at: '2026-08-19 12:00:00' },
    { role_id_text: '2', name: '高战无攻', battle_score: 200, observed_at: '2026-08-19 11:00:00' },
    { role_id_text: '3', name: '空战力', battle_score: null, attack_value: 99999, observed_at: '2026-08-19 13:00:00' },
  ]

  assert.deepEqual(
    [...rows].sort(comparePlayerProfileBattleDesc).map(row => row.name),
    ['高战无攻', '低战高攻', '空战力'],
  )
})

test('same player and day keeps highest battle score before freshness', () => {
  const rows = [
    { observation_id: 'late-low', role_id_text: '1', battle_score: 100, observed_at: '2026-08-19 20:00:00' },
    { observation_id: 'early-high', role_id_text: '1', battle_score: 200, observed_at: '2026-08-19 10:00:00' },
    { observation_id: 'next-day', role_id_text: '1', battle_score: 50, observed_at: '2026-08-20 01:00:00' },
  ]

  assert.deepEqual(
    selectDailyPlayerProfileRepresentatives(rows).map(row => row.observation_id),
    ['next-day', 'early-high'],
  )
})

test('Xianlv maximum power is merged independently without exposing a team slot', () => {
  const [row] = attachLatestXianlvTeamObservations(
    [{ role_id_text: '1', battle_score: 1000, observed_at: '2026-08-19 10:00:00' }],
    [{
      role_id_text: '1',
      xianlv_team_fight_score_max: 600,
      xianlv_team_observed_at: '2026-08-19 12:00:00',
    }],
  )

  assert.equal(row.battle_score, 1000)
  assert.equal(row.xianlv_team_fight_score_max, 600)
  assert.equal('xianlv_team_slot' in row, false)
  assert.equal(row.xianlv_team_observed_at, '2026-08-19 12:00:00')
})
