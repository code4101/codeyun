<script setup lang="ts">
import { computed, ref, type CSSProperties } from 'vue'
import { CopyDocument, Search } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'

import {
  generateAttendanceHeaderTool,
  type AttendanceHeaderToolCell,
  type AttendanceHeaderToolGroup,
  type AttendanceHeaderToolResponse,
} from '@/api/attendance'

const courseName = ref('')
const loading = ref(false)
const result = ref<AttendanceHeaderToolResponse | null>(null)
const lastError = ref('')

const clockinCount = computed(() => result.value?.cells.filter(cell => cell.kind === 'clockin').length ?? 0)
const lessonCount = computed(() => result.value?.cells.filter(cell => cell.kind === 'lesson').length ?? 0)

function getGroupStyle(group: AttendanceHeaderToolGroup): CSSProperties {
  return {
    backgroundColor: group.background_color,
    color: '#111827',
  }
}

function getCellStyle(cell: AttendanceHeaderToolCell): CSSProperties {
  return {
    backgroundColor: cell.background_color,
    color: cell.url ? '#0645ad' : '#111827',
  }
}

function escapeHtml(value: string): string {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

function buildClipboardHtml(payload: AttendanceHeaderToolResponse): string {
  const groupCells = payload.groups.map(group => {
    const colspan = Math.max(1, group.colspan)
    return `<th colspan="${colspan}" bgcolor="${group.background_color}" style="background-color:${group.background_color};color:#111827;font-weight:700;text-align:center;vertical-align:middle;border:1px solid #222;padding:6px 10px;height:30px;white-space:nowrap;mso-pattern:auto none;">${escapeHtml(group.label)}</th>`
  }).join('')
  const dataCells = payload.cells.map(cell => {
    const content = cell.url
      ? `<a href="${escapeHtml(cell.url)}" style="color:#0645ad;text-decoration:underline;">${escapeHtml(cell.label)}</a>`
      : escapeHtml(cell.label)
    return `<td bgcolor="${cell.background_color}" style="background-color:${cell.background_color};color:${cell.url ? '#0645ad' : '#111827'};text-align:center;vertical-align:middle;border:1px solid #222;padding:6px 10px;height:30px;white-space:nowrap;mso-pattern:auto none;">${content}</td>`
  }).join('')
  return [
    '<meta charset="utf-8">',
    '<table border="1" cellspacing="0" cellpadding="0" style="border-collapse:collapse;mso-table-lspace:0pt;mso-table-rspace:0pt;">',
    `<tbody><tr>${groupCells}</tr><tr>${dataCells}</tr></tbody>`,
    '</table>',
  ].join('')
}

function copyRichHtmlBySelection(html: string, plain: string): boolean {
  const selection = window.getSelection()
  if (!selection) return false

  const container = document.createElement('div')
  container.contentEditable = 'true'
  container.style.position = 'fixed'
  container.style.left = '-10000px'
  container.style.top = '0'
  container.style.width = '1px'
  container.style.height = '1px'
  container.style.overflow = 'hidden'
  container.innerHTML = html

  const previousRanges = Array.from({ length: selection.rangeCount }, (_, index) => (
    selection.getRangeAt(index).cloneRange()
  ))

  const onCopy = (event: ClipboardEvent) => {
    if (!event.clipboardData) return
    event.preventDefault()
    event.clipboardData.setData('text/html', html)
    event.clipboardData.setData('text/plain', plain)
  }

  document.body.appendChild(container)
  document.addEventListener('copy', onCopy)

  try {
    const range = document.createRange()
    range.selectNodeContents(container)
    selection.removeAllRanges()
    selection.addRange(range)
    return document.execCommand('copy')
  } finally {
    document.removeEventListener('copy', onCopy)
    selection.removeAllRanges()
    previousRanges.forEach(range => selection.addRange(range))
    document.body.removeChild(container)
  }
}

async function writeClipboard(payload: AttendanceHeaderToolResponse): Promise<'rich' | 'plain'> {
  const html = buildClipboardHtml(payload)
  const plain = payload.plain_text

  if (copyRichHtmlBySelection(html, plain)) {
    return 'rich'
  }

  if (navigator.clipboard?.write && typeof ClipboardItem !== 'undefined') {
    await navigator.clipboard.write([
      new ClipboardItem({
        'text/html': new Blob([html], { type: 'text/html' }),
        'text/plain': new Blob([plain], { type: 'text/plain' }),
      }),
    ])
    return 'rich'
  }

  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(plain)
    return 'plain'
  }

  const textArea = document.createElement('textarea')
  textArea.value = plain
  textArea.style.position = 'fixed'
  textArea.style.opacity = '0'
  document.body.appendChild(textArea)
  textArea.select()
  const ok = document.execCommand('copy')
  document.body.removeChild(textArea)
  if (!ok) {
    throw new Error('copy failed')
  }
  return 'plain'
}

async function handleGenerate() {
  const name = courseName.value.trim()
  if (!name) {
    ElMessage.warning('请输入课程前缀')
    return
  }

  loading.value = true
  lastError.value = ''
  try {
    result.value = await generateAttendanceHeaderTool(name)
  } catch (error: any) {
    result.value = null
    lastError.value = error.response?.data?.detail || '生成表头失败'
    ElMessage.error(lastError.value)
  } finally {
    loading.value = false
  }
}

async function copyHeader() {
  if (!result.value) return
  try {
    const mode = await writeClipboard(result.value)
    if (mode === 'rich') {
      ElMessage.success('富文本表头已复制')
    } else {
      ElMessage.warning('已复制纯文本，当前环境不支持富文本剪贴板')
    }
  } catch (error) {
    console.warn('Failed to copy attendance header', error)
    ElMessage.error('复制失败')
  }
}

async function copyJson() {
  if (!result.value) return
  try {
    await navigator.clipboard.writeText(JSON.stringify(result.value.document_json, null, 2))
    ElMessage.success('表格 JSON 已复制')
  } catch (error) {
    console.warn('Failed to copy attendance header json', error)
    ElMessage.error('复制 JSON 失败')
  }
}
</script>

<template>
  <div class="attendance-header-tool-page">
    <section class="page-head">
      <div>
        <div class="eyebrow">禅寺考勤 / 表头工具</div>
        <h1>表头工具</h1>
      </div>
    </section>

    <section class="toolbar">
      <el-input
        v-model="courseName"
        class="course-input"
        placeholder="d260308禅宗1至3期五阶"
        clearable
        @keyup.enter="handleGenerate"
      />
      <el-button type="primary" :icon="Search" :loading="loading" @click="handleGenerate">
        生成
      </el-button>
      <el-button :icon="CopyDocument" :disabled="!result" @click="copyHeader">
        复制表头
      </el-button>
      <el-button :disabled="!result" @click="copyJson">
        复制 JSON
      </el-button>
    </section>

    <div v-if="lastError" class="state-line error-state">{{ lastError }}</div>

    <section v-if="result" class="preview-section">
      <div class="result-meta">
        <span>{{ result.course_name }}</span>
        <span>{{ result.course_type }}</span>
        <span>打卡 {{ clockinCount }}</span>
        <span>课次 {{ lessonCount }}</span>
      </div>

      <div class="preview-scroll">
        <table class="header-preview-table">
          <tbody>
            <tr>
              <th
                v-for="group in result.groups"
                :key="`${group.kind}-${group.start_column}`"
                :colspan="group.colspan"
                :style="getGroupStyle(group)"
              >
                {{ group.label }}
              </th>
            </tr>
            <tr>
              <td
                v-for="cell in result.cells"
                :key="`${cell.kind}-${cell.column_index}`"
                :style="getCellStyle(cell)"
              >
                <a
                  v-if="cell.url"
                  class="header-link"
                  :href="cell.url"
                  target="_blank"
                  rel="noreferrer"
                >{{ cell.label }}</a>
                <span v-else>{{ cell.label }}</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <section v-else-if="!loading && !lastError" class="empty-state">
      输入课程前缀后生成两行表头。
    </section>
  </div>
</template>

<style scoped>
.attendance-header-tool-page {
  box-sizing: border-box;
  min-height: 100%;
  padding: 24px;
  color: #1f2933;
  background: #f7f8fa;
}

.page-head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 18px;
}

.eyebrow {
  margin-bottom: 6px;
  color: #687385;
  font-size: 13px;
}

.page-head h1 {
  margin: 0;
  font-size: 26px;
  font-weight: 650;
  line-height: 1.2;
}

.toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 14px;
}

.course-input {
  max-width: 420px;
}

.state-line,
.empty-state,
.preview-section {
  border: 1px solid #d9dee7;
  background: #fff;
}

.state-line {
  padding: 12px 14px;
}

.error-state {
  color: #b42318;
  background: #fff5f5;
  border-color: #f2c6c6;
}

.empty-state {
  padding: 28px 16px;
  color: #6b7280;
}

.preview-section {
  overflow: hidden;
}

.result-meta {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 10px 12px;
  color: #4b5563;
  font-size: 13px;
  border-bottom: 1px solid #e5e7eb;
}

.result-meta span:first-child {
  color: #111827;
  font-weight: 600;
}

.preview-scroll {
  overflow: auto;
  padding: 12px;
}

.header-preview-table {
  min-width: max-content;
  border-collapse: collapse;
  table-layout: fixed;
  background: #fff;
}

.header-preview-table th,
.header-preview-table td {
  min-width: 108px;
  height: 38px;
  padding: 6px 10px;
  border: 1px solid #2f3542;
  text-align: center;
  white-space: nowrap;
}

.header-preview-table th {
  font-size: 16px;
  font-weight: 700;
}

.header-preview-table td {
  font-size: 14px;
}

.header-link {
  color: #0645ad;
  text-decoration: underline;
  text-underline-offset: 2px;
}

@media (max-width: 720px) {
  .attendance-header-tool-page {
    padding: 16px;
  }

  .toolbar {
    align-items: stretch;
    flex-direction: column;
  }

  .course-input {
    max-width: none;
  }

  .toolbar :deep(.el-button) {
    width: 100%;
  }
}
</style>
