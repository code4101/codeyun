<template>
  <div class="music-tools-page">
    <section class="page-head">
      <div>
        <div class="eyebrow">综合工具</div>
        <h1>音乐工具</h1>
      </div>
      <el-tag :type="toolInfo?.demucs_installed ? 'success' : 'warning'" effect="plain">
        {{ toolStatusText }}
      </el-tag>
    </section>

    <section class="upload-row">
      <label class="file-picker">
        <input type="file" accept="audio/*,video/mp4,video/x-m4v,video/quicktime,video/webm,video/x-matroska,video/x-msvideo" @change="handleFileChange" />
        <span>{{ selectedFile ? selectedFile.name : '选择音频或视频文件' }}</span>
      </label>
      <el-select v-model="selectedEngine" class="engine-select" :disabled="task?.running" title="分离模式">
        <el-option label="四轨分离" value="demucs" />
        <el-option label="六轨细分" value="audio_separator_6s" :disabled="!toolInfo?.audio_separator_installed" />
      </el-select>
      <el-button type="primary" :disabled="!selectedFile || task?.running" :loading="task?.running" @click="startSeparation">
        分离音轨
      </el-button>
      <el-button plain :disabled="!selectedJobId" @click="resetActiveWorkspace">
        重置工作区
      </el-button>
    </section>

    <el-alert
      v-if="task"
      class="task-alert"
      :type="task.status === 'failed' ? 'error' : task.status === 'completed' ? 'success' : 'info'"
      :closable="false"
      :title="task.status === 'failed' ? task.error || task.message : task.message"
    />

    <section class="workspace-layout">
      <aside class="history-pane">
        <div class="pane-title">解析历史</div>
        <div v-if="!jobs.length" class="history-empty">暂无历史</div>
        <button
          v-for="job in jobs"
          :key="job.job_id"
          class="history-item"
          :class="{ active: job.job_id === selectedJobId }"
          type="button"
          :title="job.error || job.task_message || job.filename"
          @click="selectJob(job)"
        >
          <span class="history-name">{{ job.filename }}</span>
          <span class="history-meta">
            {{ statusText(job.status) }} · {{ formatDate(job.updated_at || job.created_at) }}
          </span>
        </button>
      </aside>

      <section v-if="audioFiles.length" class="workspace">
        <div class="active-title">
          <div class="active-name">{{ activeJob?.filename || '当前音频' }}</div>
          <div class="active-meta">{{ activeJob?.model || 'htdemucs' }}</div>
          <el-button
            v-if="canRerunActiveJob"
            size="small"
            :disabled="task?.running"
            :loading="task?.running && taskJobId === selectedJobId"
            @click="rerunActiveJob"
          >
            {{ rerunButtonText }}
          </el-button>
        </div>

        <div class="transport">
          <el-button circle :title="isPlaying ? '暂停' : '播放'" @click="togglePlayback">
            <el-icon><VideoPause v-if="isPlaying" /><VideoPlay v-else /></el-icon>
          </el-button>
          <span class="time-text">{{ formatTime(currentTime) }}</span>
          <el-slider
            v-model="currentTime"
            class="timeline"
            :min="0"
            :max="duration || 1"
            :step="0.05"
            :show-tooltip="false"
            @change="seekTo"
          />
          <span class="time-text">{{ formatTime(duration) }}</span>
        </div>

        <div class="stem-table">
          <div v-for="track in visibleTracks" :key="track.key" class="stem-row" :class="{ 'original-stem': track.key === 'original' }">
            <div class="stem-control-row">
              <el-switch
                v-model="track.enabled"
                class="stem-switch"
                @change="handleTrackToggle(track.key)"
              />
              <div class="stem-heading">
                <span class="stem-title">{{ track.label }}</span>
                <span class="stem-file">{{ getTrackFilename(track.key) || '未生成' }}</span>
              </div>
              <el-slider
                v-model="track.volume"
                class="volume-slider"
                :min="0"
                :max="1"
                :step="0.01"
                :show-tooltip="false"
                @input="applyTrackVolume(track.key)"
              />
            </div>
            <div class="stem-wave-row">
              <button
                class="waveform"
                type="button"
                :disabled="!getTrackFilename(track.key)"
                @click="handleWaveformClick($event, track.key)"
              >
                <span
                  v-for="(peak, index) in getWaveformPeaks(track.key)"
                  :key="`${track.key}:${index}`"
                  class="waveform-bar"
                  :style="{ height: `${Math.max(2, Math.round(peak * 28))}px` }"
                />
                <span
                  v-if="duration > 0"
                  class="waveform-playhead"
                  :style="{ left: `${Math.min(100, Math.max(0, (currentTime / duration) * 100))}%` }"
                />
              </button>
            </div>
          </div>
        </div>

        <section v-if="scoreInfos.length || selectedScoreKind === 'piano_stem_transcription'" class="score-panel">
          <div class="score-toolbar">
            <div class="score-heading">
              <span class="score-title">{{ scorePanelTitle }}</span>
              <span class="score-meta">{{ scorePanelMeta }}</span>
            </div>
            <el-radio-group v-model="selectedScoreKind" size="small" @change="handleScoreKindChange">
              <el-radio-button
                v-for="mode in scoreModes"
                :key="mode.kind"
                :label="mode.kind"
              >
                {{ mode.label }}
              </el-radio-button>
            </el-radio-group>
            <div class="score-actions">
              <el-button size="small" :disabled="!scoreNotes.length" :title="isScorePlaying ? '暂停谱面演奏' : '演奏谱面'" @click="toggleScorePlayback">
                <el-icon><VideoPause v-if="isScorePlaying" /><VideoPlay v-else /></el-icon>
                {{ isScorePlaying ? '暂停' : '演奏' }}
              </el-button>
              <a v-if="scoreFileUrl('musicxml')" class="score-link" :href="scoreFileUrl('musicxml')" target="_blank">MusicXML</a>
              <a v-if="scoreFileUrl('midi')" class="score-link" :href="scoreFileUrl('midi')" target="_blank">MIDI</a>
            </div>
          </div>
          <div v-if="scoreNotes.length" class="score-transport">
            <span class="time-text">{{ formatTime(scoreCurrentTime) }}</span>
            <el-slider
              v-model="scoreCurrentTime"
              class="timeline"
              :min="0"
              :max="scoreDuration || 1"
              :step="0.05"
              :show-tooltip="false"
              @change="seekScoreTo"
            />
            <span class="time-text">{{ formatTime(scoreDuration) }}</span>
          </div>
          <div v-if="scoreNotes.length" class="piano-roll" :style="{ '--visible-keys': pianoKeyCount }">
            <div class="roll-lane">
              <span
                v-for="note in visibleScoreNotes"
                :key="note.id"
                class="roll-note"
                :class="[note.hand, { active: note.active }]"
                :style="{
                  left: `${note.left}%`,
                  width: `${note.width}%`,
                  top: `${note.top}px`,
                  height: `${note.height}px`,
                }"
              />
            </div>
            <div class="piano-keyboard">
              <span
                v-for="key in pianoKeys"
                :key="key.note"
                class="piano-key"
                :class="{ black: key.black, active: activeScoreNotes.has(key.note) }"
                :title="key.name"
              />
            </div>
          </div>
          <div v-else class="score-empty">
            {{ selectedScoreKind === 'piano_stem_transcription' ? pianoStemScoreEmptyText : '这个解析结果还没有可演奏谱面。' }}
          </div>
        </section>

        <section v-else-if="scoreLoading" class="score-panel score-loading">
          正在加载钢琴独奏谱
        </section>

        <audio
          v-for="file in audioFiles"
          :key="`${selectedJobId}:${file.stem}`"
          :ref="(el) => setAudioRef(file.stem, el)"
          :src="file.url"
          preload="metadata"
          @loadedmetadata="handleMetadata(file.stem)"
          @timeupdate="handleTimeUpdate(file.stem)"
          @ended="handleEnded(file.stem)"
        />
      </section>

      <section v-else class="empty-state">
        <div class="empty-title">上传音频或视频后开始分轨</div>
        <div class="empty-text">解析完成后会保存到历史，刷新页面也可以继续试听。</div>
      </section>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { VideoPause, VideoPlay } from '@element-plus/icons-vue'
import {
  getMusicSeparationTask,
  getMusicToolInfo,
  listMusicJobScores,
  listMusicJobs,
  rerunMusicJob,
  startMusicSeparation,
  type MusicSeparationEngine,
  type MusicJob,
  type MusicScoreInfo,
  type MusicStem,
  type MusicTaskPayload,
  type MusicToolInfo,
} from '@/api/musicTools'

interface StemTrack {
  key: MusicStem
  label: string
  enabled: boolean
  volume: number
}

interface StoredTrackPreference {
  enabled?: boolean
  volume?: number
}

interface StoredJobWorkspace {
  currentTime?: number
  scoreKind?: ScoreKind
  separatedEnabledSnapshot?: Partial<Record<MusicStem, boolean>>
  tracks?: Partial<Record<MusicStem, StoredTrackPreference>>
}

interface StoredWorkspace {
  selectedJobId?: string
  selectedEngine?: MusicSeparationEngine
  jobs?: Record<string, StoredJobWorkspace>
}

interface ScoreNote {
  id: string
  start: number
  end: number
  note: number
  velocity: number
  hand: string
}

interface PianoKey {
  note: number
  name: string
  black: boolean
}

type ScoreKind = 'piano_solo_score' | 'piano_stem_transcription'

const STEM_LABELS: Record<MusicStem, string> = {
  original: '原曲',
  vocals: '人声',
  other: '伴奏/其他',
  bass: '贝斯',
  drums: '鼓',
  guitar: '吉他',
  piano: '钢琴',
}

const WORKSPACE_STORAGE_KEY = 'codeyun.music-tools.workspace.v1'
const SEPARATED_STEMS: MusicStem[] = ['vocals', 'other', 'bass', 'drums', 'guitar', 'piano']
const DEFAULT_EXPECTED_STEMS: MusicStem[] = ['vocals', 'other', 'bass', 'drums']
const DEFAULT_TRACKS: StemTrack[] = [
  { key: 'original', label: STEM_LABELS.original, enabled: true, volume: 0.85 },
  { key: 'vocals', label: STEM_LABELS.vocals, enabled: false, volume: 0.9 },
  { key: 'other', label: STEM_LABELS.other, enabled: false, volume: 0.9 },
  { key: 'bass', label: STEM_LABELS.bass, enabled: false, volume: 0.9 },
  { key: 'drums', label: STEM_LABELS.drums, enabled: false, volume: 0.9 },
  { key: 'guitar', label: STEM_LABELS.guitar, enabled: false, volume: 0.9 },
  { key: 'piano', label: STEM_LABELS.piano, enabled: false, volume: 0.9 },
]
const SCORE_KIND_LABELS: Record<ScoreKind, string> = {
  piano_solo_score: '整曲独奏谱',
  piano_stem_transcription: '钢琴轨扒谱',
}

const toolInfo = ref<MusicToolInfo | null>(null)
const selectedFile = ref<File | null>(null)
const selectedEngine = ref<MusicSeparationEngine>('demucs')
const task = ref<MusicTaskPayload | null>(null)
const jobs = ref<MusicJob[]>([])
const selectedJobId = ref('')
const pollTimer = ref<number | null>(null)
const audioRefs = new Map<MusicStem, HTMLAudioElement>()
const waveformCache = reactive<Record<string, number[]>>({})
const waveformLoading = reactive<Record<string, boolean>>({})
const isPlaying = ref(false)
const currentTime = ref(0)
const duration = ref(0)
const scoreInfos = ref<MusicScoreInfo[]>([])
const scoreInfo = ref<MusicScoreInfo | null>(null)
const scoreNotes = ref<ScoreNote[]>([])
const scoreLoading = ref(false)
const selectedScoreKind = ref<ScoreKind>('piano_solo_score')
const isScorePlaying = ref(false)
const scoreCurrentTime = ref(0)
const workspacePrefs = ref<StoredWorkspace>({})
const persistTimer = ref<number | null>(null)
const isRestoringWorkspace = ref(false)
const scoreAudioContext = ref<AudioContext | null>(null)
const scoreAnimationFrame = ref<number | null>(null)
const scorePlaybackStartedAt = ref(0)
const scorePlaybackBaseTime = ref(0)
const scheduledScoreNotes = new Set<string>()

const tracks = reactive<StemTrack[]>(DEFAULT_TRACKS.map((track) => ({ ...track })))

const activeJob = computed(() => jobs.value.find((job) => job.job_id === selectedJobId.value) || null)
const toolStatusText = computed(() => {
  if (!toolInfo.value?.demucs_installed) {
    return '分轨工具未安装'
  }
  return toolInfo.value.audio_separator_installed ? '四轨/六轨已安装' : '四轨已安装'
})
const taskJobId = computed(() => task.value?.metadata.job_id || task.value?.result?.job_id || '')
const taskFiles = computed(() => task.value?.result?.files || task.value?.metadata.files || [])
const audioFiles = computed(() => {
  if (task.value && taskJobId.value === selectedJobId.value && taskFiles.value.length) {
    return taskFiles.value
  }
  return activeJob.value?.files || []
})
const expectedStems = computed(() => {
  if (task.value && taskJobId.value === selectedJobId.value && task.value.metadata.expected_stems?.length) {
    return task.value.metadata.expected_stems
  }
  if (Array.isArray(activeJob.value?.expected_stems)) {
    return activeJob.value.expected_stems
  }
  return DEFAULT_EXPECTED_STEMS
})
const activeJobEngine = computed<MusicSeparationEngine>(() => {
  if (activeJob.value?.engine) {
    return activeJob.value.engine
  }
  return activeJob.value?.expected_stems?.includes('guitar') || activeJob.value?.expected_stems?.includes('piano')
    ? 'audio_separator_6s'
    : 'demucs'
})
const canRerunActiveJob = computed(() => {
  if (!activeJob.value || activeJob.value.status === 'queued' || activeJob.value.status === 'running') {
    return false
  }
  if (activeJob.value.input_kind === 'score_demo') {
    return false
  }
  if (selectedEngine.value === 'audio_separator_6s' && !toolInfo.value?.audio_separator_installed) {
    return false
  }
  return activeJobEngine.value !== selectedEngine.value
})
const rerunButtonText = computed(() => (selectedEngine.value === 'audio_separator_6s' ? '改为六轨' : '改为四轨'))
const visibleTracks = computed(() => {
  const visible = new Set<MusicStem>(['original', ...expectedStems.value])
  for (const file of audioFiles.value) {
    visible.add(file.stem)
  }
  return tracks.filter((track) => visible.has(track.key))
})
const hasPianoTrack = computed(() => audioFiles.value.some((file) => file.stem === 'piano'))
const scoreModes = computed(() =>
  (Object.keys(SCORE_KIND_LABELS) as ScoreKind[]).map((kind) => ({
    kind,
    label: SCORE_KIND_LABELS[kind],
    available: scoreInfos.value.some((score) => score.kind === kind),
  })),
)
const scorePanelTitle = computed(() => {
  if (scoreInfo.value) return scoreInfo.value.title
  return selectedScoreKind.value === 'piano_stem_transcription' ? '钢琴轨扒谱' : '整曲独奏谱'
})
const scorePanelMeta = computed(() => {
  if (scoreInfo.value) {
    const parts = [
      scoreInfo.value.version,
      scoreInfo.value.beats_per_bar ? `${scoreInfo.value.beats_per_bar}拍` : '',
      scoreInfo.value.measures ? `${scoreInfo.value.measures} 小节` : '',
    ].filter(Boolean)
    return parts.join(' · ') || SCORE_KIND_LABELS[selectedScoreKind.value]
  }
  return selectedScoreKind.value === 'piano_stem_transcription'
    ? (hasPianoTrack.value ? '已切到 piano.mp3，等待生成 MIDI' : '当前结果没有钢琴音轨')
    : '未生成'
})
const pianoStemScoreEmptyText = computed(() =>
  hasPianoTrack.value
    ? '当前还没有钢琴轨 MIDI。已切到只听 piano.mp3，可以先判断分离出的钢琴是否干净。'
    : '当前分轨结果没有 piano.mp3，需要先用六轨细分重新分离。',
)
const scoreDuration = computed(() => Math.max(0, ...scoreNotes.value.map((note) => note.end)))
const scoreLookAheadSeconds = 5
const pianoMinNote = 28
const pianoMaxNote = 84
const pianoKeyCount = pianoMaxNote - pianoMinNote + 1
const pianoKeys = computed<PianoKey[]>(() => {
  const blackPitchClasses = new Set([1, 3, 6, 8, 10])
  const names = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
  return Array.from({ length: pianoKeyCount }, (_, index) => {
    const note = pianoMinNote + index
    return {
      note,
      name: `${names[note % 12]}${Math.floor(note / 12) - 1}`,
      black: blackPitchClasses.has(note % 12),
    }
  })
})
const activeScoreNotes = computed(() => {
  const now = scoreCurrentTime.value
  return new Set(scoreNotes.value.filter((note) => note.start <= now && note.end >= now).map((note) => note.note))
})
const visibleScoreNotes = computed(() => {
  const now = scoreCurrentTime.value
  const laneHeight = 206
  const maxNoteHeight = 70
  return scoreNotes.value
    .filter((note) => note.note >= pianoMinNote && note.note <= pianoMaxNote)
    .filter((note) => note.end >= now - 0.2 && note.start <= now + scoreLookAheadSeconds)
    .map((note) => {
      const lane = note.note - pianoMinNote
      const left = (lane / pianoKeyCount) * 100
      const width = Math.max(0.8, 100 / pianoKeyCount)
      const startOffset = note.start - now
      const rawBottom = ((scoreLookAheadSeconds - startOffset) / scoreLookAheadSeconds) * laneHeight
      const rawHeight = Math.min(maxNoteHeight, ((note.end - note.start) / scoreLookAheadSeconds) * laneHeight)
      const rawTop = rawBottom - rawHeight
      const top = Math.max(0, rawTop)
      const bottom = Math.min(laneHeight, rawBottom)
      const height = Math.max(0, bottom - top)
      return {
        ...note,
        left,
        width,
        top,
        height,
        active: note.start <= now && note.end >= now,
      }
    })
    .filter((note) => note.height > 0)
})

const getTrack = (stem: MusicStem) => tracks.find((track) => track.key === stem)

const getSeparatedTracks = () =>
  SEPARATED_STEMS.map((stem) => getTrack(stem)).filter((track): track is StemTrack => Boolean(track))

const defaultSeparatedEnabledSnapshot = () =>
  Object.fromEntries(SEPARATED_STEMS.map((stem) => [stem, true])) as Record<MusicStem, boolean>

const currentSeparatedEnabledSnapshot = () =>
  Object.fromEntries(
    SEPARATED_STEMS.map((stem) => [stem, Boolean(getTrack(stem)?.enabled)]),
  ) as Record<MusicStem, boolean>

const getCurrentJobPrefs = () => {
  if (!selectedJobId.value) return null
  return workspacePrefs.value.jobs?.[selectedJobId.value] || null
}

const getSeparatedEnabledSnapshot = () => {
  const snapshot = getCurrentJobPrefs()?.separatedEnabledSnapshot
  if (!snapshot) return defaultSeparatedEnabledSnapshot()
  return Object.fromEntries(
    SEPARATED_STEMS.map((stem) => [stem, snapshot[stem] ?? true]),
  ) as Record<MusicStem, boolean>
}

const updateSeparatedEnabledSnapshot = (snapshot = currentSeparatedEnabledSnapshot()) => {
  if (!selectedJobId.value) return
  const nextJobs = { ...(workspacePrefs.value.jobs || {}) }
  nextJobs[selectedJobId.value] = {
    ...(nextJobs[selectedJobId.value] || {}),
    separatedEnabledSnapshot: snapshot,
  }
  workspacePrefs.value = {
    ...workspacePrefs.value,
    jobs: nextJobs,
  }
}

const getPlaybackClockStem = () => {
  if (getTrack('original')?.enabled && audioRefs.has('original')) {
    return 'original'
  }
  return SEPARATED_STEMS.find((stem) => getTrack(stem)?.enabled && audioRefs.has(stem)) || 'original'
}

const getTrackFilename = (stem: MusicStem) =>
  audioFiles.value.find((file) => file.stem === stem)?.filename || ''

const getTrackFile = (stem: MusicStem) => audioFiles.value.find((file) => file.stem === stem)

const getWaveformPeaks = (stem: MusicStem) => {
  const file = getTrackFile(stem)
  if (!file) return []
  return waveformCache[file.url] || (waveformLoading[file.url] ? Array.from({ length: 96 }, () => 0.08) : [])
}

const setAudioTime = (audio: HTMLAudioElement, time: number) => {
  try {
    audio.currentTime = Math.max(0, time)
  } catch (error) {
    console.warn('Failed to sync audio time', error)
  }
}

const setAudioRef = (stem: MusicStem, el: Element | null) => {
  if (el == null) {
    audioRefs.delete(stem)
    return
  }
  if (el instanceof HTMLAudioElement) {
    const existing = audioRefs.get(stem)
    if (existing === el) return
    audioRefs.set(stem, el)
    applyTrackVolume(stem)
    if (currentTime.value > 0) {
      setAudioTime(el, currentTime.value)
    }
  }
}

const loadStoredWorkspace = () => {
  try {
    const raw = window.localStorage.getItem(WORKSPACE_STORAGE_KEY)
    if (!raw) return {}
    const payload = JSON.parse(raw)
    if (!payload || typeof payload !== 'object') return {}
    return payload as StoredWorkspace
  } catch (error) {
    console.warn('Failed to load music workspace preferences', error)
    return {}
  }
}

const serializeCurrentJobWorkspace = (): StoredJobWorkspace => ({
  currentTime: Number.isFinite(currentTime.value) ? currentTime.value : 0,
  scoreKind: selectedScoreKind.value,
  separatedEnabledSnapshot:
    getCurrentJobPrefs()?.separatedEnabledSnapshot || currentSeparatedEnabledSnapshot(),
  tracks: Object.fromEntries(
    tracks.map((track) => [
      track.key,
      {
        enabled: track.enabled,
        volume: track.volume,
      },
    ]),
  ) as Record<MusicStem, StoredTrackPreference>,
})

const persistWorkspaceNow = () => {
  if (isRestoringWorkspace.value || !selectedJobId.value) return
  const nextPrefs: StoredWorkspace = {
    selectedJobId: selectedJobId.value,
    selectedEngine: selectedEngine.value,
    jobs: {
      ...(workspacePrefs.value.jobs || {}),
      [selectedJobId.value]: serializeCurrentJobWorkspace(),
    },
  }
  workspacePrefs.value = nextPrefs
  window.localStorage.setItem(WORKSPACE_STORAGE_KEY, JSON.stringify(nextPrefs))
}

const schedulePersistWorkspace = () => {
  if (isRestoringWorkspace.value || !selectedJobId.value) return
  if (persistTimer.value != null) {
    window.clearTimeout(persistTimer.value)
  }
  persistTimer.value = window.setTimeout(() => {
    persistTimer.value = null
    persistWorkspaceNow()
  }, 250)
}

const applyStoredJobWorkspace = (jobId: string) => {
  const jobPrefs = workspacePrefs.value.jobs?.[jobId]
  for (const defaultTrack of DEFAULT_TRACKS) {
    const track = getTrack(defaultTrack.key)
    if (!track) continue
    const storedTrack = jobPrefs?.tracks?.[defaultTrack.key]
    track.enabled = typeof storedTrack?.enabled === 'boolean' ? storedTrack.enabled : defaultTrack.enabled
    track.volume = typeof storedTrack?.volume === 'number' ? storedTrack.volume : defaultTrack.volume
  }
  if (getTrack('original')?.enabled) {
    for (const separatedTrack of getSeparatedTracks()) {
      separatedTrack.enabled = false
    }
  } else {
    updateSeparatedEnabledSnapshot(currentSeparatedEnabledSnapshot())
  }
  if (jobPrefs?.scoreKind === 'piano_solo_score' || jobPrefs?.scoreKind === 'piano_stem_transcription') {
    selectedScoreKind.value = jobPrefs.scoreKind
  } else {
    selectedScoreKind.value = 'piano_solo_score'
  }
  currentTime.value = Math.max(0, jobPrefs?.currentTime || 0)
}

const resetTracksToDefaults = () => {
  for (const defaultTrack of DEFAULT_TRACKS) {
    const track = getTrack(defaultTrack.key)
    if (!track) continue
    track.enabled = defaultTrack.enabled
    track.volume = defaultTrack.volume
  }
}

const preferBestScoreKind = () => {
  selectedScoreKind.value = scoreInfos.value.some((score) => score.kind === 'piano_stem_transcription')
    ? 'piano_stem_transcription'
    : 'piano_solo_score'
}

const resetPlayback = (options: { resetTime?: boolean } = {}) => {
  pauseAll()
  if (options.resetTime !== false) {
    currentTime.value = 0
  }
  duration.value = 0
  audioRefs.clear()
}

const selectJob = async (job: MusicJob) => {
  persistWorkspaceNow()
  stopScorePlayback()
  scoreCurrentTime.value = 0
  isRestoringWorkspace.value = true
  selectedJobId.value = job.job_id
  resetPlayback({ resetTime: false })
  applyStoredJobWorkspace(job.job_id)
  await loadScore(job.job_id)
  await nextTick()
  for (const track of tracks) {
    applyTrackVolume(track.key)
  }
  for (const audio of audioRefs.values()) {
    if (currentTime.value > 0) {
      setAudioTime(audio, currentTime.value)
    }
  }
  isRestoringWorkspace.value = false
  persistWorkspaceNow()
}

const resetActiveWorkspace = async () => {
  const job = activeJob.value
  if (!job) return
  if (persistTimer.value != null) {
    window.clearTimeout(persistTimer.value)
    persistTimer.value = null
  }
  pauseAll()
  stopScorePlayback()
  isRestoringWorkspace.value = true
  const nextJobs = { ...(workspacePrefs.value.jobs || {}) }
  delete nextJobs[job.job_id]
  workspacePrefs.value = {
    ...workspacePrefs.value,
    selectedJobId: job.job_id,
    selectedEngine: selectedEngine.value,
    jobs: nextJobs,
  }
  currentTime.value = 0
  scoreCurrentTime.value = 0
  resetTracksToDefaults()
  preferBestScoreKind()
  await loadScore(job.job_id)
  preferBestScoreKind()
  if (selectedScoreKind.value === 'piano_stem_transcription') {
    for (const track of tracks) {
      track.enabled = track.key === 'piano' && Boolean(getTrackFile('piano'))
    }
    await loadSelectedScoreNotes()
  }
  await nextTick()
  for (const audio of audioRefs.values()) {
    setAudioTime(audio, 0)
  }
  applyAllTrackVolumes()
  isRestoringWorkspace.value = false
  persistWorkspaceNow()
  ElMessage.success('工作区已重置')
}

const scoreFile = (key: string) => scoreInfo.value?.files.find((file) => file.key === key) || null

const scoreFileUrl = (key: string) => scoreFile(key)?.url || ''

const setOnlyPianoTrackEnabled = () => {
  const pianoTrack = getTrack('piano')
  if (!pianoTrack || !getTrackFile('piano')) return
  pauseAll()
  stopScorePlayback()
  for (const track of tracks) {
    track.enabled = track.key === 'piano'
  }
  updateSeparatedEnabledSnapshot(currentSeparatedEnabledSnapshot())
  applyAllTrackVolumes()
  schedulePersistWorkspace()
}

const loadSelectedScoreNotes = async () => {
  stopScorePlayback()
  scoreCurrentTime.value = 0
  scoreNotes.value = []
  scoreInfo.value = scoreInfos.value.find((score) => score.kind === selectedScoreKind.value) || null
  if (!scoreInfo.value) return
  const notesUrl = scoreInfo.value.files.find((file) => file.key === 'notes')?.url
  if (!notesUrl) return
  const response = await fetch(notesUrl)
  if (!response.ok) {
    throw new Error(`Score notes request failed: ${response.status}`)
  }
  const payload = await response.json()
  const tempo = Number(payload?.tempo_bpm || scoreInfo.value.tempo_bpm || 80)
  const rawNotes = Array.isArray(payload?.notes) ? payload.notes : []
  scoreNotes.value = rawNotes
    .map((raw: any, index: number): ScoreNote | null => {
      const note = Number(raw?.note)
      const beat = Number(raw?.beat)
      const durationBeat = Number(raw?.dur)
      if (!Number.isFinite(note) || !Number.isFinite(beat) || !Number.isFinite(durationBeat)) {
        return null
      }
      const start = (beat * 60) / tempo
      const end = ((beat + Math.max(0.05, durationBeat)) * 60) / tempo
      return {
        id: `${index}:${note}:${beat}`,
        start,
        end,
        note,
        velocity: Number(raw?.velocity || 72),
        hand: String(raw?.hand || ''),
      }
    })
    .filter((note): note is ScoreNote => Boolean(note))
    .sort((a, b) => a.start - b.start)
}

const loadScore = async (jobId: string) => {
  scoreInfos.value = []
  scoreInfo.value = null
  scoreNotes.value = []
  if (!jobId) return
  scoreLoading.value = true
  try {
    const payload = await listMusicJobScores(jobId)
    scoreInfos.value = payload.scores
    if (
      selectedScoreKind.value === 'piano_solo_score' &&
      !scoreInfos.value.some((score) => score.kind === selectedScoreKind.value) &&
      scoreInfos.value.some((score) => score.kind === 'piano_stem_transcription')
    ) {
      selectedScoreKind.value = scoreInfos.value.some((score) => score.kind === 'piano_solo_score')
        ? 'piano_solo_score'
        : 'piano_stem_transcription'
    }
    await loadSelectedScoreNotes()
  } catch (error: any) {
    if (error?.response?.status !== 404) {
      console.error(error)
    }
  } finally {
    scoreLoading.value = false
  }
}

const handleScoreKindChange = async () => {
  if (selectedScoreKind.value === 'piano_stem_transcription') {
    setOnlyPianoTrackEnabled()
  }
  scoreLoading.value = true
  try {
    await loadSelectedScoreNotes()
  } catch (error) {
    console.error(error)
    ElMessage.error('加载谱面失败')
  } finally {
    scoreLoading.value = false
    schedulePersistWorkspace()
  }
}

const loadJobs = async (preferredJobId = '') => {
  const payload = await listMusicJobs()
  jobs.value = payload.jobs
  const storedJobId = workspacePrefs.value.selectedJobId || ''
  const nextJob =
    jobs.value.find((job) => job.job_id === preferredJobId) ||
    jobs.value.find((job) => job.job_id === storedJobId) ||
    jobs.value[0]
  if (nextJob) {
    await selectJob(nextJob)
    await resumeJobPolling(nextJob)
  }
}

const resumeJobPolling = async (job: MusicJob) => {
  if (!job.task_id || (job.status !== 'queued' && job.status !== 'running')) return
  if (task.value?.task_id === job.task_id && task.value.running) return
  try {
    task.value = await getMusicSeparationTask(job.task_id)
    if (task.value.running) {
      startPolling()
      return
    }
    await loadJobs(job.job_id)
  } catch (error) {
    console.error(error)
  }
}

const handleFileChange = (event: Event) => {
  const input = event.target as HTMLInputElement
  selectedFile.value = input.files?.[0] || null
}

const startSeparation = async () => {
  if (!selectedFile.value) return
  stopPolling()
  persistWorkspaceNow()
  resetPlayback()
  try {
    task.value = await startMusicSeparation(selectedFile.value, selectedEngine.value)
    if (taskJobId.value) {
      selectedJobId.value = taskJobId.value
    }
    await loadJobs(taskJobId.value)
    startPolling()
  } catch (error) {
    console.error(error)
    ElMessage.error('启动分轨失败')
  }
}

const rerunActiveJob = async () => {
  if (!activeJob.value || task.value?.running) return
  stopPolling()
  persistWorkspaceNow()
  resetPlayback()
  try {
    task.value = await rerunMusicJob(activeJob.value.job_id, selectedEngine.value)
    if (taskJobId.value) {
      selectedJobId.value = taskJobId.value
    }
    await loadJobs(taskJobId.value)
    startPolling()
  } catch (error) {
    console.error(error)
    ElMessage.error('启动重新分轨失败')
  }
}

const startPolling = () => {
  if (!task.value) return
  stopPolling()
  pollTimer.value = window.setInterval(async () => {
    if (!task.value) return
    try {
      task.value = await getMusicSeparationTask(task.value.task_id)
      if (!task.value.running) {
        stopPolling()
        await loadJobs(taskJobId.value)
        if (task.value.status === 'completed') {
          ElMessage.success('音轨分离完成')
        }
      }
    } catch (error) {
      console.error(error)
      stopPolling()
      ElMessage.error('查询分轨状态失败')
    }
  }, 1500)
}

const stopPolling = () => {
  if (pollTimer.value != null) {
    window.clearInterval(pollTimer.value)
    pollTimer.value = null
  }
}

const applyTrackVolume = (stem: MusicStem) => {
  const audio = audioRefs.get(stem)
  const track = getTrack(stem)
  if (!audio || !track) return
  audio.volume = track.volume
  audio.muted = !track.enabled || track.volume <= 0
  schedulePersistWorkspace()
}

const applyAllTrackVolumes = () => {
  for (const track of tracks) {
    applyTrackVolume(track.key)
  }
}

const enabledAudios = () =>
  tracks
    .filter((track) => track.enabled)
    .map((track) => audioRefs.get(track.key))
    .filter((audio): audio is HTMLAudioElement => Boolean(audio))

const togglePlayback = async () => {
  if (isPlaying.value) {
    pauseAll()
    return
  }
  await playAll()
}

const playAll = async () => {
  const audios = enabledAudios()
  if (!audios.length) return
  stopScorePlayback()
  for (const audio of audios) {
    setAudioTime(audio, currentTime.value)
  }
  try {
    await Promise.all(audios.map((audio) => audio.play()))
    isPlaying.value = true
  } catch (error) {
    console.error(error)
    ElMessage.error('播放失败，浏览器可能拦截了音频播放')
  }
}

const pauseAll = () => {
  for (const audio of audioRefs.values()) {
    audio.pause()
  }
  isPlaying.value = false
}

const getScoreAudioContext = () => {
  if (scoreAudioContext.value) return scoreAudioContext.value
  const AudioContextCtor = window.AudioContext || (window as any).webkitAudioContext
  scoreAudioContext.value = new AudioContextCtor()
  return scoreAudioContext.value
}

const scoreNoteFrequency = (note: number) => 440 * 2 ** ((note - 69) / 12)

const playScoreNote = (note: ScoreNote, when: number) => {
  const context = getScoreAudioContext()
  const oscillator = context.createOscillator()
  const gain = context.createGain()
  const durationSeconds = Math.max(0.08, note.end - note.start)
  const velocity = Math.min(1, Math.max(0.12, note.velocity / 127))

  oscillator.type = 'triangle'
  oscillator.frequency.setValueAtTime(scoreNoteFrequency(note.note), when)
  gain.gain.setValueAtTime(0.0001, when)
  gain.gain.exponentialRampToValueAtTime(0.18 * velocity, when + 0.018)
  gain.gain.exponentialRampToValueAtTime(0.06 * velocity, when + Math.min(durationSeconds * 0.55, 0.8))
  gain.gain.exponentialRampToValueAtTime(0.0001, when + durationSeconds + 0.18)
  oscillator.connect(gain)
  gain.connect(context.destination)
  oscillator.start(when)
  oscillator.stop(when + durationSeconds + 0.22)
}

const scheduleScoreNotes = () => {
  if (!isScorePlaying.value) return
  const context = getScoreAudioContext()
  const now = scoreCurrentTime.value
  const scheduleUntil = now + 0.35
  for (const note of scoreNotes.value) {
    if (note.start < now - 0.02) continue
    if (note.start > scheduleUntil) break
    if (scheduledScoreNotes.has(note.id)) continue
    scheduledScoreNotes.add(note.id)
    playScoreNote(note, context.currentTime + Math.max(0, note.start - now))
  }
}

const tickScorePlayback = () => {
  if (!isScorePlaying.value) return
  scoreCurrentTime.value = scorePlaybackBaseTime.value + (performance.now() - scorePlaybackStartedAt.value) / 1000
  if (scoreCurrentTime.value >= scoreDuration.value) {
    stopScorePlayback()
    scoreCurrentTime.value = 0
    return
  }
  scheduleScoreNotes()
  scoreAnimationFrame.value = window.requestAnimationFrame(tickScorePlayback)
}

const playScore = async () => {
  if (!scoreNotes.value.length) return
  pauseAll()
  const context = getScoreAudioContext()
  if (context.state === 'suspended') {
    await context.resume()
  }
  scheduledScoreNotes.clear()
  scorePlaybackBaseTime.value = Math.min(scoreCurrentTime.value, Math.max(0, scoreDuration.value - 0.05))
  scorePlaybackStartedAt.value = performance.now()
  isScorePlaying.value = true
  tickScorePlayback()
}

const stopScorePlayback = () => {
  isScorePlaying.value = false
  scheduledScoreNotes.clear()
  if (scoreAnimationFrame.value != null) {
    window.cancelAnimationFrame(scoreAnimationFrame.value)
    scoreAnimationFrame.value = null
  }
}

const toggleScorePlayback = async () => {
  if (isScorePlaying.value) {
    stopScorePlayback()
    return
  }
  try {
    await playScore()
  } catch (error) {
    console.error(error)
    ElMessage.error('谱面演奏失败，浏览器可能拦截了音频播放')
  }
}

const seekTo = (value: number | number[]) => {
  const nextTime = Array.isArray(value) ? value[0] : value
  currentTime.value = nextTime
  for (const audio of audioRefs.values()) {
    setAudioTime(audio, nextTime)
  }
  schedulePersistWorkspace()
}

const seekScoreTo = (value: number | number[]) => {
  const nextTime = Array.isArray(value) ? value[0] : value
  scoreCurrentTime.value = Math.min(scoreDuration.value || 0, Math.max(0, nextTime))
  scheduledScoreNotes.clear()
  if (isScorePlaying.value) {
    scorePlaybackBaseTime.value = scoreCurrentTime.value
    scorePlaybackStartedAt.value = performance.now()
  }
}

const handleWaveformClick = (event: MouseEvent, stem: MusicStem) => {
  if (!duration.value || !getTrackFile(stem)) return
  const target = event.currentTarget
  if (!(target instanceof HTMLElement)) return
  const rect = target.getBoundingClientRect()
  if (rect.width <= 0) return
  const ratio = Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width))
  seekTo(ratio * duration.value)
}

const handleTrackToggle = async (stem: MusicStem) => {
  const track = getTrack(stem)
  if (!track) return

  if (stem === 'original') {
    if (track.enabled) {
      updateSeparatedEnabledSnapshot()
      for (const separatedTrack of getSeparatedTracks()) {
        separatedTrack.enabled = false
      }
    } else {
      const snapshot = getSeparatedEnabledSnapshot()
      for (const separatedTrack of getSeparatedTracks()) {
        separatedTrack.enabled = Boolean(snapshot[separatedTrack.key])
      }
    }
  } else if (track.enabled) {
    const originalTrack = getTrack('original')
    if (originalTrack) {
      originalTrack.enabled = false
    }
    updateSeparatedEnabledSnapshot()
  } else if (!getTrack('original')?.enabled) {
    updateSeparatedEnabledSnapshot()
  }

  applyAllTrackVolumes()
  schedulePersistWorkspace()

  for (const item of tracks) {
    const audio = audioRefs.get(item.key)
    if (!audio) continue
    setAudioTime(audio, currentTime.value)
    if (!item.enabled) {
      audio.pause()
    }
  }

  if (isPlaying.value) {
    const audios = enabledAudios()
    try {
      await Promise.all(audios.map((audio) => audio.play()))
    } catch (error) {
      console.error(error)
      ElMessage.error('播放失败，浏览器可能拦截了音频播放')
    }
  }
}

const handleMetadata = (stem: MusicStem) => {
  const audio = audioRefs.get(stem)
  if (audio && currentTime.value > 0) {
    setAudioTime(audio, Math.min(currentTime.value, audio.duration || currentTime.value))
  }
  if (stem === 'original' && audio?.duration && Number.isFinite(audio.duration)) {
    duration.value = audio.duration
    if (currentTime.value > audio.duration) {
      seekTo(audio.duration)
    }
  }
}

const handleTimeUpdate = (stem: MusicStem) => {
  if (stem !== getPlaybackClockStem()) return
  const audio = audioRefs.get(stem)
  if (!audio || Math.abs(audio.currentTime - currentTime.value) < 0.2) return
  currentTime.value = audio.currentTime
  schedulePersistWorkspace()
}

const handleEnded = (stem: MusicStem) => {
  if (stem === getPlaybackClockStem()) {
    pauseAll()
    currentTime.value = 0
    schedulePersistWorkspace()
  }
}

const statusText = (status: MusicJob['status']) => {
  if (status === 'completed') return '完成'
  if (status === 'failed') return '失败'
  if (status === 'running') return '运行中'
  return '排队中'
}

const formatDate = (timestamp: number | null | undefined) => {
  if (!timestamp) return ''
  return new Date(timestamp * 1000).toLocaleString()
}

const formatTime = (seconds: number) => {
  const safeSeconds = Number.isFinite(seconds) ? Math.max(0, seconds) : 0
  const minutes = Math.floor(safeSeconds / 60)
  const rest = Math.floor(safeSeconds % 60)
  return `${minutes}:${String(rest).padStart(2, '0')}`
}

const buildPeaks = (buffer: AudioBuffer, bucketCount: number) => {
  const channelCount = Math.max(1, buffer.numberOfChannels)
  const sampleCount = buffer.length
  const bucketSize = Math.max(1, Math.floor(sampleCount / bucketCount))
  const peaks: number[] = []

  for (let bucketIndex = 0; bucketIndex < bucketCount; bucketIndex += 1) {
    const start = bucketIndex * bucketSize
    const end = Math.min(sampleCount, start + bucketSize)
    let sum = 0
    let count = 0

    for (let channel = 0; channel < channelCount; channel += 1) {
      const data = buffer.getChannelData(channel)
      for (let index = start; index < end; index += 8) {
        const value = data[index] || 0
        sum += value * value
        count += 1
      }
    }

    peaks.push(count > 0 ? Math.sqrt(sum / count) : 0)
  }

  const maxPeak = Math.max(...peaks, 0.001)
  return peaks.map((peak) => Math.min(1, peak / maxPeak))
}

const loadWaveform = async (url: string) => {
  if (waveformCache[url] || waveformLoading[url]) return
  waveformLoading[url] = true
  try {
    const response = await fetch(url)
    if (!response.ok) {
      throw new Error(`Waveform request failed: ${response.status}`)
    }
    const arrayBuffer = await response.arrayBuffer()
    const AudioContextCtor = window.AudioContext || (window as any).webkitAudioContext
    const context = new AudioContextCtor()
    try {
      const decoded = await context.decodeAudioData(arrayBuffer.slice(0))
      waveformCache[url] = buildPeaks(decoded, 180)
    } finally {
      await context.close()
    }
  } catch (error) {
    console.error('Failed to build waveform', error)
    waveformCache[url] = []
  } finally {
    waveformLoading[url] = false
  }
}

watch(
  audioFiles,
  (files) => {
    for (const file of files) {
      void loadWaveform(file.url)
    }
  },
  { immediate: true },
)

watch(selectedEngine, () => {
  schedulePersistWorkspace()
})

onMounted(async () => {
  workspacePrefs.value = loadStoredWorkspace()
  if (workspacePrefs.value.selectedEngine === 'demucs' || workspacePrefs.value.selectedEngine === 'audio_separator_6s') {
    selectedEngine.value = workspacePrefs.value.selectedEngine
  }
  try {
    const [info] = await Promise.all([
      getMusicToolInfo().then((value) => {
        toolInfo.value = value
        return value
      }),
      loadJobs(),
    ])
    toolInfo.value = info
  } catch (error) {
    console.error(error)
  }
})

onBeforeUnmount(() => {
  persistWorkspaceNow()
  if (persistTimer.value != null) {
    window.clearTimeout(persistTimer.value)
    persistTimer.value = null
  }
  stopPolling()
  pauseAll()
  stopScorePlayback()
  void scoreAudioContext.value?.close()
})
</script>

<style scoped>
.music-tools-page {
  padding: 24px;
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.page-head,
.upload-row,
.transport,
.score-toolbar,
.score-actions,
.score-transport,
.stem-control-row,
.stem-heading {
  display: flex;
  align-items: center;
}

.page-head {
  justify-content: space-between;
  gap: 16px;
}

.eyebrow {
  color: #6b7280;
  font-size: 13px;
  margin-bottom: 4px;
}

h1 {
  margin: 0;
  font-size: 26px;
  font-weight: 650;
  color: #111827;
}

.upload-row {
  gap: 10px;
  flex-wrap: wrap;
}

.file-picker {
  min-width: 280px;
  max-width: 520px;
  height: 36px;
  padding: 0 12px;
  border: 1px solid #dcdfe6;
  border-radius: 6px;
  display: inline-flex;
  align-items: center;
  color: #374151;
  cursor: pointer;
  background: #fff;
}

.file-picker input {
  display: none;
}

.file-picker span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.engine-select {
  width: 118px;
}

.task-alert {
  max-width: 920px;
}

.workspace-layout {
  display: grid;
  grid-template-columns: minmax(240px, 320px) minmax(0, 980px);
  gap: 18px;
  align-items: start;
}

.history-pane {
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  overflow: hidden;
  background: #fff;
}

.pane-title {
  padding: 10px 12px;
  font-weight: 650;
  color: #111827;
  border-bottom: 1px solid #eef0f3;
}

.history-empty {
  padding: 14px 12px;
  color: #6b7280;
}

.history-item {
  width: 100%;
  padding: 10px 12px;
  display: flex;
  flex-direction: column;
  gap: 3px;
  border: 0;
  border-bottom: 1px solid #eef0f3;
  background: #fff;
  color: inherit;
  text-align: left;
  cursor: pointer;
}

.history-item:last-child {
  border-bottom: 0;
}

.history-item.active {
  background: #f3f7ff;
}

.history-name {
  color: #111827;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.history-meta {
  color: #6b7280;
  font-size: 12px;
}

.workspace {
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.active-title {
  display: flex;
  align-items: baseline;
  gap: 10px;
}

.active-name {
  font-size: 18px;
  font-weight: 650;
  color: #111827;
}

.active-meta {
  color: #6b7280;
  font-size: 12px;
}

.transport {
  gap: 12px;
}

.timeline {
  flex: 1;
  min-width: 180px;
}

.time-text {
  width: 48px;
  font-variant-numeric: tabular-nums;
  color: #4b5563;
  text-align: center;
}

.stem-table {
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  overflow: hidden;
}

.stem-row {
  --stem-accent: #0f8f86;
  --stem-title-color: #111827;

  min-height: 58px;
  padding: 10px 14px;
  border-bottom: 1px solid #eef0f3;
}

.stem-row:last-child {
  border-bottom: 0;
}

.original-stem {
  --stem-accent: #64748b;
  --stem-title-color: #334155;

  background: #f8fafc;
}

.original-stem .stem-title {
  color: var(--stem-title-color);
}

.stem-switch {
  --el-switch-on-color: var(--stem-accent);
}

.stem-control-row {
  gap: 14px;
  min-height: 26px;
}

.stem-heading {
  min-width: 0;
  flex: 1;
  gap: 10px;
}

.stem-title {
  flex: 0 0 auto;
  font-weight: 600;
  color: var(--stem-title-color);
}

.stem-file {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
  color: #6b7280;
}

.stem-wave-row {
  margin-top: 6px;
  margin-left: 54px;
}

.waveform {
  position: relative;
  width: 100%;
  height: 30px;
  padding: 0;
  border: 0;
  border-radius: 4px;
  display: flex;
  align-items: center;
  gap: 1px;
  background: #f3f4f6;
  overflow: hidden;
  cursor: pointer;
}

.waveform:disabled {
  cursor: default;
  opacity: 0.55;
}

.waveform-bar {
  flex: 1 1 0;
  min-width: 1px;
  border-radius: 1px;
  background: var(--stem-accent);
  opacity: 0.72;
}

.waveform-playhead {
  position: absolute;
  top: 0;
  bottom: 0;
  width: 2px;
  background: #111827;
  opacity: 0.72;
  transform: translateX(-1px);
  pointer-events: none;
}

.volume-slider {
  --el-slider-main-bg-color: var(--stem-accent);

  width: 180px;
}

.score-panel {
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  overflow: hidden;
  background: #fff;
}

.score-toolbar {
  min-height: 48px;
  padding: 8px 12px;
  justify-content: space-between;
  gap: 12px;
  border-bottom: 1px solid #eef0f3;
}

.score-heading {
  min-width: 0;
  display: flex;
  align-items: baseline;
  gap: 10px;
}

.score-title {
  font-weight: 650;
  color: #111827;
}

.score-meta {
  font-size: 12px;
  color: #6b7280;
}

.score-actions {
  gap: 10px;
  flex: 0 0 auto;
}

.score-link {
  font-size: 12px;
  color: #2563eb;
  text-decoration: none;
}

.score-link:hover {
  text-decoration: underline;
}

.score-transport {
  gap: 12px;
  padding: 8px 12px;
  border-bottom: 1px solid #eef0f3;
}

.score-loading {
  padding: 12px;
  color: #6b7280;
}

.score-empty {
  display: flex;
  align-items: center;
  min-height: 88px;
  padding: 12px;
  color: #6b7280;
  background: #f8fafc;
  border-top: 1px solid #e5e7eb;
}

.piano-roll {
  height: 258px;
  background: #1f2933;
}

.roll-lane {
  position: relative;
  height: 206px;
  margin: 0 12px;
  overflow: hidden;
  background:
    linear-gradient(to right, rgba(255, 255, 255, 0.055) 1px, transparent 1px) 0 0 / calc(100% / var(--visible-keys)) 100%,
    linear-gradient(to bottom, rgba(255, 255, 255, 0.08), transparent 42%);
}

.roll-note {
  position: absolute;
  border-radius: 3px;
  background: #38bdf8;
  box-shadow: 0 0 0 1px rgba(15, 23, 42, 0.35), 0 0 10px rgba(56, 189, 248, 0.34);
  opacity: 0.88;
}

.roll-note.right_chord {
  background: #22c55e;
  box-shadow: 0 0 0 1px rgba(15, 23, 42, 0.35), 0 0 10px rgba(34, 197, 94, 0.3);
}

.roll-note.left {
  background: #f59e0b;
  box-shadow: 0 0 0 1px rgba(15, 23, 42, 0.35), 0 0 10px rgba(245, 158, 11, 0.32);
}

.roll-note.active {
  filter: brightness(1.25);
}

.piano-keyboard {
  position: relative;
  height: 52px;
  margin: 0 12px;
  display: grid;
  grid-template-columns: repeat(var(--visible-keys), minmax(2px, 1fr));
  border-top: 2px solid #111827;
  background: #111827;
}

.piano-key {
  height: 52px;
  border-right: 1px solid #9ca3af;
  background: #f9fafb;
}

.piano-key.black {
  height: 32px;
  margin-inline: -35%;
  z-index: 1;
  border-right: 0;
  border-radius: 0 0 2px 2px;
  background: #111827;
}

.piano-key.active {
  background: #93c5fd;
}

.piano-key.black.active {
  background: #3b82f6;
}

.empty-state {
  max-width: 620px;
  padding: 28px 0;
  color: #6b7280;
}

.empty-title {
  font-size: 18px;
  color: #111827;
  margin-bottom: 6px;
}

audio {
  display: none;
}

@media (max-width: 820px) {
  .music-tools-page {
    padding: 16px;
  }

  .workspace-layout {
    grid-template-columns: 1fr;
  }

  .page-head {
    align-items: flex-start;
    flex-direction: column;
  }

  .stem-row {
    padding: 10px 12px;
  }

  .stem-control-row {
    align-items: flex-start;
    flex-wrap: wrap;
  }

  .stem-heading {
    flex-basis: calc(100% - 60px);
    flex-direction: column;
    align-items: flex-start;
    gap: 2px;
  }

  .stem-wave-row {
    margin-left: 0;
  }

  .file-picker,
  .volume-slider {
    width: 100%;
  }
}
</style>
