import { BasicBlocksKit } from "@/components/editor/plugins/basic-blocks-kit";
import { BasicMarksKit } from "@/components/editor/plugins/basic-marks-kit";
import { CodeBlockKit } from "@/components/editor/plugins/code-block-kit";
import { BodyImageKit, BodyToolbarKit } from './plateImageKit';
import { FloatingToolbarKit } from "@/components/editor/plugins/floating-toolbar-kit";
import { FontKit } from "@/components/editor/plugins/font-kit";
import { LinkKit } from "@/components/editor/plugins/link-kit";
import { ListKit } from "@/components/editor/plugins/list-kit";
import { MathKit } from "@/components/editor/plugins/math-kit";
import { TableKit } from "@/components/editor/plugins/table-kit";
import { Editor, EditorContainer } from "@/components/ui/editor";
import { type TLinkElement, Value } from "platejs";
import { Plate, PlateElement, type PlateElementProps, useEditorReadOnly, usePlateEditor } from "platejs/react";
import { getLinkAttributes } from '@platejs/link';
import { MarkdownKit } from '@/components/editor/plugins/markdown-kit';
import { insertImageFromFiles } from '@platejs/media';
import { CollapseKit } from './plateCollapseKit';
import { SuggestionPlugin } from '@platejs/suggestion/react';
import { useLayoutEffect } from 'react';

function BodyLink(props: PlateElementProps<TLinkElement>) {
  const readOnly = useEditorReadOnly();
  // Resolve browser-relative URLs before Plate's scheme validation; the stored
  // document stays unchanged and unsafe schemes still lose their href.
  let url = props.element.url;
  try { url = new URL(url, location.href).href; } catch { /* Plate rejects invalid URLs. */ }
  const attributes = getLinkAttributes(props.editor, { ...props.element, url });
  return <PlateElement {...props} as="a"
    className="text-primary decoration-primary font-medium underline underline-offset-4"
    attributes={{ ...props.attributes, ...attributes,
      // Stored targets (including _self and named frames) never navigate the body.
      target: '_blank', rel: 'noopener noreferrer',
      onMouseOver: event => event.stopPropagation(),
      onClick: event => {
        if (!readOnly || !attributes.href) return;
        event.preventDefault();
        window.open(event.currentTarget.href, '_blank', 'noopener,noreferrer');
      },
    }}>{props.children}</PlateElement>;
}

const BodyLinkKit = LinkKit.map(plugin => plugin.configure({
  options: { defaultLinkAttributes: { target: '_blank', rel: 'noopener noreferrer' } },
}).withComponent(BodyLink));

/** Shared body editor: no project, node identity, filesystem or save queue.
 * Both PG nodes and Notes supply their own content and persistence callbacks.
 * Reuses upstream plugin kits; verify this small composition when upgrading PG. */
export default function PlateDocumentEditor({ value, onChange, readOnly = false, onReady }: {
  value: Value; onChange: (value: Value) => void; readOnly?: boolean;
  onReady?: (getValue: () => Value) => void;
}) {
  const editor = usePlateEditor({
    plugins: [
      ...FloatingToolbarKit,
      ...BodyToolbarKit,
      ...BodyImageKit,
      ...CollapseKit,
      // Upstream insert/convert toolbar transforms call withoutSuggestions,
      // even when suggestion mode is off. Supply that public API dependency.
      SuggestionPlugin,
      ...BasicMarksKit,
      ...BasicBlocksKit,
      ...FontKit,
      ...TableKit,
      ...MathKit,
      ...CodeBlockKit,
      ...ListKit,
      ...BodyLinkKit,
      ...MarkdownKit,
    ],
    value,
  });
  useLayoutEffect(() => { onReady?.(() => editor.children); }, [editor, onReady]);
  return <Plate editor={editor} readOnly={readOnly} onValueChange={({ value }) => { if (!readOnly) onChange(value); }}>
    <EditorContainer><Editor variant="nodeDetails" onPaste={event => {
      const files = event.clipboardData.files;
      if (!readOnly && Array.from(files).some(file => file.type.startsWith('image/'))) {
        // Handle binary clipboard images before text/Markdown deserializers.
        event.preventDefault();
        insertImageFromFiles(editor, files);
        return true;
      }
    }} /></EditorContainer>
  </Plate>;
}
