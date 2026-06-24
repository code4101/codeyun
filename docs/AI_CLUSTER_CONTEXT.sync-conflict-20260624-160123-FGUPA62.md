# AI Context: Cluster Management (CodeYun)

> **Last Updated**: 2026-02-19
> **Purpose**: 本文档专门针对 **CodeYun 集群管理** 功能，为 AI 提供深度的架构设计、核心逻辑与实现细节，以便准确理解和维护集群相关代码。

## 1. 项目概览 (Project Overview)

**CodeYun** 是一个**完全独立**的个人超级工具集成平台。其核心愿景是打造一个**去中心化与主从混合**的设备管理网络，让任何一台运行 CodeYun 的设备（Node）既能作为**控制端 (Manager)** 管理其他设备，也能作为**执行端 (Agent)** 被其他设备管理。

*   **核心目标**: 统一管理多台设备上的常驻服务、进程、文件系统，实现“单点登录，全网控制”。
*   **技术栈**: FastAPI (Backend), Vue 3 (Frontend), HTTP/WebSocket (Communication), SQLite (Storage).

## 2. 集群管理架构 (Cluster Architecture)

CodeYun 的集群管理不依赖中心化的注册服务器，而是采用**点对点 (P2P-like)** 的显式注册机制。

### 2.1 节点角色 (Node Roles)
*   **Manager (控制端)**: 发起指令的节点。通常是用户当前操作的界面所在节点。
    *   负责存储设备列表 (`UserDevice` 表)。
    *   负责聚合所有节点的状态并推送给前端。
*   **Agent (执行端)**: 接收指令并执行的节点。
    *   运行实际的进程 (`subprocess`)。
    *   提供 API 供 Manager 调用。
    *   *注：Local Node 同时扮演 Manager 和 Agent 两个角色。*

### 2.2 设备发现与注册 (Device Discovery)
*   **机制**: **显式注册 (Explicit Registration)**。不使用广播/组播。
*   **流程**:
    1.  用户在 Manager 端输入目标 Agent 的 **URL** (如 `http://192.168.1.x:8000`) 和 **API Token**。
    2.  Manager 调用 Agent 的 `/api/agent/status` 接口进行握手验证。
    3.  验证通过后，Agent 信息（ID, Name, URL, Token）被加密存储在 Manager 的 SQLite 数据库中。

## 3. 核心组件与实现 (Core Components)

集群逻辑主要由以下 Python 模块驱动：

### 3.1 `backend/core/device.py` (设备抽象层)
这是集群管理的基石，定义了统一的设备操作接口。

*   **`Device` (Base Class)**: 定义了 `start_task`, `stop_task`, `get_status`, `get_logs` 等抽象方法。
*   **`LocalDevice` (Implementation)**:
    *   **执行**: 直接调用 `subprocess.Popen` 启动进程。
    *   **监控**: 使用 `psutil` 检查 PID 状态、CPU/内存占用。
    *   **日志**: 启动独立线程读取 `stdout/stderr`，写入本地日志文件，并推送到 WebSocket。
*   **`RemoteDevice` (Implementation)**:
    *   **代理 (Proxy)**: 实现了与 `LocalDevice` 相同的接口，但内部将调用转化为 **HTTP 请求**。
    *   **鉴权**: 在 HTTP Header 中自动携带 `X-API-Token`。
    *   **透传**: 将远程返回的 JSON 数据反序列化为本地对象格式。

### 3.2 `backend/api/task_manager.py` (任务调度器)
负责协调数据库、设备实例和前端接口。

*   **任务归属**: 每个任务在创建时 (`TaskConfig`) 绑定一个 `device_id`。
*   **分发逻辑**:
    *   当接收到 `start_task(task_id)` 请求时，根据 `task.device_id` 查找对应的 `Device` 实例。
    *   若为 `LocalDevice` -> 本地启动。
    *   若为 `RemoteDevice` -> 发送 `POST {remote_url}/api/task/{id}/start`。

### 3.3 `backend/api/agent.py` (Agent 接口)
暴露给外部 Manager 调用的接口。

*   **`/api/agent/status`**: 返回本机系统信息（用于握手）。
*   **`/api/agent/tasks`**: 返回本机运行的所有任务状态（供远程 Manager 轮询）。

## 4. 数据流与通信 (Data Flow)

### 4.1 任务执行 (Task Execution)
1.  **User** -> **Manager UI** -> **Manager API** (`start_task`)
2.  **Manager API** -> `TaskManager` -> `RemoteDevice.start_task()`
3.  **RemoteDevice** --(HTTP POST)--> **Agent API** (`/api/task/start`)
4.  **Agent API** -> `LocalDevice.start_task()` -> `subprocess.Popen`

### 4.2 状态同步 (Status Synchronization)
为了保证 UI 的实时性，系统采用了 **混合推送模式**：

1.  **Agent 端**: `LocalDevice` 周期性（或事件驱动）扫描本地 PID 状态。
2.  **Manager 端**:
    *   `TaskManager` 启动后台任务 (`status_broadcaster`)，每 **2秒** 执行一次。
    *   **并行拉取**: 同时向所有注册的 `RemoteDevice` 发送 HTTP GET 请求获取最新状态。
    *   **聚合**: 将 Local 和 Remote 的状态合并。
    *   **推送**: 通过 WebSocket (`/ws/tasks`) 广播给前端。

### 4.3 日志流 (Log Streaming)
*   **Local**: `tail -f` 模式读取文件 -> WebSocket 推送。
*   **Remote**: Manager 通过 HTTP GET `/api/task/{id}/logs` 拉取远程日志片段 -> 转发给前端。
    *   *优化点*: 目前主要支持“拉取历史”，实时流式转发 (Proxy Streaming) 仍在完善中。

## 5. 开发注意事项 (Development Notes)

1.  **网络延迟与超时**: `RemoteDevice` 的 HTTP 请求必须设置合理的 `timeout`，防止单节点故障拖垮整个 Manager 的响应速度。
2.  **Token 安全**: API Token 是集群互信的唯一凭证，严禁在日志中打印 Token 明文。
3.  **CORS**: 虽然设计上支持前端直连 Agent，但为了简化鉴权和网络配置，推荐所有流量经过 Manager **后端代理 (Proxy)**。
4.  **ID 唯一性**: 确保 `config.json` 中的 `machine_id` 在集群内唯一，避免“幽灵设备”问题。

## 6. 关键文件索引 (Key Files)

*   `backend/core/device.py`: **核心**。Local/Remote 设备实现。
*   `backend/api/task_manager.py`: 任务管理与分发逻辑。
*   `backend/api/agent.py`: Agent 端被调用的 API。
*   `frontend/src/store/taskStore.ts`: 前端状态管理，处理 WebSocket 数据。
*   `frontend/src/views/TaskManager.vue`: 集群管理主界面。
