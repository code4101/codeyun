export interface TalentPillMilestone {
  task_id: number
  order: number
  target: number
  talent_pill_count: number
}

export function projectTalentPillMilestones<T extends TalentPillMilestone>(rows: T[]) {
  let cumulativeTalentPills = 0
  return rows
    .filter(row => row.talent_pill_count > 0)
    .map(row => {
      cumulativeTalentPills += row.talent_pill_count
      return {
        ...row,
        cumulativeTalentPills,
        costPerTalentPill: row.target / cumulativeTalentPills,
      }
    })
}
