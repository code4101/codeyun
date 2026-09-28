import assert from 'node:assert/strict';
import test from 'node:test';
import { platePreviewText, createPlatePreviewCache } from '../../integrations/project-graph/overlay/platePreview.ts';

test('media preview never reads encoded data; nested title, text and links survive', () => {
  const image = { type: 'img', children: [{ text: '' }], get url() { throw new Error('binary read'); } };
  const value = [{ type: 'codeyun-collapse', title: '标题', collapsed: true, children: [
    { type: 'p', children: [{ text: '说明 ' }, { type: 'a', url: 'https://example.com', children: [{ text: '链接' }] }] }, image,
  ] }];
  assert.equal(platePreviewText(value), '标题\n说明 链接\n[图片]');
});

test('long text and deep/empty trees have bounded output and traversal', () => {
  assert.equal(platePreviewText([{ text: 'x'.repeat(100_000) }], 80), 'x'.repeat(79) + '…');
  let tree: unknown = { text: 'deep' };
  for (let i = 0; i < 10_000; i++) tree = { type: 'p', children: [tree] };
  assert.equal(platePreviewText([tree]), '…');
  const children = Array.from({ length: 1000 }, () => ({ text: '' }));
  Object.defineProperty(children, 300, { get() { throw new Error('unbounded traversal'); } });
  assert.equal(platePreviewText(children), '…');
  assert.equal(platePreviewText([{ text: '12345' }], 5), '12345');
});

test('cache uses immutable value identity and refreshes when edits replace it', () => {
  const preview = createPlatePreviewCache();
  let reads = 0;
  const value = [{ get text() { reads++; return 'old'; } }];
  assert.equal(preview(value), 'old');
  const first = reads;
  for (let i = 0; i < 100; i++) assert.equal(preview(value), 'old');
  assert.equal(reads, first);
  assert.equal(preview([{ text: 'new' }]), 'new');
});
