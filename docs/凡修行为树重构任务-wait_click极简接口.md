# 凡修行为树重构任务：wait_click 极简接口

## 背景

凡修 data-annotation Runtime 目前已经具备若干底层能力：场景识别、shape 查找、shape 条件匹配、OCR 匹配、图像匹配、固定坐标点击、浮动按钮点击等。但业务层在表达流程时仍容易落入“找 shape、判断条件、等待、点击、处理浮动”的细节。

目标是把这些细节收敛到标注层和 Runtime 基础设施里，让业务逻辑可以用接近如下形式描述：

```python
yield from wait_click("#247", "[秘藏阁]")
```

业务层只说明“在哪个帧语义下，操作哪个标注目标”。是否等待场景、是否 OCR、是否图像匹配、是否浮动、是否是父子 shape、是否固定坐标点击，都应由标注数据和基础设施决定。

## 设计审查

这个方向是正交的，适合重构：

- 业务层只表达任务步骤，不直接关心坐标、OCR、图像匹配、浮动模板。
- 标注层负责声明目标是什么，以及目标如何被识别。
- Runtime 负责把标注声明解释成等待、匹配和点击动作。
- Scheduler/作业层不参与 GUI 细节，只负责触发和互斥调度。

需要避免的问题：

- 不要把 `#247`、`秘藏阁` 这类具体业务写死进 Runtime。
- 不要为每个业务任务单独写“父区域内 OCR 子目标”的特判。
- 不要让“浮动 shape 没条件怎么办”散落在业务代码里。
- 不要让 shape 重名时默默点第一个，必须支持精确选择或报出候选路径。

## 极简接口语义

建议统一入口：

```python
yield from wait_click(frame, shape, **options)
```

常见用法：

```python
yield from wait_click("#247", "[秘藏阁]")
yield from wait_click("#247", "[下方菜单/秘藏阁]")
yield from wait_click(247, "秘藏阁")
yield from wait_click(None, "[确认]")
```

参数语义：

- `frame`：帧选择器，可以是 `"#247"`、`247`、场景 key，或 `None`。
- `shape`：shape 选择器，可以是 `"秘藏阁"`、`"[秘藏阁]"`、`"[下方菜单/秘藏阁]"`。
- `options`：只放通用策略，例如 timeout、点击比例、是否允许多候选取第一个等。

## 等待与点击规则

`wait_click(frame, shape)` 的规则应固定在基础设施中：

1. 如果 `frame` 能解析到帧，并且该帧有场景标识：
   - 先等待当前画面匹配该场景。
   - 再解析并操作 shape。

2. 如果 `frame` 能解析到帧，但该帧没有场景标识：
   - 不强行场景等待。
   - 直接按该帧坐标系解析 shape 并操作。

3. 如果 `frame is None`：
   - 不做场景等待。
   - 只根据 shape 选择器和当前上下文操作。
   - 这适合已经由上一步保证场景的连续动作。

4. 如果 shape 有图像或 OCR 条件：
   - 先等待条件命中。
   - 命中后点击实际 resolved box 的目标点。

5. 如果 shape 没有图像和 OCR 条件：
   - 退化为固定坐标点击。
   - 即使 shape 标了 `floating=True`，没有匹配条件时也只能固定点击，并记录日志提示。

6. 如果 shape 是嵌套 shape：
   - 父 shape 默认作为检索区域。
   - 子 shape 的 OCR/图像匹配应优先限制在父区域内。
   - 例如 `#247 [下方菜单/秘藏阁]`，业务层不需要再写“在下方菜单区域 OCR 秘藏阁”。

## shape 选择器规则

基础设施应支持两级选择：

- 简写：`[秘藏阁]`
  - 当当前帧内只有一个同名 shape 时可用。
  - 如果有多个同名 shape，应报错并列出候选路径。

- 精确路径：`[下方菜单/秘藏阁]`
  - 用于 shape 重名、嵌套结构、局部检索区域。
  - 路径按标注树 title 匹配。

建议错误信息示例：

```text
shape 选择器 [确认] 命中多个目标：
- [提示/确认]
- [离开确认/确认]
请使用精确路径。
```

## 标注层约定

shape 的行为由标注字段决定：

- 有 `imageMatchRole != off`：可图像匹配。
- 有 `ocrMatchRole != off` 且 `ocrText` 非空：可 OCR 匹配。
- 有 `floating=True` 且有匹配条件：在匹配结果处点击。
- 有 `floating=True` 但无匹配条件：只能固定坐标点击。
- 有父 shape：父 shape 作为子 shape 的默认检索区域。
- 没有父 shape：在整帧内匹配。

这类规则属于标注层 + Runtime 基础设施，不应进入具体业务任务。

## 重构实施步骤

1. 梳理现有 Runtime 点击入口
   - `_find_shape`
   - `_click_shape`
   - `_wait_shape_match`
   - `_click_shape_respecting_conditions`
   - `DataAnnotationRuntimeDebugContext.tap_shape`

2. 增加通用选择器解析
   - 解析 `#247` / `247` / scene key。
   - 解析 `[秘藏阁]` / `[下方菜单/秘藏阁]`。
   - 支持嵌套 shape 路径。
   - 重名时不默认猜测。

3. 增加统一 Runtime API
   - `wait_click(frame, shape, **options)`。
   - 内部串联场景等待、shape 解析、条件等待、点击。
   - 保持 generator 语义，能在行为树中 `yield from`。

4. 增加 debug_eval API
   - `ctx.wait_click("#247", "[秘藏阁]")`。
   - 用于手动验证标注和 Runtime 行为。

5. 替换新业务中的散装逻辑
   - 新增任务优先使用 `wait_click`。
   - 旧任务暂不大规模迁移，先在日常_仙市、副本、游历等新流程里打磨接口。

6. 补测试
   - 无条件 shape：固定点击。
   - 有 OCR 条件 shape：等待 OCR 命中后点击。
   - 有图像条件 shape：等待图像命中后点击。
   - 浮动但无条件 shape：固定点击并记录提示。
   - 嵌套 shape：父区域约束子 shape。
   - 重名 shape：简写报错，路径选择成功。

## 验收标准

最小验收：

- `ctx.wait_click("#247", "[秘藏阁]")` 能在 debug_eval act 模式表达并执行。
- `ctx.wait_click("#247", "[下方菜单/秘藏阁]")` 能精确命中嵌套 shape。
- shape 无 OCR/图像条件时仍能点击。
- frame 无场景标识时不会因无法等待场景而失败。
- 重名 shape 不再静默点错。

真实运行验收：

- 从 #34 进入仙市。
- 在 #247 通过 `wait_click("#247", "[秘藏阁]")` 或精确路径进入秘藏阁。
- Runtime 日志能看出：等待场景、解析 shape、匹配条件或固定点击的过程。
- 若目标是浮动 OCR 子 shape，点击点应来自实际 OCR/匹配结果，而不是误用父区域中心。

## 凌晨重构注意事项

- 先只做基础设施，不继续推进业务规则。
- 不要重构 Scheduler 作业互斥逻辑，除非它直接阻塞 `wait_click` API。
- 不要把已有业务流程整体翻新，先保留行为稳定性。
- 不要使用私有函数或静态截图冒充验收；凡修 Runtime 相关验证必须走真实 Runtime 入口和真实游戏画面。
