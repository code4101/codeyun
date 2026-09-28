import { useState } from 'react';
import { ChevronDown, ChevronRight, CornerDownRight, ChevronsUpDown } from 'lucide-react';
import { ElementApi, NodeApi, PathApi, RangeApi, type Path, type TElement } from 'platejs';
import { createPlatePlugin, PlateElement, type PlateElementProps, type PlateEditor,
  useEditorRef, useEditorReadOnly } from 'platejs/react';
import { ToolbarButton } from '@/components/ui/toolbar';
import './plateCollapse.css';

export const COLLAPSE = 'codeyun-collapse';
type CollapseElement = TElement & { title: string; collapsed: boolean };
const paragraph = () => ({ type: 'p', children: [{ text: '' }] });

/** A container owns ordinary Plate blocks, including more containers. Its title
 * and presentation state are metadata; children use the one shared editor, so
 * selection, history, media and persistence never cross an editor boundary. */
function createCollapse(children: TElement[] = [paragraph()]): CollapseElement {
  return { type: COLLAPSE, title: '折叠块', collapsed: false, children };
}

// Pick the deepest shared document container. Tables/code blocks stay intact
// when wrapping a selection; their internal cells/lines aren't document siblings.
function documentParent(editor: PlateEditor, a: Path, b: Path): Path {
  for (let depth = Math.min(a.length, b.length) - 1; depth > 0; depth--) {
    const path = a.slice(0, depth);
    if (PathApi.equals(path, b.slice(0, depth)) && editor.api.node(path)?.[0].type === COLLAPSE) return path;
  }
  return [];
}

export function insertCollapse(editor: PlateEditor) {
  const selection = editor.selection;
  let insertedPath: Path = [];
  editor.tf.withNewBatch(() => editor.tf.withoutNormalizing(() => {
    if (!selection) {
      const at = [editor.children.length];
      insertedPath = at;
      editor.tf.insertNodes(createCollapse(), { at });
      editor.tf.select(editor.api.start(at)!);
      return;
    }
    const [start, end] = RangeApi.edges(selection);
    const parent = documentParent(editor, start.path, end.path);
    const first = start.path.slice(0, parent.length + 1);
    let last = end.path.slice(0, parent.length + 1);
    // A selection ending at the next block's start does not include that block.
    if (!PathApi.equals(first, last) && editor.api.isStart(end, last)) last = PathApi.previous(last)!;
    if (!RangeApi.isCollapsed(selection)) {
      insertedPath = first;
      editor.tf.wrapNodes(createCollapse([]), {
        at: { anchor: editor.api.start(first)!, focus: editor.api.end(last)! },
        match: (node, path) => ElementApi.isElement(node) && path.length === parent.length + 1,
        mode: 'highest',
      });
      editor.tf.select(editor.api.start(first)!);
    } else {
      const block = editor.api.node(first)?.[0];
      const empty = block?.type === 'p' && NodeApi.string(block) === '';
      const at = empty ? first : PathApi.next(first);
      insertedPath = at;
      if (empty) editor.tf.removeNodes({ at: first });
      editor.tf.insertNodes(createCollapse(), { at });
      editor.tf.select(editor.api.start(at)!);
    }
  }));
  // An unfocused toolbar click used to append below the viewport and then
  // focus the document start. Focus the actual insertion and reveal its header
  // after React commits the node, including when wrapping a tall selection.
  const inserted = editor.api.node(insertedPath)?.[0];
  editor.tf.focus({ at: editor.api.start(insertedPath)! });
  requestAnimationFrame(() => {
    if (!inserted || !editor.api.findPath(inserted)) return;
    editor.api.toDOMNode(inserted)?.querySelector('.codeyun-collapse-heading')?.scrollIntoView({ block: 'nearest' });
  });
}

function exitCollapse(editor: PlateEditor, path: Path) {
  const after = PathApi.next(path);
  editor.tf.withNewBatch(() => {
    if (!editor.api.hasPath(after)) editor.tf.insertNodes(paragraph(), { at: after });
    editor.tf.select(editor.api.start(after)!);
  });
  editor.tf.focus();
}

function CollapseBlock(props: PlateElementProps<CollapseElement>) {
  const editor = useEditorRef();
  const readOnly = useEditorReadOnly();
  // Reading can expand a saved closed block without editing the document.
  const [readingOpen, setReadingOpen] = useState<boolean | null>(null);
  const closed = readOnly && readingOpen !== null ? !readingOpen : Boolean(props.element.collapsed);
  const path = () => editor.api.findPath(props.element);
  const toggle = () => {
    if (readOnly) { setReadingOpen(closed); return; }
    const at = path();
    if (!at) return;
    editor.tf.withNewBatch(() => {
      editor.tf.setNodes({ collapsed: !closed }, { at });
      // Apply the value change before the selection-only operation so Plate's
      // batched onValueChange publishes the saved state as well as the render.
      // A hidden caret must not keep receiving edits from the toolbar/keyboard.
      if (!closed) editor.tf.deselectDOM();
    });
  };
  return <PlateElement {...props} className="codeyun-collapse" data-collapsed={closed}>
    <div className="codeyun-collapse-heading" contentEditable={false}>
      <button type="button" aria-label={closed ? '展开折叠块' : '收起折叠块'} aria-expanded={!closed}
        onMouseDown={event => event.preventDefault()} onClick={toggle}>
        {closed ? <ChevronRight size={18} /> : <ChevronDown size={18} />}
      </button>
      {readOnly ? <span className="codeyun-collapse-title">{props.element.title || '折叠块'}</span> :
        <input className="codeyun-collapse-title" aria-label="折叠块标题" placeholder="折叠块标题"
          value={props.element.title || ''}
          onChange={event => { const at = path(); if (at) editor.tf.setNodes({ title: event.target.value }, { at }); }}
          onKeyDown={event => {
            event.stopPropagation();
            const at = path();
            if (!at || event.nativeEvent.isComposing) return;
            if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'z') {
              event.preventDefault();
              if (event.shiftKey) editor.tf.redo(); else editor.tf.undo();
            } else if (event.key === 'Enter') {
              event.preventDefault();
              editor.tf.setNodes({ collapsed: false }, { at });
              editor.tf.focus({ at: editor.api.start(at)! });
            }
          }} />}
      {!readOnly && <>
        <button type="button" title="在折叠块后继续（Ctrl+Enter）" aria-label="在折叠块后继续"
          onMouseDown={event => event.preventDefault()} onClick={() => { const at = path(); if (at) exitCollapse(editor, at); }}>
          <CornerDownRight size={16} />
        </button>
        <button type="button" title="取消折叠，保留正文" aria-label="取消折叠，保留正文"
          onMouseDown={event => event.preventDefault()} onClick={() => {
            const at = path();
            if (!at) return;
            editor.tf.withNewBatch(() => editor.tf.withoutNormalizing(() => {
              // Keep the title as a paragraph when removing the container.
              if (props.element.title) editor.tf.insertNodes({ type: 'p', children: [{ text: props.element.title }] }, { at: [...at, 0] });
              editor.tf.unwrapNodes({ at, match: node => node.type === COLLAPSE });
              editor.tf.select(editor.api.start(at)!);
            }));
            editor.tf.focus();
          }}><ChevronsUpDown size={16} /></button>
      </>}
    </div>
    <div className="codeyun-collapse-content" style={{ display: closed ? 'none' : undefined }}>
      {props.children}
    </div>
  </PlateElement>;
}

export function CollapseButton() {
  const editor = useEditorRef();
  const readOnly = useEditorReadOnly();
  if (readOnly) return null;
  return <ToolbarButton tooltip="插入折叠块（选区按完整块包裹）" aria-label="插入折叠块"
    onMouseDown={event => event.preventDefault()} onClick={() => insertCollapse(editor)}><ChevronRight /><span>折叠块</span></ToolbarButton>;
}

export const CollapseKit = [createPlatePlugin({
  key: COLLAPSE,
  node: { isElement: true, component: CollapseBlock },
  handlers: {
    onKeyDown: ({ editor, event }) => {
      if (!editor.selection || !RangeApi.isCollapsed(editor.selection) || event.nativeEvent.isComposing) return;
      const entry = editor.api.above({ match: node => node.type === COLLAPSE });
      if (!entry) return;
      const [, path] = entry;
      if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
        event.preventDefault();
        exitCollapse(editor, path);
        return true;
      }
      if (event.key === 'Backspace' && editor.api.isStart(editor.selection.anchor, path)) {
        // At the boundary, use the explicit unwrap action; don't merge the
        // container's children into a preceding paragraph and lose its schema.
        event.preventDefault();
        return true;
      }
    },
  },
}).overrideEditor(({ editor, tf: { normalizeNode } }) => ({
  transforms: {
    normalizeNode(entry) {
      const [node, path] = entry;
      if (ElementApi.isElement(node) && node.type === COLLAPSE) {
        if (!node.children.length) {
          editor.tf.insertNodes(paragraph(), { at: [...path, 0] });
          return;
        }
        const index = node.children.findIndex(child => !ElementApi.isElement(child) || editor.api.isInline(child));
        if (index !== -1) {
          editor.tf.wrapNodes(paragraph(), { at: [...path, index] });
          return;
        }
      }
      normalizeNode(entry);
    },
  },
}))];
