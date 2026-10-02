import api from '@/api'

export interface PreparationSource {
  id: string; kind: string; title: string; text: string; context: string[]
  url: string; hash: string; provenance: string; revision: number
}
export interface PreparationSnapshot {
  id: string; created_at: number; sources: PreparationSource[]
  coverage: { graphs: number; note_scanned: number; automatic_excluded: number; note_scan_complete: boolean; errors: Array<{ source: string; error: string }> }
}
export interface PreparationPacket {
  id: string; title: string; intent: string; confidence: 'explicit' | 'inferred' | 'uncertain'
  evidence_ids: string[]; snapshot_id: string; research: string; questions: string[]
  dependencies: string[]; references: string[]; decision: string; human_note: string
  created_at: number; stale: boolean; session_id: string; model: string
  history: Array<{ created_at: number; research: string; snapshot_id: string; evidence_ids: string[] }>
}
export interface PreparationOverview {
  snapshot: PreparationSnapshot | null; packets: PreparationPacket[]
  runs: Array<{ phase: string; status: string; error?: string; recorded_at: number; packets?: number }>
}
export const loadPreparation = async (): Promise<PreparationOverview> => (await api.get('/notes-preparation')).data
export const loadSnapshot = async (id: string, evidenceIds: string[]): Promise<PreparationSnapshot> => {
  const params = new URLSearchParams()
  evidenceIds.forEach(value => params.append('evidence_ids', value))
  return (await api.get(`/notes-preparation/snapshots/${id}`, { params })).data
}
export const reviewPreparation = async (id: string, decision: string, human_note: string) => (await api.patch(`/notes-preparation/packets/${id}`, { decision, human_note })).data
export const exportPreparation = async () => (await api.get('/notes-preparation/export', { responseType: 'blob', timeout: 120000 })).data as Blob
