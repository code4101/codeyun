import { Project } from '@/core/Project';
import { decode, encode } from '@msgpack/msgpack';
import { ZipReader, ZipWriter, Uint8ArrayReader, Uint8ArrayWriter } from '@zip.js/zip.js';
import { GALLERY_INDEX, GALLERY_ITEM } from '../../../frontend/src/plugins/modules/project-graph/gallery.ts';
import type { Objects } from './graphObjects';

/** Archive module belongs to one Project, never to browser/global storage.
 * Unknown extension members survive our saves; native PG may discard them.
 * Contents and assets remain in one portable archive. */
const states = new WeakMap<Project, Objects>();
const generations = new WeakMap<Project, number>();
const extras = new WeakMap<Project, Map<string, Uint8Array>>();
export function galleryObjects(project: Project): Objects { return states.get(project) ?? {}; }
export function galleryGeneration(project: Project) { return generations.get(project) ?? 0; }
export function applyGallery(project: Project, objects: Objects) {
  states.set(project, Object.fromEntries(Object.entries(objects).filter(([key]) => key.startsWith('@gallery:'))));
  generations.set(project, galleryGeneration(project) + 1);
}
export async function readGallery(project: Project, bytes: Uint8Array | null) {
  const values: Objects = {}, opaque = new Map<string, Uint8Array>();
  if (bytes) {
    const reader = new ZipReader(new Uint8ArrayReader(bytes));
    try {
      for (const entry of await reader.getEntries()) {
        if (entry.directory) continue;
        if (entry.filename === 'gallery/index.msgpack') values[GALLERY_INDEX] = decode(await entry.getData!(new Uint8ArrayWriter())) as any;
        else if (/^gallery\/items\/[^/\\]+\.msgpack$/.test(entry.filename)) {
          const id = entry.filename.slice('gallery/items/'.length, -'.msgpack'.length);
          if (values[GALLERY_ITEM + id]) throw new Error('重复图库成员');
          values[GALLERY_ITEM + id] = decode(await entry.getData!(new Uint8ArrayWriter())) as any;
        } else if (!['stage.msgpack', 'tags.msgpack', 'reference.msgpack', 'metadata.msgpack', 'README.md', 'thumbnail.png'].includes(entry.filename) && !entry.filename.startsWith('attachments/'))
          opaque.set(entry.filename, await entry.getData!(new Uint8ArrayWriter()));
      }
    } finally { await reader.close(); }
  }
  if (values[GALLERY_INDEX] && values[GALLERY_INDEX].version !== 1) throw new Error('不支持的图库版本，请使用匹配的 CodeYun 版本');
  if (Object.keys(values).length && !values[GALLERY_INDEX]) throw new Error('图库缺少目录，文件未修改');
  extras.set(project, opaque);
  applyGallery(project, values);
}
export async function writeGallery(project: Project, native: Uint8Array<ArrayBuffer>, gallery: Objects = galleryObjects(project)): Promise<Uint8Array<ArrayBuffer>> {
  const opaque = extras.get(project);
  if (!Object.keys(gallery).length && !opaque?.size) return native;
  // Capture extension bytes before awaits, matching native getFileContent's
  // snapshot semantics. Changes in flight stay dirty for the next save.
  const members = new Map(opaque);
  for (const [key, value] of Object.entries(gallery)) members.set(
    key === GALLERY_INDEX ? 'gallery/index.msgpack' : `gallery/items/${key.slice(GALLERY_ITEM.length)}.msgpack`, encode(value));
  const reader = new ZipReader(new Uint8ArrayReader(native));
  const output = new Uint8ArrayWriter(), writer = new ZipWriter(output, { level: 0 });
  try {
    for (const entry of await reader.getEntries()) {
      if (!entry.directory && !members.has(entry.filename)) await writer.add(entry.filename, new Uint8ArrayReader(await entry.getData!(new Uint8ArrayWriter())), { level: 0 });
    }
    for (const [filename, data] of members) await writer.add(filename, new Uint8ArrayReader(data), { level: 0 });
    await writer.close();
    return new Uint8Array(await output.getData());
  } finally { await reader.close(); }
}
