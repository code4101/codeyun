import {
  isPdfGalleryItem,
  type GalleryImage,
} from './imageGallery.ts';

export const isMasonryRenderable = (image: GalleryImage) =>
  isPdfGalleryItem(image)
  || Boolean(image.url && (image.urlVariant === 'thumbnail' || image.urlVariant === 'full'))
  || Boolean(image.thumbnailFailed);

export const getKnownMasonryAspectRatio = (image: GalleryImage) => {
  if (isPdfGalleryItem(image)) {
    return 4 / 3;
  }
  if (!image.width || !image.height || image.width <= 0 || image.height <= 0) {
    return null;
  }

  return Math.min(2.4, Math.max(0.45, image.width / image.height));
};

export const estimateMasonryReferenceWidth = (thumbnailWidth: number, aspectRatioHint: number) => {
  const ratioFactor = Math.min(1.25, Math.max(0.85, Math.sqrt(aspectRatioHint || 1)));
  return Math.max(1, Math.round(thumbnailWidth * ratioFactor));
};

export const estimateMasonryColumnCount = (containerWidth: number, referenceWidth: number) => {
  if (!containerWidth) {
    return 1;
  }
  return Math.max(1, Math.floor(containerWidth / Math.max(referenceWidth, 1)));
};

export const estimateMasonryColumnWidth = (
  containerWidth: number,
  columnCount: number,
  fallbackWidth: number,
) => {
  if (!containerWidth) {
    return Math.max(1, fallbackWidth);
  }
  return Math.max(1, Math.floor(containerWidth / Math.max(1, columnCount)));
};

export const getMasonryBatchSize = (columnCount: number, rowCount: number) =>
  Math.max(1, columnCount * rowCount);

export const estimateMasonryItemHeight = (
  image: GalleryImage,
  columnWidth: number,
  aspectRatioHint: number,
) => {
  const ratio = (getKnownMasonryAspectRatio(image) ?? aspectRatioHint) || 1;
  return Math.max(1, columnWidth / Math.max(0.2, ratio));
};

export const createEmptyMasonryColumnIds = (columnCount: number) =>
  Array.from({ length: Math.max(1, columnCount) }, () => [] as string[]);

export const createEmptyMasonryColumnHeights = (columnCount: number) =>
  Array.from({ length: Math.max(1, columnCount) }, () => 0);

export const getUnresolvedMasonryImages = (
  columnIds: string[][],
  images: GalleryImage[],
) => {
  const imageById = new Map(images.map((image) => [image.id, image]));
  const unresolvedImages: GalleryImage[] = [];
  const seenIds = new Set<string>();

  for (const imageId of columnIds.flat()) {
    if (seenIds.has(imageId)) continue;
    seenIds.add(imageId);
    const image = imageById.get(imageId);
    if (image && !isMasonryRenderable(image)) {
      unresolvedImages.push(image);
    }
  }

  return unresolvedImages;
};
