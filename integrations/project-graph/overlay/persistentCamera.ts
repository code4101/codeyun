import { Camera } from '@/core/stage/Camera';
import { Project } from '@/core/Project';
import { Vector } from '@graphif/data-structures';
import { createViewStateStore } from './viewState';

/** Restore on the first successful upstream reset, after canvas dimensions exist.
 * Subsequent user-requested resets retain their normal behavior. */
export class PersistentCamera extends Camera {
  static id = 'camera';
  private viewStore?: ReturnType<typeof createViewStateStore>;
  private initialized = false;
  private nextSave = 0;
  constructor(private readonly graph: Project) { super(graph); }
  configure(key: string) { this.viewStore = createViewStateStore(key, localStorage); }
  override reset() {
    super.reset();
    if (this.initialized || !this.graph.renderer.w || !this.graph.renderer.h) return;
    this.initialized = true;
    const saved = this.viewStore?.read();
    if (saved) {
      this.currentScale = this.targetScale = saved.scale;
      this.location = new Vector(saved.x, saved.y);
      this.targetLocationByScale = this.location.clone();
    }
  }
  saveView() {
    if (this.initialized) this.viewStore?.write({ scale: this.currentScale, x: this.location.x, y: this.location.y });
  }
  override tick() {
    super.tick();
    if (performance.now() >= this.nextSave) { this.nextSave = performance.now() + 250; this.saveView(); }
  }
}
