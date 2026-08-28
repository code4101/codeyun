# 凡修 Job 运行与调度语义

> 本文是 `job_status`、`run_status`、`next_time` 与 attempt 的唯一语义定义。Kernel 和运行权见 [凡修行为树运行框架约定](./凡修行为树运行框架约定.md)。

## 最小模型

```text
next_time 到期
  -> Scheduler 提交一个 Job Cell
  -> Job 在 Cell 内产生 job_status
  -> Job 在业务完成点原子写 next_time
  -> Cell 形成 run_status
  -> Scheduler 机械收尾
```

| 概念 | 生命周期 | 所有者 |
| --- | --- | --- |
| `job_status` | 当前 Job Cell | Job 业务 |
| `next_time` | 跨 Cell 持久化 | Job；仅运行错误时 Scheduler 有受限写权 |
| `run_status` | 当前 attempt | Cell 执行框架 |
| `attempt_id` | 当前 Job Cell | Scheduler，用于领取与 CAS 收尾 |

能被 Job 正常观察、分类并决定后续策略的结果都是 `job_status`，包括资源不足、未解锁、未出现、领取失败、窗口错过和稍后复查。Job 写入相应 `next_time` 后正常返回；这些结果不是 `run_status=error`。

`run_status=error` 只表示程序、基础设施或契约异常使 Cell 无法正常结束。

## `next_time` 是唯一触发事实

- `next_time <= now`：到期。
- `next_time > now`：等待。
- `next_time=None`：休眠。
- Job 正常分支负责写自己的下次绝对时间或 `None`。
- Job 专属事实探针和人工改期通过统一原子入口直接覆盖目标 Job 的 `next_time`。
- Scheduler 不理解周期、窗口、CD、OCR、业务消息或 `job_status`。

禁止新增 `retry_after`、trigger request、repeat trigger、活动门禁、隐藏 payload、内存触发队列或其它平行触发状态。

### `next_time` 不是返回协议

正式 Job 的 handler、generator、admission 和 Cell terminal 不得返回 `next_time`。每个正常结束分支必须在业务完成点调用统一 setter，并确认落盘后才能返回。

纯时间函数可以返回候选值；正式 Job 必须消费候选并自行持久化。setter 失败必须自然形成 `run_status=error`，不能交给 Scheduler 兜底。

## `run_status` 收尾矩阵

| `run_status` | Scheduler 对 `next_time` 的动作 |
| --- | --- |
| `running` | 不修改 |
| `success` | 不修改，接受 Job 已写值 |
| `error` | 按 Job 预设错误重试规则写入 |
| `interrupted` | 恢复本 attempt 的 `original_next_time` |

### `success`

只表示 Cell 正常结束，不等于游戏目标成功。Job 的每个正常分支都应显式形成调度意图；Scheduler 不解析 terminal payload，也不补默认周期。

### `error`

- 0 级 Job 默认延迟 10 分钟。
- 1 级及以上默认延迟 0 分钟。
- Job 可声明覆盖延迟。
- 有日窗口的 Job 可把越过结束边界的技术重试裁剪到下个有效窗口。

延迟只来自静态配置，不从日志、报错文案或场景推断。

### `interrupted`

中断与错误严格分离：

- 当前 Cell 所有者捕获 `KeyboardInterrupt`，写出匹配 `attempt_id` 的 `interrupted` 终态，再继续传播。
- KernelManager 只在目标 `execute_request.msg_id` 自己结束后确认 interrupt 成功。
- Scheduler 用 CAS 结束同一 attempt，并恢复 `original_next_time`。
- 不应用错误延迟，不接受中断过程中写出的临时调度值。
- 不保存 `job_status`、generator 栈、业务步骤或页面意图。
- 旧 Cell 未确认终止前，不得提交抢占者或把 GUI 交给用户。

## attempt 与持久化

Job Cell 启动时，Scheduler 原子写入：

```text
last_result=running
attempt_id=<new>
attempt_original_trigger=<current next_time>
attempt_kernel_generation=<current generation>
```

终态写回必须携带预期 `attempt_id`。若磁盘 attempt 已变化，迟到调用方失去所有权并忽略自己的结果，不能覆盖新 attempt。

共享清单采用文件锁内增量合并：省略 Job 不等于删除，只有显式 `removed_task_ids` 才删除；Kernel 内 Job 更新只能拥有目标 Job 的运行字段。

## Job 不是持久状态机

Cell 结束后，`job_status`、generator、局部变量、步骤和 scene cursor 全部失效。新 attempt：

1. 把当前 scene 视为普通导航输入。
2. 先消费当前页面能够直接证明的本 Job 业务终态。
3. 否则通过正式通用导航回到该 Job 的稳定入口，凡修通常是 `#34`。
4. 重新读取事实并整单运行。

禁止从旧确认页、进度页、结果页推断上次按钮意图后续点。同一 attempt 内消费一个动作的合法多落点属于局部事务，不是跨 attempt 恢复。

## 手动运行

两种入口都提交同一个 Job Cell：

- **提前运行**：默认模式。尚未到期时，以 `next_time + 1 分钟` 作为本 Cell 的 `effective_now`。
- **立即运行**：用户明确说“现在、立即、按当前时间”时，以真实墙钟运行。

标准 API：`POST /api/fanxiu/data-annotation/scheduler/task/run-now`，`business_time_mode=planned | current`。

`effective_now` 只影响 Cell 内 `job_now()` / `job_today()` 等业务时间；Scheduler 到期比较、attempt 时间、抢占和错误重试始终使用真实墙钟。它不持久化，也不构成第二触发协议。

## 作业定义与清单

- 作业类型随代码注册；默认实例由 `standard_job=True` 和默认实例构造器产生。
- 用户说“加作业”默认是增加可选作业类型，不自动给当前机器创建或启用实例；本地清单由用户按需添加。
- `scheduler_supported` 只表示允许 Scheduler 提交，不携带场景生命周期。
- 同一业务的星期、窗口或周期差异由一个 Job 动态计算 `next_time`，不拆成多个调度实例。
- 纯手动作业静止时写 `next_time=None`，人工运行正常结束后仍应显式写回 `None`。
- Job 启动后读取所属业务域的最新事实，禁止用 `scheduler_meta` 或隐藏 payload 运输巡检业务上下文。

## 不可逆副作用

最后一项不可重复副作用确认完成时，Job 应立即写入周期 `next_time`，再做 best-effort 离场：

- 业务项可分类失败：按业务规则写时间并正常返回。
- 业务完成、仅离场失败：保留已写时间，以 success warning 结束。
- `KeyboardInterrupt`、`GeneratorExit` 等控制流：继续传播，不能降级为离场 warning。

## 验收

1. 正常分支是否在返回前恰好写入一次 `next_time` 或 `None`？
2. terminal payload 是否完全不运输 `next_time`？
3. `success` 是否不改时间，`error` 是否只应用预设延迟？
4. `interrupted` 是否精确终止目标 Cell并恢复 `original_next_time`？
5. 终态写回是否受 `attempt_id` CAS 保护？
6. 新 attempt 是否从稳定入口整单运行，不恢复旧步骤？
7. Scheduler 是否仍只消费时间、优先级、attempt 和 Kernel 资源？
