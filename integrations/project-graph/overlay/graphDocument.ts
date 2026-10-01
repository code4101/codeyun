import { Project } from '@/core/Project';
import { deserialize, serialize } from '@graphif/serializer';
import mime from 'mime';
import { differences, objectsToStage, references, stageToObjects, type Objects } from './graphObjects';
import { galleryObjects, applyGallery } from './galleryArchive';

/** Synchronous content boundary used by commands and native undo. Attachments
 * are document-wide and unchanged by transfers, so history holds no asset copies. */
export function captureStage(project: Project): Objects {
  return { ...stageToObjects(serialize(project.stage)), ...galleryObjects(project) };
}

const attachments = new WeakMap<Blob, Promise<string>>();
function encode(blob: Blob) {
  if (!attachments.has(blob)) attachments.set(blob, blob.arrayBuffer().then(buffer => {
    const bytes = new Uint8Array(buffer); let value = '';
    for (let i = 0; i < bytes.length; i += 8192) value += String.fromCharCode(...bytes.subarray(i, i + 8192));
    return btoa(value);
  }));
  return attachments.get(blob)!;
}
export async function capture(project: Project): Promise<Objects> {
  const blobs: Objects = {};
  for (const [id, blob] of project.attachments) blobs[`@attachment:${id}.${mime.getExtension(blob.type)}`] = { value: await encode(blob) };
  const objects = captureStage(project);
  for (const [key, value] of Object.entries({ '@tags': project.tags, '@references': project.references, '@metadata': project.metadata }))
    objects[key] = { value: structuredClone(value) };
  if (project.readme) objects['@readme'] = { value: project.readme };
  return { ...objects, ...blobs };
}

/** Preserve all unchanged native instances, especially those held by an active
 * text editor. Rehydrate only changed objects plus their reference closure, then
 * reconnect public serialized references to the retained instances. */
export function applyObjects(project: Project, before: Objects, next: Objects) {
  const changes = differences(before, next), changedIds = new Set(changes.map(change => change.id));
  if ([...changedIds].some(id => id.startsWith('@gallery:'))) applyGallery(project, next);
  const existing = new Map(project.stage.map(item => [item.uuid, item]));
  const closure = new Set<string>();
  function visit(id: string) {
    if (closure.has(id) || !next[id] || id.startsWith('@')) return;
    closure.add(id); references(next[id]).forEach(visit);
  }
  for (const id of changedIds) visit(id);
  const subset: Objects = Object.fromEntries([...closure].map(id => [id, next[id]]));
  subset['@order'] = { value: (next['@order'].value as string[]).filter(id => closure.has(id)) };
  const hydrated = new Map<string, any>((deserialize(objectsToStage(subset), project) as any[]).map(item => [item.uuid, item]));
  const instances = new Map(existing);
  for (const id of changedIds) {
    if (id.startsWith('@')) continue;
    const old = existing.get(id), replacement = hydrated.get(id);
    if (replacement) { replacement.isSelected = old?.isSelected ?? false; instances.set(id, replacement); }
    else instances.delete(id);
    if (old && old !== replacement) void old.dispose?.();
  }
  function rebind(native: any, canonical: any): any {
    if (!canonical || typeof canonical !== 'object') return native;
    if (Object.keys(canonical).length === 1 && canonical.$graphRef) return instances.get(canonical.$graphRef);
    if (Array.isArray(canonical)) return canonical.map((item, index) => rebind(native?.[index], item));
    if (native && typeof native === 'object') for (const [key, value] of Object.entries(canonical))
      if (key !== '_' && references(value).length) native[key] = rebind(native[key], value);
    return native;
  }
  for (const [id, instance] of instances) rebind(instance, next[id]);
  project.stage = (next['@order'].value as string[]).map(id => instances.get(id)!);
  if (changedIds.has('@tags')) project.tags = next['@tags']?.value ?? [];
  if (changedIds.has('@references')) project.references = next['@references']?.value ?? { sections: {}, files: [] };
  if (changedIds.has('@metadata') && next['@metadata']) project.metadata = next['@metadata'].value;
  if (changedIds.has('@readme')) project.readme = next['@readme']?.value;
  for (const id of changedIds) if (id.startsWith('@attachment:')) {
    const filename = id.slice(12), uuid = filename.slice(0, filename.lastIndexOf('.'));
    if (!next[id]) project.attachments.delete(uuid);
    else {
      const data = Uint8Array.from(atob(next[id].value), c => c.charCodeAt(0));
      const blob = new Blob([data], { type: mime.getType(filename) ?? 'application/octet-stream' });
      attachments.set(blob, Promise.resolve(next[id].value)); project.attachments.set(uuid, blob);
    }
  }
  project.stageManager.updateReferences();
  project.controller.resetCountdownTimer();
  project.renderer.tick();
}
