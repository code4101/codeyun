<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { exportPreparation, loadPreparation, loadSnapshot, reviewPreparation, type PreparationOverview, type PreparationSnapshot } from '@/api/notesPreparation'

const data = ref<PreparationOverview>({ snapshot: null, packets: [], runs: [] })
const busy = ref(false)
const error = ref('')
const query = ref('')
const filter = ref('all')
const confidenceFilter = ref('candidates')
const selectedId = ref('')
const evidenceSnapshot = ref<PreparationSnapshot | null>(null)
const evidenceError = ref('')
const humanNote = ref('')
const decision = ref('pending')
const saving = ref(false)
const exporting = ref(false)
const labels: Record<string, string> = { pending: '待查看', interested: '有意尝试', deferred: '以后再说', dismissed: '不考虑' }
const confidenceLabels = { explicit: '明确意图', inferred: '概念推断', uncertain: '意图待确认' }
const packets = computed(() => data.value.packets.filter(p => (filter.value === 'all' || p.decision === filter.value) && (confidenceFilter.value === 'all' || (confidenceFilter.value === 'uncertain' ? p.confidence === 'uncertain' : p.confidence !== 'uncertain')) && `${p.title}\n${p.intent}\n${p.research}`.toLowerCase().includes(query.value.toLowerCase())))
const selected = computed(() => data.value.packets.find(p => p.id === selectedId.value))
const sources = computed(() => evidenceSnapshot.value?.sources.filter(s => selected.value?.evidence_ids.includes(s.id)) || [])
const stats = computed(() => data.value.packets.reduce((s, p) => { s[p.decision] = (s[p.decision] || 0) + 1; return s }, {} as Record<string, number>))
const timestamp = (v: number) => new Date(v * 1000).toLocaleString('zh-CN')
async function refresh() {
  if (busy.value) return
  busy.value = true
  try {
    data.value = await loadPreparation()
    error.value = ''
    if (!selectedId.value) selectedId.value = packets.value[0]?.id || ''
  } catch (e) { error.value = e instanceof Error ? e.message : '读取筹备库失败' }
  finally { busy.value = false }
}
watch(() => `${selected.value?.id || ''}:${selected.value?.snapshot_id || ''}`, async (_, previous) => {
  const p = selected.value
  if (!previous || !previous.startsWith(`${p?.id}:`)) {
    humanNote.value = p?.human_note || ''
    decision.value = p?.decision || 'pending'
  }
  evidenceSnapshot.value = null
  evidenceError.value = ''
  if (!p) return
  const id = p.id
  const snapshotId = p.snapshot_id
  try {
    const snapshot = await loadSnapshot(p.snapshot_id, p.evidence_ids)
    if (selected.value?.id === id && selected.value?.snapshot_id === snapshotId) evidenceSnapshot.value = snapshot
  } catch { if (selected.value?.id === id && selected.value?.snapshot_id === snapshotId) evidenceError.value = '原文快照读取失败，请稍后重试' }
})
async function saveReview() {
  if (!selected.value || saving.value) return
  saving.value = true
  try { await reviewPreparation(selected.value.id, decision.value, humanNote.value); await refresh(); ElMessage.success('已保存查看标签') }
  catch { ElMessage.error('标签保存失败') }
  finally { saving.value = false }
}
function download(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a'); link.href = url; link.download = name; link.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
async function exportAll() {
  try { download(await exportPreparation(), 'note-preparation.zip') }
  catch { ElMessage.error('下载失败') }
}
async function exportSelected() {
  const packet = selected.value
  if (!packet || !evidenceSnapshot.value || evidenceError.value || exporting.value) return
  exporting.value = true
  try {
    const snapshots: Record<string, PreparationSnapshot> = {}
    for (const revision of [packet, ...packet.history]) {
      const snapshot = await loadSnapshot(revision.snapshot_id, revision.evidence_ids)
      if (snapshots[revision.snapshot_id]) {
        const existing = snapshots[revision.snapshot_id]!
        existing.sources = [...new Map([...existing.sources, ...snapshot.sources].map(s => [s.id, s])).values()]
      } else snapshots[revision.snapshot_id] = snapshot
    }
    download(new Blob([JSON.stringify({ packet, evidence_snapshots: snapshots }, null, 2)], { type: 'application/json' }), `preparation-${packet.id}.json`)
  } catch { ElMessage.error('证据下载失败，未生成不完整数据包') }
  finally { exporting.value = false }
}
function referenceLink(value: string) { return /^https?:\/\//i.test(value) ? value : undefined }
let timer: ReturnType<typeof setInterval> | undefined
onMounted(() => { void refresh(); timer = setInterval(() => { if (!document.hidden) void refresh() }, 30000) })
onUnmounted(() => clearInterval(timer))
</script>

<template>
  <div class="preparation-page">
    <header>
      <div><h2>待办筹备库</h2><p>从笔记与概念图积累研究材料，等有精力时再挑选尝试。旧笔记中的问题可能已经解决，候选现状仍需核对。</p></div>
      <div class="actions"><el-button :loading="busy" @click="refresh">刷新</el-button><el-button @click="exportAll">下载全部数据包</el-button></div>
    </header>
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <div class="summary"><span>{{ data.packets.length }} 份筹备包</span><span>{{ stats.pending || 0 }} 待查看</span><span>{{ stats.interested || 0 }} 有意尝试</span><span>研究完成也会留在这里等待人工判断</span></div>
    <details class="coverage" v-if="data.snapshot">
      <summary>来源快照 · {{ timestamp(data.snapshot.created_at) }} · {{ data.snapshot.coverage.graphs }} 张图 / {{ data.snapshot.coverage.note_scanned }} 条日记</summary>
      <p>已排除 {{ data.snapshot.coverage.automatic_excluded }} 条已识别的自动导入日记；缺少来源标记的内容保留为“手工或来源待确认”。{{ data.snapshot.coverage.note_scan_complete ? '日记扫描已完成。' : '日记只覆盖本次扫描范围，更多历史内容尚待扫描。' }}</p>
      <p v-for="e in data.snapshot.coverage.errors" :key="e.source">{{ e.source }}：{{ e.error }}</p>
    </details>
    <div class="workspace">
      <aside>
        <el-input v-model="query" placeholder="搜索意图或研究内容" clearable />
        <el-select v-model="filter"><el-option label="全部" value="all" /><el-option v-for="(label, value) in labels" :key="value" :label="label" :value="value" /></el-select>
        <el-select v-model="confidenceFilter"><el-option label="明确意图与概念推断" value="candidates" /><el-option label="意图待确认 / 背景概念" value="uncertain" /><el-option label="全部材料" value="all" /></el-select>
        <button v-for="p in packets" :key="p.id" class="packet" :class="{ active: selectedId === p.id }" @click="selectedId = p.id">
          <strong>{{ p.title }}</strong><span>{{ labels[p.decision] }} · {{ confidenceLabels[p.confidence] }}<template v-if="p.stale"> · 原文已变化</template></span><small>{{ p.intent.slice(0, 120) }}</small>
        </button>
        <el-empty v-if="!packets.length" description="研究材料准备后会出现在这里" />
      </aside>
      <main v-if="selected">
        <div class="packet-heading"><h3>{{ selected.title }}</h3><el-button size="small" :disabled="!evidenceSnapshot || !!evidenceError" :loading="exporting" @click="exportSelected">下载这份包</el-button></div>
        <el-alert v-if="selected.stale" title="原文已变化：这份研究依据旧快照，尝试前请核对最新笔记。" type="warning" :closable="false" />
        <p class="intent">{{ selected.intent }}</p>
        <small>{{ confidenceLabels[selected.confidence] }} · {{ timestamp(selected.created_at) }} · {{ selected.model }}</small>
        <h4>研究材料</h4><pre>{{ selected.research || '已识别候选意图，详细研究尚待补充。' }}</pre>
        <template v-if="selected.dependencies.length"><h4>依赖与前提</h4><ul><li v-for="d in selected.dependencies" :key="d">{{ d }}</li></ul></template>
        <template v-if="selected.questions.length"><h4>以后需要你决定</h4><ul><li v-for="q in selected.questions" :key="q">{{ q }}</li></ul></template>
        <template v-if="selected.references.length"><h4>参考资料</h4><ul><li v-for="r in selected.references" :key="r"><a v-if="referenceLink(r)" :href="r" target="_blank" rel="noopener noreferrer">{{ r }}</a><span v-else>{{ r }}</span></li></ul></template>
        <h4>原文证据</h4><p v-if="evidenceError">{{ evidenceError }}</p>
        <details v-for="s in sources" :key="s.id"><summary>{{ s.title }} · {{ s.kind === 'pg' ? '概念图' : '手工或来源待确认' }}</summary><pre>{{ s.text }}</pre><p v-if="s.context.length">邻接概念：{{ s.context.join(' · ') }}</p><router-link :to="s.url">打开来源</router-link><small class="source-id">{{ s.id }} · 版本 {{ s.revision }}</small></details>
        <details v-if="selected.history.length"><summary>历史研究版本（{{ selected.history.length }}）</summary><div v-for="(h, i) in selected.history" :key="i"><small>{{ timestamp(h.created_at) }}</small><pre>{{ h.research }}</pre></div></details>
        <section class="human-review"><h4>留给以后的自己</h4><el-select v-model="decision"><el-option v-for="(label, value) in labels" :key="value" :label="label" :value="value" /></el-select><el-input v-model="humanNote" type="textarea" :rows="3" placeholder="可暂时留空，有精力时再看" /><el-button :loading="saving" @click="saveReview">保存查看标签</el-button></section>
      </main>
      <main v-else><el-empty description="选择一份筹备包查看原文与研究" /></main>
    </div>
    <details v-if="data.runs.length" class="coverage"><summary>研究运行记录（{{ data.runs.length }}）</summary><p v-for="(r, i) in data.runs.slice().reverse()" :key="i">{{ timestamp(r.recorded_at) }} · {{ r.phase }} · {{ r.status }} {{ r.error || '' }}</p></details>
  </div>
</template>

<style scoped>
.preparation-page { padding: 24px; height: 100%; overflow: auto; color: var(--el-text-color-primary); }
header, .packet-heading { display: flex; justify-content: space-between; align-items: center; gap: 16px; }
h2, h3 { margin: 0 0 8px; } header p, small, .summary { color: var(--el-text-color-secondary); }
.actions, .summary { display: flex; gap: 16px; flex-wrap: wrap; } .summary { margin: 20px 0; }
.coverage { margin: 16px 0; padding: 12px; border: 1px solid var(--el-border-color); border-radius: 8px; }
.workspace { display: grid; grid-template-columns: 310px minmax(0, 1fr); gap: 24px; }
aside { display: flex; flex-direction: column; gap: 10px; } main { min-width: 0; }
.packet { display: flex; flex-direction: column; gap: 8px; padding: 14px; text-align: left; cursor: pointer; color: inherit; background: var(--el-bg-color); border: 1px solid var(--el-border-color); border-radius: 8px; }
.packet.active { border-color: var(--el-color-primary); background: var(--el-color-primary-light-9); }
.packet span { font-size: 12px; } .intent, pre { white-space: pre-wrap; overflow-wrap: anywhere; }
pre { font: inherit; line-height: 1.75; padding: 16px; background: var(--el-fill-color-light); border-radius: 8px; }
details { margin: 12px 0; } summary { cursor: pointer; } .source-id { display: block; margin-top: 8px; }
.human-review { border-top: 1px solid var(--el-border-color); padding-top: 12px; display: grid; gap: 12px; }
@media (max-width: 800px) { .workspace { grid-template-columns: 1fr; } header { align-items: flex-start; flex-direction: column; } }
</style>
