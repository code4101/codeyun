import { Project } from '@/core/Project';
import { StageObject } from '@/core/stage/stageObject/abstract/StageObject';
import { serialize, serializable } from '@graphif/serializer';
import { stageToObjects, type Objects } from './graphObjects';
import { NodeTimestamps, nodeTimes, completeNodeTimes, type NodeTimes } from './nodeTimestamps';
import { galleryObjects, applyGallery } from './galleryArchive';

// Extend the public serializer contract once; no upstream constructors or
// private serializer metadata are patched. Rehydration explicitly restores
// these fields because native constructors only accept their own properties.
serializable(StageObject.prototype, 'createdAt');
serializable(StageObject.prototype, 'updatedAt');
const timelines = new WeakMap<Project, NodeTimestamps>();
function timeline(project: Project) {
  let value = timelines.get(project);
  if (!value) { value = new NodeTimestamps(); timelines.set(project, value); }
  return value;
}
export function applyNodeTimes(project: Project, objects: Objects) {
  for (const node of project.stage) {
    const target = node as StageObject & NodeTimes;
    delete target.createdAt; delete target.updatedAt;
    if (objects[node.uuid]) Object.assign(target, nodeTimes(objects[node.uuid]));
  }
}
/** Seed after native init and persist completion without counting it as an edit. */
export function initializeNodeTimeline(project: Project, archived: Objects, fileCreatedAt?: number) {
  const before = { ...archived, ...galleryObjects(project) };
  const completed = completeNodeTimes(before, fileCreatedAt);
  applyNodeTimes(project, completed);
  applyGallery(project, completed);
  timeline(project).adopt(stageToObjects(serialize(project.stage)));
  return JSON.stringify(before) !== JSON.stringify(completed);
}
export function captureNodes(project: Project): Objects {
  const objects = timeline(project).capture(stageToObjects(serialize(project.stage)));
  applyNodeTimes(project, objects);
  return objects;
}
export function projectNodeEdit(project: Project, before: Objects, next: Objects): Objects {
  return timeline(project).project(before, next);
}
export function adoptNodeTimeline(project: Project, objects: Objects) {
  applyNodeTimes(project, objects);
  timeline(project).adopt(objects);
}
