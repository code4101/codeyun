<template>
  <div class="markdown-editor-container">
    <MdEditor 
      v-model="internalValue" 
      :preview="!readOnly" 
      :readOnly="readOnly"
      :formatCopiedText="formatCopiedText"
      @onChange="handleChange"
      @onSave="handleSave"
      theme="light"
      style="height: 100%;"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, watch, onMounted } from 'vue';
import { MdEditor } from 'md-editor-v3';
import 'md-editor-v3/lib/style.css';

const formatCopiedText = (text: string) => {
  return text;
};

const props = defineProps<{
  modelValue: string;
  readOnly?: boolean;
}>();

const emit = defineEmits<{
  (e: 'update:modelValue', value: string): void;
  (e: 'change', value: string): void;
  (e: 'save'): void;
}>();

const internalValue = ref(props.modelValue || '');

watch(() => props.modelValue, (newVal) => {
  if (newVal !== internalValue.value) {
    internalValue.value = newVal;
  }
});

const handleChange = (val: string) => {
  emit('update:modelValue', val);
  emit('change', val);
};

const handleSave = () => {
  emit('save');
};

</script>

<style scoped>
.markdown-editor-container {
  height: 100%;
  width: 100%;
  display: flex;
  flex-direction: column;
}
/* Optional: adjust editor styles to match the system theme */
:deep(.md-editor) {
  --md-bk-color: transparent;
}
/* Ensure <br> tags actually take up vertical space in the preview */
:deep(.md-editor-preview p:empty::before),
:deep(.md-editor-preview br) {
  content: "";
  display: block;
  min-height: 1.5em;
}
:deep(.md-editor-preview p:has(br:only-child)) {
  min-height: 1.5em;
  margin: 0.5em 0;
}
</style>
