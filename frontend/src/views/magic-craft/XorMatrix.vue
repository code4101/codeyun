<script setup lang="ts">
import { ref, reactive, watch } from 'vue';
import { ElMessage } from 'element-plus';

const rows = ref(3);
const cols = ref(3);

const grid = ref<number[][]>([]);
const solution = ref<number[][] | null>(null);

const initGrid = () => {
  const newGrid = [];
  for (let r = 0; r < rows.value; r++) {
    const row = [];
    for (let c = 0; c < cols.value; c++) {
      row.push(0);
    }
    newGrid.push(row);
  }
  grid.value = newGrid;
  solution.value = null;
};

// Initialize on mount
initGrid();

const toggleCell = (r: number, c: number) => {
  grid.value[r][c] = 1 - grid.value[r][c];
  solution.value = null; // Clear solution on modification
};

const reset = () => {
  initGrid();
};

const solve = () => {
  const R = rows.value;
  const C = cols.value;
  const N = R * C;
  
  // 1. Build Augmented Matrix [A | b]
  // Size: N rows, N+1 columns
  const mat: number[][] = [];
  
  for (let r = 0; r < R; r++) {
    for (let c = 0; c < C; c++) {
      const i = r * C + c; // Current cell index (equation index)
      const rowArr = new Array(N + 1).fill(0);
      
      // Fill A part (columns represent presses)
      for (let pr = 0; pr < R; pr++) {
        for (let pc = 0; pc < C; pc++) {
          const j = pr * C + pc; // Press index
          // Check if pressing j affects i (Manhattan distance <= 1)
          // Wait, Manhattan distance is correct for grid graph? Yes.
          // Cell (r,c) is affected by (pr,pc) if |r-pr| + |c-pc| <= 1
          const dist = Math.abs(r - pr) + Math.abs(c - pc);
          if (dist <= 1) {
            rowArr[j] = 1;
          }
        }
      }
      
      // Fill b part (last column)
      // We want final state to be 1.
      // Final = Initial XOR Change
      // 1 = Initial XOR Change => Change = 1 XOR Initial
      // So if Initial is 0, we need change (1). If 1, we need change (0).
      // b[i] = 1 - grid[r][c]
      rowArr[N] = 1 - grid.value[r][c];
      
      mat.push(rowArr);
    }
  }

  // 2. Gaussian Elimination (Forward)
  let pivotRow = 0;
  const pivots = new Array(N).fill(-1); // pivots[col] = row
  const freeVars: number[] = [];
  
  for (let col = 0; col < N && pivotRow < N; col++) {
    // Find pivot
    let sel = -1;
    for (let row = pivotRow; row < N; row++) {
      if (mat[row][col] === 1) {
        sel = row;
        break;
      }
    }

    if (sel === -1) {
      freeVars.push(col);
      continue;
    }

    // Swap rows
    [mat[pivotRow], mat[sel]] = [mat[sel], mat[pivotRow]];
    pivots[col] = pivotRow;

    // Eliminate other rows
    for (let row = 0; row < N; row++) {
      if (row !== pivotRow && mat[row][col] === 1) {
        for (let k = col; k <= N; k++) {
          mat[row][k] ^= mat[pivotRow][k];
        }
      }
    }
    pivotRow++;
  }

  // Collect remaining columns as free variables if any
  for (let col = 0; col < N; col++) {
      if (pivots[col] === -1 && !freeVars.includes(col)) {
          freeVars.push(col);
      }
  }

  // 3. Check consistency
  for (let row = pivotRow; row < N; row++) {
    if (mat[row][N] === 1) {
      ElMessage.warning('当前状态无解，请检查输入');
      solution.value = null;
      return;
    }
  }

  // 4. Find Minimum Weight Solution
  // Iterate through all combinations of free variables
  const k = freeVars.length;
  // Limit complexity: if too many free vars, just take 0 assignment or warn?
  // 3x3 to 5x5 usually small k.
  // Safety break
  if (k > 16) {
      ElMessage.warning('自由变量过多，仅计算基础解');
  }
  
  const limit = Math.min(1 << k, 1 << 16); 
  
  let bestX: number[] | null = null;
  let minWeight = N + 1;

  for (let i = 0; i < limit; i++) {
    const currentX = new Array(N).fill(0);
    
    // Set free variables based on i
    for (let bit = 0; bit < k; bit++) {
      if ((i >> bit) & 1) {
        currentX[freeVars[bit]] = 1;
      }
    }
    
    // Back substitution to solve for pivot variables
    for (let col = N - 1; col >= 0; col--) {
      const r = pivots[col];
      if (r !== -1) {
        let sum = mat[r][N];
        for (let j = col + 1; j < N; j++) {
           if (mat[r][j] === 1) {
             sum ^= currentX[j];
           }
        }
        currentX[col] = sum;
      }
    }
    
    // Calculate weight
    const weight = currentX.reduce((a, b) => a + b, 0);
    if (weight < minWeight) {
      minWeight = weight;
      bestX = currentX;
    }
  }

  // 5. Convert bestX to solution format
  if (bestX) {
    const sol: number[][] = [];
    for (let i = 0; i < N; i++) {
      if (bestX[i] === 1) {
        sol.push([Math.floor(i / C), i % C]);
      }
    }
    solution.value = sol;
    ElMessage.success(`已找到最优解法！需点击 ${sol.length} 次`);
  } else {
      // Should not happen if consistency check passed
      ElMessage.error('计算出错');
  }
};

const isSolution = (r: number, c: number) => {
  if (!solution.value) return false;
  return solution.value.some(p => p[0] === r && p[1] === c);
};

// Watch for dimension changes
watch([rows, cols], () => {
    // Optional: preserve data if possible? Or just reset.
    // Reset is safer.
    initGrid();
});
</script>

<template>
  <div class="xor-matrix-container">
    <h1>魔法工艺 - 点灯解谜计算器</h1>
    <div class="instructions">
      <p>1. 设置矩阵大小（默认3x3），点击格子调整为当前游戏状态。</p>
      <p>2. 点击“计算解法”按钮。</p>
      <p>3. 按照方块上出现的“点击”标记，在游戏中操作。</p>
    </div>
    
    <div class="settings-bar">
        <span class="label">行数:</span>
        <el-input-number v-model="rows" :min="1" :max="10" size="small" />
        <span class="label">列数:</span>
        <el-input-number v-model="cols" :min="1" :max="10" size="small" />
        <el-button type="warning" size="small" @click="reset">重置矩阵</el-button>
    </div>

    <div class="grid" :style="{ '--cols': cols }">
      <div v-for="(row, r) in grid" :key="r" class="row">
        <div 
          v-for="(cell, c) in row" 
          :key="c" 
          class="cell" 
          :class="{ 'on': cell === 1, 'solution-mark': isSolution(r, c) }"
          @click="toggleCell(r, c)"
        >
          <div v-if="isSolution(r, c)" class="marker">点击</div>
        </div>
      </div>
    </div>

    <div class="controls">
      <el-button type="primary" size="large" @click="solve">计算最优解法</el-button>
    </div>
    
    <div v-if="solution" class="solution-text">
       <h3>需要点击次数: {{ solution.length }}</h3>
    </div>
  </div>
</template>

<style scoped>
.xor-matrix-container {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 20px;
  background-color: #f9f9f9;
  border-radius: 8px;
  min-height: 80vh;
}

h1 {
  color: #2c3e50;
  margin-bottom: 10px;
}

.instructions {
  background-color: #fff;
  padding: 15px;
  border-radius: 6px;
  box-shadow: 0 2px 8px rgba(0,0,0,0.05);
  margin-bottom: 20px;
  max-width: 600px;
  width: 100%;
}

.instructions p {
  color: #555;
  margin: 5px 0;
  font-size: 14px;
  text-align: left;
}

.settings-bar {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 20px;
    background: #fff;
    padding: 10px 20px;
    border-radius: 8px;
    box-shadow: 0 2px 4px rgba(0,0,0,0.05);
}

.label {
    font-size: 14px;
    color: #606266;
}

.grid {
  margin: 20px 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
  background-color: #eee;
  padding: 15px;
  border-radius: 8px;
}

.row {
  display: flex;
  gap: 10px;
}

.cell {
  width: 60px;
  height: 60px;
  border: 2px solid #bdc3c7;
  background-color: #34495e; /* Dark Blue/Grey */
  cursor: pointer;
  display: flex;
  justify-content: center;
  align-items: center;
  transition: all 0.3s cubic-bezier(0.25, 0.8, 0.25, 1);
  position: relative;
  border-radius: 4px;
}

.cell.on {
  background-color: #f1c40f; /* Yellow */
  box-shadow: 0 0 15px rgba(241, 196, 15, 0.6);
  border-color: #f39c12;
}

.cell:hover {
  transform: scale(1.05);
  z-index: 1;
}

.cell.solution-mark {
  border-color: #e74c3c; /* Red border for solution */
  border-width: 4px;
  animation: pulse 1.5s infinite;
}

.marker {
  color: #e74c3c;
  font-weight: bold;
  font-size: 12px;
  background-color: rgba(255, 255, 255, 0.9);
  padding: 2px 4px;
  border-radius: 4px;
  pointer-events: none;
  white-space: nowrap;
  box-shadow: 0 2px 4px rgba(0,0,0,0.2);
}

.controls {
  display: flex;
  gap: 15px;
  margin-top: 20px;
}

@keyframes pulse {
  0% { box-shadow: 0 0 0 0 rgba(231, 76, 60, 0.7); }
  70% { box-shadow: 0 0 0 10px rgba(231, 76, 60, 0); }
  100% { box-shadow: 0 0 0 0 rgba(231, 76, 60, 0); }
}
</style>
