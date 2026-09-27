<script setup lang="ts">
import type { SettingNode } from './settingsTree'
defineProps<{ nodes: readonly SettingNode[] }>()
function updateNumber(node: Extract<SettingNode, { kind: 'number' }>, event: Event) {
  const input = event.target as HTMLInputElement
  const value = input.valueAsNumber
  if (Number.isFinite(value)) node.write(Math.min(node.max, Math.max(node.min, value)))
  input.value = String(node.read())
}
</script>
<template>
  <div class="settings-branch">
    <template v-for="node in nodes" :key="node.id">
      <details v-if="node.kind === 'group'" open>
        <summary>{{ node.label }}</summary>
        <p v-if="node.description" class="description">{{ node.description }}</p>
        <SettingsBranch :nodes="node.children" />
      </details>
      <button v-else-if="node.kind === 'action'" type="button" class="action" @click="node.run()">{{ node.label }}</button>
      <label v-else class="setting">
        <span>{{ node.label }}<small v-if="node.description">{{ node.description }}</small></span>
        <input v-if="node.kind === 'boolean'" type="checkbox" :checked="node.read()" @change="node.write(($event.target as HTMLInputElement).checked)">
        <input v-else-if="node.kind === 'number'" type="number" :min="node.min" :max="node.max" :step="node.step ?? 1" :value="node.read()" @change="updateNumber(node, $event)">
        <select v-else :value="node.read()" @change="node.write(($event.target as HTMLSelectElement).value)">
          <option v-for="option in node.options" :key="option.value" :value="option.value">{{ option.label }}</option>
        </select>
      </label>
    </template>
  </div>
</template>
<style scoped>
.settings-branch { display: flex; flex-direction: column; gap: 8px; min-width: 0; }
details { border-bottom: 1px solid var(--reader-border, #ccc); padding-bottom: 8px; }
summary { cursor: pointer; font-weight: 600; padding: 5px 0; }
details > .settings-branch { padding: 4px 0 0 8px; }
.setting { display: flex; align-items: center; justify-content: space-between; gap: 8px; min-height: 28px; }
.setting > span { min-width: 0; overflow-wrap: anywhere; }
.description, small { display: block; font-size: 11px; opacity: .7; margin: 3px 0; line-height: 1.5; }
input, select, button { font: inherit; color: inherit; accent-color: var(--reader-accent, #397078); }
input[type=number], select, .action { background: var(--reader-content, transparent); border: 1px solid var(--reader-border, #aaa); border-radius: 4px; padding: 4px; }
input[type=number] { width: 64px; }
select { max-width: 105px; }
.action { cursor: pointer; align-self: flex-start; }
</style>
