import { NOTE_WEIGHT_MODE_LINEAR } from './noteSemantics';

export const NOTE_WEIGHT_DEFAULT = 0;
export const NOTE_WEIGHT_MIN = Number.MIN_SAFE_INTEGER;

const parseFiniteWeight = (weight: unknown) => {
  if (typeof weight === 'number' && Number.isFinite(weight)) return Math.trunc(weight);
  if (typeof weight === 'string' && weight.trim() !== '') {
    const parsed = Number(weight);
    if (Number.isFinite(parsed)) return Math.trunc(parsed);
  }
  return NOTE_WEIGHT_DEFAULT;
};

export const usesLegacyLinearNoteWeight = (_nodeType?: string | null, weightMode?: string | null) =>
  String(weightMode ?? '').toLowerCase() === NOTE_WEIGHT_MODE_LINEAR;

export const normalizeNoteWeight = (weight: unknown) => parseFiniteWeight(weight);

export const getNoteWeightAreaFactor = (weight: unknown, nodeType?: string | null, weightMode?: string | null) => {
  const normalizedWeight = normalizeNoteWeight(weight);
  if (usesLegacyLinearNoteWeight(nodeType, weightMode)) {
    return Math.max(0.1, normalizedWeight / 100);
  }
  return Math.pow(2, normalizedWeight);
};

export const getNoteWeightScaleFactor = (weight: unknown, nodeType?: string | null, weightMode?: string | null) =>
  Math.sqrt(getNoteWeightAreaFactor(weight, nodeType, weightMode));
