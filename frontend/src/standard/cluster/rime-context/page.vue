<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { ElMessage, ElMessageBox } from 'element-plus';
import { Delete, Minus, Plus, QuestionFilled, Refresh, Search } from '@element-plus/icons-vue';
import {
  adjustRimeContextWeightCompare,
  compareRimeContextWeights,
  deleteRimeContextCandidate,
  deleteRimeContextArticle,
  fetchRimeContextArticleContent,
  fetchRimeContextArticles,
  fetchRimeContextHistoryArticle,
  fetchRimeContextLint,
  fetchRimeContextPredictionTree,
  fetchRimeRuntimeConfig,
  importRimeContextArticle,
  refreshRimeContextPredictionTree,
  saveRimeContextArticleContent,
  saveRimeContextHistoryArticle,
  updateRimeRuntimeConfig,
  updateRimeContextCandidate,
  updateRimeContextArticle,
  type RimeContextArticle,
  type RimeContextArticleContentResponse,
  type RimeContextArticlesResponse,
  type RimeContextHistoryArticleResponse,
  type RimeContextLintIssue,
  type RimeContextLintResponse,
  type RimeContextPredictionRow,
  type RimeContextPredictionSource,
  type RimeContextPredictionTree,
  type RimeRuntimeConfigField,
  type RimeRuntimeConfigResponse,
  type RimeWeightCompareGroup,
  type RimeWeightCompareItem,
  type RimeWeightCompareResponse,
} from '@/api/rimeContextPrediction';
import { taskStore, type Device } from '@/store/taskStore';

interface PrefixGroup {
  key: string;
  rows: RimeContextPredictionRow[];
  totalWeight: number;
}

interface ContextGroup {
  key: string;
  prefixes: PrefixGroup[];
  rowCount: number;
  totalWeight: number;
}

interface ContextTailGroup {
  key: string;
  contexts: ContextGroup[];
  contextCount: number;
  rowCount: number;
  totalWeight: number;
}

type RimeView = 'index' | 'weight' | 'history' | 'articles' | 'lint' | 'config';
type IndexScope = 'summary' | 'detail';
type PrefixScope = 'summary' | 'detail';
type ArticleSourceType = 'imported_article' | 'lexicon' | 'negative_lexicon';
type ArticleContentSaveStatus = 'idle' | 'dirty' | 'saving' | 'saved' | 'error';

interface PendingArticleContentSave {
  articleId: string;
  page: number;
  pageSize: number;
  content: string;
  version: number;
}

const HISTORY_PAGE_SIZE = 2000;
const ARTICLE_CONTENT_PAGE_SIZE = 2000;
const ARTICLE_CONTENT_LAST_PAGE = 2_147_483_647;
const PREDICTION_TREE_LIMIT = 50000;
const WEIGHT_COMPARE_STEP = 1;

const indexSourceOptions: { value: RimeContextPredictionSource; label: string; title: string }[] = [
  { value: 'snapshot', label: '完整索引', title: '用于分析，包含前文片段、当前拼音和候选分布。' },
  { value: 'hot', label: '全局热索引', title: '小狼毫输入时加载的全局快速候选表。' },
  { value: 'context_hot', label: '前文热索引', title: '小狼毫输入时加载的前文加权候选表。' },
  { value: 'seed', label: '手动规则', title: '人工固定的纠偏和置顶规则。' },
];

const runtimeConfigGroups: { title: string; keys: string[] }[] = [
  {
    title: '候选生成',
    keys: [
      'max_candidates',
      'max_source_candidates',
      'min_input_length',
      'max_context',
      'prefix_completion_min_length',
      'prefix_completion_weight_ratio',
      'initials_completion_min_length',
      'initials_completion_weight_ratio',
    ],
  },
  {
    title: '输入捕捉',
    keys: [
      'enable_commit_capture',
      'commit_capture_mode',
      'flush_batch_size',
      'flush_interval_seconds',
      'max_buffer_rows',
    ],
  },
  {
    title: '实时功能',
    keys: [
      'enable_context_keys',
      'enable_realtime_learning',
      'capture_to_disk',
    ],
  },
];

const runtimeConfigHints: Record<string, string> = {
  max_candidates: '每次插入到候选栏里的智能推荐数量。',
  max_source_candidates: '参与融合排序的来源候选数量。',
  min_input_length: '拼音长度达到这个值后才启用预测。',
  max_context: '最多使用多少个已输入词作为前文。',
  prefix_completion_min_length: '拼音前缀补全的起效长度。',
  prefix_completion_weight_ratio: '拼音补全候选相对完整拼音的折扣。',
  initials_completion_min_length: '首字母缩写补全的起效长度。',
  initials_completion_weight_ratio: '首字母补全候选相对完整拼音的折扣。',
  enable_commit_capture: '是否捕捉已提交文本，用于后续周期性提炼语料。',
  commit_capture_mode: '输入捕捉的轻重模式。',
  flush_batch_size: '内存缓冲达到多少条后才允许落盘。',
  flush_interval_seconds: '距离上次落盘多久后才允许落盘。',
  max_buffer_rows: '内存里最多保留多少条待写入输入事件。',
  enable_context_keys: '是否刷新运行时上下文键。',
  enable_realtime_learning: '是否启用小狼毫侧实时学习。',
  capture_to_disk: '是否在输入期间直接写入历史文件。',
};

const runtimeModeInfo: Record<string, { rank: number; label: string; detail: string }> = {
  deferred_flush: {
    rank: 7,
    label: '完整 · 延迟批量',
    detail: '读取提交文本、规范化、进入内存缓冲，并按批量或间隔落盘。',
  },
  flush_check: {
    rank: 6,
    label: '检查 · 写盘判断',
    detail: '走到写盘判断这一步，但禁止真正落盘，用来排查写盘判断本身的开销。',
  },
  timestamp_buffer: {
    rank: 5,
    label: '缓存 · 时间缓冲',
    detail: '读取、规范化，并带时间戳写入内存缓冲，不主动触发落盘。',
  },
  memory_only: {
    rank: 4,
    label: '缓存 · 仅内存',
    detail: '读取、规范化，只暂存在内存里，不主动落盘。',
  },
  normalize_only: {
    rank: 3,
    label: '解析 · 只规范化',
    detail: '只读取并规范化提交文本，不保存。',
  },
  read_only: {
    rank: 2,
    label: '读取 · 只读',
    detail: '只读取提交文本，不解析、不保存。',
  },
  hook_only: {
    rank: 1,
    label: '挂钩 · 最轻',
    detail: '只接入提交事件，不读取文本。',
  },
};

const route = useRoute();
const router = useRouter();

const routeView = (value: unknown): RimeView => {
  if (value === 'index') return 'index';
  if (value === 'weight') return 'weight';
  if (value === 'config') return 'config';
  return 'articles';
};

const devices = computed(() => taskStore.devices);
const loadingDevices = ref(false);
const loadingTree = ref(false);
const loadingWeightCompare = ref(false);
const loadingArticles = ref(false);
const loadingArticleContent = ref(false);
const loadingHistory = ref(false);
const loadingLint = ref(false);
const loadingRuntimeConfig = ref(false);
const savingHistory = ref(false);
const savingArticleContent = ref(false);
const savingRuntimeConfig = ref(false);
const adjustingWeightKey = ref('');
const selectedEntryId = ref(
  Array.isArray(route.query.entry_id)
    ? (route.query.entry_id[0] || '')
    : ((route.query.entry_id as string) || ''),
);
const activeView = ref<RimeView>(routeView(route.query.view));
const searchText = ref('');
const selectedTailKey = ref('');
const selectedContextKey = ref('');
const selectedPrefixKey = ref('');
const selectedIndexScope = ref<IndexScope>('summary');
const selectedPrefixScope = ref<PrefixScope>('detail');
const selectedIndexSource = ref<RimeContextPredictionSource>('snapshot');
const selectedWeightSource = ref<RimeContextPredictionSource>('snapshot');
const tree = ref<RimeContextPredictionTree | null>(null);
const weightCompareState = ref<RimeWeightCompareResponse | null>(null);
const weightCompareText = ref('采用\n才用');
const articlesState = ref<RimeContextArticlesResponse | null>(null);
const articleContentState = ref<RimeContextArticleContentResponse | null>(null);
const historyState = ref<RimeContextHistoryArticleResponse | null>(null);
const lintState = ref<RimeContextLintResponse | null>(null);
const runtimeConfigState = ref<RimeRuntimeConfigResponse | null>(null);
const runtimeConfigDraft = ref<Record<string, any>>({});
const lintSource = ref<'all' | 'history' | 'articles'>('all');
const lintMode = ref<'rules' | 'ai'>('rules');
const lintLoadedSource = ref<'all' | 'history' | 'articles'>('all');
const lintLoadedMode = ref<'rules' | 'ai'>('rules');
const historyPage = ref(1);
const articleContentPage = ref(1);
const selectedArticleId = ref('');
const historyEditing = ref(false);
const historyDraft = ref('');
const articleContentDraft = ref('');
const articleContentSaveStatus = ref<ArticleContentSaveStatus>('idle');
const articleContentSaveError = ref('');
const articleDialogVisible = ref(false);
const candidateDialogVisible = ref(false);
const submittingArticle = ref(false);
const submittingCandidate = ref(false);
const updatingArticleId = ref('');
const deletingCandidateKey = ref('');
const articleForm = ref({
  title: '',
  content: '',
  enabled: true,
  sourceType: 'imported_article' as ArticleSourceType,
  weightMultiplier: 8,
});
const candidateForm = ref({
  originalContext: '',
  originalPrefix: '',
  originalCandidate: '',
  candidate: '',
  weight: 1,
});

let articleContentSaveTimer: ReturnType<typeof window.setTimeout> | null = null;
let pendingArticleContentSave: PendingArticleContentSave | null = null;
let articleContentDraftVersion = 0;

const currentDevice = computed(() => devices.value.find((device) => device.id === selectedEntryId.value) || null);
const hasDevices = computed(() => devices.value.length > 0);
const normalizedSearch = computed(() => searchText.value.trim().toLowerCase());
const articleDialogTitle = computed(() => {
  if (articleForm.value.sourceType === 'negative_lexicon') return '导入负向短语';
  if (articleForm.value.sourceType === 'lexicon') return '导入自定义短语';
  return '导入语料';
});
const articleContentLabel = computed(() => (articleForm.value.sourceType === 'imported_article' ? '正文' : '短语'));
const articleContentPlaceholder = computed(() => (
  articleForm.value.sourceType === 'negative_lexicon'
    ? '每行一个需要降权的短语；可写 涉及 或 涉及\tsheji\t8'
    : articleForm.value.sourceType === 'lexicon'
    ? '每行一个短语；可写 冠豸(zhai)山；需要指定编码或权重时用 Tab 分隔：候选短语\t编码\t权重'
    : '粘贴要提炼的语料正文'
));
const historySummary = computed(() => historyState.value?.summary || unavailableHistoryArticle('').summary);
const lintSummary = computed(() => lintState.value?.summary || unavailableLint('').summary);
const historyPagination = computed(() => historyState.value?.pagination || null);
const articles = computed(() => articlesState.value?.articles || []);
const selectedArticle = computed(() => (
  articles.value.find((article) => article.id === selectedArticleId.value) || null
));
const articleContentPagination = computed(() => articleContentState.value?.pagination || null);
const articleContentSaveLabel = computed(() => {
  if (!selectedArticle.value || isReadonlyArticle(selectedArticle.value)) return '';
  if (articleContentSaveStatus.value === 'dirty') return '待保存';
  if (articleContentSaveStatus.value === 'saving') return '保存中';
  if (articleContentSaveStatus.value === 'saved') return '已保存';
  if (articleContentSaveStatus.value === 'error') return articleContentSaveError.value || '保存失败';
  return '';
});
const lintIssues = computed(() => lintState.value?.issues || []);
const lintIsStale = computed(() => Boolean(
  lintState.value
  && (lintLoadedSource.value !== lintSource.value || lintLoadedMode.value !== lintMode.value),
));
const canImportArticle = computed(() => Boolean(selectedEntryId.value && articlesState.value?.available));
const historyHasDraftChanges = computed(() => historyDraft.value !== (historyState.value?.content || ''));
const currentIndexSource = computed(() => (
  indexSourceOptions.find((item) => item.value === selectedIndexSource.value) || indexSourceOptions[0]
));
const currentWeightSource = computed(() => (
  indexSourceOptions.find((item) => item.value === selectedWeightSource.value) || indexSourceOptions[0]
));
const weightCompareCandidates = computed(() => {
  const seen = new Set<string>();
  return weightCompareText.value
    .split(/[\s,，、;；]+/)
    .map((item) => item.trim())
    .filter((item) => {
      if (!item || seen.has(item)) return false;
      seen.add(item);
      return true;
    });
});
const weightCompareSummary = computed(() => weightCompareState.value?.summary || {
  candidate_count: 0,
  matched_count: 0,
  row_count: 0,
});
const runtimeConfigDirty = computed(() => (
  JSON.stringify(runtimeConfigDraft.value) !== JSON.stringify(runtimeConfigState.value?.config || {})
));
const visibleRuntimeConfigGroups = computed(() => (
  runtimeConfigGroups
    .map((group) => ({
      ...group,
      keys: group.keys.filter((key) => Boolean(runtimeConfigState.value?.fields?.[key])),
    }))
    .filter((group) => group.keys.length)
));

function isGlobalContext(value: string) {
  return value === '__global';
}

function displayContext(value: string) {
  return isGlobalContext(value) ? '[起始]' : value;
}

function displayContextTitle(value: string) {
  return isGlobalContext(value)
    ? '[起始]：没有前文时只按当前拼音统计候选，作为新起一段的兜底。'
    : `前文片段：${value}`;
}

function contextTailKey(value: string) {
  if (isGlobalContext(value)) return '__global';
  const tokens = (value || '').trim().split(/\s+/).filter(Boolean);
  return tokens[tokens.length - 1] || value || '__empty';
}

function displayContextTail(value: string) {
  if (value === '__global') return '[起始]';
  if (value === '__empty') return '[空]';
  return value;
}

function displayContextTailTitle(group: ContextTailGroup) {
  return `末词：${displayContextTail(group.key)}，${formatNumber(group.contextCount)} 个前文片段，${formatNumber(group.rowCount)} 条记录`;
}

function compareAlphabetical(left: string, right: string) {
  return left.localeCompare(right, 'en-US');
}

function comparePrefixGroups(left: PrefixGroup, right: PrefixGroup) {
  return right.totalWeight - left.totalWeight || compareAlphabetical(left.key, right.key);
}

const unavailableTree = (message: string, status = 'request_failed'): RimeContextPredictionTree => ({
  available: false,
  status,
  message,
  rime_dir: null,
  source_kind: 'snapshot',
  source: null,
  source_path: null,
  updated_at: null,
  files: [],
  summary: {
    row_count: 0,
    context_count: 0,
    prefix_count: 0,
    candidate_count: 0,
  },
  rows: [],
});

const unavailableArticles = (message: string, status = 'request_failed'): RimeContextArticlesResponse => ({
  available: false,
  status,
  message,
  rime_dir: null,
  files: [],
  summary: {
    article_count: 0,
    enabled_count: 0,
    lexicon_count: 0,
    negative_lexicon_count: 0,
    contribution_count: 0,
  },
  articles: [],
});

const unavailableHistoryArticle = (message: string, status = 'request_failed'): RimeContextHistoryArticleResponse => ({
  available: false,
  status,
  message,
  rime_dir: null,
  source: null,
  source_path: null,
  updated_at: null,
  files: [],
  summary: {
    entry_count: 0,
    char_count: 0,
    paragraph_count: 0,
    first_seen: '',
    last_seen: '',
    pending_row_count: 0,
    model_count_row_count: 0,
    truncated: false,
    limit: 0,
    edited: false,
    saved_at: 0,
    base_event_count: 0,
    appended_event_count: 0,
  },
  pagination: null,
  content: '',
});

const unavailableArticleContent = (message: string, status = 'request_failed'): RimeContextArticleContentResponse => ({
  available: false,
  status,
  message,
  rime_dir: null,
  files: [],
  article: null,
  pagination: null,
  content: '',
});

const unavailableLint = (message: string, status = 'request_failed'): RimeContextLintResponse => ({
  available: false,
  status,
  message,
  rime_dir: null,
  files: [],
  summary: {
    source_count: 0,
    issue_count: 0,
    high_count: 0,
    medium_count: 0,
    low_count: 0,
    rule_count: 0,
    ai_count: 0,
  },
  issues: [],
});

const unavailableRuntimeConfig = (message: string, status = 'request_failed'): RimeRuntimeConfigResponse => ({
  available: false,
  status,
  message,
  rime_dir: null,
  source: null,
  source_path: null,
  updated_at: null,
  files: [],
  config: {},
  fields: {},
  missing_keys: [],
  requires_reload: false,
});

const unavailableWeightCompare = (message: string, status = 'request_failed'): RimeWeightCompareResponse => ({
  available: false,
  status,
  message,
  rime_dir: null,
  source_kind: 'snapshot',
  source: null,
  source_path: null,
  updated_at: null,
  files: [],
  summary: {
    candidate_count: 0,
    matched_count: 0,
    row_count: 0,
  },
  items: [],
});

const groupedContexts = computed<ContextGroup[]>(() => {
  const contextMap = new Map<string, Map<string, RimeContextPredictionRow[]>>();
  for (const row of tree.value?.rows || []) {
    const context = row.context || '__global';
    const prefix = row.prefix || '';
    if (!contextMap.has(context)) {
      contextMap.set(context, new Map());
    }
    const prefixMap = contextMap.get(context)!;
    if (!prefixMap.has(prefix)) {
      prefixMap.set(prefix, []);
    }
    prefixMap.get(prefix)!.push(row);
  }

  return Array.from(contextMap.entries()).map(([context, prefixMap]) => {
    const prefixes = Array.from(prefixMap.entries()).map(([prefix, rows]) => {
      const sortedRows = rows.slice().sort((left, right) => right.weight - left.weight || left.candidate.localeCompare(right.candidate, 'zh-CN'));
      return {
        key: prefix,
        rows: sortedRows,
        totalWeight: sortedRows.reduce((sum, row) => sum + Number(row.weight || 0), 0),
      };
    }).sort(comparePrefixGroups);

    return {
      key: context,
      prefixes,
      rowCount: prefixes.reduce((sum, item) => sum + item.rows.length, 0),
      totalWeight: prefixes.reduce((sum, item) => sum + item.totalWeight, 0),
    };
  }).sort((left, right) => right.rowCount - left.rowCount || left.key.localeCompare(right.key, 'zh-CN'));
});

const filteredContexts = computed(() => {
  const keyword = normalizedSearch.value;
  if (!keyword) return groupedContexts.value;
  return groupedContexts.value
    .map((context) => {
      const contextMatched = `${context.key} ${displayContext(context.key)}`.toLowerCase().includes(keyword);
      const prefixes = context.prefixes
        .map((prefix) => {
          const prefixMatched = prefix.key.toLowerCase().includes(keyword);
          const rows = prefix.rows.filter((row) => (
            contextMatched
            || prefixMatched
            || row.candidate.toLowerCase().includes(keyword)
          ));
          return rows.length || prefixMatched || contextMatched
            ? { ...prefix, rows, totalWeight: rows.reduce((sum, row) => sum + Number(row.weight || 0), 0) }
            : null;
        })
        .filter((item): item is PrefixGroup => Boolean(item))
        .sort(comparePrefixGroups);
      return prefixes.length
        ? {
          ...context,
          prefixes,
          rowCount: prefixes.reduce((sum, item) => sum + item.rows.length, 0),
          totalWeight: prefixes.reduce((sum, item) => sum + item.totalWeight, 0),
        }
        : null;
    })
    .filter((item): item is ContextGroup => Boolean(item));
});

const contextTailGroups = computed<ContextTailGroup[]>(() => {
  const groupMap = new Map<string, ContextGroup[]>();
  for (const context of filteredContexts.value) {
    const key = contextTailKey(context.key);
    if (!groupMap.has(key)) {
      groupMap.set(key, []);
    }
    groupMap.get(key)!.push(context);
  }

  const groups = Array.from(groupMap.entries()).map(([key, contexts]) => {
    const sortedContexts = contexts.slice().sort((left, right) => (
      right.rowCount - left.rowCount || left.key.localeCompare(right.key, 'zh-CN')
    ));
    return {
      key,
      contexts: sortedContexts,
      contextCount: sortedContexts.length,
      rowCount: sortedContexts.reduce((sum, item) => sum + item.rowCount, 0),
      totalWeight: sortedContexts.reduce((sum, item) => sum + item.totalWeight, 0),
    };
  }).sort((left, right) => right.rowCount - left.rowCount || left.key.localeCompare(right.key, 'zh-CN'));
  return groups;
});

const selectedTailGroup = computed(() => (
  contextTailGroups.value.find((item) => item.key === selectedTailKey.value)
  || contextTailGroups.value[0]
  || null
));

const selectedContext = computed(() => (
  selectedTailGroup.value?.contexts.find((item) => item.key === selectedContextKey.value)
  || selectedTailGroup.value?.contexts[0]
  || null
));

const aggregatePrefixGroups = (contexts: ContextGroup[]): PrefixGroup[] => {
  const prefixMap = new Map<string, Map<string, RimeContextPredictionRow>>();
  for (const context of contexts) {
    for (const prefix of context.prefixes) {
      if (!prefixMap.has(prefix.key)) {
        prefixMap.set(prefix.key, new Map());
      }
      const candidateMap = prefixMap.get(prefix.key)!;
      for (const row of prefix.rows) {
        const existing = candidateMap.get(row.candidate);
        if (existing) {
          existing.weight += Number(row.weight || 0);
        } else {
          candidateMap.set(row.candidate, {
            context: selectedTailGroup.value?.key || '',
            prefix: prefix.key,
            candidate: row.candidate,
            weight: Number(row.weight || 0),
            comment: '',
          });
        }
      }
    }
  }

  return Array.from(prefixMap.entries()).map(([prefix, rowsByCandidate]) => {
    const rows = Array.from(rowsByCandidate.values()).sort((left, right) => (
      Number(right.weight || 0) - Number(left.weight || 0)
      || left.candidate.localeCompare(right.candidate, 'zh-CN')
    ));
    return {
      key: prefix,
      rows,
      totalWeight: rows.reduce((sum, row) => sum + Number(row.weight || 0), 0),
    };
  }).sort(comparePrefixGroups);
};

const summaryPrefixGroups = computed(() => aggregatePrefixGroups(selectedTailGroup.value?.contexts || []));

const activePrefixGroups = computed(() => (
  selectedIndexScope.value === 'summary'
    ? summaryPrefixGroups.value
    : (selectedContext.value?.prefixes || [])
));

const selectedPrefix = computed(() => {
  const prefixes = activePrefixGroups.value;
  return prefixes.find((item) => item.key === selectedPrefixKey.value) || prefixes[0] || null;
});

const prefixSummaryRows = computed(() => {
  const candidateMap = new Map<string, RimeContextPredictionRow>();
  for (const prefix of activePrefixGroups.value) {
    for (const row of prefix.rows) {
      const existing = candidateMap.get(row.candidate);
      if (existing) {
        existing.weight += Number(row.weight || 0);
      } else {
        candidateMap.set(row.candidate, {
          ...row,
          prefix: '',
          weight: Number(row.weight || 0),
          comment: '',
        });
      }
    }
  }
  return Array.from(candidateMap.values()).sort((left, right) => (
  Number(right.weight || 0) - Number(left.weight || 0)
  || left.candidate.localeCompare(right.candidate, 'zh-CN')
  ));
});

const selectedRows = computed(() => (
  selectedPrefixScope.value === 'summary'
    ? prefixSummaryRows.value
    : (selectedPrefix.value?.rows || [])
));
const canEditCandidateRows = computed(() => selectedIndexScope.value === 'detail' && selectedPrefixScope.value === 'detail');
const summary = computed(() => tree.value?.summary || unavailableTree('').summary);
const visibleFiles = computed(() => tree.value?.files || []);

const statusType = computed(() => {
  if (!tree.value) return 'info';
  if (tree.value.status === 'ready') return 'success';
  if (tree.value.status === 'empty') return 'warning';
  return 'info';
});

const articleStatusType = computed(() => {
  if (!articlesState.value) return 'info';
  if (articlesState.value.status === 'ready') return 'success';
  return 'info';
});

const historyStatusType = computed(() => {
  if (!historyState.value) return 'info';
  if (historyState.value.status === 'ready') return 'success';
  if (historyState.value.status === 'empty') return 'warning';
  return 'info';
});

const lintStatusType = computed(() => {
  if (!lintState.value) return 'info';
  if (lintState.value.status === 'ready') return lintSummary.value.issue_count ? 'warning' : 'success';
  if (lintState.value.status === 'empty') return 'warning';
  return 'info';
});

const statusText = computed(() => {
  const status = tree.value?.status || '';
  const labels: Record<string, string> = {
    ready: '可用',
    empty: '暂无数据',
    rime_missing: '未安装',
    extension_missing: '未扩展',
    remote_unsupported: '旧版设备',
    remote_unreachable: '不可达',
    remote_error: '远端异常',
    read_error: '读取失败',
    request_failed: '请求失败',
    unsupported_platform: '不支持',
  };
  return labels[status] || '未知';
});

const articleStatusText = computed(() => {
  const status = articlesState.value?.status || '';
  const labels: Record<string, string> = {
    ready: '可用',
    rime_missing: '未安装',
    remote_unsupported: '旧版设备',
    remote_unreachable: '不可达',
    remote_error: '远端异常',
    request_failed: '请求失败',
    unsupported_platform: '不支持',
  };
  return labels[status] || '未知';
});

const historyStatusText = computed(() => {
  const status = historyState.value?.status || '';
  const labels: Record<string, string> = {
    ready: '可用',
    empty: '暂无数据',
    history_missing: '无日志',
    rime_missing: '未安装',
    remote_unsupported: '旧版设备',
    remote_unreachable: '不可达',
    remote_error: '远端异常',
    read_error: '读取失败',
    request_failed: '请求失败',
    unsupported_platform: '不支持',
  };
  return labels[status] || '未知';
});

const lintStatusText = computed(() => {
  const status = lintState.value?.status || '';
  const labels: Record<string, string> = {
    ready: lintSummary.value.issue_count ? '有建议' : '未发现问题',
    empty: '暂无语料',
    rime_missing: '未安装',
    remote_unsupported: '旧版设备',
    remote_unreachable: '不可达',
    remote_error: '远端异常',
    read_error: '读取失败',
    request_failed: '请求失败',
    unsupported_platform: '不支持',
  };
  return labels[status] || '未知';
});

const currentStatusType = computed(() => {
  if (activeView.value === 'lint') return lintStatusType.value;
  if (activeView.value === 'articles') return articleStatusType.value;
  if (activeView.value === 'history') return historyStatusType.value;
  return statusType.value;
});
const currentStatusText = computed(() => {
  if (activeView.value === 'lint') return lintStatusText.value;
  if (activeView.value === 'articles') return articleStatusText.value;
  if (activeView.value === 'history') return historyStatusText.value;
  return statusText.value;
});

const deviceMeta = (device: Device | null) => {
  if (!device) return '';
  if (device.mode === 'local') return '本地设备';
  if (!device.server_url) return '远程设备';
  try {
    return `远程 · ${new URL(device.server_url).host}`;
  } catch {
    return `远程 · ${device.server_url.replace(/^https?:\/\//, '')}`;
  }
};

function formatNumber(value: number | null | undefined) {
  const numberValue = Number(value || 0);
  const normalized = Object.is(numberValue, -0) ? 0 : numberValue;
  return formatSignificantUnitNumber(normalized);
}

function formatSignificantUnitNumber(value: number, significantDigits = 4) {
  if (!Number.isFinite(value)) return '0';
  const normalized = Object.is(value, -0) ? 0 : value;
  const sign = normalized < 0 ? '-' : '';
  const absValue = Math.abs(normalized);
  if (absValue >= 100000000) {
    return `${sign}${formatSignificantDigits(absValue / 100000000, significantDigits)}亿`;
  }
  if (absValue >= 10000) {
    return `${sign}${formatSignificantDigits(absValue / 10000, significantDigits)}万`;
  }
  return `${sign}${formatSignificantDigits(absValue, significantDigits, false)}`;
}

function formatSignificantDigits(value: number, significantDigits: number, useGrouping = true) {
  if (!Number.isFinite(value) || value === 0) return '0';
  const decimalDigits = Math.max(0, significantDigits - Math.floor(Math.log10(Math.abs(value))) - 1);
  return value.toLocaleString(undefined, {
    useGrouping,
    minimumFractionDigits: 0,
    maximumFractionDigits: decimalDigits,
  });
}

const formatBytes = (value: number | null | undefined) => {
  const size = Number(value || 0);
  if (!size) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB'];
  let current = size;
  let unitIndex = 0;
  while (current >= 1024 && unitIndex < units.length - 1) {
    current /= 1024;
    unitIndex += 1;
  }
  return `${current >= 10 || unitIndex === 0 ? current.toFixed(0) : current.toFixed(1)} ${units[unitIndex]}`;
};

const formatDateTime = (value: number | null | undefined) => {
  if (!value) return '无';
  return new Date(value * 1000).toLocaleString('zh-CN', { hour12: false });
};

const formatWeightCompareGroups = (
  groups: RimeWeightCompareGroup[],
  labeler: (value: string) => string = (value) => value,
  limit = 3,
) => {
  if (!groups.length) return '-';
  return groups
    .slice(0, limit)
    .map((group) => `${labeler(group.key)} ${formatNumber(group.weight)}`)
    .join(' / ');
};

const formatHistoryRange = (firstSeen: string, lastSeen: string) => {
  if (!firstSeen && !lastSeen) return '无';
  if (firstSeen && lastSeen && firstSeen !== lastSeen) return `${firstSeen} 至 ${lastSeen}`;
  return firstSeen || lastSeen;
};

const candidateRowKey = (row: RimeContextPredictionRow) => `${row.context}\t${row.prefix}\t${row.candidate}`;
const weightCompareItemKey = (item: RimeWeightCompareItem) => `${item.default_pinyin || item.pinyin}\t${item.candidate}`;
const articleHashShort = (value: string) => (value ? value.slice(0, 10) : '');
const stripInputHistoryPrefix = (value: string) => value.replace(/^输入历史\s*[·:：-]\s*/, '').trim();
const articleSourceTypeLabel = (sourceType: string) => {
  if (sourceType === 'negative_lexicon' || sourceType === 'negative_terms') return '负向短语';
  if (sourceType === 'lexicon' || sourceType === 'manual_english_terms') return '自定义短语';
  if (sourceType === 'device_history' || sourceType === 'input_history') return '输入历史';
  return '文章';
};
const articleKindLabel = (article: RimeContextArticle) => articleSourceTypeLabel(article.source_type);
const defaultArticleTitle = (sourceType: ArticleSourceType) => (
  sourceType === 'imported_article' ? undefined : articleSourceTypeLabel(sourceType)
);
const articleDisplayName = (article: RimeContextArticle) => {
  if (article.source_type === 'device_history' || article.source_type === 'input_history') {
    return stripInputHistoryPrefix(article.source_label || article.title) || stripInputHistoryPrefix(article.title) || '本机';
  }
  return article.title;
};
const articleTitleTooltip = (article: RimeContextArticle) => (
  [
    article.title,
    article.source_label && article.source_label !== article.title ? article.source_label : '',
    article.source_key,
    articleHashShort(article.content_hash),
  ].filter(Boolean).join('\n')
);
const isReadonlyArticle = (article: RimeContextArticle) => Boolean(article.readonly || article.source_type === 'input_history');
const lintSourceLabel = (value: string) => {
  if (value === 'history') return '输入历史';
  if (value === 'article') return '导入文章';
  return value || '语料';
};
const lintSeverityLabel = (value: string) => {
  if (value === 'high') return '高';
  if (value === 'medium') return '中';
  if (value === 'low') return '低';
  return value || '-';
};
const lintSeverityType = (value: string) => {
  if (value === 'high') return 'danger';
  if (value === 'medium') return 'warning';
  if (value === 'low') return 'info';
  return 'info';
};
const lintConfidenceText = (value: number) => `${Math.round(Number(value || 0) * 100)}%`;
const runtimeConfigLabel = (key: string, field: RimeRuntimeConfigField) => field.label || key;
const runtimeModeDisplay = (value: string) => {
  const info = runtimeModeInfo[value];
  return info ? `${info.rank} ${info.label}` : value;
};
const runtimeModeDetail = (value: string) => runtimeModeInfo[value]?.detail || '';
const runtimeConfigHint = (key: string) => {
  if (key === 'commit_capture_mode') {
    const value = String(runtimeConfigDraft.value.commit_capture_mode || '');
    const info = runtimeModeInfo[value];
    return info ? `强度 ${info.rank}/7：${info.detail}` : '从 7 到 1 逐级减轻捕捉链路。';
  }
  return runtimeConfigHints[key] || key;
};
const runtimeConfigStep = (field: RimeRuntimeConfigField) => (field.type === 'float' ? 0.05 : 1);
const runtimeConfigMin = (field: RimeRuntimeConfigField) => (
  typeof field.min === 'number' ? field.min : undefined
);
const runtimeConfigMax = (field: RimeRuntimeConfigField) => (
  typeof field.max === 'number' ? field.max : undefined
);
const runtimeConfigEnumOptions = (key: string, field: RimeRuntimeConfigField) => {
  const values = [...(field.values || [])];
  if (key !== 'commit_capture_mode') return values;
  return values.sort((left, right) => (
    (runtimeModeInfo[right]?.rank || 0) - (runtimeModeInfo[left]?.rank || 0)
  ));
};
const runtimeConfigOptionLabel = (value: string) => runtimeModeDisplay(value);

function syncRuntimeConfigDraft() {
  runtimeConfigDraft.value = { ...(runtimeConfigState.value?.config || {}) };
}

const loadDevices = async () => {
  loadingDevices.value = true;
  try {
    await taskStore.fetchDevices();
    if (!selectedEntryId.value && devices.value.length) {
      selectedEntryId.value = devices.value.find((device) => device.mode === 'local')?.id || devices.value[0].id;
    }
  } finally {
    loadingDevices.value = false;
  }
};

const syncRouteQuery = () => {
  router.replace({
    query: {
      ...route.query,
      ...(selectedEntryId.value ? { entry_id: selectedEntryId.value } : {}),
      ...(activeView.value === 'articles' ? { view: undefined } : { view: activeView.value }),
    },
  });
};

const loadTree = async () => {
  if (!selectedEntryId.value) {
    tree.value = null;
    return;
  }
  loadingTree.value = true;
  try {
    tree.value = await fetchRimeContextPredictionTree(selectedEntryId.value, {
      source: selectedIndexSource.value,
      limit: PREDICTION_TREE_LIMIT,
    });
  } catch (err: any) {
    tree.value = unavailableTree(err.response?.data?.detail || err.message || '读取小狼毫输入法索引失败。');
  } finally {
    loadingTree.value = false;
  }
};

const loadWeightCompare = async (options: { silent?: boolean } = {}) => {
  if (!selectedEntryId.value) {
    weightCompareState.value = null;
    return;
  }
  const candidates = weightCompareCandidates.value;
  if (!candidates.length) {
    weightCompareState.value = unavailableWeightCompare('请输入要对比的候选词。', 'empty_input');
    return;
  }
  if (!options.silent) {
    loadingWeightCompare.value = true;
  }
  try {
    weightCompareState.value = await compareRimeContextWeights(selectedEntryId.value, {
      candidates,
      source: selectedWeightSource.value,
      limit: 20,
    });
  } catch (err: any) {
    weightCompareState.value = unavailableWeightCompare(
      err.response?.data?.detail || err.message || '读取候选词权重失败。',
    );
  } finally {
    if (!options.silent) {
      loadingWeightCompare.value = false;
    }
  }
};

const resetArticleContent = () => {
  clearArticleContentSaveTimer();
  pendingArticleContentSave = null;
  selectedArticleId.value = '';
  articleContentPage.value = 1;
  articleContentState.value = null;
  articleContentDraft.value = '';
  articleContentSaveStatus.value = 'idle';
  articleContentSaveError.value = '';
};

function clearArticleContentSaveTimer() {
  if (!articleContentSaveTimer) return;
  window.clearTimeout(articleContentSaveTimer);
  articleContentSaveTimer = null;
}

function syncArticleContentDraft(content: string) {
  clearArticleContentSaveTimer();
  pendingArticleContentSave = null;
  articleContentDraftVersion += 1;
  articleContentDraft.value = content;
  articleContentSaveStatus.value = 'idle';
  articleContentSaveError.value = '';
}

function replaceArticleInList(article: RimeContextArticle | null | undefined) {
  if (!article || !articlesState.value) return;
  articlesState.value = {
    ...articlesState.value,
    articles: articlesState.value.articles.map((item) => (item.id === article.id ? article : item)),
  };
}

const saveArticleContentNow = async () => {
  clearArticleContentSaveTimer();
  const pending = pendingArticleContentSave;
  if (!pending || !selectedEntryId.value) return;
  pendingArticleContentSave = null;
  savingArticleContent.value = true;
  articleContentSaveStatus.value = 'saving';
  articleContentSaveError.value = '';
  try {
    const payload = await saveRimeContextArticleContent(selectedEntryId.value, pending.articleId, {
      content: pending.content,
      page: pending.page,
      page_size: pending.pageSize,
    });
    replaceArticleInList(payload.article);
    if (
      selectedArticleId.value === pending.articleId
      && articleContentPage.value === pending.page
      && articleContentDraftVersion === pending.version
    ) {
      articleContentState.value = payload;
      articleContentPage.value = payload.pagination?.page || pending.page;
      if (articleContentDraft.value !== (payload.content || '')) {
        articleContentDraft.value = payload.content || '';
      }
      articleContentSaveStatus.value = 'saved';
    }
  } catch (err: any) {
    if (articleContentDraftVersion === pending.version) {
      articleContentSaveError.value = err.response?.data?.detail || err.message || '保存失败';
      articleContentSaveStatus.value = 'error';
    }
  } finally {
    savingArticleContent.value = false;
  }
};

const scheduleArticleContentSave = () => {
  const article = selectedArticle.value;
  const pagination = articleContentPagination.value;
  if (!selectedEntryId.value || !article || isReadonlyArticle(article) || !articleContentState.value?.available) return;
  articleContentDraftVersion += 1;
  pendingArticleContentSave = {
    articleId: article.id,
    page: pagination?.page || articleContentPage.value || 1,
    pageSize: pagination?.page_size || ARTICLE_CONTENT_PAGE_SIZE,
    content: articleContentDraft.value,
    version: articleContentDraftVersion,
  };
  articleContentSaveStatus.value = 'dirty';
  articleContentSaveError.value = '';
  clearArticleContentSaveTimer();
  articleContentSaveTimer = window.setTimeout(() => {
    void saveArticleContentNow();
  }, 900);
};

const loadArticleContent = async (page = articleContentPage.value) => {
  if (!selectedEntryId.value || !selectedArticleId.value) {
    articleContentState.value = null;
    syncArticleContentDraft('');
    return;
  }
  loadingArticleContent.value = true;
  try {
    articleContentState.value = await fetchRimeContextArticleContent(
      selectedEntryId.value,
      selectedArticleId.value,
      {
        page,
        page_size: ARTICLE_CONTENT_PAGE_SIZE,
      },
    );
    articleContentPage.value = articleContentState.value.pagination?.page || page;
    syncArticleContentDraft(articleContentState.value.content || '');
  } catch (err: any) {
    articleContentState.value = unavailableArticleContent(
      err.response?.data?.detail || err.message || '读取语料内容失败。',
    );
    syncArticleContentDraft('');
  } finally {
    loadingArticleContent.value = false;
  }
};

const loadArticles = async () => {
  if (!selectedEntryId.value) {
    articlesState.value = null;
    resetArticleContent();
    return;
  }
  loadingArticles.value = true;
  try {
    articlesState.value = await fetchRimeContextArticles(selectedEntryId.value);
    if (selectedArticleId.value) {
      if (articles.value.some((article) => article.id === selectedArticleId.value)) {
        await loadArticleContent(articleContentPage.value);
      } else {
        resetArticleContent();
      }
    }
  } catch (err: any) {
    articlesState.value = unavailableArticles(err.response?.data?.detail || err.message || '读取语料库失败。');
    resetArticleContent();
  } finally {
    loadingArticles.value = false;
  }
};

const loadHistoryArticle = async (page = historyPage.value) => {
  if (!selectedEntryId.value) {
    historyState.value = null;
    return;
  }
  loadingHistory.value = true;
  try {
    historyState.value = await fetchRimeContextHistoryArticle(selectedEntryId.value, {
      page,
      page_size: HISTORY_PAGE_SIZE,
    });
    historyPage.value = historyState.value.pagination?.page || page;
    if (!historyEditing.value) {
      historyDraft.value = historyState.value.content || '';
    }
  } catch (err: any) {
    historyState.value = unavailableHistoryArticle(err.response?.data?.detail || err.message || '读取输入历史失败。');
    if (!historyEditing.value) {
      historyDraft.value = '';
    }
  } finally {
    loadingHistory.value = false;
  }
};

const loadLint = async () => {
  if (!selectedEntryId.value) {
    lintState.value = null;
    return;
  }
  loadingLint.value = true;
  try {
    lintState.value = await fetchRimeContextLint(selectedEntryId.value, {
      source: lintSource.value,
      mode: lintMode.value,
      limit: 200,
    });
    lintLoadedSource.value = lintSource.value;
    lintLoadedMode.value = lintMode.value;
  } catch (err: any) {
    lintState.value = unavailableLint(err.response?.data?.detail || err.message || '语料检查失败。');
  } finally {
    loadingLint.value = false;
  }
};

const loadRuntimeConfig = async () => {
  if (!selectedEntryId.value) {
    runtimeConfigState.value = null;
    runtimeConfigDraft.value = {};
    return;
  }
  loadingRuntimeConfig.value = true;
  try {
    runtimeConfigState.value = await fetchRimeRuntimeConfig(selectedEntryId.value);
    syncRuntimeConfigDraft();
  } catch (err: any) {
    runtimeConfigState.value = unavailableRuntimeConfig(
      err.response?.data?.detail || err.message || '读取运行配置失败。',
    );
    runtimeConfigDraft.value = {};
  } finally {
    loadingRuntimeConfig.value = false;
  }
};

const resetRuntimeConfigDraft = () => {
  syncRuntimeConfigDraft();
};

const saveRuntimeConfig = async () => {
  if (!selectedEntryId.value || savingRuntimeConfig.value || !runtimeConfigState.value?.available) return;
  savingRuntimeConfig.value = true;
  try {
    runtimeConfigState.value = await updateRimeRuntimeConfig(selectedEntryId.value, {
      config: runtimeConfigDraft.value,
    });
    syncRuntimeConfigDraft();
    if (runtimeConfigState.value.deploy && !runtimeConfigState.value.deploy.ok) {
      ElMessage.warning(runtimeConfigState.value.message || '运行配置已保存，但自动重新部署失败');
    } else {
      ElMessage.success(runtimeConfigState.value.message || '运行配置已保存并已重新部署');
    }
  } catch (err: any) {
    ElMessage.error(err.response?.data?.detail || err.message || '保存运行配置失败');
  } finally {
    savingRuntimeConfig.value = false;
  }
};

const changeHistoryPage = async (page: number) => {
  if (loadingHistory.value || historyEditing.value) return;
  await loadHistoryArticle(Math.max(1, page));
};

const selectArticle = async (article: RimeContextArticle) => {
  if (selectedArticleId.value === article.id && articleContentState.value) return;
  await saveArticleContentNow();
  selectedArticleId.value = article.id;
  articleContentPage.value = ARTICLE_CONTENT_LAST_PAGE;
  await loadArticleContent(ARTICLE_CONTENT_LAST_PAGE);
};

const changeArticleContentPage = async (page: number) => {
  if (loadingArticleContent.value) return;
  await saveArticleContentNow();
  await loadArticleContent(Math.max(1, page));
};

const loadActiveView = async () => {
  if (activeView.value === 'lint') {
    await loadLint();
    return;
  }
  if (activeView.value === 'config') {
    await loadRuntimeConfig();
    return;
  }
  if (activeView.value === 'weight') {
    await loadWeightCompare();
    return;
  }
  if (activeView.value === 'articles') {
    await loadArticles();
    return;
  }
  if (activeView.value === 'history') {
    await loadHistoryArticle();
    return;
  }
  await loadTree();
};

const handleDeviceChange = async () => {
  historyEditing.value = false;
  historyPage.value = 1;
  historyDraft.value = '';
  runtimeConfigState.value = null;
  runtimeConfigDraft.value = {};
  weightCompareState.value = null;
  resetArticleContent();
  selectedTailKey.value = '';
  selectedContextKey.value = '';
  selectedPrefixKey.value = '';
  syncRouteQuery();
  await loadActiveView();
};

const handleIndexSourceChange = async () => {
  selectedTailKey.value = '';
  selectedContextKey.value = '';
  selectedPrefixKey.value = '';
  await loadTree();
};

const handleRefresh = async () => {
  if (activeView.value === 'lint') {
    await loadLint();
    return;
  }
  if (activeView.value === 'config') {
    await loadRuntimeConfig();
    return;
  }
  if (activeView.value === 'weight') {
    await loadWeightCompare();
    return;
  }
  if (activeView.value === 'articles') {
    await loadArticles();
    return;
  }
  if (activeView.value === 'history') {
    await loadHistoryArticle();
    return;
  }
  if (!selectedEntryId.value) return;
  loadingTree.value = true;
  try {
    tree.value = await refreshRimeContextPredictionTree(selectedEntryId.value, {
      source: selectedIndexSource.value,
      limit: PREDICTION_TREE_LIMIT,
    });
    ElMessage.success(tree.value.message || '预测索引已更新');
  } catch (err: any) {
    ElMessage.error(err.response?.data?.detail || err.message || '更新预测索引失败');
  } finally {
    loadingTree.value = false;
  }
};

const startHistoryEdit = async () => {
  if (!selectedEntryId.value || !historyState.value?.available) return;
  loadingHistory.value = true;
  try {
    const limit = Math.min(Math.max(historySummary.value.entry_count || 1, HISTORY_PAGE_SIZE), 200000);
    historyState.value = await fetchRimeContextHistoryArticle(selectedEntryId.value, { limit });
    historyDraft.value = historyState.value.content || '';
    historyEditing.value = true;
  } catch (err: any) {
    ElMessage.error(err.response?.data?.detail || err.message || '加载完整输入历史失败');
  } finally {
    loadingHistory.value = false;
  }
};

const cancelHistoryEdit = async () => {
  historyEditing.value = false;
  historyDraft.value = '';
  await loadHistoryArticle(historyPage.value || 1);
};

const saveHistoryEdit = async () => {
  if (!selectedEntryId.value || savingHistory.value) return;
  const content = historyDraft.value.trim();
  if (!content) {
    ElMessage.warning('输入历史正文不能为空');
    return;
  }
  savingHistory.value = true;
  try {
    historyState.value = await saveRimeContextHistoryArticle(selectedEntryId.value, { content });
    historyEditing.value = false;
    ElMessage.success('输入历史修订稿已保存');
    historyPage.value = 1;
    await loadHistoryArticle(1);
  } catch (err: any) {
    ElMessage.error(err.response?.data?.detail || err.message || '保存输入历史失败');
  } finally {
    savingHistory.value = false;
  }
};

const handleUpdateIndex = async () => {
  if (!selectedEntryId.value) return;
  if (historyEditing.value && historyHasDraftChanges.value) {
    ElMessage.warning('请先保存输入历史修订稿，再更新索引');
    return;
  }
  loadingTree.value = true;
  try {
    tree.value = await refreshRimeContextPredictionTree(selectedEntryId.value, {
      source: selectedIndexSource.value,
      limit: PREDICTION_TREE_LIMIT,
    });
    ElMessage.success(tree.value.message || '预测索引已更新');
    if (activeView.value === 'history') {
      await loadHistoryArticle();
    }
  } catch (err: any) {
    ElMessage.error(err.response?.data?.detail || err.message || '更新预测索引失败');
  } finally {
    loadingTree.value = false;
  }
};

const switchView = async (view: RimeView) => {
  activeView.value = view;
  syncRouteQuery();
  await loadActiveView();
};

const openArticleDialog = (sourceType: ArticleSourceType = 'imported_article') => {
  articleForm.value = {
    title: '',
    content: '',
    enabled: true,
    sourceType,
    weightMultiplier: sourceType === 'imported_article' ? 1 : 8,
  };
  articleDialogVisible.value = true;
};

const createNegativeLexicon = async () => {
  if (!selectedEntryId.value || submittingArticle.value) return;
  const existing = articles.value.find((item) => item.source_type === 'negative_lexicon' || item.source_type === 'negative_terms');
  if (existing) {
    await selectArticle(existing);
    return;
  }
  submittingArticle.value = true;
  try {
    articlesState.value = await importRimeContextArticle(selectedEntryId.value, {
      title: '负向短语',
      content: '\n',
      enabled: true,
      source_type: 'negative_lexicon',
      weight_multiplier: 8,
    });
    const article = articlesState.value.articles.find((item) => item.source_type === 'negative_lexicon');
    if (article) {
      selectedArticleId.value = article.id;
      articleContentPage.value = ARTICLE_CONTENT_LAST_PAGE;
      await loadArticleContent(ARTICLE_CONTENT_LAST_PAGE);
    }
    await loadTree();
  } catch (err: any) {
    ElMessage.error(err.response?.data?.detail || err.message || '新建负向短语失败');
  } finally {
    submittingArticle.value = false;
  }
};

const openCandidateDialog = (row: RimeContextPredictionRow) => {
  candidateForm.value = {
    originalContext: row.context,
    originalPrefix: row.prefix,
    originalCandidate: row.candidate,
    candidate: row.candidate,
    weight: Number(row.weight || 1),
  };
  candidateDialogVisible.value = true;
};

const adjustComparedWeight = async (item: RimeWeightCompareItem, direction: 1 | -1) => {
  if (!selectedEntryId.value || adjustingWeightKey.value) return;
  const prefix = item.default_pinyin || item.pinyin;
  if (!prefix) {
    ElMessage.warning('这个候选词没有可用拼音，暂时不能调整权重');
    return;
  }
  const baseWeight = Number(item.total_weight || 0);
  if (direction < 0 && baseWeight <= 0) return;
  const nextWeight = Math.max(0.1, baseWeight + direction * WEIGHT_COMPARE_STEP);
  adjustingWeightKey.value = `${direction}:${weightCompareItemKey(item)}`;
  try {
    weightCompareState.value = await adjustRimeContextWeightCompare(selectedEntryId.value, {
      prefix,
      candidate: item.candidate,
      weight: nextWeight,
      candidates: weightCompareCandidates.value,
      source: selectedWeightSource.value,
      limit: 20,
    });
    ElMessage.success(`${item.candidate} 已${direction > 0 ? '升权' : '降权'}到 ${formatNumber(nextWeight)}`);
  } catch (err: any) {
    ElMessage.error(err.response?.data?.detail || err.message || '调整候选词权重失败');
  } finally {
    adjustingWeightKey.value = '';
  }
};

const submitImportArticle = async () => {
  if (!selectedEntryId.value) return;
  if (!articleForm.value.content.trim()) {
    ElMessage.warning(
      articleForm.value.sourceType === 'imported_article'
        ? '语料内容不能为空'
        : `${articleSourceTypeLabel(articleForm.value.sourceType)}不能为空`,
    );
    return;
  }
  submittingArticle.value = true;
  try {
    const isPhraseSource = articleForm.value.sourceType !== 'imported_article';
    articlesState.value = await importRimeContextArticle(selectedEntryId.value, {
      title: articleForm.value.title.trim() || defaultArticleTitle(articleForm.value.sourceType),
      content: articleForm.value.content,
      enabled: articleForm.value.enabled,
      source_type: articleForm.value.sourceType,
      ...(isPhraseSource ? { weight_multiplier: articleForm.value.weightMultiplier } : {}),
    });
    articleDialogVisible.value = false;
    ElMessage.success(isPhraseSource ? `${articleSourceTypeLabel(articleForm.value.sourceType)}已导入` : '语料已导入');
    if (selectedArticleId.value && articlesState.value.articles.some((article) => article.id === selectedArticleId.value)) {
      await loadArticleContent(articleContentPage.value);
    }
    await loadTree();
  } catch (err: any) {
    ElMessage.error(err.response?.data?.detail || err.message || `${articleDialogTitle.value}失败`);
  } finally {
    submittingArticle.value = false;
  }
};

const handleArticleEnabledChange = async (article: RimeContextArticle, enabled: boolean) => {
  if (!selectedEntryId.value || updatingArticleId.value || isReadonlyArticle(article)) return;
  updatingArticleId.value = article.id;
  try {
    articlesState.value = await updateRimeContextArticle(selectedEntryId.value, article.id, { enabled });
    await loadTree();
  } catch (err: any) {
    ElMessage.error(err.response?.data?.detail || err.message || '更新文章状态失败');
    await loadArticles();
  } finally {
    updatingArticleId.value = '';
  }
};

const handleDeleteArticle = async (article: RimeContextArticle) => {
  if (!selectedEntryId.value || updatingArticleId.value || isReadonlyArticle(article)) return;
  const kind = articleKindLabel(article);
  try {
    await ElMessageBox.confirm(`确定删除${kind}“${article.title}”吗？`, `删除${kind}`, {
      confirmButtonText: '删除',
      cancelButtonText: '取消',
      type: 'warning',
    });
  } catch {
    return;
  }
  updatingArticleId.value = article.id;
  try {
    articlesState.value = await deleteRimeContextArticle(selectedEntryId.value, article.id);
    if (selectedArticleId.value === article.id) {
      resetArticleContent();
    }
    ElMessage.success(`${kind}已删除`);
    await loadTree();
  } catch (err: any) {
    ElMessage.error(err.response?.data?.detail || err.message || '删除文章失败');
  } finally {
    updatingArticleId.value = '';
  }
};

const handleDeleteCandidate = async (row: RimeContextPredictionRow) => {
  if (!selectedEntryId.value || deletingCandidateKey.value) return;
  try {
    await ElMessageBox.confirm(
      `确定删除“${displayContext(row.context)} / ${row.prefix} / ${row.candidate}”这条候选吗？`,
      '删除候选词',
      {
        confirmButtonText: '删除',
        cancelButtonText: '取消',
        type: 'warning',
      },
    );
  } catch {
    return;
  }

  deletingCandidateKey.value = candidateRowKey(row);
  try {
    await deleteRimeContextCandidate(selectedEntryId.value, {
      context: row.context,
      prefix: row.prefix,
      candidate: row.candidate,
    });
    await loadTree();
    ElMessage.success('候选词已删除');
  } catch (err: any) {
    ElMessage.error(err.response?.data?.detail || err.message || '删除候选词失败');
  } finally {
    deletingCandidateKey.value = '';
  }
};

const submitCandidateEdit = async () => {
  if (!selectedEntryId.value) return;
  const candidate = candidateForm.value.candidate.trim();
  const weight = Number(candidateForm.value.weight || 0);
  if (!candidate) {
    ElMessage.warning('候选词不能为空');
    return;
  }
  if (!(weight > 0)) {
    ElMessage.warning('权重必须大于 0');
    return;
  }

  submittingCandidate.value = true;
  try {
    await updateRimeContextCandidate(selectedEntryId.value, {
      original_context: candidateForm.value.originalContext,
      original_prefix: candidateForm.value.originalPrefix,
      original_candidate: candidateForm.value.originalCandidate,
      context: candidateForm.value.originalContext,
      prefix: candidateForm.value.originalPrefix,
      candidate,
      weight,
    });
    await loadTree();
    candidateDialogVisible.value = false;
    ElMessage.success('候选词已修改');
  } catch (err: any) {
    ElMessage.error(err.response?.data?.detail || err.message || '修改候选词失败');
  } finally {
    submittingCandidate.value = false;
  }
};

watch(contextTailGroups, (groups) => {
  if (!groups.length) {
    selectedTailKey.value = '';
    selectedContextKey.value = '';
    selectedPrefixKey.value = '';
    return;
  }
  if (!groups.some((item) => item.key === selectedTailKey.value)) {
    selectedTailKey.value = groups[0].key;
  }
}, { immediate: true });

watch(selectedTailGroup, (group) => {
  if (!group) {
    selectedContextKey.value = '';
    selectedPrefixKey.value = '';
    return;
  }
  if (!group.contexts.some((item) => item.key === selectedContextKey.value)) {
    selectedContextKey.value = group.contexts[0]?.key || '';
  }
}, { immediate: true });

watch(activePrefixGroups, (prefixes) => {
  if (!prefixes.length) {
    selectedPrefixKey.value = '';
    return;
  }
  if (!prefixes.some((item) => item.key === selectedPrefixKey.value)) {
    selectedPrefixKey.value = prefixes[0]?.key || '';
  }
}, { immediate: true });

onMounted(async () => {
  await loadDevices();
  syncRouteQuery();
  await loadActiveView();
});

onBeforeUnmount(() => {
  void saveArticleContentNow();
});
</script>

<template>
  <div class="rime-page">
    <header v-if="activeView === 'index'" class="rime-toolbar">
      <label v-if="activeView === 'index'" class="rime-field rime-field-index">
        <span>索引</span>
        <el-select
          v-model="selectedIndexSource"
          class="rime-select"
          :disabled="loadingTree"
          @change="handleIndexSourceChange"
        >
          <el-option
            v-for="item in indexSourceOptions"
            :key="item.value"
            :label="item.label"
            :value="item.value"
            :title="item.title"
          />
        </el-select>
      </label>

      <label v-if="activeView === 'index'" class="rime-field rime-field-search">
        <span>筛选</span>
        <el-input
          v-model="searchText"
          clearable
          :prefix-icon="Search"
          placeholder="搜索末词 / 前文 / 拼音 / 候选词"
          :disabled="!tree?.rows.length"
        />
      </label>

      <div class="rime-actions">
        <el-button
          v-if="activeView === 'index'"
          :icon="Refresh"
          :loading="loadingTree"
          :disabled="!selectedEntryId"
          title="更新预测索引"
          aria-label="更新预测索引"
          @click="handleRefresh"
        >
          更新索引
        </el-button>
        <el-tooltip effect="light" placement="bottom-end">
          <template #content>
            <div class="rime-help">
              这里读取每台设备自己的 Rime 用户目录；完整索引用于分析，全局热索引和前文热索引用于小狼毫快速候选，手动规则用于固定纠偏。
            </div>
          </template>
          <button type="button" class="rime-help-button" aria-label="小狼毫输入法说明">
            <el-icon><QuestionFilled /></el-icon>
          </button>
        </el-tooltip>
      </div>
    </header>

    <section v-if="!hasDevices && !loadingDevices" class="rime-empty">
      当前没有可用设备，请先在运行管理里添加本机或远程设备。
    </section>

    <template v-else>
      <nav class="rime-view-tabs" aria-label="小狼毫输入法视图">
        <button
          type="button"
          :class="{ 'is-active': activeView === 'articles' }"
          @click="switchView('articles')"
        >
          语料管理
        </button>
        <button
          type="button"
          :class="{ 'is-active': activeView === 'index' }"
          @click="switchView('index')"
        >
          预测索引
        </button>
        <button
          type="button"
          :class="{ 'is-active': activeView === 'weight' }"
          @click="switchView('weight')"
        >
          权重对比
        </button>
        <button
          type="button"
          :class="{ 'is-active': activeView === 'config' }"
          @click="switchView('config')"
        >
          运行配置
        </button>
      </nav>

      <template v-if="activeView === 'index'">
        <section
          v-if="!tree?.available"
          class="rime-unavailable"
          v-loading="loadingTree"
        >
          <p>{{ tree?.message || '请选择设备查看小狼毫输入法索引。' }}</p>
          <dl v-if="tree?.rime_dir" class="rime-meta-list">
            <dt>Rime目录</dt>
            <dd>{{ tree.rime_dir }}</dd>
          </dl>
          <table v-if="visibleFiles.length" class="rime-file-table">
            <thead>
              <tr>
                <th>文件</th>
                <th>状态</th>
                <th>大小</th>
                <th>修改时间</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="file in visibleFiles" :key="file.key">
                <td :title="file.path || file.key">{{ file.key }}</td>
                <td>{{ file.exists ? '存在' : '缺失' }}</td>
                <td>{{ formatBytes(file.size) }}</td>
                <td>{{ formatDateTime(file.modified_at) }}</td>
              </tr>
            </tbody>
          </table>
        </section>

        <template v-else>
          <section class="rime-summary">
            <span><strong>前文片段</strong>{{ formatNumber(summary.context_count) }}</span>
            <span><strong>当前拼音</strong>{{ formatNumber(summary.prefix_count) }}</span>
            <span><strong>候选词</strong>{{ formatNumber(summary.candidate_count) }}</span>
            <span><strong>记录</strong>{{ formatNumber(summary.row_count) }}</span>
            <span v-if="tree?.rime_dir" class="summary-path" :title="tree.rime_dir">
              <strong>目录</strong>{{ tree.rime_dir }}
            </span>
          </section>

          <section class="rime-workspace" v-loading="loadingTree && !contextTailGroups.length">
            <aside class="rime-context-pane">
              <button
                v-for="group in contextTailGroups"
                :key="group.key"
                type="button"
                class="context-row"
                :class="{ 'is-active': selectedTailGroup?.key === group.key }"
                :title="displayContextTailTitle(group)"
                @click="selectedTailKey = group.key"
              >
                <span class="context-name">{{ displayContextTail(group.key) }}</span>
                <span class="context-count">{{ formatNumber(group.rowCount) }}</span>
              </button>
              <div v-if="!contextTailGroups.length" class="rime-pane-empty">没有匹配项</div>
            </aside>

            <main class="rime-detail-pane">
              <template v-if="selectedTailGroup && selectedContext">
                <header class="rime-detail-head">
                  <span class="detail-label">末词</span>
                  <strong :title="displayContextTailTitle(selectedTailGroup)">{{ displayContextTail(selectedTailGroup.key) }}</strong>
                  <span>{{ formatNumber(selectedTailGroup.contextCount) }} 个前文片段</span>
                  <span>{{ formatNumber(selectedTailGroup.rowCount) }} 条记录</span>
                  <div class="rime-scope-tabs" aria-label="索引查看方式">
                    <button
                      type="button"
                      :class="{ 'is-active': selectedIndexScope === 'summary' }"
                      @click="selectedIndexScope = 'summary'"
                    >
                      汇总
                    </button>
                    <button
                      type="button"
                      :class="{ 'is-active': selectedIndexScope === 'detail' }"
                      @click="selectedIndexScope = 'detail'"
                    >
                      明细
                    </button>
                  </div>
                </header>

                <nav v-if="selectedIndexScope === 'detail'" class="rime-context-tabs" aria-label="前文片段">
                  <button
                    v-for="context in selectedTailGroup.contexts"
                    :key="context.key"
                    type="button"
                    :class="{ 'is-active': selectedContext.key === context.key }"
                    :title="displayContextTitle(context.key)"
                    @click="selectedContextKey = context.key"
                  >
                    <span>{{ displayContext(context.key) }}</span>
                    <small>{{ formatNumber(context.rowCount) }}</small>
                  </button>
                </nav>

                <section class="rime-selected-prefix">
                  <span>{{ selectedPrefixScope === 'summary' ? '候选分布' : '当前拼音' }}</span>
                  <strong>{{ selectedPrefixScope === 'summary' ? '综合' : (selectedPrefix?.key || '-') }}</strong>
                  <small>
                    {{ selectedPrefixScope === 'summary'
                      ? `${formatNumber(selectedRows.length)} 个候选词`
                      : `${formatNumber(selectedRows.length)} 个候选词` }}
                  </small>
                  <div class="rime-scope-tabs" aria-label="当前拼音查看方式">
                    <button
                      type="button"
                      :class="{ 'is-active': selectedPrefixScope === 'summary' }"
                      @click="selectedPrefixScope = 'summary'"
                    >
                      汇总
                    </button>
                    <button
                      type="button"
                      :class="{ 'is-active': selectedPrefixScope === 'detail' }"
                      @click="selectedPrefixScope = 'detail'"
                    >
                      明细
                    </button>
                  </div>
                </section>

                <nav v-if="selectedPrefixScope === 'detail'" class="rime-prefix-tabs" aria-label="当前拼音">
                  <button
                    v-for="prefix in activePrefixGroups"
                    :key="prefix.key"
                    type="button"
                    :class="{ 'is-active': selectedPrefix?.key === prefix.key }"
                    :title="`当前拼音：${prefix.key}，权重 ${formatNumber(prefix.totalWeight)}，候选词 ${formatNumber(prefix.rows.length)}`"
                    @click="selectedPrefixKey = prefix.key"
                  >
                    <span>{{ prefix.key }}</span>
                    <small>{{ formatNumber(prefix.totalWeight) }}</small>
                  </button>
                </nav>

                <div class="rime-candidate-table-wrap">
                  <table class="rime-candidate-table" aria-label="预测候选词">
                    <thead>
                      <tr>
                        <th>候选词</th>
                        <th>权重</th>
                        <th v-if="canEditCandidateRows"></th>
                      </tr>
                    </thead>
                    <tbody>
                      <tr
                        v-for="(row, index) in selectedRows"
                        :key="`${row.context}:${row.prefix}:${row.candidate}:${index}`"
                        class="candidate-row"
                        :class="{ 'is-summary': !canEditCandidateRows }"
                        :title="canEditCandidateRows ? `双击修改：${row.prefix} ${row.candidate}` : `汇总候选：${row.candidate}`"
                        @dblclick="canEditCandidateRows && openCandidateDialog(row)"
                      >
                        <td>{{ row.candidate }}</td>
                        <td>{{ formatNumber(row.weight) }}</td>
                        <td v-if="canEditCandidateRows">
                          <el-button
                            link
                            type="danger"
                            :icon="Delete"
                            :loading="deletingCandidateKey === candidateRowKey(row)"
                            :disabled="Boolean(deletingCandidateKey)"
                            title="删除候选词"
                            aria-label="删除候选词"
                            @dblclick.stop
                            @click="handleDeleteCandidate(row)"
                          />
                        </td>
                      </tr>
                    </tbody>
                  </table>
                </div>
              </template>
              <div v-else class="rime-pane-empty">没有可展示的预测索引。</div>
            </main>
          </section>
        </template>
      </template>

      <section v-else-if="activeView === 'weight'" class="rime-weight-compare" v-loading="loadingWeightCompare">
        <section class="rime-section-head">
          <div class="rime-section-title">
            <strong>权重对比</strong>
            <span v-if="weightCompareState?.rime_dir" class="rime-section-path" :title="weightCompareState.rime_dir">
              目录 {{ weightCompareState.rime_dir }}
            </span>
          </div>
          <div class="rime-section-actions">
            <el-select
              v-model="selectedWeightSource"
              class="rime-select"
              size="small"
              :disabled="loadingWeightCompare"
              :title="currentWeightSource.title"
              @change="loadWeightCompare"
            >
              <el-option
                v-for="item in indexSourceOptions"
                :key="item.value"
                :label="item.label"
                :value="item.value"
                :title="item.title"
              />
            </el-select>
            <el-button
              size="small"
              :loading="loadingWeightCompare"
              :disabled="!selectedEntryId || !weightCompareCandidates.length"
              @click="loadWeightCompare"
            >
              对比
            </el-button>
          </div>
        </section>

        <section class="rime-weight-input">
          <el-input
            v-model="weightCompareText"
            type="textarea"
            resize="vertical"
            :autosize="{ minRows: 3, maxRows: 8 }"
            placeholder="采用&#10;才用"
            @keydown.ctrl.enter.prevent="loadWeightCompare"
          />
        </section>

        <section v-if="!weightCompareState?.available" class="rime-unavailable">
          <p>{{ weightCompareState?.message || '输入候选词后查看权重对比。' }}</p>
        </section>

        <template v-else>
          <section class="rime-summary">
            <span><strong>候选词</strong>{{ formatNumber(weightCompareSummary.candidate_count) }}</span>
            <span><strong>已命中</strong>{{ formatNumber(weightCompareSummary.matched_count) }}</span>
            <span><strong>记录</strong>{{ formatNumber(weightCompareSummary.row_count) }}</span>
            <span v-if="weightCompareState?.updated_at" class="summary-time">
              <strong>索引更新</strong>{{ formatDateTime(weightCompareState.updated_at) }}
            </span>
          </section>

          <div class="rime-weight-table-wrap">
            <table class="rime-weight-table" aria-label="候选词权重对比">
              <thead>
                <tr>
                  <th>候选词</th>
                  <th>总权重</th>
                  <th>完整拼音</th>
                  <th>记录</th>
                  <th>拼音分布</th>
                  <th>前文分布</th>
                  <th>来源</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="item in weightCompareState.items" :key="item.candidate">
                  <td class="weight-candidate">
                    <strong>{{ item.candidate }}</strong>
                    <small v-if="item.input && item.input !== item.candidate">{{ item.input }}</small>
                  </td>
                  <td>{{ formatNumber(item.total_weight) }}</td>
                  <td>
                    <span>{{ item.default_pinyin || '-' }}</span>
                    <small v-if="item.exact_prefix_weight">
                      {{ formatNumber(item.exact_prefix_weight) }}
                    </small>
                  </td>
                  <td>{{ formatNumber(item.row_count) }}</td>
                  <td :title="formatWeightCompareGroups(item.prefixes, undefined, 20)">
                    {{ formatWeightCompareGroups(item.prefixes) }}
                  </td>
                  <td :title="formatWeightCompareGroups(item.contexts, displayContext, 20)">
                    {{ formatWeightCompareGroups(item.contexts, displayContext) }}
                  </td>
                  <td :title="formatWeightCompareGroups(item.comments, undefined, 20)">
                    {{ formatWeightCompareGroups(item.comments) }}
                  </td>
                  <td class="weight-actions">
                    <el-button
                      link
                      :icon="Minus"
                      :loading="adjustingWeightKey === `-1:${weightCompareItemKey(item)}`"
                      :disabled="Boolean(adjustingWeightKey) || Number(item.total_weight || 0) <= 0"
                      title="降低全局手动权重"
                      aria-label="降低全局手动权重"
                      @click="adjustComparedWeight(item, -1)"
                    />
                    <el-button
                      link
                      type="primary"
                      :icon="Plus"
                      :loading="adjustingWeightKey === `1:${weightCompareItemKey(item)}`"
                      :disabled="Boolean(adjustingWeightKey)"
                      title="提高全局手动权重"
                      aria-label="提高全局手动权重"
                      @click="adjustComparedWeight(item, 1)"
                    />
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </template>
      </section>

      <section v-else-if="activeView === 'config'" class="rime-runtime-config" v-loading="loadingRuntimeConfig">
        <section class="rime-section-head">
          <div class="rime-section-title">
            <strong>运行配置</strong>
            <span v-if="runtimeConfigState?.rime_dir" class="rime-section-path" :title="runtimeConfigState.rime_dir">
              目录 {{ runtimeConfigState.rime_dir }}
            </span>
          </div>
          <div class="rime-section-actions">
            <el-button
              size="small"
              :disabled="!runtimeConfigDirty || savingRuntimeConfig"
              @click="resetRuntimeConfigDraft"
            >
              恢复
            </el-button>
            <el-button
              type="primary"
              size="small"
              :loading="savingRuntimeConfig"
              :disabled="!runtimeConfigDirty || !runtimeConfigState?.available"
              @click="saveRuntimeConfig"
            >
              保存配置
            </el-button>
          </div>
        </section>

        <section v-if="!runtimeConfigState?.available" class="rime-unavailable">
          <p>{{ runtimeConfigState?.message || '请选择设备查看运行配置。' }}</p>
        </section>

        <template v-else>
          <section v-if="runtimeConfigState.missing_keys.length" class="rime-runtime-note">
            rime.lua 缺少 {{ runtimeConfigState.missing_keys.length }} 个可视化配置项。
          </section>
          <table class="rime-config-table" aria-label="小狼毫运行配置">
            <tbody v-for="group in visibleRuntimeConfigGroups" :key="group.title">
              <tr class="config-group-row">
                <th colspan="3">{{ group.title }}</th>
              </tr>
              <tr v-for="key in group.keys" :key="key">
                <td class="config-name">
                  <span>{{ runtimeConfigLabel(key, runtimeConfigState.fields[key]) }}</span>
                </td>
                <td class="config-control">
                  <el-switch
                    v-if="runtimeConfigState.fields[key].type === 'bool'"
                    v-model="runtimeConfigDraft[key]"
                  />
                  <el-select
                    v-else-if="runtimeConfigState.fields[key].type === 'enum'"
                    v-model="runtimeConfigDraft[key]"
                    class="config-select"
                    popper-class="rime-config-select-dropdown"
                  >
                    <el-option
                      v-for="option in runtimeConfigEnumOptions(key, runtimeConfigState.fields[key])"
                      :key="option"
                      :label="runtimeConfigOptionLabel(option)"
                      :value="option"
                    >
                      <div v-if="key === 'commit_capture_mode'" class="config-option-line">
                        <span class="config-option-title">{{ runtimeConfigOptionLabel(option) }}</span>
                        <small>{{ runtimeModeDetail(option) }}</small>
                      </div>
                      <span v-else>{{ option }}</span>
                    </el-option>
                  </el-select>
                  <el-input-number
                    v-else
                    v-model="runtimeConfigDraft[key]"
                    :min="runtimeConfigMin(runtimeConfigState.fields[key])"
                    :max="runtimeConfigMax(runtimeConfigState.fields[key])"
                    :step="runtimeConfigStep(runtimeConfigState.fields[key])"
                    :precision="runtimeConfigState.fields[key].type === 'float' ? 2 : 0"
                    controls-position="right"
                  />
                </td>
                <td class="config-hint">{{ runtimeConfigHint(key) }}</td>
              </tr>
            </tbody>
          </table>
        </template>
      </section>

      <section v-else-if="activeView === 'history'" class="rime-history" v-loading="loadingHistory">
        <section class="rime-summary">
          <span><strong>事件</strong>{{ formatNumber(historySummary.entry_count) }}</span>
          <span v-if="historyPagination">
            <strong>本页</strong>{{ formatNumber(historyPagination.start_index) }}-{{ formatNumber(historyPagination.end_index) }}
          </span>
          <span><strong>字符</strong>{{ formatNumber(historySummary.char_count) }}</span>
          <span><strong>段落</strong>{{ formatNumber(historySummary.paragraph_count) }}</span>
          <span v-if="historySummary.pending_row_count">
            <strong>待合并</strong>{{ formatNumber(historySummary.pending_row_count) }}
          </span>
          <span v-if="historySummary.edited">
            <strong>正文</strong>修订稿
          </span>
          <span v-if="historySummary.appended_event_count">
            <strong>新增</strong>{{ formatNumber(historySummary.appended_event_count) }}
          </span>
          <span class="summary-time" :title="formatHistoryRange(historySummary.first_seen, historySummary.last_seen)">
            <strong>时间</strong>{{ formatHistoryRange(historySummary.first_seen, historySummary.last_seen) }}
          </span>
          <span v-if="historyState?.rime_dir" class="summary-path" :title="historyState.rime_dir">
            <strong>目录</strong>{{ historyState.rime_dir }}
          </span>
        </section>

        <section v-if="!historyState?.available" class="rime-unavailable">
          <p>{{ historyState?.message || '请选择设备查看输入历史。' }}</p>
        </section>

        <article v-else class="rime-history-article" aria-label="输入历史文章">
          <header v-if="historyPagination && !historyEditing" class="history-pager">
            <el-button
              size="small"
              :disabled="!historyPagination.has_prev || loadingHistory"
              @click="changeHistoryPage(historyPagination.page - 1)"
            >
              上一页
            </el-button>
            <span>
              第 {{ formatNumber(historyPagination.page) }} / {{ formatNumber(historyPagination.total_pages) }} 页
            </span>
            <span>
              {{ formatNumber(historyPagination.start_index) }}-{{ formatNumber(historyPagination.end_index) }} / {{ formatNumber(historyPagination.total) }}
            </span>
            <el-button
              size="small"
              :disabled="!historyPagination.has_next || loadingHistory"
              @click="changeHistoryPage(historyPagination.page + 1)"
            >
              下一页
            </el-button>
          </header>
          <header v-else-if="historySummary.truncated" class="history-note">
            仅显示最近 {{ formatNumber(historySummary.limit) }} 条输入事件。
          </header>
          <el-input
            v-if="historyEditing"
            v-model="historyDraft"
            class="history-editor"
            type="textarea"
            resize="vertical"
            :autosize="{ minRows: 18 }"
            :disabled="savingHistory"
            aria-label="输入历史正文"
          />
          <pre v-else>{{ historyState.content }}</pre>
        </article>
      </section>

      <section v-else-if="activeView === 'lint'" class="rime-lint" v-loading="loadingLint">
        <section class="rime-summary">
          <span><strong>问题</strong>{{ formatNumber(lintSummary.issue_count) }}</span>
          <span><strong>高</strong>{{ formatNumber(lintSummary.high_count) }}</span>
          <span><strong>中</strong>{{ formatNumber(lintSummary.medium_count) }}</span>
          <span><strong>低</strong>{{ formatNumber(lintSummary.low_count) }}</span>
          <span><strong>来源</strong>{{ formatNumber(lintSummary.source_count) }}</span>
          <span v-if="lintSummary.ai_count"><strong>AI</strong>{{ formatNumber(lintSummary.ai_count) }}</span>
          <span v-if="lintState?.rime_dir" class="summary-path" :title="lintState.rime_dir">
            <strong>目录</strong>{{ lintState.rime_dir }}
          </span>
        </section>

        <section class="rime-lint-controls">
          <label class="rime-field rime-field-source">
            <span>范围</span>
            <el-select v-model="lintSource" class="rime-select" :disabled="loadingLint">
              <el-option label="全部语料" value="all" />
              <el-option label="输入历史" value="history" />
              <el-option label="导入文章" value="articles" />
            </el-select>
          </label>
          <label class="rime-field rime-field-source">
            <span>方式</span>
            <el-select v-model="lintMode" class="rime-select" :disabled="loadingLint">
              <el-option label="规则预检" value="rules" />
              <el-option label="AI 校对" value="ai" />
            </el-select>
          </label>
        </section>

        <section v-if="!lintState?.available" class="rime-unavailable">
          <p>{{ lintState?.message || '请选择设备后开始语料检查。' }}</p>
        </section>

        <table v-else-if="lintIssues.length" class="rime-lint-table" aria-label="语料检查问题">
          <thead>
            <tr>
              <th>级别</th>
              <th>来源</th>
              <th>位置</th>
              <th>问题</th>
              <th>原文</th>
              <th>建议</th>
              <th>置信</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="issue in lintIssues" :key="issue.id">
              <td>
                <el-tag size="small" :type="lintSeverityType(issue.severity)">
                  {{ lintSeverityLabel(issue.severity) }}
                </el-tag>
              </td>
              <td class="lint-source" :title="issue.source_title">
                <span>{{ lintSourceLabel(issue.source_type) }}</span>
                <small>{{ issue.source_title }}</small>
              </td>
              <td>{{ issue.line }}:{{ issue.column }}</td>
              <td class="lint-message" :title="issue.message">
                <strong>{{ issue.type }}</strong>
                <span>{{ issue.message }}</span>
              </td>
              <td class="lint-text" :title="issue.excerpt">{{ issue.text }}</td>
              <td class="lint-suggestion">{{ issue.suggestion || '-' }}</td>
              <td>{{ lintConfidenceText(issue.confidence) }}</td>
            </tr>
          </tbody>
        </table>

        <div v-else class="rime-pane-empty article-empty">
          没有发现需要处理的问题。
        </div>
      </section>

      <section v-else class="rime-articles" v-loading="loadingArticles">
        <section class="rime-section-head">
          <div class="rime-section-title">
            <strong>语料文件清单</strong>
            <span v-if="articlesState?.rime_dir" class="rime-section-path" :title="articlesState.rime_dir">
              目录 {{ articlesState.rime_dir }}
            </span>
          </div>
          <div class="rime-section-actions">
            <el-button
              type="primary"
              size="small"
              plain
              :icon="Plus"
              :disabled="!canImportArticle"
              @click="openArticleDialog('imported_article')"
            >
              导入语料
            </el-button>
            <el-button
              size="small"
              :icon="Plus"
              :disabled="!canImportArticle"
              @click="openArticleDialog('lexicon')"
            >
              导入自定义短语
            </el-button>
            <el-button
              size="small"
              :icon="Plus"
              :disabled="!canImportArticle || submittingArticle"
              @click="createNegativeLexicon"
            >
              新建负向短语
            </el-button>
          </div>
        </section>

        <section v-if="!articlesState?.available" class="rime-unavailable">
          <p>{{ articlesState?.message || '请选择设备查看语料库。' }}</p>
        </section>

        <template v-else-if="articles.length">
          <table class="rime-article-table" aria-label="语料文件清单">
            <thead>
              <tr>
                <th>启用</th>
                <th>类型</th>
                <th>名称</th>
                <th>字符</th>
                <th>语料更新</th>
                <th>索引</th>
                <th>索引更新</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="article in articles"
                :key="article.id"
                class="article-row"
                :class="{ 'is-selected': selectedArticleId === article.id }"
                @click="selectArticle(article)"
              >
                <td @click.stop>
                  <el-switch
                    :model-value="article.enabled"
                    :loading="updatingArticleId === article.id"
                    :disabled="isReadonlyArticle(article)"
                    @change="(value: string | number | boolean) => handleArticleEnabledChange(article, Boolean(value))"
                  />
                </td>
                <td>{{ articleKindLabel(article) }}</td>
                <td class="article-title" :title="articleTitleTooltip(article)">
                  {{ articleDisplayName(article) }}
                </td>
                <td>{{ formatNumber(article.char_count) }}</td>
                <td>{{ formatDateTime(article.updated_at) }}</td>
                <td>{{ formatNumber(article.row_count) }}</td>
                <td>{{ formatDateTime(article.processed_at) }}</td>
                <td @click.stop>
                  <el-button
                    v-if="!isReadonlyArticle(article)"
                    link
                    type="danger"
                    :icon="Delete"
                    :disabled="updatingArticleId === article.id"
                    title="删除"
                    aria-label="删除"
                    @click="handleDeleteArticle(article)"
                  />
                </td>
              </tr>
            </tbody>
          </table>

          <article
            v-if="selectedArticle"
            class="rime-history-article rime-article-content"
            aria-label="语料内容"
            v-loading="loadingArticleContent"
          >
            <header v-if="articleContentPagination" class="history-pager">
              <el-button
                size="small"
                :disabled="!articleContentPagination.has_prev || loadingArticleContent"
                @click="changeArticleContentPage(articleContentPagination.page - 1)"
              >
                上一页
              </el-button>
              <span>
                第 {{ formatNumber(articleContentPagination.page) }} / {{ formatNumber(articleContentPagination.total_pages) }} 页
              </span>
              <span>
                {{ formatNumber(articleContentPagination.start_index) }}-{{ formatNumber(articleContentPagination.end_index) }} / {{ formatNumber(articleContentPagination.total) }}
              </span>
              <span
                v-if="articleContentSaveLabel"
                class="history-save-state"
                :class="`is-${articleContentSaveStatus}`"
              >
                {{ articleContentSaveLabel }}
              </span>
              <el-button
                size="small"
                :disabled="!articleContentPagination.has_next || loadingArticleContent"
                @click="changeArticleContentPage(articleContentPagination.page + 1)"
              >
                下一页
              </el-button>
            </header>
            <section v-if="articleContentState && !articleContentState.available" class="rime-unavailable">
              <p>{{ articleContentState.message || '这份语料暂时没有可展示内容。' }}</p>
            </section>
            <el-input
              v-else-if="selectedArticle && !isReadonlyArticle(selectedArticle)"
              v-model="articleContentDraft"
              class="article-content-editor"
              type="textarea"
              :autosize="{ minRows: 4, maxRows: 24 }"
              @input="scheduleArticleContentSave"
            />
            <pre v-else>{{ articleContentState?.content || '' }}</pre>
          </article>
        </template>

        <div v-else class="rime-pane-empty article-empty">
          还没有导入语料。
        </div>
      </section>
    </template>

    <el-dialog
      v-model="candidateDialogVisible"
      title="修改候选词"
      width="420px"
      destroy-on-close
    >
      <div class="article-dialog-body">
        <div class="candidate-dialog-meta">
          <span><strong>前文片段</strong>{{ displayContext(candidateForm.originalContext) }}</span>
          <span><strong>当前拼音</strong>{{ candidateForm.originalPrefix }}</span>
        </div>
        <label class="article-dialog-field">
          <span>候选词</span>
          <el-input v-model="candidateForm.candidate" placeholder="输入修改后的候选词" />
        </label>
        <label class="article-dialog-field">
          <span>权重</span>
          <el-input-number v-model="candidateForm.weight" :min="0.1" :step="1" />
        </label>
      </div>
      <template #footer>
        <el-button @click="candidateDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="submittingCandidate" @click="submitCandidateEdit">
          保存
        </el-button>
      </template>
    </el-dialog>

    <el-dialog
      v-model="articleDialogVisible"
      :title="articleDialogTitle"
      width="680px"
      destroy-on-close
    >
      <div class="article-dialog-body">
        <label class="article-dialog-field">
          <span>标题</span>
          <el-input v-model="articleForm.title" placeholder="留空则使用正文第一行" />
        </label>
        <label class="article-dialog-field">
          <span>{{ articleContentLabel }}</span>
          <el-input
            v-model="articleForm.content"
            type="textarea"
            :rows="12"
            resize="vertical"
            :placeholder="articleContentPlaceholder"
          />
        </label>
        <label v-if="articleForm.sourceType !== 'imported_article'" class="article-dialog-field">
          <span>{{ articleForm.sourceType === 'negative_lexicon' ? '降权' : '权重' }}</span>
          <el-input-number v-model="articleForm.weightMultiplier" :min="1" :max="100" :step="1" />
        </label>
        <el-checkbox v-model="articleForm.enabled">导入后立即参与预测索引</el-checkbox>
      </div>
      <template #footer>
        <el-button @click="articleDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="submittingArticle" @click="submitImportArticle">
          {{ articleDialogTitle }}
        </el-button>
      </template>
    </el-dialog>

  </div>
</template>

<style scoped>
.rime-page {
  height: 100%;
  min-height: 0;
  display: flex;
  flex-direction: column;
  background: #f7f8fa;
  color: #1f2933;
}

.rime-toolbar {
  display: flex;
  align-items: end;
  gap: 12px;
  padding: 12px 14px;
  border-bottom: 1px solid #d8dee6;
  background: #fff;
}

.rime-field {
  display: flex;
  flex-direction: column;
  gap: 5px;
  font-size: 12px;
  color: #667085;
}

.rime-field-index {
  width: 130px;
}

.rime-field-search {
  width: 280px;
}

.rime-field-source {
  width: 160px;
}

.rime-select {
  width: 100%;
}

.rime-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-left: auto;
}

.rime-help-button {
  width: 28px;
  height: 28px;
  border: 1px solid #d0d7de;
  border-radius: 4px;
  background: #fff;
  color: #667085;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
}

.rime-help {
  max-width: 300px;
  line-height: 1.55;
}

.rime-summary {
  display: flex;
  align-items: center;
  gap: 14px;
  min-height: 36px;
  padding: 8px 14px;
  border-bottom: 1px solid #e3e7ed;
  background: #fff;
  font-size: 13px;
}

.rime-view-tabs {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 8px 14px 0;
  background: #fff;
  border-bottom: 1px solid #e3e7ed;
}

.rime-view-tabs button {
  height: 30px;
  padding: 0 12px;
  border: 0;
  border-bottom: 2px solid transparent;
  background: transparent;
  color: #667085;
  cursor: pointer;
}

.rime-view-tabs button.is-active {
  color: #1e5cc8;
  border-bottom-color: #3b82f6;
}

.rime-summary span {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.rime-summary strong {
  color: #667085;
  font-weight: 500;
}

.rime-section-head {
  display: flex;
  align-items: center;
  gap: 12px;
  min-height: 36px;
  padding: 6px 14px;
  border-bottom: 1px solid #e3e7ed;
  background: #fff;
}

.rime-section-title {
  display: flex;
  align-items: baseline;
  gap: 12px;
  min-width: 0;
}

.rime-section-title > strong {
  flex: 0 0 auto;
  font-size: 14px;
  font-weight: 600;
}

.rime-section-path {
  min-width: 0;
  overflow: hidden;
  color: #344054;
  font-size: 13px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.rime-section-actions {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-left: auto;
}

.summary-path {
  flex: 1;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.summary-time {
  max-width: 360px;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.muted {
  color: #7b8794;
}

.rime-empty,
.rime-unavailable,
.rime-pane-empty {
  color: #667085;
}

.rime-empty,
.rime-unavailable {
  padding: 18px 14px;
}

.rime-unavailable p {
  margin: 0 0 12px;
}

.rime-meta-list {
  display: grid;
  grid-template-columns: max-content minmax(0, 1fr);
  gap: 8px 12px;
  margin: 0 0 12px;
  font-size: 13px;
}

.rime-meta-list dt {
  color: #667085;
}

.rime-meta-list dd {
  margin: 0;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.rime-file-table,
.rime-candidate-table,
.rime-article-table,
.rime-weight-table,
.rime-config-table {
  border-collapse: collapse;
  table-layout: auto;
  width: auto;
  max-width: 100%;
  font-size: 13px;
  background: #fff;
}

.rime-file-table th,
.rime-file-table td,
.rime-candidate-table th,
.rime-candidate-table td,
.rime-article-table th,
.rime-article-table td,
.rime-weight-table th,
.rime-weight-table td,
.rime-config-table th,
.rime-config-table td {
  padding: 7px 10px;
  border-bottom: 1px solid #e5e8ee;
  text-align: left;
  white-space: nowrap;
}

.rime-file-table th,
.rime-candidate-table th,
.rime-article-table th,
.rime-weight-table th,
.rime-config-table th {
  color: #667085;
  font-weight: 500;
  background: #f1f4f8;
}

.rime-workspace {
  flex: 1;
  min-height: 0;
  display: grid;
  grid-template-columns: 300px minmax(0, 1fr);
  overflow: hidden;
}

.rime-context-pane {
  min-height: 0;
  max-height: 100%;
  overflow: auto;
  border-right: 1px solid #d8dee6;
  background: #fff;
  padding: 6px;
}

.context-row {
  width: 100%;
  height: 32px;
  display: grid;
  grid-template-columns: minmax(0, 1fr) max-content;
  align-items: center;
  gap: 8px;
  border: 0;
  border-radius: 4px;
  background: transparent;
  color: #1f2933;
  text-align: left;
  cursor: pointer;
  padding: 0 8px;
}

.context-row:hover {
  background: #eef3fb;
}

.context-row.is-active {
  background: #dfeaff;
  color: #1e5cc8;
}

.context-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.context-count {
  color: #7b8794;
  font-size: 12px;
}

.rime-detail-pane {
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  padding: 12px 14px 24px;
}

.rime-runtime-config {
  flex: 1;
  min-height: 0;
  overflow: auto;
}

.rime-weight-compare {
  flex: 1;
  min-height: 0;
  overflow: auto;
}

.rime-weight-input {
  max-width: 620px;
  padding: 12px 14px;
}

.rime-weight-input :deep(.el-textarea__inner) {
  line-height: 1.6;
  font-family: inherit;
}

.rime-weight-table-wrap {
  padding: 12px 14px 24px;
  overflow: auto;
}

.rime-weight-table {
  min-width: 900px;
}

.rime-weight-table td {
  vertical-align: top;
}

.rime-weight-table td:nth-child(5),
.rime-weight-table td:nth-child(6),
.rime-weight-table td:nth-child(7) {
  max-width: 260px;
  overflow: hidden;
  text-overflow: ellipsis;
}

.weight-candidate {
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.weight-candidate strong {
  font-size: 15px;
  font-weight: 600;
}

.weight-candidate small,
.rime-weight-table td small {
  color: #7b8794;
}

.weight-actions {
  white-space: nowrap;
}

.weight-actions :deep(.el-button) {
  margin-left: 0;
}

.rime-runtime-note {
  padding: 8px 14px;
  color: #7b8794;
  font-size: 13px;
}

.rime-config-table {
  margin: 12px 14px 24px;
}

.config-group-row th {
  padding-top: 10px;
  color: #344054;
  font-weight: 600;
}

.config-name {
  min-width: 120px;
  color: #1f2933;
}

.config-control {
  min-width: 170px;
}

.config-select {
  width: 190px;
}

.config-hint {
  max-width: 460px;
  color: #667085;
  white-space: normal;
  line-height: 1.45;
}

:deep(.rime-config-select-dropdown .el-select-dropdown__item) {
  height: auto;
  min-height: 34px;
  padding: 5px 12px;
  line-height: 1.35;
}

:deep(.rime-config-select-dropdown .config-option-line) {
  display: grid;
  grid-template-columns: 132px minmax(0, 1fr);
  align-items: baseline;
  column-gap: 12px;
}

:deep(.rime-config-select-dropdown .config-option-title) {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

:deep(.rime-config-select-dropdown .config-option-line small) {
  color: #7b8794;
  font-size: 12px;
  font-weight: 400;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.rime-detail-head {
  flex: 0 0 auto;
  display: flex;
  align-items: baseline;
  gap: 10px;
  margin-bottom: 10px;
}

.rime-detail-head strong {
  max-width: 560px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 16px;
}

.detail-label {
  color: #667085;
  font-size: 13px;
}

.rime-detail-head span {
  color: #667085;
  font-size: 13px;
}

.rime-scope-tabs {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  margin-left: 4px;
  padding: 2px;
  border: 1px solid #d0d7de;
  border-radius: 4px;
  background: #fff;
}

.rime-scope-tabs button {
  height: 24px;
  border: 0;
  border-radius: 3px;
  background: transparent;
  color: #667085;
  padding: 0 8px;
  cursor: pointer;
}

.rime-scope-tabs button.is-active {
  background: #eaf2ff;
  color: #1e5cc8;
}

.rime-context-tabs {
  flex: 0 0 auto;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  max-height: 96px;
  overflow: auto;
  padding-right: 4px;
  margin-bottom: 10px;
}

.rime-context-tabs button {
  max-width: 280px;
  height: 28px;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border: 1px solid #d0d7de;
  border-radius: 4px;
  background: #fff;
  color: #1f2933;
  padding: 0 8px;
  cursor: pointer;
}

.rime-context-tabs button.is-active {
  border-color: #3b82f6;
  color: #1e5cc8;
  background: #eaf2ff;
}

.rime-context-tabs span {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.rime-context-tabs small {
  color: #7b8794;
}

.rime-selected-prefix {
  flex: 0 0 auto;
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin-bottom: 8px;
  font-size: 13px;
  color: #667085;
}

.rime-selected-prefix strong {
  max-width: 560px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: #1f2933;
  font-size: 15px;
}

.rime-selected-prefix small {
  color: #7b8794;
}

.rime-prefix-tabs {
  flex: 0 0 auto;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  max-height: 220px;
  overflow: auto;
  padding-right: 4px;
  margin-bottom: 12px;
}

.rime-prefix-tabs button {
  height: 28px;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border: 1px solid #d0d7de;
  border-radius: 4px;
  background: #fff;
  color: #1f2933;
  padding: 0 9px;
  cursor: pointer;
}

.rime-prefix-tabs button.is-active {
  border-color: #3b82f6;
  color: #1e5cc8;
  background: #eaf2ff;
}

.rime-prefix-tabs small {
  color: #7b8794;
}

.rime-candidate-table-wrap {
  flex: 1 1 auto;
  min-height: 120px;
  overflow: auto;
}

.rime-candidate-table thead th {
  position: sticky;
  top: 0;
  z-index: 1;
}

.rime-candidate-table td:first-child {
  font-size: 15px;
}

.candidate-row {
  cursor: default;
}

.candidate-row td:first-child {
  cursor: pointer;
}

.candidate-row.is-summary td:first-child {
  cursor: default;
}

.rime-articles {
  flex: 1;
  min-height: 0;
  overflow: auto;
}

.rime-lint {
  flex: 1;
  min-height: 0;
  overflow: auto;
}

.rime-lint-controls {
  display: flex;
  align-items: end;
  gap: 12px;
  padding: 10px 14px;
  border-bottom: 1px solid #d8dee6;
  background: #fff;
}

.rime-lint-table {
  width: max-content;
  min-width: 760px;
  margin: 12px 14px 24px;
  border-collapse: collapse;
  background: #fff;
}

.rime-lint-table th,
.rime-lint-table td {
  border: 1px solid #e5e7eb;
  padding: 8px 10px;
  text-align: left;
  vertical-align: top;
}

.rime-lint-table th {
  background: #f1f5f9;
  color: #475569;
  font-weight: 600;
}

.lint-source,
.lint-message {
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.lint-source small,
.lint-message span {
  max-width: 360px;
  color: #667085;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.lint-text {
  max-width: 260px;
  color: #111827;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.lint-suggestion {
  max-width: 180px;
  color: #0f766e;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.rime-history {
  flex: 1;
  min-height: 0;
  overflow: auto;
}

.rime-history-article {
  margin: 12px 14px 24px;
  max-width: 920px;
  border: 1px solid #d8dee6;
  border-radius: 4px;
  background: #fff;
}

.rime-history-article pre {
  margin: 0;
  padding: 14px 16px;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  line-height: 1.75;
  font-family: inherit;
  font-size: 15px;
  color: #1f2933;
}

.history-editor {
  width: 100%;
}

.history-editor :deep(.el-textarea__inner) {
  border: 0;
  border-radius: 0;
  box-shadow: none;
  padding: 14px 16px;
  line-height: 1.75;
  font-family: inherit;
  font-size: 15px;
  color: #1f2933;
  background: #fff;
}

.history-note {
  padding: 8px 12px;
  border-bottom: 1px solid #e5e8ee;
  color: #667085;
  font-size: 13px;
  background: #f8fafc;
}

.history-pager {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 12px;
  border-bottom: 1px solid #e5e8ee;
  color: #667085;
  font-size: 13px;
  background: #f8fafc;
}

.history-save-state {
  margin-left: auto;
  color: #667085;
}

.history-save-state.is-saved {
  color: #389e0d;
}

.history-save-state.is-error {
  color: #d92d20;
}

.article-content-editor {
  width: 100%;
}

.article-content-editor :deep(.el-textarea__inner) {
  border: 0;
  border-radius: 0;
  box-shadow: none;
  padding: 14px 16px;
  line-height: 1.75;
  font-family: inherit;
  font-size: 15px;
  color: #1f2933;
  background: #fff;
}

.rime-article-table {
  margin: 12px 14px 24px;
}

.article-row {
  cursor: pointer;
}

.article-row:hover {
  background: #f5f8fc;
}

.article-row.is-selected {
  background: #dfeaff;
}

.article-title {
  max-width: 420px;
  overflow: hidden;
  text-overflow: ellipsis;
}

.article-empty {
  padding: 18px 14px;
}

.rime-article-content {
  margin: 0 14px 24px;
}

.article-dialog-body {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.article-dialog-field {
  display: flex;
  flex-direction: column;
  gap: 5px;
  font-size: 13px;
  color: #667085;
}

.candidate-dialog-meta {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 13px;
  color: #667085;
}

.candidate-dialog-meta strong {
  margin-right: 6px;
  color: #1f2933;
  font-weight: 500;
}

@media (max-width: 760px) {
  .rime-toolbar {
    align-items: stretch;
    flex-direction: column;
  }

  .rime-field-index,
  .rime-field-search {
    width: 100%;
  }

  .rime-actions {
    margin-left: 0;
  }

  .rime-section-head {
    align-items: flex-start;
    flex-direction: column;
  }

  .rime-section-title {
    align-items: flex-start;
    flex-direction: column;
    gap: 4px;
  }

  .rime-section-actions {
    flex-wrap: wrap;
    margin-left: 0;
  }

  .rime-workspace {
    grid-template-columns: 1fr;
  }

  .rime-context-pane {
    max-height: 180px;
    border-right: 0;
    border-bottom: 1px solid #d8dee6;
  }

  .rime-article-table {
    width: calc(100% - 28px);
  }
}
</style>
