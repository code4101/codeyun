<script setup lang="ts">
import {computed, onBeforeUnmount, ref, watch} from 'vue';
import {controlPdfBookOcrJob, fetchPdfBookOcrJob, type PdfBookOcrJob} from '@/api/pdfDocuments';

const props = defineProps<{pdfId: number; revision: string; active: boolean; canControl: boolean}>();
const job = ref<PdfBookOcrJob | null>(null);
const busy = ref(false);
const error = ref('');
let generation = 0;
let controller: AbortController | null = null;
let timer: ReturnType<typeof setTimeout> | undefined;
const label = computed(() => job.value?.status === 'running' ? '暂停 OCR'
  : job.value?.status === 'paused' ? '继续 OCR'
  : job.value?.status === 'failed' ? '重试未完成页' : '全书 OCR');
const progressLabel = computed(() => {
  const state = job.value;
  if (!state) return '';
  const suffix = state.status === 'paused' ? ' · 已暂停' : state.status === 'failed' ? ` · ${state.failed} 页失败` : state.status === 'completed' ? ' · 完成' : '';
  return `${state.completed} / ${state.total}${suffix}`;
});
function schedule() {
  clearTimeout(timer);
  if (props.active && job.value?.status === 'running') timer = setTimeout(load, 15000);
}
async function load() {
  if (!props.active || busy.value) return;
  const version = ++generation;
  controller?.abort();
  controller = new AbortController();
  try {
    const result = await fetchPdfBookOcrJob(props.pdfId, controller.signal);
    if (version !== generation) return;
    job.value = result;
    error.value = '';
  } catch {
    if (version !== generation) return;
    error.value = '进度暂不可用';
  }
  if (version === generation) schedule();
}
async function control() {
  if (busy.value) return;
  const version = ++generation;
  controller?.abort(); clearTimeout(timer);
  busy.value = true; error.value = '';
  try {
    const action = job.value?.status === 'running' ? 'pause' : job.value?.status === 'paused' ? 'resume' : 'start';
    const result = await controlPdfBookOcrJob(props.pdfId, action);
    if (version === generation) job.value = result;
  } catch (e: any) {
    if (version === generation) error.value = e.response?.data?.detail || '操作失败，请重试';
  } finally {
    if (version === generation) { busy.value = false; schedule(); }
  }
}
watch(() => [props.pdfId, props.revision, props.active] as const, () => {
  generation++; controller?.abort(); clearTimeout(timer);
  job.value = null; busy.value = false; error.value = '';
  void load();
}, {immediate:true});
onBeforeUnmount(() => {generation++; controller?.abort(); clearTimeout(timer);});
</script>

<template>
  <div class="book-ocr-control">
    <button v-if="canControl && job?.status !== 'completed'" type="button" :disabled="busy" @click="control"
      title="后台逐页识别，自动跳过已有结果；暂停会在当前页结束后生效">{{ label }}</button>
    <span v-if="job && job.status !== 'idle'" class="book-ocr-progress">
      <progress :value="job.completed" :max="job.total || 1" aria-label="全书 OCR 进度" />
      <span>{{ progressLabel }}</span>
    </span>
    <span v-if="error" class="book-ocr-error" role="status">{{ error }}</span>
  </div>
</template>

<style scoped>
.book-ocr-control,.book-ocr-progress { display: inline-flex; align-items: center; gap: 8px; flex-wrap: wrap; font-size: 12px; }
.book-ocr-control button { border: 0; background: none; color: #2563eb; cursor: pointer; padding: 4px; font: inherit; }
.book-ocr-control button:disabled { opacity: .5; cursor: wait; }
.book-ocr-progress { color: #64748b; }
progress { width: 88px; height: 6px; accent-color: #3b82f6; }
.book-ocr-error { color: #b45309; }
</style>
