<template>
  <el-dialog v-model="visible" title="触发时间编排" width="620px" destroy-on-close>
    <p class="sequence-help">
      当作业原始 next_time 恰好并列落在同一时刻时，按这里的顺序生成调度时间：第 1 条不偏移，第 2 条顺延 1 分钟，以此类推。
    </p>
    <div v-loading="loading" class="sequence-groups">
      <el-empty v-if="!loading && !groups.length" description="当前没有并列的触发时间" :image-size="72" />
      <SchedulerTimeSequenceGroup
        v-for="group in groups"
        :key="group.key"
        :group="group"
        @reorder="(oldIndex, newIndex) => reorder(group.key, oldIndex, newIndex)"
      />
    </div>
    <template #footer>
      <el-button @click="visible = false">取消</el-button>
      <el-button type="primary" :loading="saving" :disabled="!groups.length" @click="save">保存编排</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref } from 'vue';
import { ElMessage } from 'element-plus';
import {
  getFanxiuDataAnnotationSchedulerTimeSequence,
  saveFanxiuDataAnnotationSchedulerTimeSequence,
  type FanxiuDataAnnotationSchedulerTimeSequenceGroup,
} from '@/api/fanxiu';
import SchedulerTimeSequenceGroup from './SchedulerTimeSequenceGroup.vue';

const emit = defineEmits<{ saved: [] }>();
const visible = ref(false);
const loading = ref(false);
const saving = ref(false);
const groups = ref<FanxiuDataAnnotationSchedulerTimeSequenceGroup[]>([]);

const open = async () => {
  visible.value = true;
  loading.value = true;
  try {
    groups.value = (await getFanxiuDataAnnotationSchedulerTimeSequence()).groups;
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '读取时间编排失败');
  } finally {
    loading.value = false;
  }
};

const reorder = (key: string, oldIndex: number, newIndex: number) => {
  const group = groups.value.find(item => item.key === key);
  if (!group) return;
  const [moved] = group.items.splice(oldIndex, 1);
  group.items.splice(newIndex, 0, moved);
};

const save = async () => {
  saving.value = true;
  try {
    const response = await saveFanxiuDataAnnotationSchedulerTimeSequence(groups.value.map(group => ({
      key: group.key,
      task_ids: group.items.map(item => item.task_id),
    })));
    groups.value = response.groups;
    ElMessage.success('触发时间编排已保存');
    emit('saved');
    visible.value = false;
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '保存时间编排失败');
  } finally {
    saving.value = false;
  }
};

defineExpose({ open });
</script>

<style scoped>
.sequence-help {
  margin: -4px 0 14px;
  color: var(--el-text-color-secondary);
  font-size: 13px;
  line-height: 1.6;
}

.sequence-groups {
  display: grid;
  gap: 12px;
  min-height: 120px;
  max-height: 56vh;
  overflow: auto;
}
</style>
