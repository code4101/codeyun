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
    errorText?: string
    studentId?: string
    studentName?: string
  }>(),
  {
    items: () => [],
    total: 0,
    loading: false,
    ready: false,
    title: '历史提问与回复',
    loadingText: '正在查询历史反馈...',
    emptyText: '暂未查到这个学员的历史反馈。',
    errorText: '',
    studentId: '',
    studentName: '',
  },
)

const visible = computed(() => props.ready || props.loading || props.items.length > 0)

function hasText(value: unknown) {
  return String(value ?? '').trim().length > 0
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
        <template v-if="total > items.length">最近 {{ items.length }} 条 / 共 {{ total }} 条</template>
        <template v-else>共 {{ items.length }} 条</template>
      </span>
    </div>

    <div v-if="errorText" class="history-empty" role="alert">{{ errorText }}</div>
    <div v-else-if="loading && !items.length" class="history-empty">
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
        </div>

        <dl class="history-fields">
          <template v-if="hasText(item.correction_request)">
            <dt>修正需求</dt>
            <dd>{{ item.correction_request }}</dd>
          </template>
          <template v-if="hasText(item.extra_note)">
            <dt>补充说明</dt>
            <dd>{{ item.extra_note }}</dd>
          </template>
          <dt>处理回复</dt>
          <dd>{{ resolveProcessStatus(item) }}</dd>
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
  font-size: 14px;
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

.history-fields {
  display: grid;
  grid-template-columns: 6em minmax(0, 1fr);
  gap: 0;
  border-top: 1px solid #e2e8f0;
  margin: 0;
  color: var(--feedback-text, #334155);
  font-size: 14px;
  line-height: 1.7;
}

.history-fields dt {
  background: #f6f8fa;
  color: var(--feedback-muted, #64748b);
  font-weight: 600;
}

.history-fields dt,
.history-fields dd {
  padding: 8px 10px;
  border-bottom: 1px solid #e2e8f0;
}

.history-fields dd {
  min-width: 0;
  margin: 0;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

@media (max-width: 360px) {
  .history-fields {
    grid-template-columns: 5em minmax(0, 1fr);
  }
}
</style>
