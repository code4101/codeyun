/** PNG is the common format for browser and Windows image clipboards. */
export const MAX_IMAGE_BYTES = 10 * 1024 * 1024

export async function imageDataUrl(file: Blob): Promise<string> {
  if (file.size > MAX_IMAGE_BYTES) throw new Error('图片不能超过 10 MB')
  if (!['image/png', 'image/jpeg', 'image/webp', 'image/bmp'].includes(file.type)) throw new Error('请选择 PNG、JPEG、WebP 或 BMP 图片')
  const bitmap = await createImageBitmap(file)
  try {
    if (bitmap.width * bitmap.height > 16_000_000) throw new Error('图片不能超过 1600 万像素')
    const canvas = document.createElement('canvas')
    canvas.width = bitmap.width; canvas.height = bitmap.height
    canvas.getContext('2d')!.drawImage(bitmap, 0, 0)
    const png = await canvasPng(canvas)
    return await new Promise<string>((resolve, reject) => {
      const reader = new FileReader()
      reader.onload = () => resolve(String(reader.result))
      reader.onerror = () => reject(new Error('图片读取失败'))
      reader.readAsDataURL(png)
    })
  } finally { bitmap.close() }
}

export function canvasPng(canvas: HTMLCanvasElement): Promise<Blob> {
  return new Promise((resolve, reject) => canvas.toBlob(blob => {
    if (!blob) reject(new Error('截图失败'))
    else if (blob.size > MAX_IMAGE_BYTES) reject(new Error('图片不能超过 10 MB'))
    else resolve(blob)
  }, 'image/png'))
}

export async function copyPng(blob: Promise<Blob>): Promise<void> {
  if (!navigator.clipboard?.write || typeof ClipboardItem === 'undefined') {
    void blob.catch(() => undefined)
    throw new Error('当前浏览器无法复制图片，请下载图片；复制需要 HTTPS 和剪贴板权限')
  }
  // Register the write during the click gesture, before PNG conversion finishes.
  await navigator.clipboard.write([new ClipboardItem({ 'image/png': blob })])
}

export function downloadImage(image: string): void {
  const link = document.createElement('a')
  link.href = image; link.download = `窗口图片-${Date.now()}.png`; link.click()
}
