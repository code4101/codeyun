<template>
  <div class="editor-container" @click="handleContainerClick">
    <Toolbar
      v-if="!readOnly"
      style="border-bottom: 1px solid #ccc"
      :editor="editorRef"
      :defaultConfig="toolbarConfig"
      :mode="mode"
    />
    <!-- Extra Toolbar Items Slot -->
    <div v-if="!readOnly && ($slots.extra || enableImageMerge)" class="editor-toolbar-extra">
      <el-button
        v-if="enableImageMerge"
        size="small"
        type="success"
        plain
        :icon="Picture"
        @click="openImageMergeDialog"
      >
        拼接图片
      </el-button>
      <slot name="extra"></slot>
    </div>
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

  <el-dialog
    v-model="imageMergeVisible"
    title="图片拼接"
    width="800px"
    append-to-body
    :close-on-click-modal="false"
  >
    <div class="merge-dialog-content">
      <el-alert
        title="请先在编辑器里框选包含图片的区域，仅对选区内图片拼接。"
        type="info"
        show-icon
        :closable="false"
        style="margin-bottom: 15px;"
      />

      <div class="merge-settings">
        <span class="label">垂直间隙 (px):</span>
        <el-input-number v-model="mergeGap" :min="0" :max="100" size="small" />
        <el-button type="primary" size="small" @click="detectAndMergeImages" :loading="merging" style="margin-left: 15px;">
          开始拼接
        </el-button>
      </div>

      <div v-if="detectedImages.length > 0" class="image-preview-list">
        <p>检测到 {{ detectedImages.length }} 张图片:</p>
        <div class="preview-scroll">
          <img v-for="(src, idx) in detectedImages" :key="idx" :src="src" class="preview-thumb" />
        </div>
      </div>

      <div v-if="mergedImageResult" class="merge-result">
        <p>拼接结果预览:</p>
        <div class="result-container">
          <img :src="mergedImageResult" class="result-img" />
        </div>
      </div>
    </div>
    <template #footer>
      <span class="dialog-footer">
        <el-button @click="imageMergeVisible = false">取消</el-button>
        <el-button type="primary" @click="confirmInsertMergedImage" :disabled="!mergedImageResult">
          插入到编辑器
        </el-button>
      </span>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import '@wangeditor/editor/dist/css/style.css' // 引入 css
import { onBeforeUnmount, ref, shallowRef, onMounted, watch, toRef } from 'vue'
import { Editor, Toolbar } from '@wangeditor/editor-for-vue'
import { IEditorConfig, type IDomEditor, SlateEditor, SlateElement } from '@wangeditor/editor'
import { Picture } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { mergeImagesToPngDataUrl } from '@/utils/imageMerge'
import { registerWangEditorPlugins } from '@/utils/wangEditorPlugins'

// 注册 WangEditor 插件
registerWangEditorPlugins()

const props = defineProps({
  modelValue: {
    type: String,
    default: ''
  },
  mode: {
    type: String,
    default: 'default' // 'default' or 'simple'
  },
  enableImageMerge: {
    type: Boolean,
    default: true
  },
  readOnly: {
    type: Boolean,
    default: false
  }
})

const emit = defineEmits(['update:modelValue', 'change'])

// 编辑器实例，必须用 shallowRef
const editorRef = shallowRef()

// 内容 HTML，直接使用 props 初始化
const valueHtml = ref(props.modelValue)

const enableImageMerge = toRef(props, 'enableImageMerge')
const readOnly = toRef(props, 'readOnly')
const imageMergeVisible = ref(false)
const mergeGap = ref(0)
const detectedImages = ref<string[]>([])
const mergedImageResult = ref('')
const merging = ref(false)

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

watch(readOnly, (val) => {
    const editor = editorRef.value
    if (editor == null) return
    if (val) {
        editor.disable()
    } else {
        editor.enable()
    }
})

const toolbarConfig = {}
const editorConfig: any = {
    placeholder: '请输入内容...',
    readOnly: props.readOnly,
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
    },
    hoverbarKeys: {
        image: {
            menuKeys: [
                'imageWidth30',
                'imageWidth50',
                'imageWidth100',
                '|',
                'image-merge-button', // 自定义拼接按钮
                'editImage',
                'viewImageLink',
                'deleteImage'
            ]
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

    // 监听自定义菜单事件
    editor.on('image-merge-click', () => {
        openImageMergeDialog()
    })
    
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

const getSelectedImageSrcList = (): string[] => {
    const editor = editorRef.value as IDomEditor | undefined
    if (!editor?.selection) return []
    const srcSet = new Set<string>()
    try {
        for (const [node] of SlateEditor.nodes(editor, {
            at: editor.selection,
            match: n => SlateElement.isElement(n) && (n as any).type === 'image',
        })) {
            const src = (node as any).src || (node as any).url || (node as any).href
            if (typeof src === 'string' && src) srcSet.add(src)
        }
    } catch {
        return []
    }
    return Array.from(srcSet)
}

const openImageMergeDialog = () => {
    imageMergeVisible.value = true
    detectedImages.value = []
    mergedImageResult.value = ''
    detectedImages.value = getSelectedImageSrcList()
    if (detectedImages.value.length === 0) {
        ElMessage.info('请先在编辑器中框选包含图片的区域')
    }
}

const detectAndMergeImages = async () => {
    if (detectedImages.value.length < 2) {
        ElMessage.warning('至少需要 2 张图片才能拼接')
        return
    }
    merging.value = true
    try {
        mergedImageResult.value = await mergeImagesToPngDataUrl(detectedImages.value, mergeGap.value)
        if (!mergedImageResult.value) {
            ElMessage.error('拼接失败')
        }
    } catch (e) {
        console.error(e)
        ElMessage.error('拼接失败，可能是跨域图片限制')
    } finally {
        merging.value = false
    }
}

const confirmInsertMergedImage = () => {
    if (!mergedImageResult.value) return
    const editor = editorRef.value
    if (!editor) return
    const html = `<p><br></p><img src="${mergedImageResult.value}" style="max-width:100%;" /><p><br></p>`
    editor.dangerouslyInsertHtml(html)
    const nextHtml = editor.getHtml()
    valueHtml.value = nextHtml
    emit('update:modelValue', nextHtml)
    emit('change', nextHtml)
    imageMergeVisible.value = false
    ElMessage.success('已插入拼接后的图片')
}
</script>

<style scoped>
.editor-container {
    border: 1px solid #ccc;
    z-index: 100; /* 按需调整 */
    height: auto; /* Ensure it grows */
    min-height: 320px;
    cursor: text; /* 提示可编辑 */
}

/* 确保编辑器区域填满容器，点击空白处也能触发编辑器焦点 */
:deep(.w-e-text-container) {
    height: auto !important; /* 让它自适应内容高度 */
    min-height: 320px !important;
    background-color: transparent !important;
}

.editor-toolbar-extra {
    padding: 8px 15px;
    background-color: #fcfcfc;
    border-bottom: 1px solid #eee;
    display: flex;
    align-items: center;
    gap: 10px;
}

.merge-settings {
    display: flex;
    align-items: center;
}

.merge-settings .label {
    font-size: 12px;
    color: #606266;
    margin-right: 8px;
}

.image-preview-list {
    margin-top: 15px;
}

.preview-scroll {
    display: flex;
    gap: 10px;
    overflow-x: auto;
    padding: 10px 0;
}

.preview-thumb {
    max-height: 80px;
    border: 1px solid #ddd;
    border-radius: 4px;
}

.merge-result {
    margin-top: 15px;
}

.result-container {
    max-height: 350px;
    overflow: auto;
    border: 1px solid #eee;
    padding: 10px;
}

.result-img {
    max-width: 100%;
    height: auto;
    display: block;
}

:deep(.w-e-scroll) {
    overflow-y: hidden !important; /* 禁用内部滚动，让外部容器处理 */
}

/* 确保图片自适应宽度，防止撑破容器 */
:deep(.w-e-text-container img) {
    max-width: 100% !important;
    height: auto !important;
    display: block;
    margin: 10px 0;
}
</style>
