<script setup lang="ts">
import { ref, computed, watch } from 'vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import { Plus, Delete, ShoppingCart, ArrowDown } from '@element-plus/icons-vue';

// --- Types ---
interface Pack {
  name: string;
  price: number;
  pulls: number;
  limit: number; // 0 means unlimited
  purchased: number;
  // Computed/Internal
  round_idx?: number;
  unit_price?: number;
  status?: 'safe' | 'cautious' | 'overflow';
  recommendation?: string;
  cumulative_pulls?: number;
  cumulative_cost?: number;
}

interface Round {
  id: number;
  target_pulls: number;
  free_pulls: number;
  packs: Pack[];
}

interface StrategyItem extends Pack {
    // Inherits properties for display
}

interface StrategyResult {
    packs: StrategyItem[];
    cost: number;
    total_pulls: number;
    surplus: number;
    message?: string;
}

interface RechargeLocalState {
    version: 1;
    active_tab: string;
    rounds: Round[];
}

// --- Constants ---
const MAX_PULL_DECIMALS = 3;
const RECHARGE_STORAGE_KEY = 'codeyun_fanxiu_recharge_v1';

const DEFAULT_PACKS: Pack[] = [
    { name: "6元包", price: 6, pulls: 2, limit: 1, purchased: 0 },
    { name: "18元包", price: 18, pulls: 4, limit: 1, purchased: 0 },
    { name: "30元包", price: 30, pulls: 5, limit: 1, purchased: 0 },
    { name: "68元包", price: 68, pulls: 10, limit: 1, purchased: 0 },
    { name: "98元包", price: 98, pulls: 14, limit: 1, purchased: 0 },
    { name: "128元包", price: 128, pulls: 16, limit: 1, purchased: 0 },
    { name: "328元包", price: 328, pulls: 35, limit: 3, purchased: 0 }
];

const DAHUA_ZHUANZHUANLE_PACKS: Pack[] = [
    { name: "6元包", price: 6, pulls: 2, limit: 1, purchased: 0 },
    { name: "18元包", price: 18, pulls: 4, limit: 1, purchased: 0 },
    { name: "30元包", price: 30, pulls: 6, limit: 2, purchased: 0 },
    { name: "68元包", price: 68, pulls: 10, limit: 3, purchased: 0 },
    { name: "98元包", price: 98, pulls: 15, limit: 3, purchased: 0 },
    { name: "198元包", price: 198, pulls: 22.5, limit: 3, purchased: 0 },
    { name: "328元包", price: 328, pulls: 30, limit: 5, purchased: 0 },
    { name: "648元包", price: 648, pulls: 50, limit: 0, purchased: 0 }
];

const clonePacks = (packs: Pack[]) => JSON.parse(JSON.stringify(packs)) as Pack[];

const normalizePullValue = (value: number) => Number(Number(value || 0).toFixed(MAX_PULL_DECIMALS));

const formatPulls = (value: number) => {
    const normalized = normalizePullValue(value);
    return Number.isInteger(normalized) ? String(normalized) : normalized.toString();
};

const getPullDecimalPlaces = (value: number) => {
    const normalizedText = formatPulls(value);
    const dotIndex = normalizedText.indexOf('.');
    return dotIndex === -1 ? 0 : normalizedText.length - dotIndex - 1;
};

const getPullScaleFactor = (values: number[]) => {
    const maxDecimals = values.reduce((max, value) => Math.max(max, getPullDecimalPlaces(value)), 0);
    return 10 ** maxDecimals;
};

const scalePull = (value: number, scaleFactor: number) => Math.round(normalizePullValue(value) * scaleFactor);

const unscalePull = (value: number, scaleFactor: number) => normalizePullValue(value / scaleFactor);

const isUnlimitedLimit = (limit: number) => Number(limit || 0) <= 0;

const PRESETS = [
    { name: "2026春节", packs: DEFAULT_PACKS },
    { name: "2026/3/30 大话转转乐", packs: DAHUA_ZHUANZHUANLE_PACKS }
];

const buildInitialRound = (): Round => ({
    id: 1,
    target_pulls: 70,
    free_pulls: 31,
    packs: clonePacks(DEFAULT_PACKS)
});

const canUseLocalStorage = () => typeof window !== 'undefined' && typeof window.localStorage !== 'undefined';

const normalizeNonNegativeNumber = (value: unknown, fallback: number) => {
    const normalized = Number(value);
    if (!Number.isFinite(normalized) || normalized < 0) {
        return fallback;
    }
    return normalized;
};

const normalizePack = (value: unknown): Pack => {
    const raw = value && typeof value === 'object' ? value as Partial<Pack> : {};
    const limit = Math.floor(normalizeNonNegativeNumber(raw.limit, 1));
    const purchased = Math.floor(normalizeNonNegativeNumber(raw.purchased, 0));

    return {
        name: typeof raw.name === 'string' && raw.name.trim() ? raw.name.trim() : '新礼包',
        price: normalizeNonNegativeNumber(raw.price, 0),
        pulls: normalizePullValue(normalizeNonNegativeNumber(raw.pulls, 0)),
        limit,
        purchased: limit > 0 ? Math.min(purchased, limit) : purchased,
    };
};

const normalizeRound = (value: unknown, idx: number): Round => {
    const raw = value && typeof value === 'object' ? value as Partial<Round> : {};
    const packs = Array.isArray(raw.packs) && raw.packs.length > 0
        ? raw.packs.map(normalizePack)
        : clonePacks(DEFAULT_PACKS);

    return {
        id: idx + 1,
        target_pulls: normalizePullValue(normalizeNonNegativeNumber(raw.target_pulls, 70)),
        free_pulls: normalizePullValue(normalizeNonNegativeNumber(raw.free_pulls, idx === 0 ? 31 : 0)),
        packs,
    };
};

const normalizeRounds = (value: unknown) => {
    if (!Array.isArray(value) || value.length === 0) {
        return [buildInitialRound()];
    }

    return value.map((item, idx) => normalizeRound(item, idx));
};

const serializePack = (pack: Pack): Pack => ({
    name: pack.name,
    price: normalizeNonNegativeNumber(pack.price, 0),
    pulls: normalizePullValue(normalizeNonNegativeNumber(pack.pulls, 0)),
    limit: Math.floor(normalizeNonNegativeNumber(pack.limit, 1)),
    purchased: Math.floor(normalizeNonNegativeNumber(pack.purchased, 0)),
});

const serializeRound = (round: Round, idx: number): Round => {
    const packs = round.packs.map(serializePack);
    return normalizeRound({
        id: idx + 1,
        target_pulls: round.target_pulls,
        free_pulls: round.free_pulls,
        packs,
    }, idx);
};

const getPackPresetSignature = (packs: Pack[]) => JSON.stringify(
    packs.map(pack => ({
        name: pack.name,
        price: normalizeNonNegativeNumber(pack.price, 0),
        pulls: normalizePullValue(normalizeNonNegativeNumber(pack.pulls, 0)),
        limit: Math.floor(normalizeNonNegativeNumber(pack.limit, 1)),
    }))
);

const PRESET_SIGNATURE_TO_NAME = new Map(PRESETS.map(preset => [getPackPresetSignature(preset.packs), preset.name]));

const getRoundPresetName = (round: Round) => PRESET_SIGNATURE_TO_NAME.get(getPackPresetSignature(round.packs)) ?? '自定义';

const loadRechargeState = (): RechargeLocalState | null => {
    if (!canUseLocalStorage()) {
        return null;
    }

    try {
        const raw = window.localStorage.getItem(RECHARGE_STORAGE_KEY);
        if (!raw) {
            return null;
        }

        const payload = JSON.parse(raw) as Partial<RechargeLocalState>;
        const rounds = normalizeRounds(payload?.rounds);
        const rawActiveTab = typeof payload?.active_tab === 'string' ? payload.active_tab : '0';
        const parsedTab = Number.parseInt(rawActiveTab, 10);
        const activeTab = Number.isFinite(parsedTab)
            ? String(Math.min(Math.max(parsedTab, 0), Math.max(0, rounds.length - 1)))
            : '0';

        return {
            version: 1,
            active_tab: activeTab,
            rounds,
        };
    } catch {
        return null;
    }
};

const persistRechargeState = (activeTabValue: string, roundsValue: Round[]) => {
    if (!canUseLocalStorage()) {
        return;
    }

    const normalizedRounds = roundsValue.map((round, idx) => serializeRound(round, idx));
    const parsedTab = Number.parseInt(activeTabValue, 10);
    const normalizedTab = Number.isFinite(parsedTab)
        ? String(Math.min(Math.max(parsedTab, 0), Math.max(0, normalizedRounds.length - 1)))
        : '0';

    const payload: RechargeLocalState = {
        version: 1,
        active_tab: normalizedTab,
        rounds: normalizedRounds,
    };

    window.localStorage.setItem(RECHARGE_STORAGE_KEY, JSON.stringify(payload));
};

const initialRechargeState = loadRechargeState();

// --- State ---
const activeTab = ref(initialRechargeState?.active_tab ?? '0');
const rounds = ref<Round[]>(initialRechargeState?.rounds ?? [buildInitialRound()]);
const loading = ref(false);
const result = ref<StrategyResult | null>(null);

// --- Computed ---
const totalTargetPulls = computed(() => {
    return normalizePullValue(rounds.value.reduce((sum, r) => sum + Number(r.target_pulls || 0), 0));
});

const currentStatus = computed(() => {
    let pulls = 0;
    let spent = 0;
    rounds.value.forEach(r => {
        pulls += Number(r.free_pulls || 0);
        r.packs.forEach(p => {
            pulls += Number(p.pulls || 0) * Number(p.purchased || 0);
            spent += Number(p.price || 0) * Number(p.purchased || 0);
        });
    });
    return { total_pulls: normalizePullValue(pulls), total_spent: spent };
});

const formatCost = (value: number) => {
    const normalized = Number(Number(value || 0).toFixed(2));
    return Number.isInteger(normalized) ? String(normalized) : normalized.toString();
};

const getPackFullPurchaseCumulative = (packs: Pack[], packIdx: number) => {
    let totalPulls = 0;
    let totalCost = 0;
    let pullsInfinite = false;
    let costInfinite = false;

    for (let i = 0; i <= packIdx; i++) {
        const pack = packs[i];
        if (!pack) break;

        if (isUnlimitedLimit(pack.limit)) {
            if (Number(pack.pulls || 0) > 0) {
                pullsInfinite = true;
            }
            if (Number(pack.price || 0) > 0) {
                costInfinite = true;
            }
            if (pullsInfinite || costInfinite) {
                break;
            }
            continue;
        }

        const limit = Math.floor(normalizeNonNegativeNumber(pack.limit, 0));
        totalPulls += Number(pack.pulls || 0) * limit;
        totalCost += Number(pack.price || 0) * limit;
    }

    return {
        pulls: normalizePullValue(totalPulls),
        cost: Number(Number(totalCost).toFixed(2)),
        pullsInfinite,
        costInfinite,
    };
};

const formatPackFullPurchaseSummary = (packs: Pack[], packIdx: number) => {
    const summary = getPackFullPurchaseCumulative(packs, packIdx);
    const pullsText = summary.pullsInfinite ? '∞' : formatPulls(summary.pulls);
    const costText = summary.costInfinite ? '∞' : formatCost(summary.cost);
    return `${pullsText}抽 / ${costText}元`;
};

watch(
    [activeTab, rounds],
    ([nextActiveTab, nextRounds]) => {
        persistRechargeState(nextActiveTab, nextRounds);
    },
    { deep: true }
);

// --- Methods ---
const addRound = () => {
    const newId = rounds.value.length + 1;
    let newRound: Round = {
        id: newId,
        target_pulls: 70,
        free_pulls: 0,
        packs: clonePacks(DEFAULT_PACKS)
    };

    if (rounds.value.length > 0) {
        const lastRound = rounds.value[rounds.value.length - 1];
        newRound.target_pulls = lastRound.target_pulls;
        newRound.free_pulls = lastRound.free_pulls;
        newRound.packs = clonePacks(lastRound.packs);
        newRound.packs.forEach(p => p.purchased = 0);
    }

    rounds.value.push(newRound);
    activeTab.value = String(rounds.value.length - 1);
};

const removeRound = (idx: number) => {
    ElMessageBox.confirm('确定删除这一轮吗？', '提示', { type: 'warning' }).then(() => {
        rounds.value.splice(idx, 1);
        // Fix IDs
        rounds.value.forEach((r, i) => r.id = i + 1);
        activeTab.value = String(Math.max(0, rounds.value.length - 1));
    });
};

const handleRoundsEdit = (targetName: string | number | undefined, action: 'add' | 'remove') => {
    if (action === 'add') {
        addRound();
        return;
    }

    if (targetName === undefined) return;

    const index = typeof targetName === 'number' ? targetName : parseInt(targetName, 10);
    if (!Number.isNaN(index)) {
        removeRound(index);
    }
};

const addPack = (roundIdx: number) => {
    rounds.value[roundIdx].packs.push({
        name: "新礼包", price: 0, pulls: 0, limit: 1, purchased: 0
    });
};

const removePack = (roundIdx: number, packIdx: number) => {
    rounds.value[roundIdx].packs.splice(packIdx, 1);
};

const fillDefaultPacks = (roundIdx: number, presetIdx: number = 0) => {
    const preset = PRESETS[presetIdx];
    ElMessageBox.confirm(`确定重置为"${preset.name}"吗？将丢失当前已填写的购买数量。`, '提示', { type: 'warning' }).then(() => {
        rounds.value[roundIdx].packs = clonePacks(preset.packs);
    });
};

// --- Algorithm ---
const getExpandableCopies = (pack: Pack, neededPulls: number, maxPackPulls: number) => {
    if (pack.pulls <= 0) return 0;
    if (isUnlimitedLimit(pack.limit)) {
        return Math.max(0, Math.ceil((neededPulls + maxPackPulls) / pack.pulls));
    }

    return Math.max(0, Math.floor(Number(pack.limit || 0) - Number(pack.purchased || 0)));
};

const solveKnapsack = (targetPulls: number, availablePacks: Pack[]) => {
    const normalizedTargetPulls = normalizePullValue(targetPulls);
    const totalAvailablePulls = normalizePullValue(availablePacks.reduce((sum, p) => sum + Number(p.pulls || 0), 0));

    if (normalizedTargetPulls <= 0) return { selected: [], cost: 0, pulls: 0 };

    if (totalAvailablePulls < normalizedTargetPulls) {
        return {
            selected: [...availablePacks],
            cost: availablePacks.reduce((sum, p) => sum + p.price, 0),
            pulls: totalAvailablePulls
        };
    }

    const scaleFactor = getPullScaleFactor([normalizedTargetPulls, ...availablePacks.map(p => p.pulls)]);
    const scaledTargetPulls = scalePull(normalizedTargetPulls, scaleFactor);
    const scaledAvailablePacks = availablePacks.map((pack, idx) => ({
        idx,
        price: pack.price,
        pulls: scalePull(pack.pulls, scaleFactor)
    }));

    const maxSinglePull = Math.max(...scaledAvailablePacks.map(p => p.pulls), 0);
    const limitPulls = scaledTargetPulls + maxSinglePull + 1;

    const dp = new Array(limitPulls).fill(Infinity);
    const prevWeight = new Array(limitPulls).fill(-1);
    const prevItem = new Array(limitPulls).fill(-1);
    dp[0] = 0;

    for (let i = 0; i < scaledAvailablePacks.length; i++) {
        const pack = scaledAvailablePacks[i];
        const pCost = pack.price;
        const pPulls = pack.pulls;

        if (pPulls <= 0) continue;

        for (let j = limitPulls - 1; j >= pPulls; j--) {
            if (dp[j - pPulls] !== Infinity) {
                const nextCost = dp[j - pPulls] + pCost;
                if (nextCost < dp[j]) {
                    dp[j] = nextCost;
                    prevWeight[j] = j - pPulls;
                    prevItem[j] = i;
                }
            }
        }
    }

    let minCost = Infinity;
    let bestPulls = -1;

    for (let j = scaledTargetPulls; j < limitPulls; j++) {
        if (dp[j] < minCost) {
            minCost = dp[j];
            bestPulls = j;
        }
    }

    const selectedPacks: Pack[] = [];
    if (bestPulls !== -1 && minCost !== Infinity) {
        let currW = bestPulls;
        while (currW > 0 && prevItem[currW] !== -1) {
            const pack = availablePacks[scaledAvailablePacks[prevItem[currW]].idx];
            selectedPacks.push(pack);
            currW = prevWeight[currW];
        }
    }

    return {
        selected: selectedPacks,
        cost: minCost,
        pulls: bestPulls === -1 ? 0 : unscalePull(bestPulls, scaleFactor)
    };
};

const calculateStrategy = () => {
    loading.value = true;
    result.value = null;
    try {
        let currentPullsTotal = 0;
        const candidatePacks: Pack[] = [];
        
        rounds.value.forEach(r => {
             currentPullsTotal += Number(r.free_pulls || 0);
             r.packs.forEach(p => {
                 const purchased = Number(p.purchased || 0);
                 currentPullsTotal += Number(p.pulls || 0) * purchased;
                 
                 candidatePacks.push({
                     ...p,
                     round_idx: r.id,
                     unit_price: p.pulls > 0 ? p.price / p.pulls : Infinity
                 });
             });
        });

        currentPullsTotal = normalizePullValue(currentPullsTotal);
        const neededPulls = normalizePullValue(totalTargetPulls.value - currentPullsTotal);
        
        if (neededPulls <= 0) {
            result.value = {
                packs: [],
                cost: 0,
                total_pulls: 0,
                surplus: 0,
                message: "已达成目标，无需继续购买！"
            };
            return;
        }

        const maxPackPulls = Math.max(...candidatePacks.map(p => Number(p.pulls || 0)), 0);
        const availablePacksPool: Pack[] = [];

        candidatePacks.forEach(p => {
            const remaining = getExpandableCopies(p, neededPulls, maxPackPulls);
            for (let k = 0; k < remaining; k++) {
                availablePacksPool.push({ ...p });
            }
        });

        const { selected, cost, pulls } = solveKnapsack(neededPulls, availablePacksPool);

        selected.sort((a, b) => {
            if ((a.round_idx || 0) !== (b.round_idx || 0)) return (a.round_idx || 0) - (b.round_idx || 0);
            if ((a.unit_price || 0) !== (b.unit_price || 0)) return (a.unit_price || 0) - (b.unit_price || 0);
            return a.price - b.price;
        });

        const strategySequence: StrategyItem[] = [];
        let currentCumPulls = 0;
        let currentCumCost = 0;
        
        const maxRound = Math.max(...selected.map(p => p.round_idx || 0), 0);

        selected.forEach(p => {
            currentCumPulls = normalizePullValue(currentCumPulls + p.pulls);
            currentCumCost += p.price;
            
            let status: 'safe' | 'cautious' | 'overflow' = 'safe';
            let recommendation = '建议购买';
            
            if (p.price > 100 && p.round_idx === maxRound) {
                status = 'cautious';
                recommendation = '最后补位';
            }
            
            strategySequence.push({
                ...p,
                status,
                recommendation,
                cumulative_pulls: currentCumPulls,
                cumulative_cost: currentCumCost
            });
        });

        result.value = {
            packs: strategySequence,
            cost: cost,
            total_pulls: pulls,
            surplus: normalizePullValue(pulls - neededPulls)
        };

        setTimeout(() => {
            document.getElementById('result-section')?.scrollIntoView({ behavior: 'smooth' });
        }, 100);

    } catch (e) {
        console.error(e);
        ElMessage.error('计算出错: ' + String(e));
    } finally {
        loading.value = false;
    }
};
</script>

<template>
  <div class="recharge-container">
    <div class="header-section mb-4">
        <h2>凡修充值礼包优化计算器 (Beta)</h2>
        <p class="text-gray">基于背包算法的最优购买策略生成工具</p>
    </div>

    <!-- Global Config -->
    <el-card class="box-card mb-4" shadow="hover">
      <template #header>
        <div class="card-header">
          <span>总计</span>
        </div>
      </template>
      <el-row :gutter="20" align="middle">
        <el-col :span="8">
          <div class="stat-item">
            <div class="label">总目标抽数</div>
            <div class="value-lg">{{ formatPulls(totalTargetPulls) }}</div>
          </div>
        </el-col>
        <el-col :span="16">
          <el-alert type="info" :closable="false" show-icon>
              <template #title>
                  当前已拥有: <strong>{{ formatPulls(currentStatus.total_pulls) }}</strong> / {{ formatPulls(totalTargetPulls) }} 抽
                  &nbsp;|&nbsp;
                  已花费: <strong>{{ currentStatus.total_spent }}</strong> 元
              </template>
          </el-alert>
        </el-col>
      </el-row>
    </el-card>

    <!-- Rounds Config -->
    <div class="rounds-section mb-4">
        <el-tabs v-model="activeTab" type="border-card" editable @edit="handleRoundsEdit">
            <el-tab-pane
                v-for="(round, idx) in rounds"
                :key="idx"
                :label="`第 ${round.id} 轮`"
                :name="String(idx)"
            >
                <div class="round-config mb-4">
                    <el-row :gutter="20">
                        <el-col :span="8">
                            <el-form-item label="本轮目标抽数">
                                <el-input-number v-model="round.target_pulls" :min="0" />
                            </el-form-item>
                        </el-col>
                        <el-col :span="8">
                            <el-form-item label="免费/灵石抽数">
                                <el-input-number v-model="round.free_pulls" :min="0" />
                            </el-form-item>
                        </el-col>
                    </el-row>
                </div>

                <div class="packs-table">
                    <div class="table-header mb-2 flex justify-between">
                        <h4>礼包配置</h4>
                        <div class="flex items-center gap-2">
                            <el-dropdown @command="(cmd: number) => fillDefaultPacks(idx, cmd)">
                                <el-button size="small">
                                    {{ getRoundPresetName(round) }}<el-icon class="el-icon--right"><ArrowDown /></el-icon>
                                </el-button>
                                <template #dropdown>
                                    <el-dropdown-menu>
                                        <el-dropdown-item v-for="(p, pIdx) in PRESETS" :key="pIdx" :command="pIdx">
                                            {{ p.name }}
                                        </el-dropdown-item>
                                    </el-dropdown-menu>
                                </template>
                            </el-dropdown>
                            <el-button size="small" :icon="Plus" @click="addPack(idx)">添加礼包</el-button>
                        </div>
                    </div>
                    
                    <el-table :data="round.packs" style="width: 100%" size="small" border>
                        <el-table-column label="价格(元)" width="140">
                            <template #default="scope">
                                <el-input-number v-model="scope.row.price" :min="0" :controls="false" style="width: 100%" />
                            </template>
                        </el-table-column>
                        <el-table-column label="包含抽数" width="140">
                            <template #default="scope">
                                <el-input-number
                                    v-model="scope.row.pulls"
                                    :min="0"
                                    :step="0.5"
                                    :controls="false"
                                    style="width: 100%"
                                />
                            </template>
                        </el-table-column>
                        <el-table-column label="性价比" width="100">
                            <template #default="scope">
                                {{ (scope.row.pulls > 0 ? scope.row.price / scope.row.pulls : 0).toFixed(2) }}
                            </template>
                        </el-table-column>
                        <el-table-column label="限购(0=无限)" width="120">
                            <template #default="scope">
                                <el-input-number v-model="scope.row.limit" :min="0" :controls="false" style="width: 100%" />
                            </template>
                        </el-table-column>
                        <el-table-column label="买满累计(抽/元)" width="170">
                            <template #default="scope">
                                <span class="text-sm">{{ formatPackFullPurchaseSummary(round.packs, scope.$index) }}</span>
                            </template>
                        </el-table-column>
                        <el-table-column label="已购买" width="120">
                            <template #default="scope">
                                <el-input-number
                                    v-model="scope.row.purchased"
                                    :min="0"
                                    :max="scope.row.limit > 0 ? scope.row.limit : undefined"
                                    controls-position="right"
                                    style="width: 100%"
                                />
                            </template>
                        </el-table-column>
                        <el-table-column label="操作" width="60" align="center">
                            <template #default="scope">
                                <el-button type="danger" link :icon="Delete" @click="removePack(idx, scope.$index)"></el-button>
                            </template>
                        </el-table-column>
                    </el-table>
                </div>
            </el-tab-pane>
        </el-tabs>
    </div>

    <!-- Calculate Button -->
    <div class="actions-section mb-5">
        <el-button type="primary" size="large" :loading="loading" @click="calculateStrategy" style="width: 300px;">
            <el-icon class="mr-2"><ShoppingCart /></el-icon>
            生成最优购买序列
        </el-button>
    </div>

    <!-- Result -->
    <div v-if="result" id="result-section" class="result-section">
        <el-card class="result-card" :class="{'success-border': result.surplus >= 0}">
            <template #header>
                <div class="card-header text-primary">
                    <h3>购买策略建议</h3>
                </div>
            </template>

            <div v-if="result.message" class="py-4">
                <el-result icon="success" :title="result.message"></el-result>
            </div>

            <div v-else>
                <el-row :gutter="20" class="mb-4">
                    <el-col :span="8">
                        <div class="stat-box">
                            <div class="label">预计总花费</div>
                            <div class="value text-danger">{{ result.cost }} <small>元</small></div>
                        </div>
                    </el-col>
                    <el-col :span="8">
                        <div class="stat-box">
                            <div class="label">获得额外抽数</div>
                            <div class="value">{{ formatPulls(result.total_pulls) }} <small>抽</small></div>
                        </div>
                    </el-col>
                    <el-col :span="8">
                        <div class="stat-box">
                            <div class="label">最终溢出</div>
                            <div class="value text-success">{{ formatPulls(result.surplus) }} <small>抽</small></div>
                        </div>
                    </el-col>
                </el-row>

                <h4 class="mb-3">推荐购买顺序</h4>
                <div class="strategy-list">
                    <div v-for="(item, idx) in result.packs" :key="idx" 
                         class="strategy-item"
                         :class="`item-${item.status}`">
                        <div class="item-info">
                            <el-tag size="small" effect="plain" class="mr-2">第 {{ item.round_idx }} 轮</el-tag>
                            <span class="font-bold mr-2">{{ item.name }}</span>
                            <span class="text-gray-500 text-sm">({{ item.price }}元, {{ formatPulls(item.pulls) }}抽)</span>
                        </div>
                        
                        <div class="item-meta">
                            <div class="text-xs text-gray-400">
                                累计: {{ formatPulls(item.cumulative_pulls || 0) }}抽 / {{ item.cumulative_cost }}元
                            </div>
                            <div class="flex items-center gap-2">
                                <span class="text-xs text-gray-400">性价比: {{ item.unit_price?.toFixed(2) }}</span>
                                <el-tag size="small" :type="item.status === 'safe' ? 'success' : (item.status === 'cautious' ? 'warning' : 'info')">
                                    {{ item.recommendation }}
                                </el-tag>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </el-card>
    </div>
  </div>
</template>

<style scoped>
.recharge-container {
    padding: 20px;
    max-width: 1000px;
}

.text-gray { color: #909399; }
.text-danger { color: #F56C6C; }
.text-success { color: #67C23A; }
.text-primary { color: #409EFF; }
.font-bold { font-weight: bold; }
.text-sm { font-size: 0.875rem; }
.text-xs { font-size: 0.75rem; }
.mr-2 { margin-right: 0.5rem; }
.ml-2 { margin-left: 0.5rem; }
.mb-2 { margin-bottom: 0.5rem; }
.mb-3 { margin-bottom: 0.75rem; }
.mb-4 { margin-bottom: 1rem; }
.mb-5 { margin-bottom: 1.25rem; }

.flex { display: flex; }
.justify-between { justify-content: space-between; }
.items-center { align-items: center; }
.gap-2 { gap: 0.5rem; }

.stat-item .label, .stat-box .label {
    font-size: 14px;
    color: #909399;
}
.stat-item .value-lg {
    font-size: 24px;
    font-weight: bold;
}
.stat-box {
    background: #f5f7fa;
    padding: 15px;
    border-radius: 8px;
}
.stat-box .value {
    font-size: 28px;
    font-weight: bold;
    margin-top: 5px;
}

.strategy-item {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 12px 15px;
    border: 1px solid #EBEEF5;
    border-left-width: 4px;
    margin-bottom: 8px;
    border-radius: 4px;
    background-color: white;
}

.item-safe {
    border-left-color: #67C23A;
    background-color: #f0f9eb;
}
.item-cautious {
    border-left-color: #E6A23C;
    background-color: #fdf6ec;
}
.item-overflow {
    border-left-color: #909399;
    background-color: #f4f4f5;
    opacity: 0.8;
}

.item-meta {
    text-align: right;
    display: flex;
    flex-direction: column;
    align-items: flex-end;
    gap: 4px;
}
</style>
