<template>
  <div class="chat-data-page">
    <div class="chat-data-header">
      <h2>聊天数据</h2>
      <span>微信 / TIM 等聊天归档生成的日总结节点</span>
    </div>
    <CalendarNotes
      tab-id="chat-data-calendar"
      :data-filter-rules="chatDataFilterRules"
      :allow-create="false"
      :show-codex-workload="false"
      :fixed-view-filter-rules="chatDataFilterRules"
      split-pane-storage-key="notes:chat-data:calendar:split-pane-height"
    />
  </div>
</template>

<script setup lang="ts">
import { onBeforeMount } from 'vue'
import CalendarNotes from '../center/CalendarNotes.vue'
import { useNoteStore, type NoteProgramRule } from '@/api/notes'

const noteStore = useNoteStore()

const chatDataFilterRules: NoteProgramRule[] = [
  {
    action: 'filter',
    matcher: {
      kind: 'field',
      field: 'custom_fields.wechat_daily_source',
      op: 'eq',
      value: 'mf:v4_db_storage',
    },
  },
]

onBeforeMount(() => {
  noteStore.ensureVirtualTab({
    id: 'chat-data-calendar',
    label: '聊天数据',
    type: 'calendar',
    closable: false,
  })
})
</script>

<style scoped>
.chat-data-page {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  background: #f5f7fa;
  overflow: hidden;
}

.chat-data-header {
  display: flex;
  align-items: baseline;
  gap: 12px;
  padding: 18px 22px 8px;
  flex-shrink: 0;
}

.chat-data-header h2 {
  margin: 0;
  color: #303133;
}

.chat-data-header span {
  color: #8a94a6;
  font-size: 13px;
}

.chat-data-page :deep(.calendar-notes-layout) {
  flex: 1;
  min-height: 0;
  padding-top: 12px;
}
</style>
