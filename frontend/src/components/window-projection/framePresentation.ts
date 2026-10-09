export interface ProjectionPixels {
  width: number
  height: number
  patches: { x: number; y: number; image: string }[]
}

/** Decode the complete update before touching visible pixels or geometry.
 * A failed or superseded frame leaves the last complete frame intact.
 * The synchronous commit prevents browser paints between changed tiles.
 */
export async function presentFrame(
  target: HTMLCanvasElement,
  frame: ProjectionPixels,
  isCurrent: () => boolean,
): Promise<boolean> {
  const images = await Promise.all(frame.patches.map(async patch => {
    const image = new Image()
    image.src = patch.image
    await image.decode()
    return image
  }))
  if (!isCurrent()) return false
  const context = target.getContext('2d')
  if (!context) throw new Error('无法绘制窗口画面')
  if (target.width !== frame.width || target.height !== frame.height) {
    target.width = frame.width
    target.height = frame.height
  }
  frame.patches.forEach((patch, index) => context.drawImage(images[index]!, patch.x, patch.y))
  return true
}
