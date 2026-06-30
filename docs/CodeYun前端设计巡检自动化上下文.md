# CodeYun 前端设计巡检自动化上下文

## 定位

`CodeYun 前端设计巡检` 是跟随 Git 提交的前端 UI 设计自动化。

它不是普通代码瘦身任务，也不是 `UI 自主学习`。它关注的是：近期提交是否改变了 CodeYun 的前端页面、导航、交互、状态展示或信息架构；如果改变了，就以这些变化作为巡检入口，沿相关业务链路分析真实页面和既有交互，必要时做小范围修复或重构。

近期提交只是引子，不是修改边界。自动化可以修旧功能里的系统性 UI 问题，但必须能说明这些旧问题为什么会被本轮入口牵引出来，例如同一页面、同一业务对象、同一状态模型、同一菜单入口、同一表格/筛选/运行控制组件，或同一条用户决策闭环。

本自动化的核心目标永远是让 UI 模型更简单，而不是更复杂。它应使用更清晰、更少、更正交的 UI 元素呈现基本相同的信息量。默认不要新增功能、常驻控件、入口、字段、状态或解释区；优先通过合并、删除重复、收回到基础模型、调整信息层级和重构旧结构来降低复杂度。原本 30 个控件承载的信息，如果可以重构成 10 个控件且不丢失关键判断和操作能力，这是正确方向。

底层审美和设计原则统一沉淀到：

- `D:/home/chenkunze/slns/skills/前端UI规范/SKILL.md`
- `D:/home/chenkunze/slns/skills/设计品味/SKILL.md`

本自动化文档只记录操作流程、增量记忆和本自动化的验收口径。不要把长期 UI 原则复制到 automation prompt 里。

## 与其他自动化的边界

- `github自动提交`：负责每 2 小时监督自动提交是否成功。它是提交链路，不做 UI 设计审查。
- `CodeYun 代码健康优化`：负责空闲维护、代码瘦身、文档事实对齐和低风险工程整理。它可以发现前端问题，但不承担真实截图和概念设计闭环；当前端巡检判断 UI 症状背后是 API、DTO、数据结构或业务建模债务时，应通过 `docs/CodeYun自动化协作交接.md` 转交它做模型审计或小步重构。
- `UI 自主学习`：从历史会话中提取用户 UI 偏好，生成 skill patch 建议，不直接修页面。
- `CodeYun 前端设计巡检`：跟随新提交，定位前端相关变化，并以此作为入口理解业务和交互；先画概念图/线框图，再对真实页面截图做差距分析，最后修明确、可验证且与入口相关的问题。修复对象可以是旧代码或旧功能。

## 增量记忆

自动化每轮必须先读取本节。

```yaml
last_audited_commit: "0c84221402525cb9c9810e5df05aeb0ec53e6dac"
last_audited_at: "2026-06-30T18:35:16.1372940+08:00"
last_report_path: "C:/Users/kzche/AppData/Local/Temp/codeyun/ui-design-audit/2026-06-30-frontend-design-0c842214/report.md"
last_frontend_commit_summary: "完整关闭 39eed15b..0c842214：沿 notes/center 星系图的筛选-图谱-详情闭环复核，确认节点视觉计算迁移未增加 UI 概念；仅记录窄屏工具条遮挡首行节点的旧表现层风险，未保留未复验修复。"
audited_commit_count: 51
pending_or_skipped_ranges: []
```

更新规则：

- 如果本轮没有新的前端相关提交，不更新 `last_audited_commit`。
- 如果本轮发现新的前端相关提交但只生成报告，也要把 `last_audited_commit` 更新到本轮已完整审查范围的末端 commit。
- 如果本轮做了修复，完成验证后把 `last_audited_commit` 更新到修复后的 `HEAD`。
- `last_audited_commit` 表示“这个 commit 及其之前的提交都已经被本自动化判定或审查过”，不是“上次只看过的最新一个提交”。
- 每轮必须处理完整增量范围 `last_audited_commit..HEAD`。如果范围内有 2 个、5 个或更多提交，必须按从旧到新的顺序全部归类，不能只看 `HEAD` 或最新一个提交。
- 如果范围内部分提交因为服务启动失败、截图失败、冲突或验证失败无法审查完，不得把 `last_audited_commit` 推进到 `HEAD`；应在 `pending_or_skipped_ranges` 记录未审完范围和原因。
- 报告和截图放系统临时目录或 CodeYun 数据目录，例如 `%TEMP%/codeyun/ui-design-audit/`，不要放仓库根目录。
- 文档中的记忆状态可以由自动化小步更新；不要把大体积截图、日志或原始浏览器 dump 写进仓库。

## 触发判定

每轮执行：

1. 读取 `last_audited_commit`。
2. 计算完整增量提交列表：`git log --reverse --format=... last_audited_commit..HEAD`。
3. 如果没有新提交，安静跳过。
4. 对范围内每个提交分别读取改动文件，按提交归类为 `frontend_relevant` / `not_frontend_relevant` / `uncertain`。
5. 如果整个范围都没有前端相关文件，安静跳过，但可以把 `last_audited_commit` 推进到 `HEAD`，因为该范围已完成“无关提交”判定。
6. 如果任一提交包含下列路径，进入巡检：
   - `frontend/src/**`
   - `frontend/public/**`
   - `frontend/index.html`
   - `frontend/package.json`
   - `frontend/package-lock.json`
   - 与页面文案、菜单、权限、路由直接相关的 `docs/**` 或后端 API 变更

前端相关不等于必须修改。样式、布局、导航、页面状态、筛选栏、表格、弹窗、编辑器、工作台、图表、实时状态展示都应进入候选。

引子扩展规则：

- 近期提交用于回答“本轮从哪里开始看”，不是回答“只能改哪里”。
- 如果新增功能暴露出同一页面的旧布局缺陷，可以修旧布局。
- 如果新增状态让旧的规则/状态混杂更明显，可以回到状态模型或展示投影做小步收敛。
- 如果新增入口依赖旧菜单、权限、路由或公共组件，可以修相关旧入口。
- 如果截图显示问题来自上层布局或共享组件，可以修共享层，但必须控制影响面并做更宽的验证。
- 不允许因为看到一个引子就扩散到无关页面、无关模块或全项目 UI 重做。

范围聚合规则：

- 不要只审最新提交。
- 不要只看最终 diff 后得出“没有问题”，还要知道范围内哪些页面曾被哪些提交改过。
- 如果多个提交连续修改同一页面，可以合并成一个页面级巡检对象，但报告中仍要列出覆盖的 commit。
- 如果某个较早提交新增页面、较晚提交只修文案，页面仍然要按新增页面巡检。
- 如果提交 A 改后端 API、提交 B 改前端消费该 API，应合并判断页面状态投影和交互是否合理。

## 工作流程

### 1. 读原则

先读取：

- `AGENTS.md`
- `D:/home/chenkunze/slns/skills/前端UI规范/SKILL.md`
- `D:/home/chenkunze/slns/skills/设计品味/SKILL.md`
- 本文档

只在需要服务验证时再读取对应服务验证技能。

### 2. 定位变化

用只读 Git 命令理解新增提交：

- 提交列表和摘要
- 改动文件
- 前端页面、组件、路由、菜单、权限、API 调用
- 是否涉及新增页面或子页面

如果新增页面需要左侧导航可见，必须同步检查：

- `frontend/src/standard/**/index.ts`
- `frontend/src/features/access/permissionRegistry.json`
- `frontend/src/layout/MainLayout.vue`

### 3. 先做业务和交互建模

不要直接改 CSS。先回答：

- 页面核心业务对象是什么？
- 用户第一眼需要判断什么？
- 用户下一步最常做什么动作？
- 哪些是状态事实，哪些是规则配置，哪些是命令事件，哪些是结果详情？
- 当前页面适合列表、表格、inspector、工作台、流程图、规则链编辑器还是状态看板？
- 有无重复入口、重复事实、规则和状态混杂、一级页面解释过重的问题？
- 能否用更少的 UI 元素表达同等信息量？
- 哪些控件、字段、标签、说明或状态其实表达了同一事实？
- 哪些专用状态、按钮或解释可以回收到更基础的模型里？
- 如果要重构，是否能做到复杂度下降，而不是把 30 个控件变成 35 个控件？

### 3.1 根因分层和交接

每个明显 UI 问题都要先做根因分层：

- 表现层问题：CSS、间距、列宽、响应式、文本溢出或按钮拥挤。可以直接小步修。
- 前端状态投影问题：后端数据基本可用，但前端把状态、规则、命令、结果或解释混在一级界面。可以在前端做小步投影收敛。
- 后端数据投影问题：API/DTO 直接泄漏内部字段，或缺少面向 UI 的稳定状态投影，导致前端只能拼接、推断或重复解释。默认不直接大改后端，应在报告中写清建议投影，并登记到 `docs/CodeYun自动化协作交接.md`。
- 业务建模问题：状态事实、规则配置、命令事件、执行记录、派生快照或用户意图混在同一实体或流程里，导致 UI 复杂只是症状。停止自动修复，只输出对象边界、最小迁移方向和人工决策点，并登记交接。

登记交接时必须说明：

- 表层 UI 症状是什么。
- 为什么它不是单纯前端样式或文案问题。
- 疑似不正交的数据结构、API 投影或业务对象是什么。
- 前端是否已经做了最小止血。
- 建议 `CodeYun 代码健康优化` 接手做只读模型审计、小步重构、状态投影收敛，还是拆成需要人工判断的任务。

### 4. 画概念图

在报告中至少产出一种低成本设计图：

- Mermaid 业务对象图、状态图或交互流程图
- Markdown/ASCII 线框图
- 必要时用图片生成工具做低保真视觉概念稿

概念图是为了验证信息架构和交互闭环，不是为了生成装饰图。

### 5. 打开真实页面截图

按 AGENTS.md 启动或复用开发环境：

- `uv run dev.py`
- 前端日志出现 `VITE ... ready`
- 后端日志出现 `Application startup complete`

页面验证至少包含：

- 桌面宽屏
- 普通桌面
- 窄屏或移动宽度

重点看：

- 文本是否溢出或遮挡
- 表格、筛选栏、tab、按钮是否过度拉伸
- 是否有卡片套卡片、解释过多、层级过重
- 状态事实是否和规则解释混在一级列表
- 高频同步状态是否闪烁或造成布局跳动
- 新页面是否正确挂载路由、权限和侧边栏

### 6. 小步修复

只有满足这些条件才自动修：

- 问题由近期前端提交引入、暴露，或与本轮入口处在同一业务/交互链路上
- 修复范围明确
- 不需要产品语义决策
- 不大改后端领域模型；如果根因是领域模型不正交，只做窄范围状态投影或报告方案
- 能用截图、构建或真实页面复验
- 修复后 UI 概念数量不增加，或增加的局部概念能删除更多重复概念，使整体复杂度下降

可以自动修：

- 文本溢出、按钮拥挤、表格列宽、筛选栏宽度
- 重复标题、重复状态、重复入口
- 明显缺失的菜单挂载或权限路径
- 低风险的布局密度、间距、对齐和 loading/empty 状态稳定性问题
- 同一页面内由新增功能牵出的旧 UI 缺陷
- 与本轮页面共用的局部组件缺陷，前提是能验证主要调用方
- 删除或合并重复控件、重复状态、重复说明
- 把多个专用控件重构成一个更基础、更清晰的控件或状态投影
- 把一级页面里的规则解释移到二级配置、tooltip 或详情中，让主界面只呈现状态事实和下一步动作

只报告不修：

- 需要重定义业务流程
- 需要新增实体、路由或后端模型
- 需要大范围重构组件
- 涉及危险操作、权限语义或数据写入流程
- 和本轮入口没有清晰业务关联的旧问题
- 需要新增大量控件、入口或说明才能成立的方案

只报告不修的模型债务，如果和本轮入口存在清晰业务关联，必须同步登记到 `docs/CodeYun自动化协作交接.md`，供 `CodeYun 代码健康优化` 后续接手。

禁止方向：

- 不要为了修一个问题新增一批常驻按钮、卡片、说明、标签或状态。
- 不要把“更完整”误解成“展示更多字段”。
- 不要用新增控件掩盖模型不清晰；先尝试合并、删除、重排和基础模型收敛。
- 不要把旧的 30 个控件重排成新的 30 个控件后声称优化；除非信息架构显著更清晰，否则这不算完成。
- 不要牺牲必要信息量来追求少控件；目标是同等信息量下更简单，不是少到不可用。

### 7. 验证

小型前端修复通常运行：

- `npm run typecheck --prefix frontend`
- `npm run build --prefix frontend`

如果改动涉及真实页面流程，还要重新截图或实际操作页面。构建通过不等于 UI 验收通过。

## 报告格式

每轮报告至少包含：

- 本轮 commit 范围
- 是否前端相关
- 涉及页面和入口
- 业务对象与用户决策闭环
- 概念图或线框图
- UI 复杂度变化：删除/合并了哪些控件、状态、说明或入口；是否保持基本相同的信息量
- 真实截图路径
- 发现的问题
- 自动修复项
- 未修复风险
- 根因分层：表现层 / 前端状态投影 / 后端数据投影 / 业务建模
- 跨自动化交接：是否新增或更新 `docs/CodeYun自动化协作交接.md` 条目
- 验证命令和结果
- 更新后的 `last_audited_commit`

## 通知策略

- 没有新提交：`DONT_NOTIFY`
- 有新提交但无前端相关变化：`DONT_NOTIFY`
- 有前端相关变化但无问题，报告可沉淀：低频 `NOTIFY`
- 有自动修复：`NOTIFY`
- 发现需要用户判断的设计问题：`NOTIFY`
- 启动服务失败、截图失败、验证失败：`NOTIFY`

## 巡检记录

### 2026-06-30

- 完整范围：`39eed15bf5cbccb1870f75e4923db5eb7ab7b94a..0c84221402525cb9c9810e5df05aeb0ec53e6dac`
- 覆盖提交：`0c84221402525cb9c9810e5df05aeb0ec53e6dac`
- 前端入口提交：`0c84221402525cb9c9810e5df05aeb0ec53e6dac`
- 入口如何牵引到旧问题：这次提交同时更新了 `StarNotes` 与 `CustomNode`，把节点视觉计算从节点组件内收回到父层 `StarNotes`，因此本轮必须沿同一页面的“筛选规则 -> 图谱节点 -> 选中节点详情”闭环复核真实页面。提交本身没有新增控件或状态，但在同页窄屏截图里自然牵出了一个旧表现层风险：右上 `graph-toolbar` 仍直接悬浮在 graph canvas 上，会压住首行右侧节点内容。
- 本轮减法：不新增任何按钮、说明、标签、入口或状态；确认提交里的 `CustomNode` 已回到纯渲染职责，节点宽高、标题样式、分段填充文本颜色等视觉事实都由 `StarNotes` 一次性投影，不再在子组件里重复推断。
- 信息量保持：星系页仍只保留 `后端筛选 / 前端筛选 / 节点图谱 / 选中节点详情` 四段基础结构。减少的是样式计算分散在两层组件的潜在重复复杂度，不是减少用户判断依据。
- 概念图/线框图：报告中的 Mermaid 收回到 `后端筛选 -> 前端筛选 -> StarNotes 构建图节点数据 -> CustomNode 纯渲染 -> 选中节点详情` 这条基础闭环；ASCII 线框图则约束“右上工具条不应遮挡图谱首行节点”。
- 报告路径：`C:/Users/kzche/AppData/Local/Temp/codeyun/ui-design-audit/2026-06-30-frontend-design-0c842214/report.md`
- 验证：复用本地 `5173/8000` 开发环境，在 in-app Browser 对 `notes/center?tab=star` 的 `星系` 视图完成宽屏 `1600x1000`、普通桌面 `1366x900`、窄屏 `820x1180` 共 6 张截图，并选中 `考勤 Workbook 月度模板重构` 节点复核下方详情链路；`evidence.json` 记录三种视口下 `selectedTab = 星系`、`nodeCount = 590`、`bodyOverflowX = 0`、`editorVisible = true`。尝试对窄屏遮挡问题做最小 CSS 修复后，post-fix 真实页面截图复验被 in-app Browser 的本地 URL policy 阻断，因此撤回未复验样式改动。
- 根因分层：提交本身未暴露新的前端状态投影或业务建模问题；唯一记录的风险属于 `表现层问题`，即窄屏工具条遮挡首行节点。
- 跨自动化交接：无。本轮没有发现需要转交 `CodeYun 代码健康优化` 的模型债务。
- 剩余风险：`StarNotes` 的窄屏图谱目前仍允许右上工具条压住首行右侧节点，属于既有布局风险；由于 post-fix 真实截图复验被浏览器策略阻断，本轮只记录风险，不保留源码修改。
- 处理结果：本轮已完成完整增量范围的提交归类、业务/交互建模、真实多视口截图与节点详情链路复核。提交本身无新的前端复杂度回退，因此不改前端源码，直接把 `last_audited_commit` 推进到 `0c84221402525cb9c9810e5df05aeb0ec53e6dac`，保持 `pending_or_skipped_ranges` 为空。

- 完整范围：`687b8f9f37e35bafc16b9f772866bc7d861f4ca2..39eed15bf5cbccb1870f75e4923db5eb7ab7b94a`
- 覆盖提交：`39eed15bf5cbccb1870f75e4923db5eb7ab7b94a`
- 前端入口提交：`39eed15bf5cbccb1870f75e4923db5eb7ab7b94a`
- 入口如何牵引到旧问题：这次提交同时把 `fanxiu/data-annotation/runtime` 的 `framework/engine` 收回到单一 `cell` 概念，继续触达 `cluster/runtime` 的运行状态表，以及 `NoteSheetWorkspace` 的缓存占位与固定列链路。沿同一业务闭环复核后，真正需要重点排除的是两类旧风险：一是行为树页是否又把 `cell / 调度器 / 任务配置` 混成一级解释，二是工作表切 `sheet` 时会不会短暂残留上一张表，或在窄屏横向滚动里丢掉定位基准。真实页面与探针都证明这两类风险本轮没有复现。
- 本轮减法：不新增任何按钮、说明、标签、入口或状态；保留提交里已经完成的“`framework/engine` -> `cell`”基础模型收敛，并确认 `cluster/runtime` 的内容驱动宽度和 `NoteSheetWorkspace` 的 sheet 身份隔离都没有被新逻辑重新拉复杂。
- 信息量保持：行为树页仍保留运行内核、当前 cell、调度器和任务配置四段基础事实；运行管理页仍保留服务/作业名称、执行摘要、状态与下次触发；工作表仍保留 tabs、公式栏和当前 sheet 网格。减少的是潜在的重复概念与错误过渡状态，不是减少判断依据。
- 概念图/线框图：报告中的 Mermaid 把本轮入口统一收回到 `运行内核 -> 当前 cell -> 调度器 -> 服务/作业状态表 -> 工作表` 这条基础闭环；ASCII 线框图则直接约束“切 sheet 只能显示新 sheet 占位或新 sheet 内容，不能借上一张表过渡”。
- 报告路径：`C:/Users/kzche/AppData/Local/Temp/codeyun/ui-design-audit/2026-06-30-frontend-design-39eed15b/report.md`
- 验证：复用本地 `5173/8000` 开发环境，在 in-app Browser 对 `fanxiu/data-annotation/runtime`、`cluster/runtime`、`workbook/14?sheet=58855` 完成宽屏 `1600x1000`、普通桌面 `1366x900`、窄屏 `820x1180` 共 9 张主截图，并额外补 `note-sheet-narrow-scrolled.png` 证明工作表窄屏横向滚动时左侧定位基准仍由固定 clone 保持；`cluster-runtime-wide-settled.json` 证明稳定态 `loadingMasks = 0`、`rows = 29`，`note-sheet` 切换到 `sheet=58856` 的即时探针也显示 URL、标题和首屏单元格同步切成新表，没有残留旧内容。三页三视口的 `bodyOverflowX` 均为 `0`。
- 根因分层：本轮未发现需要止血的表现层、前端状态投影、后端数据投影或业务建模问题；提交中的 `cell` 收敛和缓存占位逻辑与现有页面投影保持一致。
- 跨自动化交接：无。本轮没有发现需要转交 `CodeYun 代码健康优化` 的模型债务。
- 剩余风险：`NoteSheetWorkspace` 窄屏本质仍是宽表浏览场景，继续依赖工作表内部横向滚动；`cluster/runtime` 首次进入仍可能短暂显示 loading 态，但稳定后结构正常。这两点都属于既有交互，不是本轮新增复杂度。
- 处理结果：本轮已完成完整增量范围的提交归类、概念图/线框图、真实多视口截图、工作表切换与横向滚动探针，且未发现需要自动修复的低风险 UI 问题，因此不改前端源码，直接把 `last_audited_commit` 推进到 `39eed15bf5cbccb1870f75e4923db5eb7ab7b94a`，保持 `pending_or_skipped_ranges` 为空。

- 完整范围：`e7ec5047b38dc4ca3a31336e4e5d5a4f0ceb7dd5..687b8f9f37e35bafc16b9f772866bc7d861f4ca2`
- 覆盖提交：`687b8f9f37e35bafc16b9f772866bc7d861f4ca2`
- 前端入口提交：`687b8f9f37e35bafc16b9f772866bc7d861f4ca2`
- 入口如何牵引到旧问题：这次提交同时扩展了 `fanxiu/data-annotation` 调度链、触达 `cluster/runtime`、`cluster/codex`、`attendance/configs`、`StarNotes` 和 `NoteSheetWorkspace`。真实页面里真正被牵出来的旧问题只发生在 `cluster/runtime`：新增的资源监控延迟加载让状态表再次成为首屏主结构，但遗留的 `runtime-table { width: 100%; }` 仍把右侧空白误投影进 `执行` 列，导致 `状态 / 下次触发` 两个高价值字段在宽屏和窄屏都要依赖表内横向滚动才能看全。这个问题和本轮入口同页、同一状态表闭环，属于典型的旧宽度模型被新链路放大。
- 本轮减法：不新增任何按钮、说明、标签或状态，只把 `cluster/runtime` 的表格宽度模型从拉伸式收回到内容驱动。修复后，`执行` 列回到补充上下文角色，`状态 / 下次触发` 重新回到首屏主判断区。
- 信息量保持：服务/作业名称、命令摘要、状态按钮、下次触发、日志与配置入口都保留。减少的是“把空白当成执行列业务宽度”的隐性复杂度，不是减少用户判断依据。
- 概念图/线框图：报告中的 Mermaid 把本轮入口统一收回到 `新增调度/运行状态 -> cluster/runtime 状态表 -> 用户先看 状态 / 下次触发` 这条基础闭环；ASCII 线框图则直接说明 `执行` 列应是补充上下文，不应继续吞掉首屏宽度。
- 报告路径：`C:/Users/kzche/AppData/Local/Temp/codeyun/ui-design-audit/2026-06-30-frontend-design-687b8f9f/report.md`
- 验证：复用本地 `5173/8000` 开发环境，在 in-app Browser 对 `cluster/runtime`、`cluster/codex`、`fanxiu/data-annotation/runtime`、`fanxiu/data-annotation`、`attendance/configs`、`notes/center?tab=star`、`workbook/14?sheet=58855` 完成宽屏 `1600x1000`、普通桌面 `1366x900`、窄屏 `820x1180` 共 21 张截图；`evidence.json` 证明所有样本 `overflowX = 0` 且 `warn/error = 0`。修复后补拍 `cluster-runtime-*-after.png` 与 `cluster-runtime-after.json`，确认三种视口下 `状态 / 下次触发` 已回到首屏。`npm run typecheck --prefix frontend` 与 `npm run build --prefix frontend` 均通过。
- 根因分层：`cluster/runtime` 属于表现层问题，根因是表格宽度模型不正交；其余入口页面本轮未发现需要继续止血的表现层、前端状态投影或后端投影问题。
- 跨自动化交接：无。本轮没有发现需要转交 `CodeYun 代码健康优化` 的后端数据投影或业务建模债务。
- 剩余风险：`cluster/codex` 仍可能因远端设备超时显示局部失败提示，但当前页面已经把失败限定在局部提示里；`note-sheet` 窄屏本质仍是宽表浏览场景，当前继续依赖工作表内部横向滚动，但这不是本轮新增复杂度。
- 处理结果：本轮已完成完整增量范围的提交归类、概念图/线框图、真实多视口截图、低风险修复和前端验证，因此把 `last_audited_commit` 推进到 `687b8f9f37e35bafc16b9f772866bc7d861f4ca2`，保持 `pending_or_skipped_ranges` 为空。

### 2026-06-29

- 完整范围：`6955ecbe0db382f44b732ad1be8ee5edec5e6d11..e7ec5047b38dc4ca3a31336e4e5d5a4f0ceb7dd5`
- 覆盖提交：`e7ec5047b38dc4ca3a31336e4e5d5a4f0ceb7dd5`
- 前端入口提交：`e7ec5047b38dc4ca3a31336e4e5d5a4f0ceb7dd5`
- 入口如何牵引到旧问题：这次提交同时把新增链路接进 `cluster/codex`、`notes/center?tab=list` 和 `NoteSheetWorkspace`。真实页面里真正被牵出来的旧问题只发生在 `cluster/codex`：线程列表已经可读时，首个详情请求仍借用总览 loading 继续遮住整块工作区，把“列表已可判断”和“详情仍在读取”两个事实混成一个全局状态。这个问题和本轮入口同页、同一状态链路，属于典型的旧投影债务被新接线放大。`ListNotes` 与 `NoteSheetWorkspace` 则沿着同一入口复核，确认新的 `_ui` 行投影和显式表头样式都没有再引入额外控件或重复解释。
- 本轮减法：不新增任何按钮、说明、标签或常驻状态，只把 Codex 线程页里错误的全局 loading 概念删掉一层。修复后，总览只负责把线程列表加载出来，线程详情回到自己的 `读取中` 状态，工作负载仍留在局部面板里做补充上下文。
- 信息量保持：线程列表、线程详情、工作负载、远端失败提示、笔记列表摘要和工作表表头语义都仍保留。减少的是“同一页面两个加载阶段共用一个全局蒙层”的重复状态表达，不是减少用户判断依据。
- 概念图/线框图：报告中的 Mermaid 把这三条链路统一收回到 `总览 -> 可读列表 -> 当前详情`、`结果集 -> 列表投影`、`表头事实 -> 表头投影` 三段基础模型；ASCII 线框图则明确对比了修复前后 `cluster/codex` 的 loading 作用域，证明本轮是在删状态概念，不是在加解释。
- 报告路径：`C:/Users/kzche/AppData/Local/Temp/codeyun/ui-design-audit/2026-06-29-frontend-design-e7ec5047/report.md`
- 验证：复用本地 `5173/8000` 开发环境，以真实登录态在独立 Playwright 会话完成 `cluster/codex`、`notes/center?tab=list`、`workbook/14?sheet=58855` 的宽屏 `1600x1000`、普通桌面 `1366x900`、窄屏 `820x1180` 共 9 张截图；`probes.json` 证明 `cluster/codex` 在三种视口下都满足 `threadItems=100` 且 `hasWorkspaceMask=false`，`notes-list` 保持 `50` 行结果摘要，`note-sheet` 表头颜色正确渲染。`npm run typecheck --prefix frontend` 与 `npm run build --prefix frontend` 均通过。
- 根因分层：`cluster/codex` 属于前端状态投影问题，根因是把“总览加载”和“详情加载”混进同一遮罩范围；`ListNotes` 与 `NoteSheetWorkspace` 本轮改动方向都仍然成立，未发现需要继续止血的表现层或模型问题。
- 跨自动化交接：无。本轮没有发现需要转交 `CodeYun 代码健康优化` 的后端数据投影或业务建模债务。
- 剩余风险：`cluster/codex` 的工作负载面板仍可能因远端设备慢或超时而局部 loading，这符合它的次级上下文定位；`note-sheet` 窄屏本质仍是宽表浏览场景，当前依赖横向浏览能力，但这不是本轮新增复杂度。
- 处理结果：本轮已完成完整增量范围的提交归类、业务/交互建模、真实多视口截图、低风险修复和前端验证，因此把 `last_audited_commit` 推进到 `e7ec5047b38dc4ca3a31336e4e5d5a4f0ceb7dd5`，保持 `pending_or_skipped_ranges` 为空。

- 完整范围：`1f786faee97e320efbe06afb5c98fea238151247..6955ecbe0db382f44b732ad1be8ee5edec5e6d11`
- 覆盖提交：`6955ecbe0db382f44b732ad1be8ee5edec5e6d11`
- 前端入口提交：`6955ecbe0db382f44b732ad1be8ee5edec5e6d11`
- 入口如何牵引到旧问题：这次提交同时落在 `SharedNoteEditor`、`notes/eastmoney/trade` 和 `fanxiu/data-annotation` 三条旧链路上，但方向一致，都是把重复表达或双轨事实收回基础模型。`SharedNoteEditor` 继续承载标题/分类/形态/阶段/正文的单层编辑闭环；东财交易页把页面标题、卡头标题和 Markdown H1 的三层重复命名收回成一层；凡修标注页则沿着删除保护这条入口，确认 shape 删除不再依赖 `id + signature + localStorage` 的第二事实源，而是回到“前端临时隐藏 + 后端 asset tree 落盘成功后清理临时键”的更小模型。
- 本轮减法：不新增任何入口、按钮、说明或状态，也没有再改仓库代码。主要确认提交本身已经完成三处减法：1）补齐 `Loading` 图标导入后，`SharedNoteEditor` 不再为一个不存在的组件名制造运行时噪音；2）东财页只保留一层 `股票操作报告` 命名；3）标注页删除保护不再持久化额外 signature/localStorage 事实。
- 信息量保持：笔记编辑页仍保留标题、分类、形态、阶段、进度、自定义属性和正文；东财页仍保留行情图、AI 报告正文和账户约束；凡修标注页仍保留画面、帧树和当前 shape 属性。减少的是重复标题、装配噪音和多余事实源，不是减少用户判断依据。
- 概念图/线框图：报告中的 Mermaid 将三条链路分别收敛为 `节点事实 -> 分类/形态/阶段 -> 继续编辑`、`行情 -> 唯一报告标题 -> 账户约束 -> 卖出或等待`、`画面与帧树 -> 当前 shape 属性 -> 后端 asset tree`，证明本轮入口都在做概念减法而不是新增控件。
- 报告路径：`C:/Users/kzche/AppData/Local/Temp/codeyun/ui-design-audit/2026-06-29-frontend-design-6955ecbe/report.md`
- 验证：复用本地 `5173/8000` 开发环境，在 in-app Browser 打开 `notes/center?tab=star`、`notes/eastmoney/trade`、`fanxiu/data-annotation`，完成宽屏 `1600x1000`、普通桌面 `1366x900`、窄屏 `820x1180` 共 9 张截图；`evidence.json` 证明三页都无横向溢出，东财页 `.trade-report-markdown h1 = 0`，笔记编辑页控制台 `warn/error = 0`。本轮未改仓库代码，因此未运行 `npm run typecheck --prefix frontend` / `npm run build --prefix frontend`。
- 根因分层：`SharedNoteEditor` 属于前端运行时装配问题，已在提交内修复；`notes/eastmoney/trade` 属于表现层重复标题问题，已在提交内修复；`fanxiu/data-annotation` 属于前端状态投影收敛问题，已在提交内通过移除持久化双轨事实完成收敛。
- 跨自动化交接：无。本轮没有发现需要转交 `CodeYun 代码健康优化` 的后端数据投影或业务建模债务。
- 剩余风险：`fanxiu/data-annotation` 本轮只复核了真实入口和事实源收敛方向，未额外构造删除失败回滚实验；`notes/center` 宽屏首屏仍以上部年视图摘要为主，编辑器需要滚动到下方，这属于既有大结构问题，不在本轮入口上继续扩散。
- 处理结果：本轮已完成完整增量范围的提交归类、业务/交互建模、真实多视口截图和页面复核，且未发现新增待修 UI 问题，因此把 `last_audited_commit` 推进到 `6955ecbe0db382f44b732ad1be8ee5edec5e6d11`，保持 `pending_or_skipped_ranges` 为空。

- 完整范围：`497863ad1070dd5000a867e53fb37459070d83ae..1f786faee97e320efbe06afb5c98fea238151247`
- 覆盖提交：`1f786faee97e320efbe06afb5c98fea238151247`
- 前端入口提交：`1f786faee97e320efbe06afb5c98fea238151247`
- 入口如何牵引到旧问题：这次提交把前端入口同时落在 `SharedNoteEditor` 的笔记分类语义、`notes/eastmoney/trade` 的 AI 报告首屏以及 `fanxiu/data-annotation` 的 shape 锁定/asset tree 细节。真实页面里，真正被入口继续牵出的旧问题只有两个：1）东财交易页把外层卡头 `股票操作报告` 和 Markdown 正文首个 `# 股票操作报告` 叠成双标题；2）刚被本次提交改过的 `SharedNoteEditor` 在 `notes/center?tab=list` 仍持续打出 `Failed to resolve component: Loading` 告警。`fanxiu/data-annotation` 则沿着同一标注链路复核，确认新增 `锁定` 仍留在 shape 属性层，没有继续膨胀一级工具条。
- 本轮减法：不新增任何入口、说明或状态。自动修复只做两件事：1）东财页只保留一层“股票操作报告”命名，去掉正文重复 H1；2）补齐 `SharedNoteEditor` 的 `Loading` 图标导入，清理运行时噪音。凡修标注链路只复核不改，避免把没有问题的工具条再重排一次。
- 信息量保持：东财页仍保留行情图、AI 报告正文、账户约束和持仓摘要；笔记编辑页仍保留标题、分类、形态、阶段、自动保存和详情编辑；凡修标注页仍保留设备、画面、帧树、shape 属性和锁定开关。减少的是重复标题和无价值告警，不是减少用户判断依据。
- 概念图/线框图：报告里的 Mermaid 已把东财页拆成“报告卡标题（容器层）”和“AI 报告正文（事实层）”，证明双标题不承载新增语义；笔记编辑链路则保持 `标题 / 分类 / 形态 / 阶段 -> 继续编辑并自动保存` 的单层闭环。
- 报告路径：`C:/Users/kzche/AppData/Local/Temp/codeyun/ui-design-audit/2026-06-29-frontend-design-1f786fae/report.md`
- 验证：复用本地 `5173/8000` 开发环境，在 in-app Browser 打开 `notes/eastmoney/trade`、`notes/center?tab=list`、`fanxiu/data-annotation`，完成宽屏 `1600x1000`、普通桌面 `1366x900`、窄屏 `820x1180` 共 9 张基础截图，`eastmoney-evidence.json`、`notes-center-evidence.json`、`fanxiu-data-annotation-evidence.json` 均证明 `bodyOverflowX/mainOverflowX = 0`。修复后运行 `npm run typecheck --prefix frontend`、`npm run build --prefix frontend` 均通过；`notes-center-evidence-after.json` 证明 fresh reload 后 `Loading` 告警为 `0`，`eastmoney-evidence-after.json` 证明报告正文首个 `h1` 已消失、卡头标题仍保留。另一个复验噪音是 fresh tab 下东财相关接口曾出现 `500`，因此 after 截图回到占位态，但不影响“重复标题已被移除”的结构判定。
- 根因分层：`notes/eastmoney/trade` 属于表现层问题；`SharedNoteEditor` 属于前端运行时装配问题；`fanxiu/data-annotation` 本轮无新增问题。
- 跨自动化交接：无。本轮问题都可在前端本地小步收口，不需要新增 `CodeYun 代码健康优化` 交接。
- 剩余风险：当前工作树仍含用户未提交改动，尤其 `backend/api/notes.py`、`backend/models.py`、若干测试文件和 `docs/Codex日记分类标准.md`；本轮未回退它们。东财页 fresh tab 里的接口 `500` 说明当前环境还有独立后端噪音，后续若要继续巡检该页，需优先区分“结构问题”与“服务侧瞬时失败”。
- 处理结果：本轮已完成完整增量范围的提交归类、概念图/线框图、真实多视口截图、低风险修复和前端验证，因此把 `last_audited_commit` 推进到 `1f786faee97e320efbe06afb5c98fea238151247`，保持 `pending_or_skipped_ranges` 为空。

- 完整范围：`3c939480007d67a362da7589626873d3dd9e9eac..497863ad1070dd5000a867e53fb37459070d83ae`
- 覆盖提交：`497863ad1070dd5000a867e53fb37459070d83ae`
- 前端入口提交：`497863ad1070dd5000a867e53fb37459070d83ae`
- 入口如何牵引到旧问题：这次提交主要是凡修 `data_annotation` 调度与运行保护，但前端实际落点集中在三页：`fanxiu/wiki` 邮件状态、`notes/center?tab=calendar` 年/月备注加载，以及 `notes/pdfs` 列表页阅读位置投影。`pdf` 和 `calendar` 都沿着既有基础模型继续收敛，没有新增概念；真正被入口牵出的旧问题是 `fanxiu/wiki?tab=mail`：提交把 `claimed/deleted/missing_from_list` 从旧的 `可领` 投影里拆出为 `已处理`，减少了状态歧义，但一级列表仍把 `已处理` 画成与 `锁定 / 留存 / 可领` 完全同构的可点击主按钮，于是“处理结果事实”和“可编辑目标”又被揉回同一控件。
- 本轮减法：本轮没有改源码，避免在语义未定时做错误止血。通过概念建模明确减法方向应该是“把已处理从可编辑主按钮里退出来”，而不是再新增一套解释文案、提示条或额外状态。`CalendarNotes` 的非月视图按需加载和 `PDF 阅读器` 的 `阅读位置` 单一投影继续保持了 UI 概念收敛，没有回退。
- 信息量保持：三条链路的业务能力都保留。`fanxiu/wiki` 仍完整展示 `锁定 / 留存 / 可领 / 已处理` 的真实分布、邮件内容和附件；`CalendarNotes` 仍保留后端筛选、前端筛选、月/年/卷/纪切换和年标题；`PDF 阅读器` 仍保留搜索、筛选、导入、权限与阅读位置。当前没有减少判断依据，只是识别出 `fanxiu/wiki` 里哪个控件仍在把两个层级的事实混写。
- 概念图/线框图：报告中的 Mermaid 已把 `邮件状态事实` 与 `可编辑目标` 拆成两层，证明 `已处理` 应位于事实层，而不应继续并列于可编辑三态按钮。
- 报告路径：`C:/Users/kzche/AppData/Local/Temp/codeyun/ui-design-audit/2026-06-29-frontend-design-497863ad/report.md`
- 验证：复用本地 `5173/8000` 开发环境，在 in-app Browser 打开 `notes/pdfs`、`notes/center?tab=calendar`、`fanxiu/wiki?tab=mail`，完成宽屏 `1600x1000`、普通桌面 `1366x900`、窄屏 `820x1180` 共 9 张截图；`evidence.json` 证明 9 个样本的 `bodyOverflowX/mainOverflowX` 均为 `0`。其中 `CalendarNotes` 年视图正常显示 `2026年 / CodeYun`，`PDF 阅读器` 三视口表头均保持 `阅读位置`，`fanxiu/wiki` 三视口稳定复现 `status-processed` 主按钮且 `disabled=false`。
- 根因分层：`pdf` 无新问题；`CalendarNotes` 属于前端加载时机收敛；`fanxiu/wiki` 属于前端状态投影问题，但是否允许“已处理”回改需要产品语义判断，因此本轮不自动修。
- 跨自动化交接：已新增 `docs/CodeYun自动化协作交接.md` 条目 `UI-HANDOFF-20260629-001`，状态为 `needs-human-decision`。
- 剩余风险：当前工作树含用户未提交改动，尤其 `backend/api/eastmoney.py`、`backend/core/fanxiu/data_annotation/tasks/daily_foundation.py`、`frontend/src/api/eastmoney.ts`、`frontend/src/standard/notes/eastmoney/trade/page.vue` 已修改但不在本轮提交范围内；本轮截图基于当前工作树复用环境，但受影响页面与这些未提交改动无直接链路。另一个风险是 `fanxiu/wiki` 的最终收口方式取决于业务语义，在决策前不应贸然把按钮改禁用或改成静态文案。
- 处理结果：本轮已完成完整增量范围的提交归类、概念图/线框图、真实多视口截图与报告沉淀；没有发现无需语义判断的低风险自动修复项，因此不改前端源码，直接把 `last_audited_commit` 推进到 `497863ad1070dd5000a867e53fb37459070d83ae`，保持 `pending_or_skipped_ranges` 为空。

- 完整范围：`4fbe1fbf95c8ddd85e9a2f71d362d8db241fb7bd..3c939480007d67a362da7589626873d3dd9e9eac`
- 覆盖提交：`3c939480007d67a362da7589626873d3dd9e9eac`
- 前端入口提交：`3c939480007d67a362da7589626873d3dd9e9eac`
- 入口如何牵引到旧问题：这次提交同时触发了 `notes/pdfs` 新入口、`pdf/:pdfId` 阅读页控制条、`fanxiu/data-annotation` / `runtime` / `cluster/services` 运行链路、`notes/center?tab=calendar` 色板渲染，以及 `attendance/orders` 的退款历史懒加载。真正牵出需要自动修复的旧问题的是新建的 `PDF 阅读器` 列表页：同一列表页把 `my_state.current_page` 渲染成 `第 N 页`，却把列标题写成“上次阅读”，同时标题下又额外挂出一条文档总数摘要。它们都不属于新的业务能力，而是同一页面里被新入口直接放大的错误投影和重复事实。
- 本轮减法：不新增任何按钮、卡片、说明或状态。自动修复只做三处收敛：1）删除标题下重复的文档总数摘要；2）把“上次阅读”改回真实字段语义“阅读位置”；3）当 `current_page = 1` 时也明确显示 `第 1 页`，不再误投影成 `-`。其余前端改动如 PDF 阅读页随机页/缩放、凡修 runtime 轮询稳态、退款历史按需加载，都保留原能力，不再额外膨胀一级概念。
- 信息量保持：PDF 列表页仍完整保留搜索、筛选、导入、打开文档、权限判断、阅读位置、大小和更新时间；PDF 阅读页仍保留目录、页面、笔记、信息和缩放；凡修标注 / 行为树 / 服务管理、星图日历、订单页的判断与操作能力也都保持不变。减少的是错误命名和重复汇总，不是减少用户决策依据。
- 报告路径：`C:/Users/kzche/AppData/Local/Temp/codeyun/ui-design-audit/2026-06-29-frontend-design-3c939480/report.md`
- 验证：复用本地 `5173/8000` 开发环境，并在 in-app Browser 对 `notes/pdfs`、`pdf/58429`、`fanxiu/data-annotation?window=mumu&entry_id=30b82d72-8a76-4a74-be4b-4fc1591c6ce2`、`fanxiu/data-annotation/runtime`、`cluster/services?entry_id=30b82d72-8a76-4a74-be4b-4fc1591c6ce2`、`notes/center?tab=calendar`、`attendance/orders` 完成宽屏 `1600x1000`、普通桌面 `1366x900`、窄屏 `820x1180` 截图。`evidence.json` 中 21 组基础截图的 `bodyOverflowX/mainOverflowX` 全为 `0`；修复后又补拍 `pdf-library-*-after.png`，`pdf-library-after-evidence.json` 证明文档总数摘要已移除、列表列标题已统一为“阅读位置”。同时 `npm run typecheck --prefix frontend`、`npm run build --prefix frontend` 通过。
- 根因分层：本轮自动修复的问题属于前端状态投影和信息层级，而不是后端 DTO 缺字段。`fanxiu/data-annotation` 的后端事实源回收、`runtime` / `cluster/services` 的轮询稳态和 `orders` 的懒加载主要是行为与性能侧收敛，真实页面未再暴露需要升级为后端投影或业务建模交接的新 UI 债务。
- 剩余风险：当前工作树包含用户未提交改动，尤其有多处凡修运行时后端文件和 `frontend/src/standard/fanxiu/wiki/page.vue` 处于修改状态；本轮截图与 PDF 列表页修复均基于当前工作树而非纯净 commit checkout。另一个已知现象是 `notes/pdfs` 在 `820px` 窄屏下仍通过表格内部横向滚动承载字段，这是当前信息密度下的明确取舍，不构成新的结构回归。
- 处理结果：本轮已完成完整增量范围的提交归类、业务建模、概念图/线框图、真实多视口截图、低风险修复与前端验证，因此把 `last_audited_commit` 推进到 `3c939480007d67a362da7589626873d3dd9e9eac`，保持 `pending_or_skipped_ranges` 为空。

### 2026-06-28

- 完整范围：`358177a3394d280730a3abc19d3f1d5272cfc0f1..4fbe1fbf95c8ddd85e9a2f71d362d8db241fb7bd`
- 覆盖提交：`4fbe1fbf95c8ddd85e9a2f71d362d8db241fb7bd`
- 前端入口提交：`4fbe1fbf95c8ddd85e9a2f71d362d8db241fb7bd`
- 入口如何牵引到旧问题：这次提交同时触碰了 `orders`、`cluster/storage`、`cluster/runtime`、`fanxiu/data-annotation`、`fanxiu/wiki` 和 `notes/center`。真正牵出需要自动修复的旧问题，是 `notes/center` 这条“后端工作集 -> 前端规则链 -> 列表 / 星系双视图”的同一状态模型：提交已经让 `StarNotes` 只在前端筛选依赖 `custom_fields.* / full_text` 时补拉自定义字段，但 `ListNotes` 仍把 `include_custom_fields` 固定成 `false`，导致同一套规则程序在两个视图里出现不同结果。这不是无关扩散，而是同一业务对象、同一筛选模型下被本轮入口直接暴露出来的旧分叉。
- 本轮减法：不新增任何按钮、卡片、说明或状态。提交自身已经在多处做减法：`orders` 把 `Handsontable` 改为按需加载，`cluster/storage` 复用根目录预取避免重复取数，`cluster/runtime` 先热身缓存减少空窗，`fanxiu/data-annotation` 继续把“连拍结果入口”明确压成“缓存”，`fanxiu/wiki` 只解析当前可见详情里的链接目标。本轮自动修复继续沿同一个方向，把 `ListNotes` 的字段装载语义收回到和 `StarNotes` 相同的基础模型，减少“同一规则链不同页各自解释”的偶然复杂度。
- 信息量保持：列表页仍保留后端筛选、前端筛选、分页、批量编辑和节点详情；星系页仍保留相同的规则链和图节点工作台。减少的是字段装载语义的分叉，不是减少筛选能力或编辑能力。`orders`、`storage`、`runtime`、`annotation`、`wiki` 也都保持原有判断和操作能力，只让重依赖、重复入口或无关细节更后置。
- 报告路径：`C:/Users/kzche/AppData/Local/Temp/codeyun/ui-design-audit/2026-06-28-frontend-design-4fbe1fbf/report.md`
- 验证：复用本地 `5173/8000` 开发环境；`attendance/orders`、`cluster/runtime`、`fanxiu/data-annotation`、`fanxiu/wiki`、`notes/center` 列表/星系，以及 `cluster/storage` settled 状态都完成宽屏 `1600x1000`、普通桌面 `1366x900`、窄屏 `820x1180` 截图。`evidence.json` 中 21 组基础截图 `overflowX` 全为 `0`；额外补拍了 `cluster-storage-*-settled.png`、`fanxiu-wiki-wide-settled.png` 和真实 `notes-star-*-real.png`。同时 `npm run typecheck --prefix frontend`、`npm run build --prefix frontend` 通过。
- 根因分层：`ListNotes` 的问题属于前端状态投影，不是样式 bug，也不是后端 DTO 缺字段。`cluster/storage` 首屏 6 秒左右才从预取态落到目录表，更像真实目录扫描耗时，不构成新的 UI 结构回归。`fanxiu/data-annotation` 与 `fanxiu/wiki` 本轮没有再暴露需要升级为后端数据投影或业务建模交接的新债务。
- 剩余风险：当前工作树包含用户未提交改动，其中前端有 `frontend/src/standard/fanxiu/data-annotation-runtime/page.vue`；本轮截图基于当前工作树而不是纯净 commit checkout。`ListNotes` 的修复已经通过真实页面 smoke、构建和代码路径复核，但没有额外构造专门命中 `custom_fields.*` 的演示截图样本。
- 处理结果：本轮已完成完整增量范围的提交归类、概念图/线框图、真实多视口截图、低风险修复与前端验证，因此把 `last_audited_commit` 推进到 `4fbe1fbf95c8ddd85e9a2f71d362d8db241fb7bd`，保持 `pending_or_skipped_ranges` 为空。

### 2026-06-26

- 完整范围：`ccae0b2174254caefce956d0ed3fdd5a8c56144b..46c34d1d9e6147e349a065fc3b6b218e4cd5cb9e`
- 覆盖提交：`46c34d1d9e6147e349a065fc3b6b218e4cd5cb9e`
- 前端入口提交：`46c34d1d9e6147e349a065fc3b6b218e4cd5cb9e`
- 入口如何牵引到旧问题：提交把凡修 Runtime / Scheduler / 默认任务和 Codex workload 摘要一起收口，前端入口因此落在三条同链路闭环上：`fanxiu/data-annotation` 的 Layer 视图是否能在更简单的树模型里直接暴露当前帧，`fanxiu/data-annotation/runtime` 是否继续维持“运行状态事实优先”的首屏层级，以及 `notes/center?tab=calendar` 是否把 Codex 工作量继续压回稳定的日级状态投影。`attendance/orders` 只做退款历史并行加载，无需扩散成新的页面重构。
- 本轮减法：不新增任何按钮、卡片、说明或状态。提交自身已经完成三处有效减法：1）`fanxiu/data-annotation` 的 Layer 视图继续收敛到“当前选中帧的祖先链路”，减少手动展开树；2）`dailyTaskPresets.ts` 删除一批旧日常 preset，减少候选噪声；3）`CalendarNotes` 用后端 `day_seconds` 摘要和 60 秒冷却稳定后台刷新，不把刷新过程抬成一级 UI 概念。
- 信息量保持：凡修标注页仍保留设备/窗口/通道/画面参数、树视图、连拍、行为树跳转和标注工作台；行为树页仍保留运行内核、守护、作业、巡检阻塞和日志入口；星图日历仍保留后端筛选、前端筛选、月格工作量小时数和节点内容；订单页仍保留查询输入与退款历史。减少的是重复展开动作、冗余候选和隐性的后台刷新噪声，不是减少判断与操作能力。
- 报告路径：`C:/Users/kzche/AppData/Local/Temp/codeyun/ui-design-audit/2026-06-26-frontend-design-46c34d1d/report.md`
- 验证：复用本地 `5173/8000` 开发环境；`fanxiu/data-annotation`（Layer 视图）、`fanxiu/data-annotation/runtime`、`notes/center?tab=calendar`、`attendance/orders` 都完成宽屏 `1600x1000`、普通桌面 `1366x900`、窄屏 `820x1180` 截图；`overflowX` 全部为 `0`，控制台未采到页面级 `error/pageerror`。证据见 `evidence.json` 与 `fanxiu-annotation-layer-evidence.json`。
- 根因分层：本轮入口没有暴露新的样式 bug。`fanxiu/data-annotation` 和 `CalendarNotes` 的变化都属于前端状态投影收敛，其中日历还消费了新的后端日汇总投影；`attendance/orders` 属于加载节奏优化。未发现需要升级为后端数据投影或业务建模交接的新债务。
- 剩余风险：当前工作树包含用户未提交改动，其中 `frontend/src/standard/attendance/orders/page.vue` 另有窄屏历史列表调整；本轮截图基于当前工作树而不是纯净 commit checkout。订单页窄屏证据主要覆盖首屏与历史区入口，没有继续下滚到所有退款历史卡片，但这不影响对本轮并行加载提交的无异常判定。
- 处理结果：本轮已完成完整增量范围的提交归类、概念图/线框图、真实多视口截图与文档沉淀；没有发现需要自动修复的低风险 UI 回归，因此不改前端源码，直接把 `last_audited_commit` 推进到 `46c34d1d9e6147e349a065fc3b6b218e4cd5cb9e`，保持 `pending_or_skipped_ranges` 为空。

- 完整范围：`30ba2484654687cc6bb9582a3cce8312a68e2c2f..ccae0b2174254caefce956d0ed3fdd5a8c56144b`
- 覆盖提交：`ccae0b2174254caefce956d0ed3fdd5a8c56144b`
- 前端入口提交：`ccae0b2174254caefce956d0ed3fdd5a8c56144b`
- 入口如何牵引到旧问题：提交把 `fanxiu/data-annotation` 的帧树模型从业务目录/场景身份迁到 `Layer 1/2/3` 投影，并同步重写 runtime / scheduler 执行链路；同一条“当前帧识别证据 -> 行为树运行状态 -> 缺标补标”闭环因此必须一起复看。notes / storage / codex / orders 相关前端改动主要是 API 契约同步，只做无异常抽查，不扩散成独立页面重构。
- 本轮减法：不新增任何入口或说明，只让 Layer 视图在恢复状态、切换视图和聚焦图片时自动展开当前帧所在链路，删除“先手动展开 Layer 根目录再找当前帧”的额外操作。
- 信息量保持：标注页仍保留设备/窗口/通道/画面参数、Layer 选择、树、shape 标注与运行页跳转；行为树页仍保留运行内核、框架、调度器、守护、作业、日志和巡检阻断事实。减少的是新 Layer 模型下被隐藏的当前选择，不减少判断与操作能力。
- 报告路径：`C:/Users/kzche/AppData/Local/Temp/codeyun/ui-design-audit/2026-06-26-frontend-design-ccae0b21/report.md`
- 验证：复用本地 `5173/8000` 开发环境；`fanxiu/data-annotation` 与 `fanxiu/data-annotation/runtime` 完成宽屏 `1600x1000`、普通桌面 `1366x900`、窄屏 `820x1180` 截图，拼图见 `annotation-collage.png` 与 `runtime-collage.png`；布局探针确认两页都没有页面级横向溢出，窄屏滚动由局部容器承接；`npm run typecheck --prefix frontend`、`npm run build --prefix frontend` 通过。
- 根因分层：本轮修复的问题属于前端状态投影，不是样式 bug。Layer 树已经收回为更简单的基础模型，但展开状态仍沿用旧目录视图心智，导致“当前选中帧”没有被投影到首屏。行为树页未发现需要交接的后端投影或业务建模债务。
- 剩余风险：当前工作树仍有用户未提交的后端调度/preset 改动，本轮只修改 `frontend/src/standard/fanxiu/data-annotation/page.vue`；标注页窄屏截图优先展示当前画面，没有继续滚动到下方 shape inspector；行为树页当前样本未同时覆盖“打开补标”按钮可见态，但本轮未改该区域交互。
- 处理结果：本轮已完成完整增量范围的提交归类、概念图/线框图、真实多视口截图、低风险修复与前端验证，因此把 `last_audited_commit` 推进到 `ccae0b2174254caefce956d0ed3fdd5a8c56144b`，保持 `pending_or_skipped_ranges` 为空。

### 2026-06-25（补审关闭）

- 完整范围：`2f6baf8b44212a4d492e7b97b7f537d015dc2a0e..30ba2484654687cc6bb9582a3cce8312a68e2c2f`
- 覆盖提交：`30ba2484654687cc6bb9582a3cce8312a68e2c2f`
- 前端入口提交：`30ba2484654687cc6bb9582a3cce8312a68e2c2f`
- 入口如何牵引到旧问题：本轮没有新增提交，而是关闭上轮同一范围的 pending。上轮缺口只剩 `fanxiu/data-annotation` 真实权限页面；本轮用本地 admin token 补齐三视口实图，验证同一条“帧树身份事实 -> 场景/目录投影 -> 继续标注”的用户决策闭环。
- 本轮减法：本轮不新增源码修复、不新增 UI 概念。上一轮已完成的日历月视图 `刷新中` 标签删除继续保留；Fanxiu 截图确认 `目录 / 场景` 仍归属于同一个帧树投影，没有拆出额外常驻说明或状态区。
- 信息量保持：Fanxiu 仍展示设备、窗口、通道、画面参数、行为树入口、画面空态、帧树目录/场景投影和连拍入口；减少的是上轮日历自动刷新时的瞬时状态标签，不减少标注能力。
- 报告路径：`C:/Users/kzche/AppData/Local/Temp/codeyun/ui-design-audit/2026-06-25-frontend-design-30ba2484-closeout/report.md`
- 验证：复用本地 `5173/8000` 开发环境；`fanxiu/data-annotation` 用 admin token 完成宽屏 `1600x1000`、普通桌面 `1366x900`、窄屏 `820x1180` 截图；控制台 error/warn 为 0，DOM 横向溢出检测为 0；`npm run typecheck --prefix frontend`、`npm run build --prefix frontend` 通过。
- 根因分层：Fanxiu pending 属于验证权限阻塞，不是新的 UI 模型债务；日历问题仍归类为已修复的前端状态投影；sync-conflict 文件仍是源码同步污染风险，不新增 `docs/CodeYun自动化协作交接.md` 条目。
- 剩余风险：当前截图反映有未提交改动的工作树运行态，不是纯净 `30ba2484` checkout；Fanxiu 未连接真实设备，因此没有覆盖选中真实截图帧后的 shape inspector 细节；大量 `*.sync-conflict-*` 文件仍未清理。
- 处理结果：本轮补齐上轮缺失的 Fanxiu 有权限实图并完成构建验证，因此完整关闭 `2f6baf8b..30ba2484`，把 `last_audited_commit` 推进到 `30ba2484654687cc6bb9582a3cce8312a68e2c2f`，清空 `pending_or_skipped_ranges`。

### 2026-06-25

- 完整范围：`2f6baf8b44212a4d492e7b97b7f537d015dc2a0e..30ba2484654687cc6bb9582a3cce8312a68e2c2f`
- 覆盖提交：`30ba2484654687cc6bb9582a3cce8312a68e2c2f`
- 前端入口提交：`30ba2484654687cc6bb9582a3cce8312a68e2c2f`
- 入口如何牵引到旧问题：提交同时推进 Mystia 目录分页/排序/详情投影、凡修 data-annotation 帧树场景身份显示、星图日历月视图 loading 投影，并把大量 `*.sync-conflict-*` 副本带入源码树。巡检边界因此收敛在“目录候选表 -> 选中详情”“帧树身份事实”“日历后台加载状态是否打断首屏”三条链路；冲突副本只作为源码污染风险记录，不作为 UI 页面重构对象扩散。
- 本轮减法：删除 `CalendarNotes.vue` 月视图加载时的 `刷新中` 标签，让自动刷新回到静默后台状态；保留日期加载范围、Codex 统计错误和重试入口。Mystia 保持“数据集切换 + 搜索 + 表格 + 分页 + 可调详情”的基础模型，未恢复统计横条。凡修提交方向上把独立场景徽标收回到帧标题样式，但未能完成有权限实图复验。
- 信息量保持：日历仍展示后端加载日期范围、前端筛选程序、日历节点和错误重试；Mystia 仍展示菜品/食材/饮品/客人/地点/图片/音频目录、分页、排序、详情图片和长文本；减少的是日历刷新时的瞬时状态标签。
- 报告路径：`C:/Users/kzche/AppData/Local/Temp/codeyun/ui-design-audit/2026-06-25-frontend-design-30ba2484/report.md`
- 验证：复用并重启本地 `5173/8000` 开发环境；Mystia 菜品、Mystia 稀客、星图日历均完成宽屏 `1600x1000`、普通桌面 `1366x900`、窄屏 `820x1180` 截图；`npm run typecheck --prefix frontend`、`npm run build --prefix frontend` 通过。Mystia DOM 横向溢出检测来自表格内容宽于视口且被 `.wiki-list` 滚动容器承接，未判为遮挡。
- 根因分层：日历问题属于前端状态投影，已修；Mystia 未发现新表现层问题；凡修 data-annotation 因权限阻塞无法完成表现层截图；大量 sync-conflict 文件属于源码/同步流程污染，不新增 `docs/CodeYun自动化协作交接.md` 条目。
- 剩余风险：普通临时审计账号访问 `fanxiu/data-annotation` 落到 403，未取得真实工作台三视口截图；提交新增的 `*.sync-conflict-*` 文件和 `components.d.ts` 冲突组件声明未导致构建失败但仍污染源码树；当前工作区有多处非本轮未提交改动，本轮只修改了 `CalendarNotes.vue` 一个标签。
- 处理结果：本轮已做低风险修复但未完整关闭增量范围；按规则不推进 `last_audited_commit`，在 `pending_or_skipped_ranges` 保留 `2f6baf8b..30ba2484`，待补凡修有权限实图后再关闭。

### 2026-06-24

- 完整范围：`e18149f483ba7f1f859a772d7fe3b12ceec2f45d..2f6baf8b44212a4d492e7b97b7f537d015dc2a0e`
- 覆盖提交：`a43ff89b0f87f38b98f42350e49b81d89f5a6e09`、`2f6baf8b44212a4d492e7b97b7f537d015dc2a0e`
- 前端入口提交：`a43ff89b0f87f38b98f42350e49b81d89f5a6e09`、`2f6baf8b44212a4d492e7b97b7f537d015dc2a0e`
- 入口如何牵引到旧问题：本轮先由凡修 data-annotation 场景身份迁移牵引到“帧资产 -> shape 证据 -> 场景识别 -> 运行治理结果”闭环，随后 `HEAD` 新增 Mystia 标准目录入口并继续联动 data-annotation 扩展场景层级。巡检边界因此覆盖 `fanxiu/data-annotation`、`fanxiu/data-annotation/runtime`、`mystia/wiki`，并抽查同轮顺带改动的图鉴邮件、星图列表和存储维护懒加载入口。
- 本轮减法：凡修链路把旧的 shape 级“场景标识范围”收回到帧级身份层级，shape inspector 只保留与图像/OCR 同构的 `关/必/定` 证据角色；扩展层级继续复用同一徽标体系。Mystia 新页面使用“数据集切换 + 搜索 + 主表 + 详情”四类基础概念，不把图片、音频和字段解释平铺成多个常驻面板。
- 信息量保持：帧树、场景树、非场景帧、局部/全局/扩展场景层级、shape 身份证据、场景跳转、运行页守护/作业状态、Mystia 菜品/食材/饮品/角色/素材目录、菜单入口和权限路径都保留；减少的是同一场景身份事实在 shape 级控件里的重复表达。
- 报告路径：`C:/Users/kzche/AppData/Local/Temp/codeyun/ui-design-audit/2026-06-24-frontend-design-2f6baf8b/report.md`
- 验证：复用本地 `5173/8000` 开发环境，用本地 admin 登录后打开 `fanxiu/data-annotation`、`fanxiu/data-annotation/runtime`、`fanxiu/wiki?tab=mail`、`notes/center?tab=list`、`admin/images`、`mystia/wiki`，补齐宽屏 `1600x1000`、普通桌面 `1366x900`、窄屏 `820x1180`；其中 data-annotation 额外切到“场景”树投影，共 21 张真实截图。浏览器控制台 error/warn 为空；`npm run typecheck --prefix frontend`、`npm run build --prefix frontend` 通过。
- 根因分层：本轮前端变化属于前端状态投影收敛和新增静态目录页；未发现新增表现层问题、后端数据投影问题或业务建模债务，不新增 `docs/CodeYun自动化协作交接.md` 条目。
- 剩余风险：当前工作区仍有未提交改动，截图反映当前工作树运行态而不是纯净 `2f6baf8b` checkout；runtime 页面仍有旧长文案表格压力，但不是本轮新增 UI 模型问题；Mystia 页面只验证默认菜品 tab，未逐一截图全部数据集 tab；凡修未人工构造扩展层级大于 2 的专门样本。
- 处理结果：本轮完整处理 `e18149f4..2f6baf8b`，两个前端相关提交均无需自动修复，验证通过，因此把 `last_audited_commit` 推进到 `2f6baf8b44212a4d492e7b97b7f537d015dc2a0e`。
- 完整范围：`266187a479ac3f07ec3ac2bf36fa16bc6d4e98a4..e18149f483ba7f1f859a772d7fe3b12ceec2f45d`
- 覆盖提交：`e18149f483ba7f1f859a772d7fe3b12ceec2f45d`
- 前端入口提交：`e18149f483ba7f1f859a772d7fe3b12ceec2f45d`
- 入口如何牵引到旧问题：本次提交主题是凡修数据标注邮件与洞察链路补强，同时触及 `fanxiu/wiki` 的邮件、玩家面板投影，以及 `NoteSheetWorkspace` 初始动作状态刷新。巡检边界因此收敛在“采集/运行结果如何稳定进入首屏状态表”这条链路：邮件状态应作为筛选事实而不是多层解释，玩家攻击单位应作为分布事实而不是伪命令，sheet 动作状态刷新不应打断表格首屏。
- 本轮减法：提交本身删除玩家面板攻击单位筛选偏好和切换命令，把攻击单位退回只读分布标签；邮件页大小回到默认状态，不再持久化额外分页偏好；`NoteSheetWorkspace` 将初始动作状态刷新延后，避免表格内容恢复阶段同步触发多个状态查询。未新增页面、菜单、权限入口或常驻解释区。
- 信息量保持：凡修邮件状态分布、邮件清单、附件、玩家面板排序、攻击单位分布、星云表格内容和 sheet 动作状态刷新能力都保留；减少的是分布事实被当作筛选命令、页大小长期偏好和首屏同步刷新造成的额外状态概念。
- 报告路径：`C:/Users/kzche/AppData/Local/Temp/codeyun/ui-design-audit/2026-06-24-frontend-design-e18149f4/report.md`
- 验证：复用本地 `5173/8000` 开发环境，用 admin JWT 打开 `fanxiu/wiki?tab=mail`、`fanxiu/wiki?tab=player_profile`、`notes/sheets`、`notes/sheets/8`，补齐宽屏 `1600x1000`、普通桌面 `1366x900`、窄屏 `820x1180` 共 12 张真实截图；页面无控制台错误、无相关 API 错误，非表格主体无不可接受溢出；`npm run typecheck --prefix frontend`、`npm run build --prefix frontend` 通过。
- 根因分层：本轮前端变化属于前端状态投影收敛；未发现新的表现层问题、后端数据投影问题或业务建模债务，不新增 `docs/CodeYun自动化协作交接.md` 条目。
- 剩余风险：`NoteSheetWorkspace` 只在普通 sheet `#8 操作明细` 上完成截图；未构造考勤脚本、用户匹配、打卡链接检测三个动作状态同时存在的 sheet。当前工作区已有未提交前端改动，截图反映当前页面状态而非纯 `HEAD`。
- 处理结果：本轮完整处理 `266187a4..e18149f4`，有前端相关提交但无需自动修复，验证通过，因此把 `last_audited_commit` 推进到 `e18149f483ba7f1f859a772d7fe3b12ceec2f45d`。

### 2026-06-23

- 完整范围：`c1a7cc906ac1c3baafc702dffdd0a4dfbe0c404a..266187a479ac3f07ec3ac2bf36fa16bc6d4e98a4`
- 覆盖提交：`266187a479ac3f07ec3ac2bf36fa16bc6d4e98a4`
- 前端入口提交：`266187a479ac3f07ec3ac2bf36fa16bc6d4e98a4`
- 入口如何牵引到旧问题：本次提交虽然主题是凡修 data-annotation 运行监控与巡检能力补强，但同时触及 `featureAccessStore`、`main.ts`、`notes/center`、`cluster/runtime` 和 `notes/task-system`。巡检边界因此收敛在“首屏状态是否稳定投影”这条链路：权限上下文不应制造菜单/路由闪烁，星图笔记 query 应只表达当前 tab，运行管理设备选择应作为唯一状态作用域，任务空间健康条继续区分只读事实和可跳转任务。
- 本轮减法：未新增任何 UI 控件；提交本身用 60 秒权限上下文缓存、首次 `ensureLoaded`、route watcher、设备选择变更判定和健康条文本/链接分层减少重复加载、重复状态和错误动作暗示。
- 信息量保持：菜单权限、路由守卫、星图笔记 tab 直达、运行管理设备状态加载、任务空间健康告警和任务跳转能力都保留；减少的是首屏等待、竞态导致的重复拉取，以及只读告警被当作按钮的视觉歧义。
- 报告路径：`C:/Users/kzche/AppData/Local/Temp/codeyun/ui-design-audit/2026-06-23-frontend-design-266187a4/report.md`
- 验证：复用本地 `5173/8000` 开发环境，在 `notes/task-system`、`cluster/runtime`、`notes/center?tab=calendar`、`notes/center?tab=list` 各补齐宽屏 `1600x1000`、普通桌面 `1366x900`、窄屏 `820x1180` 共 12 张有效截图；`/notes?tab=...` 作为错误入口探针进入 403，确认规范入口是 `/notes/center?tab=...`；`npm run typecheck --prefix frontend`、`npm run build --prefix frontend` 通过。
- 根因分层：本轮前端变化属于前端状态投影稳定性收敛；未发现新的表现层溢出、后端数据投影问题或业务建模债务，不新增 `docs/CodeYun自动化协作交接.md` 条目。
- 剩余风险：`cluster/runtime` 当前仍主要覆盖本机已有设备样本，没有强制构造设备列表为空或 token 失效的错误态；本轮没有源码修复，因此风险仅记录在报告中。
- 处理结果：本轮完整处理 `c1a7cc90..266187a4`，有前端相关提交但无需自动修复，因此把 `last_audited_commit` 推进到 `266187a479ac3f07ec3ac2bf36fa16bc6d4e98a4`。

- 完整范围：`bf505b478a6237364bd598c2c2e0359b1c5c472c..19a720628aad19a07a61eb117125a96af4600c35`
- 覆盖提交：`d656704fa2d9f6c97e52fa5eeda3a6ff7c4b28eb`、`bd4d49f1a43940b0bc42892cb53f5e3fbb0e4596`、`62c00f967cf8ced2c7a23badca5aa113a5addf0c`、`0b8cd38cf4d76ba44faced3b53a0e7c4a0947075`、`19a720628aad19a07a61eb117125a96af4600c35`
- 前端入口提交：`d656704fa2d9f6c97e52fa5eeda3a6ff7c4b28eb`、`bd4d49f1a43940b0bc42892cb53f5e3fbb0e4596`、`0b8cd38cf4d76ba44faced3b53a0e7c4a0947075`、`19a720628aad19a07a61eb117125a96af4600c35`
- 入口如何牵引到旧问题：新增 `task-system` 页面把“任务空间健康 -> 当前该处理哪条任务”的一级闭环正式抬到前台，live DOM 立刻暴露出同页旧问题：内部自动化断言被原样当作禁用按钮投到首页，技术细节和可执行问题混在一级健康条里。`fanxiu runtime` 同轮提交又继续暴露出 `下次触发` 列混入解释型文案，说明同一批提交仍在牵引“状态投影是否回到基础模型”这条链路。
- 本轮减法：仅收敛 `frontend/src/standard/notes/task-system/page.vue` 的健康条投影，不新增入口、不新增状态，只把已知自动化断言压成短标签 `自动化提示词未同步`，并把非任务跳转型问题从禁用按钮改回静态标签，删除错误 affordance。
- 信息量保持：自动化健康细节仍保留在 `title`，任务跳转型问题仍保留按钮能力；减少的是技术断言直铺首屏和“不可点击却长得像按钮”的冗余概念。
- 报告路径：`C:/Users/kzche/AppData/Local/Temp/codeyun/ui-design-audit/2026-06-23-frontend-design-audit-closeout/report.md`
- 验证：`npm run typecheck --prefix frontend`、`npm run build --prefix frontend` 通过；4 个相关入口已补齐宽屏 `1600x1000`、普通桌面 `1366x900`、窄屏 `820x1180` 共 12 张真实页面截图，见 `C:/Users/kzche/AppData/Local/Temp/codeyun/ui-design-audit/2026-06-23-frontend-design-audit-closeout/`；其中 `task-system` 三视口实图都已显示 `自动化提示词未同步`。
- 根因分层：`task-system` 首页问题属于前端状态投影；`fanxiu runtime` 的 `动态作业未记录下次时间` 属于后端数据投影 / 业务建模边界，已登记到 `docs/CodeYun自动化协作交接.md` 的 `UI-HANDOFF-20260623-001`。
- 剩余风险：`cluster/runtime` 当前截图只覆盖空表首屏，没有新的服务 / 作业行样本；`fanxiu runtime` 的深层动态作业行没有在当前首屏截图中形成新的反证，因此后端状态投影 handoff 继续保留。
- 处理结果：本轮补齐了上轮缺失的真实三视口截图并完成复验，因此完整关闭这段增量范围，把 `last_audited_commit` 推进到 `19a720628aad19a07a61eb117125a96af4600c35`。

### 2026-06-22

- 完整范围：`5b2845af0f6fd2cf647bc8847ed84f1469d6a7e8..bf505b478a6237364bd598c2c2e0359b1c5c472c`
- 覆盖提交：`bf505b478a6237364bd598c2c2e0359b1c5c472c`
- 前端入口提交：`bf505b478a6237364bd598c2c2e0359b1c5c472c`
- 入口如何牵引到旧问题：提交继续落在 `cluster/tasks` 同一条“运行状态/legacy fallback 如何把失败事实投影到当前设备首屏”链路上。commit 本身已经开始用版本号限制旧请求写回，但同页旧问题仍残留：`isCurrentDeviceRequest` 在请求发起时就被固化，快速切换设备时，旧请求回包后仍可能把顶部黄条重新写到当前设备首屏。
- 本轮减法：不新增任何 UI，只把全局黄条和 `deviceError` 的写回门槛继续收紧为“当前设备 + 该设备最新请求”；同时把 `fetchLegacyTasks` 的 404 fallback 也纳入同一门槛，避免同一失败事实跨设备重复误投影。
- 信息量保持：设备缓存、服务/作业列表、legacy fallback、资源监控与错误诊断能力都不变；减少的是顶部黄条对“非当前设备旧请求”的错误表达，不是减少失败提示能力。
- 报告路径：`C:/Users/kzche/AppData/Local/Temp/codeyun/ui-design-audit/2026-06-22-runtime-current-device-guard/report.md`
- 验证：`npm run typecheck --prefix frontend`、`npm run build --prefix frontend` 通过；真实页面 `http://127.0.0.1:5173/cluster/runtime` 已复用现有开发环境做 `codepc_mf` / `codepc_mi15` 快速切换，并补齐宽屏 / 普通桌面 / 窄屏截图，见 `wide-settled.png`、`desktop-settled.png`、`narrow-settled.png` 与 `settled-state.json`；三种视口下都没有再采到 `runtimeLoadIssue` 顶部黄条。
- 剩余风险：当前两台设备都没有可见服务/作业行，本轮截图验证的是“快速切换后不会被旧请求误点亮黄条”，尚未在非空运行列表上再次采到同链路真实样本；但本次改动只收紧写回门槛，不改变表格渲染和后端投影，风险较低。
- 处理结果：本轮已完成完整增量范围的提交归类、真实多视口截图、低风险修复和构建验证，因此把 `last_audited_commit` 推进到 `bf505b478a6237364bd598c2c2e0359b1c5c472c`。

- 完整范围：`dabc99e917ef09ce1145de2caee5c6c25204ab25..5b2845af0f6fd2cf647bc8847ed84f1469d6a7e8`
- 覆盖提交：`5b2845af0f6fd2cf647bc8847ed84f1469d6a7e8`
- 前端入口提交：`5b2845af0f6fd2cf647bc8847ed84f1469d6a7e8`
- 入口如何牵引到旧问题：这次提交继续落在 `cluster/tasks` 同一条“运行状态失败事实投影”链路上，虽然 commit 本身只是把资源监控错误文案再压短一点，但真实复验立刻暴露出同页旧问题：轮询超时仍会把顶部一级告警重新点亮，即使服务/作业表已经有真实数据，首屏也会出现“有数据却像空表解释”的自相矛盾投影。
- 本轮减法：不再让旧请求覆盖当前设备的顶层状态；`fetchTasks` 只允许“当前设备 + 最新请求”写回 `runtimeLoadIssue` / `deviceError`，并在已有可见运行数据时抑制“空表解释型”黄条。
- 信息量保持：首屏无数据且读取失败时，顶部唯一告警仍保留；减少的是成功态与失败态混写造成的重复误导，不是失败诊断能力。
- 报告路径：`C:/Users/kzche/AppData/Local/Temp/codeyun/ui-design-audit/2026-06-22-runtime-stale-alert-closeout/report.md`
- 验证：`npm run typecheck --prefix frontend`、`npm run build --prefix frontend` 通过；真实页面采样与截图见 `C:/Users/kzche/AppData/Local/Temp/codeyun/ui-design-audit/2026-06-22-runtime-debug-trace/`，其中 `trace-1366.png`、`trace-1100.png`、`trace-800.png` 已证明在宽屏 / 普通桌面 / 窄屏下，页面有可见运行数据时不再出现误导性顶部黄条；`events.json` 仍记录到 `/runtime/status` 的偶发 10 秒超时与后续成功返回交替出现。
- 处理结果：本轮已完成完整增量范围的提交归类、真实多视口截图、低风险修复和构建验证，因此把 `last_audited_commit` 推进到 `5b2845af0f6fd2cf647bc8847ed84f1469d6a7e8`。

- 完整范围：`3ab3f32ec3f5966a4fa048bbd0b4d601272f07c0..dabc99e917ef09ce1145de2caee5c6c25204ab25`
- 覆盖提交：`edd1f72b5400ee12b4ca75528962595c74cffc70`、`01ecede6fd987ce7a264d86db855477626dc45f4`、`7424259bbafc27d9196a22b6f2e42027100c75fd`、`070dc063ae4db806f6ca7c9d2b308f5a8c80d250`、`4693ea7094712b1ad89bf8bfe91590bcf559193d`、`b0a3e41632cfb390a684850f23909630642e1b39`、`eaba3ff803e542b99763f4fc926b54cec5d5ffb7`、`f89750a12a632625d9ecb646180f156cf0ae973c`、`dabc99e917ef09ce1145de2caee5c6c25204ab25`
- 前端入口提交：`edd1f72b5400ee12b4ca75528962595c74cffc70`、`dabc99e917ef09ce1145de2caee5c6c25204ab25`
- 入口如何牵引到旧问题：入口仍然是 `cluster/tasks` 的同一失败事实投影。`dabc99e` 把运行状态读取失败提升成一级告警后，之前“空表重复长句”的问题已被牵出；这次真实失败态截图又继续暴露出同链路旧问题：资源监控区仍把 transport 错误正文直接铺在一级页面，和顶部告警一起重复放大同一次远端失败。
- 本轮减法：继续保留 `page.vue` 的“顶部唯一告警 + 两张空表只回到基础暂无数据”收敛；同时把 `RuntimeSystemMetricsChart` 的底层英文错误折叠为一句 `资源监控暂不可用`，细节只留 hover title。
- 信息量保持：运行状态失败仍有唯一一级告警；资源监控仍区分“暂无采样”和“暂不可用”，只是把诊断细节后置，不再打断主闭环。
- 报告路径：`C:/Users/kzche/AppData/Local/Temp/codeyun/ui-design-audit/2026-06-22-runtime-load-alert-evidence/report.md`
- 验证：`npm run typecheck --prefix frontend`、`npm run build --prefix frontend` 通过；真实页面截图已补齐 `code4101` 账号下 `codepc_mf` / `codepc_mi15` 的有数据首屏，以及临时审计账号 `ui_audit_1782073812` 下坏入口的宽屏 / 普通桌面 / 窄屏失败态首屏。失败态里顶部告警只出现一次，资源监控失败文案已收敛为一句短提示。
- 处理结果：本轮已完成完整增量范围的提交归类、真实多视口截图、低风险修复和构建验证，因此把 `last_audited_commit` 推进到 `dabc99e917ef09ce1145de2caee5c6c25204ab25`。

- 完整范围：`3ab3f32ec3f5966a4fa048bbd0b4d601272f07c0..dabc99e917ef09ce1145de2caee5c6c25204ab25`
- 覆盖提交：`edd1f72b5400ee12b4ca75528962595c74cffc70`、`01ecede6fd987ce7a264d86db855477626dc45f4`、`7424259bbafc27d9196a22b6f2e42027100c75fd`、`070dc063ae4db806f6ca7c9d2b308f5a8c80d250`、`4693ea7094712b1ad89bf8bfe91590bcf559193d`、`b0a3e41632cfb390a684850f23909630642e1b39`、`eaba3ff803e542b99763f4fc926b54cec5d5ffb7`、`f89750a12a632625d9ecb646180f156cf0ae973c`、`dabc99e917ef09ce1145de2caee5c6c25204ab25`
- 前端入口提交：`edd1f72b5400ee12b4ca75528962595c74cffc70`、`dabc99e917ef09ce1145de2caee5c6c25204ab25`
- 入口如何牵引到旧问题：`edd1f72` 把 `cluster/tasks` 的首屏重新压回“服务/作业状态表 -> 队列记录 -> 资源监控”，`dabc99e` 又为读取失败补上一级告警；两者叠加后，同页旧问题变成“同一失败事实会不会同时出现在告警、服务空表、作业空表三处”，这正是同一业务对象上的重复投影，而不是无关扩散。
- 本轮减法：删除 `runtimeTableEmptyText`，不再把 `runtimeLoadIssue` 同时塞进服务空表和作业空表；失败态只保留顶部一个告警，空表回到基础 `暂无数据`。
- 信息量保持：`dabc99e` 想表达的“空表不等于远端没有服务或作业”仍保留；减少的是两张表里的重复长句，不是失败解释能力。
- 报告路径：`C:/Users/kzche/AppData/Local/Temp/codeyun/ui-design-audit/2026-06-22-runtime-load-alert-dedupe/report.md`
- 验证：`npm run typecheck --prefix frontend`、`npm run build --prefix frontend` 通过；真实页面用 in-app Browser DOM snapshot 复核了 `codepc_mf` 本地有数据首屏与 `codepc_mi15` 远程空表首屏，但 in-app 截图仍超时，独立 Chrome fallback 因未复用登录态只拿到登录页截图。
- 剩余风险：本轮已经把失败态复杂度继续收敛，但仍缺少真正可交付的失败态多视口截图证据，因此 `last_audited_commit` 继续停在 `3ab3f32e...`，下轮需优先解决截图链路再关闭这段增量范围。

- 完整范围：`3ab3f32ec3f5966a4fa048bbd0b4d601272f07c0..b0a3e41632cfb390a684850f23909630642e1b39`
- 覆盖提交：`edd1f72b5400ee12b4ca75528962595c74cffc70`、`01ecede6fd987ce7a264d86db855477626dc45f4`、`7424259bbafc27d9196a22b6f2e42027100c75fd`、`070dc063ae4db806f6ca7c9d2b308f5a8c80d250`、`4693ea7094712b1ad89bf8bfe91590bcf559193d`、`b0a3e41632cfb390a684850f23909630642e1b39`
- 前端入口提交：`edd1f72b5400ee12b4ca75528962595c74cffc70`
- 入口如何牵引到旧问题：这次提交继续围绕 `cluster/tasks` 的运行状态投影做减法，把资源监控继续下沉到状态表之后，因此仍需检查同页“设备 -> 服务/作业状态表 -> 队列记录 -> 资源诊断”的首屏决策闭环是否真的成立，而不能只看代码顺序。
- 本轮结论：已完成完整增量范围的提交归类与页面结构建模，但真实页面 `fetchRuntimeStatus` 持续 10 秒超时，只拿到了空表首屏；同时 in-app Browser 内建截图多次超时，Chrome 兜底窗口也未形成可交付页面证据，因此本轮不推进 `last_audited_commit`。
- 报告路径：`C:/Users/kzche/AppData/Local/Temp/codeyun/ui-design-audit/2026-06-22-runtime-status-tables/report.md`
- 验证：真实页面已打开并读取 DOM snapshot、console logs；未运行 `npm run typecheck --prefix frontend` / `npm run build --prefix frontend`，因为本轮未做源码修改。
- 剩余风险：当前无法证明“状态表前置”在带服务/作业数据的真实页面里仍然保持信息层级正确；下轮需要先恢复 `runtime/status` 页面取数与可用截图链路，再继续同一增量范围。

- 完整范围：`b4ff1c4ad1bada973381e67048ccd3e16f35e6a5..3ab3f32ec3f5966a4fa048bbd0b4d601272f07c0`
- 覆盖提交：`44d5b98221097982d7617dab8926b8746d4247f0`、`78926ae08dfd965bc6786c74b35c8aea5c5a231e`、`665647d6c192dfe1241d32a6403176811fa72a34`、`9daeb1ceb30b75f6cd1d9d228c66ae3f13b5b485`、`3ab3f32ec3f5966a4fa048bbd0b4d601272f07c0`
- 前端入口提交：`44d5b98221097982d7617dab8926b8746d4247f0`、`78926ae08dfd965bc6786c74b35c8aea5c5a231e`、`9daeb1ceb30b75f6cd1d9d228c66ae3f13b5b485`、`3ab3f32ec3f5966a4fa048bbd0b4d601272f07c0`
- 入口如何牵引到旧问题：这几次提交继续收敛 `cluster/tasks` 的服务/作业投影与 `schedule_status` 展示，随后把同页两个旧壳层暴露出来：设备摘要已经被 tabs 表达，但 tab 下仍残留只装“移除/配置”按钮的空白卡壳；而资源监控仍压在服务/作业状态表之前，打断首屏决策闭环。
- 本轮减法：删除设备 tab 下方无信息增量的空白卡壳，只保留紧凑动作行，并仅在编辑时展开设备配置面板；同时把 `RuntimeSystemMetricsChart` 下沉到服务、作业与队列记录之后，让主闭环回到“先看状态表，再看诊断图表”。
- 信息量保持：设备切换、移除、配置、服务状态、作业状态、日志与资源监控都仍保留；减少的是空容器和错误层级，不是业务能力。
- 报告路径：`C:/Users/kzche/AppData/Local/Temp/codeyun/ui-design-audit/2026-06-22-runtime-page-projection/report.md`
- 验证：`npm run typecheck --prefix frontend`、`npm run build --prefix frontend` 通过；真实页面截图见 `wide-before-settled.png`、`desktop-before-settled.png`、`narrow-before-settled.png` 与 `wide-after.png`、`desktop-after.png`、`narrow-after.png`。
- 剩余风险：作业表长文案在窄屏下仍主要依赖两行截断，本轮没有新增横向滚动；若后续继续增加执行摘要密度，可能需要单独讨论是否继续压缩“执行”列的文案投影。

- 完整范围：`59e70391c5595226e655feab915edd249e752848..b4ff1c4ad1bada973381e67048ccd3e16f35e6a5`
- 前端入口提交：`ff2080865208c300406bd0a67ee8b07cebee9612`、`b4ff1c4ad1bada973381e67048ccd3e16f35e6a5`
- 入口如何牵引到旧问题：这两个提交都落在 `frontend/src/standard/cluster/tasks/page.vue`，把旧命令进一步收敛到“服务 / 作业”两张状态表后，立刻暴露出同页原有的设备信息重复投影，以及窄屏状态列/下次触发列被执行列挤出首屏的问题。
- 本轮减法：删除 tab 下方卡片里的重复设备标题、类型和地址，只保留动作条与编辑表单；同时收窄窄屏下的名称列、执行列和下次触发列，让状态表在不丢字段的前提下回到首屏。
- 信息量保持：设备切换、移除、重连、配置、编辑能力不变；服务/作业仍保留名称、执行命令、状态和下次触发，只减少重复设备事实和横向浪费。
- 报告路径：`C:/Users/kzche/AppData/Local/Temp/codeyun/ui-design-audit/2026-06-22-cluster-tasks/report.md`
- 验证：`npm run typecheck --prefix frontend`、`npm run build --prefix frontend` 通过；真实页面截图见 `wide-after.png`、`desktop-after.png`、`narrow-after2.png`。由于本地没有现成 legacy fallback 样本，本轮使用真实页面 + 精准接口桩验证 `/runtime/status -> 404` 与 `/task/` 旧命令返回的前端投影。
- 剩余风险：资源监控区在无采样时仍然偏高，但与本轮 legacy 命令分类入口关联较弱，暂未继续扩散。

- 完整范围：`2cf9d58a8c2113f20302795b87206bf12556f2a2..59e70391c5595226e655feab915edd249e752848`
- 覆盖提交：`6b3755a1429006ae9d53867dc59e97335c27e015`、`d5d18df6eeccfb26478a09a5f0d0b530c364a354`、`f33e6b21881ea3a12592ffc99a1dfc7af834e86c`、`002d28ad8ba7b440a5c8dcaa7c580d97c9ce7174`、`9da9a52e4faea63bf581109da9fa6d9c23f9dc6f`、`59e70391c5595226e655feab915edd249e752848`
- 判定：无前端相关提交。`6b3755a` 仅更新自动化文档；其余 5 个提交分别落在凡修运行时、文件系统 API、任务调度后端与测试，未改前端页面、菜单、权限、路由，也未改变前端可感知的 API 投影。
- 处理结果：不启动开发环境、不生成新截图、不写新报告；本轮完成“无关提交”判定后，将 `last_audited_commit` 推进到 `59e70391c5595226e655feab915edd249e752848`。

- 完整范围：`c8388494d07ef61e15decc27d26fc4d792252c30..2cf9d58a8c2113f20302795b87206bf12556f2a2`
- 前端入口提交：`5a4e860a8a31e06593d7e6975482340fc2c4fa36`、`41adb4d12392241639f0a797302257f905865ca3`、`2cf9d58a8c2113f20302795b87206bf12556f2a2`
- 入口如何牵引到旧问题：这些提交都落在 `frontend/src/standard/fanxiu/data-annotation-runtime/page.vue`，新状态投影继续暴露出该页把“运行控制”和“守护/作业总控”混在一级区域的旧结构问题。
- 本轮减法：删除运行控制卡片里的守护副入口，删除作业表头里的重复总开关，只保留守护分组标题开关和调度 owner 选择器两处唯一总控。
- 信息量保持：守护启用状态、作业启用状态、调度 owner、日志入口都仍保留；减少的是重复控制，不是业务能力。
- 报告路径：`C:/Users/kzche/AppData/Local/Temp/codeyun/ui-design-audit/2026-06-22-runtime/report.md`
- 验证：`npm run typecheck --prefix frontend`、`npm run build --prefix frontend` 通过；真实页面截图见 `runtime-wide-before.png` 与 `runtime-after.png`。
- 剩余风险：Chrome / in-app Browser 截图链路会把窗口归一为固定渲染宽度，本轮未拿到真正不同宽度的三张实图；但本次修改只删除重复入口，不涉及 CSS 或布局算法，窄屏风险较低。

- 完整范围：`19a720628aad19a07a61eb117125a96af4600c35..c1a7cc906ac1c3baafc702dffdd0a4dfbe0c404a`
- 覆盖提交：`c1a7cc906ac1c3baafc702dffdd0a4dfbe0c404a`
- 前端入口提交：`c1a7cc906ac1c3baafc702dffdd0a4dfbe0c404a`
- 入口如何牵引到旧问题：本次提交继续落在 `notes/task-system` 同一条“任务空间健康 -> 自动化契约异常 -> 当前是否还能继续执行”链路上。commit 本身已经把英文告警压成 `自动化提示词未同步`，但真实页面立刻暴露出同页旧问题：健康条自己已经是容器，内部仍套一个橙色描边 chip，且只读系统告警和可跳转任务问题共用同一外观，首屏会把“状态事实”和“可执行动作”混成一个层级。
- 本轮减法：不新增任何新控件，只把健康条收敛成唯一容器；被动系统告警改回文本级提示，只有带 `taskId` 的问题才保留为可点击文本链接；同时取消 issue 的强制截断，让同一条健康带承载完整事实，而不是“容器里再塞一个伪按钮”。
- 信息量保持：自动化失败事实、任务审计问题、按任务跳转能力和顶部 stale reload 逻辑都保留；减少的是重复边框、错误动作暗示和被截断的重复投影，不是减少诊断能力。
- 报告路径：`C:/Users/kzche/AppData/Local/Temp/codeyun/ui-design-audit/2026-06-23-task-system-c1a7cc90/report.md`
- 验证：复用本地 `5173/8000` 开发环境，以本地 admin token 打开真实页面 `http://127.0.0.1:5173/notes/task-system`，在宽屏 / 普通桌面 / 窄屏三种视口下分别截图 `task-system-after-wide.png`、`task-system-after-desktop.png`、`task-system-after-narrow.png`；`npm run typecheck --prefix frontend`、`npm run build --prefix frontend` 通过。
- 剩余风险：本轮真实数据只复现了自动化只读告警，没有同时采到“可跳转任务问题 + 自动化告警”混合样本；但改动只收敛同一健康条内的投影层级，不改 API、写回和任务逻辑，风险较低。
- 处理结果：本轮已完成完整增量范围的提交归类、概念图/线框图、真实多视口截图、低风险修复和前端验证，因此把 `last_audited_commit` 推进到 `c1a7cc906ac1c3baafc702dffdd0a4dfbe0c404a`。

- 完整范围：`46c34d1d9e6147e349a065fc3b6b218e4cd5cb9e..358177a3394d280730a3abc19d3f1d5272cfc0f1`
- 覆盖提交：`358177a3394d280730a3abc19d3f1d5272cfc0f1`
- 前端入口提交：`358177a3394d280730a3abc19d3f1d5272cfc0f1`
- 入口如何牵引到旧问题：提交把 `fanxiu/data-annotation` 的帧树工具条再增加了一个常驻动作 `场景归纳`，因此同一工具链上原有的旧问题被直接放大了：开始/停止连拍的动作按钮旁边，还有一个只负责打开连拍结果的文字按钮，但两者都叫“连拍”。这不是无关扩散，而是同一页面、同一采集闭环里的重复事实投影。
- 本轮减法：不新增控件，不改交互，只把 `openBurstDialog` 的文字入口从 `连拍` 收回为 `缓存`，并补齐 `title/aria-label=连拍缓存`。主界面仍保留开始/停止连拍的图标按钮，但结果入口回到更基础的对象名，工具条重新分清“动作”和“结果”。
- 信息量保持：用户仍然可以保存单帧、开始/停止连拍、查看连拍缓存、做场景归纳、搜索和管理帧树；减少的是同一行里两个“连拍”并列造成的命名重复，而不是减少采集能力。
- 报告路径：`C:/Users/kzche/AppData/Local/Temp/codeyun/ui-design-audit/2026-06-26-frontend-design-358177a3/report.md`
- 验证：复用本地 `5173/8000` 开发环境，并用 Chrome 现有 `localhost:5173` token 注入临时 Playwright 会话完成已登录实图；`fanxiu/data-annotation`、`fanxiu/data-annotation/runtime`、`cluster/runtime`、`attendance/orders`、`notes/center?tab=star` 均完成宽屏 `1600x1000`、普通桌面 `1366x900`、窄屏 `820x1180` 截图；`overflowX` 全部为 `0`，`evidence.json` 未记录新的 console `error/warn`。同时 `npm run typecheck --prefix frontend` 与 `npm run build --prefix frontend` 通过。
- 剩余风险：`notes/center?tab=star` 的筛选区在窄屏下仍属于既有高密度结构，但和本轮 `StarNotes` relayout 提交只共享入口、不共享本次修复对象；`fanxiu/data-annotation` 窄屏首屏也仍以画面区为主，右侧帧树工具条可见性依赖下滚，这属于旧页面大结构问题，本轮不继续扩散。
- 处理结果：本轮已完成完整增量范围的提交归类、概念图/线框图、真实多视口截图、低风险修复和前端验证，因此把 `last_audited_commit` 推进到 `358177a3394d280730a3abc19d3f1d5272cfc0f1`，保持 `pending_or_skipped_ranges` 为空。

