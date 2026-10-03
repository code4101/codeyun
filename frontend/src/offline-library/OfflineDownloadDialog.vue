<script setup lang="ts">
import { ref, watch, onBeforeUnmount } from 'vue'
import { downloadOfflineBook, type OfflineDownloadTarget } from './download'
import { listOfflineBooks, offlineBookKey, formatOfflineBytes, OFFLINE_LIBRARY_URL, type OfflineBookSummary } from './store'
const props = defineProps<{ modelValue: boolean; target: OfflineDownloadTarget | null }>()
const emit = defineEmits<{ 'update:modelValue': [value: boolean] }>()
const busy = ref(false), status = ref(''), error = ref(''), cached = ref<OfflineBookSummary>()
let controller: AbortController | undefined
watch(() => [props.modelValue, props.target] as const, async ([visible, target]) => {
  if (!visible || !target || busy.value) return
  error.value = ''; status.value = ''
  const key = offlineBookKey(target.kind, target.id)
  cached.value = (await listOfflineBooks()).find(book => book.key === key)
})
async function start() {
  if (!props.target || busy.value) return
  busy.value = true; error.value = ''; controller = new AbortController()
  try {
    cached.value = await downloadOfflineBook(props.target, message => { status.value = message }, controller.signal)
    status.value = '整本书已保存，可开启飞行模式阅读'
  } catch (cause) {
    error.value = controller.signal.aborted ? '下载已取消，原有离线副本保留' : cause instanceof Error ? cause.message : '下载失败，请重试'
    status.value = ''
  } finally { busy.value = false }
}
onBeforeUnmount(() => controller?.abort())
</script>
<template>
  <el-dialog :model-value="modelValue" title="下载供离线阅读" width="min(460px, 94vw)" :close-on-click-modal="!busy" @update:model-value="emit('update:modelValue', $event)">
    <p>保存整本正文、图片、目录及已有批注。全部下载完成后，飞行模式下可从离线书架重新打开。</p>
    <p v-if="cached">{{ cached.title }} · {{ formatOfflineBytes(cached.bytes) }} · {{ new Date(cached.downloadedAt).toLocaleString() }}</p>
    <p v-if="status" role="status">{{ status }}</p>
    <p v-if="error" role="alert">{{ error }}</p>
    <template #footer>
      <a :href="OFFLINE_LIBRARY_URL" target="_blank" rel="noopener" style="margin-right:16px">打开离线书架</a>
      <el-button v-if="busy" @click="controller?.abort()">取消下载</el-button>
      <el-button v-else type="primary" @click="start">{{ cached ? '更新整本缓存' : '下载整本书' }}</el-button>
    </template>
  </el-dialog>
</template>
