import { Editor, Element, Range, Transforms } from 'slate'
import { HistoryEditor } from 'slate-history'

/** 段首标记 + 半角空格转为富文本；只处理键入，不重新解析已存 HTML。 */
export function withNoteMarkdownShortcuts<T extends Editor>(editor: T): T {
  const insertText = editor.insertText.bind(editor)
  editor.insertText = text => {
    const { selection } = editor
    if (text !== ' ' || !selection || !Range.isCollapsed(selection)) {
      insertText(text)
      return
    }
    const entry = Editor.above(editor, {
      match: node => Element.isElement(node) && Editor.isBlock(editor, node),
    })
    // 限定顶层正文，避免代码、表格、已有列表和标题内误触发。
    if (!entry || entry[1].length !== 1 || (entry[0] as { type?: string }).type !== 'paragraph') {
      insertText(text)
      return
    }
    const [, path] = entry
    const range = { anchor: Editor.start(editor, path), focus: selection.anchor }
    const prefix = Editor.string(editor, range)
    let properties: Record<string, unknown> | undefined
    if (/^#{1,5}$/.test(prefix)) properties = { type: `header${prefix.length}` }
    else if (prefix === '>') properties = { type: 'blockquote' }
    else if (/^[-+*]$/.test(prefix)) properties = { type: 'list-item', ordered: false, level: 0 }
    else if (prefix === '1.') properties = { type: 'list-item', ordered: true, level: 0 }
    else if (/^\[( |x|X)?\]$/.test(prefix)) properties = { type: 'todo', checked: /x/i.test(prefix) }
    if (!properties) {
      insertText(text)
      return
    }
    // 空格单独开一个历史批次，删除标记与切换格式合并；一次撤销恢复标记。
    const convert = () => {
      if (HistoryEditor.isHistoryEditor(editor)) {
        HistoryEditor.withoutMerging(editor, () => insertText(text))
      } else insertText(text)
      Transforms.delete(editor, { at: { anchor: range.anchor, focus: editor.selection!.anchor } })
      Transforms.setNodes(editor, properties!, { at: path })
    }
    Editor.withoutNormalizing(editor, convert)
  }
  return editor
}
