# 技术设计文档：CodeYun 任务管理器与进程发现

## 1. 背景与目标 (Context & Goals)

### 1.1 背景
CodeYun 旨在打造一个个人超级工具集成平台，其中 **任务管理器 (Task Manager)** 是核心模块之一。用户需要管理本地及远程设备上的后台任务（如 Python 脚本、Syncthing 服务、Web 服务器等）。
早期的设计试图通过全局扫描进程名来自动“认领”任务，导致了 PID 复用冲突、多实例识别错误等问题。

### 1.2 目标
-   **稳健的任务控制**：确保任务状态的准确性，避免“幽灵任务”或状态误判。
-   **支持多实例**：允许同一命令启动多个独立实例。
-   **同名进程检测**：提供工具帮助用户发现和清理系统中已存在的同名进程，但不自动接管。
-   **架构解耦**：将“任务生命周期管理”与“系统进程发现”解耦。

---

## 2. 总体设计 (High-Level Design)

### 2.1 核心理念：PID Authoritative (PID 为王)
-   **任务身份**：一个任务实例（Task Instance）的唯一标识是其启动时获得的 **PID**。
-   **生命周期**：
    -   **Start**: Spawn Process -> 获得 PID -> 绑定 Task ID。
    -   **Monitor**: 仅检查该 PID 是否存活且未被操作系统复用。
    -   **Stop**: Kill PID -> 清除 Task ID 绑定的 PID。
-   **不自动关联**：CodeYun 不再通过扫描进程名来自动恢复“失联”的任务。如果 PID 丢失或进程重启，该任务即视为 Stopped。

### 2.2 模块正交性分析
本设计将系统划分为两个正交的维度：

1.  **任务控制平面 (Control Plane)**
    -   **职责**：负责 Spawn 新进程、维护 PID 映射、日志流式传输。
    -   **原则**：只对自己启动的子进程负责。不关心系统里其他无关进程。
    
2.  **系统诊断平面 (Diagnostic Plane)**
    -   **职责**：提供“同名进程检测”功能，扫描全系统进程表。
    -   **原则**：只读（Read-only）扫描 + 显式清理（Kill）。**绝不自动修改** 控制平面的状态（即不自动 Attach）。

---

## 3. 详细设计 (Detailed Design)

### 3.1 数据结构

#### 3.1.1 任务状态 (TaskStatus)
```python
class TaskStatus(BaseModel):
    id: str
    running: bool
    pid: Optional[int]       # 核心：当前绑定的系统进程ID
    started_at: Optional[float] # 辅助：用于校验PID复用
    # ... 资源监控字段 ...
```

### 3.2 接口定义 (API)

#### 3.2.1 任务管理 (Task Management)
-   `POST /task/{id}/start`
    -   **逻辑**：
        1.  检查当前 `task.pid` 是否存活。
        2.  (前端可选) 预检是否存在同名进程并警告。
        3.  执行 `subprocess.Popen`。
        4.  记录 PID 到内存/文件。
-   `POST /task/{id}/stop`
    -   **逻辑**：直接 Kill 记录的 PID。

#### 3.2.2 同名进程检测 (Process Discovery)
-   `GET /task/{id}/related_processes`
    -   **输入**：任务 ID（后端获取其 command）。
    -   **输出**：`List[ProcessInfo]`，按匹配度排序。
    -   **匹配算法**：
        -   **Level 3 (完全匹配)**：命令行参数完全一致。
        -   **Level 2 (部分匹配)**：命令行包含关键参数。
        -   **Level 1 (名称匹配)**：仅主程序名相同（如 `python.exe`）。
-   `POST /task/process/kill`
    -   **输入**：`pid`
    -   **逻辑**：调用 `psutil.kill()`。这是一个独立的工具接口，不更新任务状态。

### 3.3 关键流程：启动冲突检测
为了防止用户无意中启动多个单例服务（如 Syncthing），在前端实现软性拦截：

1.  用户点击“启动”。
2.  前端调用 `related_processes`。
3.  如果发现 **Level 3 (完全匹配)** 的进程：
    -   弹出警告：“检测到系统中已有完全匹配的进程 (PID: xxx)。继续启动将产生新实例。”
    -   用户可选择“取消”或“继续”。
4.  若继续，则调用 `start` 接口，产生新的 PID。

---

## 4. 设计优势 (Benefits)

1.  **确定性 (Determinism)**：
    -   消除了“猜测”逻辑。系统不再因为进程名相似而错误地将别人的进程认作自己的。
2.  **灵活性 (Flexibility)**：
    -   天然支持多实例。用户可以创建 10 个配置相同的任务，启动 10 次，获得 10 个 PID，互不干扰。
3.  **安全性 (Safety)**：
    -   将“清理僵尸进程”的操作权交给用户，而不是系统自动决策，避免了误杀重要系统进程的风险。
4.  **解耦 (Decoupling)**：
    -   控制逻辑与诊断逻辑分离。修改扫描算法不会影响任务的启动/停止稳定性。

## 5. 局限性与未来规划
-   **无自动接管 (No Auto-Attach)**：当前设计不支持将系统已有的进程“纳管”为 CodeYun 任务。用户必须杀掉旧进程，由 CodeYun 重新启动。
    -   *未来扩展*：可增加 `Attach Process` 功能，手动将某个 PID 填入任务状态。
-   **重启后状态丢失**：如果 CodeYun 后端重启，且没有持久化 PID 文件，任务状态会变回 Stopped（即使进程还在跑）。
    -   *已实现*：当前代码已支持重启后读取 `tasks.json` 恢复 PID 监控。
