# Handsontable 表格开发说明

> Last Updated: 2026-04-21

## 1. 定位

在这个项目里，后续凡是偏“后台工具台”“批量录入”“结果校对”“半结构化数据编辑”的表格场景，都应优先考虑 `Handsontable`，而不是先默认退回到普通展示型表格。

重点不是把它当成“一个表格组件”，而是把它当成“嵌在页面里的轻量电子表格”。

适合优先使用 `Handsontable` 的场景：

- 批量粘贴 ID、订单号、账号、路径、标签等数据
- 多行批量录入，再调用后端接口统一处理
- 查单、校验、修正、补全这类“先查后改”的工作台
- 有明显“只读列 + 少数可编辑列”的结果表
- 用户更像在用 Excel，而不是在看后台列表

不必强行使用 `Handsontable` 的场景：

- 纯历史记录列表
- 纯只读分页表
- 数据量很大、主要诉求是服务端分页和排序
- 只是普通后台 CRUD 展示，没有单元格编辑需求

一句话原则：

**偏编辑工作台，用 `Handsontable`；偏历史列表，用普通表格。**

## 2. 当前仓库里的落地基线

目前仓库内已经有一套可直接复用的 `Handsontable` 落地样板，入口在：

- [frontend/src/standard/attendance/orders/page.vue](/D:/home/chenkunze/slns/codeyun/frontend/src/standard/attendance/orders/page.vue:5)

依赖声明在：

- [frontend/package.json](/D:/home/chenkunze/slns/codeyun/frontend/package.json:13)

实际依赖：

- `@handsontable/vue3`
- `handsontable`

页面里已经用到的关键接法包括：

- `HotTable` 组件挂载
- `registerAllModules()`
- `zhCN` 语言包注册
- 表格实例 `hotInstance` 引用
- `afterChange / afterRender / afterCreateRow / afterRemoveRow`
- `cells` 回调控制只读列
- `loadData()` 与 `getSourceData()` 双向同步

这意味着后续再做类似表格，不需要重新试验选型，直接沿这套模式扩就行。

## 3. 推荐把 Handsontable 当成默认实现的原因

`Element Plus el-table` 更偏“展示型后台表格”，而 `Handsontable` 更偏“可操作电子表格”。对内部工具页来说，后者通常更顺手。

`Handsontable` 的优势主要在这些能力：

- 支持直接批量粘贴
- 单元格编辑是原生中心能力，不是后补插件
- 行列尺寸可手调
- 自带右键菜单
- 容易保留空白备用行
- 容易做“结果表里的局部可编辑列”
- 用户心智更接近 Excel，学习成本低

所以以后遇到下面这类需求，默认优先方案应该是：

1. 先做一块 `Handsontable` 输入区。
2. 后端返回结果后继续映射回 `Handsontable`。
3. 把少量允许修正的列留成可编辑。
4. 再发起下一步动作。

这比“`el-table` 里塞一堆 `el-input` / `el-select`”更自然，也更省代码。

## 4. 标准集成方式

### 4.1 基础依赖与初始化

参考现有页面：

```ts
import { HotTable } from '@handsontable/vue3'
import { registerAllModules } from 'handsontable/registry'
import type Handsontable from 'handsontable/base'
import type { CellProperties, ColumnSettings } from 'handsontable/settings'
import { registerLanguageDictionary, zhCN } from 'handsontable/i18n'

import 'handsontable/styles/handsontable.css'
import 'handsontable/styles/ht-theme-main.css'

registerAllModules()
registerLanguageDictionary(zhCN)
```

这套初始化建议直接照用，不要每个页面再发明一套局部写法。

### 4.2 基础组件骨架

推荐最小骨架：

```vue
<script setup lang="ts">
import { ref } from 'vue'
import { HotTable } from '@handsontable/vue3'
import type Handsontable from 'handsontable/base'
import type { ColumnSettings } from 'handsontable/settings'

type Row = {
  编号: string
  名称: string
  备注: string
}

const hotTableRef = ref<{ hotInstance: Handsontable } | null>(null)
const rows = ref<Row[]>([{ 编号: '', 名称: '', 备注: '' }])

const columns: ColumnSettings[] = [
  { data: '编号' },
  { data: '名称' },
  { data: '备注' },
]
</script>

<template>
  <HotTable
    ref="hotTableRef"
    :data="rows"
    :columns="columns"
    :col-headers="['编号', '名称', '备注']"
    :row-headers="true"
    :manual-column-resize="true"
    :manual-row-resize="true"
    :copy-paste="true"
    :context-menu="true"
    :auto-row-size="true"
    :min-spare-rows="1"
    :selection-mode="'multiple'"
    :outside-click-deselects="false"
    :theme-name="'ht-theme-main'"
    :license-key="'non-commercial-and-evaluation'"
  />
</template>
```

如果一个页面是内部工具型表格，这套可以当作起步模板。

## 5. 推荐的页面内数据模型

不要直接围着表格 DOM 写逻辑，而是保持这三层：

1. `rows`：Vue 侧的标准数据
2. `HotTable`：可视化编辑层
3. `mapToPayload / mapFromBackend`：接口入参与返回值映射层

考勤订单页已经是这个模式：

- 输入表：页面本地行模型
- 查询接口：映射为后端请求结构
- 查询结果：再映射回页面表格行模型
- 退款执行：再次映射为接口请求

这个分层的好处是：

- 表格只是编辑器，不是业务模型本身
- 接口结构变了，只改映射函数
- 以后要替换某一列的显示/校验方式，影响面更小

## 6. 推荐的同步策略

这是 `Handsontable` 最关键的一点。

不要假设 Vue 的 `rows` 和表格内部数据永远自动一致。当前仓库里已经采用了更稳的做法：

- 从表格读：`hot.getSourceData()`
- 往表格写：`hot.loadData(...)`
- 编辑完成前先 `finishEditing(false)`

推荐复用的模式：

```ts
function getHotInstance() {
  return hotTableRef.value?.hotInstance ?? null
}

function syncRowsFromGrid() {
  const hot = getHotInstance()
  if (!hot) return rows.value

  const sourceRows = hot.getSourceData() as Row[]
  rows.value = sourceRows
  return rows.value
}

function loadRowsToGrid(nextRows: Row[]) {
  rows.value = nextRows

  const hot = getHotInstance()
  if (hot) {
    hot.loadData(nextRows, 'external-update')
    hot.render()
  }
}
```

这是后续页面应优先沿用的写法。

## 7. afterChange 的推荐写法

`afterChange` 里如果不区分来源，很容易在 `loadData()` 时造成重复同步甚至循环。

参考当前模式，建议统一过滤：

```ts
function handleAfterChange(_changes: unknown, source?: string) {
  if (source === 'loadData' || source === 'external-update') return
  syncRowsFromGrid()
}
```

这个小判断最好形成固定习惯。

## 8. 只读列与“结果表局部可编辑”模式

这是 `Handsontable` 很适合做内部工具的一个关键点。

例如查单结果里：

- 订单号、金额、已返款，是后端返回结果，应只读
- 退款额度、退款原因，是人工确认项，可编辑

推荐直接用 `cells` 回调，而不是每列各写一套杂糅逻辑：

```ts
const READONLY_COLUMNS = new Set(['订单金额', '已返款'])

function cells(_row: number, _column: number, prop: string | number): CellProperties {
  const cellProperties: CellProperties = {}
  if (READONLY_COLUMNS.has(String(prop))) {
    cellProperties.readOnly = true
    cellProperties.className = 'htDimmed hot-readonly-cell'
  }
  return cellProperties
}
```

这种方式特别适合：

- 查重结果
- 匹配结果
- 自动补全结果
- AI 初步生成结果 + 人工微调

后续这些页都可以优先照这个模式做。

## 9. 推荐的交互配置

对于大多数内部工具页，建议默认开启这些配置：

- `rowHeaders: true`
- `manualColumnResize: true`
- `manualRowResize: true`
- `copyPaste: true`
- `contextMenu: true`
- `autoRowSize: true`
- `autoWrapRow: true`
- `autoWrapCol: true`
- `selectionMode: 'multiple'`
- `outsideClickDeselects: false`

常用差异项：

- 需要持续给用户留一行空白输入：`minSpareRows: 1`
- 结果表不需要空白尾行：`minSpareRows: 0`
- 内容型内部工具一般保留 `stretchH: 'none'`

这些配置在考勤订单页已经验证过，后续可以直接作为默认模板。

## 10. 高度与视觉建议

`Handsontable` 不是普通块级表格，直接交给浏览器自然撑高，往往不好看。当前页已经用了“按行数计算高度”的思路，这个值得复用。

建议：

- 少量行时不要太矮
- 大量行时不要无限拉长页面
- 结果表高度根据当前数据量动态变化

这类页面通常最好给表格包一层自己的容器，例如：

- `sheet-frame`
- 面板卡片
- 独立按钮区

这样会比把表格直接裸放到页面里更稳定。

## 11. 本地草稿保存很适合跟 Handsontable 搭配

内部工具页经常会遇到：

- 录了一半刷新了
- 查完结果没处理完先切走了
- 临时需要回来继续改

所以如果页面是明显的工作台型场景，建议默认考虑本地草稿缓存。

考勤订单页已经用了：

- 输入表草稿
- 查询结果草稿
- 退款结果草稿

并通过 `localStorage` + 作用域 key 做恢复。

这个模式以后也适合复制到：

- 批量导入预处理页
- 批量修正页
- 账号分配页
- 标签整理页

## 12. 后续开发的推荐判断顺序

以后遇到“要做一个表格页”，先按下面判断：

1. 这页是不是工作台，而不是纯历史列表？
2. 用户会不会批量粘贴、多行改动、局部修正？
3. 有没有“只读结果 + 少量人工修订列”？
4. 有没有必要保留草稿、继续编辑？

如果上面大部分答案是“是”，那默认就该优先上 `Handsontable`。

不要先从 `el-table` 开始，做到一半发现：

- 要做单元格编辑
- 要做粘贴
- 要做空白尾行
- 要做像 Excel 一样的使用感

再临时回头换方案。

## 13. 什么时候仍然保留 el-table

虽然这里强调优先用 `Handsontable`，但也不是所有表格都要统一替换。

仍然适合保留 `el-table` 的典型场景：

- 退款历史
- 审计日志
- 只读统计报表
- 纯分页数据表
- 权限列表、账号列表这类标准后台列表

也就是：

**编辑台优先 `Handsontable`，历史列表继续 `el-table`。**

## 14. 推荐结论

后续在本项目里开发表格页时，可以把下面这句话当成默认倾向：

**只要这个页面有明显的“批量输入、批量修正、结果复核、像 Excel 一样操作”的特征，就优先用 `Handsontable` 实现。**

当前考勤订单页已经提供了第一份可复用样板，后续应尽量沿这条路线扩，而不是回到展示型表格思路。
