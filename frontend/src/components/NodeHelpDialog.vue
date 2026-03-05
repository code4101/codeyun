<template>
  <el-dialog
    v-model="visible"
    title="节点属性说明 (Node Properties)"
    width="600px"
    class="node-help-dialog"
  >
    <div class="help-content">
      <el-tabs v-model="activeTab">
        <el-tab-pane label="节点类型 (Types)" name="type">
          <p class="section-intro">类型决定了节点的<b>颜色主题</b>。</p>
          <div class="legend-grid">
            <div class="legend-header">
              <span>样式示例</span>
              <span>名称 (中/英)</span>
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
                <div class="en">{{ type.labelEn }}</div>
              </div>
              <div class="legend-desc">{{ type.description }}</div>
            </div>
          </div>
        </el-tab-pane>

        <el-tab-pane label="节点状态 (Statuses)" name="status">
          <p class="section-intro">状态决定了节点的<b>边框线型</b>和<b>背景填充</b>。</p>
          <div class="legend-grid status-grid">
            <div class="legend-header">
              <span>样式示例</span>
              <span>名称 (中/英)</span>
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
                <div class="en">{{ status.labelEn }}</div>
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
  type NodeTypeItem, 
  type NodeStatusItem,
  getNodeStyle 
} from '@/utils/nodeConfig';

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

const activeTab = ref('type');
const orderedTypes = computed(() => getOrderedNodeTypes());
const orderedStatuses = computed(() => getOrderedNodeStatuses());

// Helper for Type Preview
const getTypeStyle = (type: NodeTypeItem) => {
  // For type preview, use default 'idea' style (solid very light gray border)
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

// Helper for Status Preview (Use 'Task' blue as base color context)
const getStatusStyle = (status: NodeStatusItem) => {
  // Mock context: Type = Task (Blue)
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

const getStatusRule = (status: NodeStatusItem) => {
  const map: Record<string, string> = {
    idea: '实线超浅灰边框，无填充',
    todo: '虚线深灰边框，无填充',
    doing: '实线黑色边框，无填充',
    predone: '虚线类型色边框，类型色浅色填充',
    done: '实线类型色边框，类型色浅色填充',
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
