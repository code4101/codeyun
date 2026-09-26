<script setup lang="ts">
import {onBeforeUnmount, ref, watch} from 'vue';
import api from '@/api';
export interface SearchScope {title:string; start:number; end:number}
const props = defineProps<{pdfId:number; scope:SearchScope | null}>();
const emit = defineEmits<{navigate:[hit:{page:number; occurrence:number; query:string}]; clearScope:[]}>();
const query = ref('');
const hits = ref<Array<{page:number; occurrence:number; snippet:string}>>([]);
const total = ref(0), indexed = ref(0), pages = ref(0), offset = ref(0);
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
      params:{q:query.value,start:props.scope?.start || 1,end:props.scope?.end,offset:offset.value}});
    if (current !== version) return;
    hits.value = data.hits; total.value = data.total; indexed.value = data.indexed_pages; pages.value = data.scope_pages;
  } catch(e:any) {
    if (current === version) error.value = e.response?.data?.detail || '搜索暂不可用，请重试';
  } finally {if (current === version) busy.value = false;}
}
function changePage(delta:number) { offset.value += delta; void search(); }
watch(() => [query.value, props.pdfId, props.scope] as const, () => {
  version++; controller?.abort(); clearTimeout(timer); offset.value = 0; hits.value = []; total.value = 0;
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
    <small>已识别 {{ indexed }} / {{ pages }} 页<span v-if="indexed < pages">；其余页尚未纳入搜索</span></small>
    <p v-if="error" role="alert">{{ error }} <button @click="search">重试</button></p>
    <p v-else-if="busy">搜索中…</p>
    <p v-else-if="query.trim()">{{ total ? `${total} 处匹配` : '已识别页面中没有匹配' }}</p>
    <button v-for="hit in hits" :key="`${hit.page}:${hit.occurrence}`" class="search-result" :disabled="busy"
      @click="emit('navigate', {...hit,query})"><b>第 {{ hit.page }} 页</b><span>{{ hit.snippet }}</span></button>
    <div v-if="total > 50" class="search-pages"><button :disabled="busy || !offset" @click="changePage(-50)">上一组</button><span>{{ offset + 1 }}–{{ Math.min(offset + 50,total) }}</span><button :disabled="busy || offset + 50 >= total" @click="changePage(50)">下一组</button></div>
  </div>
</template>
<style scoped>
.book-search { padding: 12px 14px; font-size: 13px; display: flex; flex-direction: column; gap: 12px; }
.search-scope,.search-pages { display:flex; gap:8px; justify-content:space-between; align-items:center; }
.book-search input { box-sizing:border-box; width:100%; padding:8px 10px; border:1px solid #dce3ed; border-radius:6px; font:inherit; }
.book-search small { color:#64748b; line-height:1.6; }
.book-search button { border:0; background:transparent; color:#2563eb; cursor:pointer; font:inherit; }
.book-search button:disabled { opacity:.5; cursor:default; }
.book-search .search-result { display:flex; flex-direction:column; gap:6px; text-align:left; padding:10px; background:#f8fafc; border-radius:6px; color:#334155; line-height:1.6; overflow-wrap:anywhere; }
.search-result b { font-weight:500; color:#2563eb; }
.search-result:hover { background:#eff6ff; }
</style>
