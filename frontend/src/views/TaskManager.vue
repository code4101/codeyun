<script setup lang="ts">
import { ref, onMounted, onUnmounted, nextTick, computed, watch } from 'vue';
import { useRouter, useRoute } from 'vue-router';
import DocPage from '@/components/DocPage.vue';
import api, { getDeviceApi } from '@/api';
import { ElMessage, ElMessageBox } from 'element-plus';
import { Plus, VideoPlay, VideoPause, Delete, Document, Monitor, Connection, Setting, Refresh, Edit, Rank, Lock, Clock } from '@element-plus/icons-vue';
import { taskStore, type Task, type Device } from '@/store/taskStore';
import Sortable from 'sortablejs';

const router = useRouter();
const route = useRoute();
const devices = computed(() => taskStore.devices);
const tasks = computed({
  get: () => taskStore.tasks[currentDeviceId.value] || [],
  set: (val) => {
    if (currentDeviceId.value) {
      taskStore.tasks[currentDeviceId.value] = val;
    }
  }
});
const loading = ref(false); // Initial loading
const dialogVisible = ref(false);
const deviceDialogVisible = ref(false);
const currentDeviceId = ref<string>(Array.isArray(route.query.device_id) ? (route.query.device_id[0] || '') : ((route.query.device_id as string) || ''));
const isEditingDevice = ref(false);
const isEditingTask = ref(false);
const currentTaskId = ref<string>('');
const addDeviceLoading = ref(false);

const form = ref({
  name: '',
  command: '',
  cwd: '',
  description: '',
  device_id: '',
  run_as_admin: false,
  schedule: '',
  timeout: null as number | null
});

const nlpInput = ref('');

const parseNlp = () => {
    const text = nlpInput.value.trim();
    if (!text) return;
    
    // Simple regex rules
    const minuteMatch = text.match(/每(\d+)分钟/);
    if (minuteMatch) {
        form.value.schedule = `*/${minuteMatch[1]} * * * *`;
        ElMessage.success('已解析为 Cron 表达式');
        return;
    }
    
    if (text.includes('每小时')) {
        form.value.schedule = '0 * * * *';
        ElMessage.success('已解析为 Cron 表达式');
        return;
    }

    const dailyMatch = text.match(/每天(\d+)点/);
    if (dailyMatch) {
        form.value.schedule = `0 ${dailyMatch[1]} * * *`;
        ElMessage.success('已解析为 Cron 表达式');
        return;
    }

    // New: Timeout parsing (e.g., "超时1小时", "超时30分钟")
    const timeoutHour = text.match(/超时(\d+)小时/);
    if (timeoutHour) {
        form.value.timeout = parseInt(timeoutHour[1]) * 3600;
        ElMessage.success(`已设置超时: ${timeoutHour[1]} 小时`);
        return;
    }
    const timeoutMin = text.match(/超时(\d+)分钟/);
    if (timeoutMin) {
        form.value.timeout = parseInt(timeoutMin[1]) * 60;
        ElMessage.success(`已设置超时: ${timeoutMin[1]} 分钟`);
        return;
    }

    ElMessage.warning('无法解析该自然语言描述，请尝试标准格式或手动输入 Cron');
};

const deviceForm = ref({
  url: ''
});

const currentDeviceConfig = ref({
  python_exec: '',
  new_name: ''
});

let pollInterval: number | null = null;
let ws: WebSocket | null = null;

const connectWebSocket = () => {
    if (ws) return;
    
    // Determine WS URL based on current device URL
    // If local, use window.location.host
    // But we need to connect to backend.
    // If we are on dev server, backend is localhost:8000
    // If we are on production, it's relative.
    
    // We only support WS for LOCAL device for now, or if we have a proxy.
    // Actually, if we are viewing a remote device, we should connect to its WS?
    // CORS might be an issue.
    // But let's assume we connect to the backend we are talking to.
    
    // Get backend base URL
    const device = devices.value.find(d => d.id === currentDeviceId.value);
    if (!device) return;
    
    let wsUrl = device.url || 'http://localhost:8000';
    wsUrl = wsUrl.replace(/^http/, 'ws');
    wsUrl = `${wsUrl}/api/task/ws/tasks`;
    
    try {
        ws = new WebSocket(wsUrl);
        ws.onopen = () => {
            console.log('WS connected');
        };
        ws.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                // Update tasks
                // Data format: List of tasks with status
                const targetDeviceId = currentDeviceId.value;
                const newTasks = data;
                
                // Ensure device_id
                newTasks.forEach((t: Task) => {
                    if (!t.device_id) t.device_id = targetDeviceId;
                });

                // Merge logic (Same as fetchTasks)
                if (!taskStore.tasks[targetDeviceId] || taskStore.tasks[targetDeviceId].length === 0) {
                    taskStore.tasks[targetDeviceId] = newTasks;
                } else {
                    const currentList = taskStore.tasks[targetDeviceId];
                    newTasks.forEach((nt: Task) => {
                        const existing = currentList.find(t => t.id === nt.id);
                        if (existing) {
                            existing.status = nt.status;
                            existing.name = nt.name;
                            existing.command = nt.command;
                            existing.description = nt.description;
                            existing.schedule = nt.schedule;
                            existing.timeout = nt.timeout;
                        } else {
                            currentList.push(nt);
                        }
                    });
                    const newIds = new Set(newTasks.map((t: Task) => t.id));
                    taskStore.tasks[targetDeviceId] = currentList.filter(t => newIds.has(t.id));
                }
                
                loading.value = false;
            } catch (e) {
                console.error("WS parse error", e);
            }
        };
        ws.onclose = () => {
            console.log('WS closed, retrying in 3s...');
            ws = null;
            setTimeout(connectWebSocket, 3000);
        };
        ws.onerror = (err) => {
            console.error('WS error', err);
            if (ws) ws.close();
        };
    } catch (e) {
        console.error("WS connection failed", e);
    }
};

const fetchDevices = async () => {
  // Use cache first if available
  if (taskStore.devices.length > 0) {
      if (!currentDeviceId.value) {
          const local = taskStore.devices.find(d => d.type === 'LocalDevice');
          currentDeviceId.value = local ? local.id : taskStore.devices[0].id;
      }
      // If we have devices, we can already show them.
      // We still fetch in background to update status/list.
  }

  try {
    const res = await api.get('/task/devices');
    taskStore.devices = res.data;
    
    if (currentDeviceId.value) {
      const exists = taskStore.devices.find(d => d.id === currentDeviceId.value);
      if (!exists) {
         const local = taskStore.devices.find(d => d.type === 'LocalDevice');
         currentDeviceId.value = local ? local.id : (taskStore.devices[0]?.id || '');
      }
    } else if (taskStore.devices.length > 0) {
        const local = taskStore.devices.find(d => d.type === 'LocalDevice');
        currentDeviceId.value = local ? local.id : taskStore.devices[0].id;
    }

    // Update current device config if selected
    const current = taskStore.devices.find(d => d.id === currentDeviceId.value);
    if (current && !isEditingDevice.value) {
      currentDeviceConfig.value = {
        python_exec: current.python_exec || '',
        new_name: current.name || current.id
      };
    }
  } catch (err) {
    console.error('Failed to fetch devices', err);
    if (taskStore.devices.length === 0) {
        ElMessage.error('获取设备列表失败，请检查后端服务是否运行');
    }
  }
};

const currentTasks = computed(() => {
  return tasks.value.filter(t => t.device_id === currentDeviceId.value);
});

const currentDevice = computed(() => {
  return devices.value.find(d => d.id === currentDeviceId.value);
});

watch(loading, (newVal) => {
  if (!newVal) {
    nextTick(() => {
      initSortable();
    });
  }
});

watch(currentDeviceId, (newId) => {
  // Sync URL
  router.replace({ query: { ...route.query, device_id: newId } });

  const dev = devices.value.find(d => d.id === newId);
  if (dev) {
    currentDeviceConfig.value = {
      python_exec: dev.python_exec || '',
      new_name: dev.name || dev.id
    };
    isEditingDevice.value = false;
  }
  // Load status for new device, show loading
  fetchTasks(newId, false);
  
  // Reconnect WS
  if (ws) {
      ws.close();
      ws = null;
  }
  connectWebSocket();
  
  nextTick(() => {
    initSortable();
  });
});

const updateDeviceConfig = async () => {
  if (!currentDevice.value) return;
  try {
    // We send new_name as 'id' field to match backend request model (temporarily)
    // Or we should update backend request model to have 'name' field.
    // The backend updated logic: "req.id here comes from the form field... treat req.id as the new name"
    // So we send new_name in 'id' field.
    const res = await api.post(`/task/devices/${currentDevice.value.id}/update`, {
      id: currentDeviceConfig.value.new_name,
      python_exec: currentDeviceConfig.value.python_exec
    });
    ElMessage.success('设备配置已更新');
    isEditingDevice.value = false;
    
    await fetchDevices();
  } catch (err: any) {
    ElMessage.error(err.response?.data?.detail || '更新失败');
  }
};

const fetchTasks = async (refreshDeviceId: string | null = null, isBackground = false) => {
  const targetDeviceId = refreshDeviceId || currentDeviceId.value;
  if (!targetDeviceId) return;

  const device = devices.value.find(d => d.id === targetDeviceId);
  if (!device) return;

  // Use cache first
  const cached = taskStore.tasks[targetDeviceId];
  if (cached && cached.length > 0) {
      if (!isBackground) {
          loading.value = false;
          isBackground = true; // Downgrade to background refresh
      }
  }

  if (!isBackground) loading.value = true;

  try {
    const client = getDeviceApi(device.url);
    const res = await client.get('/task/list');
    
    // Process new tasks
    const newTasks = res.data;
    newTasks.forEach((t: Task) => {
        if (!t.device_id) t.device_id = targetDeviceId;
    });

    if (!taskStore.tasks[targetDeviceId] || taskStore.tasks[targetDeviceId].length === 0) {
      // Just replace
      taskStore.tasks[targetDeviceId] = newTasks;
    } else {
      // Merge with existing tasks for the SAME device
      const currentList = taskStore.tasks[targetDeviceId];

      // Update existing tasks
      newTasks.forEach((nt: Task) => {
        const existing = currentList.find(t => t.id === nt.id);
        if (existing) {
          existing.status = nt.status;
          existing.name = nt.name;
          existing.command = nt.command;
          existing.description = nt.description;
          // preserve actionLoading
          existing.timeout = nt.timeout;
        } else {
          currentList.push(nt);
        }
      });
      // Remove deleted tasks
      const newIds = new Set(newTasks.map((t: Task) => t.id));
      taskStore.tasks[targetDeviceId] = currentList.filter(t => newIds.has(t.id));
    }
  } catch (err) {
    if (!isBackground) ElMessage.error('Failed to fetch tasks');
  } finally {
    if (!isBackground) loading.value = false;
  }
};

const handleStatusClick = async (task: Task) => {
  if (task.actionLoading) return;
  
  const device = devices.value.find(d => d.id === task.device_id);
  if (!device) return;

  task.actionLoading = true;
  try {
    const client = getDeviceApi(device.url);
    if (task.status.running) {
      // Stop
      await client.post(`/task/${task.id}/stop`);
      ElMessage.success(`Task "${task.name}" stopping...`);
    } else {
      // Start
      await client.post(`/task/${task.id}/start`);
      ElMessage.success(`Task "${task.name}" started`);
    }
    // Refresh immediately
    await fetchTasks(task.device_id || currentDeviceId.value, true);
  } catch (err: any) {
    ElMessage.error(err.response?.data?.detail || 'Operation failed');
  } finally {
    task.actionLoading = false;
  }
};

const handleSubmitTask = async () => {
  if (!form.value.name || !form.value.command) {
    ElMessage.warning('Name and Command are required');
    return;
  }
  
  const device = devices.value.find(d => d.id === currentDeviceId.value);
  if (!device) return;

  try {
    const client = getDeviceApi(device.url);
    if (isEditingTask.value) {
      await client.post(`/task/${currentTaskId.value}/update`, form.value);
      ElMessage.success('Task updated');
    } else {
      // Force device_id to current selected device
      form.value.device_id = currentDeviceId.value;
      await client.post('/task/create', form.value);
      ElMessage.success('Task created');
    }
    
    dialogVisible.value = false;
    form.value = { name: '', command: '', cwd: '', description: '', device_id: currentDeviceId.value, run_as_admin: false, schedule: '', timeout: null }; // Reset
    isEditingTask.value = false;
    currentTaskId.value = '';
    fetchTasks(currentDeviceId.value, true);
  } catch (err: any) {
    ElMessage.error(err.response?.data?.detail || (isEditingTask.value ? 'Failed to update task' : 'Failed to create task'));
  }
};

const handleEdit = (task: Task) => {
  isEditingTask.value = true;
  currentTaskId.value = task.id;
  form.value = {
    name: task.name,
    command: task.command,
    cwd: task.cwd || '',
    description: task.description || '',
    device_id: task.device_id || currentDeviceId.value,
    schedule: task.schedule || '',
    timeout: task.timeout || null
  };
  dialogVisible.value = true;
};

const openCreateDialog = () => {
    isEditingTask.value = false;
    currentTaskId.value = '';
    form.value = { name: '', command: '', cwd: '', description: '', device_id: currentDeviceId.value, run_as_admin: false, schedule: '', timeout: null };
    dialogVisible.value = true;
};

watch(dialogVisible, (val) => {
  if (val) {
    if (!isEditingTask.value) {
       form.value.device_id = currentDeviceId.value;
    }
  }
});

const handleAddDevice = async () => {
  let url = deviceForm.value.url.trim();
  if (!url) {
    ElMessage.warning('URL is required');
    return;
  }
  
  addDeviceLoading.value = true;
  
  // Auto-complete URL
  if (!url.startsWith('http://') && !url.startsWith('https://')) {
    url = 'http://' + url;
  }
  
  try {
    const urlObj = new URL(url);
    if (!urlObj.port) {
      urlObj.port = '8000';
      url = urlObj.toString();
    }
    // Remove trailing slash
    if (url.endsWith('/')) {
      url = url.slice(0, -1);
    }
    deviceForm.value.url = url;
  } catch (e) {
    // If parsing fails, use original (processed) url
    console.warn('URL parse failed, using raw value', e);
  }
  
  try {
    const res = await api.post('/task/devices/add', {
      url: deviceForm.value.url
    });
    ElMessage.success('Device added');
    
    // Switch to the new device
    const newDeviceId = res.data.id;
    deviceForm.value = { url: '' };
    
    await fetchDevices();
    currentDeviceId.value = newDeviceId;
    deviceDialogVisible.value = false; // Close dialog to show the new device tab
    
  } catch (err: any) {
    console.error(err);
    const errorMsg = err.response?.data?.detail || err.message || 'Failed to add device';
    ElMessage.error(errorMsg);
  } finally {
    addDeviceLoading.value = false;
  }
};

const handleSwitchDevice = (device: Device) => {
  currentDeviceId.value = device.id;
  deviceDialogVisible.value = false;
};

const handleDeleteDevice = async (device: Device) => {
  try {
    await ElMessageBox.confirm(`确定要删除设备 "${device.name}" 吗?`, '警告', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消'
    });
    
    await api.delete(`/task/devices/${device.id}`);
    ElMessage.success('设备已删除');
    
    // Refresh list first
    await fetchDevices();
    
    // If current device was deleted or invalid, switch to local
    const currentStillExists = devices.value.find(d => d.id === currentDeviceId.value);
    if (!currentStillExists) {
       const local = devices.value.find(d => d.type === 'LocalDevice');
       currentDeviceId.value = local ? local.id : (devices.value[0]?.id || '');
    }
  } catch (err) {
    if (err !== 'cancel') {
        console.error(err);
        ElMessage.error('删除失败');
    }
  }
};

const handleDelete = async (task: Task) => {
  try {
    await ElMessageBox.confirm(`Are you sure to delete task "${task.name}"?`, 'Warning', {
      type: 'warning',
      confirmButtonText: 'Delete',
      cancelButtonText: 'Cancel'
    });
    
    const device = devices.value.find(d => d.id === task.device_id);
    if (!device) return;
    const client = getDeviceApi(device.url);
    
    await client.delete(`/task/${task.id}`);
    ElMessage.success('Task deleted');
    await fetchTasks(task.device_id || currentDeviceId.value, false);
  } catch (err) {
    // Cancelled
  }
};

const viewLogs = (task: Task) => {
  router.push({ name: 'TaskLogs', params: { id: task.id }, query: { device_id: task.device_id } });
};

const getDeviceName = (id?: string) => {
  if (!id) return 'Unknown';
  return id;
};

const formatDuration = (seconds: number | undefined | null) => {
  if (seconds === undefined || seconds === null || seconds <= 0) return '';
  
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  
  let result = '';
  if (h > 0) result += `${h}小时`;
  if (m > 0) result += `${m}分`;
  if (s > 0) result += `${s}秒`;
  
  return result || `${seconds}秒`;
};

const tableRef = ref<any>(null);
let sortableInstance: Sortable | null = null;

const initSortable = () => {
  if (sortableInstance) sortableInstance.destroy();

  if (!tableRef.value) return;
  const el = tableRef.value.$el.querySelector('.el-table__body-wrapper tbody');
  if (!el) return;

  sortableInstance = Sortable.create(el, {
    handle: '.drag-handle',
    animation: 150,
    onEnd: async ({ newIndex, oldIndex }: any) => {
      if (newIndex === oldIndex) return;
      
      const currList = [...tasks.value];
      const targetRow = currList.splice(oldIndex, 1)[0];
      currList.splice(newIndex, 0, targetRow);
      
      // Update local state
      tasks.value = currList;
      
      // Call API
      const device = devices.value.find(d => d.id === currentDeviceId.value);
      if (!device) return;
      
      try {
        const client = getDeviceApi(device.url);
        await client.post('/task/reorder', currList.map(t => t.id));
        ElMessage.success('顺序已更新');
      } catch (err) {
        ElMessage.error('排序保存失败');
        fetchTasks(currentDeviceId.value, true); // Revert
      }
    }
  });
};

onMounted(async () => {
  if (taskStore.devices.length > 0) {
    // Cache hit: trigger background refresh, don't wait
    fetchDevices();
  } else {
    // No cache: must wait
    await fetchDevices();
  }

  if (currentDeviceId.value) {
      await fetchTasks(currentDeviceId.value, false);
      connectWebSocket();
  }
  
  nextTick(() => {
    initSortable();
  });
  
  // Use WS instead of polling for updates
  // But keep polling as fallback or just remove?
  // User asked to replace polling with WS.
  // So we remove the interval.
  
  /*
  pollInterval = setInterval(() => {
      if (currentDeviceId.value) {
          fetchTasks(currentDeviceId.value, true);
      }
  }, 5000) as unknown as number;
  */
});

onUnmounted(() => {
  if (ws) {
      ws.close();
      ws = null;
  }
  if (pollInterval) {
    clearInterval(pollInterval);
  }
});
</script>

<template>
  <DocPage title="集群管理" description="管理集群节点及其后台任务">
    <!-- Device Tabs -->
    <el-tabs v-model="currentDeviceId" type="card" class="device-tabs">
      <el-tab-pane
        v-for="dev in devices"
        :key="dev.id"
        :label="dev.name || dev.id"
        :name="dev.id"
      />
      <el-tab-pane name="add_new" disabled>
        <template #label>
          <el-button type="primary" link :icon="Plus" @click="deviceDialogVisible = true">添加设备</el-button>
        </template>
      </el-tab-pane>
    </el-tabs>

    <!-- Device Info & Config -->
    <div class="device-info-card" v-if="currentDevice">
      <div class="card-header">
        <div class="left">
          <span class="device-title">{{ currentDevice.name || currentDevice.id }}</span>
          <el-tag size="small" :type="currentDevice.type === 'LocalDevice' ? '' : 'success'">{{ currentDevice.type }}</el-tag>
          <span class="device-url" v-if="currentDevice.url">({{ currentDevice.url }})</span>
          <span class="device-url" style="margin-left: 10px; font-size: 10px;">ID: {{ currentDevice.id }}</span>
        </div>
        <div class="right">
          <el-button v-if="!isEditingDevice" :icon="Setting" size="small" @click="isEditingDevice = true">配置</el-button>
          <div v-else>
            <el-button size="small" @click="isEditingDevice = false">取消</el-button>
            <el-button type="primary" size="small" @click="updateDeviceConfig">保存</el-button>
          </div>
        </div>
      </div>
      
      <div class="card-content">
        <el-form :inline="true" label-width="100px" size="small">
          <el-form-item label="设备名称">
             <el-input 
               v-if="isEditingDevice" 
               v-model="currentDeviceConfig.new_name" 
               placeholder="设备显示名称" 
               style="width: 200px;"
             />
             <span v-else class="readonly-text">{{ currentDevice.name || currentDevice.id }}</span>
          </el-form-item>
          <!-- Removed Name field -->
          <el-form-item label="Python路径" style="width: 100%;">
             <el-input 
               v-if="isEditingDevice" 
               v-model="currentDeviceConfig.python_exec" 
               placeholder="默认Python解释器路径" 
               style="width: 400px;"
             />
             <span v-else class="readonly-text">{{ currentDevice.python_exec || '(默认)' }}</span>
          </el-form-item>
        </el-form>
      </div>
    </div>

    <div class="toolbar">
      <el-button type="primary" :icon="Plus" @click="openCreateDialog">新建任务</el-button>
      <el-button :icon="Refresh" @click="fetchTasks(currentDeviceId, false)">刷新列表</el-button>
    </div>

    <el-table ref="tableRef" :data="currentTasks" v-loading="loading" style="width: 100%" row-key="id">
      <el-table-column width="60" align="center" label="排序">
        <template #default>
          <el-icon class="drag-handle" style="cursor: move; font-size: 18px; color: #909399;"><Rank /></el-icon>
        </template>
      </el-table-column>
      <el-table-column prop="name" label="任务名称" width="200">
        <template #default="{ row }">
          <div class="task-name">
            {{ row.name }}
            <el-tag v-if="row.run_as_admin" type="danger" size="small" effect="plain" style="margin-left: 5px;">Admin</el-tag>
          </div>
          <div class="task-desc" v-if="row.description">{{ row.description }}</div>
          <div class="task-desc" v-if="row.schedule" style="color: #409EFF; display: flex; align-items: center; margin-top: 2px;">
            <el-icon style="margin-right: 3px;"><Clock /></el-icon>
            {{ row.schedule }}
          </div>
          <div class="task-desc" v-if="row.timeout" style="color: #E6A23C; display: flex; align-items: center; margin-top: 2px;">
            <el-icon style="margin-right: 3px;"><Clock /></el-icon>
            超时: {{ formatDuration(row.timeout) }}
          </div>
        </template>
      </el-table-column>
      
      <el-table-column prop="command" label="命令" show-overflow-tooltip />
      
      <el-table-column label="状态" width="120" align="center">
        <template #default="{ row }">
          <!-- 
            Yellow: Checking/Loading (actionLoading)
            Green: Running
            Gray: Stopped
          -->
          <el-button 
            circle
            :type="row.actionLoading ? 'warning' : (row.status.running ? 'success' : 'info')"
            :loading="row.actionLoading"
            @click="handleStatusClick(row)"
            :title="row.status.running ? '点击停止' : '点击启动'"
          >
            <template #icon>
              <component :is="row.status.running ? VideoPause : VideoPlay" v-if="!row.actionLoading" />
            </template>
          </el-button>
        </template>
      </el-table-column>

      <el-table-column label="操作" width="200" align="right">
        <template #default="{ row }">
          <el-button size="small" :icon="Document" @click="viewLogs(row)" title="详情" circle />
          <el-button size="small" type="danger" :icon="Delete" @click="handleDelete(row)" plain title="删除" circle />
        </template>
      </el-table-column>
    </el-table>

    <!-- Create Task Dialog -->
    <el-dialog v-model="dialogVisible" title="新建任务" width="500px">
      <el-form :model="form" label-width="80px">
        <el-form-item label="名称" required>
          <el-input v-model="form.name" placeholder="任务名称" />
        </el-form-item>
        <el-form-item label="设备">
          <el-select v-model="form.device_id" placeholder="选择运行设备" style="width: 100%" disabled>
            <el-option
              v-for="item in devices"
              :key="item.id"
              :label="item.name || item.id"
              :value="item.id"
            />
          </el-select>
          <div class="form-tip">默认创建在当前选中的设备上</div>
        </el-form-item>
        <el-form-item label="命令" required>
          <el-input v-model="form.command" type="textarea" placeholder="例如: python -m module.name args" />
        </el-form-item>
        <el-form-item label="工作目录">
          <el-input v-model="form.cwd" placeholder="可选: 执行目录绝对路径" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="form.description" placeholder="可选: 任务描述" />
        </el-form-item>
        <el-form-item label="定时调度">
          <el-input v-model="form.schedule" placeholder="Cron 表达式 (例如: */5 * * * *)" />
          <div class="form-tip">支持 Cron 表达式。留空表示仅手动执行。</div>
        </el-form-item>
        <el-form-item label="超时时间">
          <el-input v-model.number="form.timeout" placeholder="单位: 秒" type="number" />
          <div class="form-tip">任务运行超过指定秒数后自动停止。留空表示不限制。</div>
        </el-form-item>
        <el-collapse accordion style="margin-top: 10px; width: 100%;">
          <el-collapse-item title="自然语言解析 (实验性)">
            <el-input v-model="nlpInput" placeholder="例如: 每5分钟, 超时1小时" class="input-with-select">
              <template #append>
                <el-button @click="parseNlp">解析</el-button>
              </template>
            </el-input>
          </el-collapse-item>
        </el-collapse>
      </el-form>
      <template #footer>
        <span class="dialog-footer">
          <el-button @click="dialogVisible = false">取消</el-button>
          <el-button type="primary" @click="handleSubmitTask">{{ isEditingTask ? '保存' : '创建' }}</el-button>
        </span>
      </template>
    </el-dialog>

    <!-- Device Manager Dialog -->
    <el-dialog v-model="deviceDialogVisible" title="设备管理" width="600px">
      <div style="margin-bottom: 20px;">
        <el-table :data="devices" border style="width: 100%">
          <el-table-column prop="name" label="名称" width="150" />
          <el-table-column label="地址">
            <template #default="{ row }">
              {{ row.url || 'http://localhost:8000' }}
            </template>
          </el-table-column>
          <el-table-column label="操作" width="120" align="center">
            <template #default="{ row }">
              <el-button link type="primary" size="small" @click="handleSwitchDevice(row)">切换</el-button>
              <el-button 
                link 
                type="danger" 
                size="small" 
                @click="handleDeleteDevice(row)" 
                v-if="row.type !== 'LocalDevice'"
              >删除</el-button>
            </template>
          </el-table-column>
        </el-table>
      </div>
      
      <el-divider>添加远程设备</el-divider>
      
      <el-form :inline="true" :model="deviceForm">
        <el-form-item label="URL">
          <el-input v-model="deviceForm.url" placeholder="IP (默认端口8000)" style="width: 280px" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="addDeviceLoading" :icon="Connection" @click="handleAddDevice">添加</el-button>
        </el-form-item>
      </el-form>
    </el-dialog>
  </DocPage>
</template>

<style scoped>
.toolbar {
  margin-bottom: 20px;
  display: flex;
  gap: 10px;
}

.device-tabs {
  margin-bottom: 15px;
}

.device-info-card {
  border: 1px solid #e4e7ed;
  border-radius: 4px;
  margin-bottom: 20px;
  background-color: #fff;
}

.card-header {
  padding: 10px 15px;
  border-bottom: 1px solid #e4e7ed;
  display: flex;
  justify-content: space-between;
  align-items: center;
  background-color: #f5f7fa;
}

.device-title {
  font-weight: bold;
  font-size: 16px;
  margin-right: 10px;
}

.device-url {
  margin-left: 10px;
  color: #888;
  font-size: 12px;
}

.card-content {
  padding: 15px;
}

.readonly-text {
  color: #606266;
}

.task-name {
  font-weight: bold;
}

.task-desc {
  font-size: 12px;
  color: #888;
}

.form-tip {
  font-size: 12px;
  color: #909399;
  line-height: 1.2;
  margin-top: 4px;
}
</style>