<script setup lang="ts">
import { ref, onMounted, onUnmounted, nextTick } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import DocPage from '@/components/DocPage.vue';
import { getDeviceApi } from '@/api';
import { ElMessage, ElMessageBox } from 'element-plus';
import { Edit, VideoPlay, VideoPause, Search, Delete, Connection } from '@element-plus/icons-vue';
import { taskStore } from '@/store/taskStore';

interface TaskStatus {
  id: string;
  running: boolean;
  pid?: number;
  started_at?: number;
  finished_at?: number;
  cpu_percent?: number;
  memory_rss?: number;
  message?: string;
}

interface Task {
  id: string;
  name: string;
  command: string;
  description?: string;
  cwd?: string;
  device_id?: string;
  schedule?: string;
  timeout?: number;
  status: TaskStatus;
  actionLoading?: boolean;
}

const route = useRoute();
const router = useRouter();
const taskId = route.params.id as string;
const targetDeviceId = Array.isArray(route.query.device_id) 
  ? (route.query.device_id[0] || '') 
  : ((route.query.device_id as string) || '');
const currentDeviceUrl = ref('');
const currentDeviceToken = ref('');
const deviceName = ref('');

const logs = ref<string[]>([]);
const task = ref<Task | null>(null);
const autoScroll = ref(true);
const logContainer = ref<HTMLElement | null>(null);
let pollInterval: number | null = null;
let ws: WebSocket | null = null;
let wsStatus: WebSocket | null = null; // Separate WS for status? No, maybe single one or separate.

// We need two WS connections or one multiplexed?
// Backend has /ws/logs/{task_id} and /ws/tasks
// For logs page, we need logs stream AND task status updates (cpu/mem).
// Actually, /ws/tasks broadcasts ALL tasks status.
// /ws/logs/{task_id} broadcasts logs.

// We can connect to both.

const connectWsLogs = () => {
    if (ws) return;
    
    // const device = { url: currentDeviceUrl.value }; // Simple mock, assuming currentDeviceUrl is set
    // Actually we need to handle if currentDeviceUrl is empty or not resolved yet.
    if (!currentDeviceUrl.value) return;

    let wsUrl = currentDeviceUrl.value || 'http://localhost:8000';
    wsUrl = wsUrl.replace(/^http/, 'ws');
    wsUrl = `${wsUrl}/api/task/ws/logs/${taskId}`;
    
    const protocols = [];
    if (currentDeviceToken.value) {
        protocols.push(currentDeviceToken.value);
    }

    try {
        ws = new WebSocket(wsUrl, protocols);
        ws.onopen = () => {
            console.log('WS Logs connected');
            // We might want to clear logs or fetch history first?
            // Current backend just streams new logs.
            // So we still need fetchLogs() for history.
        };
        ws.onmessage = (event) => {
            try {
                const msg = JSON.parse(event.data);
                if (msg.type === 'log') {
                    // Append log
                    logs.value.push(msg.data);
                    // Trim logs if too many?
                    if (logs.value.length > 2000) {
                        logs.value = logs.value.slice(-2000);
                    }
                    if (autoScroll.value) {
                        scrollToBottom();
                    }
                }
            } catch (e) {
                console.error("WS log parse error", e);
            }
        };
        ws.onclose = () => {
             console.log('WS Logs closed, retrying...');
             ws = null;
             setTimeout(connectWsLogs, 3000);
        };
    } catch (e) {
        console.error("WS Logs connection failed", e);
    }
};

const connectWsStatus = () => {
    // For status, we can reuse /ws/tasks but filter for this task?
    // Or just poll status?
    // User wants "all polling replaced by WS".
    // So we should connect to /ws/tasks and filter.
    
    if (wsStatus) return;
    
    let wsUrl = currentDeviceUrl.value || 'http://localhost:8000';
    wsUrl = wsUrl.replace(/^http/, 'ws');
    wsUrl = `${wsUrl}/api/task/ws/tasks`;
    
    const protocols = [];
    if (currentDeviceToken.value) {
        protocols.push(currentDeviceToken.value);
    }

    try {
        wsStatus = new WebSocket(wsUrl, protocols);
        wsStatus.onmessage = (event) => {
             try {
                const data = JSON.parse(event.data);
                // data is list of tasks
                const myTask = data.find((t: any) => t.id === taskId);
                if (myTask) {
                    // Update task info
                    if (!task.value) task.value = myTask;
                    else {
                        Object.assign(task.value, myTask);
                    }
                }
             } catch (e) {
                 
             }
        };
        wsStatus.onclose = () => {
             wsStatus = null;
             setTimeout(connectWsStatus, 3000);
        };
    } catch (e) {
        
    }
};

// Edit Mode
const isEditing = ref(false);
const editForm = ref({
  name: '',
  command: '',
  cwd: '',
  description: '',
  device_id: '',
  schedule: '',
  timeout: null as number | null
});

// Related Process Scan
const scanDialogVisible = ref(false);
const scanLoading = ref(false);
const relatedProcesses = ref<any[]>([]);

const resolveDevice = async () => {
  if (!targetDeviceId) return true; // No device ID, assume local or handled elsewhere
  
  // Ensure store is loaded
  if (taskStore.devices.length === 0) {
      await taskStore.fetchDevices();
  }
  
  const device = taskStore.devices.find(d => d.id === targetDeviceId);
  if (device) {
    currentDeviceUrl.value = device.url || '';
    currentDeviceToken.value = device.access_token || '';
    deviceName.value = device.name || device.id;
    return true;
  } else {
    // Fallback: try global list if user list fails?
    // But user list is definitive.
    // If not found, maybe show error.
    ElMessage.error('无法找到设备或无权访问');
    // If we can't find the device, we probably shouldn't default to local blindly if a device_id was requested.
    // But existing logic falls through.
    if (targetDeviceId && targetDeviceId !== 'local') {
         console.warn(`Device ${targetDeviceId} not found in store. Store devices:`, taskStore.devices);
         return false;
    }
    return true; // Fallback to allow local if somehow targetDeviceId is something else
  }
};

const handleStatusClick = async () => {
  if (!task.value || task.value.actionLoading) return;
  
  task.value.actionLoading = true;
  try {
    const client = getDeviceApi(currentDeviceUrl.value, currentDeviceToken.value);
    if (task.value.status.running) {
      // Stop
      await client.post(`/task/${task.value.id}/stop`);
      ElMessage.success(`Task "${task.value.name}" stopping...`);
    } else {
      // Start check conflict
      try {
        const relatedRes = await client.get(`/task/${task.value.id}/related_processes`);
        const exactMatches = relatedRes.data.filter((p: any) => p.score >= 3);
        
        if (exactMatches.length > 0) {
            try {
               await ElMessageBox.confirm(
                 `检测到系统中已有 ${exactMatches.length} 个完全匹配的进程 (PID: ${exactMatches[0].pid}) 在运行。继续启动将产生新的实例。`, 
                 '重复进程警告', 
                 {
                   confirmButtonText: '继续启动',
                   cancelButtonText: '取消',
                   type: 'warning'
                 }
               );
            } catch {
               task.value.actionLoading = false;
               return; // Cancelled
            }
        }
      } catch (e) {
        // Ignore scan error, proceed to start
      }

      // Start
      await client.post(`/task/${task.value.id}/start`);
      ElMessage.success(`Task "${task.value.name}" started`);
    }
    // Refresh immediately to ensure polling starts if running
    await refreshAll();
  } catch (err: any) {
    ElMessage.error(err.response?.data?.detail || 'Operation failed');
  } finally {
    if (task.value) task.value.actionLoading = false;
  }
};

const nlpInput = ref('');

const parseNlp = () => {
    const text = nlpInput.value.trim();
    if (!text) return;
    
    // Simple regex rules
    const minuteMatch = text.match(/每(\d+)分钟/);
    if (minuteMatch) {
        editForm.value.schedule = `*/${minuteMatch[1]} * * * *`;
        ElMessage.success('已解析为 Cron 表达式');
        return;
    }
    
    if (text.includes('每小时')) {
        editForm.value.schedule = '0 * * * *';
        ElMessage.success('已解析为 Cron 表达式');
        return;
    }

    const dailyMatch = text.match(/每天(\d+)点/);
    if (dailyMatch) {
        editForm.value.schedule = `0 ${dailyMatch[1]} * * *`;
        ElMessage.success('已解析为 Cron 表达式');
        return;
    }

    // New: Timeout parsing (e.g., "超时1小时", "超时30分钟")
    const timeoutHour = text.match(/超时(\d+)小时/);
    if (timeoutHour) {
        editForm.value.timeout = parseInt(timeoutHour[1]) * 3600;
        ElMessage.success(`已设置超时: ${timeoutHour[1]} 小时`);
        return;
    }
    const timeoutMin = text.match(/超时(\d+)分钟/);
    if (timeoutMin) {
        editForm.value.timeout = parseInt(timeoutMin[1]) * 60;
        ElMessage.success(`已设置超时: ${timeoutMin[1]} 分钟`);
        return;
    }

    ElMessage.warning('无法解析该自然语言描述，请尝试标准格式或手动输入 Cron');
};

const startEditing = () => {
  if (!task.value) return;
  editForm.value = {
    name: task.value.name,
    command: task.value.command,
    cwd: task.value.cwd || '',
    description: task.value.description || '',
    device_id: task.value.device_id || 'local',
    schedule: task.value.schedule || '',
    timeout: task.value.timeout || null
  };
  isEditing.value = true;
};

const cancelEditing = () => {
  isEditing.value = false;
};

const saveEditing = async () => {
  try {
    const client = getDeviceApi(currentDeviceUrl.value, currentDeviceToken.value);
    await client.post(`/task/${taskId}/update`, editForm.value);
    ElMessage.success('Task updated');
    isEditing.value = false;
    await fetchTask();
  } catch (err: any) {
    ElMessage.error(err.response?.data?.detail || 'Failed to update task');
  }
};

const formatTime = (timestamp: number | undefined) => {
  if (!timestamp) return '-';
  return new Date(timestamp * 1000).toLocaleString();
};

const formatBytes = (bytes: number | undefined) => {
  if (bytes === undefined || bytes === null) return '-';
  if (bytes === 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
};

const formatDuration = (seconds: number | undefined | null) => {
  if (seconds === undefined || seconds === null || seconds < 0) return '不限制';
  if (seconds === 0) return '0秒';
  
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  
  let result = '';
  if (h > 0) result += `${h}小时`;
  if (m > 0) result += `${m}分`;
  if (s > 0) result += `${s}秒`;
  
  return result || `${seconds}秒`;
};

// Composable for dynamic duration updates without polling
const useDuration = (taskRef: any) => {
  const duration = ref('');
  let timer: any = null;

  const update = () => {
    if (!taskRef.value || !taskRef.value.status.running || !taskRef.value.status.started_at) {
      duration.value = '-';
      return;
    }
    const now = Date.now() / 1000;
    const elapsed = Math.floor(now - taskRef.value.status.started_at);
    duration.value = formatDuration(elapsed);
  };

  onMounted(() => {
    update();
    timer = setInterval(update, 1000);
  });

  onUnmounted(() => {
    if (timer) clearInterval(timer);
  });

  return duration;
};

const dynamicDuration = useDuration(task);

const fetchTask = async () => {
  try {
    const client = getDeviceApi(currentDeviceUrl.value, currentDeviceToken.value);
    const res = await client.get(`/task/${taskId}`);
    task.value = res.data;
  } catch (err) {
    // console.error('Failed to fetch task details', err);
  }
};

const fetchLogs = async () => {
  try {
    const client = getDeviceApi(currentDeviceUrl.value, currentDeviceToken.value);
    const res = await client.get(`/task/${taskId}/logs?n=500`);
    logs.value = res.data.logs;
    if (autoScroll.value) {
      scrollToBottom();
    }
  } catch (err) {
    console.error('Failed to fetch logs', err);
  }
};

const refreshAll = async () => {
  // If we already have an interval running, we might be inside it.
  // But if this is called manually or initially, we need to decide strategy.
  
  if (pollInterval) clearInterval(pollInterval);
  pollInterval = null;

  // Fetch initial state
  await Promise.all([fetchTask(), fetchLogs()]);
  
  // Connect WS
  connectWsLogs();
  connectWsStatus();
};

// const checkStatusOnly = async () => {
//     // ... implementation
// };

const scrollToBottom = async () => {
  await nextTick();
  if (logContainer.value) {
    logContainer.value.scrollTop = logContainer.value.scrollHeight;
  }
};

const goBack = () => {
  router.push({ name: 'ClusterManager', query: { device_id: route.query.device_id } });
};

const handleScanProcesses = async () => {
  scanDialogVisible.value = true;
  scanLoading.value = true;
  try {
    const client = getDeviceApi(currentDeviceUrl.value, currentDeviceToken.value);
    const res = await client.get(`/task/${taskId}/related_processes`);
    relatedProcesses.value = res.data;
  } catch (err: any) {
    ElMessage.error(err.response?.data?.detail || 'Scan failed');
  } finally {
    scanLoading.value = false;
  }
};

const killProcess = async (pid: number) => {
  try {
    await ElMessageBox.confirm(`Are you sure to kill process PID ${pid}?`, 'Warning', {
      type: 'warning',
      confirmButtonText: 'Kill',
      confirmButtonClass: 'el-button--danger',
    });
    
    const client = getDeviceApi(currentDeviceUrl.value, currentDeviceToken.value);
    await client.post('/task/process/kill', { pid });
    ElMessage.success(`Process ${pid} killed`);
    
    // Refresh list
    handleScanProcesses();
  } catch (err) {
    if (err !== 'cancel') {
        console.error(err);
        ElMessage.error('Failed to kill process');
    }
  }
};

const associateProcess = async (pid: number) => {
  try {
    await ElMessageBox.confirm(`确定要将当前任务关联到进程 PID ${pid} 吗？\n这将更新任务的命令、工作目录等信息，并以该进程为准进行监控。`, '确认关联', {
      type: 'warning',
      confirmButtonText: '关联',
      cancelButtonText: '取消'
    });
    
    const client = getDeviceApi(currentDeviceUrl.value, currentDeviceToken.value);
    await client.post(`/task/${taskId}/associate`, { pid });
    ElMessage.success(`已关联到进程 PID ${pid}`);
    
    scanDialogVisible.value = false;
    refreshAll();
  } catch (err: any) {
    if (err !== 'cancel') {
      console.error(err);
      ElMessage.error(err.response?.data?.detail || '关联失败');
    }
  }
};

onMounted(async () => {
  const success = await resolveDevice();
  if (success) {
      refreshAll(); // Initial fetch, polling logic is inside refreshAll
  }
});

onUnmounted(() => {
  if (ws) {
    ws.close();
    ws = null;
  }
  if (wsStatus) {
    wsStatus.close();
    wsStatus = null;
  }
});
</script>

<template>
  <DocPage title="任务详情" :description="task?.name ? `任务: ${task.name}` : `任务ID: ${taskId}`">
    <div class="toolbar">
      <el-button @click="goBack">返回列表</el-button>
      <div style="flex: 1"></div>
      
      <div v-if="!isEditing" style="display: flex; gap: 10px;">
        <el-button :icon="Edit" @click="startEditing">编辑</el-button>
        <el-button :icon="Search" @click="handleScanProcesses">同名进程</el-button>
        <el-checkbox v-model="autoScroll" style="margin-right: 10px;">自动滚动</el-checkbox>
        <el-button type="primary" link @click="refreshAll">刷新</el-button>

        <el-button 
          v-if="task"
          :type="task.actionLoading ? 'warning' : (task.status.running ? 'danger' : 'success')"
          :loading="task.actionLoading"
          @click="handleStatusClick"
          :icon="task.status.running ? VideoPause : VideoPlay"
        >
          {{ task.status.running ? '停止任务' : '启动任务' }}
        </el-button>
      </div>
      <div v-else style="display: flex; gap: 10px;">
        <el-button @click="cancelEditing">取消</el-button>
        <el-button type="primary" @click="saveEditing">保存修改</el-button>
      </div>
    </div>

    <div v-if="task" class="task-info">
      <el-descriptions :column="2" border size="small" style="margin-top: 10px;" v-if="!isEditing">
        <el-descriptions-item label="任务名称">{{ task.name }}</el-descriptions-item>
        <el-descriptions-item label="设备">{{ deviceName || task.device_id || 'Local' }}</el-descriptions-item>

        <el-descriptions-item label="任务状态">
           <el-tag :type="task.status.running ? 'success' : 'info'" size="small">
              {{ task.status.running ? 'Running' : 'Stopped' }}
           </el-tag>
           <span v-if="task.status.pid" style="margin-left: 10px; color: #909399;">
              PID: {{ task.status.pid }}
           </span>
        </el-descriptions-item>
        <el-descriptions-item label="资源使用">
          <span v-if="task.status.running">
             CPU: {{ task.status.cpu_percent }}% | Mem: {{ formatBytes(task.status.memory_rss) }}
          </span>
          <span v-else style="color: #909399;">-</span>
        </el-descriptions-item>

        <el-descriptions-item label="启动时间">
          {{ formatTime(task.status.started_at) }}
        </el-descriptions-item>
        <el-descriptions-item label="结束/时长">
           <span v-if="!task.status.running && task.status.finished_at">结束: {{ formatTime(task.status.finished_at) }}</span>
           <span v-if="task.status.running && task.status.started_at">时长: {{ dynamicDuration }}</span>
           <span v-if="!task.status.running && !task.status.finished_at">-</span>
        </el-descriptions-item>

        <el-descriptions-item label="定时调度">{{ task.schedule || '手动' }}</el-descriptions-item>
        <el-descriptions-item label="超时时间">{{ formatDuration(task.timeout) }}</el-descriptions-item>

        <el-descriptions-item label="执行命令" :span="2">
          {{ task.command }}
        </el-descriptions-item>
        
        <el-descriptions-item label="工作目录" :span="2">{{ task.cwd || '默认' }}</el-descriptions-item>
        
        <el-descriptions-item label="描述" :span="2">{{ task.description || '无' }}</el-descriptions-item>
      </el-descriptions>

      <!-- Inline Edit Form -->
      <el-form v-else :model="editForm" label-width="80px" class="edit-form">
        <el-row :gutter="20">
          <el-col :span="12">
            <el-form-item label="名称" required>
              <el-input v-model="editForm.name" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="定时调度">
               <el-input v-model="editForm.schedule" placeholder="Cron 表达式 (例如: */5 * * * *)">
                  <template #append>
                    <el-popover placement="bottom" title="自然语言解析" :width="300" trigger="click">
                      <template #reference>
                        <el-button :icon="Edit" />
                      </template>
                      <el-input v-model="nlpInput" placeholder="例如: 每5分钟, 超时1小时" size="small">
                        <template #append>
                          <el-button @click="parseNlp">解析</el-button>
                        </template>
                      </el-input>
                    </el-popover>
                  </template>
               </el-input>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="超时时间">
               <el-input v-model.number="editForm.timeout" placeholder="单位: 秒" type="number" />
            </el-form-item>
          </el-col>
        </el-row>
        
        <el-form-item label="命令" required>
          <el-input v-model="editForm.command" type="textarea" :rows="3" />
        </el-form-item>
        
        <el-form-item label="工作目录">
          <el-input v-model="editForm.cwd" placeholder="可选: 执行目录绝对路径" />
        </el-form-item>
        
        <el-form-item label="描述">
          <el-input v-model="editForm.description" placeholder="可选: 任务描述" />
        </el-form-item>
      </el-form>
    </div>

    <div class="log-container" ref="logContainer">
      <div v-if="logs.length === 0" class="no-logs">暂无日志</div>
      <div v-else v-for="(log, index) in logs" :key="index" class="log-line">
        {{ log }}
      </div>
    </div>

    <!-- Scan Result Dialog -->
    <el-dialog v-model="scanDialogVisible" title="同名进程检测" width="800px">
      <div v-loading="scanLoading">
        <el-alert
          type="info"
          show-icon
          :closable="false"
          style="margin-bottom: 15px;"
        >
          <template #title>
             <div>
               以下是系统中与当前任务命令相似的进程。
               <div style="margin-top: 4px; font-weight: normal;">
                 <span style="color: #606266;">可执行文件: </span>
                 <el-tag size="small" type="info" effect="plain" v-if="relatedProcesses.length > 0 && relatedProcesses[0].exe">
                    {{ relatedProcesses[0].exe }}
                 </el-tag>
                 <span v-else>{{ relatedProcesses.length > 0 ? relatedProcesses[0].name : '未知' }}</span>
               </div>
               <div style="margin-top: 2px; color: #909399; font-size: 12px;">如果发现重复实例，可以尝试手动清理。</div>
             </div>
          </template>
        </el-alert>
        
        <el-table :data="relatedProcesses" border style="width: 100%" height="400">
          <el-table-column prop="pid" label="PID" width="80" />
          <!-- <el-table-column prop="name" label="进程名" width="150" show-overflow-tooltip /> -->
          <el-table-column label="命令行" show-overflow-tooltip>
            <template #default="{ row }">
              <span v-if="row.cmd_args" :title="row.cmdline">{{ row.cmd_args }}</span>
              <span v-else>{{ row.cmdline }}</span>
            </template>
          </el-table-column>
          <el-table-column label="启动时间" width="160">
            <template #default="{ row }">
              {{ formatTime(row.started_at) }}
            </template>
          </el-table-column>
          <el-table-column label="内存" width="100">
            <template #default="{ row }">
              {{ formatBytes(row.memory_rss) }}
            </template>
          </el-table-column>
          <el-table-column label="匹配度" width="80" align="center">
             <template #default="{ row }">
                <el-tag size="small" :type="row.score >= 3 ? 'success' : (row.score === 2 ? 'warning' : 'info')">
                  {{ row.score >= 3 ? '完全' : (row.score === 2 ? '部分' : '名称') }}
                </el-tag>
             </template>
          </el-table-column>
          <el-table-column label="操作" width="120" align="center">
            <template #default="{ row }">
              <el-button 
                type="primary" 
                :icon="Connection" 
                circle 
                plain
                size="small" 
                title="关联此进程"
                @click="associateProcess(row.pid)"
                style="margin-right: 5px;"
              />
              <el-button 
                type="danger" 
                :icon="Delete" 
                circle 
                size="small" 
                title="强制结束"
                @click="killProcess(row.pid)"
              />
            </template>
          </el-table-column>
        </el-table>
      </div>
      <template #footer>
        <el-button @click="scanDialogVisible = false">关闭</el-button>
        <el-button type="primary" :icon="Search" :loading="scanLoading" @click="handleScanProcesses">重新扫描</el-button>
      </template>
    </el-dialog>
  </DocPage>
</template>

<style scoped>
.toolbar {
  display: flex;
  gap: 15px;
  align-items: center;
  margin-bottom: 15px;
}

.task-info {
  margin-bottom: 20px;
}

.pid-tag {
  margin-left: 8px;
  font-size: 0.9em;
  color: #888;
}

.code-block {
  font-family: 'Consolas', 'Monaco', monospace;
  background-color: #f5f7fa;
  padding: 8px;
  border-radius: 4px;
  white-space: pre-wrap;
  word-break: break-all;
  border: 1px solid #e4e7ed;
  max-height: 200px;
  overflow-y: auto;
}

.edit-form {
  padding: 20px;
  background-color: #f5f7fa;
  border: 1px solid #e4e7ed;
  border-radius: 4px;
}

.log-container {
  background: #1e1e1e;
  color: #d4d4d4;
  padding: 15px;
  border-radius: 4px;
  font-family: 'Consolas', 'Monaco', monospace;
  height: 600px;
  overflow-y: auto;
  white-space: pre-wrap;
}

.log-line {
  line-height: 1.5;
  border-bottom: 1px solid #333;
}

.no-logs {
  color: #666;
  text-align: center;
  padding: 20px;
}

.form-tip {
  font-size: 12px;
  color: #909399;
  line-height: 1.2;
  margin-top: 4px;
}
</style>
