<template>
  <div class="editor-container" @click="handleContainerClick">
    <Toolbar
      style="border-bottom: 1px solid #ccc"
      :editor="editorRef"
      :defaultConfig="toolbarConfig"
      :mode="mode"
    />
    <Editor
      class="editor-content-area"
      style="height: auto; min-height: 500px; overflow: visible;"
      v-model="valueHtml"
      :defaultConfig="editorConfig"
      :mode="mode"
      @onCreated="handleCreated"
      @onChange="handleChange"
    />
  </div>
</template>

<script setup lang="ts">
import '@wangeditor/editor/dist/css/style.css' // 引入 css
import { onBeforeUnmount, ref, shallowRef, onMounted, watch } from 'vue'
import { Editor, Toolbar } from '@wangeditor/editor-for-vue'
import { IEditorConfig } from '@wangeditor/editor'

const props = defineProps({
  modelValue: {
    type: String,
    default: ''
  },
  mode: {
    type: String,
    default: 'default' // 'default' or 'simple'
  }
})

const emit = defineEmits(['update:modelValue', 'change'])

// 编辑器实例，必须用 shallowRef
const editorRef = shallowRef()

// 内容 HTML，直接使用 props 初始化
const valueHtml = ref(props.modelValue)

// 模拟 ajax 异步获取内容
onMounted(() => {
    valueHtml.value = props.modelValue
})

// 监听 props 变化
watch(() => props.modelValue, (newVal) => {
    // 只有当传入的新值与当前编辑器内容确实不同，且新值不为空时才更新
    // 或者当新值为空字符串，且编辑器不为空时更新（处理清空操作）
    if (newVal !== valueHtml.value) {
        valueHtml.value = newVal
    }
})

const toolbarConfig = {}
const editorConfig: Partial<IEditorConfig> = {
    placeholder: '请输入内容...',
    MENU_CONF: {
        uploadImage: {
            server: '/api/upload/image', // Upload API endpoint
            fieldName: 'file',
            maxFileSize: 10 * 1024 * 1024, // 10M
            base64LimitSize: 5 * 1024, // Images smaller than 5kb insert as base64, larger upload
            headers: {
                Authorization: `Bearer ${localStorage.getItem('token')}` // Add token
            }
        }
    }
}

// 组件销毁时，也及时销毁编辑器
onBeforeUnmount(() => {
    const editor = editorRef.value
    if (editor == null) return
    editor.destroy()
})

const handleCreated = (editor: any) => {
    editorRef.value = editor // 记录 editor 实例，重要！
    
    // Auto focus on creation if it's an empty note to improve UX
    if (!valueHtml.value || valueHtml.value === '<p><br></p>') {
        setTimeout(() => {
            if (editorRef.value) {
                editorRef.value.focus()
            }
        }, 100)
    }
}

const handleContainerClick = (e: MouseEvent) => {
    const editor = editorRef.value
    if (editor == null) return
    
    // Only focus if the editor isn't already focused and we're clicking the container/empty area
    const target = e.target as HTMLElement
    // Check if we're clicking on the container itself or the editor's empty space
    if (!editor.isFocused()) {
        const isContainer = target.classList.contains('editor-container')
        const isTextContainer = target.closest('.w-e-text-container')
        const isEditableArea = target.closest('.w-e-text')
        
        // If we click the outer container or the text container background (but not the editable text itself)
        // force focus. Note: if we click the editable text itself, wangEditor handles it.
        if (isContainer || isTextContainer) {
            editor.focus()
        }
    }
}

const handleChange = (editor: any) => {
    emit('update:modelValue', editor.getHtml())
    emit('change', editor.getHtml())
}
</script>

<style scoped>
.editor-container {
    border: 1px solid #ccc;
    z-index: 100; /* 按需调整 */
    height: auto; /* Ensure it grows */
    cursor: text; /* 提示可编辑 */
}

/* 确保编辑器区域填满容器，点击空白处也能触发编辑器焦点 */
:deep(.w-e-text-container) {
    min-height: inherit;
    background-color: transparent !important;
}

:deep(.w-e-scroll) {
    overflow-y: hidden !important; /* 让外部容器滚动，或者编辑器自适应高度 */
}
</style>
