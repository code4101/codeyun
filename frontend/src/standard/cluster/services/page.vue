<template>
  <div class="services-page">
    <aside class="device-panel panel">
      <div class="panel-header">
        <h1>服务管理</h1>
        <el-button text :icon="Refresh" :loading="deviceLoading" title="刷新设备" @click="refreshDevices" />
      </div>
      <div class="device-list">
        <button
          v-for="device in devices"
          :key="device.id"
          type="button"
          class="device-item"
          :class="{ active: currentEntryId === device.id }"
          @click="selectDevice(device.id)"
        >
          <span class="device-name">{{ device.name }}</span>
          <span class="device-meta">{{ getDeviceMeta(device) }}</span>
        </button>
      </div>
      <div v-if="!devices.length && !deviceLoading" class="empty-text">暂无设备入口</div>
    </aside>

    <main class="service-panel panel">
      <section class="service-section">
        <div class="section-title-row">
          <div class="service-title">
            <h2>OCR</h2>
            <el-tag :type="ocrStateTagType" effect="plain">{{ ocrStateLabel }}</el-tag>
          </div>
          <div class="toolbar-actions">
            <el-button :icon="Document" @click="openDocs">使用文档</el-button>
          </div>
        </div>
        <div v-if="ocrService" class="metric-grid">
          <div class="metric">
            <span class="metric-label">运行设备</span>
            <span class="metric-value">{{ ocrService.device }} · {{ ocrService.lang }}</span>
          </div>
          <div class="metric">
            <span class="metric-label">实例</span>
            <span class="metric-value">{{ ocrService.instance_count }} / {{ ocrService.max_instances }}</span>
          </div>
          <div class="metric">
            <span class="metric-label">并发</span>
            <span class="metric-value">{{ ocrService.active_instance_count }}</span>
          </div>
          <div class="metric">
            <span class="metric-label">空闲释放</span>
            <span class="metric-value">{{ idleReleaseText }}</span>
          </div>
          <div class="metric">
            <span class="metric-label">调用</span>
            <span class="metric-value">{{ ocrService.call_count }}</span>
          </div>
          <div class="metric">
            <span class="metric-label">错误</span>
            <span class="metric-value">{{ ocrService.error_count }}</span>
          </div>
        </div>
        <div v-else class="empty-text">正在读取服务状态</div>
        <div v-if="ocrService?.last_error" class="error-line">{{ ocrService.last_error }}</div>
        <div class="service-actions">
          <el-button
            type="warning"
            plain
            :disabled="!userStore.isAdmin || !currentEntryId"
            :loading="resetLoading"
            @click="resetOcr"
          >
            释放 OCR
          </el-button>
        </div>
      </section>

      <section v-if="userStore.isAdmin" class="token-section">
        <div class="section-title-row">
          <h2>Token</h2>
          <div class="section-actions">
            <span class="section-meta">{{ tokens.length }} 个，{{ enabledTokenCount }} 启用</span>
            <el-button
              text
              :icon="Message"
              :loading="creatingSmsToken"
              title="新增短信上传 Token"
              aria-label="新增短信上传 Token"
              @click="createSmsToken"
            />
            <el-button
              text
              :icon="Plus"
              :loading="creatingToken"
              title="新增 Token"
              aria-label="新增 Token"
              @click="createToken"
            />
          </div>
        </div>
        <div v-if="tokensLoading" class="empty-text">加载中</div>
        <div v-else-if="tokens.length" class="token-list">
          <div v-for="token in tokens" :key="token.id" class="token-row">
            <el-switch
              size="small"
              :model-value="token.enabled"
              :loading="updatingTokenId === token.id"
              @change="(value: boolean | string | number) => toggleToken(token, Boolean(value))"
            />
            <code class="token-value" :class="{ disabled: !token.enabled }">{{ getTokenDisplayValue(token) }}</code>
            <el-button
              class="token-icon-button"
              text
              size="small"
              :icon="isTokenVisible(token.id) ? Hide : View"
              :loading="revealingTokenId === token.id"
              :title="isTokenVisible(token.id) ? '隐藏明文' : '查看明文'"
              @click="toggleTokenReveal(token)"
            />
            <span class="token-stat">调用 {{ token.call_count }}</span>
            <span class="token-stat">{{ formatTimestamp(token.last_used_at) || '未使用' }}</span>
            <el-button
              class="row-remove-button"
              text
              type="danger"
              :icon="Minus"
              :loading="deletingTokenId === token.id"
              title="删除 Token"
              aria-label="删除 Token"
              @click="deleteToken(token)"
            />
          </div>
        </div>
        <div v-else class="empty-text">暂无服务 Token</div>
      </section>
      <section v-else class="token-section readonly-token-section">
        <div class="section-title-row">
          <h2>Token</h2>
          <span class="section-meta">管理员可管理</span>
        </div>
      </section>
    </main>

    <el-drawer v-model="docsVisible" title="OCR 使用文档" size="54%">
      <div v-if="docsLoading" class="empty-text">加载中</div>
      <template v-else-if="docs">
        <section class="docs-section">
          <h3>连接方式</h3>
          <div class="connection-list">
            <div v-for="connection in docs.connections" :key="`${connection.kind}-${connection.url || connection.label}`" class="connection-row">
              <span class="connection-label">{{ getConnectionLabel(connection) }}</span>
              <code v-if="connection.url">{{ connection.url }}</code>
              <span v-else class="empty-text">公网入口未配置/待确认</span>
            </div>
          </div>
        </section>
        <section class="docs-section">
          <h3>调用示例</h3>
          <el-tabs v-model="activeDocTab">
            <el-tab-pane label="Python" name="python">
              <pre>{{ docs.examples.python }}</pre>
            </el-tab-pane>
            <el-tab-pane label="curl" name="curl">
              <pre>{{ docs.examples.curl }}</pre>
            </el-tab-pane>
            <el-tab-pane label="JavaScript" name="javascript">
              <pre>{{ docs.examples.javascript }}</pre>
            </el-tab-pane>
          </el-tabs>
        </section>
      </template>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Document, Hide, Message, Minus, Plus, Refresh, View } from '@element-plus/icons-vue'
import {
  createClusterServiceToken,
  deleteClusterServiceToken,
  fetchClusterServiceDocs,
  fetchClusterServiceSummary,
  fetchClusterServiceTokens,
  resetClusterOcrService,
  revealClusterServiceToken,
  updateClusterServiceToken,
  type CodeYunServiceStatus,
  type ServiceAccessToken,
  type ServiceDocs,
  type ServiceSummary,
} from '@/api/services'
import { taskStore, type Device } from '@/store/taskStore'
import { useUserStore } from '@/store/userStore'

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()

const devices = computed(() => taskStore.devices)
const currentEntryId = ref<string>('')
const summary = ref<ServiceSummary | null>(null)
const tokens = ref<ServiceAccessToken[]>([])
const docs = ref<ServiceDocs | null>(null)
const docsVisible = ref(false)
const activeDocTab = ref('python')
const deviceLoading = ref(false)
const summaryLoading = ref(false)
const tokensLoading = ref(false)
const docsLoading = ref(false)
const resetLoading = ref(false)
const creatingToken = ref(false)
const creatingSmsToken = ref(false)
const updatingTokenId = ref('')
const deletingTokenId = ref('')
const revealingTokenId = ref('')
const tokenPlaintexts = ref<Record<string, string>>({})
let pollTimer: number | null = null
let loadRequestToken = 0
let mountingInitialDevice = false
let initialLoadStarted = false

const ocrService = computed<CodeYunServiceStatus | null>(() => (
  summary.value?.services.find(service => service.key === 'ocr') || null
))
const enabledTokenCount = computed(() => tokens.value.filter(token => token.enabled).length)

const ocrStateLabel = computed(() => {
  if (!ocrService.value) return '未知'
  if (ocrService.value.state === 'running') return '运行中'
  if (ocrService.value.state === 'idle') return '已加载'
  return '冷启动'
})

const ocrStateTagType = computed(() => {
  if (!ocrService.value || ocrService.value.state === 'cold') return 'info'
  if (ocrService.value.state === 'running') return 'success'
  return 'warning'
})

const idleReleaseText = computed(() => {
  const service = ocrService.value
  if (!service?.loaded) return '未加载'
  if (service.active_instance_count > 0) return `${service.idle_timeout_seconds}s`
  if (typeof service.idle_remaining_seconds === 'number') {
    return `${Math.ceil(service.idle_remaining_seconds)}s`
  }
  return `${service.idle_timeout_seconds}s`
})

function getDeviceMeta(device: Device) {
  if (device.mode === 'local') return '本地'
  if (!device.server_url) return '远程'
  try {
    return new URL(device.server_url).host
  } catch {
    return device.server_url.replace(/^https?:\/\//, '')
  }
}

function getConnectionLabel(connection: { kind: string, label: string }) {
  if (connection.kind === 'local') return '本机'
  if (connection.kind === 'lan') return '局域网'
  if (connection.kind === 'public') return '公网'
  return connection.label
}

function formatTimestamp(value?: number | null) {
  if (!value) return ''
  return new Date(value * 1000).toLocaleString()
}

function getErrorMessage(error: unknown) {
  if (typeof error === 'object' && error && 'response' in error) {
    const maybeError = error as { response?: { data?: { detail?: string } }, message?: string }
    return maybeError.response?.data?.detail || maybeError.message || '请求失败'
  }
  return error instanceof Error ? error.message : '请求失败'
}

async function refreshDevices() {
  deviceLoading.value = true
  try {
    await taskStore.fetchDevices()
    if (!currentEntryId.value && devices.value.length) {
      selectDevice(devices.value[0].id, false)
    }
  } finally {
    deviceLoading.value = false
  }
}

function selectDevice(entryId: string, pushRoute = true) {
  currentEntryId.value = entryId
  summary.value = null
  tokens.value = []
  docs.value = null
  tokenPlaintexts.value = {}
  if (pushRoute) {
    void router.replace({ path: route.path, query: { ...route.query, entry_id: entryId } })
  }
}

async function loadCurrentDevice() {
  if (!currentEntryId.value) return
  const entryId = currentEntryId.value
  const requestToken = ++loadRequestToken
  summaryLoading.value = true
  tokensLoading.value = userStore.isAdmin
  const [summaryResult, tokensResult] = await Promise.allSettled([
    fetchClusterServiceSummary(entryId),
    userStore.isAdmin ? fetchClusterServiceTokens(entryId) : Promise.resolve<ServiceAccessToken[] | null>(null),
  ])
  if (requestToken !== loadRequestToken || entryId !== currentEntryId.value) {
    return
  }
  if (summaryResult.status === 'fulfilled') {
    summary.value = summaryResult.value
  } else {
    ElMessage.error(getErrorMessage(summaryResult.reason))
  }
  summaryLoading.value = false
  if (userStore.isAdmin) {
    if (tokensResult.status === 'fulfilled') {
      tokens.value = tokensResult.value || []
    } else {
      ElMessage.error(getErrorMessage(tokensResult.reason))
    }
    tokensLoading.value = false
  }
}

async function resetOcr() {
  if (!currentEntryId.value) return
  try {
    await ElMessageBox.confirm('将释放当前空闲 OCR 实例，正在执行的请求会完成后再释放。', '释放 OCR', {
      confirmButtonText: '释放',
      cancelButtonText: '取消',
      type: 'warning',
    })
  } catch {
    return
  }
  resetLoading.value = true
  try {
    await resetClusterOcrService(currentEntryId.value)
    await loadCurrentDevice()
    ElMessage.success('已请求释放 OCR')
  } catch (error) {
    ElMessage.error(getErrorMessage(error))
  } finally {
    resetLoading.value = false
  }
}

async function createToken() {
  if (!currentEntryId.value) return
  creatingToken.value = true
  try {
    const token = await createClusterServiceToken(currentEntryId.value, {})
    tokens.value = [...tokens.value, token]
    ElMessage.success('已新增服务 Token')
  } catch (error) {
    if (error instanceof Error || (typeof error === 'object' && error && 'response' in error)) {
      ElMessage.error(getErrorMessage(error))
    }
  } finally {
    creatingToken.value = false
  }
}

async function createSmsToken() {
  if (!currentEntryId.value) return
  creatingSmsToken.value = true
  try {
    const token = await createClusterServiceToken(currentEntryId.value, {
      label: '小米短信上传',
      scopes: ['mobile.sms:upload'],
      notes: 'Android 短信采集 App 上传专用 Token',
    })
    tokens.value = [...tokens.value, token]
    tokenPlaintexts.value = { ...tokenPlaintexts.value, [token.id]: token.plaintext_value || '' }
    ElMessage.success('已新增短信上传 Token')
  } catch (error) {
    if (error instanceof Error || (typeof error === 'object' && error && 'response' in error)) {
      ElMessage.error(getErrorMessage(error))
    }
  } finally {
    creatingSmsToken.value = false
  }
}

async function toggleToken(token: ServiceAccessToken, enabled: boolean) {
  if (!currentEntryId.value) return
  updatingTokenId.value = token.id
  try {
    const updated = await updateClusterServiceToken(currentEntryId.value, token.id, { enabled })
    tokens.value = tokens.value.map(item => item.id === token.id ? updated : item)
  } catch (error) {
    ElMessage.error(getErrorMessage(error))
  } finally {
    updatingTokenId.value = ''
  }
}

function isTokenVisible(tokenId: string) {
  return Boolean(tokenPlaintexts.value[tokenId])
}

function getTokenDisplayValue(token: ServiceAccessToken) {
  return tokenPlaintexts.value[token.id] || token.masked_value
}

async function toggleTokenReveal(token: ServiceAccessToken) {
  if (tokenPlaintexts.value[token.id]) {
    const next = { ...tokenPlaintexts.value }
    delete next[token.id]
    tokenPlaintexts.value = next
    return
  }
  if (!currentEntryId.value) return
  revealingTokenId.value = token.id
  try {
    const revealed = await revealClusterServiceToken(currentEntryId.value, token.id)
    tokenPlaintexts.value = { ...tokenPlaintexts.value, [token.id]: revealed.plaintext_value || '' }
  } catch (error) {
    ElMessage.error(getErrorMessage(error))
  } finally {
    revealingTokenId.value = ''
  }
}

async function deleteToken(token: ServiceAccessToken) {
  if (!currentEntryId.value) return
  try {
    await ElMessageBox.confirm('将删除这个服务 Token，外部调用会立即失效。', '删除 Token', {
      confirmButtonText: '删除',
      cancelButtonText: '取消',
      type: 'warning',
    })
  } catch {
    return
  }
  deletingTokenId.value = token.id
  try {
    await deleteClusterServiceToken(currentEntryId.value, token.id)
    tokens.value = tokens.value.filter(item => item.id !== token.id)
    const next = { ...tokenPlaintexts.value }
    delete next[token.id]
    tokenPlaintexts.value = next
    ElMessage.success('已删除服务 Token')
  } catch (error) {
    ElMessage.error(getErrorMessage(error))
  } finally {
    deletingTokenId.value = ''
  }
}

async function openDocs() {
  if (!currentEntryId.value) return
  docsVisible.value = true
  if (docs.value) return
  docsLoading.value = true
  try {
    docs.value = await fetchClusterServiceDocs(currentEntryId.value)
  } catch (error) {
    ElMessage.error(getErrorMessage(error))
  } finally {
    docsLoading.value = false
  }
}

function startPolling() {
  stopPolling()
  pollTimer = window.setInterval(() => {
    if (currentEntryId.value) {
      void fetchClusterServiceSummary(currentEntryId.value).then((payload) => {
        summary.value = payload
      }).catch(() => undefined)
    }
  }, 5000)
}

function stopPolling() {
  if (pollTimer) {
    window.clearInterval(pollTimer)
    pollTimer = null
  }
}

watch(currentEntryId, () => {
  if (mountingInitialDevice) {
    initialLoadStarted = true
  }
  void loadCurrentDevice()
})

onMounted(async () => {
  try {
    mountingInitialDevice = true
    initialLoadStarted = false
    const queryEntryId = Array.isArray(route.query.entry_id) ? route.query.entry_id[0] : route.query.entry_id
    currentEntryId.value = queryEntryId || ''
    await refreshDevices()
    if (!currentEntryId.value && devices.value.length) {
      currentEntryId.value = devices.value[0].id
    }
  } finally {
    mountingInitialDevice = false
  }
  if (!initialLoadStarted) {
    await loadCurrentDevice()
  }
  startPolling()
})

onUnmounted(() => {
  stopPolling()
})
</script>

<style scoped>
.services-page {
  min-height: 100%;
  padding: 18px;
  box-sizing: border-box;
  background: #f6f8fb;
  display: grid;
  grid-template-columns: minmax(240px, 300px) minmax(0, 1fr);
  gap: 14px;
}

.panel {
  border: 1px solid #dbe3ec;
  border-radius: 8px;
  background: #fff;
  min-width: 0;
}

.device-panel {
  padding: 14px;
  min-height: 0;
}

.service-panel {
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-width: 0;
}

.panel-header,
.section-title-row,
.toolbar-actions,
.section-actions,
.service-title {
  display: flex;
  align-items: center;
  gap: 10px;
}

.panel-header,
.section-title-row {
  justify-content: space-between;
}

h1,
h2,
h3 {
  margin: 0;
  color: #0f172a;
  letter-spacing: 0;
}

h1 {
  font-size: 20px;
}

h2 {
  font-size: 16px;
}

h3 {
  font-size: 15px;
}

.device-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-top: 12px;
}

.device-item {
  border: 1px solid transparent;
  border-radius: 8px;
  background: transparent;
  padding: 10px;
  text-align: left;
  cursor: pointer;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.device-item:hover {
  background: #f8fafc;
}

.device-item.active {
  border-color: #93c5fd;
  background: #eff6ff;
}

.device-name {
  font-weight: 600;
  color: #0f172a;
}

.device-meta,
.section-meta,
.token-stat,
.metric-label,
.empty-text {
  color: #64748b;
  font-size: 12px;
}

.service-section,
.token-section {
  border-top: 1px solid #e2e8f0;
  padding-top: 12px;
}

.service-section:first-child {
  border-top: 0;
  padding-top: 0;
}

.metric-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(130px, max-content));
  gap: 12px 28px;
  margin-top: 14px;
}

.metric {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.metric-value {
  color: #0f172a;
  font-size: 14px;
}

.error-line {
  margin-top: 10px;
  color: #b91c1c;
  font-size: 13px;
  word-break: break-all;
}

.service-actions {
  margin-top: 14px;
}

.token-list {
  margin-top: 6px;
  display: flex;
  flex-direction: column;
}

.token-row {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 34px;
  padding: 4px 0;
  border-top: 1px solid #edf2f7;
}

.token-row:first-child {
  border-top: 0;
}

code {
  font-family: Consolas, 'Courier New', monospace;
  font-size: 12px;
  color: #334155;
  word-break: break-all;
}

.token-value {
  flex: 0 1 auto;
  min-width: 120px;
  max-width: min(46vw, 360px);
}

.token-value.disabled {
  color: #94a3b8;
}

.token-icon-button {
  width: 22px;
  height: 22px;
  min-height: 22px;
  padding: 0;
  color: #64748b;
}

.token-stat {
  white-space: nowrap;
}

.token-row .row-remove-button {
  margin-left: auto;
}

.row-remove-button {
  width: 24px;
  height: 24px;
  min-height: 24px;
  padding: 0;
}

.docs-section + .docs-section {
  margin-top: 18px;
}

.connection-list {
  margin-top: 8px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.connection-row {
  display: grid;
  grid-template-columns: 58px minmax(0, 1fr);
  gap: 10px;
  align-items: start;
}

.connection-label {
  color: #334155;
  font-weight: 600;
}

pre {
  margin: 0;
  padding: 12px;
  border-radius: 8px;
  background: #0f172a;
  color: #e2e8f0;
  overflow: auto;
  font-size: 12px;
  line-height: 1.55;
}

@media (max-width: 900px) {
  .services-page {
    grid-template-columns: 1fr;
  }

  .metric-grid {
    grid-template-columns: repeat(2, minmax(120px, 1fr));
  }
}
</style>
