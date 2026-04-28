<script setup lang="ts">
import type { StyleValue } from 'vue';
import { ref } from 'vue';
import SortableOrderHandle from '@/components/SortableOrderHandle.vue';
import { useSortableList } from '@/utils/useSortableList';

interface SlotState {
  itemId: string | null;
  locked: boolean;
}

interface SlotOption {
  id: string;
  label: string;
}

const props = defineProps<{
  slots: SlotState[];
  listStyle?: StyleValue;
  getOptions: (slotIndex: number) => SlotOption[];
}>();

const emit = defineEmits<{
  (e: 'update-item', slotIndex: number, itemId: string | null): void;
  (e: 'toggle-lock', slotIndex: number): void;
  (e: 'reorder', payload: { oldIndex: number; newIndex: number }): void;
}>();

const slotListRef = ref<HTMLElement | null>(null);

useSortableList({
  listRef: slotListRef,
  getDeps: () => [
    props.slots.length,
    props.slots.map(slot => slot.itemId || '').join('|'),
    props.slots.map(slot => (slot.locked ? '1' : '0')).join(''),
  ] as const,
  isEnabled: () => props.slots.length > 1,
  ghostClass: 'formation-slot-ghost',
  onReorder: (oldIndex, newIndex) => emit('reorder', { oldIndex, newIndex }),
});
</script>

<template>
  <div ref="slotListRef" class="slot-list" :style="listStyle">
    <div
      v-for="(slot, slotIndex) in slots"
      :key="`${slotIndex}:${slot.itemId || ''}`"
      class="slot-row"
    >
      <SortableOrderHandle :index="slotIndex" :total="slots.length" size="sm" />

      <el-select
        :model-value="slot.itemId"
        class="slot-select"
        size="small"
        clearable
        filterable
        @update:model-value="value => emit('update-item', slotIndex, value || null)"
      >
        <el-option
          v-for="item in getOptions(slotIndex)"
          :key="item.id"
          :label="item.label"
          :value="item.id"
        />
      </el-select>

      <el-button
        size="small"
        :type="slot.locked ? 'primary' : 'default'"
        :disabled="!slot.itemId"
        @click="emit('toggle-lock', slotIndex)"
      >
        {{ slot.locked ? '已锁定' : '锁定' }}
      </el-button>
    </div>
  </div>
</template>

<style scoped>
.slot-list {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 6px;
}

.slot-row {
  display: grid;
  align-items: center;
  gap: 8px;
  grid-template-columns: 22px fit-content(760px) 78px;
  width: max-content;
  max-width: 100%;
}

.slot-select {
  width: var(--slot-select-width, max-content);
  min-width: 0;
  max-width: none;
}

.slot-select :deep(.el-select__wrapper) {
  width: auto;
  min-height: 32px;
  font-size: 14px;
}

.slot-select :deep(.el-select__selected-item),
.slot-select :deep(.el-select__placeholder) {
  font-size: 14px;
}

.slot-row > .el-button {
  min-height: 32px;
  padding: 0 12px;
  font-size: 14px;
}

:deep(.formation-slot-ghost) {
  opacity: 0.7;
  background: #ecf5ff;
}

@media (max-width: 760px) {
  .slot-list {
    align-items: stretch;
  }

  .slot-row {
    width: 100%;
    grid-template-columns: 22px minmax(0, 1fr) 78px;
  }

  .slot-select {
    width: 100%;
    min-width: 0;
    max-width: none;
  }
}
</style>
