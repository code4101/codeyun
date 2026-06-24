# CodeYun 自动化协作交接

## 定位

本文档是 CodeYun 各个自动化之间的轻量协作空间。

它只保存需要跨自动化传递的长期结论，不保存截图、trace、stdout、原始 JSON 或大体积证据。临时证据仍写入系统临时目录或 CodeYun 数据目录，例如 `%TEMP%\codeyun\...`。

当前主要用途：

- `CodeYun 前端设计巡检` 发现 UI 症状背后疑似存在 API、DTO、数据结构或业务建模债务时，在这里登记。
- `CodeYun 代码健康优化` 读取这些条目，判断是否适合做只读模型审计、小步重构、状态投影收敛或继续拆分。

## 交接原则

- 前端巡检可以识别模型债务，但默认不直接重构后端领域模型。
- 代码健康优化接手的是“可验证的小切片”，不是一次性大重构。
- 文档只记录摘要、归因、建议接手方向和证据链接；原始证据放临时目录。
- 每个条目必须说明为什么它不是单纯 CSS 或文案问题。
- 接手方若无法自动修改，应至少产出模型审计、重构方案、验证命令或明确的人工决策点。

## 状态

- `open`：等待接手。
- `accepted`：已有自动化或人工开始处理。
- `fixed`：已完成并通过验证。
- `wontfix`：确认不是债务，或收益不值得成本。
- `needs-human-decision`：需要产品语义、权限、数据迁移或业务规则判断。

## 待代码健康优化接手

### UI-HANDOFF-20260623-002

- 状态：accepted
- 来源自动化：CodeYun 前端设计巡检 / 凡修自动化复盘
- 来源报告：`docs/凡修拜谒与行为树基础设施任务清单.md`
- 触发范围：`/fanxiu/data-annotation`、`/fanxiu/data-annotation/runtime`、凡修 Runtime 业务节点
- 表层症状：滚动查找、候选复位、点击后等待目标场景、OCR 精细点击这些通用动作已经反复出现，但业务实现仍容易临时覆盖滚动比例、手写等待、或把 OCR line 级结果当成可点击对象。
- 非前端根因判断：这不是单纯页面提示或文案问题，而是 Runtime/标注数据/API 之间的行为模型还不够正交。业务层需要的其实是“在某识别区滚动查找候选”“点击 shape 并等待声明落点”“按词/字级 OCR 计算动作落点”等通用能力；如果接口只暴露底层滚动、OCR 行和点击原语，前端标注页与业务节点都会重复解释同一套规则。
- 涉及对象：`backend/core/fanxiu/data_annotation/runtime_runner.py`、`backend/core/fanxiu/data_annotation/tasks/*`、`backend/core/ocr/preview.py`、`frontend/src/standard/fanxiu/data-annotation/page.vue`、`docs/凡修行为树业务能力约定.md`
- 已做前端止血：无前端止血。本轮已在 Runtime 层补默认滚动常量、`wait_click_then_view()` 和 `ocr_words_in_shapes()`，但仍缺少一次面向“业务节点是否还能绕过通用接口”的只读审计。
- 建议接手动作：只读模型审计，盘点凡修业务节点里直接调用底层滚动/OCR/点击的残留；将可复用模式归并为小型 Runtime helper 或标注协议字段；先产出候选清单，不直接大规模重构。
- 验证建议：`rg -n "scroll_shape_content\\(|drag|ocr_in_shape|ocr_words_in_shapes|wait_click_then_view|wait_click\\(" backend/core/fanxiu/data_annotation/tasks backend/core/fanxiu/data_annotation/runtime_runner.py -S`，再按候选运行对应 focused pytest；涉及真实动作的修改必须另走凡修 Runtime 真实自检。
- 风险和停手条件：如果候选涉及真实 MuMu/ADB 点击、缺少标注、或需要重新定义拜谒/邮件/日常任务完成态，不在代码健康自动化里直接改；只输出审计和拆分任务，等待人工确认或业务专项继续。

### UI-HANDOFF-20260623-001

- 状态：accepted
- 来源自动化：CodeYun 前端设计巡检
- 来源报告：`C:/Users/kzche/AppData/Local/Temp/codeyun/ui-design-audit/2026-06-23-frontend-design-audit-closeout/report.md`
- 触发范围：`bf505b478a6237364bd598c2c2e0359b1c5c472c..19a720628aad19a07a61eb117125a96af4600c35` / `/fanxiu/data-annotation/runtime`
- 表层症状：`作业` 表的一级 `下次触发` 列仍出现 `动态作业未记录下次时间` 这类解释型文案，把时间事实和规则/缺数说明揉进同一单元格。
- 非前端根因判断：行为树调度接口缺少面向一级状态表的稳定投影。前端当前只能把“没有有效下次时间 / 需要先求值”的后端缺口翻译成解释句，而不是渲染纯状态事实。
- 涉及对象：`backend/api/fanxiu.py`、`backend/core/fanxiu/data_annotation/*`、`frontend/src/standard/fanxiu/data-annotation-runtime/page.vue`
- 已做前端止血：无。本轮只在 `task-system` 健康条做了减法收敛，未继续扩散到需要调度语义判断的作业表。
- 建议接手动作：只读模型审计，判断是否应新增稳定状态投影，例如“有效下次时间 / 是否待求值 / 阻塞原因”，再由前端恢复一级列表的纯时间语义。
- 验证建议：`uv run pytest tests/test_fanxiu_data_annotation_scheduler.py tests/backend/test_fanxiu_runtime_view_model.py`，并打开 `http://127.0.0.1:5173/fanxiu/data-annotation/runtime` 复核动态作业行是否回到稳定状态表。
- 风险和停手条件：如果 `动态作业未记录下次时间` 背后其实承载多个不同业务阶段，不能只换文案或前端硬编码；需要先由人工或后端明确“缺时间”和“应执行”的正式状态边界。

新增条目模板：

```md
### UI-HANDOFF-YYYYMMDD-NNN

- 状态：open
- 来源自动化：CodeYun 前端设计巡检
- 来源报告：`%TEMP%\codeyun\ui-design-audit\...\report.md`
- 触发范围：`<commit-range>` / `<page-or-component>`
- 表层症状：`<用户在 UI 上看到的问题>`
- 非前端根因判断：`API/DTO/数据结构/业务建模中哪个边界不清>`
- 涉及对象：`<backend api/model/service/frontend page>`
- 已做前端止血：`无` / `<已完成的小修>`
- 建议接手动作：`只读模型审计` / `新增只读状态投影` / `小步重构` / `拆分任务`
- 验证建议：`<pytest/typecheck/build/service check/browser check>`
- 风险和停手条件：`<何时不能自动改，需人工判断>`
```

## 已接手记录

- `UI-HANDOFF-20260623-002`：已完成 Runtime 通用动作接口残留只读审计，归因为 Runtime action model / 业务节点投影债务；当前通用 helper 已覆盖 `wait_click_then_view`、`scroll_shape_content`、`nudge_shape_content_for_box`、`ocr_words_in_shapes`、`drag_shape_to_shape`，但业务层仍有 `shape.click(runtime)` 21 处、`click_shape_center` 31 处、`scroll_shape_content` 5 处。结论是不要批量替换普通 `wait_click`，下一步只处理明确小切片，例如 `xianfu.py` 的重复直接点击导航或 `daily_foundation.py` 的滚动查找候选，并且涉及真实点击前必须另走真实 Runtime 验收。报告：`%TEMP%\codeyun\idle-maintenance\20260624-000512-runtime_action_helper_residue_audit.json`；验证：`uv run pytest backend/tests/test_fanxiu_data_annotation_runtime_guard.py::test_runtime_drag_shape_to_shape_uses_runtime_drag backend/tests/test_fanxiu_data_annotation_runtime_guard.py::test_scroll_shape_content_uses_half_page_slow_drag backend/tests/test_fanxiu_data_annotation_runtime_guard.py::test_scroll_shape_content_can_limit_signature_to_recognition_shape tests/test_fanxiu_data_annotation_scheduler.py::test_runtime_ocr_words_in_shapes_requests_word_boxes_and_restores_crop_offset backend/tests/test_fanxiu_data_annotation_runtime_guard.py::test_debug_eval_context_exposes_wait_click_then_view -q --durations=10`，结果 `5 passed, 1 warning in 2.51s`。
- `UI-HANDOFF-20260623-001`：已完成只读模型审计，归因为 API/DTO 投影债务；现有后端动态作业 `next_time` 同步链路验证通过，下一步应补一级状态表可用的稳定 `next_trigger` 投影，而不是继续让前端用空 `next_time` + `schedule_kind` 推导解释文案。报告：`%TEMP%\codeyun\idle-maintenance\20260623-234942-runtime_next_trigger_projection_audit.json`；验证：`uv run pytest tests/test_fanxiu_data_annotation_scheduler.py::test_data_annotation_scheduler_syncs_dynamic_next_time_from_world_facts tests/test_fanxiu_data_annotation_scheduler.py::test_data_annotation_scheduler_syncs_retry_after_from_world_facts tests/test_fanxiu_data_annotation_scheduler.py::test_data_annotation_scheduler_sync_ignores_manual_pending_fact_next_time -q --durations=10`，结果 `3 passed, 1 warning in 2.31s`。

## 维护规则

- 前端巡检登记新条目时，优先放入“待代码健康优化接手”，状态为 `open`。
- 代码健康优化接手后，把条目状态改为 `accepted`，并在“已接手记录”补一行处理摘要或报告路径。
- 条目完成后改为 `fixed`，保留验证命令和结果摘要。
- 如果判断不是模型债务，改为 `wontfix` 并说明依据。
- 如果需要用户决策，改为 `needs-human-decision`，并把问题压缩成一个明确决策点。
