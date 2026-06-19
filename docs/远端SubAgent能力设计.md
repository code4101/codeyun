# 远端 Sub-Agent 能力设计

## 1. 背景

CodeYun 已经具备远端设备注册、设备 token 鉴权、`/api/device-control/python-runs` 受信 Python 执行、远端文件和任务代理等能力。
在排查跨机器问题时，当前可以临时通过 `python-runs` 在远端设备上执行 `codex exec`，让远端 Codex CLI 读取该机器本地仓库、环境和登录态后返回结论。

这个临时链路可用，但不适合作为正式能力：

- AI 不容易知道每台设备是否有 Codex CLI。
- 调用方式依赖手写脚本，缺少统一参数、审计和错误结构。
- `python-runs` 是通用远端 Python 执行入口，不应该承担“远端 AI 助手”这个更高层语义。
- 不同设备的 Codex CLI、登录态、可访问工作目录、沙盒策略都可能不同，不能默认存在。

目标是把这件事正式抽象成 CodeYun 的“远端 Sub-Agent 能力”。

## 2. 设计原则

1. **能力探测先于调用**
   调用方必须先询问设备有哪些 sub-agent runtime，不能假设每台机器都有 Codex CLI。

2. **本机能力与跨设备代理解耦**
   设备本机只负责检测和运行自己的 runtime；跨设备代理只负责鉴权、转发和隐藏 token。

3. **运行时抽象，不绑定 mf 或 mi15**
   `codex_cli` 只是第一种 runtime，未来可以扩展到其他 CLI agent 或本地模型 agent。

4. **默认只读**
   调查类任务默认 `read-only` sandbox。写操作必须由配置和请求同时显式允许。

5. **可审计**
   每次 sub-agent run 都要有 `run_id`、设备、runtime、cwd、sandbox、prompt 摘要、stdout、stderr、last_message、开始结束时间。

## 3. 总体架构

```text
调用方 AI / 前端
  |
  | 设备代理接口
  v
/api/device-entries/{entry_id}/subagents/*
  |
  | 使用 UserDevice.url + token 转发，不暴露 token
  v
远端设备 /api/device-control/subagents/*
  |
  | 调用本机 runtime
  v
Codex CLI / 其他 Agent Runtime
```

## 4. 核心概念

### 4.1 AgentRuntime

```json
{
  "runtime_id": "codex-cli-default",
  "kind": "codex_cli",
  "label": "Codex CLI",
  "available": true,
  "enabled": true,
  "version": "codex-cli 0.128.0",
  "login_status": "logged_in",
  "supports": {
    "exec": true,
    "resume": true,
    "images": true,
    "json_events": true,
    "output_last_message": true
  },
  "default_sandbox": "read-only"
}
```

- `available`: 当前机器能检测到并执行 runtime。
- `enabled`: CodeYun 配置允许远端调用该 runtime。
- `login_status`: 例如 `logged_in`、`not_logged_in`、`unknown`。
- `supports`: 用于让调用方判断能否传图片、能否恢复会话等。

### 4.2 SubAgentRun

```json
{
  "run_id": "uuid-or-hex",
  "runtime_id": "codex-cli-default",
  "status": "running",
  "cwd": "D:\\home\\chenkunze\\slns\\pyxllib",
  "sandbox": "read-only",
  "created_at": 1778980000.0,
  "finished_at": null,
  "last_message": "",
  "stdout_tail": "",
  "stderr_tail": ""
}
```

## 5. API 草案

### 5.1 设备本机接口

```text
GET  /api/device-control/subagents/capabilities
POST /api/device-control/subagents/runs
GET  /api/device-control/subagents/runs/{run_id}
POST /api/device-control/subagents/doctor
```

### 5.2 跨设备代理接口

```text
GET  /api/device-entries/{entry_id}/subagents/capabilities
POST /api/device-entries/{entry_id}/subagents/runs
GET  /api/device-entries/{entry_id}/subagents/runs/{run_id}
POST /api/device-entries/{entry_id}/subagents/doctor
```

跨设备代理层只做三件事：

- 校验当前用户拥有该 `entry_id`。
- 使用 `UserDevice.url` 和 `UserDevice.token` 调用远端设备。
- 把远端响应原样结构化返回，不向前端或 AI 泄露 token。

### 5.3 运行请求

```json
{
  "runtime_id": "codex-cli-default",
  "prompt": "只读调查这个仓库最近一周 src/kq5034/db.py 的变更，报告结论和证据。",
  "cwd": "D:\\home\\chenkunze\\slns\\pyxllib",
  "sandbox": "read-only",
  "approval_policy": "never",
  "model": "",
  "async": true,
  "timeout": 1800,
  "ephemeral": true
}
```

Codex CLI 第一版推荐命令形态：

```powershell
codex exec `
  --sandbox read-only `
  --ask-for-approval never `
  --ephemeral `
  --skip-git-repo-check `
  --color never `
  --json `
  --cd <cwd> `
  --output-last-message <file> `
  -
```

不要默认使用 `--dangerously-bypass-approvals-and-sandbox`。只有本机显式配置允许，并且请求也显式要求写权限时，才允许放宽沙盒。

## 6. 配置草案

第一版可以放在现有 settings/config 体系中，不急着建表：

```json
{
  "subagents": {
    "enabled": true,
    "runtimes": [
      {
        "runtime_id": "codex-cli-default",
        "kind": "codex_cli",
        "command": "codex",
        "enabled": true,
        "allowed_workspace_roots": [
          "D:\\home\\chenkunze\\slns",
          "C:\\home\\chenkunze\\slns"
        ],
        "default_sandbox": "read-only",
        "max_timeout_seconds": 3600
      }
    ]
  }
}
```

校验规则：

- `cwd` 必须在 `allowed_workspace_roots` 内。
- 请求的 `timeout` 不能超过 `max_timeout_seconds`。
- 写权限 sandbox 必须被 runtime 配置允许。
- 不允许在响应里返回 token、完整环境变量、认证文件路径内容。

## 7. 建议落地文件

当前仓库尚未落地 SubAgent 专用后端模块。本节记录的是拟新增结构，不表示这些路径已经存在。

- 拟新增核心包：`backend.core.subagents`
  - `__init__.py`
  - `models.py`
  - `service.py`
  - `codex_cli.py`
- 拟新增本机接口模块：`device_control_subagents.py`

本机接口挂载到 `backend/standard/cluster/control/module.py` 的 `/api/device-control` 下。

跨设备代理接口可以放在 `backend/api/device_entries.py`，复用现有 `UserDevice` 所有权校验、远端 URL、远端 token 代理模式。

## 8. AI 调用规则

后续 AI 使用 CodeYun 远端 sub-agent 时，应遵守：

1. 不要假设设备有 sub-agent，先调用 `capabilities`。
2. 只有 `available=true` 且 `enabled=true` 才能发起 run。
3. 默认使用 `read-only` sandbox。
4. 调查类任务优先 `async=true`，随后轮询 `run_id`。
5. 不要通过 `python-runs` 手工拼 `codex exec`，除非 sub-agent 接口尚未实现。
6. 不要输出、请求或记录设备 token。
7. 对返款、支付、删除、批量修改等高风险任务，即使远端 sub-agent 可用，也只能先做只读调查，执行动作必须由用户明确确认。

## 9. 第一期实施范围

第一期只支持 `codex_cli`：

1. `capabilities` 检测 `codex --version` 和 `codex login status`。
2. `runs` 启动 `codex exec`，保存 `stdout`、`stderr`、`last_message`。
3. 支持同步短任务和异步长任务。
4. 增加跨设备代理接口。
5. 增加单元测试覆盖命令构造、路径限制、未登录、不可用 runtime、远端错误透传。

mi15 和 mf 当前都可以检测到 Codex CLI，但这只是当前设备状态，不能写死在功能里。
