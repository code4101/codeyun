<template>
  <div class="editor-container" :class="`is-${layout}`" :style="editorStyle" @click="handleContainerClick">
    <div v-if="showToolbar && !readOnly" class="editor-toolbar-row">
      <Toolbar
        class="editor-toolbar"
        :editor="editorRef"
        :defaultConfig="toolbarConfig"
        :mode="mode"
      />
      <el-tooltip content="上传附件并插入链接" placement="bottom">
        <el-button
          size="small"
          class="attachment-upload-button"
          :icon="Upload"
          :loading="attachmentUploading"
          @click.stop="openAttachmentPicker"
        >
          附件
        </el-button>
      </el-tooltip>
      <input
        ref="attachmentInputRef"
        class="attachment-input"
        type="file"
        multiple
        @change="handleAttachmentInputChange"
        @click.stop
      />
    </div>
    <!-- Extra Toolbar Items Slot -->
    <div v-if="showToolbar && !readOnly && $slots.extra" class="editor-toolbar-extra">
      <slot name="extra"></slot>
    </div>
    <Editor
      class="editor-content-area"
      :class="`is-${layout}`"
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
        class="merge-alert"
      />

      <div class="merge-settings">
        <span class="label">垂直间隙 (px):</span>
        <el-input-number v-model="mergeGap" :min="0" :max="100" size="small" />
        <el-button type="primary" size="small" class="merge-button" @click="detectAndMergeImages" :loading="merging">
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
import { computed, onBeforeUnmount, ref, shallowRef, onMounted, watch, toRef } from 'vue'
import { Editor, Toolbar } from '@wangeditor/editor-for-vue'
import { type IDomEditor, SlateEditor, SlateElement } from '@wangeditor/editor'
import { ElMessage } from 'element-plus'
import { Upload } from '@element-plus/icons-vue'
import api from '@/api'
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
  readOnly: {
    type: Boolean,
    default: false
  },
  showToolbar: {
    type: Boolean,
    default: true
  },
  autoFocusOnEmpty: {
    type: Boolean,
    default: true
  },
  minHeight: {
    type: Number,
    default: undefined
  },
  layout: {
    type: String,
    default: 'fill' // 'fill' for split panes, 'flow' for dialogs/standalone blocks
  }
})

const emit = defineEmits(['update:modelValue', 'change'])

// 编辑器实例，必须用 shallowRef
const editorRef = shallowRef()

// 内容 HTML，直接使用 props 初始化
const valueHtml = ref(props.modelValue)

const readOnly = toRef(props, 'readOnly')
const showToolbar = toRef(props, 'showToolbar')
const imageMergeVisible = ref(false)
const mergeGap = ref(0)
const detectedImages = ref<string[]>([])
const mergedImageResult = ref('')
const merging = ref(false)
const attachmentInputRef = ref<HTMLInputElement | null>(null)
const attachmentUploading = ref(false)

const MAX_ATTACHMENT_UPLOAD_BYTES = 100 * 1024 * 1024
const ATTACHMENT_UPLOAD_TIMEOUT_MS = 120 * 1000

const editorStyle = computed(() => {
    if (props.layout !== 'flow' || typeof props.minHeight !== 'number' || props.minHeight <= 0) {
        return {}
    }
    const minHeight = `${props.minHeight}px`
    return {
        '--editor-flow-min-height': minHeight,
        '--editor-content-min-height': minHeight,
        '--editor-text-min-height': minHeight,
    }
})

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

interface UploadedImageData {
  url?: string
  alt?: string
  href?: string
}

interface UploadImageResponse {
  errno?: number
  message?: string
  data?: UploadedImageData | UploadedImageData[]
}

interface UploadedAttachmentData {
  url?: string
  filename?: string
  original_filename?: string
  name?: string
  content_type?: string
  size?: number
}

interface UploadAttachmentResponse {
  errno?: number
  message?: string
  data?: UploadedAttachmentData
}

const HTML_ESCAPE_MAP: Record<string, string> = {
  '&': '&amp;',
  '<': '&lt;',
  '>': '&gt;',
  '"': '&quot;',
  "'": '&#39;',
}

const escapeHtml = (value: string) => value.replace(/[&<>"']/g, char => HTML_ESCAPE_MAP[char] || char)

const formatBytes = (bytes: number) => {
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB']
  let value = bytes
  let unitIndex = 0
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024
    unitIndex += 1
  }
  return `${value >= 10 || unitIndex === 0 ? Math.round(value) : value.toFixed(1)} ${units[unitIndex]}`
}

const uploadEditorImage = async (
  file: File,
  insertFn: (src: string, alt: string, href: string) => void
) => {
  const formData = new FormData()
  formData.append('file', file)

  try {
    const response = await api.post<UploadImageResponse>('/upload/image', formData)
    const { errno = 1, data, message } = response.data || {}

    if (errno !== 0 || !data) {
      throw new Error(message || '图片上传失败')
    }

    const uploadedImages = Array.isArray(data) ? data : [data]
    for (const image of uploadedImages) {
      if (!image?.url) continue
      insertFn(image.url, image.alt || file.name, image.href || image.url)
    }

    if (!uploadedImages.some(image => image?.url)) {
      throw new Error('图片上传成功，但未返回可用地址')
    }
  } catch (error: any) {
    const message = error?.response?.data?.detail || error?.message || '图片上传失败'
    ElMessage.error(message)
    throw error
  }
}

const openAttachmentPicker = () => {
  if (attachmentUploading.value) return
  attachmentInputRef.value?.click()
}

const uploadAttachmentFile = async (file: File): Promise<UploadedAttachmentData> => {
  if (file.size > MAX_ATTACHMENT_UPLOAD_BYTES) {
    throw new Error(`${file.name} 超过 ${formatBytes(MAX_ATTACHMENT_UPLOAD_BYTES)}`)
  }

  const formData = new FormData()
  formData.append('file', file)

  const response = await api.post<UploadAttachmentResponse>('/upload/file', formData, {
    timeout: ATTACHMENT_UPLOAD_TIMEOUT_MS
  })
  const { errno = 1, data, message } = response.data || {}

  if (errno !== 0 || !data?.url) {
    throw new Error(message || `${file.name} 上传失败`)
  }

  return {
    ...data,
    name: data.name || data.original_filename || file.name
  }
}

const buildAttachmentLinkHtml = (attachment: UploadedAttachmentData) => {
  const url = String(attachment.url || '')
  const label = String(attachment.name || attachment.original_filename || attachment.filename || '附件')
  if (!url) return ''
  return `<p><a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer" download="${escapeHtml(label)}" data-codeyun-attachment="true">${escapeHtml(label)}</a></p>`
}

const insertAttachmentLinks = (attachments: UploadedAttachmentData[]) => {
  const editor = editorRef.value as IDomEditor | undefined
  if (!editor) {
    ElMessage.error('编辑器还未准备好')
    return
  }

  const html = attachments
    .map(buildAttachmentLinkHtml)
    .filter(Boolean)
    .join('')

  if (!html) return
  editor.focus()
  editor.dangerouslyInsertHtml(html)
  syncValueFromEditor()
}

const handleAttachmentInputChange = async (event: Event) => {
  const input = event.target as HTMLInputElement
  const files = Array.from(input.files || [])
  input.value = ''
  if (!files.length) return

  attachmentUploading.value = true
  try {
    const uploaded: UploadedAttachmentData[] = []
    for (const file of files) {
      uploaded.push(await uploadAttachmentFile(file))
    }
    insertAttachmentLinks(uploaded)
    ElMessage.success(uploaded.length === 1 ? '已插入附件' : `已插入 ${uploaded.length} 个附件`)
  } catch (error: any) {
    const message = error?.response?.data?.detail || error?.message || '附件上传失败'
    ElMessage.error(message)
  } finally {
    attachmentUploading.value = false
  }
}

const editorConfig: any = {
    placeholder: '',
    readOnly: props.readOnly,
    MENU_CONF: {
        uploadImage: {
            maxFileSize: 10 * 1024 * 1024, // 10M
            base64LimitSize: 5 * 1024, // Images smaller than 5kb insert as base64, larger upload
            customUpload: uploadEditorImage
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
        },
        text: {
            menuKeys: [
                'image-merge-button',
                '|',
                'bold', 'underline', 'italic', 'through', 'color', 'bgColor', 'clearStyle'
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
    if (props.autoFocusOnEmpty && (!valueHtml.value || valueHtml.value === '<p><br></p>')) {
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

const syncValueFromEditor = () => {
    const editor = editorRef.value as IDomEditor | undefined
    if (!editor) return
    const nextHtml = editor.getHtml()
    if (nextHtml === valueHtml.value) return
    valueHtml.value = nextHtml
    emit('update:modelValue', nextHtml)
    emit('change', nextHtml)
}

const focusEditor = (isEnd = true) => {
    const editor = editorRef.value as IDomEditor | undefined
    if (!editor) return
    editor.focus(isEnd)
}

const undo = () => {
    const editor = editorRef.value as IDomEditor | undefined
    if (!editor?.undo) return
    editor.undo()
    queueMicrotask(syncValueFromEditor)
    editor.focus()
}

const redo = () => {
    const editor = editorRef.value as IDomEditor | undefined
    if (!editor?.redo) return
    editor.redo()
    queueMicrotask(syncValueFromEditor)
    editor.focus()
}

defineExpose({
    focus: focusEditor,
    undo,
    redo,
})

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
    cursor: text; /* 提示可编辑 */
    display: flex;
    flex-direction: column;
    min-width: 0;
}

.editor-container.is-fill {
    flex: 1;
    min-height: 0;
    height: 100%;
    overflow: hidden;
}

.editor-container.is-flow {
    height: auto;
    min-height: var(--editor-flow-min-height, 320px);
}

.editor-toolbar-row {
    display: flex;
    align-items: center;
    border-bottom: 1px solid #ccc;
    background: #fff;
    flex-shrink: 0;
    min-width: 0;
}

.editor-toolbar {
    flex: 1;
    min-width: 0;
}

.attachment-upload-button {
    flex: 0 0 auto;
    margin-right: 8px;
}

.attachment-input {
    display: none;
}

.editor-content-area {
    min-width: 0;
}

.editor-content-area.is-fill {
    flex: 1;
    min-height: 0;
    overflow: hidden;
}

.editor-content-area.is-flow {
    height: auto;
    min-height: var(--editor-content-min-height, 500px);
}

/* 确保编辑器区域填满容器，点击空白处也能触发编辑器焦点 */
:deep([data-w-e-textarea='true']) {
    min-width: 0;
}

.editor-container.is-fill :deep([data-w-e-textarea='true']) {
    display: flex;
    flex-direction: column;
    flex: 1;
    min-height: 0;
}

.editor-container.is-flow :deep([data-w-e-textarea='true']) {
    height: auto;
}

.editor-container.is-fill :deep(.w-e-text-container) {
    height: 100% !important;
    min-height: 0 !important;
    display: flex;
    flex-direction: column;
    background-color: transparent !important;
    overflow: hidden !important;
}

.editor-container.is-flow :deep(.w-e-text-container) {
    height: auto !important;
    min-height: var(--editor-text-min-height, 320px) !important;
    background-color: transparent !important;
}

.editor-container.is-fill :deep(.w-e-text-container .w-e-scroll) {
    flex: 1;
    min-height: 0;
    overflow-y: auto;
}

:deep(.w-e-text-container blockquote),
:deep(.w-e-text-container li),
:deep(.w-e-text-container p),
:deep(.w-e-text-container td),
:deep(.w-e-text-container th) {
    line-height: 1 !important;
}

:deep(.w-e-text-container [data-slate-editor] p) {
    margin: 6px 0 !important;
}

.editor-toolbar-extra {
    padding: 8px 15px;
    background-color: #fcfcfc;
    border-bottom: 1px solid #eee;
    display: flex;
    align-items: center;
    gap: 10px;
    flex-shrink: 0;
}

.merge-settings {
    display: flex;
    align-items: center;
}

.merge-alert {
    margin-bottom: 15px;
}

.merge-button {
    margin-left: 15px;
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

/* 去掉 wangEditor 表格外层默认虚线容器，仅保留单元格边框 */
:deep(.w-e-text-container [data-slate-editor] .table-container) {
    border: none !important;
    border-radius: 0 !important;
    padding: 0 !important;
}

/* 确保图片自适应宽度，防止撑破容器 */
:deep(.w-e-text-container img) {
    max-width: 100% !important;
    height: auto !important;
    display: block;
    margin: 10px 0;
}
</style>
