<script setup lang="ts">
/** auto: 文档/列表自身滚动；hidden: 将滚动交给内部编辑器或子 Pane。 */
withDefaults(defineProps<{ scroll?: 'auto' | 'hidden' }>(), { scroll: 'auto' })
</script>
<template>
  <section class="workbench-pane">
    <header v-if="$slots.header" class="pane-chrome"><slot name="header" /></header>
    <div class="pane-body" :class="{ 'is-document': scroll === 'auto' }" :style="{ overflow: scroll }"><slot /></div>
    <footer v-if="$slots.footer" class="pane-chrome"><slot name="footer" /></footer>
  </section>
</template>
<style scoped>
.workbench-pane { display: flex; flex-direction: column; flex: 1; box-sizing: border-box; min-width: 0; min-height: 0; overflow: hidden; }
.pane-body { display: flex; flex-direction: column; flex: 1; min-width: 0; min-height: 0; overscroll-behavior: contain; }
/* 文档根节点可超过 Pane 高度；编辑器根节点自行设置 flex/min-height。 */
.pane-body > :slotted(*) { min-width: 0; }
.is-document > :slotted(*) { flex-shrink: 0; }
.pane-chrome { flex: none; min-width: 0; }
</style>
