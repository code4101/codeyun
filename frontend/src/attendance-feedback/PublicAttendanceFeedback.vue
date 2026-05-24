<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'

import {
  fetchPublicAttendanceFeedbackFormMeta,
  fetchPublicAttendanceFeedbackHistory,
  submitPublicAttendanceFeedback,
  type PublicAttendanceFeedbackCourseOption,
  type PublicAttendanceFeedbackFormMeta,
  type PublicAttendanceWjxDataItem,
} from '@/api/publicAttendanceFeedback'

type FeedbackDraft = {
  course: string
  studentId: string
  studentName: string
  correctionRequest: string
  extraNote: string
}

type PersistedFeedbackDraft = Pick<FeedbackDraft, 'course' | 'studentId' | 'studentName'>

const FEEDBACK_FORM_STORAGE_KEY = 'codeyun-attendance-feedback-draft'
const CODEYUN_PUBLIC_HOST = 'code4101.com'

const form = reactive<FeedbackDraft>({
  course: '',
  studentId: '',
  studentName: '',
  correctionRequest: '',
  extraNote: '',
})

const statusMessage = ref('')
const statusKind = ref<'idle' | 'success' | 'error' | 'warning'>('idle')
const lastSubmittedAt = ref('')
const lastSubmittedDraftKey = ref<string | null>(null)
const formMeta = ref<PublicAttendanceFeedbackFormMeta | null>(null)
const loadingFormMeta = ref(false)
const readyToPersist = ref(false)
const submitting = ref(false)
const feedbackHistoryItems = ref<PublicAttendanceWjxDataItem[]>([])
const feedbackHistoryTotal = ref(0)
const feedbackHistoryLoading = ref(false)
let feedbackHistoryTimer: ReturnType<typeof window.setTimeout> | null = null
let feedbackHistoryRequestId = 0

const courseOptions = computed(() => {
  const structuredOptions = formMeta.value?.course_options
  if (structuredOptions?.length) {
    return structuredOptions.map(normalizeCourseOption).filter((item) => item.name)
  }
  return (formMeta.value?.course_names ?? []).map((name) => ({ name, attendance_sheet_url: '' }))
})
const validCourseOptions = computed(() => new Set(courseOptions.value.map((course) => course.name)))
const currentNormalizedDraftKey = computed(() => buildFeedbackDraftKey())
const hasSubmittedCurrentDraft = computed(() => (
  lastSubmittedDraftKey.value !== null
  && lastSubmittedDraftKey.value === currentNormalizedDraftKey.value
))
const publicSheetLinks = computed(() => [
  { label: '问卷数据', url: normalizeProvidedCodeyunUrl(formMeta.value?.data_sheet_url) },
].filter((item) => item.url))
const feedbackHistoryReady = computed(() => canLoadFeedbackHistory())

function normalizeStoredText(value: unknown) {
  return typeof value === 'string' ? value : ''
}

function normalizeStoredCourse(value: unknown) {
  return normalizeStoredText(value)
}

function getUrlHostname(url: URL) {
  return url.hostname.trim().toLowerCase().replace(/^\[|\]$/g, '')
}

function isLocalhostCodeyunHost(hostname: string) {
  return hostname === 'localhost' || hostname === '127.0.0.1' || hostname === '::1'
}

function isLanIpv4Host(hostname: string) {
  const parts = hostname.split('.').map((part) => Number(part))
  if (parts.length !== 4 || parts.some((part) => !Number.isInteger(part) || part < 0 || part > 255)) {
    return false
  }
  const [first, second] = parts
  return first === 10 || first === 192 || (first === 172 && second >= 16 && second <= 31)
}

function shouldUsePublicCodeyunHost(url: URL) {
  if (url.protocol !== 'http:' && url.protocol !== 'https:') {
    return false
  }
  const hostname = getUrlHostname(url)
  return isLocalhostCodeyunHost(hostname) || isLanIpv4Host(hostname)
}

function normalizeProvidedCodeyunUrl(value: unknown) {
  const rawUrl = normalizeStoredText(value).trim()
  if (!rawUrl) {
    return rawUrl
  }

  try {
    const sourceUrl = new URL(rawUrl, window.location.href)
    if (!shouldUsePublicCodeyunHost(sourceUrl)) {
      return rawUrl
    }

    const targetUrl = new URL(sourceUrl.toString())
    targetUrl.protocol = 'https:'
    targetUrl.hostname = CODEYUN_PUBLIC_HOST
    targetUrl.port = ''
    return targetUrl.toString()
  } catch {
    return rawUrl
  }
}

function normalizeCourseMatchText(value: unknown) {
  return normalizeStoredText(value)
    .trim()
    .replace(/\s+/g, '')
    .replace(/[\/／\\|-]?考勤表$/u, '')
}

function normalizeCourseOption(value: PublicAttendanceFeedbackCourseOption) {
  return {
    name: normalizeStoredText(value.name).trim(),
    attendance_sheet_url: normalizeProvidedCodeyunUrl(value.attendance_sheet_url),
  }
}

function resolveCoursePrefill(value: unknown) {
  const course = normalizeStoredCourse(value).trim()
  if (!course) {
    return ''
  }

  const exactMatch = courseOptions.value.find((option) => option.name === course)
  if (exactMatch) {
    return exactMatch.name
  }

  const courseKey = normalizeCourseMatchText(course)
  if (!courseKey) {
    return course
  }

  const fuzzyMatch = courseOptions.value.find((option) => normalizeCourseMatchText(option.name) === courseKey)
    ?? courseOptions.value.find((option) => {
      const optionKey = normalizeCourseMatchText(option.name)
      return optionKey.includes(courseKey) || courseKey.includes(optionKey)
    })
  return fuzzyMatch?.name ?? course
}

function hasCourseOption(value: string) {
  return validCourseOptions.value.has(value)
}

function buildFeedbackDraftKey() {
  return JSON.stringify({
    course: hasCourseOption(form.course) ? form.course : form.course.trim(),
    studentId: form.studentId.trim(),
    studentName: form.studentName.trim(),
    correctionRequest: form.correctionRequest.trim(),
    extraNote: form.extraNote.trim(),
  } satisfies FeedbackDraft)
}

function resolveFeedbackHistoryCourse() {
  return hasCourseOption(form.course) ? form.course : ''
}

function canLoadFeedbackHistory() {
  return Boolean(
    resolveFeedbackHistoryCourse()
    && (form.studentId.trim() || form.studentName.trim()),
  )
}

function setStatus(kind: typeof statusKind.value, message: string) {
  statusKind.value = kind
  statusMessage.value = message
}

function clearFeedbackHistoryTimer() {
  if (feedbackHistoryTimer === null) {
    return
  }
  window.clearTimeout(feedbackHistoryTimer)
  feedbackHistoryTimer = null
}

function resetFeedbackHistory() {
  feedbackHistoryRequestId += 1
  clearFeedbackHistoryTimer()
  feedbackHistoryItems.value = []
  feedbackHistoryTotal.value = 0
  feedbackHistoryLoading.value = false
}

async function loadFeedbackHistory() {
  clearFeedbackHistoryTimer()
  const course = resolveFeedbackHistoryCourse()
  const studentId = form.studentId.trim()
  const studentName = form.studentName.trim()
  if (!course || (!studentId && !studentName)) {
    resetFeedbackHistory()
    return
  }

  const requestId = feedbackHistoryRequestId + 1
  feedbackHistoryRequestId = requestId
  feedbackHistoryLoading.value = true
  try {
    const result = await fetchPublicAttendanceFeedbackHistory({
      course_name: course,
      student_id_text: studentId,
      student_name: studentName,
      limit: 8,
    })
    if (requestId !== feedbackHistoryRequestId) {
      return
    }
    feedbackHistoryItems.value = result.items || []
    feedbackHistoryTotal.value = result.total || 0
  } catch {
    if (requestId !== feedbackHistoryRequestId) {
      return
    }
    feedbackHistoryItems.value = []
    feedbackHistoryTotal.value = 0
  } finally {
    if (requestId === feedbackHistoryRequestId) {
      feedbackHistoryLoading.value = false
    }
  }
}

function scheduleFeedbackHistoryLoad() {
  if (!canLoadFeedbackHistory()) {
    resetFeedbackHistory()
    return
  }

  clearFeedbackHistoryTimer()
  feedbackHistoryLoading.value = true
  feedbackHistoryTimer = window.setTimeout(() => {
    void loadFeedbackHistory()
  }, 300)
}

async function loadFeedbackFormMeta(showError = true) {
  loadingFormMeta.value = true
  try {
    formMeta.value = await fetchPublicAttendanceFeedbackFormMeta()
    if (form.course) {
      form.course = resolveCoursePrefill(form.course)
    }
    if (form.course && !hasCourseOption(form.course)) {
      form.course = ''
    }
  } catch (error) {
    if (showError) {
      setStatus('error', error instanceof Error ? error.message : '加载课程清单失败')
    }
  } finally {
    loadingFormMeta.value = false
  }
}

function persistFormToLocalStorage() {
  window.localStorage.setItem(
    FEEDBACK_FORM_STORAGE_KEY,
    JSON.stringify({
      course: form.course,
      studentId: form.studentId,
      studentName: form.studentName,
    } satisfies PersistedFeedbackDraft),
  )
}

function hydrateFormFromLocalStorage() {
  try {
    const raw = window.localStorage.getItem(FEEDBACK_FORM_STORAGE_KEY)
    if (!raw) {
      return
    }

    const parsed = JSON.parse(raw)
    if (!parsed || typeof parsed !== 'object') {
      return
    }

    form.course = normalizeStoredCourse(parsed.course)
    form.studentId = normalizeStoredText(parsed.studentId)
    form.studentName = normalizeStoredText(parsed.studentName)
  } catch {
    window.localStorage.removeItem(FEEDBACK_FORM_STORAGE_KEY)
  }
}

function getRouteQueryText(...keys: string[]) {
  const query = new URLSearchParams(window.location.search)
  for (const key of keys) {
    const value = query.get(key)
    if (value !== null) {
      return normalizeStoredText(value)
    }
  }
  return undefined
}

function hydrateFormFromRouteQuery() {
  const course = getRouteQueryText('course', 'courseName', 'course_name')
  const studentId = getRouteQueryText('studentId', 'student_id', 'student_id_text')
  const studentName = getRouteQueryText('studentName', 'student_name', 'name')

  if (course !== undefined) {
    form.course = normalizeStoredCourse(course).trim()
  }
  if (studentId !== undefined) {
    form.studentId = normalizeStoredText(studentId).trim()
  }
  if (studentName !== undefined) {
    form.studentName = normalizeStoredText(studentName).trim()
  }
}

async function submitForm() {
  if (!form.course || !hasCourseOption(form.course)) {
    setStatus('warning', '请先选择所属课程')
    return
  }
  if (!form.studentId.trim()) {
    setStatus('warning', '请填写学号')
    return
  }
  if (!form.studentName.trim()) {
    setStatus('warning', '请填写姓名')
    return
  }
  if (!form.correctionRequest.trim()) {
    setStatus('warning', '请填写修正需求')
    return
  }

  const normalizedCourse = hasCourseOption(form.course) ? form.course : ''
  const normalizedStudentId = form.studentId.trim()
  const normalizedStudentName = form.studentName.trim()
  const normalizedCorrectionRequest = form.correctionRequest.trim()
  const normalizedExtraNote = form.extraNote.trim()
  const submittedDraftKey = JSON.stringify({
    course: normalizedCourse,
    studentId: normalizedStudentId,
    studentName: normalizedStudentName,
    correctionRequest: normalizedCorrectionRequest,
    extraNote: normalizedExtraNote,
  } satisfies FeedbackDraft)

  submitting.value = true
  try {
    const saved = await submitPublicAttendanceFeedback({
      course_name: normalizedCourse,
      student_id_text: normalizedStudentId,
      student_name: normalizedStudentName,
      correction_request: normalizedCorrectionRequest,
      extra_note: normalizedExtraNote,
    })

    lastSubmittedAt.value = saved.submitted_at_text || new Date().toLocaleString('zh-CN', { hour12: false })
    lastSubmittedDraftKey.value = submittedDraftKey

    form.course = normalizedCourse
    form.studentId = normalizedStudentId
    form.studentName = normalizedStudentName
    form.correctionRequest = normalizedCorrectionRequest
    form.extraNote = normalizedExtraNote
    persistFormToLocalStorage()
    setStatus('success', `${lastSubmittedAt.value} 已提交`)
    void loadFeedbackHistory()
  } catch (error) {
    setStatus('error', error instanceof Error ? error.message : '提交失败')
  } finally {
    submitting.value = false
  }
}

function hasText(value: unknown) {
  return String(value ?? '').trim().length > 0
}

function hasSecondaryFields(item: PublicAttendanceWjxDataItem) {
  return (
    hasText(item.extra_note)
    || (hasText(item.process_note) && item.process_note !== item.process_status)
  )
}

function resolveProcessStatus(item: PublicAttendanceWjxDataItem) {
  return item.process_status?.trim() || '待处理'
}

function isResolved(item: PublicAttendanceWjxDataItem) {
  return resolveProcessStatus(item).includes('已')
}

function isPending(item: PublicAttendanceWjxDataItem) {
  return !item.process_status?.trim()
}

onMounted(async () => {
  hydrateFormFromLocalStorage()
  hydrateFormFromRouteQuery()
  await loadFeedbackFormMeta()
  readyToPersist.value = true
  persistFormToLocalStorage()
})

onBeforeUnmount(() => {
  clearFeedbackHistoryTimer()
  feedbackHistoryRequestId += 1
})

watch(courseOptions, () => {
  if (form.course) {
    form.course = resolveCoursePrefill(form.course)
  }
  if (form.course && !hasCourseOption(form.course)) {
    form.course = ''
  }
  if (readyToPersist.value) {
    persistFormToLocalStorage()
  }
})

watch(
  form,
  () => {
    if (!readyToPersist.value) {
      return
    }
    persistFormToLocalStorage()
    if (statusKind.value === 'warning' || statusKind.value === 'success') {
      setStatus('idle', '')
    }
  },
  { deep: true },
)

watch(
  [
    () => form.course,
    () => form.studentId,
    () => form.studentName,
    () => courseOptions.value.map((course) => course.name).join('\n'),
  ],
  () => {
    scheduleFeedbackHistoryLoad()
  },
  { immediate: true },
)
</script>

<template>
  <main class="collect-page">
    <section class="feedback-card">
      <div class="card-banner">
        <div class="banner-copy">
          <h1>考勤问题反馈表</h1>
        </div>
        <div v-if="publicSheetLinks.length" class="banner-links">
          <a
            v-for="link in publicSheetLinks"
            :key="link.label"
            class="banner-sheet-link"
            :href="link.url"
            target="_blank"
            rel="noopener noreferrer"
          >
            {{ link.label }}
          </a>
        </div>
      </div>

      <form class="card-body" @submit.prevent="submitForm">
        <section class="question-block">
          <div class="question-title">
            <span class="required-star">*</span>
            <span>1. 所属课程</span>
          </div>
          <div v-if="loadingFormMeta" class="course-empty">
            正在加载课程清单...
          </div>
          <div v-else-if="courseOptions.length" class="course-list">
            <div
              v-for="course in courseOptions"
              :key="course.name"
              class="course-option"
              :class="{ 'is-selected': form.course === course.name }"
            >
              <button type="button" class="course-select-button" @click="form.course = course.name">
                <span class="course-dot" />
                <span class="course-name">{{ course.name }}</span>
              </button>
              <a
                v-if="course.attendance_sheet_url"
                class="course-attendance-link"
                :href="course.attendance_sheet_url"
                target="_blank"
                rel="noopener noreferrer"
                @click.stop
              >
                考勤表链接
              </a>
            </div>
          </div>
          <div v-else class="course-empty">
            暂无未完结课程。
          </div>
        </section>

        <section class="question-block">
          <label class="question-title" for="student-id">
            <span class="required-star">*</span>
            <span>2. 学号</span>
            <span class="question-help">例如 `123`、`1-23`、`1_23`</span>
          </label>
          <input
            id="student-id"
            v-model="form.studentId"
            class="feedback-input"
            placeholder="例如 2-17"
            maxlength="64"
            autocomplete="off"
          />
        </section>

        <section class="question-block">
          <label class="question-title" for="student-name">
            <span class="required-star">*</span>
            <span>3. 姓名</span>
          </label>
          <input
            id="student-name"
            v-model="form.studentName"
            class="feedback-input"
            placeholder="请输入姓名"
            maxlength="64"
            autocomplete="name"
          />
        </section>

        <section v-if="feedbackHistoryReady || feedbackHistoryLoading || feedbackHistoryItems.length" class="feedback-history">
          <div class="history-header">
            <h2>历史反馈</h2>
            <span v-if="feedbackHistoryItems.length" class="history-count">
              最近 {{ feedbackHistoryItems.length }} 条<span v-if="feedbackHistoryTotal > feedbackHistoryItems.length"> / 共 {{ feedbackHistoryTotal }} 条</span>
            </span>
          </div>
          <div v-if="feedbackHistoryLoading && !feedbackHistoryItems.length" class="history-empty">
            正在查询历史反馈...
          </div>
          <div v-else-if="feedbackHistoryReady && !feedbackHistoryItems.length" class="history-empty">
            暂未查到这个学员的历史反馈。
          </div>
          <div v-if="feedbackHistoryItems.length" class="history-list">
            <article
              v-for="item in feedbackHistoryItems"
              :key="`${item.activity_id}-${item.seq}-${item.id}`"
              class="history-record"
            >
              <div class="record-head">
                <span class="record-seq">序号 {{ item.seq }}</span>
                <span v-if="item.submitted_at_text" class="record-time">{{ item.submitted_at_text }}</span>
                <span
                  class="record-status"
                  :class="{ 'is-resolved': isResolved(item), 'is-pending': isPending(item) }"
                >
                  {{ resolveProcessStatus(item) }}
                </span>
              </div>
              <div v-if="hasText(item.correction_request) && !hasSecondaryFields(item)" class="history-primary-text">
                {{ item.correction_request }}
              </div>
              <dl v-else class="history-fields">
                <template v-if="hasText(item.correction_request)">
                  <dt>修正需求</dt>
                  <dd>{{ item.correction_request }}</dd>
                </template>
                <template v-if="hasText(item.extra_note)">
                  <dt>补充说明</dt>
                  <dd>{{ item.extra_note }}</dd>
                </template>
                <template v-if="hasText(item.process_note) && item.process_note !== item.process_status">
                  <dt>处理备注</dt>
                  <dd>{{ item.process_note }}</dd>
                </template>
              </dl>
            </article>
          </div>
        </section>

        <section class="question-block">
          <label class="question-title" for="correction-request">
            <span class="required-star">*</span>
            <span>4. 修正需求</span>
            <span class="question-help">例如“共学打卡已满 21 次”或“第 18 课有完成当堂学习”</span>
          </label>
          <textarea
            id="correction-request"
            v-model="form.correctionRequest"
            class="feedback-textarea"
            rows="4"
            placeholder="请把需要修正的内容直接写清楚"
            maxlength="400"
          />
          <div class="word-count">{{ form.correctionRequest.length }} / 400</div>
        </section>

        <section class="question-block">
          <label class="question-title" for="extra-note">
            <span>5. 补充说明</span>
          </label>
          <textarea
            id="extra-note"
            v-model="form.extraNote"
            class="feedback-textarea"
            rows="5"
            placeholder="可选填写"
            maxlength="600"
          />
          <div class="word-count">{{ form.extraNote.length }} / 600</div>
        </section>

        <div class="action-row">
          <button
            class="submit-button"
            type="submit"
            :disabled="submitting || hasSubmittedCurrentDraft"
          >
            {{ submitting ? '提交中...' : '提交' }}
          </button>
        </div>

        <div v-if="statusMessage" class="submit-status" :class="[`is-${statusKind}`]">
          {{ statusMessage }}{{ lastSubmittedAt && statusKind === 'success' ? (hasSubmittedCurrentDraft ? '，修改内容后可再次提交' : '，当前内容已变更，可重新提交') : '' }}
        </div>
      </form>
    </section>
  </main>
</template>

<style scoped>
.collect-page {
  min-height: 100dvh;
  display: flex;
  justify-content: center;
  box-sizing: border-box;
  padding: 24px 18px 32px;
  background: #fffdf8;
}

.feedback-card {
  --feedback-bg: #fffdf8;
  --feedback-surface: rgba(255, 255, 255, 0.9);
  --feedback-border: rgba(147, 112, 70, 0.18);
  --feedback-shadow: 0 24px 60px rgba(67, 52, 31, 0.14);
  --feedback-text: #2f2418;
  --feedback-muted: #7d6850;
  --feedback-accent-strong: #1f8fff;
  width: min(100%, 920px);
  overflow: hidden;
  border: 1px solid var(--feedback-border);
  border-radius: 30px;
  background: var(--feedback-bg);
  box-shadow: var(--feedback-shadow);
}

.card-banner {
  display: flex;
  justify-content: space-between;
  gap: 20px;
  align-items: flex-start;
  padding: 28px 32px;
  background:
    radial-gradient(circle at 14% 30%, rgba(255, 237, 176, 0.62), transparent 24%),
    radial-gradient(circle at 88% 18%, rgba(128, 193, 255, 0.35), transparent 22%),
    linear-gradient(135deg, #eef7ff 0%, #fdf2df 52%, #edf7f4 100%);
}

.banner-copy h1 {
  margin: 0;
  color: var(--feedback-text);
  font-family: "Source Han Serif SC", "Songti SC", "Noto Serif SC", serif;
  font-size: clamp(30px, 5vw, 42px);
  line-height: 1.08;
}

.banner-links {
  display: flex;
  flex: 0 0 auto;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 8px;
}

.banner-sheet-link {
  padding: 6px 10px;
  border: 1px solid rgba(47, 36, 24, 0.16);
  border-radius: 6px;
  background: rgba(255, 255, 255, 0.54);
  color: #2e77f0;
  font-size: 14px;
  line-height: 20px;
  text-decoration: none;
}

.card-body {
  display: flex;
  flex-direction: column;
  gap: 28px;
  padding: 34px 32px 36px;
}

.question-block {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.question-title {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: baseline;
  color: var(--feedback-text);
  font-family: "Source Han Serif SC", "Songti SC", "Noto Serif SC", serif;
  font-size: 24px;
  line-height: 1.45;
}

.required-star {
  color: #df5b38;
}

.question-help {
  color: var(--feedback-muted);
  font-family: "PingFang SC", "Microsoft YaHei", "Noto Sans SC", sans-serif;
  font-size: 14px;
}

.course-list {
  display: grid;
  gap: 6px;
}

.course-empty {
  padding: 16px 18px;
  border-radius: 18px;
  background: rgba(219, 194, 146, 0.14);
  color: var(--feedback-muted);
  line-height: 1.7;
}

.course-option {
  display: flex;
  align-items: center;
  gap: 12px;
  width: 100%;
  padding: 6px 0;
  color: var(--feedback-text);
}

.course-select-button {
  display: inline-flex;
  min-width: 0;
  align-items: center;
  gap: 10px;
  padding: 0;
  border: none;
  background: transparent;
  color: var(--feedback-text);
  font-size: 16px;
  line-height: 1.7;
  text-align: left;
  cursor: pointer;
}

.course-option.is-selected .course-select-button {
  color: #1f4f7a;
}

.course-name {
  min-width: 0;
}

.course-attendance-link {
  flex: 0 0 auto;
  color: #2e77f0;
  font-size: 14px;
  line-height: 1.7;
  text-decoration: none;
}

.course-attendance-link:hover {
  text-decoration: underline;
}

.course-dot {
  width: 17px;
  height: 17px;
  border: 1.5px solid rgba(92, 88, 82, 0.5);
  border-radius: 999px;
  box-sizing: border-box;
  flex-shrink: 0;
  background: #fff;
}

.course-option.is-selected .course-dot {
  border-color: var(--feedback-accent-strong);
  background: radial-gradient(circle at center, var(--feedback-accent-strong) 0 48%, #fff 54% 100%);
}

.feedback-input,
.feedback-textarea {
  width: 100%;
  box-sizing: border-box;
  border: 1px solid rgba(137, 112, 81, 0.18);
  border-radius: 18px;
  background: rgba(255, 255, 255, 0.88);
  color: var(--feedback-text);
  font: inherit;
  font-size: 16px;
  line-height: 1.7;
  outline: none;
}

.feedback-input {
  min-height: 44px;
  padding: 6px 14px;
}

.feedback-textarea {
  min-height: 112px;
  padding: 10px 14px;
  resize: vertical;
}

.feedback-input:focus,
.feedback-textarea:focus {
  border-color: rgba(31, 143, 255, 0.42);
  box-shadow: 0 0 0 3px rgba(31, 143, 255, 0.08);
}

.word-count {
  align-self: flex-end;
  color: var(--feedback-muted);
  font-size: 12px;
  line-height: 1;
}

.action-row {
  display: flex;
  justify-content: center;
  gap: 14px;
  padding-top: 8px;
}

.submit-button {
  min-width: 168px;
  min-height: 44px;
  padding: 0 24px;
  border: none;
  border-radius: 22px;
  background: linear-gradient(135deg, #1f8fff, #2e77f0);
  box-shadow: 0 16px 28px rgba(46, 119, 240, 0.22);
  color: #fff;
  font-size: 16px;
  font-weight: 700;
  cursor: pointer;
}

.submit-button:disabled {
  cursor: not-allowed;
  opacity: 0.58;
}

.submit-status {
  text-align: center;
  color: #7d6850;
  line-height: 1.8;
}

.submit-status.is-error,
.submit-status.is-warning {
  color: #bd3f24;
}

.submit-status.is-success {
  color: #19784a;
}

.feedback-history {
  display: grid;
  gap: 10px;
  padding-top: 4px;
}

.history-header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
}

.history-header h2 {
  margin: 0;
  color: var(--feedback-text);
  font-size: 18px;
  font-weight: 700;
  line-height: 1.5;
}

.history-count,
.history-empty {
  color: var(--feedback-muted);
  font-size: 13px;
  line-height: 20px;
}

.history-empty {
  padding: 10px 0;
  font-size: 14px;
  line-height: 1.7;
}

.history-list {
  display: grid;
  border-top: 1px solid rgba(137, 112, 81, 0.14);
}

.history-record {
  display: grid;
  gap: 8px;
  padding: 12px 0;
  border-bottom: 1px solid rgba(137, 112, 81, 0.14);
}

.record-head {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 12px;
  align-items: center;
  color: var(--feedback-muted);
  font-size: 13px;
  line-height: 20px;
}

.record-seq {
  color: var(--feedback-text);
  font-weight: 700;
}

.record-status {
  display: inline-flex;
  align-items: center;
  min-height: 20px;
  padding: 0 6px;
  border-radius: 999px;
  color: #a15d1e;
  font-size: 12px;
  font-weight: 700;
  line-height: 20px;
}

.record-status.is-resolved {
  background: #e8f7ee;
  color: #19784a;
}

.record-status.is-pending {
  background: #fff4c7;
  color: #9a5b00;
}

.history-fields {
  display: grid;
  grid-template-columns: max-content minmax(0, 1fr);
  gap: 8px 14px;
  margin: 0;
  color: var(--feedback-text);
  font-size: 14px;
  line-height: 1.7;
}

.history-fields dt {
  color: var(--feedback-muted);
  font-weight: 600;
}

.history-fields dd {
  min-width: 0;
  margin: 0;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.history-primary-text {
  color: var(--feedback-text);
  font-size: 14px;
  line-height: 1.7;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

@media (max-width: 768px) {
  .collect-page {
    padding: 18px 12px 24px;
  }

  .feedback-card {
    border-radius: 24px;
  }

  .card-banner,
  .card-body {
    padding-left: 20px;
    padding-right: 20px;
  }

  .card-banner {
    flex-direction: column;
  }

  .banner-links {
    justify-content: flex-start;
  }

  .question-title {
    font-size: 20px;
  }

  .course-option {
    flex-wrap: wrap;
    gap: 6px 10px;
  }

  .course-attendance-link {
    margin-left: 27px;
  }

  .action-row {
    flex-direction: column-reverse;
  }

  .submit-button {
    width: 100%;
  }
}

@media (max-width: 560px) {
  .history-header {
    flex-direction: column;
    align-items: flex-start;
    gap: 2px;
  }

  .history-fields {
    grid-template-columns: 1fr;
    gap: 2px;
  }
}
</style>
