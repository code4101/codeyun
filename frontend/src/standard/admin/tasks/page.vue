<template>
  <div class="background-tasks-page">
    <header class="page-header">
      <div>
        <h2>后台任务</h2>
        <div class="header-meta">
          <span :class="['queue-dot', status?.queue.is_idle ? 'idle' : 'busy']" />
          <span>{{ status?.queue.is_idle ? '队列空闲' : '队列运行中' }}</span>
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

      <el-table-column label="计划" width="170">
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
            <code>{{ row.cron_expression || '-' }}</code>
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

      <el-table-column label="操作" width="120" fixed="right">
        <template #default="{ row }">
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
        </template>
      </el-table-column>
    </el-table>

    <section v-if="recentItems.length" class="recent-section">
      <div class="section-title">队列记录</div>
      <div class="recent-list">
        <div v-for="item in recentItems" :key="item.id" class="recent-row">
          <span>{{ item.name }}</span>
          <el-tag size="small" :type="statusType(item.status)" effect="plain">{{ statusLabel(item.status) }}</el-tag>
          <span class="muted">{{ formatTimestamp(item.finished_at || item.started_at || item.queued_at) }}</span>
        </div>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import { Refresh, VideoPlay } from '@element-plus/icons-vue';
import {
  fetchBackgroundTaskStatus,
  triggerBackgroundTask,
  toggleBackgroundTask,
  type BackgroundTaskItem,
  type BackgroundTaskStatusResponse,
} from '@/api/admin';

type TagType = 'success' | 'warning' | 'info' | 'danger' | 'primary';

const status = ref<BackgroundTaskStatusResponse | null>(null);
const initialLoading = ref(false);
const manualRefreshing = ref(false);
const triggeringKey = ref('');
const togglingKey = ref('');
let refreshTimer = 0;
let silentRefreshRunning = false;
let latestStatusRequestId = 0;

const tableLoading = computed(() => initialLoading.value && !status.value);
const tasks = computed(() => status.value?.tasks || []);
const pendingCount = computed(() => status.value?.queue.pending?.length || 0);
const recentItems = computed(() => (status.value?.queue.recent || []).slice(0, 8));

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
  if (key === 'failed') return '失败';
  if (key === 'skipped') return '跳过';
  if (key === 'clean') return '无变更';
  return key || '暂无';
};

const statusType = (value?: string): TagType => {
  if (value === 'completed' || value === 'clean') return 'success';
  if (value === 'running' || value === 'pending') return 'primary';
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

onMounted(() => {
  loadStatus();
  refreshTimer = window.setInterval(() => {
    if (!status.value || !status.value.queue.is_idle || status.value.tasks.some((item) => item.active)) {
      loadStatus({ silent: true });
    }
  }, 3000);
});

onBeforeUnmount(() => {
  if (refreshTimer) {
    window.clearInterval(refreshTimer);
  }
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

.schedule-cell code {
  padding: 1px 5px;
  border-radius: 4px;
  background: #f5f7fa;
  color: #606266;
  font-size: 12px;
}

.run-status {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
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

.recent-list {
  display: grid;
  gap: 6px;
}

.recent-row {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 28px;
  color: #303133;
  font-size: 13px;
}
</style>
