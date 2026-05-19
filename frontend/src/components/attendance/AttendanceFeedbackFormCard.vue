<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'

import AttendanceFeedbackHistoryList from './AttendanceFeedbackHistoryList.vue'
import {
  fetchAttendanceFeedbackHistory,
  fetchAttendanceFeedbackFormMeta,
  submitAttendanceFeedback,
  type AttendanceFeedbackCourseOption,
  type AttendanceFeedbackFormMeta,
  type AttendanceWjxDataItem,
} from '@/api/attendance'

type FeedbackFormMode = 'public'

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

const props = withDefaults(
  defineProps<{
    mode?: FeedbackFormMode
  }>(),
  {
    mode: 'public',
  },
)

const route = useRoute()

const form = reactive<FeedbackDraft>({
  course: '',
  studentId: '',
  studentName: '',
  correctionRequest: '',
  extraNote: '',
})

const lastSubmittedAt = ref('')
const lastSubmittedDraftKey = ref<string | null>(null)
const formMeta = ref<AttendanceFeedbackFormMeta | null>(null)
const loadingFormMeta = ref(false)
const readyToPersist = ref(false)
const submitting = ref(false)
const feedbackHistoryItems = ref<AttendanceWjxDataItem[]>([])
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
const displayCourseOptions = computed(() => courseOptions.value)
const validCourseOptions = computed(() => new Set(displayCourseOptions.value.map((course) => course.name)))
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
  return (
    first === 10
    || first === 192
    || (first === 172 && second >= 16 && second <= 31)
  )
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
  if (!rawUrl || typeof window === 'undefined') {
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

function normalizeCourseOption(value: AttendanceFeedbackCourseOption) {
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

  const exactMatch = displayCourseOptions.value.find((option) => option.name === course)
  if (exactMatch) {
    return exactMatch.name
  }

  const courseKey = normalizeCourseMatchText(course)
  if (!courseKey) {
    return course
  }

  const fuzzyMatch = displayCourseOptions.value.find((option) => normalizeCourseMatchText(option.name) === courseKey)
    ?? displayCourseOptions.value.find((option) => {
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
    const result = await fetchAttendanceFeedbackHistory({
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
    formMeta.value = await fetchAttendanceFeedbackFormMeta()
    if (form.course) {
      form.course = resolveCoursePrefill(form.course)
    }
    if (form.course && !hasCourseOption(form.course)) {
      form.course = ''
    }
  } catch (error: any) {
    if (showError) {
      ElMessage.error(error.response?.data?.detail || '加载课程清单失败')
    }
  } finally {
    loadingFormMeta.value = false
  }
}

function persistFormToLocalStorage() {
  if (typeof window === 'undefined') {
    return
  }

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
  if (typeof window === 'undefined') {
    return
  }

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
  for (const key of keys) {
    if (!(key in route.query)) {
      continue
    }

    const value = route.query[key]
    if (Array.isArray(value)) {
      return normalizeStoredText(value[0])
    }
    return normalizeStoredText(value)
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
    ElMessage.warning('请先选择所属课程')
    return
  }
  if (!form.studentId.trim()) {
    ElMessage.warning('请填写学号')
    return
  }
  if (!form.studentName.trim()) {
    ElMessage.warning('请填写姓名')
    return
  }
  if (!form.correctionRequest.trim()) {
    ElMessage.warning('请填写修正需求')
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
    const saved = await submitAttendanceFeedback({
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
    void loadFeedbackHistory()
  } catch (error: any) {
    ElMessage.error(error.response?.data?.detail || '提交失败')
  } finally {
    submitting.value = false
  }
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
  },
  { deep: true },
)

watch(
  [
    () => form.course,
    () => form.studentId,
    () => form.studentName,
    () => displayCourseOptions.value.map((course) => course.name).join('\n'),
  ],
  () => {
    scheduleFeedbackHistoryLoad()
  },
  { immediate: true },
)
</script>

<template>
  <section class="feedback-card" :class="[`mode-${props.mode}`]">
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

    <div class="card-body">
      <section class="question-block">
          <div class="question-title">
            <span class="required-star">*</span>
            <span>1. 所属课程</span>
          </div>
          <div v-if="loadingFormMeta" class="course-empty">
            正在加载课程清单...
          </div>
          <div v-else-if="displayCourseOptions.length" class="course-list">
            <div
              v-for="course in displayCourseOptions"
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
        <div class="question-title">
          <span class="required-star">*</span>
          <span>2. 学号</span>
          <span class="question-help">例如 `123`、`1-23`、`1_23`</span>
        </div>
        <el-input
          v-model="form.studentId"
          size="large"
          placeholder="例如 2-17"
          maxlength="64"
        />
        </section>

        <section class="question-block">
        <div class="question-title">
          <span class="required-star">*</span>
          <span>3. 姓名</span>
        </div>
        <el-input
          v-model="form.studentName"
          size="large"
          placeholder="请输入姓名"
          maxlength="64"
        />
      </section>

      <AttendanceFeedbackHistoryList
        :ready="feedbackHistoryReady"
        :loading="feedbackHistoryLoading"
        :items="feedbackHistoryItems"
        :total="feedbackHistoryTotal"
        :student-id="form.studentId"
        :student-name="form.studentName"
      />

        <section class="question-block">
        <div class="question-title">
          <span class="required-star">*</span>
          <span>4. 修正需求</span>
          <span class="question-help">例如“共学打卡已满 21 次”或“第 18 课有完成当堂学习”</span>
        </div>
        <el-input
          v-model="form.correctionRequest"
          type="textarea"
          :rows="4"
          resize="vertical"
          placeholder="请把需要修正的内容直接写清楚"
          maxlength="400"
          show-word-limit
        />
      </section>

        <section class="question-block">
        <div class="question-title">
          <span>5. 补充说明</span>
        </div>
        <el-input
          v-model="form.extraNote"
          type="textarea"
          :rows="5"
          resize="vertical"
          placeholder="可选填写"
          maxlength="600"
          show-word-limit
        />
      </section>

        <div class="action-row">
          <el-button
            size="large"
            type="primary"
            :loading="submitting"
            :disabled="hasSubmittedCurrentDraft"
            @click="submitForm"
          >
            提交
          </el-button>
        </div>

        <div v-if="lastSubmittedAt" class="submit-status">
          {{ lastSubmittedAt }} 已提交{{ hasSubmittedCurrentDraft ? '，修改内容后可再次提交' : '，当前内容已变更，可重新提交' }}
        </div>
    </div>
  </section>
</template>

<style scoped>
.feedback-card {
  --feedback-bg: #fffdf8;
  --feedback-surface: rgba(255, 255, 255, 0.9);
  --feedback-border: rgba(147, 112, 70, 0.18);
  --feedback-shadow: 0 24px 60px rgba(67, 52, 31, 0.14);
  --feedback-text: #2f2418;
  --feedback-muted: #7d6850;
  --feedback-accent: #c5772f;
  --feedback-accent-strong: #1f8fff;
  width: min(100%, 920px);
  border-radius: 30px;
  overflow: hidden;
  background: var(--feedback-bg);
  border: 1px solid var(--feedback-border);
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
  font-size: clamp(30px, 5vw, 42px);
  line-height: 1.08;
  color: var(--feedback-text);
  font-family: "Source Han Serif SC", "Songti SC", "Noto Serif SC", serif;
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

.banner-sheet-link:hover {
  border-color: rgba(46, 119, 240, 0.34);
  background: rgba(255, 255, 255, 0.78);
}

.card-body {
  padding: 34px 32px 36px;
  display: flex;
  flex-direction: column;
  gap: 28px;
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
  font-size: 24px;
  line-height: 1.45;
  font-family: "Source Han Serif SC", "Songti SC", "Noto Serif SC", serif;
}

.required-star {
  color: #df5b38;
}

.question-help {
  color: var(--feedback-muted);
  font-size: 14px;
  font-family: "PingFang SC", "Microsoft YaHei", "Noto Sans SC", sans-serif;
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
  color: var(--feedback-text);
  font-size: 16px;
  line-height: 1.7;
  text-align: left;
  border: none;
  background: transparent;
  cursor: pointer;
  transition:
    color 0.16s ease,
    opacity 0.16s ease;
}

.course-select-button:hover {
  opacity: 0.82;
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

.course-attendance-link:focus-visible {
  border-radius: 4px;
  outline: 2px solid rgba(46, 119, 240, 0.24);
  outline-offset: 2px;
}

.course-dot {
  width: 17px;
  height: 17px;
  border-radius: 999px;
  border: 1.5px solid rgba(92, 88, 82, 0.5);
  background: #fff;
  box-sizing: border-box;
  flex-shrink: 0;
  transition: border-color 0.16s ease, background 0.16s ease;
}

.course-option.is-selected .course-dot {
  border-color: var(--feedback-accent-strong);
  background: radial-gradient(circle at center, var(--feedback-accent-strong) 0 48%, #fff 54% 100%);
}

.action-row {
  display: flex;
  justify-content: center;
  gap: 14px;
  padding-top: 8px;
}

.submit-status {
  text-align: center;
  color: #7d6850;
  line-height: 1.8;
}

:deep(.el-input__wrapper),
:deep(.el-textarea__inner) {
  box-shadow: none;
  border-radius: 18px;
  border: 1px solid rgba(137, 112, 81, 0.18);
  background: rgba(255, 255, 255, 0.88);
}

:deep(.el-input__wrapper.is-focus),
:deep(.el-textarea__inner:focus) {
  border-color: rgba(31, 143, 255, 0.42);
  box-shadow: 0 0 0 3px rgba(31, 143, 255, 0.08);
}

:deep(.el-input__inner),
:deep(.el-textarea__inner) {
  color: var(--feedback-text);
  font-size: 16px;
  line-height: 1.7;
}

:deep(.el-button--primary) {
  min-width: 168px;
  border: none;
  background: linear-gradient(135deg, #1f8fff, #2e77f0);
  box-shadow: 0 16px 28px rgba(46, 119, 240, 0.22);
}

.mode-public {
  border-radius: 32px;
}

@media (max-width: 768px) {
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
    font-size: 16px;
    flex-wrap: wrap;
    gap: 6px 10px;
  }

  .course-attendance-link {
    margin-left: 27px;
  }

  .action-row {
    flex-direction: column-reverse;
  }

  :deep(.el-button) {
    width: 100%;
  }
}
</style>
