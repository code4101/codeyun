<template>
  <div ref="editorContainerRef" class="editor-container" :class="`is-${layout}`" :style="editorStyle" @click="handleContainerClick">
    <div v-if="isEditorReady && ((showToolbar && !readOnly) || showWrapToggle)" class="editor-toolbar-row">
      <Toolbar
        v-if="showToolbar && !readOnly"
        class="editor-toolbar"
        :editor="editorRef"
        :defaultConfig="toolbarConfig"
        :mode="mode"
      />
      <div v-else class="editor-toolbar-spacer"></div>
      <template v-if="showToolbar && !readOnly">
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
      </template>
      <el-tooltip v-if="showWrapToggle" :content="autoWrapTooltip" placement="bottom">
        <el-checkbox v-model="autoWrapEnabled" size="small" class="auto-wrap-toggle" @click.stop>
          自动换行
        </el-checkbox>
      </el-tooltip>
    </div>
    <!-- Extra Toolbar Items Slot -->
    <div v-if="showToolbar && !readOnly && $slots.extra" class="editor-toolbar-extra">
      <slot name="extra"></slot>
    </div>
    <Editor
      class="editor-content-area"
      :class="[`is-${layout}`, { 'is-no-wrap': !autoWrapEnabled }]"
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
import { decreaseExpandedSelectionIndent, registerWangEditorPlugins } from '@/utils/wangEditorPlugins'

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
  },
  showWrapToggle: {
    type: Boolean,
    default: false
  }
})

const emit = defineEmits(['update:modelValue', 'change'])

// 编辑器实例，必须用 shallowRef
const editorRef = shallowRef()
const editorContainerRef = ref<HTMLElement | null>(null)
const detachIndentHotkeysRef = ref<(() => void) | null>(null)
const isEditorReady = computed(() => Boolean(editorRef.value))
const suppressModelDrivenChange = ref(true)
let releaseModelDrivenChangeTimer: number | null = null

const isLegacyLakeHtml = (html: string) => (
    /<!DOCTYPE\s+lake/i.test(html)
    || /data-lake-id\s*=/i.test(html)
    || /class=["'][^"']*lake-/i.test(html)
    || /name=["']doc-version["']/i.test(html)
)

const sanitizeLegacyLakeHtml = (html: string) => {
    const source = String(html || '')
    if (!source || !isLegacyLakeHtml(source)) return source

    const stripped = source
        .replace(/<!DOCTYPE[^>]*>/gi, '')
        .replace(/<meta\b[^>]*>/gi, '')
        .trim()

    if (typeof DOMParser === 'undefined') return stripped || '<p><br></p>'

    try {
        const doc = new DOMParser().parseFromString(stripped, 'text/html')
        doc.body.querySelectorAll('script, style, link, meta, title').forEach(el => el.remove())
        doc.body.querySelectorAll<HTMLElement>('*').forEach(el => {
            Array.from(el.attributes).forEach(attr => {
                const name = attr.name.toLowerCase()
                if (
                    name === 'id'
                    || name === 'fid'
                    || name === 'list'
                    || name === 'spellcheck'
                    || name === 'data-lake-id'
                    || name.startsWith('data-lake-')
                ) {
                    el.removeAttribute(attr.name)
                }
            })

            const classAttr = el.getAttribute('class')
            if (classAttr) {
                const classes = classAttr.split(/\s+/).filter(name => name && !name.startsWith('lake-'))
                if (classes.length) el.setAttribute('class', classes.join(' '))
                else el.removeAttribute('class')
            }
        })
        return doc.body.innerHTML.trim() || '<p><br></p>'
    } catch {
        return stripped || '<p><br></p>'
    }
}

const normalizeAdjacentInlineSpans = (html: string) => {
    const source = String(html || '')
    if (!source || !source.includes('<span')) return source
    if (typeof DOMParser === 'undefined') return source

    const sameAttributes = (left: Element, right: Element) => {
        const leftAttrs = Array.from(left.attributes)
            .map(attr => [attr.name, attr.value] as const)
            .sort(([a], [b]) => a.localeCompare(b))
        const rightAttrs = Array.from(right.attributes)
            .map(attr => [attr.name, attr.value] as const)
            .sort(([a], [b]) => a.localeCompare(b))
        if (leftAttrs.length !== rightAttrs.length) return false
        return leftAttrs.every(([name, value], index) => {
            const [rightName, rightValue] = rightAttrs[index]
            return name === rightName && value === rightValue
        })
    }

    const mergeIntoLeft = (left: Element, right: Element) => {
        while (right.firstChild) left.appendChild(right.firstChild)
        right.remove()
    }

    const visit = (element: Element) => {
        Array.from(element.children).forEach(visit)
        let node: ChildNode | null = element.firstChild
        while (node) {
            const next = node.nextSibling
            if (
                next
                && node.nodeType === Node.ELEMENT_NODE
                && next.nodeType === Node.ELEMENT_NODE
                && (node as Element).tagName.toLowerCase() === 'span'
                && (next as Element).tagName.toLowerCase() === 'span'
                && sameAttributes(node as Element, next as Element)
            ) {
                mergeIntoLeft(node as Element, next as Element)
                continue
            }
            node = next
        }
    }

    try {
        const doc = new DOMParser().parseFromString(source, 'text/html')
        Array.from(doc.body.children).forEach(visit)
        return doc.body.innerHTML || source
    } catch {
        return source
    }
}

const normalizeEditorInputHtml = (html: string) => normalizeAdjacentInlineSpans(sanitizeLegacyLakeHtml(html))

// 内容 HTML，先把旧语雀 Lake 文档片段清理成编辑器可解析的普通 HTML
const valueHtml = ref(normalizeEditorInputHtml(props.modelValue))

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
const NOTE_EDITOR_AUTO_WRAP_STORAGE_KEY = 'codeyun.noteEditor.autoWrap'

const canUseLocalStorage = () => typeof window !== 'undefined' && typeof window.localStorage !== 'undefined'

const loadAutoWrapPreference = () => {
  if (!canUseLocalStorage()) return true
  const raw = window.localStorage.getItem(NOTE_EDITOR_AUTO_WRAP_STORAGE_KEY)
  if (raw == null) return true
  return raw !== '0'
}

const autoWrapEnabled = ref(loadAutoWrapPreference())
const autoWrapTooltip = computed(() => autoWrapEnabled.value
  ? '长行会在编辑区内自动换行'
  : '长行保持单行，用横向滚动查看'
)

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

const suppressInitialEditorChange = () => {
    suppressModelDrivenChange.value = true
    if (releaseModelDrivenChangeTimer != null) {
        window.clearTimeout(releaseModelDrivenChangeTimer)
    }
    releaseModelDrivenChangeTimer = window.setTimeout(() => {
        suppressModelDrivenChange.value = false
        releaseModelDrivenChangeTimer = null
    }, 250)
}

// 模拟 ajax 异步获取内容
onMounted(() => {
    suppressInitialEditorChange()
    valueHtml.value = normalizeEditorInputHtml(props.modelValue)
})

// 监听 props 变化
watch(() => props.modelValue, (newVal) => {
    const normalizedVal = normalizeEditorInputHtml(newVal)
    // 只有当传入的新值与当前编辑器内容确实不同，且新值不为空时才更新
    // 或者当新值为空字符串，且编辑器不为空时更新（处理清空操作）
    if (normalizedVal !== valueHtml.value) {
        suppressInitialEditorChange()
        valueHtml.value = normalizedVal
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

watch(autoWrapEnabled, value => {
    if (!canUseLocalStorage()) return
    window.localStorage.setItem(NOTE_EDITOR_AUTO_WRAP_STORAGE_KEY, value ? '1' : '0')
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
    queueMicrotask(syncValueFromEditor)
    window.setTimeout(syncValueFromEditor, 0)

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
    autoFocus: false,
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
    if (releaseModelDrivenChangeTimer != null) {
        window.clearTimeout(releaseModelDrivenChangeTimer)
        releaseModelDrivenChangeTimer = null
    }
    detachIndentHotkeysRef.value?.()
    detachIndentHotkeysRef.value = null
    const editor = editorRef.value
    if (editor == null) return
    editor.destroy()
})

const bindIndentHotkeys = (editor: IDomEditor) => {
    detachIndentHotkeysRef.value?.()
    detachIndentHotkeysRef.value = null

    const container = editorContainerRef.value
    if (!container) return

    const handleKeydown = (event: KeyboardEvent) => {
        if (event.defaultPrevented || event.key !== 'Tab' || !event.shiftKey) return

        const targetNode = event.target
        if (!(targetNode instanceof Node) || !container.contains(targetNode)) return

        const targetElement = targetNode instanceof HTMLElement ? targetNode : targetNode.parentElement
        if (!targetElement?.closest('[data-slate-editor]')) return

        if (!decreaseExpandedSelectionIndent(editor)) return

        event.preventDefault()
        queueMicrotask(syncValueFromEditor)
    }

    container.addEventListener('keydown', handleKeydown)
    detachIndentHotkeysRef.value = () => {
        container.removeEventListener('keydown', handleKeydown)
    }
}

const handleCreated = (editor: any) => {
    editorRef.value = editor // 记录 editor 实例，重要！
    bindIndentHotkeys(editor)
    suppressInitialEditorChange()

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
    if (suppressModelDrivenChange.value) return
    const nextHtml = editor.getHtml()
    emit('update:modelValue', nextHtml)
    emit('change', nextHtml)
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

.editor-toolbar-spacer {
    flex: 1;
    min-width: 0;
}

.attachment-upload-button {
    flex: 0 0 auto;
    margin-right: 8px;
}

.auto-wrap-toggle {
    flex: 0 0 auto;
    margin-right: 12px;
    color: #606266;
    user-select: none;
}

.auto-wrap-toggle :deep(.el-checkbox__label) {
    font-size: 12px;
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

.editor-content-area.is-no-wrap {
    display: flex;
    flex-direction: column;
    min-height: 0;
}

/* 确保编辑器区域填满容器，点击空白处也能触发编辑器焦点 */
:deep([data-w-e-textarea='true']) {
    min-width: 0;
}

:deep(.w-e-text-container [data-slate-editor]) {
    color: #1f2933;
    font-size: 15px;
    line-height: 1.7;
}

.editor-container.is-flow :deep(.w-e-text-container [data-slate-editor]) {
    max-width: 920px;
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

.editor-content-area.is-no-wrap :deep(.w-e-text-container .w-e-scroll) {
    flex: 1 1 auto;
    min-height: 0;
    height: auto !important;
    overflow: auto !important;
}

.editor-content-area.is-no-wrap :deep([data-w-e-textarea='true']) {
    display: flex;
    flex-direction: column;
    flex: 1 1 auto;
    min-height: 0;
}

.editor-content-area.is-no-wrap :deep(.w-e-text-container) {
    display: flex;
    flex-direction: column;
    flex: 1 1 auto;
    min-height: 0 !important;
    overflow: hidden !important;
}

:deep(.w-e-text-container blockquote),
:deep(.w-e-text-container li),
:deep(.w-e-text-container p) {
    line-height: 1.7 !important;
}

:deep(.w-e-text-container td),
:deep(.w-e-text-container th) {
    line-height: 1.45 !important;
}

:deep(.w-e-text-container [data-slate-editor] p) {
    margin: 0 0 0.72em !important;
}

:deep(.w-e-text-container [data-slate-editor] h1) {
    margin: 0.95em 0 0.55em !important;
    font-size: 2em !important;
    line-height: 1.25 !important;
}

:deep(.w-e-text-container [data-slate-editor] h2) {
    margin: 1.25em 0 0.55em !important;
    font-size: 1.55em !important;
    line-height: 1.3 !important;
}

:deep(.w-e-text-container [data-slate-editor] h3) {
    margin: 1.1em 0 0.5em !important;
    font-size: 1.25em !important;
    line-height: 1.35 !important;
}

:deep(.w-e-text-container [data-slate-editor] h1:first-child),
:deep(.w-e-text-container [data-slate-editor] h2:first-child),
:deep(.w-e-text-container [data-slate-editor] h3:first-child) {
    margin-top: 0 !important;
}

:deep(.w-e-text-container [data-slate-editor] ol),
:deep(.w-e-text-container [data-slate-editor] ul) {
    margin: 0.35em 0 0.85em !important;
    padding-left: 1.55em !important;
}

:deep(.w-e-text-container [data-slate-editor] li) {
    margin: 0.22em 0 !important;
}

:deep(.w-e-text-container [data-slate-editor] blockquote) {
    margin: 0.85em 0 !important;
    padding: 0.05em 0 0.05em 0.9em !important;
    border-left: 3px solid #dcdfe6 !important;
    color: #4b5563;
}

:deep(.w-e-text-container [data-slate-editor] pre) {
    margin: 0.9em 0 !important;
    padding: 10px 12px !important;
    border-radius: 4px;
    background: #f5f7fa !important;
    line-height: 1.55 !important;
}

.editor-content-area.is-no-wrap :deep(.w-e-text-container [data-slate-editor]) {
    min-width: max-content;
    white-space: pre !important;
    word-break: normal !important;
    word-wrap: normal !important;
}

.editor-content-area.is-no-wrap :deep(.w-e-text-container [data-slate-editor] blockquote),
.editor-content-area.is-no-wrap :deep(.w-e-text-container [data-slate-editor] li),
.editor-content-area.is-no-wrap :deep(.w-e-text-container [data-slate-editor] p),
.editor-content-area.is-no-wrap :deep(.w-e-text-container [data-slate-editor] td),
.editor-content-area.is-no-wrap :deep(.w-e-text-container [data-slate-editor] th) {
    white-space: inherit;
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
