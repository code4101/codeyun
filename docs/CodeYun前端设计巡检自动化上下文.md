# CodeYun 前端设计巡检自动化上下文

## 定位

`CodeYun 前端设计巡检` 是跟随 Git 提交的前端 UI 设计自动化。

它不是普通代码瘦身任务，也不是 `UI 自主学习`。它关注的是：近期提交是否改变了 CodeYun 的前端页面、导航、交互、状态展示或信息架构；如果改变了，就以这些变化作为巡检入口，沿相关业务链路分析真实页面和既有交互，必要时做小范围修复或重构。

近期提交只是引子，不是修改边界。自动化可以修旧功能里的系统性 UI 问题，但必须能说明这些旧问题为什么会被本轮入口牵引出来，例如同一页面、同一业务对象、同一状态模型、同一菜单入口、同一表格/筛选/运行控制组件，或同一条用户决策闭环。

本自动化的核心目标永远是让 UI 模型更简单，而不是更复杂。它应使用更清晰、更少、更正交的 UI 元素呈现基本相同的信息量。默认不要新增功能、常驻控件、入口、字段、状态或解释区；优先通过合并、删除重复、收回到基础模型、调整信息层级和重构旧结构来降低复杂度。原本 30 个控件承载的信息，如果可以重构成 10 个控件且不丢失关键判断和操作能力，这是正确方向。

底层审美和设计原则统一沉淀到：

- `D:/home/chenkunze/slns/skills/前端UI规范/SKILL.md`
- `D:/home/chenkunze/slns/skills/设计品味/SKILL.md`
- `D:/home/chenkunze/slns/skills/前端工程架构/SKILL.md`

本自动化文档只记录操作流程、增量记忆和本自动化的验收口径。不要把长期 UI 原则复制到 automation prompt 里。

## 与其他自动化的边界

- `github自动提交`：负责每 2 小时监督自动提交是否成功。它是提交链路，不做 UI 设计审查。
- `CodeYun 代码健康优化`：负责空闲维护、代码瘦身、文档事实对齐和低风险工程整理。它可以发现前端问题，但不承担真实截图和概念设计闭环；当前端巡检判断 UI 症状背后是 API、DTO、数据结构或业务建模债务时，应通过 `docs/CodeYun自动化协作交接.md` 转交它做模型审计或小步重构。
- `UI 自主学习`：从历史会话中提取用户 UI 偏好，生成 skill patch 建议，不直接修页面。
- `CodeYun 前端设计巡检`：跟随新提交，定位前端相关变化，并以此作为入口理解业务和交互；先画概念图/线框图，再对真实页面截图做差距分析，最后修明确、可验证且与入口相关的问题。修复对象可以是旧代码或旧功能。

## 增量记忆

自动化每轮必须先读取本节。

```yaml
last_audited_commit: "6421d1833078e750a83b41939e85f9cac7700594"
last_audited_at: "2026-07-19T01:10:21.8705308+08:00"
last_report_path: "C:/Users/kzche/AppData/Local/Temp/codeyun/ui-design-audit/2026-07-19-frontend-design-6421d183/report.md"
last_frontend_commit_summary: "完整关闭历史改写后的 6c2b6d8c..6421d183 共 91 个当前提交身份；火山公主音频/剧院删除构建与重复统计摘要，剧院窄屏题库收敛为单列，生产入口未被局部 chunk 污染。"
audited_commit_count: 217
pending_or_skipped_ranges: []
```

### 2026-07-19 · `6421d183`

- 完整范围：`6c2b6d8c5b118cb3d720a1cec7ea67e8ff69461a..6421d1833078e750a83b41939e85f9cac7700594`。旧游标不再是当前 HEAD 祖先，Git 返回 91 个提交；本轮仍逐提交读取文件清单并分类为 72 个直接前端提交、6 个 API/可视状态投影提交、13 个非前端提交。当前历史中与旧游标同时间/主题的对应提交是 `3a815cfc`，其后 9 个新增语义提交也全部单独覆盖；详细 hash 分类见报告。
- 入口与模型：火山公主音频使用“搜索/分组/分页列表 → 选中音频 → 播放与必要属性”的上下 inspector；剧院使用“题库/剧目/场景”三种同一资料库视图；凡修数据标注把旧专用识别层级继续收回基础识别候选，凡修图鉴邮件保持搜索、状态过滤、邮件清单与附件投影。
- 入口牵引到旧问题：音频和剧院标题下常驻 Steam Build、Unity 与条目总量，属于数据源元信息/重复统计，不参与用户决策；剧院 `760px` 单列断点未覆盖带侧栏的 `820px` 窄屏，台词双列被压到约 300px 后过度换行。
- 本轮减法：删除两个新页面的构建/统计摘要及对应样式，把剧院单列断点调整为 `900px`；源码净计 2 文件、3 行新增、26 行删除，没有新增控件、字段、状态或入口，基本信息量不变。
- 真实页面：音频、剧院、数据标注、凡修图鉴覆盖 `1600x1000`、`1366x900`、`820x1180`，全部 `body/root` 横向溢出为 0。凡修图鉴首次停在 shell loading，2.5 秒后正常进入邮件清单且控制台无 warn/error。修复后剧院 `820px` 题库为单列。
- 入口依赖污染：`npm run build --prefix frontend` 后，`dist/index.html` 只预载基础 vendor；`main-Doib7KN9.js` 顶层静态 import 未加载火山公主或 file-viewer/PDF/表格/编辑器/图表/worker 等局部 chunk。生产 preview 的剧院入口离开 shell loading，资源只包含基础 vendor、当前 page 与 `volcanoPrincess` API chunk，控制台无 warn/error。
- 验证：`npm run typecheck --prefix frontend`、`npm run build --prefix frontend` 通过。巡检前已有 4 个凡修/Pixiv 未提交文件，本轮未触碰。报告与证据：`C:/Users/kzche/AppData/Local/Temp/codeyun/ui-design-audit/2026-07-19-frontend-design-6421d183/`。

### 2026-07-17 · `6c2b6d8c`

- 完整范围：`dd8678cd279868efd8130d1d8bd811e67aa05a30..6c2b6d8c5b118cb3d720a1cec7ea67e8ff69461a`；共 4 个提交，按从旧到新逐个归类。`d2fb2c27` 直接修改东财计算器前端及其行情 API；`43c67f70`、`1e32bc7d`、`6c2b6d8c` 只涉及维护快照/文档、凡修 Runtime/调度、考勤桥接与测试，没有改变前端页面或可视状态投影。
- 入口与模型：页面继续使用“标的 + 基准价 → 9 个价格点位 + 单一现价标记 → 该标的交易记录”的决策闭环。行情刷新改为读取后端 `fetched_at + ttl_seconds` 安排下一次刷新，并复用一个静默 `setTimeout`；没有新增刷新按钮、loading、状态标签、说明区或持久状态。
- 本轮减法：没有追加源码修复。提交本身把固定 60 秒轮询收回到后端 TTL 这一基础缓存事实，避免前端再维护一套独立刷新规则；异常时仍回到 10 秒重试，页面语义和控件数量保持不变。
- 真实页面：`/notes/eastmoney/calculator` 覆盖 `1600x1000`、`1366x900`、`820x1180`。三个视口的 `body/root` 横向溢出均为 0；窄屏仅 9 点价格轴内部保留 101px 局部横向滚动，交易表无额外横向溢出。页面、菜单、真实行情与交易记录正常，观察一个刷新周期后控制台无 `warn/error`。
- 验证：`npm run typecheck --prefix frontend`、`npm run build --prefix frontend` 通过（Vite 7.3.1，4256 modules，约 1m07s）；`uv run pytest tests/backend/test_eastmoney_calculator.py -q` 通过（4 passed）。本轮没有依赖、Vite/Rollup、全局样式、worker/wasm、解析器/预览器/编辑器/图表库或公开入口改动，不触发强制入口依赖污染检查。
- 现场边界：巡检开始前工作区已有 NoteSheet、凡修日常任务及相关测试的 7 个未提交修改；本轮未覆盖或改写这些用户变更。报告与证据：`C:/Users/kzche/AppData/Local/Temp/codeyun/ui-design-audit/2026-07-17-frontend-design-6c2b6d8c/`。

### 2026-07-16 · `dd8678cd`

- 完整范围：`c580233e4070c23c653955d382eb1f3cb12b7613..dd8678cd279868efd8130d1d8bd811e67aa05a30`；共 5 个提交，按从旧到新逐个归类。`333bee07`、`f144f6f9`、`98302707` 直接修改前端；`8ed34462`、`dd8678cd` 只改变凡修抓包、标注护栏和 Zaohua 工具链后端实现，未改变页面或状态投影。
- 入口与模型：文件空目录只保留“设备/路径/目录”工作区；运行管理明确使用“服务 = 长期进程、作业 = 按规则或一次性触发”的两种基础对象；数据标注把 OCR 候选作为 shape 名称的下划线和 hover 建议，不新增按钮、字段、面板或持久状态。
- 本轮减法：复验 `333bee07` 已把真正空目录的第二块媒体空壳隐藏并取消 640px 强制高度；`98302707` 删除“添加作业 → 自定义命令”平行入口，并将 legacy command 统一归为 service。没有追加源码修复。
- 真实页面：文件空目录、`/cluster/runtime` 的添加作业弹窗、`/fanxiu/data-annotation` 均覆盖 `1600x1000`、`1366x900`、`820x1180`。全部页面 `body/root` 横向溢出为 0；添加作业弹窗不含“自定义命令”；数据标注控制台无 `warn/error`。
- 验证：`npm run typecheck --prefix frontend`、`npm run build --prefix frontend` 通过（Vite 7.3.1，4256 modules，约 1m09s）。本轮未触发强制入口依赖污染检查。
- 剩余风险：当前真实选中帧的 2 个 shape 没有命中 OCR 建议，故只验证了视觉语法、降级和布局，尚缺一张真实命中状态截图；后续同链路提交应补验下划线与场景标识红色、选中蓝色的区分度。报告与证据：`C:/Users/kzche/AppData/Local/Temp/codeyun/ui-design-audit/2026-07-16-frontend-design-dd8678cd/`。

### 2026-07-15 · `c580233e`

- 完整范围：`eca87774c84fb19d3ecfbb9171c552dce10cb03b..c580233e4070c23c653955d382eb1f3cb12b7613`；共 4 个提交，按从旧到新逐个归类。`97ecbc28`、`5dc77e3d`、`80a43a74` 直接修改前端；`c580233e` 虽未改前端文件，但改变笔记分类 palette/default 与 B 类考勤工作簿的可见投影，因此按前端相关处理。
- 入口与模型：Runtime 仍分别展示外部 Scheduler、Kernel 和 Runtime 事实，`外部 Scheduler 在线 · 自动派发开启` 只出现一次；日历详情继续使用“月历 + 同页选中详情”，周行收在现有高度内；笔记类型 selector/manager 共享后端权威 palette，17 个类型继续使用同一“名称 + 颜色 + 描述 + 顺序”实体；修道班 13 期 1 阶考勤表把用户、返款、打卡、周次放在既有 Handsontable 表头层级中。
- 入口牵引到旧问题：`5dc77e3d` 为了让空子目录继续保留目录导航，把 Gallery 工作区扩展到无媒体状态；真实页面因此同时出现“当前目录下没有子目录”和独立的“当前筛选条件下没有可显示的媒体”大空壳，且 `browser-panel` 固定至少 640px。两者表达同一空目录事实，属于本轮文件工作区入口直接放大的旧壳层问题。
- 本轮减法：在真正的空子目录状态下保留唯一目录工作区、设备/路径/上一级/递归检索能力，隐藏无媒体的第二空面板并取消 640px 强制高度；没有新增控件、字段、状态或入口。非空目录复验仍显示 48 个媒体卡片，Gallery 主面板正常。
- 真实页面：Runtime、文件空目录、日历选中笔记详情、`/workbook/16?sheet=60370` 均覆盖 `1600x1000`、`1366x900`、`820x1180`；笔记分类 selector/manager 另有桌面截图。全部目标页面 `body/root` 横向溢出为 0；工作簿横向滚动只归 Handsontable，日历的页面壳未新增滚动层。空目录修复后可见空状态从两块收敛为一块，宽屏卡片高度由原强制壳层收回到 424px。
- 验证：`npm run typecheck --prefix frontend` 通过；`npm run build --prefix frontend` 通过（4256 modules，1m44s）。本轮没有重依赖、Vite/Rollup、自动导入、全局样式、worker/wasm、解析器/预览器/编辑器/图表库或公开匿名入口改动，不触发强制入口依赖污染检查。
- 剩余风险：Runtime 当前真实业务状态存在一次 `go_scene #34` 的 unknown 阻塞，这是现有游戏/标注运行事实，不是本轮 UI 投影回退；本轮只验证页面状态边界与布局，没有执行新的游戏业务 Cell。报告与证据：`C:/Users/kzche/AppData/Local/Temp/codeyun/ui-design-audit/2026-07-15-frontend-design-c580233e/`。

### 2026-07-14 · `eca87774`

- 完整范围：`6b5ff516022894ed440c22b48be041f8cdafeffc..eca87774c84fb19d3ecfbb9171c552dce10cb03b`；共 15 个提交，按从旧到新逐个归类。前端相关为 `8ecef5d4`、`00921bd9`、`1608d81b`、`994ecbba`、`b00f7ab7`、`88e96cff`、`eca87774`；其余 `e41f6933`、`45218a94`、`8415e397`、`f4052c7b`、`d2da5aa9`、`142fab11`、`2fc97379`、`436acc7d` 均未改变前端 UI 模型、路由、菜单或可视状态投影。
- 入口与减法：洞天继续把独立方案页收回“配置 → 求解 → 种植策略 → 可选保存”，无保存数据时不再常驻空侧栏；法宝阵图的智能放置使用“一个计算状态 + 最多三个候选链接 + 原有 8 个槽位”，没有新增平行面板。`eca87774` 将 Runtime 从人工/AI/工程隔离、多层 status、单步 tick 收敛为唯一 Kernel Cell 入口和 AI/工程两种调度来源，前端相关变更为 20 行新增、166 行删除。
- 新计算器：`/notes/eastmoney/calculator` 的核心模型为“标的 + 基准价 → 9 个价格点位 + 现价标记 → 该标的交易记录”；新建只使用一个按需弹窗，无重复汇总条、常驻解释面板或额外详情侧栏。路由、权限和左侧菜单已同步挂载。
- 真实页面：复用本地 `5173/8000` 开发环境和 in-app Browser 已登录会话，覆盖计算器、法宝阵图、Runtime、数据标注和 Runtime 日志五个页面，每页均采集 `1600x1000`、`1366x900`、`820x1180`。全部 15 张证据的 `bodyOverflowX/rootOverflowX/mainOverflowX` 均为 `0`；法宝阵图已真实读取 77 个法宝、5 张阵图和最多 3 个候选方案，上轮 `/403` 阻塞已关闭。
- 验证：`npm run typecheck --prefix frontend` 通过；`npm run build --prefix frontend` 通过（4256 modules，43.47s）。本轮无重依赖、Vite/Rollup、全局样式、worker/wasm 或公开匿名入口变更，不触发强制入口依赖污染检查；补充读取显示 `dist/index.html` 只预载基础 vendor，`main-C54Ja3TG.js` 顶层 import 未直接加载计算器、法宝阵图或 Runtime 页面 chunk。
- 处理结果：未发现明确、低风险且需要自动修复的 UI 回退，未改前端源码，也未新增跨自动化交接。报告与证据：`C:/Users/kzche/AppData/Local/Temp/codeyun/ui-design-audit/2026-07-14-frontend-design-eca87774/`。

### 2026-07-13 · `1608d81b`（未闭环）

- 完整范围：`6b5ff516022894ed440c22b48be041f8cdafeffc..1608d81b7751504afa74fde528a1db8d68ff6a22`；逐提交归类为 `8ecef5d4 frontend_relevant`、`e41f6933 not_frontend_relevant`、`45218a94 not_frontend_relevant`、`00921bd9 frontend_relevant`、`8415e397 not_frontend_relevant`、`f4052c7b not_frontend_relevant`、`1608d81b frontend_relevant`。
- 入口与减法：独立 `pasture-plan` 已回收到洞天的“配置 → 求解 → 种植策略 → 可选保存”模型；真实页面发现无方案时仍常驻空侧栏，宽屏占用 280px、窄屏形成大段空白。本轮让方案侧栏仅在存在保存数据时出现，工作区由固定双列收敛为按数据出现的双列，没有新增 UI 概念。
- 真实页面：洞天和标注页均完成 `1600x1000`、`1366x900`、`820x1180` 截图；修复后洞天三视口 `savedAsideCount=0`、`bodyOverflowX/rootOverflowX=0`，工作区单列宽度为 `1345px / 1111px / 712px`。标注页三视口无横向溢出，爆发截图改动未新增 UI 概念。
- 阻塞：法宝阵图访问被路由到 `/403`，未能验证提交新增的“智能放置状态 + 最多三个候选方案入口”在真实数据下是否过度常驻；因此不推进 `last_audited_commit`，完整范围保留在 `pending_or_skipped_ranges`。
- 验证：`npm run typecheck --prefix frontend`、`npm run build --prefix frontend` 通过；本轮未涉及强制入口依赖污染检查触发项。报告：`C:/Users/kzche/AppData/Local/Temp/codeyun/ui-design-audit/2026-07-13-frontend-design-1608d81b/report.md`。

### 2026-07-12 · `6b5ff516`

- 完整范围：`cbd92e4e6c69611cd5dfe23028adf47fbac712d2..6b5ff516022894ed440c22b48be041f8cdafeffc`；覆盖提交仅 `6b5ff516022894ed440c22b48be041f8cdafeffc`，归类为 `frontend_relevant`。
- 入口与减法：提交联动 `zaohua/alchemy`、`zaohua/herbs`、`zaohua/pasture` 及一个临时牧场方案页。炼丹把可拖拽价值排序程序收回为稳定的品阶降序结果，药材/丹药用归纳表承担跨品阶比较；洞天配置从 11 个常驻数量框收敛为 1 个灵田步进器 + 10 个启用开关，仅允许多份的建筑在启用后显示数量，信息量不减但一级控件显著减少。
- 入口牵引到旧问题：新 `pasture-plan` 专页把固定“聚元丹方案”另起路由、权限节点和常驻链接，和洞天求解结果属于同一对象却形成平行入口；当前主工作树已有未提交改动将它删除并把方案能力收回洞天页，本轮为避免覆盖在途工作不重复修改。
- 真实页面：复用提交形成前已采集的同内容证据，`alchemy`、`herbs`、`pasture` 均覆盖 `1600x1000`、`1366x900`、`820x1180`，`bodyOverflowX/rootOverflowX` 均为 `0`；洞天首屏为 `switchCount=10`、`stepperCount=1`、`已配置 9 / 9 格`。
- 工程验证：干净提交快照 `npm run typecheck --prefix frontend` 通过；补入本机按既有插件契约存在但未跟踪的 `media-sync` 模块后，`npm run build --prefix frontend` 通过。`dist/index.html` 未预载 `file-viewer/handsontable/hyperformula/pdfjs/wangeditor/echarts/elk/worker`，`main-BIDFzFAY.js` 唯一顶层 import 为 `_plugin-vue_export-helper`，本轮新增 Element Plus 样式优化项未污染主入口。
- 报告：`C:/Users/kzche/AppData/Local/Temp/codeyun/ui-design-audit/2026-07-12-frontend-design-6b5ff516/report.md`。

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
- `D:/home/chenkunze/slns/skills/前端工程架构/SKILL.md`
- 本文档

只在需要服务验证时再读取对应服务验证技能。

### 2. 定位变化

用只读 Git 命令理解新增提交：

- 提交列表和摘要
- 改动文件
- 前端页面、组件、路由、菜单、权限、API 调用
- 是否涉及新增页面或子页面
- 是否引入或扩展重依赖、Vite/Rollup 插件、`manualChunks`、全局样式、worker、wasm、文件解析器、预览器、编辑器、图表库等可能污染主入口的前端工程变化

如果新增页面需要左侧导航可见，必须同步检查：

- `frontend/src/standard/**/index.ts`
- `frontend/src/features/access/permissionRegistry.json`
- `frontend/src/layout/MainLayout.vue`

### 2.1 入口依赖污染检查

如果本轮前端相关提交涉及下列任一情况，必须把“入口依赖污染”纳入巡检对象，不能只做视觉截图：

- 新增或升级重依赖：文件预览、PDF、富文本、图表、地图、OCR、音视频、游戏、worker、wasm、压缩包解析、邮件解析等。
- 修改 `frontend/vite.config.ts`、`frontend/package.json`、`frontend/package-lock.json`、Vite/Rollup 插件、`manualChunks`、自动导入、组件注册或全局样式。
- 局部功能组件被主布局、全局 store、公共 util、注册表或页面路由静态 import。
- 公开入口、微信传播入口、匿名访问入口、考勤/表格/反馈页等用户高频入口在本轮相关链路上。

检查顺序：

1. 先按 `前端工程架构` skill 判断依赖边界：局部能力是否只在局部路由、局部组件或用户触发后加载。
2. 运行 `npm run build --prefix frontend` 后检查 `frontend/dist/index.html`，确认无关入口没有预加载局部功能 chunk 的 `modulepreload` 或 `stylesheet`。
3. 检查 `frontend/dist/assets/main-*.js`，确认主入口没有顶层 import 局部功能 chunk 或局部依赖。
4. 如果使用 `manualChunks`，额外确认 Vite preload helper 等公共 helper 没有落入局部功能 chunk，避免主入口为了 helper 反向加载局部 chunk。
5. 对公开 URL 或用户高频 URL，用生产构建或公网实例打开真实入口，确认页面离开 shell loading、控制台没有入口 chunk 执行期错误、资源列表没有加载不相关高风险 chunk。

这类问题属于前端工程架构污染，不是传统 UI 样式问题；但它会直接破坏用户可见入口，因此归入本自动化的前端巡检体系。若只发现污染风险但无法低风险修复，应报告并通知，不得推进 `last_audited_commit` 时隐瞒该风险。

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

### 2026-07-11（第二十七轮）

- 完整范围：`fdd0b7a4dc32b508af4939ea2b88ba4cc4629a05..cbd92e4e6c69611cd5dfe23028adf47fbac712d2`
- 覆盖提交：`cbd92e4e6c69611cd5dfe23028adf47fbac712d2`
- 前端入口提交：`cbd92e4e6c69611cd5dfe23028adf47fbac712d2`
- 入口如何牵引到旧问题：这次提交同时把 `Zaohua` 的药材展示从纯明细表扩到分组矩阵，把 `pasture` 的单一格子输入扩到 `境界 / 生产计划 / 灵田灵池`，并调整 `notes/list` 的查询缓存刷新时机。它们共享的不是同一业务对象，而是同一条减法约束：一级页面只保留当前对象判断和当前工作集事实，不把重复请求、重复过滤或额外摘要层继续堆回首屏。因此本轮沿 `zaohua/alchemy / furnaces / herbs / pasture` 和 `notes/center?tab=list` 这两条旧链路一起复核。
- 本轮减法：没有追加仓库源码修复。真实页面确认 `alchemy / furnaces / herbs` 仍保持 `选择视角 -> 当前对象 -> 单一详情` 的 inspector 主闭环；`pasture` 虽然新增了计划控件，但仍只服务于同一个求解输入模型，没有再长出总览卡或第二条结果链；`notes/list` 则把“缓存已足够新时仍立刻重刷”收掉，二次进入直接恢复已缓存的 50 行工作集。
- 信息量保持：`alchemy` 新增的 `耐药` 仍落在已有表格列和详情字段里；`herbs` 仍保留药材品阶、价格、种植时间、炼丹属性和丹方去向，只是把默认首屏改成归纳矩阵；`pasture` 仍保留格子、建筑与结果布局，只是把输入从“格子数”扩成同一求解模型下的更明确计划参数；`notes/list` 仍保留后端筛选、前端筛选和 50 行结果摘要，减少的是二次进入的重复刷新，不是减少工作集能力。
- 概念图/线框图：报告中的 Mermaid 把本轮收敛成两条基础闭环：`Zaohua 视角选择 -> 归纳/候选 -> 当前对象详情 -> 仅在需要时求解`，以及 `notes/list 同一查询 -> 命中新缓存 -> 直接恢复工作集`；ASCII 线框图直接对比了 `herbs` 从长表扫描收成矩阵归纳，以及 `notes/list` 从“缓存后仍重刷”收回到“缓存即事实”。
- 报告路径：`C:/Users/kzche/AppData/Local/Temp/codeyun/ui-design-audit/2026-07-11-frontend-design-cbd92e4/report.md`
- 验证：为避开主工作树未提交的 [`frontend/src/standard/zaohua/herbs/page.vue`](D:/home/chenkunze/slns/codeyun/frontend/src/standard/zaohua/herbs/page.vue) 试验改动，本轮创建干净 worktree `C:/Users/kzche/AppData/Local/Temp/codeyun/worktrees/codeyun-cbd92e4-5174`，在 `http://127.0.0.1:5174` 复用本机 `8000` 后端取证。`alchemy / furnaces / herbs / pasture` 均完成 `1600x1000`、`1366x900`、`820x1180` 三视口截图；`notes/list` 额外完成三视口两轮进入截图与请求计数。`evidence.json` 记录各页 `bodyOverflowX = 0`、`docOverflowX = 0`、console `warn/error = 0`；`notes/list` 首次进入 `query-program` 请求数分别为宽屏 `2`、普通桌面 `1`、窄屏 `1`，30 秒内第二次进入三视口均为 `0`，证明 `skipImmediateRefresh` 已生效。由于本轮未做源码修改且未命中入口依赖污染检查条件，未运行 `npm run typecheck --prefix frontend` 或 `npm run build --prefix frontend`。
- 入口依赖污染检查：本轮未命中强制入口污染检查条件。提交未改 `frontend/package*.json`、`vite.config.*`、`manualChunks`、自动导入、全局样式、worker/wasm 或公开匿名入口装配边界。
- 根因分层：本轮前端变化属于前端状态投影和信息归纳方式的调整，不是后端 DTO 泄漏或业务对象不正交。未发现需要转交 `CodeYun 代码健康优化` 的模型债务。
- 处理结果：本轮已完成完整增量范围的提交归类、业务建模、概念图、真实多视口截图和缓存行为复核，没有新增 UI 回归或需自动修复的问题，因此把 `last_audited_commit` 推进到 `cbd92e4e6c69611cd5dfe23028adf47fbac712d2`，`pending_or_skipped_ranges` 保持为空。

### 2026-07-11（第二十七轮 follow-up）

- 完整范围：`cbd92e4e6c69611cd5dfe23028adf47fbac712d2..HEAD`（当前为空，不推进巡检游标）
- 关联已关闭范围：`fdd0b7a4dc32b508af4939ea2b88ba4cc4629a05..cbd92e4e6c69611cd5dfe23028adf47fbac712d2`
- 入口如何牵引到旧问题：上一轮虽然已经在干净 worktree 里确认 `pasture` 没有新增总览卡或第二条结果链，但当前主工作树里同链路的 `frontend/src/standard/zaohua/pasture/page.vue` 又把 `building_counts` 扩成“所有建筑常驻数量框”，把“是否启用”和“启用后数量”两层事实重新混在一级首屏。这仍然是 `zaohua/pasture` 同一求解输入模型上的旧问题复发，不是无关扩散。
- 本轮减法：把建筑配置从“11 个常驻数量输入”收回到“1 个灵田数量输入 + 10 个启用开关”；只有建筑已启用且支持多份时才显示数量框，并把默认基线恢复到 `9 / 9` 灵田，避免第一次进入就掉进空计划态。
- 信息量保持：灵田格数、可选建筑、建筑数量、境界切换和求解结果都仍保留；减少的是未启用建筑占据的重复输入，不是减少求解能力。
- 概念图/线框图：follow-up 报告里的 Mermaid 把首屏闭环重新收回到 `选择境界 -> 确认灵田基线 -> 决定是否启用建筑 -> 仅在启用后补数量 -> 查看布局结果`；ASCII 线框图直接对比了右栏从“所有建筑都带 [0]”回到“默认只看开关，启用后再看数量”。
- 报告路径：`C:/Users/kzche/AppData/Local/Temp/codeyun/ui-design-audit/2026-07-11-frontend-design-cbd92e4-partial/report.md`
- 验证：`npm run typecheck --prefix frontend`、`npm run build --prefix frontend` 通过；真实页面 `http://127.0.0.1:4174/zaohua/pasture` 已补齐 `1600x1000`、`1366x900`、`820x1180` 三视口截图，`pasture-verified-evidence.json` 记录 `switchCount = 10`、`stepperCount = 1`、`summaryText = 已配置 9 / 9 格`、`bodyOverflowX = 0`、`rootOverflowX = 0`。
- 入口依赖污染检查：本轮没有新的前端提交命中强制入口污染检查条件；执行 `npm run build --prefix frontend` 仅作为 follow-up 静态验证，不改变上一轮对入口依赖边界的结论。
- 根因分层：本轮问题属于前端状态投影/交互密度问题，不是后端 DTO 泄漏或业务对象重构问题。
- 剩余风险：当前主工作树仍有 `frontend/src/standard/zaohua/alchemy/page.vue`、`frontend/src/standard/zaohua/herbs/page.vue`、`frontend/vite.config.ts` 等同链路未提交改动，因此这些页面的当前截图只作为现场参考，不作为新的纯 commit 关闭证据。
- 处理结果：本轮只完成同链路 follow-up 减法修复与验证；由于 `last_audited_commit..HEAD` 当前为空，`last_audited_commit` 维持 `cbd92e4e6c69611cd5dfe23028adf47fbac712d2`，`pending_or_skipped_ranges` 保持为空。

### 2026-07-11（第二十六轮）

- 完整范围：`bda52f6ee6b30ddac0d4b3616971725611f33a22..fdd0b7a4dc32b508af4939ea2b88ba4cc4629a05`
- 覆盖提交：`ae7fc535fdbedbcf2e29c8505590d2fcabdfd438`、`fdd0b7a4dc32b508af4939ea2b88ba4cc4629a05`
- 前端入口提交：`ae7fc535fdbedbcf2e29c8505590d2fcabdfd438`、`fdd0b7a4dc32b508af4939ea2b88ba4cc4629a05`
- 入口如何牵引到旧问题：这两次提交把 `Zaohua` 的丹药/洞天求解、全局路由动态加载恢复、`resource-view` 公开工作簿权限态和 `attendance/orders` 的后台配置恢复一起往前推。它们共享的不是同一业务实体，而是同一条减法约束：一级页面只呈现当前对象和当前失败事实，不把未参与求解的参数、后台恢复节奏或动态加载故障细节扩成常驻控件。因此本轮沿 `zaohua/alchemy -> zaohua/pasture`、`public sheet/workbook resource`、`attendance/orders` 以及真实 `route load error` 兜底链路一起复核。
- 本轮减法：提交本身已经把 `resource-view` 的 401/403 告警收回到页面单一卡片，并为动态路由失败补上一次自动重载后的单一错误卡。本轮追加的低风险修复只落在 `frontend/src/standard/zaohua/pasture/page.vue`：`building_counts` 是参与求解后的细节，不该在未启用建筑上以 7 个禁用 `el-input-number` 常驻首屏；修复后数量控件只在建筑启用后出现，保持同等求解能力，同时把首屏噪音收回。
- 信息量保持：`alchemy` 仍保留品阶筛选、配方列表、选中详情和炉内求解；`pasture` 仍保留格子数、建筑选择和数量能力，只是把数量从“默认常驻”收回到“启用后出现”；`attendance/orders` 仍保留退款/详情和历史闭环，后台配置刷新没有回长成一级提示；`public workbook 403` 仍只保留登录/重试卡片，`routeLoadRecovery` 仍保留一次自动重载后的显式重试按钮。
- 概念图/线框图：报告中的 Mermaid 把本轮收敛成两条基础闭环：`对象进入求解 -> 只在参与后显示细节参数`，以及 `资源/路由失败 -> 自动重试一次 -> 单一错误卡 + 重试按钮`；ASCII 线框图直接对比了 `pasture` 修复前后右栏从“开关 + 标签 + 7 个禁用 stepper”回到“开关 + 标签，启用后才出现数量”。
- 报告路径：`C:/Users/kzche/AppData/Local/Temp/codeyun/ui-design-audit/2026-07-11-frontend-design-fdd0b7a/report.md`
- 验证：复用本地 `5173/8000` 环境，对 `zaohua/alchemy`、`zaohua/pasture`、`attendance/orders`、公开 `sheet/58855?view=lookup`、公开 `workbook/14?sheet=58855` 完成 `1600x1000`、`1366x900`、`820x1180` 三视口截图，证据见 `evidence.json`。修复前 `pasture` 三视口都有 `7` 个禁用数量控件；修复后 `pasture-after-evidence.json` 记录三视口 `stepperCount = 0`、`bodyOverflowX = 0`。`public workbook 403` 三视口都只剩浏览器原生 `403` 网络错误和页面内单一卡片，没有再出现额外 `console.warn`。另外通过浏览器拦截 `src/standard/zaohua/pasture/page.vue` 两次真实复现了动态路由加载失败，`route-load-error-real-evidence.json` 记录三视口都落到 `页面没有加载成功 / 重新加载` 的单一错误卡，且 `bodyOverflowX = 0`。静态校验 `npm run typecheck --prefix frontend`、`npm run build --prefix frontend` 通过。
- 入口依赖污染检查：本轮未命中强制入口污染检查条件。提交未改 `frontend/package*.json`、`vite.config.*`、`manualChunks`、自动导入、全局样式、worker/wasm 或重依赖装配边界；动态路由恢复只涉及现有懒加载失败兜底，不涉及主入口依赖扩面。
- 根因分层：本轮唯一需要自动修复的问题属于 `Zaohua pasture` 的前端状态投影/交互密度问题，不是后端 DTO 或业务建模问题。`attendance/orders` 的后台配置恢复、`resource-view` 的权限拒绝态和 `routeLoadRecovery` 的失败兜底都保持在正确的一级事实层，没有暴露新的模型债务，也无需转交 `CodeYun 代码健康优化`。
- 处理结果：本轮已完成完整增量范围的提交归类、业务建模、概念图、真实多视口截图、低风险减法修复和前端验证，因此把 `last_audited_commit` 推进到 `fdd0b7a4dc32b508af4939ea2b88ba4cc4629a05`，`pending_or_skipped_ranges` 保持为空。

### 2026-07-10（第二十五轮）

- 完整范围：`a9a7ed5dbad886d482f707f5a0347617cacd941e..bda52f6ee6b30ddac0d4b3616971725611f33a22`
- 覆盖提交：`bda52f6ee6b30ddac0d4b3616971725611f33a22`
- 前端入口提交：`bda52f6ee6b30ddac0d4b3616971725611f33a22`
- 入口如何牵引到旧问题：这次提交继续把 `Zaohua` 的价值评估、公式图和丹炉联动往前推，同时补了 `resource-view` 的公开资源查看链路。巡检边界因此不是只看 `alchemy` 的新增字段，而是沿同一对象链路复核 `游戏工具 -> 造化仙缘 -> 丹药 / 丹炉 / 药材 / 洞天` 的 inspector/规划模型，以及 `sheet lookup / workbook sheet` 的公开查看权限投影是否仍然足够简单。
- 本轮减法：`Zaohua` 四页没有继续长出新的总览卡、摘要条或并列入口，仍保持 `筛选栏 -> 候选表 -> 详情区 -> 后置结果` 的基础模型。追加的低风险修复落在 `resource-view`：预期的 `401/403` 权限态原本已经用居中登录/拒绝态表达事实，但代码还会再打印一层 `console.warn`，等于把同一事实重复投影到页面和日志；本轮把这层重复告警收回，只在非权限类失败时保留告警。
- 信息量保持：`alchemy` 仍保留价值、五行、药材示例、丹炉条件和公式图；`furnaces` / `herbs` / `pasture` 仍保留各自对象事实；`resource-view` 仍完整区分公开成功态与权限拒绝态。减少的不是能力，而是去掉 403 权限态下多余的一层前端失败噪音。
- 概念图/线框图：报告中的 Mermaid 把本轮入口收敛成两条基础闭环：`Zaohua` 继续沿统一 inspector/规划模型展开，`resource-view` 则只保留“资源可访问 / 资源被拒绝”这一个判断分叉；ASCII 线框图直接证明 403 态无需再长一层解释或日志投影。
- 报告路径：`C:/Users/kzche/AppData/Local/Temp/codeyun/ui-design-audit/2026-07-10-frontend-design-bda52f6e/report.md`
- 验证：复用本地 `5173/8000` 环境，完成 `alchemy / furnaces / herbs / pasture / sheet lookup / workbook 403` 共 6 个场景在 `1600x1000`、`1366x900`、`820x1180` 三视口下的 18 张截图，证据写入 `evidence.json`；18/18 页面满足 `bodyScrollWidth == bodyClientWidth`。修复前 `workbook/14?sheet=58855` 三视口都只出现浏览器原生 403 和一层应用 `console.warn`；修复后复拍 `resource-workbook-403-desktop-after-fix.png`，只剩浏览器原生 403，页面仍保持干净的登录/拒绝态。`npm run typecheck --prefix frontend`、`npm run build --prefix frontend` 通过。
- 入口依赖污染检查：本轮未命中强制入口污染检查条件，因为提交没有改 `frontend/package*.json`、`vite.config.*`、`manualChunks`、自动导入、全局样式、worker/wasm、文件解析器、预览器、编辑器、图表库或公开匿名传播入口装配。
- 根因分层：`Zaohua` 本轮未暴露新的表现层或业务建模回退；已修复的问题属于 `resource-view` 的前端状态投影重复。当前本地匿名授权数据只覆盖部分 `sheet`，不覆盖 `workbook`，因此 workbook 成功态无法直接实拍，但匿名可访问的 `sheet/58855?view=lookup` 成功态与 `workbook` 403 拒绝态都已验证。
- 处理结果：本轮已完成完整增量范围的提交归类、概念图、真实多视口截图、低风险修复和前端静态验证；没有剩余未审范围，因此把 `last_audited_commit` 推进到 `bda52f6ee6b30ddac0d4b3616971725611f33a22`，`pending_or_skipped_ranges` 保持为空。

### 2026-07-10（第二十四轮）

- 完整范围：`e1345446b4d144479a004a53ec06641bea5b2e00..a9a7ed5dbad886d482f707f5a0347617cacd941e`
- 覆盖提交：`a9a7ed5dbad886d482f707f5a0347617cacd941e`
- 前端入口提交：`a9a7ed5dbad886d482f707f5a0347617cacd941e`
- 入口如何牵引到旧问题：这次提交把 `造化仙缘` 从原先的 `丹药 / 药材` 两页扩成 `丹药 / 丹炉 / 药材 / 洞天` 四页，并给 `丹药` 详情补进求解器和丹炉尺寸。巡检边界因此不是只看两个新路由文件，而是沿同一菜单入口和同一业务链路检查 `游戏工具 -> 造化仙缘` 下的对象选择、候选表、详情区和规划页是否继续落在更少、更正交的基础模型里。
- 本轮减法：没有追加仓库源码修复。真实页面确认 `丹药 / 丹炉 / 药材` 都继续采用统一的 `筛选栏 -> 候选表 -> 选中详情 -> 后置逆向来源` inspector 模型；`洞天` 也只保留 `格子数与建筑开关 -> 唯一布局结果 -> 建筑列表` 这一条主闭环，没有为新能力再长出总览卡、统计条或重复帮助区。
- 信息量保持：`丹药` 仍保留五行需求、药材示例、炉内规则与求解结果；`丹炉` 保留尺寸、属性与加成；`药材` 保留灵气、炼丹属性与用途；`洞天` 保留格子数、参与建筑与布局结果。减少的不是能力，而是没有为新增页面再造一层额外摘要或并列入口。
- 概念图/线框图：报告中的 Mermaid 把 `造化仙缘` 收敛为三张共享图鉴页加一张规划页；ASCII 线框图直接证明前三页仍是同一 inspector 模型，`洞天` 则是轻量工作台模型，不需要再增加解释层。
- 报告路径：`C:/Users/kzche/AppData/Local/Temp/codeyun/ui-design-audit/2026-07-10-frontend-design-a9a7ed5d/report.md`
- 验证：`alchemy / furnaces / herbs / pasture` 各完成 `1600x1000`、`1366x900`、`820x1180` 三视口实拍，共 12 张截图，证据写入 `evidence.json`；四页都满足 `bodyScrollWidth == bodyClientWidth`，控制台没有新增 `warn/error`。`attendance/configs` 的同提交改动只落在 bootstrap cache 容错路径；匿名访问正确落到 `/login`，复用本机 profile 中可提取 token 时页面仍被权限控制到 `/403`，因此本轮把它记为代码与受限路径复核，而不是新的页面模型焦点。`npm run typecheck --prefix frontend`、`npm run build --prefix frontend` 通过。
- 入口依赖污染检查：本轮未命中强制入口污染检查条件，因为提交没有改 `frontend/package*.json`、`vite.config.ts`、`manualChunks`、全局样式注入链，也没有触达公开匿名传播入口。
- 根因分层：本轮未暴露新的后端数据投影或业务建模债务。`洞天` 宽屏下结果板会留下较多空白，但这是求解结果天然稀疏带来的表现层特征，不是重复 UI 概念堆叠，不做自动修复。
- 处理结果：本轮已完成完整增量范围的提交归类、概念图、真实多视口截图与前端静态校验；没有发现需要自动止血的低风险 UI 回退，因此把 `last_audited_commit` 推进到 `a9a7ed5dbad886d482f707f5a0347617cacd941e`，`pending_or_skipped_ranges` 保持为空。

### 2026-07-10（第二十三轮）

- 完整范围：`67a7fe4c4d0e8b15bc654838c5b5a2f9d9462cdc..e1345446b4d144479a004a53ec06641bea5b2e00`
- 覆盖提交：`d3183fe1ddb2ab57b5556884bce2e6bf33d0dd2c`、`e1345446b4d144479a004a53ec06641bea5b2e00`
- 前端入口提交：`d3183fe1ddb2ab57b5556884bce2e6bf33d0dd2c`、`e1345446b4d144479a004a53ec06641bea5b2e00`
- 入口如何牵引到旧问题：这两次提交把 `Zaohua` 作为一个新游戏工具组接入主导航，并连续补齐 `alchemy/herbs` 两页的前后端数据投影与数值展示。巡检边界因此不是只看两个新页面文件，而是沿同一业务闭环检查 `游戏工具 -> 造化仙缘 -> 丹药/药材` 的菜单挂载、首屏候选表、选中详情和逆向来源后置是否共同落在更简单的基础模型里。
- 本轮减法：没有追加仓库源码修复。真实页面确认 `alchemy` 与 `herbs` 都使用同一个 `筛选栏 -> 候选表 -> 选中详情 -> 逆向来源` inspector 模型，没有额外长出统计卡、说明条或重复入口；左侧菜单中的 `造化仙缘 -> 丹药 / 药材` 挂载也完整。
- 信息量保持：`alchemy` 仍保留丹药名、成丹、品级、价格、作用、五行需求、示例药材和炉内规则；`herbs` 仍保留药材名、品级、炼丹属性、灵气、价格和关联丹方。减少的不是能力，而是避免为新标准页面再造一层总览解释区或并列入口。
- 概念图/线框图：报告中的 Mermaid 把两页统一收回到 `导航选择对象 -> 筛选缩小候选 -> 表格选中当前对象 -> 详情区承接完整事实 -> 逆向来源后置折叠`；ASCII 线框图则直接证明当前结构已经足以承载两类对象，而不需要再加摘要卡或帮助区。
- 报告路径：`C:/Users/kzche/AppData/Local/Temp/codeyun/ui-design-audit/2026-07-10-frontend-design-e1345446/report.md`
- 验证：为避开主工作树上同链路未提交的 `zaohua` 试验改动，本轮先创建干净 worktree `.../worktrees/codeyun-e1345446-142920`，用 `HEAD` 前端在 `http://127.0.0.1:5174` 做实拍；`alchemy` 与 `herbs` 各完成宽屏 `1600x1000`、普通桌面 `1366x900`、窄屏 `820x1180` 共 6 张截图。`evidence.json` 记录 6/6 页面满足 `bodyScrollWidth == bodyClientWidth`，页面级控制台仅有 Vite 连接日志和既有 `<Suspense>` info，没有新增 `warn/error`。由于独立 `8010` 后端未加载到同一份 Zaohua catalog，本轮截图复用了当前本机 `8000` 数据服务；但可见差异仅停留在主工作树后台新增的 `shape` 加法字段，`HEAD` 前端并未消费这些字段，因此不影响本轮页面模型结论。
- 入口依赖污染检查：本轮未命中强制入口污染检查条件，因为提交没有改 `frontend/package*.json`、`vite.config.ts`、`manualChunks`、全局样式、worker/wasm 或公开匿名入口。曾在干净 worktree 尝试 `npm run build --prefix frontend`，但被与本轮 Zaohua 页面无关的既有 `media-sync` 路由依赖缺失挡住，因此不把该构建结果当成本轮页面是否通过的判据。
- 根因分层：当前可见问题主要停留在表现层和信息密度权衡。`alchemy` 在 820px 窄屏下仍依赖表格内横向滚动来承载 9 列对比，但没有外层页面溢出；是否继续压缩首屏字段属于产品判断，不做自动修复。本轮未暴露新的后端数据投影或业务建模债务，也无需转交 `CodeYun 代码健康优化`。
- 处理结果：本轮已完成完整增量范围的提交归类、业务建模、概念图、真实多视口截图与菜单挂载核验；没有发现需要自动止血的低风险 UI 回退，因此把 `last_audited_commit` 推进到 `e1345446b4d144479a004a53ec06641bea5b2e00`，`pending_or_skipped_ranges` 保持为空。

### 2026-07-10（第二十二轮）

- 完整范围：`7a6fa47261f0154c92fa56b6195fbc2757544ba7..67a7fe4c4d0e8b15bc654838c5b5a2f9d9462cdc`
- 覆盖提交：`67a7fe4c4d0e8b15bc654838c5b5a2f9d9462cdc`
- 前端入口提交：`67a7fe4c4d0e8b15bc654838c5b5a2f9d9462cdc`
- 入口如何牵引到旧问题：这次提交虽然同时落在 `fanxiu/data-annotation/runtime`、`notes/center`、`attendance/orders`、`cluster/storage` 与 `ImageGalleryWorkspace`，但共享的是同一条减法方向：一级页面只保留当前判断事实，把缓存时间戳、日志轮询粒度、草稿恢复时机和重复文件前置条件收回到后台补料、当前子页或当前目录，而不是继续长成一级解释区。因此本轮沿 `fanxiu/runtime -> data-annotation`、`notes/star -> calendar`、`attendance/configs -> orders -> storage -> cluster/files` 三条链路复核，而不是只看单一文件 diff。
- 本轮减法：没有追加仓库源码修复。真实页面确认 `fanxiu/runtime` 仍维持“守护 / 作业 / 运行状态 / Cell 日志摘要”四块事实，完整历史没有回流一级页；`StarNotes` 与 `CalendarNotes` 的缓存/后台刷新没有长出提示条；`attendance/orders` 的退款与详情子页继续各自独立；`cluster/storage` 在设备根目录切到 `重复文件` 时稳定退回一句前置条件提示；`cluster/files` 的瀑布流补料继续留在组件内部，没有新增任何常驻说明。
- 信息量保持：`fanxiu/runtime` 仍保留守护启用、作业启用、下次触发、运行状态和 Cell 摘要；`notes` 仍保留时间尺度、筛选程序和节点下钻；`attendance/orders` 仍保留退款与详情两条操作闭环；`cluster/storage` 仍保留目录树和重复文件入口；`cluster/files` 仍保留瀑布流/卡片、排序、目录与搜索能力。减少的是无效前置 pane、跨子页草稿干扰和看起来像“又要刷新一次”的一级噪音，不是能力本身。
- 概念图/线框图：报告中的 Mermaid 把三条链路统一收回到“先选上下文 -> 只看当前事实 -> 需要时再进入日志/详情/分析”的基础模型；ASCII 线框图明确 `storage/duplicates` 只有在进入具体磁盘或目录后才成立，`notes/calendar` 的 workload 与 `star` 的缓存补料则后置到后台。
- 报告路径：`C:/Users/kzche/AppData/Local/Temp/codeyun/ui-design-audit/2026-07-10-frontend-design-67a7fe4c/report.md`
- 验证：复用本地 `5173/8000` 开发环境，完成 `fanxiu/data-annotation/runtime`、`fanxiu/data-annotation`、`notes/center?tab=star`、`notes/center?tab=calendar`、`attendance/configs`、`attendance/orders`（退款/详情）、`cluster/storage`（目录树/重复文件）和 `cluster/files`（瀑布流）共 10 个场景在宽屏 `1600x1000`、普通桌面 `1366x900`、窄屏 `820x1180` 下的 30 张截图。`evidence.json` 记录 30/30 页面满足 `bodyScrollWidth == bodyClientWidth`，页面级 `warn/error` 为 `0`；`cluster/storage` 重复文件页在根目录三视口都稳定显示“请先进入具体磁盘或目录，再分析重复文件。”；`attendance/orders?tab=detail` 首屏未混入退款历史；`cluster/files` 宽屏首屏存在 15 个可见瀑布流媒体卡片。静态校验：`npm run typecheck --prefix frontend`、`npm run build --prefix frontend` 通过。
- 入口依赖污染检查：本轮未命中强制入口污染检查条件，因为提交没有改 `frontend/package*.json`、`vite.config.ts`、`manualChunks`、全局样式、worker/wasm 或公开匿名入口；因此仅记录常规 `npm run build --prefix frontend` 通过结果，不单独展开 `dist/index.html` / `main-*.js` 审计。
- 根因分层：`fanxiu/runtime` 属于前端状态投影与轮询粒度收敛；`notes/star`、`notes/calendar` 属于缓存时间戳与后台补料时机收敛；`attendance/orders`、`cluster/storage` 属于当前子页/目录的前端状态恢复与前置条件投影收敛；`cluster/files` 属于共享组件内部补料策略收敛。本轮未暴露新的后端数据投影或业务建模债务。
- 处理结果：本轮已完成完整增量范围的提交归类、业务建模、概念图、真实多视口截图与前端静态验证；没有发现需要追加自动修复的新 UI 回退，因此把 `last_audited_commit` 推进到 `67a7fe4c4d0e8b15bc654838c5b5a2f9d9462cdc`，`pending_or_skipped_ranges` 保持为空。

### 2026-07-09（第二十一轮）

- 完整范围：`918c1b7602e1d3223167588c0fe4d0830f256114..7a6fa47261f0154c92fa56b6195fbc2757544ba7`
- 覆盖提交：`7a6fa47261f0154c92fa56b6195fbc2757544ba7`
- 前端入口提交：`7a6fa47261f0154c92fa56b6195fbc2757544ba7`
- 入口如何牵引到旧问题：这次提交同时落在 `attendance/configs`、`fanxiu/data-annotation/runtime` 和 `notes/star`，共享同一条减法方向：一级页面只保留当前判断事实，把浏览器级刷新、全量日志历史和冷启动查询回放分别收回到浏览器默认能力、日志页和后台缓存。因此本轮沿同一业务链路复核了 `attendance/orders`、`notes/list` 与 `fanxiu/data-annotation/runtime/logs`，而不是只看 commit 本身的代码片段。
- 本轮减法：没有继续改源码。真实页面复核确认 `attendance/configs` 顶栏仍不存在与 F5 等价的常驻 `刷新`；`fanxiu/runtime` 一级页保持 `内核/守护/作业/巡检/Cell 日志摘要` 结构，完整历史可在实际路由 `/fanxiu/data-annotation/runtime/logs` 查看；`notes/star` 的 query cache 后置没有新增提示条、状态条或说明块，同链路旧页 `notes/list` 仍保持筛选摘要 + 列表事实。
- 信息量保持：`attendance/configs` 仍保留 step1-step6 覆盖、默认设备、订单模式、提醒对象和问卷账号；`fanxiu/runtime` 仍保留守护、作业、巡检状态和 cell 日志，只把全量历史后置到日志页；`notes/star` 仍保留后端筛选、前端筛选、年度摘要和节点入口。
- 概念图/线框图：报告中的 Mermaid 把三条链路统一收回到 `一级页只放当前判断事实 -> 二级页/后台链路承接完整历史与加速机制`；ASCII 线框图则明确 `fanxiu/runtime` 的首屏只保留摘要日志，完整历史退到日志页，`notes/star` 的缓存只留在 session/localStorage。
- 报告路径：`C:/Users/kzche/AppData/Local/Temp/codeyun/ui-design-audit/2026-07-09-frontend-design-7a6fa47/report.md`
- 验证：复用本地 `5173/8000` 开发环境，完成 `attendance/configs`、`attendance/orders`、`fanxiu/data-annotation/runtime`、`fanxiu/data-annotation/runtime/logs`、`notes/center?tab=star`、`notes/center?tab=list` 的宽屏 `1600x1000`、普通桌面 `1366x900`、窄屏 `820x1180` 截图。一级页面 18 份截图均满足 `bodyScrollWidth == bodyClientWidth`、`toastTexts = []`；`attendance/configs` 三视口 `refreshButtons = []`；`notes/star` 三视口保持 `1303 条节点 · 折叠 1195 条`；`notes/list` 三视口保持 `共 261 条 / 当前显示 50 条`；`fanxiu/runtime` 补拍窄屏稳定图后确认守护与作业表正常展开。静态校验：`npm run typecheck --prefix frontend`、`npm run build --prefix frontend` 通过。
- 入口依赖污染检查：本轮未命中强制入口污染检查条件，因为没有改 `frontend/package*.json`、`vite.config.ts`、`manualChunks`、全局样式、worker/wasm 或公开匿名入口。
- 根因分层：`attendance/configs` 属于表现层入口减法继续成立；`fanxiu/runtime` 与 `notes/star` 都属于前端状态/性能投影减法成立，没有暴露新的后端数据投影或业务建模问题。
- 处理结果：本轮已完成完整增量范围的提交归类、业务建模、概念图、真实多视口截图、同链路旧页复核和前端静态验证；没有剩余未审范围，因此把 `last_audited_commit` 推进到 `7a6fa47261f0154c92fa56b6195fbc2757544ba7`，`pending_or_skipped_ranges` 保持为空。

### 2026-07-09（第二十轮）

- 完整范围：`718269828e4c0dc7fca1cdd84403703e69f0f1b5..918c1b7602e1d3223167588c0fe4d0830f256114`
- 覆盖提交：`918c1b7602e1d3223167588c0fe4d0830f256114`
- 前端入口提交：`918c1b7602e1d3223167588c0fe4d0830f256114`
- 入口如何牵引到旧问题：这次提交同时落在 `attendance/configs` 的 bootstrap 收口、`notes/list` 的缓存/静默刷新链路和 `cluster/files` 的根目录壳层控制上。三条入口共享同一条减法方向：一级页面先呈现状态事实和下一步动作，不把后台刷新、缓存策略或尚未进入具体目录前的工作台先抬成常驻结构。因此本轮继续沿同一链路复核 `attendance/orders`、`notes/list` 和 `cluster/files` 的真实页，而不是只看 API diff。
- 本轮减法：`attendance/configs` 在真实页面上仍残留一个与 F5 等价、没有局部重算语义的常驻 `刷新` 按钮；已在 [`frontend/src/standard/attendance/configs/page.vue`](D:/home/chenkunze/slns/codeyun/frontend/src/standard/attendance/configs/page.vue) 删除该入口并去掉未再使用的 `RefreshRight` 导入。`cluster/files` 则验证“设备根目录不提前展开排序/媒体工作台，进入具体目录后再展开”的收口已经成立；`notes/list` 真实页继续保持列表事实稳定，没有把缓存/静默刷新细节投影成一级提示。
- 信息量保持：`attendance/configs` 仍保留 step1-step6 运行位置、默认执行设备、订单模式、提醒对象和问卷账号；`notes/list` 仍保留完整筛选、分页和详情编辑；`cluster/files` 仍保留目录排序与媒体工作台，只是在对象尚未成立时不再提前挂出。
- 概念图/线框图：报告中的 Mermaid 把三条链路统一收回到 `先确认配置/候选/目录对象 -> 再展开保存、编辑或工作台`；ASCII 线框图则直接对比了 `cluster/files` 的根目录轻壳与进入目录后的工作台展开，以及 `attendance/configs` 去掉冗余刷新后的首屏结构。
- 报告路径：`C:/Users/kzche/AppData/Local/Temp/codeyun/ui-design-audit/2026-07-09-frontend-design-918c1b7/report.md`
- 验证：复用本地 `5173/8000` 开发环境。`attendance/configs` 修复后完成宽屏 `1600x1000`、普通桌面 `1366x900`、窄屏 `820x1180` 复拍，`evidence-attendance-postfix.json` 记录 `refreshButtonTexts = []`、`panelCount = 3`、三视口 `bodyScrollWidth == bodyClientWidth`、`toastTexts = []`；`notes/list` 三视口 `rowCount = 50`、`listSummary = 共 261 条 当前显示 50 条`、`toastTexts = []`；`cluster/files` 在 `codepc_mi15` 根目录样本下记录 `pathValue = 设备根目录`、`sortSummaryCardCount = 0`、`mediaToolbarVisible = false`，在 `codepc_mf` 具体目录样本下记录 `pathValue = D:\home\chenkunze\data`、`sortSummaryCardCount = 2`、`mediaToolbarVisible = true`，两种态三视口都满足 `bodyScrollWidth == bodyClientWidth`；同链路旧页 `attendance/orders` 三视口也未见新增噪音。静态校验：`npm run typecheck --prefix frontend`、`npm run build --prefix frontend` 通过。
- 入口依赖污染检查：本轮未命中强制入口污染检查条件，因为没有改 `frontend/package*.json`、`vite.config.ts`、`manualChunks`、全局样式、worker/wasm 或公开匿名入口；构建通过后也未观察到新的入口级错误。
- 根因分层：`attendance/configs` 的问题属于表现层入口冗余；`notes/list` 本轮真实页未继续暴露新的表现层或业务建模问题，入口改动主要属于前端状态投影与缓存链路收口；`cluster/files` 这轮验证结果属于前端信息层级收敛成立。
- 处理结果：本轮已完成完整增量范围的提交归类、业务建模、概念图、真实多视口取证、低风险修复和前端静态验证；没有剩余未审范围，因此把 `last_audited_commit` 推进到 `918c1b7602e1d3223167588c0fe4d0830f256114`，`pending_or_skipped_ranges` 保持为空。

### 2026-07-08（第十九轮）

- 完整范围：`144da8b95f22734ff7238f7b8c77377e7e6a0b47..718269828e4c0dc7fca1cdd84403703e69f0f1b5`
- 覆盖提交：`718269828e4c0dc7fca1cdd84403703e69f0f1b5`
- 前端入口提交：`718269828e4c0dc7fca1cdd84403703e69f0f1b5`
- 入口如何牵引到旧问题：这次提交同时落在 `fanxiu/data-annotation/runtime` 的调度 owner 语义和 `notes/list` 的 query-program 静默刷新链路上。两处入口都在继续把一级页面收回到“状态事实 + 下一步动作”，避免把后台刷新或调度规则解释误升格成主界面噪音，因此需要按同一减法方向复核真实页面，而不是只看文案 diff。
- 本轮减法：`fanxiu/runtime` 保持 owner 只用 `人工 / AI / 工程` 三个基础权威表达，不再把“工程不自动跑”误投影成 AI 已接管执行；`notes/list` 则把 silent refresh 失败继续收回到内部刷新层，真实页面不再出现额外一级 toast。
- 信息量保持：`fanxiu/runtime` 仍保留内核、守护、作业、运行状态与 cell 日志；`notes/list` 仍保留完整工作集、前后端筛选、分页和详情编辑，只减少后台刷新失败对一级页面的噪音投影。
- 概念图/线框图：报告中的 Mermaid 把链路收回到 `7182698 -> fanxiu runtime owner 语义收口 / notes list silent refresh toast 抑制 -> 一级页面只保留状态事实与主动失败`，证明本轮没有新增常驻控件或额外状态条。
- 报告路径：`C:/Users/kzche/AppData/Local/Temp/codeyun/ui-design-audit/2026-07-08-frontend-design-7182698/report.md`
- 验证：复用本地 `5173/8000` 开发环境，对 `notes/center?tab=list` 与 `fanxiu/data-annotation/runtime` 完成宽屏 `1600x1000`、普通桌面 `1366x900`、窄屏 `820x1180` 取证。`notes/list` 三视口 `bodyScrollWidth == bodyClientWidth`、`rowCount = 50`、`toastTexts = []`；`fanxiu/runtime` settled 后无 loading mask、无 console `warn/error`，三视口都能直接看到“内核 -> 守护 -> 作业”主闭环。静态校验：`npm run typecheck --prefix frontend`、`npm run build --prefix frontend` 通过。
- 入口依赖污染检查：本轮未命中强制入口污染检查条件，因为没有改 `frontend/package*.json`、`vite.config.ts`、`manualChunks`、全局样式、worker/wasm 或公开入口。
- 根因分层：两处入口都属于前端状态投影收口；本轮真实页面未继续暴露新的表现层或业务建模问题。
- 处理结果：本轮已完成完整增量范围的提交归类、业务建模、概念图、真实多视口取证与前端静态验证；没有发现需要继续自动修复的新 UI 回退，因此把 `last_audited_commit` 推进到 `718269828e4c0dc7fca1cdd84403703e69f0f1b5`，`pending_or_skipped_ranges` 保持为空。

### 2026-07-07（第十八轮，已关闭）

- 完整范围：`b1eef41fd9b948a83bb5248b4fd0f69bbeaec33f..144da8b95f22734ff7238f7b8c77377e7e6a0b47`
- 覆盖提交：`144da8b95f22734ff7238f7b8c77377e7e6a0b47`
- 前端入口提交：`144da8b95f22734ff7238f7b8c77377e7e6a0b47`
- 入口如何牵引到旧问题：这次提交把 `attendance/configs` 的一级摘要继续删掉，把 `notes/list` 加入 query cache 静默回填，同时重构 `fanxiu/data-annotation/runtime` 的 task cell / scheduler / behavior tree 控制流。三条入口看似分散，但都在把主界面收回到“状态事实 + 下一步动作”，减少重复摘要和过程噪音。
- 本轮减法：`attendance/configs` 延续提交本身的减法，三视口确认重复摘要条已经消失；`notes/list` 则暴露出新回退，缓存命中后的后台静默刷新仍沿用主动执行失败的 toast 语义。已在 [`frontend/src/api/notes.ts`](D:/home/chenkunze/slns/codeyun/frontend/src/api/notes.ts) 和 [`frontend/src/standard/notes/center/ListNotes.vue`](D:/home/chenkunze/slns/codeyun/frontend/src/standard/notes/center/ListNotes.vue) 把 silent refresh 的失败提示收回到内部刷新层，不再把“后台刷新失败”误升格成一级页面错误。
- 信息量保持：`attendance/configs` 仍保留 step1-step6、默认设备、订单对象、订单模式和账号事实；`notes/list` 仍保留完整列表、筛选、分页和详情编辑，只是删除了静默刷新失败对一级页面的误导提示。
- 概念图/线框图：报告中的 Mermaid 把链路收回到 `144da8b -> attendance 摘要条减法 / notes cache hydration / fanxiu runtime 重构 -> silent refresh 失败被错误投影到主界面 -> suppressErrorToast 收回到内部语义`，证明本轮修复是在同一减法方向上继续收口，而不是新增解释控件。
- 报告路径：`C:/Users/kzche/AppData/Local/Temp/codeyun/ui-design-audit/2026-07-07-frontend-design-144da8b/report.md`
- 验证：复用本地 `5173/8000` 开发环境，对 `attendance/configs`、`notes/center?tab=list`、`fanxiu/data-annotation/runtime` 完成宽屏 `1600x1000`、普通桌面 `1366x900`、窄屏 `820x1180` 取证。`attendance/configs` 的 `summaryStripCount = 0` 且三视口 `bodyScrollWidth == bodyClientWidth`；`notes/list` 修复后二次复拍 `rowCount = 50`、`listSummary = 共 246 条 当前显示 50 条`、`toastTexts = []`。静态校验：`npm run typecheck --prefix frontend`、`npm run build --prefix frontend` 通过。
- 入口依赖污染检查：本轮未命中强制入口污染检查条件，因为没有改 `frontend/package*.json`、`vite.config.ts`、`manualChunks`、全局样式、worker/wasm 或公开入口。
- 根因分层：`attendance/configs` 仍是表现层冗余减法；`notes/list` 的新回退属于前端状态投影问题，缓存命中后的后台刷新沿用了“主动执行失败”的一级提示；`fanxiu/runtime` 当前只能判定为验证环境受同链路未提交 Runtime 改动污染，还不能把 500 直接归因到本次提交。
- 剩余风险：`fanxiu/runtime` 三视口虽然都已离开 shell loading，但控制台持续记录 `/fanxiu/data-annotation/runtime/status`、`/scheduler`、`/doctor-watch`、`/cell-logs` 的 500 warning；同时当前工作树在 `backend/core/fanxiu/runtime/__init__.py`、`backend/core/fanxiu/runtime/behavior_tree.py`、`backend/core/fanxiu/runtime/kernel.py`、`scripts/fanxiu_bt.py` 等同链路文件上存在未提交改动，当前本地服务无法形成纯 commit 验证闭环。
- 处理结果：本轮已完成完整增量范围的提交归类、业务建模、概念图、三视口真实取证、`notes/list` 低风险减法修复和前端验证；但由于 `fanxiu/runtime` 同链路存在本地未提交 Runtime 改动污染，暂不推进 `last_audited_commit`，并把 `b1eef41..144da8b` 记录到 `pending_or_skipped_ranges`，待工作树稳定后优先关闭。

- 补关闭：本轮在干净工作树 `D:/home/chenkunze/slns/codeyun-ui-audit-144da8b` 重新启动 `144da8b` 对应服务，并复用 Chrome 已登录 `localhost:5173` 标签页刷新 `fanxiu/data-annotation/runtime`。刷新后页面稳定离开 shell loading，能够渲染 `凡修行为树 / 守护 / 作业` 等主内容；上一轮控制台里的大量 `500` 主要是刷新前旧失败样本，不再作为当前页面阻塞证据。
- 补关闭 API 探针：`GET /api/auth/me = 200`、`GET /api/devices/ = 200`、`GET /api/fanxiu/data-annotation/scheduler/tasks = 200`、`GET /api/fanxiu/data-annotation/runtime/doctor-watch/latest = 200`；旧标签页里的 `entry_id=30b82d72-8a76-4a74-be4b-4fc1591c6ce2` 对应 `GET /runtime/status` 返回 `404 Device entry not found`，说明它在干净快照里已失效，不能再拿来证明 `144da8b` 本身导致 runtime 页面失效。
- 补关闭证据：`C:/Users/kzche/AppData/Local/Temp/codeyun/ui-design-audit/2026-07-07-frontend-design-144da8b-closeout/report.md`、`fanxiu-runtime-chrome-stable.png`、`viewport-probe.json`。本轮 in-app Browser 持续出现动态 import / HMR websocket 不稳定，因此 clean proof 主要依赖 Chrome 稳定页与 API 探针；多视口布局判断继续沿用上一轮已拿到的页面截图。
- 最终处理结果：`b1eef41..144da8b` 的 pending 已关闭，可以把 `last_audited_commit` 推进到 `144da8b95f22734ff7238f7b8c77377e7e6a0b47`，并清空 `pending_or_skipped_ranges`。

### 2026-07-07（第十七轮）

- 完整范围：`58019c37a4945e8278fbdc72d8b8959bd7fc4f30..b1eef41fd9b948a83bb5248b4fd0f69bbeaec33f`
- 覆盖提交：`b1eef41fd9b948a83bb5248b4fd0f69bbeaec33f`
- 前端入口提交：`b1eef41fd9b948a83bb5248b4fd0f69bbeaec33f`
- 入口如何牵引到旧问题：这次提交同时触达 `fanxiu/data-annotation/runtime`、`attendance/configs`、`attendance/orders`、`NoteSheetWorkspace` 和 `deviceFiles`。这些页面与共享链路表面上属于不同业务，但都在把“执行位置 / 运行命令 / 局部能力”收回到更少的一层概念里：凡修从“手动任务”收回到 `task cell` 运行边界，考勤配置把设备获取变成后台刷新与局部缓存，工作表把 attendance API 停留在局部异步 import，设备文件保留本地路径与索引路径双通道而不扩成新的一级入口说明。
- 本轮减法：`attendance/configs` 删除了两个重复摘要条，不再把“当前浏览器/主机/默认执行设备/订单模式”在一级页面上重复说一遍；页面保留原始配置表单、step 覆写、账号配置和保存动作，信息量不变但一级阅读路径更短。其余相关页面保持提交本身的减法方向，没有额外新增常驻控件或解释壳层。
- 信息量保持：凡修行为树页仍保留运行状态与调度入口；考勤配置页仍保留 step1-step6、默认设备、订单对象、订单模式和账号事实；订单页仍保留查询/退款闭环；工作表与设备文件页仍保留原有浏览和操作能力，只是共享能力边界更局部。
- 概念图/线框图：报告中的 Mermaid 把本轮链路收回到 `b1eef41 -> task cell 边界 / 后台设备加载 / 局部异步 import -> 主界面只保留配置事实与下一步动作 -> 删除重复摘要条`，证明这轮修复是在同一减法方向上继续收口，而不是新增说明区掩盖复杂度。
- 报告路径：`C:/Users/kzche/AppData/Local/Temp/codeyun/ui-design-audit/2026-07-07-frontend-design-b1eef41f/report.md`
- 验证：复用本地 `5173/8000` 开发环境，对 `fanxiu/data-annotation/runtime`、`attendance/configs`、`attendance/orders`、`workbook/14?sheet=58855`、`cluster/files` 完成宽屏 `1600x1000`、普通桌面 `1366x900`、窄屏 `820x1180` 取证；五页主体均正常离开 shell loading，三视口下没有新增页面级横向溢出。修复后 `attendance/configs` 三视口截图已重拍，`attendance-configs-postfix-summary.json` 记录 `summaryStrips=0` 且 `bodyScrollWidth == bodyClientWidth`。静态校验：`npm run typecheck --prefix frontend`、`npm run build --prefix frontend` 通过。
- 入口依赖污染检查：本轮未命中强制入口污染检查条件，因为没有改 `frontend/package*.json`、`vite.config.ts`、`manualChunks`、全局样式、worker/wasm 或公开入口；但由于提交触达共享异步边界，仍补做轻量构建检查，确认 `frontend/dist/index.html` 未预加载 `attendance`、`deviceFiles`、`fanxiu`、`NoteSheetWorkspace` 局部 chunk；`main-DCChCjSv.js` 中相关名字仅出现在 Vite 依赖映射表，未见可直接判定为主入口顶层静态 import 的证据。
- 根因分层：凡修行为树与考勤配置/订单这轮主要属于前端状态投影与共享能力边界收敛，`attendance/configs` 的重复摘要条属于同页表现层冗余；`NoteSheetWorkspace` 的 attendance API 动态导入属于前端工程架构局部化。本轮没有暴露必须转交后端模型审计的新问题。
- 剩余风险：窄屏强制 reload 过程中曾出现一次瞬时 `加载考勤配置失败` toast，但二次复测未复现，最终实拍无该提示，更像重载过程噪声而非稳定回归；另外构建仍保留项目既有的大 chunk warning 和 `jszip` 相关 browser compatibility warning，这些属于既有工程体积风险，不是本轮新增污染。
- 处理结果：本轮已完成完整增量范围的提交归类、业务建模、概念图、真实多视口取证、低风险减法修复、轻量入口依赖检查和前端验证，因此把 `last_audited_commit` 推进到 `b1eef41fd9b948a83bb5248b4fd0f69bbeaec33f`，`pending_or_skipped_ranges` 保持为空。

### 2026-07-07（第十六轮）

- 完整范围：`62b30e1480bfaa72d2259012bea6192c304a5285..58019c37a4945e8278fbdc72d8b8959bd7fc4f30`
- 覆盖提交：`68929496188314ea19a2ecc9bbb43d94057fd16e`、`58019c37a4945e8278fbdc72d8b8959bd7fc4f30`
- 前端入口提交：`68929496188314ea19a2ecc9bbb43d94057fd16e`
- 非前端提交判定：`58019c37a4945e8278fbdc72d8b8959bd7fc4f30` 仅触达后端/测试/文档，没有前端页面、菜单、权限、路由或前端可感知 API 投影变更。
- 入口如何牵引到旧问题：这次提交同时触达 `pokemon-tcg/catalog`、`notes/center?tab=calendar`、`workbook/14?sheet=58855` 与共享 `fanxiu.ts`。四者共享的不是同一个业务实体，而是同一类“把同一份事实继续收回到更少前端概念”的减法动作：图鉴收回到项目统一分页模型，年/卷/纪视图收回到 bucket 代表节点本身，工作表收回到单一 fill-height 表格工作区，凡修 note 语义 helper 收回到异步局部链路，不再停留在共享同步 API 入口。
- 本轮减法：`pokemon-tcg` 保留搜索、卡包、属性、卡墙、分页与详情，但把底部分页收回到项目统一的 `StandardPagination`；`CalendarNotes` 保留月/年/卷/纪与前后端筛选闭环，但不再把 bucket 中已表达的节点再平铺成一份 `flat nodes` 进入共享 store；`resource-view` / `NoteSheetWorkspace` 去掉额外 `runtime-max-grid-height=960` 上限，并把首屏高度计算前置到 HotTable 真正挂载前；`fanxiu.ts` 把 note 规范化与 payload helper 下沉到 `fanxiuNoteHelpers` 异步 chunk。
- 信息量保持：图鉴仍保留完整卡牌事实和来源跳转；日历仍保留四种时间尺度、前后端筛选程序与月份代表节点；工作表仍保留 tab、公式栏和完整表格事实；凡修 note 能力不变，只是共享主入口少带一份 note 语义依赖。
- 概念图/线框图：报告里的 Mermaid 把链路收回到 `6892949 -> StandardPagination / bucket 代表节点 / fill-height workbook / async fanxiu note helper -> 维持原闭环但删除重复概念`，证明本轮重点是共享模型减法，而不是新增页面功能。
- 报告路径：`C:/Users/kzche/AppData/Local/Temp/codeyun/ui-design-audit/2026-07-07-frontend-design-58019c37/report.md`
- 验证：复用本地 `5173/8000` 开发环境，对 `pokemon-tcg/catalog`、`notes/center?tab=calendar`、`workbook/14?sheet=58855&sheetPerf=1` 完成宽屏 `1600x1000`、普通桌面 `1366x900`、窄屏 `820x1180` 取证；`pokemon-tcg` 与 `notes-calendar` 三视口下都没有新增横向溢出、分页错位或筛选栏回退；`workbook` 三视口 settled 截图均已正常落出表格内容，并采到 `sheet-perf:event` 说明 `sheetPerf=1` 已真实透传进 `NoteSheetWorkspace`。静态校验：`uv run pytest backend/tests/test_note_calendar_summary.py tests/test_note_sheet_workspace_performance_guards.py -q`、`npm run typecheck --prefix frontend`、`npm run build --prefix frontend` 通过；三页页面控制台未采到 `warn/error`。
- 入口依赖污染检查：本轮未被规则强制触发，因为提交未改 `frontend/package*.json`、`vite.config.ts`、`manualChunks`、全局样式或公开入口；但由于触达共享 API 与性能护栏，仍补做构建检查，确认 `frontend/dist/index.html` 与 `main-CZJDOWlb.js` 都未预加载或顶层引入 `fanxiuNoteHelpers`，且 helper 已单独产出为 `fanxiuNoteHelpers-C0RHaxq8.js`，由 `fanxiu-CMIBBRnB.js` 动态拉起。
- 根因分层：`pokemon-tcg` 属于前端表现层与交互统一，`CalendarNotes` 属于前端状态投影收敛，`resource-view` / `NoteSheetWorkspace` 属于前端运行时性能与挂载顺序优化，`fanxiu.ts` helper 拆分属于前端工程架构依赖边界收敛；本轮没有暴露需要转交后端模型审计的新问题。
- 剩余风险：`workbook/14?sheet=58855&sheetPerf=1` 在 `1600x1000` 首次进入时，外围 chrome 会先出现，HotTable 大约在 0.7 秒内完成延迟挂载；settled 截图和 perf 日志证明它没有持续白屏或卡死，但“短暂空白工作区”仍是这次性能护栏改动后需要继续观察的体感风险。
- 处理结果：本轮已完成完整增量范围的提交归类、业务建模、概念图、真实多视口取证、轻量入口依赖检查与静态验证；没有发现需要追加自动修复的新持久性 UI 回退，因此把 `last_audited_commit` 推进到 `58019c37a4945e8278fbdc72d8b8959bd7fc4f30`，`pending_or_skipped_ranges` 保持为空。

### 2026-07-06（第十五轮）

- 完整范围：`b3c8aa4b0933a1414223006e1153bea15923ade3..62b30e1480bfaa72d2259012bea6192c304a5285`
- 覆盖提交：`62b30e1480bfaa72d2259012bea6192c304a5285`
- 前端入口提交：`62b30e1480bfaa72d2259012bea6192c304a5285`
- 入口如何牵引到旧问题：这次提交同时触达 `pokemon-tcg/catalog`、`attendance/orders` 与共享 `NoteSheetWorkspace`。三者共享的不是同一个业务实体，而是同一类“把主决策闭环收回到更少 UI 概念”的减法动作：图鉴要回到 `候选浏览 -> 当前详情`，订单页要回到 `输入/执行 -> 历史结果`，工作表则要把性能护栏留在运行时内部，不能反向长出新的壳层或常驻状态。
- 本轮减法：`pokemon-tcg` 从旧的并列分栏收回到 CodeYun 一致的上下 inspector，保留搜索、卡包、属性、分页和详情事实，但不再让右侧常驻详情压缩卡墙横向浏览面；`attendance/orders` 把退款历史桌面态收回到内容驱动的原生表格，`820px` 下直接切成卡片列表；`NoteSheetWorkspace` 这次主要减少公式显示、合并单元格和隐藏行更新里的重复 render/update 路径，没有新增任何一级界面实体。
- 信息量保持：`pokemon-tcg` 仍保留完整中文标题、属性、招式、弱点/抵抗/撤退和来源链接，只是把详情放回卡墙之后；`attendance/orders` 仍保留输入、查询、执行退款、历史金额和处理结果，只是让历史区回到更轻的表格/卡片；`workbook` 仍保留 tab、公式栏和表格能力，性能护栏只收敛运行时更新，不改变用户可见能力。
- 概念图/线框图：报告里的 Mermaid 把链路收回到 `62b30e1 -> Pokemon 上下 inspector / Orders 原生历史表 / Workbook 性能护栏 -> 先完成主闭环 -> 再检查是否引入重复控件或解释壳层`，证明本轮重点是模型减法，不是新增功能解释区。
- 报告路径：`C:/Users/kzche/AppData/Local/Temp/codeyun/ui-design-audit/2026-07-06-frontend-design-62b30e1/report.md`
- 验证：复用本地 `5173/8000` 开发环境，对 `pokemon-tcg/catalog`、`attendance/orders`、`workbook/14?sheet=58855` 完成宽屏 `1600x1000`、普通桌面 `1366x900`、窄屏 `820x1180` 截图；`pokemon-tcg` 三视口下详情区仍保持首屏可见，`attendance/orders` 在桌面态维持内容驱动表格、在 `820px` 下正确切成历史卡片，`workbook` 三视口都稳定落出公式栏与表格。静态校验：`uv run pytest tests/test_note_sheet_workspace_performance_guards.py -q`、`npm run typecheck --prefix frontend`、`npm run build --prefix frontend` 通过；三页真实控制台均未采到页面级 `warn/error`。
- 入口依赖污染检查：本轮未触发。提交未改 `frontend/package*.json`、`vite.config.ts`、`manualChunks`、全局样式或公开入口依赖边界，因此不额外做主入口污染专项；仅补跑构建确认无构建级回退。
- 根因分层：`pokemon-tcg` 属于前端状态投影与布局模型重排，`attendance/orders` 属于表现层和表格投影收敛，`workbook` 属于运行时性能护栏；三者都没有暴露需要自动止血的新模型问题。
- 剩余风险：当前本地工作树另有 `NoteSheetWorkspace` / `resource-view` / 性能护栏测试与设计文档的少量未提交跟进，它们不属于本轮 commit 范围；真实页面复核是在当前本地服务状态下完成，未观察到新增 UI 概念或可见回退，但若这些跟进继续扩大，后续应单独复核。
- 处理结果：本轮已完成完整增量范围的提交归类、业务建模、概念图、真实多视口取证与静态验证；没有发现需要追加自动修复的新 UI 回退，因此把 `last_audited_commit` 推进到 `62b30e1480bfaa72d2259012bea6192c304a5285`，`pending_or_skipped_ranges` 保持为空。

### 2026-07-06（第十四轮）

- 完整范围：`582d2becc74bd1b7fa1a104a58e6fbac4128cbf2..b3c8aa4b0933a1414223006e1153bea15923ade3`
- 覆盖提交：`b3c8aa4b0933a1414223006e1153bea15923ade3`
- 前端入口提交：`b3c8aa4b0933a1414223006e1153bea15923ade3`
- 入口如何牵引到旧问题：提交同时触达 `pokemon-tcg/catalog`、`notes/center?tab=list` 与 `NoteSheetWorkspace`。这三处共享的不是新功能堆叠，而是同一条“首屏先给用户稳定判断对象”的链路：卡牌页要先看到当前卡详情，列表页要先拿到分类投影，工作表要先稳定落出表格与公式栏。因此本轮重点不是加控件，而是确认中文投影、色板缓存和公式预加载没有把旧页面重新推回解释过重、首屏抖动或层级变形。
- 本轮减法：本轮没有追加源码修复。提交自身新增的是中文事实投影和性能护栏，不是新的界面实体；真实三视口复验确认它没有把页面包装成更多摘要条、说明卡或重复状态。`pokemon-tcg` 仍维持“筛选 -> 卡墙 -> 当前详情”，`notes/list` 仍维持“后端筛选 / 前端筛选 / 列表 / 详情”，`workbook` 仍维持“tab / 公式栏 / 表格”的基础模型。
- 信息量保持：`pokemon-tcg` 现在可直接读到中文标题、属性和招式文本，但没有额外扩写常驻说明；`notes/list` 首屏更快拿到分类配置，但没有新增筛选层；`workbook` 预加载公式引擎与合并单元格渲染门槛只是收敛启动节奏，不改变表格能力。
- 概念图/线框图：报告中的 Mermaid 已把链路收回到 `增量提交 -> pokemon 中文投影 / notes 首屏缓存 / sheet 预加载护栏 -> 用户先完成当前页主判断 -> 检查是否引入重复事实或首屏回退`，证明本轮是围绕基础模型稳定性验收，而不是额外增设 UI 概念。
- 报告路径：`C:/Users/kzche/AppData/Local/Temp/codeyun/ui-design-audit/2026-07-06-frontend-design-b3c8aa4b/report.md`
- 验证：复用本地 `5173/8000` 开发环境，对 `pokemon-tcg/catalog`、`notes/center?tab=list`、`workbook/14?sheet=58855` 完成宽屏 `1600x1000`、普通桌面 `1366x900`、窄屏 `820x1180` 截图；`pokemon-tcg` 三视口下 `detailTop` 均保持约 `194px`，未回到上轮修过的详情跌出首屏问题。静态校验：`uv run pytest tests/test_note_sheet_workspace_performance_guards.py -q`、`npm run typecheck --prefix frontend`、`npm run build --prefix frontend` 通过；三页真实控制台均未采到页面级 `warn/error`。
- 入口依赖污染检查：本轮未触发。提交未改 `frontend/package*.json`、`vite.config.ts`、`manualChunks`、全局样式或公开入口依赖边界，因此不额外做主入口污染专项；仅补跑构建确认无构建级回退。
- 根因分层：`pokemon-tcg` 属于前端状态投影增强，`notes/list` 属于前端状态投影/性能护栏，`workbook` 属于表现层与运行时性能护栏；三者都没有暴露需要自动止血的新模型问题。
- 剩余风险：`notes/center?tab=list` 在 `820px` 下仍依赖表格横向滚动，右侧列首屏可见性偏弱；这是该页既有高密度表格结构，本轮提交没有放大它，因此只记录不扩修。
- 处理结果：本轮已完成完整增量范围的提交归类、业务建模、概念图、真实多视口取证与静态验证；没有发现需要追加自动修复的新 UI 回退，因此把 `last_audited_commit` 推进到 `b3c8aa4b0933a1414223006e1153bea15923ade3`，`pending_or_skipped_ranges` 保持为空。

### 2026-07-06（第十三轮）

- 完整范围：`1dd2c8ae02e9d40b7caa6aa9b8ec73b8d4b51fa2..582d2becc74bd1b7fa1a104a58e6fbac4128cbf2`
- 覆盖提交：`d4dfa2cb92da56735bc0b4ccada2a1e853e6eac0`、`46bd7400ab62e1a9c03b008f3f269a9daa5eabe6`、`582d2becc74bd1b7fa1a104a58e6fbac4128cbf2`
- 前端入口提交：`d4dfa2cb92da56735bc0b4ccada2a1e853e6eac0`、`46bd7400ab62e1a9c03b008f3f269a9daa5eabe6`、`582d2becc74bd1b7fa1a104a58e6fbac4128cbf2`
- 入口如何牵引到旧问题：这三次提交一条线同时触达 `NoteSheetWorkspace`、公共入口加载、`attendance/orders`、`cluster/services`、`fanxiu/data-annotation/runtime`，并新增了 `pokemon-tcg/catalog`。真实三视口复验后，`orders`、`services`、`runtime` 与两个 `workbook` 入口没有出现新的复杂度回退；真正被同链路牵出的旧问题落在新页面 `pokemon-tcg/catalog`，因为提交刚引入“卡墙 + 当前详情”的基础模型，但 `820px` 视口过早退成单列，导致当前选中卡详情被整面卡墙压到数千像素之后，首屏不再支持“选中一张卡后立即判断详情”。
- 本轮减法：没有新增任何按钮、字段、说明区或状态，只在 [`frontend/src/standard/pokemon-tcg/page.vue`](D:/home/chenkunze/slns/codeyun/frontend/src/standard/pokemon-tcg/page.vue) 重排响应式断点。新增 `1180px` 和 `900px` 两层双栏收窄态，让详情栏在平板宽度继续与卡墙并排；把真正的单列堆叠阈值从 `980px` 收回到 `760px`。这样保留原有信息量，但删除了“选中详情掉到卡墙尾部”的结构性空转。
- 信息量保持：`pokemon-tcg` 仍保留搜索、卡包筛选、卡牌分页、卡墙、详情图、招式/弱点/稀有度等完整事实；`orders`、`services`、`runtime` 和 `workbook` 入口本轮只做同链路复验，没有扩写新概念。减少的是过早改单列造成的闭环断裂，不是减少页面能力。
- 概念图/线框图：报告中的 Mermaid 已把链路收回到 `增量提交 -> pokemon-tcg / workbook / runtime / orders / services -> 发现 820px 提前改单列 -> 详情跌出首屏 -> 保留双栏到 760px`，证明修复是在收回布局层级，而不是追加新控件。
- 报告路径：`C:/Users/kzche/AppData/Local/Temp/codeyun/ui-design-audit/2026-07-06-frontend-design-582d2be/report.md`
- 验证：复用本地 `5173/8000` 开发环境，对 `pokemon-tcg/catalog`、`attendance/orders`、`cluster/services`、`fanxiu/data-annotation/runtime`、`workbook/7?sheet=20`、`workbook/14?sheet=58855` 完成宽屏 `1600x1000`、普通桌面 `1366x900`、窄屏 `820x1180` 截图，证据见 `capture-results.json` 与全部页面截图。`pokemon-tcg` 修复前 `detailTop = 4368.83`、`detailVisibleInViewport = false`；修复后 `detailTop = 194.39`、`detailVisibleInViewport = true`。静态校验：`uv run pytest tests/test_note_sheet_workspace_performance_guards.py -q`、`npm run typecheck --prefix frontend`、`npm run build --prefix frontend` 通过。
- 入口依赖污染检查：本轮命中 `frontend/index.html`、`src/main.ts`、`src/router/index.ts` 与 `NoteSheetWorkspace` 性能链路，因此补做构建与真实入口核查。`frontend/dist/index.html` 未预加载 `handsontable-vendor`、`formula-vendor`、`file-viewer-vendor`、`pdfjs-vendor` 或 `pokemon` / `NoteSheetWorkspace` 局部 chunk；`frontend/dist/assets/main-CTsgWEEW.js` 也没有这些高风险局部依赖的顶层直接 import。真实资源列表里，`pokemon-tcg/catalog` 与 `notes/center?tab=list` 都未命中 sheet/file-viewer/pdf 相关资源，而 `workbook/14?sheet=58855` 按预期加载了 `NoteSheetWorkspace`、`hyperformula` 与 `@handsontable/vue3`。说明主入口未被局部重依赖拖脏。
- 根因分层：`pokemon-tcg` 的问题属于表现层/信息层级，根因是断点过早改单列，让同一业务对象的“浏览”和“判断”首屏分离；`orders`、`services`、`runtime`、`workbook` 本轮未暴露新的前端状态投影、后端数据投影或业务建模债务，无需新增 `docs/CodeYun自动化协作交接.md` 条目。
- 剩余风险：`workbook` 两个入口的窄屏继续依赖工作表局部横向滚动，这是既有宽表形态；`pokemon-tcg` 在真正手机宽度下仍会改成单列，这符合移动端“先扫卡图、再向下看详情”的基础模型，不属于本轮风险。
- 处理结果：本轮已完成完整增量范围的提交归类、概念图/线框图、真实多视口截图、入口依赖污染检查、低风险减法修复和前端验证，因此把 `last_audited_commit` 推进到 `582d2becc74bd1b7fa1a104a58e6fbac4128cbf2`，保持 `pending_or_skipped_ranges` 为空。

### 2026-07-05（第十二轮）

- 完整范围：`d74182548ac33389a577751f5cbad158e77b7846..1dd2c8ae02e9d40b7caa6aa9b8ec73b8d4b51fa2`
- 覆盖提交：`1dd2c8ae02e9d40b7caa6aa9b8ec73b8d4b51fa2`
- 前端入口提交：`1dd2c8ae02e9d40b7caa6aa9b8ec73b8d4b51fa2`
- 入口如何牵引到旧问题：这次提交同时把性能与守卫继续压进 `NoteSheetWorkspace` 和 `cluster/services`。真实三视口复验后，`workbook/7?sheet=20` 的动作行仍维持统一按钮模型，`workbook/14?sheet=58855` 与 `cluster/runtime` 也没有新增复杂度回退；真正被同链路牵出的旧问题落在 `cluster/services` 本身，左侧设备面板只有 2 个入口，却被右侧 Token 长列表拉成整列高卡片，放大了一整块没有新增信息的空白壳层。
- 本轮减法：不新增任何控件、入口、字段或说明区，只在 [`frontend/src/standard/cluster/services/page.vue`](D:/home/chenkunze/slns/codeyun/frontend/src/standard/cluster/services/page.vue) 给 `.device-panel` 补了 `align-self: start;`，让设备列表按内容高度结束，不再和右侧 Token 列表等高。`NoteSheetWorkspace` 入口本轮只做复验，没有继续增殖局部状态或解释。
- 信息量保持：服务页仍保留 `设备 -> OCR 状态 -> Token 管理` 的完整闭环，`NoteSheetWorkspace` 仍保留报名表动作与考勤表浏览能力；减少的是“设备列表外再包一整列空白卡壳”，不是减少操作能力。
- 概念图/线框图：报告中的 Mermaid 已把链路收回到 `提交 1dd2c8ae -> NoteSheetWorkspace / cluster/services -> workbook/7 动作行复验 + services 设备页复验 -> 发现左侧设备卡被 Token 长列表拉高 -> 收回为空间按内容结束的设备列表`，证明修复是在删除空结构，而不是补新控件。
- 报告路径：`C:/Users/kzche/AppData/Local/Temp/codeyun/ui-design-audit/2026-07-05-frontend-design-1dd2c8ae/report.md`
- 验证：复用本地 `5173/8000` 开发环境，对 `workbook/7?sheet=20`、`workbook/14?sheet=58855`、`cluster/services?entry_id=da2d06f2-ab06-4c05-a614-e396e2e6e6a3`、`cluster/runtime?entry_id=da2d06f2-ab06-4c05-a614-e396e2e6e6a3` 完成宽屏 `1600x1000`、普通桌面 `1366x900`、窄屏 `820x1180` 截图；`workbook-registration` 三视口都确认动作行仍包含 `导入excel / 新增学员 / 更新订单匹配 / 更新用户匹配 / 综合更新`，`cluster-services` 远端设备三视口都稳定为 `19 个，19 启用`，`cluster-runtime` 稳定态 `hasRuntimeIssue = false`。静态校验：`uv run pytest tests/test_note_sheet_workspace_performance_guards.py -q`、`npm run typecheck --prefix frontend`、`npm run build --prefix frontend` 通过。
- 入口依赖污染检查：本轮虽然未改 `vite.config.ts`，但提交仍落在 `NoteSheetWorkspace / handsontable / formula` 链路上，因此补做了构建与真实入口核查。`frontend/dist/index.html` 没有 `handsontable` / `formula` 相关 `modulepreload` 或 `stylesheet`；`frontend/dist/assets/main-U27EBVlQ.js` 只在依赖映射表中提到 `NoteSheetWorkspace` 异步 chunk，没有主入口顶层命中 `handsontable-vendor` / `formula-vendor` 的直接 import；真实 `cluster/runtime` 与 `notes/center?tab=list` 资源列表也都未命中这些高风险 chunk。
- 根因分层：`cluster/services` 的问题属于表现层/信息层级，根因是 grid 默认拉伸让设备列表壳层和右侧长列表等高；`NoteSheetWorkspace` 本轮没有再暴露新的表现层、前端状态投影或后端数据投影回退，无需新增 `docs/CodeYun自动化协作交接.md` 条目。
- 剩余风险：`NoteSheetWorkspace` 窄屏本质仍是宽表浏览场景，继续依赖工作表内部横向滚动；`cluster/services` 的 Token 行数很多，但当前已经通过内容驱动左栏高度避免再制造第二个空白结构。这两点都属于既有业务形态，不是本轮新增复杂度。
- 处理结果：本轮已完成完整增量范围的提交归类、概念图/线框图、真实多视口截图、低风险减法修复和前端验证，因此把 `last_audited_commit` 推进到 `1dd2c8ae02e9d40b7caa6aa9b8ec73b8d4b51fa2`，保持 `pending_or_skipped_ranges` 为空。

- 完整范围：`048ce7494b660cd0a559e6d1514c5169f38b37fd..d74182548ac33389a577751f5cbad158e77b7846`
- 覆盖提交：`d74182548ac33389a577751f5cbad158e77b7846`
- 前端入口提交：`d74182548ac33389a577751f5cbad158e77b7846`
- 入口如何牵引到旧问题：这次提交同时触达 `ImageGalleryWorkspace`、`attendance/orders`、`cluster/view-mn`、`ListNotes` 和 `NoteSheetWorkspace`。真实多视口复验后，`attendance/orders`、`cluster/view-mn`、`notes/center` 没有新增复杂度回退；真正被同链路牵出的旧问题落在 `workbook/7?sheet=20` 的报名表动作行，因为这次提交刚治理同一工作台的性能与守护链路，结果历史 header entity 脏数据把 `更新用户匹配` 从统一按钮模型打回成纯文本，动作闭环缺了一环。
- 本轮减法：没有新增任何控件、入口、字段或说明区，只在 [`frontend/src/standard/notes/components/NoteSheetWorkspace.vue`](D:/home/chenkunze/slns/codeyun/frontend/src/standard/notes/components/NoteSheetWorkspace.vue) 收紧两段旧数据兼容逻辑。第一步允许默认注册动作在“同类型旧文案文本”上恢复 action；第二步新增 header entity action 清洗，让规范化后的 `cell_meta` 成为动作唯一来源，不再被旧 `entity_cells` 反向覆盖。结果是报名表动作行重新收回到既有 5 个动作概念：`导入excel / 新增学员 / 更新订单匹配 / 更新用户匹配 / 综合更新`。
- 信息量保持：`workbook` 的动作、提示、说明、列结构和业务能力都不变；减少的是“同一动作有的列是按钮、有的列是纯文本”的双轨表达，不是减少操作能力。`attendance/orders`、`cluster/view-mn`、`notes/center` 本轮只做同链路复验，没有扩写新概念。
- 概念图/线框图：报告中的 Mermaid 已把链路收回到 `提交 d7418254 -> 报名表动作行复验 -> cell_meta 已规范化 -> header entity_cells 旧 action 覆盖 -> 用户ID列退化为纯文本 -> 清洗 header entity action`，证明这次修复是在减少旧模型分裂，而不是补新控件。
- 报告路径：`C:/Users/kzche/AppData/Local/Temp/codeyun/ui-design-audit/2026-07-05-frontend-design-d7418254/report.md`
- 验证：先收敛本地 `uv run dev.py` 重复 supervisor / `uvicorn_hidden`，恢复稳定 `5173/8000`；随后用真实 `http://127.0.0.1:5173/workbook/7?sheet=20` 采集 `1600x1000`、`1366x900`、`820x1180` 三视口截图与结构 JSON，见 `workbook-7-sheet-20-localhost-stable-*.png/.json` 与 `workbook-7-sheet-20-localhost-stable-summary.json`。最终三档视口都确认 `registration_user_match` 已恢复为按钮，`buttonTypes` 在动作行 index `15` 为 `registration_user_match`。静态校验：`uv run pytest tests/test_note_sheet_workspace_performance_guards.py -q`、`npm run typecheck --prefix frontend`、`npm run build --prefix frontend` 通过。
- 入口依赖污染检查：本轮提交未涉及 `vite.config`、重依赖、`manualChunks`、worker、wasm、预览器或公开/匿名入口，也未改变主入口依赖边界，因此未触发额外的“入口依赖污染检查”；仅完成常规 `npm run build --prefix frontend` 收口。
- 根因分层：表现层无问题；真正根因属于前端状态投影链路，具体是 `loadSheetDocument()` 后的规范化 `cell_meta` 已正确，但 `initializeSheetEntitiesFromDocument()` 又让旧 header `entity_cells.action` 覆盖回来。后端数据层仍返回历史错位 action，但前端现在已能把这类旧数据收回到统一按钮模型。
- 剩余风险：当前 API 返回的历史 `cell_meta` / `entity_cells` 仍含错位动作。本轮前端兼容已把同类 UI 回退压住，但如果后端未来继续写入新的 header 动作脏数据，仍建议后续在生成链路补一次根治。
- 处理结果：本轮已完成完整增量范围的提交归类、真实多视口截图、低风险修复和构建验证，因此把 `last_audited_commit` 推进到 `d74182548ac33389a577751f5cbad158e77b7846`，保持 `pending_or_skipped_ranges` 为空。

- 完整范围：`3aea6f94afbccf3ce35b4207a6f9e0111ce186fc..048ce7494b660cd0a559e6d1514c5169f38b37fd`
- 覆盖提交：`048ce7494b660cd0a559e6d1514c5169f38b37fd`
- 前端入口提交：`048ce7494b660cd0a559e6d1514c5169f38b37fd`
- 入口如何牵引到旧问题：这次提交同时触达 `cluster/tasks`、`taskStore`、`attendance/orders`、`ListNotes` 和 `handsontableOrderSetup`。真实页面复验后，`cluster/runtime` 与 `fanxiu/data-annotation/runtime` 的任务状态同步方向成立，`notes/list` 的分类标签也恢复为可读文案；真正被同链路牵出的旧问题落在 `attendance/orders`，因为这次提交刚收紧了表格列宽和 auto sizing，结果 `820px` 视口下退款历史仍硬撑桌面宽表，右侧订单 / 退款原因 / 处理结果被截成半列，首屏判断闭环断裂。
- 本轮减法：仅在 [`frontend/src/standard/attendance/orders/page.vue`](D:/home/chenkunze/slns/codeyun/frontend/src/standard/attendance/orders/page.vue) 把退款历史切到已有移动卡片视图的阈值提前到 `960px`，并把 `.mobile-history-list` / `.desktop-history-table` / 卡片详情样式上提到同一断点。没有新增入口、字段、说明区或状态，只是把同一份退款事实收回到已有的窄屏表达。
- 信息量保持：宽屏 `1600x1000` 和普通桌面 `1366x900` 仍保留桌面表格；窄屏 `820x1180` 继续保留金额、时间、操作人、订单号、退款原因和处理结果，但不再要求用户在半截宽表里横向猜字段。`cluster/runtime`、`fanxiu/runtime`、`notes/list` 本轮只做同链路复验，没有继续扩写新概念。
- 概念图/线框图：报告中的 Mermaid 把这轮链路收回到 `提交 -> 任务状态同步 / 表格装配 / 分类 palette -> 三条真实页面复验 -> attendance 窄屏历史闭环`，证明窄屏历史不该继续复用桌面宽表。
- 报告路径：`C:/Users/kzche/AppData/Local/Temp/codeyun/ui-design-audit/2026-07-05-frontend-design-048ce749/report.md`
- 验证：复用本地 `5173/8000` 开发环境，对 `cluster/runtime`、`fanxiu/data-annotation/runtime`、`attendance/orders`、`notes/center?tab=list` 完成宽屏 `1600x1000`、普通桌面 `1366x900`、窄屏 `820x1180` 三视口取证；初始 12 张截图与 `evidence.json` 写入同目录，所有页面 `overflowX = 0`，取证期未采到新的 `console error/warn`。修复后补拍 `attendance-orders-1600x1000-after.png`、`attendance-orders-1366x900-after.png` 与 `attendance-orders-820x1180-final.png`；最终 `820px` 窄屏已确认 `articleCount = 20`、桌面历史表隐藏、卡片历史正常显示。附加静态校验：`npm run typecheck --prefix frontend`、`npm run build --prefix frontend` 通过。
- 入口依赖污染检查：本轮虽然未改 `vite.config`，但命中了 `handsontable` 装配链，因此补做检查。`frontend/dist/index.html` 仅预加载 `_plugin-vue_export-helper`、`vue-vendor`、`dayjs-vendor`、`vendor`、`element-icons-vendor`、`element-plus-core`、`data-vendor`，未发现 `handsontable-vendor` / `attendance` / `formula` 相关 `modulepreload` 或 `stylesheet`；`frontend/dist/assets/main-*.js` 顶层也未命中这些局部依赖。真实入口资源检查里，`cluster/runtime` 和 `notes/center?tab=list` 都未观察到 `handsontable` / `formula` 相关资源名，说明表格重依赖仍停留在考勤局部入口。
- 根因分层：本轮自动修复点属于前端断点与样式条件不同步，不是退款后端模型、分页接口或查询流程问题；`cluster/runtime` / `fanxiu/runtime` / `notes/list` 本轮未发现新的状态投影或工程边界回退。
- 剩余风险：`attendance/orders` 的窄屏历史现在在 `<=960px` 统一改走卡片视图，后续如果产品想在平板宽度保留更密集的表格信息，需单独重新设计卡片与表格的切换边界；但本轮目标只是修复 `820px` 的真实截断，当前复杂度已下降。
- 处理结果：本轮已完成完整增量范围的提交归类、业务建模、真实多视口取证、入口依赖污染检查、低风险减法修复和前端验证，因此把 `last_audited_commit` 推进到 `048ce7494b660cd0a559e6d1514c5169f38b37fd`，`pending_or_skipped_ranges` 保持为空。

### 2026-07-05（第十一轮）

- 完整范围：`1a2511806db719879b192d325e95f4df3642b77a..3aea6f94afbccf3ce35b4207a6f9e0111ce186fc`
- 覆盖提交：`3aea6f94afbccf3ce35b4207a6f9e0111ce186fc`
- 前端入口提交：`3aea6f94afbccf3ce35b4207a6f9e0111ce186fc`
- 入口如何牵引到旧问题：这次提交同时触达 `fanxiu/data-annotation`、`frontend/src/api/notes.ts`、`SharedNoteEditor`、`NoteDetailPanel`、`ListNotes`、`cluster/files` 和 `attendance/orders`。真正被牵出的旧问题落在 `notes/center?tab=list`：提交把笔记 taxonomy 归一逻辑继续收向 `primary_category / note_form / lifecycle_stage` 后，真实列表页首屏开始直接暴露内部 palette key，例如 `custom_mmxc...`、`旧色E6A23C`，把内部编码当成一级分类事实展示。
- 本轮减法：在 [`frontend/src/standard/notes/center/ListNotes.vue`](D:/home/chenkunze/slns/codeyun/frontend/src/standard/notes/center/ListNotes.vue) 补齐与 `CalendarNotes` 一致的 `ensureNoteTypePaletteLoaded()` 预载，让列表页首屏直接使用人类可读的分类标签，而不是等用户进入编辑器后才偶然加载 palette。没有新增控件、字段、解释区或状态，只把泄漏出的内部编码收回到已有分类模型后面。
- 信息量保持：`notes/list` 仍保留标题、分类、形态、阶段、权重、私密和起始时间；减少的是“把 palette key 暴露给用户”的无效噪音。`fanxiu/data-annotation`、`cluster/files`、`attendance/orders` 本轮只做同链路复验，没有额外扩写新概念。
- 概念图/线框图：报告中的 Mermaid 把这轮链路收回到 `笔记 taxonomy -> 分类 palette -> 列表首屏标签`，证明一级列表应承载“用户可读分类”，而不是“内部 key”。
- 报告路径：`C:/Users/kzche/AppData/Local/Temp/codeyun/ui-design-audit/2026-07-05-frontend-design-3aea6f94/report.md`
- 验证：复用本地 `5173/8000` 开发环境，分别对 `fanxiu/data-annotation`、`cluster/files`、`notes/center?tab=list`、`attendance/orders` 完成宽屏 `1600x1000`、普通桌面 `1366x900`、窄屏 `820x1180` 三视口取证，12 张截图与 `evidence.json` 写入同目录；所有页面 `bodyOverflowX = 0`、`documentOverflowX = 0`。`notes/list` 首屏分类列从 `custom_mmxc...` / `旧色...` 恢复为 `CodeYun/资源`、`CodeYun/集群`、`考勤`、`后勤` 等可读标签。附加静态校验：`npm run typecheck --prefix frontend`、`npm run build --prefix frontend` 通过。
- 根因分层：`notes/list` 问题属于前端状态投影 / 基础数据准备缺失，不是后端 taxonomy 建模错误；`ListNotes` 之前直接读取 `getNodeTypeConfig()`，但自身没有像 `CalendarNotes` 一样先加载 category palette，所以首屏只能退化成原始 key。`fanxiu/data-annotation`、`cluster/files`、`attendance/orders` 本轮未发现新的表现层或工程边界回归。
- 剩余风险：当前 `fanxiu/data-annotation` 样本的识别矩阵仍处于 `未生成 145/148 节点` 状态，所以本轮只能验证“识别运维页面在三视口下稳定打开且不会因新增 score 字段回退”，没有拿到带真实分数边标签的现场截图；这属于低风险未覆盖点，不影响关闭当前提交范围。
- 处理结果：本轮已完成完整增量范围的提交归类、业务建模、真实多视口取证、低风险修复和前端验证，因此把 `last_audited_commit` 推进到 `3aea6f94afbccf3ce35b4207a6f9e0111ce186fc`，`pending_or_skipped_ranges` 保持为空。

### 2026-07-04（第十轮）

- 完整范围：`7a29a6239a99dad60e9fcf54285642f0433d854b..1a2511806db719879b192d325e95f4df3642b77a`
- 覆盖提交：`1a2511806db719879b192d325e95f4df3642b77a`
- 前端入口提交：`1a2511806db719879b192d325e95f4df3642b77a`
- 入口如何牵引到旧问题：这次提交同时改了 `fanxiu/data-annotation`、`fanxiu.ts`、`vite.config.ts` 与锁文件。真正需要复核的不是“多了识别运维能力”，而是它是否继续长成第二条左侧模式轴；同时因为命中了 Vite 和依赖锁文件，也必须补做入口依赖污染检查，确认局部重依赖没有反向拖脏主入口。
- 本轮减法：提交方向成立，`识别运维` 已从并列 tab 收回到单一 `assetTreeViewMode` 里，左侧工作台从“两套模式状态”收回到“一套基础枚举”。runtime 页继续保持 `内核 / 守护 / 作业 / 日志` 的基础闭环，没有把识别运维再混成一级常驻结构。本轮不改仓库源码，只关闭审计与验证闭环。
- 信息量保持：标注页仍保留资产树、识别层、识别运维、关系图/编辑与回到画面的操作能力；runtime 仍保留守护、作业、运行状态与 cell 日志。减少的是重复概念，不是业务能力。
- 概念图/线框图：本轮报告里的 Mermaid 继续把链路收回到 `数据标注入口 -> 左侧单一模式轴 -> 主工作区 / runtime`。它证明 `识别运维` 是左侧视图模式，而不是另一条一级导航轴。
- 报告路径：`C:/Users/kzche/AppData/Local/Temp/codeyun/ui-design-audit/2026-07-04-frontend-design-1a251180/report.md`
- 验证：为避开主仓库当前未提交改动，本轮继续在 detached worktree 构建 clean `1a251180` 快照，并通过临时静态 + 反向代理服务 `http://127.0.0.1:4273 -> http://127.0.0.1:18000` 做真实取证。`data-annotation`、`识别运维`、`runtime` 三条链路都补齐了宽屏 / 桌面 / 窄屏截图，`bodyOverflowX = 0`、`documentOverflowX = 0`。`runtime` 初次出现的 `/403` 已确认只是取证脚本先缓存匿名 `feature-access-context`、随后临时 JWT 又过期造成的假阳性；修正为首屏前注入有效 token 后，`/api/auth/me` 返回 `200`、`/api/access/context` 为 superuser、runtime 页三视口均正常打开。入口污染检查结果：`dist/index.html` 未预加载 `handsontable-vendor` / `file-viewer-vendor` / `pdfjs-vendor` / `formula-vendor`；`dist/assets/main-BUAvxJVE.js` 顶层 import 未静态拉入这些局部重依赖；真实 `fanxiu/data-annotation` 与 `fanxiu/data-annotation/runtime` 页面资源列表中也未出现这些 chunk。附加静态校验：`npm run build --prefix frontend`、`npm run typecheck --prefix frontend` 通过。
- 根因分层：本轮核心仍是前端状态投影 / 工作台模型收敛，不是后端 DTO 问题；`vite.config.ts` 与锁文件改动属于前端工程边界问题，本轮污染检查未发现主入口被拖脏。
- 剩余风险：主仓库 `frontend/src/standard/fanxiu/data-annotation/page.vue`、`frontend/vite.config.ts`、`frontend/src/api/fanxiu.ts` 仍有未提交工作区改动，但它们不属于本轮 commit 快照证据；后续若这些本地改动形成新提交，需要按新的完整增量范围重新巡检。
- 处理结果：本轮已完成完整增量范围的提交归类、业务建模、概念图、三视口真实取证和入口依赖污染检查，没有发现需要继续自动修复的新 UI 回归，因此把 `last_audited_commit` 推进到 `1a2511806db719879b192d325e95f4df3642b77a`，`pending_or_skipped_ranges` 保持为空。

### 2026-07-04（第九轮，关闭 pending）

- 完整范围：`a9116dabfe300755ced35ffce3c6f7d4f269bbfa..7a29a6239a99dad60e9fcf54285642f0433d854b`
- 覆盖提交：`7a29a6239a99dad60e9fcf54285642f0433d854b`
- 前端入口提交：`7a29a6239a99dad60e9fcf54285642f0433d854b`
- 入口如何牵引到旧问题：入口仍是 `fanxiu/data-annotation` 左侧工作台模型膨胀。上一轮已经完成提交归类、概念建模和同链路页面巡检，只差因为主仓库同文件未提交回收改动而缺失的纯 commit 宽屏 / 桌面证据；本轮仅为关闭这段 pending，复核 `attendance/orders` 同轮依赖整理没有衍生新的 UI 回退，并补做 `handsontableOrderSetup` 触发的入口依赖污染检查。
- 本轮减法：不改主仓库源码，只在 detached worktree 里用干净 `7a29a623` 前端快照完成构建与截图，证明 `识别运维` 只是左侧工作台的另一种模式，不该和 `资产树 / 场景归纳` 并列成长为第二条模式轴。`attendance/orders` 继续保持上一轮已确认的“筛选 -> 工单/历史 -> 分页”单层闭环，没有被这次 closeout 扩写新概念。
- 信息量保持：`data-annotation` 仍保留直播画面、资产树、场景归纳、识别运维、节点/边统计与回到画面的操作能力；减少的是同一左侧工作模式被两套控件重复表达。`attendance/orders` 仍保留筛选、工单列表、历史记录与分页，没有新增冗余说明或常驻控件。
- 概念图/线框图：沿用上一轮报告中的 Mermaid 单轴工作台模型；本轮真实宽屏 / 桌面 / 窄屏截图已经补齐，可与概念图一一对应。
- 报告路径：`C:/Users/kzche/AppData/Local/Temp/codeyun/ui-design-audit/2026-07-04-frontend-design-7a29a623-closeout/report.md`
- 验证：为避免主仓库脏工作区污染证据，本轮在 detached worktree 构建生产 `dist`，再通过临时静态 + 反向代理服务 `http://127.0.0.1:4273 -> http://127.0.0.1:8000` 完成登录与页面取证。新增有效截图为 `data-annotation-wide.png`、`data-annotation-desktop.png`、`data-annotation-narrow.png`、`data-annotation-desktop-recognition-ops.png`；上一轮 `attendance-orders-wide.png`、`attendance-orders-desktop.png`、`attendance-orders-narrow.png` 继续有效。入口污染检查结果：`dist/index.html` 未预加载 `handsontable-vendor` / `file-viewer-vendor` / `pdfjs-vendor` / `formula-vendor`；`dist/assets/main-9w4PXgYy.js` 顶层 import 未静态引入这些局部重依赖；真实 `fanxiu/data-annotation` 页面已加载脚本里也未出现 `handsontable`、`file-viewer`、`attendance`、`formula`、`pdfjs` 相关 chunk。
- 根因分层：`data-annotation` 问题仍属于前端状态投影 / 工作台模型膨胀，不是后端 DTO 或表现层细节；`attendance/orders` 这轮相关文件更多是重依赖装配边界与路由级表格依赖整理，本轮污染检查未发现主入口被拖脏。
- 剩余风险：主仓库当前 `frontend/src/standard/fanxiu/data-annotation/page.vue` 仍存在同方向未提交回收改动，但它现在应视为后续新的候选增量，而不是继续阻塞 `7a29a623` 的关闭；如果这份本地回收随后提交，下一轮要按新的提交范围重新巡检。
- 处理结果：本轮已补齐上一轮缺失的纯 commit 截图证据，并完成需要的入口依赖污染检查，因此清空 `pending_or_skipped_ranges`，把 `last_audited_commit` 推进到 `7a29a6239a99dad60e9fcf54285642f0433d854b`。

### 2026-07-04（第八轮）

- 完整范围：`a9116dabfe300755ced35ffce3c6f7d4f269bbfa..7a29a6239a99dad60e9fcf54285642f0433d854b`
- 覆盖提交：`7a29a6239a99dad60e9fcf54285642f0433d854b`
- 前端入口提交：`7a29a6239a99dad60e9fcf54285642f0433d854b`
- 入口如何牵引到旧问题：这次提交同时触达 `fanxiu/data-annotation`、`attendance/orders`、`handsontableOrderSetup` 与凡修前端联调 API。真正被牵出的旧问题不在新功能数量，而在左侧工作台模型：提交为了补“识别运维”入口，在 `data-annotation` 左侧再叠加了一层 `annotation-panel-tabs`，使同一事实同时由 `leftWorkbenchTab` 和 `assetTreeViewMode` 两套状态表达；`attendance/orders` 则只是同轮路由级表格依赖收口，需要做页面复验但没有暴露同等模型膨胀。
- 本轮减法：不直接改仓库源码。报告把 `data-annotation` 左侧结构收敛为单一模式轴：`业务树 / 场景树 / 识别运维` 应属于同一左侧工作台枚举，而不是先切 tab、再切树视角。当前工作区同一文件已经存在同方向未提交回收，因此本轮不重复改写冲突区域。
- 信息量保持：标注页仍保留画面、资产树、识别运维、历史矩阵、节点/边统计和回到画面的操作能力；减少的是“同一个左侧面板模式被两套控件表达”的重复概念。`attendance/orders` 仍保留筛选、工单列表、历史表格与分页；本轮未发现新的布局回退。
- 概念图/线框图：报告里的 Mermaid 对比了“`leftWorkbenchTab + assetTreeViewMode` 双轴”与“单轴左侧模式”两种投影，证明 `识别运维` 应回收到基础工作台模型，而不是长成新的常驻一级 tab。
- 报告路径：`C:/Users/kzche/AppData/Local/Temp/codeyun/ui-design-audit/2026-07-04-frontend-design-7a29a623/report.md`
- 验证：复用并重启本地 `5173/8000` 开发环境，对 `attendance/orders`、`fanxiu/data-annotation` 同链路页面以及 `cluster/runtime` 关键旧页完成真实页面取证。`attendance/orders` 已补齐宽屏 `1600x1000`、普通桌面 `1366x900`、窄屏 `820x1180` 三视口，`bodyOverflowX = 0`，未见新溢出；`fanxiu/data-annotation` 已拿到 `识别运维` 真实页与窄屏主工作台截图，但默认 live 工作台的宽屏/桌面实图受同文件未提交改动与会话态漂移影响，不能作为纯 commit 闭环证据。本轮提交不涉及重依赖、Vite/Rollup 插件、`manualChunks` 或公开入口，因此未触发入口依赖污染检查。
- 根因分层：`data-annotation` 问题属于前端状态投影 / 工作台模型膨胀，不是后端 DTO 或样式细节问题；`attendance/orders` 变更属于前端工程层的局部依赖整理，本轮未发现新的表现层问题。`cluster/runtime` 截到的部分 500/功能权限噪音属于既有环境现象，与本轮提交无直接因果。
- 剩余风险：当前工作区在 `frontend/src/standard/fanxiu/data-annotation/page.vue` 已有同链路未提交回收改动，且默认 live 工作台截图会混入这份本地状态；如果直接推进游标，下一轮将失去对 `7a29a623` 原始提交形态的纯证据。需要等待该文件的本地改动稳定或并入提交后，再关闭这段范围。
- 处理结果：本轮已完成完整增量范围的提交归类、业务建模、概念图和多页实拍，但由于同文件同链路未提交改动使 `data-annotation` 无法形成纯 commit 闭环证据，暂不推进 `last_audited_commit`；`pending_or_skipped_ranges` 记录 `a9116dab..7a29a623`，待本地改动稳定后优先关闭。

### 2026-07-04（第七轮）

- 完整范围：`63724108d7334bfdc4285b92db49941516024555..a9116dabfe300755ced35ffce3c6f7d4f269bbfa`
- 覆盖提交：`a9116dabfe300755ced35ffce3c6f7d4f269bbfa`
- 前端入口提交：`a9116dabfe300755ced35ffce3c6f7d4f269bbfa`
- 入口如何牵引到旧问题：这次提交同时触达 `fanxiu/data-annotation/runtime`、`fanxiu/data-annotation`、`cluster/storage`、`FileExplorer` 和 `vite.config.ts`。其中 `runtime` 延续了前几轮一直在收敛的同一条主闭环：一级页只保留“现在是否在跑、哪些守护开启、哪些作业会触发”；`FileExplorer + vite.config` 则把 `GenericFileViewer` 及邮件/图表等重依赖继续压回局部异步 chunk。因此本轮既要复核 `runtime` 的减法是否真实成立，也必须额外执行入口依赖污染检查，确认公开入口和主入口没有被局部能力反向拉脏。
- 本轮减法：不新增仓库代码改动，只做完整复验。真实页面确认 `fanxiu/data-annotation/runtime` 首屏已经从一组混杂的运行控制收回到更基础的三层模型：`内核开关 -> 守护列表 -> 作业列表`。`fanxiu/data-annotation` 也继续保持上轮收掉“无关系节点空图工作区”后的结构，没有把空关系图区带回一级编辑区。`cluster/storage` 当前 canonical path 已固定重定向到 `/cluster/treesize`，首屏保留设备/路径/目录树两层基础模型，没有再额外引入并列工作台。
- 信息量保持：`runtime` 仍保留守护、作业、调度器与工程入口；`data-annotation` 仍保留画面、资产树、场景归纳和关系图（仅在有关系边时出现）；`cluster/storage` 仍保留目录树/重复文件分析；`FileExplorer` 仍保留重文件预览能力。减少的是一级运行页的重复控制，以及主入口对局部重依赖的错误耦合，不是减少业务能力。
- 概念图/线框图：报告里的 Mermaid 把这次提交拆成两条收敛链路：`runtime` 把首屏收回到状态事实与下一步动作，`file-viewer` 把重依赖收回到局部异步边界。它证明这轮的“前端设计巡检”不仅是看视觉，也要看主入口对象边界有没有被打穿。
- 报告路径：`C:/Users/kzche/AppData/Local/Temp/codeyun/ui-design-audit/2026-07-04-frontend-design-a9116dab/report.md`
- 验证：复用本地 `5173/8000` 开发环境，对 `fanxiu/data-annotation/runtime`、`fanxiu/data-annotation`、`cluster/storage`（实际落到 `/cluster/treesize`）以及公开页 `https://code4101.com/sheet/58855?view=lookup` 完成宽屏 `1600x1000`、普通桌面 `1366x900`、窄屏 `820x1180` 截图，证据见同目录 `evidence.json` 与 12 张 `*.png`。同时执行 `npm run build --prefix frontend`，检查 `frontend/dist/index.html` 与 `frontend/dist/assets/main-*.js`，确认没有预加载或顶层 import `file-viewer-vendor` / `GenericFileViewer` / `postal-mime` / `@kenjiuno/msgreader` / `billboard.js`；`GenericFileViewer` chunk 则按预期单独引用 `file-viewer-vendor-*`。公开 `sheet lookup` 三视口都能离开 shell loading，资源列表未误拉 `file-viewer` 相关 chunk。
- 根因分层：`fanxiu/data-annotation/runtime` 的问题属于前端状态投影，提交本身已经完成有效减法；`FileExplorer + vite chunks` 属于前端工程架构边界收敛；公开 `sheet lookup` 上的 `401` / websocket `404` 属于既有工程噪音，不是本轮局部依赖污染。
- 剩余风险：`fanxiu/data-annotation` 窄屏仍是既有“大画面优先、右侧树依赖下滚”的旧结构，但本轮提交没有加重它，也没有牵出可单独低风险收掉的新重复控件。公开 `sheet lookup` 的匿名态 `401/ws404` 控制台噪音仍值得后续单列工程清洁任务，但不影响本轮把游标推进。
- 处理结果：本轮已完成完整增量范围的提交归类、业务建模、入口依赖污染检查和真实多视口取证，没有发现需要继续自动修复的新 UI 回归，因此把 `last_audited_commit` 推进到 `a9116dabfe300755ced35ffce3c6f7d4f269bbfa`，`pending_or_skipped_ranges` 保持为空。

### 2026-07-04（第六轮）

- 完整范围：`90ed802e5c3660ae3a316d8c8a5620bdc411872e..63724108d7334bfdc4285b92db49941516024555`
- 覆盖提交：`63724108d7334bfdc4285b92db49941516024555`
- 前端入口提交：`63724108d7334bfdc4285b92db49941516024555`
- 入口如何牵引到旧问题：这次提交同时改了 `fanxiu/data-annotation`、`cluster/runtime` 和 `cluster/files`。`cluster/runtime` 只是延续上轮已经验证过的“表格按内容收口”方向，`cluster/files` 则是把 `GenericFileViewer` 改成异步加载，不改变页面信息架构。真正被牵出的旧问题落在 `fanxiu/data-annotation`：提交为标注页增加关系图与识别投影标记后，真实页面里的很多图片节点其实没有任何识别/跳转关系边，但一级编辑区前仍常驻一整块 224px 的关系图画布，只剩孤立节点和大块留白。
- 本轮减法：在 [`frontend/src/standard/fanxiu/data-annotation/page.vue`](D:/home/chenkunze/slns/codeyun/frontend/src/standard/fanxiu/data-annotation/page.vue) 只在选中图片存在任一关系边时才渲染 `scene-relation-graph` 与 resizer。没有新增按钮、入口、字段、状态或说明；有关系边的节点仍保留原关系图能力。
- 信息量保持：`fanxiu/data-annotation` 仍保留资产树、场景归纳、关系图、图片对比、图片/shape 编辑与识别投影标记；减少的是“无关系边节点也要先经过空图结构工作区”的重复层级。`cluster/runtime` 仍保留设备 tabs、服务/作业状态与资源监控；`cluster/files` 仍保留文件浏览与预览。
- 概念图/线框图：本轮报告里的 Mermaid 直接把标注页关系图区收敛到一个更基础的判断：`选中图片 -> 若存在关系边则显示关系图 -> 否则直接进入编辑`。它证明关系图属于辅助诊断，而不是所有图片都必须经过的一级工作区。
- 报告路径：`C:/Users/kzche/AppData/Local/Temp/codeyun/ui-design-audit/2026-07-04-frontend-design-63724108/report.md`
- 验证：复用本地 `5173/8000` 开发环境，对 `fanxiu/data-annotation`、`cluster/runtime`、`cluster/files` 完成宽屏 `1600x1000`、普通桌面 `1366x900`、窄屏 `820x1180` 截图。`fanxiu/data-annotation` 用 `#260 0260.png` 验证无关系边节点不再渲染图结构区，用 `#15 登录` 验证有关系边节点仍保留关系图，DOM 采样到 `edgeCount = 1`、`nodeCount = 2`。同时 `npm run typecheck --prefix frontend`、`npm run build --prefix frontend` 通过。
- 根因分层：`fanxiu/data-annotation` 是前端状态投影 / 信息层级问题；关系能力本身成立，但前端把“有时成立的辅助诊断”升级成了“总是成立的一级工作区”。`cluster/runtime` 与 `cluster/files` 本轮未发现新的表现层或投影问题。
- 剩余风险：`fanxiu/data-annotation` 当前仍保留“当前 tab 无边但另一类关系可能有边”时的空图状态；本轮只收掉了“完全无关系边却常驻空画布”的明确回归，没有继续改 tab 自动切换策略。该风险与本轮入口强相关但优先级低于已修复问题。
- 处理结果：本轮已完成完整增量范围的提交归类、页面级建模、真实多视口取证、低风险减法修复和前端验证，因此把 `last_audited_commit` 推进到 `63724108d7334bfdc4285b92db49941516024555`，`pending_or_skipped_ranges` 保持为空。

### 2026-07-03（第五轮）

- 完整范围：`da1575be28606b8d2a9199603cb06f3b418e778b..90ed802e5c3660ae3a316d8c8a5620bdc411872e`
- 覆盖提交：`90ed802e5c3660ae3a316d8c8a5620bdc411872e`
- 前端入口提交：`90ed802e5c3660ae3a316d8c8a5620bdc411872e`
- 入口如何牵引到旧问题：这次提交同时碰了 `cluster/runtime`、`attendance/orders` 和 `NoteCopyDialog`。其中 `attendance/orders` 与复制对话框的方向都延续了前几轮的减法：退款历史继续静默待载，无分类节点继续保留空语义。真正被牵出的旧问题落在 `cluster/runtime`：本轮后端/运行时调整并不要求前端扩容，但提交顺手把服务/作业状态表从“按内容收口”改回了“填满容器”，让运行状态表再次出现大块表内留白。
- 本轮减法：在 [`frontend/src/standard/cluster/tasks/page.vue`](D:/home/chenkunze/slns/codeyun/frontend/src/standard/cluster/tasks/page.vue) 把服务表和作业表的 `table-layout` 从 `fixed` 收回到 `auto`，并把 `.runtime-table` 从 `width: 100%` 收回到 `width: max-content`。没有新增功能、入口、字段、状态或解释；`attendance/orders` 与 `NoteCopyDialog` 只做复验，不追加新控件。
- 信息量保持：`cluster/runtime` 仍保留设备切换、服务/作业状态、状态按钮和下次触发；减少的是把右侧留白塞进表内的表现层回归。`attendance/orders` 仍保留查询输入、退款历史和分页；`NoteCopyDialog` 仍保留标题、时间、权重、分类、形态、阶段、关联关系与内容编辑，只是在无分类节点上稳定显示 `分类:-`。
- 概念图/线框图：报告里的 Mermaid 把三条入口统一收敛成一个基础判断：`对象/状态事实先成立，再展开下游工作台；不要为内部节奏或空语义新增解释型常驻控件`。ASCII 线框图则直接对比了 `cluster/runtime` 的“表内空白”与“表外空白”。
- 报告路径：`C:/Users/kzche/AppData/Local/Temp/codeyun/ui-design-audit/2026-07-03-frontend-design-90ed802e/report.md`
- 验证：复用本地 `5173/8000` 开发环境，对 `cluster/runtime`、`attendance/orders` 与 `/doc/55491 -> 复制节点` 完成真实页面取证。`cluster/runtime` 宽屏与普通桌面表宽都收回到 `910px`，窄屏收回到 `568px`，三视口 `bodyOverflowX = 0` 且 `hasNextColumn = true`；`attendance/orders` 三视口都满足 `historyTableExists = true`、`pendingShellExists = false`、`historyPaginationExists = true`、`bodyOverflowX = 0`；复制节点对话框满足 `dialogTitle = 复制节点`、`categoryLine` 含 `分类:-`、`bodyOverflowX = 0`。同时 `npm run typecheck --prefix frontend`、`npm run build --prefix frontend` 通过。
- 根因分层：`cluster/runtime` 是前端表现层 / 状态表投影回归；`attendance/orders` 与 `NoteCopyDialog` 本轮未发现新的 UI 复杂度问题。
- 剩余风险：复制节点对话框本轮用 `/doc/55491` 入口完成真实复验，尚未再补 `notes/center` 详情面板里的同组件第二处截图；但组件本体一致，本轮改动也未触发布局重排，风险较低。
- 处理结果：本轮已完成完整增量范围的提交归类、页面级建模、真实多视口取证、低风险减法修复和前端验证，因此把 `last_audited_commit` 推进到 `90ed802e5c3660ae3a316d8c8a5620bdc411872e`，`pending_or_skipped_ranges` 保持为空。

### 2026-07-03（第四轮）

- 完整范围：`4422a85ea5cc237017e9080fd5373a09e4aed63e..da1575be28606b8d2a9199603cb06f3b418e778b`
- 覆盖提交：`da1575be28606b8d2a9199603cb06f3b418e778b`
- 前端入口提交：`da1575be28606b8d2a9199603cb06f3b418e778b`
- 入口如何牵引到旧问题：这次提交同时动了 `cluster/files`、`cluster/storage -> 重复文件` 和 `attendance/orders` 的同一条“对象先成立，再展开下游工作台”链路。真实页面复验后，`cluster/storage` 的前置条件收口方向成立，`cluster/files` 当前样本也未暴露新的布局回归；真正继续被牵出的旧问题落在 `attendance/orders`：为了把退款工作区优先呈现，页面首屏又多出一块解释为什么退款历史还没加载完的虚线提示框。它不承载新的业务事实，只是在解释内部加载节奏，因此可以继续收回。
- 本轮减法：不新增功能、入口、字段或状态。在 [`frontend/src/standard/attendance/orders/page.vue`](D:/home/chenkunze/slns/codeyun/frontend/src/standard/attendance/orders/page.vue) 新增 `refundHistoryPending` 只作为静默待载阶段的内部状态；一级页面删除 `首屏已优先加载退款工作区，退款历史稍后补齐。` 这块虚线解释框，改成沿用同一张历史卡片里的简洁待载壳层。`cluster/storage` 的 `重复文件` 子页则按提交方向复验为：根目录下只保留一句前置条件提示，不再提前显示零统计分析器壳层。
- 信息量保持：`attendance/orders` 仍保留查询输入、退款执行、退款结果、退款历史与分页；减少的是解释内部延迟加载的额外说明区。`cluster/storage` 仍保留重复文件规则、统计和结果，只是继续收回到进入具体磁盘或目录之后。`cluster/files` 当前样本仍保留媒体工作台与普通文件表，没有因为本轮巡检再增加额外概念。
- 概念图/线框图：报告里的 Mermaid 把 `cluster/files`、`cluster/storage`、`attendance/orders` 都收敛到同一个基础模型：`前置对象成立 -> 再展开下游工作台`；ASCII 线框图则直接对比了退款页“解释内部节奏”与“静默待载”的差异。
- 报告路径：`C:/Users/kzche/AppData/Local/Temp/codeyun/ui-design-audit/2026-07-03-frontend-design-da1575be/report.md`
- 验证：复用本地 `5173/8000` 开发环境，并重新加载前端代码后，对 `cluster/files`、`cluster/storage`、`attendance/orders` 完成宽屏 `1600x1000`、普通桌面 `1366x900`、窄屏 `820x1180` 三视口截图，证据见 `evidence.json` 与对应 `*.png`。`cluster/files` 三视口都满足 `bodyOverflowX = 0`、`hasMediaToolbar = true`、`hasOtherFiles = true`；`cluster/storage` 三视口都满足 `activeTab = 重复文件`、`duplicatePrerequisite = 请先进入具体磁盘或目录，再分析重复文件。`、`duplicateSummaryExists = false`；`attendance/orders` 三视口 settled 状态都满足 `placeholderExists = false`、`hasHistoryTable = true`、`hasPagination = true`、`bodyOverflowX = 0`。同时 `npm run typecheck --prefix frontend`、`npm run build --prefix frontend` 通过。
- 根因分层：`cluster/storage` 仍是前端信息层级问题，本轮只做复验；`cluster/files` 当前样本未暴露新的高优先级问题；`attendance/orders` 则是前端交互节奏问题，内部延后加载一度被升级成了一级说明区。
- 剩余风险：`cluster/files` 当前真实样本路径 `D:\home\chenkunze\data` 仍含可预览媒体，未再次构造“只有普通文件、没有媒体”的专门目录样本；但本轮入口链路在真实页面中未复现新的 UI 回归。当前工作树也仍有用户未提交的后端改动，本轮未触碰这些文件。
- 处理结果：本轮已完成完整增量范围的提交归类、页面级建模、真实多视口取证、低风险减法修复和前端验证，因此把 `last_audited_commit` 推进到 `da1575be28606b8d2a9199603cb06f3b418e778b`，`pending_or_skipped_ranges` 保持为空。

### 2026-07-03（第三轮）

- 完整范围：`64b872eb783ae8b4a7706650a121f3b14ac7ed07..4422a85ea5cc237017e9080fd5373a09e4aed63e`
- 覆盖提交：`686bba369cb42e237e9e2632c5c9fae0fa1e80d3`、`4422a85ea5cc237017e9080fd5373a09e4aed63e`
- 前端入口提交：`686bba369cb42e237e9e2632c5c9fae0fa1e80d3`、`4422a85ea5cc237017e9080fd5373a09e4aed63e`
- 入口如何牵引到旧问题：这两次提交都在把文件/预览/笔记工作台做厚：`cluster/files` 把普通文件预览并入媒体工作区，`cluster/treesize` 把重复文件分析独立成子页，`notes/center` 继续打通共享笔记与文件预览。真实页面复验后，暴露出来的不是“功能少”，而是同一条对象链路上的旧问题：目录里没有可预览媒体时，媒体工作台的侧栏、视图切换、缩放、分页仍先出现；还停在设备根目录时，重复文件分析页的规则栏、零统计和分页也已经常驻。两者本质上都是“对象事实未成立，下游工作台先出现”，因此本轮沿同一业务链路把这两个旧壳层一起收回，而不是停留在提交表面的新增预览能力。
- 本轮减法：在 [`frontend/src/standard/cluster/files/page.vue`](D:/home/chenkunze/slns/codeyun/frontend/src/standard/cluster/files/page.vue) 只把真正可预览的 `image / media / pdf` 送入媒体工作台，并在当前目录无可预览媒体时隐藏媒体专用 chrome，让普通文件只留在“其他文件”表里；在 [`frontend/src/standard/cluster/storage/StorageDuplicatePane.vue`](D:/home/chenkunze/slns/codeyun/frontend/src/standard/cluster/storage/StorageDuplicatePane.vue) 把“重复文件”子页收回成前置条件态，只有进入具体磁盘或目录后才显示分析器，未进入范围时只保留一句 `请先进入具体磁盘或目录，再分析重复文件。`
- 信息量保持：普通文件仍可见、仍可预览；重复文件规则、统计和结果也都仍保留，只是不再在对象未成立时提前占据一级页面。减少的是“普通文件被双重投影”为媒体卡片 + 普通文件表，以及“无范围时的零统计分析器壳层”。
- 概念图/线框图：报告里的 Mermaid 和 ASCII 线框图都把页面先拆成 `当前对象是否成立 -> 若否只显示前置条件 -> 若是再展开下游工作台`，用同一个基础模型解释 `cluster/files` 与 `cluster/treesize` 的减法。
- 报告路径：`C:/Users/kzche/AppData/Local/Temp/codeyun/ui-design-audit/2026-07-03-frontend-design-4422a85/report.md`
- 验证：复用本地 `5173/8000` 开发环境，Chrome 已登录态复验 `cluster/files` 与 `cluster/treesize`；随后用临时无头 Chrome 注入本地 JWT，在宽屏 `1600x1000`、普通桌面 `1366x900`、窄屏 `820x1180` 三视口补齐 `cluster/files`、`cluster/treesize`、`notes/center?tab=list` 截图与结构探针，证据见 `evidence.json`。`cluster/files` 三视口都满足 `hasMediaToolbar = false`、`hasOtherFiles = true`、`inlineEmptyText = 上方列出普通文件。`；`cluster/treesize` 三视口都满足 `hasDuplicatePrerequisite = true`、`hasDuplicateZeroSummary = false`；`notes/center?tab=list` 三视口无 `bodyOverflowX` 与 console `warn/error`，仅保留既有表格内部横向滚动。`npm run typecheck --prefix frontend`、`npm run build --prefix frontend` 通过。
- 根因分层：`cluster/files` 是前端状态投影问题，普通文件被重复映射到媒体工作台与普通文件表两个层面；`cluster/treesize` 是前端信息层级问题，下游分析器在前置条件未成立时提前常驻；`notes/center` 本轮真实页面复验未发现需要继续自动修复的高优先级问题。
- 处理结果：本轮已完成完整增量范围的提交归类、概念建模、真实多视口取证、低风险减法修复和前端验证，因此把 `last_audited_commit` 推进到 `4422a85ea5cc237017e9080fd5373a09e4aed63e`，`pending_or_skipped_ranges` 保持为空。

### 2026-07-03（第二轮）

- 完整范围：`fd0c1918d535a5a68d17f1016cc3717bf309f1c6..64b872eb783ae8b4a7706650a121f3b14ac7ed07`
- 覆盖提交：`64b872eb783ae8b4a7706650a121f3b14ac7ed07`
- 前端入口提交：`64b872eb783ae8b4a7706650a121f3b14ac7ed07`
- 入口如何牵引到旧问题：这次提交把 `notes/center?tab=list` 的 `后端筛选` 收成摘要条，试图把一级页面从“常驻规则编辑器”拉回“当前工作集事实 + 节点列表”的主闭环。真实页面复验后发现旧问题并不在视觉皮肤，而在交互闭环：一旦点 `编辑规则`，规则栏就会永久停留展开态，等于把旧复杂度重新带回一级页面。因此本轮继续沿同一页面、同一筛选模型追到 `ListNotes.vue` 的展开状态管理，而不是停留在提交表面的折叠外观。
- 本轮减法：在 [`frontend/src/standard/notes/center/ListNotes.vue`](D:/home/chenkunze/slns/codeyun/frontend/src/standard/notes/center/ListNotes.vue) 让 `applyDataProgram()` 与 `resetDataProgram()` 在完成后都把 `backendFilterExpanded` 置回 `false`。没有新增任何常驻控件，只让已经引入的摘要态真正成为稳定默认，而不是一次性的首屏幻觉。
- 信息量保持：用户仍然能看到当前加载范围摘要、进入规则链、执行后端加载、浏览节点列表、进入详情编辑；减少的是“规则栏展开后长期霸占一级页面”的重复复杂度。
- 概念图/线框图：报告里的 Mermaid 和 ASCII 线框图把 `一级列表页`、`后端筛选摘要`、`临时规则编辑器` 三层分开，并明确 `执行/恢复默认 -> 回到摘要态` 才是这个模型成立的闭环。
- 报告路径：`C:/Users/kzche/AppData/Local/Temp/codeyun/ui-design-audit/2026-07-03-frontend-design-64b872eb/report.md`
- 验证：复用本地 `5173/8000` 开发环境，authenticated 打开 `notes/center?tab=list`；先记录 `initial`、`afterEdit`，再验证 `afterReset.backendCollapsed = true` 与 `afterApply.backendCollapsed = true`。三视口宽屏 `1600x1000`、普通桌面 `1366x900`、窄屏 `820x1180` 截图与结构探针写入 `evidence.json`，三视口 `bodyOverflowX = 0`，console `warn/error` 为空。`npm run typecheck --prefix frontend`、`npm run build --prefix frontend` 通过。
- 根因分层：这是前端状态投影问题，不涉及后端 API 投影或业务建模债务；提交已经做了“默认折叠”的第一步，但还缺少“执行后收回摘要态”的第二步。
- 处理结果：本轮已完成完整增量范围的提交归类、概念建模、真实多视口取证、低风险减法修复和前端验证，因此把 `last_audited_commit` 推进到 `64b872eb783ae8b4a7706650a121f3b14ac7ed07`，`pending_or_skipped_ranges` 保持为空。

### 2026-07-03

- 完整范围：`01cc40d747499fd9a9e44681a0ebcec9e815ee7c..fd0c1918d535a5a68d17f1016cc3717bf309f1c6`
- 覆盖提交：`fd0c1918d535a5a68d17f1016cc3717bf309f1c6`
- 前端入口提交：`fd0c1918d535a5a68d17f1016cc3717bf309f1c6`
- 入口如何牵引到旧问题：这次提交一边删除 `frontend/src/standard/cluster/tasks/page.vue` 里的整块 `设备代理` 工作台，一边从 `frontend/src/store/aiAppStore.ts` 删除 `device-agent` 业务项，同时在 `attendance/orders/page.vue` 给退款失败补本地错误留痕。三者都落在同一条“一级页面只保留状态事实和下一步动作，不把跨设备会话/模型配置/短暂异常丢进主界面”链路上，因此需要把 `cluster/runtime`、`AI配置` 和 `订单` 三个入口合并做页面级巡检，而不是只看单文件 diff。
- 本轮减法：不新增源码改动；通过真实页面复拍确认 `cluster/runtime` 首屏现在只保留 `服务 / 作业 / 资源监控` 主闭环，`AI配置 -> 业务` 列表里也不再出现 `设备代理`，避免同一能力同时占据 `运行管理` 和 `AI 配置` 两个长期一级入口。`attendance/orders` 新增的错误告警只在退款失败时出现在当前卡片内，属于局部留痕，不会形成新的常驻概念区。
- 信息量保持：设备切换、服务/作业状态、资源监控、AI 业务项绑定、退款查询、退款执行、退款历史与退款结果能力都还在；减少的是“跨设备代理会话/模型配置”误入运行首页与 AI 业务树的重复概念，以及退款失败只能靠 toast 瞬时消失的上下文丢失。
- 概念图/线框图：报告里的 Mermaid 把 `运行状态表`、`AI 业务配置`、`退款闭环` 三个对象边界拆开，并明确 `设备代理` 不再属于一级运行页或一级业务树。
- 报告路径：`C:/Users/kzche/AppData/Local/Temp/codeyun/ui-design-audit/2026-07-03-frontend-design-fd0c1918/report.md`
- 验证：复用本地 `5173/8000` 开发环境，对 `cluster/runtime`、`attendance/orders`、`tools/ai-config` 完成宽屏 `1600x1000`、普通桌面 `1366x900`、窄屏 `820x1180` 三视口截图，证据见 `evidence.json` 与对应 `*.png`；三个页面三种视口下 `bodyOverflowX = 0`，console `warn/error` 为空。`cluster/runtime` 在三视口下都满足 `hasDeviceAgentText = false`，只保留 `服务 / 作业 / 资源监控` 三个区块；`AI配置 -> 业务` 也满足 `hasDeviceAgentText = false`。`attendance/orders` 因涉及真实退款副作用，本轮未触发 `执行退款`，只对真实页面结构、历史表与错误告警挂载位置做静态/视觉验证；本轮未改源码，因此未运行 `npm run typecheck --prefix frontend` / `npm run build --prefix frontend`。
- 根因分层：`cluster/runtime` 与 `AI配置` 的主因仍是前端状态投影与入口边界收敛；`attendance/orders` 的新增告警属于前端交互闭环补足，不涉及后端数据投影或业务建模债务。
- 处理结果：本轮已完成完整增量范围的提交归类、业务建模、概念图和真实多视口取证，没有发现需要继续自动修复的新 UI 问题，因此把 `last_audited_commit` 推进到 `fd0c1918d535a5a68d17f1016cc3717bf309f1c6`，`pending_or_skipped_ranges` 保持为空。

### 2026-07-02

- 完整范围：`72a3d80aaabb730cf6277293a8487067dcb705ca..01cc40d747499fd9a9e44681a0ebcec9e815ee7c`
- 覆盖提交：`01cc40d747499fd9a9e44681a0ebcec9e815ee7c`
- 前端入口提交：`01cc40d747499fd9a9e44681a0ebcec9e815ee7c`
- 入口如何牵引到旧问题：本次提交把 `device-agent` 接口、`aiAppStore` 业务项和 `cluster/tasks(page.vue)` 内的整块设备代理工作台一起推到前台，直接撞上这条页面过去几轮刚刚收敛出来的核心边界：`cluster/runtime` 首页应该是“按设备看当前运行事实，再决定下一步”的状态表，而不是把跨设备请求、模型绑定、上下文输入和会话历史重新塞回一级页面。因此本轮不是无关扩散，而是沿着同一条“设备 -> 服务/作业 -> 队列 -> 资源监控”的闭环继续做减法审查。
- 本轮减法：不在冲突中的源码上继续自动改动；通过完整 diff、概念图和真实页面复拍确认 `01cc40d7` 提交里的 `设备代理` 区会把首页重新膨胀成混合工作台，而当前工作树未提交改动已经把这块常驻 UI 抽掉，页面重新回到 `服务 / 作业 / 队列记录 / 资源监控` 主闭环。换句话说，本轮减掉的是“运行首页常驻设备代理工作台”这个错误层级，不是减掉设备代理能力本身。
- 信息量保持：设备切换、服务/作业状态、队列记录、资源监控、跨设备代理能力和 AI 模型配置都仍可存在；减少的是把“配置 + 请求 + 结果 + 历史”四类语义同时塞进运行首页的重复概念。`device-agent` 模型绑定也不该同时出现在 `AI配置` 和 `运行管理` 两个长期入口里。
- 概念图/线框图：报告里的 Mermaid 明确区分 `运行状态表` 与 `设备代理会话` 两类对象；ASCII 线框图直接对比了“正确首页”与 `01cc40d7` 提交后的膨胀首页。
- 报告路径：`C:/Users/kzche/AppData/Local/Temp/codeyun/ui-design-audit/2026-07-02-frontend-design-01cc40d7/report.md`
- 验证：复用本地 `5173/8000` 开发环境，用 in-app Browser 打开 `http://127.0.0.1:5173/cluster/runtime`，补拍宽屏 `1600x1000`、普通桌面 `1366x900`、窄屏 `820x1180` 截图，并写出 `evidence.json`。真实页面因当前工作树已有未提交回收，截图看到的是简化后的在制状态，但 `evidence.json` 已证明页面当前只保留 `服务 / 作业 / 资源监控` 区块，`hasDeviceAgentText = false`、`hasDeviceAgentApiLabel = false`，同方向减法成立。由于本轮未改源码，未运行 `npm run typecheck --prefix frontend` / `npm run build --prefix frontend`。
- 根因分层：主因是前端状态投影问题，次因是页面对象边界轻度失守；还不是后端数据投影债务。当前问题更接近“把错误对象放进了错误首页”，不是接口不可用。
- 处理结果：本轮已完成完整增量范围的提交归类、业务建模、概念图/线框图和真实多视口取证。由于工作区里同一文件已经存在用户未提交的回收改动，为避免覆盖现场，本轮只生成报告并更新记忆，不直接改源码；但这不影响把 `last_audited_commit` 推进到 `01cc40d747499fd9a9e44681a0ebcec9e815ee7c`，`pending_or_skipped_ranges` 保持为空。

- 完整范围：`e1d79d0cf571e2dded7942e5900639ec0a05ce5e..72a3d80aaabb730cf6277293a8487067dcb705ca`
- 覆盖提交：`78774fb2ed3ccfd4a59b916bf1e7acf0bbdcb820`、`72a3d80aaabb730cf6277293a8487067dcb705ca`
- 前端入口提交：`78774fb2ed3ccfd4a59b916bf1e7acf0bbdcb820`、`72a3d80aaabb730cf6277293a8487067dcb705ca`
- 入口如何牵引到旧问题：上一轮已经把 `检查已返款` 从 `NoteSheetWorkspace` 组件层做完，但卡在 `/notes/sheets-manager -> 403`，无法确认它在真实工作表里的层级是否合适。新提交继续收缩 AI 工具菜单和 Fanxiu 链路，因此本轮沿着同一条“工作表上下文动作 + 工具入口减法”链路收口，而不是扩散到无关旧页。
- 本轮减法：不新增任何功能、状态、说明区或路由，也没有再改仓库代码。第一，转到当前账号可访问的 `http://127.0.0.1:5173/workbook/14?sheet=58855`，验证 `检查已返款` 只在 `已返款` 列上下文里出现，并以单个结果弹窗完成闭环。第二，展开 `fanxiu/data-annotation` 的 AI 工具菜单，确认 `Notebook/AGENTS` 已从一级入口消失，只保留当前仍成立的 AI 工具项。
- 信息量保持：工作表仍然保留完整退款核对能力，AI 工具仍保留 `配置 / Codex / AI聊天 / AI归纳 / AI提交 / CodeClaw`；减少的是“受限管理页阻塞导致的未闭环判断”和“废弃工具入口残留”两层噪音，而不是减少业务能力。
- 概念图/线框图：报告中的 Mermaid 把 `已返款列 -> 右键菜单 -> 检查已返款 -> 结果弹窗` 与 `AI工具 -> 保留项 / 删除项` 明确拆层；ASCII 线框图直接说明为什么 `检查已返款` 应该留在对象上下文，而不是升格为常驻工具栏。
- 报告路径：`C:/Users/kzche/AppData/Local/Temp/codeyun/ui-design-audit/2026-07-02-frontend-design-72a3d80a-closeout/report.md`
- 验证：复用本地 `5173/8000` 开发环境，对 `fanxiu/data-annotation` 与 `workbook/14?sheet=58855` 完成宽屏 `1600x1000`、普通桌面 `1366x900`、窄屏 `820x1180` 截图；`evidence.json` 记录两页三视口都满足 `bodyOverflowX = 0`，工作表同时满足 `gridOverflowX = 0`、`hasRefundedHeader = true`、`rowCountText = 共 34 行`。桌面视口下右键 `已返款` 单元格后可见 `检查已返款` 菜单项，点击后弹窗文案为 `已核对 34/34 行，当前表格已返款与历史返款记录一致。`；`fanxiu/data-annotation` 的 AI 工具菜单中 `hasAiNotebook = false`、`hasAgents = false`。
- 根因分层：上一轮 pending 的根因是入口权限差异，不是 `NoteSheetWorkspace` 本身的 UI 模型错误；本轮新增的 AI 工具减法属于共享导航边界收敛，不涉及新的后端数据投影或业务建模债务；无需新增 `docs/CodeYun自动化协作交接.md` 条目。
- 处理结果：本轮未改仓库代码，但已完成完整增量范围的提交归类、概念建模、真实三视口截图和关键交互复验，因此关闭 `e1d79d0c..78774fb2` 的 pending，并把 `last_audited_commit` 推进到 `72a3d80aaabb730cf6277293a8487067dcb705ca`，清空 `pending_or_skipped_ranges`。

- 完整范围：`e1d79d0cf571e2dded7942e5900639ec0a05ce5e..78774fb2ed3ccfd4a59b916bf1e7acf0bbdcb820`
- 覆盖提交：`78774fb2ed3ccfd4a59b916bf1e7acf0bbdcb820`
- 前端入口提交：`78774fb2ed3ccfd4a59b916bf1e7acf0bbdcb820`
- 入口如何牵引到旧问题：本次提交把 `fanxiu/data-annotation` 的 OCR 专用抠图、`cluster/tasks` 的 sortable 初始化、`notes/list` / `notes/star` 的展示与渲染，以及 `NoteSheetWorkspace` 的 `检查已返款` 一并推到前台。真实页面复核后，真正被同链路牵出的旧问题落在 `data-annotation` 的同一条 shape 检测行：新增的 `OCR抠图策略` 属于 OCR 工作态下的细化策略，却在默认态也跟 `OCR 文本 + 匹配模式` 并列常驻，形成多一个概念解释同一事实的过度暴露。
- 本轮减法：不新增任何功能、状态或说明区，只在 [`frontend/src/standard/fanxiu/data-annotation/page.vue`](D:/home/chenkunze/slns/codeyun/frontend/src/standard/fanxiu/data-annotation/page.vue) 新增 `selectedShapeShowsOcrMaskControls`，把 `OCR抠图策略` 与 `OCR抠图` 按钮收回到“当前 shape 已进入 OCR 使用态，或已经存在 OCR 专用配置”时再显示。OCR 关闭且没有 OCR 专用配置时，这组新概念完全消失。
- 信息量保持：OCR 文本、OCR 匹配模式、OCR 专用抠图能力和已有 OCR 专用配置都保留；减少的是“还没用 OCR 就先常驻暴露 OCR 策略”这一层重复概念，不是减少检测能力。
- 概念图/线框图：报告里的 Mermaid 把 `选择 shape -> 图像命中 -> 需要时补 OCR -> 需要时再细化 OCR 专用抠图 -> 检测/保存` 拆层，明确 `OCR 专用抠图` 不应升级成默认一级常驻元素。
- 报告路径：`C:/Users/kzche/AppData/Local/Temp/codeyun/ui-design-audit/2026-07-02-frontend-design-78774fb2/report.md`
- 验证：`npm run typecheck --prefix frontend`、`npm run build --prefix frontend` 通过。`fanxiu/data-annotation` 已补拍 `1600x1000`、`1366x900`、`820x1180` 三视口截图；`cluster/runtime`、`notes/center?tab=list`、`notes/center?tab=star`、`attendance/orders` 也已做桌面复核。`domSnapshot()` 在 OCR 关闭样本上只剩 `OCR：不要求`，不再出现 `OCR抠图策略`；切换 OCR 后，同一行会重新出现策略控件，说明能力仍在。`/notes/sheets-manager` 当前账号返回 403，未取得真实 `已返款` sheet 入口。
- 根因分层：`data-annotation` 的问题属于前端状态投影 + 表现层过度暴露；`cluster/tasks` / `notes/list` / `notes/star` 本轮未发现新的设计债务。`NoteSheetWorkspace` 则仍缺真实页面验证，暂时不能判断新右键入口在真实表格里的层级是否合适。
- 处理结果：本轮已完成完整提交列表归类、同链路建模、`data-annotation` 低风险减法修复、静态验证和多页实拍，但因为 `NoteSheetWorkspace` 真实 `已返款` 表入口没有打开成功，整段范围 `e1d79d0c..78774fb2` 先记录到 `pending_or_skipped_ranges`，`last_audited_commit` 保持在 `e1d79d0cf571e2dded7942e5900639ec0a05ce5e`。

- 完整范围：`891fb26355df59b6de0b7c0fe5146b76acc7cae6..e1d79d0cf571e2dded7942e5900639ec0a05ce5e`
- 覆盖提交：`e1d79d0cf571e2dded7942e5900639ec0a05ce5e`
- 前端入口提交：`e1d79d0cf571e2dded7942e5900639ec0a05ce5e`
- 入口如何牵引到旧问题：本次提交把 `fanxiu/data-annotation` 的 `frame_structure.diagnostics` 回挂到资产树节点展示，并同步触达 `notes/list` 与 `notes/star`。真实页面复核后，被牵出的旧问题仍在同一资产树链路上：`suggestion` 级结构提示被做成常驻绿色整行底，和 tooltip 文本、`场景归纳` 确认消息在重复表达同一事实，导致一级浏览层级被次级建议抢占。
- 本轮减法：不新增功能、入口、状态或说明区，只把 [`frontend/src/standard/fanxiu/data-annotation/page.vue`](D:/home/chenkunze/slns/codeyun/frontend/src/standard/fanxiu/data-annotation/page.vue) 里的 `suggestion` 样式从整行绿色底退回为左侧边缘标记。`error/warning` 级提醒、tooltip 诊断文本和 `场景归纳` 操作都保留，删除的是“次级建议常驻染色整行”这个重复视觉层级。
- 信息量保持：scene/sub-scene 体检、结构提示、节点 tooltip 和场景归纳写入能力都保留；减少的是 suggestion 事实在一级资产树里的重复强调，不是减少诊断能力。
- 概念图/线框图：报告中的 Mermaid 把 `资产树节点 -> 一级判断 -> 场景归纳 -> 附带结构提示` 拆层，明确结构提示应退回 tooltip / 边缘标记，而不是升级成一级常驻色块。
- 报告路径：`C:/Users/kzche/AppData/Local/Temp/codeyun/ui-design-audit/2026-07-02-frontend-design-e1d79d0c/report.md`
- 验证：`npm run typecheck --prefix frontend`、`npm run build --prefix frontend` 通过。`fanxiu/data-annotation` 已完成 `1600x1000`、`1366x900`、`820x1180` 三视口复拍；`data-annotation-evidence.json` 记录三视口均满足 `bodyOverflowX = 0`、`actionWraps = false`，且 `has-diagnostic-suggestion` 的 `backgroundColor = transparent`、`borderLeftColor = rgb(82, 196, 26)`。`notes/list` 与 `notes/star` 也已做真实页面复核，未发现新增交互回归。
- 根因分层：本轮自动修复属于同一页面内的表现层 + 前端状态投影问题，不涉及新的后端数据投影或业务建模债务；无需新增 `docs/CodeYun自动化协作交接.md` 条目。
- 处理结果：本轮已完成完整增量范围的提交归类、概念建模、低风险减法修复、三视口真实页面复拍和前端验证，因此把 `last_audited_commit` 推进到 `e1d79d0cf571e2dded7942e5900639ec0a05ce5e`，并保持 `pending_or_skipped_ranges` 为空。

- 完整范围：`b72baa1b93641e86bd98961391f459d27d0c8176..891fb26355df59b6de0b7c0fe5146b76acc7cae6`
- 覆盖提交：`891fb26355df59b6de0b7c0fe5146b76acc7cae6`
- 前端入口提交：`891fb26355df59b6de0b7c0fe5146b76acc7cae6`
- 入口如何牵引到旧问题：本轮是对同一 `fanxiu/data-annotation` 入口的补闭环。上一轮已经识别出“识别层仍常驻无效编辑动作”并做了减法修复，但缺少修复后 authenticated 实图，游标没法推进。补拍成功后，又在同一资产树工具条链路上看到当前工作树新增的常驻 `结构提示` tag，它重复了树节点高亮、tooltip 与场景归纳反馈已经表达的事实，因此一并收回。
- 本轮减法：不新增任何功能、状态、说明区或路由，只继续压缩同一条工具条。第一，补齐 `识别层` 宽屏 / 普通桌面 / 窄屏 authenticated 实图，证明 `新建分组` 与 `删除` 已收回到正确层级且不再横向溢出。第二，删除一级工具条里的 `结构提示` 常驻 tag，把结构诊断退回到节点高亮、节点 tooltip 与 `场景归纳` 确认/结果消息。
- 信息量保持：资产树 / 识别层浏览、保存当前帧、场景归纳、连拍、连拍缓存和结构诊断能力都保留；减少的是同一诊断事实在一级工具条的重复投影，而不是减少诊断能力。
- 概念图/线框图：报告中的 Mermaid 把 `浏览投影`、`分组编辑`、`运行辅助` 和 `结构诊断` 明确拆层；ASCII 线框图直接对比了“结构提示 tag 常驻”与“诊断退回节点级提示”前后的概念数量变化。
- 报告路径：`C:/Users/kzche/AppData/Local/Temp/codeyun/ui-design-audit/2026-07-02-frontend-design-891fb263-closeout/report.md`
- 验证：`npm run typecheck --prefix frontend`、`npm run build --prefix frontend` 通过。authenticated in-app Browser 已完成 `data-annotation` 的修复后三视口复拍和 `资产树` 桌面对照，`data-annotation-evidence.json` 记录 `scene` 三视口均满足 `plusVisible = false`、`deleteVisible = false`、`bodyOverflowX = 0`、`actionsClientWidth == actionsScrollWidth`，`business` 对照满足 `plusVisible = true` 且 `tagText = []`。
- 根因分层：本轮问题仍属于同一页面内的表现层 + 前端状态投影问题，不涉及新的后端数据投影或业务建模债务；无需新增 `docs/CodeYun自动化协作交接.md` 条目。
- 处理结果：本轮已完成完整增量范围的提交归类、authenticated 真实页面复拍、同链路低风险减法修复和前端验证，因此把 `last_audited_commit` 推进到 `891fb26355df59b6de0b7c0fe5146b76acc7cae6`，并清空 `pending_or_skipped_ranges`。

### 2026-07-01

- 完整范围：`b72baa1b93641e86bd98961391f459d27d0c8176..891fb26355df59b6de0b7c0fe5146b76acc7cae6`
- 覆盖提交：`891fb26355df59b6de0b7c0fe5146b76acc7cae6`
- 前端入口提交：`891fb26355df59b6de0b7c0fe5146b76acc7cae6`
- 入口如何牵引到旧问题：本次提交显式把 `fanxiu/data-annotation` 从旧的“帧树 / Layer / subframe”命名收回到“资产树 / 识别层 / scene / sub-scene”，同时限制识别层视图下的编辑动作。真实页面复核后，被牵出的旧问题不是命名文案，而是同一页面资产树工具条仍把只属于资产树的编辑动作以禁用态常驻展示，并在普通桌面宽度把按钮挤出容器。
- 本轮减法：不新增任何功能、状态、说明区或路由，只在 [`frontend/src/standard/fanxiu/data-annotation/page.vue`](D:/home/chenkunze/slns/codeyun/frontend/src/standard/fanxiu/data-annotation/page.vue) 做 3 个减法。第一，`识别层` 视图下不再常驻显示 `新建分组`。第二，删除一级工具条里的 `删除选中节点`，把删除入口收回到已有右键菜单。第三，把 `连拍缓存` 收成图标次要动作，避免继续挤爆主工具条。
- 信息量保持：资产树 / 识别层浏览、保存当前帧、场景归纳、连拍和连拍缓存能力都保留；减少的是“识别层里无效编辑动作”和“删除入口重复”两层偶然复杂度，不是减少标注能力。
- 概念图/线框图：报告中的 Mermaid 把 `数据标注 -> 浏览投影(资产树 / 识别层) -> 仅资产树可编辑` 与 `运行辅助动作` 明确拆层；ASCII 线框图直接对比了修复前把无效编辑动作堆在识别层工具条，以及修复后收回到正确层级的差异。
- 报告路径：`C:/Users/kzche/AppData/Local/Temp/codeyun/ui-design-audit/2026-07-01-frontend-design-891fb263/report.md`
- 验证：`npm run typecheck --prefix frontend`、`npm run build --prefix frontend` 通过。authenticated in-app Browser 已拿到修复前 wide `1600x1000`、desktop `1366x900`、narrow `820x1180` 截图与 `data-annotation-evidence.json`；但修复后最终 authenticated 复拍时浏览器会话重置，只有 unauthenticated headless fallback 登录页截图，不作为闭环证据。
- 根因分层：本轮问题属于同一页面内的表现层 + 前端状态投影问题，不涉及新的后端数据投影或业务建模债务；无需新增 `docs/CodeYun自动化协作交接.md` 条目。
- 处理结果：本轮已完成完整增量范围的提交归类、概念建模、代码减法修复和前端构建验证，但因为修复后 authenticated 真实页面复拍失败，暂不推进 `last_audited_commit`，并把当前范围记录到 `pending_or_skipped_ranges`。

- 完整范围：`d09a63ee07e96782b9a0e0fd0ab568a3e5ad6c0f..b72baa1b93641e86bd98961391f459d27d0c8176`
- 覆盖提交：`b72baa1b93641e86bd98961391f459d27d0c8176`
- 前端入口提交：`b72baa1b93641e86bd98961391f459d27d0c8176`
- 入口如何牵引到旧问题：本次提交前端显式触达 `cluster/services` 与共享 `MainLayout`，并通过后端摘要裁剪把服务管理页进一步收敛到 OCR 主对象。真实页面复核后，真正被牵出的旧问题不在服务卡片内部，而在同一条 `cluster` 入口链路上：`/cluster/services` 虽有页面、有权限标题，但既没有左侧菜单挂载，也没有在 cluster 菜单切页时保留当前 `entry_id`，导致新服务页只能靠直链进入，且跨页会丢设备上下文。
- 本轮减法：不新增任何新页面、按钮、状态或说明，只做两处共享入口收口。第一，为 `cluster.services` 补 `menu_paths` 并在 [`frontend/src/layout/MainLayout.vue`](D:/home/chenkunze/slns/codeyun/frontend/src/layout/MainLayout.vue) 的 `集群管理` 分组显式加入 `服务管理`。第二，把 cluster 菜单跳转统一收回到一个 `buildMenuRouteLocation`，让 `/cluster/*` 在同一设备链路里自动保留当前 `entry_id`。
- 信息量保持：服务页本身仍只展示 OCR 摘要、Token 与使用文档；运行管理、TreeSize、浏览文件等现有 cluster 能力都不变。减少的是“隐藏直链页”和“跨页设备上下文丢失”两层偶然复杂度，不是新增运维概念。
- 概念图/线框图：报告中的 Mermaid 把 `集群管理菜单 -> 运行管理 / 服务管理 -> entry_id = 当前设备 -> OCR 摘要 / Token` 收回到一条共享设备上下文链路；ASCII 线框图直接对比了修复前 `服务管理` 缺席菜单、只能靠 URL 进入，与修复后回到单一菜单入口的差异。
- 报告路径：`C:/Users/kzche/AppData/Local/Temp/codeyun/ui-design-audit/2026-07-01-frontend-design-b72baa1b/report.md`
- 验证：复用本地 `5173/8000` 开发环境，在 in-app Browser 对 `cluster/services?entry_id=30b82d72-8a76-4a74-be4b-4fc1591c6ce2` 与 `cluster/runtime?entry_id=30b82d72-8a76-4a74-be4b-4fc1591c6ce2` 完成宽屏 `1600x1000`、普通桌面 `1366x900`、窄屏 `820x1180` 截图；`evidence.json` 记录 6 张截图全部满足 `bodyOverflowX = 0`、`mainOverflowX = 0`，且桌面视口下 `集群管理` 菜单都包含 `服务管理`。另外补做远程设备交互探针：从 `cluster/runtime?entry_id=da2d06f2-ab06-4c05-a614-e396e2e6e6a3` 点击左侧 `服务管理` 后，真实 URL 保持 `entry_id=da2d06f2-ab06-4c05-a614-e396e2e6e6a3`，活动设备仍为 `codepc_mi15`。`npm run typecheck --prefix frontend` 与 `npm run build --prefix frontend` 均通过。
- 根因分层：本轮问题属于共享导航层 / 前端入口模型，而不是新的后端 DTO 或业务建模债务；`cluster/services` 本次只读 `ocr` 摘要的一级页面模型保持成立，不新增 `docs/CodeYun自动化协作交接.md` 条目。
- 处理结果：本轮已完成完整增量范围的提交归类、概念图/线框图、真实三视口截图、共享导航层低风险修复和前端验证，因此把 `last_audited_commit` 推进到 `b72baa1b93641e86bd98961391f459d27d0c8176`，保持 `pending_or_skipped_ranges` 为空。

- 完整范围：`cf6ac33da15354e3530527ff46c63bf7455e93fe..d09a63ee07e96782b9a0e0fd0ab568a3e5ad6c0f`
- 覆盖提交：`d09a63ee07e96782b9a0e0fd0ab568a3e5ad6c0f`
- 前端入口提交：`d09a63ee07e96782b9a0e0fd0ab568a3e5ad6c0f`
- 入口如何牵引到旧问题：本次提交同时改了 `cluster/tasks`、`cluster/logs`、`cluster/files`，分别收敛运行单元动作文案、日志页动作反馈和文件页排序编辑器默认态；真实多视口复核后，三页又被同一个旧问题一起牵出：`集群管理` 共享侧栏在窄屏仍按桌面模式展开，直接挤压同一条运维链路的主内容区。
- 本轮减法：不新增任何新控件，只在 [`frontend/src/layout/MainLayout.vue`](D:/home/chenkunze/slns/codeyun/frontend/src/layout/MainLayout.vue) 把窄屏默认态收回为自动折叠图标侧栏，并在离开窄屏时只恢复这次自动折叠造成的状态，不覆盖用户手动切换。
- 信息量保持：菜单入口、页面能力和运维状态信息都不变；减少的是共享布局层对 `运行管理 / 浏览文件 / 运行详情` 三页重复制造的横向挤压，不是减少业务能力。
- 概念图/线框图：临时报告中的 Mermaid 把 `集群管理菜单 -> 运行管理 / 浏览文件 / 运行详情` 与共享 `MainLayout` 侧栏策略连到同一条“窄屏主内容可读宽度”链路，证明问题来自共享布局层，而非三页各自再发明了一套问题。
- 报告路径：`C:/Users/kzche/AppData/Local/Temp/codeyun/ui-design-audit/2026-07-01-frontend-design-d09a63ee/report.md`
- 验证：`npm run typecheck --prefix frontend`、`npm run build --prefix frontend` 通过；真实页面最终截图见 `tasks/files/logs-fanxiu-behavior-tree` 的 `wide-final`、`desktop-final`、`narrow-final` 共 9 张，修复前窄屏对比见 `*-narrow-viewport.png`。
- 根因分层：本轮问题属于共享表现层 / 布局默认态，而不是新的后端数据投影或业务建模债务；`cluster/tasks`、`cluster/logs`、`cluster/files` 提交自身的一级模型收敛保持成立，不新增 `docs/CodeYun自动化协作交接.md` 条目。
- 处理结果：本轮已完成完整增量范围的提交归类、概念图/线框图、真实三视口截图、低风险修复和前端验证，因此把 `last_audited_commit` 推进到 `d09a63ee07e96782b9a0e0fd0ab568a3e5ad6c0f`，保持 `pending_or_skipped_ranges` 为空。

- 完整范围：`bcc40826fa23de6be619166195890b2b2e846138..cf6ac33da15354e3530527ff46c63bf7455e93fe`
- 覆盖提交：`cf6ac33da15354e3530527ff46c63bf7455e93fe`
- 前端入口提交：`cf6ac33da15354e3530527ff46c63bf7455e93fe`
- 入口如何牵引到旧问题：这次提交把 data-annotation runtime/scheduler 重构继续传导到 `cluster/runtime`、`notes/center?tab=list`、`cluster/storage` 和 `fanxiu/wiki`。沿同一“运行状态 -> 次级命令 -> 诊断结果”链路复核后，真正被牵出来的旧问题出现在 `cluster/runtime`：新加 `inspect / wake / restart / reset` 之后，内建服务右键菜单里旧的 `配置` 仍然存在，但对大部分服务它实际只是再次打开日志，导致同一事实既叫“配置”又叫“日志”。
- 本轮减法：不新增任何按钮、说明、状态或入口；只把 [`frontend/src/standard/cluster/tasks/page.vue`](D:/home/chenkunze/slns/codeyun/frontend/src/standard/cluster/tasks/page.vue) 的 `配置` 收回到真实可配置对象。命令对象仍可编辑，内建作业仍可配调度，`CodeYun Watchdog` 仍保留 `自启` 入口；行为树等内建服务只保留 `运行诊断 / 唤醒行为树 / 重启行为树 / 日志` 四类真实动作。
- 信息量保持：运行管理页仍保留服务/作业状态、运行命令摘要、下次触发、诊断、唤醒、重启与日志能力；减少的是“伪配置”这层错误概念，不是减少运维能力。`notes/list` 的前端筛选折叠卡、`cluster/storage` 的判重工具条拆分、`fanxiu/runtime` 的状态读取瘦身和 `fanxiu/wiki` 的灵体公式压缩都在真实页面里继续保持复杂度下降方向，没有新回退。
- 概念图/线框图：报告中的 Mermaid 把 `cluster/runtime` 收回到“状态表 -> 右键动作 -> 真配置对象 / 运维动作”两层结构；ASCII 线框图直接对比了修复前后的行为树右键菜单，证明本轮是在删掉把日志伪装成配置的重复入口。
- 报告路径：`C:/Users/kzche/AppData/Local/Temp/codeyun/ui-design-audit/2026-07-01-frontend-design-cf6ac33d/report.md`
- 验证：复用本地 `5173/8000` 开发环境，在 in-app Browser 对 `cluster/runtime`、`notes/center?tab=list` 完成宽屏 `1600x1000`、普通桌面 `1366x900`、窄屏 `820x1180` 截图，对 `cluster/storage`、`fanxiu/data-annotation/runtime`、`standalone/fanxiu/wiki?tab=item&id=16000100` 完成宽屏/窄屏截图；`evidence.json` 记录 12 张截图全部满足 `bodyScrollWidth == bodyClientWidth` 与 `docScrollWidth == docClientWidth`。其中 `cluster/runtime` 三视口右键菜单标签都只剩 `运行诊断 / 唤醒行为树 / 重启行为树 / 日志`。`npm run typecheck --prefix frontend` 与 `npm run build --prefix frontend` 均通过。
- 根因分层：`cluster/runtime` 属于前端状态投影/信息架构问题，根因是旧菜单把日志伪装成配置；其余入口页面本轮未发现新的表现层、前端状态投影、后端数据投影或业务建模问题。
- 跨自动化交接：无。本轮问题可在前端页面层直接收敛，不需要转交 `CodeYun 代码健康优化`。
- 剩余风险：`fanxiu/wiki` 的灵体契约详情虽然已开始用公式段压缩高阶重复文本，但前半段仍保留较多单阶原文；当前方向仍是减法，本轮不继续扩散到更大重构。`notes/center?tab=star` 本次只涉及无边图自动 relayout 条件收紧，真实页面未发现新的 UI 回退，因此不在无关页面继续发散修复。
- 处理结果：本轮已完成完整增量范围的提交归类、概念图/线框图、真实多视口截图、低风险修复和前端验证，因此把 `last_audited_commit` 推进到 `cf6ac33da15354e3530527ff46c63bf7455e93fe`，保持 `pending_or_skipped_ranges` 为空。

- 完整范围：`31b691883a4991c5b582aad3eb82ac69d8a547b9..bcc40826fa23de6be619166195890b2b2e846138`
- 覆盖提交：`bcc40826fa23de6be619166195890b2b2e846138`
- 前端入口提交：`bcc40826fa23de6be619166195890b2b2e846138`
- 入口如何牵引到旧问题：该提交本体主要重构本机守护运行时，但它在前端上同时触达 `fanxiu/data-annotation/runtime` 与 `notes/center/ListNotes`。两处变化都指向同一个设计原则：同一事实只保留一个表达面。`fanxiu/runtime` 里右键命令不应把“推进调度”误写成状态名词，也不应对 task cell 暴露无效动作；`ListNotes` 里标题列不应继续重复 `形态` 列已经表达的 NoteFormBadge。
- 本轮减法：不新增任何按钮、说明、状态或入口；只确认本次提交已经完成两处有效减法。第一，`fanxiu/runtime` task cell 右键菜单只保留 `触发一次 / 日志`，定时作业才保留 `推进到下次`。第二，`ListNotes` 标题列去掉重复的 `NoteFormBadge`，把 `形态` 事实收回到独立列。
- 信息量保持：`fanxiu/runtime` 仍保留手动触发、推进调度与日志三类必要操作，但按作业类型过滤无效动作；`ListNotes` 仍保留标题、分类、形态、阶段、权重、私密与起始时间完整判断面，只删除标题列内的重复形态提示。
- 概念图/线框图：报告中的 Mermaid 把本轮入口收回到“作业状态表 -> 作业类型 -> 有效命令集合”和“标题列 -> 形态列 -> 去重事实”两条简化链路；ASCII 线框图直接对比了提交前后的右键菜单与列表列职责。
- 报告路径：`C:/Users/kzche/AppData/Local/Temp/codeyun/ui-design-audit/2026-07-01-frontend-design-bcc40826/report.md`
- 验证：复用本地 `5173/8000` 开发环境，在 in-app Browser 打开 `/fanxiu/data-annotation/runtime?entry_id=30b82d72-8a76-4a74-be4b-4fc1591c6ce2` 与 `/notes/center?tab=list`，完成宽屏 `1600x1000`、窄屏 `820x1180` 实图复核；`runtime-manual-context-menu-1600x1000.png` 证明 task cell 右键菜单只剩 `触发一次 / 日志`；`evidence.json` 记录两页宽屏/窄屏都满足 `bodyScrollWidth == bodyClientWidth`。本轮未改仓库代码，因此未运行 `npm run typecheck --prefix frontend` 或 `npm run build --prefix frontend`。
- 根因分层：`fanxiu/runtime` 属于前端状态投影问题，根因是把命令入口写成状态名词并暴露给不支持该动作的 task cell；`ListNotes` 属于表现层/信息架构去重问题，根因是标题列和形态列同时表达同一事实。两处都不涉及新的后端数据投影或业务建模债务。
- 跨自动化交接：无。本轮没有发现需要转交 `CodeYun 代码健康优化` 的模型层问题。
- 剩余风险：`fanxiu/runtime` 顶部阻塞告警仍然较长，但当前已通过单行截断保持主布局稳定；它不是本次提交引入的问题，也没有因为本轮入口出现新的复杂度回退。`ListNotes` 本轮主要复核列表首屏与窄屏密度，没有在编辑态继续做无关扩散验证。
- 处理结果：本轮已完成完整增量范围的提交归类、业务/交互建模、概念图与真实多视口截图复核，且未发现需要追加自动修复的低风险 UI 回退，因此直接把 `last_audited_commit` 推进到 `bcc40826fa23de6be619166195890b2b2e846138`，保持 `pending_or_skipped_ranges` 为空。

- 完整范围：`e3acd95d7bddb56cde8e665b0dadd0e0a938c029..31b691883a4991c5b582aad3eb82ac69d8a547b9`
- 覆盖提交：`31b691883a4991c5b582aad3eb82ac69d8a547b9`
- 前端入口提交：`31b691883a4991c5b582aad3eb82ac69d8a547b9`
- 入口如何牵引到旧问题：该提交同时触达 `cluster/runtime`、`fanxiu/data-annotation/runtime` 与 `NoteSheetWorkspace`。`cluster/runtime` 这条链路主要用来确认运行管理页此前压回的主闭环顺序没有被新联动带回退；`NoteSheetWorkspace` 入口则用 `freebill` 代表页复核复制/剪切增强没有额外增殖常驻解释；真正需要收敛的问题出现在 `fanxiu/data-annotation/runtime`，新加的右键动作把“推进调度到下一次”的命令直接写成了状态名词 `下次触发`，并且对 task cell 也暴露了无效入口。
- 本轮减法：不新增任何按钮、说明、状态或新入口；只把 `fanxiu/runtime` 右键菜单里的 `下次触发` 收敛成动词短语 `推进到下次`，并且只保留在可计算下一次触发的非 task cell 上。这样一级界面继续只保留“触发一次 / 推进调度 / 日志”三类必要动作，不让状态列名和命令入口混成同一层语义。
- 信息量保持：定时作业仍能手动触发、推进调度和查看日志；task cell 仍能触发一次并查看日志；`cluster/runtime` 仍保留 `服务 -> 作业 -> 队列记录 -> 资源监控` 四段信息；`NoteSheetWorkspace` 仍保留表格、筛选和复制剪切链路。减少的是无效命令和名词化动作，不是减少判断依据。
- 概念图/线框图：报告中的 Mermaid 把本轮入口收回到 `cluster/runtime`、`fanxiu/runtime`、`NoteSheetWorkspace` 三条相关页面链路；ASCII 线框图直接对比了 `触发一次 / 下次触发 / 日志` 与修复后的 `触发一次 / 推进到下次 / 日志`。
- 报告路径：`C:/Users/kzche/AppData/Local/Temp/codeyun/ui-design-audit/2026-07-01-frontend-design-31b69188/report.md`
- 验证：复用本地 `5173/8000` 开发环境，在 in-app Browser 对 `/cluster/runtime`、`/fanxiu/data-annotation/runtime`、`/notes/freebill` 完成宽屏 `1600x1000`、普通桌面 `1366x900`、窄屏 `820x1180` 截图；`evidence.json` 记录三条链路当前 `overflowX = 0`，且 `cluster/runtime` 区块顺序稳定为 `服务 -> 作业 -> 队列记录 -> 资源监控`。`fanxiu/runtime` 修复后，定时作业右键菜单按钮为 `触发一次 / 推进到下次 / 日志`，task cell 右键菜单按钮只剩 `触发一次 / 日志`。`npm run typecheck --prefix frontend` 与 `npm run build --prefix frontend` 均通过。
- 根因分层：`fanxiu/runtime` 属于前端状态投影问题，根因是把命令动作投影成状态名词，并把对 task cell 无意义的命令暴露成常驻入口；`cluster/runtime` 与 `NoteSheetWorkspace` 本轮未发现新的表现层、后端数据投影或业务建模问题。
- 跨自动化交接：无。本轮问题可在前端页面层直接收敛，不需要转交 `CodeYun 代码健康优化`。
- 剩余风险：`NoteSheetWorkspace` 本轮主要复核了代表页布局、横向溢出和主阅读路径，没有在可编辑工作表里逐项手动演练新的剪切行视觉反馈；当前只确认该增强没有把主页面再包装成更复杂的常驻结构。
- 处理结果：本轮已完成完整增量范围的提交归类、概念图/线框图、真实多视口截图、低风险修复和前端验证，因此把 `last_audited_commit` 推进到 `31b691883a4991c5b582aad3eb82ac69d8a547b9`，保持 `pending_or_skipped_ranges` 为空。

### 2026-06-30

- 完整范围：`079ecc19378922bcc89d568a1a48c92a08913789..e3acd95d7bddb56cde8e665b0dadd0e0a938c029`
- 覆盖提交：`e3acd95d7bddb56cde8e665b0dadd0e0a938c029`
- 前端入口提交：`e3acd95d7bddb56cde8e665b0dadd0e0a938c029`
- 入口如何牵引到旧问题：这次提交同时触达 `cluster/runtime` 与 `fanxiu/data-annotation`。`data-annotation` 的改动本质是把浮动子标注的匹配计算回收到“父框匹配 -> 子框相对换算”这条基础模型，没有新增新控件；真正被牵出来的旧 UI 问题发生在 `cluster/runtime`，提交把 `资源监控` 区块重新挪回 `服务/作业` 之前，直接复活了该页之前已经压掉的首屏层级回退。
- 本轮减法：不新增任何按钮、说明、标签、入口或状态；只把 `cluster/runtime` 的 `资源监控` 区块从首屏主闭环后置回 `队列记录` 之后，恢复 `服务 -> 作业 -> 队列记录 -> 资源监控` 的基础顺序。`fanxiu/data-annotation` 维持既有 `画面区 + 右侧目录/工具` 基础模型，不因行为补强继续扩 UI。
- 信息量保持：运行管理页仍保留服务、作业、队列记录与资源监控四类信息；凡修标注页仍保留入口、画面、目录和工具链。减少的是错误层级与诊断信息抢占一级注意力，不是减少判断依据。
- 概念图/线框图：报告中的 Mermaid 收回到 `设备 -> 服务状态表 -> 作业状态表 -> 队列记录 -> 资源监控` 这条闭环；ASCII 线框图直接对比了错误回退顺序 `设备 -> 资源监控 -> 服务/作业` 与修复后的主闭环顺序。
- 报告路径：`C:/Users/kzche/AppData/Local/Temp/codeyun/ui-design-audit/2026-06-30-frontend-design-e3acd95d/report.md`
- 验证：复用本地 `5173/8000` 开发环境，在 in-app Browser 对 `cluster/runtime` 完成宽屏 `1600x1000`、普通桌面 `1366x900`、窄屏 `820x1180` 的修复前后截图，对 `fanxiu/data-annotation` 完成三视口截图与基础结构复核；`evidence-before.json` 记录修复前宽屏/普通桌面下 `sectionTitles = [资源监控, 服务, 作业]`，修复后 DOM 顺序恢复为 `服务 -> 作业 -> 队列记录 -> 资源监控`，且所有截图 `overflowX = 0`；`console-logs.json` 记录两页本轮均无 `warn/error`。`npm run typecheck --prefix frontend` 与 `npm run build --prefix frontend` 均通过。
- 根因分层：`cluster/runtime` 属于前端状态投影问题，根因是把诊断区错误提升到一级主闭环；`fanxiu/data-annotation` 本轮未发现新的表现层、前端状态投影、后端数据投影或业务建模问题。
- 跨自动化交接：无。本轮问题可在前端页面层直接收敛，不需要转交 `CodeYun 代码健康优化`。
- 剩余风险：`fanxiu/data-annotation` 这次提交主要补强运行时匹配逻辑；本轮前端设计巡检只验证了真实页面结构和基础可用性，没有在真实游戏画面里复现“浮动子标注跟随父框匹配”的运行结果。
- 处理结果：本轮已完成完整增量范围的提交归类、概念图/线框图、真实多视口截图、低风险修复和前端验证，因此把 `last_audited_commit` 推进到 `e3acd95d7bddb56cde8e665b0dadd0e0a938c029`，保持 `pending_or_skipped_ranges` 为空。

- 完整范围：`0c84221402525cb9c9810e5df05aeb0ec53e6dac..079ecc19378922bcc89d568a1a48c92a08913789`
- 覆盖提交：`079ecc19378922bcc89d568a1a48c92a08913789`
- 判定：无前端相关提交。该提交只改了 `backend/api/note_sheets.py`、`backend/core/fanxiu/data_annotation/tasks/daily_foundation.py`、对应后端测试，以及 `docs/CodeYun前端设计巡检自动化上下文.md`、`docs/凡修行为树运行框架约定.md`、`docs/考勤课程工作簿月度生成规范.md` 三份规范文档；未改 `frontend/src/**`、路由、菜单、权限，也没有同步改变前端可感知的 `note-sheets` API 形状或页面投影。
- 处理结果：不启动开发环境、不生成新截图、不写新报告；本轮完成完整增量范围的提交归类和“无关提交”判定后，将 `last_audited_commit` 推进到 `079ecc19378922bcc89d568a1a48c92a08913789`，保持 `pending_or_skipped_ranges` 为空。

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
- 信息量保持：帧树、场景树、非场景帧、默认 Layer 1/2 与调用方动态传入的 Layer 0 候选、shape 身份证据、场景跳转、运行页守护/作业状态、Mystia 菜品/食材/饮品/角色/素材目录、菜单入口和权限路径都保留；场景识别统一由图模型处理，不再表达局部/全局范围。
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

