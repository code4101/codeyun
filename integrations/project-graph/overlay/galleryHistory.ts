import { Project, ProjectState } from '@/core/Project';
import { Settings } from '@/core/service/Settings';
import { HistoryManager } from '@/core/stage/stageManager/StageHistoryManager';
import { deserialize } from '@graphif/serializer';
import { captureStage, applyObjects } from './graphDocument';
import { changed, differences, objectsToStage, type Objects, type Change } from './graphObjects';

/** Native stage-only history would duplicate a stored task on undo. Keep one
 * delta history spanning canvas and gallery; gallery assets are never duplicated.
 * Collaborative sessions replace this service with their receipt-based history. */
export class GalleryHistory extends HistoryManager {
  private baseline: Objects;
  private current: Objects;
  private steps: Change[][] = [];
  private index = -1;
  constructor(private readonly graph: Project) {
    super(graph);
    this.baseline = this.current = captureStage(graph);
  }
  override recordStep() {
    const next = captureStage(this.graph), changes = differences(this.current, next);
    if (!changes.length) return;
    this.steps.splice(this.index + 1);
    this.steps.push(changes); this.index++;
    while (this.steps.length > Math.max(1, Settings.historySize)) {
      this.baseline = changed(this.baseline, this.steps.shift()!); this.index--;
    }
    this.current = next;
    this.graph.projectState = ProjectState.Unsaved;
    this.graph.emit('stage-commit');
  }
  private restore(next: Objects) {
    applyObjects(this.graph, captureStage(this.graph), next);
    this.current = next;
    this.graph.projectState = ProjectState.Unsaved;
    this.graph.emit('stage-commit');
  }
  override undo() {
    if (this.index < 0) return;
    const inverse = this.steps[this.index--].map(change => ({ ...change, before: change.after, after: change.before }));
    this.restore(changed(this.current, inverse));
  }
  override redo() { if (this.index + 1 < this.steps.length) this.restore(changed(this.current, this.steps[++this.index])); }
  override clearHistory() { this.steps = []; this.index = -1; this.baseline = this.current = captureStage(this.graph); }
  override get(index: number) {
    let state = this.baseline;
    for (const step of this.steps.slice(0, index + 1)) state = changed(state, step);
    return deserialize(objectsToStage(state), this.graph);
  }
}
