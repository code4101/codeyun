<script setup lang="ts">
defineOptions({ name: 'WorkbenchShell' })
</script>
<template>
  <div class="workbench-shell">
    <header v-if="$slots.top" class="shell-chrome"><slot name="top" /></header>
    <div class="shell-body">
      <slot name="left" />
      <main class="shell-center"><slot /></main>
      <slot name="right" />
    </div>
    <footer v-if="$slots.bottom" class="shell-chrome"><slot name="bottom" /></footer>
  </div>
</template>
<style scoped>
/* Shell 分配有限空间；只有 Pane 可以滚动。嵌套 Shell 继承父级高度。 */
.workbench-shell { display: flex; flex-direction: column; width: 100%; height: 100%; min-width: 0; min-height: 0; overflow: hidden; }
.shell-body { display: flex; flex: 1; min-width: 0; min-height: 0; overflow: hidden; }
.shell-center { display: flex; flex-direction: column; flex: 1; min-width: 0; min-height: 0; overflow: hidden; }
.shell-chrome { flex: none; min-width: 0; }
</style>
