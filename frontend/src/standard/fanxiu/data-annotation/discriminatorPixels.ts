/** Pixel comparison only: image loading, frame sampling and preview rendering belong to callers. */
export interface RgbaPixels {
  width: number;
  height: number;
  data: Uint8ClampedArray;
}

export interface DiscriminatorReference {
  reference: RgbaPixels;
  alpha: Uint8ClampedArray | null;
}

/** Emphasize the strongest inter-reference differences; preserve the existing 88%/12 threshold. */
export function buildDiscriminatorWeights(
  variants: readonly DiscriminatorReference[], width: number, height: number,
) {
  const total = width * height;
  const weights = new Float32Array(total);
  const rawDiffs = new Float32Array(total);
  let maxDiff = 0;
  for (let index = 0; index < total; index += 1) {
    const offset = index * 4;
    let minR = 255;
    let minG = 255;
    let minB = 255;
    let maxR = 0;
    let maxG = 0;
    let maxB = 0;
    let visibleCount = 0;
    for (const variant of variants) {
      const alpha = variant.alpha ? (variant.alpha[index] ?? 0) : 255;
      if (alpha <= 5) continue;
      visibleCount += 1;
      minR = Math.min(minR, variant.reference.data[offset]);
      minG = Math.min(minG, variant.reference.data[offset + 1]);
      minB = Math.min(minB, variant.reference.data[offset + 2]);
      maxR = Math.max(maxR, variant.reference.data[offset]);
      maxG = Math.max(maxG, variant.reference.data[offset + 1]);
      maxB = Math.max(maxB, variant.reference.data[offset + 2]);
    }
    const diff = visibleCount >= 2
      ? Math.max(maxR - minR, maxG - minG, maxB - minB)
      : (visibleCount === 1 ? 24 : 0);
    rawDiffs[index] = diff;
    maxDiff = Math.max(maxDiff, diff);
  }
  const sortedDiffs = Array.from(rawDiffs).sort((a, b) => a - b);
  const percentileIndex = Math.max(0, Math.floor(sortedDiffs.length * 0.88));
  const activeThreshold = Math.max(12, sortedDiffs[percentileIndex] ?? 0);
  let activePixels = 0;
  for (let index = 0; index < total; index += 1) {
    if (rawDiffs[index] < activeThreshold || maxDiff <= 0) {
      weights[index] = 0;
      continue;
    }
    weights[index] = rawDiffs[index] / maxDiff;
    activePixels += 1;
  }
  return { weights, activePixels };
}

export const computeDiscriminatorVariantError = (
  sample: RgbaPixels,
  reference: RgbaPixels,
  weights: Float32Array,
  alpha: Uint8ClampedArray | null,
) => {
  let weightedDiff = 0;
  let totalWeight = 0;
  const total = sample.width * sample.height;
  for (let index = 0; index < total; index += 1) {
    const alphaWeight = alpha ? (alpha[index] ?? 0) / 255 : 1;
    if (alphaWeight <= 0.02) continue;
    const weight = weights[index] * alphaWeight;
    if (weight <= 0) continue;
    const offset = index * 4;
    const diff = Math.max(
      Math.abs(sample.data[offset] - reference.data[offset]),
      Math.abs(sample.data[offset + 1] - reference.data[offset + 1]),
      Math.abs(sample.data[offset + 2] - reference.data[offset + 2]),
    );
    weightedDiff += diff * weight;
    totalWeight += weight;
  }
  return totalWeight > 0 ? weightedDiff / totalWeight : Number.POSITIVE_INFINITY;
};
