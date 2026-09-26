import { Image } from './image';
export const readText = () => navigator.clipboard.readText();
export const writeText = (text: string) => navigator.clipboard.writeText(text);
export async function readImage() {
  for (const item of await navigator.clipboard.read()) {
    const type = item.types.find(type => type.startsWith('image/'));
    if (type) return Image.fromBytes(await (await item.getType(type)).arrayBuffer());
  }
  throw new Error('剪贴板中没有图片');
}
export async function writeImage(image: Image | ArrayBuffer | Uint8Array) {
  const value = image instanceof Image ? image : await Image.fromBytes(image);
  await navigator.clipboard.write([new ClipboardItem({ 'image/png': await value.png() })]);
}
