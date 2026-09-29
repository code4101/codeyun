<script setup lang="ts">
import { ElSubMenu, ElMenuItem, ElTooltip } from 'element-plus'
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
    <ElMenuItem v-else :index="item.id" :disabled="disabled || item.disabled" :aria-disabled="!!(disabled || item.disabled)" :class="{ 'workspace-menu-unavailable': item.disabledReason }">
      {{ item.label }}
      <ElTooltip v-if="item.disabledReason" :content="item.disabledReason" placement="right" :show-after="200">
        <span class="workspace-menu-reason" tabindex="0" role="img" :aria-label="`${item.label}：${item.disabledReason}`" @click.stop @keydown.enter.stop.prevent @keydown.space.stop.prevent>!</span>
      </ElTooltip>
    </ElMenuItem>
  </template>
</template>
<style scoped>
.workspace-menu-reason{display:inline-flex;align-items:center;justify-content:center;flex:0 0 14px;width:14px;height:14px;margin-left:12px;border:1px solid currentColor;border-radius:50%;font-size:10px;line-height:1;pointer-events:auto;cursor:help}
.workspace-menu-reason:focus-visible{outline:2px solid currentColor;outline-offset:3px}
.workspace-menu-unavailable.is-disabled{opacity:.65}
</style>
