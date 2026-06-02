# 游戏窗口3自动化 Runtime 与 Scheduler

本文档记录新版 `游戏窗口3` 正式自动化框架的职责边界，避免继续把任务状态机写回前端步进器。

## 核心原则

- 帧树是感知与定位的第一数据源。
- 已有场景、父帧/子帧、shape 标注可用时，Runtime 禁止猜坐标。
- 前端只负责控制、调试、展示和标注，不承载正式任务状态机。
- Runtime 负责实际执行、当前场景识别、守护 tick、点击、输入、等待。
- Scheduler 负责任务清单、到期判断、优先级、可中断策略和手动触发。

## 帧树语义

父帧和子帧表示同一场景的不同区域、状态或派生形态。

如果一个流程可达某个父帧，那么该父帧下的子帧在逻辑上也视为可达候选；任务实现不应要求每条跳转路径都重复写到每个子帧。

Runtime 识别场景时应优先使用帧树内已有帧和 shape。只有缺少标注时，任务才应报错或进入人工补标流程，不应在正式任务里临时猜测按钮位置。

## Runtime

Runtime 是后端单实例执行器，同一时间只运行一个正式 task。

第一版状态写入：

```text
CODEYUN_DATA_DIR/fanxiu/game-window3/runtime/
```

Runtime 状态包含：

- `idle/running/stopping/stopped/success/error`
- 当前任务类型、任务 id、phase
- 当前场景 id
- 当前任务优先级和是否可中断
- 日志、错误、开始/更新时间
- 守护开关状态

守护是 Runtime 的独立开关，由前端任务调试台显式启停。没有任务时也可以开启空转检测；关闭后应停止守护线程以节省 CPU。

守护 tick 的职责边界：

- 优先处理独立弹窗、遮挡帧和已标注的全局干扰项。
- 只使用帧树里的场景标识和动作 shape。
- 对 `sceneJumpTarget=-1` 的 shape，可视为独立弹窗关闭动作。
- 对无自动动作的遮挡帧，只记录发现，不猜测点击。
- 对“万灵切磋邀请”这类特殊守护任务，必须先在帧树里补足识别 shape 和动作 shape，Runtime 才能稳定处理。

## Scheduler

Scheduler 不直接点击游戏，只负责决定要把什么 task 发送给 Runtime。

Scheduler 任务字段：

- `enabled`
- `priority`
- `interruptible`
- `next_time`
- `last_result`
- `retry_after`
- `checkpoint`
- `payload`

第一版不实现暂停恢复。任务被中断后，本次状态丢弃，后续重新从头执行。`checkpoint` 字段仅预留。

`GET /api/fanxiu/game-window3/scheduler/plan` 提供后端只读规划视图，合并任务清单、到期状态、Runtime 状态和 `WorldFacts` 摘要。前端任务调试台只展示该结果，不在前端重新实现调度判断。

Scheduler 读取任务清单时会同步 `WorldFacts.discoveries.task` 里的时间事实：

- 动态任务可从 `discovered_next_time` 或 `next_time` 回写 `next_time`。
- 任意任务可从 `discovered_retry_after` 或 `retry_after` 回写 `retry_after`。
- 回写只更新 Scheduler 状态，不触发点击或任务执行。

默认任务定义已经接入旧版凡修行为树的动态任务和每日世界任务清单：

- `source=legacy_behavior_tree` 表示来自旧版行为树任务目录。
- `schedule_kind=dynamic` 表示旧版动态时间任务，真实下次时间后续由 Runtime 读取游戏状态后回写。
- `schedule_kind=daily` 表示每日定时任务，执行后 Scheduler 会推进到下一次 `schedule_times`。
- 尚未迁移的旧任务使用 `legacy_daily_task` / `legacy_dynamic_task` 占位，但不允许作为 Runtime 任务启动。
- 未验收任务不应在 Runtime 中保留可调用执行函数；需要迁移时先补齐帧树/shape 验证，再新增对应执行函数和测试。
- 当前默认清单只把旧版任务导入为占位，不默认启用具体日常任务。具体迁移必须等对应帧树和 shape 数据确认充分后再逐项打开。
- `run-due` 只启动已支持的到期任务；旧任务占位即使被手动启用，也不会作为可执行任务启动。
- `run-now` 对未纳入框架验收的任务直接返回 400，避免误触发未确认链路。

这个设计的目的不是继续固定死行为树，而是保留旧版成熟任务目录，同时允许人工随时通过 Scheduler 发送单个任务，例如临时收到礼包码后手动执行 `gift_code_redeem`。

抢占规则：

- Runtime 空闲时，任务直接启动。
- Runtime 正在运行且当前任务可中断，来者 priority 更高时，Scheduler 请求停止当前任务并短暂等待，然后启动新任务。
- Runtime 正在运行且当前任务不可中断，或来者优先级不够高时，新任务标记为 `queued`。

## 典型手动任务

礼包码兑换属于典型手动任务：它通常没有固定到期时间，而是用户临时提供礼包码，由 Scheduler 手动发送 task 给 Runtime。

这类任务允许在 `scheduler/task/run-now` 请求里带一次性 `payload` 覆盖，例如 `{"codes":["..."]}`。这样不会把临时礼包码写入长期 Scheduler 任务定义。

`run-now` 的 payload 覆盖只对本次 Runtime task 生效；Scheduler 长期任务定义仍保留原 payload，避免临时礼包码污染配置文件。
前端任务调试台对 `gift_code_redeem` 提供临时礼包码输入；这些礼包码只进入本次 `run-now` 请求，不写入 Scheduler 任务清单。

正式流程：

```text
对齐 #49
进入 #78
for code in codes:
  清空输入
  输入 code
  点击兑换
  等待结果:
    #82 -> 当前码已领，继续下一个
    #81 -> 过渡，继续等
    #49 -> 成功回到设置页，继续下一个
    #78 -> 停留/失败/重复提示，按规则处理
全部完成后回 #49
点击 #49[回退]
```

这里的 #49、#78、#81、#82 和按钮位置都来自帧树标注。

## 前端

前端页面中的正式入口应称为“任务调试台”。

允许保留的前端能力：

- 帧树编辑
- shape 标注
- 图片对比
- 抠图/检测
- 单步 Runtime tick
- 启停 Runtime task
- 启停守护
- 查看 Runtime/Scheduler 写入的 `WorldFacts`
- 查看 Scheduler 后端生成的只读 `plan`
- Scheduler 任务清单与手动触发

不应继续作为正式执行框架的能力：

- 前端长流程任务状态机
- 前端循环等待并决策下一步
- 前端持久化 stepper task storage 作为正式任务源

旧接口 `/api/fanxiu/game-window3/stepper/logs` 和 `/api/fanxiu/game-window3/gift-code-task/*` 已移除。新代码统一使用 `/runtime/*` 和 `/scheduler/*`。

## 当前落地状态

已完成：

- 后端 Runtime 单实例执行器。
- 后端 Scheduler 任务清单、到期过滤、手动任务、执行到期任务。
- Runtime 守护开关，前端可显式启停。
- `WorldFacts` 稳定分层为 `runtime`、`guard`、`discoveries`、`events`，Runtime 和 Scheduler 都只写自己的事实，不共同覆盖核心状态。
- `GET /api/fanxiu/game-window3/runtime/world-facts` 只读暴露事实文件，任务调试台可直接查看。
- `GET /api/fanxiu/game-window3/scheduler/plan` 只读暴露 Scheduler 规划结果。
- Scheduler 可从 `WorldFacts.discoveries.task` 回写动态任务 `next_time` 和任务 `retry_after`。
- 前端“任务调试台”替代正式步进器入口。
- 当前框架验收入口只包含 `gift_code_redeem`、`go_scene` 到 #49、`hide_floating_window`。
- 旧版动态任务和每日任务目录接入 Scheduler。
- 旧版任务当前使用 `legacy_daily_task` / `legacy_dynamic_task` 占位，不进入 Runtime 执行入口。
- 未验收任务即使被外部状态写成 `enabled=true`，读取任务清单时也会强制改回 `enabled=false` 并标记 `last_result=unsupported`。
- 未验收的日常/报名/游历执行函数已从 Runtime 中移除，避免误认为已迁移。
- 前端正式 stepper 状态机和旧 stepper 日志接口已移除。

尚未完成：

- 多数旧版每日任务的具体行为没有迁移，只是出现在 Scheduler 目录里。
- 旧版动态任务还没有从游戏状态识别真实 `next_time`；但 Scheduler 已支持从 `WorldFacts` 回写时间事实。
- `WorldFacts` 已有基础事实模型，但 Scheduler 还没有根据它自动规划新任务。
- 真实设备的全量“执行全部到期任务”还没有覆盖完整旧版行为树效果。

已加测试：

- 默认 Scheduler 任务目录包含旧版行为树每日/动态任务。
- 旧状态文件会被默认结构修正，不会保留错误的 task_type/source/schedule_kind。
- 每日任务执行后会推进到下一次 `schedule_times`。
- `enabled`、`next_time`、`retry_after` 的到期判断。
- 高优先级可抢占任务会取消当前任务并请求停止。
- 不可抢占任务会标记为 queued。
- Runtime task dispatch 使用后端正式任务入口。
- Scheduler 任务成功/错误后的 `next_time` / `retry_after` 更新。
- Runtime/Guard 事实写入不会覆盖历史发现和事件。
- Scheduler 任务状态变化会写入 `discoveries.task` 和事件流。
- Scheduler 会从 `WorldFacts.discoveries.task` 同步动态 `next_time` 和 `retry_after`。
- Runtime 会递归读取父帧/子帧，并把子帧中的独立弹窗/遮挡标记纳入守护候选。
- 未验收 legacy 任务不能被状态文件或 PUT 请求误启用。
