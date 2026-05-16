<template>
  <div class="background-tasks-page">
    <header class="page-header">
      <div>
        <h2>后台任务</h2>
        <div class="header-meta">
          <span :class="['queue-dot', status?.runner_running ? 'busy' : 'stopped']" />
          <span>{{ status?.runner_running ? '行为树运行中' : '行为树未启动' }}</span>
          <span v-if="status?.next_wake_at">下次唤醒：{{ formatDateTime(status.next_wake_at) }}</span>
          <span v-if="status?.runner_error" class="error-text">{{ status.runner_error }}</span>
          <span :class="['queue-dot', status?.queue.is_idle ? 'idle' : 'busy']" />
          <span>{{ status?.queue.is_idle ? '执行队列空闲' : '执行队列运行中' }}</span>
          <span v-if="status?.queue.running">当前：{{ status.queue.running.name }}</span>
          <span v-if="pendingCount">等待 {{ pendingCount }} 项</span>
        </div>
      </div>
      <el-button :icon="Refresh" :loading="manualRefreshing" @click="loadStatus()">刷新</el-button>
    </header>

    <el-table
      v-loading="tableLoading"
      :data="tasks"
      row-key="key"
      table-layout="auto"
      :fit="false"
      class="tasks-table"
      @row-contextmenu="handleTaskRowContextMenu"
    >
      <el-table-column label="任务" min-width="250">
        <template #default="{ row }">
          <div class="task-name">
            <span>{{ row.title }}</span>
            <el-tag size="small" effect="plain">{{ row.category }}</el-tag>
          </div>
          <div class="task-desc">{{ row.description }}</div>
        </template>
      </el-table-column>

      <el-table-column label="调度" width="230">
        <template #default="{ row }">
          <div class="schedule-cell">
            <el-switch
              v-model="row.enabled"
              :loading="togglingKey === row.key"
              @change="handleToggle(row, $event)"
              inline-prompt
              active-text="启用"
              inactive-text="停用"
            />
            <div class="schedule-text">
              <span>{{ row.schedule_label || '-' }}</span>
              <small v-if="row.retry_policy">{{ row.retry_policy }}</small>
            </div>
          </div>
        </template>
      </el-table-column>

      <el-table-column label="下次运行" width="170">
        <template #default="{ row }">
          <span class="muted">{{ formatDateTime(row.next_run_at) }}</span>
        </template>
      </el-table-column>

      <el-table-column label="最近状态" min-width="210">
        <template #default="{ row }">
          <div class="run-status">
            <el-tag size="small" :type="statusType(row.latest_run?.status)" effect="plain">
              {{ statusLabel(row.latest_run?.status) }}
            </el-tag>
            <span>{{ row.latest_run?.stage_label || row.latest_run?.error_message || latestRunText(row) }}</span>
          </div>
          <div v-if="row.latest_run" class="run-meta">
            {{ formatTimestamp(row.latest_run.finished_at || row.latest_run.updated_at || row.latest_run.started_at || row.latest_run.created_at) }}
          </div>
        </template>
      </el-table-column>

      <el-table-column label="结果" width="190">
        <template #default="{ row }">
          <div v-if="row.key === 'auto_git_commit' && row.latest_run" class="compact-result">
            变更 {{ row.latest_run.changed_repo_count || 0 }}，
            提交 {{ row.latest_run.committed_repo_count || 0 }}，
            失败 {{ row.latest_run.failed_repo_count || 0 }}
          </div>
          <div v-else-if="row.key === 'note_metadata_feedback_optimization' && row.latest_run" class="compact-result">
            样本 {{ row.latest_run.sample_count || 0 }}
          </div>
          <div v-else-if="row.key === 'codex_diary_yesterday_import' && row.latest_run" class="compact-result">
            节点 {{ row.latest_run.created_note_count || 0 }}，
            记录 {{ row.latest_run.source_turn_count || 0 }}
          </div>
          <span v-else class="muted">-</span>
        </template>
      </el-table-column>

      <el-table-column label="操作" width="110" fixed="right">
        <template #default="{ row }">
          <div class="action-buttons">
            <el-button
              size="small"
              type="primary"
              plain
              :icon="VideoPlay"
              :disabled="row.active || !row.can_trigger"
              :loading="triggeringKey === row.key"
              @click="handleTrigger(row)"
            >
              执行
            </el-button>
          </div>
        </template>
      </el-table-column>
    </el-table>

    <section class="recent-section">
      <div class="section-title">队列记录</div>
      <div class="queue-record-groups">
        <div class="queue-record-group">
          <div class="queue-record-title">将要运行</div>
          <div v-if="upcomingQueueItems.length" class="recent-list">
            <div
              v-for="item in upcomingQueueItems"
              :key="queueItemKey(item)"
              :class="['recent-row', { deleting: item.queueItem?.id === deletingQueueItemId, actionable: item.queueItem }]"
              @contextmenu.prevent.stop="handleQueueRecordContextMenu(item, $event)"
            >
              <span>{{ item.name }}</span>
              <el-tag v-if="shouldShowQueueRecordTag(item)" size="small" :type="statusType(item.status)" effect="plain">
                {{ statusLabel(item.status) }}
              </el-tag>
              <span class="muted">{{ item.timeText }}</span>
            </div>
          </div>
          <div v-else class="empty-queue-text">暂无可计算的下次运行</div>
        </div>

        <div class="queue-record-group">
          <div class="queue-record-title">最近完成</div>
          <div v-if="recentQueueItems.length" class="recent-list">
            <div
              v-for="item in recentQueueItems"
              :key="queueItemKey(item)"
              :class="['recent-row', { deleting: item.queueItem?.id === deletingQueueItemId, actionable: item.queueItem }]"
              @contextmenu.prevent.stop="handleQueueRecordContextMenu(item, $event)"
            >
              <span>{{ item.name }}</span>
              <el-tag v-if="shouldShowQueueRecordTag(item)" size="small" :type="statusType(item.status)" effect="plain">
                {{ statusLabel(item.status) }}
              </el-tag>
              <span class="muted">{{ item.timeText }}</span>
            </div>
          </div>
          <div v-else class="empty-queue-text">暂无完成记录</div>
        </div>
      </div>
    </section>

    <div
      v-if="contextMenu.visible"
      class="task-context-menu"
      :style="{ left: `${contextMenu.x}px`, top: `${contextMenu.y}px` }"
      @click.stop
      @contextmenu.prevent
    >
      <button type="button" class="context-menu-item danger" @click="handleContextDelete">
        <el-icon><Delete /></el-icon>
        <span>删除</span>
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import { Delete, Refresh, VideoPlay } from '@element-plus/icons-vue';
import {
  deleteBackgroundTask,
  deleteBackgroundQueueTask,
  fetchBackgroundTaskStatus,
  triggerBackgroundTask,
  toggleBackgroundTask,
  type BackgroundTaskItem,
  type BackgroundTaskRunSummary,
  type BackgroundTaskStatusResponse,
} from '@/api/admin';

type TagType = 'success' | 'warning' | 'info' | 'danger' | 'primary';
type ContextMenuTarget =
  | { kind: 'task'; item: BackgroundTaskItem }
  | { kind: 'queue'; item: BackgroundTaskRunSummary };
interface QueueRecordItem {
  id: string
  name: string
  status: string
  timestamp: number
  timeText: string
  queueItem?: BackgroundTaskRunSummary
}

const status = ref<BackgroundTaskStatusResponse | null>(null);
const initialLoading = ref(false);
const manualRefreshing = ref(false);
const triggeringKey = ref('');
const togglingKey = ref('');
const deletingTaskKey = ref('');
const deletingQueueItemId = ref('');
const contextMenu = ref<{
  visible: boolean;
  x: number;
  y: number;
  target: ContextMenuTarget | null;
}>({
  visible: false,
  x: 0,
  y: 0,
  target: null,
});
let refreshTimer = 0;
let silentRefreshRunning = false;
let latestStatusRequestId = 0;

const tableLoading = computed(() => initialLoading.value && !status.value);
const tasks = computed(() => status.value?.tasks || []);
const pendingCount = computed(() => status.value?.queue.pending?.length || 0);
const sortQueueRecords = (items: QueueRecordItem[], direction: 'asc' | 'desc') => [...items].sort((left, right) => {
  const diff = left.timestamp - right.timestamp;
  return direction === 'asc' ? diff : -diff;
});
const timestampFromDateTime = (value?: string | null) => {
  if (!value) return 0;
  const parsed = new Date(value).getTime();
  return Number.isNaN(parsed) ? 0 : Math.floor(parsed / 1000);
};
const queueRunTimestamp = (item: BackgroundTaskRunSummary) => (
  item.finished_at || item.started_at || item.queued_at || item.created_at || item.updated_at || 0
);
const toQueueRunRecord = (item: BackgroundTaskRunSummary): QueueRecordItem => {
  const timestamp = queueRunTimestamp(item);
  return {
    id: item.id || `${item.name || 'queue'}-${timestamp}`,
    name: item.name || '-',
    status: item.status || '',
    timestamp,
    timeText: formatTimestamp(timestamp),
    queueItem: item,
  };
};
const shouldShowQueueRecordTag = (item: QueueRecordItem) => item.status !== 'scheduled';
const upcomingQueueItems = computed(() => {
  const running = status.value?.queue.running;
  const runningRecords = running ? [toQueueRunRecord(running)] : [];
  const pendingRecords = (status.value?.queue.pending || []).map((item) => {
    const timestamp = item.queued_at || item.created_at || 0;
    return {
      id: item.id || `${item.name || 'pending'}-${timestamp}`,
      name: item.name || '-',
      status: item.status || 'pending',
      timestamp,
      timeText: formatTimestamp(timestamp),
      queueItem: item,
    };
  });
  const scheduledRecords = tasks.value
    .filter((item) => item.enabled && item.next_run_at && !item.active)
    .map((item) => {
      const timestamp = timestampFromDateTime(item.next_run_at);
      return {
        id: `scheduled-${item.key}`,
        name: item.title,
        status: 'scheduled',
        timestamp,
        timeText: formatDateTime(item.next_run_at),
      };
    });
  return sortQueueRecords([...runningRecords, ...pendingRecords, ...scheduledRecords], 'asc').slice(0, 10);
});
const recentQueueItems = computed(() => sortQueueRecords(
  (status.value?.queue.recent || []).map(toQueueRunRecord),
  'desc',
).slice(0, 10));
const queueItemKey = (item: QueueRecordItem) => item.id;

const loadStatus = async (options: { silent?: boolean } = {}) => {
  const silent = options.silent === true;
  if (silent) {
    if (silentRefreshRunning || initialLoading.value || manualRefreshing.value) return;
    silentRefreshRunning = true;
  }

  const requestId = ++latestStatusRequestId;
  if (!silent) {
    if (status.value) {
      manualRefreshing.value = true;
    } else {
      initialLoading.value = true;
    }
  }

  try {
    const nextStatus = await fetchBackgroundTaskStatus();
    if (requestId === latestStatusRequestId) {
      status.value = nextStatus;
    }
  } catch (error) {
    console.error(error);
    if (!silent) {
      ElMessage.error('后台任务状态读取失败');
    }
  } finally {
    if (!silent) {
      initialLoading.value = false;
      manualRefreshing.value = false;
    } else {
      silentRefreshRunning = false;
    }
  }
};

const statusLabel = (value?: string) => {
  const key = value || '';
  if (key === 'completed') return '完成';
  if (key === 'running') return '运行中';
  if (key === 'pending') return '等待';
  if (key === 'scheduled') return '计划';
  if (key === 'failed') return '失败';
  if (key === 'skipped') return '跳过';
  if (key === 'clean') return '无变更';
  return key || '暂无';
};

const statusType = (value?: string): TagType => {
  if (value === 'completed' || value === 'clean') return 'success';
  if (value === 'running' || value === 'pending') return 'primary';
  if (value === 'scheduled') return 'info';
  if (value === 'failed') return 'danger';
  if (value === 'skipped') return 'info';
  return 'info';
};

const formatTimestamp = (value?: number | null) => {
  if (!value) return '';
  return new Date(value * 1000).toLocaleString();
};

const formatDateTime = (value?: string | null) => {
  if (!value) return '-';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString();
};

const latestRunText = (row: BackgroundTaskItem) => {
  if (row.active) return '已进入队列';
  return '暂无运行记录';
};

const handleTrigger = async (row: BackgroundTaskItem) => {
  const warning = row.trigger_warning || '将立即把该任务加入后台队列。';
  try {
    await ElMessageBox.confirm(warning, `执行：${row.title}`, {
      confirmButtonText: '执行',
      cancelButtonText: '取消',
      type: 'warning',
    });
  } catch {
    return;
  }

  triggeringKey.value = row.key;
  try {
    await triggerBackgroundTask(row.key);
    ElMessage.success('已加入后台队列');
    await loadStatus();
  } catch (error: any) {
    console.error(error);
    ElMessage.error(error?.response?.data?.detail || '任务触发失败');
  } finally {
    triggeringKey.value = '';
  }
};

const handleToggle = async (row: BackgroundTaskItem, val: string | number | boolean) => {
  const enabled = Boolean(val);
  togglingKey.value = row.key;
  try {
    await toggleBackgroundTask(row.key, enabled);
    ElMessage.success(`已${enabled ? '启用' : '停用'}`);
    await loadStatus();
  } catch (error: any) {
    console.error(error);
    row.enabled = !enabled; // revert
    ElMessage.error(error?.response?.data?.detail || '操作失败');
  } finally {
    togglingKey.value = '';
  }
};

const openContextMenu = (target: ContextMenuTarget, event: MouseEvent) => {
  event.preventDefault();
  event.stopPropagation();
  contextMenu.value = {
    visible: true,
    x: event.clientX,
    y: event.clientY,
    target,
  };
};

const closeContextMenu = () => {
  contextMenu.value.visible = false;
  contextMenu.value.target = null;
};

const handleTaskRowContextMenu = (row: BackgroundTaskItem, _column: unknown, event: MouseEvent) => {
  openContextMenu({ kind: 'task', item: row }, event);
};

const handleQueueItemContextMenu = (item: BackgroundTaskRunSummary, event: MouseEvent) => {
  openContextMenu({ kind: 'queue', item }, event);
};

const handleQueueRecordContextMenu = (item: QueueRecordItem, event: MouseEvent) => {
  if (!item.queueItem) return;
  handleQueueItemContextMenu(item.queueItem, event);
};

const handleContextDelete = () => {
  const target = contextMenu.value.target;
  closeContextMenu();
  if (!target) return;
  if (target.kind === 'task') {
    void handleDeleteTask(target.item);
  } else {
    void handleDeleteQueueItem(target.item);
  }
};

const handleDeleteTask = async (row: BackgroundTaskItem) => {
  if (!row.key || deletingTaskKey.value) return;
  try {
    await ElMessageBox.confirm(
      '删除后会停用并隐藏该任务，不会中断已经在运行的队列任务。',
      `删除：${row.title}`,
      {
        confirmButtonText: '删除',
        cancelButtonText: '取消',
        type: 'warning',
      },
    );
  } catch {
    return;
  }

  deletingTaskKey.value = row.key;
  try {
    await deleteBackgroundTask(row.key);
    ElMessage.success('已删除任务');
    await loadStatus();
  } catch (error: any) {
    console.error(error);
    ElMessage.error(error?.response?.data?.detail || '删除失败');
  } finally {
    deletingTaskKey.value = '';
  }
};

const handleDeleteQueueItem = async (item: BackgroundTaskRunSummary) => {
  if (!item.id || deletingQueueItemId.value) return;
  const isPending = item.status === 'pending';
  const message = isPending ? '删除后不会继续执行。' : '只删除这条队列记录，不影响已完成结果。';
  try {
    await ElMessageBox.confirm(message, `删除：${item.name || item.id}`, {
      confirmButtonText: '删除',
      cancelButtonText: '取消',
      type: 'warning',
    });
  } catch {
    return;
  }

  deletingQueueItemId.value = item.id;
  try {
    await deleteBackgroundQueueTask(item.id);
    ElMessage.success(isPending ? '已删除等待任务' : '已删除队列记录');
    await loadStatus();
  } catch (error: any) {
    console.error(error);
    ElMessage.error(error?.response?.data?.detail || '删除失败');
  } finally {
    deletingQueueItemId.value = '';
  }
};

onMounted(() => {
  loadStatus();
  refreshTimer = window.setInterval(() => {
    if (!status.value || !status.value.queue.is_idle || status.value.tasks.some((item) => item.active)) {
      loadStatus({ silent: true });
    }
  }, 3000);
  window.addEventListener('click', closeContextMenu);
  window.addEventListener('scroll', closeContextMenu, true);
});

onBeforeUnmount(() => {
  if (refreshTimer) {
    window.clearInterval(refreshTimer);
  }
  window.removeEventListener('click', closeContextMenu);
  window.removeEventListener('scroll', closeContextMenu, true);
});
</script>

<style scoped>
.background-tasks-page {
  padding: 20px 24px 28px;
}

.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 16px;
}

.page-header h2 {
  margin: 0 0 8px;
  font-size: 20px;
  line-height: 1.3;
}

.header-meta {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
  color: #606266;
  font-size: 13px;
}

.queue-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
}

.queue-dot.idle {
  background: #67c23a;
}

.queue-dot.busy {
  background: #409eff;
}

.queue-dot.stopped {
  background: #c0c4cc;
}

.error-text {
  color: #f56c6c;
}

.tasks-table {
  width: 100%;
}

.task-name {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
  color: #1f2d3d;
}

.task-desc,
.run-meta,
.muted {
  color: #909399;
  font-size: 12px;
}

.task-desc {
  margin-top: 5px;
  line-height: 1.5;
}

.schedule-cell {
  display: flex;
  align-items: center;
  gap: 8px;
}

.schedule-text {
  display: flex;
  flex-direction: column;
  gap: 2px;
  font-size: 12px;
  color: #606266;
  line-height: 1.35;
}

.schedule-text small {
  color: #a8abb2;
}

.run-status {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.action-buttons {
  display: flex;
  align-items: center;
  gap: 8px;
}

.run-status span:last-child {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.compact-result {
  color: #606266;
  font-size: 13px;
}

.recent-section {
  margin-top: 18px;
  border-top: 1px solid #ebeef5;
  padding-top: 14px;
}

.section-title {
  margin-bottom: 8px;
  font-weight: 600;
}

.queue-record-groups {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(260px, max-content));
  gap: 24px;
  align-items: start;
}

.queue-record-title {
  margin-bottom: 6px;
  color: #606266;
  font-size: 13px;
  font-weight: 600;
}

.empty-queue-text {
  color: #a8abb2;
  font-size: 13px;
  line-height: 28px;
}

.recent-list {
  display: grid;
  gap: 6px;
}

.recent-row {
  display: flex;
  align-items: center;
  gap: 10px;
  width: fit-content;
  min-height: 28px;
  padding: 0 4px;
  border-radius: 4px;
  color: #303133;
  font-size: 13px;
}

.recent-row:hover {
  background: #f5f7fa;
}

.recent-row.actionable {
  cursor: context-menu;
}

.recent-row.deleting {
  opacity: 0.55;
}

.task-context-menu {
  position: fixed;
  z-index: 3000;
  min-width: 112px;
  padding: 6px;
  border: 1px solid #dcdfe6;
  border-radius: 6px;
  background: #fff;
  box-shadow: 0 8px 22px rgb(0 0 0 / 14%);
}

.context-menu-item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  min-height: 30px;
  padding: 0 10px;
  border: 0;
  border-radius: 4px;
  background: transparent;
  color: #303133;
  font-size: 13px;
  text-align: left;
  cursor: pointer;
}

.context-menu-item:hover {
  background: #f5f7fa;
}

.context-menu-item.danger {
  color: #f56c6c;
}
</style>
