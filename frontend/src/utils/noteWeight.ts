export const NOTE_WEIGHT_DEFAULT = 0;

const LEGACY_LINEAR_WEIGHT_NODE_TYPES = new Set(['memo']);

const parseFiniteWeight = (weight: unknown) => {
  if (typeof weight === 'number' && Number.isFinite(weight)) return Math.trunc(weight);
  if (typeof weight === 'string' && weight.trim() !== '') {
    const parsed = Number(weight);
    if (Number.isFinite(parsed)) return Math.trunc(parsed);
  }
  return NOTE_WEIGHT_DEFAULT;
};

export const usesLegacyLinearNoteWeight = (nodeType?: string | null) =>
  LEGACY_LINEAR_WEIGHT_NODE_TYPES.has(String(nodeType ?? '').toLowerCase());

export const normalizeNoteWeight = (weight: unknown) => Math.max(0, parseFiniteWeight(weight));

export const getNoteWeightAreaFactor = (weight: unknown, nodeType?: string | null) => {
  const normalizedWeight = normalizeNoteWeight(weight);
  if (usesLegacyLinearNoteWeight(nodeType)) {
    return Math.max(0.1, normalizedWeight / 100);
  }
  return Math.pow(2, normalizedWeight);
};

export const getNoteWeightScaleFactor = (weight: unknown, nodeType?: string | null) =>
  Math.sqrt(getNoteWeightAreaFactor(weight, nodeType));
