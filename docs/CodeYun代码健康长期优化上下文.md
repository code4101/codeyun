# CodeYun 代码健康长期优化上下文

> Last Updated: 2026-06-18
> Purpose: 给长期自动化和后续 AI 会话保存代码健康优化的规划、候选队列、验证命令和已完成切片，避免每轮只重复扫描。

## 定位

这是 `CodeYun 代码健康优化` 自动化的长期上下文文档。

它不是一次性扫描报告，也不是提交记录。它负责沉淀：

- 高复杂模块地图
- 当前优先候选
- 每个候选的证据、风险和验证命令
- 已完成的小切片和可继续推进的下一步
- 哪些扫描结果属于噪声或低价值候选

临时 JSON、性能探针原始输出、pytest stdout 等仍写入系统临时目录，例如：

```text
%TEMP%\codeyun\idle-maintenance\
```

本文件只保留可累积的结论和下一步任务。

## 自动化执行原则

每轮触发时，只要后台队列空闲且没有硬阻塞，就必须推进一个具体、可验证的小切片。

每轮任务必须明确：

- `object`: 具体文件、函数、接口、测试或文档段落
- `action`: 本轮要做的动作
- `success_metric`: 可判断是否完成的指标
- `verification`: 测试、性能 baseline、引用证据或机器可读报告

如果工作区已有未提交变更：

- 不直接放弃。
- 优先做只读分析、候选细化、性能 baseline、验证命令设计。
- 也可以修改与现有变更不冲突的独立文件，但必须明确不混入用户改动。

## 高复杂模块地图

优先关注这些长期高收益区域：

| 区域 | 重点对象 | 主要风险/收益 |
| --- | --- | --- |
| 后端大 API | `backend/api/filesystem.py`, `backend/api/fanxiu.py`, `backend/api/eastmoney.py`, `backend/api/device_entries.py`, `backend/api/note_sheets.py` | 大文件、长函数、路由和业务逻辑混杂 |
| 凡修 Runtime | `backend/core/fanxiu/data_annotation/`, `backend/core/fanxiu/runtime/` | 状态流转复杂、真实运行验证要求高 |
| Runtime/后台任务 | `backend/core/runtime/`, `backend/api/task_manager.py` | 长驻任务、队列状态、重试和日志路径 |
| 前端高复杂页面 | `frontend/src/standard/cluster/`, `frontend/src/standard/fanxiu/`, `frontend/src/components/ImageGalleryWorkspace.vue` | 状态重复、请求重复、组件过大 |
| 数据/文件/扫描链路 | 文件系统扫描、Everything、图片/文档处理、缓存、序列化、数据库查询 | 慢接口、重复 IO、响应体过大 |

## 当前主线：`backend/api/filesystem.py` 重复文件分析瘦身

### 背景

`backend/api/filesystem.py` 是文件系统和媒体/重复文件相关能力的大型接口文件。此前 `code_slimming_scan` 只把它识别为大文件候选；后续已进一步细化到函数级候选。

当前主目标：

```text
backend/api/filesystem.py::_run_duplicate_analysis_task
```

原始候选状态：

- 文件约 6074 行。
- 文件内约 203 个函数。
- `_run_duplicate_analysis_task` 原始约 251 行。
- 职责混合：缓存命中、Everything 查询、文件系统遍历、进度发布、分组、缓存写入、失败状态落盘。

### 已完成切片

| 日期 | 切片 | 结果 | 验证 |
| --- | --- | --- | --- |
| 2026-06-18 | 函数级候选细化 | 生成 `filesystem_function_slimming_candidate_refinement` 报告，确定 `_run_duplicate_analysis_task` 为首个目标 | 只读 AST/引用分析 |
| 2026-06-18 | baseline 建立 | 将目标函数拆成 cache-hit、Everything、filesystem traversal、failure 4 个阶段候选 | `3 passed`, pytest reported `0.96s` |
| 2026-06-18 | cache-hit 分支抽取 | 新增 `_try_complete_duplicate_analysis_task_from_candidate_cache(...)`，目标函数减少 25 行 | `3 passed`, pytest reported `0.48s` |
| 2026-06-18 | Everything 分支抽取 | 新增 `_try_complete_duplicate_analysis_task_from_everything(...)`，目标函数再减少 50 行，累计减少 75 行 | `3 passed`, pytest reported `0.94s` |
| 2026-06-18 | filesystem traversal 分支抽取 | 新增 `_complete_duplicate_analysis_task_from_filesystem(...)`，目标函数再减少 99 行，累计从 251 行降到 77 行 | `3 passed`, pytest reported `0.52s` |
| 2026-06-18 | failure 分支抽取 | 新增 `_fail_duplicate_analysis_task(...)`，目标函数再减少 10 行，当前为 67 行 | `3 passed`, pytest reported `0.45s` |
| 2026-06-18 | 当前函数主线复盘 | 只读评估 helper 参数、闭包依赖和测试覆盖；结论是当前函数瘦身线应在现有改动提交后收束 | `3 passed`, pytest reported `0.46s` |
| 2026-06-18 | 重复文件分析行为补测 | 新增 Everything unavailable 与 `scan_limit` 截断行为测试 | `7 passed`, pytest reported `0.87s` |

当前验证命令：

```powershell
uv run pytest tests/backend/test_device_entry_proxy.py::test_local_entry_proxy_lists_duplicate_files_by_size_and_hash tests/backend/test_device_entry_proxy.py::test_local_entry_proxy_duplicate_files_uses_snapshot_for_pagination tests/backend/test_device_entry_proxy.py::test_local_entry_proxy_duplicate_file_task_reports_result_and_filters_paths -q --durations=10
```

### 下一步候选

当前结论：

- `_run_duplicate_analysis_task` 已从 251 行降到 67 行，剩余职责主要是阶段编排和统一失败捕获。
- 当前不建议继续为了减少行数而引入上下文对象；helper 最大参数数为 11，确实偏多，但在已有未提交改动较多时继续抽象会扩大风险。
- `publish_partial` 仍是 `_complete_duplicate_analysis_task_from_filesystem(...)` 内部函数。它闭包依赖 `task_id/rules/sort_mode/groups/hash_computed_count`，此时提升为独立 helper 会增加参数噪声，暂不作为立即修改项。

优先级 1 已完成：

- `object`: `backend/api/filesystem.py` 重复文件分析行为覆盖
- `action`: 已增补 Everything unavailable 与 `scan_limit` 导致 `complete=False` 的聚焦测试
- `success_metric`: 异常路径和截断扫描行为已有显式测试覆盖，为后续参数治理/上下文对象抽象提供保护
- `risk`: low-medium
- `verification`: `uv run pytest tests/backend/test_device_entry_proxy.py -q -k "duplicate" --durations=10`

新的优先级 1：

- `object`: `backend/api/filesystem.py` 重复文件分析主线
- `action`: 等当前 `backend/api/filesystem.py` 和相关测试改动提交后，将此主线标记为阶段完成；下一条高收益主线转向 docs 扫描噪声治理或另一个大文件函数级候选
- `success_metric`: 当前主线有提交边界；长期上下文中记录下一主线入口
- `risk`: low
- `verification`: 提交前继续跑 duplicate 相关测试

优先级 2：

- `object`: `publish_partial` 内部函数
- `action`: 评估是否提升为独立 helper，减少闭包依赖
- `success_metric`: 闭包依赖减少，后续 traversal 分支更易拆分
- `risk`: medium
- `verification`: 同重复文件测试，并重点关注部分进度状态字段

优先级 3：

- `object`: `backend/api/filesystem.py` 重复文件分析 helper 群
- `action`: 评估是否引入轻量上下文对象，减少多个 helper 之间重复传递 `query_signature/resolved/rules/sort_mode` 等参数
- `success_metric`: 不改变行为的前提下降低参数噪声；只有当签名简化明显时才执行
- `risk`: medium
- `verification`: 同重复文件测试；不做大范围抽象

## 已知扫描噪声

### `docs_sync_scan`

当前 docs 路径扫描经常报告 50 条候选，但主要噪声集中在：

- `backend/codeyun_backend.egg-info/SOURCES.txt`
- `AGENTS.md` 中的通配路径、示例路径或历史路径

后续不要直接把这些当作文档事实错误修正。更优先的小切片是：

- 从 docs 扫描器中排除 `*.egg-info/SOURCES.txt`
- 识别 `**` 通配路径为模式而不是真实路径
- 对明确的仓库路径和示例路径做不同分类

### `code_slimming_scan`

大文件扫描长期候选包括：

- `backend/core/fanxiu/catalog/hot_update.py`
- `backend/tests/test_fanxiu_resources.py`
- `frontend/src/standard/notes/components/NoteSheetWorkspace.vue`
- `frontend/src/standard/fanxiu/wiki/page.vue`
- `backend/api/note_sheets.py`

这些候选不能只停留在“大文件”层面。下一步必须像 `filesystem.py` 一样细化到函数、组件区块、测试夹具或数据块。

## 报告约定

每轮长期优化可以写机器可读报告到：

```text
%TEMP%\codeyun\idle-maintenance\
```

建议 `task_key` 使用稳定命名，例如：

- `filesystem_function_slimming_candidate_refinement`
- `filesystem_duplicate_analysis_slice_baseline`
- `safe_code_slimming_filesystem_duplicate_cache_branch`
- `safe_code_slimming_filesystem_duplicate_everything_branch`

报告必须包含：

- `target`
- `success_metric`
- `verification.command`
- `verification.status`
- `next_candidate`

## 维护本文件的规则

当自动化完成以下任一结果时，应更新本文件：

- 完成一个安全代码瘦身切片
- 建立新的性能 baseline
- 确认一个高价值候选或淘汰一个误报
- 改变主线优先级
- 新增标准验证命令

不要把大段 stdout、截图、原始 JSON、完整 diff 粘贴进本文档；这些属于临时产物。
