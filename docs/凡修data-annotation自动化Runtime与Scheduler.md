# 凡修 data-annotation 自动化 Runtime 与 Scheduler

> Kernel / Cell 的唯一架构定义见 [凡修行为树运行框架约定](./凡修行为树运行框架约定.md)。本文只描述业务 Runtime 与外部 Scheduler 数据。

## Runtime

Runtime 是加载在凡修 Jupyter Kernel 中的业务框架，不是第二个执行器。它提供：

- 当前帧、OCR、shape、scene 与资产树访问；
- 点击、长按、拖拽、输入和等待；
- task、guard、generator 的推进；
- 业务日志、本次业务结果和可供 Scheduler 消费的发现事实。

Runtime 只返回本次业务结果并可发布业务发现事实；`last_result / retry_after / attempt` 的权威终态由 Kernel 外 Scheduler 写回，Runtime 不直接推进 Scheduler 状态机。

正式任务和调试代码都通过普通 Cell 进入同一 Kernel。`/data-annotation/runtime/cells/task` 只把已注册任务构造成 Cell；`/data-annotation/runtime/cells/code` 直接提交 Python Cell。

## Scheduler

Scheduler 位于 Kernel 外，持久化：

- 作业类型、实例 id、显示名；
- `enabled`、schedule kind、规则；
- `next_time`、`retry_after`；
- `last_run_at`、`last_result`、最终消息；
- 当前执行尝试 id、Kernel generation、开始/结束时间；
- 业务所需 payload。

到期判断只读取这些事实。到期时 Scheduler 选择一个任务，构造 `run_task(...)` Cell，提交当前 Kernel并等待最终结果。下一次调度重新读取最新事实，不保存“上次做到第几步”的业务进度。

`scheduler_meta` 只允许保存世界事实同步时间、阻断原因和人工调度备注等调度元数据；不得保存 scene、step、generator 或业务游标。

工程、AI 和人工来源的优先级及模拟器独占仲裁属于 Scheduler / Dispatch Arbiter。Kernel 不保存来源锁，也不消费 Scheduler 队列。当前 Jupyter shell 自身按协议串行执行 Cell；来源仲裁不得被实现为 Kernel 内 JSON queue。

## 作业原子性

工程 Scheduler 的业务作业按原子事务理解：

1. 从任务声明的稳定锚点开始，通常是 `#34 世界`。
2. 从头执行完整业务闭环。
3. 收尾回稳定锚点。
4. 只写最终结果、下次触发或重试时间。

同一个 Cell 内的 generator 可以跨 tick 保留内存进度；Kernel restart、后端重载或下一次 Scheduler 触发不得恢复旧业务中间步骤。

故障处理固定为：旧执行尝试作废 -> Scheduler 写失败和 `retry_after` -> 新 Cell 回稳定起点 -> 整单重跑。禁止把 Kernel 级故障降格为业务步骤级续跑。

AI/人工逐步调试可以提交多段 Cell，因为上下文由当前会话持有；调试分段不得沉淀为工程作业的自动恢复状态机。

## Guard

Guard 是 Runtime 框架策略，不是常驻 Kernel 队列里的高优先级任务。它可以在任务运行前或框架每轮推进前检查已标注遮挡、弹窗和设备健康；处理一次后必须把控制权交还主任务。

Guard 配置可以持久化，但其执行只能发生在一个正在执行的 Cell 内。Kernel 空闲时不得由 resident runner 自行轮询、OCR 或点击游戏。

## 场景与 shape

- 业务任务只声明 scene 和 shape，不处理物理坐标。
- 资产树读取统一走 data-annotation storage。
- 场景身份用 `isSceneIdentity / sceneIdentityRole`。
- 点击和长按使用已命名 shape。
- 跳转使用 `sceneJumpTarget` 和通用场景规划；该字段记录真实落点及频次，是规划先验而不是白名单。
- 可靠识别到新的实际落点时，将它追加到 `sceneJumpTarget` 并从该场景重新规划；不因“此前未声明”而中断，也不另建 `observedLanding` 双轨字段。
- 缺标、错标或 unknown 超出有界恢复范围时停止并交给人工，不猜坐标。

设备与资产事实见 [凡修 data-annotation 运行设备约定](./凡修data-annotation运行设备约定.md)。

## 状态维度

页面需要分别展示：

- Kernel：`idle / busy / dead`、generation、manager pid、kernel pid。
- Runtime：当前 task、phase、业务结果、日志。
- Scheduler：enabled、next_time、retry_after、last_result、due plan。

三者不能互相伪造。例如 Kernel idle 不等于业务成功，Scheduler 到期也不等于 Cell 已提交。

## 公开动作

- 提交代码 Cell：`POST /data-annotation/runtime/cells/code`
- 提交正式任务 Cell：`POST /data-annotation/runtime/cells/task`
- 中断当前 Cell：Kernel interrupt
- 重启 Kernel：Kernel restart
- 关闭 Kernel：Kernel shutdown
- 读取或修改 Scheduler tasks/settings
- 执行一个指定 Scheduler task 或一个到期 task

旧 task queue、queue/cancel/clear-queue、resident wake、Kernel isolation API 不属于当前模型。

## 验收

- Scheduler task 最终走普通 Cell execute，不调用 runner 的第二启动入口。
- Kernel 空闲时没有 resident task queue polling。
- Scheduler 的 next_time/retry/result 仍能正常更新。
- 真实游戏作业验收必须同时检查 Runtime 最终状态、日志和真实 ADB 画面；Kernel 架构验收只使用无害 Cell。
