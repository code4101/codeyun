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
  <section class="book-ocr-control" aria-label="全书 OCR 任务">
    <p class="book-ocr-description">后台逐页识别，自动跳过已有结果。关闭此面板后任务仍会继续。</p>
    <button v-if="canControl && job?.status !== 'completed'" type="button" :disabled="busy || !job" @click="control"
      title="后台逐页识别，自动跳过已有结果；暂停会在当前页结束后生效">{{ label }}</button>
    <span v-if="job && job.status !== 'idle'" class="book-ocr-progress">
      <progress :value="job.completed" :max="job.total || 1" aria-label="全书 OCR 进度" />
      <span>{{ progressLabel }}</span>
    </span>
    <span v-if="error" class="book-ocr-error" role="status">{{ error }}</span>
    <span v-else-if="!job" role="status">正在读取进度…</span>
  </section>
</template>

<style scoped>
.book-ocr-control { display: flex; flex-direction: column; gap: 16px; padding: 14px; border: 1px solid var(--reader-border, #e4e9ef); border-radius: 6px; background: var(--reader-content, #fff); color: var(--reader-text, #302b25); font-size: 13px; }
.book-ocr-description { margin: 0; line-height: 1.7; color: var(--reader-muted, #64748b); }
.book-ocr-control button { align-self: flex-start; border: 1px solid var(--reader-border, #e4e9ef); border-radius: 4px; background: var(--reader-panel, #f7f9fb); color: var(--reader-link, #2563eb); cursor: pointer; padding: 6px 12px; font: inherit; }
.book-ocr-control button:disabled { opacity: .5; cursor: wait; }
.book-ocr-progress { display: flex; flex-direction: column; gap: 8px; color: var(--reader-muted, #64748b); }
progress { width: 100%; height: 6px; accent-color: var(--reader-link, #3b82f6); }
.book-ocr-error { color: #b45309; }
</style>
