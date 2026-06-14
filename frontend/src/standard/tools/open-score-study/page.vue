<template>
  <div class="open-score-page">
    <section class="page-head">
      <div>
        <div class="eyebrow">开源乐谱解析</div>
        <h1>内置曲库分轨试听</h1>
      </div>
      <div class="head-actions">
        <div class="study-mode-links">
          <span>当前：总谱 MIDI</span>
          <a href="/standalone/tools/music-tools?mode=multitrack">真实录音分轨</a>
        </div>
        <div class="source-strip">
          <a v-for="source in sources" :key="source.name" :href="source.url" target="_blank" rel="noreferrer">
            {{ source.name }}
          </a>
        </div>
      </div>
    </section>

    <section class="study-layout">
      <aside class="path-pane">
        <div class="pane-title">内置曲库</div>
        <div class="kind-filter" aria-label="曲目分类">
          <button
            v-for="kind in studyKinds"
            :key="kind"
            type="button"
            :class="{ active: selectedStudyKind === kind }"
            @click="selectedStudyKind = kind"
          >
            {{ kind }}
          </button>
        </div>
        <button
          v-for="work in filteredWorks"
          :key="work.id"
          class="work-item"
          :class="{ active: work.id === selectedWorkId }"
          type="button"
          @click="selectWork(work.id)"
        >
          <span class="work-level">{{ work.level }}</span>
          <span class="work-name">{{ work.title }}</span>
          <span class="work-meta">{{ work.study_kind || '完整曲目' }} · {{ work.form }} · {{ work.part_count || work.instrumentation.length }} 乐器/声部</span>
        </button>
      </aside>

      <main class="score-workbench">
        <section class="work-summary">
          <div class="summary-main">
            <div class="summary-kicker">{{ selectedWork.level }} · {{ selectedWork.form }}</div>
            <h2>{{ selectedWork.title }}</h2>
            <p>{{ selectedWork.description }}</p>
          </div>
          <div class="summary-actions">
            <a :href="selectedWork.source_url" target="_blank" rel="noreferrer">谱源</a>
            <a :href="selectedWork.pdf_url" target="_blank" rel="noreferrer">总谱 PDF</a>
          </div>
        </section>

        <section v-if="loadError" class="status-panel error">{{ loadError }}</section>
        <section v-else-if="isLoadingWork" class="status-panel">正在下载并解析真实 MIDI...</section>
        <section v-else-if="!isWebAudioAvailable" class="status-panel warning">当前浏览器不支持 Web Audio 试听；请用 Chrome/Edge 打开本页，仍可查看声部结构和总谱。</section>

        <section v-else class="transport-panel">
          <el-button circle :title="isPlaying ? '暂停' : '播放'" @click="togglePlayback">
            <el-icon><VideoPause v-if="isPlaying" /><VideoPlay v-else /></el-icon>
          </el-button>
          <span class="time-text">{{ formatTime(currentTime) }}</span>
          <el-slider
            v-model="currentTime"
            class="timeline-slider"
            :min="0"
            :max="selectedWork.duration"
            :step="0.05"
            :show-tooltip="false"
            @change="seekPlayback"
          />
          <span class="time-text">{{ formatTime(selectedWork.duration) }}</span>
          <el-button size="small" plain @click="enableAllParts">全开</el-button>
          <el-button size="small" plain @click="soloSelectedPart">只听当前</el-button>
          <el-button v-if="principalParts.length" size="small" plain @click="soloPrincipalParts">只听主角</el-button>
          <el-button v-if="foundationParts.length" size="small" plain @click="soloFoundationParts">听骨架</el-button>
        </section>

        <section v-if="selectedWork.parts.length" class="study-guide">
          <div>
            <span>拆听目标</span>
            <strong>{{ studyGuide.goal }}</strong>
          </div>
          <div>
            <span>建议顺序</span>
            <strong>{{ studyGuide.order }}</strong>
          </div>
          <div>
            <span>当前编制</span>
            <strong>{{ orchestrationSummary }}</strong>
          </div>
        </section>

        <section v-if="availablePartGroups.length" class="group-solo-panel">
          <span>按组独听</span>
          <button
            v-for="group in availablePartGroups"
            :key="group.name"
            type="button"
            :class="{ active: activeSoloGroup === group.name }"
            @click="soloPartGroup(group.name)"
          >
            {{ group.name }} {{ group.count }}
          </button>
        </section>

        <section class="mixer-panel">
          <div class="section-title">乐器独听</div>
          <div class="part-list">
            <div
              v-for="part in selectedWork.parts"
              :key="part.name"
              class="part-row"
              :class="{ active: part.name === selectedPartName, muted: !enabledParts[part.name] }"
              role="button"
              tabindex="0"
              @click="selectedPartName = part.name"
              @keydown.enter="selectedPartName = part.name"
            >
              <el-switch
                v-model="enabledParts[part.name]"
                class="part-switch"
                @click.stop
                @change="restartIfPlaying"
              />
              <div class="part-heading">
                <span class="part-name">{{ part.name }}</span>
                <span class="part-role">{{ part.role }}</span>
              </div>
              <div class="part-focus">{{ part.range.join(' - ') }} · {{ part.note_count }} notes</div>
              <div class="part-timeline">
                <span
                  v-for="(note, index) in displayNotes(part)"
                  :key="`${part.name}:${index}:${note.start}`"
                  class="note-block"
                  :class="{ active: isNoteActive(note) }"
                  :style="{ left: `${(note.start / selectedWork.duration) * 100}%`, width: `${(note.duration / selectedWork.duration) * 100}%` }"
                />
              </div>
              <el-slider
                v-model="partVolumes[part.name]"
                class="volume-slider"
                :min="0"
                :max="1"
                :step="0.01"
                :show-tooltip="false"
                @click.stop
                @input="restartIfPlaying"
              />
            </div>
          </div>
        </section>

        <section class="analysis-grid">
          <section class="detail-panel">
            <div class="section-title">当前乐器</div>
            <div class="part-detail">
              <div class="detail-heading">
                <span>{{ selectedPart.name }}</span>
                <strong>{{ selectedPart.role }}</strong>
              </div>
              <p>{{ selectedPartHint }}</p>
              <div class="listen-plan">
                <span v-for="task in selectedWork.listen_focus" :key="task">{{ task }}</span>
              </div>
            </div>
          </section>

          <section class="task-panel">
            <div class="section-title">赏析流程</div>
            <div class="task-list">
              <div v-for="(task, index) in listenTasks" :key="task" class="task-item">
                <span>{{ index + 1 }}</span>
                <p>{{ task }}</p>
              </div>
            </div>
          </section>
        </section>

        <section class="source-panel">
          <div class="section-title">已内置内容</div>
          <div class="source-detail">
            <span>{{ selectedWork.source_name }} · {{ selectedWork.license_label }}</span>
            <span>后端缓存并解析真实 MIDI，页面优先用 SoundFont 乐器采样按声部试听；谱源和 PDF 用于核对原始总谱。</span>
          </div>
        </section>
      </main>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { VideoPause, VideoPlay } from '@element-plus/icons-vue'
import { getOpenScoreWork, listOpenScoreWorks, type OpenScoreNote, type OpenScorePart, type OpenScoreWork, type OpenScoreWorkSummary } from '@/api/musicTools'

const sources = [
  { name: 'Mutopia', url: 'https://www.mutopiaproject.org/' },
  { name: 'OpenScore', url: 'https://fourscoreandmore.org/openscore/' },
  { name: 'KernScores', url: 'https://kern.ccarh.org/' },
  { name: 'MusicXML', url: 'https://www.musicxml.com/music-in-musicxml/' },
]

const fallbackWork: OpenScoreWork = {
  id: 'loading',
  title: '正在加载真实曲库',
  composer: '',
  level: '真实曲库',
  form: '多声部',
  instrumentation: [],
  description: '系统会从后端精选曲库加载可独立试听的真实开放乐谱。',
  listen_focus: [],
  source_name: '',
  license_label: '',
  source_url: 'https://www.mutopiaproject.org/',
  midi_url: '',
  pdf_url: 'https://www.mutopiaproject.org/',
  tempo_bpm: 120,
  duration: 0,
  ticks_per_beat: 384,
  parts: [],
  cached_at: 0,
  midi_size: 0,
}

const works = ref<OpenScoreWorkSummary[]>([])
const loadedWorks = reactive<Record<string, OpenScoreWork>>({})
const selectedWorkId = ref('')
const selectedPartName = ref('')
const selectedStudyKind = ref('全部')
const currentTime = ref(0)
const isPlaying = ref(false)
const isLoadingList = ref(false)
const isLoadingWork = ref(false)
const loadError = ref('')
const enabledParts = reactive<Record<string, boolean>>({})
const partVolumes = reactive<Record<string, number>>({})
const activeSoloGroup = ref('')
const isWebAudioAvailable = ref(false)

let audioContext: AudioContext | null = null
let playbackStartedAt = 0
let playbackBaseTime = 0
let animationFrame: number | null = null
const scheduledNodes: AudioScheduledSourceNode[] = []
const scheduledNoteKeys = new Set<string>()
const sampleBuffers = new Map<string, AudioBuffer | null>()
const sampleBufferPromises = new Map<string, Promise<AudioBuffer | null>>()

const SOUNDFONT_BASE_URL = 'https://gleitz.github.io/midi-js-soundfonts/FluidR3_GM'
const SAMPLE_PRELOAD_SECONDS = 2.5

interface VoiceProfile {
  wave: OscillatorType
  gain: number
  attack: number
  release: number
}

interface SampleProfile {
  instrument: string
  gain: number
  attack: number
  release: number
}

type AudioContextConstructor = new () => AudioContext

const getAudioContextConstructor = (): AudioContextConstructor | null => {
  if (typeof window === 'undefined') return null
  const browserWindow = window as Window & { webkitAudioContext?: AudioContextConstructor }
  return window.AudioContext || browserWindow.webkitAudioContext || null
}

const selectedSummary = computed(() => works.value.find((work) => work.id === selectedWorkId.value))
const studyKinds = computed(() => {
  const kinds = Array.from(new Set(works.value.map((work) => work.study_kind || '完整曲目')))
  return ['全部', ...kinds]
})
const filteredWorks = computed(() => {
  if (selectedStudyKind.value === '全部') return works.value
  return works.value.filter((work) => (work.study_kind || '完整曲目') === selectedStudyKind.value)
})
const selectedWork = computed<OpenScoreWork>(() => loadedWorks[selectedWorkId.value] || (selectedSummary.value ? { ...fallbackWork, ...selectedSummary.value } : fallbackWork))
const selectedPart = computed<OpenScorePart>(() => selectedWork.value.parts.find((part) => part.name === selectedPartName.value) || selectedWork.value.parts[0] || {
  name: '未选择乐器',
  role: '',
  program: null,
  channel: null,
  note_count: 0,
  range: ['', ''],
  notes: [],
})

const principalParts = computed(() => selectedWork.value.parts.filter((part) => isPrincipalPart(part)))
const foundationParts = computed(() => selectedWork.value.parts.filter((part) => isFoundationPart(part)))
const availablePartGroups = computed(() => {
  const counts = selectedWork.value.parts.reduce<Record<string, number>>((acc, part) => {
    const group = partGroup(part)
    acc[group] = (acc[group] || 0) + 1
    return acc
  }, {})
  const order = ['独奏', '弦乐', '木管', '铜管', '低音/节奏', '人声', '键盘', '其他']
  return order
    .filter((name) => counts[name])
    .map((name) => ({ name, count: counts[name] }))
})
const orchestrationSummary = computed(() => {
  const groups = selectedWork.value.parts.reduce<Record<string, number>>((acc, part) => {
    const group = partGroup(part)
    acc[group] = (acc[group] || 0) + 1
    return acc
  }, {})
  const order = ['独奏', '弦乐', '木管', '铜管', '低音/节奏', '人声', '键盘', '其他']
  return order
    .filter((group) => groups[group])
    .map((group) => `${group}${groups[group]}`)
    .join(' · ')
})
const studyGuide = computed(() => {
  const work = selectedWork.value
  if ((work.study_kind || '') === '独奏主角') {
    return {
      goal: '听主角如何被乐队托起、回应和放大',
      order: '主角 -> 低音/节奏 -> 分组打开 -> 全开',
    }
  }
  if (work.form.includes('交响') || work.form.includes('序曲') || work.form.includes('管弦')) {
    return {
      goal: '听不同乐器组如何叠成完整管弦织体',
      order: '低音动机 -> 弦乐 -> 木管/铜管 -> 全开',
    }
  }
  return {
    goal: '听清主旋律、内声部和低音的分工',
    order: '主旋律 -> 低音 -> 内声部 -> 全开',
  }
})
const selectedPartHint = computed(() => {
  const part = selectedPart.value
  if (isPrincipalPart(part)) return `${part.name} 是当前曲目的听觉入口。先只听它，抓住主题、句尾和重复位置，再打开伴奏声部比较它们如何回应。`
  if (isFoundationPart(part)) return `${part.name} 更像骨架声部。独听它可以看到节奏、根音或重音怎么支撑整首曲子。`
  const group = partGroup(part)
  if (group === '木管' || group === '铜管') return `${part.name} 负责颜色、回应或强调。建议先听主角，再打开这个声部观察它在什么时候接话。`
  return `${part.name} 是 ${part.role || '声部'}。把它和主角、低音分别组合试听，可以判断它是在填和声、做对位，还是推动节奏。`
})

const listenTasks = computed(() => {
  const work = selectedWork.value
  if (!work.parts.length) return ['加载曲目后，先选择一个乐器独听。']
  return [
    principalParts.value.length ? `先点“只听主角”，抓住 ${principalParts.value.map((part) => part.name).join(' / ')} 的主题。` : `先只听 ${work.parts[0].name}，抓住最容易辨认的主题或入口。`,
    foundationParts.value.length ? `再点“听骨架”，感受 ${foundationParts.value.map((part) => part.name).slice(0, 3).join(' / ')} 的低音、节奏或重音。` : '观察这个乐器的音域、节奏和重复。',
    '最后逐个打开其他乐器，听完整织体如何叠起来。',
  ]
})

const isPrincipalPart = (part: OpenScorePart) => {
  const text = `${part.name} ${part.role}`.toLowerCase()
  return text.includes('solo') || text.includes('独奏') || text.includes('主旋律') || text.includes('旋律主角')
}

const isFoundationPart = (part: OpenScorePart) => {
  const text = `${part.name} ${part.role}`.toLowerCase()
  return text.includes('bass') || text.includes('cello') || text.includes('低音') || text.includes('根基') || text.includes('timpani') || text.includes('节奏')
}

const partGroup = (part: OpenScorePart) => {
  const text = `${part.name} ${part.role}`.toLowerCase()
  if (isPrincipalPart(part)) return '独奏'
  if (isFoundationPart(part)) return '低音/节奏'
  if (text.includes('violin') || text.includes('viola') || text.includes('cello') || text.includes('strings') || text.includes('弦乐') || text.includes('中声部') || text.includes('低音线')) return '弦乐'
  if (text.includes('flute') || text.includes('oboe') || text.includes('clarinet') || text.includes('bassoon') || text.includes('木管')) return '木管'
  if (text.includes('horn') || text.includes('trumpet') || text.includes('trombone') || text.includes('铜管')) return '铜管'
  if (text.includes('soprano') || text.includes('alto') || text.includes('tenor') || text.includes('choir') || text.includes('女') || text.includes('男')) return '人声'
  if (text.includes('piano') || text.includes('harpsichord') || text.includes('键盘') || text.includes('钢琴')) return '键盘'
  return '其他'
}

const selectWork = (workId: string) => {
  if (workId === selectedWorkId.value) return
  stopPlayback({ resetTime: true })
  selectedWorkId.value = workId
}

const loadWorks = async () => {
  isLoadingList.value = true
  loadError.value = ''
  try {
    const payload = await listOpenScoreWorks()
    works.value = payload.works
    if (!selectedWorkId.value && payload.works.length) {
      selectedWorkId.value = payload.works[0].id
    }
  } catch (error) {
    console.error(error)
    loadError.value = '真实曲库列表加载失败。'
  } finally {
    isLoadingList.value = false
  }
}

const loadSelectedWork = async (workId: string) => {
  if (!workId || loadedWorks[workId]) {
    setupPartState()
    return
  }
  isLoadingWork.value = true
  loadError.value = ''
  try {
    loadedWorks[workId] = await getOpenScoreWork(workId)
    setupPartState()
  } catch (error) {
    console.error(error)
    loadError.value = '曲目 MIDI 下载或解析失败。'
  } finally {
    isLoadingWork.value = false
  }
}

const setupPartState = () => {
  for (const key of Object.keys(enabledParts)) delete enabledParts[key]
  for (const key of Object.keys(partVolumes)) delete partVolumes[key]
  for (const part of selectedWork.value.parts) {
    enabledParts[part.name] = true
    partVolumes[part.name] = defaultPartVolume(part)
  }
  selectedPartName.value = selectedWork.value.parts[0]?.name || ''
  activeSoloGroup.value = ''
  currentTime.value = 0
}

const defaultPartVolume = (part: OpenScorePart) => {
  const group = partGroup(part)
  if (group === '铜管') return 0.48
  if (group === '低音/节奏') return 0.52
  if (group === '键盘') return 0.56
  if (part.name.toLowerCase().includes('cello')) return 0.55
  if (part.name.toLowerCase().includes('bass')) return 0.5
  return 0.62
}

const noteToFrequency = (pitch: number) => 440 * 2 ** ((pitch - 69) / 12)

const midiToFlatNoteName = (pitch: number) => {
  const names = ['C', 'Db', 'D', 'Eb', 'E', 'F', 'Gb', 'G', 'Ab', 'A', 'Bb', 'B']
  const octave = Math.floor(pitch / 12) - 1
  return `${names[((pitch % 12) + 12) % 12]}${octave}`
}

const sampleProfile = (part: OpenScorePart): SampleProfile | null => {
  const text = `${part.name} ${part.role}`.toLowerCase()
  if (text.includes('violin')) return { instrument: 'violin', gain: 0.85, attack: 0.008, release: 0.12 }
  if (text.includes('viola')) return { instrument: 'viola', gain: 0.8, attack: 0.01, release: 0.14 }
  if (text.includes('cello')) return { instrument: 'cello', gain: 0.82, attack: 0.012, release: 0.16 }
  if (text.includes('bassoon')) return { instrument: 'bassoon', gain: 0.78, attack: 0.014, release: 0.14 }
  if (text.includes('bass') || text.includes('contrabass')) return { instrument: 'contrabass', gain: 0.78, attack: 0.012, release: 0.16 }
  if (text.includes('strings') || text.includes('弦乐')) return { instrument: 'string_ensemble_1', gain: 0.72, attack: 0.018, release: 0.18 }
  if (text.includes('piano') || text.includes('钢琴')) return { instrument: 'acoustic_grand_piano', gain: 0.82, attack: 0.004, release: 0.2 }
  if (text.includes('flute')) return { instrument: 'flute', gain: 0.8, attack: 0.012, release: 0.12 }
  if (text.includes('oboe')) return { instrument: 'oboe', gain: 0.74, attack: 0.012, release: 0.12 }
  if (text.includes('clarinet')) return { instrument: 'clarinet', gain: 0.78, attack: 0.014, release: 0.14 }
  if (text.includes('horn')) return { instrument: 'french_horn', gain: 0.78, attack: 0.02, release: 0.18 }
  if (text.includes('trumpet')) return { instrument: 'trumpet', gain: 0.7, attack: 0.01, release: 0.14 }
  if (text.includes('trombone')) return { instrument: 'trombone', gain: 0.72, attack: 0.012, release: 0.16 }
  if (text.includes('soprano') || text.includes('alto') || text.includes('tenor') || text.includes('choir')) return { instrument: 'choir_aahs', gain: 0.76, attack: 0.02, release: 0.2 }
  return null
}

const loadSampleBuffer = (profile: SampleProfile, pitch: number) => {
  if (!audioContext) return Promise.resolve(null)
  const noteName = midiToFlatNoteName(pitch)
  const cacheKey = `${profile.instrument}:${noteName}`
  if (sampleBuffers.has(cacheKey)) return Promise.resolve(sampleBuffers.get(cacheKey) || null)
  const existingPromise = sampleBufferPromises.get(cacheKey)
  if (existingPromise) return existingPromise

  const promise = fetch(`${SOUNDFONT_BASE_URL}/${profile.instrument}-mp3/${noteName}.mp3`)
    .then((response) => (response.ok ? response.arrayBuffer() : null))
    .then((data) => (data && audioContext ? audioContext.decodeAudioData(data) : null))
    .catch(() => null)
    .then((buffer) => {
      sampleBuffers.set(cacheKey, buffer)
      sampleBufferPromises.delete(cacheKey)
      return buffer
    })
  sampleBufferPromises.set(cacheKey, promise)
  return promise
}

const preloadSamplesForWindow = async (playbackOffset: number, scheduleUntil: number) => {
  if (!audioContext) return
  const tasks: Promise<AudioBuffer | null>[] = []
  for (const part of selectedWork.value.parts) {
    if (!enabledParts[part.name]) continue
    const profile = sampleProfile(part)
    if (!profile) continue
    for (const note of part.notes) {
      const noteEnd = note.start + note.duration
      if (noteEnd <= playbackOffset - 0.03) continue
      if (note.start > scheduleUntil) break
      tasks.push(loadSampleBuffer(profile, note.pitch))
    }
  }
  await Promise.all(tasks)
}

const voiceProfile = (part: OpenScorePart): VoiceProfile => {
  const group = partGroup(part)
  const text = `${part.name} ${part.role}`.toLowerCase()
  if (group === '键盘') return { wave: 'triangle', gain: 0.2, attack: 0.008, release: 0.16 }
  if (group === '弦乐' || group === '独奏') {
    return { wave: text.includes('cello') || text.includes('bass') ? 'sawtooth' : 'triangle', gain: 0.17, attack: 0.03, release: 0.2 }
  }
  if (group === '木管') return { wave: 'sine', gain: 0.18, attack: 0.018, release: 0.16 }
  if (group === '铜管') return { wave: 'square', gain: 0.12, attack: 0.012, release: 0.18 }
  if (group === '低音/节奏') return { wave: 'sawtooth', gain: 0.15, attack: 0.008, release: 0.14 }
  if (group === '人声') return { wave: 'sine', gain: 0.16, attack: 0.03, release: 0.2 }
  return { wave: 'sine', gain: 0.16, attack: 0.02, release: 0.16 }
}

const scheduleSampleNote = (part: OpenScorePart, note: OpenScoreNote, startAt: number, activeDuration: number, volume: number) => {
  if (!audioContext) return false
  const profile = sampleProfile(part)
  if (!profile) return false
  const noteName = midiToFlatNoteName(note.pitch)
  const buffer = sampleBuffers.get(`${profile.instrument}:${noteName}`)
  if (!buffer) {
    void loadSampleBuffer(profile, note.pitch)
    return false
  }

  const source = audioContext.createBufferSource()
  const gain = audioContext.createGain()
  const velocityGain = Math.max(0.18, Math.min(1, note.velocity / 127))
  const peakGain = Math.max(0.0001, volume * velocityGain * profile.gain)
  source.buffer = buffer
  gain.gain.setValueAtTime(0.0001, startAt)
  gain.gain.exponentialRampToValueAtTime(peakGain, startAt + profile.attack)
  gain.gain.exponentialRampToValueAtTime(Math.max(0.0001, peakGain * 0.82), startAt + Math.min(activeDuration * 0.45, 0.7))
  gain.gain.exponentialRampToValueAtTime(0.0001, startAt + activeDuration + profile.release)
  source.connect(gain)
  gain.connect(audioContext.destination)
  source.start(startAt, 0, activeDuration + profile.release)
  source.stop(startAt + activeDuration + profile.release + 0.02)
  scheduledNodes.push(source)
  return true
}

const schedulePart = (part: OpenScorePart, playbackOffset: number, scheduleUntil: number) => {
  if (!audioContext || !enabledParts[part.name]) return
  const now = audioContext.currentTime
  const volume = partVolumes[part.name] ?? 0.6
  const voice = voiceProfile(part)
  for (const [index, note] of part.notes.entries()) {
    const noteEnd = note.start + note.duration
    if (noteEnd <= playbackOffset - 0.03) continue
    if (note.start > scheduleUntil) break
    const noteKey = `${part.name}:${index}:${note.start}:${note.pitch}`
    if (scheduledNoteKeys.has(noteKey)) continue
    scheduledNoteKeys.add(noteKey)

    const startAt = now + Math.max(0, note.start - playbackOffset)
    const activeDuration = Math.max(0.08, noteEnd - Math.max(note.start, playbackOffset))
    const stopAt = startAt + activeDuration
    if (scheduleSampleNote(part, note, startAt, activeDuration, volume)) {
      continue
    }
    const oscillator = audioContext.createOscillator()
    const gain = audioContext.createGain()
    oscillator.type = voice.wave
    oscillator.frequency.setValueAtTime(noteToFrequency(note.pitch), startAt)
    const velocityGain = Math.max(0.12, Math.min(1, note.velocity / 127))
    const peakGain = Math.max(0.0001, volume * velocityGain * voice.gain)
    gain.gain.setValueAtTime(0.0001, startAt)
    gain.gain.exponentialRampToValueAtTime(peakGain, startAt + voice.attack)
    gain.gain.exponentialRampToValueAtTime(Math.max(0.0001, peakGain * 0.35), startAt + Math.min(activeDuration * 0.55, 0.8))
    gain.gain.exponentialRampToValueAtTime(0.0001, stopAt + voice.release)
    oscillator.connect(gain)
    gain.connect(audioContext.destination)
    oscillator.start(startAt)
    oscillator.stop(stopAt + voice.release + 0.02)
    scheduledNodes.push(oscillator)
  }
}

const schedulePlaybackWindow = () => {
  if (!audioContext || !isPlaying.value) return
  const scheduleUntil = currentTime.value + 0.45
  for (const part of selectedWork.value.parts) {
    schedulePart(part, currentTime.value, scheduleUntil)
  }
}

const stopScheduledNodes = () => {
  while (scheduledNodes.length) {
    const node = scheduledNodes.pop()
    try {
      node?.stop()
    } catch {
      // already stopped
    }
    node?.disconnect()
  }
  scheduledNoteKeys.clear()
}

const tickPlayback = () => {
  if (!audioContext || !isPlaying.value) return
  const elapsed = audioContext.currentTime - playbackStartedAt
  currentTime.value = Math.min(selectedWork.value.duration, playbackBaseTime + elapsed)
  if (currentTime.value >= selectedWork.value.duration) {
    stopPlayback({ resetTime: true })
    return
  }
  schedulePlaybackWindow()
  animationFrame = window.requestAnimationFrame(tickPlayback)
}

const startPlayback = async () => {
  if (!selectedWork.value.parts.length) return
  if (!audioContext) {
    const AudioContextClass = getAudioContextConstructor()
    if (!AudioContextClass) {
      isWebAudioAvailable.value = false
      return
    }
    audioContext = new AudioContextClass()
  }
  if (audioContext.state === 'suspended') {
    await audioContext.resume()
  }
  stopScheduledNodes()
  playbackBaseTime = Math.min(currentTime.value, selectedWork.value.duration)
  await preloadSamplesForWindow(playbackBaseTime, playbackBaseTime + SAMPLE_PRELOAD_SECONDS)
  playbackStartedAt = audioContext.currentTime
  isPlaying.value = true
  schedulePlaybackWindow()
  tickPlayback()
}

const stopPlayback = (options: { resetTime?: boolean } = {}) => {
  isPlaying.value = false
  stopScheduledNodes()
  if (animationFrame != null) {
    window.cancelAnimationFrame(animationFrame)
    animationFrame = null
  }
  if (options.resetTime) {
    currentTime.value = 0
  }
}

const togglePlayback = async () => {
  if (isPlaying.value) {
    stopPlayback()
    return
  }
  await startPlayback()
}

const restartIfPlaying = () => {
  if (!isPlaying.value) return
  void startPlayback()
}

const seekPlayback = () => {
  if (currentTime.value >= selectedWork.value.duration) {
    currentTime.value = 0
  }
  restartIfPlaying()
}

const enableAllParts = () => {
  for (const part of selectedWork.value.parts) {
    enabledParts[part.name] = true
  }
  activeSoloGroup.value = ''
  restartIfPlaying()
}

const soloSelectedPart = () => {
  for (const part of selectedWork.value.parts) {
    enabledParts[part.name] = part.name === selectedPartName.value
  }
  activeSoloGroup.value = ''
  restartIfPlaying()
}

const soloPrincipalParts = () => {
  const names = new Set(principalParts.value.map((part) => part.name))
  if (!names.size) return
  for (const part of selectedWork.value.parts) {
    enabledParts[part.name] = names.has(part.name)
  }
  selectedPartName.value = principalParts.value[0]?.name || selectedPartName.value
  activeSoloGroup.value = '独奏'
  restartIfPlaying()
}

const soloFoundationParts = () => {
  const names = new Set(foundationParts.value.map((part) => part.name))
  if (!names.size) return
  for (const part of selectedWork.value.parts) {
    enabledParts[part.name] = names.has(part.name)
  }
  selectedPartName.value = foundationParts.value[0]?.name || selectedPartName.value
  activeSoloGroup.value = '低音/节奏'
  restartIfPlaying()
}

const soloPartGroup = (group: string) => {
  let firstPart = ''
  for (const part of selectedWork.value.parts) {
    const enabled = partGroup(part) === group
    enabledParts[part.name] = enabled
    if (enabled && !firstPart) {
      firstPart = part.name
    }
  }
  if (firstPart) {
    selectedPartName.value = firstPart
  }
  activeSoloGroup.value = group
  restartIfPlaying()
}

const isNoteActive = (note: OpenScoreNote) => currentTime.value >= note.start && currentTime.value < note.start + note.duration

const displayNotes = (part: OpenScorePart) => {
  const maxBlocks = 900
  if (part.notes.length <= maxBlocks) return part.notes
  const stride = Math.ceil(part.notes.length / maxBlocks)
  return part.notes.filter((_, index) => index % stride === 0)
}

const formatTime = (seconds: number) => {
  const safeSeconds = Number.isFinite(seconds) ? Math.max(0, seconds) : 0
  const minutes = Math.floor(safeSeconds / 60)
  const rest = Math.floor(safeSeconds % 60)
  return `${minutes}:${String(rest).padStart(2, '0')}`
}

watch(selectedWorkId, (workId) => {
  void loadSelectedWork(workId)
})

void loadWorks()
isWebAudioAvailable.value = Boolean(getAudioContextConstructor())

onBeforeUnmount(() => {
  stopPlayback()
  void audioContext?.close()
})
</script>

<style scoped>
.open-score-page {
  --score-blue: #2e82f0;
  --score-teal: #13a394;
  --score-ink: #101828;
  --score-muted: #667085;
  --score-line: #dbe3ee;
  --score-page: #eef3f8;

  height: calc(100vh - 48px);
  min-height: 0;
  padding: 18px 24px 28px;
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  gap: 12px;
  overflow: hidden;
  background: var(--score-page);
}

.page-head {
  width: min(100%, 1260px);
  min-height: 76px;
  padding: 16px 18px;
  border: 1px solid rgba(148, 163, 184, 0.22);
  border-radius: 14px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  box-sizing: border-box;
  background:
    linear-gradient(135deg, rgba(13, 25, 43, 0.98), rgba(20, 38, 63, 0.98) 58%, rgba(19, 69, 78, 0.92));
  box-shadow: 0 16px 34px rgba(15, 23, 42, 0.08);
}

.eyebrow {
  color: #92a4bf;
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

h1,
h2,
p {
  margin: 0;
}

h1 {
  color: #fff;
  font-size: 25px;
  font-weight: 700;
}

.source-strip,
.head-actions,
.study-mode-links,
.summary-actions,
.transport-panel,
.part-heading,
.detail-heading,
.listen-plan,
.source-detail {
  display: flex;
  align-items: center;
}

.source-strip {
  gap: 8px;
  flex-wrap: wrap;
}

.head-actions {
  flex-direction: column;
  align-items: flex-end;
  gap: 8px;
}

.study-mode-links {
  gap: 8px;
  justify-content: flex-end;
}

.study-mode-links span,
.study-mode-links a {
  height: 28px;
  padding: 0 10px;
  border-radius: 999px;
  display: inline-flex;
  align-items: center;
  font-size: 12px;
  font-weight: 700;
}

.study-mode-links span {
  color: #dbeafe;
  background: rgba(46, 130, 240, 0.24);
}

.study-mode-links a {
  border: 1px solid rgba(20, 184, 166, 0.36);
  color: #bff4eb;
  text-decoration: none;
  background: rgba(19, 163, 148, 0.15);
}

.source-strip a,
.summary-actions a {
  text-decoration: none;
}

.source-strip a {
  height: 30px;
  padding: 0 11px;
  border: 1px solid rgba(191, 219, 254, 0.28);
  border-radius: 999px;
  display: inline-flex;
  align-items: center;
  color: #dbeafe;
  font-size: 12px;
  font-weight: 650;
  background: rgba(255, 255, 255, 0.08);
}

.study-layout {
  width: min(100%, 1260px);
  flex: 1 1 auto;
  min-height: 0;
  display: grid;
  grid-template-columns: 300px minmax(0, 1fr);
  gap: 16px;
  overflow: hidden;
}

.path-pane,
.score-workbench,
.work-summary,
.transport-panel,
.study-guide,
.status-panel,
.mixer-panel,
.detail-panel,
.task-panel,
.source-panel {
  border: 1px solid var(--score-line);
  border-radius: 8px;
  background: #fff;
}

.path-pane {
  min-height: 0;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.pane-title,
.section-title {
  color: var(--score-ink);
  font-weight: 700;
}

.pane-title {
  padding: 13px 14px;
  border-bottom: 1px solid var(--score-line);
}

.kind-filter {
  padding: 9px 12px;
  border-bottom: 1px solid #edf2f8;
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
}

.kind-filter button {
  height: 27px;
  padding: 0 9px;
  border: 1px solid #d5dfeb;
  border-radius: 6px;
  color: #475467;
  font-size: 12px;
  font-weight: 650;
  background: #fff;
  cursor: pointer;
}

.kind-filter button.active {
  border-color: rgba(46, 130, 240, 0.3);
  color: var(--score-blue);
  background: #edf4ff;
}

.work-item {
  width: 100%;
  padding: 12px 14px;
  border: 0;
  border-bottom: 1px solid #edf2f8;
  display: grid;
  gap: 4px;
  background: #fff;
  color: inherit;
  text-align: left;
  cursor: pointer;
}

.work-item.active {
  background: #edf4ff;
  box-shadow: inset 3px 0 0 var(--score-blue);
}

.work-level,
.summary-kicker {
  color: var(--score-blue);
  font-size: 12px;
  font-weight: 700;
}

.work-name {
  color: var(--score-ink);
  font-weight: 700;
}

.work-meta,
.summary-main p,
.part-focus,
.score-workbench p,
.source-detail {
  color: var(--score-muted);
  font-size: 12px;
}

.score-workbench {
  min-width: 0;
  min-height: 0;
  padding: 14px;
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  gap: 12px;
  overflow: auto;
}

.work-summary {
  padding: 14px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.work-summary,
.transport-panel,
.study-guide,
.group-solo-panel,
.mixer-panel,
.analysis-grid,
.source-panel {
  flex: 0 0 auto;
}

.summary-main {
  min-width: 0;
  display: grid;
  gap: 5px;
}

h2 {
  color: var(--score-ink);
  font-size: 20px;
}

.summary-actions {
  flex: 0 0 auto;
  gap: 8px;
}

.summary-actions a {
  height: 32px;
  padding: 0 12px;
  border: 1px solid #cfe0f5;
  border-radius: 6px;
  display: inline-flex;
  align-items: center;
  color: var(--score-blue);
  font-size: 13px;
  font-weight: 650;
  background: #f8fbff;
}

.transport-panel {
  min-height: 42px;
  padding: 7px 12px;
  gap: 11px;
  flex-wrap: wrap;
}

.transport-panel :deep(.el-button.is-circle) {
  width: 31px;
  height: 31px;
  color: var(--score-blue);
}

.status-panel {
  padding: 14px 16px;
  color: var(--score-muted);
  font-size: 13px;
}

.status-panel.error {
  border-color: #fecaca;
  color: #b42318;
  background: #fff7f7;
}

.status-panel.warning {
  border-color: #fde68a;
  color: #92400e;
  background: #fffbeb;
}

.time-text {
  width: 42px;
  color: #475467;
  text-align: center;
  font-variant-numeric: tabular-nums;
}

.timeline-slider {
  flex: 1 1 auto;
  min-width: 160px;
}

.study-guide {
  padding: 10px 13px;
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
  background: #f8fbff;
}

.study-guide div {
  min-width: 0;
  display: grid;
  gap: 3px;
}

.study-guide span {
  color: var(--score-muted);
  font-size: 12px;
}

.study-guide strong {
  color: var(--score-ink);
  font-size: 13px;
  font-weight: 700;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.group-solo-panel {
  min-height: 38px;
  padding: 7px 12px;
  border: 1px solid var(--score-line);
  border-radius: 8px;
  display: flex;
  align-items: center;
  gap: 7px;
  flex-wrap: wrap;
  background: #fff;
}

.group-solo-panel span {
  color: var(--score-muted);
  font-size: 12px;
  font-weight: 700;
}

.group-solo-panel button {
  height: 26px;
  padding: 0 9px;
  border: 1px solid #d5dfeb;
  border-radius: 6px;
  color: #475467;
  font-size: 12px;
  font-weight: 650;
  background: #fff;
  cursor: pointer;
}

.group-solo-panel button.active {
  border-color: rgba(19, 163, 148, 0.32);
  color: #0f766e;
  background: #e8f7f5;
}

.mixer-panel,
.detail-panel,
.task-panel,
.source-panel {
  overflow: hidden;
}

.section-title {
  padding: 12px 13px;
  border-bottom: 1px solid var(--score-line);
}

.part-list {
  display: grid;
}

.part-row {
  min-height: 62px;
  padding: 9px 13px;
  border-bottom: 1px solid #edf2f8;
  display: grid;
  grid-template-columns: 46px 160px 120px minmax(180px, 1fr) 160px;
  align-items: center;
  gap: 12px;
  cursor: pointer;
}

.part-row:last-child {
  border-bottom: 0;
}

.part-row.active {
  background: #f0fbfa;
  box-shadow: inset 3px 0 0 var(--score-teal);
}

.part-row.muted {
  opacity: 0.58;
}

.part-heading {
  min-width: 0;
  align-items: baseline;
  gap: 8px;
}

.part-name,
.detail-heading span {
  color: var(--score-ink);
  font-weight: 700;
}

.part-role,
.detail-heading strong {
  color: var(--score-teal);
  font-size: 13px;
}

.part-focus {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.part-timeline {
  position: relative;
  height: 30px;
  border-radius: 4px;
  overflow: hidden;
  background: #f1f4f8;
}

.note-block {
  position: absolute;
  top: 7px;
  height: 16px;
  min-width: 3px;
  border-radius: 3px;
  background: #8aa0b8;
  opacity: 0.72;
}

.note-block.active {
  background: #38bdf8;
  opacity: 1;
  box-shadow: 0 0 0 1px rgba(14, 116, 144, 0.22);
}

.volume-slider {
  --el-slider-main-bg-color: var(--score-teal);
}

.analysis-grid {
  display: grid;
  grid-template-columns: minmax(280px, 0.8fr) minmax(0, 1.2fr);
  gap: 12px;
}

.part-detail {
  padding: 14px;
  display: grid;
  gap: 12px;
}

.detail-heading {
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
}

.listen-plan {
  flex-wrap: wrap;
  gap: 7px;
}

.listen-plan span {
  padding: 5px 8px;
  border-radius: 999px;
  color: #0f766e;
  font-size: 12px;
  background: #e5f7f4;
}

.task-list {
  display: grid;
}

.task-item {
  padding: 10px 13px;
  border-bottom: 1px solid #edf2f8;
  display: grid;
  grid-template-columns: 26px minmax(0, 1fr);
  gap: 10px;
  align-items: start;
}

.task-item:last-child {
  border-bottom: 0;
}

.task-item span {
  width: 22px;
  height: 22px;
  border-radius: 999px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: #fff;
  font-size: 12px;
  font-weight: 700;
  background: var(--score-blue);
}

.task-item p {
  color: var(--score-ink);
  font-size: 13px;
  line-height: 1.6;
}

.source-detail {
  align-items: flex-start;
  gap: 10px;
  padding: 11px 13px;
  line-height: 1.6;
}

.source-detail span + span {
  padding-left: 10px;
  border-left: 1px solid #edf2f8;
}

@media (max-width: 960px) {
  .open-score-page {
    overflow: auto;
  }

  .page-head,
  .work-summary,
  .transport-panel,
  .source-detail {
    align-items: flex-start;
    flex-direction: column;
  }

  .head-actions,
  .study-mode-links {
    align-items: flex-start;
    justify-content: flex-start;
  }

  .study-layout,
  .analysis-grid,
  .study-guide {
    grid-template-columns: 1fr;
    overflow: visible;
  }

  .part-row {
    grid-template-columns: 46px minmax(0, 1fr);
  }

  .part-focus,
  .part-timeline,
  .volume-slider {
    grid-column: 2;
    width: 100%;
  }

  .source-detail span + span {
    padding-left: 0;
    border-left: 0;
  }
}
</style>

