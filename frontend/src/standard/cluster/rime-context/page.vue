<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { ElMessage, ElMessageBox } from 'element-plus';
import { Check, Close, Delete, Edit, Plus, QuestionFilled, Refresh, Search } from '@element-plus/icons-vue';
import {
  deleteRimeContextCandidate,
  deleteRimeContextArticle,
  fetchRimeContextArticles,
  fetchRimeContextHistoryArticle,
  fetchRimeContextLint,
  fetchRimeContextPredictionTree,
  importRimeContextArticle,
  importRimeContextDeviceHistory,
  refreshRimeContextPredictionTree,
  saveRimeContextHistoryArticle,
  updateRimeContextCandidate,
  updateRimeContextArticle,
  type RimeContextArticle,
  type RimeContextArticlesResponse,
  type RimeContextHistoryArticleResponse,
  type RimeContextLintIssue,
  type RimeContextLintResponse,
  type RimeContextPredictionRow,
  type RimeContextPredictionTree,
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

type RimeView = 'index' | 'history' | 'articles' | 'lint';
type IndexScope = 'summary' | 'detail';
type PrefixScope = 'summary' | 'detail';

const HISTORY_PAGE_SIZE = 2000;

const route = useRoute();
const router = useRouter();

const routeView = (value: unknown): RimeView => {
  if (value === 'lint') return 'lint';
  if (value === 'articles') return 'articles';
  if (value === 'history') return 'history';
  return 'index';
};

const devices = computed(() => taskStore.devices);
const loadingDevices = ref(false);
const loadingTree = ref(false);
const loadingArticles = ref(false);
const loadingHistory = ref(false);
const loadingLint = ref(false);
const savingHistory = ref(false);
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
const tree = ref<RimeContextPredictionTree | null>(null);
const articlesState = ref<RimeContextArticlesResponse | null>(null);
const historyState = ref<RimeContextHistoryArticleResponse | null>(null);
const lintState = ref<RimeContextLintResponse | null>(null);
const lintSource = ref<'all' | 'history' | 'articles'>('all');
const lintMode = ref<'rules' | 'ai'>('rules');
const lintLoadedSource = ref<'all' | 'history' | 'articles'>('all');
const lintLoadedMode = ref<'rules' | 'ai'>('rules');
const historyPage = ref(1);
const historyEditing = ref(false);
const historyDraft = ref('');
const articleDialogVisible = ref(false);
const deviceHistoryDialogVisible = ref(false);
const candidateDialogVisible = ref(false);
const submittingArticle = ref(false);
const importingDeviceHistory = ref(false);
const submittingCandidate = ref(false);
const updatingArticleId = ref('');
const deletingCandidateKey = ref('');
const articleForm = ref({
  title: '',
  content: '',
  enabled: true,
});
const deviceHistoryForm = ref({
  sourceEntryId: '',
  enabled: true,
});
const candidateForm = ref({
  originalContext: '',
  originalPrefix: '',
  originalCandidate: '',
  candidate: '',
  weight: 1,
});

const currentDevice = computed(() => devices.value.find((device) => device.id === selectedEntryId.value) || null);
const hasDevices = computed(() => devices.value.length > 0);
const normalizedSearch = computed(() => searchText.value.trim().toLowerCase());
const articleSummary = computed(() => articlesState.value?.summary || unavailableArticles('').summary);
const historySummary = computed(() => historyState.value?.summary || unavailableHistoryArticle('').summary);
const lintSummary = computed(() => lintState.value?.summary || unavailableLint('').summary);
const historyPagination = computed(() => historyState.value?.pagination || null);
const articles = computed(() => articlesState.value?.articles || []);
const lintIssues = computed(() => lintState.value?.issues || []);
const lintIsStale = computed(() => Boolean(
  lintState.value
  && (lintLoadedSource.value !== lintSource.value || lintLoadedMode.value !== lintMode.value),
));
const canImportArticle = computed(() => Boolean(selectedEntryId.value && articlesState.value?.available));
const historySourceDevices = computed(() => devices.value.filter((device) => device.id !== selectedEntryId.value));
const canImportDeviceHistory = computed(() => Boolean(
  selectedEntryId.value
  && currentDevice.value?.mode === 'local'
  && articlesState.value?.available
  && historySourceDevices.value.length,
));
const historyHasDraftChanges = computed(() => historyDraft.value !== (historyState.value?.content || ''));

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

const refreshButtonLoading = computed(() => {
  if (activeView.value === 'lint') return loadingLint.value;
  if (activeView.value === 'articles') return loadingArticles.value;
  if (activeView.value === 'history') return loadingHistory.value;
  return loadingTree.value;
});

const refreshButtonText = computed(() => {
  if (activeView.value === 'index') return '更新索引';
  if (activeView.value === 'lint') return '检查';
  return '刷新';
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

const formatNumber = (value: number | null | undefined) => Number(value || 0).toLocaleString('zh-CN');

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

const formatHistoryRange = (firstSeen: string, lastSeen: string) => {
  if (!firstSeen && !lastSeen) return '无';
  if (firstSeen && lastSeen && firstSeen !== lastSeen) return `${firstSeen} 至 ${lastSeen}`;
  return firstSeen || lastSeen;
};

const candidateRowKey = (row: RimeContextPredictionRow) => `${row.context}\t${row.prefix}\t${row.candidate}`;
const articleHashShort = (value: string) => (value ? value.slice(0, 10) : '');
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
      ...(activeView.value === 'index' ? { view: undefined } : { view: activeView.value }),
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
    tree.value = await fetchRimeContextPredictionTree(selectedEntryId.value);
  } catch (err: any) {
    tree.value = unavailableTree(err.response?.data?.detail || err.message || '读取小狼毫输入法索引失败。');
  } finally {
    loadingTree.value = false;
  }
};

const loadArticles = async () => {
  if (!selectedEntryId.value) {
    articlesState.value = null;
    return;
  }
  loadingArticles.value = true;
  try {
    articlesState.value = await fetchRimeContextArticles(selectedEntryId.value);
  } catch (err: any) {
    articlesState.value = unavailableArticles(err.response?.data?.detail || err.message || '读取导入文章清单失败。');
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

const changeHistoryPage = async (page: number) => {
  if (loadingHistory.value || historyEditing.value) return;
  await loadHistoryArticle(Math.max(1, page));
};

const loadActiveView = async () => {
  if (activeView.value === 'lint') {
    await loadLint();
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
  selectedTailKey.value = '';
  selectedContextKey.value = '';
  selectedPrefixKey.value = '';
  syncRouteQuery();
  await loadActiveView();
};

const handleRefresh = async () => {
  if (activeView.value === 'lint') {
    await loadLint();
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
    tree.value = await refreshRimeContextPredictionTree(selectedEntryId.value);
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
    tree.value = await refreshRimeContextPredictionTree(selectedEntryId.value);
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

const openArticleDialog = () => {
  articleForm.value = {
    title: '',
    content: '',
    enabled: true,
  };
  articleDialogVisible.value = true;
};

const openDeviceHistoryDialog = () => {
  const firstSource = historySourceDevices.value[0];
  deviceHistoryForm.value = {
    sourceEntryId: firstSource?.id || '',
    enabled: true,
  };
  deviceHistoryDialogVisible.value = true;
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

const submitImportArticle = async () => {
  if (!selectedEntryId.value) return;
  if (!articleForm.value.content.trim()) {
    ElMessage.warning('文章内容不能为空');
    return;
  }
  submittingArticle.value = true;
  try {
    articlesState.value = await importRimeContextArticle(selectedEntryId.value, {
      title: articleForm.value.title.trim() || undefined,
      content: articleForm.value.content,
      enabled: articleForm.value.enabled,
    });
    articleDialogVisible.value = false;
    ElMessage.success('文章已导入');
    await loadTree();
  } catch (err: any) {
    ElMessage.error(err.response?.data?.detail || err.message || '导入文章失败');
  } finally {
    submittingArticle.value = false;
  }
};

const submitImportDeviceHistory = async () => {
  if (!selectedEntryId.value) return;
  if (!deviceHistoryForm.value.sourceEntryId) {
    ElMessage.warning('请选择来源设备');
    return;
  }
  importingDeviceHistory.value = true;
  try {
    articlesState.value = await importRimeContextDeviceHistory(selectedEntryId.value, {
      source_entry_id: deviceHistoryForm.value.sourceEntryId,
      enabled: deviceHistoryForm.value.enabled,
    });
    deviceHistoryDialogVisible.value = false;
    ElMessage.success(articlesState.value.message || '设备历史已同步');
    await loadTree();
  } catch (err: any) {
    ElMessage.error(err.response?.data?.detail || err.message || '同步设备历史失败');
  } finally {
    importingDeviceHistory.value = false;
  }
};

const handleArticleEnabledChange = async (article: RimeContextArticle, enabled: boolean) => {
  if (!selectedEntryId.value || updatingArticleId.value) return;
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
  if (!selectedEntryId.value || updatingArticleId.value) return;
  try {
    await ElMessageBox.confirm(`确定删除导入文章“${article.title}”吗？`, '删除导入文章', {
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
    ElMessage.success('文章已删除');
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
    tree.value = await deleteRimeContextCandidate(selectedEntryId.value, {
      context: row.context,
      prefix: row.prefix,
      candidate: row.candidate,
    });
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
    tree.value = await updateRimeContextCandidate(selectedEntryId.value, {
      original_context: candidateForm.value.originalContext,
      original_prefix: candidateForm.value.originalPrefix,
      original_candidate: candidateForm.value.originalCandidate,
      context: candidateForm.value.originalContext,
      prefix: candidateForm.value.originalPrefix,
      candidate,
      weight,
    });
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
</script>

<template>
  <div class="rime-page">
    <header class="rime-toolbar">
      <label class="rime-field rime-field-device">
        <span>设备</span>
        <el-select
          v-model="selectedEntryId"
          class="rime-select"
          placeholder="选择设备"
          :disabled="loadingDevices"
          @change="handleDeviceChange"
        >
          <el-option
            v-for="device in devices"
            :key="device.id"
            :label="device.name || device.device_id"
            :value="device.id"
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
          v-if="activeView === 'articles'"
          type="primary"
          :icon="Plus"
          :disabled="!canImportArticle"
          @click="openArticleDialog"
        >
          导入文章
        </el-button>
        <el-button
          v-if="activeView === 'articles'"
          :icon="Refresh"
          :loading="importingDeviceHistory"
          :disabled="!canImportDeviceHistory"
          title="把其他设备的输入历史同步为本机导入文章"
          aria-label="把其他设备的输入历史同步为本机导入文章"
          @click="openDeviceHistoryDialog"
        >
          拉取历史
        </el-button>
        <el-button
          v-if="activeView === 'history'"
          type="primary"
          :icon="Refresh"
          :loading="loadingTree"
          :disabled="!selectedEntryId || (historyEditing && historyHasDraftChanges)"
          title="从输入历史重建预测索引"
          aria-label="从输入历史重建预测索引"
          @click="handleUpdateIndex"
        >
          更新索引
        </el-button>
        <template v-if="activeView === 'history'">
          <el-button
            v-if="!historyEditing"
            :icon="Edit"
            :disabled="!historyState?.available"
            title="编辑输入历史正文"
            aria-label="编辑输入历史正文"
            @click="startHistoryEdit"
          >
            编辑
          </el-button>
          <template v-else>
            <el-button
              type="primary"
              :icon="Check"
              :loading="savingHistory"
              :disabled="!historyHasDraftChanges"
              title="保存输入历史修订稿"
              aria-label="保存输入历史修订稿"
              @click="saveHistoryEdit"
            >
              保存
            </el-button>
            <el-button
              :icon="Close"
              :disabled="savingHistory"
              title="取消编辑"
              aria-label="取消编辑"
              @click="cancelHistoryEdit"
            >
              取消
            </el-button>
          </template>
        </template>
        <el-button
          :icon="Refresh"
          :loading="refreshButtonLoading"
          :disabled="!selectedEntryId"
          :title="refreshButtonText"
          :aria-label="refreshButtonText"
          @click="handleRefresh"
        >
          {{ refreshButtonText }}
        </el-button>
        <el-tooltip effect="light" placement="bottom-end">
          <template #content>
            <div class="rime-help">
              这里读取每台设备自己的 Rime 用户目录；预测索引来自候选统计，输入历史来自上屏日志，不是系统级键盘监听。
            </div>
          </template>
          <button type="button" class="rime-help-button" aria-label="小狼毫输入法说明">
            <el-icon><QuestionFilled /></el-icon>
          </button>
        </el-tooltip>
      </div>
    </header>

    <section v-if="!hasDevices && !loadingDevices" class="rime-empty">
      当前没有可用设备，请先在设备任务里添加本机或远程设备。
    </section>

    <template v-else>
      <section class="rime-status">
        <el-tag size="small" :type="currentStatusType">{{ currentStatusText }}</el-tag>
        <span v-if="currentDevice">{{ currentDevice.name || currentDevice.device_id }}</span>
        <span v-if="currentDevice" class="muted">{{ deviceMeta(currentDevice) }}</span>
        <span v-if="activeView === 'index' && tree?.source" class="muted">来源 {{ tree.source }}</span>
        <span v-if="activeView === 'index' && tree?.updated_at" class="muted">更新 {{ formatDateTime(tree.updated_at) }}</span>
        <span v-if="activeView === 'history' && historyState?.source" class="muted">来源 {{ historyState.source }}</span>
        <span v-if="activeView === 'history' && historyState?.updated_at" class="muted">更新 {{ formatDateTime(historyState.updated_at) }}</span>
        <span v-if="activeView === 'lint' && lintIsStale" class="muted">参数已变更，点击检查后生效</span>
        <span v-else-if="activeView === 'lint' && lintState?.message" class="muted">{{ lintState.message }}</span>
      </section>

      <nav class="rime-view-tabs" aria-label="小狼毫输入法视图">
        <button
          type="button"
          :class="{ 'is-active': activeView === 'index' }"
          @click="switchView('index')"
        >
          预测索引
        </button>
        <button
          type="button"
          :class="{ 'is-active': activeView === 'history' }"
          @click="switchView('history')"
        >
          输入历史
        </button>
        <button
          type="button"
          :class="{ 'is-active': activeView === 'articles' }"
          @click="switchView('articles')"
        >
          导入文章
        </button>
        <button
          type="button"
          :class="{ 'is-active': activeView === 'lint' }"
          @click="switchView('lint')"
        >
          语料检查
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
              </template>
              <div v-else class="rime-pane-empty">没有可展示的预测索引。</div>
            </main>
          </section>
        </template>
      </template>

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
        <section class="rime-summary">
          <span><strong>文章</strong>{{ formatNumber(articleSummary.article_count) }}</span>
          <span><strong>启用</strong>{{ formatNumber(articleSummary.enabled_count) }}</span>
          <span><strong>贡献</strong>{{ formatNumber(articleSummary.contribution_count) }}</span>
          <span v-if="articlesState?.rime_dir" class="summary-path" :title="articlesState.rime_dir">
            <strong>目录</strong>{{ articlesState.rime_dir }}
          </span>
        </section>

        <section v-if="!articlesState?.available" class="rime-unavailable">
          <p>{{ articlesState?.message || '请选择设备查看导入文章清单。' }}</p>
        </section>

        <table v-else-if="articles.length" class="rime-article-table" aria-label="导入文章清单">
          <thead>
            <tr>
              <th>启用</th>
              <th>文章</th>
              <th>来源</th>
              <th>贡献</th>
              <th>字符</th>
              <th>状态</th>
              <th>更新时间</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="article in articles" :key="article.id">
              <td>
                <el-switch
                  :model-value="article.enabled"
                  :loading="updatingArticleId === article.id"
                  @change="(value: string | number | boolean) => handleArticleEnabledChange(article, Boolean(value))"
                />
              </td>
              <td class="article-title" :title="`${article.title}\n${articleHashShort(article.content_hash)}`">
                {{ article.title }}
              </td>
              <td class="article-source" :title="article.source_key || article.source_label">
                {{ article.source_label }}
              </td>
              <td>{{ formatNumber(article.row_count) }}</td>
              <td>{{ formatNumber(article.char_count) }}</td>
              <td>{{ article.status === 'ready' ? '已提炼' : article.status }}</td>
              <td>{{ formatDateTime(article.updated_at) }}</td>
              <td>
                <el-button
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

        <div v-else class="rime-pane-empty article-empty">
          还没有导入文章。
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
      title="导入文章"
      width="680px"
      destroy-on-close
    >
      <div class="article-dialog-body">
        <label class="article-dialog-field">
          <span>标题</span>
          <el-input v-model="articleForm.title" placeholder="留空则使用正文第一行" />
        </label>
        <label class="article-dialog-field">
          <span>正文</span>
          <el-input
            v-model="articleForm.content"
            type="textarea"
            :rows="12"
            resize="vertical"
            placeholder="粘贴要提炼的文章正文"
          />
        </label>
        <el-checkbox v-model="articleForm.enabled">导入后立即参与预测索引</el-checkbox>
      </div>
      <template #footer>
        <el-button @click="articleDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="submittingArticle" @click="submitImportArticle">
          导入
        </el-button>
      </template>
    </el-dialog>

    <el-dialog
      v-model="deviceHistoryDialogVisible"
      title="拉取设备历史"
      width="460px"
      destroy-on-close
    >
      <div class="article-dialog-body">
        <label class="article-dialog-field">
          <span>来源设备</span>
          <el-select
            v-model="deviceHistoryForm.sourceEntryId"
            class="rime-select"
            placeholder="选择来源设备"
            :disabled="importingDeviceHistory"
          >
            <el-option
              v-for="device in historySourceDevices"
              :key="device.id"
              :label="`${device.name || device.device_id} · ${deviceMeta(device)}`"
              :value="device.id"
            />
          </el-select>
        </label>
        <el-checkbox v-model="deviceHistoryForm.enabled">同步后立即参与预测索引</el-checkbox>
      </div>
      <template #footer>
        <el-button @click="deviceHistoryDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="importingDeviceHistory" @click="submitImportDeviceHistory">
          同步
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

.rime-field-device {
  width: 220px;
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

.rime-status,
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
.rime-article-table {
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
.rime-article-table td {
  padding: 7px 10px;
  border-bottom: 1px solid #e5e8ee;
  text-align: left;
  white-space: nowrap;
}

.rime-file-table th,
.rime-candidate-table th,
.rime-article-table th {
  color: #667085;
  font-weight: 500;
  background: #f1f4f8;
}

.rime-workspace {
  flex: 1;
  min-height: 0;
  display: grid;
  grid-template-columns: 300px minmax(0, 1fr);
}

.rime-context-pane {
  min-height: 0;
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
  overflow: auto;
  padding: 12px 14px 24px;
}

.rime-detail-head {
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
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
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
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
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

.rime-article-table {
  margin: 12px 14px 24px;
}

.article-title {
  max-width: 420px;
  overflow: hidden;
  text-overflow: ellipsis;
}

.article-source {
  max-width: 180px;
  color: #667085;
  overflow: hidden;
  text-overflow: ellipsis;
}

.article-empty {
  padding: 18px 14px;
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

  .rime-field-device,
  .rime-field-search {
    width: 100%;
  }

  .rime-actions {
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
