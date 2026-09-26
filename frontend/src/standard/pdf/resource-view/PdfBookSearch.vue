<script setup lang="ts">
import {computed, onBeforeUnmount, ref, watch} from 'vue';
import api from '@/api';
export interface SearchScope {title:string; start:number; end:number}
const props = defineProps<{pdfId:number; scope:SearchScope | null}>();
const emit = defineEmits<{navigate:[hit:{page:number; occurrence:number; query:string}]; clearScope:[]}>();
const query = ref('');
const visibleCounts = ref<Record<number, number>>({});
const pageSummaries = ref<Array<{page:number; count:number; snippet:string}>>([]);
const openPages = ref<Record<number, boolean>>({});
const pageBusy = ref<Record<number, boolean>>({});
const pageErrors = ref<Record<number, string>>({});
async function expandPage(page:number) {
  visibleCounts.value[page] = (visibleCounts.value[page] ?? 5) + 5;
  await loadPage(page);
}
async function togglePage(page:number) {
  openPages.value[page] = !openPages.value[page];
  if (openPages.value[page]) await loadPage(page);
}
async function loadPage(page:number) {
  if (pageBusy.value[page]) return;
  const current = version;
  pageBusy.value[page] = true; pageErrors.value[page] = '';
  try {
    // Fetch bounded batches until enough distinct contexts are available.
    // One page expansion never loads the other pages' snippets.
    while (current === version && openPages.value[page]) {
      const loaded = hits.value.filter(hit => hit.page === page).length;
      const summary = pageSummaries.value.find(item => item.page === page);
      const group = groupedHits.value.find(item => item.page === page);
      if (!summary || loaded >= summary.count || (group?.matches.length ?? 0) > (visibleCounts.value[page] ?? 5)) break;
      const {data} = await api.get(`/pdf-documents/${props.pdfId}/search`, {signal:controller?.signal,
        params:{q:query.value,start:page,end:page,offset:loaded}});
      if (current !== version) return;
      if (!data.hits.length) break;
      hits.value.push(...data.hits);
    }
  } catch { if (current === version) pageErrors.value[page] = '加载失败，点击重试'; }
  finally {if (current === version) pageBusy.value[page] = false;}
}
const hits = ref<Array<{page:number; occurrence:number; snippet:string; block?:number; start?:number; end?:number}>>([]);
const groupedHits = computed(() => {
  const groups = new Map<number, typeof hits.value>();
  for (const hit of hits.value) {
    const group = groups.get(hit.page);
    const previous = group?.[group.length - 1];
    if (previous && hit.block !== undefined && previous.block === hit.block
      && hit.start !== undefined && hit.end !== undefined && previous.end !== undefined
      && hit.start <= previous.end) {
      // Merge only overlapping intervals in the same source paragraph.
      previous.snippet += hit.snippet.slice(Math.max(0, previous.end - hit.start));
      previous.end = Math.max(previous.end, hit.end);
    } else if (previous && hit.block === undefined && previous.snippet === hit.snippet) {
      // Compatibility with responses from an older server during a rolling reload.
      continue;
    } else if (group) group.push({...hit});
    else groups.set(hit.page, [{...hit}]);
  }
  return pageSummaries.value.map(({page,count,snippet}) => ({page, count, snippet, matches:groups.get(page) || [], loaded:hits.value.filter(hit => hit.page === page).length}));
});
// Snippets use the server's lowercase, whitespace-free search representation.
// Render text nodes rather than HTML so OCR content is never interpreted as markup.
function highlightSnippet(text:string) {
  const term = query.value.toLowerCase().replace(/\s/g, '');
  if (!term) return [{text, matched:false}];
  const parts:Array<{text:string; matched:boolean}> = [];
  const normalized = text.toLowerCase();
  let cursor = 0;
  while (cursor < text.length) {
    const index = normalized.indexOf(term, cursor);
    if (index < 0) { parts.push({text:text.slice(cursor),matched:false}); break; }
    if (index > cursor) parts.push({text:text.slice(cursor,index),matched:false});
    parts.push({text:text.slice(index,index + term.length),matched:true});
    cursor = index + term.length;
  }
  return parts;
}
const total = ref(0);
const busy = ref(false), error = ref('');
let timer: ReturnType<typeof setTimeout> | undefined;
let controller: AbortController | null = null;
let version = 0;
async function search() {
  const current = ++version;
  controller?.abort(); controller = new AbortController();
  busy.value = true; error.value = '';
  try {
    const {data} = await api.get(`/pdf-documents/${props.pdfId}/search`, {signal:controller.signal,
      params:{q:query.value,start:props.scope?.start || 1,end:props.scope?.end}});
    if (current !== version) return;
    hits.value = data.hits; total.value = data.total;
    pageSummaries.value = data.pages || [];
    // Few pages are immediately readable; large result sets start as compact rows.
    if (data.total <= 20 && pageSummaries.value.length <= 3) {
      for (const item of pageSummaries.value) openPages.value[item.page] = true;
      for (const item of pageSummaries.value) {
        if (current !== version) break;
        await loadPage(item.page);
      }
    }
  } catch(e:any) {
    if (current === version) error.value = e.response?.data?.detail || '搜索暂不可用，请重试';
  } finally {if (current === version) busy.value = false;}
}
watch(() => [query.value, props.pdfId, props.scope] as const, () => {
  version++; controller?.abort(); clearTimeout(timer); visibleCounts.value = {}; pageSummaries.value = []; openPages.value = {}; pageBusy.value = {}; pageErrors.value = {}; hits.value = []; total.value = 0;
  busy.value = true;
  timer = setTimeout(search, 300);
}, {immediate:true});
onBeforeUnmount(() => {version++; clearTimeout(timer); controller?.abort();});
</script>
<template>
  <div class="book-search">
    <div class="search-scope"><span>{{ scope ? `该节：${scope.title}` : '全书' }}</span><button v-if="scope" @click="emit('clearScope')">切换全书</button></div>
    <small v-if="scope">第 {{ scope.start }}–{{ scope.end }} 页</small>
    <input v-model="query" type="search" maxlength="120" placeholder="输入关键词" aria-label="搜索书中文字" />
    <p v-if="error" role="alert">{{ error }} <button @click="search">重试</button></p>
    <div v-else-if="busy" class="search-progress" role="status"><span>搜索中…</span><progress aria-label="搜索进度"></progress></div>
    <p v-else-if="query.trim()">{{ total ? `${total} 处匹配` : '已识别页面中没有匹配' }}</p>
    <section v-for="group in groupedHits" :key="group.page" class="search-result">
      <div class="page-heading">
        <button class="page-jump" @click="emit('navigate', {page:group.page, occurrence:0, query})">
          <b>第 {{ group.page }} 页</b>
          <span class="page-preview" :title="group.snippet"><template v-for="(part, index) in highlightSnippet(group.snippet || '')" :key="index"><mark v-if="part.matched">{{ part.text }}</mark><template v-else>{{ part.text }}</template></template></span>
        </button>
        <button class="page-expand" :aria-expanded="!!openPages[group.page]" :aria-label="`${openPages[group.page] ? '折叠' : '展开'}第 ${group.page} 页匹配`" @click="togglePage(group.page)">{{ group.count }} 处 {{ openPages[group.page] ? '⌄' : '›' }}</button>
      </div>
      <template v-if="openPages[group.page]">
      <button v-for="hit in group.matches.slice(0, visibleCounts[group.page] ?? 5)" :key="hit.occurrence" :disabled="busy" class="search-match"
        @click="emit('navigate', {...hit,query})"><template v-for="(part, index) in highlightSnippet(hit.snippet)" :key="index"><mark v-if="part.matched">{{ part.text }}</mark><template v-else>{{ part.text }}</template></template></button>
      <button v-if="group.matches.length > (visibleCounts[group.page] ?? 5) || group.loaded < group.count" class="expand-results"
        :disabled="pageBusy[group.page]" :aria-label="`展开第 ${group.page} 页的更多匹配结果`" @click="expandPage(group.page)">{{ pageBusy[group.page] ? '加载中…' : '...' }}</button>
      <button v-if="pageErrors[group.page]" @click="loadPage(group.page)">{{ pageErrors[group.page] }}</button>
      </template>
    </section>
  </div>
</template>
<style scoped>
.book-search { padding: 12px 14px; font-size: 13px; display: flex; flex-direction: column; gap: 12px; }
.search-scope,.page-heading { display:flex; gap:8px; justify-content:space-between; align-items:center; }
.book-search input { box-sizing:border-box; width:100%; padding:8px 10px; border:1px solid #dce3ed; border-radius:6px; font:inherit; }
.book-search small { color:#64748b; line-height:1.6; }
.book-search button { border:0; background:transparent; color:#2563eb; cursor:pointer; font:inherit; }
.book-search button:disabled { opacity:.5; cursor:default; }
.book-search .search-result { display:flex; flex-direction:column; gap:6px; text-align:left; padding:10px; background:#f8fafc; border-radius:6px; color:#334155; line-height:1.6; overflow-wrap:anywhere; }
.search-result b { font-weight:500; color:#2563eb; }
.book-search .search-match { color:inherit; text-align:left; line-height:inherit; padding:6px 0; border-radius:3px; }
.search-match:hover { background:#eff6ff; }
.search-match + .search-match { border-top:1px solid #edf0f4; }
.search-match mark, .page-preview mark { background:#ffebb0; color:inherit; padding:0; border-radius:2px; }
.book-search .expand-results { align-self:center; padding:2px 16px; font-size:18px; line-height:1.2; }
.page-heading { width:100%; text-align:left; padding:0; }
.book-search .page-jump { display:flex; gap:8px; min-width:0; flex:1; align-items:center; padding:0; text-align:left; }
.page-jump b { flex-shrink:0; font-weight:500; }
.page-preview { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; color:#64748b; min-width:0; }
.book-search .page-expand { flex-shrink:0; padding:4px 0 4px 4px; color:#94a3b8; font-size:12px; }

.search-progress { display:flex; flex-direction:column; gap:8px; padding:8px 0; color:#64748b; }
.search-progress progress { display:block; width:100%; height:4px; border:0; border-radius:2px; overflow:hidden; accent-color:#409eff; }
.search-progress progress::-webkit-progress-bar { background:#eaf1fb; border-radius:2px; }
.search-progress progress:indeterminate { background:linear-gradient(90deg,#eaf1fb 0%,#eaf1fb 25%,#409eff 50%,#eaf1fb 75%,#eaf1fb 100%); background-size:200% 100%; animation:search-progress 1.3s linear infinite; }
.search-progress progress:indeterminate::-webkit-progress-bar { background:transparent; }
@keyframes search-progress { from { background-position:100% 0; } to { background-position:-100% 0; } }
@media (prefers-reduced-motion:reduce) { .search-progress progress:indeterminate { animation:none; } }
</style>
