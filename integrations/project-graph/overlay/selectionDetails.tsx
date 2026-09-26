import { atom, useAtomValue } from 'jotai';
import { Project, ProjectState } from '@/core/Project';
import { ControllerUtils } from '@/core/service/controlService/controller/concrete/utilsControl';
import { Entity } from '@/core/stage/stageObject/abstract/StageEntity';
import PlateDocumentEditor from './PlateDocumentEditor';
import { store } from '@/state';
import { InspectorPositionControl } from './inspectorLayout';

const inspected = atom<{ project: Project; entity: Entity | null; title: string; count: number; generation: number } | null>(null);
let generation = 0;
function entityTitle(entity: Entity | null) {
  return entity && 'text' in entity && typeof entity.text === 'string' ? entity.text : '节点正文';
}

function inspect(project: Project, entity: Entity | null, count: number) {
  store.set(inspected, { project, entity, title: entityTitle(entity), count, generation: ++generation });
}

/** Selection has no stable upstream event yet. Observe its public state as a lifecycle
 * service, comparing identities so undo/reload also replace the editor safely. */
export class SelectionDetailsService {
  static id = 'codeyunSelectionDetails';
  private nextCheck = 0;
  constructor(private readonly project: Project) { inspect(project, null, 0); }
  tick() {
    if (performance.now() < this.nextCheck) return;
    this.nextCheck = performance.now() + 50;
    const objects = this.project.stageManager.getStageObjects();
    const selected = objects.filter(item => item.isSelected);
    const current = store.get(inspected);
    // Selection and the open document are independent: blank clicks, multiple
    // selection and objects without a body leave the current editor mounted.
    // Resolve by UUID to rebind after undo, and release deleted objects safely.
    const retained = current?.entity
      ? objects.find(item => item.uuid === current.entity!.uuid)
      : null;
    const candidate = selected.length === 1 && selected[0] instanceof Entity
      ? selected[0] : retained;
    const entity = candidate instanceof Entity ? candidate : null;
    if (current?.entity !== entity) {
      inspect(this.project, entity, selected.length);
    } else if (current && current.title !== entityTitle(entity)) {
      // Updating a node label must not remount the body editor or reset its cursor.
      store.set(inspected, { ...current, title: entityTitle(entity) });
    }
  }
  dispose() { if (store.get(inspected)?.project === this.project) store.set(inspected, null); }
}

/** Replace only the public details-opening behavior; retain upstream editing tools.
 * Toolbar, Ctrl-double-click and selection all share this one inspector. */
export class FollowingControllerUtils extends ControllerUtils {
  static id = 'controllerUtils';
  constructor(private readonly hostProject: Project) { super(hostProject); }
  override editNodeDetails(entity: Entity) {
    if (store.get(inspected)?.entity !== entity) inspect(this.hostProject, entity, 1);
  }
}

export default function SelectionDetailsPanel() {
  const state = useAtomValue(inspected);
  const entity = state?.entity;
  const title = state?.title ?? '节点正文';
  return <aside data-codeyun-details className="absolute inset-0 flex flex-col bg-background text-foreground">
    <header className="flex items-center gap-2 shrink-0 border-b border-border px-3 py-2">
      <div className="min-w-0 flex-1 truncate text-sm font-medium" title={title}>{entity ? title || '未命名节点' : '节点正文'}</div>
      <InspectorPositionControl />
    </header>
    <div className="relative min-h-0 flex-1 overflow-auto">
      {state && entity ? <PlateDocumentEditor key={state.generation} value={entity.details}
        onChange={value => {
          // The callback captures its own node, never whichever node becomes selected later.
          if (!state.project.stageManager.getEntities().includes(entity) || value === entity.details) return;
          entity.details = value;
          state.project.syncAssociationManager.syncFrom(entity, 'details');
          state.project.projectState = ProjectState.Unsaved;
          state.project.historyManager.recordStep();
        }} /> : <p className="text-muted-foreground p-4 text-sm">{state && state.count > 1 ? '已选中多个对象，请单选一个节点查看正文。' : '单击一个节点，在这里查看和编辑正文。'}</p>}
    </div>
  </aside>;
}
