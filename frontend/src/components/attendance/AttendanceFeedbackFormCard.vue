<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Delete, Plus } from '@element-plus/icons-vue'

import SortableOrderHandle from '@/components/SortableOrderHandle.vue'
import {
  fetchAttendanceFeedbackFormMeta,
  submitAttendanceFeedback,
  updateAttendanceFeedbackFormMeta,
  type AttendanceFeedbackFormMeta,
} from '@/api/attendance'
import { requirePageCanonicalPath } from '@/router/pageRegistry'
import { toStandalonePath } from '@/router/standalone'
import { useSortableList } from '@/utils/useSortableList'

type FeedbackFormMode = 'workspace' | 'public'

type FeedbackDraft = {
  course: string
  studentId: string
  studentName: string
  correctionRequest: string
  extraNote: string
}

type PersistedFeedbackDraft = Pick<FeedbackDraft, 'course' | 'studentId' | 'studentName'>

const FEEDBACK_FORM_STORAGE_KEY = 'codeyun-attendance-feedback-draft'
const ATTENDANCE_WJX_COLLECT_STANDALONE_PATH = toStandalonePath(requirePageCanonicalPath('AttendanceWjxCollect'))

const props = withDefaults(
  defineProps<{
    mode?: FeedbackFormMode
    standalonePath?: string
  }>(),
  {
    mode: 'workspace',
  },
)

const form = reactive<FeedbackDraft>({
  course: '',
  studentId: '',
  studentName: '',
  correctionRequest: '',
  extraNote: '',
})

const lastSubmittedAt = ref('')
const formMeta = ref<AttendanceFeedbackFormMeta | null>(null)
const loadingFormMeta = ref(false)
const readyToPersist = ref(false)
const submitting = ref(false)
const savingCourseCatalog = ref(false)
const editableCourseNames = ref<string[]>([])
const pendingCourseName = ref('')
const editingCourseIndex = ref<number | null>(null)
const editingCourseName = ref('')
const courseListRef = ref<HTMLElement | null>(null)
const renameInputRef = ref<{ focus?: () => void; select?: () => void } | null>(null)
const courseOptions = computed(() => formMeta.value?.course_names ?? [])
const isWorkspaceMode = computed(() => props.mode === 'workspace')
const canManageCourseCatalog = computed(() => isWorkspaceMode.value)
const displayCourseOptions = computed(() => (
  canManageCourseCatalog.value ? editableCourseNames.value : courseOptions.value
))
const validCourseOptions = computed(() => new Set(displayCourseOptions.value))
const pageTitle = computed(() => (isWorkspaceMode.value ? '采集配置' : '考勤问题反馈表'))
const resolvedStandalonePath = computed(() => props.standalonePath ?? ATTENDANCE_WJX_COLLECT_STANDALONE_PATH)

function normalizeStoredText(value: unknown) {
  return typeof value === 'string' ? value : ''
}

function normalizeStoredCourse(value: unknown) {
  return normalizeStoredText(value)
}

function hasCourseOption(value: string) {
  return validCourseOptions.value.has(value)
}

function normalizeCourseNames(items: string[]) {
  const seen = new Set<string>()
  const result: string[] = []
  for (const item of items) {
    const text = item.trim()
    if (!text || seen.has(text)) {
      continue
    }
    seen.add(text)
    result.push(text)
  }
  return result
}

async function loadFeedbackFormMeta(showError = true) {
  loadingFormMeta.value = true
  try {
    formMeta.value = await fetchAttendanceFeedbackFormMeta()
    if (form.course && !hasCourseOption(form.course)) {
      form.course = ''
    }
  } catch (error: any) {
    if (showError) {
      ElMessage.error(error.response?.data?.detail || '加载采集配置失败')
    }
  } finally {
    loadingFormMeta.value = false
  }
}

async function saveCourseCatalog(nextCourseNames: string[]) {
  if (savingCourseCatalog.value) {
    return false
  }

  const previousCourseNames = editableCourseNames.value.slice()
  const previousSelectedCourse = form.course
  const normalizedCourseNames = normalizeCourseNames(nextCourseNames)
  if (!normalizedCourseNames.length) {
    ElMessage.warning('至少保留一个所属课程')
    return false
  }

  editableCourseNames.value = normalizedCourseNames
  if (form.course && !normalizedCourseNames.includes(form.course)) {
    form.course = ''
  }

  savingCourseCatalog.value = true
  try {
    formMeta.value = await updateAttendanceFeedbackFormMeta({
      course_names: normalizedCourseNames,
    })
    editableCourseNames.value = (formMeta.value?.course_names ?? []).slice()
    return true
  } catch (error: any) {
    editableCourseNames.value = previousCourseNames
    if (previousSelectedCourse && previousCourseNames.includes(previousSelectedCourse)) {
      form.course = previousSelectedCourse
    }
    ElMessage.error(error.response?.data?.detail || '保存采集配置失败')
    return false
  } finally {
    savingCourseCatalog.value = false
  }
}

async function appendCourseOption() {
  const nextCourseName = pendingCourseName.value.trim()
  if (!nextCourseName) {
    ElMessage.warning('请先输入课程名称，空白内容不会保存')
    return
  }
  if (editableCourseNames.value.includes(nextCourseName)) {
    ElMessage.warning('该课程已在清单里')
    return
  }

  const saved = await saveCourseCatalog([...editableCourseNames.value, nextCourseName])
  if (saved) {
    pendingCourseName.value = ''
  }
}

function cancelRenameCourse() {
  editingCourseIndex.value = null
  editingCourseName.value = ''
}

async function beginRenameCourse(index: number) {
  if (savingCourseCatalog.value) {
    return
  }

  const currentName = editableCourseNames.value[index]
  if (!currentName) {
    return
  }

  editingCourseIndex.value = index
  editingCourseName.value = currentName
  await nextTick()
  renameInputRef.value?.focus?.()
  renameInputRef.value?.select?.()
}

async function commitRenameCourse(index: number) {
  if (editingCourseIndex.value !== index) {
    return
  }

  const originalName = editableCourseNames.value[index]
  const nextCourseName = editingCourseName.value.trim()
  cancelRenameCourse()

  if (!originalName || nextCourseName === originalName) {
    return
  }
  if (!nextCourseName) {
    ElMessage.warning('课程名称不能为空，已保留原名称')
    return
  }
  if (editableCourseNames.value.some((course, courseIndex) => courseIndex !== index && course === nextCourseName)) {
    ElMessage.warning('该课程已在清单里，已保留原名称')
    return
  }

  const nextCourseNames = editableCourseNames.value.slice()
  nextCourseNames[index] = nextCourseName
  const shouldKeepSelectedCourse = form.course === originalName
  const saved = await saveCourseCatalog(nextCourseNames)
  if (saved && shouldKeepSelectedCourse) {
    form.course = nextCourseName
  }
}

async function removeCourseOption(index: number) {
  if (editableCourseNames.value.length <= 1) {
    ElMessage.warning('至少保留一个所属课程')
    return
  }

  const nextCourseNames = editableCourseNames.value.filter((_, itemIndex) => itemIndex !== index)
  await saveCourseCatalog(nextCourseNames)
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

    form.course = normalizedCourse
    form.studentId = normalizedStudentId
    form.studentName = normalizedStudentName
    form.correctionRequest = normalizedCorrectionRequest
    form.extraNote = normalizedExtraNote
    persistFormToLocalStorage()
  } catch (error: any) {
    ElMessage.error(error.response?.data?.detail || '提交失败')
  } finally {
    submitting.value = false
  }
}

onMounted(async () => {
  hydrateFormFromLocalStorage()
  await loadFeedbackFormMeta()

  readyToPersist.value = true
  persistFormToLocalStorage()
})

watch(courseOptions, () => {
  if (!savingCourseCatalog.value) {
    editableCourseNames.value = courseOptions.value.slice()
  }
  if (editingCourseIndex.value !== null) {
    cancelRenameCourse()
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

useSortableList({
  listRef: courseListRef,
  getDeps: () => [
    editableCourseNames.value.length,
    canManageCourseCatalog.value,
    savingCourseCatalog.value,
    editingCourseIndex.value,
  ],
  isEnabled: () => (
    canManageCourseCatalog.value
    && !savingCourseCatalog.value
    && editingCourseIndex.value === null
  ),
  ghostClass: 'course-drag-ghost',
  onReorder: async (oldIndex, newIndex) => {
    const nextCourseNames = editableCourseNames.value.slice()
    const [movedItem] = nextCourseNames.splice(oldIndex, 1)
    if (!movedItem) {
      return
    }
    nextCourseNames.splice(newIndex, 0, movedItem)
    await saveCourseCatalog(nextCourseNames)
  },
})
</script>

<template>
  <section class="feedback-card" :class="[`mode-${props.mode}`]">
    <div class="card-banner">
      <div class="banner-copy">
        <h1>{{ pageTitle }}</h1>
        <p v-if="isWorkspaceMode" class="banner-description">
          这里配置采集表模板，目前先维护反馈表第 1 题使用的课程清单。真实采集请打开右上角的采集页面。
        </p>
      </div>
      <RouterLink
        v-if="props.mode === 'workspace'"
        :to="resolvedStandalonePath"
        class="banner-link"
      >
        采集页面
      </RouterLink>
    </div>

    <div class="card-body">
      <section v-if="isWorkspaceMode" class="config-section">
        <div class="config-header">
          <div>
            <h2>配置</h2>
            <p>反馈表第 1 题的“所属课程”会直接读取这里的顺序；支持拖拽排序、双击重命名、删除，并在末尾追加新课程。</p>
          </div>
          <span v-if="canManageCourseCatalog && savingCourseCatalog" class="config-status">保存中...</span>
        </div>

        <div v-if="loadingFormMeta" class="course-empty">
          正在加载课程清单...
        </div>
        <template v-else-if="displayCourseOptions.length">
          <div
            v-if="canManageCourseCatalog"
            ref="courseListRef"
            class="course-editor-list"
          >
            <div
              v-for="(course, index) in editableCourseNames"
              :key="`${course}-${index}`"
              class="course-editor-row"
            >
              <SortableOrderHandle
                :index="index"
                :total="editableCourseNames.length"
                size="sm"
                :disabled="savingCourseCatalog"
              />
              <el-input
                v-if="editingCourseIndex === index"
                ref="renameInputRef"
                v-model="editingCourseName"
                class="course-rename-input"
                size="large"
                maxlength="80"
                @keydown.enter.prevent="commitRenameCourse(index)"
                @keydown.esc.prevent="cancelRenameCourse"
                @blur="commitRenameCourse(index)"
              />
              <button
                v-else
                type="button"
                class="course-config-name course-config-trigger"
                :disabled="savingCourseCatalog"
                @dblclick.stop="beginRenameCourse(index)"
              >
                {{ course }}
              </button>
              <el-button
                text
                type="danger"
                :icon="Delete"
                :disabled="savingCourseCatalog || editableCourseNames.length <= 1"
                @click.stop="removeCourseOption(index)"
              >
                删除
              </el-button>
            </div>
          </div>

          <div v-else class="course-readonly-list">
            <div
              v-for="(course, index) in displayCourseOptions"
              :key="course"
              class="course-readonly-row"
            >
              <span class="course-order-badge">{{ index + 1 }}</span>
              <span>{{ course }}</span>
            </div>
          </div>

          <div v-if="canManageCourseCatalog" class="course-add-row">
            <el-input
              v-model="pendingCourseName"
              size="large"
              placeholder="新增课程名称"
              :disabled="savingCourseCatalog"
              @keyup.enter="appendCourseOption"
            />
            <el-button
              type="primary"
              :icon="Plus"
              :disabled="savingCourseCatalog"
              @click="appendCourseOption"
            >
              新增
            </el-button>
          </div>
        </template>
        <template v-else>
          <div class="course-empty">
            {{ canManageCourseCatalog ? '暂未配置课程清单，请先新增一个课程。' : '暂未配置课程清单。' }}
          </div>
          <div v-if="canManageCourseCatalog" class="course-add-row">
            <el-input
              v-model="pendingCourseName"
              size="large"
              placeholder="新增课程名称"
              :disabled="savingCourseCatalog"
              @keyup.enter="appendCourseOption"
            />
            <el-button
              type="primary"
              :icon="Plus"
              :disabled="savingCourseCatalog"
              @click="appendCourseOption"
            >
              新增
            </el-button>
          </div>
        </template>
      </section>

      <template v-else>
        <section class="question-block">
          <div class="question-title">
            <span class="required-star">*</span>
            <span>1. 所属课程</span>
          </div>
          <div v-if="loadingFormMeta" class="course-empty">
            正在加载课程清单...
          </div>
          <div v-else-if="displayCourseOptions.length" class="course-list">
            <button
              v-for="course in displayCourseOptions"
              :key="course"
              type="button"
              class="course-option"
              :class="{ 'is-selected': form.course === course }"
              @click="form.course = course"
            >
              <span class="course-dot" />
              <span>{{ course }}</span>
            </button>
          </div>
          <div v-else class="course-empty">
            暂未配置课程清单。
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
          <span>5. 其他补充说明</span>
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
          <el-button size="large" type="primary" :loading="submitting" @click="submitForm">提交</el-button>
        </div>

        <div v-if="lastSubmittedAt" class="submit-status">
          {{ lastSubmittedAt }} 已提交
        </div>
      </template>
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

.banner-description {
  margin: 12px 0 0;
  max-width: 560px;
  color: var(--feedback-muted);
  line-height: 1.75;
}

.banner-link {
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  min-height: 42px;
  padding: 0 16px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.82);
  color: #1f4f7a;
  font-weight: 600;
  text-decoration: none;
  transition: transform 0.18s ease, box-shadow 0.18s ease;
}

.banner-link:hover {
  transform: translateY(-1px);
  box-shadow: 0 10px 24px rgba(31, 79, 122, 0.16);
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

.config-section {
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.config-header {
  display: flex;
  justify-content: space-between;
  gap: 18px;
  align-items: flex-start;
}

.config-header h2 {
  margin: 0;
  color: var(--feedback-text);
  font-size: 28px;
  line-height: 1.2;
  font-family: "Source Han Serif SC", "Songti SC", "Noto Serif SC", serif;
}

.config-header p {
  margin: 10px 0 0;
  color: var(--feedback-muted);
  line-height: 1.75;
}

.config-status {
  flex-shrink: 0;
  color: #1f4f7a;
  font-weight: 600;
  line-height: 40px;
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

.course-editor-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.course-editor-row {
  display: flex;
  align-items: center;
  gap: 10px;
}

.course-config-name {
  flex: 1;
  min-height: 40px;
  display: flex;
  align-items: center;
  width: 100%;
  padding: 0;
  border: none;
  background: transparent;
  color: var(--feedback-text);
  font-size: 16px;
  line-height: 1.7;
  text-align: left;
}

.course-config-trigger {
  cursor: text;
}

.course-config-trigger:hover {
  color: #1f4f7a;
}

.course-rename-input {
  flex: 1;
}

.course-rename-input :deep(.el-input__wrapper) {
  min-height: 40px;
}

.course-readonly-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.course-readonly-row {
  display: flex;
  gap: 12px;
  align-items: center;
  min-height: 40px;
  color: var(--feedback-text);
  font-size: 16px;
  line-height: 1.7;
}

.course-order-badge {
  display: inline-flex;
  justify-content: center;
  align-items: center;
  width: 28px;
  height: 28px;
  border-radius: 999px;
  background: rgba(31, 79, 122, 0.08);
  color: #1f4f7a;
  font-size: 13px;
  font-weight: 700;
  flex-shrink: 0;
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
  gap: 10px;
  align-items: center;
  width: 100%;
  padding: 6px 0;
  border: none;
  border-radius: 0;
  background: transparent;
  color: var(--feedback-text);
  font-size: 16px;
  line-height: 1.7;
  text-align: left;
  cursor: pointer;
  transition:
    color 0.16s ease,
    opacity 0.16s ease;
}

.course-option:hover {
  opacity: 0.82;
}

.course-option.is-selected {
  color: #1f4f7a;
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

.course-add-row {
  display: flex;
  gap: 12px;
  align-items: stretch;
}

.course-add-row :deep(.el-input) {
  flex: 1;
}

.course-drag-ghost {
  opacity: 0.55;
  background: rgba(191, 219, 254, 0.35);
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

:deep(.course-add-row .el-button--primary) {
  min-width: 120px;
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

  .question-title {
    font-size: 20px;
  }

  .config-header,
  .course-editor-row,
  .course-add-row {
    flex-direction: column;
    align-items: stretch;
  }

  .course-option {
    font-size: 16px;
  }

  .action-row {
    flex-direction: column-reverse;
  }

  :deep(.el-button) {
    width: 100%;
  }
}
</style>
