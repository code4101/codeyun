<script setup lang="ts">
import { ref, onMounted, onUnmounted, nextTick, computed, watch } from 'vue';
import { useRouter, useRoute } from 'vue-router';
import DocPage from '@/components/DocPage.vue';
import api, { getDeviceEntryPath } from '@/api';
import { ElMessage, ElMessageBox } from 'element-plus';
import { Plus, VideoPlay, VideoPause, Delete, Document, Connection, Setting, Refresh, Rank, Clock } from '@element-plus/icons-vue';
import { taskStore, type Task, type Device } from '@/store/taskStore';
import Sortable from 'sortablejs';

const router = useRouter();
const route = useRoute();
const devices = computed(() => taskStore.devices);
const currentDevice = computed(() => {
  return taskStore.devices.find(d => d.id === currentDeviceId.value);
});

const currentTasks = computed(() => {
  return taskStore.tasks[currentDeviceId.value] || [];
});
const getDeviceTypeTagType = (deviceType?: string): 'success' | undefined => {
  return deviceType === 'LocalDevice' ? undefined : 'success';
};
const getDeviceEntryMeta = (device: Device) => {
  if (device.mode === 'local') {
    return '本地入口';
  }

  if (!device.server_url) {
    return '远程入口';
  }
  try {
    return `远程 · ${new URL(device.server_url).host}`;
  } catch {
    return `远程 · ${device.server_url.replace(/^https?:\/\//, '')}`;
  }
};
const isLoopbackHost = (host: string) => {
  const normalized = host.trim().toLowerCase();
  return normalized === 'localhost' || normalized === '127.0.0.1' || normalized === '::1' || normalized === '[::1]';
};
const loading = ref(false); // Initial loading
const dialogVisible = ref(false);
const deviceDialogVisible = ref(false);
const currentDeviceId = ref<string>(
  Array.isArray(route.query.entry_id)
    ? (route.query.entry_id[0] || '')
    : ((route.query.entry_id as string) || (Array.isArray(route.query.device_id) ? (route.query.device_id[0] || '') : ((route.query.device_id as string) || '')))
);
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
  mode: 'remote' as 'local' | 'remote',
  device_id: '',
  server_url: '',
  name: '',
  token: ''
});

const currentDeviceConfig = ref({
  new_name: '',
  server_url: '',
  token: ''
});

const updateDeviceConfig = async () => {
  const device = devices.value.find(d => d.id === currentDeviceId.value);
  if (!device) return;

  try {
    await taskStore.updateDevice(device.id, {
        name: currentDeviceConfig.value.new_name,
        server_url: device.mode === 'remote' ? currentDeviceConfig.value.server_url : undefined,
        token: currentDeviceConfig.value.token
    });
    ElMessage.success('设备配置已更新');
    isEditingDevice.value = false;
  } catch (err: any) {
    ElMessage.error(err.response?.data?.detail || '更新失败');
  }
};

let taskPollTimer: number | null = null;

const stopTaskPolling = () => {
  if (taskPollTimer) {
    window.clearInterval(taskPollTimer);
    taskPollTimer = null;
  }
};

const startTaskPolling = (entryId: string) => {
  stopTaskPolling();
  if (!entryId) return;
  taskPollTimer = window.setInterval(() => {
    if (currentDeviceId.value === entryId) {
      fetchTasks(entryId, true);
    }
  }, 3000);
};

const syncDeviceConfig = () => {
  const device = devices.value.find(d => d.id === currentDeviceId.value);
  if (device) {
    currentDeviceConfig.value = {
      new_name: device.name || device.device_id,
      server_url: device.server_url || '',
      token: ''
    };
  }
};

const startEditingDevice = () => {
  syncDeviceConfig();
  isEditingDevice.value = true;
};

// Watch for device switching to refresh tasks and restart polling
watch(currentDeviceId, async (newId, oldId) => {
  if (newId && newId !== oldId) {
    isEditingDevice.value = false;
    deviceError.value = false;
    stopTaskPolling();
    await fetchTasks(newId, false);
    startTaskPolling(newId);
  } else if (!newId) {
    stopTaskPolling();
  }
});

const fetchDevices = async () => {
  // Try to use the store's action which now calls /api/devices
  try {
    await taskStore.fetchDevices();
    
    // Auto-select first device if none selected
    if (taskStore.devices.length > 0) {
       if (!currentDeviceId.value) {
           currentDeviceId.value = taskStore.devices[0].id;
       } else {
           const exists = taskStore.devices.find(d => d.id === currentDeviceId.value);
           if (exists) {
               currentDeviceId.value = exists.id;
           } else {
               const legacyDevice = taskStore.devices.find(d => d.device_id === currentDeviceId.value);
               currentDeviceId.value = legacyDevice?.id || taskStore.devices[0].id;
           }
       }
    } else {
        // No devices, clear selection
        currentDeviceId.value = '';
    }

    // Update current device config if selected
    const current = taskStore.devices.find(d => d.id === currentDeviceId.value);
    if (current) {
      // No need to sync here either, it's done when "Config" button is clicked
      if (isEditingDevice.value) {
        // If somehow we are editing, close it
        isEditingDevice.value = false;
      }
    } else {
        // If device was selected but not found in list (e.g. deleted), clear selection
        if (currentDeviceId.value) {
            currentDeviceId.value = '';
        }
    }
  } catch (err) {
    console.error('Failed to fetch devices', err);
    ElMessage.error('获取设备列表失败');
  }
};

const deviceError = ref(false);
const tokenDialogVisible = ref(false);
const tokenForm = ref({
    token: ''
});

const openTokenDialog = () => {
    tokenForm.value.token = '';
    tokenDialogVisible.value = true;
};

const handleUpdateToken = async () => {
    if (!tokenForm.value.token) {
        ElMessage.warning('请输入新的 Token');
        return;
    }
    
    const device = devices.value.find(d => d.id === currentDeviceId.value);
    if (!device) return;
    
    try {
        await taskStore.updateDevice(device.id, {
            token: tokenForm.value.token
        });
        ElMessage.success('Token 已更新');
        tokenDialogVisible.value = false;
        deviceError.value = false;
        fetchTasks(device.id, false); // Retry fetching tasks
    } catch (err: any) {
        ElMessage.error(err.response?.data?.detail || '更新失败');
    }
};

const fetchTasks = async (deviceId: string, isPolling: boolean) => {
    if (!deviceId) return;
    
    // Check if we have cached tasks
    const hasCache = taskStore.tasks[deviceId] && taskStore.tasks[deviceId].length > 0;
    
    // Don't show global loading if we have cache or are polling
    if (!isPolling && !hasCache) {
        loading.value = true;
    }
    
    const device = devices.value.find(d => d.id === deviceId);
    if (!device) {
        loading.value = false;
        return;
    }
    
    try {
        const response = await api.get(getDeviceEntryPath(deviceId, '/task/'));
        
        const tasks = response.data;
        tasks.forEach((t: Task) => {
             t.entry_id = deviceId;
        });

        // Update store
        taskStore.tasks[deviceId] = tasks;
        deviceError.value = false;
        
    } catch (err: any) {
        console.error('Failed to fetch tasks', err);
        if (!isPolling) {
            if (err.response?.status === 401 || err.response?.status === 502 || err.code === 'ERR_NETWORK') {
                deviceError.value = true;
                if (!isPolling && !hasCache) {
                   ElMessage.error('无法通过平台代理连接设备，请检查后端地址或 Token');
                }
            }
        } else {
             if (err.response?.status === 401 || err.response?.status === 502) {
                 deviceError.value = true;
             }
        }
    } finally {
        if (!isPolling) loading.value = false;
    }
};

const handleStatusClick = async (task: Task) => {
    const device = devices.value.find(d => d.id === task.entry_id);
    if (!device) return;
    
    task.actionLoading = true;
    try {
        const action = task.status.running ? 'stop' : 'start';
        await api.post(getDeviceEntryPath(device.id, `/task/${task.id}/${action}`));
        
        // Optimistic update
        task.status.running = !task.status.running;
        
        // Refresh after delay
        setTimeout(() => fetchTasks(device.id, true), 1000);
        
    } catch (err: any) {
        ElMessage.error(err.response?.data?.detail || '操作失败');
    } finally {
        task.actionLoading = false;
    }
};

const handleSwitchDevice = (device: Device) => {
    currentDeviceId.value = device.id;
    deviceDialogVisible.value = false;
};

const openCreateDialog = () => {
    if (!currentDeviceId.value) {
        ElMessage.warning('请先选择一个设备');
        return;
    }
    
    isEditingTask.value = false;
    currentTaskId.value = '';
    form.value = {
        name: '',
        command: '',
        cwd: '',
        description: '',
        device_id: currentDevice.value?.device_id || '',
        run_as_admin: false,
        schedule: '',
        timeout: null
    };
    dialogVisible.value = true;
};

const handleSubmitTask = async () => {
    if (!form.value.name || !form.value.command) {
        ElMessage.warning('名称和命令必填');
        return;
    }
    
    const entryId = currentDeviceId.value;
    const device = devices.value.find(d => d.id === entryId);
    if (!device) return;
    
    try {
        if (isEditingTask.value) {
            // Update not implemented in UI yet, but API supports it
            // For now, assume create only or re-create
        } else {
            await api.post(getDeviceEntryPath(entryId, '/task/create'), form.value);
            ElMessage.success('任务创建成功');
        }
        
        dialogVisible.value = false;
        fetchTasks(entryId, false);
        
    } catch (err: any) {
        ElMessage.error(err.response?.data?.detail || '创建失败');
    }
};

const handleAddDevice = async () => {
  if (!deviceForm.value.token.trim()) {
    ElMessage.warning('Token is required');
    return;
  }

  if (deviceForm.value.mode === 'remote') {
    if (!deviceForm.value.device_id.trim()) {
      ElMessage.warning('远程模式必须填写设备 ID');
      return;
    }

    let serverUrl = deviceForm.value.server_url.trim();
    if (!serverUrl) {
      ElMessage.warning('远程模式必须填写后端地址');
      return;
    }

    if (!serverUrl.startsWith('http://') && !serverUrl.startsWith('https://')) {
      serverUrl = 'http://' + serverUrl;
      deviceForm.value.server_url = serverUrl;
    }

    try {
      const urlObj = new URL(serverUrl);
      if (isLoopbackHost(urlObj.hostname)) {
        ElMessage.warning('localhost、127.0.0.1、::1 不能作为远程设备后端地址，请改用本地设备模式');
        return;
      }
      if (!urlObj.port) {
        urlObj.port = '8000';
        serverUrl = urlObj.toString();
      }
      if (serverUrl.endsWith('/')) {
        serverUrl = serverUrl.slice(0, -1);
      }
      deviceForm.value.server_url = serverUrl;
    } catch (e) {
      console.warn('Server URL parse failed, using raw value', e);
    }
  }
  
  addDeviceLoading.value = true;
  
  try {
    const newDevice = await taskStore.addDevice({
        mode: deviceForm.value.mode,
        device_id: deviceForm.value.mode === 'remote' ? deviceForm.value.device_id.trim() : undefined,
        name: deviceForm.value.name,
        server_url: deviceForm.value.mode === 'remote' ? deviceForm.value.server_url : undefined,
        token: deviceForm.value.token.trim()
    });
    
    ElMessage.success('Device added successfully');
    
    deviceForm.value = { mode: 'remote', device_id: '', server_url: '', name: '', token: '' };
    currentDeviceId.value = newDevice.id;
    deviceDialogVisible.value = false;
    
  } catch (err: any) {
    console.error(err);
    const errorMsg = err.response?.data?.detail || err.message || 'Failed to add device';
    ElMessage.error(errorMsg);
  } finally {
    addDeviceLoading.value = false;
  }
};

const handleDeleteDevice = async (device: Device) => {
  try {
    await ElMessageBox.confirm(`确定要移除设备 "${device.name}" 的关联吗? (不会影响设备本身运行)`, '警告', {
      type: 'warning',
      confirmButtonText: '移除',
      cancelButtonText: '取消'
    });
    
    await taskStore.removeDevice(device.id);
    ElMessage.success('设备关联已移除');
    
    // Refresh list handled by store, just check selection
    if (taskStore.devices.length > 0) {
        if (!taskStore.devices.find(d => d.id === currentDeviceId.value)) {
            currentDeviceId.value = taskStore.devices[0].id;
        }
    } else {
        currentDeviceId.value = '';
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
    
    const device = devices.value.find(d => d.id === task.entry_id);
    if (!device) return;
    
    await api.delete(getDeviceEntryPath(device.id, `/task/${task.id}`));
    ElMessage.success('Task deleted');
    await fetchTasks(task.entry_id || currentDeviceId.value, false);
  } catch (err) {
    // Cancelled
  }
};

const viewLogs = (task: Task) => {
  router.push({ name: 'TaskLogs', params: { id: task.id }, query: { entry_id: task.entry_id } });
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
const tabsRef = ref<any>(null);
let sortableInstance: Sortable | null = null;
let tabsSortableInstance: Sortable | null = null;

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
      
      const currList = [...currentTasks.value];
      const targetRow = currList.splice(oldIndex, 1)[0];
      currList.splice(newIndex, 0, targetRow);
      
      // Update local state
      taskStore.tasks[currentDeviceId.value] = currList;
      
      // Call API
      const device = devices.value.find(d => d.id === currentDeviceId.value);
      if (!device) return;
      
      try {
        await api.post(getDeviceEntryPath(device.id, '/task/reorder'), currList.map(t => t.id));
        ElMessage.success('顺序已更新');
      } catch (err) {
        ElMessage.error('排序保存失败');
        fetchTasks(currentDeviceId.value, true); // Revert
      }
    }
  });
};

const initDeviceSortable = () => {
  if (tabsSortableInstance) tabsSortableInstance.destroy();
  if (!tabsRef.value) return;
  
  const navEl = tabsRef.value.$el.querySelector('.el-tabs__nav');
  if (!navEl) return;
  
  tabsSortableInstance = Sortable.create(navEl, {
    animation: 150,
    filter: '.is-disabled', // The Add Device tab is disabled
    onMove: (evt: any) => {
        // Prevent dragging past the last element (Add Device)
        // If the target element is disabled, don't allow dropping there
        if (evt.related.classList.contains('is-disabled')) return false;
        return true;
    },
    onEnd: async ({ newIndex, oldIndex }: any) => {
      if (newIndex === oldIndex) return;
      
      const currList = [...taskStore.devices];
      if (oldIndex >= currList.length || newIndex >= currList.length) return;
      
      const targetDev = currList.splice(oldIndex, 1)[0];
      currList.splice(newIndex, 0, targetDev);
      
      taskStore.devices = currList;
      
      try {
          await taskStore.reorderDevices(currList.map(d => d.id));
          ElMessage.success('设备顺序已更新');
      } catch (err) {
          ElMessage.error('设备排序保存失败');
          fetchDevices();
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
      startTaskPolling(currentDeviceId.value);
  }
  
  nextTick(() => {
    initSortable();
    initDeviceSortable();
  });
  
});

onUnmounted(() => {
  stopTaskPolling();
  if (sortableInstance) sortableInstance.destroy();
  if (tabsSortableInstance) tabsSortableInstance.destroy();
});
</script>

<template>
  <DocPage title="集群管理" :description="`管理集群节点及其后台任务。\n\n注意：每台设备的任务列表是全平台共享的，即对这台机器的统一状态管理。所有用户操作的都是同一份任务列表，请谨慎修改，以免影响他人使用。`">
    <!-- Device Tabs -->
    <el-tabs v-model="currentDeviceId" type="card" class="device-tabs" ref="tabsRef">
      <el-tab-pane
        v-for="dev in devices"
        :key="dev.id"
        :name="dev.id"
      >
        <template #label>
          <div class="device-tab-label">
            <span class="device-tab-name">{{ dev.name || dev.device_id }}</span>
            <span class="device-tab-meta">{{ getDeviceEntryMeta(dev) }}</span>
          </div>
        </template>
      </el-tab-pane>
      <el-tab-pane name="add_new" disabled>
        <template #label>
          <el-button link :icon="Plus" @click="deviceDialogVisible = true" class="add-device-btn">添加设备</el-button>
        </template>
      </el-tab-pane>
    </el-tabs>

    <!-- Device Info & Config -->
    <div class="device-info-card" v-if="currentDevice">
      <div class="card-header">
        <div class="left">
          <span class="device-title">{{ currentDevice.name || currentDevice.device_id }}</span>
          <el-tag size="small" :type="getDeviceTypeTagType(currentDevice.type)">{{ currentDevice.type }}</el-tag>
          <span v-if="currentDevice.mode === 'remote'" class="device-url">({{ currentDevice.server_url }})</span>
        </div>
        <div class="right">
          <el-button 
            type="info" 
            link 
            size="small" 
            :icon="Delete" 
            @click="handleDeleteDevice(currentDevice)"
            style="margin-right: 10px; color: #909399;"
          >移除设备</el-button>
          <el-button v-if="deviceError" type="danger" size="small" :icon="Connection" @click="openTokenDialog">重连 / 更新 Token</el-button>
          <el-button v-if="!isEditingDevice" :icon="Setting" size="small" @click="startEditingDevice">配置</el-button>
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
             <span v-else class="readonly-text">{{ currentDevice.name || currentDevice.device_id }}</span>
          </el-form-item>
          <el-form-item v-if="currentDevice.mode === 'remote'" label="后端地址">
             <el-input
               v-if="isEditingDevice"
               v-model="currentDeviceConfig.server_url"
               placeholder="例如 http://192.168.1.5:8000"
               style="width: 280px;"
             />
             <span v-else class="readonly-text">{{ currentDevice.server_url || '-' }}</span>
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
          <el-input v-model="form.device_id" disabled />
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
          <el-table-column label="模式" width="100">
            <template #default="{ row }">
              {{ row.mode === 'local' ? '本地' : '远程' }}
            </template>
          </el-table-column>
          <el-table-column prop="device_id" label="设备ID" width="260" />
          <el-table-column label="地址">
            <template #default="{ row }">
              {{ row.mode === 'local' ? '平台后端本地直达' : (row.server_url || '-') }}
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
              >移除</el-button>
            </template>
          </el-table-column>
        </el-table>
      </div>
      
      <el-divider>添加设备</el-divider>
      
      <el-form :model="deviceForm" label-width="80px">
        <el-form-item label="接入模式" required>
          <el-radio-group v-model="deviceForm.mode">
            <el-radio-button label="local">本地设备</el-radio-button>
            <el-radio-button label="remote">远程设备</el-radio-button>
          </el-radio-group>
          <div class="form-tip">本地设备由平台后端直接执行；远程设备由平台后端使用这条入口资产去代理访问。</div>
        </el-form-item>

        <el-form-item v-if="deviceForm.mode === 'remote'" label="设备ID" required>
            <el-input v-model="deviceForm.device_id" placeholder="目标设备的 device_id" style="width: 100%;" />
            <div class="form-tip">这是目标设备自身的稳定身份 ID，由对方管理员提供。</div>
        </el-form-item>

        <el-form-item v-if="deviceForm.mode === 'remote'" label="后端地址" required>
            <el-input v-model="deviceForm.server_url" placeholder="例如 http://192.168.1.5:8000" style="width: 100%;" />
            <div class="form-tip">这里填写的是平台后端可访问的目标设备后端地址；不允许填写 localhost、127.0.0.1、::1。</div>
        </el-form-item>
        
        <el-form-item label="别名">
            <el-input v-model="deviceForm.name" placeholder="可选: 给设备起个名字" />
        </el-form-item>
        
        <el-form-item label="Token" required>
          <el-input v-model="deviceForm.token" placeholder="请输入目标设备的 API Token" type="textarea" :rows="2" />
          <div class="form-tip">Token 属于用户自己的连接资产，填错会导致后续连接失败，但系统不会自动更正。</div>
        </el-form-item>
        
        <el-form-item>
          <el-button type="primary" :loading="addDeviceLoading" :icon="Connection" @click="handleAddDevice" style="width: 100%;">添加设备</el-button>
        </el-form-item>
      </el-form>
    </el-dialog>

    <!-- Token Update Dialog -->
    <el-dialog v-model="tokenDialogVisible" title="更新访问令牌" width="400px">
        <p style="margin-bottom: 15px; color: #E6A23C; font-size: 13px;">
            无法连接到设备。可能是 Token 已过期或网络不可达。请尝试更新 Token。
        </p>
        <el-form :model="tokenForm">
            <el-form-item label="Token">
                <el-input v-model="tokenForm.token" placeholder="输入新的 API Token" type="textarea" :rows="3" />
            </el-form-item>
        </el-form>
        <template #footer>
            <span class="dialog-footer">
                <el-button @click="tokenDialogVisible = false">取消</el-button>
                <el-button type="primary" @click="handleUpdateToken">更新并重连</el-button>
            </span>
        </template>
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

.device-tab-label {
  display: flex;
  flex-direction: column;
  line-height: 1.1;
}

.device-tab-name {
  font-weight: 500;
}

.device-tab-meta {
  color: #909399;
  font-size: 11px;
  margin-top: 3px;
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

.add-device-btn {
  color: #303133 !important;
}
.add-device-btn:hover {
  color: #409EFF !important;
}
</style>
