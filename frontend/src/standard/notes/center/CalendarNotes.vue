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
            <el-tag
              v-if="codexWorkloadStatusText"
              :type="codexWorkloadStatusType"
              :title="codexWorkloadError || undefined"
            >
              {{ codexWorkloadStatusText }}
            </el-tag>
            <el-button
              v-if="codexWorkloadError"
              size="small"
              text
              :icon="Refresh"
              @click="refreshCodexWorkloadStats"
            >
              重试
            </el-button>
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
                  <button
                    v-if="allowCreate"
                    class="create-note-btn"
                    type="button"
                    title="添加笔记"
                    aria-label="添加笔记"
                    @click.stop="createNoteForDay(day.date)"
                  >
                    +
                  </button>
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
import { ref, computed, nextTick, onMounted, onBeforeUnmount, watch, toRaw, defineAsyncComponent } from 'vue';
import { useRouter } from 'vue-router';
import NoteSplitView from '@/components/NoteSplitView.vue';
import NoteProgramBar from '@/components/NoteProgramBar.vue';
import {
  useNoteStore,
  type NoteNode,
  type NoteProgramChannel,
  type NoteProgramRule,
  type NoteProgramRequest,
  type NoteProgramResponse,
  type NoteCalendarSummaryBucketResponse,
  type NoteCalendarSummaryRequest,
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
import { fetchCodexWorkloadForEntry, fetchLocalCodexWorkload, type CodexWorkloadResponse, type CodexWorkloadTurn } from '@/api/codexSessions';
import { taskStore, type Device } from '@/store/taskStore';
import { ArrowLeft, ArrowRight, DArrowLeft, DArrowRight, Refresh } from '@element-plus/icons-vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import { Solar, HolidayUtil } from 'lunar-javascript';
import { getNodeDisplayStyle } from '@/utils/nodeConfig';
import NoteFormBadge from '@/components/NoteFormBadge.vue';
import { formatNoteDateShort } from '@/utils/noteDate';
import { getNoteWeightScaleFactor, NOTE_WEIGHT_DEFAULT } from '@/utils/noteWeight';
import { useResizablePane } from '@/utils/useResizablePane';
import { resolveCompletionProgressFillRatio } from '@/utils/noteProgress';
import { monitorPolledTask } from '@/utils/longTask';

const router = useRouter();
const noteStore = useNoteStore();
const userStore = useUserStore();
const NoteDetailPanel = defineAsyncComponent(() => import('@/components/NoteDetailPanel.vue'));
const props = withDefaults(defineProps<{
  tabId: string;
  active?: boolean;
  dataFilterRules?: NoteProgramRule[];
  fixedViewFilterRules?: NoteProgramRule[];
  showFrontFilter?: boolean;
  allowCreate?: boolean;
  showCodexWorkload?: boolean;
  splitPaneStorageKey?: string;
}>(), {
  allowCreate: true,
  showFrontFilter: true,
  showCodexWorkload: true,
});

const showFrontFilter = computed(() => props.showFrontFilter !== false);
const allowCreate = computed(() => props.allowCreate !== false);
const showCodexWorkload = computed(() => props.showCodexWorkload !== false);
const isActive = computed(() => props.active !== false);

const session = computed(() => noteStore.getTabSession(props.tabId));
const calendarQueryCache = new Map<string, CalendarQueryCacheEntry>();
const calendarSummaryCache = new Map<string, CalendarSummaryCacheEntry>();
const calendarBackgroundRefreshKeys = new Set<string>();
const calendarSummaryKey = ref('');
const calendarSummaryBuckets = ref<Record<string, CalendarSummaryBucketState>>({});

type CalendarScale = 'month' | 'year' | 'volume' | 'era';
type YearMonthMemoMap = Record<string, string>;
type YearTitleMap = Record<string, string>;
type CodexWorkloadDaySeconds = Record<string, number>;
type CalendarQueryCacheEntry = {
  request: NoteProgramRequest;
  response: NoteProgramResponse;
  cachedAt: number;
};
type CalendarSummaryCacheEntry = {
  request: NoteCalendarSummaryRequest;
  response: {
    buckets: NoteCalendarSummaryBucketResponse[];
    nodes: NoteNode[];
    total_nodes: number;
  };
  cachedAt: number;
};
type CalendarSummaryBucketState = {
  total_nodes: number;
  nodes: NoteNode[];
};
type CalendarRefreshOptions = {
  silent?: boolean;
  skipCache?: boolean;
  background?: boolean;
};
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
type CalendarVolumeRange = CalendarVolumeDefinition & {
  startTs: number;
  endTs: number;
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
const CALENDAR_QUERY_CACHE_LIMIT = 8;
const CALENDAR_QUERY_CACHE_TTL_MS = 120_000;
const NOTE_RENDER_META_CACHE_LIMIT = 40000;
const NOTE_SCORE_META_CACHE_LIMIT = 40000;
const CALENDAR_REFRESH_DEBOUNCE_MS = 120;
const MONTH_WEEK_ROW_MIN_HEIGHT = 108;
const MONTH_WEEK_ROW_UNIT_HEIGHT = 18;
const MONTH_WEEK_ROW_MAX_HEIGHT = 240;
const CODEX_WORKLOAD_DEVICE_CACHE_MS = 60_000;
const CODEX_WORKLOAD_HOUR_SECONDS = 3600;
const CODEX_WORKLOAD_STATS_CACHE_KEY = 'codeyun:notes:calendar:codex-workload-days:v2';
const CODEX_WORKLOAD_STATS_CACHE_VERSION = 2;
const CODEX_LOCAL_WORKLOAD_CACHE_DEVICE_ID = '__local_codex__';

const calendarPerfEnabled = typeof window !== 'undefined' && new URLSearchParams(window.location.search).has('perf');
const calendarPerfStart = calendarPerfEnabled ? performance.now() : 0;

const recordCalendarPerf = (entry: Record<string, unknown>) => {
  ((window as any).__codeyunCalendarPerfEvents ||= []).push(entry);
  document.documentElement.setAttribute(
    'data-codeyun-calendar-perf',
    JSON.stringify((window as any).__codeyunCalendarPerfEvents),
  );
};

const logCalendarPerf = (label: string, startedAt: number) => {
  if (!calendarPerfEnabled) return;
  const duration = performance.now() - startedAt;
  recordCalendarPerf({ label, duration });
  console.info(`[CalendarNotes perf] ${label}: ${duration.toFixed(1)}ms`);
};

const logCalendarPerfSinceStart = (label: string) => {
  if (!calendarPerfEnabled) return;
  const duration = performance.now() - calendarPerfStart;
  recordCalendarPerf({ label, duration, sinceStart: true });
  console.info(`[CalendarNotes perf] ${label}: ${duration.toFixed(1)}ms since module setup`);
};

const measureCalendarPerf = async <T>(label: string, task: () => Promise<T>): Promise<T> => {
  const startedAt = calendarPerfEnabled ? performance.now() : 0;
  try {
    return await task();
  } finally {
    logCalendarPerf(label, startedAt);
  }
};

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

const calendarVolumeRanges = computed<CalendarVolumeRange[]>(() => (
  CALENDAR_VOLUME_DEFINITIONS.map(volume => ({
    ...volume,
    startTs: dateFromTuple(volume.start).getTime(),
    endTs: dateFromTuple(volume.endExclusive).getTime(),
  }))
));

const getVolumeForDate = (date: Date) => {
  const ts = date.getTime();
  const matched = calendarVolumeRanges.value.find(volume => ts >= volume.startTs && ts < volume.endTs);
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

const isCodexDiaryImportActive = (status: string | undefined | null) => status === 'pending' || status === 'running';
const showCodexDiaryImportError = async (message?: string | null) => {
  await ElMessageBox.alert(message || 'Codex 总结日记导入失败', 'Codex 总结日记导入失败', {
    confirmButtonText: '知道了',
    type: 'error'
  }).catch(() => undefined);
};

const waitForCodexDiaryImportRun = async (runId: string): Promise<CodexDiaryImportRunResponse> => {
  const initial = await fetchCodexDiaryImportRun(runId);
  return monitorPolledTask<CodexDiaryImportRunResponse>({
    initial,
    poll: () => fetchCodexDiaryImportRun(runId),
    isRunning: (run) => isCodexDiaryImportActive(run.status),
    getUpdatedAt: (run) => run.heartbeat_at ?? run.updated_at,
    getError: (run) => run.status === 'failed' ? (run.error_message || 'Codex 总结日记导入失败') : '',
    pollIntervalMs: CODEX_DIARY_IMPORT_POLL_INTERVAL_MS,
    idleTimeoutMs: CODEX_DIARY_IMPORT_WAIT_TIMEOUT_MS,
  });
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
      const activeRunId = typeof detail.run_id === 'string' ? detail.run_id.trim() : '';
      if (activeRunId) {
        ElMessage.info(detail.message || 'Codex 总结日记仍在导入中，继续等待当前任务');
        return await fetchCodexDiaryImportRun(activeRunId);
      }
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
    clearCalendarQueryCache();
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
  const perfStartedAt = calendarPerfEnabled ? performance.now() : 0;
  const requestId = ++latestCodexWorkloadRequestId;
  codexWorkloadError.value = '';
  const cache = loadCodexWorkloadStatsCache();
  applyCachedCodexWorkloadSnapshot(cache);
  try {
    if (!taskStore.devices.length || Date.now() - taskStore.lastDeviceFetch > CODEX_WORKLOAD_DEVICE_CACHE_MS) {
      await taskStore.fetchDevices();
    }
    const todayStartMs = getCodexTodayStartMs();
    if (requestId !== latestCodexWorkloadRequestId) return;

    if (taskStore.lastDeviceFetchError) {
      try {
        await refreshStandaloneCodexWorkloadStats(cache, todayStartMs);
        return;
      } catch (error) {
        if (requestId !== latestCodexWorkloadRequestId) return;
        codexWorkloadTurns.value = [];
        codexWorkloadLoaded.value = true;
        codexWorkloadError.value = taskStore.lastDeviceFetchError
          || extractCodexWorkloadErrorMessage(error, '读取 Codex workload 失败');
        return;
      }
    }
    const devices = taskStore.devices.slice();
    if (requestId !== latestCodexWorkloadRequestId) return;
    if (!devices.length) {
      await refreshStandaloneCodexWorkloadStats(cache, todayStartMs);
      return;
    }

    codexHistoricalSecondsByDay.value = collectCachedCodexDaySecondsWithLocal(cache, devices);
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
    const localResult = await Promise.allSettled([
      refreshStandaloneCodexWorkloadSource(cache, todayStartMs)
    ]);
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
    codexHistoricalSecondsByDay.value = collectCachedCodexDaySecondsWithLocal(cache, devices);
    const localFulfilled = localResult[0]?.status === 'fulfilled' ? localResult[0].value : null;
    const localActiveTurns = localFulfilled
      ? (localFulfilled.workload.turns || [])
        .filter(turn => isCodexTurnActiveAfter(turn, todayStartMs))
        .map(turn => ({
          ...turn,
          id: `${CODEX_LOCAL_WORKLOAD_CACHE_DEVICE_ID}:${turn.id}`,
        }))
      : [];
    codexWorkloadTurns.value = [
      ...fulfilled.flatMap(({ device, workload }) => (
      (workload.turns || [])
        .filter(turn => isCodexTurnActiveAfter(turn, todayStartMs))
        .map(turn => ({
          ...turn,
          id: `${device.id}:${turn.id}`,
        }))
      )),
      ...localActiveTurns,
    ];
    codexWorkloadLoaded.value = true;

    if (!fulfilled.length && !localFulfilled) {
      const firstRejected = results.find((item): item is PromiseRejectedResult => item.status === 'rejected');
      const localRejected = localResult[0]?.status === 'rejected' ? localResult[0] : null;
      codexWorkloadError.value = extractCodexWorkloadErrorMessage(
        firstRejected?.reason || localRejected?.reason,
        '读取 Codex workload 失败'
      );
    }
  } catch (error) {
    if (requestId !== latestCodexWorkloadRequestId) return;
    codexWorkloadTurns.value = [];
    codexWorkloadLoaded.value = true;
    codexWorkloadError.value = extractCodexWorkloadErrorMessage(error, '读取 Codex workload 失败');
  } finally {
    logCalendarPerf('refreshCodexWorkloadStats', perfStartedAt);
  }
};

let scheduledCalendarRefreshToken = 0;
let scheduledCalendarRefreshTimer: ReturnType<typeof setTimeout> | null = null;
let inactiveCalendarRefreshPending = false;

const scheduleCalendarRefresh = () => {
  if (!isActive.value) {
    inactiveCalendarRefreshPending = true;
    return;
  }
  const token = ++scheduledCalendarRefreshToken;
  if (scheduledCalendarRefreshTimer !== null) {
    clearTimeout(scheduledCalendarRefreshTimer);
  }
  scheduledCalendarRefreshTimer = setTimeout(() => {
    scheduledCalendarRefreshTimer = null;
    if (token !== scheduledCalendarRefreshToken) return;
    void refreshData({ silent: true });
  }, CALENDAR_REFRESH_DEBOUNCE_MS);
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

const isIncludeAllMatcher = (matcher: NoteProgramRule['matcher']) => normalizeNoteProgramRule({
  action: 'include',
  matcher
}).matcher.kind === 'all';

const isAllVisibleProgram = (program: NoteProgramChannel) => {
  if (program.default && program.rules.length === 0) return true;
  if (!program.default && program.rules.length === 1) {
    const [rule] = program.rules;
    return rule?.action === 'include' && isIncludeAllMatcher(rule.matcher);
  }
  return false;
};

const isSummaryPushdownRule = (rule: NoteProgramRule) => {
  const normalized = normalizeNoteProgramRule(rule);
  const matcher = normalized.matcher;
  if (matcher.kind !== 'field' || !matcher.field) return false;

  const field = String(matcher.field);
  if (normalized.action === 'exclude' && field.startsWith('custom_fields.')) {
    return matcher.op !== 'regex_search';
  }

  return normalized.action === 'filter'
    && field !== 'id'
    && field !== 'start_at'
    && field !== 'updated_at'
    && !field.startsWith('custom_fields.')
    && matcher.op !== 'regex_search'
    && matcher.op !== 'not_contains';
};

const getSummaryPushdownRules = (program: NoteProgramChannel) => {
  if (isAllVisibleProgram(program)) return [];
  if (!program.default) return null;
  const normalizedRules = program.rules.map(rule => normalizeNoteProgramRule(rule));
  return normalizedRules.every(isSummaryPushdownRule) ? normalizedRules : null;
};

const applyCalendarViewProgram = (notes: NoteNode[], program: NoteProgramChannel) => (
  isAllVisibleProgram(program) ? notes : applyNoteProgramChannelLocally(notes, program)
);

const visibleNotes = computed(() => {
  const tabNotes = noteStore.getTabNotes(props.tabId);
  const fixedNotes = applyCalendarViewProgram(tabNotes, fixedViewProgram.value);
  return applyCalendarViewProgram(fixedNotes, viewProgram.value);
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

type NoteRenderMeta = {
  signature: string;
  displayStyle: ReturnType<typeof getNodeDisplayStyle>;
  progressRatio: number | null;
  progressPercent: number | null;
  scale: number;
  height: number;
  fontSize: number;
  maxLines: number;
};

type NoteScoreMeta = {
  signature: string;
  sourceKind: string;
  yearScore: number;
  volumeScore: number;
};

const noteRenderMetaCache = new Map<string, NoteRenderMeta>();
const noteScoreMetaCache = new Map<string, NoteScoreMeta>();

const buildNoteRenderSignature = (note: NoteNode) => {
  const rawNote = toRaw(note);
  return JSON.stringify([
    rawNote.primary_category ?? rawNote.node_type,
    rawNote.lifecycle_stage ?? rawNote.node_status,
    rawNote.color ?? '',
    rawNote.note_categories ?? rawNote.note_types ?? null,
    rawNote.completion_progress ?? null,
    rawNote.completion_progress_expr ?? null,
    rawNote.custom_fields ?? null,
    rawNote.weight ?? 0,
    rawNote.weight_mode ?? '',
  ]);
};

const pruneNoteRenderMetaCache = () => {
  while (noteRenderMetaCache.size > NOTE_RENDER_META_CACHE_LIMIT) {
    const firstKey = noteRenderMetaCache.keys().next().value;
    if (!firstKey) break;
    noteRenderMetaCache.delete(firstKey);
  }
};

const pruneNoteScoreMetaCache = () => {
  while (noteScoreMetaCache.size > NOTE_SCORE_META_CACHE_LIMIT) {
    const firstKey = noteScoreMetaCache.keys().next().value;
    if (!firstKey) break;
    noteScoreMetaCache.delete(firstKey);
  }
};

const getNoteRenderMeta = (note: NoteNode): NoteRenderMeta => {
  const rawNote = toRaw(note);
  const key = noteKey(rawNote.id);
  const signature = buildNoteRenderSignature(rawNote);
  const cached = noteRenderMetaCache.get(key);
  if (cached?.signature === signature) return cached;

  const progressRatio = resolveCompletionProgressFillRatio({
    lifecycleStage: rawNote.lifecycle_stage ?? rawNote.node_status,
    completionProgress: rawNote.completion_progress,
    completionProgressExpr: rawNote.completion_progress_expr,
    customFields: rawNote.custom_fields,
  });
  const scale = getNoteWeightScaleFactor(rawNote.weight, rawNote.node_type, rawNote.weight_mode);
  const height = Math.round(26 * scale);
  const fontSize = Math.min(16, Math.max(12, Math.round(12 + (scale - 1) * 2)));
  const maxLines = Math.max(1, Math.floor(height / (fontSize * 1.25)));
  const meta: NoteRenderMeta = {
    signature,
    displayStyle: getNodeDisplayStyle(
      rawNote.primary_category ?? rawNote.node_type,
      rawNote.lifecycle_stage ?? rawNote.node_status,
      rawNote.color,
      rawNote.note_categories ?? rawNote.note_types,
      progressRatio
    ),
    progressRatio,
    progressPercent: typeof progressRatio === 'number'
      ? Math.round(Math.min(1, Math.max(0, progressRatio)) * 100)
      : null,
    scale,
    height,
    fontSize,
    maxLines,
  };
  noteRenderMetaCache.set(key, meta);
  pruneNoteRenderMetaCache();
  return meta;
};

const clearCalendarRenderCaches = () => {
  noteRenderMetaCache.clear();
  noteScoreMetaCache.clear();
};

const getNoteDisplayTheme = (note: NoteNode) => getNoteRenderMeta(note).displayStyle;

const getNoteTime = (note: NoteNode) => note.start_at || note.created_at || 0;

const getNoteProgressRatio = (note: NoteNode) => getNoteRenderMeta(note).progressRatio;

const getNoteProgressPercent = (note: NoteNode) => {
  return getNoteRenderMeta(note).progressPercent;
};

const getYearNoteScore = (note: NoteNode) => {
  return getNoteScoreMeta(note).yearScore;
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

const buildNoteScoreSignature = (note: NoteNode) => {
  const rawNote = toRaw(note);
  return JSON.stringify([
    rawNote.weight ?? 0,
    rawNote.edge_count ?? 0,
    rawNote.out_degree ?? 0,
    rawNote.lifecycle_stage ?? rawNote.node_status ?? '',
    rawNote.completion_progress ?? null,
    rawNote.completion_progress_expr ?? null,
    rawNote.custom_fields ?? null,
    rawNote.note_form ?? '',
  ]);
};

const getNoteScoreMeta = (note: NoteNode): NoteScoreMeta => {
  const rawNote = toRaw(note);
  const key = noteKey(rawNote.id);
  const signature = buildNoteScoreSignature(rawNote);
  const cached = noteScoreMetaCache.get(key);
  if (cached?.signature === signature) return cached;

  const weight = Number.isFinite(Number(rawNote.weight)) ? Number(rawNote.weight) : 0;
  const relationCount = Number(rawNote.edge_count || 0) + Number(rawNote.out_degree || 0);
  const relationScore = Math.min(4, Math.log2(relationCount + 1));
  const progress = getNoteRenderMeta(rawNote).progressRatio;
  const progressScore = typeof progress === 'number' ? Math.min(1, Math.max(0, progress)) * 2 : 0;
  const stage = String(rawNote.lifecycle_stage ?? rawNote.node_status ?? '').toLowerCase();
  const stageScore = stage === 'done' || stage === 'predone' ? 1 : stage === 'doing' ? 0.8 : stage === 'todo' ? 0.4 : 0;
  const sourceKind = getNoteCustomFieldValue(rawNote, 'source_kind');
  let sourceBoost = 0;
  if (sourceKind.includes('chapter') || sourceKind.includes('section')) sourceBoost = 80;
  else if (sourceKind.includes('week')) sourceBoost = 50;
  else if (sourceKind.includes('child')) sourceBoost = 35;
  else if (sourceKind.includes('day_group')) sourceBoost = 16;

  const formBoost = rawNote.note_form === 'document' ? 8 : 0;
  const yearScore = weight * 10 + relationScore + progressScore + stageScore;
  const meta: NoteScoreMeta = {
    signature,
    sourceKind,
    yearScore,
    volumeScore: yearScore + weight * 12 + sourceBoost + formBoost,
  };
  noteScoreMetaCache.set(key, meta);
  pruneNoteScoreMetaCache();
  return meta;
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

const collectAllCachedCodexDaySeconds = (cache: CodexWorkloadStatsCache): CodexWorkloadDaySeconds => {
  const days: CodexWorkloadDaySeconds = {};
  for (const deviceCache of Object.values(cache.devices)) {
    for (const [dateKey, seconds] of Object.entries(deviceCache.days)) {
      days[dateKey] = (days[dateKey] || 0) + seconds;
    }
  }
  return days;
};

const collectStandaloneCachedCodexDaySeconds = (cache: CodexWorkloadStatsCache): CodexWorkloadDaySeconds => {
  const deviceCache = cache.devices[CODEX_LOCAL_WORKLOAD_CACHE_DEVICE_ID];
  return deviceCache ? { ...deviceCache.days } : collectAllCachedCodexDaySeconds(cache);
};

const collectCachedCodexDaySecondsWithLocal = (
  cache: CodexWorkloadStatsCache,
  devices: Device[],
): CodexWorkloadDaySeconds => {
  const days = collectCachedCodexDaySeconds(cache, devices);
  const localCache = cache.devices[CODEX_LOCAL_WORKLOAD_CACHE_DEVICE_ID];
  if (!localCache) return days;
  for (const [dateKey, seconds] of Object.entries(localCache.days)) {
    days[dateKey] = (days[dateKey] || 0) + seconds;
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

const applyCachedCodexWorkloadSnapshot = (cache: CodexWorkloadStatsCache) => {
  codexHistoricalSecondsByDay.value = taskStore.devices.length
    ? collectCachedCodexDaySecondsWithLocal(cache, taskStore.devices)
    : collectStandaloneCachedCodexDaySeconds(cache);
  codexWorkloadLoaded.value = true;
};

const refreshStandaloneCodexWorkloadStats = async (
  cache: CodexWorkloadStatsCache,
  todayStartMs: number,
) => {
  const { deviceCache, workload } = await refreshStandaloneCodexWorkloadSource(cache, todayStartMs);
  saveCodexWorkloadStatsCache(cache);
  codexHistoricalSecondsByDay.value = { ...deviceCache.days };
  codexWorkloadTurns.value = (workload.turns || []).filter(turn => isCodexTurnActiveAfter(turn, todayStartMs));
  codexWorkloadLoaded.value = true;
};

const refreshStandaloneCodexWorkloadSource = async (
  cache: CodexWorkloadStatsCache,
  todayStartMs: number,
) => {
  const deviceCache = cache.devices[CODEX_LOCAL_WORKLOAD_CACHE_DEVICE_ID] ?? {
    cachedThrough: '',
    days: {},
    updatedAt: 0,
  };
  cache.devices[CODEX_LOCAL_WORKLOAD_CACHE_DEVICE_ID] = deviceCache;
  const requestStartMs = resolveCodexCacheRequestStartAt(deviceCache, todayStartMs);
  const workload = await fetchLocalCodexWorkload(
    requestStartMs === undefined ? undefined : { startAt: requestStartMs / 1000 }
  );
  const historicalDays = mapToCodexWorkloadDaySeconds(
    aggregateCodexTurnsByDay(workload.turns || [], requestStartMs ?? Number.NEGATIVE_INFINITY, todayStartMs)
  );
  mergeCodexHistoricalDeviceDays(
    deviceCache,
    historicalDays,
    requestStartMs,
    todayStartMs,
    toDateStr(new Date(todayStartMs - 1))
  );
  return {
    deviceCache,
    workload,
  };
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
const codexWorkloadStatusText = computed(() => {
  if (!showCodexWorkload.value) return '';
  if (codexWorkloadError.value) {
    return userStore.isAuthenticated ? 'Codex统计读取失败' : '登录后显示Codex统计';
  }
  return '';
});
const codexWorkloadStatusType = computed(() => (
  codexWorkloadError.value && userStore.isAuthenticated ? 'warning' : 'info'
));

const getVolumeNoteScore = (note: NoteNode) => {
  return getNoteScoreMeta(note).volumeScore;
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
  Number(toRaw(note).weight || 0) > 0 || Boolean(getNoteScoreMeta(note).sourceKind)
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
  const perfStartedAt = calendarPerfEnabled ? performance.now() : 0;
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
  } finally {
    logCalendarPerf('loadYearMonthMemos', perfStartedAt);
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
  const hasSummary = calendarScale.value === 'year'
    && hasCalendarSummaryBucket(getYearSummaryBucketKey(currentYear.value, 0));
  if (hasSummary) {
    return Array.from({ length: 12 }, (_, monthIndex) => {
      const summaryBucket = getCalendarSummaryBucket(getYearSummaryBucketKey(currentYear.value, monthIndex));
      const totalCount = summaryBucket?.total_nodes ?? 0;
      const visible = (summaryBucket?.nodes || []).slice(0, yearMonthVisibleLimit.value);
      return {
        monthIndex,
        monthLabel: monthLabels[monthIndex] || `${monthIndex + 1}月`,
        memoKey: getYearMonthMemoKey(currentYear.value, monthIndex),
        totalCount,
        visibleNotes: visible,
        hiddenCount: Math.max(0, totalCount - visible.length),
        codexSeconds: getCodexSecondsForYearMonth(currentYear.value, monthIndex),
        isCurrentMonth: currentYear.value === now.getFullYear() && monthIndex === now.getMonth(),
        isSparse: totalCount > 0 && totalCount <= YEAR_MONTH_SPARSE_TOTAL_LIMIT
      };
    });
  }
  return yearMonthRepresentativeBuckets.value.map((bucket, monthIndex) => {
    const totalCount = bucket.totalCount;
    const visible = takeRepresentativeNotes(bucket.rankedNotes, yearMonthVisibleLimit.value);

    return {
      monthIndex,
      monthLabel: monthLabels[monthIndex] || `${monthIndex + 1}月`,
      memoKey: getYearMonthMemoKey(currentYear.value, monthIndex),
      totalCount,
      visibleNotes: visible,
      hiddenCount: Math.max(0, totalCount - visible.length),
      codexSeconds: getCodexSecondsForYearMonth(currentYear.value, monthIndex),
      isCurrentMonth: currentYear.value === now.getFullYear() && monthIndex === now.getMonth(),
      isSparse: totalCount > 0 && totalCount <= YEAR_MONTH_SPARSE_TOTAL_LIMIT
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

const volumeYearSegmentMap = computed(() => {
  const map = new Map<string, CalendarYearSegment>();
  const yearSegments = new Map<number, Omit<CalendarYearSegment, 'index' | 'count'>[]>();
  for (const volume of calendarVolumeRanges.value) {
    const start = new Date(volume.startTs);
    const end = new Date(volume.endTs - 1);
    for (let year = start.getFullYear(); year <= end.getFullYear(); year += 1) {
      const yearStartTs = new Date(year, 0, 1).getTime();
      const yearEndTs = new Date(year + 1, 0, 1).getTime();
      const startTs = Math.max(volume.startTs, yearStartTs);
      const endTs = Math.min(volume.endTs, yearEndTs);
      if (startTs >= endTs) continue;
      const segments = yearSegments.get(year) || [];
      segments.push({ volumeId: volume.id, year, startTs, endTs });
      yearSegments.set(year, segments);
    }
  }

  for (const segments of yearSegments.values()) {
    segments.sort((a, b) => a.startTs - b.startTs);
    segments.forEach((segment, index) => {
      map.set(`${segment.volumeId}:${segment.year}`, {
        ...segment,
        index,
        count: segments.length,
      });
    });
  }
  return map;
});

const getYearSegment = (volumeId: string, year: number) => (
  volumeYearSegmentMap.value.get(`${volumeId}:${year}`)
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
  const hasSummary = calendarScale.value === 'volume'
    && Object.keys(calendarSummaryBuckets.value).some(key => key.startsWith(`volume:${currentVolume.value.id}:`));
  return volumeYears.value.map(year => {
    const monthBuckets = hasSummary
      ? null
      : volumeYearMonthRepresentativeBuckets.value.get(year)
        || Array.from({ length: 12 }, () => createRepresentativeBucket());
    return {
      year,
      yearLabel: getYearSegmentLabel(currentVolume.value.id, year),
      periodLabel: getYearSegmentPeriodLabel(currentVolume.value.id, year),
      titleKey: String(year),
      title: getYearSegmentTitle(currentVolume.value.id, year),
      months: hasSummary
        ? Array.from({ length: 12 }, (_, monthIndex) => {
          const summaryBucket = getCalendarSummaryBucket(getVolumeSummaryBucketKey(currentVolume.value.id, year, monthIndex));
          if (!summaryBucket?.total_nodes) return null;
          const visibleNotes = summaryBucket.nodes.slice(0, volumeMonthVisibleLimit.value);
          if (visibleNotes.length === 0) return null;
          return {
            monthIndex,
            monthLabel: `${pad2(monthIndex + 1)}月`,
            visibleNotes,
          };
        }).filter((month): month is VolumeMonthSummary => Boolean(month))
        : buildVolumeMonthSummaries(monthBuckets || []),
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
  calendarVolumeRanges.value.find(item => ts >= item.startTs && ts < item.endTs)
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
  const hasSummary = calendarScale.value === 'era'
    && Object.keys(calendarSummaryBuckets.value).some(key => key.startsWith('era:'));
  return calendarVolumeRanges.value.flatMap(volume => {
    const start = new Date(volume.startTs);
    const end = new Date(volume.endTs - 1);
    const yearMap = hasSummary
      ? new Map<number, RepresentativeBucket>()
      : eraVolumeYearRepresentativeBuckets.value.get(volume.id) || new Map<number, RepresentativeBucket>();
    const years: EraYearSummary[] = [];

    for (let year = start.getFullYear(); year <= end.getFullYear(); year += 1) {
      const title = getYearTitle(String(year));
      const bucket = yearMap.get(year);
      const summaryBucket = hasSummary ? getCalendarSummaryBucket(getEraSummaryBucketKey(volume.id, year)) : undefined;
      const totalCount = summaryBucket?.total_nodes ?? bucket?.totalCount ?? 0;
      if (!title && !totalCount) continue;

      const source = bucket && bucket.documentCount > 0 ? bucket.documentRankedNotes : (bucket?.rankedNotes || []);
      const candidateCount = summaryBucket?.total_nodes ?? (bucket && bucket.documentCount > 0 ? bucket.documentCount : (bucket?.totalCount || 0));
      const visibleYearNotes = summaryBucket
        ? summaryBucket.nodes.slice(0, eraYearVisibleLimit.value)
        : takeRepresentativeNotes(source, eraYearVisibleLimit.value);

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
  const style = getNoteRenderMeta(note).displayStyle;
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
  const meta = getNoteRenderMeta(note);
  const percent = meta.progressPercent ?? 0;
  const style = meta.displayStyle;
  return {
    width: `${percent}%`,
    backgroundColor: style.borderColor
  };
};

const getNoteStyle = (note: NoteNode) => {
  const meta = getNoteRenderMeta(note);
  const style = meta.displayStyle;

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
    height: `${meta.height}px`,
    display: 'flex',
    alignItems: 'center'
  } as any;
};

const getNoteTitleStyle = (note: NoteNode) => {
  const meta = getNoteRenderMeta(note);
  const style = meta.displayStyle;

  return {
    color: style.color,
    fontWeight: style.fontWeight,
    textDecoration: style.textDecoration,
    fontSize: `${meta.fontSize}px`,
    width: '100%',
    display: 'flex',
    alignItems: 'center',
    gap: '4px',
    minWidth: 0
  } as any;
};

const getNoteTitleTextStyle = (note: NoteNode, singleLine: boolean = false, inheritColor: boolean = false) => {
  const meta = getNoteRenderMeta(note);
  const style = meta.displayStyle;

  return {
    ...(inheritColor ? {} : { color: style.color }),
    fontWeight: style.fontWeight,
    textDecoration: style.textDecoration,
    fontSize: `${meta.fontSize}px`,
    lineHeight: 1.25,
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
      WebkitLineClamp: meta.maxLines,
      overflow: 'hidden',
      wordBreak: 'break-all'
    })
  } as any;
};

const useSplitNoteTitle = (note: NoteNode) => {
  const ratio = getNoteRenderMeta(note).displayStyle.partialFillRatio;
  return typeof ratio === 'number' && ratio > 0 && ratio < 1;
};

const getNoteSplitLayerStyle = (note: NoteNode, mode: 'fill' | 'empty') => {
  const style = getNoteRenderMeta(note).displayStyle;
  const ratio = style.partialFillRatio ?? 0;
  return {
    color: mode === 'fill' ? style.fillTextColor : style.emptyTextColor,
    clipPath: mode === 'fill'
      ? `inset(0 ${(100 - ratio * 100).toFixed(2)}% 0 0)`
      : `inset(0 0 0 ${(ratio * 100).toFixed(2)}%)`
  } as any;
};

const toApiSeconds = (value: number) => value / 1000;

const getYearSummaryBucketKey = (year: number, monthIndex: number) => `year:${year}:${pad2(monthIndex + 1)}`;
const getVolumeSummaryBucketKey = (volumeId: string, year: number, monthIndex: number) => (
  `volume:${volumeId}:${year}:${pad2(monthIndex + 1)}`
);
const getEraSummaryBucketKey = (volumeId: string, year: number) => `era:${volumeId}:${year}`;

const getCalendarSummaryBucket = (key: string): CalendarSummaryBucketState | undefined => (
  calendarSummaryBuckets.value[key]
);

const hasCalendarSummaryBucket = (key: string) => (
  Object.prototype.hasOwnProperty.call(calendarSummaryBuckets.value, key)
);

const applyCalendarSummaryBuckets = (key: string, buckets: NoteCalendarSummaryBucketResponse[]) => {
  const next: Record<string, CalendarSummaryBucketState> = {};
  for (const bucket of buckets) {
    next[bucket.key] = {
      total_nodes: Number(bucket.total_nodes || 0),
      nodes: bucket.nodes || [],
    };
  }
  calendarSummaryKey.value = key;
  calendarSummaryBuckets.value = next;
};

const clearCalendarSummary = () => {
  calendarSummaryKey.value = '';
  calendarSummaryBuckets.value = {};
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

const buildCalendarQueryRequest = () => buildScanNoteProgramRequest(buildCalendarProgram(), {
  limit: getCalendarQueryLimit(),
  include_edges: false,
  order_by: 'start_at',
  order_desc: false
});

const canUseCalendarSummaryQuery = () => (
  calendarScale.value !== 'month'
  && getSummaryPushdownRules({
    default: true,
    rules: (props.dataFilterRules || []).map(rule => normalizeNoteProgramRule(rule)),
  }) !== null
  && getSummaryPushdownRules(fixedViewProgram.value) !== null
  && getSummaryPushdownRules(viewProgram.value) !== null
);

const buildCalendarSummaryRequest = (query: NoteProgramRequest): NoteCalendarSummaryRequest | null => {
  if (!canUseCalendarSummaryQuery()) return null;
  const fixedPushdownRules = getSummaryPushdownRules(fixedViewProgram.value) || [];
  const viewPushdownRules = getSummaryPushdownRules(viewProgram.value) || [];
  const summaryQuery: NoteProgramRequest = {
    ...query,
    program: {
      ...query.program,
      select: {
        ...query.program.select,
        rules: [
          ...query.program.select.rules,
          ...fixedPushdownRules,
          ...viewPushdownRules,
        ],
      },
    },
  };
  const limit = REPRESENTATIVE_POOL_LIMIT;
  if (calendarScale.value === 'year') {
    return {
      query: summaryQuery,
      buckets: Array.from({ length: 12 }, (_, monthIndex) => {
        const start = new Date(currentYear.value, monthIndex, 1).getTime();
        const end = new Date(currentYear.value, monthIndex + 1, 1).getTime() - 1;
        return {
          key: getYearSummaryBucketKey(currentYear.value, monthIndex),
          start_at: toApiSeconds(start),
          end_at: toApiSeconds(end),
          mode: 'year',
          limit,
        };
      }),
    };
  }
  if (calendarScale.value === 'volume') {
    return {
      query: summaryQuery,
      buckets: volumeYears.value.flatMap(year => (
        Array.from({ length: 12 }, (_, monthIndex) => {
          const monthStart = new Date(year, monthIndex, 1).getTime();
          const monthEnd = new Date(year, monthIndex + 1, 1).getTime() - 1;
          const start = Math.max(monthStart, volumeStartTs.value);
          const end = Math.min(monthEnd, volumeEndTs.value - 1);
          if (start > end) return null;
          return {
            key: getVolumeSummaryBucketKey(currentVolume.value.id, year, monthIndex),
            start_at: toApiSeconds(start),
            end_at: toApiSeconds(end),
            mode: 'volume' as const,
            limit,
          };
        }).filter((bucket): bucket is NonNullable<typeof bucket> => Boolean(bucket))
      )),
    };
  }
  return {
    query: summaryQuery,
    buckets: calendarVolumeRanges.value.flatMap(volume => {
      const start = new Date(volume.startTs);
      const end = new Date(volume.endTs - 1);
      const buckets = [];
      for (let year = start.getFullYear(); year <= end.getFullYear(); year += 1) {
        const yearStart = new Date(year, 0, 1).getTime();
        const yearEnd = new Date(year + 1, 0, 1).getTime() - 1;
        const bucketStart = Math.max(yearStart, volume.startTs);
        const bucketEnd = Math.min(yearEnd, volume.endTs - 1);
        if (bucketStart > bucketEnd) continue;
        buckets.push({
          key: getEraSummaryBucketKey(volume.id, year),
          start_at: toApiSeconds(bucketStart),
          end_at: toApiSeconds(bucketEnd),
          mode: 'era' as const,
          limit,
        });
      }
      return buckets;
    }),
  };
};

const getCalendarQueryCacheKey = (request: NoteProgramRequest) => JSON.stringify({
  scale: calendarScale.value,
  request,
});

const getCalendarSummaryCacheKey = (request: NoteCalendarSummaryRequest) => JSON.stringify({
  scale: calendarScale.value,
  request,
});

const rememberCalendarQuery = (
  key: string,
  request: NoteProgramRequest,
  response: NoteProgramResponse,
) => {
  if (calendarQueryCache.has(key)) calendarQueryCache.delete(key);
  calendarQueryCache.set(key, { request, response, cachedAt: Date.now() });
  while (calendarQueryCache.size > CALENDAR_QUERY_CACHE_LIMIT) {
    const firstKey = calendarQueryCache.keys().next().value;
    if (!firstKey) break;
    calendarQueryCache.delete(firstKey);
  }
};

const rememberCalendarSummary = (
  key: string,
  request: NoteCalendarSummaryRequest,
  response: CalendarSummaryCacheEntry['response'],
) => {
  if (calendarSummaryCache.has(key)) calendarSummaryCache.delete(key);
  calendarSummaryCache.set(key, { request, response, cachedAt: Date.now() });
  while (calendarSummaryCache.size > CALENDAR_QUERY_CACHE_LIMIT) {
    const firstKey = calendarSummaryCache.keys().next().value;
    if (!firstKey) break;
    calendarSummaryCache.delete(firstKey);
  }
};

const clearCalendarQueryCache = () => {
  calendarQueryCache.clear();
  calendarSummaryCache.clear();
  calendarBackgroundRefreshKeys.clear();
};

const queueCalendarBackgroundRefresh = (key: string) => {
  if (!isActive.value) {
    inactiveCalendarRefreshPending = true;
    return;
  }
  if (calendarBackgroundRefreshKeys.has(key)) return;
  calendarBackgroundRefreshKeys.add(key);
  void nextTick(async () => {
    try {
      await refreshData({ silent: true, skipCache: true, background: true });
    } finally {
      calendarBackgroundRefreshKeys.delete(key);
    }
  });
};

const refreshData = async (options: CalendarRefreshOptions = {}) => {
  const perfStartedAt = calendarPerfEnabled ? performance.now() : 0;
  const request = buildCalendarQueryRequest();
  const cacheKey = getCalendarQueryCacheKey(request);
  const summaryRequest = buildCalendarSummaryRequest(request);
  let usedCache = false;
  if (!options.background) {
    loading.value = true;
  }
  try {
    if (summaryRequest) {
      const summaryCacheKey = getCalendarSummaryCacheKey(summaryRequest);
      const cachedSummary = calendarSummaryCache.get(summaryCacheKey);
      if (!options.skipCache && cachedSummary) {
        if (Date.now() - cachedSummary.cachedAt <= CALENDAR_QUERY_CACHE_TTL_MS) {
          noteStore.applyQueryResponseToTab(props.tabId, cachedSummary.request.query, {
            nodes: cachedSummary.response.nodes,
            edges: [],
            total_nodes: cachedSummary.response.total_nodes,
            total_edges: 0,
          });
          applyCalendarSummaryBuckets(summaryCacheKey, cachedSummary.response.buckets);
          usedCache = true;
          queueCalendarBackgroundRefresh(summaryCacheKey);
          return;
        }
        calendarSummaryCache.delete(summaryCacheKey);
      }

      const summaryResult = await noteStore.queryNoteCalendarSummaryForTab(props.tabId, summaryRequest);
      if (summaryResult?.data) {
        rememberCalendarSummary(summaryCacheKey, summaryRequest, summaryResult.data);
        applyCalendarSummaryBuckets(summaryCacheKey, summaryResult.data.buckets);
        if (!options.silent) {
          ElMessage.success('已刷新');
        }
        return;
      }
    }

    const cached = calendarQueryCache.get(cacheKey);
    if (!options.skipCache && cached) {
      if (Date.now() - cached.cachedAt <= CALENDAR_QUERY_CACHE_TTL_MS) {
        noteStore.applyQueryResponseToTab(props.tabId, cached.request, cached.response);
        clearCalendarSummary();
        usedCache = true;
        queueCalendarBackgroundRefresh(cacheKey);
        return;
      }
      calendarQueryCache.delete(cacheKey);
    }

    const result = await noteStore.queryNoteProgramForTab(props.tabId, request);
    if (result?.data) {
      clearCalendarSummary();
      rememberCalendarQuery(cacheKey, request, result.data);
    }
    if (!options.silent) {
      ElMessage.success('已刷新');
    }
  } finally {
    if (!options.background) {
      loading.value = false;
    }
    logCalendarPerf(usedCache ? 'refreshData(cache-hit)' : 'refreshData(query-program)', perfStartedAt);
  }
};

const applyViewProgram = () => {
  viewProgram.value = cloneNoteProgramChannel(viewProgram.value);
};

const resetViewProgram = () => {
  viewProgram.value = createIncludeAllProgram();
};

const handleNoteUpdate = () => {
  clearCalendarQueryCache();
  clearCalendarRenderCaches();
};

const handleNoteDelete = (noteId: string) => {
  clearCalendarQueryCache();
  clearCalendarRenderCaches();
  if (currentNoteId.value === noteId) currentNoteId.value = '';
};

const handleNoteCreate = (note: NoteNode) => {
  clearCalendarQueryCache();
  clearCalendarRenderCaches();
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
  logCalendarPerfSinceStart('mounted');
  void measureCalendarPerf('nextTick after mounted', () => nextTick());
  void loadYearMonthMemos();
  if (showCodexWorkload.value) {
    applyCachedCodexWorkloadSnapshot(loadCodexWorkloadStatsCache());
    if (isActive.value) {
      void refreshCodexWorkloadStats();
    } else {
      inactiveCalendarRefreshPending = true;
    }
  }
  if (isActive.value) {
    refreshData({ silent: true });
  } else {
    inactiveCalendarRefreshPending = true;
  }
  window.addEventListener('click', closeContextMenus);
  window.addEventListener('scroll', closeContextMenus, true);
});

onBeforeUnmount(() => {
  if (scheduledCalendarRefreshTimer !== null) {
    clearTimeout(scheduledCalendarRefreshTimer);
    scheduledCalendarRefreshTimer = null;
  }
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
  if (!isActive.value) {
    inactiveCalendarRefreshPending = true;
    return;
  }
  if (isAuthenticated) {
    void refreshCodexWorkloadStats();
  } else {
    codexWorkloadTurns.value = [];
    applyCachedCodexWorkloadSnapshot(loadCodexWorkloadStatsCache());
    codexWorkloadError.value = '';
  }
});

watch(isActive, (active) => {
  if (!active) return;
  if (inactiveCalendarRefreshPending) {
    inactiveCalendarRefreshPending = false;
    void refreshData({ silent: true });
  } else if (noteStore.getTabNotes(props.tabId).length === 0) {
    void refreshData({ silent: true });
  }
  if (showCodexWorkload.value && !codexWorkloadLoaded.value) {
    void refreshCodexWorkloadStats();
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
  flex: none;
  width: 18px;
  height: 18px;
  margin-left: 0;
  padding: 0;
  border: none;
  border-radius: 50%;
  background: transparent;
  color: #409eff;
  cursor: pointer;
  font-size: 16px;
  font-weight: 600;
  line-height: 18px;
  text-align: center;
  opacity: 0.88;
  transition: opacity 0.15s ease, background-color 0.15s ease, color 0.15s ease;
}

.day-cell:hover .create-note-btn,
.create-note-btn:focus-visible {
  opacity: 1;
  background-color: #ecf5ff;
  outline: none;
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
  max-width: calc(100% - 34px);
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
