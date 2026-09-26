<script setup lang="ts">
import {nextTick, onBeforeUnmount, onMounted, ref, shallowRef, watch} from 'vue';
import {findPageText, indexPageText, joinHighlightRects} from './pageTextSearch';

const props = defineProps<{surface: {root: HTMLElement} | null; enabled: boolean; request?: {query:string; occurrence:number} | null}>();
const opened = ref(false);
const query = ref('');
const input = ref<HTMLInputElement | null>(null);
const hits = shallowRef<ReturnType<typeof findPageText>>([]);
const active = ref(0);
const rects = shallowRef<Array<Array<{x:number; y:number; width:number; height:number}>>>([]);
let index: ReturnType<typeof indexPageText> = [];
let timer: ReturnType<typeof setTimeout> | undefined;
let requestedOccurrence: number | null = null;

function paint(scroll: boolean) {
  const root = props.surface?.root;
  if (!root || !opened.value || !props.enabled) return;
  const bounds = root.getBoundingClientRect();
  const scroller = root.closest<HTMLElement>('.pdf-page-scroll');
  const target = rects.value[active.value]?.[0];
  if (scroll && scroller && target) {
    const view = scroller.getBoundingClientRect();
    scroller.scrollTo({top: scroller.scrollTop + bounds.top + target.y - view.top - view.height / 3,
      left: scroller.scrollLeft + bounds.left + target.x - view.left - view.width / 3,
      behavior: 'auto'});
  }
}
function search() {
  hits.value = opened.value && props.enabled ? findPageText(index, query.value) : [];
  active.value = Math.min(requestedOccurrence ?? 0, Math.max(0,hits.value.length - 1));
  // Measure all hits once per query/page. Next/previous only changes styling and scroll.
  const bounds = props.surface?.root.getBoundingClientRect();
  rects.value = bounds ? hits.value.map(hit => {
    const range = document.createRange();
    range.setStart(hit.start.node, hit.start.start);
    range.setEnd(hit.end.node, hit.end.end);
    return joinHighlightRects([...range.getClientRects()].filter(r => r.width && r.height)
      .map(r => ({x:r.left-bounds.left, y:r.top-bounds.top, width:r.width, height:r.height})));
  }) : [];
  paint(true);
}
function step(direction: number) {
  requestedOccurrence = null;
  if (timer) { clearTimeout(timer); timer = undefined; search(); }
  if (!hits.value.length) return;
  active.value = (active.value + direction + hits.value.length) % hits.value.length;
  paint(true);
}
async function open() {
  opened.value = true;
  index = props.surface ? indexPageText(props.surface.root) : [];
  search();
  await nextTick();
  input.value?.focus(); input.value?.select();
}
function close() { opened.value = false; hits.value = []; rects.value = []; }
function keyboard(event: KeyboardEvent) {
  if (!props.enabled) return;
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'f') {
    event.preventDefault(); event.stopImmediatePropagation(); void open();
  } else if (opened.value && event.key === 'Escape') {
    event.preventDefault(); event.stopImmediatePropagation(); close();
  }
}
watch(query, () => {
  if (query.value !== props.request?.query) requestedOccurrence = null;
  clearTimeout(timer);
  timer = setTimeout(() => { timer = undefined; search(); }, 120);
});
watch(() => props.request, request => {
  if (!request) return;
  requestedOccurrence = request.occurrence;
  opened.value = true;
  query.value = request.query;
  index = props.surface ? indexPageText(props.surface.root) : [];
  search();
});
watch(() => [props.surface, props.enabled] as const, () => {
  clearTimeout(timer); timer = undefined;
  index = props.surface && opened.value ? indexPageText(props.surface.root) : [];
  search();
});
onMounted(() => window.addEventListener('keydown', keyboard, true));
onBeforeUnmount(() => {clearTimeout(timer); window.removeEventListener('keydown', keyboard, true);});
</script>

<template>
  <div v-if="enabled && opened" class="page-find" role="search" aria-label="查找当前 PDF 页">
    <input ref="input" v-model="query" placeholder="查找当前页" aria-label="查找当前页" @keydown.enter.prevent.stop="step($event.shiftKey ? -1 : 1)" />
    <span class="find-count" aria-live="polite">{{ !surface ? '文字准备中' : `${hits.length ? active + 1 : 0} / ${hits.length}` }}</span>
    <button :disabled="!hits.length" aria-label="上一处" @click="step(-1)">↑</button>
    <button :disabled="!hits.length" aria-label="下一处" @click="step(1)">↓</button>
    <button aria-label="关闭查找" @click="close">×</button>
  </div>
  <Teleport v-if="surface && enabled && opened && rects.length" :to="surface.root.parentElement">
    <svg class="find-paint" aria-hidden="true">
      <g v-for="(boxes, hitIndex) in rects" :key="hitIndex" class="find-hit" :class="{'is-active': hitIndex === active}">
        <rect v-for="(r, i) in boxes" :key="i" :x="r.x" :y="r.y" :width="r.width" :height="r.height" />
      </g>
    </svg>
  </Teleport>
</template>

<style scoped>
.page-find { position: absolute; right: 16px; top: 49px; z-index: 5; display: flex; align-items: center; gap: 8px; padding: 9px 10px; border: 1px solid #dce3ed; border-radius: 8px; background: white; box-shadow: 0 3px 12px #0f172a18; max-width: calc(100% - 32px); box-sizing: border-box; }
.page-find input { width: 160px; min-width: 50px; border: 0; outline: 0; font: inherit; }
.find-count { font-size: 12px; color: #64748b; white-space: nowrap; }
.page-find button { border: 0; background: transparent; padding: 4px; cursor: pointer; color: #475569; font-size: 18px; }
.page-find button:disabled { opacity: .35; cursor: default; }
.find-paint { position: absolute; inset: 0; width: 100%; height: 100%; pointer-events: none; z-index: 2; }
.find-hit { fill: #facc15; opacity: .3; }
.find-hit.is-active { fill: #f97316; opacity: .5; }
</style>
