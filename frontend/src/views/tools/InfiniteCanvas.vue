<template>
  <div 
    class="infinite-canvas-container" 
    ref="containerRef" 
    @wheel.prevent="handleWheel" 
    @mousedown.middle="startPan" 
    @mousedown.left="handleContainerClick"
    :style="{ backgroundColor: viewState.backgroundColor }"
  >
    <!-- 背景点阵层 -->
    <div 
      v-if="viewState.showGrid"
      class="grid-background"
      :style="{
        backgroundPosition: `${viewState.x}px ${viewState.y}px`,
        backgroundSize: `${20 * viewState.scale}px ${20 * viewState.scale}px`
      }"
    ></div>

    <!-- 顶部控制条 (包含左侧提示和右侧工具栏) -->
    <div class="top-bar">
      <!-- 左侧：提示信息 -->
      <div class="left-panel">
        <el-alert
          title="数据仅保存在内存中，刷新页面即丢失。请定期“导出数据”"
          type="warning"
          show-icon
          :closable="false"
          class="mini-alert"
        />
      </div>

      <!-- 右侧：工具栏 -->
      <div class="right-panel">
        <el-tooltip content="显示/隐藏网格" placement="bottom">
          <el-switch
            v-model="viewState.showGrid"
            inline-prompt
            :active-icon="Grid"
            :inactive-icon="Close"
            style="margin-right: 10px"
          />
        </el-tooltip>
        <el-tooltip content="背景颜色" placement="bottom">
          <div class="color-picker-wrapper">
            <el-color-picker 
              v-model="viewState.backgroundColor" 
              show-alpha 
              :predefine="predefineColors" 
              @active-change="handleColorChange"
              @change="handleColorChange"
              popper-class="custom-color-picker"
              ref="colorPickerRef"
            />
            <Teleport to=".custom-color-picker .el-color-dropdown" v-if="isColorPickerOpen">
              <div class="custom-rgb-panel" @click.stop>
                <div class="rgb-row">
                  <span class="label">红色(R):</span>
                  <el-input-number v-model="rgbState.r" :min="0" :max="255" size="small" :controls="false" @change="updateColorFromRGB" />
                </div>
                <div class="rgb-row">
                  <span class="label">绿色(G):</span>
                  <el-input-number v-model="rgbState.g" :min="0" :max="255" size="small" :controls="false" @change="updateColorFromRGB" />
                </div>
                <div class="rgb-row">
                  <span class="label">蓝色(B):</span>
                  <el-input-number v-model="rgbState.b" :min="0" :max="255" size="small" :controls="false" @change="updateColorFromRGB" />
                </div>
              </div>
            </Teleport>
          </div>
        </el-tooltip>
        <el-button-group>
          <el-button type="primary" :icon="Download" @click="exportData">导出</el-button>
          <el-button type="primary" :icon="Upload" @click="triggerImport">导入</el-button>
          <el-button type="danger" :icon="Delete" @click="clearCanvas">清空</el-button>
        </el-button-group>
      </div>
      
      <input 
        type="file" 
        ref="fileInput" 
        style="display: none" 
        accept=".json" 
        @change="handleFileImport"
      />
    </div>

    <!-- 画布世界 -->
    <div 
      class="canvas-world" 
      :style="{ 
        transform: `translate(${viewState.x}px, ${viewState.y}px) scale(${viewState.scale})`,
        transformOrigin: '0 0'
      }"
    >
      <!-- 渲染所有元素 -->
      <div
        v-for="element in elements"
        :key="element.id"
        class="canvas-element"
        :class="{ 'is-selected': selectedId === element.id }"
        :style="{
          left: `${element.x}px`,
          top: `${element.y}px`,
          width: `${element.width}px`,
          height: `${element.height}px`,
          zIndex: selectedId === element.id ? 1000 : 1
        }"
        @mousedown.stop="startDragElement($event, element)"
      >
        <!-- 图片元素 -->
        <img 
          v-if="element.type === 'image'" 
          :src="element.content" 
          class="element-content image-content"
          draggable="false"
        />

        <!-- 文本元素 -->
        <div 
          v-if="element.type === 'text'"
          class="element-content text-content"
          :ref="(el) => setTextRef(element.id, el as HTMLElement | null)"
          :contenteditable="editingId === element.id"
          @dblclick.stop="startEdit(element.id)"
          @mousedown="handleTextMouseDown(element, $event)"
          @blur="commitTextContent(element, $event)"
          v-html="element.content"
        ></div>

        <!-- 选中时的控制柄 (仅调整大小) -->
        <div v-if="selectedId === element.id" class="resize-handle" @mousedown.stop="startResize($event, element)"></div>
        
        <!-- 删除按钮 -->
        <div v-if="selectedId === element.id" class="delete-btn" @mousedown.stop="deleteElement(element.id)">
          <el-icon><Close /></el-icon>
        </div>
      </div>
    </div>
    
    <!-- 操作指引 -->
    <div class="guide-overlay">
      <p>🖱️ <strong>空格+左键</strong> 拖拽画布 | 🔍 <strong>滚轮</strong> 缩放</p>
      <p>📋 <strong>Ctrl+V</strong> 粘贴图片 | 📝 <strong>双击空白处</strong> 插入文本</p>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted, onUnmounted, watch, nextTick } from 'vue';
import { Close, Download, Upload, Delete, Grid } from '@element-plus/icons-vue';
import { ElMessage, ElMessageBox } from 'element-plus';

interface CanvasElement {
  id: string;
  type: 'image' | 'text';
  x: number;
  y: number;
  width: number;
  height: number;
  content: string;
}

const containerRef = ref<HTMLElement | null>(null);
const fileInput = ref<HTMLInputElement | null>(null);
const elements = ref<CanvasElement[]>([]);
const selectedId = ref<string | null>(null);
const colorPickerRef = ref<any>(null);
const isColorPickerOpen = ref(false);
const editingId = ref<string | null>(null);

const textRefMap = new Map<string, HTMLElement>();
const setTextRef = (id: string, el: HTMLElement | null) => {
  if (!el) {
    textRefMap.delete(id);
    return;
  }
  textRefMap.set(id, el);
};

const focusText = (id: string, selectAll: boolean) => {
  const el = textRefMap.get(id);
  if (!el) return;
  el.focus();

  const sel = window.getSelection();
  if (!sel) return;
  sel.removeAllRanges();

  const range = document.createRange();
  if (selectAll) {
    range.selectNodeContents(el);
  } else {
    range.selectNodeContents(el);
    range.collapse(false);
  }
  sel.addRange(range);
};

const startEdit = (id: string, selectAll = false) => {
  selectedId.value = id;
  editingId.value = id;
  nextTick(() => focusText(id, selectAll));
};

const isEffectivelyEmptyTextHtml = (html: string) => {
  const text = html
    .replace(/<br\s*\/?>/gi, '')
    .replace(/&nbsp;/gi, ' ')
    .replace(/<[^>]*>/g, '')
    .trim();
  return text.length === 0;
};

const removeElementById = (id: string) => {
  elements.value = elements.value.filter(el => el.id !== id);
  if (selectedId.value === id) selectedId.value = null;
  if (editingId.value === id) editingId.value = null;
};

const commitTextContent = (element: CanvasElement, e: FocusEvent) => {
  const target = e.target as HTMLElement | null;
  const html = target?.innerHTML ?? '';
  if (isEffectivelyEmptyTextHtml(html)) {
    removeElementById(element.id);
    return;
  }
  element.content = html;
  if (editingId.value === element.id) editingId.value = null;
};

const handleTextMouseDown = (element: CanvasElement, e: MouseEvent) => {
  if (editingId.value === element.id) e.stopPropagation();
};

// 预设颜色
const predefineColors = ref([
  '#f0f2f5', // 默认灰
  '#ffffff', // 白
  '#000000', // 黑
  '#ff4500',
  '#ff8c00',
  '#ffd700',
  '#90ee90',
  '#00ced1',
  '#1e90ff',
  '#c71585',
]);

// 视图状态
const viewState = reactive({
  x: 0,
  y: 0,
  scale: 1,
  backgroundColor: '#f0f2f5', // 默认背景色
  showGrid: true, // 是否显示网格
});

// RGB 状态
const rgbState = reactive({
  r: 240,
  g: 242,
  b: 245
});

// 解析颜色字符串到 RGB
const parseColor = (color: string) => {
  if (!color) return;
  
  // 创建一个临时元素来解析颜色
  const div = document.createElement('div');
  div.style.color = color;
  document.body.appendChild(div);
  const computedColor = getComputedStyle(div).color;
  document.body.removeChild(div);
  
  // computedColor 格式通常是 rgb(r, g, b) 或 rgba(r, g, b, a)
  const match = computedColor.match(/(\d+),\s*(\d+),\s*(\d+)/);
  if (match) {
    rgbState.r = parseInt(match[1]);
    rgbState.g = parseInt(match[2]);
    rgbState.b = parseInt(match[3]);
  }
};

// 监听背景色变化，同步到 RGB 输入框
watch(() => viewState.backgroundColor, (newColor) => {
  if (newColor) {
    parseColor(newColor);
  }
});

const hideColorPickerDefaultControls = () => {
  const dropdowns = Array.from(document.querySelectorAll('.el-color-dropdown'));
    const popper =
      (document.querySelector('.custom-color-picker') as HTMLElement | null) ||
      (dropdowns.length > 0 ? dropdowns[dropdowns.length - 1] as HTMLElement : null);
  if (!popper) return;
  const btns = popper.querySelector('.el-color-dropdown__btns') as HTMLElement | null;
  if (btns) btns.style.display = 'none';
  const value = popper.querySelector('.el-color-dropdown__value') as HTMLElement | null;
  if (value) value.style.display = 'none';
};

watch(
  () => colorPickerRef.value?.showPicker,
  (val) => {
    isColorPickerOpen.value = !!val;
    if (val) {
      parseColor(viewState.backgroundColor);
      nextTick(() => {
        hideColorPickerDefaultControls();
        setTimeout(hideColorPickerDefaultControls, 0);
      });
    }
  }
);

const updateColorFromRGB = () => {
  // 获取当前的透明度（如果存在）
  let alpha = 1;
  if (viewState.backgroundColor.startsWith('rgba')) {
    const match = viewState.backgroundColor.match(/[\d\.]+\)$/);
    if (match) {
      alpha = parseFloat(match[0]);
    }
  }
  viewState.backgroundColor = `rgba(${rgbState.r}, ${rgbState.g}, ${rgbState.b}, ${alpha})`;
};

const handleColorChange = (val: string) => {
  if (val) {
    viewState.backgroundColor = val;
  }
};

// 生成唯一ID
const generateId = () => `el_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;

// ===================== 数据导入导出 =====================
const exportData = () => {
  if (elements.value.length === 0) {
    ElMessage.warning('画布为空，无需导出');
    return;
  }
  const data = {
    version: '1.0',
    timestamp: Date.now(),
    viewState: { ...viewState }, // 导出当前视图状态（包含背景色、网格设置）
    elements: elements.value
  };
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `infinite-canvas-${new Date().toISOString().slice(0,10)}.json`;
  a.click();
  URL.revokeObjectURL(url);
  ElMessage.success('数据已导出');
};

const triggerImport = () => {
  fileInput.value?.click();
};

const handleFileImport = (e: Event) => {
  const file = (e.target as HTMLInputElement).files?.[0];
  if (!file) return;

  const reader = new FileReader();
  reader.onload = (e) => {
    try {
      const data = JSON.parse(e.target?.result as string);
      if (data.elements && Array.isArray(data.elements)) {
        // 确认覆盖
        if (elements.value.length > 0) {
          ElMessageBox.confirm('导入将覆盖当前画布内容，是否继续？', '提示', {
            confirmButtonText: '覆盖',
            cancelButtonText: '取消',
            type: 'warning',
          }).then(() => {
            applyImportData(data);
          }).catch(() => {
             // 取消
             if (fileInput.value) fileInput.value.value = '';
          });
        } else {
          applyImportData(data);
        }
      } else {
        ElMessage.error('无效的数据文件格式');
      }
    } catch (err) {
      console.error(err);
      ElMessage.error('文件解析失败');
    }
  };
  reader.readAsText(file);
};

const applyImportData = (data: any) => {
  elements.value = data.elements;
  if (data.viewState) {
    viewState.x = data.viewState.x || 0;
    viewState.y = data.viewState.y || 0;
    viewState.scale = data.viewState.scale || 1;
    if (data.viewState.backgroundColor) {
      viewState.backgroundColor = data.viewState.backgroundColor;
    }
    // 恢复网格设置，如果旧数据没有该字段则默认为 true
    viewState.showGrid = data.viewState.showGrid !== undefined ? data.viewState.showGrid : true;
  }
  ElMessage.success('数据导入成功');
  if (fileInput.value) fileInput.value.value = '';
};

const clearCanvas = () => {
  ElMessageBox.confirm('确定要清空画布吗？此操作不可撤销。', '警告', {
    confirmButtonText: '清空',
    cancelButtonText: '取消',
    type: 'warning',
  }).then(() => {
    elements.value = [];
    selectedId.value = null;
    viewState.x = 0;
    viewState.y = 0;
    viewState.scale = 1;
    // 可以在这里重置背景色，也可以保留
    // viewState.backgroundColor = '#f0f2f5'; 
    ElMessage.success('画布已清空');
  });
};

// ===================== 画布交互 (Pan & Zoom) =====================
const isPanning = ref(false);
const lastMousePos = { x: 0, y: 0 };
const isSpacePressed = ref(false);

const handleWheel = (e: WheelEvent) => {
  if (e.ctrlKey || e.metaKey) {
    e.preventDefault();
  }
  
  const zoomIntensity = 0.1;
  const delta = e.deltaY > 0 ? -zoomIntensity : zoomIntensity;
  const newScale = Math.min(Math.max(0.1, viewState.scale + delta), 5);
  
  const rect = containerRef.value!.getBoundingClientRect();
  const mouseX = e.clientX - rect.left;
  const mouseY = e.clientY - rect.top;
  
  const worldX = (mouseX - viewState.x) / viewState.scale;
  const worldY = (mouseY - viewState.y) / viewState.scale;
  
  viewState.scale = newScale;
  viewState.x = mouseX - worldX * newScale;
  viewState.y = mouseY - worldY * newScale;
};

const startPan = (e: MouseEvent) => {
  isPanning.value = true;
  lastMousePos.x = e.clientX;
  lastMousePos.y = e.clientY;
  
  document.addEventListener('mousemove', handlePan);
  document.addEventListener('mouseup', stopPan);
};

const handlePan = (e: MouseEvent) => {
  if (!isPanning.value) return;
  const dx = e.clientX - lastMousePos.x;
  const dy = e.clientY - lastMousePos.y;
  
  viewState.x += dx;
  viewState.y += dy;
  
  lastMousePos.x = e.clientX;
  lastMousePos.y = e.clientY;
};

const stopPan = () => {
  isPanning.value = false;
  document.removeEventListener('mousemove', handlePan);
  document.removeEventListener('mouseup', stopPan);
};

// 空格键平移支持
const handleKeyDown = (e: KeyboardEvent) => {
  if ((e.key === 'Backspace' || e.key === 'Delete') && selectedId.value && !editingId.value) {
    const target = e.target as HTMLElement;
    if (!target.isContentEditable && target.tagName !== 'INPUT' && target.tagName !== 'TEXTAREA') {
      removeElementById(selectedId.value);
      e.preventDefault();
      return;
    }
  }

  if (e.code === 'Space' && !isSpacePressed.value) {
    // 如果不在输入框中
    if ((e.target as HTMLElement).isContentEditable || (e.target as HTMLElement).tagName === 'INPUT') return;
    
    isSpacePressed.value = true;
    containerRef.value!.style.cursor = 'grab';
    e.preventDefault();
  }
};

const handleKeyUp = (e: KeyboardEvent) => {
  if (e.code === 'Space') {
    isSpacePressed.value = false;
    containerRef.value!.style.cursor = 'default';
  }
};

const handleContainerMouseDown = (e: MouseEvent) => {
  if (isSpacePressed.value || e.button === 1) { // 中键或空格+左键
    startPan(e);
  } else if (e.button === 0) { // 左键
    // handleContainerClick(e);
    selectedId.value = null;
    editingId.value = null;
  }
};

// ===================== 元素交互 (Add, Drag, Resize) =====================

const handlePaste = (e: ClipboardEvent) => {
  // 如果当前焦点在文本框内，不拦截粘贴
  if ((e.target as HTMLElement).isContentEditable) return;

  const items = e.clipboardData?.items;
  if (!items) return;

  for (const item of items) {
    if (item.type.indexOf('image') !== -1) {
      const blob = item.getAsFile();
      if (!blob) continue;
      
      const reader = new FileReader();
      reader.onload = (event) => {
        const img = new Image();
        img.onload = () => {
          let w = img.width;
          let h = img.height;
          // 限制初始最大尺寸
          if (w > 500) {
            const ratio = 500 / w;
            w = 500;
            h = h * ratio;
          }

          const rect = containerRef.value!.getBoundingClientRect();
          const centerX = rect.width / 2;
          const centerY = rect.height / 2;
          // 粘贴到屏幕中心
          const worldX = (centerX - viewState.x) / viewState.scale - w / 2;
          const worldY = (centerY - viewState.y) / viewState.scale - h / 2;

          elements.value.push({
            id: generateId(),
            type: 'image',
            x: worldX,
            y: worldY,
            width: w,
            height: h,
            content: event.target?.result as string
          });
        };
        img.src = event.target?.result as string;
      };
      reader.readAsDataURL(blob);
    }
  }
};

const handleContainerClick = (_e: MouseEvent) => {
  if (!isSpacePressed.value) {
    selectedId.value = null;
    editingId.value = null;
  }
};

const handleDoubleClick = (e: MouseEvent) => {
  if (isSpacePressed.value) return;
  if (e.target !== containerRef.value && (e.target as HTMLElement).classList.contains('canvas-world') && !(e.target as HTMLElement).classList.contains('grid-background')) {
     // OK
  } else if (e.target !== containerRef.value && !(e.target as HTMLElement).classList.contains('grid-background')) {
     // 如果点击的不是容器，也不是网格背景，那就是元素
     return;
  }

  const rect = containerRef.value!.getBoundingClientRect();
  const mouseX = e.clientX - rect.left;
  const mouseY = e.clientY - rect.top;
  
  const worldX = (mouseX - viewState.x) / viewState.scale;
  const worldY = (mouseY - viewState.y) / viewState.scale;

  const newId = generateId();
  elements.value.push({
    id: newId,
    type: 'text',
    x: worldX,
    y: worldY,
    width: 200,
    height: 50,
    content: '双击编辑文本'
  });
  
  // 自动选中
  selectedId.value = newId;
  startEdit(newId, true);
};

const startDragElement = (e: MouseEvent, element: CanvasElement) => {
  if (isSpacePressed.value) return; // 空格模式下不拖拽元素
  if (editingId.value === element.id) return;
  
  selectedId.value = element.id;
  const startX = e.clientX;
  const startY = e.clientY;
  const startElX = element.x;
  const startElY = element.y;

  const onMouseMove = (moveEvent: MouseEvent) => {
    const dx = moveEvent.clientX - startX;
    const dy = moveEvent.clientY - startY;
    
    element.x = startElX + dx / viewState.scale;
    element.y = startElY + dy / viewState.scale;
  };

  const onMouseUp = () => {
    document.removeEventListener('mousemove', onMouseMove);
    document.removeEventListener('mouseup', onMouseUp);
  };

  document.addEventListener('mousemove', onMouseMove);
  document.addEventListener('mouseup', onMouseUp);
};

const startResize = (e: MouseEvent, element: CanvasElement) => {
  e.stopPropagation();
  const startX = e.clientX;
  const startY = e.clientY;
  const startWidth = element.width;
  const startHeight = element.height;

  const onMouseMove = (moveEvent: MouseEvent) => {
    const dx = moveEvent.clientX - startX;
    const dy = moveEvent.clientY - startY;

    let newWidth = startWidth + dx / viewState.scale;
    let newHeight = startHeight + dy / viewState.scale;

    if (newWidth < 20) newWidth = 20;
    if (newHeight < 20) newHeight = 20;

    element.width = newWidth;
    element.height = newHeight;
  };

  const onMouseUp = () => {
    document.removeEventListener('mousemove', onMouseMove);
    document.removeEventListener('mouseup', onMouseUp);
  };

  document.addEventListener('mousemove', onMouseMove);
  document.addEventListener('mouseup', onMouseUp);
};

const deleteElement = (id: string) => {
  removeElementById(id);
};

// 生命周期
onMounted(() => {
  window.addEventListener('paste', handlePaste);
  window.addEventListener('keydown', handleKeyDown);
  window.addEventListener('keyup', handleKeyUp);
  
  if (containerRef.value) {
    containerRef.value.addEventListener('dblclick', handleDoubleClick);
  }
});

onUnmounted(() => {
  window.removeEventListener('paste', handlePaste);
  window.removeEventListener('keydown', handleKeyDown);
  window.removeEventListener('keyup', handleKeyUp);
  
  if (containerRef.value) {
    containerRef.value.removeEventListener('dblclick', handleDoubleClick);
  }
});
</script>

<style scoped>
.infinite-canvas-container {
  width: 100%;
  height: calc(100vh - 60px);
  overflow: hidden;
  position: relative;
  cursor: default;
  user-select: none;
  transition: background-color 0.3s;
}

.grid-background {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  pointer-events: none;
  background-image: radial-gradient(#d0d0d0 1px, transparent 1px);
  z-index: 0;
}

/* 顶部控制条：全宽、左右布局 */
.top-bar {
  position: absolute;
  top: 10px;
  left: 10px;
  right: 10px;
  z-index: 2000;
  display: flex;
  justify-content: space-between;
  align-items: flex-start; /* 顶部对齐，防止高度不同 */
  pointer-events: none; /* 让中间区域穿透 */
}

.left-panel, .right-panel {
  pointer-events: auto; /* 恢复子元素交互 */
}

.left-panel {
  max-width: 50%; /* 限制提示条宽度 */
  opacity: 0.9;
}

.mini-alert {
  padding: 6px 16px; /* 紧凑一点 */
}

/* 右侧工具栏样式 */
.right-panel {
  display: flex;
  align-items: center;
  gap: 10px;
  background: rgba(255, 255, 255, 0.9);
  padding: 5px;
  border-radius: 4px;
  box-shadow: 0 2px 12px 0 rgba(0, 0, 0, 0.1);
}

.canvas-world {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  pointer-events: none;
  z-index: 1; /* 确保在网格之上 */
}

.canvas-element {
  position: absolute;
  pointer-events: auto;
  border: 1px solid transparent;
  box-sizing: border-box;
}

.canvas-element.is-selected {
  border: 2px solid #409eff;
  box-shadow: 0 0 10px rgba(0,0,0,0.1);
}

.element-content {
  width: 100%;
  height: 100%;
  display: block;
}

.image-content {
  object-fit: fill;
  pointer-events: none; /* 让鼠标事件穿透到父级处理拖拽 */
}

.text-content {
  padding: 5px;
  background: transparent; /* 改为透明 */
  /* border: 1px dashed #ccc; */ /* 移除默认边框，更干净 */
  border-radius: 4px;
  overflow: hidden; 
  white-space: pre-wrap;
  font-size: 16px;
  cursor: text;
  outline: none;
}

/* 鼠标悬停或选中时显示边框，方便定位 */
.canvas-element:hover .text-content,
.canvas-element.is-selected .text-content {
  background: rgba(255, 255, 255, 0.2); /* 微微的白色背景方便选中 */
  border: 1px dashed #ccc;
}

.text-content:focus {
  background: rgba(255, 255, 255, 0.8); /* 编辑时加背景 */
  border-color: #409eff;
  cursor: text;
}

.resize-handle {
  position: absolute;
  bottom: -6px;
  right: -6px;
  width: 12px;
  height: 12px;
  background: #409eff;
  border-radius: 50%;
  cursor: nwse-resize;
  z-index: 10;
  border: 2px solid #fff;
}

.delete-btn {
  position: absolute;
  top: -12px;
  right: -12px;
  width: 24px;
  height: 24px;
  background: #f56c6c;
  color: white;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  z-index: 10;
  font-size: 14px;
  box-shadow: 0 2px 4px rgba(0,0,0,0.2);
}

.guide-overlay {
  position: absolute;
  bottom: 20px;
  left: 20px;
  background: rgba(0, 0, 0, 0.6);
  color: white;
  padding: 10px 15px;
  border-radius: 8px;
  font-size: 12px;
  pointer-events: none;
  line-height: 1.6;
}

/* 全局样式覆盖 */
:global(.custom-color-picker .el-color-predefine__color-selector) {
  border: 1px solid #dcdfe6;
}

/* 隐藏默认的输入框和按钮 */
:global(.custom-color-picker .el-color-dropdown__btns) {
  display: none !important;
}

:global(.custom-color-picker .el-color-dropdown__value) {
  display: none !important;
}

:global(.custom-color-picker .el-color-dropdown__btn) {
  display: none !important;
}

:global(.custom-color-picker .el-color-dropdown__main-wrapper) {
  padding-bottom: 72px;
}

.custom-rgb-panel {
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  padding: 10px;
  border-top: 1px solid #ebeef5;
  background: #fff;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.rgb-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.rgb-row .label {
  font-size: 12px;
  color: #606266;
  min-width: 60px;
}

:global(.custom-rgb-panel .el-input-number) {
  width: 110px !important;
}
</style>
