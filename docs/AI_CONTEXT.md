# AI Context: CodeYun

> **Last Updated**: 2026-07-27
> **Purpose**: 本文档旨在为AI提供CodeYun项目的全局上下文、架构设计与核心逻辑，以便快速理解代码并进行准确的修改。

## 1. 项目概览 (Project Overview)

**CodeYun** 是个人超级工具集成平台，主仓库在 `codeyun`，但部分运行单元会显式编排和托管外部同源运行时，例如 `xlproject/xlsln/kq5034` 的考勤行为树。

> **CRITICAL**: 默认优先在 `D:\home\chenkunze\slns\codeyun` 内搜索、分析和修改；但当任务明确涉及考勤行为树、凡修行为树、运行单元托管、外部脚本接管或 CodeYun 调用的外部业务运行时时，必须把 `codeyun` 视为控制层、把对应外部仓库视为被托管运行时，一并检查真实入口与状态文件，不能假装 `codeyun` 与 `xlproject/pyxllib` 完全无关。


*   **核心能力**:
    *   **集群任务管理**: 统一管理本地及远程设备上的常驻服务（如 Python 脚本、Nginx、Syncthing）。
    *   **进程接管**: 基于 PID 追踪进程状态，支持“关联”已运行的外部进程。
    *   **Agent 模式**: 每个运行后端实例的设备既是 Server 也是 Agent，支持组成对等网络（Mesh-like）。
    *   **远程文件系统**: 浏览和操作远程设备的文件目录。
    *   **远端 Sub-Agent（设计中）**: 通过设备能力探测和受控运行接口调用远端 Codex CLI 等 agent runtime。设计见 `docs/platform/architecture/远端SubAgent能力设计.md`，实现前不要继续把它当成默认可用能力。
    *   **凡修手游自动化**：所有凡修任务统一进入 `D:\home\chenkunze\slns\skills\fanxiu\SKILL.md`，先按其只读主干判断接口层、调度层或业务层，再读取一个对应能力层。当前代码、API、外部工作区和运行手册从 [`docs/domains/fanxiu/README.md`](./domains/fanxiu/README.md) 查询；本文件不复制凡修内部规则。
    *   **行为树运行单元运维**: 若任务涉及考勤行为树或凡修行为树的启动、重启、停机、单实例诊断、状态文件、日志文件或“旧框架是否还有效”的判断，先读 `docs/operations/runbooks/行为树运行单元运维约定.md`。该文档定义了 CodeYun 作为控制层的正式入口、状态文件位置、旧 `xlserver`/脚本直觉的废弃边界，以及对外部 `xlproject` 运行时的接管方式。当前统一 runtime action 已明确到：考勤 `inspect/restart/reset`，凡修 `inspect/restart/wake`；不要再把凡修 `wake` 误读为 restart，也不要把 attendance Windows descendant 进程误读为第二棵 root 行为树。考勤与凡修的正式执行主机现在都为 `codepc_mf`；`codepc_mi15` 已退出考勤生产调度。前端运行页/日志页对这些 builtin service 的按钮、说明和反馈文案优先读取 runtime item 元数据 `action_labels/action_descriptions/action_success_messages/action_error_messages`，不要再额外写页面分支猜语义。凡修源码路径优先认 `backend.core.fanxiu.*`；`backend.core.fanxiu_behavior_tree` 与 `backend.core.fanxiu_data_annotation_*` 仅为兼容别名。
    *   **考勤主数据与订单入口**: `codepc_mf` 使用本机既有浏览器登录态下载小鹅通用户清单、微信支付原始 CSV，并在同一 CodeYun 数据工作区完成原文件归档、解析、用户/支付主数据保存，以及报名表用户 ID 和订单匹配。日常运行不再读取 PG 的 `user_table / weipay_table / weipay_matview`，这些表只在迁移观察期作为只读核对源。微信支付网页实时核验或退款仍统一调用 CodeYun 高层 API：`POST /api/attendance/order-execute`、`POST /api/attendance/order-refund-details`，其执行设备必须配置为 mf，不再转发到 mi15。课程视频与打卡配置、数据只使用课程工作簿，不再读取 PG。处理课程配置、完成算法和 Step 1–6 前，还必须先读 [`考勤课程ABC分类约定`](./domains/attendance/考勤课程ABC分类约定.md)：A 类是普通觉观/念住月课，B 类是禅宗/修道班，C 类是闯关，三类规则不得互套。

*   **技术栈**:
    *   **Backend**: Python 3.10+, FastAPI, Uvicorn, APScheduler (定时任务), psutil (进程管理)。
    *   **Frontend**: Vue 3, TypeScript, Vite, Element Plus, Pinia (Reactive Store)。
    *   **Communication**: HTTP RESTful API 为主；任务、日志、笔记/表格资源更新保留有限 WebSocket 通知入口。

## 2. 核心架构 (Architecture)

### 2.1 系统拓扑
*   **Local Node**: 用户当前操作的节点，运行 Frontend + Backend。
*   **Remote Node**: 其他运行 Backend 的设备，通过 URL (如 `http://192.168.1.x:8000`) 连接。
*   **交互模式**: 前端直接调用本地 Backend API；本地 Backend 充当 Proxy 转发请求到远程 Node，或前端直接请求远程 Node (需解决 CORS)。*当前实现主要倾向于后端代理或直连混合模式。*

### 2.2 数据流 (Data Flow)
1.  **任务状态**: `psutil` 实时监控 -> `DeviceManager` 聚合 -> API 轮询为主，部分任务/日志状态可通过 WebSocket 房间推送 -> 前端 Store。
2.  **配置存储**: **SQLite 数据库**（默认位于仓库外的数据目录，例如 `D:\home\chenkunze\data\m2603codeyun\codepc_mf\codeyun.db`）。
    *   `Device`: 存储设备信息及 API Token。
    *   `Task`: 存储任务配置。
    *   `User`: 用户信息。
    *   *注：`tasks.json`, `devices.json` 为旧版或备份文件，核心数据已迁移至 SQLite。*
3.  **日志流**: 实时读取本地日志文件 -> HTTP 日志接口分页/按行返回；任务日志 WebSocket 端点可用于订阅推送。

## 3. 目录映射 (Directory Map)

### Backend (`D:\home\chenkunze\slns\codeyun\backend`)
| 路径 | 职责 | 关键文件/说明 |
| :--- | :--- | :--- |
| `app.py` | **入口** | FastAPI 应用实例，CORS 配置，路由挂载。 |
| `api/` | **接口层** | `task_manager.py` (核心任务逻辑), `agent.py` (节点发现), `filesystem.py` (文件操作)。 |
| `core/` | **业务逻辑** | `device.py`: 封装设备抽象 (Local/Remote) 和底层进程操作。 |
| `CODEYUN_DATA_DIR` | **持久化** | 仓库外数据工作区，默认形如 `D:\home\chenkunze\data\m2603codeyun\codepc_<本机名>`，包含 `codeyun.db` 等运行数据。 |
| `../scripts/` | **工具脚本** | 根目录脚本集合，包含验证、同步、修复等维护入口。 |
| `../tests/` / `tests/` | **测试** | 根目录 `tests/` 是 pytest 默认收集目录；`backend/tests/` 存放后端专项测试。 |

### Frontend (`D:\home\chenkunze\slns\codeyun\frontend`)
| 路径 | 职责 | 关键文件/说明 |
| :--- | :--- | :--- |
| `src/standard/` / `src/views/` | **页面** | 新标准页面集中在 `src/standard/**/page.vue`；集群任务/日志页分别位于 `src/standard/cluster/tasks/page.vue` 和 `src/standard/cluster/logs/page.vue`，`src/views/` 仍保留部分旧入口如 `FileExplorer.vue`。 |
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
*   **环境**: Python 命令优先通过 `uv run` 执行，不依赖全局 Python 或其他项目虚拟环境。
*   **依赖管理**: 本项目使用 **uv** 进行依赖和虚拟环境管理。
    *   **添加依赖**: 使用 `uv add <package>`，严禁直接使用 `pip install`。
    *   **同步环境**: 使用 `uv sync` 确保环境与 `pyproject.toml` 一致。
*   **启动**: 统一使用 `uv run dev.py` 启动双端。
*   **测试**: 正式测试主要位于 `tests/`（pytest 默认收集目录）和 `backend/tests/`（后端专项测试）。禁止创建根目录临时脚本，测试应规范化并持久保留。
*   **UI展示**: 敏感信息（如 Token）在所有视图中均应完全隐藏。仅在编辑模式下提供“覆盖/重置”功能（即输入框默认为空，不回显旧值，输入新值则更新，留空则保持不变）。

## 6. 测试策略 (Testing Strategy)

为保证代码质量和可维护性，所有验证性代码都应视为正式测试：
*   **位置**: 优先放入 `tests/` 下对应子目录；后端专项测试可放入 `backend/tests/` 并在验证命令中显式指定。
*   **形式**: 编写为标准的 `unittest` 用例或独立的测试模块，避免随手写的 `print` 脚本。
*   **持久性**: 测试脚本应作为项目资产保留，不应在验证完成后删除，以便通过 CI/CD 或手动运行进行回归测试。

### 6.1 测试环境注意事项
*   **数据库隔离**: 单元测试 (`test_cluster_api.py`) 使用内存数据库 (`sqlite://`) 和 Mock 对象，避免污染生产数据。
*   **集成测试**: 部分测试脚本 (`test_backend.py`) 会直接调用运行中的 Backend API (`localhost:8000`)。此类测试**必须**包含清理逻辑 (`try...finally`)，确保运行后删除创建的临时资源（如测试设备、任务），防止垃圾数据堆积。
*   **单例状态**: `DeviceManager` 为单例模式。测试若修改其内部状态（如 Patch 数据库引擎），务必在 `tearDown` 中还原，防止状态泄漏影响后续测试或同一进程中的应用逻辑。

## 7. 重要开发提示 (Crucial Development Notes)

### 7.3 实时通信架构 (Real-time Communication Architecture)
CodeYun 同时存在 WebSocket 通知能力和页面级轮询逻辑，修改任务/日志链路时不要假设所有页面都已经完全 WebSocket 化：

*   **任务列表更新 (Task List Updates)**:
    *   **Endpoint**: `/ws/tasks`
    *   **Backend Behavior**: 后端保留 `task_list` 房间的 WebSocket 广播能力。
    *   **Current Frontend**: 当前集群任务页位于 `frontend/src/standard/cluster/tasks/page.vue`，仍通过 `taskStore` 和接口调用定时刷新任务列表。
*   **实时日志流 (Real-time Log Streaming)**:
    *   **Endpoint**: `/ws/logs/{task_id}`
    *   **Backend Behavior**: 后端通过 `LocalDevice` 的回调机制捕获子进程的标准输出 (stdout)，并可通过 WebSocket 推送给订阅房间。
    *   **Current Frontend**: 当前日志页位于 `frontend/src/standard/cluster/logs/page.vue`，仍保留任务和日志的 HTTP 拉取与轮询逻辑。
    *   **Fallback**: 同时保留 `/api/task/{id}/logs` HTTP 接口用于获取历史日志（默认最近 500 行）。
*   **断线重连 (Reconnection Strategy)**:
    *   新接入 WebSocket 的前端页面必须实现自动重连机制（如每 3s 尝试重连），以应对后端服务重启或网络波动；现有轮询页面则要保证轮询定时器随路由切换正确清理。

### 7.1 联动修改 (Linked Modifications)
CodeYun 的功能模块往往涉及前后端及多个组件的联动，修改时需特别注意：
*   **字段同步**: 在 `TaskConfig` (后端 Model) 中添加新字段（如 `schedule`）时，必须同步更新：
    *   **Frontend Interface**: `src/store/taskStore.ts` 中的 `Task` 接口。
    *   **Create/Edit Forms**: `frontend/src/standard/cluster/tasks/page.vue` (创建) 和 `frontend/src/standard/cluster/logs/page.vue` (编辑) 中的表单。
    *   **Details Display**: `frontend/src/standard/cluster/logs/page.vue` 中的详情展示部分，避免“只改了表单没改展示”的情况。
    *   **List View**: `frontend/src/standard/cluster/tasks/page.vue` 的列表项展示（如果适用）。
*   **API Consistency**: 确保 `create_task` 和 `update_task` 的 API 行为一致，特别是在处理默认值和可选字段时。

### 7.2 文档维护 (Documentation Maintenance)
*   每次添加新功能（如“编辑任务”），必须同步更新 `AGENTS.md` 中的功能描述，确保用户和 AI 助手都能获取最新信息。
