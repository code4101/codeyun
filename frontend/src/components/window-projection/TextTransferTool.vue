<script setup lang="ts">
import { ref } from 'vue'
defineProps<{ modelValue: string; busy: boolean; located: boolean; target: string }>()
const emit = defineEmits<{ 'update:modelValue': [value: string]; locate: []; transfer: [send: boolean, sendKey: 'enter' | 'ctrl_enter'] }>()
const sendKey = ref<'enter' | 'ctrl_enter'>('enter')
</script>
<template>
  <div class="text-transfer">
    <textarea :value="modelValue" aria-label="待传递文本" @input="emit('update:modelValue', ($event.target as HTMLTextAreaElement).value)" />
    <div class="controls">
      <button :disabled="busy || !target" @click="emit('locate')">{{ located ? '重新定位输入框' : '定位输入框' }}</button>
      <select v-model="sendKey" aria-label="应用发送快捷键"><option value="enter">Enter 发送</option><option value="ctrl_enter">Ctrl+Enter 发送</option></select>
      <button :disabled="busy || !located || !modelValue.trim()" @click="emit('transfer', false, sendKey)">仅填入</button>
      <button :disabled="busy || !located || !modelValue.trim()" @click="emit('transfer', true, sendKey)">填入并发送 ↑</button>
    </div>
  </div>
</template>
<style scoped>
.text-transfer{display:flex;flex-direction:column;gap:8px;padding:12px;height:100%;min-height:0;box-sizing:border-box;color:var(--reader-text)}textarea{flex:1;min-height:48px;resize:none;background:var(--reader-content);border:1px solid var(--reader-border);border-radius:10px;color:inherit;padding:10px;font:inherit}.controls{display:flex;gap:8px;flex-wrap:wrap}button,select{background:var(--reader-content);border:1px solid var(--reader-border);color:inherit;border-radius:6px;padding:6px 9px;font:inherit}button{cursor:pointer}button:disabled{opacity:.4;cursor:default}
</style>
