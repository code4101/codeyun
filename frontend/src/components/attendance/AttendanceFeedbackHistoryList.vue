<script setup lang="ts">
import { computed } from 'vue'

import type { AttendanceWjxDataItem } from '@/api/attendance'

const props = withDefaults(
  defineProps<{
    items?: AttendanceWjxDataItem[]
    total?: number
    loading?: boolean
    ready?: boolean
    title?: string
    loadingText?: string
    emptyText?: string
    studentId?: string
    studentName?: string
  }>(),
  {
    items: () => [],
    total: 0,
    loading: false,
    ready: false,
    title: '历史反馈',
    loadingText: '正在查询历史反馈...',
    emptyText: '暂未查到这个学员的历史反馈。',
    studentId: '',
    studentName: '',
  },
)

const visible = computed(() => props.ready || props.loading || props.items.length > 0)

function hasText(value: unknown) {
  return String(value ?? '').trim().length > 0
}

function hasSecondaryFields(item: AttendanceWjxDataItem) {
  return (
    hasText(item.extra_note)
    || (hasText(item.process_note) && item.process_note !== item.process_status)
  )
}

function isResolved(item: AttendanceWjxDataItem) {
  return resolveProcessStatus(item).includes('已')
}

function isPending(item: AttendanceWjxDataItem) {
  return !item.process_status?.trim()
}

function resolveProcessStatus(item: AttendanceWjxDataItem) {
  return item.process_status?.trim() || '待处理'
}
</script>

<template>
  <section v-if="visible" class="feedback-history">
    <div class="history-header">
      <h2>{{ title }}</h2>
      <span v-if="items.length" class="history-count">
        最近 {{ items.length }} 条<span v-if="total > items.length"> / 共 {{ total }} 条</span>
      </span>
    </div>

    <div v-if="loading && !items.length" class="history-empty">
      {{ loadingText }}
    </div>
    <div v-else-if="ready && !items.length" class="history-empty">
      {{ emptyText }}
    </div>

    <div v-if="items.length" class="history-list">
      <article
        v-for="item in items"
        :key="`${item.activity_id}-${item.seq}-${item.id}`"
        class="history-record"
      >
        <div class="record-head">
          <span class="record-seq">序号 {{ item.seq }}</span>
          <span v-if="item.submitted_at_text" class="record-time">{{ item.submitted_at_text }}</span>
          <span
            class="record-status"
            :class="{
              'is-resolved': isResolved(item),
              'is-pending': isPending(item),
            }"
          >
            {{ resolveProcessStatus(item) }}
          </span>
        </div>

        <div
          v-if="hasText(item.correction_request) && !hasSecondaryFields(item)"
          class="history-primary-text"
        >
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
</template>

<style scoped>
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
  color: var(--feedback-text, #334155);
  font-size: 18px;
  line-height: 1.5;
  font-weight: 700;
}

.history-count {
  flex: 0 0 auto;
  color: var(--feedback-muted, #64748b);
  font-size: 13px;
  line-height: 20px;
}

.history-empty {
  padding: 10px 0;
  color: var(--feedback-muted, #64748b);
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
  color: var(--feedback-muted, #64748b);
  font-size: 13px;
  line-height: 20px;
}

.record-seq {
  color: var(--feedback-text, #334155);
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
  color: var(--feedback-text, #334155);
  font-size: 14px;
  line-height: 1.7;
}

.history-fields dt {
  color: var(--feedback-muted, #64748b);
  font-weight: 600;
}

.history-fields dd {
  min-width: 0;
  margin: 0;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.history-primary-text {
  color: var(--feedback-text, #334155);
  font-size: 14px;
  line-height: 1.7;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
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
