<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { ArrowLeft, ArrowRight } from '@element-plus/icons-vue'

const props = withDefaults(defineProps<{
  page: number
  pageSize?: number
  total?: number
  pageCount?: number
  pageSizeOptions?: number[]
  showPageSize?: boolean
  disabled?: boolean
  align?: 'left' | 'right' | 'center'
}>(), {
  pageSize: 20,
  total: 0,
  pageCount: undefined,
  pageSizeOptions: () => [20, 50, 100, 200],
  showPageSize: true,
  disabled: false,
  align: 'right',
})

const emit = defineEmits<{
  'update:page': [value: number]
  'update:pageSize': [value: number]
  'page-change': [value: number]
  'page-size-change': [value: number]
}>()

const isEditingPage = ref(false)
const pageDraft = ref('')
const pageInputRef = ref<HTMLInputElement | null>(null)

const normalizedPageCount = computed(() => {
  if (typeof props.pageCount === 'number') return Math.max(1, Math.floor(props.pageCount))
  return Math.max(1, Math.ceil(Math.max(props.total, 0) / Math.max(props.pageSize, 1)))
})

const normalizedPage = computed(() => clampPage(props.page))

const pageSizeOptions = computed(() => {
  const options = props.pageSizeOptions.length ? props.pageSizeOptions : [20, 50, 100, 200]
  const unique = Array.from(new Set(options.map((item) => Number(item)).filter((item) => Number.isFinite(item) && item > 0)))
  return unique.sort((a, b) => a - b)
})

function clampPage(value: number) {
  const numberValue = Math.floor(Number(value))
  if (!Number.isFinite(numberValue)) return 1
  return Math.min(normalizedPageCount.value, Math.max(1, numberValue))
}

function commitPage(nextPage: number) {
  const target = clampPage(nextPage)
  if (target === props.page) return
  emit('update:page', target)
  emit('page-change', target)
}

function stepPage(delta: number) {
  if (props.disabled) return
  commitPage(normalizedPage.value + delta)
}

function changePageSize(value: number) {
  const nextPageSize = Number(value)
  if (!Number.isFinite(nextPageSize) || nextPageSize <= 0 || nextPageSize === props.pageSize) return
  emit('update:pageSize', nextPageSize)
  emit('page-size-change', nextPageSize)
}

async function startPageEdit() {
  if (props.disabled) return
  isEditingPage.value = true
  pageDraft.value = String(normalizedPage.value)
  await nextTick()
  pageInputRef.value?.focus()
  pageInputRef.value?.select()
}

function commitPageEdit() {
  if (!isEditingPage.value) return
  isEditingPage.value = false
  const nextPage = Number.parseInt(pageDraft.value.trim(), 10)
  if (!Number.isFinite(nextPage)) return
  commitPage(nextPage)
}

function cancelPageEdit() {
  isEditingPage.value = false
  pageDraft.value = String(normalizedPage.value)
}

watch(() => props.page, (value) => {
  if (!isEditingPage.value) pageDraft.value = String(clampPage(value))
})
</script>

<template>
  <div class="standard-pagination" :class="`align-${align}`">
    <el-select
      v-if="showPageSize"
      :model-value="pageSize"
      class="standard-pagination__size"
      size="small"
      :disabled="disabled"
      @change="value => changePageSize(Number(value))"
    >
      <el-option
        v-for="size in pageSizeOptions"
        :key="size"
        :value="size"
        :label="`${size}条/页`"
      />
    </el-select>
    <span v-else class="standard-pagination__size-label">每页 {{ pageSize }} 条</span>

    <div class="standard-pagination__nav">
      <button
        class="standard-pagination__arrow"
        type="button"
        :disabled="disabled || normalizedPage <= 1"
        title="上一页"
        aria-label="上一页"
        @click="stepPage(-1)"
      >
        <ArrowLeft />
      </button>
      <input
        v-if="isEditingPage"
        ref="pageInputRef"
        v-model="pageDraft"
        class="standard-pagination__page-input"
        inputmode="numeric"
        :aria-label="`当前页，共 ${normalizedPageCount} 页`"
        @keydown.enter.prevent="commitPageEdit"
        @keydown.esc.prevent="cancelPageEdit"
        @blur="commitPageEdit"
      >
      <span
        v-else
        class="standard-pagination__status"
        title="双击编辑当前页"
        tabindex="0"
        @dblclick="startPageEdit"
        @keydown.enter.prevent="startPageEdit"
      >
        <b>{{ normalizedPage }}</b>
        <span>/{{ normalizedPageCount }}</span>
      </span>
      <button
        class="standard-pagination__arrow"
        type="button"
        :disabled="disabled || normalizedPage >= normalizedPageCount"
        title="下一页"
        aria-label="下一页"
        @click="stepPage(1)"
      >
        <ArrowRight />
      </button>
    </div>
  </div>
</template>

<style scoped>
.standard-pagination {
  display: flex;
  align-items: center;
  gap: 12px;
}

.standard-pagination.align-left {
  justify-content: flex-start;
}

.standard-pagination.align-center {
  justify-content: center;
}

.standard-pagination.align-right {
  justify-content: flex-end;
}

.standard-pagination__size {
  width: 96px;
  flex: 0 0 auto;
}

.standard-pagination__size-label {
  flex: 0 0 auto;
  font-size: 13px;
  color: #6b7280;
  white-space: nowrap;
}

.standard-pagination__nav {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  flex: 0 0 auto;
}

.standard-pagination__arrow {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  padding: 0;
  border: 1px solid #e5e7eb;
  border-radius: 6px;
  background: #fff;
  color: #374151;
  cursor: pointer;
}

.standard-pagination__arrow:hover:not(:disabled) {
  border-color: #cbd5e1;
  color: #111827;
  background: #f8fafc;
}

.standard-pagination__arrow:disabled {
  color: #cbd5e1;
  background: #f9fafb;
  cursor: not-allowed;
}

.standard-pagination__arrow svg {
  width: 14px;
  height: 14px;
}

.standard-pagination__status {
  display: inline-flex;
  align-items: baseline;
  justify-content: center;
  min-width: 54px;
  padding: 0 2px;
  font-size: 14px;
  line-height: 28px;
  color: #6b7280;
  cursor: text;
}

.standard-pagination__status b {
  color: #111827;
  font-weight: 600;
}

.standard-pagination__status:focus-visible {
  outline: 2px solid #93c5fd;
  outline-offset: 2px;
  border-radius: 4px;
}

.standard-pagination__page-input {
  width: 54px;
  height: 28px;
  padding: 0 6px;
  border: 1px solid #bfdbfe;
  border-radius: 6px;
  font-size: 14px;
  text-align: center;
  color: #111827;
  outline: none;
}

.standard-pagination__page-input:focus {
  border-color: #60a5fa;
  box-shadow: 0 0 0 2px rgba(96, 165, 250, 0.18);
}
</style>
