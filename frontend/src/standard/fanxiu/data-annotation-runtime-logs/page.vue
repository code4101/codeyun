<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { getFanxiuDataAnnotationRuntimeLogs, type FanxiuDataAnnotationRuntimeLogEntry } from '@/api/fanxiu';

const route = useRoute();
const router = useRouter();

const entryId = computed(() => String(route.query.entry_id || ''));
const scope = computed(() => String(route.query.scope || ''));
const itemId = computed(() => String(route.query.item_id || ''));
const title = computed(() => String(route.query.title || itemId.value || '日志'));
type RuntimeLogRow = FanxiuDataAnnotationRuntimeLogEntry & { _uiKey: string };
const LOG_LIMIT = 1000;
const logs = ref<RuntimeLogRow[]>([]);
const loading = ref(false);
const loaded = ref(false);
let pollTimer: number | null = null;

const scopeText = computed(() => {
  if (scope.value === 'guard') return '守护';
  if (scope.value === 'manual_job') return '作业';
  if (scope.value === 'job') return '作业';
  return '运行';
});

const logSourceText = (entry: FanxiuDataAnnotationRuntimeLogEntry) => {
  const file = String(entry.source_file || '').trim();
  const line = entry.source_line ? `:${entry.source_line}` : '';
  const expr = String(entry.source_expr || '').trim();
  return file && expr ? `${file}${line}  ${expr}` : '';
};

const logSignature = (entry: FanxiuDataAnnotationRuntimeLogEntry) => [
  entry.time,
  entry.kind,
  entry.scope || '',
  entry.item_id || '',
  entry.message,
  entry.action || '',
  entry.source_file || '',
  entry.source_line ?? '',
  entry.source_expr || '',
].join('\u001f');

const buildLogRows = (entries: FanxiuDataAnnotationRuntimeLogEntry[]) => {
  const seen = new Map<string, number>();
  return entries.map((entry) => {
    const signature = logSignature(entry);
    const index = seen.get(signature) || 0;
    seen.set(signature, index + 1);
    return { ...entry, _uiKey: `${signature}\u001f${index}` };
  }).reverse();
};

const sameLogWindow = (nextLogs: RuntimeLogRow[]) => {
  if (nextLogs.length !== logs.value.length) return false;
  return nextLogs.every((entry, index) => entry._uiKey === logs.value[index]?._uiKey);
};

const mergeLogRows = (nextLogs: RuntimeLogRow[]) => {
  if (sameLogWindow(nextLogs)) return;
  const currentByKey = new Map(logs.value.map(entry => [entry._uiKey, entry]));
  logs.value = nextLogs.map(entry => currentByKey.get(entry._uiKey) || entry);
};

const refreshLogs = async (options: { silent?: boolean } = {}) => {
  if (!options.silent && !loaded.value) {
    loading.value = true;
  }
  try {
    const response = await getFanxiuDataAnnotationRuntimeLogs(LOG_LIMIT, scope.value, itemId.value);
    mergeLogRows(buildLogRows(response.entries || []));
  } finally {
    loaded.value = true;
    loading.value = false;
  }
};

const backToRuntime = () => {
  void router.push({ path: '/fanxiu/data-annotation/runtime', query: entryId.value ? { entry_id: entryId.value } : {} });
};

const startPolling = () => {
  if (pollTimer !== null) return;
  pollTimer = window.setInterval(() => {
    void refreshLogs({ silent: true });
  }, 30000);
};

const stopPolling = () => {
  if (pollTimer === null) return;
  window.clearInterval(pollTimer);
  pollTimer = null;
};

onMounted(async () => {
  await refreshLogs();
  startPolling();
});

onUnmounted(stopPolling);
</script>

<template>
  <div class="runtime-log-page">
    <header class="log-header">
      <button type="button" class="back-button" @click="backToRuntime">返回</button>
      <div>
        <h2>{{ title }}</h2>
        <p>{{ scopeText }} · {{ itemId || '-' }} · {{ logs.length }} 条</p>
      </div>
    </header>

    <main class="log-main" v-loading="loading">
      <div class="log-list">
        <div v-for="entry in logs" :key="entry._uiKey" class="log-row" :class="`is-${entry.kind}`">
          <span>{{ entry.time }}</span>
          <b>{{ entry.kind }}</b>
          <p>
            <code v-if="logSourceText(entry)">{{ logSourceText(entry) }}</code>
            <span>{{ entry.message }}</span>
          </p>
        </div>
        <div v-if="!logs.length" class="empty-row">暂无日志</div>
      </div>
    </main>
  </div>
</template>

<style scoped>
.runtime-log-page {
  min-height: 100%;
  background: #f5f7fa;
  color: #1f2937;
}

.log-header {
  min-height: 72px;
  padding: 12px 18px;
  display: flex;
  align-items: center;
  gap: 12px;
  border-bottom: 1px solid #e5e7eb;
  background: #fff;
}

.log-header h2 {
  margin: 0;
  font-size: 18px;
  line-height: 1.25;
}

.log-header p {
  margin: 4px 0 0;
  color: #6b7280;
  font-size: 12px;
}

.back-button {
  height: 30px;
  padding: 0 10px;
  border: 1px solid #d1d5db;
  border-radius: 6px;
  background: #fff;
  color: #374151;
  cursor: pointer;
}

.log-main {
  padding: 14px 18px 28px;
}

.log-list {
  max-height: calc(100vh - 118px);
  overflow: auto;
  border: 1px solid #edf2f7;
  background: #fff;
}

.log-row {
  display: grid;
  grid-template-columns: 72px 64px minmax(0, 1fr);
  gap: 8px;
  min-height: 32px;
  padding: 7px 10px;
  border-bottom: 1px solid #f1f5f9;
  font-size: 12px;
}

.log-row span,
.log-row b {
  color: #6b7280;
  font-weight: 400;
}

.log-row p {
  min-width: 0;
  margin: 0;
  word-break: break-all;
}

.log-row code {
  display: inline-block;
  margin-right: 10px;
  color: #0f766e;
  font-family: Consolas, 'Courier New', monospace;
  font-size: 12px;
  word-break: break-all;
}

.log-row.is-error {
  background: #fef2f2;
}

.log-row.is-success {
  background: #f0fdf4;
}

.empty-row {
  min-height: 34px;
  display: flex;
  align-items: center;
  color: #9ca3af;
  font-size: 13px;
  padding: 0 10px;
}
</style>
