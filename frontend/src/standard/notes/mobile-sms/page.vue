<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Search } from '@element-plus/icons-vue'
import StandardPagination from '@/components/StandardPagination.vue'
import {
  fetchMobileSmsMessages,
  fetchMobileSmsStats,
  type MobileSmsMessage,
  type MobileSmsStatsResponse,
} from '@/api/mobileSms'

const PAGE_SIZE_OPTIONS = [20, 50, 100, 200]

const stats = ref<MobileSmsStatsResponse | null>(null)
const messages = ref<MobileSmsMessage[]>([])
const loading = ref(false)
const keyword = ref('')
const address = ref('')
const selectedDeviceId = ref('')
const currentPage = ref(1)
const pageSize = ref(50)
const total = ref(0)

const deviceOptions = computed(() => stats.value?.devices || [])
const latestText = computed(() => {
  const latest = stats.value?.latest
  if (!latest?.date_ms) return '暂无短信'
  return `${formatSmsTime(latest.date_ms)} · ${latest.address || '未知号码'}`
})

function formatNumber(value: number | null | undefined) {
  return Number(value || 0).toLocaleString()
}

function formatSmsTime(value: number | null | undefined) {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleString()
}

function simText(row: MobileSmsMessage) {
  const parts = [
    typeof row.sim_slot_index === 'number' ? `卡${row.sim_slot_index + 1}` : '',
    row.sim_display_name || row.sim_carrier_name || '',
    typeof row.subscription_id === 'number' ? `sub ${row.subscription_id}` : '',
  ].filter(Boolean)
  return parts.join(' · ') || '-'
}

function getErrorMessage(error: unknown) {
  const candidate = error as { response?: { data?: { detail?: string } }; message?: string }
  return candidate.response?.data?.detail || candidate.message || '读取失败'
}

async function loadStats() {
  stats.value = await fetchMobileSmsStats()
}

async function loadMessages() {
  loading.value = true
  try {
    const response = await fetchMobileSmsMessages({
      page: currentPage.value,
      page_size: pageSize.value,
      device_id: selectedDeviceId.value,
      keyword: keyword.value.trim(),
      address: address.value.trim(),
    })
    messages.value = response.items
    total.value = response.total
  } catch (error) {
    ElMessage.error(getErrorMessage(error))
  } finally {
    loading.value = false
  }
}

async function reloadFromFirstPage() {
  currentPage.value = 1
  await Promise.all([loadStats(), loadMessages()])
}

async function handlePageChange() {
  await loadMessages()
}

async function handlePageSizeChange() {
  currentPage.value = 1
  await loadMessages()
}

onMounted(async () => {
  try {
    await Promise.all([loadStats(), loadMessages()])
  } catch (error) {
    ElMessage.error(getErrorMessage(error))
  }
})
</script>

<template>
  <div class="mobile-sms-page">
    <header class="page-header">
      <div class="title-block">
        <h1>短信数据</h1>
        <span>{{ formatNumber(stats?.total) }} 条 · 最新 {{ latestText }}</span>
      </div>
      <div class="filters">
        <el-select v-model="selectedDeviceId" class="device-select" size="small" placeholder="全部设备" clearable @change="reloadFromFirstPage">
          <el-option
            v-for="device in deviceOptions"
            :key="device.device_id"
            :label="`${device.device_id} · ${formatNumber(device.count)}`"
            :value="device.device_id"
          />
        </el-select>
        <el-input
          v-model="address"
          class="address-input"
          size="small"
          clearable
          placeholder="号码"
          @keyup.enter="reloadFromFirstPage"
          @clear="reloadFromFirstPage"
        />
        <el-input
          v-model="keyword"
          class="keyword-input"
          :prefix-icon="Search"
          size="small"
          clearable
          placeholder="搜索短信"
          @keyup.enter="reloadFromFirstPage"
          @clear="reloadFromFirstPage"
        />
        <el-button :icon="Search" size="small" plain @click="reloadFromFirstPage">查询</el-button>
      </div>
    </header>

    <main v-loading="loading" class="message-list">
      <table v-if="messages.length" class="sms-table">
        <thead>
          <tr>
            <th>时间</th>
            <th>号码</th>
            <th>SIM</th>
            <th>内容</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in messages" :key="row.id">
            <td class="time-cell">{{ formatSmsTime(row.date_ms) }}</td>
            <td class="address-cell">{{ row.address || '-' }}</td>
            <td class="sim-cell">{{ simText(row) }}</td>
            <td class="body-cell">{{ row.body }}</td>
          </tr>
        </tbody>
      </table>
      <div v-else class="empty-state">暂无短信</div>
    </main>

    <footer class="page-footer">
      <span>第 {{ currentPage }} 页 · {{ formatNumber(total) }} 条</span>
      <StandardPagination
        v-model:page="currentPage"
        v-model:page-size="pageSize"
        :page-size-options="PAGE_SIZE_OPTIONS"
        :total="total"
        @page-change="handlePageChange"
        @page-size-change="handlePageSizeChange"
      />
    </footer>
  </div>
</template>

<style scoped>
.mobile-sms-page {
  display: grid;
  grid-template-rows: auto minmax(0, 1fr) auto;
  height: 100%;
  min-height: 0;
  background: #f6f8fb;
  color: #111827;
}

.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  padding: 16px 18px;
  border-bottom: 1px solid #dbe3ec;
  background: #fff;
}

.title-block {
  min-width: 0;
}

.title-block h1 {
  margin: 0;
  font-size: 20px;
  font-weight: 650;
  letter-spacing: 0;
}

.title-block span {
  display: block;
  margin-top: 4px;
  overflow: hidden;
  color: #64748b;
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.filters {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 8px;
}

.device-select {
  width: 210px;
}

.address-input {
  width: 130px;
}

.keyword-input {
  width: 240px;
}

.message-list {
  min-height: 0;
  overflow: auto;
  padding: 14px 18px;
}

.sms-table {
  width: max-content;
  max-width: 100%;
  border-collapse: collapse;
  background: #fff;
  border: 1px solid #dbe3ec;
}

.sms-table th,
.sms-table td {
  padding: 9px 11px;
  border-bottom: 1px solid #edf2f7;
  text-align: left;
  vertical-align: top;
  font-size: 13px;
  line-height: 1.45;
}

.sms-table th {
  background: #f8fafc;
  color: #475569;
  font-weight: 600;
  white-space: nowrap;
}

.time-cell,
.address-cell,
.sim-cell {
  white-space: nowrap;
}

.time-cell {
  color: #475569;
}

.address-cell {
  color: #0f172a;
  font-family: Consolas, 'Courier New', monospace;
}

.sim-cell {
  color: #64748b;
}

.body-cell {
  min-width: 320px;
  max-width: min(720px, 52vw);
  color: #111827;
  white-space: pre-wrap;
  word-break: break-word;
}

.empty-state {
  padding: 28px 4px;
  color: #94a3b8;
  font-size: 13px;
}

.page-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 18px;
  border-top: 1px solid #dbe3ec;
  background: #fff;
}

.page-footer > span {
  color: #64748b;
  font-size: 12px;
}

@media (max-width: 900px) {
  .page-header,
  .page-footer {
    align-items: stretch;
    flex-direction: column;
  }

  .filters {
    flex-wrap: wrap;
  }

  .device-select,
  .address-input,
  .keyword-input {
    width: 100%;
  }

  .body-cell {
    max-width: 520px;
  }
}
</style>
