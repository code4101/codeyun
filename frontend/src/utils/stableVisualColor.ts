export interface StableBadgeStyle {
  color: string;
  backgroundColor: string;
  borderColor: string;
}

export type StableVisualColorSpace = 'soft' | 'solid' | 'dark';

export interface StableToneVariant {
  saturation: number;
  backgroundLightness: number;
  borderLightness: number;
  textLightness: number;
}

export interface StableVisualToken {
  seed: string;
  hash: number;
  preferredHue: number;
  hue: number;
  toneIndex: number;
  tone: StableToneVariant;
}

export interface StableVisualColorOptions {
  emptyStyle?: StableBadgeStyle;
  colorSpace?: StableVisualColorSpace;
  hueSlots?: readonly number[];
  toneVariants?: readonly StableToneVariant[];
}

export interface StableVisualSequenceOptions extends StableVisualColorOptions {
  minHueDistance?: number;
  minAdjacentHueDistance?: number;
}

const DEFAULT_EMPTY_STYLE: StableBadgeStyle = {
  color: '#909399',
  backgroundColor: '#f5f7fa',
  borderColor: '#dcdfe6',
};

const COLOR_SPACE_TONES: Record<StableVisualColorSpace, readonly StableToneVariant[]> = {
  soft: Object.freeze([
    { saturation: 58, backgroundLightness: 95, borderLightness: 84, textLightness: 30 },
    { saturation: 64, backgroundLightness: 94, borderLightness: 82, textLightness: 28 },
    { saturation: 70, backgroundLightness: 93, borderLightness: 80, textLightness: 26 },
    { saturation: 76, backgroundLightness: 92, borderLightness: 78, textLightness: 24 },
    { saturation: 62, backgroundLightness: 90, borderLightness: 74, textLightness: 22 },
  ]),
  solid: Object.freeze([
    { saturation: 62, backgroundLightness: 46, borderLightness: 38, textLightness: 96 },
    { saturation: 68, backgroundLightness: 42, borderLightness: 34, textLightness: 96 },
    { saturation: 74, backgroundLightness: 48, borderLightness: 40, textLightness: 97 },
    { saturation: 58, backgroundLightness: 40, borderLightness: 32, textLightness: 95 },
  ]),
  dark: Object.freeze([
    { saturation: 44, backgroundLightness: 20, borderLightness: 32, textLightness: 92 },
    { saturation: 50, backgroundLightness: 24, borderLightness: 36, textLightness: 94 },
    { saturation: 56, backgroundLightness: 28, borderLightness: 40, textLightness: 96 },
  ]),
};

const ALL_HUE_VALUES = Object.freeze(Array.from({ length: 360 }, (_, index) => index));

const FNV_OFFSET_BASIS_32 = 0x811c9dc5;
const FNV_PRIME_32 = 0x01000193;
const DEFAULT_HUE_SLOT_COUNT = 36;
const GOLDEN_ANGLE_DEGREES = 137.50776405003785;
const HUE_HASH_SALT = 0x68bc21eb;
const TONE_HASH_SALT = 0xb5297a4d;

const mixUint32 = (value: number) => {
  let mixed = value >>> 0;
  mixed ^= mixed >>> 16;
  mixed = Math.imul(mixed, 0x85ebca6b) >>> 0;
  mixed ^= mixed >>> 13;
  mixed = Math.imul(mixed, 0xc2b2ae35) >>> 0;
  mixed ^= mixed >>> 16;
  return mixed >>> 0;
};

export const stableHash32 = (value: string) => {
  let hash = FNV_OFFSET_BASIS_32;
  for (let i = 0; i < value.length; i += 1) {
    hash ^= value.charCodeAt(i);
    hash = Math.imul(hash, FNV_PRIME_32) >>> 0;
  }
  return mixUint32(hash);
};

const deriveVisualHash = (hash: number, salt: number) => mixUint32((hash ^ salt) >>> 0);

const normalizeHue = (value: number) => {
  const rounded = Math.round(value);
  return ((rounded % 360) + 360) % 360;
};

const DEFAULT_HUE_SLOTS = Object.freeze(Array.from(
  { length: DEFAULT_HUE_SLOT_COUNT },
  (_, index) => normalizeHue(index * GOLDEN_ANGLE_DEGREES),
));

export const getHueDistance = (a: number, b: number) => {
  const distance = Math.abs(normalizeHue(a) - normalizeHue(b)) % 360;
  return Math.min(distance, 360 - distance);
};

const getToneVariants = (options: StableVisualColorOptions) => {
  if (options.toneVariants?.length) return options.toneVariants;
  return COLOR_SPACE_TONES[options.colorSpace ?? 'soft'];
};

const getHueCandidates = (options: StableVisualColorOptions) => {
  if (!options.hueSlots?.length) return ALL_HUE_VALUES;
  return Array.from(new Set(options.hueSlots.map(normalizeHue)));
};

const getPreferredHue = (hash: number, options: StableVisualColorOptions) => {
  const hueHash = deriveVisualHash(hash, HUE_HASH_SALT);
  if (options.hueSlots?.length) {
    return normalizeHue(options.hueSlots[hueHash % options.hueSlots.length] ?? 0);
  }
  return DEFAULT_HUE_SLOTS[hueHash % DEFAULT_HUE_SLOTS.length] ?? 0;
};

const createStableVisualToken = (
  seed: string,
  options: StableVisualColorOptions,
  hueOverride?: number,
): StableVisualToken | null => {
  const normalizedSeed = seed.trim();
  if (!normalizedSeed) return null;

  const toneVariants = getToneVariants(options);
  const baseHash = stableHash32(normalizedSeed);
  const preferredHue = getPreferredHue(baseHash, options);
  const toneIndex = deriveVisualHash(baseHash, TONE_HASH_SALT) % toneVariants.length;

  return {
    seed: normalizedSeed,
    hash: baseHash,
    preferredHue,
    hue: hueOverride == null ? preferredHue : normalizeHue(hueOverride),
    toneIndex,
    tone: toneVariants[toneIndex],
  };
};

const isSeparatedHueUsable = (
  hue: number,
  usedHues: number[],
  previousHue: number | null,
  minHueDistance: number,
  minAdjacentHueDistance: number,
) => {
  if (previousHue != null && getHueDistance(hue, previousHue) < minAdjacentHueDistance) {
    return false;
  }
  return !usedHues.some((usedHue) => getHueDistance(hue, usedHue) < minHueDistance);
};

const pickSeparatedHue = (
  preferredHue: number,
  options: StableVisualColorOptions,
  usedHues: number[],
  previousHue: number | null,
  minHueDistance: number,
  minAdjacentHueDistance: number,
) => {
  if (isSeparatedHueUsable(preferredHue, usedHues, previousHue, minHueDistance, minAdjacentHueDistance)) {
    return preferredHue;
  }

  const candidates = getHueCandidates(options)
    .slice()
    .sort((a, b) => {
      const preferredDistanceDelta = getHueDistance(a, preferredHue) - getHueDistance(b, preferredHue);
      if (preferredDistanceDelta !== 0) return preferredDistanceDelta;

      const adjacentA = previousHue == null ? 360 : getHueDistance(a, previousHue);
      const adjacentB = previousHue == null ? 360 : getHueDistance(b, previousHue);
      if (adjacentA !== adjacentB) return adjacentB - adjacentA;

      return a - b;
    });

  const usableHue = candidates.find((hue) => (
    isSeparatedHueUsable(hue, usedHues, previousHue, minHueDistance, minAdjacentHueDistance)
  ));
  if (usableHue != null) return usableHue;

  return candidates.sort((a, b) => {
    const usedA = usedHues.length ? Math.min(...usedHues.map((usedHue) => getHueDistance(a, usedHue))) : 360;
    const usedB = usedHues.length ? Math.min(...usedHues.map((usedHue) => getHueDistance(b, usedHue))) : 360;
    if (usedA !== usedB) return usedB - usedA;

    const adjacentA = previousHue == null ? 360 : getHueDistance(a, previousHue);
    const adjacentB = previousHue == null ? 360 : getHueDistance(b, previousHue);
    if (adjacentA !== adjacentB) return adjacentB - adjacentA;

    return getHueDistance(a, preferredHue) - getHueDistance(b, preferredHue);
  })[0] ?? preferredHue;
};

export const getStableVisualToken = (
  seed: string,
  options: StableVisualColorOptions = {},
) => createStableVisualToken(seed, options);

export const resolveStableVisualTokens = (
  seeds: readonly string[],
  options: StableVisualSequenceOptions = {},
): Array<StableVisualToken | null> => {
  const usedHues: number[] = [];
  const tokenBySeed = new Map<string, StableVisualToken>();
  const minHueDistance = Math.max(0, options.minHueDistance ?? 0);
  const minAdjacentHueDistance = Math.max(0, options.minAdjacentHueDistance ?? 0);
  let previousHue: number | null = null;

  return seeds.map((seed) => {
    const normalizedSeed = seed.trim();
    if (!normalizedSeed) return null;

    const cachedToken = tokenBySeed.get(normalizedSeed);
    if (cachedToken) {
      previousHue = cachedToken.hue;
      return cachedToken;
    }

    const token = createStableVisualToken(normalizedSeed, options);
    if (!token) return null;

    const hue = pickSeparatedHue(
      token.preferredHue,
      options,
      usedHues,
      previousHue,
      minHueDistance,
      minAdjacentHueDistance,
    );
    const separatedToken = createStableVisualToken(normalizedSeed, options, hue);
    if (!separatedToken) return null;

    tokenBySeed.set(normalizedSeed, separatedToken);
    usedHues.push(separatedToken.hue);
    previousHue = separatedToken.hue;
    return separatedToken;
  });
};

export const getStableBadgeStyle = (
  seed: string,
  options: StableVisualColorOptions = {},
): StableBadgeStyle => {
  const token = getStableVisualToken(seed, options);
  if (!token) return options.emptyStyle ?? DEFAULT_EMPTY_STYLE;

  const { hue, tone } = token;
  const textSaturation = Math.max(42, tone.saturation - 18);
  const borderSaturation = Math.max(36, tone.saturation - 16);

  return {
    color: `hsl(${hue} ${textSaturation}% ${tone.textLightness}%)`,
    backgroundColor: `hsl(${hue} ${tone.saturation}% ${tone.backgroundLightness}%)`,
    borderColor: `hsl(${hue} ${borderSaturation}% ${tone.borderLightness}%)`,
  };
};
