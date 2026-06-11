import api from './index'

export type MusicStem = 'original' | 'vocals' | 'other' | 'bass' | 'drums' | 'guitar' | 'piano'
export type MusicSeparationEngine = 'demucs' | 'audio_separator_6s'

export interface MusicToolInfo {
  demucs_installed: boolean
  demucs_python: string
  audio_separator_installed: boolean
  audio_separator_exe: string
  work_root: string
}

export interface MusicAudioFile {
  stem: MusicStem
  filename: string
  url: string
  size: number
  modified_at: number
}

export interface MusicTaskPayload {
  task_id: string
  kind: string
  status: 'queued' | 'running' | 'completed' | 'failed'
  running: boolean
  stage: string
  message: string
  created_at: number
  started_at: number | null
  updated_at: number
  finished_at: number | null
  progress_current: number | null
  progress_total: number | null
  metadata: {
    job_id?: string
    filename?: string
    engine?: MusicSeparationEngine
    expected_stems?: MusicStem[]
    files?: MusicAudioFile[]
  }
  result?: {
    job_id: string
    model: string
    elapsed_ms: number
    files: MusicAudioFile[]
    log_url: string
  }
  error: string | null
  error_status_code: number | null
  elapsed_ms: number
}

export interface MusicJob {
  job_id: string
  filename: string
  status: 'queued' | 'running' | 'completed' | 'failed'
  model: string
  engine?: MusicSeparationEngine
  expected_stems?: MusicStem[]
  input_kind?: 'audio' | 'video' | 'score_demo' | string
  task_id?: string
  task_message?: string
  error?: string
  created_at: number
  updated_at: number
  elapsed_ms: number | null
  files: MusicAudioFile[]
  log_url: string
}

export interface MusicScoreFile {
  key: 'pdf' | 'html' | 'musicxml' | 'midi' | 'preview_audio' | 'notes' | 'readme' | string
  filename: string
  url: string
  size: number
  modified_at: number
}

export interface MusicScoreInfo {
  id: string
  title: string
  version: string
  kind: string
  source_stem: MusicStem | string | null
  tempo_bpm: number | null
  beats_per_bar?: number | null
  measures: number | null
  files: MusicScoreFile[]
}

export const getMusicToolInfo = async () => {
  const response = await api.get<MusicToolInfo>('/music-tools/info')
  return response.data
}

export const startMusicSeparation = async (file: File, engine: MusicSeparationEngine = 'demucs') => {
  const form = new FormData()
  form.append('file', file)
  form.append('engine', engine)
  const response = await api.post<MusicTaskPayload>('/music-tools/separate', form, {
    timeout: 60_000,
    headers: {
      'Content-Type': 'multipart/form-data',
    },
  })
  return response.data
}

export const rerunMusicJob = async (jobId: string, engine: MusicSeparationEngine = 'audio_separator_6s') => {
  const form = new FormData()
  form.append('engine', engine)
  const response = await api.post<MusicTaskPayload>(`/music-tools/jobs/${jobId}/rerun`, form, {
    timeout: 60_000,
    headers: {
      'Content-Type': 'multipart/form-data',
    },
  })
  return response.data
}

export const getMusicSeparationTask = async (taskId: string) => {
  const response = await api.get<MusicTaskPayload>(`/music-tools/tasks/${taskId}`)
  return response.data
}

export const listMusicJobs = async () => {
  const response = await api.get<{ jobs: MusicJob[] }>('/music-tools/jobs')
  return response.data
}

export const getMusicJob = async (jobId: string) => {
  const response = await api.get<MusicJob>(`/music-tools/jobs/${jobId}`)
  return response.data
}

export const getMusicJobScore = async (jobId: string) => {
  const response = await api.get<MusicScoreInfo>(`/music-tools/jobs/${jobId}/score`)
  return response.data
}

export const listMusicJobScores = async (jobId: string) => {
  const response = await api.get<{ scores: MusicScoreInfo[] }>(`/music-tools/jobs/${jobId}/scores`)
  return response.data
}
