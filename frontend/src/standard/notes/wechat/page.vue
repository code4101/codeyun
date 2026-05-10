<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Download, QuestionFilled, Refresh, Search, Timer } from '@element-plus/icons-vue'

import {
  fetchWeChatArchiveChats,
  fetchWeChatArchiveMessageTypes,
  fetchWeChatArchiveMessages,
  fetchWeChatArchiveSyncPlan,
  fetchWeChatArchiveSyncStatus,
  fetchWeChatArchiveStatus,
  importWeChatArchive,
  setWeChatArchiveStartupSyncEnabled,
  startWeChatArchiveSync,
  type WeChatArchiveChat,
  type WeChatArchiveImportResult,
  type WeChatArchiveMessage,
  type WeChatArchiveMessageType,
  type WeChatArchiveSyncPlanItem,
  type WeChatArchiveSyncStatus,
  type WeChatArchiveStatus,
} from '@/api/wechatArchive'

const status = ref<WeChatArchiveStatus | null>(null)
const chats = ref<WeChatArchiveChat[]>([])
const messages = ref<WeChatArchiveMessage[]>([])
const messageTypes = ref<WeChatArchiveMessageType[]>([])
const selectedChatId = ref<number | null>(null)
const keyword = ref('')
const directionFilter = ref('')
const typeFilter = ref('')
const pageSize = ref(80)
const currentPage = ref(1)
const totalMessages = ref(0)
const loading = ref(false)
const messageLoading = ref(false)
const importing = ref(false)
const syncStartingMode = ref('')
const importChatName = ref('文件传输助手')
const importScrolls = ref(1)
const lastImportResult = ref<WeChatArchiveImportResult | null>(null)
const syncPlan = ref<WeChatArchiveSyncPlanItem[]>([])
const syncStatus = ref<WeChatArchiveSyncStatus | null>(null)
const startupSyncEnabled = ref(true)
const startupSwitchSaving = ref(false)
let syncStatusTimer: number | undefined

const selectedChat = computed(() => (
  chats.value.find((chat) => chat.id === selectedChatId.value) ?? null
))

const summaryItems = computed(() => [
  ['账号', status.value?.accounts ?? 0],
  ['会话', status.value?.chats ?? 0],
  ['消息', status.value?.messages ?? 0],
  ['最近采集', status.value?.latest_collected_at || '-'],
])

const selectedChatName = computed(() => selectedChat.value?.name || '')
const syncActive = computed(() => Boolean(syncStatus.value?.active))
const syncQueueText = computed(() => {
  if (!syncStatus.value) return '未读取'
  const running = syncStatus.value.queue.running
  const pending = syncStatus.value.queue.pending.length
  if (running) return pending ? `运行中，待执行 ${pending}` : '运行中'
  if (pending) return `待执行 ${pending}`
  return '空闲'
})
const latestSyncResult = computed<Record<string, any> | null>(() => syncStatus.value?.latest_result?.result ?? null)

const directionOptions = [
  { label: '全部方向', value: '' },
  { label: '发出', value: 'out' },
  { label: '收到', value: 'in' },
  { label: '系统', value: 'system' },
]

const messageTypeOptions = computed(() => [
  { label: '全部类型', value: '' },
  ...messageTypes.value.map((item) => ({
    label: `${typeLabel(item.message_type)} ${item.count}`,
    value: item.message_type || '',
  })),
])

function formatNumber(value: number | null | undefined) {
  return Number(value || 0).toLocaleString()
}

function typeLabel(value: string | null | undefined) {
  const map: Record<string, string> = {
    text: '文本',
    time: '时间',
    sys: '系统',
    recall: '撤回',
    image: '图片',
    video: '视频',
    file: '文件',
    voice: '语音',
    link: '链接',
    music: '音乐',
    location: '位置',
  }
  return value ? (map[value] || value) : '未分类'
}

function directionLabel(value: string | null | undefined) {
  if (value === 'out') return '发出'
  if (value === 'in') return '收到'
  if (value === 'system') return '系统'
  return value || '-'
}

function directionTagType(value: string | null | undefined): 'success' | 'info' | 'warning' {
  if (value === 'out') return 'success'
  if (value === 'system') return 'warning'
  return 'info'
}

function getErrorMessage(error: unknown) {
  const candidate = error as { response?: { data?: { detail?: string } }; message?: string }
  return candidate.response?.data?.detail || candidate.message || '读取失败'
}

function syncImportChatName() {
  if (selectedChatName.value) {
    importChatName.value = selectedChatName.value
  }
}

async function loadStatus() {
  status.value = await fetchWeChatArchiveStatus()
}

async function loadChats() {
  const payload = await fetchWeChatArchiveChats()
  chats.value = payload.items
  if (!selectedChatId.value && chats.value.length) {
    selectedChatId.value = chats.value[0].id
  }
}

async function loadMessageTypes() {
  const payload = await fetchWeChatArchiveMessageTypes({
    chat_id: selectedChatId.value || undefined,
  })
  messageTypes.value = payload.items
  if (typeFilter.value && !messageTypes.value.some((item) => item.message_type === typeFilter.value)) {
    typeFilter.value = ''
  }
}

async function loadSyncStatus() {
  const payload = await fetchWeChatArchiveSyncStatus()
  syncStatus.value = payload
  startupSyncEnabled.value = payload.startup_sync_enabled
}

async function loadSyncPlan() {
  const payload = await fetchWeChatArchiveSyncPlan({ max_chats: 8 })
  syncPlan.value = payload.items
}

async function loadMessages() {
  messageLoading.value = true
  try {
    const payload = await fetchWeChatArchiveMessages({
      chat_id: selectedChatId.value || undefined,
      q: keyword.value.trim() || undefined,
      direction: directionFilter.value || undefined,
      message_type: typeFilter.value || undefined,
      limit: pageSize.value,
      offset: (currentPage.value - 1) * pageSize.value,
    })
    messages.value = payload.items
    totalMessages.value = payload.total
  } catch (error) {
    ElMessage.error(getErrorMessage(error))
  } finally {
    messageLoading.value = false
  }
}

async function refreshAll() {
  loading.value = true
  try {
    await loadStatus()
    await loadChats()
    await loadMessageTypes()
    await loadMessages()
    await loadSyncStatus()
    await loadSyncPlan()
  } catch (error) {
    ElMessage.error(getErrorMessage(error))
  } finally {
    loading.value = false
  }
}

function resetAndLoadMessages() {
  currentPage.value = 1
  void loadMessageTypes()
  void loadMessages()
}

async function runImport(mode: 'loaded' | 'scroll' | 'full') {
  const chatName = importChatName.value.trim()
  if (!chatName) {
    ElMessage.warning('请填写会话名')
    return
  }

  if (mode === 'full') {
    try {
      await ElMessageBox.confirm(
        '全量导入会持续控制微信窗口并向上加载历史，期间不要同时运行其他微信自动化。',
        '确认全量导入',
        { type: 'warning', confirmButtonText: '开始', cancelButtonText: '取消' },
      )
    } catch {
      return
    }
  }

  importing.value = true
  try {
    const result = await importWeChatArchive({
      chat_name: chatName,
      mode,
      max_scrolls: mode === 'scroll' ? importScrolls.value : 0,
      exact: true,
      save_media: false,
    })
    lastImportResult.value = result
    ElMessage.success(`导入完成：新增 ${result.inserted} 条，读取 ${result.seen} 条`)
    await refreshAll()
    const matched = chats.value.find((chat) => chat.name === (result.matched_name || result.chat_name))
    if (matched) {
      selectedChatId.value = matched.id
    }
  } catch (error) {
    ElMessage.error(getErrorMessage(error))
  } finally {
    importing.value = false
  }
}

async function runSync(mode: 'incremental' | 'history') {
  const payload = {
    mode,
    chat_name: mode === 'history' ? selectedChatName.value : undefined,
    max_runtime: mode === 'history' ? 180 : 90,
    max_chats: mode === 'history' ? 1 : 6,
    max_scrolls_total: mode === 'history' ? 5 : 8,
    max_scrolls_per_chat: mode === 'history' ? 5 : 1,
    exact: true,
    save_media: false,
  }
  if (mode === 'history' && !payload.chat_name) {
    ElMessage.warning('请先选择会话')
    return
  }

  syncStartingMode.value = mode
  try {
    await startWeChatArchiveSync(payload)
    ElMessage.success('同步任务已加入后台队列')
    await loadSyncStatus()
  } catch (error) {
    ElMessage.error(getErrorMessage(error))
  } finally {
    syncStartingMode.value = ''
  }
}

async function changeStartupSyncEnabled(value: string | number | boolean) {
  startupSwitchSaving.value = true
  try {
    const payload = await setWeChatArchiveStartupSyncEnabled(Boolean(value))
    startupSyncEnabled.value = payload.startup_sync_enabled
  } catch (error) {
    ElMessage.error(getErrorMessage(error))
  } finally {
    startupSwitchSaving.value = false
  }
}

watch(selectedChatId, () => {
  syncImportChatName()
  currentPage.value = 1
  typeFilter.value = ''
  void loadMessageTypes()
  void loadMessages()
})

watch([directionFilter, typeFilter], () => {
  currentPage.value = 1
  void loadMessages()
})

onMounted(() => {
  void refreshAll()
  syncStatusTimer = window.setInterval(() => {
    void loadSyncStatus()
  }, 5000)
})

onBeforeUnmount(() => {
  if (syncStatusTimer) {
    window.clearInterval(syncStatusTimer)
  }
})
</script>

<template>
  <div class="wechat-archive-page">
    <header class="page-toolbar">
      <div class="title-line">
        <h1>微信</h1>
        <el-tooltip placement="bottom-start">
          <template #content>
            <div class="tooltip-content">
              这里查看本机微信 GUI 归档库；导入会复用当前登录的微信窗口，只采集当前账号能加载到的会话内容。
            </div>
          </template>
          <el-icon class="help-icon"><QuestionFilled /></el-icon>
        </el-tooltip>
        <el-tag v-if="status?.exists" size="small" effect="plain" type="success">已建库</el-tag>
        <el-tag v-else size="small" effect="plain" type="info">未建库</el-tag>
      </div>
      <div class="toolbar-actions">
        <el-input
          v-model="importChatName"
          class="chat-input"
          size="small"
          placeholder="会话名"
          :disabled="importing"
          @keyup.enter="runImport('loaded')"
        />
        <el-button :icon="Download" :loading="importing" size="small" plain @click="runImport('loaded')">
          导入当前
        </el-button>
        <el-input-number
          v-model="importScrolls"
          :min="1"
          :max="10000"
          :disabled="importing"
          size="small"
          controls-position="right"
        />
        <el-button :loading="importing" size="small" plain @click="runImport('scroll')">
          上翻导入
        </el-button>
        <el-button :loading="importing" size="small" type="primary" @click="runImport('full')">
          全量
        </el-button>
        <el-button :icon="Refresh" :loading="loading" size="small" text @click="refreshAll">
          刷新
        </el-button>
      </div>
    </header>

    <section class="summary-strip">
      <div v-for="[label, value] in summaryItems" :key="label" class="summary-item">
        <span>{{ label }}</span>
        <strong>{{ typeof value === 'number' ? formatNumber(value) : value }}</strong>
      </div>
    </section>

    <section class="sync-strip">
      <div class="sync-actions">
        <span class="sync-label">启动同步</span>
        <el-switch
          v-model="startupSyncEnabled"
          :loading="startupSwitchSaving"
          size="small"
          @change="changeStartupSyncEnabled"
        />
        <el-button
          :icon="Timer"
          :loading="syncStartingMode === 'incremental'"
          :disabled="syncActive"
          size="small"
          plain
          @click="runSync('incremental')"
        >
          增量同步
        </el-button>
        <el-button
          :loading="syncStartingMode === 'history'"
          :disabled="syncActive || !selectedChatName"
          size="small"
          plain
          @click="runSync('history')"
        >
          继续补历史
        </el-button>
      </div>
      <div class="sync-state">
        <span>队列：{{ syncQueueText }}</span>
        <span v-if="latestSyncResult">最近新增 {{ formatNumber(Number(latestSyncResult.inserted || 0)) }} 条</span>
      </div>
    </section>

    <section v-if="syncPlan.length" class="sync-plan">
      <div class="panel-title compact">
        <h2>本轮计划</h2>
        <span>{{ syncPlan.length }} 个</span>
      </div>
      <div class="plan-list">
        <div v-for="item in syncPlan.slice(0, 6)" :key="item.name" class="plan-row">
          <strong>{{ item.name }}</strong>
          <span>{{ formatNumber(item.message_count) }} 条</span>
          <span>分值 {{ item.score }}</span>
          <el-tag v-if="item.consecutive_failures" size="small" effect="plain" type="warning">
            失败 {{ item.consecutive_failures }}
          </el-tag>
          <el-tag v-if="!item.reached_top" size="small" effect="plain" type="info">待补历史</el-tag>
        </div>
      </div>
    </section>

    <main class="archive-layout">
      <aside class="chat-panel">
        <div class="panel-title">
          <h2>会话</h2>
          <span>{{ chats.length }} 个</span>
        </div>
        <div v-if="!chats.length" class="empty-state">
          暂无归档会话
        </div>
        <button
          v-for="chat in chats"
          :key="chat.id"
          type="button"
          class="chat-row"
          :class="{ active: chat.id === selectedChatId }"
          @click="selectedChatId = chat.id"
        >
          <div class="chat-main">
            <strong>{{ chat.name }}</strong>
            <span>{{ chat.chat_type || 'chat' }}</span>
          </div>
          <div class="chat-meta">
            <span>{{ formatNumber(chat.message_count) }} 条</span>
            <el-tag v-if="chat.reached_top" size="small" effect="plain" type="success">到顶</el-tag>
          </div>
        </button>
      </aside>

      <section class="message-panel">
        <div class="message-toolbar">
          <div class="panel-title">
            <h2>{{ selectedChatName || '全部消息' }}</h2>
            <span>{{ formatNumber(totalMessages) }} 条</span>
          </div>
          <div class="message-filters">
            <el-input
              v-model="keyword"
              class="keyword-input"
              size="small"
              placeholder="搜索内容"
              :prefix-icon="Search"
              clearable
              @keyup.enter="resetAndLoadMessages"
              @clear="resetAndLoadMessages"
            />
            <el-select v-model="directionFilter" class="filter-select" size="small">
              <el-option
                v-for="option in directionOptions"
                :key="option.value"
                :label="option.label"
                :value="option.value"
              />
            </el-select>
            <el-select v-model="typeFilter" class="filter-select" size="small">
              <el-option
                v-for="option in messageTypeOptions"
                :key="option.value"
                :label="option.label"
                :value="option.value"
              />
            </el-select>
            <el-button :icon="Search" size="small" plain @click="resetAndLoadMessages">
              查询
            </el-button>
          </div>
        </div>

        <el-table
          v-loading="messageLoading"
          class="message-table"
          :data="messages"
          row-key="id"
          table-layout="auto"
          :fit="false"
          height="calc(100vh - 278px)"
          empty-text="暂无消息"
        >
          <el-table-column prop="normalized_time" label="时间" min-width="148" />
          <el-table-column label="方向" width="78">
            <template #default="{ row }">
              <el-tag size="small" effect="plain" :type="directionTagType(row.direction)">
                {{ directionLabel(row.direction) }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="sender" label="发送人" min-width="120" />
          <el-table-column label="类型" width="86">
            <template #default="{ row }">
              {{ typeLabel(row.message_type) }}
            </template>
          </el-table-column>
          <el-table-column label="内容" min-width="420">
            <template #default="{ row }">
              <span class="message-content">{{ row.content || row.media_path || '-' }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="collected_at" label="采集时间" min-width="148" />
        </el-table>

        <div class="table-footer">
          <span class="db-path">{{ status?.db_path }}</span>
          <el-pagination
            v-model:current-page="currentPage"
            v-model:page-size="pageSize"
            background
            layout="sizes, prev, pager, next, total"
            :page-sizes="[50, 80, 120, 200]"
            :total="totalMessages"
            @current-change="loadMessages"
            @size-change="resetAndLoadMessages"
          />
        </div>
      </section>
    </main>

    <div v-if="lastImportResult" class="import-result">
      <span>最近导入</span>
      <strong>{{ lastImportResult.matched_name || lastImportResult.chat_name }}</strong>
      <span>新增 {{ lastImportResult.inserted }} 条</span>
      <span>读取 {{ lastImportResult.seen }} 条</span>
      <span>上翻 {{ lastImportResult.scroll_count }} 次</span>
    </div>
  </div>
</template>

<style scoped>
.wechat-archive-page {
  min-height: 100%;
  padding: 18px 20px 14px;
  background: #f6f8fb;
  color: #1f2937;
}

.page-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 12px;
}

.title-line,
.toolbar-actions,
.message-filters,
.panel-title,
.chat-main,
.chat-meta,
.table-footer,
.import-result,
.sync-strip,
.sync-actions,
.sync-state,
.plan-row {
  display: flex;
  align-items: center;
}

.title-line {
  gap: 8px;
  min-width: 0;
}

h1,
h2 {
  margin: 0;
  line-height: 1.2;
}

h1 {
  font-size: 22px;
  font-weight: 650;
}

h2 {
  font-size: 15px;
  font-weight: 650;
}

.help-icon {
  color: #64748b;
  cursor: help;
}

.tooltip-content {
  max-width: 320px;
  line-height: 1.6;
}

.toolbar-actions {
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 8px;
}

.chat-input {
  width: 180px;
}

.toolbar-actions :deep(.el-input-number) {
  width: 92px;
}

.summary-strip {
  display: flex;
  flex-wrap: wrap;
  gap: 1px;
  margin-bottom: 12px;
  border: 1px solid #dfe5ee;
  background: #dfe5ee;
}

.summary-item {
  min-width: 132px;
  padding: 8px 12px;
  background: #fff;
}

.summary-item span {
  display: block;
  color: #64748b;
  font-size: 12px;
}

.summary-item strong {
  display: block;
  margin-top: 3px;
  font-size: 14px;
  font-weight: 650;
}

.sync-strip {
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
  padding: 8px 10px;
  border: 1px solid #dfe5ee;
  background: #fff;
}

.sync-actions,
.sync-state {
  gap: 10px;
}

.sync-label,
.sync-state,
.plan-row span {
  color: #64748b;
  font-size: 12px;
}

.sync-plan {
  margin-bottom: 12px;
  border: 1px solid #dfe5ee;
  background: #fff;
}

.panel-title.compact {
  padding: 8px 10px;
}

.plan-list {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 1px;
  background: #edf1f6;
}

.plan-row {
  min-width: 0;
  gap: 8px;
  padding: 8px 10px;
  background: #fff;
}

.plan-row strong {
  min-width: 0;
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
}

.archive-layout {
  display: grid;
  grid-template-columns: minmax(220px, 280px) minmax(0, 1fr);
  gap: 14px;
  min-height: calc(100vh - 186px);
}

.chat-panel,
.message-panel {
  min-width: 0;
  border: 1px solid #dfe5ee;
  background: #fff;
}

.chat-panel {
  overflow: auto;
}

.panel-title {
  justify-content: space-between;
  gap: 12px;
  padding: 11px 12px;
  border-bottom: 1px solid #edf1f6;
}

.panel-title span,
.chat-main span,
.chat-meta,
.db-path,
.import-result {
  color: #64748b;
  font-size: 12px;
}

.empty-state {
  padding: 18px 12px;
  color: #94a3b8;
  font-size: 13px;
}

.chat-row {
  display: block;
  width: 100%;
  padding: 10px 12px;
  border: 0;
  border-bottom: 1px solid #edf1f6;
  background: transparent;
  text-align: left;
  cursor: pointer;
}

.chat-row:hover {
  background: #f8fafc;
}

.chat-row.active {
  background: #eef6ff;
}

.chat-main {
  justify-content: space-between;
  gap: 10px;
}

.chat-main strong {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
}

.chat-meta {
  justify-content: space-between;
  gap: 8px;
  margin-top: 6px;
}

.message-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  border-bottom: 1px solid #edf1f6;
}

.message-toolbar .panel-title {
  flex: 1;
  border-bottom: 0;
}

.message-filters {
  justify-content: flex-end;
  gap: 8px;
  padding-right: 12px;
}

.keyword-input {
  width: 190px;
}

.filter-select {
  width: 128px;
}

.message-table {
  width: 100%;
}

.message-content {
  display: block;
  max-width: 760px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.table-footer {
  justify-content: space-between;
  gap: 12px;
  padding: 10px 12px;
  border-top: 1px solid #edf1f6;
}

.db-path {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.import-result {
  gap: 10px;
  margin-top: 10px;
}

.import-result strong {
  color: #334155;
}

@media (max-width: 900px) {
  .page-toolbar,
  .message-toolbar,
  .table-footer {
    align-items: stretch;
    flex-direction: column;
  }

  .archive-layout {
    grid-template-columns: 1fr;
  }

  .sync-strip {
    align-items: stretch;
    flex-direction: column;
  }

  .message-filters {
    flex-wrap: wrap;
    justify-content: flex-start;
    padding: 0 12px 10px;
  }

  .keyword-input,
  .filter-select {
    width: 100%;
  }
}
</style>
