/** Browser implementation of the small Tauri Image surface used by clipboard export. */
export class Image {
  private constructor(private canvas: HTMLCanvasElement) {}
  static async fromBytes(bytes: ArrayBuffer | Uint8Array | number[]) {
    const bitmap = await createImageBitmap(new Blob([new Uint8Array(bytes as ArrayBuffer)]));
    const canvas = document.createElement('canvas');
    canvas.width = bitmap.width; canvas.height = bitmap.height;
    canvas.getContext('2d')!.drawImage(bitmap, 0, 0);
    bitmap.close();
    return new Image(canvas);
  }
  static async new(rgba: Uint8Array | number[], width: number, height: number) {
    const canvas = document.createElement('canvas');
    canvas.width = width; canvas.height = height;
    canvas.getContext('2d')!.putImageData(new ImageData(new Uint8ClampedArray(rgba), width, height), 0, 0);
    return new Image(canvas);
  }
  async size() { return { width: this.canvas.width, height: this.canvas.height }; }
  async rgba() { return new Uint8Array(this.canvas.getContext('2d')!.getImageData(0, 0, this.canvas.width, this.canvas.height).data); }
  async close() {}
  async png(): Promise<Blob> {
    return new Promise((resolve, reject) => this.canvas.toBlob(blob => blob ? resolve(blob) : reject(new Error('图片编码失败')), 'image/png'));
  }
}
