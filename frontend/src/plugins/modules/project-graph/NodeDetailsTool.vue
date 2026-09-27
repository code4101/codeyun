<script setup lang="ts">
import { ref } from 'vue'
import PlateEditor from '@/components/PlateEditor.vue'
import { writePlateContent, readPlateContent } from '@/components/rich-text/plateDocument'
const props = defineProps<{ node: { id: string; title: string; value: unknown[] } }>()
const emit = defineEmits<{ change: [id: string, value: unknown[]] }>()
// The parent keys this component by document and node; queued edits retain that identity.
const nodeId = props.node.id
const editor = ref<InstanceType<typeof PlateEditor>>()
defineExpose({ flush: () => editor.value?.flush() })
</script>
<template>
  <div class="node-details">
    <div class="node-title">{{ node.title || '未命名节点' }}</div>
    <PlateEditor ref="editor" :model-value="writePlateContent(node.value)" @change="emit('change', nodeId, readPlateContent($event))" />
  </div>
</template>
<style scoped>
.node-details { display:flex;flex:1;flex-direction:column;min-height:0;font-size:12px; }
.node-title { padding:8px; }
.node-details :deep(.plate-body-editor),.node-details :deep(iframe) { min-height:0; }
</style>
