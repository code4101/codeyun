<template>
  <div class="open-score-page">
    <section class="page-head">
      <div>
        <div class="eyebrow">开源乐谱解析</div>
        <h1>内置谱例声部试听</h1>
      </div>
      <div class="source-strip">
        <a v-for="source in sources" :key="source.name" :href="source.url" target="_blank" rel="noreferrer">
          {{ source.name }}
        </a>
      </div>
    </section>

    <section class="study-layout">
      <aside class="path-pane">
        <div class="pane-title">入门顺序</div>
        <button
          v-for="work in works"
          :key="work.id"
          class="work-item"
          :class="{ active: work.id === selectedWorkId }"
          type="button"
          @click="selectWork(work.id)"
        >
          <span class="work-level">{{ work.level }}</span>
          <span class="work-name">{{ work.title }}</span>
          <span class="work-meta">{{ work.form }} · {{ work.parts.length }} 声部 · 已内置试听</span>
        </button>
      </aside>

      <main class="score-workbench">
        <section class="work-summary">
          <div class="summary-main">
            <div class="summary-kicker">{{ selectedWork.level }} · {{ selectedWork.form }}</div>
            <h2>{{ selectedWork.title }}</h2>
            <p>{{ selectedWork.reason }}</p>
          </div>
          <div class="summary-actions">
            <a :href="selectedWork.scoreUrl" target="_blank" rel="noreferrer">谱源</a>
            <a :href="selectedWork.referenceUrl" target="_blank" rel="noreferrer">对照资料</a>
          </div>
        </section>

        <section class="transport-panel">
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
        </section>

        <section class="mixer-panel">
          <div class="section-title">声部混音</div>
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
              <div class="part-focus">{{ part.focus }}</div>
              <div class="part-timeline">
                <span
                  v-for="(note, index) in part.notes"
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
            <div class="section-title">当前声部</div>
            <div class="part-detail">
              <div class="detail-heading">
                <span>{{ selectedPart.name }}</span>
                <strong>{{ selectedPart.role }}</strong>
              </div>
              <p>{{ selectedPart.detail }}</p>
              <div class="listen-plan">
                <span v-for="task in selectedPart.listen" :key="task">{{ task }}</span>
              </div>
            </div>
          </section>

          <section class="task-panel">
            <div class="section-title">赏析流程</div>
            <div class="task-list">
              <div v-for="(task, index) in selectedWork.tasks" :key="task" class="task-item">
                <span>{{ index + 1 }}</span>
                <p>{{ task }}</p>
              </div>
            </div>
          </section>
        </section>

        <section class="source-panel">
          <div class="section-title">已内置内容</div>
          <div class="source-detail">
            <span>{{ selectedWork.sourceNote }}</span>
            <span>页面用 Web Audio 直接合成各声部，适合拆解分工；外链谱源用于继续看完整总谱或原始资料。</span>
          </div>
        </section>
      </main>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { VideoPause, VideoPlay } from '@element-plus/icons-vue'

interface ScoreNote {
  pitch: string | null
  start: number
  duration: number
}

interface StudyPart {
  name: string
  role: string
  focus: string
  detail: string
  listen: string[]
  wave: OscillatorType
  notes: ScoreNote[]
}

interface StudyWork {
  id: string
  level: string
  title: string
  form: string
  reason: string
  scoreUrl: string
  referenceUrl: string
  sourceNote: string
  duration: number
  parts: StudyPart[]
  tasks: string[]
}

const n = (pitch: string | null, start: number, duration = 0.5): ScoreNote => ({ pitch, start, duration })

const sources = [
  { name: 'IMSLP', url: 'https://imslp.org/' },
  { name: 'Mutopia', url: 'https://www.mutopiaproject.org/' },
  { name: 'CPDL', url: 'https://www.cpdl.org/' },
  { name: 'MuseScore Studio', url: 'https://musescore.org/' },
]

const works: StudyWork[] = [
  {
    id: 'old-hundredth',
    level: '1 入门',
    title: 'Old Hundredth 四声部赞美诗',
    form: 'SATB',
    reason: '声部少、和声清楚，适合先听最高声部和最低声部，再打开内声部理解和弦。',
    scoreUrl: 'https://www.cpdl.org/wiki/index.php/Old_Hundredth',
    referenceUrl: 'https://www.mutopiaproject.org/',
    sourceNote: '公版赞美诗旋律教学样例；内置为简化四声部合成版，方便逐声部试听。',
    duration: 8,
    parts: [
      {
        name: 'Soprano',
        role: '主旋律',
        focus: '旋律走向',
        detail: '最高声部最容易被听见，先听它可以建立整段乐句轮廓。',
        listen: ['只听旋律', '找停顿', '观察重复'],
        wave: 'sine',
        notes: [n('G4', 0), n('G4', 0.5), n('A4', 1), n('B4', 1.5), n('C5', 2), n('B4', 2.5), n('A4', 3), n('G4', 3.5), n('C5', 4), n('B4', 4.5), n('A4', 5), n('G4', 5.5), n('A4', 6), n('B4', 6.5), n('G4', 7, 1)],
      },
      {
        name: 'Alto',
        role: '内声部',
        focus: '和声填充',
        detail: '女低声部通常不抢主旋律，但决定每个和弦的色彩是否完整。',
        listen: ['听共同音', '听级进', '观察是否贴近主旋律'],
        wave: 'triangle',
        notes: [n('E4', 0), n('D4', 0.5), n('E4', 1), n('G4', 1.5), n('G4', 2), n('G4', 2.5), n('F4', 3), n('E4', 3.5), n('G4', 4), n('G4', 4.5), n('F4', 5), n('E4', 5.5), n('F4', 6), n('G4', 6.5), n('E4', 7, 1)],
      },
      {
        name: 'Tenor',
        role: '内声部',
        focus: '连接变化',
        detail: '男高声部常负责平滑连接和声，让段落从一个和弦自然移动到下一个和弦。',
        listen: ['找级进', '看与低音距离', '听连接感'],
        wave: 'triangle',
        notes: [n('C4', 0), n('B3', 0.5), n('C4', 1), n('D4', 1.5), n('E4', 2), n('D4', 2.5), n('C4', 3), n('C4', 3.5), n('E4', 4), n('D4', 4.5), n('C4', 5), n('C4', 5.5), n('D4', 6), n('D4', 6.5), n('C4', 7, 1)],
      },
      {
        name: 'Bass',
        role: '低音根基',
        focus: '和弦方向',
        detail: '低音决定和声重心。只听低音时，能明显感到音乐在哪里稳定、在哪里前进。',
        listen: ['标根音', '听终止式', '判断稳定感'],
        wave: 'sawtooth',
        notes: [n('C3', 0), n('G2', 0.5), n('C3', 1), n('G2', 1.5), n('C3', 2), n('G2', 2.5), n('F2', 3), n('C3', 3.5), n('C3', 4), n('G2', 4.5), n('F2', 5), n('C3', 5.5), n('F2', 6), n('G2', 6.5), n('C3', 7, 1)],
      },
    ],
    tasks: ['先只听 Soprano，哼出旋律。', '再只听 Bass，判断和声重心。', '打开 Alto/Tenor，听中间声部如何填满和弦。'],
  },
  {
    id: 'string-quartet-demo',
    level: '2 初级',
    title: '弦乐四重奏教学片段',
    form: 'Violin I / II / Viola / Cello',
    reason: '四件同族乐器分工清楚，适合听主旋律、副旋律、内声部和低音如何轮换。',
    scoreUrl: 'https://imslp.org/wiki/Category:String_quartets',
    referenceUrl: 'https://imslp.org/wiki/String_Quartets%2C_Op.76_(Haydn%2C_Joseph)',
    sourceNote: '以公版弦乐四重奏学习场景为目标，内置一段原创教学片段模拟四重奏分工。',
    duration: 8,
    parts: [
      {
        name: 'Violin I',
        role: '主奏',
        focus: '主题呈示',
        detail: '第一小提琴负责最清楚的主题线，是四重奏中最先听的声部。',
        listen: ['找主题入口', '听重复变形', '听高音张力'],
        wave: 'sine',
        notes: [n('E5', 0), n('F5', 0.5), n('G5', 1), n('E5', 1.5), n('A5', 2), n('G5', 2.5), n('F5', 3), n('E5', 3.5), n('G5', 4), n('A5', 4.5), n('B5', 5), n('G5', 5.5), n('A5', 6), n('F5', 6.5), n('E5', 7, 1)],
      },
      {
        name: 'Violin II',
        role: '回应',
        focus: '副旋律',
        detail: '第二小提琴常在主旋律下方做回应或维持节奏型。',
        listen: ['找模仿', '听节奏型', '看是否抢旋律'],
        wave: 'triangle',
        notes: [n('C5', 0, 1), n('D5', 1, 1), n('E5', 2, 1), n('C5', 3, 1), n('E5', 4, 1), n('F5', 5, 1), n('D5', 6, 1), n('C5', 7, 1)],
      },
      {
        name: 'Viola',
        role: '中层胶水',
        focus: '和声厚度',
        detail: '中提琴把高音旋律和低音连接起来，常常决定整体是否饱满。',
        listen: ['听长音', '找切分', '观察织体变化'],
        wave: 'triangle',
        notes: [n('G3', 0, 0.75), n('A3', 0.75, 0.75), n('B3', 1.5, 0.75), n('C4', 2.25, 0.75), n('B3', 3, 1), n('C4', 4, 0.75), n('D4', 4.75, 0.75), n('B3', 5.5, 0.75), n('G3', 6.25, 1.75)],
      },
      {
        name: 'Cello',
        role: '低音与旋律',
        focus: '低音线',
        detail: '大提琴既能做低音，也能接过旋律。这里先听它如何稳住和声。',
        listen: ['标低音进行', '听旋律交接', '看长弓感'],
        wave: 'sawtooth',
        notes: [n('C3', 0, 1), n('G2', 1, 1), n('A2', 2, 1), n('E2', 3, 1), n('F2', 4, 1), n('C3', 5, 1), n('G2', 6, 1), n('C3', 7, 1)],
      },
    ],
    tasks: ['先只听 Violin I，确认主题。', '加入 Violin II，听回应和节奏支撑。', '最后打开 Viola 和 Cello，听完整四重奏厚度。'],
  },
  {
    id: 'piano-trio-demo',
    level: '3 中级',
    title: '钢琴三重奏教学片段',
    form: 'Piano / Violin / Cello',
    reason: '钢琴负责和声与织体，弦乐负责旋律对话，能看到伴奏并不是简单垫底。',
    scoreUrl: 'https://imslp.org/wiki/Category:Piano_trios',
    referenceUrl: 'https://imslp.org/wiki/Piano_Trio_No.4%2C_Op.90_(Dvo%C5%99%C3%A1k%2C_Anton%C3%ADn)',
    sourceNote: '以公版钢琴三重奏学习场景为目标，内置简化教学片段用于声部分离试听。',
    duration: 8,
    parts: [
      {
        name: 'Piano RH',
        role: '高声部织体',
        focus: '分解和弦',
        detail: '钢琴右手提供亮部音型，也可能与小提琴争夺旋律空间。',
        listen: ['看音型重复', '找和弦外音', '听亮度变化'],
        wave: 'triangle',
        notes: [n('C5', 0, 0.25), n('E5', 0.25, 0.25), n('G5', 0.5, 0.25), n('E5', 0.75, 0.25), n('D5', 1, 0.25), n('F5', 1.25, 0.25), n('A5', 1.5, 0.25), n('F5', 1.75, 0.25), n('E5', 2, 0.25), n('G5', 2.25, 0.25), n('B5', 2.5, 0.25), n('G5', 2.75, 0.25), n('C5', 3, 0.25), n('E5', 3.25, 0.25), n('G5', 3.5, 0.25), n('E5', 3.75, 0.25), n('F5', 4, 0.25), n('A5', 4.25, 0.25), n('C6', 4.5, 0.25), n('A5', 4.75, 0.25), n('E5', 5, 0.25), n('G5', 5.25, 0.25), n('B5', 5.5, 0.25), n('G5', 5.75, 0.25), n('D5', 6, 0.25), n('F5', 6.25, 0.25), n('A5', 6.5, 0.25), n('F5', 6.75, 0.25), n('C5', 7, 1)],
      },
      {
        name: 'Piano LH',
        role: '低音与节奏',
        focus: '低音框架',
        detail: '左手给出根音和节奏脉冲，决定钢琴声部是否站得稳。',
        listen: ['标根音', '听重拍', '找低音动机'],
        wave: 'sawtooth',
        notes: [n('C3', 0, 1), n('D3', 1, 1), n('E3', 2, 1), n('C3', 3, 1), n('F2', 4, 1), n('E3', 5, 1), n('D3', 6, 1), n('C3', 7, 1)],
      },
      {
        name: 'Violin',
        role: '上方旋律',
        focus: '歌唱性',
        detail: '小提琴承担长线条，和钢琴右手形成亮部对话。',
        listen: ['找长线条', '听呼吸', '看与钢琴右手关系'],
        wave: 'sine',
        notes: [n('G5', 0, 1), n('A5', 1, 0.5), n('G5', 1.5, 0.5), n('E5', 2, 1), n('D5', 3, 1), n('F5', 4, 1), n('G5', 5, 0.5), n('F5', 5.5, 0.5), n('E5', 6, 1), n('C5', 7, 1)],
      },
      {
        name: 'Cello',
        role: '低音/对唱',
        focus: '旋律交接',
        detail: '大提琴不只是低音，它会和小提琴形成一高一低的对唱。',
        listen: ['听对答', '看低音支撑', '找主题再现'],
        wave: 'triangle',
        notes: [n('C3', 0, 1.5), n('G2', 1.5, 0.5), n('A2', 2, 1.5), n('E3', 3.5, 0.5), n('F3', 4, 1), n('G3', 5, 1), n('E3', 6, 1), n('C3', 7, 1)],
      },
    ],
    tasks: ['先听 Piano LH，建立低音骨架。', '加入 Piano RH，听伴奏织体。', '再加入 Violin 和 Cello，听旋律对话。'],
  },
  {
    id: 'orchestra-demo',
    level: '4 进阶',
    title: '小型管弦乐教学片段',
    form: 'Strings / Woodwinds / Brass / Percussion',
    reason: '乐器组变多后，重点是听乐器组如何分层、加厚和制造高潮。',
    scoreUrl: 'https://imslp.org/',
    referenceUrl: 'https://musescore.com/artist/public_domain-152674',
    sourceNote: '内置为教学用小型管弦乐分组片段，帮助先理解乐器组层次。',
    duration: 8,
    parts: [
      {
        name: 'Strings',
        role: '主体织体',
        focus: '持续层',
        detail: '弦乐保持连续性，是管弦乐里最常见的铺底和主体织体。',
        listen: ['分辨主旋律/铺底', '看齐奏或分声部', '听厚度变化'],
        wave: 'triangle',
        notes: [n('C4', 0, 1), n('E4', 1, 1), n('G4', 2, 1), n('E4', 3, 1), n('F4', 4, 1), n('A4', 5, 1), n('G4', 6, 1), n('C5', 7, 1)],
      },
      {
        name: 'Woodwinds',
        role: '色彩与回应',
        focus: '音色提示',
        detail: '木管常在主题之间做短句回应，给段落加颜色。',
        listen: ['找短句回应', '听音色变化', '看是否重复主题'],
        wave: 'sine',
        notes: [n(null, 0, 1), n('G5', 1, 0.5), n('E5', 1.5, 0.5), n(null, 2, 1), n('A5', 3, 0.5), n('G5', 3.5, 0.5), n(null, 4, 1), n('C6', 5, 0.5), n('B5', 5.5, 0.5), n('G5', 6.5, 0.5), n('C6', 7, 1)],
      },
      {
        name: 'Brass',
        role: '力量与强调',
        focus: '高潮支点',
        detail: '铜管不一定一直出现，通常在关键位置加重结构。',
        listen: ['标进入时刻', '听力度变化', '看关键处出现'],
        wave: 'square',
        notes: [n(null, 0, 2), n('C4', 2, 1), n('G3', 3, 1), n(null, 4, 1), n('F3', 5, 1), n('G3', 6, 1), n('C4', 7, 1)],
      },
      {
        name: 'Percussion',
        role: '节奏与戏剧性',
        focus: '结构标点',
        detail: '打击乐标记重拍和段落边界，让高潮更明确。',
        listen: ['找重音', '看段落边界', '听紧张感'],
        wave: 'square',
        notes: [n('C2', 0, 0.12), n('C2', 1, 0.12), n('C2', 2, 0.12), n('C2', 3, 0.12), n('C2', 4, 0.12), n('C2', 5, 0.12), n('C2', 6, 0.12), n('C2', 7, 0.5)],
      },
    ],
    tasks: ['先只听 Strings，建立主体织体。', '加入 Woodwinds，听颜色和回应。', '最后打开 Brass/Percussion，观察高潮如何被加重。'],
  },
]

const selectedWorkId = ref(works[0].id)
const selectedPartName = ref(works[0].parts[0].name)
const currentTime = ref(0)
const isPlaying = ref(false)
const enabledParts = reactive<Record<string, boolean>>({})
const partVolumes = reactive<Record<string, number>>({})

let audioContext: AudioContext | null = null
let playbackStartedAt = 0
let playbackBaseTime = 0
let animationFrame: number | null = null
const scheduledNodes: OscillatorNode[] = []

const selectedWork = computed(() => works.find((work) => work.id === selectedWorkId.value) || works[0])
const selectedPart = computed(() => selectedWork.value.parts.find((part) => part.name === selectedPartName.value) || selectedWork.value.parts[0])

const selectWork = (workId: string) => {
  stopPlayback({ resetTime: true })
  selectedWorkId.value = workId
}

const setupPartState = () => {
  for (const key of Object.keys(enabledParts)) delete enabledParts[key]
  for (const key of Object.keys(partVolumes)) delete partVolumes[key]
  for (const part of selectedWork.value.parts) {
    enabledParts[part.name] = true
    partVolumes[part.name] = part.name.includes('Percussion') ? 0.35 : 0.65
  }
  selectedPartName.value = selectedWork.value.parts[0].name
}

const noteToFrequency = (pitch: string) => {
  const match = /^([A-G])(#|b)?(\d)$/.exec(pitch)
  if (!match) return 440
  const [, rawName, accidental = '', rawOctave] = match
  const semitoneMap: Record<string, number> = { C: 0, D: 2, E: 4, F: 5, G: 7, A: 9, B: 11 }
  const accidentalOffset = accidental === '#' ? 1 : accidental === 'b' ? -1 : 0
  const midi = (Number(rawOctave) + 1) * 12 + semitoneMap[rawName] + accidentalOffset
  return 440 * 2 ** ((midi - 69) / 12)
}

const schedulePart = (part: StudyPart, startOffset: number) => {
  if (!audioContext || !enabledParts[part.name]) return
  const now = audioContext.currentTime
  const volume = partVolumes[part.name] ?? 0.6
  for (const note of part.notes) {
    if (!note.pitch) continue
    const noteEnd = note.start + note.duration
    if (noteEnd <= startOffset) continue
    const startAt = now + Math.max(0, note.start - startOffset)
    const stopAt = now + Math.max(0.05, noteEnd - startOffset)
    const oscillator = audioContext.createOscillator()
    const gain = audioContext.createGain()
    oscillator.type = part.wave
    oscillator.frequency.value = noteToFrequency(note.pitch)
    gain.gain.setValueAtTime(0.0001, startAt)
    gain.gain.exponentialRampToValueAtTime(Math.max(0.0001, volume * 0.12), startAt + 0.02)
    gain.gain.exponentialRampToValueAtTime(0.0001, Math.max(startAt + 0.04, stopAt - 0.03))
    oscillator.connect(gain)
    gain.connect(audioContext.destination)
    oscillator.start(startAt)
    oscillator.stop(stopAt)
    scheduledNodes.push(oscillator)
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
}

const tickPlayback = () => {
  if (!audioContext || !isPlaying.value) return
  const elapsed = audioContext.currentTime - playbackStartedAt
  currentTime.value = Math.min(selectedWork.value.duration, playbackBaseTime + elapsed)
  if (currentTime.value >= selectedWork.value.duration) {
    stopPlayback({ resetTime: true })
    return
  }
  animationFrame = window.requestAnimationFrame(tickPlayback)
}

const startPlayback = async () => {
  if (!audioContext) {
    audioContext = new AudioContext()
  }
  if (audioContext.state === 'suspended') {
    await audioContext.resume()
  }
  stopScheduledNodes()
  playbackBaseTime = Math.min(currentTime.value, selectedWork.value.duration)
  playbackStartedAt = audioContext.currentTime
  for (const part of selectedWork.value.parts) {
    schedulePart(part, playbackBaseTime)
  }
  isPlaying.value = true
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
  restartIfPlaying()
}

const soloSelectedPart = () => {
  for (const part of selectedWork.value.parts) {
    enabledParts[part.name] = part.name === selectedPartName.value
  }
  restartIfPlaying()
}

const isNoteActive = (note: ScoreNote) => currentTime.value >= note.start && currentTime.value < note.start + note.duration

const formatTime = (seconds: number) => {
  const safeSeconds = Number.isFinite(seconds) ? Math.max(0, seconds) : 0
  const minutes = Math.floor(safeSeconds / 60)
  const rest = Math.floor(safeSeconds % 60)
  return `${minutes}:${String(rest).padStart(2, '0')}`
}

watch(selectedWorkId, setupPartState, { immediate: true })

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
}

.transport-panel :deep(.el-button.is-circle) {
  width: 31px;
  height: 31px;
  color: var(--score-blue);
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

  .study-layout,
  .analysis-grid {
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
