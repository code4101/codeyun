# 凡修 data-annotation 命名约定

> Last Updated: 2026-07-01

本文档是凡修 data-annotation / Runtime / Scheduler 的术语入口。

## 术语约定

### scene / frame / view

- scene 是主术语：表示 Runtime 能识别、能等待、能跳转的业务场景或页面编号，例如 `#34 世界`、`#121 邮件`。业务代码、公开 API、新文档和新日志优先使用 `scene`。
- frame 是底层细节：表示实时截图帧、`frame_data_url` 或 OCR/匹配输入。它不是业务 API 的主称呼。
- view 是旧版兼容：保留 `View` 类、`wait_view()`、`goto_view()` 等历史入口，避免破坏旧代码和测试。新增业务代码不要继续扩散 `view` 术语。

### 资产树 / 识别图

- 资产树 `asset tree` 是标注资产的数据容器：目录/分组、scene 数据、shape 数据、OCR/图像匹配配置、`sceneJumpTarget`、人工归档事实都放在这里。目录嵌套只表示人工组织方式，不天然等于运行时 sub scene 关系。
- 识别图 `recognition graph` 是 Runtime 根据真实 `match(s, x)` 关系动态构建的有向图：`match(s, x)` 成立即建立 `s -> x`。
- 当前画面与命中的候选场景构成匹配子图；先按候选到当前画面的图距离选最近节点，同距离时再用参考图与当前画面的全图相似度决胜。
- 识别图没有父子、祖先、后代、root 或 sub-scene 语义。资产目录嵌套只负责人工组织，不能生成识别边、扩展候选或参与去歧义。

### layer / layer0

- `layer` 只用于选择默认候选批次，不构成图层级，也不表达场景父子关系。
- `layer0` 是某次 Runtime 动作产生的动态优先候选集，通常来自当前点击 shape 的 `sceneJumpTarget`，也可以来自调用方显式目标。它不是资产图片的静态 `layer` 字段，也不是“某些 scene 被标为 layer0”。
- `layer0_wait_seconds` 是 `layer0` 的优先等待窗口。默认 30 秒；长过场入口可显式调大，例如仙府进入 `#171` 可用 60 秒。

## 主 API

- `current_scene()`：识别当前场景。
- `wait_scene()`：等待一个或多个目标场景出现。
- `go_scene()`：移动到目标场景，背后使用场景图和 `sceneJumpTarget` 做路径规划。

历史入口：

- `wait_view()`、`goto_view()` 和 `View` 仅是现存历史命名；新代码使用 scene API。

## 判断规则

- 写业务流程时，说“进入/等待/返回/跳转某页面”，用 `scene`。
- 写截图、OCR、匹配、裁剪、缓存、实时画面输入时，用 `frame`。
- 只有解释现存历史代码时才使用 `view`。

## 不要做的事

- 不要把 `frame` 当成业务页面 API 名继续扩散。
- 不要新增 `current_view()`、`detect_view()` 这类新业务 API。
- 不要从资产目录、图片 children 或人工父字段生成任何识别关系。
