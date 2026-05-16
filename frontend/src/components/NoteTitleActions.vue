<template>
  <div class="note-title-actions">
    <a
      v-if="showDocLink && docHref"
      class="note-title-action-link"
      :href="docHref"
      target="_blank"
      rel="noopener noreferrer"
      title="打开文档"
      aria-label="打开文档"
      @click.stop
    >
      <el-icon><Document /></el-icon>
    </a>

    <el-button
      v-if="showShare"
      type="primary"
      plain
      text
      circle
      :icon="Share"
      title="共享权限"
      :disabled="!canShare"
      @click.stop="emit('share')"
    />

    <el-button
      v-if="showCopy"
      type="primary"
      plain
      text
      circle
      :icon="CopyDocument"
      title="复制节点"
      :disabled="!canCopy"
      @click.stop="emit('copy')"
    />

    <el-dropdown
      v-if="showDelete"
      trigger="click"
      @command="handleMoreCommand"
    >
      <el-button
        plain
        text
        circle
        :icon="MoreFilled"
        title="更多操作"
        aria-label="更多操作"
        :disabled="!canDelete"
        @click.stop
      />
      <template #dropdown>
        <el-dropdown-menu>
          <el-dropdown-item command="delete" :icon="Delete" :disabled="!canDelete">
            删除节点
          </el-dropdown-item>
        </el-dropdown-menu>
      </template>
    </el-dropdown>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import { CopyDocument, Delete, Document, MoreFilled, Share } from '@element-plus/icons-vue';

const props = withDefaults(defineProps<{
  readonly?: boolean;
  docHref?: string;
  showDocLink?: boolean;
  showShare?: boolean;
  canShare?: boolean;
  showCopy?: boolean;
  canCopy?: boolean;
  showDelete?: boolean;
  canDelete?: boolean;
}>(), {
  showDocLink: true,
  showShare: true,
  canShare: true,
  showCopy: true,
  canCopy: true,
  showDelete: true,
  canDelete: true,
});

const emit = defineEmits<{
  (e: 'share'): void;
  (e: 'copy'): void;
  (e: 'delete'): void;
}>();

const canShare = computed(() => props.canShare);
const canCopy = computed(() => !props.readonly && props.canCopy);
const canDelete = computed(() => !props.readonly && props.canDelete);

const handleMoreCommand = (command: string) => {
  if (command === 'delete' && canDelete.value) {
    emit('delete');
  }
};
</script>

<style scoped>
.note-title-actions {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  flex: 0 0 auto;
}

.note-title-action-link {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border-radius: 50%;
  color: #409eff;
  text-decoration: none;
  vertical-align: middle;
}

.note-title-action-link:hover {
  background: #ecf5ff;
}
</style>
