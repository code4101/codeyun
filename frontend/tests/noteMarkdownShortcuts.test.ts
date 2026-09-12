import assert from 'node:assert/strict'
import test from 'node:test'
import { createEditor, Editor, Transforms } from 'slate'
import { withHistory } from 'slate-history'
import { withNoteMarkdownShortcuts } from '../src/utils/noteMarkdownShortcuts.ts'

function setup(type = 'paragraph', text = '') {
  const editor = withNoteMarkdownShortcuts(withHistory(createEditor()))
  editor.children = [{ type, children: [{ text }] }] as typeof editor.children
  Transforms.select(editor, Editor.end(editor, []))
  return editor
}

test('typed prefixes convert into native blocks with no marker left behind', () => {
  for (const [prefix, type] of [
    ...Array.from({ length: 5 }, (_, i) => ['#'.repeat(i + 1), `header${i + 1}`]),
    ['>', 'blockquote'], ['-', 'list-item'], ['*', 'list-item'], ['+', 'list-item'],
    ['1.', 'list-item'], ['[]', 'todo'], ['[ ]', 'todo'], ['[x]', 'todo'],
  ]) {
    const editor = setup()
    for (const char of prefix) editor.insertText(char)
    editor.insertText(' ')
    assert.equal((editor.children[0] as { type?: string }).type, type, prefix)
    assert.equal(Editor.string(editor, []), '', prefix)
    editor.insertText('中文正文')
    assert.equal(Editor.string(editor, []), '中文正文')
  }
})

test('undo restores literal prefix and redo reapplies conversion', async () => {
  const editor = setup()
  editor.insertText('##')
  await Promise.resolve()
  editor.insertText(' ')
  await Promise.resolve()
  editor.undo()
  assert.equal(Editor.string(editor, []), '##')
  assert.equal((editor.children[0] as { type?: string }).type, 'paragraph')
  editor.redo()
  assert.equal((editor.children[0] as { type?: string }).type, 'header2')
})

test('plain text, paste, existing blocks and expanded selections stay literal', () => {
  for (const prefix of ['######', '#######', '正文#', ' #', '2.', '#标题']) {
    const editor = setup('paragraph', prefix)
    editor.insertText(' ')
    assert.equal(Editor.string(editor, []), `${prefix} `)
    assert.equal((editor.children[0] as { type?: string }).type, 'paragraph')
  }
  for (const type of ['code', 'header1', 'list-item']) {
    const editor = setup(type, '#')
    editor.insertText(' ')
    assert.equal(Editor.string(editor, []), '# ')
  }
  const pasted = setup()
  pasted.insertText('# pasted text')
  assert.equal((pasted.children[0] as { type?: string }).type, 'paragraph')
  const selected = setup('paragraph', '#')
  Transforms.select(selected, Editor.range(selected, []))
  selected.insertText(' ')
  assert.equal(Editor.string(selected, []), ' ')
})

test('conversion preserves trailing text and list/todo attributes', () => {
  const editor = setup('paragraph', '##已有文字')
  Transforms.select(editor, { path: [0, 0], offset: 2 })
  editor.insertText(' ')
  assert.equal(Editor.string(editor, []), '已有文字')
  for (const [prefix, expected] of [['1.', { ordered: true, level: 0 }], ['[x]', { checked: true }]] as const) {
    const block = setup('paragraph', prefix)
    block.insertText(' ')
    for (const [key, value] of Object.entries(expected)) {
      assert.equal((block.children[0] as unknown as Record<string, unknown>)[key], value)
    }
  }
})
