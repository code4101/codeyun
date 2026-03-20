<template>
  <el-dialog
    v-model="visible"
    title="节点维度说明"
    width="600px"
    class="node-help-dialog"
  >
    <div class="help-content">
      <el-tabs v-model="activeTab">
        <el-tab-pane label="分类" name="category">
          <p class="section-intro">分类提供节点的<b>颜色主题</b>；多个分类会按各自权重自动混合。</p>
          <div class="legend-grid">
            <div class="legend-header">
              <span>样式示例</span>
              <span>名称</span>
              <span>含义说明</span>
            </div>
            <div v-for="type in orderedTypes" :key="type.id" class="legend-row">
              <div class="legend-sample">
                <div class="sample-box" :style="getTypeStyle(type)">
                  {{ type.label }}
                </div>
              </div>
              <div class="legend-name">
                <div class="zh">{{ type.label }}</div>
              </div>
              <div class="legend-desc">{{ type.description }}</div>
            </div>
          </div>
        </el-tab-pane>

        <el-tab-pane label="形态" name="form">
          <p class="section-intro">形态提供节点的<b>内容载体</b>语义；笔记默认不加图标，其它形态会显示各自的专属图标。</p>
          <div class="legend-grid">
            <div class="legend-header">
              <span>样式示例</span>
              <span>名称</span>
              <span>使用场景</span>
            </div>
            <div v-for="form in orderedForms" :key="form.id" class="legend-row">
              <div class="legend-sample">
                <div class="form-sample">
                  <NoteFormBadge :form="form.id" :show-label="true" />
                </div>
              </div>
              <div class="legend-name">
                <div class="zh">{{ form.label }}</div>
              </div>
              <div class="legend-desc">{{ getFormRule(form) }}</div>
            </div>
          </div>
        </el-tab-pane>

        <el-tab-pane label="阶段" name="stage">
          <p class="section-intro">阶段决定了节点的<b>边框线型</b>和<b>背景填充</b>。</p>
          <div class="legend-grid status-grid">
            <div class="legend-header">
              <span>样式示例</span>
              <span>名称</span>
              <span>样式规则</span>
            </div>
            <div v-for="status in orderedStatuses" :key="status.id" class="legend-row">
              <div class="legend-sample">
                <div class="sample-box" :style="getStatusStyle(status)">
                  {{ status.label }}
                </div>
              </div>
              <div class="legend-name">
                <div class="zh">{{ status.label }}</div>
              </div>
              <div class="legend-desc">{{ getStatusRule(status) }}</div>
            </div>
          </div>
        </el-tab-pane>
      </el-tabs>
    </div>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue';
import {
  getOrderedNodeTypes,
  getOrderedNodeStatuses,
  getOrderedNoteForms,
  type NodeTypeItem,
  type NoteFormItem,
  type NodeStatusItem,
  getNodeStyle 
} from '@/utils/nodeConfig';
import NoteFormBadge from './NoteFormBadge.vue';

const props = defineProps<{
  modelValue: boolean;
}>();

const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void;
}>();

const visible = computed({
  get: () => props.modelValue,
  set: (val) => emit('update:modelValue', val)
});

const activeTab = ref('category');
const orderedTypes = computed(() => getOrderedNodeTypes());
const orderedForms = computed(() => getOrderedNoteForms());
const orderedStatuses = computed(() => getOrderedNodeStatuses());

// Helper for category preview
const getTypeStyle = (type: NodeTypeItem) => {
  const style = getNodeStyle(type.id, 'idea');
  return {
    borderColor: style.borderColor,
    color: style.color,
    borderWidth: style.borderWidth,
    borderStyle: style.borderStyle,
    backgroundColor: style.backgroundColor,
    fontSize: '12px',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    height: '28px',
    borderRadius: '4px',
    width: '100%'
  };
};

// Helper for stage preview (use Task blue as base color context)
const getStatusStyle = (status: NodeStatusItem) => {
  const style = getNodeStyle('task', status.id);
  return {
    ...style,
    fontSize: '12px',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    height: '28px',
    borderRadius: '4px',
    width: '100%'
  };
};

const getFormRule = (form: NoteFormItem) => {
  const map: Record<string, string> = {
    note: '默认通用形态，标题旁不额外显示图标。',
    document: '适合正文排版和长内容，会显示文档图标。',
    memo: '适合短平快记录和轻量便签，会显示备忘图标。',
    music: '适合音乐作品、专辑和音频素材，会显示音乐图标。',
    video: '适合电影、剧集和视频资料，会显示影视图标。',
    book: '适合书籍、电子书和长篇阅读内容，会显示书籍图标。'
  };
  return map[form.id] || form.description;
};

const getStatusRule = (status: NodeStatusItem) => {
  const map: Record<string, string> = {
    idea: '实线超浅灰边框，无填充',
    todo: '虚线深灰边框，无填充',
    doing: '实线黑色边框，无填充',
    predone: '虚线分类色边框，分类色浅色填充',
    done: '实线分类色边框，分类色浅色填充',
    delete: '实线超浅灰边框，文字删除线，半透明'
  };
  return map[status.id] || status.description;
};
</script>

<style scoped>
.help-content {
  padding: 0 10px 20px 10px;
}

.section-intro {
  color: #606266;
  font-size: 13px;
  margin-bottom: 15px;
}

.legend-grid {
  display: flex;
  flex-direction: column;
  gap: 10px;
  border: 1px solid #ebeef5;
  border-radius: 4px;
  padding: 15px;
  background: #fdfdfd;
}

.legend-header {
  display: grid;
  grid-template-columns: 100px 120px 1fr;
  gap: 15px;
  font-size: 12px;
  color: #909399;
  font-weight: bold;
  border-bottom: 1px solid #ebeef5;
  padding-bottom: 8px;
  margin-bottom: 5px;
}

.legend-row {
  display: grid;
  grid-template-columns: 100px 120px 1fr;
  gap: 15px;
  align-items: center;
  padding: 8px 0;
  border-bottom: 1px dashed #f2f2f2;
}

.legend-row:last-child {
  border-bottom: none;
}

.legend-sample {
  display: flex;
  justify-content: center;
}

.form-sample {
  width: 100%;
  min-height: 28px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: 1px dashed #dcdfe6;
  border-radius: 4px;
  background: #fafafa;
}

.sample-box {
  transition: all 0.2s;
}

.legend-name .zh {
  font-size: 14px;
  color: #303133;
  font-weight: 500;
}

.legend-name .en {
  font-size: 12px;
  color: #909399;
  font-family: monospace;
}

.legend-desc {
  font-size: 13px;
  color: #606266;
  line-height: 1.4;
}
</style>
