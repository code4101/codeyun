import {
  defineComponent,
  h,
  onMounted,
  ref,
  shallowRef,
  toRaw,
  watch,
  watchEffect,
  type PropType,
} from 'vue'
import {
  createEditor,
  createToolbar,
  DomEditor,
  type IDomEditor,
} from '@wangeditor/editor'

type EditorConfig = Record<string, any>

const eventPropError = (eventName: string) => (
  new Error(`请使用 '@${eventName}' 事件，不要放在 props 中\nPlease use '@${eventName}' event instead of props`)
)

export const Editor = defineComponent({
  name: 'CodeyunWangEditor',
  props: {
    mode: {
      type: String,
      default: 'default',
    },
    defaultContent: {
      type: Array as PropType<any[]>,
      default: () => [],
    },
    defaultHtml: {
      type: String,
      default: '',
    },
    defaultConfig: {
      type: Object as PropType<EditorConfig>,
      default: () => ({}),
    },
    modelValue: {
      type: String,
      default: '',
    },
  },
  emits: [
    'update:modelValue',
    'onCreated',
    'onChange',
    'onDestroyed',
    'onMaxLength',
    'onFocus',
    'onBlur',
    'customAlert',
    'customPaste',
  ],
  setup(props, { emit }) {
    const box = ref<HTMLElement | null>(null)
    const editorRef = shallowRef<IDomEditor | null>(null)
    const currentValue = ref('')

    const initEditor = () => {
      if (!box.value) return

      const config = props.defaultConfig
      createEditor({
        selector: box.value,
        mode: props.mode,
        content: toRaw(props.defaultContent),
        html: props.defaultHtml || props.modelValue || '',
        config: {
          ...config,
          onCreated(editor: IDomEditor) {
            editorRef.value = editor
            emit('onCreated', editor)
            if (config.onCreated) throw eventPropError('onCreated')
          },
          onChange(editor: IDomEditor) {
            const html = editor.getHtml()
            currentValue.value = html
            emit('update:modelValue', html)
            emit('onChange', editor)
            if (config.onChange) throw eventPropError('onChange')
          },
          onDestroyed(editor: IDomEditor) {
            emit('onDestroyed', editor)
            if (config.onDestroyed) throw eventPropError('onDestroyed')
          },
          onMaxLength(editor: IDomEditor) {
            emit('onMaxLength', editor)
            if (config.onMaxLength) throw eventPropError('onMaxLength')
          },
          onFocus(editor: IDomEditor) {
            emit('onFocus', editor)
            if (config.onFocus) throw eventPropError('onFocus')
          },
          onBlur(editor: IDomEditor) {
            emit('onBlur', editor)
            if (config.onBlur) throw eventPropError('onBlur')
          },
          customAlert(info: string, type: string) {
            emit('customAlert', info, type)
            if (config.customAlert) throw eventPropError('customAlert')
          },
          customPaste(editor: IDomEditor, event: ClipboardEvent) {
            if (config.customPaste) throw eventPropError('customPaste')
            let result: boolean | undefined
            emit('customPaste', editor, event, (value: boolean) => {
              result = value
            })
            return result
          },
        },
      } as any)
    }

    const setHtml = (html: string) => {
      const editor = editorRef.value
      if (!editor || html === currentValue.value) return
      editor.setHtml(html)
    }

    onMounted(initEditor)
    watch(() => props.modelValue, setHtml)

    // A render-local ref avoids the hoisted string-ref output produced by the
    // abandoned editor-for-vue package, which no longer mounts on current Vue.
    return () => h('div', { ref: box, style: { height: '100%' } })
  },
})

export const Toolbar = defineComponent({
  name: 'CodeyunWangToolbar',
  props: {
    editor: {
      type: Object as PropType<IDomEditor | null>,
      default: null,
    },
    mode: {
      type: String,
      default: 'default',
    },
    defaultConfig: {
      type: Object as PropType<Record<string, any>>,
      default: () => ({}),
    },
  },
  setup(props) {
    const selector = ref<HTMLElement | null>(null)

    watchEffect(() => {
      const editor = props.editor
      if (!selector.value || !editor || DomEditor.getToolbar(editor)) return
      createToolbar({
        editor,
        selector: selector.value,
        mode: props.mode,
        config: props.defaultConfig,
      } as any)
    })

    return () => h('div', { ref: selector })
  },
})
