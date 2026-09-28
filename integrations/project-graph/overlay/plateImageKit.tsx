import { useRef } from 'react';
import { ImagePlus } from 'lucide-react';
import { insertImageFromFiles } from '@platejs/media';
import { ImagePlugin } from '@platejs/media/react';
import { type TImageElement } from 'platejs';
import { PlateElement, type PlateElementProps, createPlatePlugin, useEditorRef, useEditorReadOnly } from 'platejs/react';
import { FixedToolbar } from '@/components/ui/fixed-toolbar';
import { FixedToolbarButtons } from '@/components/ui/fixed-toolbar-buttons';
import { ToolbarButton } from '@/components/ui/toolbar';
import { CollapseButton } from './plateCollapseKit';

// Keep images in the Plate value as data URLs. PG persists that value in PRG;
// the shared notes editor persists it through its own document API.
function BodyImage(props: PlateElementProps<TImageElement>) {
  return <PlateElement {...props}>
    <img src={props.element.url} alt={props.element.name as string || ''}
      contentEditable={false} style={{ maxWidth: '100%', height: 'auto' }} />
    {props.children}
  </PlateElement>;
}

function ImageButton() {
  const editor = useEditorRef();
  const readOnly = useEditorReadOnly();
  const input = useRef<HTMLInputElement>(null);
  if (readOnly) return null;
  return <>
    <ToolbarButton tooltip="插入图片" aria-label="插入图片"
      onMouseDown={event => event.preventDefault()} onClick={() => input.current?.click()}>
      <ImagePlus />
    </ToolbarButton>
    <input ref={input} type="file" accept="image/*" multiple hidden onChange={event => {
      if (event.currentTarget.files?.length) insertImageFromFiles(editor, event.currentTarget.files);
      event.currentTarget.value = '';
    }} />
  </>;
}

export const BodyImageKit = [ImagePlugin.withComponent(BodyImage)];
export const BodyToolbarKit = [createPlatePlugin({
  key: 'fixed-toolbar',
  render: { beforeEditable: () => <FixedToolbar><ImageButton /><CollapseButton /><FixedToolbarButtons /></FixedToolbar> },
})];
