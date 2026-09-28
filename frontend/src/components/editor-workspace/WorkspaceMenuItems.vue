<script setup lang="ts">
import { ElSubMenu, ElMenuItem } from 'element-plus'
import type { WorkspaceMenuItem } from './workspaceMenu'
const props = defineProps<{ items: WorkspaceMenuItem[]; disabled?: boolean; path?: string[] }>()
defineEmits<{ enter: [path: string[]]; leave: [path: string[]] }>()
</script>
<template>
  <template v-for="item in items" :key="item.id">
    <li v-if="item.separator" class="workspace-menu-separator" role="separator" />
    <ElSubMenu v-else-if="item.children?.length" :index="item.id" :data-menu-path="JSON.stringify([...(props.path ?? []), item.id])" @mouseenter="$emit('enter', [...(props.path ?? []), item.id])" @mouseleave="$emit('leave', [...(props.path ?? []), item.id])" :teleported="false" :show-timeout="0" :hide-timeout="100">
      <template #title>{{ item.label }}</template>
      <WorkspaceMenuItems :items="item.children" :disabled="disabled" :path="[...(props.path ?? []), item.id]" @enter="$emit('enter', $event)" @leave="$emit('leave', $event)" />
    </ElSubMenu>
    <ElMenuItem v-else :index="item.id" :disabled="disabled || item.disabled">{{ item.label }}</ElMenuItem>
  </template>
</template>
