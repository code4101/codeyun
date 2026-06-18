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
