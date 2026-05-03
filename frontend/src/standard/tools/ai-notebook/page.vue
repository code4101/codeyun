<template>
  <div class="ai-notebook-page">
    <aside class="notebook-pane device-pane">
      <div class="pane-title-row">
        <h1>AI协作Notebook</h1>
        <el-button
          :icon="Refresh"
          circle
          plain
          :loading="loading"
          title="刷新"
          aria-label="刷新"
          @click="refreshState"
        />
      </div>

      <section class="pane-section">
        <div class="section-label">设备</div>
        <el-select
          v-model="selectedEntryId"
          filterable
          placeholder="选择设备"
          :loading="deviceLoading"
          class="full-control"
        >
          <el-option
            v-for="device in devices"
            :key="device.id"
            :label="device.name"
            :value="device.id"
          >
            <span>{{ device.name }}</span>
            <span class="option-meta">{{ device.mode }}</span>
          </el-option>
        </el-select>
      </section>

      <section class="pane-section">
        <div class="section-label">绑定</div>
        <el-input
          v-model="bindingInput"
          placeholder="demo.ipynb"
          clearable
          spellcheck="false"
        />
        <el-button
          type="primary"
          plain
          class="full-control"
          :loading="bindingSaving"
          :disabled="!selectedEntryId"
          @click="saveBinding"
        >
          绑定 / 创建
        </el-button>
        <dl v-if="state" class="binding-meta">
          <div>
            <dt>工作目录</dt>
            <dd :title="state.binding.workdir">{{ state.binding.workdir }}</dd>
          </div>
          <div>
            <dt>文件</dt>
            <dd :title="state.notebook_path">{{ compactNotebookPath }}</dd>
          </div>
          <div>
            <dt>状态</dt>
            <dd>
              <el-tag size="small" :type="state.dirty ? 'warning' : 'success'" effect="plain">
                {{ state.dirty ? '待保存' : '已同步' }}
              </el-tag>
            </dd>
          </div>
        </dl>
      </section>

      <section class="pane-section cell-list-section">
        <div class="section-label">Cells</div>
        <div v-if="!state?.cells.length" class="empty-line">没有 cell</div>
        <button
          v-for="cell in state?.cells ?? []"
          :key="cell.cell_id"
          class="cell-row"
          :class="{ active: cell.cell_id === activeCellId }"
          type="button"
          @click="activeCellId = cell.cell_id"
        >
          <span class="cell-order">{{ cell.index + 1 }}</span>
          <span class="cell-kind">{{ cell.cell_type }}</span>
          <span class="cell-preview">{{ firstLine(cell.source) }}</span>
          <el-tag v-if="cell.stale" size="small" type="warning" effect="plain">stale</el-tag>
        </button>
      </section>
    </aside>

    <main class="notebook-pane editor-pane">
      <div class="editor-toolbar">
        <div class="active-cell-title">
          <template v-if="selectedCell">
            <span>Cell {{ selectedCell.index + 1 }}</span>
            <el-tag size="small" effect="plain">{{ selectedCell.cell_type }}</el-tag>
            <el-tag v-if="selectedCell.execution_count != null" size="small" effect="plain">
              In [{{ selectedCell.execution_count }}]
            </el-tag>
            <el-tag v-if="selectedCell.stale" size="small" type="warning" effect="plain">stale</el-tag>
          </template>
          <span v-else>Cell</span>
        </div>
        <div class="toolbar-actions">
          <el-button
            :icon="Check"
            :loading="cellSaving"
            :disabled="!canSaveCell"
            title="保存 cell 草稿"
            @click="saveCellDraft"
          >
            保存 cell
          </el-button>
          <el-button
            :icon="Check"
            :loading="fileSaving"
            :disabled="!state || running"
            title="写入 ipynb 文件"
            @click="saveNotebookFile"
          >
            保存文件
          </el-button>
          <el-button
            type="primary"
            :icon="VideoPlay"
            :loading="running"
            :disabled="!canRunCell"
            title="运行当前 cell"
            @click="runCurrentCell"
          >
            运行
          </el-button>
        </div>
      </div>

      <el-input
        v-if="selectedCell"
        v-model="cellDraft"
        type="textarea"
        resize="none"
        class="cell-editor"
        spellcheck="false"
      />
      <div v-else class="editor-empty">选择一个设备和 cell</div>
    </main>

    <aside class="notebook-pane output-pane">
      <section class="pane-section status-section">
        <div class="status-row">
          <span class="section-label">Kernel</span>
          <el-tag size="small" :type="kernelTagType" effect="plain">
            {{ state?.kernel_status ?? 'stopped' }}
          </el-tag>
        </div>
        <div class="status-grid">
          <div>
            <span>stale</span>
            <strong>{{ state?.stale_cell_ids.length ?? 0 }}</strong>
          </div>
          <div>
            <span>cell</span>
            <strong>{{ state?.cells.length ?? 0 }}</strong>
          </div>
        </div>
        <el-alert
          v-if="pageError || state?.last_error"
          :title="pageError || state?.last_error || ''"
          type="error"
          :closable="false"
          show-icon
        />
      </section>

      <section class="pane-section">
        <div class="section-label">临时代码</div>
        <el-input
          v-model="temporaryCode"
          type="textarea"
          resize="none"
          class="scratch-editor"
          spellcheck="false"
        />
        <div class="scratch-actions">
          <el-button
            type="primary"
            plain
            :icon="VideoPlay"
            :loading="runningCode"
            :disabled="!state || state.dirty || !temporaryCode.trim()"
            @click="runTemporaryCode"
          >
            运行临时代码
          </el-button>
          <el-button
            :icon="Close"
            plain
            :disabled="!state"
            @click="interruptKernel"
          >
            中断
          </el-button>
        </div>
      </section>

      <section class="pane-section outputs-section">
        <div class="section-label">输出</div>
        <div v-if="lastOutputs.length" class="output-list">
          <pre v-for="(output, index) in lastOutputs" :key="index">{{ output }}</pre>
        </div>
        <div v-else class="empty-line">暂无输出</div>
      </section>

      <section class="pane-section">
        <div class="section-label">现场</div>
        <div class="browser-placeholder">浏览器协作接口预留</div>
      </section>
    </aside>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import {
  Check,
  Close,
  Refresh,
  VideoPlay,
} from '@element-plus/icons-vue'

import {
  fetchAiNotebookState,
  interruptAiNotebook,
  runAiNotebookCell,
  runAiNotebookCode,
  saveAiNotebook,
  updateAiNotebookBinding,
  updateAiNotebookCell,
  type NotebookCell,
  type NotebookState,
} from '@/api/aiNotebook'
import { taskStore } from '@/store/taskStore'

const selectedEntryId = ref('')
const state = ref<NotebookState | null>(null)
const activeCellId = ref('')
const cellDraft = ref('')
const bindingInput = ref('')
const temporaryCode = ref('print("hello")')
const lastOutputs = ref<string[]>([])
const pageError = ref('')
const deviceLoading = ref(false)
const loading = ref(false)
const bindingSaving = ref(false)
const cellSaving = ref(false)
const fileSaving = ref(false)
const running = ref(false)
const runningCode = ref(false)

const devices = computed(() => taskStore.devices)
const selectedCell = computed<NotebookCell | null>(() => {
  const cells = state.value?.cells ?? []
  return cells.find((cell) => cell.cell_id === activeCellId.value) ?? cells[0] ?? null
})
const hasCellDraftChange = computed(() => Boolean(selectedCell.value && cellDraft.value !== selectedCell.value.source))
const canSaveCell = computed(() => Boolean(state.value && selectedCell.value && hasCellDraftChange.value && !cellSaving.value))
const canRunCell = computed(() => Boolean(state.value && selectedCell.value && !state.value.dirty && !hasCellDraftChange.value && !running.value))
const compactNotebookPath = computed(() => {
  const current = state.value
  if (!current) {
    return ''
  }
  const prefix = current.binding.workdir.replace(/[\\/]+$/, '')
  if (current.notebook_path.startsWith(prefix)) {
    return current.notebook_path.slice(prefix.length).replace(/^[\\/]+/, '')
  }
  return current.notebook_path
})
const kernelTagType = computed(() => {
  const status = state.value?.kernel_status
  if (status === 'idle') return 'success'
  if (status === 'busy' || status === 'starting') return 'warning'
  if (status === 'error') return 'danger'
  return 'info'
})

function getErrorMessage(error: unknown) {
  const candidate = error as { response?: { data?: { detail?: string } }; message?: string }
  return candidate.response?.data?.detail || candidate.message || '操作失败'
}

function firstLine(source: string) {
  const line = source.split(/\r?\n/).find((item) => item.trim())
  return line?.trim() || '空 cell'
}

function applyState(nextState: NotebookState) {
  state.value = nextState
  bindingInput.value = nextState.notebook_path
  const cells = nextState.cells
  if (!cells.some((cell) => cell.cell_id === activeCellId.value)) {
    activeCellId.value = cells[0]?.cell_id ?? ''
  }
  const currentCell = cells.find((cell) => cell.cell_id === activeCellId.value)
  cellDraft.value = currentCell?.source ?? ''
}

async function loadDevices() {
  deviceLoading.value = true
  try {
    await taskStore.fetchDevices()
    if (!selectedEntryId.value && taskStore.devices.length) {
      selectedEntryId.value = taskStore.devices[0].id
    }
  } finally {
    deviceLoading.value = false
  }
}

async function refreshState() {
  if (!selectedEntryId.value) {
    return
  }
  loading.value = true
  pageError.value = ''
  try {
    applyState(await fetchAiNotebookState(selectedEntryId.value))
  } catch (error) {
    pageError.value = getErrorMessage(error)
  } finally {
    loading.value = false
  }
}

async function saveBinding() {
  if (!selectedEntryId.value) {
    return
  }
  bindingSaving.value = true
  pageError.value = ''
  try {
    applyState(await updateAiNotebookBinding(selectedEntryId.value, { notebook_path: bindingInput.value.trim() || null }))
    lastOutputs.value = []
    ElMessage.success('Notebook 绑定已更新')
  } catch (error) {
    pageError.value = getErrorMessage(error)
  } finally {
    bindingSaving.value = false
  }
}

async function saveCellDraft(showMessage = true) {
  if (!state.value || !selectedCell.value || !selectedEntryId.value) {
    return null
  }
  cellSaving.value = true
  pageError.value = ''
  try {
    const nextState = await updateAiNotebookCell(selectedEntryId.value, selectedCell.value.cell_id, {
      notebook_hash: state.value.notebook_hash,
      source: cellDraft.value,
    })
    applyState(nextState)
    if (showMessage) {
      ElMessage.success('Cell 已保存到草稿')
    }
    return nextState
  } catch (error) {
    pageError.value = getErrorMessage(error)
    return null
  } finally {
    cellSaving.value = false
  }
}

async function saveNotebookFile() {
  if (!state.value || !selectedEntryId.value) {
    return
  }
  fileSaving.value = true
  pageError.value = ''
  try {
    let currentState = state.value
    if (hasCellDraftChange.value) {
      const draftState = await saveCellDraft(false)
      if (!draftState) {
        return
      }
      currentState = draftState
    }
    applyState(await saveAiNotebook(selectedEntryId.value, { notebook_hash: currentState.notebook_hash }))
    ElMessage.success('Notebook 文件已保存')
  } catch (error) {
    pageError.value = getErrorMessage(error)
  } finally {
    fileSaving.value = false
  }
}

async function runCurrentCell() {
  if (!state.value || !selectedCell.value || !selectedEntryId.value) {
    return
  }
  running.value = true
  pageError.value = ''
  try {
    const response = await runAiNotebookCell(selectedEntryId.value, {
      notebook_hash: state.value.notebook_hash,
      cell_id: selectedCell.value.cell_id,
    })
    lastOutputs.value = response.outputs_summary
    applyState(response.state)
  } catch (error) {
    pageError.value = getErrorMessage(error)
  } finally {
    running.value = false
  }
}

async function runTemporaryCode() {
  if (!selectedEntryId.value) {
    return
  }
  runningCode.value = true
  pageError.value = ''
  try {
    const response = await runAiNotebookCode(selectedEntryId.value, { code: temporaryCode.value })
    lastOutputs.value = response.outputs_summary
    applyState(response.state)
  } catch (error) {
    pageError.value = getErrorMessage(error)
  } finally {
    runningCode.value = false
  }
}

async function interruptKernel() {
  if (!selectedEntryId.value) {
    return
  }
  pageError.value = ''
  try {
    applyState(await interruptAiNotebook(selectedEntryId.value))
  } catch (error) {
    pageError.value = getErrorMessage(error)
  }
}

watch(selectedEntryId, () => {
  state.value = null
  activeCellId.value = ''
  cellDraft.value = ''
  lastOutputs.value = []
  void refreshState()
})

watch(activeCellId, () => {
  cellDraft.value = selectedCell.value?.source ?? ''
})

onMounted(async () => {
  await loadDevices()
  await refreshState()
})
</script>

<style scoped>
.ai-notebook-page {
  display: grid;
  grid-template-columns: minmax(260px, 300px) minmax(420px, 1fr) minmax(300px, 360px);
  height: calc(100dvh - 64px);
  min-height: 620px;
  background: #f6f8fb;
  color: #1f2937;
}

.notebook-pane {
  min-width: 0;
  min-height: 0;
  padding: 14px;
  overflow: hidden;
}

.device-pane {
  border-right: 1px solid #d8dee8;
  background: #ffffff;
}

.editor-pane {
  display: flex;
  flex-direction: column;
  gap: 10px;
  background: #fbfcfe;
}

.output-pane {
  border-left: 1px solid #d8dee8;
  background: #ffffff;
  overflow-y: auto;
}

.pane-title-row,
.editor-toolbar,
.status-row,
.scratch-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.pane-title-row h1 {
  margin: 0;
  font-size: 18px;
  font-weight: 650;
}

.pane-section {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px 0;
  border-bottom: 1px solid #edf0f5;
}

.pane-section:last-child {
  border-bottom: 0;
}

.section-label {
  font-size: 12px;
  font-weight: 650;
  color: #64748b;
}

.full-control {
  width: 100%;
}

.option-meta {
  float: right;
  color: #94a3b8;
  font-size: 12px;
}

.binding-meta {
  display: grid;
  gap: 7px;
  margin: 0;
  font-size: 12px;
}

.binding-meta div {
  display: grid;
  grid-template-columns: 54px minmax(0, 1fr);
  gap: 8px;
}

.binding-meta dt {
  color: #94a3b8;
}

.binding-meta dd {
  min-width: 0;
  margin: 0;
  overflow: hidden;
  color: #334155;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.cell-list-section {
  min-height: 0;
  overflow: hidden;
}

.cell-row {
  display: grid;
  grid-template-columns: 28px 54px minmax(0, 1fr) auto;
  align-items: center;
  width: 100%;
  min-height: 34px;
  padding: 4px 6px;
  border: 1px solid transparent;
  border-radius: 6px;
  background: transparent;
  color: inherit;
  font: inherit;
  text-align: left;
  cursor: pointer;
}

.cell-row:hover,
.cell-row.active {
  border-color: #cdd7e5;
  background: #f3f6fb;
}

.cell-order {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border-radius: 6px;
  background: #e8eef7;
  color: #475569;
  font-size: 12px;
  font-weight: 650;
}

.cell-kind {
  color: #64748b;
  font-size: 12px;
}

.cell-preview {
  min-width: 0;
  overflow: hidden;
  color: #1f2937;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.editor-toolbar {
  flex-wrap: wrap;
  padding-bottom: 8px;
  border-bottom: 1px solid #dfe5ee;
}

.active-cell-title {
  display: flex;
  align-items: center;
  min-width: 0;
  gap: 8px;
  font-weight: 650;
}

.toolbar-actions {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}

.cell-editor {
  flex: 1;
  min-height: 0;
}

:deep(.cell-editor .el-textarea__inner) {
  height: 100%;
  min-height: 420px;
  padding: 14px;
  border-radius: 8px;
  font-family: "Cascadia Code", Consolas, "Liberation Mono", monospace;
  font-size: 13px;
  line-height: 1.6;
  tab-size: 4;
}

.editor-empty,
.empty-line,
.browser-placeholder {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 80px;
  border: 1px dashed #cbd5e1;
  border-radius: 8px;
  color: #94a3b8;
  font-size: 13px;
}

.status-section {
  gap: 10px;
}

.status-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
}

.status-grid div {
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-height: 32px;
  padding: 0 10px;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  color: #64748b;
  font-size: 12px;
}

.status-grid strong {
  color: #1f2937;
  font-size: 15px;
}

.scratch-editor {
  height: 160px;
}

:deep(.scratch-editor .el-textarea__inner) {
  height: 160px;
  border-radius: 8px;
  font-family: "Cascadia Code", Consolas, "Liberation Mono", monospace;
  font-size: 12px;
  line-height: 1.55;
}

.outputs-section {
  min-height: 160px;
}

.output-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.output-list pre {
  max-height: 220px;
  margin: 0;
  padding: 10px;
  overflow: auto;
  border: 1px solid #dce3ec;
  border-radius: 8px;
  background: #0f172a;
  color: #e5e7eb;
  font-family: "Cascadia Code", Consolas, "Liberation Mono", monospace;
  font-size: 12px;
  line-height: 1.55;
  white-space: pre-wrap;
}

@media (max-width: 1180px) {
  .ai-notebook-page {
    grid-template-columns: 1fr;
    height: auto;
    min-height: 0;
  }

  .device-pane,
  .output-pane {
    border: 0;
  }
}
</style>
