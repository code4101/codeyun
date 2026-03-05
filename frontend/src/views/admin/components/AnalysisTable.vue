<template>
  <div>
    <el-table 
      :data="data" 
      style="width: 100%; height: 100%" 
      stripe 
      border 
      size="small"
      :default-sort="defaultSort"
      @sort-change="handleSortChange"
    >
      <el-table-column 
        v-if="type === 'file'" 
        label="预览" 
        width="60"
      >
        <template #default="scope">
          <el-image 
            style="width: 40px; height: 40px"
            :src="scope.row.url"
            :preview-src-list="[scope.row.url]"
            fit="cover"
            preview-teleported
          />
        </template>
      </el-table-column>

      <el-table-column 
        :prop="titleProp" 
        :label="titleLabel" 
        show-overflow-tooltip 
        sortable="custom"
      />
      
      <el-table-column 
        v-if="type === 'node'"
        prop="id" 
        label="ID" 
        width="180" 
        show-overflow-tooltip 
      />

      <el-table-column 
        v-if="type === 'link'"
        prop="link" 
        label="失效链接" 
        show-overflow-tooltip 
      />

      <el-table-column 
        v-if="type !== 'link'"
        prop="size" 
        label="大小" 
        width="100" 
        sortable="custom"
      >
        <template #default="scope">{{ formatSize(scope.row.size) }}</template>
      </el-table-column>

      <el-table-column 
        v-if="type !== 'link'"
        :prop="timeProp" 
        label="时间" 
        width="160"
        sortable="custom"
      >
        <template #default="scope">{{ formatTime(scope.row[timeProp]) }}</template>
      </el-table-column>

      <el-table-column 
        v-if="type === 'file' && allowOptimize"
        label="操作"
        width="80"
        align="center"
      >
        <template #default="scope">
          <el-button 
            type="primary" 
            link 
            size="small" 
            @click="openOptimize(scope.row)"
          >
            优化
          </el-button>
        </template>
      </el-table-column>
    </el-table>

    <!-- Optimization Dialog -->
    <el-dialog
      v-model="optimizeVisible"
      title="图片优化预览"
      width="600px"
      append-to-body
    >
      <div v-if="currentItem" class="optimize-container">
        <div class="optimize-controls">
          <el-radio-group v-model="targetFormat" @change="handlePreview" size="small">
            <el-radio-button label="jpeg">JPEG (80%)</el-radio-button>
            <el-radio-button label="webp">WebP (80%)</el-radio-button>
          </el-radio-group>
        </div>

        <div class="preview-comparison" v-loading="previewLoading">
          <div class="preview-box">
            <div class="preview-label">原图</div>
            <el-image :src="currentItem.url" fit="contain" class="preview-img" />
            <div class="preview-info">{{ formatSize(currentItem.size) }}</div>
          </div>
          
          <div class="preview-arrow">
            <el-icon><Right /></el-icon>
          </div>

          <div class="preview-box">
            <div class="preview-label">优化后</div>
            <el-image v-if="previewData" :src="previewData.preview_url" fit="contain" class="preview-img" />
            <div class="preview-info" v-if="previewData">
              {{ formatSize(previewData.optimized_size) }}
              <span class="save-tag">省 {{ formatSize(previewData.saved_bytes) }}</span>
            </div>
            <div class="preview-info" v-else>等待生成...</div>
          </div>
        </div>
      </div>
      <template #footer>
        <span class="dialog-footer">
          <el-button @click="optimizeVisible = false">取消</el-button>
          <el-button type="primary" @click="confirmOptimize" :loading="confirmLoading" :disabled="!previewData">
            确认替换
          </el-button>
        </span>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue';
import { Right } from '@element-plus/icons-vue';
import { ElMessage } from 'element-plus';
import { previewOptimizedImage, confirmImageOptimization, OptimizedPreview } from '@/api/admin';

const props = defineProps<{
  data: any[];
  type: 'file' | 'node' | 'link';
  allowOptimize?: boolean;
}>();

const emit = defineEmits(['refresh']);

const titleProp = computed(() => {
  if (props.type === 'file') return 'filename';
  if (props.type === 'node') return 'title';
  if (props.type === 'link') return 'note_title';
  return 'name';
});

const titleLabel = computed(() => {
  if (props.type === 'file') return '文件名';
  if (props.type === 'node') return '标题';
  if (props.type === 'link') return '笔记标题';
  return '名称';
});

const timeProp = computed(() => {
  if (props.type === 'node') return 'updated_at';
  return 'mtime';
});

const defaultSort = computed(() => {
  if (props.type === 'link') return { prop: 'note_title', order: 'ascending' };
  return { prop: 'size', order: 'descending' };
});

const formatSize = (bytes: number) => {
  if (bytes === 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
};

const formatTime = (timestamp: number) => {
  if (!timestamp) return '-';
  return new Date(timestamp * 1000).toLocaleString();
};

const handleSortChange = ({ prop, order }: { prop: string, order: string }) => {
  if (!order) return;
  props.data.sort((a, b) => {
    let valA = a[prop];
    let valB = b[prop];
    if (typeof valA === 'string') {
        valA = valA.toLowerCase();
        valB = valB.toLowerCase();
    }
    if (order === 'ascending') {
      return valA > valB ? 1 : -1;
    } else {
      return valA < valB ? 1 : -1;
    }
  });
};

// --- Optimization Logic ---
const optimizeVisible = ref(false);
const currentItem = ref<any>(null);
const targetFormat = ref<'jpeg' | 'webp'>('jpeg');
const previewLoading = ref(false);
const previewData = ref<OptimizedPreview | null>(null);
const confirmLoading = ref(false);

const openOptimize = (item: any) => {
  currentItem.value = item;
  optimizeVisible.value = true;
  previewData.value = null;
  targetFormat.value = 'jpeg';
  handlePreview();
};

const handlePreview = async () => {
  if (!currentItem.value) return;
  previewLoading.value = true;
  try {
    previewData.value = await previewOptimizedImage({
      filename: currentItem.value.filename,
      target_format: targetFormat.value,
      quality: 80
    });
  } catch (error) {
    ElMessage.error('生成预览失败');
  } finally {
    previewLoading.value = false;
  }
};

const confirmOptimize = async () => {
  if (!currentItem.value || !previewData.value) return;
  confirmLoading.value = true;
  try {
    const res = await confirmImageOptimization({
      filename: currentItem.value.filename,
      target_format: targetFormat.value,
      quality: 80
    });
    
    if (res.success) {
      ElMessage.success('图片优化成功');
      optimizeVisible.value = false;
      emit('refresh'); // Notify parent to reload list
    }
  } catch (error) {
    ElMessage.error('替换失败');
  } finally {
    confirmLoading.value = false;
  }
};
</script>

<style scoped>
.optimize-container {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.optimize-controls {
  text-align: center;
}

.preview-comparison {
  display: flex;
  align-items: center;
  justify-content: space-around;
  background: #f5f7fa;
  padding: 20px;
  border-radius: 8px;
}

.preview-box {
  display: flex;
  flex-direction: column;
  align-items: center;
  width: 200px;
}

.preview-label {
  font-weight: bold;
  margin-bottom: 10px;
  color: #606266;
}

.preview-img {
  width: 180px;
  height: 180px;
  background: white; /* Checkerboard pattern ideal here */
  border: 1px solid #dcdfe6;
  border-radius: 4px;
}

.preview-info {
  margin-top: 10px;
  font-size: 14px;
  color: #303133;
}

.save-tag {
  color: #67C23A;
  font-weight: bold;
  margin-left: 5px;
}

.preview-arrow {
  font-size: 24px;
  color: #909399;
}
</style>
