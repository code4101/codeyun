<script setup lang="ts">
interface GradeTier {
  range: string
  realm: string
  art: string
  equipment: string
  item: string
  color: string
}

const tiers: GradeTier[] = [
  { range: '1–3', realm: '炼气期', art: '黄阶', equipment: '法器', item: '一阶', color: '#7f878c' },
  { range: '4–6', realm: '筑基期', art: '玄阶', equipment: '灵器', item: '二阶', color: '#2f8798' },
  { range: '7–9', realm: '结丹期', art: '地阶', equipment: '道器', item: '三阶', color: '#7449a8' },
  { range: '10–12', realm: '元婴期', art: '天阶', equipment: '灵宝', item: '四阶', color: '#b56b24' },
  { range: '13–15', realm: '化神期', art: '灵阶', equipment: '玄宝', item: '五阶', color: '#a62f46' },
]
</script>

<template>
  <main class="grade-reference-page">
    <header class="page-head">
      <h1>造化仙缘 · 品阶对照</h1>
      <p>不同类别共用同一套 1–15 档品阶权重；可按境界每提升一阶开放三档，作为自我限制参考。</p>
    </header>

    <div class="table-scroll">
      <table>
        <thead>
          <tr>
            <th>档位</th>
            <th>建议境界</th>
            <th>功法、术法</th>
            <th>装备</th>
            <th>丹药、材料</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="tier in tiers" :key="tier.range" :style="{ '--tier-color': tier.color }">
            <td class="range-cell">{{ tier.range }}</td>
            <td class="realm-cell">{{ tier.realm }}</td>
            <td><span class="tier-name">{{ tier.art }}</span><span class="qualities">下 / 中 / 上品</span></td>
            <td><span class="tier-name">{{ tier.equipment }}</span><span class="qualities">下 / 中 / 上品</span></td>
            <td><span class="tier-name">{{ tier.item }}</span><span class="qualities">下 / 中 / 上品</span></td>
          </tr>
        </tbody>
      </table>
    </div>

    <p class="footnote">化神以上仍以第 15 档为上限。本表是自我限制建议，并非游戏原生使用条件。</p>
  </main>
</template>

<style scoped>
.grade-reference-page {
  box-sizing: border-box;
  height: 100%;
  min-height: 0;
  padding: 18px 22px 28px;
  overflow: auto;
  color: #272b2f;
  background: #f6f7f5;
}

.page-head {
  margin-bottom: 18px;
}

.page-head h1 {
  margin: 0;
  font-size: 22px;
}

.page-head p {
  margin: 6px 0 0;
  color: #6c7379;
}

.table-scroll {
  max-width: 100%;
  overflow-x: auto;
}

table {
  width: max-content;
  min-width: 760px;
  max-width: 100%;
  border-collapse: collapse;
  background: #fff;
}

th,
td {
  height: 52px;
  padding: 7px 18px;
  border-bottom: 1px solid #e5e8e5;
  text-align: left;
  white-space: nowrap;
}

th {
  height: 36px;
  color: #555d61;
  background: #eef1ee;
  font-size: 13px;
  font-weight: 600;
}

tbody tr {
  border-left: 4px solid var(--tier-color);
}

tbody tr:hover {
  background: #f8faf7;
}

.range-cell {
  color: var(--tier-color);
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}

.realm-cell,
.tier-name {
  font-weight: 600;
}

.qualities {
  margin-left: 10px;
  color: #737a7e;
}

.footnote {
  margin: 14px 0 0;
  color: #747b7f;
  font-size: 13px;
}

@media (max-width: 760px) {
  .grade-reference-page {
    padding: 14px;
  }

  th,
  td {
    padding-right: 13px;
    padding-left: 13px;
  }
}
</style>
