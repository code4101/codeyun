<script setup lang="ts">
import FanxiuActivityUpdateButton from './FanxiuActivityUpdateButton.vue'

defineProps<{
  modelValue: string
  activities: readonly { id: string; label: string }[]
  canCollect: boolean
  collectLoading?: boolean
  collectDisabled?: boolean
}>()

defineEmits<{
  'update:modelValue': [value: string]
  collect: []
}>()
</script>

<template>
  <div class="fanxiu-activity-toolbar">
    <slot />
    <el-select
      :model-value="modelValue"
      class="activity-instance-select"
      :disabled="collectLoading"
      placeholder="选择历史活动"
      no-data-text="暂无活动数据"
      @update:model-value="$emit('update:modelValue', $event)"
    >
      <el-option
        v-for="item in activities"
        :key="item.id"
        :label="item.label"
        :value="item.id"
      />
    </el-select>
    <FanxiuActivityUpdateButton
      :visible="canCollect"
      :loading="collectLoading"
      :disabled="collectDisabled"
      @collect="$emit('collect')"
    />
  </div>
</template>

<style scoped>
.fanxiu-activity-toolbar {
  display: flex;
  align-items: center;
  gap: 16px;
}

.activity-instance-select {
  width: 220px;
}

@media (max-width: 720px) {
  .fanxiu-activity-toolbar {
    align-items: flex-start;
    flex-direction: column;
    gap: 7px;
  }
}
</style>
