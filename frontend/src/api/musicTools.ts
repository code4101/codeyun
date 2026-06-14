import api from './index'

export type MusicStem = string
export type MusicSeparationEngine = 'demucs' | 'audio_separator_6s' | 'basic_pitch_humming'

export interface MusicToolInfo {
  demucs_installed: boolean
  demucs_python: string
  audio_separator_installed: boolean
  audio_separator_exe: string
  work_root: string
}

export interface MusicAudioFile {
  stem: MusicStem
  label?: string
  role?: string
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
    input_kind?: string
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

export interface MusicInstrumentRecord {
  id: string
  name: string
  aliases: string[]
  zh_names: string[]
  hornbostel_sachs: Array<{ code: string; label: string }>
  hs_top_classes: string[]
  musicbrainz: { mbid: string; comment: string; description: string } | null
  musescore: Array<{
    id: string
    group: string
    staves: string
    clefs: string[]
    channels: Array<{ name: string; program: string; bank: string }>
  }>
  general_midi: Array<{ program: number; family: string }>
  playback: {
    gm_program?: number
    gm_family?: string
  }
  notation: {
    clefs?: string[]
  }
  derived_roles: string[]
  sources: string[]
  source_count: number
}

export interface MusicInstrumentRegistry {
  version: number
  generated_at: string
  sources: Record<string, string>
  source_counts: Record<string, number>
  total: number
  instruments: MusicInstrumentRecord[]
}

export interface OpenScoreNote {
  pitch: number
  start: number
  duration: number
  velocity: number
}

export interface OpenScorePart {
  name: string
  role: string
  program: number | null
  channel: number | null
  note_count: number
  range: [string, string]
  notes: OpenScoreNote[]
}

export interface OpenScoreWorkSummary {
  id: string
  title: string
  composer: string
  level: string
  form: string
  instrumentation: string[]
  description: string
  listen_focus: string[]
  source_name: string
  license_label: string
  source_url: string
  midi_url: string
  pdf_url: string
  study_kind?: string
  part_count?: number
}

export interface OpenScoreWork extends OpenScoreWorkSummary {
  tempo_bpm: number
  duration: number
  ticks_per_beat: number
  parts: OpenScorePart[]
  cached_at: number
  midi_size: number
}

export interface MultitrackLibrarySource {
  id: string
  name: string
  kind: string
  url: string
  import_hint: string
  fit?: string
  strengths: string[]
  cautions: string[]
  featured_works?: Array<{
    title: string
    level: string
    focus: string
    instruments: string[]
    why: string
    study: string
    style_bridge: string
  }>
}

export interface MusicCreativeBrief {
  job_id: string
  title: string
  duration_seconds: number | null
  available_stems: string[]
  audio_features: Record<string, unknown>
  description_zh: string
  suno_prompt_zh: string
  suno_prompt_en: string
  prompt_variants: Array<{ name: string; prompt_zh: string; prompt_en: string }>
  style_directions: Array<{
    key: string
    name: string
    prompt_zh: string
    prompt_en: string
    palette: string[]
    use_case: string
  }>
  stem_insights: Array<{ stem: string; label: string; role: string; focus: string; usage: string }>
  arrangement_plan: Array<{ section: string; energy: string; listen: string; arrange: string }>
  suno_fields: {
    title_ideas?: string[]
    style_tags?: string[]
    mood_tags?: string[]
    structure_tags?: string[]
    negative_prompt?: string
    instrumental_hint?: string
    copy_order?: string[]
    style_count?: number
    [key: string]: unknown
  }
  style_profile?: {
    best_fit?: string
    style_scores?: Array<{ name: string; score: number }>
    why?: string[]
    analysis_tags?: string[]
    prompt_blueprint?: {
      suno_style?: string[]
      prompt_core_zh?: string
      prompt_core_en?: string
      [key: string]: unknown
    }
    workflow?: string[]
    negative?: string[]
    [key: string]: unknown
  }
  style_presets?: Array<{
    key: string
    name: string
    fit: string
    palette: string[]
    listen_check: string[]
    suno_style: string
    suno_prompt: string
    udio_prompt: string
    negative: string
    copy_order?: string[]
    [key: string]: unknown
  }>
  creative_recipes: Array<{
    key: string
    title: string
    goal: string
    hook: string
    style_tags: string[]
    instrumentation: string[]
    arrangement_moves: string[]
    listen_first: string[]
    platform_prompts: {
      suno_style?: string
      suno_prompt?: string
      udio_prompt?: string
      negative?: string
      [key: string]: unknown
    }
  }>
  tags: string[]
  cautions: string[]
}

export interface MusicCreativePromptRecord {
  id: string
  job_id: string
  name: string
  prompt_zh: string
  prompt_en: string | null
  source: string
  created_at: number
  audio_features: Record<string, unknown>
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

export const startHummingTranscription = async (file: File, tempoBpm = 96, beatsPerBar = 4) => {
  const form = new FormData()
  form.append('file', file)
  form.append('tempo_bpm', String(tempoBpm))
  form.append('beats_per_bar', String(beatsPerBar))
  const response = await api.post<MusicTaskPayload>('/music-tools/humming-transcribe', form, {
    timeout: 60_000,
    headers: {
      'Content-Type': 'multipart/form-data',
    },
  })
  return response.data
}

export const importMultitrackZip = async (file: File, sourceId = '') => {
  const form = new FormData()
  form.append('file', file)
  form.append('source_id', sourceId)
  const response = await api.post<MusicJob>('/music-tools/multitrack-import', form, {
    timeout: 60_000,
    headers: {
      'Content-Type': 'multipart/form-data',
    },
  })
  return response.data
}

export const importMultitrackZipUrl = async (url: string, sourceId = '', filename = '') => {
  const response = await api.post<MusicJob>('/music-tools/multitrack-import-url', {
    url,
    source_id: sourceId,
    filename,
  }, {
    timeout: 60_000,
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

export const updateMusicJob = async (jobId: string, payload: { filename: string }) => {
  const response = await api.patch<MusicJob>(`/music-tools/jobs/${jobId}`, payload)
  return response.data
}

export const getMusicJobScore = async (jobId: string) => {
  const response = await api.get<MusicScoreInfo>(`/music-tools/jobs/${jobId}/score`)
  return response.data
}

export const getMusicJobCreativeBrief = async (jobId: string) => {
  const response = await api.get<MusicCreativeBrief>(`/music-tools/jobs/${jobId}/creative-brief`)
  return response.data
}

export const listMusicJobCreativePrompts = async (jobId: string) => {
  const response = await api.get<{ records: MusicCreativePromptRecord[] }>(`/music-tools/jobs/${jobId}/creative-prompts`)
  return response.data
}

export const saveMusicJobCreativePrompt = async (
  jobId: string,
  payload: { name: string; prompt_zh: string; prompt_en?: string | null; source?: string; audio_features?: Record<string, unknown> },
) => {
  const response = await api.post<MusicCreativePromptRecord>(`/music-tools/jobs/${jobId}/creative-prompts`, payload)
  return response.data
}

export const listMusicJobScores = async (jobId: string) => {
  const response = await api.get<{ scores: MusicScoreInfo[] }>(`/music-tools/jobs/${jobId}/scores`)
  return response.data
}

export const getMusicInstrumentRegistry = async () => {
  const response = await api.get<MusicInstrumentRegistry>('/music-tools/instrument-registry')
  return response.data
}

export const listMultitrackLibrary = async () => {
  const response = await api.get<{ sources: MultitrackLibrarySource[] }>('/music-tools/multitrack-library')
  return response.data
}

export const listOpenScoreWorks = async () => {
  const response = await api.get<{ works: OpenScoreWorkSummary[] }>('/music-tools/open-scores')
  return response.data
}

export const getOpenScoreWork = async (workId: string) => {
  const response = await api.get<OpenScoreWork>(`/music-tools/open-scores/${workId}`, { timeout: 60_000 })
  return response.data
}
