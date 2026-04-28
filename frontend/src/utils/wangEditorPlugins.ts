import {
  Boot,
  IButtonMenu,
  IDomEditor,
  SlateEditor,
  SlateElement,
  SlateRange,
  SlateTransforms,
} from '@wangeditor/editor'

const INDENT_STEP_EM = 2
const MAX_INDENT_EM = 40
const MIN_INDENT_EM = 0

type IndentableBlock = SlateElement & {
  type?: string
  indent?: string | null
}

const isIndentableBlock = (node: unknown): node is IndentableBlock => {
  if (!SlateElement.isElement(node)) return false
  const type = String((node as IndentableBlock).type || '')
  return type === 'paragraph' || type.startsWith('header')
}

const parseIndentToEm = (value: string | null | undefined): number => {
  if (!value) return 0

  const normalized = String(value).trim().toLowerCase()
  const match = normalized.match(/^(-?\d+(?:\.\d+)?)(em|px)?$/)
  if (!match) return 0

  const amount = Number.parseFloat(match[1])
  if (!Number.isFinite(amount) || amount <= 0) return 0

  const unit = match[2] || 'em'
  if (unit === 'px') return amount / 16
  return amount
}

const collectSelectedBlocks = (editor: IDomEditor): Array<[IndentableBlock, number[]]> => {
  const { selection } = editor
  if (!selection) return []

  const blocks: Array<[IndentableBlock, number[]]> = []
  for (const [node, path] of SlateEditor.nodes(editor, {
    at: selection,
    match: node => SlateElement.isElement(node) && !editor.isInline(node),
    mode: 'highest',
  })) {
    if (!isIndentableBlock(node)) return []
    blocks.push([node, path])
  }

  return blocks
}

const adjustExpandedSelectionIndent = (editor: IDomEditor, deltaEm: number): boolean => {
  const { selection } = editor
  if (!selection || !SlateRange.isExpanded(selection)) return false

  const blocks = collectSelectedBlocks(editor)
  if (blocks.length === 0) return false

  for (const [node, path] of blocks) {
    const currentIndentEm = parseIndentToEm(node.indent)
    const nextIndentEm = Math.max(
      MIN_INDENT_EM,
      Math.min(currentIndentEm + deltaEm, MAX_INDENT_EM)
    )

    if (nextIndentEm <= MIN_INDENT_EM) {
      SlateTransforms.setNodes(editor, { indent: null }, { at: path })
      continue
    }

    SlateTransforms.setNodes(editor, { indent: `${nextIndentEm}em` }, { at: path })
  }

  return true
}

export const increaseExpandedSelectionIndent = (editor: IDomEditor): boolean =>
  adjustExpandedSelectionIndent(editor, INDENT_STEP_EM)

export const decreaseExpandedSelectionIndent = (editor: IDomEditor): boolean =>
  adjustExpandedSelectionIndent(editor, -INDENT_STEP_EM)

const withExpandedSelectionTabIndent = <T extends IDomEditor>(editor: T): T => {
  const originalHandleTab = editor.handleTab.bind(editor)

  editor.handleTab = () => {
    if (increaseExpandedSelectionIndent(editor)) return
    originalHandleTab()
  }

  return editor
}

class ImageMergeMenu implements IButtonMenu {
  title = '拼接图片'
  // iconSvg: 使用一个简单的图片拼接图标
  iconSvg = '<svg viewBox="0 0 1024 1024"><path d="M880 112H144c-17.7 0-32 14.3-32 32v736c0 17.7 14.3 32 32 32h736c17.7 0 32-14.3 32-32V144c0-17.7-14.3-32-32-32zM184 848V184h656v664H184z m120-432h416v80H304z m0 160h416v80H304z m0-320h416v80H304z" fill="currentColor"></path></svg>'
  tag = 'button'

  getValue(editor: IDomEditor): string | boolean {
    return false
  }

  isActive(editor: IDomEditor): boolean {
    return false
  }

  isDisabled(editor: IDomEditor): boolean {
    const { selection } = editor
    if (selection == null) return true
    return false
  }

  exec(editor: IDomEditor, value: string | boolean) {
    // 触发一个自定义事件，供 Vue 组件监听
    editor.emit('image-merge-click')
  }
}

let isRegistered = false

export const registerWangEditorPlugins = () => {
  if (isRegistered) return

  Boot.registerPlugin(withExpandedSelectionTabIndent)

  Boot.registerMenu({
    key: 'image-merge-button',
    factory() {
      return new ImageMergeMenu()
    },
  })
  
  isRegistered = true
}
