# CodeYun (代号云)

CodeYun 是一个个人超级工具集成平台，旨在为跨设备后台任务管理、远程控制和系统监控提供全面的解决方案。它作为一个中心枢纽，用于管理您的数字环境。

## 🚀 功能特性

### 1. 集群任务管理器 (Cluster Task Manager)
- **统一界面**：从单一仪表板管理多个设备（本地和远程）上的任务。
- **进程管理**：基于 PID 启动、停止和监控进程，并提供权威的状态跟踪。
- **任务调度**：支持基于 Cron 的调度，以自动化日常任务。
- **实时日志**：查看正在运行任务的实时日志，以调试和监控性能。
- **拖放排序**：通过拖放功能轻松组织您的任务列表。

### 2. 多设备 Agent 系统 (Multi-Device Agent System)
- **Agent 模式**：每个 CodeYun 后端实例都可以作为一个 Agent 运行，允许您构建受管设备集群。
- **远程控制**：通过 URL 添加远程设备，并无缝控制其任务和文件系统。
- **设备发现**：（计划中）自动发现本地网络上的设备。

### 3. 文件系统资源管理器 (File System Explorer)
- **远程访问**：浏览和操作已连接远程设备上的文件和目录。
- **文件操作**：支持基本的文件操作（查看、编辑、删除）。

## 🛠️ 技术栈

### 后端 (Backend)
- **语言**：Python 3.10+
- **框架**：FastAPI (高性能 Web 框架)
- **服务器**：Uvicorn (ASGI 服务器)
- **核心库**：
  - `psutil`：用于系统和进程监控。
  - `apscheduler`：用于高级任务调度。
  - `pydantic`：用于数据验证和设置管理。

### 前端 (Frontend)
- **框架**：Vue 3 (Composition API)
- **构建工具**：Vite (下一代前端工具)
- **语言**：TypeScript
- **UI 库**：Element Plus
- **状态管理**：Pinia
- **HTTP 客户端**：Axios

## 📦 安装与设置

### 快速开始

1. **安装工具**：
   - **uv** (Python管理)：[安装指南](https://github.com/astral-sh/uv)
   - **Node.js** (前端依赖)：
     - **Ubuntu**: `sudo apt update && sudo apt install -y nodejs npm`
     - **Windows**: 推荐安装 [LTS 版本](https://nodejs.org/)，并确保 `npm` 已添加到环境变量 `PATH` 中。
     - **Mac**: 推荐使用 `brew install node`。
2. **初始化环境**：
   ```bash
   # 1. 同步后端环境
   uv sync
   
   # 2. 安装前端依赖
   # Windows 用户如果遇到 npm 找不到的问题，请尝试以管理员身份运行终端，或检查 PATH 环境变量
   npm install --prefix frontend
   ```
3. **启动服务**：
   运行启动脚本：
   ```bash
   uv run dev.py
   ```
   > **注意 (Windows)**: 如果 `dev.py` 启动前端失败，你可以手动在另一个终端窗口启动前端：
   > ```bash
   > cd frontend
   > npm run dev
   > ```

服务启动后访问：
- **前端**：[http://localhost:5173](http://localhost:5173)
- **后端 API**：[http://localhost:8000/docs](http://localhost:8000/docs)

## 📂 项目结构

```
codeyun/
├── backend/                # Python FastAPI 后端
│   ├── api/                # API 端点
│   ├── core/               # 核心逻辑和工具
│   ├── data/               # 数据存储 (JSON 文件, 日志)
│   ├── tests/              # 后端测试
│   └── app.py              # 应用程序入口点
├── frontend/               # Vue 3 + TypeScript 前端
│   ├── src/
│   │   ├── api/            # API 客户端封装
│   │   ├── components/     # 可复用 Vue 组件
│   │   ├── views/          # 页面视图 (任务管理器等)
│   │   └── store/          # 状态管理
│   └── vite.config.ts      # Vite 配置
├── tools/                  # 辅助工具 (例如本地 Node.js)
├── AGENTS.md               # Agent 文档和项目状态
├── TODO.md                 # 项目路线图和待办事项
└── dev.py                  # 开发启动脚本
```

## 📝 配置

- **后端配置**：通过环境变量和 `backend/data/` 中的本地 JSON 文件进行管理。
- **前端配置**：`frontend/vite.config.ts` 中的 Vite 配置。

## 📄 许可证

[Apache License 2.0](LICENSE)
