import { Project } from '@/core/Project';
import { EntityRenderer } from '@/core/render/canvas2d/entityRenderer/EntityRenderer';
import { Settings } from '@/core/service/Settings';
import { Entity } from '@/core/stage/stageObject/abstract/StageEntity';
import { Vector } from '@graphif/data-structures';
import { createPlatePreviewCache } from './platePreview';

/** Replace only the public canvas-summary entry point. The body editor and
 * PRG data retain their complete recursive values, formatting and media. */
export class BodyPreviewRenderer extends EntityRenderer {
  static id = 'entityRenderer';
  private readonly preview = createPlatePreviewCache();
  constructor(private readonly graph: Project) { super(graph); }

  override renderEntityDetails(entity: Entity) {
    const scale = this.graph.camera.currentScale;
    if (Settings.entityDetailsFontSize * scale <= 2 && !entity.isMouseHover && !entity.isSelected) return;
    if (!Settings.alwaysShowDetails && !entity.isMouseHover) return;
    if (Settings.entityDetailsLinesLimit <= 0) return;
    const text = this.preview(entity.details);
    if (!text) return;
    const rectangle = entity.collisionBox.getRectangle();
    this.graph.textRenderer.renderMultiLineText(
      text,
      this.graph.renderer.transformWorld2View(rectangle.location.add(new Vector(0, rectangle.size.y))),
      Settings.entityDetailsFontSize * scale,
      Math.max(Settings.entityDetailsWidthLimit * scale, rectangle.size.x * scale),
      this.graph.stageStyleManager.currentStyle.NodeDetailsText,
      1.2,
      Settings.entityDetailsLinesLimit,
    );
  }
}
