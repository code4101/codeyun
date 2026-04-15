export const FANXIU_CULTIVATION_REALMS = [
  '炼气',
  '筑基',
  '结丹',
  '元婴',
  '化神',
  '炼虚',
  '合体',
  '大乘',
  '真仙',
  '金仙',
] as const;

export const FANXIU_CULTIVATION_STAGES = ['前期', '中期', '后期'] as const;

export const CUIJIAN_MEMBER_COUNT = 10;
export const CUIJIAN_TEAM_SIZE = 5;
export const CUIJIAN_ANCHOR_REALM = '大乘' as const;
export const CUIJIAN_ANCHOR_STAGE = '前期' as const;
export const CUIJIAN_ANCHOR_LAYER = 1;
export const CUIJIAN_ANCHOR_VALUE = 201;

export type FanxiuCultivationRealm = (typeof FANXIU_CULTIVATION_REALMS)[number];
export type FanxiuCultivationStage = (typeof FANXIU_CULTIVATION_STAGES)[number];

export interface FanxiuCultivationLevel {
  realm: FanxiuCultivationRealm;
  stage: FanxiuCultivationStage;
  layer: number;
  value: number;
  text: string;
}

export interface CuijianThresholdInput {
  hard: number;
  easy: number;
  tolerance: number;
}

export interface CuijianThresholds {
  hard: number;
  easy: number;
  tolerance: number;
  effectiveHard: number;
  effectiveEasy: number;
}

export interface CuijianTeamMember {
  index: number;
  slot: number;
  value: number;
  levelText: string;
}

export interface CuijianTeam {
  indices: number[];
  sum: number;
  members: CuijianTeamMember[];
}

export interface CuijianSplit {
  signature: string;
  hardTeam: CuijianTeam;
  easyTeam: CuijianTeam;
  gap: number;
  hardDeficit: number;
  easyDeficit: number;
  totalDeficit: number;
  hardMargin: number;
  easyMargin: number;
  passes: boolean;
}

export interface CuijianUpgradeItem {
  index: number;
  slot: number;
  teamType: 'hard' | 'easy';
  currentValue: number;
  currentLevelText: string;
  delta: number;
  targetValue: number;
  targetLevelText: string;
}

export interface CuijianUpgradePlan {
  totalDelta: number;
  hardDelta: number;
  easyDelta: number;
  targetHardSum: number;
  targetEasySum: number;
  items: CuijianUpgradeItem[];
}

export interface CuijianAnalysis {
  thresholds: CuijianThresholds;
  totalValue: number;
  clearSplit: CuijianSplit | null;
  balancedSplit: CuijianSplit | null;
  upgradePlan: CuijianUpgradePlan | null;
}

export type CuijianGateKey = 'gate1' | 'gate2';

export interface CuijianGateThresholdInput {
  gate1: number;
  gate2: number;
  tolerance: number;
}

export interface CuijianGateThresholds {
  gate1: number;
  gate2: number;
  tolerance: number;
  effectiveGate1: number;
  effectiveGate2: number;
}

export interface CuijianGateSplit {
  signature: string;
  gate1Team: CuijianTeam;
  gate2Team: CuijianTeam;
  gate1Deficit: number;
  gate2Deficit: number;
  totalDeficit: number;
  gate1Margin: number;
  gate2Margin: number;
  gap: number;
  movedCount: number;
  passes: boolean;
}

export interface CuijianGateUpgradeItem {
  index: number;
  slot: number;
  gateKey: CuijianGateKey;
  currentValue: number;
  currentLevelText: string;
  delta: number;
  targetValue: number;
  targetLevelText: string;
}

export interface CuijianGateUpgradePlan {
  totalDelta: number;
  gate1Delta: number;
  gate2Delta: number;
  items: CuijianGateUpgradeItem[];
}

export interface CuijianStrategyRow {
  index: number;
  slot: number;
  value: number;
  levelText: string;
  gateKey: CuijianGateKey;
  gateLabel: string;
  moved: boolean;
  delta: number;
  targetLevelText: string;
}

export interface CuijianStrategyAnalysis {
  thresholds: CuijianGateThresholds;
  totalValue: number;
  defaultSplit: CuijianGateSplit;
  chosenSplit: CuijianGateSplit;
  upgradePlan: CuijianGateUpgradePlan;
  rows: CuijianStrategyRow[];
  mode: 'default' | 'reassigned' | 'upgrade';
  passes: boolean;
  passByDefault: boolean;
}

const REALM_INDEX = new Map<FanxiuCultivationRealm, number>(
  FANXIU_CULTIVATION_REALMS.map((realm, index) => [realm, index]),
);

const STAGE_INDEX = new Map<FanxiuCultivationStage, number>(
  FANXIU_CULTIVATION_STAGES.map((stage, index) => [stage, index]),
);

const REALM_ALIASES = new Map<string, FanxiuCultivationRealm>([
  ['炼气', '炼气'],
  ['筑基', '筑基'],
  ['结丹', '结丹'],
  ['元婴', '元婴'],
  ['原因', '元婴'],
  ['化神', '化神'],
  ['炼虚', '炼虚'],
  ['合体', '合体'],
  ['大乘', '大乘'],
  ['真仙', '真仙'],
  ['金仙', '金仙'],
]);

const REALM_ALIAS_KEYS = [...REALM_ALIASES.keys()].sort((left, right) => right.length - left.length);
const ANCHOR_REALM_INDEX = REALM_INDEX.get(CUIJIAN_ANCHOR_REALM) ?? 0;
const ANCHOR_STAGE_INDEX = STAGE_INDEX.get(CUIJIAN_ANCHOR_STAGE) ?? 0;
const REALM_VALUE_SPAN = FANXIU_CULTIVATION_STAGES.length * 10;
const DEFAULT_GATE1_INDICES = Array.from({ length: CUIJIAN_TEAM_SIZE }, (_, index) => index);

const clampLayer = (layer: number) => Math.min(10, Math.max(1, Math.trunc(layer || 0)));
const mod = (value: number, divisor: number) => ((value % divisor) + divisor) % divisor;

const lexicographicCompare = (left: string, right: string) => {
  if (left === right) return 0;
  return left < right ? -1 : 1;
};

const buildLevel = (
  realm: FanxiuCultivationRealm,
  stage: FanxiuCultivationStage,
  layer: number,
): FanxiuCultivationLevel => {
  const normalizedLayer = clampLayer(layer);
  const value = cultivationToValue(realm, stage, normalizedLayer);
  return {
    realm,
    stage,
    layer: normalizedLayer,
    value,
    text: `${realm}${stage}${normalizedLayer}层`,
  };
};

const createTeam = (indices: number[], values: number[]): CuijianTeam => {
  const sortedIndices = indices.slice().sort((left, right) => left - right);
  const members = sortedIndices.map((index) => ({
    index,
    slot: index + 1,
    value: values[index],
    levelText: formatCultivationValue(values[index]),
  }));

  return {
    indices: sortedIndices,
    sum: members.reduce((total, member) => total + member.value, 0),
    members,
  };
};

const compareBalancedSplit = (left: CuijianSplit, right: CuijianSplit) =>
  left.gap - right.gap ||
  left.totalDeficit - right.totalDeficit ||
  right.easyTeam.sum - left.easyTeam.sum ||
  lexicographicCompare(left.signature, right.signature);

const compareClearSplit = (left: CuijianSplit, right: CuijianSplit) =>
  left.totalDeficit - right.totalDeficit ||
  Math.max(left.hardDeficit, left.easyDeficit) - Math.max(right.hardDeficit, right.easyDeficit) ||
  left.gap - right.gap ||
  right.easyTeam.sum - left.easyTeam.sum ||
  lexicographicCompare(left.signature, right.signature);

export const cultivationToValue = (
  realm: FanxiuCultivationRealm,
  stage: FanxiuCultivationStage,
  layer: number,
) => {
  const realmIndex = REALM_INDEX.get(realm) ?? 0;
  const stageIndex = STAGE_INDEX.get(stage) ?? 0;
  const normalizedLayer = clampLayer(layer);
  const realmDelta = realmIndex - ANCHOR_REALM_INDEX;
  const stageDelta = stageIndex - ANCHOR_STAGE_INDEX;
  return CUIJIAN_ANCHOR_VALUE + realmDelta * REALM_VALUE_SPAN + stageDelta * 10 + (normalizedLayer - CUIJIAN_ANCHOR_LAYER);
};

export const valueToCultivation = (value: number): FanxiuCultivationLevel | null => {
  if (!Number.isFinite(value)) return null;
  const normalizedValue = Math.trunc(value);
  const offset = normalizedValue - CUIJIAN_ANCHOR_VALUE;
  const realmDelta = Math.floor(offset / REALM_VALUE_SPAN);
  const withinRealm = mod(offset, REALM_VALUE_SPAN);
  const realmIndex = ANCHOR_REALM_INDEX + realmDelta;
  if (realmIndex < 0 || realmIndex >= FANXIU_CULTIVATION_REALMS.length) {
    return null;
  }
  const stageIndex = Math.floor(withinRealm / 10);
  const layer = (withinRealm % 10) + 1;
  return buildLevel(
    FANXIU_CULTIVATION_REALMS[realmIndex],
    FANXIU_CULTIVATION_STAGES[stageIndex],
    layer,
  );
};

export const formatCultivationValue = (value: number) => {
  const level = valueToCultivation(value);
  return level ? level.text : String(Math.trunc(value));
};

export const parseCultivationText = (rawText: string): FanxiuCultivationLevel | null => {
  const normalized = String(rawText || '')
    .trim()
    .replace(/^[#\d\s、,.．()（）\-]+/, '')
    .replace(/\s+/g, '');

  if (!normalized) return null;

  if (/^-?\d+$/.test(normalized)) {
    return valueToCultivation(Number.parseInt(normalized, 10));
  }

  const realmAlias = REALM_ALIAS_KEYS.find((alias) => normalized.startsWith(alias));
  if (!realmAlias) return null;

  const realm = REALM_ALIASES.get(realmAlias);
  if (!realm) return null;

  const rest = normalized.slice(realmAlias.length);
  const stage = FANXIU_CULTIVATION_STAGES.find((item) => rest.startsWith(item));
  if (!stage) return null;

  const layerMatch = rest.slice(stage.length).match(/^(\d{1,2})层?$/);
  if (!layerMatch) return null;

  return buildLevel(realm, stage, Number.parseInt(layerMatch[1], 10));
};

export const normalizeCuijianThresholds = (input: CuijianThresholdInput): CuijianThresholds => {
  const hard = Math.max(Math.trunc(input.hard || 0), Math.trunc(input.easy || 0));
  const easy = Math.min(Math.trunc(input.hard || 0), Math.trunc(input.easy || 0));
  const tolerance = Math.max(0, Math.trunc(input.tolerance || 0));
  return {
    hard,
    easy,
    tolerance,
    effectiveHard: Math.max(0, hard - tolerance),
    effectiveEasy: Math.max(0, easy - tolerance),
  };
};

export const enumerateCuijianSplits = (
  values: number[],
  rawThresholds: CuijianThresholdInput,
): CuijianSplit[] => {
  if (values.length !== CUIJIAN_MEMBER_COUNT) {
    return [];
  }

  const thresholds = normalizeCuijianThresholds(rawThresholds);
  const result: CuijianSplit[] = [];
  const chosen = [0];
  const selected = new Set<number>(chosen);

  const commitSplit = () => {
    const opposite: number[] = [];
    for (let index = 0; index < values.length; index += 1) {
      if (!selected.has(index)) {
        opposite.push(index);
      }
    }

    const firstTeam = createTeam(chosen, values);
    const secondTeam = createTeam(opposite, values);
    const hardTeam = firstTeam.sum >= secondTeam.sum ? firstTeam : secondTeam;
    const easyTeam = hardTeam === firstTeam ? secondTeam : firstTeam;
    const hardDeficit = Math.max(0, thresholds.effectiveHard - hardTeam.sum);
    const easyDeficit = Math.max(0, thresholds.effectiveEasy - easyTeam.sum);
    const signature = `${hardTeam.indices.join('-')}|${easyTeam.indices.join('-')}`;

    result.push({
      signature,
      hardTeam,
      easyTeam,
      gap: hardTeam.sum - easyTeam.sum,
      hardDeficit,
      easyDeficit,
      totalDeficit: hardDeficit + easyDeficit,
      hardMargin: hardTeam.sum - thresholds.effectiveHard,
      easyMargin: easyTeam.sum - thresholds.effectiveEasy,
      passes: hardDeficit === 0 && easyDeficit === 0,
    });
  };

  const visit = (start: number) => {
    if (chosen.length === CUIJIAN_TEAM_SIZE) {
      commitSplit();
      return;
    }

    for (let index = start; index < values.length; index += 1) {
      chosen.push(index);
      selected.add(index);
      visit(index + 1);
      selected.delete(index);
      chosen.pop();
    }
  };

  visit(1);
  return result;
};

export const findBestBalancedSplit = (
  values: number[],
  rawThresholds: CuijianThresholdInput,
): CuijianSplit | null => {
  const splits = enumerateCuijianSplits(values, rawThresholds);
  return splits.reduce<CuijianSplit | null>((best, split) => {
    if (!best || compareBalancedSplit(split, best) < 0) {
      return split;
    }
    return best;
  }, null);
};

export const findBestClearSplit = (
  values: number[],
  rawThresholds: CuijianThresholdInput,
): CuijianSplit | null => {
  const splits = enumerateCuijianSplits(values, rawThresholds);
  return splits.reduce<CuijianSplit | null>((best, split) => {
    if (!best || compareClearSplit(split, best) < 0) {
      return split;
    }
    return best;
  }, null);
};

const distributeUpgradeDelta = (
  values: number[],
  indices: number[],
  delta: number,
  target: 'hard' | 'easy',
  upgrades: CuijianUpgradeItem[],
) => {
  let remaining = delta;

  while (remaining > 0) {
    let candidate: CuijianUpgradeItem | null = null;
    for (const index of indices) {
      const item = upgrades[index];
      if (item.teamType !== target) continue;
      if (
        !candidate ||
        item.targetValue < candidate.targetValue ||
        (item.targetValue === candidate.targetValue && item.slot < candidate.slot)
      ) {
        candidate = item;
      }
    }

    if (!candidate) {
      break;
    }

    candidate.delta += 1;
    candidate.targetValue += 1;
    candidate.targetLevelText = formatCultivationValue(candidate.targetValue);
    remaining -= 1;
  }

  return values;
};

export const buildCuijianUpgradePlan = (
  values: number[],
  split: CuijianSplit,
): CuijianUpgradePlan => {
  const upgrades: CuijianUpgradeItem[] = values.map((value, index) => ({
    index,
    slot: index + 1,
    teamType: split.hardTeam.indices.includes(index) ? 'hard' : 'easy',
    currentValue: value,
    currentLevelText: formatCultivationValue(value),
    delta: 0,
    targetValue: value,
    targetLevelText: formatCultivationValue(value),
  }));

  distributeUpgradeDelta(values, split.hardTeam.indices, split.hardDeficit, 'hard', upgrades);
  distributeUpgradeDelta(values, split.easyTeam.indices, split.easyDeficit, 'easy', upgrades);

  return {
    totalDelta: split.totalDeficit,
    hardDelta: split.hardDeficit,
    easyDelta: split.easyDeficit,
    targetHardSum: split.hardTeam.sum + split.hardDeficit,
    targetEasySum: split.easyTeam.sum + split.easyDeficit,
    items: upgrades,
  };
};

export const analyzeCuijianLineup = (
  values: number[],
  rawThresholds: CuijianThresholdInput,
): CuijianAnalysis => {
  const thresholds = normalizeCuijianThresholds(rawThresholds);
  const splits = enumerateCuijianSplits(values, thresholds);

  const clearSplit = splits.reduce<CuijianSplit | null>((best, split) => {
    if (!best || compareClearSplit(split, best) < 0) {
      return split;
    }
    return best;
  }, null);

  const balancedSplit = splits.reduce<CuijianSplit | null>((best, split) => {
    if (!best || compareBalancedSplit(split, best) < 0) {
      return split;
    }
    return best;
  }, null);

  return {
    thresholds,
    totalValue: values.reduce((total, value) => total + value, 0),
    clearSplit,
    balancedSplit,
    upgradePlan: clearSplit && !clearSplit.passes ? buildCuijianUpgradePlan(values, clearSplit) : null,
  };
};

const gateLabel = (gateKey: CuijianGateKey) => (gateKey === 'gate1' ? '第1关' : '第2关');

const normalizeGateThresholds = (input: CuijianGateThresholdInput): CuijianGateThresholds => {
  const gate1 = Math.max(0, Math.trunc(input.gate1 || 0));
  const gate2 = Math.max(0, Math.trunc(input.gate2 || 0));
  const tolerance = Math.max(0, Math.trunc(input.tolerance || 0));
  return {
    gate1,
    gate2,
    tolerance,
    effectiveGate1: Math.max(0, gate1 - tolerance),
    effectiveGate2: Math.max(0, gate2 - tolerance),
  };
};

const createGateSplit = (
  gate1Indices: number[],
  values: number[],
  thresholds: CuijianGateThresholds,
): CuijianGateSplit => {
  const gate1Set = new Set(gate1Indices);
  const gate2Indices: number[] = [];
  for (let index = 0; index < values.length; index += 1) {
    if (!gate1Set.has(index)) {
      gate2Indices.push(index);
    }
  }

  const gate1Team = createTeam(gate1Indices, values);
  const gate2Team = createTeam(gate2Indices, values);
  const gate1Deficit = Math.max(0, thresholds.effectiveGate1 - gate1Team.sum);
  const gate2Deficit = Math.max(0, thresholds.effectiveGate2 - gate2Team.sum);
  let movedCount = 0;

  for (let index = 0; index < values.length; index += 1) {
    const isDefaultGate1 = index < CUIJIAN_TEAM_SIZE;
    if (gate1Set.has(index) !== isDefaultGate1) {
      movedCount += 1;
    }
  }

  return {
    signature: `${gate1Team.indices.join('-')}|${gate2Team.indices.join('-')}`,
    gate1Team,
    gate2Team,
    gate1Deficit,
    gate2Deficit,
    totalDeficit: gate1Deficit + gate2Deficit,
    gate1Margin: gate1Team.sum - thresholds.effectiveGate1,
    gate2Margin: gate2Team.sum - thresholds.effectiveGate2,
    gap: Math.abs(gate1Team.sum - gate2Team.sum),
    movedCount,
    passes: gate1Deficit === 0 && gate2Deficit === 0,
  };
};

const enumerateGateSplits = (
  values: number[],
  thresholds: CuijianGateThresholds,
) => {
  if (values.length !== CUIJIAN_MEMBER_COUNT) {
    return [];
  }

  const result: CuijianGateSplit[] = [];
  const chosen: number[] = [];

  const visit = (start: number) => {
    if (chosen.length === CUIJIAN_TEAM_SIZE) {
      result.push(createGateSplit(chosen, values, thresholds));
      return;
    }

    for (let index = start; index < values.length; index += 1) {
      chosen.push(index);
      visit(index + 1);
      chosen.pop();
    }
  };

  visit(0);
  return result;
};

const comparePassingGateSplit = (left: CuijianGateSplit, right: CuijianGateSplit) =>
  left.movedCount - right.movedCount ||
  (Math.max(0, left.gate1Margin) + Math.max(0, left.gate2Margin)) -
    (Math.max(0, right.gate1Margin) + Math.max(0, right.gate2Margin)) ||
  left.gap - right.gap ||
  lexicographicCompare(left.signature, right.signature);

const compareUpgradeGateSplit = (left: CuijianGateSplit, right: CuijianGateSplit) =>
  left.totalDeficit - right.totalDeficit ||
  Math.max(left.gate1Deficit, left.gate2Deficit) - Math.max(right.gate1Deficit, right.gate2Deficit) ||
  left.movedCount - right.movedCount ||
  left.gap - right.gap ||
  lexicographicCompare(left.signature, right.signature);

const distributeGateUpgradeDelta = (
  indices: number[],
  delta: number,
  gateKey: CuijianGateKey,
  upgrades: CuijianGateUpgradeItem[],
) => {
  let remaining = delta;

  while (remaining > 0) {
    let candidate: CuijianGateUpgradeItem | null = null;
    for (const index of indices) {
      const item = upgrades[index];
      if (item.gateKey !== gateKey) continue;
      if (
        !candidate ||
        item.targetValue < candidate.targetValue ||
        (item.targetValue === candidate.targetValue && item.slot < candidate.slot)
      ) {
        candidate = item;
      }
    }

    if (!candidate) {
      break;
    }

    candidate.delta += 1;
    candidate.targetValue += 1;
    candidate.targetLevelText = formatCultivationValue(candidate.targetValue);
    remaining -= 1;
  }
};

const buildGateUpgradePlan = (
  values: number[],
  split: CuijianGateSplit,
): CuijianGateUpgradePlan => {
  const gate1Set = new Set(split.gate1Team.indices);
  const items: CuijianGateUpgradeItem[] = values.map((value, index) => {
    const gateKey: CuijianGateKey = gate1Set.has(index) ? 'gate1' : 'gate2';
    return {
      index,
      slot: index + 1,
      gateKey,
      currentValue: value,
      currentLevelText: formatCultivationValue(value),
      delta: 0,
      targetValue: value,
      targetLevelText: formatCultivationValue(value),
    };
  });

  distributeGateUpgradeDelta(split.gate1Team.indices, split.gate1Deficit, 'gate1', items);
  distributeGateUpgradeDelta(split.gate2Team.indices, split.gate2Deficit, 'gate2', items);

  return {
    totalDelta: split.totalDeficit,
    gate1Delta: split.gate1Deficit,
    gate2Delta: split.gate2Deficit,
    items,
  };
};

export const analyzeCuijianStrategy = (
  values: number[],
  input: CuijianGateThresholdInput,
): CuijianStrategyAnalysis => {
  const thresholds = normalizeGateThresholds(input);
  const defaultSplit = createGateSplit(DEFAULT_GATE1_INDICES, values, thresholds);
  const allSplits = enumerateGateSplits(values, thresholds);
  let chosenSplit = defaultSplit;
  let mode: CuijianStrategyAnalysis['mode'] = 'default';

  if (!defaultSplit.passes) {
    const passingSplit = allSplits
      .filter((split) => split.passes)
      .reduce<CuijianGateSplit | null>((best, split) => {
        if (!best || comparePassingGateSplit(split, best) < 0) {
          return split;
        }
        return best;
      }, null);

    if (passingSplit) {
      chosenSplit = passingSplit;
      mode = 'reassigned';
    } else {
      chosenSplit = allSplits.reduce<CuijianGateSplit>(
        (best, split) => (compareUpgradeGateSplit(split, best) < 0 ? split : best),
        defaultSplit,
      );
      mode = 'upgrade';
    }
  }

  const upgradePlan = buildGateUpgradePlan(values, chosenSplit);
  const gate1Set = new Set(chosenSplit.gate1Team.indices);
  const rows: CuijianStrategyRow[] = upgradePlan.items.map((item) => ({
    index: item.index,
    slot: item.slot,
    value: item.currentValue,
    levelText: item.currentLevelText,
    gateKey: item.gateKey,
    gateLabel: gateLabel(item.gateKey),
    moved: gate1Set.has(item.index) !== (item.index < CUIJIAN_TEAM_SIZE),
    delta: item.delta,
    targetLevelText: item.targetLevelText,
  }));

  return {
    thresholds,
    totalValue: values.reduce((total, value) => total + value, 0),
    defaultSplit,
    chosenSplit,
    upgradePlan,
    rows,
    mode,
    passes: chosenSplit.passes,
    passByDefault: defaultSplit.passes,
  };
};
