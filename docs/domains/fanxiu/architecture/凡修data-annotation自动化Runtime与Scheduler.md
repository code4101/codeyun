# 凡修 data-annotation 自动化 Runtime 与 Scheduler

> Kernel / Cell 的唯一架构定义见 [凡修行为树运行框架约定](./凡修行为树运行框架约定.md)。本文只描述业务 Runtime 与外部 Scheduler 数据。GUI 场景帧、识别分层、识别图、弹窗插入和场景移动以
> [凡修 GUI 场景地图与图模型约定](./凡修GUI场景地图与图模型约定.md) 为准。
> `run_status`、`job_status`、`next_time` 与错误重试权限以
> [凡修 Job 运行与调度语义](./fanxiu-job-scheduling-semantics.md) 为准。
> 新作业从业务研究、只读插桩、frame/shape 标注、AI Cell 调试到真实验收的步骤见
> [凡修新作业研究与上线指南](../guides/凡修新作业研究与上线指南.md)。

## Runtime

Runtime 是加载在凡修 Jupyter Kernel 中的业务框架，不是第二个执行器。它提供：

- 当前帧、OCR、shape、scene 与资产树访问；
- 点击、长按、拖拽、输入和等待；
- task、guard、generator 的推进；
- 业务日志、本次业务结果，以及业务主动设置调度时间的命令。

Runtime 负责业务原子函数。日常 Job 开发默认只设计 `job_status -> next_time`：Cell 内 `job_status` 只服务本次业务推进，Job 通过 `设置触发时间(作业, 时间或 None)` 把未来意图写入唯一 `next_time`。`run_status` 是所有 Job 共用的执行框架，通常无需在单个 Job 中重复设计；只有 Job 要覆盖 error 重试延迟或排查框架故障时才专门讨论。Kernel 外 Scheduler 仅在 `run_status=error` 时应用预设错误重试延迟：0 级默认 10 分钟，1 级及以上默认 0 分钟，Job 可配置覆盖值。不得增加 `retry_after` 等第二触发字段。

正式任务和调试代码都通过普通 Cell 进入同一 Kernel。`/data-annotation/runtime/cells/task` 只把已注册任务构造成 Cell；`/data-annotation/runtime/cells/code` 直接提交 Python Cell。

## Scheduler

Scheduler 位于 Kernel 外，持久化：

- 作业类型、实例 id、显示名；
- 纯文本 `trigger_description`；
- 作业写入的原始触发事实 `next_time`；
- `last_run_at`、`last_result`、最终消息；
- 当前执行尝试 id、Kernel generation、开始/结束时间；
- 业务所需 payload。

### 作业定义与实例配置

- Runtime 前端不提供“作业 +”或自助创建作业能力。作业的难点是业务行为、`job_status -> next_time` 规则和运行边界，必须随代码完整定义并由开发者加入标准清单；Runtime 只负责已实现作业的查看、编排和运行。
- 默认作业定义负责提供统一实例结构，并只在首次创建时生成初始 `next_time`。
- 作业实例没有独立的启用状态。实例是否自动运行只由唯一触发事实 `next_time` 决定。
- 不保留旧结构迁移、schedule override 或定义版本兼容层；结构改变时直接以当前模型收敛。
- `payload` 只放业务参数，不得塞入调度规则或兼容标记。
- `next_time=None` 表示休眠；写入绝对时间表示进入自动调度。“手动运行”只是一次提交操作。
- 每日、每周、窗口、完成周期和动态复查都属于作业内的 `next_time` 算法，Scheduler 不提供周期规则。
- Scheduler 数据模型不保存 `window`，也不根据业务截止时间作判断。带时间约束的作业使用注册定义中的同步准入函数；准入必须在任何 Runtime/GUI 副作用之前完成，窗口外只写结果与下一次 `next_time`。

到期判断严格等价于 `next_time is not None and next_time <= now`。到期时 Scheduler 选择一个任务，构造 `run_task(...)` Cell，提交当前 Kernel并等待最终结果。下一次调度重新读取最新事实，不保存“上次做到第几步”的业务进度。

Scheduler 空闲时直接睡眠到最近的 `next_time`，固定巡检周期只作兜底。提交
到期 Cell 前必须确认 Kernel 加载的行为树代码指纹仍与磁盘当前版本一致；
不一致且 Kernel idle 时先 restart 并完成 bootstrap，再立即提交作业。
该更新检查不新增触发时间，也不得引入跨轮次 task-id 冷却。

`scheduler_tasks.json` 是触发时间的唯一持久化来源。world facts 中的 task 记录只保留结果、消息和运行时间等观测信息，不镜像 `next_time`，更不能反向覆盖 Scheduler。

`scheduler_meta` 只允许保存世界事实同步时间、运行框架诊断和人工调度备注等调度元数据；不得保存 scene、step、generator、业务游标、业务 gate 或巡检业务事实。Job 专属事实巡检命中后只通过统一原子入口设置目标 Job 的 `next_time=now`；Job 启动后从所属业务域读取最新事实，不经 Scheduler 转运。

工程、AI 和人工来源的优先级及模拟器独占仲裁属于 Scheduler / Dispatch Arbiter。Kernel 不保存来源锁，也不消费 Scheduler 队列。当前 Jupyter shell 自身按协议串行执行 Cell；来源仲裁不得被实现为 Kernel 内 JSON queue。

工程作业使用 `dispatch_level=0..5` 表示抢占等级，默认 0。空闲选单优先运行最高等级；等待当前工程 Cell 时，外部 Scheduler 会继续读取最新任务事实，严格更高等级的到期作业可原生 interrupt 当前 Cell。同级不抢占。被抢占 Job Cell 形成 `run_status=interrupted`，恢复运行前原始 `next_time`，不持久化 `job_status`、generator 或业务步骤，下一轮从稳定起点整单重跑。

Job Cell 对外只形成 `run_status`。凡 Job 正常执行到结束，都必须由业务自行显式写出新的 `next_time` 后形成 `success`；Scheduler 不修改该值。只有 `error` 收尾允许 Scheduler 按预设错误重试延迟写 `next_time`；`interrupted` 恢复运行前原值。Scheduler 不理解 `job_status`、业务文案或动态 CD。

同级作业使用 `dispatch_order=0..9999` 配置软顺序：先按 `next_time` 分批，同批中正数越小越先，`0` 表示未指定并排在显式顺序之后。执行结果不参与派发排序。

触发时间编排是独立配置，按 `HH:mm` 保存有序作业 id。持久化任务中的 `next_time` 永远是作业写入的原始值；调度器和前端展示的 `next_time` 是二次加工值。只有多个作业的原始完整日期时间完全相同、并且落在对应编排组时，才按配置中的相对顺序紧凑生成偏移：第 1 条 `+0` 分钟，第 2 条 `+1` 分钟，以此类推。配置中未参与本次并列的作业不占位；二次加工不得回写或污染原始 `next_time`。

当前 21:30 默认顺序是：`灵脉_清体力(10) -> 洞天_行动力(20) -> 日常_奇袭魔界(30)`。

道法争锋与仙缘斗法各自只有一个 Scheduler Job。星期与时间差异属于 Job 的
`next_time` 算法，禁止再按星期拆成多个实例：

- 周一至周六：两者下一次均为 `23:00`。
- 周日：道法争锋下一次为 `18:30`，仙缘斗法下一次为 `19:00`。
- 仅这两个业务的 Scheduler 失败冷却与业务安全复查为 1 分钟；其它作业
  继续使用各自的重试策略。
- 两个业务每天 `10:00` 后都可运行；周一至周六到 `23:59:59` 关闭，
  周日以 `22:00` 为结算关闭边界。`23:00`、`18:30`、`19:00` 只是一般
  调度策略，不是游戏开放窗口。手动运行与自动调度使用同一真实开放窗口。
  窗口过期时本场直接作废，由同一个 Job 动态计算下一个自然日对应的触发时间。
- 每日窗口过期不是普通成功。准入仍以“无游戏副作用”的成功结果推进
  `next_time`，同时必须在 Runtime 数据目录的 `scheduler-incidents/`
  生成独立事件档案。档案至少保存周期日期、原触发时间、新触发时间、
  最近一次作业结果与消息、attempt/Kernel 信息和 Runtime 近期日志，并
  标记 `analysis_status=pending`，供后续 AI agent 分析未按时完成的根因
  和提出可验证优化；禁止因为记了事件就恢复跨日补跑。
- 工程 Scheduler 的失败 attempt 和人工中断 attempt 也写入同一目录，
  分别使用 `attempt_failed`、`attempt_interrupted`。后续 AI 应按
  `task.id + cycle_date` 将这些尝试与最终 `window_expired` 归成同一
  日周期复盘，不能只看最后一条报错。

`周常_圣祖` 是周日 `20:00` 自动开启的标准作业。业务只允许在
`20:00–20:05` 执行，因此作业自身在 Cell 最前面判断准入，并按 1 级
派发；失败时保留原 `next_time`，再次立即整单尝试。若下一次尝试已过
截止时刻，准入直接把 `next_time` 推进到下周，不触碰游戏。点击 `#385[前往挑战]` 后，
`#338` 是魔祖、圣祖等活动共用的“进行中”画面结构；本次业务动作
明确来自圣祖入口时，到达 `#338` 即证明已参战，不得根据资产目录名
改判为魔祖。进入后必须让自动战斗实际运行至少 30 秒，再点击共用页面
OCR `离开`，沿通用确认流程退出并返回 `#34`；返回后才写成功。

仙缘斗法的完成事实不是“已执行固定次数”，而是游戏
`#308[次数]=0`；动态选人、一次刷新和抓包事实边界见
[凡修仙缘斗法标准作业](../jobs/凡修仙缘斗法标准作业.md)。

动态复查作业的“N 分钟后复查”必须以业务确认时刻为基准，不得使用本次 attempt 开始时刻。例如论道作业开始后经过场景移动、抓包和入座确认才成功，下次半小时复查应从确认入座时起算。若误用 attempt 开始时间，耗时较长时会让 `next_time` 过早；后续基础设施失败保留该已逾期时间，就会形成连续重跑。

论道抓包追平失败时必须区分玩家是否已入座：从大罗名单返回后若真实场景为 `#304 闻道中`，说明现有座位仍有效，抓包争用只能降级为从当前完成时刻起半小时后复查，不得保留逾期时间立即重试；只有返回 `#296`、即尚未入座时，才允许保留原触发时间尽快整单重试，避免损失当天收益。

论道动态复查还必须按权威座位状态分频：只有 Runtime/新鲜抓包确认 `seated=true` 且 `room_id` 为三清或大罗时，才使用 30 分钟稳定复查；确认 `seated=false` 或 `room_id/seat_id` 为空且今日闻道时间未归零时，统一在 10 分钟后重试。大罗目标变化、被踢后购买次数、名单刷新失败等分支不得用“保持三清”代替座位事实判断，避免无座状态错过当天 6 小时收益。

21:00 后是明确的成本止损边界：若免费挑战次数已经为 0，当天论道直接结束并把 `next_time` 写到次日 15:30；不得购买额外次数，也不再为了最后一小时收益继续检查大罗或三清空位。该业务完成分支形成 `run_status=success`，不是运行错误重试。

## 作业原子性

工程 Scheduler 的业务作业按原子事务理解：

1. 从业务函数实际看到的当前现场开始；需要 `#34` 的作业在自己的代码入口显式导航，Scheduler 和通用 Cell 包装层不提供场景默认值。
2. 从头执行完整业务闭环。
3. 成功与失败后的场景处理都由业务函数显式完成；通用框架只记录运行结果。
4. 业务按每个分支显式设置下次触发时间；需要休眠时设置为 `None`。

任务注册中不再提供场景生命周期。`scheduler_supported` 只描述调度器能否提交，`standard_job` 只描述是否进入标准清单；两者都不得隐式推导 `#34`。任何场景移动必须出现在具体作业的执行代码中。

同一个 Cell 内的 generator 可以跨 tick 保留内存进度；Kernel restart、后端重载或下一次 Scheduler 触发不得恢复旧业务中间步骤。

未被明确分类为 `interrupted` 的基础设施故障形成 `run_status=error`，Scheduler 按该 Job 的预设错误重试延迟写入 `next_time`；明确 interrupt 则恢复运行前原值。两者都整单重跑，业务入口自行决定是否先恢复稳定起点；禁止把 Kernel 级故障降格为业务步骤级续跑。

AI/人工逐步调试可以提交多段 Cell，因为上下文由当前会话持有；调试分段不得沉淀为工程作业的跨 Cell 续跑协议。

### 幂等作业函数，按当前事实重新运行

凡修作业的工程形态应是幂等业务函数：每次运行都从当前画面和当前业务事实重新决定下一步。普通作业不得引入持久 step、流程转移表、恢复队列、跨 Cell 游标或“上次执行到哪里”的进度快照。

标准作业函数只依赖三类当前事实：

1. 当前直播帧识别出的 scene；
2. 通过场景地图到达目标 view 的导航结果；
3. 只读运行态、OCR 局部结构或页面文案能够证明的业务事实。

作业中途失败后不需要恢复旧调用栈。只要 `next_time` 仍然过期，Scheduler 会再次提交完整 Job Cell；新 Cell 从当前真实画面重新观察，必要时通过 `go_scene` / `goto_view` 到达本轮目标，然后按幂等分支决定是否动作、跳过、补剩余项或写下一次时间。

允许的复杂性集中在场景地图、识别图、导航规划器和作业内的局部事实判断；不得扩散成每个业务各自维护的流程类。若一个本可由百行幂等函数表达的作业膨胀为大量续跑、进度快照和跨 Cell 协调代码，应优先重构为“观察 -> 导航 -> 判断 -> 动作 -> 验证 -> 写 `next_time`”的直线流程。

## Guard

Guard 是 Runtime 框架策略，不是常驻 Kernel 队列里的高优先级任务。Guard 只保留设备健康等环境能力；游戏内弹窗不再由独立 Guard 扫描或点击。

Guard 配置可以持久化，但其执行只能发生在一个正在执行的 Cell 内。Kernel 空闲时不得由 resident runner 自行轮询、OCR 或点击游戏。

### 游戏维护业务事实

`#415`（“停更码字中，敬请期待更新”）是游戏业务域的不可用事实。正式登录或维护
Job 识别到它时，在世界事实 `availability.game` 记录维护状态，并由维护 Job 自己规划
`system-maintenance-recovery.next_time`、执行恢复检查和清除事实。普通 Job 的原始
`next_time` 不因维护事实被批量修改。

外部 Scheduler 不读取维护事实来筛选、阻断或改期普通 Job；它仍只按 `next_time`、
优先级、attempt 和 Kernel 资源派发。维护恢复 Job 到期后自行完整重启 MuMu、按正式
`#14/#18/#415` 场景验证服务，并在仍维护时写下一检查时间；确认离开维护循环后清除
维护事实，把自身 `next_time` 设为 `None`，必要时通过统一原子入口显式触发“登录”。

“登录”Job 不承担回到世界页的职责：设备非健康时走标准恢复；健康设备只对正式
`#14/#15/#16/#17/#18/#415` 执行动作，公告只认正式 `#14` 场景，不使用 OCR 或
overlay 推断。其它 scene 或 unknown 原地幂等完成，不强制 `goto_view(34)`。Scheduler、
通用 Cell 和 Guard 都不代做这些业务判断或场景动作。

每次业务场景识别都会把业务声明的 Layer 0 与资产树“弹窗”目录下具有显式场景身份的节点合并，交给同一次识别图裁决。资产嵌套不产生识别边，无身份素材自动忽略。图返回业务预期节点时由业务消费；返回仅由弹窗批次注入的节点时执行该节点绑定的中断动作，然后抓取新帧并用完全相同的业务+弹窗 Layer 0 重复识别，直到业务节点胜出、识别歧义或达到有界处理上限。同一 scene id 同时被当前步骤显式声明为业务预期时，以业务动作消费它；这只决定动作归属，不重算、不替换图的身份结论。不得在图裁决前后增加场景分数优先、按编号覆盖或第二层子场景识别。

## 场景与 shape

- 业务任务只声明 scene 和 shape，不处理物理坐标。
- 场景身份不设置候选作用域。交通枢纽和典型菜单关键帧进入 Layer 1；其余带场景身份证据的场景帧进入 Layer 2；没有场景身份、只提供全图相似辅助信息的参考帧进入 Layer 3。
- Layer 0 不是资产字段，而是一次识别调用动态传入的候选清单。默认识别从 Layer 1/2 构造候选；业务步骤已知合法场景范围时传入 scene id 列表形成 Layer 0。两者使用完全相同的身份评分、识别图关系和歧义判定，不得再用额外候选字段、文件名或兼容分支暗中排除候选。
- 资产树读取统一走 data-annotation storage。
- 场景身份用 `isSceneIdentity / sceneIdentityRole`。
- 场景身份只由资产树标注和统一识别图裁决。Runtime、`go_scene`和业务 task 只消费 Layer 0～2 可靠识别结果中的 scene id，不得在代码中复制任何场景身份规则。Layer 3 的参考图全图相似度只提供 unknown 辅助信息，不能输出 scene id、打断 unknown 或成为导航落点。画面连续 60 秒没有可靠 scene id 时，可降级使用 `#424[返回]` 的动作配置投影到当前宿主画面；这不把当前画面识别成 `#424`。点击后必须重新取直播帧并从 Layer 0 重新识别和规划；重复尝试必须节流且有界。

### 场景 Shape 多重继承

- `parentSceneIds` 是独立的 Shape 配置继承关系，支持按声明顺序填写多个场景编号；它与资产树嵌套、场景跳转关系和识别图边均无关。
- 资产树 JSON 中保存的是原始标注 Shape。Runtime 加载时生成一份有效 Shape 投影，供场景识别、OCR、抠图、mask、点击、弹窗处理和场景图规划统一消费；有效投影不得写回资产树。
- 父场景只提供 Shape 配置。继承到子场景后的 Shape 以子场景作为宿主，必须使用子场景的图片、尺寸和当前帧执行匹配、裁剪与动作，不能回退使用父场景图片。
- 父 Shape 的全部配置都参与继承，包括场景身份、匹配阈值、OCR、动作及 `sceneJumpTarget`。因此识别层级和图模型关系从有效 Shape 动态计算，继承关系本身不生成图边。
- 菱形继承按原始 Shape 来源去重；缺失父场景、自继承和循环继承必须明确报错，不得静默降级成部分继承。
- Runtime 对继承 Shape 产生的标注事实更新（例如真实跳转落点频次）写回该 Shape 的原始标注来源；动作执行的宿主仍然是当前子场景。
- 物理资产树上下级不再隐含 Shape 继承。需要复用时必须显式填写 `parentSceneIds`；若两个场景只需共享一部分配置，应拆出共同模板场景并让两者共同继承。
- 点击和长按使用已命名 shape。
- 跳转使用 `sceneJumpTarget` 和通用场景规划；该字段记录真实落点及频次，是规划先验而不是白名单。
- 可靠识别到新的实际落点时，将它追加到 `sceneJumpTarget` 并从该场景重新规划；不因“此前未声明”而中断，也不另建 `observedLanding` 双轨字段。
- 缺标、错标或 unknown 经 `#424[返回]` 等有界恢复仍未解决时停止并交给人工，不继续猜测其它坐标。
- `go_scene` 的停滞判定与恢复证据属于 Runtime 导航事实，不属于 Scheduler 状态。稳定自环、开始投影 `#424`、恢复耗尽、10 分钟硬上限和 24 步上限都会创建持久导航事件；事件保留动作前后真实帧、识别候选、OCR、身份裁剪、真实落点、任务/Kernel 和资产树版本，并投影到标注页“识别运维 / 运行停滞”。即使 `#424` 最终恢复成功，事件仍保持待人工复盘。

设备与资产事实见 [凡修 data-annotation 运行设备约定](../runbooks/凡修data-annotation运行设备约定.md)。

## 状态维度

页面需要分别展示：

- Kernel：`idle / busy / dead`、generation、manager pid、kernel pid。
- Runtime：当前 task、phase、业务结果、日志。
- Scheduler：next_time、last_result、due plan。

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
- Scheduler 的 next_time/result 仍能各自独立更新。
- 真实游戏作业验收必须同时检查 Runtime 最终状态、日志和真实 ADB 画面；Kernel 架构验收只使用无害 Cell。
