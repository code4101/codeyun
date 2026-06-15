<template>
  <span
    v-if="displayLabel || iconComponent"
    class="note-form-badge"
    :class="{ 'is-compact': compact }"
    :style="badgeStyle"
  >
    <el-icon v-if="iconComponent" class="form-icon"><component :is="iconComponent" /></el-icon>
    <span v-if="displayLabel" class="form-label">{{ formConfig.label }}</span>
  </span>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import { Document, Headset, MagicStick, Reading, Tickets, VideoPlay } from '@element-plus/icons-vue';
import { getNoteFormConfig } from '@/utils/nodeConfig';

const props = withDefaults(defineProps<{
  form?: string | null;
  showLabel?: boolean;
  compact?: boolean;
  textColor?: string | null;
}>(), {
  form: 'note',
  showLabel: false,
  compact: false,
  textColor: null
});

const formConfig = computed(() => getNoteFormConfig(props.form));
const badgeStyle = computed(() => (
  props.textColor ? { color: props.textColor } : undefined
));
const iconComponent = computed(() => {
  if (formConfig.value.id === 'document') return Document;
  if (formConfig.value.id === 'memo') return Tickets;
  if (formConfig.value.id === 'music') return Headset;
  if (formConfig.value.id === 'video') return VideoPlay;
  if (formConfig.value.id === 'game') return MagicStick;
  if (formConfig.value.id === 'book') return Reading;
  return null;
});
const displayLabel = computed(() => Boolean(props.showLabel));
</script>

<style scoped>
.note-form-badge{display:inline-flex;align-items:center;gap:4px;min-width:0}
.note-form-badge.is-compact{gap:0}
.form-icon{font-size:13px;line-height:1;flex-shrink:0}
.form-label{font-size:12px;line-height:1;white-space:nowrap}
</style>
