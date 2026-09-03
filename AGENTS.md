# AGENTS.md

## 项目入口

- CodeYun 是个人超级工具集成平台；全项目概览先读 `docs/AI_CONTEXT.md`，文档分层和业务域入口先读 `docs/README.md`。
- 根 `AGENTS.md` 只维护全库通用运行方式、责任边界、安全约束和文档路由，不复制考勤、凡修、星图笔记、星云表格等领域内部规则。
- 任务明确命中某个业务域时，再读取下方路由中的对应 README 和本次需要的专项正文；不要把所有领域上下文一次性加载进全局任务。

## 运行约定（重要）

- 所有命令默认在仓库根目录执行：`C:\home\chenkunze\slns\codeyun`
- Python 命令优先使用 `uv run`
- 启动开发环境统一使用：`uv run dev.py`
- 运行测试统一使用：`uv run pytest`
- 临时 Python 命令统一使用：`uv run python ...`
- 安装前端依赖：`npm install --prefix frontend`
- 单独启动前端：`npm run dev --prefix frontend`
- 仅在 `uv` 不可用时，Windows 使用 `.\.venv\Scripts\python.exe dev.py`；不要依赖全局 Python 或其他项目的虚拟环境。
- `https://code4101.com` 是本机 CodeYun 经内网穿透暴露的公网入口（回源 `localhost`），不是独立公网实例；若它与临时 Python 进程读取结果不一致，先核对运行环境与 `CODEYUN_DATA_DIR`，不要据 DNS 或临时数据库判断为双实例。

## 临时产物约定（重要）

- 源码目录只放源码、测试、文档、配置样例和明确要版本管理的静态资产。
- 调试截图、OCR 裁剪图、抓包片段、探针 JSON、临时 DB、服务 stdout/stderr 日志、一次性脚本输出等临时产物，统一写到系统临时目录，不要写到仓库根目录或源码子目录。
- Python 代码优先使用 `backend.core.temp_paths.codeyun_temp_root(...)`；一次性 PowerShell 使用 `$env:TEMP\codeyun\...`。
- 创建数据库克隆、磁盘副本等大体积临时产物前先评估占用；测试结束后必须立即停止相关进程、删除临时副本并复核磁盘空间。若清理失败，当场报告精确路径与占用，不得把 C 盘临时数据留待以后处理。
- `.codex_tmp/` 是历史遗留目录，不再新建或继续使用；需要保留结论时写入文档摘要，不把大体积原始证据放进仓库。
- 详细规则见：`docs/operations/runbooks/临时测试产物目录约定.md`。

## 文档分层约定（重要）

- `docs/README.md` 是文档总入口；新增或迁移文档时必须同步维护其中的导航和层级语义。
- `docs/` 根目录只保留 `README.md` 和约定俗成的 `AI_CONTEXT.md`，不得继续堆放业务文档。
- 当前架构与强约定放入 `platform/` 或业务域的 `architecture/`；操作方法放入 `guides/`、`jobs/`、`operations/runbooks/`。
- 未完成计划、探索记录、自动化增量上下文和历史材料分别放入 `plans/`、`research/`、`context/`、`archive/`，不得覆盖权威正文。
- 移动文档后必须同步修复 `AGENTS.md`、源码注释、测试和 Markdown 内部链接中的路径引用。

## API 与 Agent 责任分层（强约束）

- Skill 和项目文档只说明能力、稳定业务边界、公共 API/CLI 入口与验收，不复制请求参数表或服务端内部工作流。
- 参数、默认值、返回、错误码、副作用和原子性由 API schema、函数 docstring、类型与 CLI `--help` 维护，调用方以这些契约为准。
- 编号、ID 解析、落盘、备份、锁、事务、并发版本、幂等、去重、内部轮询和存储迁移属于提供方责任；不得要求 Agent 调私有函数、读内部数据库/WAL、拼内部节点或串联多个低层步骤补偿。
- 公共接口无法直接表达业务意图时，修复或新增高层 API 并补测试；不要把缺口固化成 skill/doc 中的 Agent 操作手册。
- 历史事故、探针和迁移过程放 `archive/`、`research/`、`context/` 或测试；不得回流为长期调用流程。

## 架构与领域路由

| 任务范围 | 入口 |
| --- | --- |
| 全库架构与模块地图 | `docs/AI_CONTEXT.md` |
| 文档层级与全部业务域 | `docs/README.md` |
| 前端交互、页面与菜单挂载 | `docs/platform/conventions/前端交互约定.md` |
| 资源保存、并发更新和派生缓存 | `docs/platform/conventions/资源保存与并发更新约定.md` |
| 作业、服务与本机实例 | `docs/platform/architecture/作业与服务架构.md` |
| 本地启动、部署与排障 | `docs/operations/runbooks/CodeYun本地开发与部署排障.md` |
| 考勤 | `docs/domains/attendance/README.md` |
| 凡修 | `docs/domains/fanxiu/README.md` |
| 星图笔记 | `docs/domains/notes/README.md` |
| 星云表格 | `docs/domains/spreadsheets/README.md` |
| DSP 静态资源同步 | `docs/domains/integrations/guides/DSP静态资源同步.md` |

领域文档中的当前架构和强约束只在相关任务中生效；根文件负责把 Agent 引导到正确入口，不把领域规则提升为全库规则。

## 文件删除安全边界（强约束）

- 禁止对仓库根、工作区根、`.git`、包含 Git 仓库的父目录执行递归删除或移动。
- Windows 上删除或移动文件必须在同一个 PowerShell 进程内完成；不得把 PowerShell 变量拼入 `cmd /c`、批处理或另一种 shell。
- 递归操作前必须把目标解析为绝对路径，并验证目标严格位于用户明确指定的目录内；未定义变量、空字符串、通配符和命令替换不得参与目标计算。
- 临时产物只允许写入并清理 `%TEMP%/codeyun/...`。如果误写到仓库根，只报告精确路径，除非用户明确授权，否则不得自动补救删除。
- 任何删除目标等于仓库根、包含 `.git`、或是仓库根的父级时，立即停止，不得以“清理临时文件”为由继续。
