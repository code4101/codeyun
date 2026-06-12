# 数据标注自动化 Runtime 与 Scheduler

本文档记录新版 `数据标注` 正式自动化框架的职责边界，避免继续把任务状态机写回前端步进器。

## 核心原则

- 帧树是感知与定位的第一数据源。
- 已有场景、父帧/子帧、shape 标注可用时，Runtime 禁止猜坐标。
- 处理行为树任务前必须先读取当前 `entry_id` 的资产树标注，而不是从截图重新目测入口。当前机器的标注数据位于 `CODEYUN_DATA_DIR/fanxiu/data-annotation/asset-trees/<entry_id>.json`，后端统一通过 `_data_annotation_asset_tree_path(entry_id)` 和 `_index_images()` 读取。
- 前端只负责控制、调试、展示和标注，不承载正式任务状态机。
- Runtime 负责实际执行、当前场景识别、守护 tick、点击、输入、等待。
- Scheduler 负责任务清单、到期判断、按分组和触发时间排队，以及手动触发。

## 帧树语义

父帧和子帧表示同一场景的不同区域、状态或派生形态。

如果一个流程可达某个父帧，那么该父帧下的子帧在逻辑上也视为可达候选；任务实现不应要求每条跳转路径都重复写到每个子帧。

Runtime 识别场景时应优先使用帧树内已有帧和 shape。只有缺少标注时，任务才应报错或进入人工补标流程，不应在正式任务里临时猜测按钮位置。

同一场景可以有多个 `isSceneIdentity` 锚点，Runtime 场景分数取最高分。检测/调试页可以把每个条件都展示出来，用来评估鲁棒性；正式场景判定要兼容行为树“多个条件满足一个即可”的语义，不能把一个当前不可见的备选锚点平均进去拖垮识别。

行为树实现必须复用帧树的三类信息：

- `isSceneIdentity / sceneIdentityRole`：判断当前是否真的在某个场景。
- `shape.x/y/w/h`、`floating`、`imageMatchRole / ocrMatchRole`：决定点击和匹配 payload。
- `sceneJumpTarget`：决定场景移动路径和点击后的预期落点。

如果真实画面和旧标注不一致，正确流程是中断任务并报告缺少/过期的场景、shape、`sceneJumpTarget` 或匹配规则，由人工补标/修标后重新运行 Runtime；不能在业务函数中写“看起来像”的临时坐标或 OCR 文字偏移。

### 人工标注边界

- Fanxiu Runtime 只消费已有资产树标注，不承担自主标注、视觉探索或自动补图职责。
- Runtime 和 agent 不得自动保存未知截图到资产树，不得创建“未知场景”占位 shape，不得根据当前画面猜测场景编号、shape 区域、按钮坐标或未声明跳转落点。
- 用户提交给 Runtime/Scheduler 的凡修任务默认视为标注数据已经准备好。若发现标注缺失、标注过期、场景无法识别、点击后进入未声明落点或目标控件匹配失败，应立即中断并把缺失项交给人工处理。
- `sceneJumpTarget` 是人工维护的跳转契约。Runtime 可以验证是否到达已声明目标，并在命中后累加该目标次数作为确定性运行统计；但不要把未声明的实际结果自动新增到资产树，这类情况必须作为缺标注/错标注上报。

### 滚动签名与遮挡区域

- 列表滚动后不要立即对当前帧计算签名；先固定等待 1 秒，让拖拽惯性和复位动画结束。这里不再做“等待到稳定”的递归判断，避免稳定判断本身依赖哈希而形成循环。
- 滚动到底的主要判据是：一次滚动并等待后，可见列表签名与上一轮滚动后的签名相同。连续空滚动次数只能作为异常兜底，不应作为正常完成逻辑。
- 帧树里的「遮挡标记」分组表示固定排除区域，不表示固定遮挡内容。这些区域经常出现不同通知文字、公告条或临时浮层，因此其中 OCR/图像内容不能进入列表哈希或滚动签名。
- 计算列表签名时，应把「遮挡标记」分组下所有 shape 转换为当前帧坐标屏蔽框；OCR 行中心落在屏蔽框内时直接跳过。

### 闭环幂等与浮动模板

滚动清单里的任务项应优先建模为“父条目 + 子控件”的模板，而不是为每个滚动位置保存一张专用帧。具体经验见 [凡修 data-annotation 闭环与浮动模板案例](./凡修data-annotation闭环与浮动模板案例.md)。

- 场景身份只使用稳定页头、外框或固定控件；滚动窗口内容不能作为场景锚点。
- 运行时先确认当前场景，再在滚动容器中用 OCR 找父标题，最后用模板里的子控件相对偏移计算点击点。
- 点击后必须等待明确结果帧、无事项帧或原场景超时保底；只发出点击不能算闭环。

## Runtime

Runtime 是后端单实例执行器，同一时间只运行一个正式 task。

第一版状态写入：

```text
CODEYUN_DATA_DIR/fanxiu/data-annotation/runtime/
```

Runtime 状态包含：

- `idle/running/stopping/stopped/success/error`
- 当前任务类型、任务 id、phase
- 当前场景 id
- 当前任务状态和是否可中断
- 日志、错误、开始/更新时间
- 守护开关状态

行为树是 Runtime 的常驻后端服务。页面进入当前 `entry_id` 后会确保服务 loop 存在；服务没有用户层面的开关，只有空转、执行中、错误等运行状态。

守护不是独立线程。守护开关只是常驻行为树里的前置检查节点配置：开启后每轮空闲 tick 或业务任务 tick 都会先检查守护，关闭后该节点直接跳过。没有任务时，常驻服务可以按守护间隔做低频空转识别和弹窗处理；这仍属于同一个行为树 loop，不是另起一个守护服务。

守护 tick 的职责边界：

- 优先处理独立弹窗、遮挡帧和已标注的全局干扰项。
- 只使用帧树里的场景标识和动作 shape。
- `弹窗` 分组第一层图片里的 `空白` 表示背景关闭区，可直接作为关闭动作使用，不要求填写 `sceneJumpTarget`。同一弹窗有多个关闭候选时优先点 `空白`：显式关闭按钮可能太小，`确定` 可能触发跳转或领取等业务行为；背景取消通常更大、更稳、副作用更少。
- 对 `sceneJumpTarget=-1` 的 shape，可视为独立弹窗关闭动作。
- 对无自动动作的遮挡帧，只记录发现，不猜测点击。
- 对“万灵切磋邀请”这类特殊守护任务，必须先在帧树里补足识别 shape 和动作 shape，Runtime 才能稳定处理。

## Scheduler

Scheduler 不直接点击游戏，只负责维护作业配置、到期判断和只读计划。真正执行由常驻行为树在空闲 tick 中读取 Scheduler 结果后串行提交给 Runtime 业务节点。

Scheduler 任务字段：

- `enabled`
- `interruptible`
- `next_time`
- `last_result`
- `retry_after`
- `checkpoint`
- `payload`

第一版不实现暂停恢复。任务被中断后，本次状态丢弃，后续重新从头执行。`checkpoint` 字段仅预留。

`GET /api/fanxiu/data-annotation/scheduler/plan` 提供后端只读规划视图，合并任务清单、到期状态、Runtime 状态和 `WorldFacts` 摘要。前端任务调试台只展示该结果，不在前端重新实现调度判断。

当前作业调度按“分组 + 触发时间”理解：先按任务分组顺序排列，再在组内按 `retry_after / next_time / schedule_times` 升序处理。

Scheduler 读取任务清单时会同步 `WorldFacts.discoveries.task` 里的时间事实：

- 任意任务可从 `discovered_next_time` 或 `next_time` 回写 `next_time`；`日常_报名` 这类 daily 任务成功后也要能恢复次日 `05:00`。
- 任意任务可从 `discovered_retry_after` 或 `retry_after` 回写 `retry_after`。
- 任意任务可从 `last_run_at`、`last_result` 回写上次运行结果。
- 回写只更新 Scheduler 状态，不触发点击或任务执行。

`last_run_at`、`last_result`、`next_time`、`retry_after`、`checkpoint` 是后端运行结果字段。前端保存任务清单只能修改配置字段；后端 PUT 会与当前状态合并，不能用页面里的旧整表数据覆盖刚执行完的下一次触发。

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

调度规则：

- 常驻行为树每轮按 `守护 -> 手动作业 -> 自动作业` 的顺序检查。
- 手动作业来自用户调试/API 临时提交，例如 `/data-annotation/runtime/task/start` 或单步识别；它们进入 `manual_job` 队列，不代表启动行为树服务。
- 自动作业只拉取非 `schedule_kind=manual` 的到期任务，避免用户手动任务被空转 loop 反复执行。
- Runtime 空闲时，常驻服务可以启动下一个任务。
- Runtime 正在运行时，Scheduler 不抢占当前任务，新任务标记为 `queued` 等待后续空闲 tick。
- `/data-annotation/runtime/task/stop` 是历史兼容路径，语义只能是“停止当前业务任务”。它调用 `stop_current_task` 设置当前 task 的 stop event，不能停止 resident service；无任务时应返回空转状态和“当前没有正在运行的任务”。

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

### 开发调试手动作业

`debug_eval` 是通用临时代码执行底座，不是邮件、日常或某个具体业务的专用 API。

它用于让 agent/开发者在真实 Runtime 上下文中临时运行一段 Python：

- 默认 `mode=readonly`，允许截图、场景识别、OCR、读取标注和记录日志。
- 需要点击、拖拽等真实动作时必须显式传 `mode=act`。
- 代码里自动注入 `ctx`，常用能力包括 `ctx.frame()`、`ctx.scene()`、`ctx.ocr()`、`ctx.image()`、`ctx.shape()`、`ctx.tap_shape()`、`ctx.tap()`、`ctx.drag()`、`ctx.log()`。
- 支持两种写法：直接执行短代码，或定义 `def task(ctx): ...`，系统会自动调用；`task` 返回生成器时继续沿用现有 yield/tick 机制。
- 调用入口仍使用现有 Runtime 手动作业提交接口，例如 `task_type=debug_eval`、`payload.code=...`；不要新建第二套调度或后台线程。

只读探针示例：

```json
{
  "entry_id": "30b82d72-8a76-4a74-be4b-4fc1591c6ce2",
  "task_type": "debug_eval",
  "payload": {
    "mode": "readonly",
    "timeout_seconds": 60,
    "code": "info = ctx.scene(); ctx.log({'scene': info}); result = info"
  }
}
```

自定义函数示例：

```python
def task(ctx):
    frame = ctx.frame()
    info = ctx.scene(frame)
    lines = ctx.ocr(frame)
    ctx.log({"scene": info, "ocr": [line.get("text") for line in lines]})
    return {"scene": info, "line_count": len(lines)}
```

动作模式示例：

```json
{
  "task_type": "debug_eval",
  "payload": {
    "mode": "act",
    "code": "ctx.tap_shape(121, '返回')"
  }
}
```

动作模式只能在明确需要改变游戏状态时使用。邮件、领取、删除等高风险流程仍应优先用 `readonly` 先列出决策表，再提交 `act` 代码或沉淀为正式注册作业。

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

旧接口 `/api/fanxiu/data-annotation/stepper/logs` 和 `/api/fanxiu/data-annotation/gift-code-task/*` 已移除。新代码统一使用 `/runtime/*` 和 `/scheduler/*`。

## 当前落地状态

已完成：

- 后端 Runtime 单实例执行器。
- 后端 Scheduler 任务清单、到期过滤、手动任务、执行到期任务。
- Runtime 守护开关，前端可显式启停。
- `WorldFacts` 稳定分层为 `runtime`、`guard`、`discoveries`、`events`，Runtime 和 Scheduler 都只写自己的事实，不共同覆盖核心状态。
- `GET /api/fanxiu/data-annotation/runtime/world-facts` 只读暴露事实文件，任务调试台可直接查看。
- `GET /api/fanxiu/data-annotation/scheduler/plan` 只读暴露 Scheduler 规划结果。
- Scheduler 可从 `WorldFacts.discoveries.task` 回写动态任务 `next_time` 和任务 `retry_after`。
- 前端“任务调试台”替代正式步进器入口。
- 当前框架验收入口包含 `gift_code_redeem`、通用 `go_scene` 场景移动、`hide_floating_window`。
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

## 真实运行验收约定

- 凡修 Runtime/Scheduler 的验收必须走后端公开入口，让行为树在真实 MuMu 画面上实际运行；单元测试、私有函数直调、构造截图和静态场景识别只能作为辅助证据。
- 如果任务启动时已经满足目标，例如当前已经在目标场景，Runtime 可以短路成功，但这只验证“已满足目标”的识别路径，不验证真实点击链路。汇报时必须把短路命中和真实动作分开。
- 验证 `go_scene` 时，优先设计可回退的复现路径。例如从 `#34 世界` 跳到 `#66 日程`，再运行 `go_scene #34` 返回世界；前一段复现也是对行为树场景移动的测试。
- 运行报告至少记录：起点场景、目标场景、action 日志、跳转日志、最终 `success/error`、耗时和当前游戏可见状态。没有 action 日志或场景变化时，不得说“真实操作已验证”。
- 当前游戏可见状态必须由真实 MuMu/ADB 当前帧或用户可见画面确认；如果截图观察脚本抓到桌面、旧帧或非游戏内容，这张图不能算验收证据。
- Runtime 内嵌守护不能因弹窗误命中长期抢占主作业。守护 service 命中一次弹窗后本轮返回 `RUNNING` 暂停主作业，下一轮重新取帧；当守护跳过时，主作业必须继续获得 tick，否则 `go_scene` 会停在“守护处理”日志但游戏不移动。
- 场景身份识别不能用全屏 scan 兜底，且候选场景必须取最高分而不是顺序优先。像“日程”这种也会出现在世界页入口上的元素不能单独作为日程页身份锚点。
- 不要手动点游戏、直接改状态文件或只调用底层函数来冒充行为树验收；只有用户明确要求底层探针时，才把结论限定为底层探针结果。

已加测试：

- 默认 Scheduler 任务目录包含旧版行为树每日/动态任务。
- 旧状态文件会被默认结构修正，不会保留错误的 task_type/source/schedule_kind。
- 每日任务执行后会推进到下一次 `schedule_times`。
- `enabled`、`next_time`、`retry_after` 的到期判断。
- Runtime 忙碌时，Scheduler 任务会标记为 queued，等待后续空闲 tick。
- Runtime task dispatch 使用后端正式任务入口。
- Runtime 状态新增 `service_running`，表示常驻行为树 loop 是否存在；`running` 只表示当前是否有业务任务在执行。
- runtime 页面不再自动开启“关闭弹窗”守护来制造运行状态。进入页面只确保常驻服务存在，守护/作业开关完全由页面配置决定。
- CodeYun 后端启动或热重载后，如果当前机器启用了 `fanxiu-behavior-tree` 内置服务，会自动 ensure data-annotation resident loop，恢复持久化的 entry、守护配置、日志和空转调度能力。API 入口仍只负责投递任务、修改配置或唤醒 resident loop。
- Scheduler 任务成功/错误后的 `next_time` / `retry_after` 更新。
- Runtime/Guard 事实写入不会覆盖历史发现和事件。
- Scheduler 任务状态变化会写入 `discoveries.task` 和事件流。
- Scheduler 会从 `WorldFacts.discoveries.task` 同步 `next_time`、`retry_after`、`last_run_at`、`last_result`。
- Scheduler 任务清单 PUT 会保留后端运行结果字段，避免前端旧整表状态覆盖刚执行完的 `日常_报名` 次日触发时间。
- Runtime 会递归读取父帧/子帧，并把子帧中的独立弹窗/遮挡标记纳入守护候选。
- 未验收 legacy 任务不能被状态文件或 PUT 请求误启用。
- `go_scene` 已支持嵌套子场景的 `离开/返回/关闭` 空目标隐式返回父场景标题对应的同名图片；例如“世界”文件夹下的 `#85 某区域内部` 可规划回 `#34 世界`。
- `go_scene` 等待阶段会把“离开场景”确认弹窗视为中间态，识别到 `确认/确定` 后自动点击并继续等待原目标；该确认弹窗不能被学习成原按钮的最终 `sceneJumpTarget`。
- 场景身份默认阈值按 80 执行；`#85[离开]` 这类小图标在世界画面中 69% 的弱相似度不能算命中。真实世界场景当前补了 `#34[大地图当前入口]` OCR 身份锚点，用于兼容实际画面与旧 #34 截图不同的状态。
- 2026-06-04 真实验收：从当前 MuMu/ADB 画面执行 `/data-annotation/runtime/task/start`，`task_type=go_scene`、`target_scene_id=34`，最终 `success/done/current_scene=34`，耗时约 6 秒；证据截图保存在 `.codex_tmp/fanxiu_go_scene_34_final_20260604_012723/`。后续修复守护 tick 语义后再次真实验收，仍为 `success/done/current_scene=34`，耗时约 6 秒；证据截图保存在 `.codex_tmp/fanxiu_go_scene_34_after_guard_fix_20260604_015404/`。
- 2026-06-04 常驻行为树改造后真实验收：`GET /data-annotation/runtime/status?entry_id=30b82d72-8a76-4a74-be4b-4fc1591c6ce2` 可拉起常驻服务并恢复守护配置，状态为 `service_running=true`、`running=false`、`guard_enabled=true`、`guard_running=true`；随后通过 `/data-annotation/runtime/task/start` 提交 `go_scene #34`，入口返回“手动作业已提交”，最终 `success/done/current_scene=34`，截图保存在 `.codex_tmp/fanxiu_resident_runtime_20260604/`。再次重载后状态仍能恢复为 `service_running=true` 且空转识别 `#34 world 100%`。
- 2026-06-04 手动作业入口收敛后真实验收：`/data-annotation/runtime/task/start` 不再直接启动业务线程，而是写入 `manual_job` 队列并唤醒常驻服务；常驻 loop 串行消费 `manual-1780522415517-4061817e` 后执行生成器式 `go_scene #34`，最终 `success/done/current_scene=34`，耗时约 6.7 秒，真实截图保存在 `.codex_tmp/fanxiu_manual_direct_generator_20260604/`。
- 2026-06-04 场景身份 role 语义修复后真实验收：在真实日程页执行 `/data-annotation/runtime/task/start`，`task_type=go_scene`、`target_scene_id=34`，Runtime 识别 `#66 -> #34`，点击 `返回`，跳转耗时 `2.3s`，最终 `success/done/current_scene=34`；证据截图保存在 `.codex_tmp/fanxiu_scene_role_fix_66_to_34_final_20260604/`。这次不是“已在目标场景”的短路命中。
- 2026-06-04 去掉手动作业 drain 线程和 `run-now` 直跑旧 pending job 后真实验收：通过 `/data-annotation/runtime/task/tick` 提交 `manual_tick`，resident loop 消费 `manual-1780525981456-8e88286b`，最终 `success/done/current_scene=34`，`manual_jobs.json=[]`，证据保存在 `.codex_tmp/fanxiu_resident_no_drain_manual_tick_20260604/`。
- 2026-06-04 手动作业并入 resident service 线程后真实验收：通过 `/data-annotation/runtime/task/tick` 提交 `manual_tick`，resident loop 直接执行 `manual-1780526898389-9e4c1ab5`，不再创建 `fanxiu-data-annotation-manual-job` 业务线程，最终 `success/done/current_scene=34`，`manual_jobs.json=[]`，证据保存在 `.codex_tmp/fanxiu_manual_inline_resident_tick_20260604/`。
- 2026-06-04 Scheduler 到期任务并入 resident service 线程：`start_scheduler_tasks` 不再创建 `fanxiu-data-annotation-scheduler` 业务线程，resident loop 空闲时直接串行执行到期任务；单元回归已覆盖 Runtime 不再拥有 `_thread` 字段。真实验收临时追加 `codex-temp-go-world` 到期任务，执行 `/data-annotation/scheduler/run-due` 后最终 `scheduler_run_due/success/current_scene=34`，证据保存在 `.codex_tmp/fanxiu_scheduler_inline_run_due_20260604/`，验收后已恢复原 Scheduler 配置。
- 2026-06-04 direct generic/gift-code 过渡入口已移除：直接 Runtime 任务统一走 `start_runtime_task -> _run_inline_runtime_task`，不再保留 `start_generic_runtime_task` 和旧 `start()`；Runtime 后台线程只保留 resident service。
- 2026-06-04 移除 `_thread` 字段后真实验收：通过 `/data-annotation/runtime/task/tick` 提交 `manual_tick`，resident service 消费 `manual-1780528737905-a0f24121`，最终 `success/done/current_scene=34`，`manual_jobs.json=[]`，证据保存在 `.codex_tmp/fanxiu_no_runtime_thread_manual_tick_20260604/`。
- 2026-06-04 移除旧直启入口和无调用 `_run` 后真实验收：通过 `/data-annotation/runtime/task/tick` 提交 `manual_tick`，resident service 消费 `manual-1780529858028-ce12bf62`，最终 `success/done/current_scene=34`，耗时约 31.1 秒，`manual_jobs.json=[]`，真实截图 `07_final_screencap.png` 保存在 `.codex_tmp/fanxiu_resident_after_entry_cleanup_20260604/`。
- 2026-06-04 `task/stop` 语义收窄后真实验收：先调用 `/data-annotation/runtime/task/stop`，返回 `idle/当前没有正在运行的任务/service_running=true`，证明 stop 不关闭 resident service；随后通过 `/data-annotation/runtime/task/tick` 提交 `manual_tick`，最终 `success/done/current_scene=34`，耗时约 8.4 秒，`manual_jobs.json=[]`，真实截图 `06_final_screencap.png` 保存在 `.codex_tmp/fanxiu_stop_current_task_semantics_20260604/`。
- 2026-06-04 相关回归：`uv run pytest backend\tests\test_fanxiu_data_annotation_runtime_guard.py tests\test_fanxiu_data_annotation_scheduler.py -q` 为 `71 passed`；`npm run typecheck --prefix frontend` 通过。
