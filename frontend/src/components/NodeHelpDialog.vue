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
          <p class="section-intro">分类不是固定模板，而是一套面向你自己业务结构的<b>自定义归类系统</b>。</p>
          <div class="category-guide">
            <div class="category-card">
              <div class="category-card-title">业务归类</div>
              <div class="category-card-desc">分类用来表达“这个节点属于哪一类工作对象或业务语义”。名称由你自己定义，系统不会限制成固定几种。</div>
            </div>
            <div class="category-card">
              <div class="category-card-title">颜色主题</div>
              <div class="category-card-desc">每个分类会绑定一套颜色主题，节点在日历、星图、列表等视图里的颜色统一由分类颜色决定。</div>
            </div>
            <div class="category-card">
              <div class="category-card-title">权重混色</div>
              <div class="category-card-desc">一个节点可以同时挂多个分类，并给每个分类分配权重；系统会按权重自动混色，得到最终展示色。</div>
            </div>
            <div class="category-card">
              <div class="category-card-title">筛选与组织</div>
              <div class="category-card-desc">分类不仅影响颜色，也参与筛选、统计、批量整理和工作视图组织，是节点最核心的业务维度之一。</div>
            </div>
            <div class="category-card">
              <div class="category-card-title">主分类显示</div>
              <div class="category-card-desc">当一个节点存在多个分类时，系统会根据权重推导主分类，用于简化显示；但底层仍会完整保留多分类信息。</div>
            </div>
            <div class="category-card">
              <div class="category-card-title">可长期演化</div>
              <div class="category-card-desc">分类体系可以随着你的工作方式逐步调整。新增、改名、换色、重排顺序，都会反映到整套笔记系统里。</div>
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
          <p class="section-intro">阶段表达这件事当前所处的<b>意愿与推进状态</b>；边框和填充只是帮助你快速识别状态的视觉提示。</p>
          <div class="legend-grid status-grid">
            <div class="legend-header">
              <span>样式示例</span>
              <span>名称</span>
              <span>含义说明</span>
            </div>
            <div v-for="status in orderedStatuses" :key="status.id" class="legend-row">
              <div class="legend-sample">
                <div class="sample-box" :style="getStatusStyle(status)">
                  <template v-if="useSplitStatusPreview(status)">
                    <span class="sample-label-layer" :style="getStatusSplitLayerStyle(status, 'fill')">{{ status.label }}</span>
                    <span class="sample-label-layer" :style="getStatusSplitLayerStyle(status, 'empty')">{{ status.label }}</span>
                  </template>
                  <template v-else>
                    {{ status.label }}
                  </template>
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
  getOrderedNodeStatuses,
  getOrderedNoteForms,
  type NoteFormItem,
  type NodeStatusItem,
  getNodeDisplayStyle,
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
const orderedForms = computed(() => getOrderedNoteForms());
const orderedStatuses = computed(() => getOrderedNodeStatuses());
const statusPreviewColor = '#337ECC';

// Helper for stage preview (use Task blue as base color context)
const getStatusPreviewTheme = (status: NodeStatusItem) => {
  const previewProgress = status.id === 'done' ? 0.58 : null;
  const style = getNodeDisplayStyle('task', status.id, statusPreviewColor, null, previewProgress);
  if (status.id !== 'done') return style;

  return {
    ...style,
    borderColor: statusPreviewColor,
    backgroundColor: '#FFFFFF',
    backgroundImage: `linear-gradient(to right, ${statusPreviewColor} 0%, ${statusPreviewColor} 58%, #FFFFFF 58%, #FFFFFF 100%)`,
    color: '#0F172A',
    fillTextColor: '#FFFFFF',
    emptyTextColor: '#0F172A',
    partialFillRatio: 0.58,
  };
};

const getStatusStyle = (status: NodeStatusItem) => {
  const style = getStatusPreviewTheme(status);
  return {
    borderColor: style.borderColor,
    borderWidth: style.borderWidth,
    borderStyle: style.borderStyle,
    backgroundColor: style.backgroundColor,
    backgroundImage: style.backgroundImage,
    color: style.color,
    textDecoration: style.textDecoration,
    opacity: style.opacity,
    fontSize: '12px',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    height: '28px',
    borderRadius: '4px',
    width: '100%'
  };
};

const useSplitStatusPreview = (status: NodeStatusItem) => {
  const ratio = getStatusPreviewTheme(status).partialFillRatio;
  return typeof ratio === 'number' && ratio > 0 && ratio < 1;
};

const getStatusSplitLayerStyle = (status: NodeStatusItem, mode: 'fill' | 'empty') => {
  const style = getStatusPreviewTheme(status);
  const ratio = style.partialFillRatio ?? 0;
  return {
    color: mode === 'fill' ? style.fillTextColor : style.emptyTextColor,
    clipPath: mode === 'fill'
      ? `inset(0 ${(100 - ratio * 100).toFixed(2)}% 0 0)`
      : `inset(0 0 0 ${(ratio * 100).toFixed(2)}%)`
  };
};

const getFormRule = (form: NoteFormItem) => {
  const map: Record<string, string> = {
    note: '默认通用形态，标题旁不额外显示图标。',
    document: '适合正文排版和长内容，会显示文档图标。',
    memo: '适合短平快记录和轻量便签，会显示备忘图标。',
    music: '适合音乐作品、专辑和音频素材，会显示音乐图标。',
    video: '适合电影、剧集和视频资料，会显示影视图标。',
    game: '适合游戏作品、攻略记录和游玩资料，会显示游戏图标。',
    book: '适合书籍、电子书和长篇阅读内容，会显示书籍图标。'
  };
  return map[form.id] || form.description;
};

const getStatusRule = (status: NodeStatusItem) => {
  const map: Record<string, string> = {
    idea: '默认状态，偏向记录资料、想法片段和过程信息，本身不强调“要不要做”。',
    todo: '表示这件事“可以做”，但当前没有明确打算去做，先把 idea 记下来，未来再评估是否推进。',
    doing: '表示已经决定想做、准备推进，但不要求必须已经开工，也不要求现在就有实际进度。',
    done: '表示这件事已经完成；进度留空默认就是 100%，需要表达部分或相对进度时再填写。',
    delete: '表示这条思路已经过时、被替代或不再适合继续推进，保留记录但默认视为废弃。'
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

.category-guide {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.category-card {
  border: 1px solid #ebeef5;
  border-radius: 10px;
  padding: 14px;
  background: linear-gradient(180deg, #fdfefe 0%, #f8fafc 100%);
}

.category-card-title {
  font-size: 14px;
  font-weight: 600;
  color: #303133;
  margin-bottom: 8px;
}

.category-card-desc {
  font-size: 13px;
  line-height: 1.6;
  color: #606266;
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
  position: relative;
  overflow: hidden;
}

.sample-label-layer {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
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

@media (max-width: 640px) {
  .category-guide {
    grid-template-columns: 1fr;
  }
}
</style>
