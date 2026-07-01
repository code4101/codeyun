# 凡修 data-annotation 命名约定

> Last Updated: 2026-07-01

本文档是凡修 data-annotation / Runtime / Scheduler 的术语入口。新代码、新文档和新日志先按这里理解，再看历史兼容名。

## 术语约定

### scene / frame / view

- scene 是主术语：表示 Runtime 能识别、能等待、能跳转的业务场景或页面编号，例如 `#34 世界`、`#121 邮件`。业务代码、公开 API、新文档和新日志优先使用 `scene`。
- frame 是底层细节：表示实时截图帧、`frame_data_url`、OCR/匹配输入，或资产树里用于识别的 frame/subframe/layer 结构。它不是业务 API 的主称呼。
- view 是旧版兼容：保留 `View` 类、`wait_view()`、`goto_view()` 等历史入口，避免破坏旧代码和测试。新增业务代码不要继续扩散 `view` 术语。

### 资产树 / 识别树

- 资产树 `asset tree` 是标注资产的数据容器：目录/分组、scene 数据、shape 数据、OCR/图像匹配配置、`sceneJumpTarget`、人工归档事实都放在这里。目录嵌套只表示人工组织方式，不天然等于运行时 sub scene 关系。
- 识别树 `recognition tree` 是 Runtime 每次识别时动态生成的候选计划：它根据资产事实、图片 `layer`、frame/subframe 结构、当前目标、route/path 上下文、弹窗/浮层规则和本次动作的 `layer0` 计算候选顺序与展开方式。
- sub scene / subframe 这类父子关系最终在识别树里表现为动态候选展开。资产树可以用 `image.children` 记录人工确认的 frame/subframe 结构，也可以用目录和备注帮助归档；Runtime 是否展开、按什么顺序展开，仍由识别树结合上下文决定，不能只因为目录嵌套就硬编码运行时识别策略。

### layer / layer0

- `layer` 是识别树的候选分层和排序维度，主要来自资产标注或识别规则。它决定 root frame 进入哪条全局识别队列，不表达业务父子关系。
- `layer0` 是某次 Runtime 动作产生的动态优先候选集，通常来自当前点击 shape 的 `sceneJumpTarget`，也可以来自调用方显式目标。它不是资产图片的静态 `layer` 字段，也不是“某些 scene 被标为 layer0”。
- `layer0_wait_seconds` 是 `layer0` 的优先等待窗口。默认 30 秒；长过场入口可显式调大，例如仙府进入 `#171` 可用 60 秒。

## 主 API

- `current_scene()`：识别当前场景。
- `wait_scene()`：等待一个或多个目标场景出现。
- `go_scene()`：移动到目标场景，背后使用场景图和 `sceneJumpTarget` 做路径规划。

兼容入口：

- `wait_view()` 等价于 `wait_scene()` 的历史名。
- `goto_view()` 等价于 `go_scene()` 的历史名。
- `View` 类仍是底层资产包装模型，短期不重命名。

## 判断规则

- 写业务流程时，说“进入/等待/返回/跳转某页面”，用 `scene`。
- 写截图、OCR、匹配、裁剪、缓存、实时画面输入时，用 `frame`。
- 只有维护旧接口、旧测试、`View` 类包装、或解释历史代码时，才用 `view`。

## 不要做的事

- 不要把 `frame` 当成业务页面 API 名继续扩散。
- 不要一次性全仓重命名 `View` 类或机械替换所有旧调用；兼容层应稳定保留。
- 不要新增 `current_view()`、`detect_view()` 这类新业务 API。
- 不要因为资产树里仍叫 frame/subframe/layer，就把 Runtime 业务接口重新叫回 view/frame。
