# CodeYun 代码健康长期优化上下文

> Last Updated: 2026-06-23
> Purpose: 给长期自动化和后续 AI 会话保存代码健康优化的规划、候选队列、验证命令和已完成切片，避免每轮只重复扫描。

## 定位

这是 `CodeYun 代码健康优化` 自动化的长期上下文文档。

它不是一次性扫描报告，也不是提交记录。它负责沉淀：

- 高复杂模块地图
- 当前优先候选
- 来自其他自动化的模型债务交接
- 每个候选的证据、风险和验证命令
- 已完成的小切片和可继续推进的下一步
- 哪些扫描结果属于噪声或低价值候选

临时 JSON、性能探针原始输出、pytest stdout 等仍写入系统临时目录，例如：

```text
%TEMP%\codeyun\idle-maintenance\
```

本文件只保留可累积的结论和下一步任务。

跨自动化交接队列位于：

```text
docs/CodeYun自动化协作交接.md
```

每轮代码健康优化开始时，应先检查其中 `open` 或 `accepted` 状态的条目。若条目来自 `CodeYun 前端设计巡检`，优先判断它是否揭示了 API、DTO、数据结构或业务建模不正交，而不是只把它当作前端样式问题。

## 自动化执行原则

每轮触发时，只要后台队列空闲且没有硬阻塞，就必须推进一个具体、可验证的小切片。

每轮任务必须明确：

- `object`: 具体文件、函数、接口、测试或文档段落
- `action`: 本轮要做的动作
- `success_metric`: 可判断是否完成的指标
- `verification`: 测试、性能 baseline、引用证据或机器可读报告

如果本轮接手交接条目，还必须明确：

- `handoff_id`: `docs/CodeYun自动化协作交接.md` 中的条目编号
- `root_cause_level`: API/DTO 数据投影、后端状态模型、业务对象建模、或误报
- `handoff_update`: 本轮结束后如何更新交接条目状态

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

## 前端巡检交接处理规则

当 `docs/CodeYun自动化协作交接.md` 中存在 `open` 条目时，代码健康优化应把它视为候选来源之一。

优先处理满足以下条件的条目：

- 表层 UI 症状已经由真实页面、截图或报告证明。
- 前端巡检已经说明它为什么不是单纯 CSS/文案问题。
- 涉及对象能收敛到一个 API、DTO、状态投影、服务函数或业务对象。
- 有可运行的验证命令，或能先建立只读审计报告。

可自动接手的动作：

- 只读模型审计：梳理规则、状态、命令、结果、展示投影是否混杂。
- 状态投影收敛方案：建议或实现小范围只读 DTO/API projection，减少前端推断。
- 小步重构：在测试覆盖足够且边界明确时，拆出更正交的 helper、schema 或 projection builder。
- 验证命令设计：为后续安全重构补上最小测试或静态检查。

不得自动接手的动作：

- 需要重新定义业务流程。
- 需要数据库大迁移或广泛兼容层。
- 需要新增用户可见实体、权限语义或危险数据写入流程。
- 需要一次性改完多条业务链路。

接手后更新 `docs/CodeYun自动化协作交接.md`：

- 开始处理：把状态改为 `accepted`，记录本轮报告路径。
- 完成：改为 `fixed`，记录验证命令和结果摘要。
- 判断不是债务：改为 `wontfix`，说明依据。
- 需要用户判断：改为 `needs-human-decision`，写清唯一决策点。

当前新增交接候选：

- `UI-HANDOFF-20260623-002`：凡修 Runtime 通用动作接口易被业务层绕过。下一轮适合先做只读审计，盘点 `tasks/*` 中直接使用底层滚动、OCR line、点击和等待的残留，输出“可收敛为通用 helper / 需要真实 Runtime 验证 / 暂停等待人工”的三类清单；不要在拜谒确认态未闭环前做跨任务大重构。

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
| 2026-06-18 | 提交前扩展验证 | `test_device_entry_proxy.py -k duplicate` 通过；`test_device_media_duplicate_cluster_sort.py` 暴露 2 个 visual hash prewarm 夹具失败 | `7 passed`; `2 failed, 6 passed` |
| 2026-06-18 | visual hash prewarm 夹具修复 | 测试夹具补齐 `ResourceIdentity` 表，metadata upsert 能走到 prewarm 调度分支 | `8 passed`; duplicate API `7 passed` |
| 2026-06-18 | 提交边界就绪验证 | 当前剩余改动为测试夹具和长期上下文文档；提交门槛测试通过 | media duplicate cluster `8 passed`; duplicate API `7 passed` |

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

当前扩展验证状态：

- `uv run pytest tests/backend/test_device_entry_proxy.py -q -k "duplicate" --durations=10` 通过，`7 passed`。
- `uv run pytest backend/tests/test_device_media_duplicate_cluster_sort.py -q --durations=10` 失败，`2 failed, 6 passed`。
- 失败测试：
  - `test_attach_cached_media_metadata_schedules_visual_hash_prewarm_for_browse_requests`
  - `test_list_supported_entries_prewarms_visual_hash_when_duplicate_cluster_rule_active`
- 初步定位：失败发生在 visual hash prewarm 调度断言；`_attach_cached_media_metadata` 只有在 `upsert_device_file_metadata_batch -> reconcile_device_file_batch -> ensure_device_file_resource_identity` 成功后才调用 `_schedule_visual_hash_prewarm`，而测试夹具只创建了 `DeviceFile` 表，可能缺少 `ResourceIdentity` 等资源身份表，导致 upsert 异常被业务代码吞掉，调度未发生。
- 这不是重复文件分析 API 主线的直接回归，但会影响提交前完整验证口径。

优先级 1A 已完成：

- `object`: `backend/tests/test_device_media_duplicate_cluster_sort.py` visual hash prewarm 夹具
- `action`: 已补齐测试数据库表，创建 `ResourceIdentity` 与 `DeviceFile`，使 visual hash prewarm 测试真正覆盖调度逻辑
- `success_metric`: `uv run pytest backend/tests/test_device_media_duplicate_cluster_sort.py -q --durations=10` 通过
- `risk`: low-medium
- `verification`: `8 passed`, pytest reported `1.21s`

新的优先级 1B：

- `object`: 当前 filesystem 重复文件分析主线提交边界
- `action`: 当前提交边界已就绪，等待自动提交维护接手或人工提交；提交前保持这组验证命令为门槛
- `success_metric`: `device_entry_proxy -k duplicate` 与 `test_device_media_duplicate_cluster_sort.py` 均通过
- `risk`: low
- `verification`: `uv run pytest tests/backend/test_device_entry_proxy.py -q -k "duplicate" --durations=10` 和 `uv run pytest backend/tests/test_device_media_duplicate_cluster_sort.py -q --durations=10`
- 当前剩余文件范围：
  - `backend/tests/test_device_media_duplicate_cluster_sort.py`
  - `docs/CodeYun代码健康长期优化上下文.md`

下一条主线候选：

- `object`: `docs_sync_scan` 噪声治理
- `action`: 从扫描器中排除 `*.egg-info/SOURCES.txt`，并把 `**` 通配路径分类为模式引用而非缺失路径
- `success_metric`: `docs_sync_scan` 的固定噪声候选下降，同时保留真实缺失路径报告能力
- `risk`: low
- `verification`: `uv run pytest backend/tests/test_idle_maintenance.py -q`，并运行一次只读 `docs_sync_scan` 报告对比

优先级 1C 已完成：

- `object`: `backend/core/maintenance/idle_maintenance.py` 文档路径扫描器
- `action`: 已排除 `*.egg-info/SOURCES.txt` 打包清单，并过滤 `**` glob 模式与 `backend.core...` 这类代码标识符引用
- `success_metric`: 两类固定误报不再进入 `missing_doc_path_ref`，真实缺失仓库路径仍会报告
- `risk`: low
- `verification`: `uv run pytest backend/tests/test_idle_maintenance.py -q --durations=10` 通过，`9 passed`
- `report`: `%TEMP%\codeyun\idle-maintenance\20260618-201755-docs_sync_scan.json`

新的优先级 1D：

- `object`: `docs_sync_scan` Markdown 引用提取边界
- `action`: 处理 Windows 绝对路径、带空格的 Markdown 链接路径和说明性目录引用，避免把非仓库相对路径或被截断的链接当成缺失路径
- `success_metric`: 只读扫描中的明显解析噪声减少，同时保留 `backend/...`、`frontend/...`、`docs/...` 真实缺失路径候选
- `risk`: low
- `verification`: 新增聚焦测试覆盖绝对路径和带空格链接；运行 `uv run pytest backend/tests/test_idle_maintenance.py -q --durations=10` 与一次只读 `docs_sync_scan`

优先级 1D 已完成：

- `object`: `backend/core/maintenance/idle_maintenance.py` Markdown/路径引用提取
- `action`: 已过滤 Windows 盘符绝对路径、`%TEMP%` 环境变量路径；裸路径不再从 Markdown 链接括号中截断，中文仓库路径不再被截成 ASCII 前缀
- `success_metric`: `README.md` 中的 `[docs/自动部署恢复档案.md](...)`、长期上下文中的 `%TEMP%\...docs_sync...`、`docs/CodeYun代码健康长期优化上下文.md` 不再进入前排误报；真实缺失路径仍保留
- `risk`: low
- `verification`: `uv run pytest backend/tests/test_idle_maintenance.py -q --durations=10` 通过，`11 passed`
- `report`: `%TEMP%\codeyun\idle-maintenance\20260618-203225-docs_sync_scan.json`

新的优先级 1E：

- `object`: `docs/FANXIU_REVERSE_CONTEXT.md` 旧 core 模块名漂移
- `action`: 对扫描前排的旧 Fanxiu 扁平 core 模块候选做事实核验，区分已迁移模块、已删除模块和应改为当前入口的模块
- `success_metric`: 生成候选排序；若事实明确，修正 1-3 个旧路径引用
- `risk`: low
- `verification`: `rg` 引用核验，必要时运行 `uv run pytest backend/tests/test_idle_maintenance.py -q --durations=10` 和一次只读 `docs_sync_scan`

优先级 1E 已完成：

- `object`: `docs/FANXIU_REVERSE_CONTEXT.md` 旧 Fanxiu 扁平 core 模块路径
- `action`: 已将模块表和高价值 addendum 中的旧路径更新为当前 `backend/core/fanxiu/catalog/...` 或 `backend/core/fanxiu/runtime/download_bridge.py` 路径
- `success_metric`: 13 个旧路径均核验到当前存在文件；旧 Fanxiu 扁平 core 模块路径无剩余匹配；`docs_sync_scan` 中该文档旧扁平模块候选清零
- `risk`: low
- `verification`: `uv run pytest backend/tests/test_idle_maintenance.py -q --durations=10` 通过，`11 passed`；只读 `docs_sync_scan` 从 40 条降到 39 条
- `report`: `%TEMP%\codeyun\idle-maintenance\20260618-204850-docs_sync_scan.json`

新的优先级 1F：

- `object`: `docs/FANXIU_REVERSE_CONTEXT.md` 前端页面路径漂移
- `action`: 核验旧 packet-capture 与 protocol-semantics 独立页面是否迁移、合并或已废弃，并修正文档事实
- `success_metric`: `docs_sync_scan` 中该文档剩余前端页面路径候选减少；不误删仍有产品意义的历史说明
- `risk`: low
- `verification`: `rg --files frontend/src/standard/fanxiu` 路径核验，一次只读 `docs_sync_scan`

优先级 1F 已完成：

- `object`: `docs/FANXIU_REVERSE_CONTEXT.md` 前端页面路径漂移
- `action`: 已确认旧 packet-capture 与 protocol-semantics 独立页面合并进 `frontend/src/standard/fanxiu/wiki/page.vue`，入口分别为 `/fanxiu/wiki?tab=packet` 与 `/fanxiu/wiki?tab=protocol`
- `success_metric`: `docs_sync_scan` 中 `docs/FANXIU_REVERSE_CONTEXT.md` 的缺失路径候选清零；前端和 API 集成的说明性短语也改为非路径措辞
- `risk`: low
- `verification`: `rg --files frontend/src/standard/fanxiu` 路径核验；`uv run pytest backend/tests/test_idle_maintenance.py -q --durations=10` 通过，`11 passed`；只读 `docs_sync_scan` 为 39 条且该文档无候选
- `report`: `%TEMP%\codeyun\idle-maintenance\20260618-210214-docs_sync_scan.json`

新的优先级 1G：

- `object`: `docs/AI_CLUSTER_CONTEXT.md` 集群旧后端/前端路径漂移
- `action`: 核验集群上下文中旧设备 core、agent API、TaskManager 前端页是否已有当前等价入口，先产出映射证据，再做事实性文档修正
- `success_metric`: `docs_sync_scan` 前排候选减少，且每个替换路径都能通过 `Test-Path` 或 `rg --files` 证明存在
- `risk`: low
- `verification`: `rg`/`Test-Path` 路径核验，一次只读 `docs_sync_scan`

优先级 1G 已完成：

- `object`: `docs/AI_CLUSTER_CONTEXT.md` 集群旧路径和旧接口说明
- `action`: 已将旧设备 core、agent API、TaskManager 前端页路径更新为当前 `backend/core/devices/device.py`、`backend/api/device_control.py`、`backend/api/task_manager.py`、`frontend/src/standard/cluster/tasks/page.vue`；旧 `/api/agent/status` 与 `/api/task/...` 示例同步为当前 `/api/device-control/status` 与 `/api/tasks/...`
- `success_metric`: 新路径均通过 `Test-Path`；旧路径/旧接口 `rg` 无剩余匹配；`docs_sync_scan` 中 `docs/AI_CLUSTER_CONTEXT.md` 候选清零
- `risk`: low
- `verification`: `uv run pytest backend/tests/test_idle_maintenance.py -q --durations=10` 通过，`11 passed`；只读 `docs_sync_scan` 为 33 条且该文档无候选
- `report`: `%TEMP%\codeyun\idle-maintenance\20260618-211734-docs_sync_scan.json`

新的优先级 1H：

- `object`: `docs/standard与plugins结构约定.md` 模板路径误报
- `action`: 区分 `<插件名>`、`<域>` 这类模板路径与真实仓库路径，优先判断是改扫描规则还是改文档措辞
- `success_metric`: docs scan 中模板路径候选下降，同时保留真实缺失路径检测
- `risk`: low
- `verification`: 聚焦测试覆盖尖括号模板路径；一次只读 `docs_sync_scan`

优先级 1H 已完成：

- `object`: `docs_sync_scan` 模板路径识别
- `action`: 扫描器已过滤包含尖括号占位符的模板路径，例如插件名、领域名这类说明性路径
- `success_metric`: `docs/standard与plugins结构约定.md` 的模板路径候选清零；真实缺失路径测试仍保留
- `risk`: low
- `verification`: `uv run pytest backend/tests/test_idle_maintenance.py -q --durations=10` 通过，`12 passed`；只读 `docs_sync_scan` 为 28 条且该文档无候选
- `report`: `%TEMP%\codeyun\idle-maintenance\20260618-213148-docs_sync_scan.json`

新的优先级 1I：

- `object`: 凡修运行/抓包文档旧 core 路径漂移
- `action`: 核验凡修运行设备约定和抓包服务架构约定中旧 `fanxiu_*` 扁平 core 路径的当前包路径，优先修正 3-5 个前排候选
- `success_metric`: `docs_sync_scan` 前排凡修旧路径候选减少；每个替换路径都有存在性证据
- `risk`: low
- `verification`: `rg --files backend/core/fanxiu` 映射核验，一次只读 `docs_sync_scan`

优先级 1I 已完成：

- `object`: `docs/凡修data-annotation运行设备约定.md` 与 `docs/凡修抓包服务架构约定.md` 旧 core 路径
- `action`: 已将 MuMu 控制、窗口捕获、抓包 runtime、tcp flow、洞察 worker、活动同步、业务 store、玩家面板 store 等旧扁平路径更新为当前 `backend/core/fanxiu/runtime/...`、`backend/core/fanxiu/packet/...` 或 `backend/core/devices/...` 路径
- `success_metric`: 9 个替换路径均通过 `Test-Path`；两个目标文档旧路径候选清零；`docs_sync_scan` 总候选从 28 降到 19
- `risk`: low
- `verification`: `uv run pytest backend/tests/test_idle_maintenance.py -q --durations=10` 通过，`12 passed`；只读 `docs_sync_scan` 为 19 条且两个目标文档无候选
- `report`: `%TEMP%\codeyun\idle-maintenance\20260618-214724-docs_sync_scan.json`

新的优先级 1J：

- `object`: `docs/凡修行为树运行框架约定.md` 旧 core 路径漂移
- `action`: 核验行为树、data-annotation runner/runtime_control、game window actions 等旧扁平路径的当前包路径，优先修正前排候选
- `success_metric`: `docs_sync_scan` 中行为树文档旧路径候选减少；每个替换路径都有存在性证据
- `risk`: low
- `verification`: `rg --files backend/core/fanxiu` 映射核验，一次只读 `docs_sync_scan`

优先级 1J 已完成：

- `object`: `docs/凡修行为树运行框架约定.md` 旧 core 路径漂移
- `action`: 已将行为树门面、data-annotation runner/runtime_control/default_jobs/debug_eval、game window actions、runtime errors、mail runtime store 等旧扁平 import/path 更新为当前包路径
- `success_metric`: 9 个当前路径均通过 `Test-Path`；行为树文档旧路径候选清零；`docs_sync_scan` 总候选从 19 降到 14
- `risk`: low
- `verification`: `uv run pytest backend/tests/test_idle_maintenance.py -q --durations=10` 通过，`12 passed`；只读 `docs_sync_scan` 为 14 条且目标文档无候选
- `report`: `%TEMP%\codeyun\idle-maintenance\20260618-220419-docs_sync_scan.json`

新的优先级 1K：

- `object`: `docs/凡修逆向资源安全边界.md` 旧资源模块路径漂移
- `action`: 核验旧资源模块路径当前是否迁移到 `backend/core/fanxiu/catalog/resources.py`，并修正事实性路径
- `success_metric`: `docs_sync_scan` 中该文档旧资源模块候选清零；替换路径有存在性证据
- `risk`: low
- `verification`: `Test-Path backend/core/fanxiu/catalog/resources.py`，一次只读 `docs_sync_scan`

优先级 1K 已完成：

- `object`: `docs/凡修逆向资源安全边界.md` 旧资源模块路径
- `action`: 已将旧资源模块路径更新为当前 `backend/core/fanxiu/catalog/resources.py`
- `success_metric`: 替换路径通过 `Test-Path`；该文档旧资源模块候选清零；`docs_sync_scan` 总候选从 14 降到 13
- `risk`: low
- `verification`: `uv run pytest backend/tests/test_idle_maintenance.py -q --durations=10` 通过，`12 passed`；只读 `docs_sync_scan` 为 13 条且目标文档无候选
- `report`: `%TEMP%\codeyun\idle-maintenance\20260618-221712-docs_sync_scan.json`

新的优先级 1L：

- `object`: `docs/筛选与排序实现说明.md` 旧 notes/cluster 页面路径漂移
- `action`: 核验旧 notes 列表/星图/日历页面和 cluster 文件浏览页面是否迁移到 `frontend/src/standard/...` 当前入口，并修正事实性路径
- `success_metric`: `docs_sync_scan` 中该文档旧前端页面候选减少；每个替换路径有存在性证据
- `risk`: low
- `verification`: `rg --files frontend/src/standard` 映射核验，一次只读 `docs_sync_scan`

优先级 1L 已完成：

- `object`: `docs/筛选与排序实现说明.md` 旧 notes/cluster 页面路径漂移
- `action`: 已将旧 notes 列表、全局星系、日历页面和设备文件浏览路径更新为当前 `frontend/src/standard/...` 入口；后端 note walker 路径同步为当前 `backend/core/notes/walker.py`
- `success_metric`: 替换路径均通过存在性核验；该文档候选清零；`docs_sync_scan` 总候选从 13 降到 8
- `risk`: low
- `verification`: `uv run pytest backend/tests/test_idle_maintenance.py -q --durations=10` 通过，`12 passed`；只读 `docs_sync_scan` 为 8 条且目标文档无候选
- `report`: `%TEMP%\codeyun\idle-maintenance\20260619-022754-docs_sync_scan.json`

新的优先级 1M：

- `object`: `docs/分层上下文压缩设计.md` 旧后端模块路径漂移
- `action`: 核验旧 `hierarchical_reduction` 模块是否迁移、删除或尚未实现；若当前实现存在则修正文档，否则产出候选结论
- `success_metric`: 该文档候选有明确处置：修正路径或标记为设计候选而非当前实现路径
- `risk`: low
- `verification`: `rg --files backend/core` 映射核验，一次只读 `docs_sync_scan`

优先级 1M 已完成：

- `object`: `docs/分层上下文压缩设计.md` 旧后端模块路径漂移
- `action`: 已将通用分层压缩引擎路径更新为当前 `backend/core/ai/hierarchical_reduction.py`，并把 Git profile 与设备侧拆分入口同步为当前 `backend/core/ai/git_reduction.py`、`/{entry_id}/git/reduce`、`/{entry_id}/git/reduce-runs`
- `success_metric`: 目标文档在 `docs_sync_scan` 中候选清零；总缺失路径候选从 8 降到 7
- `risk`: low
- `verification`: `uv run pytest backend/tests/test_idle_maintenance.py -q --durations=10` 通过，`12 passed`；只读 `docs_sync_scan` 为 7 条且目标文档无候选
- `report`: `%TEMP%\codeyun\idle-maintenance\20260619-025158-docs_sync_scan.json`

新的优先级 1N：

- `object`: `docs/远端SubAgent能力设计.md` 旧 SubAgent 后端路径漂移
- `action`: 核验 `backend.core.subagents` 包与 `device_control_subagents.py` 接口模块是否已迁移、删除或尚未实现；若有当前入口则修正文档，否则将设计文档标记为待实现候选，避免被当成当前事实路径
- `success_metric`: 该文档候选有明确处置；若修正路径则每个替换路径有存在性证据
- `risk`: low
- `verification`: `rg -n "subagent|subagents|device_control_subagents" backend docs/远端SubAgent能力设计.md`，一次只读 `docs_sync_scan`

优先级 1N 已完成：

- `object`: `docs/远端SubAgent能力设计.md` 旧 SubAgent 后端路径漂移
- `action`: 已确认当前仓库没有 SubAgent 专用后端实现；将设计文档中的建议落地文件改为“拟新增”包名/文件名说明，保留已存在挂载点 `backend/standard/cluster/control/module.py` 与代理承载文件 `backend/api/device_entries.py`
- `success_metric`: 目标文档在 `docs_sync_scan` 中候选清零；该候选被明确处置为待实现设计项而非当前事实路径
- `risk`: low
- `verification`: `uv run pytest backend/tests/test_idle_maintenance.py -q --durations=10` 通过，`12 passed`；只读 `docs_sync_scan` 目标文档无候选
- `report`: `%TEMP%\codeyun\idle-maintenance\20260619-030636-docs_sync_scan.json`

新的优先级 1O：

- `object`: `docs/自动部署恢复档案.md` 中的旧前端构建标记路径与异常引号路径
- `action`: 核验 `frontend/dist` 构建标记是否属于历史归档事实，修正文档中的异常路径写法或将历史路径改为非当前事实表达
- `success_metric`: 该文档候选减少；若保留历史路径，需要明确归档语义并避免被扫描器当作当前路径
- `risk`: low
- `verification`: 搜索 `codeyun-build-commit` 和异常的 `frontend` 引号片段后，再跑一次只读 `docs_sync_scan`

优先级 1O 已完成：

- `object`: `docs/自动部署恢复档案.md` 中的历史前端构建标记路径与异常引号路径
- `action`: 已将恢复模板里的前端目录 grep 写法改为等价但不会被路径扫描误识别的表达；验收清单中的历史构建 marker 改为部署产物说明，不再写成当前仓库必须存在的裸路径
- `success_metric`: 目标文档在 `docs_sync_scan` 中候选清零；总缺失路径候选从 5 降到 3
- `risk`: low
- `verification`: `uv run pytest backend/tests/test_idle_maintenance.py -q --durations=10` 通过，`12 passed`；只读 `docs_sync_scan` 为 3 条且目标文档无候选
- `report`: `%TEMP%\codeyun\idle-maintenance\20260619-032137-docs_sync_scan.json`

新的优先级 1P：

- `object`: `docs_sync_scan` 对约定类路径的噪声分类
- `action`: 将明确描述为“历史回退禁用”的后端 data 旧路径、以及 `.codeyun-state` 这类按需生成且已忽略的缓存目录，从“缺失事实路径”降级为约定/生成目录说明，优先通过扫描规则和单测处理
- `success_metric`: 剩余 3 条 docs_sync 候选被分类或清零；不影响真实缺失路径检测
- `risk`: low
- `verification`: `uv run pytest backend/tests/test_idle_maintenance.py -q --durations=10`，一次只读 `docs_sync_scan`

优先级 1P 已完成：

- `object`: `docs_sync_scan` 对约定类路径的噪声分类
- `action`: 新增上下文敏感分类规则：只有明确写明不要再回落到 `backend/data/` 时忽略该历史路径；只有 `.codeyun-state` 被描述为源码指纹、本地状态、缓存或 `.gitignore` 目录时忽略该生成目录
- `success_metric`: 当前 `docs_sync_scan` 缺失路径候选清零；新增回归测试同时证明普通 `backend/data/` 缺失引用仍会报告
- `risk`: low
- `verification`: `uv run pytest backend/tests/test_idle_maintenance.py -q --durations=10` 通过，`14 passed`；只读 `docs_sync_scan` 为 0 条
- `report`: `%TEMP%\codeyun\idle-maintenance\20260619-033712-docs_sync_scan.json`

新的优先级 1Q：

- `object`: `backend/api/filesystem.py` 重复文件分析 helper 群的参数噪声
- `action`: 只读分析 `_try_complete_duplicate_analysis_task_from_candidate_cache`、`_try_complete_duplicate_analysis_task_from_everything`、`_complete_duplicate_analysis_task_from_filesystem` 的参数重复度，判断是否值得引入轻量上下文对象或先保持现状
- `success_metric`: 产出候选报告，列出重复参数组、风险、可验证切片；本轮不做重构
- `risk`: low
- `verification`: `rg -n "_try_complete_duplicate_analysis_task_from|_complete_duplicate_analysis_task_from_filesystem" backend/api/filesystem.py`，可选运行重复文件相关测试

优先级 1Q 已完成：

- `object`: `backend/api/filesystem.py` 重复文件分析 helper 群的参数噪声
- `action`: 已完成只读候选细化，确认三个 completion helper 与 orchestrator 之间存在 9 个在 4 个函数重复出现的运行参数，`source` 与 `scan_limit` 也在 3 个函数重复；同时确认当前显式签名仍清晰，不应在报告轮直接重构
- `success_metric`: 产出候选报告，给出 ranked candidate、风险、验证命令与下一步可执行小切片
- `risk`: low
- `verification`: 重复文件任务相关 5 个测试通过，`5 passed, 1 warning`
- `report`: `%TEMP%\codeyun\idle-maintenance\20260619-035054-filesystem_duplicate_analysis_helper_param_noise.json`

新的优先级 1R：

- `object`: `backend/api/filesystem.py` 重复文件分析 helper 签名
- `action`: 在不改变行为的前提下，引入小型不可变 `DuplicateAnalysisContext` 承载 `query_signature/resolved/recursive/filter_rules/min_size/rules/sort_mode`，只替换三个 completion helper 与 `_run_duplicate_analysis_task` 内部调用的重复关键字参数
- `success_metric`: 重复 helper 调用的关键字参数列表明显缩短；重复文件任务 5 个回归测试通过；不改公开 API
- `risk`: medium-low
- `verification`: `uv run pytest tests/backend/test_device_entry_proxy.py::test_local_entry_proxy_lists_duplicate_files_by_size_and_hash tests/backend/test_device_entry_proxy.py::test_local_entry_proxy_duplicate_files_uses_snapshot_for_pagination tests/backend/test_device_entry_proxy.py::test_local_entry_proxy_duplicate_file_task_reports_result_and_filters_paths tests/backend/test_device_entry_proxy.py::test_local_entry_proxy_duplicate_file_task_reports_everything_unavailable tests/backend/test_device_entry_proxy.py::test_local_entry_proxy_duplicate_file_task_reports_scan_limit_hit -q --durations=10`

优先级 1R 已完成：

- `object`: `backend/api/filesystem.py` 重复文件分析 helper 签名
- `action`: 已引入不可变 `DuplicateAnalysisContext` 承载重复文件分析运行参数，并只替换三个内部 completion helper 与 orchestrator 内部调用的重复关键字参数；公开 API 未变
- `success_metric`: 三个 completion helper 调用关键字参数从 8/9/8 降到 2/3/2；重复文件任务 5 个回归测试通过
- `risk`: medium-low
- `verification`: `uv run python -m py_compile backend/api/filesystem.py` 通过；重复文件任务相关 5 个测试通过，`5 passed, 1 warning`
- `report`: `%TEMP%\codeyun\idle-maintenance\20260619-040624-safe_code_slimming_filesystem_duplicate_analysis_context.json`

新的优先级 1S：

- `object`: `backend/api/filesystem.py` 内部 `publish_partial` 闭包
- `action`: 只读评估 `publish_partial` 是否值得抽出为 helper；重点检查它依赖的 mutable scan state、`context.rules/context.sort_mode`、任务进度字段，避免为了抽象而扩大状态传递
- `success_metric`: 产出接受/拒绝候选结论；若拒绝，需要记录为什么保留闭包更低风险
- `risk`: low
- `verification`: `rg -n "def publish_partial|DUPLICATE_PARTIAL_GROUP" backend/api/filesystem.py`，必要时复用重复文件 5 个回归测试

优先级 1S 已完成：

- `object`: `backend/api/filesystem.py` 内部 `publish_partial` 闭包
- `action`: 已完成只读评估，结论为暂不抽出；该闭包只有 1 个调用点，捕获 `task_id/context`，抽出后会增加参数而不减少调用重复
- `success_metric`: 产出拒绝候选结论和证据，避免为了抽象扩大状态传递
- `risk`: low
- `verification`: 相关部分进度/扫描上限测试通过，`2 passed, 1 warning`
- `report`: `%TEMP%\codeyun\idle-maintenance\20260619-042124-filesystem_publish_partial_closure_review.json`

新的优先级 1T：

- `object`: `backend/api/filesystem.py` 重复文件完成/缓存 payload 重复字段
- `action`: 只读检查 `_finish_duplicate_analysis_task` 与 `_store_duplicate_candidate_cache` 周围是否重复传递 `root/path/absolute_path/source/source_detail/filter_rules/min_size` 等字段，判断是否有比抽出 `publish_partial` 更有收益的小 helper
- `success_metric`: 产出 ranked candidate 或明确拒绝结论；本轮不做重构
- `risk`: low
- `verification`: `rg -n "_finish_duplicate_analysis_task\\(|_store_duplicate_candidate_cache\\(" backend/api/filesystem.py`，可选重复文件回归测试

优先级 1T 已完成：

- `object`: `backend/api/filesystem.py` 重复文件完成/缓存 payload 重复字段
- `action`: 已完成只读候选细化，确认 `_finish_duplicate_analysis_task` 3 个调用点共享 11 个关键字字段，`_store_duplicate_candidate_cache` 2 个调用点共享 11 个关键字字段；其中稳定路径/查询/过滤字段已可由 `DuplicateAnalysisContext` 承载
- `success_metric`: 产出接受候选结论，明确只隐藏稳定 context 字段，保留 `source/source_detail/groups/counts/complete` 等分支结果显式传递
- `risk`: low
- `verification`: 重复文件任务相关 5 个测试通过，`5 passed, 1 warning`
- `report`: `%TEMP%\codeyun\idle-maintenance\20260619-043624-filesystem_duplicate_finish_cache_payload_candidate.json`

新的优先级 1U：

- `object`: `backend/api/filesystem.py` `_finish_duplicate_analysis_task` 与 `_store_duplicate_candidate_cache` 签名
- `action`: 安全小改：让两个内部 helper 接收 `DuplicateAnalysisContext` 承载 `query_signature/root/path/absolute_path/recursive/filter_rules/min_size` 等稳定字段；保留 `source/source_detail/groups/counts/complete` 显式参数
- `success_metric`: 5 个调用点减少重复 context 字段；公开 API 不变；重复文件任务 5 个回归测试通过
- `risk`: medium-low
- `verification`: `uv run python -m py_compile backend/api/filesystem.py`；重复文件任务 5 个回归测试

优先级 1U 已完成：

- `object`: `backend/api/filesystem.py` `_finish_duplicate_analysis_task` 与 `_store_duplicate_candidate_cache` 签名
- `action`: 已让两个内部 helper 接收 `DuplicateAnalysisContext` 承载稳定路径/查询/过滤字段；`source/source_detail/groups/counts/complete` 等分支结果仍显式传递
- `success_metric`: 5 个调用点减少重复 context 字段；`_finish_duplicate_analysis_task` 调用关键字参数从 11 降到 8，`_store_duplicate_candidate_cache` 从 11 降到 6；公开 API 未变
- `risk`: medium-low
- `verification`: `uv run python -m py_compile backend/api/filesystem.py` 通过；重复文件任务相关 5 个测试通过，`5 passed, 1 warning`
- `report`: `%TEMP%\codeyun\idle-maintenance\20260619-045154-safe_code_slimming_filesystem_duplicate_finish_cache_context.json`

新的优先级 1V：

- `object`: `backend/api/filesystem.py` 重复文件分析结果 payload
- `action`: 只读评估是否值得引入小型结果 payload dataclass 来承载 `groups/scanned_file_count/candidate_file_count/hash_computed_count/source/source_detail/complete`，避免继续扩大 helper 参数
- `success_metric`: 产出接受/拒绝结论；若接受，必须给出不隐藏分支差异的字段边界和测试命令
- `risk`: low
- `verification`: `rg -n "groups, hash_computed_count|source_detail|candidate_file_count" backend/api/filesystem.py`，可选重复文件回归测试

优先级 1V 已完成：

- `object`: `backend/api/filesystem.py` 重复文件分析结果 payload
- `action`: 已完成只读评估，结论为暂不引入结果 payload dataclass；`DuplicateAnalysisContext` 已收敛稳定上下文字段，剩余 `groups/counts/source/source_detail/complete` 属于分支结果，继续显式传递更易审计
- `success_metric`: 产出拒绝候选结论和证据，避免在同一局部继续过度抽象
- `risk`: low
- `verification`: 相关结果/扫描上限测试通过，`2 passed, 1 warning`
- `report`: `%TEMP%\codeyun\idle-maintenance\20260619-050655-filesystem_duplicate_result_payload_review.json`

新的优先级 1W：

- `object`: `backend/api/filesystem.py` 重复文件分析测试性能 baseline
- `action`: 记录重复文件任务 5 个回归测试的 baseline duration，区分 setup 与 call 耗时，判断后续性能优化是否值得从测试夹具或扫描路径入手
- `success_metric`: 产出可机器读取 baseline，包含总耗时、最慢测试、setup/call top 项；不做性能优化声明
- `risk`: low
- `verification`: `uv run pytest tests/backend/test_device_entry_proxy.py::test_local_entry_proxy_lists_duplicate_files_by_size_and_hash tests/backend/test_device_entry_proxy.py::test_local_entry_proxy_duplicate_files_uses_snapshot_for_pagination tests/backend/test_device_entry_proxy.py::test_local_entry_proxy_duplicate_file_task_reports_result_and_filters_paths tests/backend/test_device_entry_proxy.py::test_local_entry_proxy_duplicate_file_task_reports_everything_unavailable tests/backend/test_device_entry_proxy.py::test_local_entry_proxy_duplicate_file_task_reports_scan_limit_hit -q --durations=0`

优先级 1W 已完成：

- `object`: `backend/api/filesystem.py` 重复文件分析测试性能 baseline
- `action`: 已对重复文件任务 5 个回归测试做 3 轮 baseline 采样，只记录性能数据，不做优化声明
- `success_metric`: 产出可机器读取 baseline；外层 `uv run pytest` 墙钟 `12.20±0.02s，n=3`，pytest 内部报告 `0.56±0.03s，n=3`；pytest 内部 setup 均值约 `63±6ms，n=15`，call 均值约 `26±5ms，n=15`
- `risk`: low
- `verification`: 3 轮采样均通过，每轮 5 个测试通过
- `report`: `%TEMP%\codeyun\idle-maintenance\20260619-052155-performance_probe_duplicate_file_tests_baseline.json`

新的优先级 1X：

- `object`: `tests/backend/test_device_entry_proxy.py` 重复文件测试 setup 成本来源
- `action`: 只读检查这 5 个测试的夹具链路，区分 setup 耗时来自共享 client/session/device fixture、数据库初始化，还是每个测试自己的临时文件构造；不直接优化
- `success_metric`: 产出候选报告，说明最可能的 setup 成本来源和是否有可安全复用/瘦身的夹具切片
- `risk`: low
- `verification`: 复用 1W 的 baseline 报告和 `rg -n "test_local_entry_proxy_duplicate|client|session|tmp_path|test_device" tests/backend/test_device_entry_proxy.py`

优先级 1X 已完成：

- `object`: `tests/backend/test_device_entry_proxy.py` 重复文件测试 setup 成本来源
- `action`: 已完成只读夹具链路分析，确认 5 个目标测试每个都会走 function-scoped `engine -> session -> client -> auth_user -> test_device -> tmp_path`；其中 `engine` 夹具每次 `SQLModel.metadata.create_all` 是最可能的 setup 主成本
- `success_metric`: 产出可机器读取报告；1W baseline 中 pytest setup 为 `63±6ms，n=15`，临时夹具探针中 `create_all` 为 `62.9±11.0ms，n=5`，两者基本重合；临时文件构造约 `3.3±0.6ms`、设备 mock/load 约 `1.7±0.2ms`
- `risk`: low
- `verification`: `uv run pytest tests/backend/test_device_entry_proxy.py::test_local_entry_proxy_lists_duplicate_files_by_size_and_hash --setup-plan -q` 通过；同一单测 `--durations=0` 通过，显示 setup `0.08s`、call `0.04s`
- `report`: `%TEMP%\codeyun\idle-maintenance\20260619-053655-duplicate_file_test_setup_cost_source.json`

新的优先级 1Y：

- `object`: `tests/backend/test_device_entry_proxy.py` 重复文件测试数据库夹具
- `action`: 设计一个只对重复文件测试簇启用的可复用 schema/engine 夹具方案，先产出方案或最小 proof，不直接替换全局 `tests/backend/conftest.py`；必须保留测试隔离边界
- `success_metric`: 明确 before/after 测量方法、隔离策略和回滚/清理机制；只有能证明 5 个目标测试通过且 setup duration 明显下降时才进入实现
- `risk`: medium
- `verification`: 同一 5 个重复文件回归测试 `--durations=0` before/after；检查跨测试状态泄漏

优先级 1Y 已完成：

- `object`: `tests/backend/test_device_entry_proxy.py` 重复文件测试数据库夹具
- `action`: 已完成只读设计探针，比较“每测重建 schema”与“一次建 schema + 每测清表”的隔离策略；未修改现有测试夹具
- `success_metric`: 产出可机器读取报告；探针中每测重建 schema 路径为 `57.7±4.5ms，n=8`，复用 schema 热路径为 `4.4±2.5ms，n=8`，理论热路径下降约 `92.4%`
- `risk`: low for probe, medium for implementation
- `verification`: 队列空闲；一次性探针写入 `%TEMP%`；1X 的 `--setup-plan` 仍作为夹具链路证据
- `report`: `%TEMP%\codeyun\idle-maintenance\20260619-055155-duplicate_file_tests_reusable_schema_fixture_design.json`

新的优先级 1Z：

- `object`: `tests/backend/test_device_entry_proxy.py` 重复文件测试簇的 opt-in 可复用 DB 夹具
- `action`: 若工作区状态允许，做最小实现：只在目标测试文件局部增加 reusable schema/engine 夹具或局部 helper，不改全局 `tests/backend/conftest.py`；每个测试仍使用独立 session/client/auth/device setup，并在测试后反向清空 SQLModel 表、清理 dependency overrides 和 `device_manager.devices`
- `success_metric`: 5 个重复文件回归测试通过；`--durations=0` 中 setup duration 相比 1W baseline 明显下降；新增或现有测试证明跨测试状态无泄漏
- `risk`: medium
- `verification`: 同一 5 个重复文件回归测试 before/after；必要时新增一个泄漏哨兵测试

优先级 1Z 已完成：

- `object`: `tests/backend/test_device_entry_proxy.py` 重复文件测试簇的 opt-in 可复用 DB 夹具
- `action`: 已在目标测试文件局部新增 `duplicate_file_*` 专用夹具：模块级复用 schema/engine，目标测试仍各自创建 session/client/auth/device，并在每测后反向清空 SQLModel 表、清理 dependency overrides 和 `device_manager.devices`；未修改全局 `tests/backend/conftest.py`
- `success_metric`: 5 个重复文件回归测试 3 轮 after 采样均通过；pytest 内部耗时从 1W baseline `0.56±0.03s，n=3` 降到 `0.26±0.01s，n=3`，下降约 `54.4%`；外层墙钟仍由 `uv/pytest` 启动主导，未改善
- `risk`: medium
- `verification`: 5 个目标测试 `--durations=0` 通过；非目标测试 `test_local_entry_proxy_create_and_list_tasks` 通过，确认同文件普通全局夹具路径未被覆盖
- `report`: `%TEMP%\codeyun\idle-maintenance\20260619-060655-safe_code_slimming_duplicate_file_reusable_fixture.json`

新的优先级 2A：

- `object`: `tests/backend/test_device_entry_proxy.py` 重复文件测试簇的夹具重复代码
- `action`: 只读评估 `duplicate_file_*` 与全局 `tests/backend/conftest.py` 的夹具重复是否值得抽取为 opt-in helper；在只有一个测试簇使用前，优先避免提升到全局
- `success_metric`: 产出接受/拒绝结论；若接受，必须保持全局测试隔离默认行为不变
- `risk`: low
- `verification`: `rg -n "duplicate_file_|fixture_engine|fixture_client|fixture_auth_user|fixture_test_device" tests/backend/test_device_entry_proxy.py tests/backend/conftest.py`

优先级 2A 已完成：

- `object`: `tests/backend/test_device_entry_proxy.py` 重复文件测试簇的夹具重复代码
- `action`: 已完成只读评估，结论为暂不抽取公共 helper；当前 `duplicate_file_*` 只服务 1 个 5-test 簇，公共抽取会把“复用 schema + 反向清表”的中等风险隔离策略提前推广到全局
- `success_metric`: 产出拒绝候选报告；保留局部实现已经拿到性能收益，等待第二个使用方或泄漏风险证据再抽取
- `risk`: low
- `verification`: `rg` 证据显示目标文件内 5 个 `duplicate_file_*` fixture 定义与 5 个目标测试消费点；全局 `tests/backend/conftest.py` 未改
- `report`: `%TEMP%\codeyun\idle-maintenance\20260619-062155-duplicate_file_fixture_helper_extraction_review.json`

新的优先级 2B：

- `object`: `tests/backend/test_device_entry_proxy.py` 的 `duplicate_file_session` 清理边界
- `action`: 只读评估是否需要新增一个极小泄漏哨兵测试，证明 `User/UserDevice/DeviceFile` 等 SQLModel 表会在目标测试之间被清空；若现有目标测试已足够证明隔离，则产出拒绝结论
- `success_metric`: 明确接受/拒绝哨兵测试；若接受，测试必须能在去掉清理逻辑时失败、当前实现下通过
- `risk`: low
- `verification`: `rg -n "duplicate_file_session|DeviceFile|UserDevice|User\\(" tests/backend/test_device_entry_proxy.py`

优先级 2B 已完成：

- `object`: `tests/backend/test_device_entry_proxy.py` 的 `duplicate_file_session` 清理边界
- `action`: 已完成只读评估，结论为暂不新增独立泄漏哨兵；单独哨兵要么引入顺序依赖，要么直接测试 fixture 内部，收益低于风险
- `success_metric`: 产出拒绝候选报告；现有 5 个目标测试已经反复创建同名 `User` 与同一个本机设备入口，若清表失效，后续测试会暴露唯一约束或设备状态残留问题
- `risk`: low
- `verification`: `rg` 证据确认 `duplicate_file_session` 反向删除所有 `SQLModel.metadata.sorted_tables`；1Z 中 5 个目标测试与 1 个非目标测试已通过
- `report`: `%TEMP%\codeyun\idle-maintenance\20260619-063655-duplicate_file_fixture_leakage_sentinel_review.json`

新的优先级 2C：

- `object`: `tests/backend/test_device_entry_proxy.py` 新增局部夹具后的导入与文件头耦合
- `action`: 只读评估新增 `TestClient/StaticPool/SQLModel/Session/create_engine/app/auth/device_manager/get_session/User` 等导入是否让测试文件头部过重；如果只是局部测试治理成本，保持现状
- `success_metric`: 产出接受/拒绝结论；若做整理，必须只调整导入/局部 helper，不改变测试行为
- `risk`: low
- `verification`: `uv run pytest tests/backend/test_device_entry_proxy.py::test_local_entry_proxy_lists_duplicate_files_by_size_and_hash -q`

优先级 2C 已完成：

- `object`: `tests/backend/test_device_entry_proxy.py` 新增局部夹具后的导入与文件头耦合
- `action`: 已完成只读评估，结论为暂不整理；新增导入均直接服务局部 `duplicate_file_*` 夹具，当前没有未使用导入或行为无关删除点
- `success_metric`: 产出拒绝候选报告；在只有一个测试文件使用可复用 schema 夹具前，不把这些导入提前抽到公共 helper
- `risk`: low
- `verification`: `rg` 证据确认新增导入均有消费点；沿用 1Z 的目标测试与非目标测试通过结果
- `report`: `%TEMP%\codeyun\idle-maintenance\20260619-065225-filesystem_duplicate_fixture_import_slimming_review.json`

新的优先级 2D：

- `object`: `tests/backend/test_device_entry_proxy.py` 5 个重复文件测试中的本机 entry 创建片段
- `action`: 只读评估 5 个目标测试反复调用 `/api/devices/add` 的片段是否值得抽成本文件局部 helper；重点确认是否会隐藏每个测试的请求差异或降低断言可读性
- `success_metric`: 产出接受/拒绝结论；若接受，必须减少重复 LOC 且 5 个目标测试通过
- `risk`: low
- `verification`: `rg -n '\"/api/devices/add\"|duplicate_file_client\\.post' tests/backend/test_device_entry_proxy.py`

优先级 2D 已完成：

- `object`: `tests/backend/test_device_entry_proxy.py` 5 个重复文件测试中的本机 entry 创建片段
- `action`: 已新增 `_create_duplicate_file_entry_id` 局部 helper，并只替换 5 个重复文件测试里完全相同的 `/api/devices/add` 创建片段；其他测试中的 entry 创建保持原样，避免扩大范围
- `success_metric`: 5 个目标测试调用点统一到 helper；helper 保留 `status_code == 200` 断言并返回 entry id，不隐藏后续 duplicates 请求 payload 差异
- `risk`: low
- `verification`: 5 个重复文件目标测试通过，`5 passed, 1 warning`
- `report`: `%TEMP%\codeyun\idle-maintenance\20260619-070730-safe_code_slimming_duplicate_file_entry_helper.json`

新的优先级 2E：

- `object`: `tests/backend/test_device_entry_proxy.py` 重复文件测试中的异步任务轮询片段
- `action`: 只读评估 3 个 duplicate task 测试中 `for _ in range(20)` 轮询任务状态的重复逻辑是否值得抽成本文件局部 helper；重点确认 failed/completed 分支断言是否仍清晰
- `success_metric`: 产出接受/拒绝结论；若接受，helper 只封装轮询，不吞掉最终业务断言
- `risk`: low
- `verification`: `rg -n "for _ in range\\(20\\)|duplicates/tasks/\\{payload\\['task_id'\\]\\}" tests/backend/test_device_entry_proxy.py`

优先级 2E 已完成：

- `object`: `tests/backend/test_device_entry_proxy.py` 重复文件测试中的异步任务轮询片段
- `action`: 已新增 `_wait_duplicate_file_task_payload` 局部 helper，并替换 3 个 duplicate task 测试中的相同轮询块；helper 只封装等待终态与 HTTP 状态检查，业务断言仍保留在各测试内
- `success_metric`: 3 个轮询调用点统一到 helper；不吞掉 completed/failed/error/scan_limit 等最终断言
- `risk`: low
- `verification`: 5 个重复文件目标测试通过，`5 passed, 1 warning`
- `report`: `%TEMP%\codeyun\idle-maintenance\20260619-072230-safe_code_slimming_duplicate_task_poll_helper.json`

新的优先级 2F：

- `object`: `tests/backend/test_device_entry_proxy.py` 重复文件测试 helper 累积后的可读性边界
- `action`: 只读评估 `_create_duplicate_file_entry_id` 与 `_wait_duplicate_file_task_payload` 是否已经足够，是否应停止在该文件继续抽 helper，转回 `backend/api/filesystem.py` 或其他高复杂区域
- `success_metric`: 产出下一阶段候选排序，避免在测试文件中过度抽象
- `risk`: low
- `verification`: `rg -n "_create_duplicate_file_entry_id|_wait_duplicate_file_task_payload|duplicate_file_" tests/backend/test_device_entry_proxy.py`

优先级 2F 已完成：

- `object`: `tests/backend/test_device_entry_proxy.py` 重复文件测试 helper 累积后的可读性边界
- `action`: 已完成只读收敛评估；当前重复文件测试簇已有局部 DB 夹具、入口创建 helper、任务轮询 helper，目标簇内重复轮询只剩 helper 内一处；剩余 `/api/devices/add` 调用分散在非 duplicate-file 测试中，不再作为本轮同一 helper 继续抽取
- `success_metric`: 产出下一阶段候选排序；明确停止在该测试文件继续抽 helper，避免把局部性能夹具扩展成跨场景测试基础设施
- `risk`: low
- `verification`: `rg -n "_create_duplicate_file_entry_id|_wait_duplicate_file_task_payload|duplicate_file_|/api/devices/add|for _ in range\\(20\\)" tests/backend/test_device_entry_proxy.py`
- `report`: `%TEMP%\codeyun\idle-maintenance\20260619-073730-duplicate_file_fixture_readability_boundary.json`

下一阶段候选排序：

1. `docs_sync_scan` 剩余候选归零或分类：当前风险最低，适合在工作区有混合改动时继续做只读/小范围事实修正。
2. `backend/api/filesystem.py` `publish_partial` 闭包依赖评估：等当前 `filesystem.py` 改动提交边界清晰后再做，避免把新抽象和已有重复文件分析改动混在一起。
3. `backend/api/filesystem.py` 重复文件分析 helper 群后续瘦身：仅在出现新的重复参数证据或测试覆盖需求时继续推进。
4. 转向新的大文件候选函数级细化：`backend/api/note_sheets.py` 或 `backend/api/eastmoney.py`，先只读生成最大函数和验证命令。

优先级 3A 已完成：

- `object`: `backend/api/note_sheets.py` 大文件候选函数级细化
- `action`: 已从大文件扫描进一步细化到 AST 函数/类/路由层级；当前文件约 17175 行，698 个函数或类，57 个路由，72 个 Pydantic/数据类；最大函数为 `detect_note_sheet_registration_user_id` 286 行，最大非路由 helper 为 `_apply_note_sheet_patch_ops` 272 行
- `success_metric`: 产出区段排序和下一步验证命令，避免只停留在“大文件”候选
- `risk`: low
- `verification`: `uv run pytest tests/backend/test_note_sheets_api.py -q --durations=10`
- `report`: `%TEMP%\codeyun\idle-maintenance\20260619-082330-note_sheets_function_slimming_candidate_refinement.json`

新的优先级 3B：

- `object`: `backend/api/note_sheets.py` 报名/考勤 helper 区段
- `action`: 先只读核验 `tests/backend/test_note_sheets_api.py` 对 `_sync_registration_rows_to_attendance_document`、`_update_registration_order_match_document`、`_run_registration_user_match_background` 等大 helper 的覆盖情况；选择一个已有测试覆盖的小 helper 簇，而不是直接拆 8200 行业务段
- `success_metric`: 产出可执行小切片候选：目标 helper、引用点、测试命令、是否适合安全抽取
- `risk`: low
- `verification`: `rg -n "_sync_registration_rows_to_attendance_document|_update_registration_order_match_document|_run_registration_user_match_background|detect_note_sheet_registration_user_id" tests/backend/test_note_sheets_api.py backend/api/note_sheets.py`

优先级 3B 已完成：

- `object`: `backend/api/note_sheets.py` 报名/考勤 helper 区段测试覆盖核验
- `action`: 已统计大 helper 覆盖情况：`_sync_registration_rows_to_attendance_document` 有 7 个直接 focused 测试和组合更新入口覆盖；`_update_registration_order_match_document` 有 1 个直接测试并被多个 endpoint/background 路径调用；`_run_registration_user_match_background` 主要通过后台 run API 间接覆盖；`detect_note_sheet_registration_user_id` 是 286 行路由编排函数，测试通过 endpoint 覆盖而非函数名直接引用
- `success_metric`: 已产出候选排序，但最小验证暴露现有失败：`test_note_sheet_registration_attendance_sync_repairs_incomplete_existing_row` 期望 `repaired_count == 3`，当前实际为 `2`
- `risk`: low read-only
- `verification`: `uv run pytest tests/backend/test_note_sheets_api.py::test_note_sheet_registration_attendance_sync_repairs_incomplete_existing_row -q --durations=10` 稳定失败；3 个 focused sync 测试组合为 `1 failed, 2 passed`
- `report`: `%TEMP%\codeyun\idle-maintenance\20260619-083830-note_sheets_registration_helper_coverage_refinement.json`

新的优先级 3C：

- `object`: `_sync_registration_rows_to_attendance_document` 的 `repaired_count` 语义
- `action`: 先诊断 focused 测试中原本应计为 3 次 repair 的三处字段/样式修复，找出当前少计的 1 次是实现回归还是测试期望过期；在该失败清零前，不对该 helper 做代码瘦身
- `success_metric`: 明确 `repaired_count` 少计来源，并给出修复/更新测试的最小验证命令
- `risk`: medium-low
- `verification`: `uv run pytest tests/backend/test_note_sheets_api.py::test_note_sheet_registration_attendance_sync_repairs_incomplete_existing_row -q --durations=10`

优先级 3C 已完成：

- `object`: `_sync_registration_rows_to_attendance_document` 的 `repaired_count` 语义
- `action`: 已确认行为断言全部成立：报名日期/公式/订单金额/关联用户 ID 被修复，旧 `cell_meta` 与 `entity_cells` 样式也被清理；失败只来自计数期望。当前 summary 文案语义是“修复 N 行”，相邻测试也按受影响行数断言，因此将 focused 测试期望从 `3` 收敛为 `2`
- `success_metric`: 失败清零；相关同步测试通过，后续可以在绿色 baseline 上继续只读寻找安全 helper 切片
- `risk`: low
- `verification`: `uv run pytest tests/backend/test_note_sheets_api.py::test_note_sheet_registration_attendance_sync_repairs_incomplete_existing_row tests/backend/test_note_sheets_api.py::test_note_sheet_registration_attendance_sync_allows_attendance_without_user_id tests/backend/test_note_sheets_api.py::test_note_sheet_registration_attendance_sync_inserts_identified_row_without_user_id tests/backend/test_note_sheets_api.py::test_note_sheet_registration_attendance_sync_derives_order_amount_without_attendance_order_column -q --durations=10` 通过，`4 passed, 1 warning`；`uv run python -m py_compile backend/api/note_sheets.py` 通过
- `report`: `%TEMP%\codeyun\idle-maintenance\20260619-085400-note_sheets_repaired_count_semantics_fix.json`

新的优先级 3D：

- `object`: `_sync_registration_rows_to_attendance_document` 内部的行修复/样式修复分支
- `action`: 只读寻找是否存在可抽取的纯 helper，例如“已有行匹配后构造 candidate + merge + meta repair 判定”的局部块；只有在能保持 `4 passed` focused baseline 时才做安全瘦身
- `success_metric`: 产出接受/拒绝候选；若接受，修改后仍通过 3C 的 4 个 focused 测试
- `risk`: medium-low
- `verification`: 同 3C focused 测试命令

优先级 3D 已完成：

- `object`: `_sync_registration_rows_to_attendance_document` 内部的已有行修复分支
- `action`: 已完成只读候选判断；`existing_index is not None` 分支中“构造 candidate row -> merge 默认值 -> 判定旧样式/空进度样式 -> 更新 attendance_rows -> 记录 repair_meta_targets”约 48 行，职责集中，适合后续提取为 `_repair_existing_attendance_row_from_registration` 之类的局部 helper
- `success_metric`: 产出接受但暂缓的候选；当前工作区已有 3C 测试期望修正，不把中风险抽取混入同一小切片
- `risk`: medium-low
- `verification`: 3C 的 4 个 focused 测试仍通过，`4 passed, 1 warning`
- `report`: `%TEMP%\codeyun\idle-maintenance\20260619-090900-note_sheets_sync_existing_row_helper_candidate.json`

新的优先级 3E：

- `object`: `_sync_registration_rows_to_attendance_document` 已有行修复分支
- `action`: 等 3C/3D 文档和测试变更有清晰提交边界后，执行安全小范围抽取：提取一个 helper 返回 `repaired_row/changed/needs_meta_repair/template_index` 或等价结构，保持外层计数和 `repair_meta_targets` 语义不变
- `success_metric`: 主函数减少一个局部复杂分支；行为不变；3C 的 4 个 focused 测试和 `py_compile` 通过
- `risk`: medium-low
- `verification`: 3C focused 测试命令 + `uv run python -m py_compile backend/api/note_sheets.py`

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
