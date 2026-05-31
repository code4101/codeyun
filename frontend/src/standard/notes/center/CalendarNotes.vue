<template>
  <div class="calendar-notes-layout">
    <div class="filter-section">
      <div class="backend-filter-panel">
        <div class="backend-filter-header">
          <div class="backend-filter-title">后端筛选</div>
          <div class="backend-filter-help">{{ backendFilterHelp }}</div>
        </div>

        <div class="backend-filter-toolbar">
          <div class="backend-filter-controls">
            <el-segmented
              v-model="calendarScale"
              :options="calendarScaleOptions"
              size="small"
              @change="onScaleChange"
            />
            <el-date-picker
              v-if="calendarScale === 'month'"
              v-model="currentMonth"
              type="month"
              placeholder="选择月份"
              :clearable="false"
              @change="onPeriodChange"
            />
            <el-date-picker
              v-else-if="calendarScale === 'year'"
              v-model="currentMonth"
              type="year"
              placeholder="选择年份"
              :clearable="false"
              @change="onPeriodChange"
            />
            <el-select
              v-else-if="calendarScale === 'volume'"
              class="volume-picker"
              :model-value="currentVolume.id"
              placeholder="选择分卷"
              @change="onVolumeSelectChange"
            >
              <el-option
                v-for="volume in calendarVolumeOptions"
                :key="volume.id"
                :label="volume.label"
                :value="volume.id"
              />
            </el-select>
            <div v-if="calendarScale !== 'era'" class="period-nav-buttons">
              <el-button
                v-if="calendarScale !== 'year'"
                @click="prevYearPeriod"
                :icon="DArrowLeft"
                circle
                title="上一年"
              />
              <el-button @click="prevPeriod" :icon="ArrowLeft" circle :title="prevPeriodTitle" />
              <el-button @click="nextPeriod" :icon="ArrowRight" circle :title="nextPeriodTitle" />
              <el-button
                v-if="calendarScale !== 'year'"
                @click="nextYearPeriod"
                :icon="DArrowRight"
                circle
                title="下一年"
              />
            </div>
            <el-button v-if="calendarScale === 'month'" @click="goToToday">今天</el-button>
            <label
              v-if="calendarScale === 'year' || calendarScale === 'volume' || calendarScale === 'era'"
              class="period-limit-control"
              :title="monthVisibleLimitTitle"
            >
              <span>{{ activeVisibleLimitLabel }}</span>
              <el-input-number
                :model-value="activeMonthVisibleLimit"
                :min="MONTH_VISIBLE_LIMIT_MIN"
                :max="MONTH_VISIBLE_LIMIT_MAX"
                :step="1"
                step-strictly
                controls-position="right"
                size="small"
                @change="setActiveMonthVisibleLimit"
              />
              <span>条</span>
            </label>
          </div>

          <div class="backend-filter-tags">
            <el-tag type="success">加载: {{ formatDateShort(periodStartTs) }} - {{ formatDateShort(periodEndTs - 1) }}</el-tag>
          </div>
        </div>
      </div>
    </div>

    <div v-if="showFrontFilter" class="filter-section front-filter-section">
      <NoteProgramBar
        v-model="viewProgram"
        title="前端筛选"
        :help-text="frontFilterHelp"
        hint-text=""
        apply-text="即时生效"
        reset-text="默认配置"
        @apply="applyViewProgram"
        @reset="resetViewProgram"
      />
    </div>

    <NoteSplitView
      class="calendar-workspace"
      :class="{ 'calendar-workspace--calendar-only': !currentNoteId }"
      :top-height="calendarPaneHeight"
      :show-editor="Boolean(currentNoteId)"
      empty-description="请在日历中选择一个节点"
      editor-mode="flow"
      :editor-min-height="currentNoteId ? 400 : 0"
      @resize-start="startResizing"
    >
      <template #main>
        <div v-if="calendarScale === 'month'" class="calendar-container" v-loading="loading">
          <div class="weekday-header">
            <div v-for="day in weekDays" :key="day" class="weekday-cell">{{ day }}</div>
          </div>

          <div class="days-grid" :style="{ gridTemplateRows }">
            <div
              v-for="day in gridDays"
              :key="day.dateStr"
              class="day-cell"
              :class="{ 'is-outside': !day.isCurrentMonth }"
              @contextmenu.prevent.stop="openDayContextMenu($event, day.date)"
            >
              <div class="day-number" :class="{ 'is-today': isToday(day.date) }">
                <div class="day-left">
                  <span class="solar-day" :class="{ 'is-rest-text': day.isRest }">{{ day.dayNum }}</span>
                  <span
                    v-if="showCodexWorkload && shouldShowCodexHours(getCodexSecondsForDate(day.date))"
                    class="codex-hours"
                    :title="getCodexHoursTitle(getCodexSecondsForDate(day.date))"
                  >
                    {{ formatCodexHours(getCodexSecondsForDate(day.date)) }}
                  </span>
                  <span v-if="isToday(day.date)" class="today-tag">今天</span>
                  <span v-if="day.holidayName" class="holiday-marker" :class="{ 'is-rest': day.isRest === true, 'is-work': day.isRest === false }">
                    {{ day.isRest === true ? '休' : '班' }}
                  </span>
                  <el-button v-if="allowCreate" class="create-note-btn" size="small" text circle :icon="Plus" title="新建节点" @click.stop="createNoteForDay(day.date)" />
                </div>
                <div class="day-right">
                  <span class="lunar-info" :class="{ 'is-festival': day.festival || day.jieQi }">
                    {{ day.festival || day.jieQi || day.lunarDay }}
                  </span>
                </div>
              </div>
              <div class="day-content">
                <div
                  v-for="note in getNotesForDay(day.date)"
                  :key="note.id"
                  class="note-item"
                  :style="getNoteStyle(note)"
                  @click.stop="openNote(note)"
                >
                  <span v-if="useSplitNoteTitle(note)" class="note-title note-title--split" :style="getNoteTitleStyle(note)">
                    <span class="note-title-layer" :style="getNoteSplitLayerStyle(note, 'fill')">
                      <NoteFormBadge :form="note.note_form" compact />
                      <span class="note-title-text" :style="getNoteTitleTextStyle(note, true, true)">{{ note.title }}</span>
                    </span>
                    <span class="note-title-layer" :style="getNoteSplitLayerStyle(note, 'empty')">
                      <NoteFormBadge :form="note.note_form" compact />
                      <span class="note-title-text" :style="getNoteTitleTextStyle(note, true, true)">{{ note.title }}</span>
                    </span>
                  </span>
                  <span v-else class="note-title" :style="getNoteTitleStyle(note)">
                    <NoteFormBadge :form="note.note_form" compact />
                    <span class="note-title-text" :style="getNoteTitleTextStyle(note)">{{ note.title }}</span>
                  </span>
                </div>
              </div>
            </div>
          </div>
        </div>

        <div v-else-if="calendarScale === 'year'" class="year-container" v-loading="loading">
          <div class="year-summary">
            <div class="year-summary-heading">
              <span class="year-summary-title">{{ currentYear }}年</span>
              <input
                v-if="editingYearTitleKey === currentYearTitleKey"
                :ref="setYearTitleInputRef"
                v-model="yearTitleDraft"
                class="year-title-input"
                placeholder="年标题"
                @click.stop
                @dblclick.stop
                @keydown.esc.prevent.stop="cancelYearTitleEdit"
                @keydown.enter.prevent.stop="saveYearTitleEdit"
                @blur="saveYearTitleEdit"
              />
              <button
                v-else
                type="button"
                class="year-title-text"
                :class="{ 'is-empty': !getYearTitle(currentYearTitleKey) }"
                :title="getYearTitle(currentYearTitleKey) || undefined"
                @dblclick.stop="startYearTitleEdit(currentYearTitleKey)"
              >
                <span v-if="getYearTitle(currentYearTitleKey)">{{ getYearTitle(currentYearTitleKey) }}</span>
                <span v-else class="year-title-placeholder">写标题</span>
              </button>
            </div>
            <div class="year-summary-meta">
              {{ yearVisibleNoteCount }} 条节点
              <span v-if="yearHiddenNoteCount > 0"> · 折叠 {{ yearHiddenNoteCount }} 条</span>
            </div>
          </div>

          <div class="year-month-table">
            <div
              v-for="month in yearMonthSummaries"
              :key="month.monthIndex"
              class="year-month-row"
              :class="{ 'is-current-month': month.isCurrentMonth }"
            >
              <div class="year-month-label">
                <div class="year-month-heading">
                  <button
                    type="button"
                    class="year-month-name-button"
                    :title="`${month.monthLabel}，点击进入月视图`"
                    @click="openMonthFromYear(month.monthIndex)"
                  >
                    {{ month.monthLabel }}
                  </button>
                  <span
                    v-if="showCodexWorkload && shouldShowCodexHours(month.codexSeconds)"
                    class="codex-hours year-month-codex-hours"
                    :title="getCodexHoursTitle(month.codexSeconds)"
                  >
                    {{ formatCodexHours(month.codexSeconds) }}
                  </span>
                </div>

                <textarea
                  v-if="editingYearMonthMemoKey === month.memoKey"
                  :ref="setYearMonthMemoTextareaRef"
                  v-model="yearMonthMemoDraft"
                  class="year-month-memo-input"
                  rows="3"
                  placeholder="阅读日记、总结"
                  @click.stop
                  @dblclick.stop
                  @keydown.esc.prevent.stop="cancelYearMonthMemoEdit"
                  @keydown.ctrl.enter.prevent.stop="saveYearMonthMemoEdit"
                  @blur="saveYearMonthMemoEdit"
                />
                <button
                  v-else
                  type="button"
                  class="year-month-memo"
                  :class="{ 'is-empty': !getYearMonthMemo(month.memoKey) }"
                  :title="getYearMonthMemoTitle(month.memoKey)"
                  @click.stop
                  @dblclick.stop="startYearMonthMemoEdit(month.memoKey)"
                >
                  <span v-if="getYearMonthMemo(month.memoKey)">{{ getYearMonthMemo(month.memoKey) }}</span>
                  <span v-else class="year-month-memo-placeholder">写总结</span>
                </button>
              </div>

              <div class="year-month-events" :class="{ 'is-sparse': month.isSparse }">
                <button
                  v-for="(note, noteIndex) in month.visibleNotes"
                  :key="note.id"
                  type="button"
                  class="year-event"
                  :style="getYearEventStyle(note)"
                  :title="getYearEventTitle(note)"
                  @click.stop="openNote(note)"
                >
                  <span v-if="shouldShowYearEventDay(month.visibleNotes, noteIndex)" class="year-event-day">
                    {{ formatYearEventDay(note) }}
                  </span>
                  <span class="year-event-title">{{ formatYearEventTitle(note) }}</span>
                  <span v-if="getNoteProgressPercent(note) !== null" class="year-event-progress">
                    <span :style="getYearEventProgressStyle(note)" />
                  </span>
                </button>

                <button
                  v-if="month.hiddenCount > 0"
                  type="button"
                  class="year-more"
                  :title="`进入${month.monthLabel}查看全部节点`"
                  @click="openMonthFromYear(month.monthIndex)"
                >
                  +{{ month.hiddenCount }}
                </button>

              </div>
            </div>
          </div>
        </div>

        <div v-else-if="calendarScale === 'volume'" class="volume-container" v-loading="loading">
          <div class="year-summary">
            <div class="year-summary-title">{{ currentVolume.label }}</div>
            <div class="year-summary-meta">
              {{ formatDateFull(volumeStartTs) }} - {{ formatDateFull(volumeEndTs - 1) }}
            </div>
          </div>

          <div class="volume-year-table">
            <div
              v-for="year in volumeYearSummaries"
              :key="year.year"
              class="volume-year-row"
              :class="{ 'is-current-year': year.isCurrentYear }"
            >
              <div class="volume-year-label">
                <button
                  type="button"
                  class="volume-year-name-button"
                  :title="`${year.periodLabel}，点击进入年视图`"
                  @click="openYearFromVolume(year.year)"
                >
                  {{ year.yearLabel }}
                </button>
                <input
                  v-if="editingYearTitleKey === year.titleKey"
                  :ref="setYearTitleInputRef"
                  v-model="yearTitleDraft"
                  class="volume-year-title-input"
                  placeholder="年标题"
                  @click.stop
                  @dblclick.stop
                  @keydown.esc.prevent.stop="cancelYearTitleEdit"
                  @keydown.enter.prevent.stop="saveYearTitleEdit"
                  @blur="saveYearTitleEdit"
                />
                <button
                  v-else
                  type="button"
                  class="volume-year-title-text"
                  :class="{ 'is-empty': !year.title }"
                  :title="getYearTitle(year.titleKey) || undefined"
                  @dblclick.stop="startYearTitleEdit(year.titleKey)"
                >
                  <span v-if="year.title">{{ year.title }}</span>
                  <span v-else class="year-title-placeholder">写标题</span>
                </button>
              </div>

              <div class="volume-year-months">
                <div
                  v-for="month in year.months"
                  :key="month.monthIndex"
                  class="volume-month-group"
                >
                  <button
                    type="button"
                    class="volume-month-label"
                    :title="`${year.year}年${month.monthLabel}，点击进入月视图`"
                    @click="openMonthFromVolume(year.year, month.monthIndex)"
                  >
                    {{ month.monthLabel }}
                  </button>
                  <div class="volume-month-events">
                    <button
                      v-for="note in month.visibleNotes"
                      :key="note.id"
                      type="button"
                      class="year-event volume-event"
                      :style="getYearEventStyle(note)"
                      :title="getYearEventTitle(note)"
                      @click.stop="openNote(note)"
                    >
                      <span class="year-event-title">{{ formatYearEventTitle(note) }}</span>
                      <span v-if="getNoteProgressPercent(note) !== null" class="year-event-progress">
                        <span :style="getYearEventProgressStyle(note)" />
                      </span>
                    </button>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>

        <div v-else class="era-container" v-loading="loading">
          <div class="year-summary">
            <div class="year-summary-title">纪</div>
            <div class="year-summary-meta">
              {{ formatDateFull(eraStartTs) }} - {{ formatDateFull(eraEndTs - 1) }}
            </div>
          </div>

          <div class="era-volume-table">
            <div
              v-for="volume in eraVolumeSummaries"
              :key="volume.id"
              class="era-volume-row"
            >
              <div class="era-volume-label">
                <button
                  type="button"
                  class="era-volume-name-button"
                  :title="`${volume.label}，点击进入卷视图`"
                  @click="openVolumeFromEra(volume.id)"
                >
                  <span
                    v-for="(segment, index) in formatVolumeLabelSegments(volume.label)"
                    :key="index"
                    class="era-volume-name-segment"
                  >
                    {{ segment }}
                  </span>
                </button>
              </div>

              <div class="era-year-list">
                <div
                  v-for="year in volume.years"
                  :key="year.year"
                  class="era-year-item"
                >
                  <button
                    type="button"
                    class="era-year-button"
                    :title="`${year.periodLabel}，点击进入年视图`"
                    @click="openYearFromVolume(year.year)"
                  >
                    {{ year.yearLabel }}
                  </button>
                  <div class="era-year-content">
                    <span v-if="year.title" class="era-year-title">{{ year.title }}</span>
                    <button
                      v-for="note in year.visibleNotes"
                      :key="note.id"
                      type="button"
                      class="year-event era-event"
                      :style="getYearEventStyle(note)"
                      :title="getYearEventTitle(note)"
                      @click.stop="openNote(note)"
                    >
                      <span class="year-event-title">{{ formatYearEventTitle(note) }}</span>
                      <span v-if="getNoteProgressPercent(note) !== null" class="year-event-progress">
                        <span :style="getYearEventProgressStyle(note)" />
                      </span>
                    </button>
                    <button
                      v-if="year.hiddenCount > 0"
                      type="button"
                      class="year-more"
                      :title="`进入${year.year}年查看全部节点`"
                      @click="openYearFromVolume(year.year)"
                    >
                      +{{ year.hiddenCount }}
                    </button>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </template>

      <template #editor>
        <NoteDetailPanel
          :noteId="currentNoteId"
          editor-layout="flow"
          @update="handleNoteUpdate"
          @delete="handleNoteDelete"
          @create="handleNoteCreate"
        />
      </template>
    </NoteSplitView>

    <div
      v-if="allowCreate && dayContextMenu.visible"
      class="day-context-menu"
      :style="{ left: `${dayContextMenu.x}px`, top: `${dayContextMenu.y}px` }"
      @click.stop
    >
      <button type="button" class="day-context-menu-item" @click="createNoteFromContextMenu">
        新建节点
      </button>
      <button
        type="button"
        class="day-context-menu-item"
        :disabled="codexDiaryImporting"
        @click="importCodexDiaryFromContextMenu"
      >
        添加 Codex 总结日记
      </button>
    </div>

  </div>
</template>

<script setup lang="ts">
import { ref, computed, nextTick, onMounted, onBeforeUnmount, watch, toRaw } from 'vue';
import { useRouter } from 'vue-router';
import NoteSplitView from '@/components/NoteSplitView.vue';
import NoteProgramBar from '@/components/NoteProgramBar.vue';
import {
  useNoteStore,
  type NoteNode,
  type NoteProgramRule,
  type CodexDiaryImportRunResponse,
  applyNoteProgramChannelLocally,
  buildScanNoteProgramRequest,
  cloneNoteProgramChannel,
  createFixedRangeProgram,
  createIncludeAllProgram,
  normalizeNoteProgramRule,
  normalizeNoteProgramChannel,
  startCodexDiaryImportRun,
  fetchCodexDiaryImportRun,
  fetchCalendarYearMonthMemos,
  noteKey,
  saveCalendarYearMonthMemos
} from '@/api/notes';
import { useUserStore } from '@/store/userStore';
import { fetchCodexWorkloadForEntry, type CodexWorkloadResponse, type CodexWorkloadTurn } from '@/api/codexSessions';
import { taskStore, type Device } from '@/store/taskStore';
import { ArrowLeft, ArrowRight, DArrowLeft, DArrowRight, Plus } from '@element-plus/icons-vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import { Solar, HolidayUtil } from 'lunar-javascript';
import { getNodeDisplayStyle } from '@/utils/nodeConfig';
import NoteDetailPanel from '@/components/NoteDetailPanel.vue';
import NoteFormBadge from '@/components/NoteFormBadge.vue';
import { formatNoteDateShort } from '@/utils/noteDate';
import { getNoteWeightScaleFactor, NOTE_WEIGHT_DEFAULT } from '@/utils/noteWeight';
import { useResizablePane } from '@/utils/useResizablePane';
import { resolveCompletionProgressFillRatio } from '@/utils/noteProgress';

const router = useRouter();
const noteStore = useNoteStore();
const userStore = useUserStore();
const props = defineProps<{
  tabId: string;
  dataFilterRules?: NoteProgramRule[];
  fixedViewFilterRules?: NoteProgramRule[];
  showFrontFilter?: boolean;
  allowCreate?: boolean;
  showCodexWorkload?: boolean;
  splitPaneStorageKey?: string;
}>();

const showFrontFilter = computed(() => props.showFrontFilter !== false);
const allowCreate = computed(() => props.allowCreate !== false);
const showCodexWorkload = computed(() => props.showCodexWorkload !== false);

const session = computed(() => noteStore.getTabSession(props.tabId));

type CalendarScale = 'month' | 'year' | 'volume' | 'era';
type YearMonthMemoMap = Record<string, string>;
type YearTitleMap = Record<string, string>;
type CodexWorkloadDaySeconds = Record<string, number>;
type CodexWorkloadDeviceStatsCache = {
  cachedThrough: string;
  days: CodexWorkloadDaySeconds;
  updatedAt: number;
};
type CodexWorkloadStatsCache = {
  version: number;
  timezone: string;
  devices: Record<string, CodexWorkloadDeviceStatsCache>;
};
type CalendarVolumeDefinition = {
  id: string;
  label: string;
  start: [number, number, number];
  endExclusive: [number, number, number];
};

const YEAR_VIEW_MIN_PANE_HEIGHT = 900;
const YEAR_MONTH_VISIBLE_LIMIT_DEFAULT = 15;
const VOLUME_MONTH_VISIBLE_LIMIT_DEFAULT = 4;
const ERA_YEAR_VISIBLE_LIMIT_DEFAULT = 4;
const MONTH_VISIBLE_LIMIT_MIN = 1;
const MONTH_VISIBLE_LIMIT_MAX = 24;
const YEAR_MONTH_SPARSE_TOTAL_LIMIT = 4;
const CALENDAR_QUERY_LIMIT_DEFAULT = 5000;
const CALENDAR_VOLUME_QUERY_LIMIT = 10000;
const CALENDAR_ERA_QUERY_LIMIT = 30000;
const MONTH_WEEK_ROW_MIN_HEIGHT = 108;
const MONTH_WEEK_ROW_UNIT_HEIGHT = 18;
const MONTH_WEEK_ROW_MAX_HEIGHT = 240;
const CODEX_WORKLOAD_DEVICE_CACHE_MS = 60_000;
const CODEX_WORKLOAD_HOUR_SECONDS = 3600;
const CODEX_WORKLOAD_STATS_CACHE_KEY = 'codeyun:notes:calendar:codex-workload-days:v2';
const CODEX_WORKLOAD_STATS_CACHE_VERSION = 2;

const CALENDAR_VOLUME_DEFINITIONS: CalendarVolumeDefinition[] = [
  { id: 'v1', label: '卷一 开辟鸿蒙~2008.7', start: [1992, 1, 1], endExclusive: [2008, 8, 1] },
  { id: 'v2', label: '卷二 高中学涯~2011.7', start: [2008, 8, 1], endExclusive: [2011, 8, 1] },
  { id: 'v3', label: '卷三 大学编程~2015.7，CODE', start: [2011, 8, 1], endExclusive: [2015, 8, 1] },
  { id: 'v4', label: '卷四 模式识别~2017.7，XLPR', start: [2015, 8, 1], endExclusive: [2017, 8, 1] },
  { id: 'v5', label: '卷五 快乐学习~2020.6，KLXX', start: [2017, 8, 1], endExclusive: [2020, 7, 1] },
  { id: 'v6', label: '卷六 人工智能~2023.7，RGZN', start: [2020, 7, 1], endExclusive: [2023, 8, 1] },
  { id: 'v7', label: '卷七 北京商汤~2024.12，ST', start: [2023, 8, 1], endExclusive: [2025, 1, 1] },
  { id: 'v8', label: '卷八', start: [2025, 1, 1], endExclusive: [2027, 1, 1] }
];

const formatVolumeLabelSegments = (label: string) => {
  const text = label.trim();
  const splitIndex = text.indexOf('~');
  if (splitIndex < 0) return [text];

  const prefix = text.slice(0, splitIndex).trim();
  const suffix = text.slice(splitIndex).trim();
  return [prefix, suffix].filter(Boolean);
};

const getVolumeTopicTitle = (volumeId: string) => {
  const volume = CALENDAR_VOLUME_DEFINITIONS.find(item => item.id === volumeId);
  const label = volume?.label.trim() || '';
  return label
    .replace(/^卷[一二三四五六七八九十]+\s*/, '')
    .split('~')[0]
    .trim();
};

const normalizeCalendarScale = (value: unknown): CalendarScale => {
  if (value === 'era' || value === 'overview') return 'era';
  return value === 'year' || value === 'volume' ? value : 'month';
};
const normalizeYearMonthVisibleLimit = (value: unknown) => {
  const n = Number(value);
  if (!Number.isFinite(n)) return YEAR_MONTH_VISIBLE_LIMIT_DEFAULT;
  return Math.min(MONTH_VISIBLE_LIMIT_MAX, Math.max(MONTH_VISIBLE_LIMIT_MIN, Math.round(n)));
};
const normalizeVolumeMonthVisibleLimit = (value: unknown) => {
  const n = Number(value);
  if (!Number.isFinite(n)) return VOLUME_MONTH_VISIBLE_LIMIT_DEFAULT;
  return Math.min(MONTH_VISIBLE_LIMIT_MAX, Math.max(MONTH_VISIBLE_LIMIT_MIN, Math.round(n)));
};
const normalizeEraYearVisibleLimit = (value: unknown) => {
  const n = Number(value);
  if (!Number.isFinite(n)) return ERA_YEAR_VISIBLE_LIMIT_DEFAULT;
  return Math.min(MONTH_VISIBLE_LIMIT_MAX, Math.max(MONTH_VISIBLE_LIMIT_MIN, Math.round(n)));
};
const normalizeYearMonthMemos = (value: unknown): YearMonthMemoMap => {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return {};
  return Object.fromEntries(
    Object.entries(value as Record<string, unknown>)
      .filter(([key, text]) => /^\d{4}-\d{2}$/.test(key) && typeof text === 'string')
      .map(([key, text]) => [key, text.trim()])
      .filter(([, text]) => text)
  );
};
const normalizeYearTitles = (value: unknown): YearTitleMap => {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return {};
  return Object.fromEntries(
    Object.entries(value as Record<string, unknown>)
      .filter(([key, text]) => /^\d{4}$/.test(key) && typeof text === 'string')
      .map(([key, text]) => [key, text.trim()])
      .filter(([, text]) => text)
  );
};

const calendarScale = ref<CalendarScale>(normalizeCalendarScale(session.value?.viewState.calendarScale));
const calendarScaleOptions = [
  { label: '月', value: 'month' },
  { label: '年', value: 'year' },
  { label: '卷', value: 'volume' },
  { label: '纪', value: 'era' }
];
const currentMonth = ref<Date>(
  session.value?.viewState.currentMonth ? new Date(session.value.viewState.currentMonth) : new Date()
);
const viewProgram = ref(normalizeNoteProgramChannel(
  session.value?.viewState.viewProgram ?? createIncludeAllProgram()
));
const yearMonthVisibleLimit = ref(normalizeYearMonthVisibleLimit(session.value?.viewState.yearMonthVisibleLimit));
const volumeMonthVisibleLimit = ref(normalizeVolumeMonthVisibleLimit(session.value?.viewState.volumeMonthVisibleLimit));
const eraYearVisibleLimit = ref(normalizeEraYearVisibleLimit(session.value?.viewState.eraYearVisibleLimit));
const yearMonthMemos = ref<YearMonthMemoMap>(normalizeYearMonthMemos(session.value?.viewState.yearMonthMemos));
const yearTitles = ref<YearTitleMap>(normalizeYearTitles(session.value?.viewState.yearTitles));
const editingYearMonthMemoKey = ref('');
const yearMonthMemoDraft = ref('');
const yearMonthMemoTextareaRef = ref<HTMLTextAreaElement | null>(null);
const setYearMonthMemoTextareaRef = (el: unknown) => {
  yearMonthMemoTextareaRef.value = el instanceof HTMLTextAreaElement ? el : null;
};
const editingYearTitleKey = ref('');
const yearTitleDraft = ref('');
const yearTitleInputRef = ref<HTMLInputElement | null>(null);
const setYearTitleInputRef = (el: unknown) => {
  yearTitleInputRef.value = el instanceof HTMLInputElement ? el : null;
};
const weekDays = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'];
const currentNoteId = ref('');
const loading = ref(false);
const codexDiaryImporting = ref(false);
const codexWorkloadTurns = ref<CodexWorkloadTurn[]>([]);
const codexHistoricalSecondsByDay = ref<CodexWorkloadDaySeconds>({});
const codexWorkloadLoaded = ref(false);
const codexWorkloadError = ref('');
const CODEX_DIARY_IMPORT_POLL_INTERVAL_MS = 1500;
const CODEX_DIARY_IMPORT_WAIT_TIMEOUT_MS = 16 * 60 * 1000;
let latestCodexWorkloadRequestId = 0;
const dayContextMenu = ref({
  visible: false,
  x: 0,
  y: 0,
  date: null as Date | null
});
const formatDateShort = (value: Date | string | number | null | undefined) => formatNoteDateShort(value);
const formatDateFull = (value: number) => new Date(value).toLocaleDateString('zh-CN', {
  year: 'numeric',
  month: '2-digit',
  day: '2-digit'
});

const pad2 = (n: number) => String(n).padStart(2, '0');

const toDateStr = (d: Date) => {
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
};

const dateFromTuple = ([year, month, day]: CalendarVolumeDefinition['start']) => new Date(year, month - 1, day);

const getVolumeForDate = (date: Date) => {
  const ts = date.getTime();
  const matched = CALENDAR_VOLUME_DEFINITIONS.find(volume => {
    const start = dateFromTuple(volume.start).getTime();
    const end = dateFromTuple(volume.endExclusive).getTime();
    return ts >= start && ts < end;
  });
  if (matched) return matched;

  const fallbackStartYear = Math.floor(date.getFullYear() / 4) * 4;
  return {
    id: `fallback-${fallbackStartYear}`,
    label: `${fallbackStartYear}-${fallbackStartYear + 3}年`,
    start: [fallbackStartYear, 1, 1],
    endExclusive: [fallbackStartYear + 4, 1, 1],
  } satisfies CalendarVolumeDefinition;
};

const monthAnchor = computed(() => {
  const d = currentMonth.value instanceof Date ? currentMonth.value : new Date();
  return new Date(d.getFullYear(), d.getMonth(), 1);
});

const currentYear = computed(() => monthAnchor.value.getFullYear());
const currentYearTitleKey = computed(() => String(currentYear.value));
const currentVolume = computed(() => getVolumeForDate(monthAnchor.value));
const calendarVolumeOptions = computed(() => {
  const options = [...CALENDAR_VOLUME_DEFINITIONS];
  return options.some(volume => volume.id === currentVolume.value.id)
    ? options
    : [...options, currentVolume.value];
});
const eraStartTs = computed(() => dateFromTuple(CALENDAR_VOLUME_DEFINITIONS[0]!.start).getTime());
const eraEndTs = computed(() => dateFromTuple(CALENDAR_VOLUME_DEFINITIONS[CALENDAR_VOLUME_DEFINITIONS.length - 1]!.endExclusive).getTime());
const volumeStartTs = computed(() => dateFromTuple(currentVolume.value.start).getTime());
const volumeEndTs = computed(() => dateFromTuple(currentVolume.value.endExclusive).getTime());
const yearStartTs = computed(() => new Date(currentYear.value, 0, 1).getTime());
const yearEndTs = computed(() => new Date(currentYear.value + 1, 0, 1).getTime());
const monthStartTs = computed(() => monthAnchor.value.getTime());
const monthEndTs = computed(() => new Date(monthAnchor.value.getFullYear(), monthAnchor.value.getMonth() + 1, 1).getTime());
const activeMonthVisibleLimit = computed(() => {
  if (calendarScale.value === 'era') return eraYearVisibleLimit.value;
  return calendarScale.value === 'volume' ? volumeMonthVisibleLimit.value : yearMonthVisibleLimit.value;
});
const activeVisibleLimitLabel = computed(() => (calendarScale.value === 'era' ? '每年' : '每月'));
const prevPeriodTitle = computed(() => {
  if (calendarScale.value === 'year') return '上一年';
  if (calendarScale.value === 'volume') return '上一卷';
  return '上个月';
});
const nextPeriodTitle = computed(() => {
  if (calendarScale.value === 'year') return '下一年';
  if (calendarScale.value === 'volume') return '下一卷';
  return '下个月';
});
const monthVisibleLimitTitle = computed(() => {
  if (calendarScale.value === 'era') return '控制纪视图每年最多直接显示的代表性文档节点数';
  return calendarScale.value === 'volume'
    ? '控制卷视图每个月最多直接显示的节点数'
    : '控制年度视图每个月最多直接显示的节点数';
});
const backendFilterHelp = computed(() => {
  if (calendarScale.value === 'era') return '纪视图按全部分卷时间范围从后端加载节点；按卷和年份展示代表性文档节点。';
  if (calendarScale.value === 'volume') return '卷视图按当前卷的时间范围从后端加载节点；每月按配置数量展示高价值节点。';
  if (calendarScale.value === 'year') return '年视图按当前年份从后端加载节点；每月按配置数量展示高价值节点，其余折叠后可下钻查看。';
  return '日历按当前月视图可见日期范围从后端加载节点；切换月份会立即重载并保存。';
});
const frontFilterHelp = computed(() => {
  if (calendarScale.value === 'era') return '基于当前纪视图已加载的节点实时筛选并渲染代表性文档节点，修改后立即生效并保存。';
  if (calendarScale.value === 'volume') return '基于当前卷已加载的节点实时筛选并渲染月度节点，修改后立即生效并保存。';
  if (calendarScale.value === 'year') return '基于当前年份已加载的节点实时筛选并渲染年度摘要，修改后立即生效并保存。';
  return '基于当前月份已加载的节点实时筛选并渲染日历，修改后立即生效并保存。';
});

const requireLoginForCodexDiary = () => {
  if (!userStore.isAuthenticated) {
    ElMessageBox.confirm('该功能需要登录账号后才可用。是否前往登录？', '提示', {
      confirmButtonText: '前往登录',
      cancelButtonText: '取消',
      type: 'info'
    }).then(() => {
      router.push({ path: '/login', query: { redirect: router.currentRoute.value.fullPath } });
    }).catch(() => {});
    return false;
  }
  return true;
};

const createNoteForDay = async (date: Date) => {
  const now = new Date();
  const isTargetToday = isToday(date);
  
  const yy = String(date.getFullYear()).slice(-2);
  const mm = pad2(date.getMonth() + 1);
  const dd = pad2(date.getDate());
  
  let hh = '00';
  let min = '00';
  let startAt: number;

  if (isTargetToday) {
    hh = pad2(now.getHours());
    min = pad2(now.getMinutes());
    // 如果是今天，保留当前时分秒
    startAt = new Date(date.getFullYear(), date.getMonth(), date.getDate(), now.getHours(), now.getMinutes(), now.getSeconds()).getTime();
  } else {
    // 非今天，时分秒归零
    startAt = new Date(date.getFullYear(), date.getMonth(), date.getDate(), 0, 0, 0, 0).getTime();
  }
  
  const defaultTitle = `${yy}${mm}${dd}_${hh}${min}`;

  const newNote = await noteStore.createNote(defaultTitle, '', NOTE_WEIGHT_DEFAULT, startAt);
  if (newNote) {
    noteStore.addNoteToTab(props.tabId, newNote.id);
    currentNoteId.value = noteKey(newNote.id);
    ElMessage.success('已创建节点');
  }
};

const openDayContextMenu = (event: MouseEvent, date: Date) => {
  if (!allowCreate.value) return;
  dayContextMenu.value = {
    visible: true,
    x: event.clientX,
    y: event.clientY,
    date: new Date(date)
  };
};

const closeDayContextMenu = () => {
  dayContextMenu.value.visible = false;
};

const closeContextMenus = () => {
  closeDayContextMenu();
};

const createNoteFromContextMenu = async () => {
  const date = dayContextMenu.value.date;
  closeDayContextMenu();
  if (!date) return;
  await createNoteForDay(date);
};

const delay = (ms: number) => new Promise(resolve => window.setTimeout(resolve, ms));
const isCodexDiaryImportActive = (status: string | undefined | null) => status === 'pending' || status === 'running';
const showCodexDiaryImportError = async (message?: string | null) => {
  await ElMessageBox.alert(message || 'Codex 总结日记导入失败', 'Codex 总结日记导入失败', {
    confirmButtonText: '知道了',
    type: 'error'
  }).catch(() => undefined);
};

const waitForCodexDiaryImportRun = async (runId: string): Promise<CodexDiaryImportRunResponse> => {
  let latest = await fetchCodexDiaryImportRun(runId);
  const deadline = Date.now() + CODEX_DIARY_IMPORT_WAIT_TIMEOUT_MS;
  while (isCodexDiaryImportActive(latest.status) && Date.now() < deadline) {
    await delay(CODEX_DIARY_IMPORT_POLL_INTERVAL_MS);
    latest = await fetchCodexDiaryImportRun(runId);
  }
  return latest;
};

const startCodexDiaryImport = async (date: Date, confirmDuplicate = false): Promise<CodexDiaryImportRunResponse | null> => {
  try {
    return await startCodexDiaryImportRun({
      date: toDateStr(date),
      confirm_duplicate: confirmDuplicate
    });
  } catch (error) {
    const maybeError = error as {
      response?: {
        status?: number;
        data?: {
          detail?: string | { code?: string; message?: string; duplicate_count?: number };
        };
      };
      message?: string;
    };
    const detail = maybeError.response?.data?.detail;
    if (typeof detail === 'object' && detail?.code === 'active_import') {
      await showCodexDiaryImportError(detail.message || '该日期的 Codex 总结日记仍在导入中，请稍后再试。');
      return null;
    }
    if (maybeError.response?.status === 409 && !confirmDuplicate) {
      const message = typeof detail === 'object' && detail?.message
        ? detail.message
        : '该日期已导入过 Codex 总结日记，继续会重复生成一批新节点。';
      try {
        await ElMessageBox.confirm(message, '重复导入确认', {
          confirmButtonText: '继续导入',
          cancelButtonText: '取消',
          type: 'warning'
        });
      } catch {
        return null;
      }
      return startCodexDiaryImport(date, true);
    }
    await showCodexDiaryImportError(
      (typeof maybeError.response?.data?.detail === 'string' && maybeError.response.data.detail)
      || maybeError.message
    );
    return null;
  }
};

const importCodexDiaryForDay = async (date: Date) => {
  if (!requireLoginForCodexDiary() || codexDiaryImporting.value) return;
  codexDiaryImporting.value = true;
  const targetDate = new Date(date);
  try {
    const startedRun = await startCodexDiaryImport(targetDate);
    if (!startedRun) return;
    ElMessage.info('正在导入 Codex 总结日记');
    const completedRun = await waitForCodexDiaryImportRun(startedRun.id);
    if (completedRun.status === 'failed') {
      await showCodexDiaryImportError(completedRun.error_message);
      return;
    }
    if (isCodexDiaryImportActive(completedRun.status)) {
      ElMessage.warning('Codex 总结日记仍在运行，请稍后刷新日历查看结果');
      return;
    }
    if (completedRun.status !== 'completed') {
      await showCodexDiaryImportError(completedRun.error_message || `Codex 总结日记状态异常：${completedRun.status}`);
      return;
    }
    await refreshData({ silent: true });
    completedRun.created_note_ids.forEach(noteId => noteStore.addNoteToTab(props.tabId, noteId));
    if (completedRun.created_note_ids.length > 0) {
      currentNoteId.value = noteKey(completedRun.created_note_ids[0]);
      ElMessage.success(completedRun.stage_label || `已创建 ${completedRun.created_note_ids.length} 个节点`);
    } else {
      ElMessage.info(completedRun.stage_label || '当天没有可导入的 Codex 会话记录');
    }
  } catch (error) {
    const maybeError = error as {
      response?: { data?: { detail?: string; message?: string } };
      message?: string;
    };
    await showCodexDiaryImportError(
      maybeError.response?.data?.detail
      || maybeError.response?.data?.message
      || maybeError.message
    );
  } finally {
    codexDiaryImporting.value = false;
  }
};

const importCodexDiaryFromContextMenu = async () => {
  const date = dayContextMenu.value.date;
  closeDayContextMenu();
  if (!date) return;
  await importCodexDiaryForDay(date);
};

const extractCodexWorkloadErrorMessage = (error: any, fallback: string) => {
  const detail = error?.response?.data?.detail;
  const message = typeof detail === 'string' ? detail : error?.message;
  return typeof message === 'string' && message.trim() ? message : fallback;
};

const refreshCodexWorkloadStats = async () => {
  const requestId = ++latestCodexWorkloadRequestId;
  codexWorkloadError.value = '';
  try {
    if (!taskStore.devices.length || Date.now() - taskStore.lastDeviceFetch > CODEX_WORKLOAD_DEVICE_CACHE_MS) {
      await taskStore.fetchDevices();
    }
    const devices = taskStore.devices.slice();
    if (requestId !== latestCodexWorkloadRequestId) return;
    if (!devices.length) {
      codexWorkloadTurns.value = [];
      codexHistoricalSecondsByDay.value = {};
      codexWorkloadLoaded.value = true;
      return;
    }

    const cache = loadCodexWorkloadStatsCache();
    codexHistoricalSecondsByDay.value = collectCachedCodexDaySeconds(cache, devices);
    const todayStartMs = getCodexTodayStartMs();
    const yesterdayKey = toDateStr(new Date(todayStartMs - 1));

    const results = await Promise.allSettled(
      devices.map(async (device) => {
        const requestStartMs = resolveCodexCacheRequestStartAt(cache.devices[device.id], todayStartMs);
        return {
          device,
          requestStartMs,
          workload: await fetchCodexWorkloadForEntry(
            device.id,
            requestStartMs === undefined ? undefined : { startAt: requestStartMs / 1000 }
          ),
        };
      })
    );
    if (requestId !== latestCodexWorkloadRequestId) return;

    const fulfilled = results
      .filter((item): item is PromiseFulfilledResult<{
        device: Device;
        requestStartMs: number | undefined;
        workload: CodexWorkloadResponse;
      }> => item.status === 'fulfilled')
      .map(item => item.value);
    for (const { device, requestStartMs, workload } of fulfilled) {
      const deviceCache = cache.devices[device.id] ?? {
        cachedThrough: '',
        days: {},
        updatedAt: 0,
      };
      cache.devices[device.id] = deviceCache;
      const historicalDays = mapToCodexWorkloadDaySeconds(
        aggregateCodexTurnsByDay(workload.turns || [], requestStartMs ?? Number.NEGATIVE_INFINITY, todayStartMs)
      );
      mergeCodexHistoricalDeviceDays(deviceCache, historicalDays, requestStartMs, todayStartMs, yesterdayKey);
    }
    saveCodexWorkloadStatsCache(cache);
    codexHistoricalSecondsByDay.value = collectCachedCodexDaySeconds(cache, devices);
    codexWorkloadTurns.value = fulfilled.flatMap(({ device, workload }) => (
      (workload.turns || [])
        .filter(turn => isCodexTurnActiveAfter(turn, todayStartMs))
        .map(turn => ({
          ...turn,
          id: `${device.id}:${turn.id}`,
        }))
    ));
    codexWorkloadLoaded.value = true;

    if (!fulfilled.length) {
      const firstRejected = results.find((item): item is PromiseRejectedResult => item.status === 'rejected');
      codexWorkloadError.value = extractCodexWorkloadErrorMessage(firstRejected?.reason, '读取 Codex workload 失败');
    }
  } catch (error) {
    if (requestId !== latestCodexWorkloadRequestId) return;
    codexWorkloadTurns.value = [];
    codexHistoricalSecondsByDay.value = {};
    codexWorkloadLoaded.value = true;
    codexWorkloadError.value = extractCodexWorkloadErrorMessage(error, '读取 Codex workload 失败');
  }
};

let scheduledCalendarRefreshToken = 0;

const scheduleCalendarRefresh = () => {
  const token = ++scheduledCalendarRefreshToken;
  void nextTick(() => {
    if (token !== scheduledCalendarRefreshToken) return;
    void refreshData({ silent: true });
  });
};

const onPeriodChange = (value: Date | string | number | undefined) => {
  const d = value instanceof Date ? value : value ? new Date(value) : new Date();
  if (Number.isNaN(d.getTime())) return;
  currentMonth.value = calendarScale.value !== 'month'
    ? new Date(d.getFullYear(), 0, 1)
    : new Date(d.getFullYear(), d.getMonth(), 1);
  scheduleCalendarRefresh();
};

const onScaleChange = () => {
  scheduleCalendarRefresh();
};

const onVolumeSelectChange = (value: string | number | boolean | undefined) => {
  const volumeId = String(value ?? '');
  const volume = calendarVolumeOptions.value.find(item => item.id === volumeId);
  if (!volume) return;
  currentMonth.value = dateFromTuple(volume.start);
  scheduleCalendarRefresh();
};

const openVolumeFromEra = (volumeId: string) => {
  const volume = calendarVolumeOptions.value.find(item => item.id === volumeId);
  if (!volume) return;
  currentMonth.value = dateFromTuple(volume.start);
  calendarScale.value = 'volume';
  scheduleCalendarRefresh();
};

const prevPeriod = () => {
  if (calendarScale.value === 'era') return;
  const d = monthAnchor.value;
  if (calendarScale.value === 'volume') {
    const previousDay = new Date(volumeStartTs.value - 1);
    currentMonth.value = new Date(previousDay.getFullYear(), previousDay.getMonth(), 1);
  } else {
    currentMonth.value = calendarScale.value === 'year'
      ? new Date(d.getFullYear() - 1, 0, 1)
      : new Date(d.getFullYear(), d.getMonth() - 1, 1);
  }
  scheduleCalendarRefresh();
};

const nextPeriod = () => {
  if (calendarScale.value === 'era') return;
  const d = monthAnchor.value;
  if (calendarScale.value === 'volume') {
    const nextDay = new Date(volumeEndTs.value);
    currentMonth.value = new Date(nextDay.getFullYear(), nextDay.getMonth(), 1);
  } else {
    currentMonth.value = calendarScale.value === 'year'
      ? new Date(d.getFullYear() + 1, 0, 1)
      : new Date(d.getFullYear(), d.getMonth() + 1, 1);
  }
  scheduleCalendarRefresh();
};

const shiftYearPeriod = (delta: number) => {
  if (calendarScale.value === 'era') return;
  const d = monthAnchor.value;
  currentMonth.value = new Date(d.getFullYear() + delta, d.getMonth(), 1);
  scheduleCalendarRefresh();
};

const prevYearPeriod = () => {
  shiftYearPeriod(-1);
};

const nextYearPeriod = () => {
  shiftYearPeriod(1);
};

const goToToday = () => {
  const d = new Date();
  currentMonth.value = new Date(d.getFullYear(), d.getMonth(), 1);
  scheduleCalendarRefresh();
};

const openMonthFromYear = (monthIndex: number) => {
  currentMonth.value = new Date(currentYear.value, monthIndex, 1);
  calendarScale.value = 'month';
  scheduleCalendarRefresh();
};

const openYearFromVolume = (year: number) => {
  currentMonth.value = new Date(year, 0, 1);
  calendarScale.value = 'year';
  scheduleCalendarRefresh();
};

const openMonthFromVolume = (year: number, monthIndex: number) => {
  currentMonth.value = new Date(year, monthIndex, 1);
  calendarScale.value = 'month';
  scheduleCalendarRefresh();
};

const setYearMonthVisibleLimit = (value: number | undefined) => {
  yearMonthVisibleLimit.value = normalizeYearMonthVisibleLimit(value);
};

const setVolumeMonthVisibleLimit = (value: number | undefined) => {
  volumeMonthVisibleLimit.value = normalizeVolumeMonthVisibleLimit(value);
};

const setEraYearVisibleLimit = (value: number | undefined) => {
  eraYearVisibleLimit.value = normalizeEraYearVisibleLimit(value);
};

const setActiveMonthVisibleLimit = (value: number | undefined) => {
  if (calendarScale.value === 'era') {
    setEraYearVisibleLimit(value);
    return;
  }
  if (calendarScale.value === 'volume') {
    setVolumeMonthVisibleLimit(value);
    return;
  }
  setYearMonthVisibleLimit(value);
};

const isToday = (d: Date) => {
  const now = new Date();
  return d.getFullYear() === now.getFullYear() && d.getMonth() === now.getMonth() && d.getDate() === now.getDate();
};

type CalendarDay = {
  date: Date;
  dateStr: string;
  dayNum: number;
  isCurrentMonth: boolean;
  lunarDay: string;
  festival?: string;
  jieQi?: string;
  holidayName?: string;
  isRest?: boolean | null;
};

const buildDayMeta = (date: Date) => {
  let lunarDay = '';
  let festival: string | undefined;
  let jieQi: string | undefined;
  let holidayName: string | undefined;
  let isRest: boolean | null | undefined;

  try {
    const solar = Solar.fromDate(date as any);
    const lunar = solar.getLunar?.();
    const dayInChinese = lunar?.getDayInChinese?.();
    const monthInChinese = lunar?.getMonthInChinese?.();
    lunarDay = dayInChinese === '初一' && monthInChinese ? `${monthInChinese}月` : (dayInChinese || '');

    const lunarFestivals: string[] = lunar?.getFestivals?.() || [];
    const solarFestivals: string[] = solar.getFestivals?.() || [];
    festival = [...lunarFestivals, ...solarFestivals][0];
    jieQi = lunar?.getJieQi?.() || undefined;

    const h = HolidayUtil.getHoliday?.(date.getFullYear(), date.getMonth() + 1, date.getDate());
    if (h) {
      holidayName = h.getName?.() || h.getName || undefined;
      if (typeof h.isWork === 'function') isRest = !h.isWork();
      else if (typeof h.isRest === 'function') isRest = h.isRest();
      else if (typeof h.getWork === 'function') isRest = h.getWork() !== 1;
      else isRest = true;
    }
  } catch (e) {
    lunarDay = '';
  }

  return { lunarDay, festival, jieQi, holidayName, isRest: isRest ?? null };
};

const startPadding = computed(() => {
  const first = new Date(monthAnchor.value.getFullYear(), monthAnchor.value.getMonth(), 1);
  const day = first.getDay();
  return (day + 6) % 7;
});

const gridDays = computed<CalendarDay[]>(() => {
  const d = monthAnchor.value;
  const year = d.getFullYear();
  const month = d.getMonth();
  const dayCount = new Date(year, month + 1, 0).getDate();

  const totalCells = Math.ceil((startPadding.value + dayCount) / 7) * 7;
  const gridStart = new Date(year, month, 1 - startPadding.value);

  const list: CalendarDay[] = [];
  for (let i = 0; i < totalCells; i += 1) {
    const date = new Date(gridStart);
    date.setDate(gridStart.getDate() + i);
    const meta = buildDayMeta(date);
    list.push({
      date,
      dateStr: toDateStr(date),
      dayNum: date.getDate(),
      isCurrentMonth: date.getFullYear() === year && date.getMonth() === month,
      lunarDay: meta.lunarDay,
      festival: meta.festival,
      jieQi: meta.jieQi,
      holidayName: meta.holidayName,
      isRest: meta.isRest
    });
  }
  return list;
});

const WEEK_BASE_WEIGHT = 5;

const getWeekLevelFromCount = (count: number) => {
  if (count <= 0) return 0;
  // 1, 3, 6, 10... 条对应 1, 2, 3, 4 级；中间数量平滑落在小数级别。
  return (Math.sqrt(1 + 8 * count) - 1) / 2;
};

const isCurrentWeekRow = (week: CalendarDay[]) => week.some(day => isToday(day.date));

const gridTemplateRows = computed(() => {
  const days = gridDays.value;
  if (!days.length) return '';

  const rowsCount = days.length / 7;
  const weekRows = Array.from({ length: rowsCount }, (_, i) => days.slice(i * 7, i * 7 + 7));
  const weekNoteCounts = weekRows.map(week => week.reduce((sum, day) => sum + getNotesForDay(day.date).length, 0));
  const maxWeekNoteCount = Math.max(...weekNoteCounts, 0);

  return weekRows.map((week, index) => {
    const weekNoteCount = weekNoteCounts[index];
    const effectiveCount = isCurrentWeekRow(week) ? Math.max(weekNoteCount, maxWeekNoteCount) : weekNoteCount;
    const level = getWeekLevelFromCount(effectiveCount);
    const weight = Number((WEEK_BASE_WEIGHT + level).toFixed(3));
    const height = Math.min(
      MONTH_WEEK_ROW_MAX_HEIGHT,
      Math.max(MONTH_WEEK_ROW_MIN_HEIGHT, Math.round(weight * MONTH_WEEK_ROW_UNIT_HEIGHT))
    );
    return `${height}px`;
  }).join(' ');
});

const gridStartTs = computed(() => gridDays.value[0]?.date.getTime() ?? monthStartTs.value);
const gridEndTs = computed(() => {
  const last = gridDays.value[gridDays.value.length - 1]?.date;
  if (!last) return monthEndTs.value;
  return new Date(last.getFullYear(), last.getMonth(), last.getDate() + 1).getTime();
});
const periodStartTs = computed(() => {
  if (calendarScale.value === 'era') return eraStartTs.value;
  if (calendarScale.value === 'volume') return volumeStartTs.value;
  if (calendarScale.value === 'year') return yearStartTs.value;
  return gridStartTs.value;
});
const periodEndTs = computed(() => {
  if (calendarScale.value === 'era') return eraEndTs.value;
  if (calendarScale.value === 'volume') return volumeEndTs.value;
  if (calendarScale.value === 'year') return yearEndTs.value;
  return gridEndTs.value;
});

const fixedViewProgram = computed(() => ({
  default: true,
  rules: (props.fixedViewFilterRules || []).map(rule => normalizeNoteProgramRule(rule))
}));
const visibleNotes = computed(() => {
  const fixedNotes = applyNoteProgramChannelLocally(noteStore.getTabNotes(props.tabId), fixedViewProgram.value);
  return applyNoteProgramChannelLocally(fixedNotes, viewProgram.value);
});

const notesByDay = computed(() => {
  const map = new Map<string, NoteNode[]>();

  for (const note of visibleNotes.value) {
    const ts = note.start_at || note.created_at;
    if (!ts) continue;
    const key = toDateStr(new Date(ts));
    const arr = map.get(key) || [];
    arr.push(note);
    map.set(key, arr);
  }

  for (const arr of map.values()) {
    // 优先按时间（start_at 或 created_at）升序排序，时间相同时按权重降序
    arr.sort((a, b) => {
      const timeA = a.start_at || a.created_at;
      const timeB = b.start_at || b.created_at;
      if (timeA !== timeB) return timeA - timeB;
      return (b.weight || 0) - (a.weight || 0);
    });
  }

  return map;
});

const getNotesForDay = (date: Date) => {
  return notesByDay.value.get(toDateStr(date)) || [];
};

const openNote = (note: NoteNode) => {
  currentNoteId.value = noteKey(note.id);
};

const getNoteDisplayTheme = (note: NoteNode) => getNodeDisplayStyle(
  note.primary_category ?? note.node_type,
  note.lifecycle_stage ?? note.node_status,
  note.color,
  note.note_categories ?? note.note_types,
  resolveCompletionProgressFillRatio({
    lifecycleStage: note.lifecycle_stage ?? note.node_status,
    completionProgress: note.completion_progress,
    completionProgressExpr: note.completion_progress_expr,
    customFields: note.custom_fields,
  })
);

const getNoteTime = (note: NoteNode) => note.start_at || note.created_at || 0;

const getNoteProgressRatio = (note: NoteNode) => resolveCompletionProgressFillRatio({
  lifecycleStage: note.lifecycle_stage ?? note.node_status,
  completionProgress: note.completion_progress,
  completionProgressExpr: note.completion_progress_expr,
  customFields: toRaw(note).custom_fields,
});

const getNoteProgressPercent = (note: NoteNode) => {
  const ratio = getNoteProgressRatio(note);
  if (typeof ratio !== 'number') return null;
  return Math.round(Math.min(1, Math.max(0, ratio)) * 100);
};

const getYearNoteScore = (note: NoteNode) => {
  const rawNote = toRaw(note);
  const weight = Number.isFinite(Number(rawNote.weight)) ? Number(rawNote.weight) : 0;
  const relationCount = Number(rawNote.edge_count || 0) + Number(rawNote.out_degree || 0);
  const relationScore = Math.min(4, Math.log2(relationCount + 1));
  const progress = getNoteProgressRatio(rawNote);
  const progressScore = typeof progress === 'number' ? Math.min(1, Math.max(0, progress)) * 2 : 0;
  const stage = String(rawNote.lifecycle_stage ?? rawNote.node_status ?? '').toLowerCase();
  const stageScore = stage === 'done' || stage === 'predone' ? 1 : stage === 'doing' ? 0.8 : stage === 'todo' ? 0.4 : 0;
  return weight * 10 + relationScore + progressScore + stageScore;
};

const getNoteCustomFieldRawValue = (note: NoteNode, key: string) => {
  const fields = toRaw(note).custom_fields;
  if (!Array.isArray(fields)) return undefined;
  for (const item of fields) {
    if (Array.isArray(item) && item[0] === key) return item[2];
    if (item && typeof item === 'object' && (item as any).key === key) return (item as any).value;
  }
  return undefined;
};

const getNoteCustomFieldValue = (note: NoteNode, key: string) => {
  const value = getNoteCustomFieldRawValue(note, key);
  return value == null ? '' : String(value).trim();
};

const toEpochMs = (value?: number | string | null) => {
  if (value == null || value === '') return 0;
  const numeric = Number(value);
  if (Number.isFinite(numeric)) return Math.abs(numeric) < 1_000_000_000_000 ? numeric * 1000 : numeric;
  const parsed = new Date(value).getTime();
  return Number.isNaN(parsed) ? 0 : parsed;
};

const startOfLocalDayMs = (value: number) => {
  const date = new Date(value);
  return new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
};

const addLocalDaysMs = (value: number, days = 1) => {
  const date = new Date(value);
  return new Date(date.getFullYear(), date.getMonth(), date.getDate() + days).getTime();
};

const parseLocalDateKeyMs = (dateKey: string) => {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(dateKey);
  if (!match) return 0;
  const [, year, month, day] = match;
  const value = new Date(Number(year), Number(month) - 1, Number(day)).getTime();
  return Number.isNaN(value) ? 0 : value;
};

const normalizeCodexWorkloadDaySeconds = (value: unknown): CodexWorkloadDaySeconds => {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return {};
  return Object.fromEntries(
    Object.entries(value as Record<string, unknown>)
      .filter(([dateKey, seconds]) => /^\d{4}-\d{2}-\d{2}$/.test(dateKey) && Number(seconds) > 0)
      .map(([dateKey, seconds]) => [dateKey, Number(seconds)])
  );
};

const canUseCodexWorkloadStatsCache = () => (
  typeof window !== 'undefined' && typeof window.localStorage !== 'undefined'
);

const createEmptyCodexWorkloadStatsCache = (): CodexWorkloadStatsCache => ({
  version: CODEX_WORKLOAD_STATS_CACHE_VERSION,
  timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || '',
  devices: {},
});

const loadCodexWorkloadStatsCache = (): CodexWorkloadStatsCache => {
  if (!canUseCodexWorkloadStatsCache()) return createEmptyCodexWorkloadStatsCache();
  try {
    const raw = window.localStorage.getItem(CODEX_WORKLOAD_STATS_CACHE_KEY);
    const parsed = raw ? JSON.parse(raw) : null;
    if (!parsed || parsed.version !== CODEX_WORKLOAD_STATS_CACHE_VERSION || typeof parsed.devices !== 'object') {
      return createEmptyCodexWorkloadStatsCache();
    }
    return {
      version: CODEX_WORKLOAD_STATS_CACHE_VERSION,
      timezone: typeof parsed.timezone === 'string' ? parsed.timezone : '',
      devices: Object.fromEntries(
        Object.entries(parsed.devices as Record<string, any>).map(([deviceId, item]) => [
          deviceId,
          {
            cachedThrough: typeof item?.cachedThrough === 'string' ? item.cachedThrough : '',
            days: normalizeCodexWorkloadDaySeconds(item?.days),
            updatedAt: Number(item?.updatedAt || 0),
          },
        ])
      ),
    };
  } catch {
    return createEmptyCodexWorkloadStatsCache();
  }
};

const saveCodexWorkloadStatsCache = (cache: CodexWorkloadStatsCache) => {
  if (!canUseCodexWorkloadStatsCache()) return;
  try {
    window.localStorage.setItem(CODEX_WORKLOAD_STATS_CACHE_KEY, JSON.stringify(cache));
  } catch {
    // 缓存失败不影响页面实时统计。
  }
};

const collectCachedCodexDaySeconds = (
  cache: CodexWorkloadStatsCache,
  devices: Device[],
): CodexWorkloadDaySeconds => {
  const days: CodexWorkloadDaySeconds = {};
  for (const device of devices) {
    const deviceCache = cache.devices[device.id];
    if (!deviceCache) continue;
    for (const [dateKey, seconds] of Object.entries(deviceCache.days)) {
      days[dateKey] = (days[dateKey] || 0) + seconds;
    }
  }
  return days;
};

const aggregateCodexTurnsByDay = (
  turns: CodexWorkloadTurn[],
  rangeStartMs = Number.NEGATIVE_INFINITY,
  rangeEndMs = Number.POSITIVE_INFINITY,
) => {
  const map = new Map<string, number>();
  for (const turn of turns) {
    const startMs = toEpochMs(turn.start_at);
    const rawEndMs = toEpochMs(turn.end_at);
    const durationMs = Math.max(0, Number(turn.duration_seconds || 0) * 1000);
    const endMs = rawEndMs > startMs ? rawEndMs : startMs + durationMs;
    const clippedStartMs = Math.max(startMs, rangeStartMs);
    const clippedEndMs = Math.min(endMs, rangeEndMs);
    if (!startMs || clippedEndMs <= clippedStartMs) continue;

    for (
      let dayStartMs = startOfLocalDayMs(clippedStartMs);
      dayStartMs < clippedEndMs;
      dayStartMs = addLocalDaysMs(dayStartMs)
    ) {
      const dayEndMs = addLocalDaysMs(dayStartMs);
      const overlapMs = Math.max(0, Math.min(clippedEndMs, dayEndMs) - Math.max(clippedStartMs, dayStartMs));
      if (!overlapMs) continue;
      const dateKey = toDateStr(new Date(dayStartMs));
      map.set(dateKey, (map.get(dateKey) || 0) + overlapMs / 1000);
    }
  }
  return map;
};

const getCodexTodayStartMs = () => startOfLocalDayMs(Date.now());

const mapToCodexWorkloadDaySeconds = (map: Map<string, number>): CodexWorkloadDaySeconds => (
  Object.fromEntries(Array.from(map.entries()).filter(([, seconds]) => seconds > 0))
);

const isCodexTurnActiveAfter = (turn: CodexWorkloadTurn, startMs: number) => {
  const turnStartMs = toEpochMs(turn.start_at);
  const rawEndMs = toEpochMs(turn.end_at);
  const durationMs = Math.max(0, Number(turn.duration_seconds || 0) * 1000);
  const turnEndMs = rawEndMs > turnStartMs ? rawEndMs : turnStartMs + durationMs;
  return turnEndMs > startMs;
};

const resolveCodexCacheRequestStartAt = (
  deviceCache: CodexWorkloadDeviceStatsCache | undefined,
  todayStartMs: number,
) => {
  if (!deviceCache?.cachedThrough) return undefined;
  const cachedThroughMs = parseLocalDateKeyMs(deviceCache.cachedThrough);
  if (!cachedThroughMs) return undefined;
  const nextMissingDayMs = addLocalDaysMs(cachedThroughMs);
  return Math.min(nextMissingDayMs, todayStartMs);
};

const mergeCodexHistoricalDeviceDays = (
  deviceCache: CodexWorkloadDeviceStatsCache,
  days: CodexWorkloadDaySeconds,
  rangeStartMs: number | undefined,
  todayStartMs: number,
  cachedThroughKey: string,
) => {
  if (rangeStartMs === undefined) {
    deviceCache.days = days;
  } else if (rangeStartMs < todayStartMs) {
    for (const dateKey of Object.keys(deviceCache.days)) {
      const dateMs = parseLocalDateKeyMs(dateKey);
      if (dateMs >= rangeStartMs && dateMs < todayStartMs) {
        delete deviceCache.days[dateKey];
      }
    }
    Object.assign(deviceCache.days, days);
  }
  deviceCache.cachedThrough = cachedThroughKey;
  deviceCache.updatedAt = Date.now();
};

const codexDynamicSecondsByDay = computed(() => (
  aggregateCodexTurnsByDay(codexWorkloadTurns.value, getCodexTodayStartMs())
));

const codexSecondsByDay = computed(() => {
  const map = new Map<string, number>(
    Object.entries(codexHistoricalSecondsByDay.value)
  );
  for (const [dateKey, seconds] of codexDynamicSecondsByDay.value.entries()) {
    map.set(dateKey, (map.get(dateKey) || 0) + seconds);
  }
  return map;
});

const codexSecondsByMonth = computed(() => {
  const map = new Map<string, number>();
  for (const [dateKey, seconds] of codexSecondsByDay.value.entries()) {
    const monthKey = dateKey.slice(0, 7);
    map.set(monthKey, (map.get(monthKey) || 0) + seconds);
  }
  return map;
});

const getCodexSecondsForDate = (date: Date) => codexSecondsByDay.value.get(toDateStr(date)) || 0;
const getCodexSecondsForYearMonth = (year: number, monthIndex: number) => (
  codexSecondsByMonth.value.get(`${year}-${pad2(monthIndex + 1)}`) || 0
);

const shouldShowCodexHours = (seconds: number) => Math.round(seconds / CODEX_WORKLOAD_HOUR_SECONDS) > 0;
const formatCodexHours = (seconds: number) => `${Math.round(seconds / CODEX_WORKLOAD_HOUR_SECONDS)}h`;
const getCodexHoursTitle = (seconds: number) => {
  const minutes = Math.round(seconds / 60);
  const sourceText = codexWorkloadLoaded.value ? '来自 Codex workload' : '正在读取 Codex workload';
  return `Codex 工作约 ${formatCodexHours(seconds)}（${minutes} 分钟，${sourceText}）`;
};

const getVolumeNoteScore = (note: NoteNode) => {
  const rawNote = toRaw(note);
  const sourceKind = getNoteCustomFieldValue(rawNote, 'source_kind');
  const weight = Number.isFinite(Number(rawNote.weight)) ? Number(rawNote.weight) : NOTE_WEIGHT_DEFAULT;
  let sourceBoost = 0;
  if (sourceKind.includes('chapter') || sourceKind.includes('section')) sourceBoost = 80;
  else if (sourceKind.includes('week')) sourceBoost = 50;
  else if (sourceKind.includes('child')) sourceBoost = 35;
  else if (sourceKind.includes('day_group')) sourceBoost = 16;

  const formBoost = rawNote.note_form === 'document' ? 8 : 0;
  return getYearNoteScore(rawNote) + weight * 12 + sourceBoost + formBoost;
};

const REPRESENTATIVE_POOL_LIMIT = 100;

type RepresentativeBucket = {
  totalCount: number;
  rankedNotes: NoteNode[];
  preferredCount: number;
  preferredRankedNotes: NoteNode[];
  documentCount: number;
  documentRankedNotes: NoteNode[];
};

const createRepresentativeBucket = (): RepresentativeBucket => ({
  totalCount: 0,
  rankedNotes: [],
  preferredCount: 0,
  preferredRankedNotes: [],
  documentCount: 0,
  documentRankedNotes: []
});

const compareNoteTimeAsc = (a: NoteNode, b: NoteNode) => getNoteTime(a) - getNoteTime(b);

const compareYearRepresentativePriority = (a: NoteNode, b: NoteNode) => {
  const scoreDiff = getYearNoteScore(b) - getYearNoteScore(a);
  if (scoreDiff !== 0) return scoreDiff;
  return compareNoteTimeAsc(a, b);
};

const compareVolumeRepresentativePriority = (a: NoteNode, b: NoteNode) => {
  const scoreDiff = getVolumeNoteScore(b) - getVolumeNoteScore(a);
  if (scoreDiff !== 0) return scoreDiff;
  return compareNoteTimeAsc(a, b);
};

const isPreferredVolumeNote = (note: NoteNode) => (
  Number(toRaw(note).weight || 0) > 0 || Boolean(getNoteCustomFieldValue(note, 'source_kind'))
);

const insertRepresentativeCandidate = (
  rankedNotes: NoteNode[],
  note: NoteNode,
  compare: (a: NoteNode, b: NoteNode) => number,
  limit = REPRESENTATIVE_POOL_LIMIT
) => {
  const insertAt = rankedNotes.findIndex(existing => compare(note, existing) < 0);
  if (insertAt >= 0) {
    rankedNotes.splice(insertAt, 0, note);
    if (rankedNotes.length > limit) rankedNotes.pop();
    return;
  }
  if (rankedNotes.length < limit) rankedNotes.push(note);
};

const addRepresentativeNote = (
  bucket: RepresentativeBucket,
  note: NoteNode,
  compare: (a: NoteNode, b: NoteNode) => number,
  options: { preferred?: boolean; document?: boolean } = {}
) => {
  bucket.totalCount += 1;
  insertRepresentativeCandidate(bucket.rankedNotes, note, compare);
  if (options.preferred) {
    bucket.preferredCount += 1;
    insertRepresentativeCandidate(bucket.preferredRankedNotes, note, compare);
  }
  if (options.document) {
    bucket.documentCount += 1;
    insertRepresentativeCandidate(bucket.documentRankedNotes, note, compare);
  }
};

const takeRepresentativeNotes = (rankedNotes: NoteNode[], limit: number) => (
  rankedNotes.slice(0, limit).sort(compareNoteTimeAsc)
);

const monthLabels = Array.from({ length: 12 }, (_, index) => `${index + 1}月`);
const getYearMonthMemoKey = (year: number, monthIndex: number) => `${year}-${pad2(monthIndex + 1)}`;
const getYearMonthMemo = (key: string) => yearMonthMemos.value[key]?.trim() || '';
const getYearMonthMemoTitle = (key: string) => getYearMonthMemo(key) || undefined;
const getYearTitle = (key: string) => yearTitles.value[key]?.trim() || '';

const loadYearMonthMemos = async () => {
  try {
    const response = await fetchCalendarYearMonthMemos();
    const remoteMemos = normalizeYearMonthMemos(response.memos);
    const remoteYearTitles = normalizeYearTitles(response.year_titles);
    if (Object.keys(remoteMemos).length === 0 && Object.keys(remoteYearTitles).length === 0) return;

    const mergedMemos = normalizeYearMonthMemos({
      ...yearMonthMemos.value,
      ...remoteMemos
    });
    const mergedYearTitles = normalizeYearTitles({
      ...yearTitles.value,
      ...remoteYearTitles
    });
    yearMonthMemos.value = mergedMemos;
    yearTitles.value = mergedYearTitles;
    noteStore.updateTabViewState(props.tabId, {
      yearMonthMemos: { ...mergedMemos },
      yearTitles: { ...mergedYearTitles }
    });
  } catch (error) {
    console.warn('Failed to load calendar year-month memos:', error);
  }
};

const persistCalendarTextSettings = (errorMessage: string) => {
  noteStore.updateTabViewState(props.tabId, {
    yearMonthMemos: { ...yearMonthMemos.value },
    yearTitles: { ...yearTitles.value }
  });
  void saveCalendarYearMonthMemos(yearMonthMemos.value, yearTitles.value).catch(error => {
    console.warn('Failed to save calendar text settings:', error);
    ElMessage.error(errorMessage);
  });
};

const persistYearMonthMemos = () => persistCalendarTextSettings('保存月总结失败');
const persistYearTitles = () => persistCalendarTextSettings('保存年标题失败');

const startYearMonthMemoEdit = async (key: string) => {
  editingYearMonthMemoKey.value = key;
  yearMonthMemoDraft.value = getYearMonthMemo(key);
  await nextTick();
  yearMonthMemoTextareaRef.value?.focus();
  yearMonthMemoTextareaRef.value?.select();
};

const saveYearMonthMemoEdit = () => {
  const key = editingYearMonthMemoKey.value;
  if (!key) return;

  const previous = getYearMonthMemo(key);
  const next = yearMonthMemoDraft.value.trim();
  const memos = { ...yearMonthMemos.value };
  if (next) memos[key] = next;
  else delete memos[key];

  editingYearMonthMemoKey.value = '';
  yearMonthMemoDraft.value = '';

  if (next === previous) return;
  yearMonthMemos.value = memos;
  persistYearMonthMemos();
};

const cancelYearMonthMemoEdit = () => {
  editingYearMonthMemoKey.value = '';
  yearMonthMemoDraft.value = '';
};

const startYearTitleEdit = async (key: string) => {
  editingYearTitleKey.value = key;
  yearTitleDraft.value = getYearTitle(key);
  await nextTick();
  yearTitleInputRef.value?.focus();
  yearTitleInputRef.value?.select();
};

const saveYearTitleEdit = () => {
  const key = editingYearTitleKey.value;
  if (!key) return;

  const previous = getYearTitle(key);
  const next = yearTitleDraft.value.trim();
  const titles = { ...yearTitles.value };
  if (next) titles[key] = next;
  else delete titles[key];

  editingYearTitleKey.value = '';
  yearTitleDraft.value = '';

  if (next === previous) return;
  yearTitles.value = titles;
  persistYearTitles();
};

const cancelYearTitleEdit = () => {
  editingYearTitleKey.value = '';
  yearTitleDraft.value = '';
};

type YearMonthSummary = {
  monthIndex: number;
  monthLabel: string;
  memoKey: string;
  totalCount: number;
  visibleNotes: NoteNode[];
  hiddenCount: number;
  codexSeconds: number;
  isCurrentMonth: boolean;
  isSparse: boolean;
};

const yearMonthRepresentativeBuckets = computed(() => {
  const buckets = Array.from({ length: 12 }, () => createRepresentativeBucket());
  const start = yearStartTs.value;
  const end = yearEndTs.value;

  for (const note of visibleNotes.value) {
    const ts = getNoteTime(note);
    if (!ts || ts < start || ts >= end) continue;
    const date = new Date(ts);
    const bucket = buckets[date.getMonth()];
    if (bucket) addRepresentativeNote(bucket, note, compareYearRepresentativePriority);
  }
  return buckets;
});

const yearMonthSummaries = computed<YearMonthSummary[]>(() => {
  const now = new Date();
  return yearMonthRepresentativeBuckets.value.map((bucket, monthIndex) => {
    const visible = takeRepresentativeNotes(bucket.rankedNotes, yearMonthVisibleLimit.value);

    return {
      monthIndex,
      monthLabel: monthLabels[monthIndex] || `${monthIndex + 1}月`,
      memoKey: getYearMonthMemoKey(currentYear.value, monthIndex),
      totalCount: bucket.totalCount,
      visibleNotes: visible,
      hiddenCount: Math.max(0, bucket.totalCount - visible.length),
      codexSeconds: getCodexSecondsForYearMonth(currentYear.value, monthIndex),
      isCurrentMonth: currentYear.value === now.getFullYear() && monthIndex === now.getMonth(),
      isSparse: bucket.totalCount > 0 && bucket.totalCount <= YEAR_MONTH_SPARSE_TOTAL_LIMIT
    };
  });
});

const yearVisibleNoteCount = computed(() => yearMonthSummaries.value.reduce((sum, month) => sum + month.totalCount, 0));
const yearDisplayedNoteCount = computed(() => yearMonthSummaries.value.reduce((sum, month) => sum + month.visibleNotes.length, 0));
const yearHiddenNoteCount = computed(() => Math.max(0, yearVisibleNoteCount.value - yearDisplayedNoteCount.value));

type VolumeMonthSummary = {
  monthIndex: number;
  monthLabel: string;
  visibleNotes: NoteNode[];
};

type VolumeYearSummary = {
  year: number;
  yearLabel: string;
  periodLabel: string;
  titleKey: string;
  title: string;
  months: VolumeMonthSummary[];
  isCurrentYear: boolean;
};

type CalendarYearSegment = {
  volumeId: string;
  year: number;
  index: number;
  count: number;
  startTs: number;
  endTs: number;
};

const getYearSegments = (year: number): CalendarYearSegment[] => {
  const yearStartTs = new Date(year, 0, 1).getTime();
  const yearEndTs = new Date(year + 1, 0, 1).getTime();
  const segments = CALENDAR_VOLUME_DEFINITIONS.flatMap(volume => {
    const startTs = Math.max(dateFromTuple(volume.start).getTime(), yearStartTs);
    const endTs = Math.min(dateFromTuple(volume.endExclusive).getTime(), yearEndTs);
    return startTs < endTs
      ? [{ volumeId: volume.id, year, startTs, endTs }]
      : [];
  }).sort((a, b) => a.startTs - b.startTs);

  return segments.map((segment, index) => ({
    ...segment,
    index,
    count: segments.length
  }));
};

const getYearSegment = (volumeId: string, year: number) => (
  getYearSegments(year).find(segment => segment.volumeId === volumeId)
);

const getYearSegmentSuffix = (segment?: CalendarYearSegment) => {
  if (!segment || segment.count <= 1) return '';
  if (segment.count === 2) return segment.index === 0 ? '上' : '下';
  return String(segment.index + 1);
};

const getYearSegmentLabel = (volumeId: string, year: number) => {
  const suffix = getYearSegmentSuffix(getYearSegment(volumeId, year));
  return suffix ? `${year}${suffix}` : `${year}年`;
};

const getYearSegmentPeriodLabel = (volumeId: string, year: number) => {
  const segment = getYearSegment(volumeId, year);
  if (!segment || segment.count <= 1) return `${year}年`;
  return `${getYearSegmentLabel(volumeId, year)}（${formatDateShort(segment.startTs)} - ${formatDateShort(segment.endTs - 1)}）`;
};

const getYearSegmentTitle = (volumeId: string, year: number) => {
  const title = getYearTitle(String(year));
  const segment = getYearSegment(volumeId, year);
  if (!title || !segment || segment.count <= 1) return title;

  const parts = title.split(/\s*[\/／]\s*/).map(part => part.trim()).filter(Boolean);
  if (parts.length < segment.count) {
    return segment.index > 0 ? getVolumeTopicTitle(volumeId) || title : title;
  }
  return parts[Math.min(segment.index, parts.length - 1)] || title;
};

const volumeYears = computed(() => {
  const start = dateFromTuple(currentVolume.value.start);
  const end = new Date(volumeEndTs.value - 1);
  const years: number[] = [];
  for (let year = start.getFullYear(); year <= end.getFullYear(); year += 1) {
    years.push(year);
  }
  return years;
});

const volumeYearMonthRepresentativeBuckets = computed(() => {
  const start = volumeStartTs.value;
  const end = volumeEndTs.value;
  const bucketsByYear = new Map<number, RepresentativeBucket[]>();

  for (const note of visibleNotes.value) {
    const ts = getNoteTime(note);
    if (!ts || ts < start || ts >= end) continue;
    const date = new Date(ts);
    const year = date.getFullYear();
    const monthIndex = date.getMonth();
    let buckets = bucketsByYear.get(year);
    if (!buckets) {
      buckets = Array.from({ length: 12 }, () => createRepresentativeBucket());
      bucketsByYear.set(year, buckets);
    }
    const bucket = buckets[monthIndex];
    if (bucket) {
      addRepresentativeNote(bucket, note, compareVolumeRepresentativePriority, {
        preferred: isPreferredVolumeNote(note)
      });
    }
  }

  return bucketsByYear;
});

const buildVolumeMonthSummaries = (monthBuckets: RepresentativeBucket[]) => (
  monthBuckets.flatMap((bucket, monthIndex) => {
    if (bucket.totalCount === 0) return [];

    const source = bucket.preferredCount > 0 ? bucket.preferredRankedNotes : bucket.rankedNotes;
    const visibleNotes = takeRepresentativeNotes(source, volumeMonthVisibleLimit.value);

    if (visibleNotes.length === 0) return [];
    return [{
      monthIndex,
      monthLabel: `${pad2(monthIndex + 1)}月`,
      visibleNotes
    }];
  })
);

const volumeYearSummaries = computed<VolumeYearSummary[]>(() => {
  const now = new Date();
  return volumeYears.value.map(year => {
    const monthBuckets = volumeYearMonthRepresentativeBuckets.value.get(year)
      || Array.from({ length: 12 }, () => createRepresentativeBucket());
    return {
      year,
      yearLabel: getYearSegmentLabel(currentVolume.value.id, year),
      periodLabel: getYearSegmentPeriodLabel(currentVolume.value.id, year),
      titleKey: String(year),
      title: getYearSegmentTitle(currentVolume.value.id, year),
      months: buildVolumeMonthSummaries(monthBuckets),
      isCurrentYear: year === now.getFullYear()
    };
  });
});

type EraYearSummary = {
  year: number;
  yearLabel: string;
  periodLabel: string;
  title: string;
  visibleNotes: NoteNode[];
  hiddenCount: number;
};

type EraVolumeSummary = {
  id: string;
  label: string;
  years: EraYearSummary[];
};

const findVolumeDefinitionByTime = (ts: number) => (
  CALENDAR_VOLUME_DEFINITIONS.find(item => {
    const start = dateFromTuple(item.start).getTime();
    const end = dateFromTuple(item.endExclusive).getTime();
    return ts >= start && ts < end;
  })
);

const eraVolumeYearRepresentativeBuckets = computed(() => {
  const bucketsByVolumeYear = new Map<string, Map<number, RepresentativeBucket>>();

  for (const note of visibleNotes.value) {
    const ts = getNoteTime(note);
    if (!ts || ts < eraStartTs.value || ts >= eraEndTs.value) continue;
    const volume = findVolumeDefinitionByTime(ts);
    if (!volume) continue;

    const year = new Date(ts).getFullYear();
    let yearMap = bucketsByVolumeYear.get(volume.id);
    if (!yearMap) {
      yearMap = new Map<number, RepresentativeBucket>();
      bucketsByVolumeYear.set(volume.id, yearMap);
    }
    let bucket = yearMap.get(year);
    if (!bucket) {
      bucket = createRepresentativeBucket();
      yearMap.set(year, bucket);
    }
    addRepresentativeNote(bucket, note, compareVolumeRepresentativePriority, {
      document: note.note_form === 'document'
    });
  }

  return bucketsByVolumeYear;
});

const eraVolumeSummaries = computed<EraVolumeSummary[]>(() => {
  return CALENDAR_VOLUME_DEFINITIONS.flatMap(volume => {
    const start = dateFromTuple(volume.start);
    const end = new Date(dateFromTuple(volume.endExclusive).getTime() - 1);
    const yearMap = eraVolumeYearRepresentativeBuckets.value.get(volume.id) || new Map<number, RepresentativeBucket>();
    const years: EraYearSummary[] = [];

    for (let year = start.getFullYear(); year <= end.getFullYear(); year += 1) {
      const title = getYearTitle(String(year));
      const bucket = yearMap.get(year);
      if (!title && !bucket?.totalCount) continue;

      const source = bucket && bucket.documentCount > 0 ? bucket.documentRankedNotes : (bucket?.rankedNotes || []);
      const candidateCount = bucket && bucket.documentCount > 0 ? bucket.documentCount : (bucket?.totalCount || 0);
      const visibleYearNotes = takeRepresentativeNotes(source, eraYearVisibleLimit.value);

      years.push({
        year,
        yearLabel: getYearSegmentLabel(volume.id, year),
        periodLabel: getYearSegmentPeriodLabel(volume.id, year),
        title: getYearSegmentTitle(volume.id, year),
        visibleNotes: visibleYearNotes,
        hiddenCount: Math.max(0, candidateCount - visibleYearNotes.length)
      });
    }

    return years.length > 0 ? [{ id: volume.id, label: volume.label, years }] : [];
  });
});

const formatYearEventDay = (note: NoteNode) => pad2(new Date(getNoteTime(note)).getDate());
const getYearEventDateKey = (note: NoteNode) => {
  const date = new Date(getNoteTime(note));
  return `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())}`;
};

const shouldShowYearEventDay = (notes: NoteNode[], index: number) => {
  if (index <= 0) return true;
  const note = notes[index];
  const previous = notes[index - 1];
  if (!note || !previous) return true;
  return getYearEventDateKey(note) !== getYearEventDateKey(previous);
};

const formatYearEventTitle = (note: NoteNode) => {
  const title = (note.title || '').trim();
  if (!title) return '无标题';
  return title.replace(/^w\d{6}\s*[:：]\s*/i, '').trim() || title;
};

const getYearEventTitle = (note: NoteNode) => {
  const date = new Date(getNoteTime(note));
  const progress = getNoteProgressPercent(note);
  const parts = [
    `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())}`,
    note.title || '无标题',
    `权重 ${note.weight || 0}`
  ];
  if (progress !== null) parts.push(`进度 ${progress}%`);
  return parts.join(' · ');
};

const getYearEventStyle = (note: NoteNode) => {
  const style = getNoteDisplayTheme(note);
  const weight = Math.max(0, Number(note.weight || 0));
  return {
    color: style.color,
    fontWeight: style.fontWeight,
    textDecoration: style.textDecoration,
    borderColor: style.borderColor,
    borderLeftColor: style.borderColor,
    borderLeftWidth: `${Math.min(5, 2 + weight)}px`,
    backgroundColor: style.backgroundColor,
    backgroundImage: style.backgroundImage,
    opacity: style.opacity,
  } as any;
};

const getYearEventProgressStyle = (note: NoteNode) => {
  const percent = getNoteProgressPercent(note) ?? 0;
  const style = getNoteDisplayTheme(note);
  return {
    width: `${percent}%`,
    backgroundColor: style.borderColor
  };
};

const getNoteStyle = (note: NoteNode) => {
  const style = getNoteDisplayTheme(note);

  const scale = getNoteWeightScaleFactor(note.weight, note.node_type, note.weight_mode);
  const baseHeight = 26;
  const height = Math.round(baseHeight * scale);

    return {
      marginBottom: '4px',
      padding: '0 6px',
      borderRadius: '4px',
      borderColor: style.borderColor,
      borderWidth: style.borderWidth,
      borderStyle: style.borderStyle,
      backgroundColor: style.backgroundColor,
      backgroundImage: style.backgroundImage,
      opacity: style.opacity,
      cursor: 'pointer',
      overflow: 'hidden',
    height: `${height}px`,
    display: 'flex',
    alignItems: 'center'
  } as any;
};

const getNoteTitleStyle = (note: NoteNode) => {
  const style = getNoteDisplayTheme(note);
  const scale = getNoteWeightScaleFactor(note.weight, note.node_type, note.weight_mode);
  const fontSize = Math.min(16, Math.max(12, Math.round(12 + (scale - 1) * 2)));

  return {
    color: style.color,
    fontWeight: style.fontWeight,
    textDecoration: style.textDecoration,
    fontSize: `${fontSize}px`,
    width: '100%',
    display: 'flex',
    alignItems: 'center',
    gap: '4px',
    minWidth: 0
  } as any;
};

const getNoteTitleTextStyle = (note: NoteNode, singleLine: boolean = false, inheritColor: boolean = false) => {
  const style = getNoteDisplayTheme(note);
  const scale = getNoteWeightScaleFactor(note.weight, note.node_type, note.weight_mode);
  // Font size: Base 12px, grow slower to allow more text
  const fontSize = Math.min(16, Math.max(12, Math.round(12 + (scale - 1) * 2)));

  const baseHeight = 26;
  const height = Math.round(baseHeight * scale);

  const lineHeight = 1.25;
  const lineHeightPx = fontSize * lineHeight;
  // Calculate max lines based on height
  const maxLines = Math.max(1, Math.floor(height / lineHeightPx));

  return {
    ...(inheritColor ? {} : { color: style.color }),
    fontWeight: style.fontWeight,
    textDecoration: style.textDecoration,
    fontSize: `${fontSize}px`,
    lineHeight: lineHeight,
    minWidth: 0,
    flex: 1,
    ...(singleLine ? {
      display: 'block',
      whiteSpace: 'nowrap',
      overflow: 'hidden',
      textOverflow: 'ellipsis',
      wordBreak: 'normal'
    } : {
      display: '-webkit-box',
      WebkitBoxOrient: 'vertical',
      WebkitLineClamp: maxLines,
      overflow: 'hidden',
      wordBreak: 'break-all'
    })
  } as any;
};

const useSplitNoteTitle = (note: NoteNode) => {
  const ratio = getNoteDisplayTheme(note).partialFillRatio;
  return typeof ratio === 'number' && ratio > 0 && ratio < 1;
};

const getNoteSplitLayerStyle = (note: NoteNode, mode: 'fill' | 'empty') => {
  const style = getNoteDisplayTheme(note);
  const ratio = style.partialFillRatio ?? 0;
  return {
    color: mode === 'fill' ? style.fillTextColor : style.emptyTextColor,
    clipPath: mode === 'fill'
      ? `inset(0 ${(100 - ratio * 100).toFixed(2)}% 0 0)`
      : `inset(0 0 0 ${(ratio * 100).toFixed(2)}%)`
  } as any;
};

const buildCalendarProgram = () => {
  const base = createFixedRangeProgram(periodStartTs.value, periodEndTs.value - 1, 'start_at');
  const fixedRules = (props.dataFilterRules || []).map(rule => normalizeNoteProgramRule(rule));
  return {
    ...base,
    rules: [
      ...base.rules,
      ...fixedRules
    ]
  };
};
const getCalendarQueryLimit = () => {
  if (calendarScale.value === 'era') return CALENDAR_ERA_QUERY_LIMIT;
  if (calendarScale.value === 'volume') return CALENDAR_VOLUME_QUERY_LIMIT;
  return CALENDAR_QUERY_LIMIT_DEFAULT;
};

const refreshData = async (options: { silent?: boolean } = {}) => {
  loading.value = true;
  try {
    await noteStore.queryNoteProgramForTab(props.tabId, buildScanNoteProgramRequest(buildCalendarProgram(), {
      limit: getCalendarQueryLimit(),
      include_edges: false,
      order_by: 'start_at',
      order_desc: false
    }));
    if (!options.silent) {
      ElMessage.success('已刷新');
    }
  } finally {
    loading.value = false;
  }
};

const applyViewProgram = () => {
  viewProgram.value = cloneNoteProgramChannel(viewProgram.value);
};

const resetViewProgram = () => {
  viewProgram.value = createIncludeAllProgram();
};

const handleNoteUpdate = () => {};

const handleNoteDelete = (noteId: string) => {
  if (currentNoteId.value === noteId) currentNoteId.value = '';
};

const handleNoteCreate = (note: NoteNode) => {
  noteStore.addNoteToTab(props.tabId, note.id);
  currentNoteId.value = noteKey(note.id);
};

const {
    paneHeight: calendarHeight,
    startResizing,
} = useResizablePane({
    initialHeight: 600,
    getAdaptiveHeight: () => {
        const vh = window.innerHeight;
        const reservedHeight = 100;
        return Math.max(400, Math.floor((vh - reservedHeight) * 0.6));
    },
    getResizeBounds: () => ({
        min: 300,
    }),
    storageKey: props.splitPaneStorageKey || 'notes:center:calendar:split-pane-height',
});
const calendarPaneHeight = computed(() => (
  calendarScale.value !== 'month'
    ? Math.max(calendarHeight.value, YEAR_VIEW_MIN_PANE_HEIGHT)
    : calendarHeight.value
));

onMounted(() => {
  void loadYearMonthMemos();
  if (showCodexWorkload.value) {
    void refreshCodexWorkloadStats();
  }
  refreshData({ silent: true });
  window.addEventListener('click', closeContextMenus);
  window.addEventListener('scroll', closeContextMenus, true);
});

onBeforeUnmount(() => {
  window.removeEventListener('click', closeContextMenus);
  window.removeEventListener('scroll', closeContextMenus, true);
});

watch(currentMonth, (value) => {
  noteStore.updateTabViewState(props.tabId, {
    currentMonth: value.toISOString()
  });
});

watch(calendarScale, (value) => {
  noteStore.updateTabViewState(props.tabId, {
    calendarScale: value
  });
});

watch([calendarScale, periodStartTs, periodEndTs], () => {
  scheduleCalendarRefresh();
});

watch(yearMonthVisibleLimit, (value) => {
  noteStore.updateTabViewState(props.tabId, {
    yearMonthVisibleLimit: value
  });
});

watch(volumeMonthVisibleLimit, (value) => {
  noteStore.updateTabViewState(props.tabId, {
    volumeMonthVisibleLimit: value
  });
});

watch(eraYearVisibleLimit, (value) => {
  noteStore.updateTabViewState(props.tabId, {
    eraYearVisibleLimit: value
  });
});

watch(viewProgram, (value) => {
  noteStore.updateTabViewState(props.tabId, {
    viewProgram: normalizeNoteProgramChannel(value)
  });
}, { deep: true });

watch(() => userStore.isAuthenticated, (isAuthenticated) => {
  if (!showCodexWorkload.value) return;
  if (isAuthenticated) {
    void refreshCodexWorkloadStats();
  } else {
    codexWorkloadTurns.value = [];
    codexHistoricalSecondsByDay.value = {};
    codexWorkloadLoaded.value = false;
    codexWorkloadError.value = '';
  }
});

</script>

<style scoped>
.calendar-notes-layout {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  background-color: #fff;
  padding: 20px;
  box-sizing: border-box;
  overflow-x: hidden;
  overflow-y: auto;
}

.filter-section {
  margin-bottom: 12px;
}

.front-filter-section {
  margin-bottom: 16px;
}

.calendar-workspace {
  min-height: 0;
  overflow: visible;
}

.calendar-workspace:not(.calendar-workspace--calendar-only) :deep(.note-main-pane) {
  overflow: hidden;
}

.calendar-workspace--calendar-only :deep(.note-main-pane) {
  height: auto !important;
  overflow: visible;
}

.calendar-workspace--calendar-only :deep(.note-main-resizer),
.calendar-workspace--calendar-only :deep(.note-editor-pane) {
  display: none;
}

.backend-filter-panel {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 14px 16px;
  border: 1px solid #ebeef5;
  border-radius: 10px;
  background: linear-gradient(180deg, #fcfdff 0%, #f7f9fc 100%);
}

.backend-filter-header {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.backend-filter-title {
  font-size: 13px;
  font-weight: 600;
  color: #303133;
}

.backend-filter-help {
  font-size: 11px;
  color: #909399;
  line-height: 1.5;
}

.backend-filter-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
  flex-wrap: wrap;
}

.backend-filter-controls {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.period-nav-buttons {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.period-nav-buttons :deep(.el-button + .el-button) {
  margin-left: 0;
}

.backend-filter-tags {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.period-limit-control {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: #606266;
  font-size: 13px;
  white-space: nowrap;
}

.period-limit-control :deep(.el-input-number) {
  width: 92px;
}

.volume-picker {
  width: 280px;
}

.calendar-container {
  height: auto;
  display: flex;
  flex-direction: column;
  border: 1px solid #ebeef5;
  border-radius: 4px;
  overflow: visible;
  min-height: 0;
}

.calendar-workspace:not(.calendar-workspace--calendar-only) .calendar-container,
.calendar-workspace:not(.calendar-workspace--calendar-only) .year-container,
.calendar-workspace:not(.calendar-workspace--calendar-only) .volume-container,
.calendar-workspace:not(.calendar-workspace--calendar-only) .era-container {
  height: 100%;
  overflow: auto;
}

.year-container,
.volume-container,
.era-container {
  height: auto;
  min-height: 0;
  display: flex;
  flex-direction: column;
  border: 1px solid #dcdfe6;
  border-radius: 4px;
  overflow: visible;
  background: #fff;
}

.era-volume-table {
  flex: none;
  min-height: 0;
  overflow: visible;
  display: block;
}

.era-volume-row {
  display: grid;
  grid-template-columns: 180px minmax(0, 1fr);
  min-height: 78px;
  border-bottom: 1px solid #ebeef5;
}

.era-volume-row:last-child {
  border-bottom: none;
}

.era-volume-label {
  border-right: 1px solid #dcdfe6;
  background: #f8fafc;
  padding: 9px 10px;
  min-width: 0;
}

.era-volume-name-button {
  appearance: none;
  border: none;
  background: transparent;
  color: #303133;
  padding: 0;
  text-align: left;
  cursor: pointer;
  font: inherit;
  font-size: 15px;
  font-weight: 700;
  line-height: 1.35;
}

.era-volume-name-segment {
  display: block;
}

.era-volume-name-button:hover {
  color: #409eff;
}

.era-year-list {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 7px;
  padding: 8px 10px;
}

.era-year-item {
  display: grid;
  grid-template-columns: max-content minmax(0, 1fr);
  gap: 5px;
  align-items: center;
  min-width: 0;
}

.era-year-button {
  appearance: none;
  border: none;
  background: transparent;
  color: #303133;
  height: 24px;
  padding: 0;
  text-align: left;
  cursor: pointer;
  font: inherit;
  font-size: 13px;
  font-weight: 700;
  line-height: 1.3;
  white-space: nowrap;
  display: inline-flex;
  align-items: center;
}

.era-year-button:hover {
  color: #409eff;
}

.era-year-content {
  min-width: 0;
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  align-items: center;
}

.era-year-title {
  flex: none;
  max-width: 160px;
  height: 24px;
  display: inline-flex;
  align-items: center;
  color: #303133;
  font-size: 13px;
  font-weight: 700;
  line-height: 1.3;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.era-event {
  max-width: min(300px, 100%);
  padding-left: 6px;
}

.volume-year-table {
  flex: none;
  min-height: 0;
  overflow: visible;
  display: block;
}

.volume-year-row {
  display: grid;
  grid-template-columns: 118px minmax(0, 1fr);
  min-height: 74px;
  border-bottom: 1px solid #ebeef5;
}

.volume-year-row:last-child {
  border-bottom: none;
}

.volume-year-row.is-current-year .volume-year-label {
  background: #ecf5ff;
}

.volume-year-label {
  border-right: 1px solid #dcdfe6;
  background: #f8fafc;
  color: #303133;
  padding: 8px;
  text-align: left;
  display: flex;
  flex-direction: column;
  justify-content: flex-start;
  gap: 5px;
  min-width: 0;
  overflow: hidden;
}

.volume-year-name-button {
  appearance: none;
  border: none;
  background: transparent;
  color: #303133;
  padding: 0;
  text-align: left;
  cursor: pointer;
  font: inherit;
  font-size: 16px;
  font-weight: 700;
  line-height: 1.2;
}

.volume-year-name-button:hover {
  color: #409eff;
}

.volume-year-title-text,
.volume-year-title-input {
  width: 100%;
  box-sizing: border-box;
  font-family: inherit;
  font-size: 12px;
  line-height: 1.35;
}

.volume-year-title-text {
  appearance: none;
  border: 1px solid transparent;
  background: transparent;
  color: #606266;
  padding: 1px 0;
  text-align: left;
  cursor: text;
  display: block;
  word-break: break-word;
}

.volume-year-title-text:hover {
  color: #303133;
}

.volume-year-title-text.is-empty {
  color: #c0c4cc;
}

.volume-year-label:not(:hover) .volume-year-title-text.is-empty .year-title-placeholder {
  opacity: 0;
}

.volume-year-title-input {
  min-height: 24px;
  border: 1px solid #409eff;
  border-radius: 3px;
  background: #fff;
  color: #303133;
  padding: 2px 4px;
  outline: none;
}

.volume-year-months {
  background: #fff;
  color: #303133;
  padding: 7px 10px;
  font-size: 13px;
  line-height: 1.35;
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
}

.volume-month-group {
  display: grid;
  grid-template-columns: 34px minmax(0, 1fr);
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.volume-month-label {
  appearance: none;
  border: none;
  background: transparent;
  color: #606266;
  height: 24px;
  padding: 0;
  text-align: left;
  cursor: pointer;
  font: inherit;
  font-size: 12px;
  font-weight: 700;
  line-height: 1.2;
  white-space: nowrap;
  display: inline-flex;
  align-items: center;
}

.volume-month-label:hover {
  color: #409eff;
}

.volume-month-events {
  min-width: 0;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px;
}

.volume-event {
  max-width: min(300px, 100%);
  padding-left: 6px;
}

.year-summary {
  flex: none;
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 12px;
  border-bottom: 1px solid #dcdfe6;
  background: #f5f7fa;
}

.year-summary-title {
  font-size: 18px;
  font-weight: 700;
  color: #303133;
}

.year-summary-heading {
  min-width: 0;
  display: inline-flex;
  align-items: baseline;
  gap: 8px;
}

.year-title-text,
.year-title-input {
  min-width: 0;
  max-width: 360px;
  box-sizing: border-box;
  font-family: inherit;
  font-size: 16px;
  line-height: 1.3;
}

.year-title-text {
  appearance: none;
  border: 1px solid transparent;
  background: transparent;
  color: #606266;
  padding: 0;
  text-align: left;
  cursor: text;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.year-title-text:hover {
  color: #303133;
}

.year-title-text.is-empty {
  color: #c0c4cc;
}

.year-summary-heading:not(:hover) .year-title-text.is-empty .year-title-placeholder {
  opacity: 0;
}

.year-title-input {
  width: 220px;
  height: 28px;
  border: 1px solid #409eff;
  border-radius: 3px;
  background: #fff;
  color: #303133;
  padding: 2px 6px;
  outline: none;
}

.year-summary-meta {
  font-size: 12px;
  color: #606266;
  white-space: nowrap;
}

.year-month-table {
  flex: none;
  min-height: 0;
  overflow: visible;
  display: block;
}

.year-month-row {
  display: grid;
  grid-template-columns: 118px minmax(0, 1fr);
  min-height: 68px;
  border-bottom: 1px solid #ebeef5;
}

.year-month-row:last-child {
  border-bottom: none;
}

.year-month-row.is-current-month .year-month-label {
  background: #ecf5ff;
}

.year-month-label {
  border-right: 1px solid #dcdfe6;
  background: #f8fafc;
  color: #303133;
  padding: 6px 8px;
  text-align: left;
  display: flex;
  flex-direction: column;
  justify-content: flex-start;
  gap: 4px;
  min-width: 0;
  overflow: hidden;
}

.year-month-name-button {
  appearance: none;
  border: none;
  background: transparent;
  color: #303133;
  padding: 0;
  text-align: left;
  cursor: pointer;
  font: inherit;
}

.year-month-name-button:hover {
  color: #409eff;
}

.year-month-heading {
  display: inline-flex;
  align-items: baseline;
  gap: 6px;
  min-width: 0;
}

.year-month-name-button {
  font-size: 15px;
  font-weight: 700;
  line-height: 1.2;
}

.year-month-memo,
.year-month-memo-input {
  width: 100%;
  box-sizing: border-box;
  border-radius: 3px;
  font-family: inherit;
  font-size: 12px;
  line-height: 1.35;
}

.year-month-memo {
  appearance: none;
  min-height: 28px;
  border: 1px solid transparent;
  background: transparent;
  color: #606266;
  padding: 2px 3px;
  text-align: left;
  cursor: text;
  display: block;
  word-break: break-word;
}

.year-month-memo:hover {
  color: #303133;
}

.year-month-memo.is-empty {
  color: #c0c4cc;
}

.year-month-label:not(:hover) .year-month-memo.is-empty .year-month-memo-placeholder {
  opacity: 0;
}

.year-month-memo-input {
  min-height: 48px;
  border: 1px solid #409eff;
  background: #fff;
  color: #303133;
  padding: 4px 5px;
  outline: none;
  resize: vertical;
}

.year-month-events {
  min-width: 0;
  padding: 6px 8px;
  display: flex;
  flex-wrap: wrap;
  align-content: flex-start;
  align-items: flex-start;
  gap: 5px 4px;
  overflow: hidden;
}

.year-event {
  position: relative;
  max-width: min(96px, 100%);
  height: 24px;
  min-width: 0;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  border: 1px solid;
  border-left-style: solid;
  border-radius: 4px;
  padding: 0 5px;
  cursor: pointer;
  overflow: hidden;
  line-height: 1;
  font-size: 12px;
  font-family: inherit;
}

.year-month-events.is-sparse .year-event {
  max-width: 100%;
}

.year-event:hover {
  transform: translateY(-1px);
  box-shadow: 0 2px 5px rgba(15, 23, 42, 0.12);
}

.year-event-day {
  flex: none;
  color: inherit;
  font-size: 11px;
  font-weight: 600;
}

.year-event-title {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  display: inline-flex;
  align-items: center;
  height: 100%;
}

.year-event-progress {
  position: absolute;
  left: 6px;
  right: 6px;
  bottom: 3px;
  height: 2px;
  background: rgba(144, 147, 153, 0.18);
}

.year-event-progress > span {
  display: block;
  height: 100%;
}

.year-more {
  height: 24px;
  padding: 0 7px;
  border: 1px dashed #c0c4cc;
  border-radius: 4px;
  background: #fff;
  color: #606266;
  font-size: 12px;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
}

.year-more:hover {
  border-color: #409eff;
  color: #409eff;
  background: #ecf5ff;
}

.weekday-header {
  display: grid;
  grid-template-columns: repeat(7, 1fr);
  background-color: #f5f7fa;
  border-bottom: 1px solid #ebeef5;
}

.weekday-cell {
  padding: 10px;
  text-align: center;
  font-weight: bold;
  color: #606266;
  border-right: 1px solid #ebeef5;
}

.weekday-cell:last-child {
  border-right: none;
}

.days-grid {
  flex: none;
  display: grid;
  grid-template-columns: repeat(7, 1fr);
  grid-auto-rows: 108px;
  overflow: hidden;
}

.day-cell {
  border-right: 1px solid #ebeef5;
  border-bottom: 1px solid #ebeef5;
  padding: 5px;
  display: flex;
  flex-direction: column;
  min-height: 0;
  overflow: hidden;
}

.day-cell:nth-child(7n) {
  border-right: none;
}

.padding-cell {
  background-color: #fcfcfc;
}

.day-cell.is-outside {
  background-color: #fcfcfc;
}

.day-cell.is-outside .solar-day {
  color: #909399;
}

.day-cell.is-outside .lunar-info {
  color: #c0c4cc;
}

.create-note-btn {
  margin-left: 4px;
  opacity: 0;
}

.day-cell:hover .create-note-btn {
  opacity: 1;
}

.day-number {
  font-size: 14px;
  font-weight: bold;
  color: #303133;
  margin-bottom: 5px;
  flex: none;
  display: flex;
  justify-content: space-between;
  align-items: center;
  min-width: 0;
}

.day-left {
  display: flex;
  align-items: center;
  gap: 4px;
  min-width: 0;
}

.codex-hours {
  flex: none;
  color: #168466;
  font-size: 11px;
  font-weight: 700;
  line-height: 1;
  white-space: nowrap;
}

.year-month-codex-hours {
  font-size: 12px;
}

.solar-day.is-rest-text {
  color: #f56c6c;
}

.day-right {
  display: flex;
  align-items: center;
  min-width: 0;
}

.lunar-info {
  font-size: 11px;
  font-weight: normal;
  color: #909399;
}

.lunar-info.is-festival {
  color: #f56c6c;
}

.holiday-marker {
  font-size: 10px;
  padding: 1px 2px;
  border-radius: 2px;
  line-height: 1;
}

.holiday-marker.is-rest {
  background-color: #fef0f0;
  color: #f56c6c;
}

.holiday-marker.is-work {
  background-color: #ecf5ff;
  color: #409eff;
}

.is-today {
  color: #409eff;
}

.today-tag {
  font-size: 10px;
  background-color: #ecf5ff;
  color: #409eff;
  padding: 1px 4px;
  border-radius: 2px;
}

.day-content {
  flex: 1;
  min-height: 0;
  overflow-x: hidden;
  overflow-y: auto;
  padding-right: 2px;
  scrollbar-gutter: stable;
  overscroll-behavior: contain;
}

.day-content::-webkit-scrollbar {
  width: 6px;
}

.day-content::-webkit-scrollbar-thumb {
  background: #dcdfe6;
  border-radius: 999px;
}

.day-content::-webkit-scrollbar-track {
  background: transparent;
}

.note-item {
  transition: all 0.2s;
}

.note-item:hover {
  transform: translateY(-1px);
  box-shadow: 0 2px 4px rgba(0,0,0,0.1);
}

.note-title {
  display: flex;
  align-items: center;
  gap: 4px;
  min-width: 0;
}

.note-title--split {
  display: grid !important;
  width: 100%;
}

.note-title-layer {
  grid-area: 1 / 1;
  display: flex;
  align-items: center;
  gap: 4px;
  min-width: 0;
  overflow: hidden;
}

.note-title-text {
  min-width: 0;
}

.day-context-menu {
  position: fixed;
  z-index: 2200;
  min-width: 168px;
  padding: 4px;
  border: 1px solid #dcdfe6;
  border-radius: 6px;
  background: #fff;
  box-shadow: 0 8px 24px rgba(15, 23, 42, 0.12);
}

.day-context-menu-item {
  width: 100%;
  height: 30px;
  padding: 0 10px;
  border: none;
  border-radius: 4px;
  background: transparent;
  color: #303133;
  font-size: 13px;
  line-height: 30px;
  text-align: left;
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 6px;
}

.day-context-menu-item:hover:not(:disabled) {
  background: #f5f7fa;
}

.day-context-menu-item:disabled {
  color: #c0c4cc;
  cursor: not-allowed;
}
</style>
