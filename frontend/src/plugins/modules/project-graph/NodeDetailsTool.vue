<script setup lang="ts">
import { computed, ref } from 'vue'
import PlateEditor from '@/components/PlateEditor.vue'
import { writePlateContent, readPlateContent } from '@/components/rich-text/plateDocument'
import type { GraphNodeDetails } from './storage'
import { emptyPlateValue } from '@/components/rich-text/plateValue'
const props = defineProps<{ node: GraphNodeDetails | null; readOnly?: boolean }>()
const emit = defineEmits<{ change: [id: string, value: unknown[]] }>()
// Keep the editor runtime warm across selections. The bridge carries the source
// node ID on each edit, including messages queued before a selection change.
const content = computed(() => writePlateContent(props.node?.value.length ? props.node.value : emptyPlateValue()))
const editor = ref<InstanceType<typeof PlateEditor>>()
defineExpose({ flush: () => editor.value?.flush() })
function timeLabel(value?: number | null) {
  if (!value) return '补齐中'
  return new Date(value).toLocaleString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false })
}
</script>
<template>
  <div class="node-details">
    <dl v-if="node" class="node-times" aria-label="节点时间">
      <div><dt>创建时间</dt><dd><time :datetime="node.createdAt ? new Date(node.createdAt).toISOString() : undefined">{{ timeLabel(node.createdAt) }}</time></dd></div>
      <div><dt>最近编辑时间</dt><dd><time :datetime="node.updatedAt ? new Date(node.updatedAt).toISOString() : undefined">{{ timeLabel(node.updatedAt) }}</time></dd></div>
    </dl>
    <p v-if="node?.lockedBy">{{ node.lockedBy }} 正在编辑此对象</p>
    <PlateEditor v-show="node" ref="editor" :model-value="content" :content-key="node?.id ?? ''" :read-only="!node || readOnly || node.readOnly" @scoped-change="(id, value) => { if (id && !readOnly && !node?.readOnly) emit('change', id, readPlateContent(value)) }" />
    <p v-if="!node">选择一个对象，查看和编辑正文。</p>
  </div>
</template>
<style scoped>
.node-details { display:flex;flex:1;flex-direction:column;min-height:0;font-size:12px; }
.node-details :deep(.plate-body-editor),.node-details :deep(iframe) { min-height:0; }
.node-times{flex:none;margin:0;padding:10px 12px;border-bottom:1px solid var(--reader-border);font-size:11px;color:var(--reader-muted)}
.node-times>div{display:flex;align-items:baseline;justify-content:space-between;gap:8px;margin:3px 0;flex-wrap:wrap}.node-times dt,.node-times dd{margin:0}.node-times dd{color:var(--reader-text);font-variant-numeric:tabular-nums}
</style>
