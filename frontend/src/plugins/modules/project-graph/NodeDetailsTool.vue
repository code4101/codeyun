<script setup lang="ts">
import { computed, ref } from 'vue'
import PlateEditor from '@/components/PlateEditor.vue'
import { writePlateContent, readPlateContent } from '@/components/rich-text/plateDocument'
import { emptyPlateValue } from '@/components/rich-text/plateValue'
const props = defineProps<{ node: { id: string; title: string; value: unknown[] } | null }>()
const emit = defineEmits<{ change: [id: string, value: unknown[]] }>()
// Keep the editor runtime warm across selections. The bridge carries the source
// node ID on each edit, including messages queued before a selection change.
const content = computed(() => writePlateContent(props.node?.value.length ? props.node.value : emptyPlateValue()))
const editor = ref<InstanceType<typeof PlateEditor>>()
defineExpose({ flush: () => editor.value?.flush() })
</script>
<template>
  <div class="node-details">
    <div v-if="node" class="node-title">{{ node.title || '未命名节点' }}</div>
    <PlateEditor v-show="node" ref="editor" :model-value="content" :content-key="node?.id ?? ''" :read-only="!node" @scoped-change="(id, value) => { if (id) emit('change', id, readPlateContent(value)) }" />
    <p v-if="!node">单击一个节点，查看和编辑正文。</p>
  </div>
</template>
<style scoped>
.node-details { display:flex;flex:1;flex-direction:column;min-height:0;font-size:12px; }
.node-title { padding:8px; }
.node-details :deep(.plate-body-editor),.node-details :deep(iframe) { min-height:0; }
</style>
