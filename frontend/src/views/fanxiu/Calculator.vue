<template>
  <div class="calculator-container">
    <div class="header-section">
      <h2 class="title">凡修兽魂计算器 v7.2</h2>
      <div class="header-actions">
        <el-button type="default" size="small" @click="exportJSON">📥 导出</el-button>
        <el-button type="default" size="small" @click="triggerImport">📤 导入</el-button>
        <el-button type="danger" size="small" @click="resetAll">🗑️ 重置</el-button>
        <input type="file" ref="fileInput" style="display:none" accept=".json" @change="importJSON" />
      </div>
    </div>

    <div class="layout-container">
      <!-- 左侧：配置与列表 -->
      <div class="left-panel">
        <!-- 配置栏 -->
        <div class="config-bar">
          <el-form :inline="true" size="small">
            <el-form-item label="行数">
              <el-input-number v-model="rows" :min="1" :max="10" @change="saveData" />
            </el-form-item>
            <el-form-item label="列数">
              <el-input-number v-model="cols" :min="1" :max="10" @change="saveData" />
            </el-form-item>
          </el-form>
        </div>

        <!-- 兽魂列表 -->
        <div class="list-header row-grid">
          <span class="col-idx">#</span>
          <span class="col-shape">兽魂形状 (支持拖拽/旋转)</span>
          <span class="col-score">评分</span>
          <span class="col-action">操作</span>
        </div>

        <div ref="sortableList" class="item-list">
          <div 
            v-for="(item, index) in items" 
            :key="item.id" 
            class="item-row row-grid"
            :data-id="item.id"
          >
            <div class="col-idx-content">
              <div class="drag-handle" title="按住拖动排序">≡</div>
              <div class="badge" :style="{ backgroundColor: getBadgeColor(index) }">{{ index + 1 }}</div>
            </div>

            <div class="shape-control-group">
              <!-- 形状选择器 -->
              <el-popover
                placement="bottom"
                :width="300"
                trigger="click"
              >
                <template #reference>
                  <div class="shape-trigger" v-if="!item.isCustomMode">
                    <canvas :ref="(el) => setCanvasRef(el, index)" width="20" height="20"></canvas>
                    <span class="shape-name">{{ getShapeName(item.code) }}</span>
                    <span class="dropdown-arrow">▼</span>
                  </div>
                </template>
                
                <!-- 形状调色板 -->
                <div class="palette-grid">
                  <div 
                    v-for="shape in BASE_SHAPES" 
                    :key="shape.n" 
                    class="palette-item" 
                    @click="selectShape(index, shape)"
                    :title="shape.n"
                  >
                    <canvas v-if="shape.v !== 'custom'" :ref="(el) => setPaletteCanvasRef(el, shape.v)" width="24" height="24"></canvas>
                    <span v-else style="font-size: 24px;">✏️</span>
                    <span>{{ shape.n.split('(')[0] }}</span>
                  </div>
                </div>
              </el-popover>

              <!-- 旋转/翻转按钮 -->
              <template v-if="!item.isCustomMode">
                <el-button size="small" circle @click="rotateShape(index)" title="顺时针旋转90°">↻</el-button>
                <el-button size="small" circle @click="flipShape(index)" title="左右翻转">⇄</el-button>
              </template>

              <!-- 自定义输入区域 -->
              <div v-else class="custom-area">
                <el-button size="small" @click="exitCustomMode(index)" title="返回预设模式">↩</el-button>
                <el-input 
                  type="textarea" 
                  v-model="item.customVal" 
                  :rows="2" 
                  placeholder=". \n..." 
                  class="custom-input"
                  @input="saveData"
                />
              </div>
            </div>

            <!-- 分数输入 -->
            <el-input-number 
              v-model="item.score" 
              :min="0" 
              :controls="false" 
              class="input-score" 
              placeholder="评分"
              @change="saveData"
            />
            
            <!-- 删除按钮 -->
            <el-button type="danger" link icon="Close" @click="delRow(index)"></el-button>
          </div>
        </div>

        <!-- 底部按钮 -->
        <div class="action-bar">
          <el-button class="btn-main btn-add" @click="addRow()">+ 添加兽魂</el-button>
          <el-button class="btn-main btn-calc" type="primary" :loading="calculating" @click="runCalc">⚡ 计算最优解</el-button>
        </div>
      </div>

      <!-- 右侧：结果展示 -->
      <div class="right-panel">
        <div class="result-sticky-container">
          <h3 class="section-title">计算结果</h3>
          
          <div v-if="result" class="result-content">
            <div class="stat-text">
              <span v-if="result.score === -1">❌ 无法放置</span>
              <span v-else>🏆 最高得分: <span style="color: #ef4444;">{{ result.score }}</span></span>
            </div>
            
            <div v-if="result.score !== -1" class="grid-wrapper">
              <div 
                class="backpack-grid" 
                :style="{ 
                  gridTemplateColumns: `repeat(${cols}, 40px)`, 
                  gridTemplateRows: `repeat(${rows}, 40px)`,
                  width: `${cols * 40 + (cols - 1) * 2}px`
                }"
              >
                <div 
                  v-for="(cell, idx) in resultGrid" 
                  :key="idx" 
                  class="cell" 
                  :class="{ active: cell }"
                  :style="{ background: cell ? cell.color : '' }"
                >
                  {{ cell ? cell.id : '' }}
                </div>
              </div>
            </div>
          </div>
          <div v-else class="empty-result">
            <el-empty description="点击“计算最优解”查看结果" :image-size="100"></el-empty>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, nextTick, computed } from 'vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import Sortable from 'sortablejs';

// --- Constants ---
const STORAGE_KEY = 'soul_backpack_v7_vue';
const COLORS = ['#ef4444', '#3b82f6', '#10b981', '#f59e0b', '#8b5cf6', '#ec4899', '#6366f1', '#06b6d4'];
const BASE_SHAPES = [
  { n: "田字(4)", v: "11;11" },
  { n: "长条(4)", v: "1111" },
  { n: "L型(4)", v: "10;10;11" },
  { n: "T型(4)", v: "111;010" },
  { n: "Z型(4)", v: "110;011" },
  { n: "拐角(3)", v: "11;10" },
  { n: "长条(3)", v: "111" },
  { n: "长条(2)", v: "11" },
  { n: "单点(1)", v: "1" },
  { n: "自定义", v: "custom" }
];

// --- State ---
interface SoulItem {
  id: string; // Unique ID for key
  code: string; // Shape code e.g. "11;11"
  customVal: string; // Custom string for custom mode
  score: number;
  isCustomMode: boolean;
}

const rows = ref(5);
const cols = ref(6);
const items = ref<SoulItem[]>([]);
const sortableList = ref<HTMLElement | null>(null);
const fileInput = ref<HTMLInputElement | null>(null);
const calculating = ref(false);

interface CalcResult {
  score: number;
  sol: Array<{ id: number; r: number; c: number; coords: Array<{ r: number; c: number }> }>;
}
const result = ref<CalcResult | null>(null);

// --- Lifecycle ---
onMounted(() => {
  if (!loadData()) {
    // Default data
    addRow("11;11", 100);
    addRow("10;10;11", 120);
  }
  
  // Init Sortable
  if (sortableList.value) {
    Sortable.create(sortableList.value, {
      animation: 150,
      handle: '.drag-handle',
      ghostClass: 'sortable-ghost',
      onEnd: () => {
        // We need to reorder items array based on DOM order?
        // Actually, Sortable modifies DOM. Vue's virtual DOM might get out of sync.
        // It's better to update the array.
        // But for simplicity with Vue, we usually use vuedraggable.
        // If we use raw Sortable, we should sync the array manually.
        const newIndices = Array.from(sortableList.value!.children).map((el: any) => el.dataset.id);
        const newItems = [];
        for (const id of newIndices) {
          const item = items.value.find(i => i.id === id);
          if (item) newItems.push(item);
        }
        items.value = newItems;
        saveData();
      }
    });
  }
  
  // Draw initial canvases
  nextTick(() => {
    items.value.forEach((item, idx) => {
      if (!item.isCustomMode) {
        drawShapeOnCanvas(idx, item.code);
      }
    });
    
    // Auto run calculation if data exists
    if (items.value.length > 0) {
      runCalc();
    }
  });
});

// --- Methods ---

// Canvas Helpers
const draw = (canvas: HTMLCanvasElement, str: string) => {
  const ctx = canvas.getContext('2d');
  if (!ctx) return;
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  
  let mat = str.split(/[\n;]/).map(r => r.split('').map(c => parseInt(c) || 0));
  if (!mat.length) return;
  
  let h = mat.length;
  let w = mat.reduce((m, r) => Math.max(m, r.length), 0);
  let s = Math.floor((canvas.width - 4) / Math.max(h, w));
  if (s < 1) s = 1;
  
  let ox = (canvas.width - w * s) / 2;
  let oy = (canvas.height - h * s) / 2;
  
  ctx.fillStyle = '#4b5563';
  for (let r = 0; r < h; r++) {
    for (let c = 0; c < (mat[r] || []).length; c++) {
      if (mat[r][c]) ctx.fillRect(ox + c * s, oy + r * s, s - 1, s - 1);
    }
  }
};

const setCanvasRef = (el: any, index: number) => {
  if (el) {
    draw(el as HTMLCanvasElement, items.value[index].code);
  }
};

const setPaletteCanvasRef = (el: any, code: string) => {
  if (el) {
    draw(el as HTMLCanvasElement, code);
  }
};

const drawShapeOnCanvas = (index: number, code: string) => {
  // This is tricky because refs are dynamic.
  // We rely on the template ref callback to draw.
  // But if we update data, we need to force redraw.
  // Vue's reactivity might handle the DOM update, but canvas content needs manual redraw.
  // We can force update by key or watch?
  // Actually, the ref callback is called on mount/update.
  // Let's just ensure we trigger reactivity.
  console.log('drawShapeOnCanvas', index, code);
};

// Data Management
const saveData = () => {
  const data = {
    rows: rows.value,
    cols: cols.value,
    items: items.value
  };
  localStorage.setItem(STORAGE_KEY, JSON.stringify(data));
};

const loadData = () => {
  const raw = localStorage.getItem(STORAGE_KEY);
  if (!raw) return false;
  try {
    const data = JSON.parse(raw);
    rows.value = data.rows || 5;
    cols.value = data.cols || 6;
    items.value = data.items || [];
    return true;
  } catch (e) {
    return false;
  }
};

const exportJSON = () => {
  saveData();
  const raw = localStorage.getItem(STORAGE_KEY);
  if (!raw) return;
  
  const blob = new Blob([raw], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  const date = new Date().toISOString().slice(0, 10);
  a.href = url;
  a.download = `凡修兽魂数据_${date}.json`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
};

const triggerImport = () => {
  fileInput.value?.click();
};

const importJSON = (event: Event) => {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  if (!file) return;

  const reader = new FileReader();
  reader.onload = (e) => {
    try {
      const json = e.target?.result as string;
      const data = JSON.parse(json);
      if (data && Array.isArray(data.items)) {
        ElMessageBox.confirm('导入将覆盖当前数据，确定吗？', '提示', {
          type: 'warning'
        }).then(() => {
          rows.value = parseInt(data.rows) || 5;
          cols.value = parseInt(data.cols) || 6;
          
          // Map legacy format
          items.value = data.items.map((item: any) => ({
            id: item.id || (crypto.randomUUID ? crypto.randomUUID() : 'id-' + Math.random().toString(36).substr(2, 9)),
            code: item.code || item.val || "11;11",
            customVal: item.customVal || item.custom || "",
            score: parseInt(item.score) || 0,
            isCustomMode: item.isCustomMode !== undefined ? item.isCustomMode : (item.val === 'custom' || (item.custom && item.custom !== ""))
          }));
          
          saveData();
          ElMessage.success('导入成功');
          // Force refresh
          setTimeout(() => location.reload(), 500);
        });
      } else {
        ElMessage.error('文件格式错误');
      }
    } catch (err) {
      console.error(err);
      ElMessage.error('导入失败');
    }
    input.value = '';
  };
  reader.readAsText(file);
};

const resetAll = () => {
  ElMessageBox.confirm('确定清空所有数据吗？', '警告', {
    type: 'warning'
  }).then(() => {
    localStorage.removeItem(STORAGE_KEY);
    items.value = [];
    rows.value = 5;
    cols.value = 6;
    result.value = null;
    ElMessage.success('已重置');
  });
};

// Item Operations
const addRow = (code = "11;11", score = 0) => {
  items.value.push({
    id: crypto.randomUUID ? crypto.randomUUID() : 'id-' + Math.random().toString(36).substr(2, 9),
    code,
    customVal: "",
    score,
    isCustomMode: false
  });
  saveData();
};

const delRow = (index: number) => {
  items.value.splice(index, 1);
  saveData();
};

const getBadgeColor = (index: number) => {
  return COLORS[index % COLORS.length];
};

const getShapeName = (code: string) => {
  const found = BASE_SHAPES.find(s => s.v === code);
  return found ? found.n : "自定义";
};

// Shape Logic
const selectShape = (index: number, shape: any) => {
  const item = items.value[index];
  if (shape.v === 'custom') {
    item.isCustomMode = true;
  } else {
    item.isCustomMode = false;
    item.code = shape.v;
  }
  saveData();
};

const exitCustomMode = (index: number) => {
  const item = items.value[index];
  item.isCustomMode = false;
  item.code = "11;11"; // Reset to default
  saveData();
};

const strToMat = (str: string) => str.split(';').map(line => line.split('').map(c => parseInt(c)));
const matToStr = (mat: number[][]) => mat.map(row => row.join('')).join(';');

const rotateShape = (index: number) => {
  const item = items.value[index];
  if (item.isCustomMode) return;
  
  const mat = strToMat(item.code);
  const R = mat.length;
  const C = mat[0].length;
  let newMat = Array(C).fill(0).map(() => Array(R).fill(0));
  
  for(let r=0; r<R; r++) {
    for(let c=0; c<C; c++) {
      newMat[c][R-1-r] = mat[r][c];
    }
  }
  item.code = matToStr(newMat);
  saveData();
};

const flipShape = (index: number) => {
  const item = items.value[index];
  if (item.isCustomMode) return;
  
  const mat = strToMat(item.code);
  const newMat = mat.map(r => r.reverse());
  item.code = matToStr(newMat);
  saveData();
};

// --- Calculation Logic ---
const parseShape = (s: string) => {
  let lines = s.trim().split(/[\n;]/);
  let coords: Array<{ r: number; c: number }> = [];
  lines.forEach((l, r) => {
    for (let c = 0; c < l.length; c++) {
      if (l[c] !== '.' && l[c] !== ' ' && l[c] !== '0') coords.push({ r, c });
    }
  });
  return { coords, h: lines.length, w: lines.reduce((m, l) => Math.max(m, l.length), 0) };
};

const runCalc = () => {
  const R = rows.value;
  const C = cols.value;
  
  // Prepare items
  const calcItems = items.value.map((item, idx) => {
    let raw = item.isCustomMode ? item.customVal : item.code;
    if (item.isCustomMode) {
       // normalize custom input
       raw = raw.split('\n').map(line => line.split('').map(c => (c === ' ' || c === '.' || c === '0') ? '0' : '1').join('')).join(';');
    }
    return {
      id: idx + 1, // use 1-based index for display
      score: item.score,
      p: parseShape(raw)
    };
  }).filter(i => i.p.coords.length > 0); // Filter empty shapes
  
  if (calcItems.length === 0) {
    ElMessage.warning('请添加有效数据');
    return;
  }
  
  calculating.value = true;
  result.value = null; // Reset result
  
  setTimeout(() => {
    try {
      // Sort by score desc
      calcItems.sort((a, b) => b.score - a.score);
      
      let best = { score: -1, sol: [] as any[] };
      let board = new Uint8Array(R * C);
      
      const solve = (idx: number, curScore: number, sol: any[]) => {
        // Pruning: Max possible remaining score
        let pot = curScore;
        for (let i = idx; i < calcItems.length; i++) pot += calcItems[i].score;
        if (pot <= best.score) return;
        
        if (idx >= calcItems.length) {
          if (curScore > best.score) {
            best.score = curScore;
            best.sol = JSON.parse(JSON.stringify(sol));
          }
          return;
        }
        
        let it = calcItems[idx];
        
        // Try to place
        for (let r = 0; r <= R - it.p.h; r++) {
          for (let c = 0; c <= C - it.p.w; c++) {
            let ok = true;
            for (let k = 0; k < it.p.coords.length; k++) {
              let co = it.p.coords[k];
              if (board[(r + co.r) * C + (c + co.c)]) { ok = false; break; }
            }
            
            if (ok) {
              // Place
              for (let k = 0; k < it.p.coords.length; k++) board[(r + it.p.coords[k].r) * C + (c + it.p.coords[k].c)] = 1;
              sol.push({ id: it.id, r, c, coords: it.p.coords });
              
              solve(idx + 1, curScore + it.score, sol);
              
              // Backtrack
              sol.pop();
              for (let k = 0; k < it.p.coords.length; k++) board[(r + it.p.coords[k].r) * C + (c + it.p.coords[k].c)] = 0;
            }
          }
        }
        
        // Option: Don't place this item
        solve(idx + 1, curScore, sol);
      };
      
      solve(0, 0, []);
      result.value = best;
      
      if (best.score !== -1) {
        ElMessage.success(`计算完成！最高分: ${best.score}`);
      } else {
        ElMessage.warning('无法放置');
      }
    } catch (err) {
      console.error(err);
      ElMessage.error('计算出错');
    } finally {
      calculating.value = false;
    }
  }, 50);
};

// Result Grid Computed
const resultGrid = computed(() => {
  if (!result.value || result.value.score === -1) return [];
  
  const R = rows.value;
  const C = cols.value;
  const grid = Array(R * C).fill(null);
  
  result.value.sol.forEach(s => {
    let color = COLORS[(s.id - 1) % COLORS.length];
    s.coords.forEach(co => {
      grid[(s.r + co.r) * C + (s.c + co.c)] = { id: s.id, color };
    });
  });
  
  return grid;
});

</script>

<style scoped>
.calculator-container {
  padding: 20px;
  width: fit-content;
}

.header-section {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 20px;
  border-bottom: 1px solid #ebeef5;
  padding-bottom: 15px;
}

.title {
  font-size: 24px;
  font-weight: bold;
  color: #303133;
  margin: 0;
}

.header-actions {
  display: flex;
  gap: 10px;
}

.layout-container {
  display: flex;
  gap: 20px;
  align-items: flex-start;
}

.left-panel {
  width: fit-content;
  min-width: 0;
}

.right-panel {
  width: 380px;
  flex-shrink: 0;
}

.result-sticky-container {
  position: sticky;
  top: 20px;
  background: #fff;
  padding: 20px;
  border-radius: 8px;
  border: 1px solid #ebeef5;
  box-shadow: 0 2px 12px 0 rgba(0,0,0,0.05);
}

.section-title {
  margin-top: 0;
  margin-bottom: 20px;
  font-size: 18px;
  color: #303133;
  border-left: 4px solid #409eff;
  padding-left: 10px;
}

.result-content {
  display: flex;
  flex-direction: column;
  align-items: center;
}

.config-bar {
  background: #f0f2f5;
  padding: 15px;
  border-radius: 8px;
  margin-bottom: 20px;
}

.row-grid {
  display: grid;
  grid-template-columns: 70px 340px 120px 50px;
  align-items: center;
  gap: 10px;
}

.list-header {
  padding: 0 10px 10px;
  font-size: 12px;
  color: #909399;
  font-weight: bold;
  border-bottom: 1px solid #ebeef5;
  margin-bottom: 10px;
}

.item-list {
  min-height: 50px;
}

.item-row {
  padding: 10px;
  background: #fff;
  border-bottom: 1px solid #f0f0f0;
  margin-bottom: 5px;
  border-radius: 4px;
}

.item-row:hover {
  background: #fafafa;
}

.col-idx-content {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
}

.col-idx {
  text-align: center;
}

.col-score, .col-action {
  text-align: center;
}

.drag-handle {
  cursor: grab;
  color: #909399;
  font-size: 20px;
  padding: 0 5px;
}

.badge {
  width: 24px;
  height: 24px;
  border-radius: 4px;
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  font-weight: bold;
}

.shape-control-group {
  display: flex;
  align-items: center;
  gap: 10px;
}

.shape-trigger {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 5px 10px;
  border: 1px solid #dcdfe6;
  border-radius: 4px;
  cursor: pointer;
  min-width: 100px;
  background: #fff;
}

.shape-trigger:hover {
  border-color: #409eff;
}

.palette-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 10px;
  padding: 10px;
}

.palette-item {
  display: flex;
  flex-direction: column;
  align-items: center;
  border: 1px solid #eee;
  padding: 5px;
  border-radius: 4px;
  cursor: pointer;
}

.palette-item:hover {
  background: #ecf5ff;
  border-color: #409eff;
}

.input-score {
  width: 100%;
}

.custom-area {
  display: flex;
  align-items: center;
  gap: 5px;
  flex: 1;
}

.custom-input {
  font-family: monospace;
}

.action-bar {
  margin-top: 20px;
  display: flex;
  gap: 10px;
}

.btn-main {
  flex: 1;
}

.result-panel {
  margin-top: 30px;
  text-align: center;
}

.stat-text {
  font-size: 1.5rem;
  font-weight: bold;
  margin-bottom: 20px;
}

.grid-wrapper {
  background: #1f2937;
  padding: 15px;
  border-radius: 8px;
  display: inline-block;
}

.backpack-grid {
  display: grid;
  gap: 2px;
  background: #1f2937;
}

.cell {
  width: 40px;
  height: 40px;
  background: #374151;
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: bold;
  color: #6b7280;
  border-radius: 2px;
}

.cell.active {
  color: #fff;
  box-shadow: inset 0 0 0 1px rgba(0,0,0,0.1);
}
</style>
