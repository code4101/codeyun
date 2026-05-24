<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Refresh, Search } from '@element-plus/icons-vue'

import {
  getFanxiuProtocolSemantics,
  type FanxiuProtocolSemanticEdge,
  type FanxiuProtocolSemanticFeature,
  type FanxiuProtocolSemanticResponse,
  type FanxiuProtocolSemanticRow,
} from '@/api/fanxiu'

const DEFAULT_FEATURES: FanxiuProtocolSemanticFeature[] = [
  { key: 'bluestarsea', title: 'BlueStarSea' },
  { key: 'blld', title: 'BLLD' },
  { key: 'faze', title: 'Faze' },
  { key: 'gongfa', title: 'Gongfa' },
]

const initialParams = new URLSearchParams(window.location.search)
const feature = ref(initialParams.get('feature') || 'bluestarsea')
const query = ref(initialParams.get('query') || initialParams.get('q') || '')
const role = ref('')
const operation = ref('')
const loading = ref(false)
const response = ref<FanxiuProtocolSemanticResponse | null>(null)
const selectedPacket = ref('')
let requestSeq = 0

const features = computed(() => response.value?.available_features?.length ? response.value.available_features : DEFAULT_FEATURES)
const rows = computed(() => response.value?.items ?? [])
const edges = computed(() => response.value?.edges ?? [])
const roles = computed(() => response.value?.roles ?? [])
const operations = computed(() => response.value?.operations ?? [])
const counts = computed(() => response.value?.counts ?? null)
const selectedRow = computed(() => rows.value.find(item => item.packet === selectedPacket.value) ?? rows.value[0] ?? null)

const selectedEdges = computed(() => {
  const row = selectedRow.value
  if (!row) return edges.value
  const packet = row.packet
  const op = row.operation
  return edges.value.filter((edge) => {
    return edge.source === packet
      || edge.target === packet
      || (!!op && (edge.source === op || edge.evidence === op))
  })
})

const roleStats = computed(() => {
  const stats = counts.value?.by_role ?? {}
  return Object.entries(stats).map(([key, value]) => ({ key, value }))
})

function displayText(value: unknown, fallback = '-') {
  const text = String(value ?? '').trim()
  return text || fallback
}

function compactFields(row: FanxiuProtocolSemanticRow) {
  return row.write_fields || row.read_fields || row.assigned_fields || ''
}

function edgeLabel(edge: FanxiuProtocolSemanticEdge) {
  return `${edge.source_type}:${edge.source} -> ${edge.target_type}:${edge.target}`
}

async function loadData() {
  const seq = ++requestSeq
  loading.value = true
  try {
    const data = await getFanxiuProtocolSemantics({
      feature: feature.value,
      query: query.value.trim(),
      role: role.value,
      operation: operation.value,
      limit: 500,
      edge_limit: 800,
    })
    if (seq !== requestSeq) return
    response.value = data
    const current = selectedPacket.value
    selectedPacket.value = data.items.some(item => item.packet === current)
      ? current
      : data.items[0]?.packet ?? ''
  } catch (error) {
    console.error(error)
    ElMessage.error('加载协议语义失败')
  } finally {
    if (seq === requestSeq) loading.value = false
  }
}

function selectRow(row: FanxiuProtocolSemanticRow) {
  selectedPacket.value = row.packet
}

function clearRole() {
  role.value = ''
  void loadData()
}

function clearOperation() {
  operation.value = ''
  void loadData()
}

watch(feature, () => {
  role.value = ''
  operation.value = ''
  selectedPacket.value = ''
  void loadData()
})

watch([role, operation], () => {
  selectedPacket.value = ''
  void loadData()
})

onMounted(() => {
  void loadData()
})
</script>

<template>
  <div class="protocol-page">
    <header class="protocol-header">
      <div>
        <h1>协议语义</h1>
        <div class="protocol-meta">
          {{ response?.title || 'Fanxiu' }}
          <template v-if="counts">
            · 行 {{ counts.filtered_rows }}/{{ counts.rows }} · 边 {{ counts.filtered_edges }}/{{ counts.edges }}
          </template>
        </div>
      </div>
      <el-button :icon="Refresh" :loading="loading" @click="loadData">刷新</el-button>
    </header>

    <div class="protocol-toolbar">
      <el-radio-group v-model="feature" size="small">
        <el-radio-button v-for="item in features" :key="item.key" :label="item.key">
          {{ item.title }}
        </el-radio-button>
      </el-radio-group>
      <el-input
        v-model="query"
        class="query-input"
        clearable
        :prefix-icon="Search"
        placeholder="packet / handler / 字段"
        @keyup.enter="loadData"
        @clear="loadData"
      />
      <el-button type="primary" :icon="Search" :loading="loading" @click="loadData">搜索</el-button>
      <el-select v-model="role" class="compact-select" clearable placeholder="角色" @clear="clearRole">
        <el-option v-for="item in roles" :key="item" :label="item" :value="item" />
      </el-select>
      <el-select v-model="operation" class="compact-select operation-select" clearable filterable placeholder="操作" @clear="clearOperation">
        <el-option v-for="item in operations" :key="item" :label="item" :value="item" />
      </el-select>
    </div>

    <div class="role-strip" v-if="roleStats.length">
      <button
        v-for="item in roleStats"
        :key="item.key"
        class="role-chip"
        :class="{ active: role === item.key }"
        type="button"
        @click="role = role === item.key ? '' : item.key"
      >
        <span>{{ item.key }}</span>
        <b>{{ item.value }}</b>
      </button>
    </div>

    <main class="protocol-workbench" v-loading="loading">
      <section class="packet-pane">
        <el-table
          :data="rows"
          height="100%"
          highlight-current-row
          table-layout="auto"
          :fit="false"
          :current-row-key="selectedPacket"
          row-key="packet"
          @row-click="selectRow"
        >
          <el-table-column prop="packet" label="packet" min-width="230" show-overflow-tooltip />
          <el-table-column prop="operation" label="操作" min-width="130" show-overflow-tooltip />
          <el-table-column prop="role" label="角色" min-width="210" show-overflow-tooltip />
          <el-table-column prop="authority_class" label="边界" min-width="210" show-overflow-tooltip />
        </el-table>
      </section>

      <section class="detail-pane" v-if="selectedRow">
        <div class="detail-title-row">
          <h2>{{ selectedRow.packet }}</h2>
          <span class="packet-id">{{ selectedRow.id }}</span>
        </div>
        <div class="detail-grid">
          <div><span>方向</span><strong>{{ displayText(selectedRow.direction) }}</strong></div>
          <div><span>操作</span><strong>{{ displayText(selectedRow.operation) }}</strong></div>
          <div><span>角色</span><strong>{{ displayText(selectedRow.role) }}</strong></div>
          <div><span>边界</span><strong>{{ displayText(selectedRow.authority_class) }}</strong></div>
          <div><span>handler</span><strong>{{ displayText(selectedRow.handler_names || selectedRow.net_function) }}</strong></div>
          <div><span>flow</span><strong>{{ displayText(selectedRow.flow_kind) }}</strong></div>
        </div>

        <div class="detail-block">
          <div class="block-label">字段</div>
          <p>{{ displayText(compactFields(selectedRow)) }}</p>
        </div>
        <div class="detail-block" v-if="selectedRow.state_sinks || selectedRow.semantic_note">
          <div class="block-label">状态与语义</div>
          <p>{{ displayText(selectedRow.state_sinks || selectedRow.semantic_note) }}</p>
          <p v-if="selectedRow.state_sinks && selectedRow.semantic_note" class="note-text">
            {{ selectedRow.semantic_note }}
          </p>
        </div>

        <div class="edge-title">相关边 {{ selectedEdges.length }}</div>
        <el-table :data="selectedEdges" max-height="360" table-layout="auto" :fit="false">
          <el-table-column label="关系" min-width="150">
            <template #default="{ row }">
              {{ row.edge }}
            </template>
          </el-table-column>
          <el-table-column label="链路" min-width="360" show-overflow-tooltip>
            <template #default="{ row }">
              {{ edgeLabel(row) }}
            </template>
          </el-table-column>
          <el-table-column prop="evidence" label="证据" min-width="220" show-overflow-tooltip />
        </el-table>
      </section>

      <section class="detail-pane empty-detail" v-else>
        没有匹配的协议行
      </section>
    </main>
  </div>
</template>

<style scoped>
.protocol-page {
  display: flex;
  flex-direction: column;
  gap: 12px;
  height: calc(100vh - 92px);
  padding: 20px 24px;
  color: #182234;
}

.protocol-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}

.protocol-header h1 {
  margin: 0;
  font-size: 24px;
  line-height: 1.2;
}

.protocol-meta {
  margin-top: 6px;
  color: #697589;
  font-size: 13px;
}

.protocol-toolbar {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}

.query-input {
  width: 260px;
}

.compact-select {
  width: 180px;
}

.operation-select {
  width: 210px;
}

.role-strip {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.role-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border: 1px solid #d8dfeb;
  background: #fff;
  color: #45566f;
  padding: 4px 8px;
  font-size: 12px;
  cursor: pointer;
}

.role-chip.active {
  border-color: #2f7de1;
  color: #1f65bd;
  background: #edf5ff;
}

.role-chip b {
  font-weight: 600;
}

.protocol-workbench {
  display: grid;
  grid-template-columns: minmax(520px, 48%) minmax(520px, 1fr);
  min-height: 0;
  flex: 1;
  border: 1px solid #dce3ee;
  background: #fff;
}

.packet-pane {
  min-width: 0;
  border-right: 1px solid #dce3ee;
}

.detail-pane {
  min-width: 0;
  overflow: auto;
  padding: 18px 20px 22px;
}

.empty-detail {
  display: flex;
  align-items: center;
  justify-content: center;
  color: #78869a;
}

.detail-title-row {
  display: flex;
  align-items: baseline;
  gap: 10px;
  margin-bottom: 12px;
}

.detail-title-row h2 {
  margin: 0;
  font-size: 20px;
}

.packet-id {
  color: #7b8799;
  font-size: 13px;
}

.detail-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px 14px;
  padding-bottom: 14px;
  border-bottom: 1px solid #edf0f5;
}

.detail-grid div {
  min-width: 0;
}

.detail-grid span,
.block-label,
.edge-title {
  display: block;
  color: #7b8799;
  font-size: 12px;
  line-height: 1.5;
}

.detail-grid strong {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
  font-weight: 600;
}

.detail-block {
  padding: 14px 0;
  border-bottom: 1px solid #edf0f5;
}

.detail-block p {
  margin: 4px 0 0;
  line-height: 1.65;
  white-space: pre-wrap;
  word-break: break-word;
}

.note-text {
  color: #5d6b80;
}

.edge-title {
  margin: 16px 0 8px;
}

@media (max-width: 1180px) {
  .protocol-page {
    height: auto;
  }

  .protocol-workbench {
    grid-template-columns: 1fr;
  }

  .packet-pane {
    height: 420px;
    border-right: none;
    border-bottom: 1px solid #dce3ee;
  }
}
</style>
