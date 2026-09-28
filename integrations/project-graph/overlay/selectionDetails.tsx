import { Project, ProjectState } from '@/core/Project';
import { ControllerUtils } from '@/core/service/controlService/controller/concrete/utilsControl';
import { Entity } from '@/core/stage/stageObject/abstract/StageEntity';
import type { Value } from 'platejs';
import { emptyPlateValue, storedPlateValue } from './plateValue';

let enabled = false;
let publish: (payload: unknown) => void = () => {};
let previous: { entity: Entity | null; title: string; value: Value | undefined } | undefined;
let access: (id: string) => { readOnly: boolean; lockedBy?: string } = () => ({ readOnly: false });
let previousAccess = '';
export function configureDetailsAccess(callback: typeof access) { access = callback; previous = undefined; }
export function configureDetails(active: boolean, callback?: typeof publish) {
  enabled = active;
  if (callback) publish = callback;
  previous = undefined;
}

/** Host owns the tool; the graph exposes selection snapshots and node-scoped edits. */
export class SelectionDetailsService {
  static id = 'codeyunSelectionDetails';
  private nextCheck = 0;
  private currentId: string | null = null;
  constructor(private readonly project: Project) {}
  tick() {
    if (!enabled || performance.now() < this.nextCheck) return;
    this.nextCheck = performance.now() + 50;
    const selected = this.project.stageManager.getStageObjects().filter(item => item.isSelected);
    if (selected.length === 1 && selected[0] instanceof Entity) this.currentId = selected[0].uuid;
    // The body tool follows the last individually selected object, not the
    // transient canvas selection. Resolve by ID so deletion and undo/redo cannot
    // leave an editable stale object; each project owns its own retained target.
    const entity = this.currentId
      ? this.project.stageManager.getEntities().find(item => item.uuid === this.currentId) ?? null
      : null;
    if (!entity) this.currentId = null;
    const title = entity && 'text' in entity && typeof entity.text === 'string' ? entity.text : '正文';
    const permission = entity ? access(entity.uuid) : { readOnly: true };
    const permissionKey = JSON.stringify(permission);
    // Plate and upstream undo/redo replace the value. Compare its identity before
    // crossing the bridge; never stringify every embedded image on every tick.
    if (previous?.entity === entity && previous.title === title && previous.value === entity?.details && previousAccess === permissionKey) return;
    previousAccess = permissionKey;
    previous = { entity, title, value: entity?.details };
    const snapshot = entity ? {
      id: entity.uuid,
      title,
      value: entity.details.length ? entity.details : emptyPlateValue(),
      ...permission,
    } : null;
    publish(snapshot);
  }
  dispose() { this.currentId = null; previous = undefined; }
}

export function updateNodeDetails(project: Project, id: string, value: Value) {
  const entity = project.stageManager.getEntities().find(item => item.uuid === id);
  if (!entity || !Array.isArray(value)) return;
  const content = storedPlateValue(value);
  if (JSON.stringify(entity.details) === JSON.stringify(content)) return;
  entity.details = content;
  // Acknowledging our own edit by loading it back can overwrite newer typing
  // already queued in the body iframe. Only publish external changes or selection.
  if (previous?.entity === entity) previous.value = content;
  project.syncAssociationManager.syncFrom(entity, 'details');
  project.projectState = ProjectState.Unsaved;
  project.historyManager.recordStep();
}

/** Details are exclusively controlled by the host activity bar. */
export class FollowingControllerUtils extends ControllerUtils {
  static id = 'controllerUtils';
  override editNodeDetails(_entity: Entity) {}
}
