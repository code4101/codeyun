# AGENTS.md

## 默认策略

- 默认依据相关代码、测试、配置、类型和公共接口调查，不预读全库文档。
- 仅在跨模块架构、业务语义、部署运维、历史决策或文档维护时，按需读取 `docs/文档中心.md` 指向的最小必要正文，并用代码和测试核验实现事实。
- 若 CodeYun 编排仓库外运行时，同时检查其真实入口、公共接口和状态文件。
- 用户说“工作台（Workbench）”时，按 `frontend/src/components/editor-workspace/workspaceMenu.ts` 的术语与代码入口定位共享 UI；涉及具体业务时再进入对应装配组件。

## 运行约定

- 命令默认在仓库根目录 `C:\home\chenkunze\slns\codeyun` 执行。
- Python 命令优先使用 `uv run`；启动开发环境用 `uv run dev.py`，测试用 `uv run pytest`，临时命令用 `uv run python ...`。
- 前端依赖安装用 `npm install --prefix frontend`，单独启动用 `npm run dev --prefix frontend`。
- 仅在 `uv` 不可用时使用 `.\.venv\Scripts\python.exe`，不要依赖全局 Python 或其他项目的虚拟环境。

## 环境与临时产物

- `https://code4101.com` 回源本机 `localhost`，不是独立公网实例。数据通常位于仓库外的 `CODEYUN_DATA_DIR`；结果不一致时先核对实际进程环境和数据目录。
- 截图、日志、探针 JSON、临时数据库及一次性输出写入 `%TEMP%\codeyun\...`；Python 优先使用 `backend.core.temp_paths.codeyun_temp_root(...)`，不要污染源码目录。
- 创建大体积临时副本前评估空间；结束后停止相关进程、删除副本并复核空间，失败时报告精确路径和占用。

## 删除安全

- 禁止递归删除或移动仓库根、工作区根、`.git` 或包含 Git 仓库的父目录。递归操作前必须解析并核验绝对目标，禁止使用空变量、未定义变量、通配符或命令替换确定目标。
- Windows 删除或移动须在同一 PowerShell 进程内完成，不得跨 shell 传递路径。临时产物误写入仓库时，只报告路径，未经授权不要删除。

## API 与 Agent 边界

- 以公共 API schema、类型、docstring 和 CLI `--help` 为契约；不要调用私有函数、读取内部数据库/WAL，或拼低层流程补偿接口缺口。
- ID 解析、落盘、锁、事务、并发、幂等、去重、内部轮询和迁移属于提供方。接口不能表达业务意图时，修复高层接口并补测试。

## 文档维护

- 仅在新增、移动或改变文档权威关系时维护 `docs/文档中心.md`；本文件不复制目录树、业务导航或领域规则。
- 计划、研究和历史材料不能覆盖当前架构与公共契约；移动文档时同步修复有效引用。
- 凡修与考勤的领域文档以各自技能为权威：仓库内 `doc/` 目录通过目录联接（Junction）指向 `C:\home\chenkunze\slns\skills\凡修` 与 `考勤`，`docs/文档中心.md` 亦以 `../../skills/` 相对路径引用。
