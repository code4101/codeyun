<script setup lang="ts">
import { QuestionFilled } from '@element-plus/icons-vue';
import { computed, reactive, ref, watch } from 'vue';
import {
  FANXIU_CULTIVATION_REALMS,
  FANXIU_CULTIVATION_STAGES,
  analyzeCuijianStrategy,
  cultivationToValue,
  parseCultivationText,
  type FanxiuCultivationLevel,
  type FanxiuCultivationRealm,
  type FanxiuCultivationStage,
} from '@/utils/fanxiuCuijian';

interface CompanionFormItem {
  realm: FanxiuCultivationRealm;
  stage: FanxiuCultivationStage;
  layer: number;
}

interface CuijianLocalState {
  hardScore: number;
  easyScore: number;
  tolerance: number;
  companions: CompanionFormItem[];
}

const STORAGE_KEY = 'codeyun_fanxiu_cuijian_trial_v1';
const EXAMPLE_LEVELS = [
  '大乘后期3层',
  '大乘后期3层',
  '大乘后期2层',
  '大乘后期2层',
  '大乘中期10层',
  '大乘中期10层',
  '大乘前期10层',
  '大乘前期10层',
  '大乘前期1层',
  '大乘前期1层',
];

const canUseLocalStorage = () => typeof window !== 'undefined' && typeof window.localStorage !== 'undefined';
const normalizePositiveInteger = (value: unknown, fallback: number) => {
  const normalized = Number(value);
  if (!Number.isFinite(normalized)) return fallback;
  return Math.max(0, Math.trunc(normalized));
};

const cloneCompanion = (item: CompanionFormItem): CompanionFormItem => ({
  realm: item.realm,
  stage: item.stage,
  layer: item.layer,
});

const levelToCompanion = (level: FanxiuCultivationLevel | null): CompanionFormItem => ({
  realm: level?.realm ?? '大乘',
  stage: level?.stage ?? '前期',
  layer: level?.layer ?? 1,
});

const createExampleState = (): CuijianLocalState => ({
  hardScore: 1112,
  easyScore: 1055,
  tolerance: 10,
  companions: EXAMPLE_LEVELS.map((text) => levelToCompanion(parseCultivationText(text))),
});

const normalizeCompanion = (value: unknown, fallback: CompanionFormItem): CompanionFormItem => {
  const raw = value && typeof value === 'object' ? (value as Partial<CompanionFormItem>) : {};
  const realm = FANXIU_CULTIVATION_REALMS.includes(raw.realm as FanxiuCultivationRealm)
    ? (raw.realm as FanxiuCultivationRealm)
    : fallback.realm;
  const stage = FANXIU_CULTIVATION_STAGES.includes(raw.stage as FanxiuCultivationStage)
    ? (raw.stage as FanxiuCultivationStage)
    : fallback.stage;
  const layer = Math.min(10, Math.max(1, Math.trunc(Number(raw.layer) || fallback.layer)));
  return { realm, stage, layer };
};

const loadState = (): CuijianLocalState => {
  const fallback = createExampleState();
  if (!canUseLocalStorage()) {
    return fallback;
  }

  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) {
      return fallback;
    }

    const payload = JSON.parse(raw) as Partial<CuijianLocalState>;
    const companions = Array.from({ length: fallback.companions.length }, (_, index) =>
      normalizeCompanion(payload.companions?.[index], fallback.companions[index]),
    );

    return {
      hardScore: normalizePositiveInteger(payload.hardScore, fallback.hardScore),
      easyScore: normalizePositiveInteger(payload.easyScore, fallback.easyScore),
      tolerance: normalizePositiveInteger(payload.tolerance, fallback.tolerance),
      companions,
    };
  } catch {
    return fallback;
  }
};

const serializeState = (state: CuijianLocalState) => ({
  hardScore: state.hardScore,
  easyScore: state.easyScore,
  tolerance: state.tolerance,
  companions: state.companions.map(cloneCompanion),
});

const state = reactive<CuijianLocalState>(loadState());
const realmOptions = [...FANXIU_CULTIVATION_REALMS].reverse();
const refreshToken = ref(0);

watch(
  state,
  () => {
    if (!canUseLocalStorage()) return;
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(serializeState(state)));
  },
  { deep: true },
);

const companionValues = computed(() =>
  state.companions.map((item) => cultivationToValue(item.realm, item.stage, item.layer)),
);

const strategy = computed(() => {
  refreshToken.value;
  return analyzeCuijianStrategy(companionValues.value, {
    gate1: state.hardScore,
    gate2: state.easyScore,
    tolerance: state.tolerance,
  });
});
const strategyRows = computed(() => strategy.value.rows);
const formatDelta = (value: number) => (value > 0 ? `+${value}` : '0');
const refreshCompanions = () => {
  state.companions = [...state.companions]
    .sort((left, right) => {
      const leftValue = cultivationToValue(left.realm, left.stage, left.layer);
      const rightValue = cultivationToValue(right.realm, right.stage, right.layer);
      return rightValue - leftValue;
    })
    .map(cloneCompanion);
  refreshToken.value += 1;
};

</script>

<template>
  <div class="cuijian-page">
    <div class="page-shell">
      <div class="main-grid">
        <div class="input-column">
          <el-card class="section-card" shadow="never">
            <template #header>
              <div class="card-header">
                <div class="card-title-with-help">
                  <span>关卡配置</span>
                  <el-popover trigger="click" placement="right-start" :width="430">
                    <template #reference>
                      <el-button
                        class="help-button"
                        size="small"
                        circle
                        aria-label="查看淬剑试炼说明"
                      >
                        <el-icon><QuestionFilled /></el-icon>
                      </el-button>
                    </template>
                    <div class="cuijian-help-doc">
                      <h4>淬剑试炼说明</h4>
                      <p>等级数值以“大乘前期 1 层 = 201”为锚点，每升 1 层数值 +1。前期、中期、后期各 10 层，境界顺序按炼气到金仙连续递增。</p>
                      <p>第 1 关和第 2 关各固定 5 人。默认前 5 人去第 1 关，后 5 人去第 2 关；默认能过时不调整，过不了才搜索新的 5+5 分配。</p>
                      <p>容差表示可卡线范围：队伍总分达到“关卡难度 - 容差”就算可过。关卡配置第 2 行显示“当前分 + 还需升级数”。</p>
                      <p>表格里的“再升几级”按当前推荐分队计算；需要补等级时优先给低等级仙侣，因为低等级升级经验更划算，而每级提供的数值都是 +1。</p>
                    </div>
                  </el-popover>
                </div>
              </div>
            </template>

          <div class="stage-grid">
            <div class="field-block">
              <span class="field-label">第1关难度</span>
              <el-input-number
                v-model="state.hardScore"
                :min="0"
                :step="1"
                controls-position="right"
              />
            </div>
              <div class="field-block">
                <span class="field-label">第2关难度</span>
                <el-input-number
                  v-model="state.easyScore"
                  :min="0"
                  :step="1"
                  controls-position="right"
                />
              </div>
              <div class="field-block">
              <span class="field-label">容差</span>
              <el-input-number v-model="state.tolerance" :min="0" :controls="false" />
            </div>
          </div>

          <div class="strategy-grid">
            <div class="strategy-cell">
              <span class="strategy-value">{{ strategy.chosenSplit.gate1Team.sum }}</span>
              <span class="strategy-plus">+{{ strategy.upgradePlan.gate1Delta }}</span>
            </div>
            <div class="strategy-cell">
              <span class="strategy-value">{{ strategy.chosenSplit.gate2Team.sum }}</span>
              <span class="strategy-plus">+{{ strategy.upgradePlan.gate2Delta }}</span>
            </div>
            <div class="strategy-cell strategy-cell-empty"></div>
          </div>
        </el-card>

          <el-card class="section-card" shadow="never">
            <template #header>
              <div class="card-header">
                <div class="card-actions">
                  <span>仙侣等级</span>
                  <el-button size="small" @click="refreshCompanions">更新</el-button>
                </div>
              </div>
            </template>

            <div class="table-wrap">
              <table class="companion-table">
                <thead>
                  <tr>
                    <th class="index-column">#</th>
                    <th class="realm-column">境界</th>
                    <th class="stage-column">小境界</th>
                    <th class="layer-column">层数</th>
                    <th class="value-column">数值</th>
                    <th class="gate-column">去第几关</th>
                    <th class="delta-column">再升几级</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="(item, index) in state.companions" :key="index">
                    <td class="index-column">
                      <span class="member-index">{{ index + 1 }}</span>
                    </td>
                    <td>
                      <el-select v-model="item.realm" class="cell-select">
                        <el-option
                          v-for="realm in realmOptions"
                          :key="realm"
                          :label="realm"
                          :value="realm"
                        />
                      </el-select>
                    </td>
                    <td>
                      <el-select v-model="item.stage" class="cell-select">
                        <el-option
                          v-for="stage in FANXIU_CULTIVATION_STAGES"
                          :key="stage"
                          :label="stage"
                          :value="stage"
                        />
                      </el-select>
                    </td>
                    <td>
                      <el-input-number
                        v-model="item.layer"
                        class="cell-number"
                        :min="1"
                        :max="10"
                        controls-position="right"
                      />
                    </td>
                    <td class="value-column">
                      <span class="value-badge">{{ companionValues[index] }}</span>
                    </td>
                    <td class="gate-column">
                      <el-tag
                        :type="strategyRows[index].gateKey === 'gate1' ? 'danger' : 'success'"
                        effect="plain"
                        class="strategy-tag"
                      >
                        {{ strategyRows[index].gateLabel }}
                      </el-tag>
                    </td>
                    <td class="delta-column">
                      <span :class="['delta-pill', { 'is-idle': strategyRows[index].delta === 0 }]">
                        {{ formatDelta(strategyRows[index].delta) }}
                      </span>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </el-card>
        </div>

      </div>
    </div>
  </div>
</template>

<style scoped>
.cuijian-page {
  --page-bg-top: rgba(214, 160, 57, 0.16);
  --page-bg-side: rgba(183, 107, 16, 0.08);
  --card-border: rgba(234, 217, 181, 0.92);
  --card-bg: rgba(255, 255, 255, 0.94);
  --text-main: #3f2b0c;
  --text-soft: #6f5a37;
  --text-muted: #8a7453;
  min-height: 100%;
  padding: 24px;
  background:
    radial-gradient(circle at top right, var(--page-bg-top), transparent 24%),
    radial-gradient(circle at left center, var(--page-bg-side), transparent 28%),
    linear-gradient(180deg, #fff8ec 0, #f6f8fc 220px, #ffffff 100%);
}

.page-shell {
  max-width: 1520px;
}

.page-header,
.title-row,
.card-title-with-help,
.header-actions,
.card-header,
.secondary-stats,
.mini-team-members {
  display: flex;
}

.page-header,
.card-header {
  justify-content: space-between;
}

.page-header,
.main-grid,
.team-grid,
.member-list,
.secondary-stats,
.mini-team-grid {
  gap: 16px;
}

.page-header {
  align-items: flex-start;
  margin-bottom: 18px;
}

.header-main {
  max-width: 860px;
}

.page-kicker {
  margin: 0 0 8px;
  font-size: 13px;
  letter-spacing: 0.08em;
  color: #a56b10;
  text-transform: uppercase;
}

.title-row {
  align-items: center;
  gap: 10px;
}

.card-title-with-help {
  align-items: center;
  gap: 8px;
}

.page-title {
  margin: 0;
  font-size: 34px;
  color: var(--text-main);
}

.help-button {
  width: 22px;
  height: 22px;
  min-height: 22px;
  border-color: rgba(196, 138, 22, 0.28);
  color: #8a4d00;
  background: rgba(255, 249, 237, 0.82);
}

.cuijian-help-doc {
  display: flex;
  flex-direction: column;
  gap: 10px;
  color: #5f4b2f;
  line-height: 1.65;
}

.cuijian-help-doc h4 {
  margin: 0;
  font-size: 15px;
  color: var(--text-main);
}

.cuijian-help-doc p {
  margin: 0;
}

.help-panel {
  display: flex;
  flex-direction: column;
  gap: 10px;
  color: #5f4b2f;
  line-height: 1.6;
}

.help-panel p {
  margin: 0;
}

.header-actions,
.secondary-stats,
.mini-team-members {
  align-items: center;
  flex-wrap: wrap;
}

.section-card {
  border-radius: 22px;
  border: 1px solid var(--card-border);
  background: var(--card-bg);
}

.main-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  align-items: start;
}

.input-column,
.result-column,
.team-panel,
.member-list,
.mini-team-panel {
  display: flex;
  flex-direction: column;
}

.input-column,
.result-column {
  gap: 16px;
}

.sticky-card {
  position: sticky;
  top: 20px;
}

.card-header {
  align-items: center;
  gap: 12px;
  font-size: 16px;
  font-weight: 600;
  color: var(--text-main);
}

.card-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.stage-grid {
  display: grid;
  grid-template-columns: repeat(3, max-content);
  justify-content: start;
  gap: 28px;
}

.strategy-grid {
  display: grid;
  grid-template-columns: repeat(3, max-content);
  justify-content: start;
  gap: 28px;
  margin-top: 12px;
}

.field-block {
  display: flex;
  flex-direction: column;
  gap: 8px;
  width: 150px;
}

.field-label {
  font-size: 12px;
  color: var(--text-muted);
}

.field-block :deep(.el-input-number) {
  width: 100%;
}

.strategy-cell {
  width: 150px;
  min-height: 28px;
  display: flex;
  align-items: center;
  gap: 6px;
  padding-left: 2px;
  color: var(--text-soft);
}

.strategy-cell-empty {
  pointer-events: none;
}

.strategy-value,
.strategy-plus {
  font-size: 13px;
  line-height: 1;
}

.strategy-value {
  color: var(--text-main);
  font-weight: 600;
}

.strategy-plus {
  color: #8a7453;
}

.upgrade-summary,
.balanced-summary {
  margin-top: 14px;
  line-height: 1.6;
  color: var(--text-soft);
}

.table-wrap,
.upgrade-table-wrap {
  overflow-x: auto;
}

.companion-table,
.upgrade-table {
  width: max-content;
  border-collapse: separate;
  border-spacing: 0 10px;
}

.companion-table {
  min-width: 620px;
}

.upgrade-table {
  min-width: 720px;
}

.companion-table th,
.upgrade-table th {
  padding: 0 12px 4px;
  text-align: left;
  font-size: 12px;
  font-weight: 600;
  color: var(--text-muted);
}

.companion-table td,
.upgrade-table td {
  padding: 10px 12px;
  background: linear-gradient(180deg, rgba(255, 250, 241, 0.98), rgba(250, 252, 255, 0.96));
  color: var(--text-main);
  border-top: 1px solid rgba(234, 217, 181, 0.68);
  border-bottom: 1px solid rgba(234, 217, 181, 0.68);
}

.companion-table td:first-child,
.upgrade-table td:first-child {
  border-left: 1px solid rgba(234, 217, 181, 0.68);
  border-radius: 16px 0 0 16px;
}

.companion-table td:last-child,
.upgrade-table td:last-child {
  border-right: 1px solid rgba(234, 217, 181, 0.68);
  border-radius: 0 16px 16px 0;
}

.index-column {
  width: 70px;
  text-align: center;
}

.realm-column {
  width: 88px;
}

.stage-column {
  width: 88px;
}

.layer-column {
  width: 98px;
}

.gate-column,
.value-column,
.delta-column {
  width: 110px;
  text-align: center;
}

.member-index,
.value-badge,
.delta-pill {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 34px;
  min-height: 34px;
  padding: 0 12px;
  border-radius: 999px;
  font-weight: 700;
}

.member-index {
  width: 34px;
  padding: 0;
  background: linear-gradient(135deg, #6d4f17, #b67a1a);
  color: #fffdf8;
}

.value-badge {
  background: rgba(173, 122, 22, 0.12);
  color: #8a4d00;
}

.delta-pill {
  background: rgba(212, 141, 20, 0.16);
  color: #9a5806;
}

.delta-pill.is-idle {
  background: rgba(148, 163, 184, 0.14);
  color: #687385;
}

.cell-select,
.cell-number {
  width: 100%;
}

.companion-table .cell-number {
  width: 92px;
}

.strategy-tag {
  min-width: 68px;
  justify-content: center;
}

.headline-panel {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 16px 18px;
  border-radius: 18px;
  border: 1px solid rgba(234, 217, 181, 0.72);
}

.headline-panel.is-pass {
  background: linear-gradient(135deg, rgba(240, 249, 235, 0.94), rgba(255, 251, 244, 0.92));
}

.headline-panel.is-fail {
  background: linear-gradient(135deg, rgba(253, 246, 236, 0.95), rgba(255, 250, 244, 0.92));
}

.headline-title {
  font-size: 22px;
  line-height: 1.35;
  color: var(--text-main);
}

.headline-meta {
  color: var(--text-soft);
  line-height: 1.5;
}

.team-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  margin-top: 16px;
}

.team-panel,
.mini-team-panel {
  gap: 10px;
  padding: 16px;
  border-radius: 18px;
  border: 1px solid rgba(234, 217, 181, 0.72);
}

.hard-team-panel {
  background: linear-gradient(180deg, rgba(255, 245, 238, 0.96), rgba(255, 251, 246, 0.92));
}

.easy-team-panel {
  background: linear-gradient(180deg, rgba(243, 251, 241, 0.96), rgba(255, 251, 246, 0.92));
}

.team-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 17px;
  font-weight: 700;
  color: var(--text-main);
}

.team-meta {
  color: var(--text-soft);
  line-height: 1.5;
}

.member-list {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(146px, 1fr));
  gap: 10px;
}

.member-chip {
  padding: 11px 12px;
  border-radius: 14px;
  border: 1px solid rgba(148, 163, 184, 0.16);
  background: rgba(255, 255, 255, 0.86);
}

.member-chip-top,
.mini-team-grid {
  display: flex;
}

.member-chip-top {
  justify-content: space-between;
  align-items: center;
  gap: 10px;
}

.member-chip-slot {
  font-weight: 700;
  color: #5f4b2f;
}

.member-chip-score {
  font-weight: 700;
  color: #8a4d00;
}

.member-chip-text {
  margin-top: 8px;
  color: #5f4b2f;
  line-height: 1.5;
}

.member-chip-upgrade {
  margin-top: 6px;
  color: #b45309;
  line-height: 1.5;
}

.secondary-stats {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  margin-top: 16px;
}

.secondary-stat {
  padding: 14px 16px;
  border-radius: 16px;
  background: linear-gradient(180deg, rgba(255, 248, 231, 0.9), rgba(250, 252, 255, 0.92));
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.secondary-stat span {
  font-size: 12px;
  color: var(--text-muted);
}

.secondary-stat strong {
  font-size: 24px;
  color: var(--text-main);
}

.mini-team-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  margin-top: 14px;
}

.mini-team-title {
  font-size: 13px;
  color: var(--text-muted);
}

.mini-team-members {
  gap: 8px;
}

.text-success {
  color: #2f8f46;
}

.text-danger {
  color: #c45656;
}

@media (max-width: 1180px) {
  .main-grid,
  .team-grid,
  .secondary-stats,
  .mini-team-grid,
  .stage-grid,
  .strategy-grid {
    grid-template-columns: 1fr;
  }

  .sticky-card {
    position: static;
  }
}

@media (max-width: 768px) {
  .cuijian-page {
    padding: 16px;
  }

  .page-header {
    flex-direction: column;
  }

  .header-actions {
    width: 100%;
  }

  .header-actions :deep(.el-button) {
    flex: 1;
  }

  .card-header {
    flex-direction: column;
    align-items: stretch;
  }

  .page-title {
    font-size: 30px;
  }
}
</style>
