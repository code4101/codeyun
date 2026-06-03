# AI Context: CodeYun

> **Last Updated**: 2026-05-30
> **Purpose**: 本文档旨在为AI提供CodeYun项目的全局上下文、架构设计与核心逻辑，以便快速理解代码并进行准确的修改。

## 1. 项目概览 (Project Overview)

**CodeYun** 是一个**完全独立**的个人超级工具集成平台，与其他项目（如 `pyxllib`, `xlproject` 等）**无任何依赖关系**。

> **CRITICAL**: 在进行代码搜索、分析或修改时，**严格限制在 `c:\home\chenkunze\slns\codeyun` 目录下**。严禁搜索或引用项目根目录之外的任何文件。


*   **核心能力**:
    *   **集群任务管理**: 统一管理本地及远程设备上的常驻服务（如 Python 脚本、Nginx、Syncthing）。
    *   **进程接管**: 基于 PID 追踪进程状态，支持“关联”已运行的外部进程。
    *   **Agent 模式**: 每个运行后端实例的设备既是 Server 也是 Agent，支持组成对等网络（Mesh-like）。
    *   **远程文件系统**: 浏览和操作远程设备的文件目录。
    *   **远端 Sub-Agent（设计中）**: 通过设备能力探测和受控运行接口调用远端 Codex CLI 等 agent runtime。设计见 `docs/远端SubAgent能力设计.md`，实现前不要继续把它当成默认可用能力。
    *   **凡修逆向/图鉴上下文**: 凡人修仙传手游的资源、APK、IL2CPP、热更新 Lua、图鉴和抓包分析有独立 AI 交接文档：`docs/FANXIU_REVERSE_CONTEXT.md`。后续 agent 接手相关任务时先读该文档，再看具体代码或外部导出目录。若用户提到增量更新凡修逆向、更新凡修图鉴或同步最新游戏数据，还必须遵守 `docs/凡修逆向增量更新约定.md`：默认使用快照、diff、选择性合并的增量机制，不要无理由暴力全量重算或覆盖业务数据。若任务涉及凡修抓包服务、pcap、协议解析、储物袋、拜谒榜单、活动排行或运行态数据入库，先读 `docs/凡修抓包服务架构约定.md` 和 `docs/凡修抓包业务数据落库设计.md`：抓包监控器、解析队列、业务增量写入器和图鉴只读展示必须解耦，已探明业务域必须由抓包服务增量写入数据库；图鉴页绝对禁止触发抓包、解析、同步、重建或历史补扫。若用户对照图鉴页反馈解析/更新问题，优先处理抓包服务和业务写入链路，只有数据库已有正确事实但展示错误时才改图鉴页。若任务涉及凡修 game-window3、Runtime、Scheduler、守护、MuMu 画面流或资产树，先读 `docs/凡修game-window3运行设备约定.md`：当前凡修自动化只以 `codepc_mf` 为运行目标，`codepc_mi15` 旧凡修数据不再作为事实来源。若任务涉及 `/fanxiu/data-annotation/runtime`、行为树、守护/作业清单、开关、tick 或运行日志，先读 `docs/凡修行为树运行框架约定.md`：守护和作业清单必须由后端定义，前端只展示后端状态并提交 `guard_id`/任务 id 的开关或白名单信息。

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
2.  **配置存储**: **SQLite 数据库**（默认位于仓库外的数据目录，例如 `D:\home\chenkunze\data\m2603codeyun\codepc_mf\codeyun.db`）。
    *   `Device`: 存储设备信息及 API Token。
    *   `Task`: 存储任务配置。
    *   `User`: 用户信息。
    *   *注：`tasks.json`, `devices.json` 为旧版或备份文件，核心数据已迁移至 SQLite。*
3.  **日志流**: 实时读取本地日志文件 -> API 分页返回。

## 3. 目录映射 (Directory Map)

### Backend (`c:\home\chenkunze\slns\codeyun\backend`)
| 路径 | 职责 | 关键文件/说明 |
| :--- | :--- | :--- |
| `app.py` | **入口** | FastAPI 应用实例，CORS 配置，路由挂载。 |
| `api/` | **接口层** | `task_manager.py` (核心任务逻辑), `agent.py` (节点发现), `filesystem.py` (文件操作)。 |
| `core/` | **业务逻辑** | `device.py`: 封装设备抽象 (Local/Remote) 和底层进程操作。 |
| `data/` | **持久化** | **`codeyun.db` (SQLite 数据源)**。`config.json` 存储本机唯一ID和API Token。 |
| `scripts/` | **工具脚本** | `get_machine_token.py`: **获取本机 API Token**。 |
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
*   **身份标识**: 本机通过机器级 identity 文件持久化唯一 ID (UUID)，业务运行数据位于仓库外的数据目录。若身份文件丢失，重启后会生成新 ID，可能导致旧数据失效或重复注册（Phantom Devices）。

## 5. 开发规约 (Conventions)

*   **端口**: Backend `:8000`, Frontend `:5173`。
*   **路径**: 所有文件路径应使用绝对路径，或基于 `root_dir` 动态计算。
*   **环境**: 优先使用项目内的 `.venv`，其次是系统 Python。
*   **依赖管理**: 本项目使用 **uv** 进行依赖和虚拟环境管理。
    *   **添加依赖**: 使用 `uv add <package>`，严禁直接使用 `pip install`。
    *   **同步环境**: 使用 `uv sync` 确保环境与 `pyproject.toml` 一致。
*   **启动**: 统一使用根目录 `dev.py` 启动双端。
*   **测试**: 测试代码必须存放在 `backend/tests/`。禁止创建根目录临时脚本，测试应规范化并持久保留。
*   **UI展示**: 敏感信息（如 Token）在所有视图中均应完全隐藏。仅在编辑模式下提供“覆盖/重置”功能（即输入框默认为空，不回显旧值，输入新值则更新，留空则保持不变）。

## 6. 测试策略 (Testing Strategy)

为保证代码质量和可维护性，所有验证性代码都应视为正式测试：
*   **位置**: 统一存放在 `backend/tests/` 目录下。
*   **形式**: 编写为标准的 `unittest` 用例或独立的测试模块，避免随手写的 `print` 脚本。
*   **持久性**: 测试脚本应作为项目资产保留，不应在验证完成后删除，以便通过 CI/CD 或手动运行进行回归测试。

### 6.1 测试环境注意事项
*   **数据库隔离**: 单元测试 (`test_cluster_api.py`) 使用内存数据库 (`sqlite://`) 和 Mock 对象，避免污染生产数据。
*   **集成测试**: 部分测试脚本 (`test_backend.py`) 会直接调用运行中的 Backend API (`localhost:8000`)。此类测试**必须**包含清理逻辑 (`try...finally`)，确保运行后删除创建的临时资源（如测试设备、任务），防止垃圾数据堆积。
*   **单例状态**: `DeviceManager` 为单例模式。测试若修改其内部状态（如 Patch 数据库引擎），务必在 `tearDown` 中还原，防止状态泄漏影响后续测试或同一进程中的应用逻辑。

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
