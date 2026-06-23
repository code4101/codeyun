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
last_audited_commit: "266187a479ac3f07ec3ac2bf36fa16bc6d4e98a4"
last_audited_at: "2026-06-23T16:15:00+08:00"
last_report_path: "C:/Users/kzche/AppData/Local/Temp/codeyun/ui-design-audit/2026-06-23-frontend-design-266187a4/report.md"
last_frontend_commit_summary: "完整关闭 c1a7cc90..266187a4：权限上下文缓存、星图笔记 tab 路由同步、运行管理设备选择启动时序和 task-system 健康条均完成三视口实图巡检，无需自动修复。"
audited_commit_count: 35
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
