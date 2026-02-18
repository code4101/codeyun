# AI Context: CodeYun

> **Last Updated**: 2026-02-17
> **Purpose**: 本文档旨在为AI提供CodeYun项目的全局上下文、架构设计与核心逻辑，以便快速理解代码并进行准确的修改。

## 1. 项目概览 (Project Overview)

**CodeYun** 是一个个人超级工具集成平台，核心定位是**跨设备的后台任务管理与远程控制系统**。

*   **核心能力**:
    *   **集群任务管理**: 统一管理本地及远程设备上的常驻服务（如 Python 脚本、Nginx、Syncthing）。
    *   **进程接管**: 基于 PID 追踪进程状态，支持“关联”已运行的外部进程。
    *   **Agent 模式**: 每个运行后端实例的设备既是 Server 也是 Agent，支持组成对等网络（Mesh-like）。
    *   **远程文件系统**: 浏览和操作远程设备的文件目录。

*   **技术栈**:
    *   **Backend**: Python 3.10+, FastAPI, Uvicorn, APScheduler (定时任务), psutil (进程管理)。
    *   **Frontend**: Vue 3, TypeScript, Vite, Element Plus, Pinia (Reactive Store)。
    *   **Communication**: HTTP RESTful API (设备间直接通信或通过主节点代理)。

## 2. 核心架构 (Architecture)

### 2.1 系统拓扑
*   **Local Node**: 用户当前操作的节点，运行 Frontend + Backend。
*   **Remote Node**: 其他运行 Backend 的设备，通过 URL (如 `http://192.168.1.x:8000`) 连接。
*   **交互模式**: 前端直接调用本地 Backend API；本地 Backend 充当 Proxy 转发请求到远程 Node，或前端直接请求远程 Node (需解决 CORS)。*当前实现主要倾向于后端代理或直连混合模式。*

### 2.2 数据流 (Data Flow)
1.  **任务状态**: `psutil` 实时监控 -> `DeviceManager` 聚合 -> API 轮询/WebSocket (规划中) -> 前端 Store。
2.  **配置存储**: JSON 文件持久化 (`backend/data/tasks.json`, `devices.json`)。
3.  **日志流**: 实时读取本地日志文件 -> API 分页返回。

## 3. 目录映射 (Directory Map)

### Backend (`c:\home\chenkunze\slns\codeyun\backend`)
| 路径 | 职责 | 关键文件/说明 |
| :--- | :--- | :--- |
| `app.py` | **入口** | FastAPI 应用实例，CORS 配置，路由挂载。 |
| `api/` | **接口层** | `task_manager.py` (核心任务逻辑), `agent.py` (节点发现), `filesystem.py` (文件操作)。 |
| `core/` | **业务逻辑** | `device.py`: 封装设备抽象 (Local/Remote) 和底层进程操作。 |
| `data/` | **持久化** | 存放 `tasks.json`, `devices.json` 及任务日志。 |
| `tests/` | **测试** | 单元测试和集成测试。 |

### Frontend (`c:\home\chenkunze\slns\codeyun\frontend`)
| 路径 | 职责 | 关键文件/说明 |
| :--- | :--- | :--- |
| `src/views/` | **页面** | `TaskManager.vue` (集群管理核心), `FileExplorer.vue`。 |
| `src/store/` | **状态** | `taskStore.ts`: 简单的 Reactive 对象，管理任务列表和设备列表。 |
| `src/api/` | **网络** | `index.ts`: Axios 封装。 |

## 4. 核心业务逻辑 (Domain Logic)

### 4.1 任务管理 (Task Management)
*   **模型 (`TaskConfig`)**:
    *   `id`: UUID。
    *   `command`: 执行命令 (支持 shell 拆分)。
    *   `cwd`: 工作目录。
    *   `schedule`: Cron 表达式 (可选)，由 `APScheduler` 驱动。
    *   `device_id`: 归属设备。
*   **生命周期**:
    *   `start`: `subprocess.Popen` 启动 -> 记录 PID -> 状态变更为 Running。
    *   `stop`: 发送 SIGTERM/SIGKILL -> 状态变更为 Stopped。
    *   `monitor`: 周期性检查 PID 是否存活。
*   **进程关联 (Association)**:
    *   允许用户输入一个 PID，系统反向查找该 PID 的 Command 和 CWD，生成 Task 配置。用于接管在 CodeYun 之外启动的进程。

### 4.2 设备管理 (Device Management)
*   **LocalDevice**: 直接调用 `psutil` 和 `subprocess`。
*   **RemoteDevice**: 实现了与 `LocalDevice` 相同的接口，但通过 `requests` 调用远程 API。
*   **同步机制**: 前端轮询 `/api/task/list`，后端会触发 `device_manager` 同步所有注册设备的状态。

## 5. 开发规约 (Conventions)

*   **端口**: Backend `:8000`, Frontend `:5173`。
*   **路径**: 所有文件路径应使用绝对路径，或基于 `root_dir` 动态计算。
*   **环境**: 优先使用项目内的 `.venv`，其次是系统 Python。
*   **启动**: 统一使用根目录 `dev.py` 启动双端。
*   **测试**: 测试代码必须存放在 `backend/tests/`。禁止创建根目录临时脚本，测试应规范化并持久保留。

## 6. 测试策略 (Testing Strategy)

为保证代码质量和可维护性，所有验证性代码都应视为正式测试：
*   **位置**: 统一存放在 `backend/tests/` 目录下。
*   **形式**: 编写为标准的 `unittest` 用例或独立的测试模块，避免随手写的 `print` 脚本。
*   **持久性**: 测试脚本应作为项目资产保留，不应在验证完成后删除，以便通过 CI/CD 或手动运行进行回归测试。

## 7. 重要开发提示 (Crucial Development Notes)

### 7.3 实时通信架构 (Real-time Communication Architecture)
为提升用户体验并降低轮询带来的资源浪费，CodeYun 已全面转向 **WebSocket** 架构：

*   **任务列表更新 (Task List Updates)**:
    *   **Endpoint**: `/ws/tasks`
    *   **Behavior**: 后端定期（如 2s）广播所有任务的最新状态。前端 `TaskManager.vue` 连接此 WebSocket，实时接收并更新任务列表，不再使用轮询。
*   **实时日志流 (Real-time Log Streaming)**:
    *   **Endpoint**: `/ws/logs/{task_id}`
    *   **Behavior**: 后端通过 `LocalDevice` 的回调机制捕获子进程的标准输出 (stdout)，并通过 WebSocket 实时推送给前端 `TaskLogs.vue`。
    *   **Fallback**: 同时保留 `/api/task/{id}/logs` HTTP 接口用于获取历史日志（默认最近 500 行）。
*   **断线重连 (Reconnection Strategy)**:
    *   前端必须实现自动重连机制（如每 3s 尝试重连），以应对后端服务重启或网络波动的情况，确保页面长期运行的稳定性。

### 7.1 联动修改 (Linked Modifications)
CodeYun 的功能模块往往涉及前后端及多个组件的联动，修改时需特别注意：
*   **字段同步**: 在 `TaskConfig` (后端 Model) 中添加新字段（如 `schedule`）时，必须同步更新：
    *   **Frontend Interface**: `src/store/taskStore.ts` 中的 `Task` 接口。
    *   **Create/Edit Forms**: `TaskManager.vue` (创建) 和 `TaskLogs.vue` (编辑) 中的表单。
    *   **Details Display**: `TaskLogs.vue` 中的详情展示部分 (`el-descriptions`)，避免“只改了表单没改展示”的情况。
    *   **List View**: `TaskManager.vue` 的列表项展示（如果适用）。
*   **API Consistency**: 确保 `create_task` 和 `update_task` 的 API 行为一致，特别是在处理默认值和可选字段时。

### 7.2 文档维护 (Documentation Maintenance)
*   每次添加新功能（如“编辑任务”），必须同步更新 `AGENTS.md` 中的功能描述，确保用户和 AI 助手都能获取最新信息。
