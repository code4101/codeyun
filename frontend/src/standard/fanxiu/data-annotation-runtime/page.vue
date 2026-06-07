<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { ElMessage } from 'element-plus';
import { QuestionFilled } from '@element-plus/icons-vue';
import { taskStore } from '@/store/taskStore';
import {
  getFanxiuDataAnnotationRuntimeLogs,
  getFanxiuDataAnnotationRuntimeStatus,
  getFanxiuDataAnnotationSchedulerPlan,
  getFanxiuDataAnnotationSchedulerTasks,
  saveFanxiuDataAnnotationSchedulerTasks,
  setFanxiuDataAnnotationRuntimeGuard,
  type FanxiuDataAnnotationRuntimeLogEntry,
  type FanxiuDataAnnotationRuntimeGuardItem,
  type FanxiuDataAnnotationRuntimeStatus,
  type FanxiuDataAnnotationSchedulerPlanResponse,
  type FanxiuDataAnnotationSchedulerTaskItem,
} from '@/api/fanxiu';

const route = useRoute();
const router = useRouter();

const entryId = ref(String(route.query.entry_id || ''));
const runtimeStatus = ref<FanxiuDataAnnotationRuntimeStatus | null>(null);
const schedulerTasks = ref<FanxiuDataAnnotationSchedulerTaskItem[]>([]);
const schedulerPlan = ref<FanxiuDataAnnotationSchedulerPlanResponse | null>(null);
const logs = ref<FanxiuDataAnnotationRuntimeLogEntry[]>([]);
const loading = ref(false);
const actionLoading = ref('');
const contextMenu = ref({
  visible: false,
  x: 0,
  y: 0,
  scope: '',
  itemId: '',
  title: '',
});
let pollTimer: number | null = null;

const devices = computed(() => taskStore.devices);
const guardEnabled = computed(() => Boolean(runtimeStatus.value?.guard_enabled));
const guardItemEnabled = (guardId: string) => Boolean(runtimeStatus.value?.guard_items?.[guardId]?.enabled);
const machineName = 'codepc_mf';
const currentSceneText = computed(() => {
  const scene = runtimeStatus.value?.current_scene;
  return typeof scene === 'number' ? `#${scene}` : '-';
});
const runtimeStateText = computed(() => {
  if (!runtimeStatus.value) return '未连接';
  if (runtimeStatus.value.running) return '运行中';
  if (runtimeStatus.value.status === 'stopping') return '停止中';
  if (runtimeStatus.value.service_running) return '空转';
  return '空转';
});
const runtimeMessage = computed(() => runtimeStatus.value?.message || '-');
const runtimePhaseText = computed(() => runtimeStatus.value?.phase || runtimeStatus.value?.task_type || '-');
const taskProgressText = computed(() => {
  const status = runtimeStatus.value;
  if (!status || !status.total) return '';
  return `${status.current_index}/${status.total}`;
});
const guardItems = computed<FanxiuDataAnnotationRuntimeGuardItem[]>(() => {
  const items = runtimeStatus.value?.guard_items || {};
  return Object.values(items).map((item) => ({
    ...item,
    id: item.id || '',
    label: item.label || item.id || '未命名守护',
    enabled: guardItemEnabled(item.id || ''),
    message: item.message || '-',
  }));
});
const isBusinessTask = (task: FanxiuDataAnnotationSchedulerTaskItem) => {
  if (['go_scene', 'hide_floating_window'].includes(task.task_type)) return false;
  const label = task.label || '';
  if (/到.*#\d+|隐藏浮动窗|到世界|到设置页/.test(label)) return false;
  return true;
};

const taskGroupRank = (task: FanxiuDataAnnotationSchedulerTaskItem) => {
  const ranks: Record<string, number> = { daily: 10, dynamic: 20, manual: 30 };
  return ranks[task.schedule_kind || ''] ?? 90;
};

const taskTriggerValue = (task: FanxiuDataAnnotationSchedulerTaskItem) => {
  const exact = parseRuntimeTime(task.retry_after || task.next_time || '');
  if (exact) return exact.getTime();
  const clock = [...(task.schedule_times || [])].filter(Boolean).sort()[0] || '';
  return clock ? Date.parse(`1970-01-01T${clock}`) : 0;
};

const businessTasks = computed(() => schedulerTasks.value
  .filter((task) => task.supported && isBusinessTask(task))
  .sort((a, b) => (
    taskGroupRank(a) - taskGroupRank(b)
    || taskTriggerValue(a) - taskTriggerValue(b)
    || String(a.label || a.id).localeCompare(String(b.label || b.id), 'zh-CN')
  )));

const taskMetaText = (task: FanxiuDataAnnotationSchedulerTaskItem) => {
  const labels: Record<string, string> = {
    daily: '每日',
    dynamic: '动态',
    manual: '手动',
  };
  return labels[task.schedule_kind || ''] || task.source || '任务';
};

const pad2 = (value: number) => String(value).padStart(2, '0');

const parseRuntimeTime = (value: string) => {
  const text = String(value || '').trim();
  if (!text) return null;
  const date = new Date(text.replace(' ', 'T'));
  return Number.isFinite(date.getTime()) ? date : null;
};

const formatRuntimeTime = (value: string) => {
  const date = parseRuntimeTime(value);
  if (!date) return value;
  const now = new Date();
  const time = `${pad2(date.getHours())}:${pad2(date.getMinutes())}`;
  const isSameDate = date.getFullYear() === now.getFullYear()
    && date.getMonth() === now.getMonth()
    && date.getDate() === now.getDate();
  const isWithinNext24Hours = date.getTime() >= now.getTime()
    && date.getTime() - now.getTime() < 24 * 60 * 60 * 1000;
  if (isSameDate || isWithinNext24Hours) return time;
  const monthDayTime = `${pad2(date.getMonth() + 1)}-${pad2(date.getDate())} ${time}`;
  if (date.getFullYear() === now.getFullYear()) return monthDayTime;
  return `${date.getFullYear()}-${monthDayTime}`;
};

const nextTriggerText = (task: FanxiuDataAnnotationSchedulerTaskItem) => {
  if (task.retry_after) return `重试 ${formatRuntimeTime(task.retry_after)}`;
  if (task.next_time) return formatRuntimeTime(task.next_time);
  return '';
};

const nextTriggerTitle = (task: FanxiuDataAnnotationSchedulerTaskItem) => task.retry_after || task.next_time || '';

const openLogMenu = (event: MouseEvent, scope: string, itemId: string, title: string) => {
  contextMenu.value = {
    visible: true,
    x: event.clientX,
    y: event.clientY,
    scope,
    itemId,
    title,
  };
};

const closeLogMenu = () => {
  contextMenu.value.visible = false;
};

const openContextLogs = () => {
  const menu = contextMenu.value;
  closeLogMenu();
  void router.push({
    path: '/fanxiu/data-annotation/runtime/logs',
    query: {
      entry_id: entryId.value,
      scope: menu.scope,
      item_id: menu.itemId,
      title: menu.title,
    },
  });
};

const applyStatus = (status: FanxiuDataAnnotationRuntimeStatus) => {
  runtimeStatus.value = status;
};

const refreshStatus = async () => {
  const status = await getFanxiuDataAnnotationRuntimeStatus(entryId.value);
  applyStatus(status);
};

const refreshLogs = async () => {
  const response = await getFanxiuDataAnnotationRuntimeLogs(500);
  logs.value = response.entries || [];
};

const refreshScheduler = async () => {
  const [tasksResponse, planResponse] = await Promise.all([
    getFanxiuDataAnnotationSchedulerTasks(),
    getFanxiuDataAnnotationSchedulerPlan(),
  ]);
  schedulerTasks.value = tasksResponse.tasks || [];
  schedulerPlan.value = planResponse;
};

const refreshAll = async () => {
  loading.value = true;
  try {
    await Promise.all([refreshStatus(), refreshLogs(), refreshScheduler()]);
  } finally {
    loading.value = false;
  }
};

const runAction = async (name: string, action: () => Promise<FanxiuDataAnnotationRuntimeStatus | void>) => {
  if (!entryId.value) {
    ElMessage.warning('未找到 mf 设备入口');
    return;
  }
  actionLoading.value = name;
  try {
    const status = await action();
    if (status) applyStatus(status);
    await Promise.all([refreshLogs(), refreshScheduler()]);
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || '操作失败');
  } finally {
    actionLoading.value = '';
  }
};

const toggleGuard = () => runAction('guard', () => setFanxiuDataAnnotationRuntimeGuard(entryId.value, !guardEnabled.value, 2, 'close_popups'));

const toggleGuardItem = (itemId: string) => {
  if (itemId === 'close_popups') {
    void toggleGuard();
    return;
  }
  void runAction(`guard:${itemId}`, () => setFanxiuDataAnnotationRuntimeGuard(entryId.value, !guardItemEnabled(itemId), 2, itemId));
};

const toggleTaskEnabled = async (task: FanxiuDataAnnotationSchedulerTaskItem) => {
  if (task.schedule_kind === 'manual') return;
  actionLoading.value = `enable:${task.id}`;
  try {
    const tasks = schedulerTasks.value.map((item) => (
      item.id === task.id ? { ...item, enabled: !item.enabled } : { ...item }
    ));
    const response = await saveFanxiuDataAnnotationSchedulerTasks(tasks);
    schedulerTasks.value = response.tasks || [];
    await refreshScheduler();
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || '保存失败');
  } finally {
    actionLoading.value = '';
  }
};

const startPolling = () => {
  if (pollTimer !== null) return;
  pollTimer = window.setInterval(() => {
    void refreshStatus();
  }, 1500);
};

const stopPolling = () => {
  if (pollTimer === null) return;
  window.clearInterval(pollTimer);
  pollTimer = null;
};

onMounted(async () => {
  await taskStore.fetchDevices();
  if (!entryId.value) {
    const mfDevice = devices.value.find((item) => item.name === machineName || item.id === machineName || item.id.includes('codepc_mf'));
    entryId.value = mfDevice?.id || devices.value[0]?.id || '';
  }
  await refreshAll();
  startPolling();
  window.addEventListener('click', closeLogMenu);
});

onUnmounted(() => {
  stopPolling();
  window.removeEventListener('click', closeLogMenu);
});
</script>

<template>
  <div class="runtime-page">
    <header class="runtime-header">
      <div>
        <div class="runtime-title">
          <h2>凡修行为树</h2>
          <el-popover trigger="click" placement="right-start" :width="420">
            <template #reference>
              <el-button
                class="help-button"
                size="small"
                circle
                aria-label="查看行为树说明"
              >
                <el-icon><QuestionFilled /></el-icon>
              </el-button>
            </template>
            <div class="runtime-help-doc">
              <h4>运行分组</h4>
              <p>运行时按分组管理记忆。当前分为守护、手动作业、自动作业。</p>
              <p>跨组只暂停，不清记忆。守护处理弹窗时，正在运行的作业本轮不推进；守护结束后，作业从原来的生成器位置继续。</p>
              <p>手动按钮会写入手动作业队列。没有守护时，手动作业优先于后台自动作业执行。</p>
              <p>同组默认串行，不抢占。一个作业开始后会先跑完，同组里新到期的作业进入候选池，等当前作业结束后再选择。</p>
              <p>前端只修改启用白名单和任务配置；后端动态容器读取配置后生成行为树节点。</p>
            </div>
          </el-popover>
        </div>
      </div>
    </header>

    <main class="runtime-main" v-loading="loading">
      <section class="runtime-section">
        <div class="section-title">
          <h3>守护</h3>
        </div>
        <div class="runtime-table">
          <table class="runtime-native-table is-guard-table">
            <colgroup>
              <col class="col-index" />
              <col class="col-name" />
              <col class="col-exec" />
              <col class="col-enable" />
            </colgroup>
            <thead>
              <tr>
                <th>序号</th>
                <th>名称</th>
                <th>备注</th>
                <th>启用</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="(item, index) in guardItems"
                :key="item.id"
                @contextmenu.prevent.stop="openLogMenu($event, 'guard', item.id, item.label)"
              >
                <td><span class="index-pill">{{ index + 1 }}</span></td>
                <td><strong>{{ item.label }}</strong></td>
                <td>{{ item.message }}</td>
                <td>
                  <button
                    class="enable-dot"
                    :class="{ enabled: item.enabled }"
                    type="button"
                    :disabled="actionLoading === 'guard' || actionLoading === `guard:${item.id}`"
                    title="切换启用"
                    @click="toggleGuardItem(item.id)"
                  />
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <section class="runtime-section">
        <div class="section-title">
          <h3>作业</h3>
        </div>
        <div class="runtime-table">
          <table class="runtime-native-table is-job-table">
            <colgroup>
              <col class="col-index" />
              <col class="col-name" />
              <col class="col-exec" />
              <col class="col-enable" />
              <col class="col-trigger" />
            </colgroup>
            <thead>
              <tr>
                <th>序号</th>
                <th>名称</th>
                <th>备注</th>
                <th>启用</th>
                <th>下次触发</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="(task, index) in businessTasks"
                :key="task.id"
                @contextmenu.prevent.stop="openLogMenu($event, 'job', task.id, task.label)"
              >
                <td><span class="index-pill">{{ index + 1 }}</span></td>
                <td><strong>{{ task.label }}</strong></td>
                <td>{{ taskMetaText(task) }}</td>
                <td>
                  <button
                    class="enable-dot"
                    :class="{ enabled: task.enabled }"
                    type="button"
                    :disabled="task.schedule_kind === 'manual' || actionLoading === `enable:${task.id}`"
                    :title="task.schedule_kind === 'manual' ? '手动作业不启用' : '切换启用'"
                    @click="toggleTaskEnabled(task)"
                  />
                </td>
                <td :title="nextTriggerTitle(task)">{{ nextTriggerText(task) }}</td>
              </tr>
              <tr v-if="!businessTasks.length">
                <td colspan="5" class="empty-cell">暂无作业</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <section class="runtime-section">
        <div class="section-title">
          <h3>运行状态</h3>
          <span>{{ schedulerPlan?.message || machineName }}</span>
        </div>
        <div class="runtime-facts">
          <span>{{ runtimeStateText }}</span>
          <span>场景 {{ currentSceneText }}</span>
          <span>阶段 {{ runtimePhaseText }}</span>
          <span v-if="taskProgressText">进度 {{ taskProgressText }}</span>
        </div>
        <div class="runtime-message" :title="runtimeMessage">{{ runtimeMessage }}</div>
      </section>

      <section class="runtime-section">
        <div class="section-title">
          <h3>日志</h3>
          <span>{{ logs.length }} 条</span>
        </div>
        <div class="log-list">
          <div v-for="(entry, index) in logs" :key="`${entry.time}-${index}`" class="log-row" :class="`is-${entry.kind}`">
            <span>{{ entry.time }}</span>
            <b>{{ entry.kind }}</b>
            <p>{{ entry.message }}</p>
          </div>
          <div v-if="!logs.length" class="empty-row">暂无日志</div>
        </div>
      </section>
    </main>

    <div
      v-if="contextMenu.visible"
      class="runtime-context-menu"
      :style="{ left: `${contextMenu.x}px`, top: `${contextMenu.y}px` }"
      @click.stop
    >
      <button type="button" @click="openContextLogs">日志</button>
    </div>
  </div>
</template>

<style scoped>
.runtime-page {
  min-height: 100%;
  background: #f5f7fa;
  color: #1f2937;
}

.runtime-header {
  min-height: 72px;
  padding: 12px 18px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  border-bottom: 1px solid #e5e7eb;
  background: #fff;
}

.runtime-header h2 {
  margin: 4px 0 0;
  font-size: 20px;
  line-height: 1.2;
}

.runtime-title {
  display: flex;
  align-items: center;
  gap: 8px;
}

.help-button {
  width: 22px;
  height: 22px;
  min-height: 22px;
  padding: 0;
  color: #64748b;
}

.runtime-help-doc h4 {
  margin: 0 0 8px;
  font-size: 14px;
}

.runtime-help-doc p {
  margin: 7px 0;
  color: #4b5563;
  font-size: 13px;
  line-height: 1.55;
}

.runtime-main {
  padding: 14px 18px 28px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.runtime-section {
  padding: 12px;
  border: 1px solid #dfe6ef;
  background: #fff;
}

.section-title {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
}

.section-title h3 {
  margin: 0;
  font-size: 15px;
}

.section-title span,
.muted {
  color: #6b7280;
  font-size: 12px;
}

.runtime-facts {
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.runtime-table {
  width: max-content;
  max-width: 100%;
  overflow-x: auto;
  border-top: 1px solid #e5e7eb;
}

.runtime-native-table {
  border-collapse: collapse;
  table-layout: fixed;
}

.runtime-native-table.is-guard-table {
  width: 466px;
}

.runtime-native-table.is-job-table {
  width: 582px;
}

.runtime-native-table th,
.runtime-native-table td {
  box-sizing: border-box;
}

.runtime-native-table th:nth-child(1),
.runtime-native-table td:nth-child(1) {
  width: 56px;
}

.runtime-native-table th:nth-child(2),
.runtime-native-table td:nth-child(2) {
  width: 128px;
}

.runtime-native-table th:nth-child(3),
.runtime-native-table td:nth-child(3) {
  width: 210px;
}

.runtime-native-table th:nth-child(4),
.runtime-native-table td:nth-child(4) {
  width: 72px;
}

.runtime-native-table th:nth-child(5),
.runtime-native-table td:nth-child(5) {
  width: 116px;
}

.runtime-native-table th,
.runtime-native-table td {
  height: 42px;
  padding: 0 16px;
  border-bottom: 1px solid #e9eef5;
  font-size: 13px;
  text-align: left;
  vertical-align: middle;
  white-space: nowrap;
}

.runtime-native-table th {
  height: 34px;
  color: #64748b;
  font-size: 12px;
  font-weight: 500;
}

.runtime-native-table th:first-child,
.runtime-native-table td:first-child {
  padding-left: 0;
}

.runtime-native-table th:nth-child(4),
.runtime-native-table td:nth-child(4) {
  text-align: center;
}

.runtime-native-table strong {
  font-weight: 500;
}

.runtime-native-table tbody tr {
  cursor: default;
}

.index-pill {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 22px;
  color: #64748b;
  font-size: 12px;
  border-radius: 8px;
  background: #e8eef6;
}

.enable-dot {
  width: 28px;
  height: 28px;
  padding: 0;
  border: 0;
  border-radius: 50%;
  background: #9ca3af;
  cursor: pointer;
  box-shadow: inset 0 0 0 1px rgba(15, 23, 42, 0.04);
}

.enable-dot.enabled {
  background: #16a34a;
  box-shadow: 0 0 0 3px rgba(22, 163, 74, 0.16);
}

.enable-dot:disabled {
  cursor: not-allowed;
  opacity: 1;
}

.runtime-facts span {
  display: inline-flex;
  align-items: center;
  height: 24px;
  padding: 0 8px;
  color: #606266;
  font-size: 12px;
  border: 1px solid #dcdfe6;
  background: #f5f7fa;
}

.runtime-message {
  margin-top: 8px;
  min-width: 0;
  color: #6b7280;
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.empty-cell {
  color: #9ca3af;
  font-size: 13px;
}

.empty-row {
  min-height: 30px;
  display: flex;
  align-items: center;
  color: #9ca3af;
  font-size: 13px;
  padding: 0 10px;
  border-bottom: 1px solid #f1f5f9;
}

.log-list {
  max-height: 460px;
  overflow: auto;
  border: 1px solid #edf2f7;
}

.log-row {
  display: grid;
  grid-template-columns: 68px 54px minmax(0, 1fr);
  gap: 8px;
  min-height: 30px;
  padding: 7px 9px;
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

.log-row.is-error {
  background: #fef2f2;
}

.log-row.is-success {
  background: #f0fdf4;
}

.runtime-context-menu {
  position: fixed;
  z-index: 1000;
  min-width: 92px;
  padding: 4px;
  border: 1px solid #d1d5db;
  border-radius: 6px;
  background: #fff;
  box-shadow: 0 10px 24px rgba(15, 23, 42, 0.14);
}

.runtime-context-menu button {
  width: 100%;
  height: 28px;
  padding: 0 10px;
  border: 0;
  border-radius: 4px;
  background: transparent;
  color: #1f2937;
  font-size: 13px;
  text-align: left;
  cursor: pointer;
}

.runtime-context-menu button:hover {
  background: #f3f4f6;
}

@media (max-width: 900px) {
  .runtime-header {
    display: flex;
    flex-direction: column;
    align-items: stretch;
  }
}
</style>
