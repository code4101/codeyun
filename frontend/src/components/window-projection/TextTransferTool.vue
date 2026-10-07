<script setup lang="ts">
import { ref } from 'vue'
defineProps<{ modelValue: string; image: string; busy: boolean; located: boolean; target: string }>()
const emit = defineEmits<{ 'update:modelValue': [value: string]; locate: []; transfer: [send: boolean, sendKey: 'enter' | 'ctrl_enter']; file: [file: File]; clearImage: []; transferImage: []; readImage: []; copyImage: []; downloadImage: [] }>()
const sendKey = ref<'enter' | 'ctrl_enter'>('enter')
const picker = ref<HTMLInputElement>()
function pasteImage(event: ClipboardEvent) {
  const file = Array.from(event.clipboardData?.files || []).find(file => file.type.startsWith('image/'))
  if (file) { event.preventDefault(); emit('file', file) }
}
function chooseImage(event: Event) {
  const input = event.target as HTMLInputElement
  if (input.files?.[0]) emit('file', input.files[0])
  input.value = ''
}
</script>
<template>
  <div class="text-transfer" @paste="pasteImage">
    <textarea :value="modelValue" aria-label="待传递文本" @input="emit('update:modelValue', ($event.target as HTMLTextAreaElement).value)" />
    <div class="controls">
      <button :disabled="busy || !target" @click="emit('locate')">{{ located ? '重新定位输入框' : '定位输入框' }}</button>
      <select v-model="sendKey" aria-label="应用发送快捷键"><option value="enter">Enter 发送</option><option value="ctrl_enter">Ctrl+Enter 发送</option></select>
      <button :disabled="busy || !located || !modelValue.trim()" @click="emit('transfer', false, sendKey)">仅填入</button>
      <button :disabled="busy || !located || !modelValue.trim()" @click="emit('transfer', true, sendKey)">填入并发送 ↑</button>
    </div>
    <input ref="picker" hidden type="file" accept="image/png,image/jpeg,image/webp,image/bmp" @change="chooseImage" />
    <div class="controls">
      <button :disabled="busy" @click="picker?.click()">选择图片</button>
      <button :disabled="busy" @click="emit('readImage')">读取远端剪贴板图片</button>
      <button :disabled="busy || !located || !image" @click="emit('transferImage')">填入图片</button>
    </div>
    <p class="hint">可在此粘贴图片（Ctrl+V）。远端先通过应用菜单复制图片，再读取剪贴板。</p>
    <div v-if="image" class="image-preview">
      <img :src="image" alt="待传递图片" />
      <div class="controls"><button @click="emit('copyImage')">复制图片到本地</button><button @click="emit('downloadImage')">下载图片</button><button @click="emit('clearImage')">移除</button></div>
    </div>
  </div>
</template>
<style scoped>
.image-preview{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.image-preview img{max-width:180px;max-height:100px;object-fit:contain}.hint{margin:0;font-size:11px;color:var(--reader-muted)}
.text-transfer{display:flex;flex-direction:column;gap:8px;padding:12px;height:100%;min-height:0;box-sizing:border-box;color:var(--reader-text)}textarea{flex:1;min-height:48px;resize:none;background:var(--reader-content);border:1px solid var(--reader-border);border-radius:10px;color:inherit;padding:10px;font:inherit}.controls{display:flex;gap:8px;flex-wrap:wrap}button,select{background:var(--reader-content);border:1px solid var(--reader-border);color:inherit;border-radius:6px;padding:6px 9px;font:inherit}button{cursor:pointer}button:disabled{opacity:.4;cursor:default}
</style>
