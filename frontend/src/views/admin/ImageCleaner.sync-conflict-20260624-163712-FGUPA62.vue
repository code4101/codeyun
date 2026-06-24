<template>
  <div class="image-cleaner">
    <div class="header">
      <h2>图片清理</h2>
      <div class="actions">
        <el-button type="primary" @click="scanImages" :loading="loading">
          扫描未引用图片
        </el-button>
        <el-button 
          type="danger" 
          @click="confirmDelete" 
          :disabled="selectedFiles.length === 0"
          :loading="deleting"
        >
          批量删除 ({{ selectedFiles.length }})
        </el-button>
      </div>
    </div>

    <div v-if="stats" class="stats-cards">
      <el-card shadow="hover" class="stats-card">
        <template #header>
          <div class="card-header">
            <span>存储总量</span>
          </div>
        </template>
        <div class="card-content">
          <div class="stat-item">
            <span class="label">图片总数</span>
            <span class="value">{{ stats.total_count }} 张</span>
          </div>
          <div class="stat-item">
            <span class="label">总占用</span>
            <span class="value">{{ formatSize(stats.total_size) }}</span>
          </div>
        </div>
      </el-card>

      <el-card shadow="hover" class="stats-card warning-card">
        <template #header>
          <div class="card-header">
            <span>待清理</span>
          </div>
        </template>
        <div class="card-content">
          <div class="stat-item">
            <span class="label">孤儿图片</span>
            <span class="value highlight">{{ stats.orphan_count }} 张</span>
          </div>
          <div class="stat-item">
            <span class="label">可释放</span>
            <span class="value highlight">{{ formatSize(stats.orphan_size) }}</span>
          </div>
        </div>
      </el-card>
    </div>

    <el-alert
      v-if="orphans.length > 0"
      :title="`扫描到 ${orphans.length} 张未被引用的图片，共占用 ${formatSize(totalSize)}`"
      type="warning"
      show-icon
      class="mb-4"
    />

    <el-table
      v-loading="loading"
      :data="orphans"
      style="width: 100%"
      @selection-change="handleSelectionChange"
    >
      <el-table-column type="selection" width="55" />
      
      <el-table-column label="预览" width="120">
        <template #default="scope">
          <el-image 
            style="width: 80px; height: 80px"
            :src="getImageUrl(scope.row.url)"
            :preview-src-list="[getImageUrl(scope.row.url)]"
            fit="cover"
            preview-teleported
          />
        </template>
      </el-table-column>
      
      <el-table-column prop="filename" label="文件名" show-overflow-tooltip sortable />
      
      <el-table-column prop="size" label="大小" width="120" sortable>
        <template #default="scope">
          {{ formatSize(scope.row.size) }}
        </template>
      </el-table-column>
      
      <el-table-column prop="mtime" label="修改时间" width="180" sortable>
        <template #default="scope">
          {{ formatTime(scope.row.mtime) }}
        </template>
      </el-table-column>

      <el-table-column label="操作" width="100">
        <template #default="scope">
          <el-button 
            type="danger" 
            size="small" 
            link 
            @click="deleteSingle(scope.row.filename)"
          >
            删除
          </el-button>
        </template>
      </el-table-column>
    </el-table>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import { fetchOrphanImages, deleteOrphanImages, OrphanImage, StorageStats } from '@/api/admin';

const loading = ref(false);
const deleting = ref(false);
const orphans = ref<OrphanImage[]>([]);
const stats = ref<StorageStats | null>(null);
const selectedFiles = ref<string[]>([]);

const totalSize = computed(() => {
  return orphans.value.reduce((acc, curr) => acc + curr.size, 0);
});

// Helper to handle URL. 
// If URL starts with /static, prepend base URL if needed.
// However, the proxy handles /static, so we can use relative path.
// But el-image might need full URL if it's not going through proxy?
// No, standard img src works with relative path if proxy is set up.
// Let's assume relative path works.
const getImageUrl = (url: string) => {
  return url;
};

const formatSize = (bytes: number) => {
  if (bytes === 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
};

const formatTime = (timestamp: number) => {
  return new Date(timestamp * 1000).toLocaleString();
};

const scanImages = async () => {
  loading.value = true;
  orphans.value = [];
  stats.value = null;
  try {
    const response = await fetchOrphanImages();
    orphans.value = response.orphans;
    stats.value = response.stats;
    if (orphans.value.length === 0) {
      ElMessage.success('没有发现未引用的图片');
    }
  } catch (error) {
    console.error(error);
    ElMessage.error('扫描失败');
  } finally {
    loading.value = false;
  }
};

const handleSelectionChange = (selection: OrphanImage[]) => {
  selectedFiles.value = selection.map(item => item.filename);
};

const deleteSingle = (filename: string) => {
  selectedFiles.value = [filename];
  confirmDelete();
};

const confirmDelete = () => {
  if (selectedFiles.value.length === 0) return;

  ElMessageBox.confirm(
    `确定要删除选中的 ${selectedFiles.value.length} 张图片吗？此操作不可恢复。`,
    '警告',
    {
      confirmButtonText: '确定删除',
      cancelButtonText: '取消',
      type: 'warning',
    }
  ).then(async () => {
    deleting.value = true;
    try {
      const result = await deleteOrphanImages(selectedFiles.value);
      ElMessage.success(`成功删除 ${result.deleted_count} 张图片`);
      
      if (result.errors && result.errors.length > 0) {
        console.error(result.errors);
        ElMessage.warning(`部分文件删除失败: ${result.errors.join(', ')}`);
      }
      
      // Refresh list
      await scanImages();
    } catch (error) {
      console.error(error);
      ElMessage.error('删除失败');
    } finally {
      deleting.value = false;
      selectedFiles.value = [];
    }
  }).catch(() => {
    // Cancelled
  });
};
</script>

<style scoped>
.image-cleaner {
  padding: 20px;
}

.header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 20px;
}

.mb-4 {
  margin-bottom: 16px;
}

.stats-cards {
  display: flex;
  gap: 20px;
  margin-bottom: 20px;
}

.stats-card {
  flex: 1;
}

.card-header {
  font-weight: bold;
}

.card-content {
  display: flex;
  justify-content: space-around;
}

.stat-item {
  display: flex;
  flex-direction: column;
  align-items: center;
}

.stat-item .label {
  font-size: 12px;
  color: #909399;
  margin-bottom: 5px;
}

.stat-item .value {
  font-size: 20px;
  font-weight: bold;
  color: #303133;
}

.warning-card .highlight {
  color: #F56C6C;
}
</style>
