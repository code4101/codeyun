# CodeYun

[English](README.md)

官网：https://code4101.com/

CodeYun 是一个本地优先、可自托管的个人与小团队生产力平台。

它来自真实的日常工作流，但核心模块被整理成可复用的构件：知识图谱笔记、表格数据工作流、任务调度、集群/进程管理、日志，以及轻量级运维工具。

CodeYun 把知识管理、表格数据流、任务调度、集群进程管理和常用工具页面整合到同一个前后端工作台里，适合长期维护自己的自动化系统、数据工作流和内部工具。

## 核心亮点

- **知识图谱笔记**：将笔记作为结构化实体和关系来管理，并通过可复用的筛选规则支持列表、全局图谱、日历等不同视图。
- **表格工作流**：支持表格数据的导入、重建、校验，并通过后端 API 和专用工具页面提供可重复使用的数据处理能力。
- **任务与作业管理**：定义可复用的任务类型，运行长驻作业，查看状态，并将调度配置与源码保持分离。
- **集群/进程管理**：注册本地或远程设备，在统一界面中启动、停止任务，收集日志，并监控任务状态。
- **运维与实用工具**：提供 AI 辅助工作流、文件/PDF 处理、配置管理、诊断检查和开发验证等小型实用工具。
- **开发友好的运行方式**：使用统一的 `uv` + FastAPI + Vue/Vite 工作流，并配套测试、生产检查、环境模式和文档化约定。

## 适用场景

- 搭建个人或小团队自托管自动化面板。
- 用图谱数据管理知识笔记，而不是维护孤立文档。
- 将重复性的表格操作沉淀成可复用的 API 工作流。
- 跨多台本地或远程机器管理后台服务。
- 把项目专用工具收束到一个一致的内部平台，而不是散落在脚本里。

## 界面截图

### 集群与任务管理

![集群与任务管理面板](docs/assets/集群管理.png)

在一个面板里管理本地和远程设备，查看资源占用，监督服务状态，并调度周期作业。

### 星图笔记

![星图笔记日历视图](docs/assets/星图笔记.png)

将笔记组织成图谱实体，使用规则链筛选，并在日历、图谱、列表等视图之间切换。

### 可视化规划工具

![可视化生产规划工具](docs/assets/戴森球.png)

把领域工具纳入同一个平台，包括交互式计算器、规划器和可视化工作流界面。

## 文档

- [模块说明](docs/modules.zh-CN.md)：笔记、表格、集群管理、作业和工具模块的中文说明。
- [Module Guide](docs/modules.md)：English feature overview and usage guide.
- [仓库约定](AGENTS.md)：本地开发、测试、前端、部署和维护约定。

## 技术栈

- **后端**：Python, FastAPI, SQLModel, APScheduler, Uvicorn
- **前端**：Vue, Vite, TypeScript, Element Plus
- **运行环境**：`uv`、本地 SQLite/数据工作区、可选自托管部署
- **测试**：使用 `pytest` 覆盖后端行为和集成检查

## 快速开始

在仓库根目录执行：

```bash
uv sync
npm install --prefix frontend
uv run dev.py
```

启动后访问：

- Frontend: http://localhost:5173
- Backend API: http://localhost:8000/docs

公开官网：https://code4101.com/

`dev.py` 是推荐的开发入口。它会启动并监督后端和前端开发服务，同时将独立管理的集群任务与开发进程隔离开。

默认开发行为可通过以下环境变量配置：

- `CODEYUN_DEV_CHECK_INTERVAL_SECONDS`
- `CODEYUN_DEV_BACKEND_RELOAD_COOLDOWN_SECONDS`
- `CODEYUN_DEV_BACKEND_RELOAD_MODE`

如需使用 Uvicorn 内置 reload 模式：

```bash
uv run dev.py --backend-reload-mode uvicorn
```

## 运行约定

- Python 命令通过 `uv run` 执行。
- 避免依赖全局 Python 或其他项目的虚拟环境。
- 使用 `uv run dev.py` 启动集成开发环境。
- 使用 `uv run pytest` 运行测试。
- 详细仓库约定见 [AGENTS.md](AGENTS.md)。

## 常用命令

```bash
# 后端测试
uv run pytest

# 前端开发
npm run dev --prefix frontend

# 仅启动后端
uv run python -m uvicorn backend.app:app --reload --host 0.0.0.0 --port 8000

# 前端类型/构建检查
npm run check --prefix frontend

# 本地生产式检查
uv run python scripts/check_prod.py
```

## 环境模式

- `development`：由 `dev.py` 使用，保留开发 CORS 和热更新行为。
- `test`：由自动化测试使用，避免读取本地 `.env`。
- `production`：默认关闭公开 API 文档，并使用面向生产的配置。

后端配置集中在 [`backend/core/settings.py`](backend/core/settings.py)，可通过 `.env` 或系统环境变量覆盖。

## 项目结构

```text
codeyun/
├── backend/          # FastAPI 后端、API、服务和标准模块
├── frontend/         # Vue/Vite 前端
├── docs/             # 设计文档和 AI 维护上下文
├── scripts/          # 维护脚本与数据工作流脚本
├── tests/            # 后端测试
├── dev.py            # 集成开发监督进程
├── pyproject.toml
└── AGENTS.md
```

## 维护说明

仓库曾包含 GitHub Actions 自动部署流程，但该流程已移除。当前生产部署不依赖仓库内的部署 workflow。如需恢复旧方案，可参考 [docs/自动部署恢复档案.md](docs/自动部署恢复档案.md)。

## 许可证

[Apache License 2.0](LICENSE)
