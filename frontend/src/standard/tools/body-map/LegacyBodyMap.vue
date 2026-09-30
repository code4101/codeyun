<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import BodyModelViewer from './BodyModelViewer.vue'
import { describeMarks, parseMarks, regionAt, storageKey, views, type BodyMark, type BodyView } from './bodyMap'

const viewer = ref<InstanceType<typeof BodyModelViewer>>()
const message = ref('')
const storageError = ref('')
let initial: BodyMark[] = []
try { initial = parseMarks(localStorage.getItem(storageKey) ?? localStorage.getItem('codeyun.body-map.v1')) }
catch { storageError.value = '浏览器存储不可用，请复制标注保留。' }
const marks = ref<BodyMark[]>(initial)
const selectedId = ref(initial[0]?.id ?? '')
const selected = computed(() => marks.value.find(m => m.id === selectedId.value))
const summary = computed(() => describeMarks(marks.value))
const summaryOpen = ref(false)
watch(marks, value => {
  try { localStorage.setItem(storageKey, JSON.stringify(value)); storageError.value = '' }
  catch { storageError.value = '自动保存失败，请复制标注保留。' }
}, { deep: true, immediate: true })
function addMark(point: { view: BodyView; x: number; y: number }) {
  if (marks.value.length >= 100) { message.value = '最多保留 100 个标注，请先删除不需要的标注。'; return }
  const mark: BodyMark = { ...point, id: crypto.randomUUID(), radius: 10, region: regionAt(point.view, point.x, point.y), note: '' }
  marks.value.push(mark); selectedId.value = mark.id; message.value = '已添加标注。'
}
function choose(mark: BodyMark) { selectedId.value = mark.id; viewer.value?.focusMark(mark) }
function remove(id: string) {
  marks.value = marks.value.filter(m => m.id !== id)
  if (selectedId.value === id) selectedId.value = marks.value[marks.value.length - 1]?.id ?? ''
}
async function copy() {
  try { await navigator.clipboard.writeText(summary.value); message.value = '已复制标注。' }
  catch { summaryOpen.value = true; message.value = '请在下方文本框中选中并复制。' }
}
</script>
<template>
  <main class="body-map">
    <header class="page-heading">
      <div><p class="eyebrow">HUMAN BODY / 人体模型</p><h1>人体模型</h1><p>切换视角、缩放查看，自由添加位置标注。</p></div>
      <span class="model-badge">男性 · 体表示意</span>
    </header>
    <div class="body-layout">
      <BodyModelViewer ref="viewer" :marks="marks" :selected-id="selectedId" @add="addMark" @select="selectedId = $event" @message="message = $event" />
      <aside class="records" aria-label="位置标注">
        <div class="records-title"><h2>我的标注 <span>{{ marks.length }}</span></h2><button :disabled="!marks.length" @click="copy">复制标注</button></div>
        <div v-if="!marks.length" class="empty-state"><div class="empty-symbol">◎</div><h3>查看与标注</h3><p>切换视角或拖动、缩放查看人体。需要记录位置时，选择「点选标注」。</p><p>标注支持调整范围和自由填写说明。</p></div>
        <div v-else class="mark-list">
          <button v-for="(mark, index) in marks" :key="mark.id" class="mark-item" :class="{ selected: selectedId === mark.id }" @click="choose(mark)"><span class="mark-number">{{ index + 1 }}</span><span><strong>{{ mark.region }}</strong><small>{{ views.find(v => v.id === mark.view)?.label }} · {{ mark.note || '暂无说明' }}</small></span></button>
        </div>
        <form v-if="selected" class="mark-editor" @submit.prevent>
          <label>位置名称 <small>自动提示，可修改</small><input v-model="selected.region" maxlength="100" aria-label="位置名称" /></label>
          <label>标注范围<input v-model.number="selected.radius" type="range" min="3" max="35" aria-label="标注范围" /><span class="range-labels"><small>一个点</small><small>一大片</small></span></label>
          <label>补充说明<textarea v-model="selected.note" rows="3" maxlength="2000" aria-label="补充说明" placeholder="填写与这个位置有关的说明……"></textarea></label>
          <button class="delete-button" type="button" @click="remove(selected.id)">删除这处标注</button>
        </form>
        <p class="save-note">{{ storageError || '标注自动保存在当前浏览器，可复制文字或下载当前视角图片。' }}</p>
        <p v-if="message" class="status-message" role="status">{{ message }}</p>
        <button v-if="marks.length" class="text-button" @click="summaryOpen = !summaryOpen">{{ summaryOpen ? '收起文字标注' : '查看文字标注' }}</button>
        <textarea v-if="summaryOpen" class="summary" :value="summary" readonly rows="7" aria-label="文字标注" @focus="($event.target as HTMLTextAreaElement).select()"></textarea>
      </aside>
    </div>
  </main>
</template>

<style scoped>
.body-map{--ink:#273a38;--muted:#74817b;--line:#e1e6df;--green:#28695c;color:var(--ink);background:#f4f5f1;min-height:100%;padding:28px 32px;box-sizing:border-box;font-family:inherit}
.page-heading{max-width:1320px;margin:0 auto 24px;display:flex;align-items:center;justify-content:space-between;gap:16px}
.eyebrow{font-size:11px;letter-spacing:2px;color:var(--green);font-weight:650;margin:0 0 9px}
h1{font-size:29px;margin:0;font-weight:600;letter-spacing:1px}
.page-heading p:last-child{font-size:13px;color:var(--muted);margin:9px 0 0}
.model-badge{font-size:12px;border:1px solid #cfd9d1;padding:8px 13px;border-radius:22px;white-space:nowrap}
.body-layout{max-width:1320px;margin:auto;display:grid;grid-template-columns:minmax(0,1fr) 340px;gap:20px;align-items:start}
.records{background:white;border:1px solid var(--line);border-radius:16px;overflow:hidden}
button,input,select,textarea{font:inherit}
button{cursor:pointer;border:1px solid var(--line);border-radius:7px;background:white;color:var(--ink);font-size:12px;padding:8px 11px}
button:hover{background:#f0f5ef;border-color:#b7c6bd}
button:focus-visible,input:focus-visible,select:focus-visible,textarea:focus-visible{outline:2px solid var(--green);outline-offset:2px}
button:disabled{opacity:.4;cursor:default}
.records{padding:20px;box-sizing:border-box}
.records-title{display:flex;align-items:center;justify-content:space-between;gap:8px}
.records-title h2{font-size:16px;margin:0}
.records-title h2 span{font-weight:400;color:var(--muted);margin-left:4px}
.records-title button{color:var(--green);font-size:11px;padding:8px}
.empty-state{padding:36px 8px;color:var(--muted);font-size:13px;line-height:1.8}
.empty-state h3{color:var(--ink);font-weight:500;font-size:16px}
.empty-symbol{font-size:42px;color:#b5c8b9}
.mark-list{display:grid;gap:6px;margin-top:17px;max-height:215px;overflow:auto}
.mark-item{display:flex;text-align:left;gap:10px;align-items:center;padding:10px}
.mark-item.selected{border-color:#b1cbb8;background:#f0f5ef}
.mark-number{background:#b74830;color:white;border-radius:50%;width:22px;height:22px;display:grid;place-items:center;flex-shrink:0}
.mark-item strong{font-weight:500;display:block}
.mark-item small{display:block;color:var(--muted);margin-top:4px}
.mark-editor{margin-top:20px;display:grid;gap:15px}
.mark-editor label{display:block;font-size:12px}
.mark-editor label>small{font-size:10px;color:var(--muted);margin-left:5px}
input:not([type=range]),select,textarea{display:block;box-sizing:border-box;width:100%;padding:9px 10px;margin-top:7px;border:1px solid var(--line);border-radius:7px;color:var(--ink);background:#fff;font-size:12px}
textarea{resize:vertical;line-height:1.6}
input[type=range]{width:100%;margin:12px 0 2px;accent-color:var(--green)}
.range-labels{display:flex;justify-content:space-between;color:var(--muted)}
.delete-button{justify-self:start;border:0;color:#ac513d;padding:3px 0}
.save-note{font-size:11px;line-height:1.8;color:var(--muted);margin:18px 0 0}
.status-message{font-size:12px;line-height:1.6;color:var(--green)}
.text-button{border:0;padding:10px 0 0;color:var(--green);font-size:11px}
.summary{font-size:11px}
@media(max-width:1000px){.body-map{padding:20px 16px}.body-layout{grid-template-columns:minmax(0,1fr) 300px;gap:12px}.records{padding:16px}}
@media(max-width:720px){.body-map{padding:16px 10px}.page-heading{margin-bottom:18px}h1{font-size:24px}.model-badge{padding:6px 9px;font-size:10px}.body-layout{grid-template-columns:1fr}.records{padding:18px}.empty-state{padding:15px 0}.empty-symbol{display:none}}
</style>
