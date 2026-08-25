<template>
  <section class="sequence-group">
    <header>
      <strong>{{ group.original_time }}</strong>
    </header>
    <div ref="listRef" class="sequence-list">
      <div v-for="(item, index) in group.items" :key="item.task_id" class="sequence-row">
        <SortableOrderHandle :index="index" :total="group.items.length" size="sm" />
        <span class="task-label">{{ item.task_label }}</span>
        <span class="time-projection">顺序 {{ index + 1 }}</span>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { ref } from 'vue';
import SortableOrderHandle from '@/components/SortableOrderHandle.vue';
import { useSortableList } from '@/utils/useSortableList';
import type { FanxiuDataAnnotationSchedulerTimeSequenceGroup } from '@/api/fanxiu';

const props = defineProps<{ group: FanxiuDataAnnotationSchedulerTimeSequenceGroup }>();
const emit = defineEmits<{ reorder: [oldIndex: number, newIndex: number] }>();
const listRef = ref<HTMLElement | null>(null);

useSortableList({
  listRef,
  getDeps: () => [props.group.key, props.group.items.map(item => item.task_id).join('|')],
  onReorder: (oldIndex, newIndex) => emit('reorder', oldIndex, newIndex),
});
</script>

<style scoped>
.sequence-group {
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 10px;
  overflow: hidden;
}

header,
.sequence-row {
  display: flex;
  align-items: center;
}

header {
  gap: 8px;
  padding: 9px 12px;
  background: var(--el-fill-color-light);
}

header span {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.sequence-row {
  gap: 10px;
  min-height: 42px;
  padding: 0 12px;
  border-top: 1px solid var(--el-border-color-lighter);
}

.task-label {
  min-width: 0;
  flex: 1;
}

.time-projection {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}
</style>
