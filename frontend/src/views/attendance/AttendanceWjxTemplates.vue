<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { RefreshRight, VideoPlay } from '@element-plus/icons-vue'

import {
  fetchAttendanceConfig,
  fetchAttendanceRun,
  startAttendanceRun,
  type AttendanceConfigResponse,
  type AttendanceRun,
} from '@/api/attendance'

const loading = ref(false)
const running = ref(false)
const config = ref<AttendanceConfigResponse | null>(null)
const latestRun = ref<AttendanceRun | null>(null)
const runPollTimer = ref<number | null>(null)

const executionForm = reactive({
  hideText: '',
  addText: '',
})

const currentAccount = computed(() => config.value?.current_account ?? null)
const currentExecutionDevice = computed(() => config.value?.current_execution_device ?? null)
const templateInfo = computed(() => config.value?.fixed_wjx_template ?? null)

const visibleCourseNames = computed(() => {
  const result = latestRun.value?.result || {}
  if (Array.isArray(result.visible_names)) {
    return result.visible_names as string[]
  }
  if (result.after && Array.isArray((result.after as Record<string, unknown>).visible_names)) {
    return (result.after as Record<string, unknown>).visible_names as string[]
  }
  return []
})

function parseLines(raw: string) {
  return raw
    .split(/\r?\n/g)
    .map(item => item.trim())
    .filter(Boolean)
}

function stopRunPolling() {
  if (runPollTimer.value !== null) {
    window.clearTimeout(runPollTimer.value)
    runPollTimer.value = null
  }
}

async function loadPageData() {
  loading.value = true
  try {
    config.value = await fetchAttendanceConfig()
  } catch (error: any) {
    ElMessage.error(error.response?.data?.detail || '加载问卷星模板页失败')
  } finally {
    loading.value = false
  }
}

function scheduleRunPolling(runId: string) {
  stopRunPolling()
  runPollTimer.value = window.setTimeout(async () => {
    try {
      const nextRun = await fetchAttendanceRun(runId)
      latestRun.value = nextRun
      if (nextRun.status === 'running' || nextRun.status === 'pending') {
        scheduleRunPolling(runId)
        return
      }
      running.value = false
      if (nextRun.status === 'completed') {
        await loadPageData()
        ElMessage.success(nextRun.action === 'inspect' ? '已读取当前未隐藏清单' : '问卷星课程清单已更新')
      } else if (nextRun.error_message) {
        ElMessage.error(nextRun.error_message)
      }
    } catch (error: any) {
      running.value = false
      ElMessage.error(error.response?.data?.detail || '轮询运行结果失败')
    }
  }, 1800)
}

async function startRun(action: 'inspect' | 'apply') {
  if (!templateInfo.value) {
    ElMessage.error('固定问卷配置缺失')
    return
  }
  if (!currentAccount.value?.id) {
    ElMessage.warning('请先在考勤配置里配置问卷星账号')
    return
  }
  if (!currentExecutionDevice.value?.entry_id) {
    ElMessage.warning('请先在考勤配置里选择执行设备')
    return
  }

  running.value = true
  try {
    const run = await startAttendanceRun({
      action,
      hide: action === 'apply' ? parseLines(executionForm.hideText) : [],
      add: action === 'apply' ? parseLines(executionForm.addText) : [],
    })
    latestRun.value = run
    scheduleRunPolling(run.id)
  } catch (error: any) {
    running.value = false
    ElMessage.error(error.response?.data?.detail || '启动问卷星任务失败')
  }
}

onMounted(() => {
  void loadPageData()
})

onBeforeUnmount(() => {
  stopRunPolling()
})
</script>

<template>
  <div class="attendance-page">
    <section class="hero-panel">
      <div class="hero-copy">
        <div class="eyebrow">禅寺考勤 / 问卷星模板</div>
        <h1>问卷星模板</h1>
        <p>这里只有一份固定的课程清单问卷。账号和执行设备都以“考勤配置”为唯一来源，这里只负责读取当前未隐藏清单，以及批量隐藏/新增课程。</p>
      </div>
      <el-button type="primary" :icon="RefreshRight" :loading="loading" @click="loadPageData">
        刷新
      </el-button>
    </section>

    <div class="template-layout">
      <aside class="template-sidebar">
        <section class="panel-card">
          <div class="panel-header">
            <div>
              <p class="panel-kicker">固定问卷</p>
              <h2>{{ templateInfo?.name || '课程清单问卷' }}</h2>
            </div>
          </div>

          <div class="info-stack">
            <div class="info-row">
              <span class="info-label">activity_id</span>
              <code>{{ templateInfo?.activity_id || '264266843' }}</code>
            </div>
            <div class="info-row">
              <span class="info-label">设计页</span>
              <a
                v-if="templateInfo?.design_url"
                :href="templateInfo.design_url"
                target="_blank"
                rel="noreferrer"
                class="info-link"
              >
                打开问卷设计页
              </a>
              <span v-else>未配置</span>
            </div>
          </div>

          <div class="summary-card">
            <div class="summary-row">
              <span>问卷星账号</span>
              <strong>{{ currentAccount?.login_username || '未配置' }}</strong>
            </div>
            <div class="summary-row">
              <span>执行设备</span>
              <strong>{{ currentExecutionDevice?.name || '未配置' }}</strong>
            </div>
          </div>

          <div class="note-card">
            <p>这里不再维护问卷或设备。</p>
            <p>如果账号或设备没配置，请去“考勤配置”页面处理。</p>
          </div>
        </section>
      </aside>

      <section class="template-main">
        <section class="panel-card">
          <div class="panel-header">
            <div>
              <p class="panel-kicker">执行台</p>
              <h2>读取与修改</h2>
            </div>
            <el-tag type="warning" effect="plain">固定问卷</el-tag>
          </div>

          <el-form label-position="top" class="settings-form">
            <el-form-item label="隐藏课程（每行一个，可留空）">
              <el-input
                v-model="executionForm.hideText"
                type="textarea"
                :rows="5"
                placeholder="例如&#10;20260301第44届觉观&#10;20260309梵呗初阶"
              />
            </el-form-item>

            <el-form-item label="新增课程（每行一个，按顺序追加到末尾）">
              <el-input
                v-model="executionForm.addText"
                type="textarea"
                :rows="5"
                placeholder="例如&#10;20260401第45届觉观&#10;20260408第39届念住"
              />
            </el-form-item>
          </el-form>

          <div class="action-row">
            <el-button :loading="running" @click="startRun('inspect')">查看当前未隐藏清单</el-button>
            <el-button type="primary" :icon="VideoPlay" :loading="running" @click="startRun('apply')">
              一键执行修改
            </el-button>
          </div>
        </section>

        <section class="panel-card">
          <div class="panel-header">
            <div>
              <p class="panel-kicker">运行结果</p>
              <h2>当前清单与执行反馈</h2>
            </div>
            <el-tag
              v-if="latestRun"
              :type="latestRun.status === 'completed' ? 'success' : (latestRun.status === 'failed' ? 'danger' : 'warning')"
              effect="plain"
            >
              {{ latestRun.status }}
            </el-tag>
          </div>

          <div v-if="!latestRun" class="placeholder-card">
            <p>先运行一次“查看当前未隐藏清单”或“一键执行修改”，结果会显示在这里。</p>
          </div>

          <template v-else>
            <el-alert
              v-if="latestRun.error_message"
              :title="latestRun.error_message"
              type="error"
              :closable="false"
              show-icon
            />

            <div class="result-grid">
              <div class="result-block">
                <div class="result-title">当前未隐藏课程</div>
                <div v-if="visibleCourseNames.length" class="chip-list">
                  <span v-for="item in visibleCourseNames" :key="item" class="result-chip">{{ item }}</span>
                </div>
                <div v-else class="result-empty">暂无结果</div>
              </div>

              <div class="result-block" v-if="latestRun.action === 'apply'">
                <div class="result-title">本次修改摘要</div>
                <div class="summary-list">
                  <div>隐藏成功：{{ (latestRun.result.hidden_applied as string[] | undefined)?.join('，') || '无' }}</div>
                  <div>已隐藏跳过：{{ (latestRun.result.hidden_skipped as string[] | undefined)?.join('，') || '无' }}</div>
                  <div>未找到项：{{ (latestRun.result.hidden_missing as string[] | undefined)?.join('，') || '无' }}</div>
                  <div>新增成功：{{ (latestRun.result.added_applied as string[] | undefined)?.join('，') || '无' }}</div>
                </div>
              </div>
            </div>
          </template>
        </section>
      </section>
    </div>
  </div>
</template>

<style scoped>
.attendance-page {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.hero-panel {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  align-items: flex-start;
  padding: 28px 30px;
  border-radius: 24px;
  background:
    radial-gradient(circle at top left, rgba(255, 221, 147, 0.32), transparent 32%),
    linear-gradient(135deg, rgba(111, 64, 29, 0.95), rgba(31, 86, 93, 0.92));
  color: #fff7ed;
  box-shadow: 0 18px 42px rgba(53, 39, 25, 0.18);
}

.hero-copy h1 {
  margin: 6px 0 10px;
  font-size: 30px;
  line-height: 1.1;
}

.hero-copy p {
  margin: 0;
  max-width: 760px;
  line-height: 1.7;
  color: rgba(255, 247, 237, 0.9);
}

.eyebrow {
  font-size: 12px;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  color: rgba(255, 247, 237, 0.7);
}

.template-layout {
  display: grid;
  grid-template-columns: 320px minmax(0, 1fr);
  gap: 20px;
}

.template-sidebar,
.template-main {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.panel-card {
  padding: 24px;
  border-radius: 22px;
  background: #fffaf2;
  border: 1px solid rgba(121, 93, 55, 0.14);
  box-shadow: 0 12px 28px rgba(68, 48, 26, 0.08);
}

.panel-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 16px;
  margin-bottom: 18px;
}

.panel-kicker {
  margin: 0 0 4px;
  color: #8a693c;
  font-size: 12px;
  letter-spacing: 0.12em;
  text-transform: uppercase;
}

.panel-header h2 {
  margin: 0;
  font-size: 22px;
  color: #322719;
}

.info-stack,
.summary-card,
.note-card {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.summary-card,
.note-card {
  margin-top: 16px;
  padding: 16px;
  border-radius: 16px;
  background: rgba(219, 194, 146, 0.14);
  color: #5f4728;
}

.info-row,
.summary-row {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.info-label {
  font-size: 12px;
  color: #8a693c;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.info-link {
  color: #17606c;
  text-decoration: none;
}

.info-link:hover {
  text-decoration: underline;
}

.settings-form {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.action-row {
  margin-top: 18px;
  display: flex;
  gap: 12px;
}

.placeholder-card {
  padding: 18px;
  border-radius: 16px;
  background: rgba(219, 194, 146, 0.14);
  color: #6b5332;
}

.result-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
  margin-top: 16px;
}

.result-block {
  padding: 16px;
  border-radius: 18px;
  background: rgba(219, 194, 146, 0.13);
}

.result-title {
  margin-bottom: 12px;
  font-weight: 600;
  color: #3d2f1f;
}

.chip-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.result-chip {
  display: inline-flex;
  align-items: center;
  padding: 6px 10px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.88);
  color: #4d3b25;
  font-size: 13px;
}

.result-empty {
  color: #8a6a43;
}

.summary-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  color: #5f4728;
}

@media (max-width: 960px) {
  .hero-panel {
    flex-direction: column;
  }

  .template-layout,
  .result-grid {
    grid-template-columns: 1fr;
  }
}
</style>
