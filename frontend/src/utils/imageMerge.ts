export function extractImageSrcListFromHtml(html: string): string[] {
  if (!html) return [];
  const parser = new DOMParser();
  const doc = parser.parseFromString(html, 'text/html');
  const imgs = doc.querySelectorAll('img');
  const srcList: string[] = [];
  imgs.forEach(img => {
    if (img.src) srcList.push(img.src);
  });
  return srcList;
}

export async function mergeImagesToPngDataUrl(srcList: string[], gap: number): Promise<string> {
  const sources = (srcList || []).filter(Boolean);
  if (sources.length === 0) return '';

  const imageElements = await Promise.all(
    sources.map(src => {
      return new Promise<HTMLImageElement>((resolve, reject) => {
        const img = new Image();
        img.crossOrigin = 'Anonymous';
        img.onload = () => resolve(img);
        img.onerror = e => reject(e);
        img.src = src;
      });
    })
  );

  const maxWidth = Math.max(...imageElements.map(img => img.naturalWidth));
  const totalHeight =
    imageElements.reduce((acc, img) => acc + img.naturalHeight, 0) + (imageElements.length - 1) * gap;

  const canvas = document.createElement('canvas');
  canvas.width = maxWidth;
  canvas.height = totalHeight;
  const ctx = canvas.getContext('2d');
  if (!ctx) return '';

  ctx.fillStyle = '#ffffff';
  ctx.fillRect(0, 0, canvas.width, canvas.height);

  let currentY = 0;
  imageElements.forEach(img => {
    const x = (maxWidth - img.naturalWidth) / 2;
    ctx.drawImage(img, x, currentY);
    currentY += img.naturalHeight + gap;
  });

  return canvas.toDataURL('image/png', 0.9);
}
