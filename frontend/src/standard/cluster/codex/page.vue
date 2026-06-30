<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { ChatDotRound, QuestionFilled, RefreshRight } from '@element-plus/icons-vue';
import api from '@/api';
import StandardPagination from '@/components/StandardPagination.vue';
import {
  fetchCodexOverviewForEntry,
  fetchCodexThreadDetailForEntry,
  fetchCodexThreadMessageImagesForEntry,
  fetchCodexWorkloadForEntry,
  type CodexOverviewResponse,
  type CodexProjectGroup,
  type CodexThreadMessage,
  type CodexThreadMessageImage,
  type CodexThreadDetailResponse,
  type CodexThreadSummary,
  type CodexWorkloadTurn,
  type CodexWorkloadResponse,
} from '@/api/codexSessions';
import { taskStore, type Device } from '@/store/taskStore';
import { ensureNoteTypePaletteLoaded, type NoteTypePaletteItem } from '@/utils/nodeConfig';
import { useResizablePane } from '@/utils/useResizablePane';

const route = useRoute();
const router = useRouter();

const isLoadingDevices = ref(false);
const isLoadingOverview = ref(false);
const isLoadingDetail = ref(false);
const isLoadingWorkload = ref(false);
const overview = ref<CodexOverviewView | null>(null);
const threadDetail = ref<CodexThreadDetailResponseView | null>(null);
const workload = ref<CodexWorkloadView | null>(null);
const workloadGranularity = ref<CodexWorkloadGranularity>('day');
const workloadMetric = ref<CodexWorkloadMetric>('duration');
const workloadDateRange = ref<[Date, Date] | null>(null);
const overviewError = ref('');
const detailError = ref('');
const workloadError = ref('');
const messageImageCache = ref<Record<string, CodexThreadMessageImage[]>>({});
const messageImageErrors = ref<Record<string, string>>({});
const messageImageLoading = ref<Record<string, boolean>>({});
const noteTypePaletteItems = ref<NoteTypePaletteItem[]>([]);
const noteProjectColorHints = ref<CodexNoteProjectColorHint[]>([]);
const isProcessExpanded = ref(false);
const selectedEntryId = ref('');
const selectedThreadSourceEntryId = ref('');
const selectedThreadRootDir = ref<string | undefined>();
const currentThreadPage = ref(1);
const rootDirInput = ref('');
const selectedThreadId = ref('');
const selectedMessageSeq = ref<number | null>(null);
const messageScrollbarRef = ref<{ setScrollTop?: (value: number) => void } | null>(null);
const messageWorkspaceRef = ref<HTMLElement | null>(null);

const MESSAGE_OUTLINE_STORAGE_KEY = 'codeyun:cluster:codex:message-outline-height:v1';
const MESSAGE_RESIZER_HEIGHT = 12;
const MESSAGE_WORKSPACE_FALLBACK_HEIGHT = 360;
const WORKLOAD_CHART_WIDTH = 1000;
const WORKLOAD_CHART_HEIGHT = 136;
const WORKLOAD_MAX_TICKS = 5;
const HOUR_MS = 3_600_000;
const DAY_MS = 86_400_000;
const WORKLOAD_CHART_PADDING = {
  top: 10,
  right: 12,
  bottom: 26,
  left: 12,
};
const ALL_DEVICES_ENTRY_ID = '__all__';
const THREAD_PAGE_SIZE = 100;
const CODEX_REMOTE_OVERVIEW_SOFT_TIMEOUT_MS = 4000;
const CODEX_REMOTE_WORKLOAD_SOFT_TIMEOUT_MS = 6000;
const PROJECT_COLOR_PALETTE = [
  '#4f8ff7',
  '#5bb974',
  '#f0a24d',
  '#8a6fe8',
  '#e87979',
  '#4fb7b0',
  '#d4b443',
  '#5f7ee6',
  '#d870a8',
  '#7cb058',
];
const DAY_FADE_STOPS = [
  { days: 0, factor: 1 },
  { days: 1, factor: 0.9 },
  { days: 7, factor: 0.8 },
  { days: 30, factor: 0.65 },
  { days: 365, factor: 0.5 },
];
const WORKLOAD_GRANULARITY_OPTIONS: Array<{ label: string; value: CodexWorkloadGranularity }> = [
  { label: '实时区间', value: 'detail' },
  { label: '按天', value: 'day' },
  { label: '按周', value: 'week' },
  { label: '按月', value: 'month' },
];
const WORKLOAD_AGGREGATED_METRIC_OPTIONS = [
  { label: '轮次', value: 'turn_count' as const },
  { label: '累计工时', value: 'duration' as const },
  { label: '峰值并发', value: 'concurrency' as const },
];
const WORKLOAD_METRIC_OPTIONS: Record<CodexWorkloadGranularity, Array<{ label: string; value: CodexWorkloadMetric }>> = {
  detail: [
    { label: '并发工作数', value: 'concurrency' },
    { label: '累计工时', value: 'duration' },
  ],
  day: WORKLOAD_AGGREGATED_METRIC_OPTIONS,
  week: WORKLOAD_AGGREGATED_METRIC_OPTIONS,
  month: WORKLOAD_AGGREGATED_METRIC_OPTIONS,
};

interface CodexMessageSummaryItem {
  key: string;
  role: CodexThreadMessage['role'];
  turnIndex: number | null;
  displayMessage: CodexThreadMessage;
  allMessages: CodexThreadMessage[];
  processMessages: CodexThreadMessage[];
  hasExplicitResult: boolean;
}

interface CodexWorkloadChartBar {
  key: string;
  clipId: string;
  x: number;
  y: number;
  width: number;
  height: number;
  title: string;
  slices: CodexWorkloadChartBarSlice[];
}

interface CodexWorkloadChartBarSlice {
  key: string;
  y: number;
  height: number;
  fill: string;
  title: string;
}

interface CodexWorkloadChartTick {
  key: string;
  x: number;
  label: string;
  showLabel?: boolean;
}

interface CodexWorkloadChartGuide {
  key: string;
  y: number;
  label: string;
}

interface CodexWorkloadChartModel {
  viewBox: string;
  chartWidth: number;
  chartHeight: number;
  baselineY: number;
  bars: CodexWorkloadChartBar[];
  guides: CodexWorkloadChartGuide[];
  ticks: CodexWorkloadChartTick[];
  hasActivity: boolean;
  peakValue: number;
  unitLabel: string;
}

interface CodexMessageRenderBlock {
  key: string;
  type: 'text' | 'image' | 'image-placeholder';
  text?: string;
  image?: CodexThreadMessageImage;
  imageIndex?: number;
}

interface CodexDeviceSource {
  source_entry_id?: string;
  source_device_name?: string;
  source_root_dir?: string;
}

interface CodexNoteCategoryAssignment {
  key?: string | null;
  weight?: number | null;
}

interface CodexNoteProjectColorHint {
  title: string;
  categoryKey: string;
  updatedAt: number;
}

interface CodexNoteProjectColorNode {
  title?: string | null;
  primary_category?: string | null;
  note_categories?: CodexNoteCategoryAssignment[] | null;
  node_type?: string | null;
  note_types?: CodexNoteCategoryAssignment[] | null;
  updated_at?: number | null;
}

type CodexThreadSummaryView = CodexThreadSummary & CodexDeviceSource;
type CodexThreadDetailResponseView = Omit<CodexThreadDetailResponse, 'thread'> & {
  thread: CodexThreadDetailResponse['thread'] & CodexDeviceSource;
};
type CodexProjectGroupView = Omit<CodexProjectGroup, 'threads'> & {
  threads: CodexThreadSummaryView[];
};
type CodexOverviewView = Omit<CodexOverviewResponse, 'groups'> & {
  groups: CodexProjectGroupView[];
  root_dirs?: string[];
  returned_threads?: number;
  has_more?: boolean;
};
type CodexWorkloadTurnView = CodexWorkloadTurn & CodexDeviceSource;
type CodexWorkloadView = Omit<CodexWorkloadResponse, 'turns'> & {
  turns: CodexWorkloadTurnView[];
  root_dirs?: string[];
};

type CodexAggregatedWorkloadGranularity = 'day' | 'week' | 'month';
type CodexWorkloadGranularity = 'detail' | CodexAggregatedWorkloadGranularity;
type CodexWorkloadMetric = 'concurrency' | 'turn_count' | 'duration';
type CodexWorkloadTickUnit = 'hour' | 'day' | 'week' | 'month';

let latestOverviewRequestId = 0;
let latestDetailRequestId = 0;
let latestWorkloadRequestId = 0;

const devices = computed(() => taskStore.devices);
const canLoad = computed(() => Boolean(selectedEntryId.value));
const showDeviceEmptyState = computed(() => !isLoadingDevices.value && !devices.value.length);
const isAllDevicesMode = computed(() => selectedEntryId.value === ALL_DEVICES_ENTRY_ID);

const formatDeviceLabel = (device?: Pick<Device, 'name' | 'device_id'> | null) => (
  device?.name || device?.device_id || ''
);

const selectedDevice = computed(() => (
  devices.value.find((device) => device.id === selectedEntryId.value) ?? null
));

const selectedSourceDevices = computed<Device[]>(() => {
  if (isAllDevicesMode.value) return devices.value.slice();
  return selectedDevice.value ? [selectedDevice.value] : [];
});

const getCodexOverviewTimeoutMs = (device: Device) => (
  device.mode === 'remote' ? CODEX_REMOTE_OVERVIEW_SOFT_TIMEOUT_MS : undefined
);

const getCodexWorkloadTimeoutMs = (device: Device) => (
  device.mode === 'remote' ? CODEX_REMOTE_WORKLOAD_SOFT_TIMEOUT_MS : undefined
);

const toTimestamp = (value?: number | string | null) => {
  if (value === null || value === undefined || value === '') return 0;
  if (typeof value === 'number') {
    return value < 1_000_000_000_000 ? value * 1000 : value;
  }
  const numeric = Number(value);
  if (!Number.isNaN(numeric)) {
    return numeric < 1_000_000_000_000 ? numeric * 1000 : numeric;
  }
  const parsed = new Date(value).getTime();
  return Number.isNaN(parsed) ? 0 : parsed;
};

const toSecondTimestamp = (value?: number | string | null) => toTimestamp(value) / 1000;

const getThreadSourceEntryId = (thread?: CodexDeviceSource | null) => (
  thread?.source_entry_id || selectedEntryId.value
);

const getThreadSourceRootDir = (thread?: CodexDeviceSource | null) => (
  thread?.source_root_dir || normalizeRootDirForRequest()
);

const getThreadSelectionKey = (thread: CodexThreadSummaryView) => [
  getThreadSourceEntryId(thread),
  thread.id,
].join('|');

const isSelectedThread = (thread: CodexThreadSummaryView) => (
  selectedThreadId.value === thread.id
  && (selectedThreadSourceEntryId.value || selectedEntryId.value) === getThreadSourceEntryId(thread)
);

const withThreadSource = (
  thread: CodexThreadSummary,
  device: Device,
  rootDir?: string | null,
): CodexThreadSummaryView => ({
  ...thread,
  source_entry_id: device.id,
  source_device_name: formatDeviceLabel(device),
  source_root_dir: rootDir || undefined,
});

const buildOverviewGroupKey = (thread: CodexThreadSummaryView) => [
  thread.project_label || thread.cwd || thread.id,
  thread.project_secondary_label || '',
].join('|');

function getThreadPageOffset(page = currentThreadPage.value) {
  return Math.max(0, page - 1) * THREAD_PAGE_SIZE;
}

const sortCodexThreadViews = (threads: CodexThreadSummaryView[]) => (
  threads.slice().sort((left, right) => {
    const updatedDiff = toTimestamp(right.updated_at) - toTimestamp(left.updated_at);
    if (updatedDiff !== 0) return updatedDiff;

    const createdDiff = toTimestamp(right.created_at) - toTimestamp(left.created_at);
    if (createdDiff !== 0) return createdDiff;

    return getThreadSelectionKey(right).localeCompare(getThreadSelectionKey(left));
  })
);

const buildOverviewFromThreads = (
  sourceThreads: CodexThreadSummaryView[],
  base: Omit<CodexOverviewView, 'groups'>,
): CodexOverviewView => {
  const groupsByKey = new Map<string, CodexProjectGroupView>();

  sourceThreads.forEach((thread) => {
    const groupKey = buildOverviewGroupKey(thread);
    const targetGroup = groupsByKey.get(groupKey) ?? {
      key: groupKey,
      label: thread.project_label,
      secondary_label: thread.project_secondary_label,
      cwd: thread.cwd ?? null,
      workspace_root: thread.workspace_root ?? null,
      thread_count: 0,
      archived_thread_count: 0,
      latest_updated_at: null,
      threads: [],
    };
    targetGroup.threads.push(thread);
    groupsByKey.set(groupKey, targetGroup);
  });

  const groups = Array.from(groupsByKey.values()).map((group) => {
    const threads = sortCodexThreadViews(group.threads);
    return {
      ...group,
      latest_updated_at: threads[0]?.updated_at ?? null,
      thread_count: threads.length,
      archived_thread_count: threads.filter(thread => thread.archived).length,
      threads,
    };
  }).sort((left, right) => {
    const updatedDiff = toTimestamp(right.latest_updated_at) - toTimestamp(left.latest_updated_at);
    if (updatedDiff !== 0) return updatedDiff;
    return right.label.localeCompare(left.label, 'zh-CN');
  });

  return {
    ...base,
    groups,
  };
};

const buildMergedOverview = (
  entries: Array<{ device: Device; overview: CodexOverviewResponse }>,
  page: number,
): CodexOverviewView => {
  const rootDirs = Array.from(new Set(entries.map(item => item.overview.root_dir).filter(Boolean)));
  const loadedThreads = entries.flatMap(({ device, overview: itemOverview }) => (
    itemOverview.groups.flatMap(group => (
      group.threads.map(thread => withThreadSource(thread, device, itemOverview.root_dir))
    ))
  ));
  const totalThreads = entries.reduce((sum, item) => sum + (item.overview.total_threads || 0), 0);
  const pageOffset = getThreadPageOffset(page);
  const pageThreads = sortCodexThreadViews(loadedThreads).slice(pageOffset, pageOffset + THREAD_PAGE_SIZE);
  return buildOverviewFromThreads(pageThreads, {
    root_dir: rootDirs.length === 1 ? rootDirs[0] : `${entries.length} 台设备各自默认 .codex`,
    default_root_dir: '',
    state_db_path: '',
    session_index_path: '',
    global_state_path: '',
    total_groups: entries.reduce((sum, item) => sum + (item.overview.total_groups || 0), 0),
    total_threads: totalThreads,
    archived_threads: entries.reduce((sum, item) => sum + (item.overview.archived_threads || 0), 0),
    thread_offset: pageOffset,
    thread_limit: THREAD_PAGE_SIZE,
    returned_threads: pageThreads.length,
    has_more: pageOffset + pageThreads.length < totalThreads,
    root_dirs: rootDirs,
  });
};

const buildWorkloadSegmentsFromTurns = (turns: CodexWorkloadTurnView[]): CodexWorkloadResponse['segments'] => {
  if (!turns.length) return [];

  const startCounts = new Map<number, number>();
  const endCounts = new Map<number, number>();
  const points = new Set<number>();
  turns.forEach((turn) => {
    const startAt = Number(turn.start_at || 0);
    const endAt = Math.max(Number(turn.end_at || startAt), startAt);
    startCounts.set(startAt, (startCounts.get(startAt) ?? 0) + 1);
    endCounts.set(endAt, (endCounts.get(endAt) ?? 0) + 1);
    points.add(startAt);
    points.add(endAt);
  });

  const sortedPoints = Array.from(points).sort((left, right) => left - right);
  const segments: CodexWorkloadResponse['segments'] = [];
  let concurrency = 0;
  sortedPoints.slice(0, -1).forEach((point, index) => {
    concurrency = Math.max(0, concurrency - (endCounts.get(point) ?? 0) + (startCounts.get(point) ?? 0));
    const nextPoint = sortedPoints[index + 1];
    if (nextPoint <= point || concurrency <= 0) return;
    segments.push({
      start_at: point,
      end_at: nextPoint,
      duration_seconds: nextPoint - point,
      concurrency,
    });
  });
  return segments;
};

const buildMergedWorkload = (
  entries: Array<{ device: Device; workload: CodexWorkloadResponse }>,
): CodexWorkloadView => {
  const rootDirs = Array.from(new Set(entries.map(item => item.workload.root_dir).filter(Boolean)));
  const turns = entries.flatMap(({ device, workload: itemWorkload }) => (
    itemWorkload.turns.map((turn) => ({
      ...turn,
      id: `${device.id}:${turn.id}`,
      source_entry_id: device.id,
      source_device_name: formatDeviceLabel(device),
      source_root_dir: itemWorkload.root_dir || undefined,
    }))
  )).sort((left, right) => (
    toTimestamp(left.start_at) - toTimestamp(right.start_at)
    || getThreadSourceEntryId(left).localeCompare(getThreadSourceEntryId(right))
    || left.thread_id.localeCompare(right.thread_id)
    || left.turn_index - right.turn_index
  ));
  const segments = buildWorkloadSegmentsFromTurns(turns);
  return {
    root_dir: rootDirs.length === 1 ? rootDirs[0] : `${entries.length} 台设备各自默认 .codex`,
    total_threads: new Set(turns.map(turn => `${turn.source_entry_id}:${turn.thread_id}`)).size,
    total_turns: turns.length,
    skipped_threads: entries.reduce((sum, item) => sum + (item.workload.skipped_threads || 0), 0),
    max_concurrency: Math.max(0, ...segments.map(segment => segment.concurrency)),
    time_range_start: turns.length ? Math.min(...turns.map(turn => toSecondTimestamp(turn.start_at))) : null,
    time_range_end: turns.length ? Math.max(...turns.map(turn => toSecondTimestamp(turn.end_at))) : null,
    turns,
    segments,
    root_dirs: rootDirs,
  };
};

const allThreads = computed<CodexThreadSummaryView[]>(() => (
  sortCodexThreadViews((overview.value?.groups ?? []).flatMap((group) => group.threads))
));

const totalThreadCount = computed(() => overview.value?.total_threads ?? allThreads.value.length);
const totalThreadPages = computed(() => Math.max(1, Math.ceil(totalThreadCount.value / THREAD_PAGE_SIZE)));
const currentThreadPageStart = computed(() => (
  totalThreadCount.value > 0 ? getThreadPageOffset() + 1 : 0
));
const currentThreadPageEnd = computed(() => Math.min(
  getThreadPageOffset() + allThreads.value.length,
  totalThreadCount.value,
));
const threadPaneCountLabel = computed(() => {
  if (!totalThreadCount.value) return '0 条';
  return `第 ${currentThreadPage.value}/${totalThreadPages.value} 页 · ${formatCount(totalThreadCount.value, ' 条')}`;
});
const threadPaginationText = computed(() => {
  if (!totalThreadCount.value) return '暂无会话';
  return `${currentThreadPageStart.value}-${currentThreadPageEnd.value} / ${totalThreadCount.value}`;
});

const normalizeRootDirForRequest = () => {
  if (isAllDevicesMode.value) return undefined;
  const value = rootDirInput.value.trim();
  return value || undefined;
};

const resolveDateValue = (value?: number | string | null) => {
  if (value === null || value === undefined || value === '') return null;
  const date = typeof value === 'number'
    ? new Date(value < 1_000_000_000_000 ? value * 1000 : value)
    : new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
};

const formatDateTime = (value?: number | string | null) => {
  const date = resolveDateValue(value);
  if (!date) return '未记录';
  return date.toLocaleString('zh-CN', { hour12: false });
};

const formatTimeOnly = (value?: number | string | null) => {
  const date = resolveDateValue(value);
  if (!date) return '未记录';
  return [
    String(date.getHours()).padStart(2, '0'),
    String(date.getMinutes()).padStart(2, '0'),
    String(date.getSeconds()).padStart(2, '0'),
  ].join(':');
};

const isSameCalendarDay = (
  left?: number | string | null,
  right?: number | string | null,
) => {
  const leftDate = resolveDateValue(left);
  const rightDate = resolveDateValue(right);
  if (!leftDate || !rightDate) return false;
  return leftDate.getFullYear() === rightDate.getFullYear()
    && leftDate.getMonth() === rightDate.getMonth()
    && leftDate.getDate() === rightDate.getDate();
};

const formatCount = (value?: number | null, suffix = '') => `${value ?? 0}${suffix}`;

const extractErrorMessage = (error: any, fallback: string) => {
  const detail = error?.response?.data?.detail;
  const message = typeof detail === 'string' ? detail : error?.message;
  return typeof message === 'string' && message.trim() ? message : fallback;
};

type CodexDeviceRequestFailure = {
  device: Device;
  error: unknown;
};

const createDeviceRequestFailure = (device: Device, error: unknown): CodexDeviceRequestFailure => ({
  device,
  error,
});

const compactFailureReason = (value: string, maxLength = 80) => {
  const compact = value.replace(/\s+/g, ' ').trim();
  if (compact.length <= maxLength) return compact;
  return `${compact.slice(0, maxLength)}...`;
};

const formatDeviceFailureDetails = (results: PromiseSettledResult<unknown>[], fallback: string) => {
  const rejected = results.filter((item): item is PromiseRejectedResult => item.status === 'rejected');
  if (!rejected.length) return '';

  const details = rejected.map((item, index) => {
    const reason = item.reason as Partial<CodexDeviceRequestFailure> | undefined;
    const device = reason?.device;
    const error = reason?.error ?? item.reason;
    const deviceLabel = formatDeviceLabel(device) || device?.id || `第 ${index + 1} 台设备`;
    const message = compactFailureReason(extractErrorMessage(error, fallback));
    return `${deviceLabel}（${message}）`;
  });
  return `${rejected.length} 台设备读取失败：${details.join('；')}`;
};

const formatThreadSource = (thread: CodexThreadSummaryView) => {
  const parts = [thread.project_label];
  if (thread.project_secondary_label) {
    parts.push(thread.project_secondary_label);
  }
  if (isAllDevicesMode.value && thread.source_device_name) {
    parts.push(thread.source_device_name);
  }
  return parts.join(' · ');
};

const formatMessageRole = (role: CodexThreadMessage['role']) => (
  role === 'user' ? '用户' : '助手'
);

const formatMessagePhase = (phase?: string | null) => {
  if (!phase) return '';
  if (phase === 'final_answer') return '结果';
  if (phase === 'commentary') return '过程';
  return phase;
};

const summarizeMessageText = (text: string, maxLength = 120) => {
  const compact = text.replace(/\s+/g, ' ').trim();
  if (compact.length <= maxLength) return compact;
  return `${compact.slice(0, maxLength)}...`;
};

const extractPreferredSummaryText = (text: string) => {
  const normalizedText = text.replace(/\r\n?/g, '\n');
  const lines = normalizedText.split('\n');
  const requestHeadingPattern = /^#+\s*My request for Codex:\s*(.*)$/i;
  const requestLineIndex = lines.findIndex(line => requestHeadingPattern.test(line.trim()));
  const preferredText = requestLineIndex >= 0
    ? (() => {
        const requestMatch = lines[requestLineIndex].trim().match(requestHeadingPattern);
        const inlineText = requestMatch?.[1]?.trim() || '';
        const remainingLines = lines.slice(requestLineIndex + 1).join('\n').trim();
        return [inlineText, remainingLines].filter(Boolean).join('\n').trim();
      })()
    : normalizedText;
  const cleanedText = preferredText
    .replace(/<image>[\s\S]*?<\/image>/gi, ' ')
    .replace(/<\/?image>/gi, ' ')
    .trim();
  return cleanedText || normalizedText;
};

const buildMessageSummaryItems = (messages: CodexThreadMessage[]): CodexMessageSummaryItem[] => {
  const items: CodexMessageSummaryItem[] = [];
  let index = 0;
  let userTurnIndex = 0;

  while (index < messages.length) {
    const currentMessage = messages[index];
    if (currentMessage.role === 'user') {
      userTurnIndex += 1;
      items.push({
        key: `user-${currentMessage.seq}`,
        role: 'user',
        turnIndex: userTurnIndex,
        displayMessage: currentMessage,
        allMessages: [currentMessage],
        processMessages: [],
        hasExplicitResult: false,
      });
      index += 1;
      continue;
    }

    const assistantMessages: CodexThreadMessage[] = [];
    while (index < messages.length && messages[index].role === 'assistant') {
      assistantMessages.push(messages[index]);
      index += 1;
    }

    const resultMessage = [...assistantMessages].reverse().find(message => message.phase === 'final_answer') ?? null;
    const displayMessage = resultMessage ?? assistantMessages[assistantMessages.length - 1];
    items.push({
      key: `assistant-${displayMessage.seq}`,
      role: 'assistant',
      turnIndex: userTurnIndex || null,
      displayMessage,
      allMessages: assistantMessages,
      processMessages: assistantMessages.filter(message => message.seq !== displayMessage.seq),
      hasExplicitResult: Boolean(resultMessage),
    });
  }

  return items;
};

const messageSummaryItems = computed<CodexMessageSummaryItem[]>(() => buildMessageSummaryItems(threadDetail.value?.messages ?? []));

const selectedSummaryItem = computed<CodexMessageSummaryItem | null>(() => {
  const items = messageSummaryItems.value;
  return items.find((item) => item.displayMessage.seq === selectedMessageSeq.value) ?? items[0] ?? null;
});

const selectedMessage = computed<CodexThreadMessage | null>(() => selectedSummaryItem.value?.displayMessage ?? null);

const hasMessageImagePlaceholder = (message: CodexThreadMessage) => /<image>\s*<\/image>/i.test(message.text);

const buildMessageImageCacheKey = (threadId: string, messageSeq: number) => [
  selectedThreadSourceEntryId.value || selectedEntryId.value,
  selectedThreadRootDir.value || normalizeRootDirForRequest() || '',
  threadId,
  messageSeq,
].join('|');

const resetMessageImageState = () => {
  messageImageCache.value = {};
  messageImageErrors.value = {};
  messageImageLoading.value = {};
};

const getMessageImages = (message: CodexThreadMessage, threadId = selectedThreadId.value) => {
  if (!threadId) return [];
  return messageImageCache.value[buildMessageImageCacheKey(threadId, message.seq)] ?? [];
};

const getMessageImageError = (message: CodexThreadMessage, threadId = selectedThreadId.value) => {
  if (!threadId) return '';
  return messageImageErrors.value[buildMessageImageCacheKey(threadId, message.seq)] ?? '';
};

const isMessageImageLoading = (message: CodexThreadMessage, threadId = selectedThreadId.value) => {
  if (!threadId) return false;
  return Boolean(messageImageLoading.value[buildMessageImageCacheKey(threadId, message.seq)]);
};

const buildMessageRenderBlocks = (
  message: CodexThreadMessage,
  threadId = selectedThreadId.value,
): CodexMessageRenderBlock[] => {
  const normalizedText = message.text.replace(/\r\n?/g, '\n');
  const images = getMessageImages(message, threadId);
  const blocks: CodexMessageRenderBlock[] = [];
  const imagePattern = /<image>\s*<\/image>/gi;
  let lastIndex = 0;
  let imageIndex = 0;
  let hasPlaceholder = false;

  for (const match of normalizedText.matchAll(imagePattern)) {
    hasPlaceholder = true;
    const start = match.index ?? 0;
    const textPart = normalizedText.slice(lastIndex, start);
    if (textPart.trim()) {
      blocks.push({
        key: `text-${message.seq}-${blocks.length}`,
        type: 'text',
        text: textPart,
      });
    }

    const image = images[imageIndex] ?? null;
    blocks.push({
      key: `image-${message.seq}-${imageIndex}`,
      type: image ? 'image' : 'image-placeholder',
      image: image ?? undefined,
      imageIndex,
    });
    imageIndex += 1;
    lastIndex = start + match[0].length;
  }

  const trailingText = normalizedText.slice(lastIndex);
  if (trailingText.trim() || (!hasPlaceholder && !blocks.length)) {
    blocks.push({
      key: `text-${message.seq}-tail`,
      type: 'text',
      text: trailingText || normalizedText,
    });
  }

  for (const image of images.slice(imageIndex)) {
    blocks.push({
      key: `image-extra-${message.seq}-${image.index}`,
      type: 'image',
      image,
      imageIndex: image.index - 1,
    });
  }

  return blocks;
};

const getMessageImagePlaceholderLabel = (message: CodexThreadMessage, threadId = selectedThreadId.value) => {
  if (isMessageImageLoading(message, threadId)) return '图片加载中';
  const error = getMessageImageError(message, threadId);
  if (error) return error;
  return '未读取到图片数据';
};

const loadMessageImages = async (threadId: string, message: CodexThreadMessage) => {
  const sourceEntryId = selectedThreadSourceEntryId.value || selectedEntryId.value;
  if (!sourceEntryId || !hasMessageImagePlaceholder(message)) return;
  const cacheKey = buildMessageImageCacheKey(threadId, message.seq);
  if (messageImageCache.value[cacheKey] || messageImageLoading.value[cacheKey]) return;

  messageImageLoading.value = {
    ...messageImageLoading.value,
    [cacheKey]: true,
  };
  delete messageImageErrors.value[cacheKey];

  try {
    const payload = await fetchCodexThreadMessageImagesForEntry(
      sourceEntryId,
      threadId,
      message.seq,
      selectedThreadRootDir.value || normalizeRootDirForRequest(),
    );
    messageImageCache.value = {
      ...messageImageCache.value,
      [cacheKey]: payload.images ?? [],
    };
  } catch (error: any) {
    messageImageErrors.value = {
      ...messageImageErrors.value,
      [cacheKey]: extractErrorMessage(error, '读取图片失败'),
    };
  } finally {
    const nextLoading = { ...messageImageLoading.value };
    delete nextLoading[cacheKey];
    messageImageLoading.value = nextLoading;
  }
};

const syncVisibleMessageImages = async () => {
  const threadId = selectedThreadId.value;
  const imageMessages = [
    selectedMessage.value,
    ...(isProcessExpanded.value ? (selectedSummaryItem.value?.processMessages ?? []) : []),
  ].filter((message): message is CodexThreadMessage => Boolean(message && hasMessageImagePlaceholder(message)));
  if (!imageMessages.length) return;
  await Promise.all(imageMessages.map(message => loadMessageImages(threadId, message)));
};

const formatMessageImageAlt = (message: CodexThreadMessage, imageIndex = 0) => (
  `${formatMessageRole(message.role)}图片 ${imageIndex + 1}`
);

const formatMessageTurnIndex = (turnIndex?: number | null) => (
  turnIndex ? `#${turnIndex}` : ''
);

const buildCompactMessagePrefix = (
  item: CodexMessageSummaryItem,
  previousItem: CodexMessageSummaryItem | null = null,
) => {
  const parts: string[] = [];
  if (item.role === 'assistant' && !item.hasExplicitResult && item.displayMessage.phase) {
    parts.push(formatMessagePhase(item.displayMessage.phase));
  }
  const timestamp = item.displayMessage.timestamp;
  const timeLabel = previousItem && isSameCalendarDay(timestamp, previousItem.displayMessage.timestamp)
    ? formatTimeOnly(timestamp)
    : formatDateTime(timestamp);
  parts.push(timeLabel);
  if (item.role === 'user' && item.turnIndex) {
    parts.push(formatMessageTurnIndex(item.turnIndex));
  }
  return parts.join(' ');
};

const buildCompactMessageSummary = (
  item: CodexMessageSummaryItem,
  previousItem: CodexMessageSummaryItem | null = null,
) => (
  `${buildCompactMessagePrefix(item, previousItem)} ${summarizeMessageText(extractPreferredSummaryText(item.displayMessage.text), 4000)}`.trim()
);

const formatSelectedSummaryMeta = (item: CodexMessageSummaryItem | null) => {
  if (!item) return '';
  if (item.role === 'user') {
    const turnIndex = formatMessageTurnIndex(item.turnIndex);
    return turnIndex ? `${turnIndex} · 用户` : '用户';
  }
  if (item.role === 'assistant' && item.hasExplicitResult) {
    return '助手结果';
  }
  return formatMessageRole(item.role);
};

const hashText = (text: string) => {
  let hash = 0;
  for (const ch of text) {
    hash = ((hash << 5) - hash + ch.charCodeAt(0)) | 0;
  }
  return Math.abs(hash);
};

const normalizeProjectPaletteToken = (value?: string | null) => (
  String(value || '')
    .trim()
    .toLowerCase()
    .replace(/^custom_/, '')
    .replace(/[\s._\-|·/\\:]+/g, '')
);

const collectProjectPaletteCandidates = (...values: Array<string | null | undefined>) => {
  const seen = new Set<string>();
  const candidates: string[] = [];
  const addCandidate = (value?: string | null) => {
    const trimmed = String(value || '').trim();
    if (!trimmed || seen.has(trimmed)) return;
    seen.add(trimmed);
    candidates.push(trimmed);
  };

  values.forEach((value) => {
    const trimmed = String(value || '').trim();
    if (!trimmed) return;
    addCandidate(trimmed);
    trimmed.split(/[|·]/).forEach(part => addCandidate(part));
    const pathParts = trimmed.split(/[\\/]/).filter(Boolean);
    addCandidate(pathParts[pathParts.length - 1]);
  });
  return candidates;
};

const noteTypePaletteLookup = computed(() => {
  const lookup = new Map<string, NoteTypePaletteItem>();
  const addLookup = (key: string, item: NoteTypePaletteItem, replace = false) => {
    const normalized = normalizeProjectPaletteToken(key);
    if (normalized && (replace || !lookup.has(normalized))) {
      lookup.set(normalized, item);
    }
  };

  noteTypePaletteItems.value.forEach((item) => {
    addLookup(item.key, item);
    addLookup(item.label, item);
    const shouldPreferCategoryRoot = /(?:综合|总览|整体)$/.test(item.label);
    item.label
      .split(/[\/／>＞:：]/)
      .forEach(part => addLookup(part, item, shouldPreferCategoryRoot));
    if (item.key.startsWith('custom_')) {
      addLookup(item.key.slice('custom_'.length), item);
    }
  });
  return lookup;
});

const noteTypePaletteByKey = computed(() => (
  noteTypePaletteItems.value.reduce<Record<string, NoteTypePaletteItem>>((result, item) => {
    result[item.key] = item;
    return result;
  }, {})
));

const noteProjectExactPaletteLookup = computed(() => {
  const paletteByKey = noteTypePaletteByKey.value;
  const lookup = new Map<string, NoteTypePaletteItem>();
  noteProjectColorHints.value.forEach((hint) => {
    const normalizedTitle = normalizeProjectPaletteToken(hint.title);
    const paletteItem = paletteByKey[hint.categoryKey];
    if (normalizedTitle && paletteItem && !lookup.has(normalizedTitle)) {
      lookup.set(normalizedTitle, paletteItem);
    }
  });
  return lookup;
});

const resolveNoteProjectPaletteItem = (candidates: string[]) => {
  const exactLookup = noteProjectExactPaletteLookup.value;
  for (const candidate of candidates) {
    const item = exactLookup.get(normalizeProjectPaletteToken(candidate));
    if (item) return item;
  }

  const paletteByKey = noteTypePaletteByKey.value;
  for (const candidate of candidates) {
    const normalizedCandidate = normalizeProjectPaletteToken(candidate);
    if (normalizedCandidate.length < 3) continue;

    const categoryScores = new Map<string, { score: number; updatedAt: number }>();
    noteProjectColorHints.value.forEach((hint) => {
      const normalizedTitle = normalizeProjectPaletteToken(hint.title);
      if (!normalizedTitle.includes(normalizedCandidate)) return;
      const current = categoryScores.get(hint.categoryKey) ?? { score: 0, updatedAt: 0 };
      current.score += normalizedTitle.startsWith(normalizedCandidate) ? 3 : 1;
      current.updatedAt = Math.max(current.updatedAt, hint.updatedAt);
      categoryScores.set(hint.categoryKey, current);
    });

    const bestCategoryKey = Array.from(categoryScores.entries()).sort((left, right) => {
      const scoreDiff = right[1].score - left[1].score;
      if (scoreDiff !== 0) return scoreDiff;
      return right[1].updatedAt - left[1].updatedAt;
    })[0]?.[0];
    const paletteItem = bestCategoryKey ? paletteByKey[bestCategoryKey] : null;
    if (paletteItem) return paletteItem;
  }
  return null;
};

const resolveProjectPaletteItem = (
  projectLabel?: string | null,
  projectSecondaryLabel?: string | null,
  projectKey?: string | null,
) => {
  const candidates = collectProjectPaletteCandidates(projectLabel, projectSecondaryLabel, projectKey);
  const notePaletteItem = resolveNoteProjectPaletteItem(candidates);
  if (notePaletteItem) return notePaletteItem;

  const lookup = noteTypePaletteLookup.value;
  for (const candidate of candidates) {
    const item = lookup.get(normalizeProjectPaletteToken(candidate));
    if (item) return item;
  }
  return null;
};

const hexToRgb = (hex: string) => {
  const normalized = hex.replace('#', '');
  const value = normalized.length === 3
    ? normalized.split('').map(part => `${part}${part}`).join('')
    : normalized;
  const numeric = Number.parseInt(value, 16);
  return {
    r: (numeric >> 16) & 255,
    g: (numeric >> 8) & 255,
    b: numeric & 255,
  };
};

const parseColorToRgb = (color: string) => {
  if (color.startsWith('#')) {
    return hexToRgb(color);
  }

  const rgbMatch = color.match(/rgba?\(\s*(\d{1,3})\s*[, ]\s*(\d{1,3})\s*[, ]\s*(\d{1,3})/i);
  if (rgbMatch) {
    return {
      r: Number(rgbMatch[1]),
      g: Number(rgbMatch[2]),
      b: Number(rgbMatch[3]),
    };
  }

  return { r: 255, g: 255, b: 255 };
};

const rgbToCss = ({ r, g, b }: { r: number; g: number; b: number }) => `rgb(${r}, ${g}, ${b})`;

const mixColors = (backgroundColor: string, foregroundColor: string, factor: number) => {
  const clampedFactor = Math.max(0, Math.min(1, factor));
  const background = parseColorToRgb(backgroundColor);
  const foreground = parseColorToRgb(foregroundColor);
  const mixChannel = (backgroundValue: number, foregroundValue: number) => (
    Math.round(backgroundValue + (foregroundValue - backgroundValue) * clampedFactor)
  );
  return rgbToCss({
    r: mixChannel(background.r, foreground.r),
    g: mixChannel(background.g, foreground.g),
    b: mixChannel(background.b, foreground.b),
  });
};

const mixWithWhite = (hex: string, factor: number) => {
  const clampedFactor = Math.max(0, Math.min(1, factor));
  const { r, g, b } = hexToRgb(hex);
  const mixChannel = (value: number) => Math.round(255 + (value - 255) * clampedFactor);
  return `rgb(${mixChannel(r)}, ${mixChannel(g)}, ${mixChannel(b)})`;
};

const resolveRelativeLuminance = (color: string) => {
  const { r, g, b } = parseColorToRgb(color);
  const normalize = (channel: number) => {
    const value = channel / 255;
    return value <= 0.03928 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
  };
  const red = normalize(r);
  const green = normalize(g);
  const blue = normalize(b);
  return 0.2126 * red + 0.7152 * green + 0.0722 * blue;
};

const resolveContrastRatio = (backgroundColor: string, foregroundColor: string) => {
  const backgroundLuminance = resolveRelativeLuminance(backgroundColor);
  const foregroundLuminance = resolveRelativeLuminance(foregroundColor);
  const lighter = Math.max(backgroundLuminance, foregroundLuminance);
  const darker = Math.min(backgroundLuminance, foregroundLuminance);
  return (lighter + 0.05) / (darker + 0.05);
};

const resolveContrastTextColor = (backgroundColor: string) => {
  const candidates = ['#0b1220', '#ffffff'];
  return candidates.reduce((best, candidate) => (
    resolveContrastRatio(backgroundColor, candidate) > resolveContrastRatio(backgroundColor, best)
      ? candidate
      : best
  ));
};

const resolveSurfaceTextTokens = (backgroundColor: string) => {
  const primary = resolveContrastTextColor(backgroundColor);
  const secondaryFactor = primary === '#ffffff' ? 0.78 : 0.72;
  const tertiaryFactor = primary === '#ffffff' ? 0.62 : 0.56;
  return {
    primary,
    secondary: mixColors(backgroundColor, primary, secondaryFactor),
    tertiary: mixColors(backgroundColor, primary, tertiaryFactor),
  };
};

const resolveUserFadeFactor = (timestamp?: number | string | null) => {
  const messageTimestamp = toTimestamp(timestamp);
  if (!messageTimestamp) return DAY_FADE_STOPS[0].factor;

  const daysAgo = Math.max(0, (Date.now() - messageTimestamp) / 86_400_000);
  for (let index = 1; index < DAY_FADE_STOPS.length; index += 1) {
    const previous = DAY_FADE_STOPS[index - 1];
    const current = DAY_FADE_STOPS[index];
    if (daysAgo <= current.days) {
      const progress = (daysAgo - previous.days) / (current.days - previous.days);
      return previous.factor + (current.factor - previous.factor) * progress;
    }
  }

  return DAY_FADE_STOPS[DAY_FADE_STOPS.length - 1].factor;
};

const buildProjectColorKey = (
  primaryLabel?: string | null,
  secondaryLabel?: string | null,
  workspaceRoot?: string | null,
  fallbackScope?: string | null,
) => [
  primaryLabel,
  secondaryLabel,
  workspaceRoot || fallbackScope,
].filter(Boolean).join(' | ');

const resolveFallbackProjectBaseColor = (projectKey?: string | null) => {
  const paletteIndex = hashText(projectKey || 'codex-default-project') % PROJECT_COLOR_PALETTE.length;
  return PROJECT_COLOR_PALETTE[paletteIndex];
};

const resolveProjectBaseColor = (
  projectKey?: string | null,
  projectLabel?: string | null,
  projectSecondaryLabel?: string | null,
) => (
  resolveProjectPaletteItem(projectLabel, projectSecondaryLabel, projectKey)?.color
  || resolveFallbackProjectBaseColor(projectKey)
);

const resolveWorkloadProjectColor = (
  projectKey?: string | null,
  projectLabel?: string | null,
  projectSecondaryLabel?: string | null,
) => {
  return resolveProjectBaseColor(projectKey, projectLabel, projectSecondaryLabel);
};

const resolveMessageSurfaceColor = (message: CodexThreadMessage) => {
  if (message.role === 'assistant') {
    return message.phase === 'final_answer' ? '#ffffff' : '#f3f4f6';
  }

  const thread = threadDetail.value?.thread;
  const projectKey = buildProjectColorKey(
    thread?.project_label || thread?.group_label,
    thread?.project_secondary_label || thread?.group_secondary_label,
    thread?.workspace_root,
    thread?.cwd,
  );
  const fadeFactor = resolveUserFadeFactor(message.timestamp);
  return mixWithWhite(
    resolveProjectBaseColor(
      projectKey,
      thread?.project_label || thread?.group_label,
      thread?.project_secondary_label || thread?.group_secondary_label,
    ),
    fadeFactor,
  );
};

const getMessageSurfaceStyle = (message: CodexThreadMessage) => {
  const backgroundColor = resolveMessageSurfaceColor(message);
  const textTokens = resolveSurfaceTextTokens(backgroundColor);
  return {
    '--codex-message-surface-bg': backgroundColor,
    '--codex-surface-fg': textTokens.primary,
    '--codex-surface-muted': textTokens.secondary,
    '--codex-surface-subtle': textTokens.tertiary,
  };
};

const getThreadSurfaceStyle = (thread: CodexThreadSummaryView) => {
  const projectKey = buildProjectColorKey(
    thread.project_label,
    thread.project_secondary_label,
    thread.workspace_root,
    thread.cwd,
  );
  const fadedColor = mixWithWhite(
    resolveProjectBaseColor(projectKey, thread.project_label, thread.project_secondary_label),
    resolveUserFadeFactor(thread.updated_at ?? thread.created_at),
  );
  const textTokens = resolveSurfaceTextTokens(fadedColor);
  return {
    '--codex-thread-surface-bg': fadedColor,
    '--codex-surface-fg': textTokens.primary,
    '--codex-surface-muted': textTokens.secondary,
    '--codex-surface-subtle': textTokens.tertiary,
  };
};

const formatWorkloadProjectLabel = (
  projectLabel?: string | null,
  projectSecondaryLabel?: string | null,
) => {
  const parts = [projectLabel, projectSecondaryLabel].filter(Boolean);
  return parts.join(' · ') || '未归类项目';
};

const buildWorkloadTurnProjectKey = (turn: CodexWorkloadResponse['turns'][number]) => (
  buildProjectColorKey(
    turn.project_label || turn.group_label,
    turn.project_secondary_label,
    turn.workspace_root,
    turn.group_label,
  ) || 'codex-default-project'
);

const startOfLocalDayMs = (timestamp: number) => {
  const date = new Date(timestamp);
  date.setHours(0, 0, 0, 0);
  return date.getTime();
};

const startOfLocalWeekMs = (timestamp: number) => {
  const date = new Date(timestamp);
  const weekOffset = (date.getDay() + 6) % 7;
  date.setDate(date.getDate() - weekOffset);
  date.setHours(0, 0, 0, 0);
  return date.getTime();
};

const startOfLocalMonthMs = (timestamp: number) => {
  const date = new Date(timestamp);
  date.setDate(1);
  date.setHours(0, 0, 0, 0);
  return date.getTime();
};

const addLocalDays = (timestamp: number, days: number) => {
  const date = new Date(timestamp);
  date.setDate(date.getDate() + days);
  date.setHours(0, 0, 0, 0);
  return date.getTime();
};

const addLocalWeeks = (timestamp: number, weeks: number) => addLocalDays(timestamp, weeks * 7);

const addLocalMonths = (timestamp: number, months: number) => {
  const date = new Date(timestamp);
  date.setMonth(date.getMonth() + months, 1);
  date.setHours(0, 0, 0, 0);
  return date.getTime();
};

const isAggregatedWorkloadGranularity = (
  granularity: CodexWorkloadGranularity,
): granularity is CodexAggregatedWorkloadGranularity => granularity !== 'detail';

const getWorkloadGranularityLabel = (granularity: CodexWorkloadGranularity) => {
  if (granularity === 'day') return '天';
  if (granularity === 'week') return '周';
  if (granularity === 'month') return '月';
  return '实时区间';
};

const getWorkloadPeakLabel = (granularity: CodexWorkloadGranularity) => {
  if (granularity === 'day') return '单日峰值';
  if (granularity === 'week') return '单周峰值';
  if (granularity === 'month') return '单月峰值';
  return '峰值';
};

const startOfWorkloadBucketMs = (
  timestamp: number,
  granularity: CodexAggregatedWorkloadGranularity,
) => {
  if (granularity === 'day') return startOfLocalDayMs(timestamp);
  if (granularity === 'week') return startOfLocalWeekMs(timestamp);
  return startOfLocalMonthMs(timestamp);
};

const addWorkloadBuckets = (
  timestamp: number,
  granularity: CodexAggregatedWorkloadGranularity,
  count = 1,
) => {
  if (granularity === 'day') return addLocalDays(timestamp, count);
  if (granularity === 'week') return addLocalWeeks(timestamp, count);
  return addLocalMonths(timestamp, count);
};

const countWorkloadBuckets = (
  rangeStartMs: number,
  rangeEndExclusiveMs: number,
  granularity: CodexAggregatedWorkloadGranularity,
) => {
  let total = 0;
  for (
    let currentMs = rangeStartMs;
    currentMs < rangeEndExclusiveMs;
    currentMs = addWorkloadBuckets(currentMs, granularity, 1)
  ) {
    total += 1;
  }
  return Math.max(total, 1);
};

const getAllowedWorkloadTickUnits = (granularity: CodexWorkloadGranularity): CodexWorkloadTickUnit[] => {
  if (granularity === 'detail') return ['hour', 'day', 'week', 'month'];
  if (granularity === 'day') return ['day', 'week', 'month'];
  if (granularity === 'week') return ['week', 'month'];
  return ['month'];
};

const getPreferredWorkloadTickUnits = (
  totalRangeMs: number,
): Array<{ unit: CodexWorkloadTickUnit; penalty: number }> => {
  if (totalRangeMs <= DAY_MS) {
    return [{ unit: 'hour', penalty: 0 }];
  }
  if (totalRangeMs <= 7 * DAY_MS) {
    return [{ unit: 'day', penalty: 0 }];
  }
  if (totalRangeMs <= 60 * DAY_MS) {
    return [{ unit: 'week', penalty: 0 }];
  }
  return [
    { unit: 'month', penalty: 0 },
    { unit: 'week', penalty: 1.5 },
  ];
};

const getWorkloadTickStepCandidates = (unit: CodexWorkloadTickUnit) => {
  if (unit === 'hour') return [1, 2, 3, 4, 5, 6, 8, 12];
  if (unit === 'day') return [1, 2, 3, 4, 5, 7];
  if (unit === 'week') return [1, 2, 3, 4, 6, 8];
  return [1, 2, 3, 4, 6, 12];
};

const alignWorkloadTickBoundary = (timestamp: number, unit: CodexWorkloadTickUnit) => {
  const date = new Date(timestamp);
  if (unit === 'hour') {
    date.setMinutes(0, 0, 0);
    if (date.getTime() < timestamp) {
      date.setHours(date.getHours() + 1);
    }
    return date.getTime();
  }
  if (unit === 'day') {
    date.setHours(0, 0, 0, 0);
    if (date.getTime() < timestamp) {
      date.setDate(date.getDate() + 1);
    }
    return date.getTime();
  }
  if (unit === 'week') {
    const aligned = startOfLocalWeekMs(timestamp);
    return aligned < timestamp ? addLocalWeeks(aligned, 1) : aligned;
  }
  const aligned = startOfLocalMonthMs(timestamp);
  return aligned < timestamp ? addLocalMonths(aligned, 1) : aligned;
};

const addWorkloadTickStep = (timestamp: number, unit: CodexWorkloadTickUnit, step: number) => {
  if (unit === 'hour') {
    const date = new Date(timestamp);
    date.setHours(date.getHours() + step, 0, 0, 0);
    return date.getTime();
  }
  if (unit === 'day') return addLocalDays(timestamp, step);
  if (unit === 'week') return addLocalWeeks(timestamp, step);
  return addLocalMonths(timestamp, step);
};

const collectAlignedWorkloadTickTimestamps = (
  rangeStartMs: number,
  rangeEndMs: number,
  unit: CodexWorkloadTickUnit,
  step: number,
) => {
  const timestamps: number[] = [];
  for (
    let timestamp = alignWorkloadTickBoundary(rangeStartMs, unit);
    timestamp <= rangeEndMs;
    timestamp = addWorkloadTickStep(timestamp, unit, step)
  ) {
    timestamps.push(timestamp);
  }
  return timestamps;
};

const formatDateOnly = (timestamp: number) => {
  const date = new Date(timestamp);
  return `${date.getFullYear()}/${date.getMonth() + 1}/${date.getDate()}`;
};

const formatDurationHours = (hours: number) => {
  if (!Number.isFinite(hours)) return '0';
  return hours >= 10 ? hours.toFixed(1) : hours.toFixed(2).replace(/\.?0+$/, '');
};

const formatWorkloadMetricValue = (value: number, metric: CodexWorkloadMetric) => {
  if (metric === 'duration') {
    return formatDurationHours(value);
  }
  return String(Math.round(value));
};

const formatAverageWorkloadMetricValue = (value: number, metric: CodexWorkloadMetric) => {
  if (metric === 'duration') {
    return formatDurationHours(value);
  }
  if (!Number.isFinite(value)) return '0';
  if (Math.abs(value - Math.round(value)) < 0.01) {
    return String(Math.round(value));
  }
  return value >= 10
    ? value.toFixed(1).replace(/\.?0+$/, '')
    : value.toFixed(2).replace(/\.?0+$/, '');
};

const resolveWorkloadMetricUnit = (metric: CodexWorkloadMetric) => {
  if (metric === 'duration') return '小时';
  if (metric === 'turn_count') return '轮次';
  return '并发';
};

const toSecondsTimestamp = (timestampMs: number) => Number((timestampMs / 1000).toFixed(3));

const formatWorkloadTickLabel = (
  timestamp: number,
  unit: CodexWorkloadTickUnit,
) => {
  const date = new Date(toTimestamp(timestamp));
  if (Number.isNaN(date.getTime())) return '';
  if (unit === 'hour') {
    return [
      String(date.getHours()).padStart(2, '0'),
      String(date.getMinutes()).padStart(2, '0'),
    ].join(':');
  }
  if (unit === 'day' || unit === 'week') {
    return `${date.getMonth() + 1}/${date.getDate()}`;
  }
  if (unit === 'month') {
    return `${date.getFullYear()}/${date.getMonth() + 1}`;
  }
  return '';
};

const buildFallbackWorkloadTicks = (
  rangeStartMs: number,
  rangeEndMs: number,
  plotWidth: number,
  totalRangeMs: number,
  granularity: CodexWorkloadGranularity = 'detail',
) => {
  const fallbackUnit = getAllowedWorkloadTickUnits(granularity)[0] ?? 'day';
  const fallbackTickCount = 2;
  return Array.from({ length: fallbackTickCount }, (_, index) => {
    const ratio = fallbackTickCount === 1 ? 0 : index / (fallbackTickCount - 1);
    const timestamp = rangeStartMs + totalRangeMs * ratio;
    return {
      key: `fallback-${index}-${timestamp}`,
      x: WORKLOAD_CHART_PADDING.left + ratio * plotWidth,
      label: formatWorkloadTickLabel(timestamp, fallbackUnit),
    };
  });
};

const buildWorkloadChartTicks = (
  timestamps: number[],
  unit: CodexWorkloadTickUnit,
  rangeStartMs: number,
  plotWidth: number,
  totalRangeMs: number,
  step: number,
  maxLabels = timestamps.length,
) => {
  const visibleLabelIndexes = new Set<number>();
  if (timestamps.length <= maxLabels) {
    timestamps.forEach((_, index) => visibleLabelIndexes.add(index));
  } else {
    const labelStep = Math.max(1, Math.ceil(timestamps.length / maxLabels));
    for (let index = 0; index < timestamps.length; index += labelStep) {
      visibleLabelIndexes.add(index);
    }
    visibleLabelIndexes.add(timestamps.length - 1);
  }

  return timestamps.map((timestamp, index) => ({
    key: `aligned-${unit}-${step}-${timestamp}`,
    x: WORKLOAD_CHART_PADDING.left + ((timestamp - rangeStartMs) / totalRangeMs) * plotWidth,
    label: formatWorkloadTickLabel(timestamp, unit),
    showLabel: visibleLabelIndexes.has(index),
  }));
};

const buildForcedWorkloadTicks = (
  rangeStartMs: number,
  rangeEndMs: number,
  plotWidth: number,
  totalRangeMs: number,
  unit: CodexWorkloadTickUnit,
) => {
  const timestamps = collectAlignedWorkloadTickTimestamps(rangeStartMs, rangeEndMs, unit, 1);
  if (timestamps.length < 2) {
    return null;
  }
  return buildWorkloadChartTicks(
    timestamps,
    unit,
    rangeStartMs,
    plotWidth,
    totalRangeMs,
    1,
    WORKLOAD_MAX_TICKS,
  );
};

const buildAlignedWorkloadTicks = (
  rangeStartMs: number,
  rangeEndMs: number,
  plotWidth: number,
  totalRangeMs: number,
  granularity: CodexWorkloadGranularity = 'detail',
) => {
  if (granularity === 'day') {
    const weeklyTicks = buildForcedWorkloadTicks(
      rangeStartMs,
      rangeEndMs,
      plotWidth,
      totalRangeMs,
      'week',
    );
    if (weeklyTicks) return weeklyTicks;
  }

  if (granularity === 'week') {
    const monthlyTicks = buildForcedWorkloadTicks(
      rangeStartMs,
      rangeEndMs,
      plotWidth,
      totalRangeMs,
      'month',
    );
    if (monthlyTicks) return monthlyTicks;
  }

  const allowedUnits = getAllowedWorkloadTickUnits(granularity);
  const preferredUnits = getPreferredWorkloadTickUnits(totalRangeMs);
  const basePenalty = 4;
  const unitPenaltyMap = new Map<CodexWorkloadTickUnit, number>(
    preferredUnits.map(({ unit, penalty }) => [unit, penalty]),
  );
  const candidateUnits = [
    ...preferredUnits.map(item => item.unit).filter(unit => allowedUnits.includes(unit)),
    ...allowedUnits.filter(unit => !unitPenaltyMap.has(unit)),
  ];

  let bestCandidate: {
    unit: CodexWorkloadTickUnit;
    step: number;
    timestamps: number[];
    score: number;
  } | null = null;

  for (const unit of candidateUnits) {
    const penalty = unitPenaltyMap.get(unit) ?? basePenalty;
    for (const step of getWorkloadTickStepCandidates(unit)) {
      const timestamps = collectAlignedWorkloadTickTimestamps(rangeStartMs, rangeEndMs, unit, step);
      if (timestamps.length < 2) continue;
      const score = Math.abs(timestamps.length - 5) + penalty + step * 0.01;
      if (!bestCandidate || score < bestCandidate.score) {
        bestCandidate = { unit, step, timestamps, score };
      }
    }
  }

  if (!bestCandidate) {
    return buildFallbackWorkloadTicks(rangeStartMs, rangeEndMs, plotWidth, totalRangeMs, granularity);
  }

  return buildWorkloadChartTicks(
    bestCandidate.timestamps,
    bestCandidate.unit,
    rangeStartMs,
    plotWidth,
    totalRangeMs,
    bestCandidate.step,
  );
};

const buildWorkloadGuides = (
  peakValue: number,
  plotHeight: number,
  baselineY: number,
  metric: CodexWorkloadMetric,
) => {
  const safePeak = peakValue > 0 ? peakValue : 1;
  const candidates = [
    safePeak,
    safePeak / 2,
    ...(safePeak >= 1 ? [1] : []),
  ]
    .map(value => Number(value.toFixed(2)))
    .filter((value, index, array) => array.findIndex(item => Math.abs(item - value) < 0.01) === index)
    .sort((left, right) => right - left);
  return candidates.map((value) => ({
    key: String(value),
    y: baselineY - (value / safePeak) * plotHeight,
    label: formatWorkloadMetricValue(value, metric),
  }));
};

const buildWorkloadBucketTitleRange = (
  startMs: number,
  endExclusiveMs: number,
  granularity: CodexAggregatedWorkloadGranularity,
) => {
  if (granularity === 'day') {
    return formatDateOnly(startMs);
  }
  if (granularity === 'month') {
    const date = new Date(startMs);
    return `${date.getFullYear()}/${date.getMonth() + 1}`;
  }
  return `${formatDateOnly(startMs)} - ${formatDateOnly(endExclusiveMs - 1)}`;
};

const resolveSegmentWorkHours = (segment: CodexWorkloadResponse['segments'][number]) => (
  (segment.duration_seconds / 3600) * segment.concurrency
);

const calculateAggregatedWorkloadTotalValue = (
  data: CodexWorkloadResponse | null,
  granularity: CodexAggregatedWorkloadGranularity,
  metric: CodexWorkloadMetric,
) => {
  if (!data) return 0;
  if (metric === 'turn_count') {
    return data.turns.length;
  }
  if (metric === 'duration') {
    return data.turns.reduce((sum, turn) => sum + (turn.duration_seconds || 0) / 3600, 0);
  }

  const bucketPeaks = new Map<number, number>();
  data.segments.forEach((segment) => {
    const segmentStartMs = toTimestamp(segment.start_at);
    const segmentEndMs = toTimestamp(segment.end_at);
    for (
      let currentBucketStartMs = startOfWorkloadBucketMs(segmentStartMs, granularity);
      currentBucketStartMs < segmentEndMs;
      currentBucketStartMs = addWorkloadBuckets(currentBucketStartMs, granularity, 1)
    ) {
      bucketPeaks.set(
        currentBucketStartMs,
        Math.max(bucketPeaks.get(currentBucketStartMs) ?? 0, segment.concurrency),
      );
    }
  });

  return Array.from(bucketPeaks.values()).reduce((sum, value) => sum + value, 0);
};

const workloadRangeBounds = computed(() => {
  if (!workload.value?.turns.length) return null;
  const startMs = startOfLocalDayMs(Math.min(...workload.value.turns.map(turn => toTimestamp(turn.start_at))));
  const endMs = startOfLocalDayMs(Math.max(...workload.value.turns.map((turn) => {
    const turnStartMs = toTimestamp(turn.start_at);
    const turnEndMs = toTimestamp(turn.end_at);
    return Math.max(turnStartMs, turnEndMs - 1);
  })));
  return { startMs, endMs };
});

const buildDefaultWorkloadDateRange = (bounds: { startMs: number; endMs: number }): [Date, Date] => {
  const defaultEndMs = bounds.endMs;
  const defaultStartMs = Math.max(
    bounds.startMs,
    startOfLocalDayMs(addLocalMonths(addLocalDays(defaultEndMs, 1), -1)),
  );
  return [new Date(defaultStartMs), new Date(defaultEndMs)];
};

const selectedWorkloadRange = computed(() => {
  const bounds = workloadRangeBounds.value;
  if (!bounds) return null;

  if (!workloadDateRange.value?.length) {
    return {
      startMs: bounds.startMs,
      endDayMs: bounds.endMs,
      endExclusiveMs: addLocalDays(bounds.endMs, 1),
    };
  }

  const rawStartMs = startOfLocalDayMs(workloadDateRange.value[0].getTime());
  const rawEndMs = startOfLocalDayMs(workloadDateRange.value[1].getTime());
  const startMs = Math.min(
    Math.max(rawStartMs, bounds.startMs),
    bounds.endMs,
  );
  const endDayMs = Math.max(
    startMs,
    Math.min(rawEndMs, bounds.endMs),
  );

  return {
    startMs,
    endDayMs,
    endExclusiveMs: addLocalDays(endDayMs, 1),
  };
});

const isWorkloadDateDisabled = (date: Date) => {
  const bounds = workloadRangeBounds.value;
  if (!bounds) return true;
  const dayMs = startOfLocalDayMs(date.getTime());
  return dayMs < bounds.startMs || dayMs > bounds.endMs;
};

const filterWorkloadData = (
  data: CodexWorkloadResponse | null,
  range: { startMs: number; endDayMs: number; endExclusiveMs: number } | null,
  granularity: CodexWorkloadGranularity,
  metric: CodexWorkloadMetric,
): CodexWorkloadResponse | null => {
  if (!data || !range) return data;

  const turns = data.turns
    .filter((turn) => {
      const turnStartMs = toTimestamp(turn.start_at);
      const turnEndMs = toTimestamp(turn.end_at);
      if (isAggregatedWorkloadGranularity(granularity) && metric === 'turn_count') {
        return turnStartMs >= range.startMs && turnStartMs < range.endExclusiveMs;
      }
      return turnEndMs > range.startMs && turnStartMs < range.endExclusiveMs;
    })
    .map((turn) => {
      const turnStartMs = toTimestamp(turn.start_at);
      const turnEndMs = toTimestamp(turn.end_at);
      const clippedStartMs = Math.max(turnStartMs, range.startMs);
      const clippedEndMs = Math.min(turnEndMs, range.endExclusiveMs);
      return {
        ...turn,
        start_at: toSecondsTimestamp(clippedStartMs),
        end_at: toSecondsTimestamp(Math.max(clippedEndMs, clippedStartMs)),
        duration_seconds: Math.max((clippedEndMs - clippedStartMs) / 1000, 0),
      };
    });

  const segments = data.segments
    .filter((segment) => {
      const segmentStartMs = toTimestamp(segment.start_at);
      const segmentEndMs = toTimestamp(segment.end_at);
      return segmentEndMs > range.startMs && segmentStartMs < range.endExclusiveMs;
    })
    .map((segment) => {
      const segmentStartMs = toTimestamp(segment.start_at);
      const segmentEndMs = toTimestamp(segment.end_at);
      const clippedStartMs = Math.max(segmentStartMs, range.startMs);
      const clippedEndMs = Math.min(segmentEndMs, range.endExclusiveMs);
      return {
        ...segment,
        start_at: toSecondsTimestamp(clippedStartMs),
        end_at: toSecondsTimestamp(Math.max(clippedEndMs, clippedStartMs)),
        duration_seconds: Math.max((clippedEndMs - clippedStartMs) / 1000, 0),
      };
    })
    .filter(segment => segment.duration_seconds > 0);

  return {
    ...data,
    total_turns: turns.length,
    total_threads: new Set(turns.map(turn => turn.thread_id)).size,
    max_concurrency: Math.max(0, ...segments.map(segment => segment.concurrency)),
    time_range_start: turns.length ? Math.min(...turns.map(turn => Number(turn.start_at))) : null,
    time_range_end: turns.length ? Math.max(...turns.map(turn => Number(turn.end_at))) : null,
    turns,
    segments,
  };
};

const buildWorkloadChartModel = (
  data: CodexWorkloadResponse | null,
  granularity: CodexWorkloadGranularity,
  metric: CodexWorkloadMetric,
): CodexWorkloadChartModel | null => {
  if (!data) {
    return null;
  }

  const chartWidth = WORKLOAD_CHART_WIDTH;
  const chartHeight = WORKLOAD_CHART_HEIGHT;
  const plotWidth = chartWidth - WORKLOAD_CHART_PADDING.left - WORKLOAD_CHART_PADDING.right;
  const plotHeight = chartHeight - WORKLOAD_CHART_PADDING.top - WORKLOAD_CHART_PADDING.bottom;
  const baselineY = WORKLOAD_CHART_PADDING.top + plotHeight;
  const rangeStart = data.time_range_start ?? data.segments[0]?.start_at ?? data.turns[0]?.start_at ?? null;
  const rangeEnd = data.time_range_end ?? data.segments[data.segments.length - 1]?.end_at ?? data.turns[data.turns.length - 1]?.end_at ?? null;
  if (rangeStart === null || rangeEnd === null) {
    return {
      viewBox: `0 0 ${chartWidth} ${chartHeight}`,
      chartWidth,
      chartHeight,
      baselineY,
      bars: [],
      guides: [],
      ticks: [],
      hasActivity: false,
      peakValue: 0,
      unitLabel: resolveWorkloadMetricUnit(metric),
    };
  }

  const normalizedTurns = data.turns.map(turn => ({
    ...turn,
    startMs: toTimestamp(turn.start_at),
    endMs: toTimestamp(turn.end_at),
    projectKey: buildWorkloadTurnProjectKey(turn),
    projectPrimaryLabel: turn.project_label || turn.group_label,
    projectSecondaryLabel: turn.project_secondary_label,
    projectLabel: formatWorkloadProjectLabel(turn.project_label, turn.project_secondary_label),
  }));

  if (isAggregatedWorkloadGranularity(granularity)) {
    const bucketRangeStartMs = startOfWorkloadBucketMs(toTimestamp(rangeStart), granularity);
    const bucketRangeEndExclusiveMs = addWorkloadBuckets(
      startOfWorkloadBucketMs(Math.max(bucketRangeStartMs, toTimestamp(rangeEnd) - 1), granularity),
      granularity,
      1,
    );
    const totalRangeMs = Math.max(bucketRangeEndExclusiveMs - bucketRangeStartMs, DAY_MS);
    const buckets = new Map<number, {
      startMs: number;
      endMs: number;
      total: number;
      projects: Map<string, { key: string; label: string; count: number; fill: string }>;
    }>();

    for (
      let currentMs = bucketRangeStartMs;
      currentMs < bucketRangeEndExclusiveMs;
      currentMs = addWorkloadBuckets(currentMs, granularity, 1)
    ) {
      buckets.set(currentMs, {
        startMs: currentMs,
        endMs: addWorkloadBuckets(currentMs, granularity, 1),
        total: 0,
        projects: new Map(),
      });
    }

    const ensureBucketProject = (
      bucket: {
        projects: Map<string, { key: string; label: string; count: number; fill: string }>;
      },
      turn: typeof normalizedTurns[number],
    ) => {
      const existing = bucket.projects.get(turn.projectKey);
      if (existing) return existing;
      const created = {
        key: turn.projectKey,
        label: turn.projectLabel,
        count: 0,
        fill: resolveWorkloadProjectColor(turn.projectKey, turn.projectPrimaryLabel, turn.projectSecondaryLabel),
      };
      bucket.projects.set(turn.projectKey, created);
      return created;
    };

    if (metric === 'turn_count') {
      normalizedTurns.forEach((turn) => {
        const bucket = buckets.get(startOfWorkloadBucketMs(turn.startMs, granularity));
        if (!bucket) return;
        bucket.total += 1;
        ensureBucketProject(bucket, turn).count += 1;
      });
    } else if (metric === 'duration') {
      normalizedTurns.forEach((turn) => {
        for (
          let currentBucketStartMs = startOfWorkloadBucketMs(turn.startMs, granularity);
          currentBucketStartMs < turn.endMs;
          currentBucketStartMs = addWorkloadBuckets(currentBucketStartMs, granularity, 1)
        ) {
          const bucket = buckets.get(currentBucketStartMs);
          if (!bucket) continue;
          const bucketEndMs = addWorkloadBuckets(currentBucketStartMs, granularity, 1);
          const overlapMs = Math.max(0, Math.min(turn.endMs, bucketEndMs) - Math.max(turn.startMs, currentBucketStartMs));
          if (!overlapMs) continue;
          const overlapHours = overlapMs / HOUR_MS;
          bucket.total += overlapHours;
          ensureBucketProject(bucket, turn).count += overlapHours;
        }
      });
    } else {
      data.segments.forEach((segment) => {
        const segmentStartMs = toTimestamp(segment.start_at);
        const segmentEndMs = toTimestamp(segment.end_at);
        for (
          let currentBucketStartMs = startOfWorkloadBucketMs(segmentStartMs, granularity);
          currentBucketStartMs < segmentEndMs;
          currentBucketStartMs = addWorkloadBuckets(currentBucketStartMs, granularity, 1)
        ) {
          const bucket = buckets.get(currentBucketStartMs);
          if (!bucket || segment.concurrency <= bucket.total) continue;
          bucket.total = segment.concurrency;
          bucket.projects.clear();
          normalizedTurns
            .filter(turn => turn.startMs < segmentEndMs && turn.endMs > segmentStartMs)
            .forEach((turn) => {
              ensureBucketProject(bucket, turn).count += 1;
            });
        }
      });
    }

    const peakValue = Math.max(0, ...Array.from(buckets.values()).map(bucket => bucket.total));
    const normalizedPeak = peakValue > 0 ? peakValue : 1;
    const bars = Array.from(buckets.values())
      .filter(bucket => bucket.total > 0)
      .map((bucket, index) => {
        const rawX = WORKLOAD_CHART_PADDING.left + ((bucket.startMs - bucketRangeStartMs) / totalRangeMs) * plotWidth;
        const rawWidth = ((bucket.endMs - bucket.startMs) / totalRangeMs) * plotWidth;
        const gap = Math.min(8, rawWidth * 0.28);
        const x = rawX + gap / 2;
        const width = Math.max(rawWidth - gap, 4);
        const height = (bucket.total / normalizedPeak) * plotHeight;
        const y = baselineY - height;
        const bucketRangeLabel = buildWorkloadBucketTitleRange(bucket.startMs, bucket.endMs, granularity);
        const projectSlices = Array.from(bucket.projects.values())
          .sort((left, right) => (
            left.count === right.count
              ? (
                  left.label === right.label
                    ? left.key.localeCompare(right.key)
                    : left.label.localeCompare(right.label, 'zh-CN')
                )
              : right.count - left.count
          ));
        let currentY = baselineY;
        const slices = projectSlices.map((project, sliceIndex) => {
          const sliceHeight = sliceIndex === projectSlices.length - 1
            ? Math.max(currentY - y, 0)
            : (project.count / normalizedPeak) * plotHeight;
          const sliceY = currentY - sliceHeight;
          currentY = sliceY;
          const projectValueText = `${formatWorkloadMetricValue(project.count, metric)} ${resolveWorkloadMetricUnit(metric)}`;
          return {
            key: `${bucket.startMs}-${project.key}`,
            y: sliceY,
            height: sliceHeight,
            fill: project.fill,
            title: `${project.label}：${projectValueText}\n${bucketRangeLabel}`,
          };
        });
        const totalValueText = `${formatWorkloadMetricValue(bucket.total, metric)} ${resolveWorkloadMetricUnit(metric)}`;
        const breakdown = projectSlices
          .map(project => `${project.label} ${formatWorkloadMetricValue(project.count, metric)}`)
          .join('，');
        return {
          key: `${granularity}-${bucket.startMs}-${index}`,
          clipId: `codex-workload-clip-${granularity}-${index}`,
          x,
          y,
          width,
          height,
          title: `${bucketRangeLabel}：${totalValueText}${breakdown ? `（${breakdown}）` : ''}`,
          slices,
        };
      });

    return {
      viewBox: `0 0 ${chartWidth} ${chartHeight}`,
      chartWidth,
      chartHeight,
      baselineY,
      bars,
      guides: buildWorkloadGuides(peakValue, plotHeight, baselineY, metric),
      ticks: buildAlignedWorkloadTicks(bucketRangeStartMs, bucketRangeEndExclusiveMs - 1, plotWidth, totalRangeMs, granularity),
      hasActivity: bars.length > 0,
      peakValue,
      unitLabel: resolveWorkloadMetricUnit(metric),
    };
  }

  const rangeStartMs = toTimestamp(rangeStart);
  const rangeEndMs = toTimestamp(rangeEnd);
  const totalRangeMs = Math.max(rangeEndMs - rangeStartMs, 1_000);
  const peakValue = metric === 'duration'
    ? Math.max(0, ...data.segments.map(resolveSegmentWorkHours), 0)
    : Math.max(data.max_concurrency, 1);
  const normalizedPeak = peakValue > 0 ? peakValue : 1;
  const bars = data.segments.map((segment, index) => {
    const startMs = toTimestamp(segment.start_at);
    const endMs = toTimestamp(segment.end_at);
    const x = WORKLOAD_CHART_PADDING.left + ((startMs - rangeStartMs) / totalRangeMs) * plotWidth;
    const rawWidth = ((endMs - startMs) / totalRangeMs) * plotWidth;
    const width = Math.max(rawWidth, 2);
    const segmentValue = metric === 'duration'
      ? resolveSegmentWorkHours(segment)
      : segment.concurrency;
    const height = (segmentValue / normalizedPeak) * plotHeight;
    const y = baselineY - height;
    const activeProjects = new Map<string, {
      key: string;
      label: string;
      count: number;
      fill: string;
    }>();
    normalizedTurns
      .filter(turn => turn.startMs < endMs && turn.endMs > startMs)
      .forEach((turn) => {
        const existing = activeProjects.get(turn.projectKey);
        const projectContribution = metric === 'duration' ? segment.duration_seconds / 3600 : 1;
        if (existing) {
          existing.count += projectContribution;
          return;
        }
        activeProjects.set(turn.projectKey, {
          key: turn.projectKey,
          label: turn.projectLabel,
          count: projectContribution,
          fill: resolveWorkloadProjectColor(turn.projectKey, turn.projectPrimaryLabel, turn.projectSecondaryLabel),
        });
      });
    const projectSlices = Array.from(activeProjects.values())
      .sort((left, right) => (
        left.count === right.count
          ? (
              left.label === right.label
                ? left.key.localeCompare(right.key)
                : left.label.localeCompare(right.label, 'zh-CN')
            )
          : right.count - left.count
      ));
    let currentY = baselineY;
    const slices = projectSlices.length
      ? projectSlices.map((project, sliceIndex) => {
          const sliceHeight = sliceIndex === projectSlices.length - 1
            ? Math.max(currentY - y, 0)
            : (project.count / normalizedPeak) * plotHeight;
          const sliceY = currentY - sliceHeight;
          currentY = sliceY;
          const projectValueText = `${formatWorkloadMetricValue(project.count, metric)} ${resolveWorkloadMetricUnit(metric)}`;
          return {
            key: `${segment.start_at}-${segment.end_at}-${project.key}`,
            y: sliceY,
            height: sliceHeight,
            fill: project.fill,
            title: `${project.label}：${projectValueText}\n${formatDateTime(segment.start_at)} - ${formatDateTime(segment.end_at)}`,
          };
        })
      : [{
          key: `${segment.start_at}-${segment.end_at}-fallback`,
          y,
          height,
          fill: resolveWorkloadProjectColor(),
          title: `${formatDateTime(segment.start_at)} - ${formatDateTime(segment.end_at)}：${formatWorkloadMetricValue(segmentValue, metric)} ${resolveWorkloadMetricUnit(metric)}`,
        }];
    const breakdown = projectSlices
      .map(project => `${project.label} ${formatWorkloadMetricValue(project.count, metric)}`)
      .join('，');
    return {
      key: `${segment.start_at}-${segment.end_at}-${index}`,
      clipId: `codex-workload-clip-${index}`,
      x,
      y,
      width,
      height,
      title: `${formatDateTime(segment.start_at)} - ${formatDateTime(segment.end_at)}：${formatWorkloadMetricValue(segmentValue, metric)} ${resolveWorkloadMetricUnit(metric)}${breakdown ? `（${breakdown}）` : ''}`,
      slices,
    };
  });

  return {
    viewBox: `0 0 ${chartWidth} ${chartHeight}`,
    chartWidth,
    chartHeight,
    baselineY,
    bars,
    guides: buildWorkloadGuides(peakValue, plotHeight, baselineY, metric),
    ticks: buildAlignedWorkloadTicks(rangeStartMs, rangeEndMs, plotWidth, totalRangeMs),
    hasActivity: bars.length > 0,
    peakValue,
    unitLabel: resolveWorkloadMetricUnit(metric),
  };
};

const scopedWorkload = computed(() => (
  filterWorkloadData(workload.value, selectedWorkloadRange.value, workloadGranularity.value, workloadMetric.value)
));
const workloadChartModel = computed(() => (
  buildWorkloadChartModel(scopedWorkload.value, workloadGranularity.value, workloadMetric.value)
));
const workloadMetricOptions = computed(() => WORKLOAD_METRIC_OPTIONS[workloadGranularity.value]);
const workloadModeDescription = computed(() => {
  if (!isAggregatedWorkloadGranularity(workloadGranularity.value)) {
    return workloadMetric.value === 'duration'
      ? '按实时区间统计累计工时'
      : '按单轮“用户消息 -> 助手最后一条消息”统计并发工作数';
  }
  const metricLabel = workloadMetric.value === 'duration'
    ? '累计工时'
    : workloadMetric.value === 'turn_count'
      ? '轮次'
      : '峰值并发';
  return `按${getWorkloadGranularityLabel(workloadGranularity.value)}统计${metricLabel}`;
});
const workloadAveragePrefix = computed(() => {
  if (workloadGranularity.value === 'day') return '日均';
  if (workloadGranularity.value === 'week') return '周均';
  if (workloadGranularity.value === 'month') return '月均';
  return '平均';
});
const workloadPeakLabel = computed(() => getWorkloadPeakLabel(workloadGranularity.value));
const workloadAggregatedBucketCount = computed(() => {
  if (!isAggregatedWorkloadGranularity(workloadGranularity.value) || !selectedWorkloadRange.value) {
    return 0;
  }
  return countWorkloadBuckets(
    selectedWorkloadRange.value.startMs,
    selectedWorkloadRange.value.endExclusiveMs,
    workloadGranularity.value,
  );
});
const workloadAggregatedTotalValue = computed(() => {
  if (!isAggregatedWorkloadGranularity(workloadGranularity.value)) return 0;
  return calculateAggregatedWorkloadTotalValue(
    scopedWorkload.value,
    workloadGranularity.value,
    workloadMetric.value,
  );
});
const workloadPrimaryStatLabel = computed(() => {
  if (isAggregatedWorkloadGranularity(workloadGranularity.value)) {
    if (workloadMetric.value === 'duration') {
      return '累计工时';
    }
    if (workloadMetric.value === 'turn_count') {
      return '累计轮次';
    }
    return '峰值并发和';
  }
  if (workloadMetric.value === 'duration') {
    return '累计工时';
  }
  if (isAggregatedWorkloadGranularity(workloadGranularity.value) && workloadMetric.value === 'turn_count') {
    return '累计轮次';
  }
  return '轮次';
});
const workloadPrimaryStatValue = computed(() => {
  if (!scopedWorkload.value) return '0';
  if (isAggregatedWorkloadGranularity(workloadGranularity.value)) {
    return `${formatWorkloadMetricValue(workloadAggregatedTotalValue.value, workloadMetric.value)} ${resolveWorkloadMetricUnit(workloadMetric.value)}`;
  }
  if (workloadMetric.value === 'duration') {
    const totalHours = scopedWorkload.value.turns.reduce((sum, turn) => sum + (turn.duration_seconds || 0) / 3600, 0);
    return `${formatDurationHours(totalHours)} 小时`;
  }
  return formatCount(scopedWorkload.value.total_turns);
});
const workloadSecondaryStatLabel = computed(() => {
  if (!isAggregatedWorkloadGranularity(workloadGranularity.value)) {
    return workloadPeakLabel.value;
  }
  if (workloadMetric.value === 'duration') {
    return `${workloadAveragePrefix.value}工时`;
  }
  if (workloadMetric.value === 'turn_count') {
    return `${workloadAveragePrefix.value}轮次`;
  }
  return `${workloadAveragePrefix.value}峰值并发`;
});
const workloadSecondaryStatValue = computed(() => {
  if (!isAggregatedWorkloadGranularity(workloadGranularity.value)) {
    return `${formatWorkloadMetricValue(workloadChartModel.value?.peakValue ?? 0, workloadMetric.value)} ${workloadChartModel.value?.unitLabel ?? resolveWorkloadMetricUnit(workloadMetric.value)}`;
  }
  const bucketCount = workloadAggregatedBucketCount.value;
  const averageValue = bucketCount > 0 ? workloadAggregatedTotalValue.value / bucketCount : 0;
  return `${formatAverageWorkloadMetricValue(averageValue, workloadMetric.value)} ${resolveWorkloadMetricUnit(workloadMetric.value)}`;
});

const calculateMessageOutlineBounds = () => {
  const viewportHeight = typeof window === 'undefined' ? 0 : window.innerHeight;
  const availableHeight = Math.max(
    MESSAGE_WORKSPACE_FALLBACK_HEIGHT,
    messageWorkspaceRef.value?.clientHeight ?? viewportHeight - 430,
  );
  const minimumPaneHeight = Math.min(
    220,
    Math.max(140, Math.floor((availableHeight - MESSAGE_RESIZER_HEIGHT) * 0.3)),
  );
  const maxHeight = Math.max(
    minimumPaneHeight,
    availableHeight - MESSAGE_RESIZER_HEIGHT - minimumPaneHeight,
  );
  const adaptiveHeight = Math.min(
    maxHeight,
    Math.max(minimumPaneHeight, Math.floor((availableHeight - MESSAGE_RESIZER_HEIGHT) / 2)),
  );

  return {
    adaptiveHeight,
    minHeight: minimumPaneHeight,
    maxHeight,
  };
};

const {
  paneHeight: messageOutlineHeight,
  isResizing: isMessageOutlineResizing,
  startResizing: startMessageOutlineResizing,
  updateAdaptiveHeight: updateMessageOutlineHeight,
} = useResizablePane({
  initialHeight: 320,
  storageKey: MESSAGE_OUTLINE_STORAGE_KEY,
  getAdaptiveHeight: () => calculateMessageOutlineBounds().adaptiveHeight,
  getResizeBounds: () => {
    const bounds = calculateMessageOutlineBounds();
    return {
      min: bounds.minHeight,
      max: bounds.maxHeight,
    };
  },
});

const resolveNoteProjectCategoryKey = (note: CodexNoteProjectColorNode) => (
  note.primary_category
  || note.note_categories?.find(item => item?.key)?.key
  || note.node_type
  || note.note_types?.find(item => item?.key)?.key
  || ''
);

const loadNoteProjectColorHints = async () => {
  try {
    const response = await api.post<{ nodes: CodexNoteProjectColorNode[] }>('/notes/query', {
      scope: { mode: 'all' },
      rules: [],
      order_by: 'updated_at',
      order_desc: true,
      limit: 5000,
      include_edges: false,
    });
    noteProjectColorHints.value = (response.data.nodes || [])
      .map((note) => ({
        title: String(note.title || '').trim(),
        categoryKey: String(resolveNoteProjectCategoryKey(note) || '').trim(),
        updatedAt: Number(note.updated_at || 0),
      }))
      .filter(hint => hint.title && hint.categoryKey);
  } catch (error) {
    console.warn('Failed to load note project colors for Codex project colors:', error);
  }
};

const loadNoteTypePalette = async (force = false) => {
  try {
    noteTypePaletteItems.value = await ensureNoteTypePaletteLoaded(force);
    await loadNoteProjectColorHints();
  } catch (error) {
    console.warn('Failed to load note type palette for Codex project colors:', error);
  }
};

const ensureDevicesLoaded = async () => {
  if (isLoadingDevices.value) return;
  isLoadingDevices.value = true;
  try {
    await taskStore.fetchDevices();
    if (!selectedEntryId.value && taskStore.devices.length) {
      selectedEntryId.value = taskStore.devices.length > 1 ? ALL_DEVICES_ENTRY_ID : taskStore.devices[0].id;
    } else if (selectedEntryId.value === ALL_DEVICES_ENTRY_ID && taskStore.devices.length < 2) {
      selectedEntryId.value = taskStore.devices[0]?.id || '';
    }
  } finally {
    isLoadingDevices.value = false;
  }
};

const loadThreadDetail = async (thread: CodexThreadSummaryView | null) => {
  const threadId = thread?.id || '';
  const sourceEntryId = thread ? getThreadSourceEntryId(thread) : selectedEntryId.value;
  const sourceRootDir = thread ? getThreadSourceRootDir(thread) : normalizeRootDirForRequest();
  if (!sourceEntryId || !threadId) {
    selectedThreadId.value = '';
    selectedThreadSourceEntryId.value = '';
    selectedThreadRootDir.value = undefined;
    selectedMessageSeq.value = null;
    threadDetail.value = null;
    detailError.value = '';
    isProcessExpanded.value = false;
    return;
  }

  const previousThreadId = selectedThreadId.value;
  const previousSourceEntryId = selectedThreadSourceEntryId.value;
  const previousMessageSeq = selectedMessageSeq.value;
  const shouldResetMessageScroll = threadId !== selectedThreadId.value || sourceEntryId !== selectedThreadSourceEntryId.value;
  selectedThreadId.value = threadId;
  selectedThreadSourceEntryId.value = sourceEntryId;
  selectedThreadRootDir.value = sourceRootDir;
  detailError.value = '';
  const requestId = ++latestDetailRequestId;
  isLoadingDetail.value = true;
  try {
    const payload = await fetchCodexThreadDetailForEntry(
      sourceEntryId,
      threadId,
      sourceRootDir,
    );
    if (requestId !== latestDetailRequestId) return;
    threadDetail.value = {
      ...payload,
      thread: {
        ...payload.thread,
        source_entry_id: sourceEntryId,
        source_device_name: thread?.source_device_name,
        source_root_dir: sourceRootDir,
      },
    };
    const summaryItems = buildMessageSummaryItems(payload.messages);
    const nextSelectedSummary = (
      threadId === previousThreadId && sourceEntryId === previousSourceEntryId
        ? summaryItems.find((item) => item.displayMessage.seq === previousMessageSeq)
        : null
    ) ?? summaryItems[0] ?? null;
    selectedMessageSeq.value = nextSelectedSummary?.displayMessage.seq ?? null;
    isProcessExpanded.value = false;
    await nextTick();
    updateMessageOutlineHeight();
    if (shouldResetMessageScroll || nextSelectedSummary?.displayMessage.seq !== previousMessageSeq) {
      messageScrollbarRef.value?.setScrollTop?.(0);
    }
  } catch (error: any) {
    if (requestId !== latestDetailRequestId) return;
    selectedMessageSeq.value = null;
    threadDetail.value = null;
    detailError.value = extractErrorMessage(error, '读取会话详情失败');
  } finally {
    if (requestId === latestDetailRequestId) {
      isLoadingDetail.value = false;
    }
  }
};

const loadWorkload = async () => {
  const sourceDevices = selectedSourceDevices.value;
  if (!sourceDevices.length) {
    workload.value = null;
    workloadError.value = '';
    return;
  }

  workloadError.value = '';
  const requestId = ++latestWorkloadRequestId;
  isLoadingWorkload.value = true;
  try {
    const payload = isAllDevicesMode.value
      ? await (async () => {
          const results = await Promise.allSettled(
            sourceDevices.map(async (device) => {
              try {
                return {
                  device,
                  workload: await fetchCodexWorkloadForEntry(device.id, {
                    rootDir: normalizeRootDirForRequest(),
                    timeoutMs: getCodexWorkloadTimeoutMs(device),
                  }),
                };
              } catch (error) {
                throw createDeviceRequestFailure(device, error);
              }
            }),
          );
          const fulfilled = results
            .filter((item): item is PromiseFulfilledResult<{ device: Device; workload: CodexWorkloadResponse }> => item.status === 'fulfilled')
            .map(item => item.value);
          if (!fulfilled.length) {
            throw new Error(formatDeviceFailureDetails(results, '读取 Codex 工作强度失败') || '读取 Codex 工作强度失败');
          }
          return buildMergedWorkload(fulfilled);
        })()
      : await fetchCodexWorkloadForEntry(sourceDevices[0].id, normalizeRootDirForRequest());
    if (requestId !== latestWorkloadRequestId) return;
    workload.value = payload;
  } catch (error: any) {
    if (requestId !== latestWorkloadRequestId) return;
    workload.value = null;
    workloadError.value = extractErrorMessage(error, '读取 Codex 工作强度失败');
  } finally {
    if (requestId === latestWorkloadRequestId) {
      isLoadingWorkload.value = false;
    }
  }
};

const syncSelectionAfterOverview = async (preserveThread = true) => {
  const threads = allThreads.value;
  const nextThread = preserveThread
    ? threads.find((item) => item.id === selectedThreadId.value && getThreadSourceEntryId(item) === selectedThreadSourceEntryId.value) ?? threads[0] ?? null
    : threads[0] ?? null;

  if (!nextThread) {
    selectedThreadId.value = '';
    selectedThreadSourceEntryId.value = '';
    selectedThreadRootDir.value = undefined;
    selectedMessageSeq.value = null;
    threadDetail.value = null;
    detailError.value = '';
    return;
  }

  await loadThreadDetail(nextThread);
};

const buildOverviewRequestParams = (threadOffset: number, threadLimit = THREAD_PAGE_SIZE) => ({
  rootDir: normalizeRootDirForRequest(),
  threadOffset,
  threadLimit,
});

const loadOverview = async (preserveThread = true) => {
  if (isLoadingOverview.value) return;
  const sourceDevices = selectedSourceDevices.value;
  if (!sourceDevices.length) {
    overview.value = null;
    threadDetail.value = null;
    workload.value = null;
    selectedThreadSourceEntryId.value = '';
    selectedThreadRootDir.value = undefined;
    overviewError.value = '';
    detailError.value = '';
    workloadError.value = '';
    resetMessageImageState();
    isProcessExpanded.value = false;
    return;
  }

  overviewError.value = '';
  const requestId = ++latestOverviewRequestId;
  const page = currentThreadPage.value;
  const pageOffset = getThreadPageOffset(page);
  const pageEnd = pageOffset + THREAD_PAGE_SIZE;
  isLoadingOverview.value = true;
  resetMessageImageState();
  isProcessExpanded.value = false;
  try {
    const payload = isAllDevicesMode.value
      ? await (async () => {
          const results = await Promise.allSettled(
            sourceDevices.map(async (device) => {
              try {
                const overviewPayload = await fetchCodexOverviewForEntry(device.id, {
                  ...buildOverviewRequestParams(0, pageEnd),
                  timeoutMs: getCodexOverviewTimeoutMs(device),
                });
                return {
                  device,
                  overview: overviewPayload,
                };
              } catch (error) {
                throw createDeviceRequestFailure(device, error);
              }
            }),
          );
          const fulfilled = results
            .filter((item): item is PromiseFulfilledResult<{ device: Device; overview: CodexOverviewResponse }> => item.status === 'fulfilled')
            .map(item => item.value);
          const failureDetails = formatDeviceFailureDetails(results, '读取 Codex 会话失败');
          if (!fulfilled.length) {
            throw new Error(failureDetails || '读取 Codex 会话失败');
          }
          if (failureDetails) {
            overviewError.value = `${failureDetails}，已显示其余设备`;
          }
          return buildMergedOverview(fulfilled, page);
        })()
      : await (async () => {
          const device = sourceDevices[0];
          return fetchCodexOverviewForEntry(device.id, buildOverviewRequestParams(pageOffset));
        })();
    if (requestId !== latestOverviewRequestId) return;
    overview.value = payload;
    rootDirInput.value = isAllDevicesMode.value ? '' : payload.root_dir;
    // Thread detail owns its own loading state; avoid keeping the whole workspace masked.
    void syncSelectionAfterOverview(preserveThread);
    // Workload is best-effort context and should not keep the thread pane under a loading mask.
    void loadWorkload();
  } catch (error: any) {
    if (requestId !== latestOverviewRequestId) return;
    overview.value = null;
    threadDetail.value = null;
    workload.value = null;
    overviewError.value = extractErrorMessage(error, '读取 Codex 会话失败');
    detailError.value = '';
    workloadError.value = '';
  } finally {
    if (requestId === latestOverviewRequestId) {
      isLoadingOverview.value = false;
    }
  }
};

const handleRefresh = async () => {
  void loadNoteTypePalette(true);
  await loadOverview(true);
};

const handleThreadPageChange = async () => {
  selectedThreadId.value = '';
  selectedThreadSourceEntryId.value = '';
  selectedThreadRootDir.value = undefined;
  selectedMessageSeq.value = null;
  threadDetail.value = null;
  await loadOverview(false);
};

const handleDeviceChange = async () => {
  currentThreadPage.value = 1;
  selectedThreadId.value = '';
  selectedThreadSourceEntryId.value = '';
  selectedThreadRootDir.value = undefined;
  selectedMessageSeq.value = null;
  threadDetail.value = null;
  isProcessExpanded.value = false;
  workloadGranularity.value = 'day';
  workloadMetric.value = 'duration';
  workloadDateRange.value = null;
  if (isAllDevicesMode.value) {
    rootDirInput.value = '';
  }
  await loadOverview(false);
};

const handleSelectThread = async (thread: CodexThreadSummaryView) => {
  await loadThreadDetail(thread);
};

const handleSelectSummary = async (item: CodexMessageSummaryItem) => {
  if (item.displayMessage.seq === selectedMessageSeq.value) return;
  selectedMessageSeq.value = item.displayMessage.seq;
  isProcessExpanded.value = false;
  await nextTick();
  messageScrollbarRef.value?.setScrollTop?.(0);
};

const handleProcessDetailsToggle = (event: Event) => {
  isProcessExpanded.value = (event.currentTarget as HTMLDetailsElement | null)?.open ?? false;
};

const goToClusterTasks = () => {
  void router.push('/cluster/runtime');
};

const applyRouteSeed = () => {
  if (typeof route.query.entryId === 'string' && route.query.entryId.trim()) {
    selectedEntryId.value = route.query.entryId.trim();
  }
  if (typeof route.query.rootDir === 'string') {
    rootDirInput.value = route.query.rootDir.trim();
  }
};

watch(workloadGranularity, (nextGranularity) => {
  const nextOptions = WORKLOAD_METRIC_OPTIONS[nextGranularity];
  if (!nextOptions.some(option => option.value === workloadMetric.value)) {
    workloadMetric.value = nextOptions[0].value;
  }
});

watch(workloadRangeBounds, (bounds) => {
  if (!bounds) {
    workloadDateRange.value = null;
    return;
  }

  if (!workloadDateRange.value?.length) {
    workloadDateRange.value = buildDefaultWorkloadDateRange(bounds);
    return;
  }

  const nextStartMs = Math.min(
    Math.max(startOfLocalDayMs(workloadDateRange.value[0].getTime()), bounds.startMs),
    bounds.endMs,
  );
  const nextEndMs = Math.max(
    nextStartMs,
    Math.min(startOfLocalDayMs(workloadDateRange.value[1].getTime()), bounds.endMs),
  );

  if (
    startOfLocalDayMs(workloadDateRange.value[0].getTime()) !== nextStartMs
    || startOfLocalDayMs(workloadDateRange.value[1].getTime()) !== nextEndMs
  ) {
    workloadDateRange.value = [new Date(nextStartMs), new Date(nextEndMs)];
  }
}, { immediate: true });

watch(workloadDateRange, (nextRange) => {
  const bounds = workloadRangeBounds.value;
  if (!bounds || !nextRange?.length) return;

  const nextStartMs = Math.min(
    Math.max(startOfLocalDayMs(nextRange[0].getTime()), bounds.startMs),
    bounds.endMs,
  );
  const nextEndMs = Math.max(
    nextStartMs,
    Math.min(startOfLocalDayMs(nextRange[1].getTime()), bounds.endMs),
  );

  if (
    startOfLocalDayMs(nextRange[0].getTime()) !== nextStartMs
    || startOfLocalDayMs(nextRange[1].getTime()) !== nextEndMs
  ) {
    workloadDateRange.value = [new Date(nextStartMs), new Date(nextEndMs)];
  }
});

onMounted(async () => {
  applyRouteSeed();
  void loadNoteTypePalette();
  await ensureDevicesLoaded();
  if (selectedEntryId.value) {
    await loadOverview(false);
  }
});

watch(
  [selectedThreadId, selectedMessageSeq, isProcessExpanded],
  () => {
    void syncVisibleMessageImages();
  },
  { flush: 'post' },
);
</script>

<template>
  <div class="codex-page">
    <section v-if="showDeviceEmptyState" class="codex-empty">
      <div class="codex-empty-badge">Codex</div>
      <h2>先添加设备</h2>
      <p>这个页面按设备读取本机或远端节点上的 `.codex` 会话数据，所以要先在集群管理里准备可用设备。</p>
      <el-button type="primary" @click="goToClusterTasks">去运行管理</el-button>
    </section>

    <section v-else class="codex-shell">
      <section class="codex-toolbar">
        <div class="codex-toolbar-row">
          <div class="codex-field">
            <span class="codex-field-label">设备</span>
            <el-select
              v-model="selectedEntryId"
              class="codex-field-control"
              size="large"
              placeholder="选择设备"
              :disabled="isLoadingDevices || !devices.length"
              @change="handleDeviceChange"
            >
              <el-option
                v-if="devices.length > 1"
                :key="ALL_DEVICES_ENTRY_ID"
                label="全部设备"
                :value="ALL_DEVICES_ENTRY_ID"
              />
              <el-option
                v-for="device in devices"
                :key="device.id"
                :label="formatDeviceLabel(device)"
                :value="device.id"
              />
            </el-select>
          </div>

          <div class="codex-field codex-field-root">
            <div class="codex-field-label-row">
              <span class="codex-field-label">Codex 根目录</span>
              <el-tooltip effect="light" placement="top">
                <template #content>
                  只需要配置 `.codex` 根目录。页面会从其中的 `state_5.sqlite`、`sessions` 和 `.codex-global-state.json` 读取项目分组与会话内容。
                </template>
                <button type="button" class="codex-help-button" aria-label="Codex 根目录说明">
                  <el-icon><QuestionFilled /></el-icon>
                </button>
              </el-tooltip>
            </div>
            <el-input
              v-model="rootDirInput"
              class="codex-field-control"
              size="large"
              clearable
              :disabled="isAllDevicesMode"
              :placeholder="isAllDevicesMode ? '全部设备使用各自默认 .codex' : '例如 C:\Users\kzche\.codex'"
              @keyup.enter="handleRefresh"
            />
          </div>

          <div class="codex-toolbar-actions">
            <el-button
              type="primary"
              size="large"
              :loading="isLoadingOverview"
              :disabled="!canLoad"
              @click="handleRefresh"
            >
              <el-icon><RefreshRight /></el-icon>
              <span>刷新</span>
            </el-button>
          </div>
        </div>

        <section class="codex-workload-panel" v-loading="isLoadingWorkload">
          <div class="codex-workload-header">
            <div class="codex-workload-title-group">
              <div class="codex-workload-title-row">
                <span class="codex-workload-title">工作统计</span>
                <el-tooltip effect="light" placement="top-start">
                  <template #content>
                    {{ workloadModeDescription }}
                  </template>
                  <button type="button" class="codex-help-button" aria-label="工作统计说明">
                    <el-icon><QuestionFilled /></el-icon>
                  </button>
                </el-tooltip>
              </div>
            </div>
            <div class="codex-workload-header-side">
              <div class="codex-workload-granularity">
                <small>颗粒度</small>
                <el-select v-model="workloadGranularity" size="small" class="codex-workload-granularity-select">
                  <el-option
                    v-for="option in WORKLOAD_GRANULARITY_OPTIONS"
                    :key="option.value"
                    :label="option.label"
                    :value="option.value"
                  />
                </el-select>
              </div>
              <div class="codex-workload-granularity">
                <small>统计指标</small>
                <el-select v-model="workloadMetric" size="small" class="codex-workload-granularity-select">
                  <el-option
                    v-for="option in workloadMetricOptions"
                    :key="option.value"
                    :label="option.label"
                    :value="option.value"
                  />
                </el-select>
              </div>
              <div class="codex-workload-granularity codex-workload-range">
                <small>统计范围</small>
                <el-date-picker
                  v-model="workloadDateRange"
                  type="daterange"
                  size="small"
                  unlink-panels
                  clearable
                  class="codex-workload-range-picker"
                  range-separator="至"
                  start-placeholder="开始日期"
                  end-placeholder="结束日期"
                  :disabled="!workloadRangeBounds"
                  :disabled-date="isWorkloadDateDisabled"
                />
              </div>
              <div v-if="workload" class="codex-workload-stats">
                <div class="codex-workload-stat">
                  <small>{{ workloadPrimaryStatLabel }}</small>
                  <strong>{{ workloadPrimaryStatValue }}</strong>
                </div>
                <div class="codex-workload-stat">
                  <small>{{ workloadSecondaryStatLabel }}</small>
                  <strong>{{ workloadSecondaryStatValue }}</strong>
                </div>
                <div class="codex-workload-stat">
                  <small>线程</small>
                  <strong>{{ formatCount(scopedWorkload?.total_threads) }}</strong>
                </div>
              </div>
            </div>
          </div>

          <div v-if="workloadError" class="codex-workload-empty">{{ workloadError }}</div>

          <template v-else-if="workload">
            <div v-if="workloadChartModel?.hasActivity" class="codex-workload-chart-shell">
              <svg
                class="codex-workload-chart"
                :viewBox="workloadChartModel.viewBox"
                preserveAspectRatio="none"
                aria-hidden="true"
              >
                <defs>
                  <clipPath
                    v-for="bar in workloadChartModel.bars"
                    :id="bar.clipId"
                    :key="`clip-${bar.clipId}`"
                    clipPathUnits="userSpaceOnUse"
                  >
                    <rect
                      :x="bar.x"
                      :y="bar.y"
                      :width="bar.width"
                      :height="bar.height"
                      rx="4"
                      ry="4"
                    />
                  </clipPath>
                </defs>

                <g class="codex-workload-guides">
                  <line
                    v-for="guide in workloadChartModel.guides"
                    :key="guide.key"
                    :x1="WORKLOAD_CHART_PADDING.left"
                    :x2="workloadChartModel.chartWidth - WORKLOAD_CHART_PADDING.right"
                    :y1="guide.y"
                    :y2="guide.y"
                    class="codex-workload-guide-line"
                  />
                  <text
                    v-for="guide in workloadChartModel.guides"
                    :key="`label-${guide.key}`"
                    :x="workloadChartModel.chartWidth - 2"
                    :y="guide.y - 4"
                    class="codex-workload-guide-text"
                  >
                    {{ guide.label }}
                  </text>
                </g>

                <line
                  :x1="WORKLOAD_CHART_PADDING.left"
                  :x2="workloadChartModel.chartWidth - WORKLOAD_CHART_PADDING.right"
                  :y1="workloadChartModel.baselineY"
                  :y2="workloadChartModel.baselineY"
                  class="codex-workload-baseline"
                />

                <g class="codex-workload-tick-guides">
                  <template
                    v-for="tick in workloadChartModel.ticks"
                    :key="`tick-guide-${tick.key}`"
                  >
                    <line
                      :x1="tick.x"
                      :x2="tick.x"
                      :y1="WORKLOAD_CHART_PADDING.top"
                      :y2="workloadChartModel.baselineY"
                      class="codex-workload-tick-guide-line"
                    />
                    <line
                      :x1="tick.x"
                      :x2="tick.x"
                      :y1="workloadChartModel.baselineY"
                      :y2="workloadChartModel.baselineY + 4"
                      class="codex-workload-tick-mark"
                    />
                  </template>
                </g>

                <g class="codex-workload-bars">
                  <g
                    v-for="bar in workloadChartModel.bars"
                    :key="bar.key"
                  >
                    <g :clip-path="`url(#${bar.clipId})`">
                      <rect
                        v-for="slice in bar.slices"
                        :key="slice.key"
                        :x="bar.x"
                        :y="slice.y"
                        :width="bar.width"
                        :height="slice.height"
                        :fill="slice.fill"
                        class="codex-workload-bar-slice"
                      >
                        <title>{{ slice.title }}</title>
                      </rect>
                    </g>
                    <rect
                      :x="bar.x"
                      :y="bar.y"
                      :width="bar.width"
                      :height="bar.height"
                      rx="4"
                      ry="4"
                      class="codex-workload-bar-frame"
                    >
                      <title>{{ bar.title }}</title>
                    </rect>
                  </g>
                </g>

                <template
                  v-for="tick in workloadChartModel.ticks"
                  :key="tick.key"
                >
                  <text
                    v-if="tick.showLabel !== false && tick.label"
                    :x="tick.x"
                    :y="workloadChartModel.chartHeight - 6"
                    text-anchor="middle"
                    class="codex-workload-tick-text"
                  >
                    {{ tick.label }}
                  </text>
                </template>
              </svg>
            </div>
            <div v-else class="codex-workload-empty">当前根目录下还没有可统计的工作区间</div>

            <div class="codex-workload-footer">
              <small v-if="workload.skipped_threads">
                跳过 {{ formatCount(workload.skipped_threads) }} 条缺失或异常会话
              </small>
            </div>
          </template>
        </section>

        <div v-if="overviewError" class="codex-error-banner">{{ overviewError }}</div>
      </section>

      <section class="codex-workspace" v-loading="isLoadingOverview">
        <article class="codex-pane codex-pane-threads">
          <header class="codex-pane-header">
            <span>会话</span>
            <small>{{ threadPaneCountLabel }}</small>
          </header>
          <div v-if="totalThreadCount" class="codex-thread-pagination">
            <span class="codex-thread-pagination-text">{{ threadPaginationText }}</span>
            <StandardPagination
              v-model:page="currentThreadPage"
              :page-size="THREAD_PAGE_SIZE"
              :total="totalThreadCount"
              :show-page-size="false"
              :disabled="isLoadingOverview"
              @page-change="handleThreadPageChange"
            />
          </div>
          <el-scrollbar class="codex-pane-scroll">
            <button
              v-for="thread in allThreads"
              :key="getThreadSelectionKey(thread)"
              type="button"
              class="codex-thread-item"
              :class="{ 'is-active': isSelectedThread(thread) }"
              :style="getThreadSurfaceStyle(thread)"
              @click="handleSelectThread(thread)"
            >
              <div class="codex-thread-top">
                <span class="codex-thread-title">{{ thread.title }}</span>
                <el-tag v-if="thread.archived" size="small" effect="plain" type="info">归档</el-tag>
              </div>
              <p v-if="thread.preview" class="codex-thread-preview">{{ thread.preview }}</p>
              <div class="codex-thread-footer">
                <span class="codex-thread-source">{{ formatThreadSource(thread) }}</span>
                <span class="codex-thread-meta">{{ formatDateTime(thread.updated_at) }}</span>
              </div>
            </button>

            <el-empty
              v-if="!isLoadingOverview && !allThreads.length"
              description="当前根目录下还没有会话"
            />
          </el-scrollbar>
        </article>

        <article class="codex-pane codex-pane-detail">
          <header class="codex-pane-header codex-pane-header-detail">
            <div>
              <span>消息</span>
              <small>{{ formatCount(messageSummaryItems.length, ' 条') }}</small>
            </div>
            <div v-if="isLoadingDetail" class="codex-detail-loading">读取中</div>
          </header>

          <div v-if="detailError" class="codex-detail-error">{{ detailError }}</div>

          <div v-else-if="threadDetail" class="codex-detail">
            <section class="codex-detail-summary">
              <div class="codex-detail-title-row">
                <h2 :title="threadDetail.thread.title">{{ threadDetail.thread.title }}</h2>
                <div class="codex-detail-tags">
                  <el-tag size="small" effect="plain">{{ threadDetail.thread.group_label }}</el-tag>
                  <el-tag
                    v-if="threadDetail.thread.group_secondary_label"
                    size="small"
                    effect="plain"
                    type="info"
                  >
                    {{ threadDetail.thread.group_secondary_label }}
                  </el-tag>
                  <el-tag v-if="threadDetail.thread.archived" size="small" effect="plain" type="info">
                    已归档
                  </el-tag>
                  <el-tag
                    v-if="isAllDevicesMode && threadDetail.thread.source_device_name"
                    size="small"
                    effect="plain"
                    type="warning"
                  >
                    {{ threadDetail.thread.source_device_name }}
                  </el-tag>
                </div>
              </div>

              <div class="codex-detail-meta-grid">
                <div class="codex-detail-meta">
                  <span class="codex-detail-meta-label">工作目录</span>
                  <code>{{ threadDetail.thread.cwd || '未记录' }}</code>
                </div>
                <div class="codex-detail-meta">
                  <span class="codex-detail-meta-label">JSONL</span>
                  <code>{{ threadDetail.thread.rollout_path || '未记录' }}</code>
                </div>
                <div class="codex-detail-meta">
                  <span class="codex-detail-meta-label">最近更新</span>
                  <span>{{ formatDateTime(threadDetail.thread.updated_at) }}</span>
                </div>
                <div class="codex-detail-meta">
                  <span class="codex-detail-meta-label">角色计数</span>
                  <span>
                    用户 {{ formatCount(threadDetail.user_message_count) }} /
                    助手 {{ formatCount(threadDetail.assistant_message_count) }}
                  </span>
                </div>
              </div>
            </section>

            <section
              v-if="threadDetail.messages.length"
              ref="messageWorkspaceRef"
              class="codex-message-workspace"
            >
              <section class="codex-message-outline" :style="{ height: `${messageOutlineHeight}px` }">
                <div class="codex-message-section-header">
                  <span>节点摘要</span>
                  <small>点选卡片查看完整内容</small>
                </div>
                <div class="codex-message-outline-scroll">
                  <button
                    v-for="(item, index) in messageSummaryItems"
                    :key="item.key"
                    type="button"
                    class="codex-message-node-card"
                    :class="[`is-${item.role}`, { 'is-active': item.displayMessage.seq === selectedMessage?.seq }]"
                    :style="getMessageSurfaceStyle(item.displayMessage)"
                    :title="buildCompactMessageSummary(item, messageSummaryItems[index - 1] ?? null)"
                    @click="handleSelectSummary(item)"
                  >
                    <span class="codex-message-node-inline">
                      <strong class="codex-message-node-prefix">
                        {{ buildCompactMessagePrefix(item, messageSummaryItems[index - 1] ?? null) }}
                      </strong>
                      <span class="codex-message-node-inline-text">
                        {{ summarizeMessageText(extractPreferredSummaryText(item.displayMessage.text), 4000) }}
                      </span>
                    </span>
                  </button>
                </div>
              </section>

              <button
                type="button"
                class="codex-message-resizer"
                :class="{ 'is-resizing': isMessageOutlineResizing }"
                aria-label="拖拽调整节点摘要和完整内容高度"
                title="拖拽调整节点摘要和完整内容高度"
                @mousedown.prevent="startMessageOutlineResizing"
              >
                <span class="codex-message-resizer-indicator"></span>
              </button>

              <section class="codex-message-content">
                <div class="codex-message-section-header">
                  <span>完整内容</span>
                  <small v-if="selectedSummaryItem">
                    {{ formatSelectedSummaryMeta(selectedSummaryItem) }}
                  </small>
                </div>
                <el-scrollbar ref="messageScrollbarRef" class="codex-message-scroll">
                  <section class="codex-message-list">
                    <details
                      v-if="selectedSummaryItem?.role === 'assistant' && selectedSummaryItem.processMessages.length"
                      :key="selectedSummaryItem.key"
                      :open="isProcessExpanded"
                      class="codex-message-process-details"
                      @toggle="handleProcessDetailsToggle"
                    >
                      <summary class="codex-message-process-summary">
                        <span>过程 {{ formatCount(selectedSummaryItem.processMessages.length, ' 条') }}</span>
                        <small>展开查看执行过程</small>
                      </summary>
                      <section class="codex-message-process-list">
                        <article
                          v-for="message in selectedSummaryItem.processMessages"
                          :key="`process-${message.seq}`"
                          class="codex-message-card codex-message-card-process"
                          :style="getMessageSurfaceStyle(message)"
                        >
                          <div class="codex-message-header">
                            <div class="codex-message-role">
                              <el-icon><ChatDotRound /></el-icon>
                              <strong>{{ formatMessageRole(message.role) }}</strong>
                              <el-tag
                                v-if="message.phase"
                                size="small"
                                effect="plain"
                                :type="message.phase === 'final_answer' ? 'success' : 'info'"
                              >
                                {{ formatMessagePhase(message.phase) }}
                              </el-tag>
                            </div>
                            <span class="codex-message-time">{{ formatDateTime(message.timestamp) }}</span>
                          </div>
                          <section class="codex-message-body">
                            <template v-for="block in buildMessageRenderBlocks(message)" :key="block.key">
                              <pre v-if="block.type === 'text'" class="codex-message-text">{{ block.text }}</pre>
                              <figure
                                v-else-if="block.type === 'image'"
                                class="codex-message-image-shell"
                              >
                                <img
                                  class="codex-message-image"
                                  :src="block.image?.image_url"
                                  :alt="formatMessageImageAlt(message, block.imageIndex)"
                                  loading="lazy"
                                />
                              </figure>
                              <div v-else class="codex-message-image-shell is-placeholder">
                                <span class="codex-message-image-placeholder">
                                  {{ getMessageImagePlaceholderLabel(message) }}
                                </span>
                              </div>
                            </template>
                          </section>
                        </article>
                      </section>
                    </details>

                    <article
                      v-if="selectedMessage"
                      class="codex-message-card"
                      :class="`is-${selectedMessage.role}`"
                      :style="getMessageSurfaceStyle(selectedMessage)"
                    >
                      <div class="codex-message-header">
                        <div class="codex-message-role">
                          <el-icon><ChatDotRound /></el-icon>
                          <strong>{{ formatMessageRole(selectedMessage.role) }}</strong>
                          <el-tag
                            v-if="selectedMessage.phase"
                            size="small"
                            effect="plain"
                            :type="selectedMessage.phase === 'final_answer' ? 'success' : 'info'"
                          >
                            {{ formatMessagePhase(selectedMessage.phase) }}
                          </el-tag>
                        </div>
                        <span class="codex-message-time">{{ formatDateTime(selectedMessage.timestamp) }}</span>
                      </div>
                      <section class="codex-message-body">
                        <template v-for="block in buildMessageRenderBlocks(selectedMessage)" :key="block.key">
                          <pre v-if="block.type === 'text'" class="codex-message-text">{{ block.text }}</pre>
                          <figure
                            v-else-if="block.type === 'image'"
                            class="codex-message-image-shell"
                          >
                            <img
                              class="codex-message-image"
                              :src="block.image?.image_url"
                              :alt="formatMessageImageAlt(selectedMessage, block.imageIndex)"
                              loading="lazy"
                            />
                          </figure>
                          <div v-else class="codex-message-image-shell is-placeholder">
                            <span class="codex-message-image-placeholder">
                              {{ getMessageImagePlaceholderLabel(selectedMessage) }}
                            </span>
                          </div>
                        </template>
                      </section>
                    </article>
                  </section>
                </el-scrollbar>
              </section>
            </section>

            <el-empty v-else description="当前会话还没有可显示的消息节点" />
          </div>

          <el-empty v-else description="选择一条会话后查看消息内容" />
        </article>
      </section>
    </section>
  </div>
</template>

<style scoped>
.codex-page {
  height: calc(100dvh - 60px);
  min-height: calc(100dvh - 60px);
  display: flex;
  flex-direction: column;
}

.codex-empty,
.codex-shell {
  display: flex;
  flex-direction: column;
  gap: 16px;
  flex: 1;
  min-height: 0;
}

.codex-empty {
  align-items: flex-start;
  padding: 24px;
  border: 1px solid var(--el-border-color-light);
  border-radius: 20px;
  background: linear-gradient(180deg, var(--el-fill-color-blank), var(--el-fill-color-light));
}

.codex-empty-badge {
  padding: 6px 10px;
  border-radius: 999px;
  background: var(--el-color-primary-light-9);
  color: var(--el-color-primary);
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.08em;
}

.codex-toolbar {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 18px;
  border: 1px solid var(--el-border-color-light);
  border-radius: 20px;
  background: linear-gradient(180deg, var(--el-fill-color-blank), var(--el-fill-color-light));
}

.codex-toolbar-row {
  display: grid;
  grid-template-columns: minmax(220px, 320px) minmax(260px, 1fr) auto;
  gap: 14px;
  align-items: end;
}

.codex-field {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
}

.codex-field-root {
  min-width: 0;
}

.codex-field-label,
.codex-field-label-row {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--el-text-color-regular);
  font-size: 13px;
}

.codex-field-control {
  width: 100%;
}

.codex-help-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border: none;
  border-radius: 50%;
  background: transparent;
  color: var(--el-text-color-secondary);
  cursor: pointer;
}

.codex-toolbar-actions {
  display: flex;
  align-items: center;
}

.codex-workload-panel {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 12px 14px 14px;
  border-radius: 18px;
  border: 1px solid var(--el-border-color-lighter);
  background: var(--el-fill-color-blank);
}

.codex-workload-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.codex-workload-header-side {
  display: flex;
  align-items: flex-start;
  justify-content: flex-end;
  flex-wrap: wrap;
  gap: 10px;
}

.codex-workload-title-group {
  display: flex;
  align-items: center;
  flex: 0 0 auto;
  min-height: 28px;
}

.codex-workload-title-row {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  flex-wrap: nowrap;
  white-space: nowrap;
}

.codex-workload-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--el-text-color-primary);
  white-space: nowrap;
}

.codex-workload-footer small {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.codex-workload-granularity {
  display: flex;
  align-items: center;
  gap: 8px;
}

.codex-workload-range {
  min-width: 0;
}

.codex-workload-granularity small {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  white-space: nowrap;
}

.codex-workload-granularity-select {
  width: 132px;
}

.codex-workload-range-picker {
  width: 252px;
}

.codex-workload-stats {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 8px;
}

.codex-workload-stat {
  display: inline-flex;
  align-items: baseline;
  gap: 6px;
  padding: 6px 10px;
  border-radius: 12px;
  background: var(--el-fill-color-light);
  border: 1px solid var(--el-border-color-extra-light);
}

.codex-workload-stat small {
  color: var(--el-text-color-secondary);
  font-size: 11px;
}

.codex-workload-stat strong {
  font-size: 14px;
  color: var(--el-text-color-primary);
}

.codex-workload-chart-shell {
  width: 100%;
}

.codex-workload-chart {
  display: block;
  width: 100%;
  height: 136px;
  overflow: visible;
}

.codex-workload-guide-line,
.codex-workload-baseline {
  stroke: rgba(0, 0, 0, 0.08);
  stroke-width: 1;
}

.codex-workload-tick-guide-line {
  stroke: rgba(0, 0, 0, 0.08);
  stroke-width: 1;
}

.codex-workload-tick-mark {
  stroke: rgba(0, 0, 0, 0.16);
  stroke-width: 1;
}

.codex-workload-guide-text,
.codex-workload-tick-text {
  fill: var(--el-text-color-secondary);
  font-size: 11px;
}

.codex-workload-bar-slice {
  stroke: rgba(255, 255, 255, 0.82);
  stroke-width: 0.8;
  vector-effect: non-scaling-stroke;
}

.codex-workload-bar-frame {
  fill: none;
  stroke: rgba(0, 0, 0, 0.08);
  stroke-width: 1;
  vector-effect: non-scaling-stroke;
}

.codex-workload-empty {
  padding: 20px 14px;
  border-radius: 14px;
  background: var(--el-fill-color-light);
  color: var(--el-text-color-secondary);
  text-align: center;
  font-size: 13px;
}

.codex-workload-footer {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  gap: 8px 16px;
}

.codex-error-banner,
.codex-detail-error {
  padding: 10px 12px;
  border-radius: 14px;
  background: var(--el-color-danger-light-9);
  color: var(--el-color-danger);
}

.codex-workspace {
  display: grid;
  grid-template-columns: clamp(280px, 30vw, 360px) minmax(0, 1fr);
  gap: 14px;
  flex: 1;
  min-height: 0;
  overflow: hidden;
}

.codex-pane {
  display: flex;
  flex-direction: column;
  min-height: 0;
  border: 1px solid var(--el-border-color-light);
  border-radius: 20px;
  background: var(--el-fill-color-blank);
  overflow: hidden;
}

.codex-pane-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 18px;
  border-bottom: 1px solid var(--el-border-color-lighter);
  background: rgba(0, 0, 0, 0.02);
  font-weight: 600;
}

.codex-pane-header small {
  color: var(--el-text-color-secondary);
  font-weight: 400;
}

.codex-pane-scroll,
.codex-message-scroll {
  min-height: 0;
  flex: 1;
  height: 0;
}

.codex-thread-item {
  width: 100%;
  padding: 14px 16px;
  border: none;
  border-bottom: 1px solid var(--el-border-color-extra-light);
  background: var(--codex-thread-surface-bg, transparent);
  color: var(--codex-surface-fg, var(--el-text-color-primary));
  text-align: left;
  cursor: pointer;
  transition: box-shadow 0.16s ease, filter 0.16s ease;
}

.codex-thread-item:hover {
  filter: brightness(0.985);
}

.codex-thread-item.is-active {
  box-shadow:
    inset 3px 0 0 var(--el-color-primary),
    inset 0 0 0 999px rgba(64, 158, 255, 0.08);
}

.codex-thread-top {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  min-width: 0;
}

.codex-thread-title {
  flex: 1;
  min-width: 0;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  font-weight: 600;
  color: inherit;
  line-height: 1.45;
  word-break: break-word;
}

.codex-thread-meta,
.codex-thread-source,
.codex-thread-preview,
.codex-message-time,
.codex-detail-loading {
  color: var(--codex-surface-muted, var(--el-text-color-secondary));
}

.codex-thread-preview {
  margin: 8px 0 0;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  line-height: 1.5;
}

.codex-thread-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-top: 10px;
  font-size: 12px;
}

.codex-thread-source {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.codex-thread-meta {
  flex-shrink: 0;
}

.codex-thread-top :deep(.el-tag) {
  flex-shrink: 0;
}

.codex-thread-pagination {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 8px;
  padding: 8px 12px;
  border-bottom: 1px solid var(--el-border-color-extra-light);
}

.codex-thread-pagination-text {
  flex: 1;
  min-width: 0;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.codex-thread-pagination :deep(.standard-pagination) {
  flex-shrink: 0;
  margin-left: auto;
}

.codex-pane-detail {
  min-width: 0;
}

.codex-pane-header-detail {
  align-items: flex-start;
}

.codex-detail {
  display: flex;
  flex-direction: column;
  min-height: 0;
  height: 100%;
}

.codex-detail-summary {
  padding: 14px 16px;
  border-bottom: 1px solid var(--el-border-color-lighter);
  flex-shrink: 0;
}

.codex-detail-title-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  min-width: 0;
}

.codex-detail-title-row h2 {
  margin: 0;
  flex: 1;
  min-width: 0;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  font-size: 17px;
  line-height: 1.45;
  word-break: break-word;
}

.codex-detail-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  justify-content: flex-end;
  flex-shrink: 0;
}

.codex-detail-meta-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
  margin-top: 12px;
}

.codex-detail-meta {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
}

.codex-detail-meta-label {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.codex-detail-meta code {
  padding: 8px 10px;
  border-radius: 12px;
  background: var(--el-fill-color-light);
  color: var(--el-text-color-primary);
  font-size: 12px;
  line-height: 1.45;
  white-space: pre-wrap;
  word-break: break-all;
}

.codex-message-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 0;
}

.codex-message-workspace {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
  overflow: hidden;
}

.codex-message-outline {
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  flex-shrink: 0;
  min-height: 0;
  padding: 12px 14px 14px;
}

.codex-message-section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
  font-weight: 600;
}

.codex-message-section-header small {
  color: var(--el-text-color-secondary);
  font-weight: 400;
}

.codex-message-outline-scroll {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
  gap: 8px;
  overflow-x: hidden;
  overflow-y: auto;
  padding-right: 4px;
}

.codex-message-resizer {
  flex-shrink: 0;
  height: 12px;
  border: none;
  border-top: 1px solid var(--el-border-color-lighter);
  border-bottom: 1px solid var(--el-border-color-lighter);
  background: var(--el-fill-color-light);
  cursor: ns-resize;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: background-color 0.16s ease;
}

.codex-message-resizer:hover,
.codex-message-resizer.is-resizing {
  background: var(--el-color-primary-light-9);
}

.codex-message-resizer-indicator {
  width: 44px;
  height: 4px;
  border-top: 1px solid var(--el-border-color);
  border-bottom: 1px solid var(--el-border-color);
  border-radius: 999px;
}

.codex-message-node-card {
  display: block;
  width: 100%;
  min-width: 0;
  padding: 9px 12px;
  border: 1px solid var(--el-border-color-light);
  border-radius: 14px;
  background: var(--codex-message-surface-bg, var(--el-fill-color-light));
  color: var(--codex-surface-fg, var(--el-text-color-primary));
  text-align: left;
  cursor: pointer;
  font: inherit;
  transition: border-color 0.16s ease, background-color 0.16s ease, box-shadow 0.16s ease;
}

.codex-message-node-card:hover {
  border-color: var(--el-color-primary-light-5);
}

.codex-message-node-card.is-active {
  border-color: var(--el-color-primary);
  box-shadow: 0 0 0 1px rgba(64, 158, 255, 0.16);
}

.codex-message-node-inline {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
  width: 100%;
}

.codex-message-node-prefix {
  flex: 0 0 auto;
  color: inherit;
  font-size: 12px;
  font-weight: 600;
  white-space: nowrap;
}

.codex-message-node-inline-text {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--codex-surface-muted, var(--el-text-color-regular));
  font-size: 12px;
  line-height: 1.45;
}

.codex-message-content {
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
  padding: 12px 14px 14px;
}

.codex-pane-scroll :deep(.el-scrollbar__wrap),
.codex-message-scroll :deep(.el-scrollbar__wrap) {
  height: 100%;
  overscroll-behavior: contain;
}

.codex-message-card {
  padding: 12px 14px;
  border-radius: 18px;
  border: 1px solid var(--el-border-color-light);
  background: var(--codex-message-surface-bg, var(--el-fill-color-light));
  color: var(--codex-surface-fg, var(--el-text-color-primary));
  overflow: hidden;
}

.codex-message-process-details {
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 18px;
  background: var(--el-fill-color-blank);
  overflow: hidden;
}

.codex-message-process-summary {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 14px;
  cursor: pointer;
  font-weight: 600;
  list-style: none;
}

.codex-message-process-summary::-webkit-details-marker {
  display: none;
}

.codex-message-process-summary small {
  color: var(--el-text-color-secondary);
  font-weight: 400;
}

.codex-message-process-details[open] .codex-message-process-summary {
  border-bottom: 1px solid var(--el-border-color-lighter);
}

.codex-message-process-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 12px;
}

.codex-message-card-process {
  border-style: dashed;
}

.codex-message-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
}

.codex-message-role {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.codex-message-body {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.codex-message-text {
  margin: 0;
  font: inherit;
  font-size: 13px;
  font-weight: 400;
  line-height: 1.55;
  color: inherit;
  white-space: pre-wrap;
  word-break: break-word;
  overflow-wrap: anywhere;
}

.codex-message-image-shell {
  margin: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 84px;
  padding: 10px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 14px;
  background: rgba(255, 255, 255, 0.64);
}

.codex-message-image-shell.is-placeholder {
  justify-content: flex-start;
}

.codex-message-image {
  display: block;
  max-width: 100%;
  max-height: min(60dvh, 520px);
  border-radius: 12px;
  object-fit: contain;
  background: #fff;
}

.codex-message-image-placeholder {
  color: var(--codex-surface-subtle, var(--el-text-color-secondary));
  font-size: 12px;
  line-height: 1.45;
}

@media (max-width: 1080px) {
  .codex-page {
    height: auto;
    min-height: calc(100dvh - 60px);
  }

  .codex-toolbar-row {
    grid-template-columns: 1fr;
  }

  .codex-toolbar-actions {
    justify-content: flex-end;
  }

  .codex-workload-header {
    flex-direction: column;
    align-items: stretch;
  }

  .codex-workload-header-side {
    justify-content: space-between;
  }

  .codex-workload-stats {
    justify-content: flex-start;
  }

  .codex-workspace {
    grid-template-columns: 1fr;
    height: auto;
    overflow: visible;
  }

  .codex-pane {
    min-height: 260px;
  }

  .codex-detail-meta-grid {
    grid-template-columns: 1fr;
  }
}
</style>
