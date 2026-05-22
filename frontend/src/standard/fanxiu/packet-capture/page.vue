<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Connection, QuestionFilled, Refresh, SetUp } from '@element-plus/icons-vue'

import {
  getFanxiuPacketCaptureSnapshot,
  type FanxiuPacketCaptureConnection,
  type FanxiuPacketCaptureSnapshot,
} from '@/api/fanxiu'

const DEFAULT_DNS_HOSTS = 'cdn-frxxz.akbing.com\nakbing.com'

const dnsHostText = ref(DEFAULT_DNS_HOSTS)
const snapshot = ref<FanxiuPacketCaptureSnapshot | null>(null)
const baselineKeys = ref<Set<string> | null>(null)
const loading = ref(false)
const activeFilter = ref<'all' | 'mumu' | 'fake' | 'proxy' | 'new'>('all')

const dnsHosts = computed(() => {
  const seen = new Set<string>()
  return dnsHostText.value
    .split(/[\n,，\s]+/)
    .map(item => item.trim().toLowerCase())
    .filter((item) => {
      if (!item || seen.has(item)) return false
      seen.add(item)
      return true
    })
})

const summaryItems = computed(() => {
  const summary = snapshot.value?.summary ?? {}
  return [
    { label: '进程', value: summary.process_count ?? 0 },
    { label: '连接', value: summary.connection_count ?? 0 },
    { label: 'MuMu', value: summary.mumu_connection_count ?? 0 },
    { label: 'Fake IP', value: summary.fake_ip_connection_count ?? 0 },
    { label: '已映射', value: summary.mapped_connection_count ?? 0 },
  ]
})

const connectionKey = (item: FanxiuPacketCaptureConnection) => {
  const local = item.local?.label ?? ''
  const remote = item.remote?.label ?? ''
  return `${item.protocol}|${item.pid}|${local}|${remote}`
}

const currentConnectionKeys = computed(() => new Set((snapshot.value?.connections ?? []).map(connectionKey)))

const newConnectionCount = computed(() => {
  if (!baselineKeys.value) return 0
  let count = 0
  for (const key of currentConnectionKeys.value) {
    if (!baselineKeys.value.has(key)) count += 1
  }
  return count
})

const filteredConnections = computed(() => {
  const items = snapshot.value?.connections ?? []
  return items.filter((item) => {
    if (activeFilter.value === 'mumu') return item.process_group === 'mumu'
    if (activeFilter.value === 'fake') return item.is_fake_ip
    if (activeFilter.value === 'proxy') return item.process_group === 'proxy'
    if (activeFilter.value === 'new') return baselineKeys.value ? !baselineKeys.value.has(connectionKey(item)) : false
    return true
  })
})

const mappedHostLabel = (item: FanxiuPacketCaptureConnection) => {
  return item.mapped_hosts.length ? item.mapped_hosts.join(', ') : '-'
}

const connectionTagType = (item: FanxiuPacketCaptureConnection) => {
  if (item.process_group === 'mumu') return 'primary'
  if (item.process_group === 'proxy') return 'success'
  return 'info'
}

const isNewConnection = (item: FanxiuPacketCaptureConnection) => {
  return Boolean(baselineKeys.value && !baselineKeys.value.has(connectionKey(item)))
}

const refreshSnapshot = async () => {
  loading.value = true
  try {
    snapshot.value = await getFanxiuPacketCaptureSnapshot(dnsHosts.value)
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || '读取凡修抓包快照失败')
  } finally {
    loading.value = false
  }
}

const setBaseline = () => {
  baselineKeys.value = new Set(currentConnectionKeys.value)
  ElMessage.success('已设置当前连接基线')
}

const clearBaseline = () => {
  baselineKeys.value = null
}

onMounted(() => {
  refreshSnapshot()
})
</script>

<template>
  <div class="fanxiu-packet-capture-page">
    <header class="page-header">
      <div>
        <h2 class="page-title">凡修抓包</h2>
        <p class="page-subtitle">只读观察本机 MuMu、Clash 相关连接和 fake-ip 映射。</p>
      </div>
      <div class="page-actions">
        <el-button :icon="SetUp" :disabled="!snapshot" @click="setBaseline">设基线</el-button>
        <el-button :disabled="!baselineKeys" @click="clearBaseline">清基线</el-button>
        <el-button type="primary" :icon="Refresh" :loading="loading" @click="refreshSnapshot">刷新</el-button>
      </div>
    </header>

    <section class="control-row">
      <label class="field-label" for="fanxiu-packet-capture-hosts">
        DNS 探针
        <el-tooltip content="用于向 Clash 本地 DNS 查询 fake-ip 映射；每行一个域名。" placement="top">
          <el-icon class="help-icon"><QuestionFilled /></el-icon>
        </el-tooltip>
      </label>
      <el-input
        id="fanxiu-packet-capture-hosts"
        v-model="dnsHostText"
        class="host-input"
        type="textarea"
        :autosize="{ minRows: 2, maxRows: 4 }"
        placeholder="cdn-frxxz.akbing.com"
      />
    </section>

    <section class="summary-strip">
      <div v-for="item in summaryItems" :key="item.label" class="summary-item">
        <span class="summary-label">{{ item.label }}</span>
        <strong>{{ item.value }}</strong>
      </div>
      <div class="summary-item">
        <span class="summary-label">新增</span>
        <strong>{{ newConnectionCount }}</strong>
      </div>
      <div class="summary-meta">
        <span v-if="snapshot">采样 {{ snapshot.captured_at }}</span>
        <span v-if="snapshot">DNS {{ snapshot.dns_server }}</span>
      </div>
    </section>

    <el-alert
      v-for="warning in snapshot?.warnings ?? []"
      :key="warning"
      class="warning-line"
      type="warning"
      :closable="false"
      :title="warning"
    />

    <el-tabs v-model="activeFilter" class="connection-tabs">
      <el-tab-pane label="全部连接" name="all" />
      <el-tab-pane label="MuMu" name="mumu" />
      <el-tab-pane label="Fake IP" name="fake" />
      <el-tab-pane label="代理外连" name="proxy" />
      <el-tab-pane label="新增" name="new" />
    </el-tabs>

    <el-table
      class="connection-table"
      :data="filteredConnections"
      table-layout="auto"
      :fit="false"
      border
      empty-text="暂无连接"
    >
      <el-table-column label="进程" min-width="170">
        <template #default="{ row }">
          <div class="process-cell">
            <el-tag :type="connectionTagType(row)" effect="plain" size="small">{{ row.process_group }}</el-tag>
            <span class="process-name">{{ row.process_name }}</span>
            <span class="pid">#{{ row.pid }}</span>
          </div>
        </template>
      </el-table-column>
      <el-table-column label="协议" prop="protocol" width="72" />
      <el-table-column label="状态" prop="status" width="112" />
      <el-table-column label="本地" min-width="180">
        <template #default="{ row }">{{ row.local?.label ?? '-' }}</template>
      </el-table-column>
      <el-table-column label="远端" min-width="190">
        <template #default="{ row }">
          <div class="remote-cell">
            <el-icon v-if="row.is_fake_ip" class="fake-icon"><Connection /></el-icon>
            <span>{{ row.remote?.label ?? '-' }}</span>
          </div>
        </template>
      </el-table-column>
      <el-table-column label="映射域名" min-width="220">
        <template #default="{ row }">{{ mappedHostLabel(row) }}</template>
      </el-table-column>
      <el-table-column label="" width="76" align="center">
        <template #default="{ row }">
          <el-tag v-if="isNewConnection(row)" type="warning" effect="plain" size="small">新增</el-tag>
        </template>
      </el-table-column>
    </el-table>

    <section class="lower-grid">
      <div>
        <h3 class="section-title">DNS 映射</h3>
        <el-table :data="snapshot?.dns_mappings ?? []" table-layout="auto" :fit="false" border empty-text="暂无映射">
          <el-table-column label="域名" prop="host" min-width="220" />
          <el-table-column label="Fake IP" min-width="180">
            <template #default="{ row }">{{ row.ips.length ? row.ips.join(', ') : '-' }}</template>
          </el-table-column>
          <el-table-column label="错误" prop="error" min-width="160" />
        </el-table>
      </div>

      <div>
        <h3 class="section-title">相关进程</h3>
        <el-table :data="snapshot?.processes ?? []" table-layout="auto" :fit="false" border empty-text="暂无进程">
          <el-table-column label="进程" min-width="180">
            <template #default="{ row }">
              <span>{{ row.name }}</span>
              <span class="pid">#{{ row.pid }}</span>
            </template>
          </el-table-column>
          <el-table-column label="分组" prop="group" width="86" />
          <el-table-column label="路径" prop="exe" min-width="260" show-overflow-tooltip />
        </el-table>
      </div>
    </section>
  </div>
</template>

<style scoped>
.fanxiu-packet-capture-page {
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 18px 22px 28px;
  color: #1f2937;
}

.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 18px;
}

.page-title {
  margin: 0;
  font-size: 22px;
  font-weight: 650;
}

.page-subtitle {
  margin: 6px 0 0;
  color: #6b7280;
  font-size: 13px;
}

.page-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.control-row {
  display: flex;
  align-items: flex-start;
  gap: 12px;
}

.field-label {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  min-width: 76px;
  padding-top: 8px;
  color: #374151;
  font-size: 14px;
}

.help-icon {
  color: #909399;
  cursor: help;
}

.host-input {
  width: min(560px, 100%);
}

.summary-strip {
  display: flex;
  align-items: center;
  gap: 18px;
  min-height: 38px;
  padding: 0 0 10px;
  border-bottom: 1px solid #e5e7eb;
}

.summary-item {
  display: inline-flex;
  align-items: baseline;
  gap: 6px;
}

.summary-label,
.summary-meta,
.pid {
  color: #6b7280;
  font-size: 12px;
}

.summary-item strong {
  font-size: 18px;
  font-weight: 650;
}

.summary-meta {
  display: flex;
  gap: 12px;
  margin-left: auto;
}

.warning-line {
  max-width: 860px;
}

.connection-tabs {
  margin-bottom: -8px;
}

.connection-table {
  width: 100%;
}

.process-cell,
.remote-cell {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  white-space: nowrap;
}

.process-name {
  font-weight: 500;
}

.fake-icon {
  color: #2563eb;
}

.lower-grid {
  display: grid;
  grid-template-columns: minmax(320px, 0.9fr) minmax(360px, 1.1fr);
  gap: 18px;
  align-items: start;
}

.section-title {
  margin: 6px 0 10px;
  font-size: 15px;
  font-weight: 650;
}

@media (max-width: 960px) {
  .page-header,
  .control-row {
    flex-direction: column;
  }

  .page-actions,
  .summary-strip,
  .summary-meta {
    flex-wrap: wrap;
  }

  .summary-meta {
    margin-left: 0;
  }

  .lower-grid {
    grid-template-columns: 1fr;
  }
}
</style>
