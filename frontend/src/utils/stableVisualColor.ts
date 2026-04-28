export interface StableBadgeStyle {
  color: string;
  backgroundColor: string;
  borderColor: string;
}

export interface StableToneVariant {
  saturation: number;
  backgroundLightness: number;
  borderLightness: number;
  textLightness: number;
}

export interface StableVisualToken {
  hash: number;
  hue: number;
  toneIndex: number;
  tone: StableToneVariant;
}

export interface StableVisualColorOptions {
  emptyStyle?: StableBadgeStyle;
  hueSlots?: readonly number[];
  toneVariants?: readonly StableToneVariant[];
}

const DEFAULT_EMPTY_STYLE: StableBadgeStyle = {
  color: '#909399',
  backgroundColor: '#f5f7fa',
  borderColor: '#dcdfe6',
};

// Use fixed hue anchors instead of a continuous hue wheel so mapped colors stay separated.
const DEFAULT_HUE_SLOTS = Object.freeze([6, 36, 66, 96, 126, 156, 186, 216, 246, 276, 306, 336]);

const DEFAULT_TONE_VARIANTS = Object.freeze<StableToneVariant[]>([
  { saturation: 58, backgroundLightness: 95, borderLightness: 84, textLightness: 30 },
  { saturation: 64, backgroundLightness: 94, borderLightness: 82, textLightness: 28 },
  { saturation: 70, backgroundLightness: 93, borderLightness: 80, textLightness: 26 },
  { saturation: 76, backgroundLightness: 92, borderLightness: 78, textLightness: 24 },
  { saturation: 62, backgroundLightness: 90, borderLightness: 74, textLightness: 22 },
]);

const FNV_OFFSET_BASIS_32 = 0x811c9dc5;
const FNV_PRIME_32 = 0x01000193;
const GOLDEN_GAMMA_32 = 0x9e3779b9;

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

const nextMixedUint32 = (state: number) => mixUint32((state + GOLDEN_GAMMA_32) >>> 0);

export const getStableVisualToken = (
  seed: string,
  options: StableVisualColorOptions = {},
): StableVisualToken | null => {
  if (!seed) return null;

  const hueSlots = options.hueSlots?.length ? options.hueSlots : DEFAULT_HUE_SLOTS;
  const toneVariants = options.toneVariants?.length ? options.toneVariants : DEFAULT_TONE_VARIANTS;
  const baseHash = stableHash32(seed);
  let state = baseHash;
  const hue = hueSlots[state % hueSlots.length];
  state = nextMixedUint32(state);
  const toneIndex = state % toneVariants.length;

  return {
    hash: baseHash,
    hue,
    toneIndex,
    tone: toneVariants[toneIndex],
  };
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
