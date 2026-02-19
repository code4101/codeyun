# CodeYun (代码云)

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

###先决条件
- Python 3.10 或更高版本
- Node.js & npm (为了方便，`tools/node` 中包含了一个本地版本)

### 快速开始

本项目包含一个辅助脚本 `dev.py`，用于自动启动后端和前端服务。

1. **克隆仓库**：
   ```bash
   git clone <repository-url>
   cd codeyun
   ```

2. **安装后端依赖**：
   建议使用虚拟环境。
   ```bash
   # 创建虚拟环境
   python -m venv .venv
   
   # 激活虚拟环境
   # Windows:
   .venv\Scripts\activate
   # Linux/Mac:
   source .venv/bin/activate

   # 安装依赖
   pip install -r backend/requirements.txt
   # 或者如果使用 pyproject.toml
   pip install -e backend
   ```

3. **启动开发服务器**：
   从根目录运行 `dev.py` 脚本。该脚本将：
   - 检查本地 Node.js 环境。
   - 在 `http://localhost:8000` 启动 FastAPI 后端。
   - 安装前端依赖（如果缺失）并在 `http://localhost:5173` 启动 Vite 开发服务器。

   ```bash
   python dev.py
   ```

4. **访问应用**：
   打开浏览器并访问：
   - **前端**：[http://localhost:5173](http://localhost:5173)
   - **后端 API 文档**：[http://localhost:8000/docs](http://localhost:8000/docs)

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
├── dev.py                  # 开发启动脚本
└── start.ps1               # PowerShell 启动脚本
```

## 📝 配置

- **后端配置**：通过环境变量和 `backend/data/` 中的本地 JSON 文件进行管理。
- **前端配置**：`frontend/vite.config.ts` 中的 Vite 配置。

## 🤝 贡献

1. Fork 本仓库。
2. 创建您的特性分支 (`git checkout -b feature/AmazingFeature`)。
3. 提交您的更改 (`git commit -m 'Add some AmazingFeature'`)。
4. 推送到分支 (`git push origin feature/AmazingFeature`)。
5. 开启一个 Pull Request。

## 📄 许可证

[Apache License 2.0](LICENSE)
