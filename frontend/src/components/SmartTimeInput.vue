<template>
  <div class="smart-time-input">
    <el-input
      v-model="displayValue"
      :placeholder="placeholder"
      :size="size"
      :style="inputStyle"
      @blur="handleCommit"
      @keydown.enter="handleCommit"
    />
    <el-tooltip effect="light" placement="top">
      <template #content>
        <div style="line-height: 1.6; max-width: 200px;">
          <b>快捷时间输入:</b><br/>
          支持简写格式，自动补全:<br/>
          - <b>14</b> &rarr; 14:00:00<br/>
          - <b>1430</b> &rarr; 14:30:00<br/>
          - <b>143015</b> &rarr; 14:30:15<br/>
          - <b>930</b> &rarr; 09:30:00<br/>
          (输入回车或焦点离开生效)
        </div>
      </template>
      <el-icon class="help-icon"><QuestionFilled /></el-icon>
    </el-tooltip>
  </div>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue';
import { QuestionFilled } from '@element-plus/icons-vue';

const props = defineProps({
  modelValue: {
    type: String,
    default: ''
  },
  placeholder: {
    type: String,
    default: '时间'
  },
  size: {
    type: String as any,
    default: 'small'
  },
  inputStyle: {
    type: [Object, String],
    default: () => ({ width: '100px' })
  }
});

const emit = defineEmits(['update:modelValue', 'change']);

const displayValue = ref(props.modelValue);

watch(() => props.modelValue, (val) => {
  displayValue.value = val;
});

const handleCommit = () => {
  const val = displayValue.value;
  if (!val) {
      // Allow empty? If so, emit empty.
      // But typically we want a valid time. 
      // If empty, let's just emit empty and let parent handle it.
      emit('update:modelValue', '');
      emit('change', '');
      return;
  }

  const expanded = smartTimeExpand(val);
  if (expanded) {
    displayValue.value = expanded;
    emit('update:modelValue', expanded);
    emit('change', expanded);
  } else {
    // If invalid, revert to prop value (reset)
    displayValue.value = props.modelValue;
  }
};

const smartTimeExpand = (input: string): string | null => {
    input = input.trim();
    if (!input) return null;
    
    // Replace Chinese colon
    let s = input.replace(/：/g, ':');
    
    // Check if it's standard HH:mm or HH:mm:ss
    if (s.includes(':')) {
        const parts = s.split(':');
        if (parts.length === 2) {
             // 14:30 -> 14:30:00
             const h = parseInt(parts[0]);
             const m = parseInt(parts[1]);
             if (h >= 0 && h <= 23 && m >= 0 && m <= 59) {
                 return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:00`;
             }
        } else if (parts.length === 3) {
             // 14:30:15 -> 14:30:15 (Normalization)
             const h = parseInt(parts[0]);
             const m = parseInt(parts[1]);
             const sec = parseInt(parts[2]);
             if (h >= 0 && h <= 23 && m >= 0 && m <= 59 && sec >= 0 && sec <= 59) {
                 return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}`;
             }
        }
        return null; // Invalid colon format
    }
    
    // Pure numbers
    if (/^\d+$/.test(s)) {
        const len = s.length;
        let h = -1, m = 0, sec = 0;
        
        if (len >= 1 && len <= 2) {
            // H or HH -> HH:00:00
            h = parseInt(s);
        } else if (len === 3) {
            // Hmm -> H:mm:00 (e.g. 930 -> 09:30:00)
            h = parseInt(s.substring(0, 1));
            m = parseInt(s.substring(1));
        } else if (len === 4) {
            // HHmm -> HH:mm:00
            h = parseInt(s.substring(0, 2));
            m = parseInt(s.substring(2));
        } else if (len === 5) {
            // Hmmss -> H:mm:ss
            h = parseInt(s.substring(0, 1));
            m = parseInt(s.substring(1, 3));
            sec = parseInt(s.substring(3));
        } else if (len === 6) {
            // HHmmss -> HH:mm:ss
            h = parseInt(s.substring(0, 2));
            m = parseInt(s.substring(2, 4));
            sec = parseInt(s.substring(4));
        }
        
        if (h >= 0 && h <= 23 && m >= 0 && m <= 59 && sec >= 0 && sec <= 59) {
            return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}`;
        }
    }

    return null;
};
</script>

<style scoped>
.smart-time-input {
    display: inline-flex;
    align-items: center;
}
.help-icon {
    margin-left: 5px;
    font-size: 14px;
    color: #909399;
    cursor: help;
}
</style>
