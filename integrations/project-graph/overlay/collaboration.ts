import { Project, ProjectState } from '@/core/Project';
import { Settings } from '@/core/service/Settings';
import { HistoryManager } from '@/core/stage/stageManager/StageHistoryManager';
import { Vector } from '@graphif/data-structures';
import { toast } from 'sonner';
import { capture, applyObjects } from './graphDocument';
import { rebaseLocal } from './graphObjects';
import { ObjectSession, type Credentials } from '../../../frontend/src/collaboration/ObjectSession.ts';

/** Own operations are undone with their object preconditions. Native whole-file
 * history is deliberately replaced so undo cannot erase somebody else's edit. */
export class ObjectHistory extends HistoryManager {
  session?: ObjectCollaboration;
  constructor(private readonly graph: Project) { super(graph); }
  override recordStep() { this.graph.emit('stage-commit'); }
  override undo() { void this.session?.undo().catch(error => toast.error(String(error))); }
  override redo() { void this.session?.redo().catch(error => toast.error(String(error))); }
}

/** PG adapter: canvas gestures, native history and presence rendering only. */
export class ObjectCollaboration extends ObjectSession {
  private replaying = false;
  private pointerHeld = false;
  private gesture?: { down: PointerEvent; move?: PointerEvent; up?: PointerEvent };
  private lastCursor = 0;
  private panel = document.createElement('div');
  private markers = document.createElement('div');
  private frame = 0;
  constructor(private readonly project: Project, readOnly: boolean,
    credentials: () => Promise<Credentials>, draftKey: string,
    publish: (type: string, payload: any) => void) {
    super(readOnly, credentials, draftKey, publish, {
      capture: () => capture(project), apply: (before, after) => applyObjects(project, before, after), rebase: rebaseLocal,
      isStructuralObject: id => id === '@order',
      setEditable: editable => { Settings.viewerMode = !editable; },
      setSaveState: state => { project.projectState = state === 'saved' ? ProjectState.Saved : ProjectState.Unsaved; },
    });
    this.panel.className = 'codeyun-collaboration'; this.panel.setAttribute('role', 'status');
    this.markers.className = 'codeyun-collaborators'; document.body.append(this.panel, this.markers);
    project.disposeService('historyManager'); project.loadService(ObjectHistory);
    (project.historyManager as ObjectHistory).session = this;
    project.canvas.element.addEventListener('pointerdown', this.pointerDown, true);
    project.canvas.element.addEventListener('pointermove', this.pointerMove, true);
    window.addEventListener('pointerup', this.pointerUp, true);
    this.draw(); this.start();
  }
  protected override retainedObjects() { return this.gestureObjects(); }
  protected override canFlush() { return !this.pointerHeld; }
  private selected(): string[] { return this.project.stage.filter(item => item.isSelected).map(item => item.uuid); }
  private gestureObjects(target?: any): string[] {
    const objects = this.project.stage.filter(item => item.isSelected);
    if (target && !objects.includes(target)) objects.push(target);
    const ids = new Set<string>();
    const visit = (object: any) => { if (!object || ids.has(object.uuid)) return; ids.add(object.uuid); if (Array.isArray(object.children)) object.children.forEach(visit); };
    objects.forEach(visit); return [...ids];
  }
  private pointerDown = (event: PointerEvent) => {
    if (this.replaying || event.button !== 0 || this.readOnly) return;
    const view = this.project.canvas.clientToView(event.clientX, event.clientY);
    const target = this.project.controllerUtils.getClickedStageObject(this.project.renderer.transformView2World(view));
    const ids = this.gestureObjects(target);
    this.pointerHeld = true;
    if (this.connected && !this.blocked && ids.every(id => this.tokens[id])) return;
    event.preventDefault(); event.stopImmediatePropagation();
    const gesture: NonNullable<typeof this.gesture> = this.gesture = { down: event };
    void this.acquire(ids).then(() => {
      this.replaying = true;
      const dispatch = (source: PointerEvent) => this.project.canvas.element.dispatchEvent(new PointerEvent(source.type, source));
      dispatch(gesture.down); if (gesture.move) dispatch(gesture.move); if (gesture.up) dispatch(gesture.up);
    }).catch(error => toast.warning(String(error))).finally(() => { this.replaying = false; this.gesture = undefined; });
  };
  private pointerMove = (event: PointerEvent) => {
    if (this.gesture && !this.replaying) { this.gesture.move = event; event.stopImmediatePropagation(); }
    if (performance.now() - this.lastCursor < 100) return;
    this.lastCursor = performance.now();
    const point = this.project.renderer.transformView2World(this.project.canvas.clientToView(event.clientX, event.clientY));
    this.send({ type: 'presence', cursor: { x: point.x, y: point.y }, selection: this.selected() });
  };
  private pointerUp = (event: PointerEvent) => {
    if (this.replaying) return;
    this.pointerHeld = false;
    if (this.gesture) { this.gesture.up = event; event.stopImmediatePropagation(); }
    else queueMicrotask(() => void this.flush().catch(() => {}));
  };

  async editDetails(id: string, edit: () => void) { await this.acquire([id]); edit(); await this.flush(); }
  protected override render() {
    this.panel.replaceChildren();
    const status = document.createElement('span'); status.textContent = this.message; this.panel.append(status);
    for (const peer of this.peers) {
      const person = document.createElement('span'); person.textContent = peer.name + (peer.id === this.peerId ? '（我）' : '');
      person.style.borderColor = peer.color; person.className = 'collaborator'; this.panel.append(person);
    }
    if (this.blocked) {
      const recover = document.createElement('button'); recover.textContent = '下载草稿并重新同步';
      recover.onclick = async () => {
        this.publish('exported', { bytes: await this.project.getFileContent({ includeThumbnail: false }) });
        this.resetFromServer();
      };
      this.panel.append(recover);
    }
  }
  private draw = () => {
    if (this.disposed) return;
    this.markers.replaceChildren();
    const label = (x: number, y: number, text: string, color: string, kind: string) => {
      const point = this.project.canvas.viewToClient(this.project.renderer.transformWorld2View(new Vector(x, y)));
      const item = document.createElement('div'); item.className = kind; item.textContent = text;
      Object.assign(item.style, { left: `${point.x}px`, top: `${point.y}px`, color, borderColor: color }); this.markers.append(item);
    };
    for (const peer of this.peers) if (peer.id !== this.peerId && peer.cursor) label(peer.cursor.x, peer.cursor.y, `↖ ${peer.name}`, peer.color, 'collaboration-cursor');
    for (const [id, lease] of Object.entries(this.locks)) {
      if (lease.peerId === this.peerId) continue;
      const peer = this.peers.find(item => item.id === lease.peerId), object = this.project.stage.find(item => item.uuid === id);
      const shape = object?.collisionBox.shapes[0]?.getRectangle();
      if (peer && shape) label(shape.location.x, shape.location.y, `${peer.name} 正在编辑`, peer.color, 'collaboration-lock');
    }
    this.frame = requestAnimationFrame(this.draw);
  };
  override dispose() {
    super.dispose(); cancelAnimationFrame(this.frame);
    this.project.canvas.element.removeEventListener('pointerdown', this.pointerDown, true);
    this.project.canvas.element.removeEventListener('pointermove', this.pointerMove, true);
    window.removeEventListener('pointerup', this.pointerUp, true);
    this.panel.remove(); this.markers.remove();
  }
}
