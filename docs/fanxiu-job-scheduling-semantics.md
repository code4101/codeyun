# 凡修 Job 运行与调度语义

> 本文是凡修 `run_status`、`job_status` 与 `next_time` 的唯一语义定义。
> 用户或代码讨论任一关键词时，默认直接采用本文含义，不再临时重新解释术语。

## 日常开发的核心模型

凡修 Job 都是短期运行单元，通常在十分钟内结束。每次 Scheduler 启动 Job，
就是提交一个独立 Job Cell。

日常开发默认只设计这一条业务链：

```text
Job Cell 内产生 job_status
  -> Job 根据业务规则写入 next_time
  -> Job Cell 结束，job_status 随之消失
```

`run_status` 是所有 Job 共用的执行框架，不需要在每个 Job 的设计中重复展开。
只有 Job 要覆盖 `run_status=error` 的默认重试延迟，或正在排查 error、打断、
抢占等框架问题时，才专门讨论 `run_status`。

```text
next_time 到期
  -> Scheduler 启动 Job Cell
  -> Job 在 Cell 内按 job_status 推进业务
  -> Job 按业务逻辑设置 next_time
  -> Job Cell 对外只形成 run_status
  -> Scheduler 按 run_status 收尾
```

| 概念 | 生命周期 | 所有者 | 对 Scheduler 的意义 |
| --- | --- | --- | --- |
| `job_status` | 只存在于当前 Job Cell 内 | Job | 无；Scheduler 不读取 |
| `next_time` | 跨 Cell 持久化 | Job 业务域；仅 `error` 收尾时 Scheduler 有受限写权限 | 唯一到期事实 |
| `run_status` | 当前 Job Cell 的运行周期 | 通用执行框架 | 决定本次运行如何收尾 |

`job_status` 是本次业务过程；Job 结束后它就消失。业务判断带来的未来影响，
全部落在 `next_time`。Job Cell 对外不返回 `job_status`，只形成 `run_status`。

“业务失败”仍是 `job_status`，不是 `run_status=error`。凡是 Job 能够正常观察、
分类并决定后续策略的情况——例如资源不足、资格未解锁、目标未出现、领取失败、
窗口错过或需要稍后复查——都应由业务分支写入合适的 `next_time`（包括延后、
下一周期或 `None`），然后让 Cell 正常返回。业务代码不需要也不得主动返回一个
“run error”。`run_status=error` 只由未被业务正常处理的程序异常自然抛出后形成，
例如代码缺陷、基础设施失败、契约断裂或无法安全继续执行的异常。

### `next_time` 不是返回协议（强约束）

- 正式 Job 的 flow、handler、generator、admission 和 Cell 终态返回值都不得携带
  `next_time`；禁止 dict、tuple、`result_text` 或其它 terminal payload 运输该字段。
- 正常结束的每个业务分支必须在 Job Cell 内、业务完成点调用统一原子入口持久化
  `next_time` 或 `None`，确认写入成功后才允许正常返回。
- 统一 setter 写入失败必须让本次 Cell 进入 `run_status=error`；不得返回
  `next_time` 等待 Runtime 包装器或 Scheduler 兜底补写。
- 纯时间计算或纯决策 helper 可以返回候选时间；调用它的正式 Job 必须在自身返回前
  消费该值并完成原子写入，候选值不能继续越过 Job 边界。
- Runtime 通用包装器与 Scheduler 必须拒绝或忽略终态 payload 中的 `next_time`，
  不得默认生成次日时间、600 秒复查时间或任何业务周期。

凡修 Job 的工程形态是从稳定起点整单运行的幂等业务函数，不是持久状态机。Job Cell
结束时，本轮 `job_status`、局部变量、generator 栈、业务步骤、scene cursor 和选择意图
全部消失；跨 Cell 只保留正式业务事实与唯一 `next_time`。一次 attempt 中断或失败后，
Scheduler 只按 `run_status` 处理 `next_time`；下一 attempt 必须先把当前 scene 当作通用
导航输入，沿正式场景图归一到该 Job 的稳定起点（凡修通常为 `#34`），再重新观察事实并
从入口整单执行。禁止把 `#436/#437` 等中间页、结果页、旧 generator、旧步骤或持久游标
解释为恢复授权，也禁止从这些页面推断上次按钮意图后续点。同一 attempt 内，动作刚产生
的合法多落点仍由当前调用栈有界消费；这是局部事务分支，不是跨 attempt 恢复。

## `next_time` 的所有权

正常情况下，`next_time` 完全属于 Job 业务域：

- 无目标、CD 中、窗口结束、业务完成或业务未完成，都由 Job 自己判断。
- Job 自己决定立即再运行、延迟运行、进入下一周期或设为 `None`。
- Job 专属的事实巡检可以发出业务触发命令，把该 Job 提前到期；这是该 Job
  业务逻辑的外部入口。命令的完整实现就是原子写入该 Job 的
  `next_time=now`，不产生其它持久触发状态，也不是 Scheduler 根据运行结果猜测时间。
- 纯手动作业静止态 `next_time=None`；人工运行正常结束时 Job Cell 必须再次
  写回 `None`，不能被复用的旧日常流程重新安排到次日。这里仍是 Job 的业务
  决策，不新增触发类型，也不是 Scheduler 在 `success` 收尾时改时间。
- Scheduler 只判断 `next_time is not None and next_time <= now`。
- Scheduler 不读取业务消息，不理解周期、窗口、CD，也不从 `job_status` 推导时间。

Scheduler 只有一条路径可以修改 `next_time`：

> 本次 Job Cell 的 `run_status=error` 时，Scheduler 按该 Job 预先声明的运行错误重试规则写入新的 `next_time`。

除此之外，Scheduler 的通用运行收尾没有权限，也不应存在代码路径修改
`next_time`。人工改期和 Job 专属事实巡检属于显式触发命令；Scheduler 只忠实
应用命令，不自行产生业务判断。

### 手动运行的统一语义

Runtime 作业菜单区分两种直接运行方式；二者都立即提交同一个普通 Job Cell，区别
只在本次 Cell 的业务时间。全局默认是“提前运行”：

- **提前运行（按计划时间）**：保留原 `next_time` 作为计划锚点并立即运行；当它尚未到期时，
  本次 Cell 自动使用 `effective_now = next_time + 1 分钟`，模拟作业刚过计划触发点。
- **立即运行（按当前时间）**：立即运行同一个 Job Cell，业务窗口和下一周期计算读取
  真实此刻。

用户或 AI 只说“提前运行某作业”时，固定采用第一种语义，不再询问使用哪个时间。
只有明确说“立即运行”“现在运行”或“按当前时间运行”时才采用第二种。没有
`next_time` 的纯手动作业不能提前运行，只能立即运行。

标准 API 是 `POST /api/fanxiu/data-annotation/scheduler/task/run-now`，请求字段
`business_time_mode` 取 `planned | current`，默认 `planned`。调用方不得为普通提前
运行自行拼装 `effective_now`；后端统一从目标 Job 的 `next_time` 生成业务有效时间。
`trigger-once` 只表示把 `next_time` 写成当前时间并等待 Scheduler，不属于这两种直接
运行入口，也不得在 UI 或 Agent 操作中冒充“立即运行”。

`effective_now` 只影响 Job Cell 内通过统一业务时钟进行的窗口判断和时间计算；
Scheduler 的到期比较、attempt 时间、抢占和 `run_status=error` 重试仍使用真实墙钟。
因此，提前运行可能把 Job 的 `next_time` 直接推进到下一业务周期；立即运行
则可能因尚未进入业务窗口而正常跳过，并保留或重新计算较近的触发时间。没有
`next_time` 的纯手动作业不能提前运行；调用方显式提供 `effective_now` 时以
显式值为准。

## 通用 `run_status` 收尾

下面是框架一次设计、所有 Job 共用的矩阵。日常 Job 开发通常无需重复描述。

| `run_status` | Scheduler 对 `next_time` 的动作 | 说明 |
| --- | --- | --- |
| `running` | 不修改 | Job Cell 尚未结束 |
| `success` | 不修改 | 接受 Job 已写出的业务调度意图 |
| `error` | 按预设错误重试延迟写入 | Scheduler 唯一获准修改 `next_time` 的情况 |
| `interrupted` | 维持运行前原值 | 打断不消费本轮业务，也不产生错误重试延迟 |

### `success`

`success` 表示 Job Cell 正常运行到结束。Scheduler 只记录通用运行结果，
不关心 Cell 内部业务是否达成，也绝不再次调整 `next_time`。

为了让边界可验证，Job 的每个正常结束分支都应显式执行一次
`set_next_time(...)`；判断的是“是否明确写出调度意图”，不是新旧时间值是否不同。

### `error`

`error` 表示 Job Cell 没有正常完成。此时 Job 可能来不及形成可靠的业务调度意图，
Scheduler 才能机械应用预先配置的错误重试规则：

- 0 级 Job 默认延迟 10 分钟。
- 1 级及以上 Job 默认延迟 0 分钟，即立即重新到期。
- Job 可以声明其它错误重试延迟，覆盖所属等级的默认值。
- 有每日活动窗口的 Job 同时声明 `daily_start_time` / `daily_end_time`；若错误重试
  落在结束边界或更晚，Scheduler 将其裁剪为次日首个有效时刻。结束边界本身已
  属于关闭状态，例如 `22:00` 结束的活动不能写入当天 `22:00`。

该延迟只由配置决定。Scheduler 不得根据业务日志、报错文案或场景内容动态推断。

### `interrupted`

`interrupted` 与 `error` 分开处理。抢占或工程/AI 运行权切换只作废当前 Job Cell，
并维持本次运行开始前的原始 `next_time`：

- 不套用 10 分钟或其它错误延迟。
- 不接受被打断 Cell 可能已经写出的中间调度值。
- 不保存 `job_status`、generator 栈或业务步骤。
- 必须确认旧 Cell 已真正停止后，才能提交新的高等级 Job Cell。

实现上应在 Job Cell 启动时保留 `original_next_time`。`interrupted` 收尾恢复该值，
防止半途中断把尚未完成的业务意图提交到下一轮。

## 标准流程

```text
Job Cell 启动
  -> 记录 original_next_time
  -> run_status = running
  -> Job 内部运行并产生 job_status
  -> Job 调用 set_next_time(...)
  -> 正常结束：run_status = success，保留 Job 写值
  -> 异常结束：run_status = error，Scheduler 写错误重试时间
  -> 外部打断：run_status = interrupted，还原 original_next_time
```

## 单次 Job Cell 的业务有效时间

正式 Job Cell 可通过可选 `effective_now` 配置本次业务看到的当前时间；默认不传时
仍读取真实墙钟。该配置只在当前 `run_task_cell(...)` 执行期间生效，Cell 结束或异常
后立即恢复，不持久化为第二种触发状态。

- Python Cell：`kernel.task("task_type", effective_now="2026-08-13 21:15:00")`。
- Scheduler `run-now` API：请求体顶层传
  `"effective_now": "2026-08-13 21:15:00"`。
- Job 业务代码使用 `job_now()` / `job_today()`；通用
  `next_business_time(...)`、`next_biweekly_time(...)` 已自动读取该上下文。
- Scheduler 的到期比较、attempt 时间、抢占与 `run_status=error` 重试继续使用真实墙钟，
  不受业务有效时间影响。

它用于提前执行依赖日期、星期或业务窗口的完整正式 Job。例如 21:10 作业在 21:06
提前执行时可令业务按 21:15 计算，使正常完成后的 `next_time` 直接进入下一周期。
它仍然只是普通 Cell payload，不形成第二协议，也不授权 Scheduler 解释业务周期。

## 日常_红包解释

### Job 正常结束

```text
Job Cell 内：job_status = 已领取 / 领取失败 / 当前无红包 / 群未命中 / 缺少新鲜上下文
Job 写入：next_time = now + 12 小时
Job Cell：run_status = success
Scheduler：不修改 next_time
```

这些都是 Job 已正常完成判断的业务结果，不为其中任何一种另设短期重试分支。
`job_status` 不对外持久化，差异只用于本轮日志；未来意图统一落为十二小时后的
`next_time`。

### 每分钟红包事实巡检

```text
每分钟读取游戏 Runtime 红包事实
  -> 仍有待领取红包
  -> 原子写入 日常_红包.next_time = now
  -> Scheduler 启动 Job Cell
  -> Job 不把巡检事实当作点击授权，重新执行当前画面的多层视觉门卫
```

巡检不是第二套运行结果规则，而是红包 Job 的业务触发入口。只要权威事实仍显示
有待领取红包，即使候选集合与上一分钟相同，也应再次允许触发；不能因一次性指纹
去重，让 Job 写入的十二小时覆盖掉仍然存在的红包事实。

红包事实、群路由和观测时间保留在红包业务自己的 Runtime 快照中。禁止通过
`scheduler_meta`、Cell payload 或内存触发队列把这些事实运输给 Job；Scheduler
记录中只有 `next_time` 表达本次触发意图。

巡检误报或画面状态已经变化时，Job 应在任一视觉门卫阴性后正常结束，写入
十二小时后的 `next_time`，不得以固定标注坐标探测性点击。当前执行门卫依次为：
世界页 `#395[红包]`、聊天页 `#332[红包]`、群列表实时匹配的
`#332[红包图标]`，以及群内排除“已领取/你领取了”历史后的 #30 OCR 红包卡片。
只有当前层证据成立，才允许进入下一层；巡检随后仍可依据新事实再次提前调度。

### 红包发生运行错误

```text
Job Cell：run_status = error
Job 配置：未覆盖错误重试延迟，dispatch_level = 0
Scheduler：next_time = now + 10 分钟
```

这里的 10 分钟来自通用运行错误策略，与“领取失败”等 `job_status` 无关。
如果事实巡检同时仍看到待领取红包，它仍可独立发出业务触发命令，把 Job 更早拉起。

### 灵泉抢占红包

```text
红包：run_status = running，dispatch_level = 0
灵泉：next_time 到期，dispatch_level = 1
Scheduler：interrupt 红包
红包：run_status = interrupted
Scheduler：恢复红包 original_next_time，不加 10 分钟
确认红包 Cell 停止后启动灵泉
```

因此，灵泉到期后红包仍继续操作游戏，或者红包和灵泉来回切换，都不是合法状态。

## 代码与文档约束

- `run_status` 和 `job_status` 是凡修固定关键词；用户提到时直接进入本模型。
- 用户说“添加作业”时，默认同时注册作业类型并把实例加入当前机器的 Scheduler 标准清单；只有明确说“只加类型”或“先不加清单”才省略实例。未说明触发规则时，默认是手动作业：`trigger_description="手动"`、`next_time=None`。
- Runtime 页不提供“作业 +”或自助创建作业入口。作业必须先随代码完整定义业务行为、`job_status -> next_time` 和运行边界，再由开发者加入标准清单；Runtime 只操作已实现作业。
- 标准清单不能再与 Job Cell 注册表平行手工维护：`standard_job=True` 的定义由默认清单构造器自动实例化；需要固定首次触发点的业务 Job 显式提供默认实例。契约测试会校验所有 `scheduler_supported=True` 的 Job 已进入清单；当前没有隐藏的 Scheduler 原语例外，`login_game` 也作为“登录”手动作业展示。运行时不做硬报错，以免测试或临时 Cell 注册污染正式清单。
- 历史缺失的 `历练_领取`、`历练_事件`、`日常_游历`、`日常_双修`、`日常_每日副本` 属于同一类清单漂移：执行实现存在，但默认实例曾在框架重构中被遗漏或删除。修复时既补默认定义，也通过 Scheduler repair 补当前机器存量数据，不能只改其中一层。
- Scheduler 清单是共享持久状态，不允许 API、外部 Scheduler、Kernel/Runtime 各复制一套整表读写。统一存储入口在文件锁内以磁盘最新成员集为基准：调用方旧快照中省略的 Job 必须保留，只有显式 `removed_task_ids` 才能删除。Kernel 内 Job 更新 `next_time` 只拥有目标 Job 的运行字段，不能用整表快照改变清单成员。
- 2026-08-02 的历史备份审计证实：6 月 11 日清单仍含 `日常_游历 / 日常_双修 / 日常_每日副本`，6 月 26 日前两项已消失，7 月 6 日第三项也消失；修复过程中还现场观察到旧 Kernel 快照把已合并的 `sunday-daofa / sunday-xianyuan-duel` 再次写回。根因是两套读写器都采用“读整表 -> 改一项 -> 写整表”，文件锁只串行化写入，无法阻止旧快照回滚成员集合。
- 同一业务即使不同星期、窗口或周期使用不同触发时刻，也仍是一个 Job；这些差异由 Job 正常结束时动态计算下一次绝对 `next_time`。禁止为了表达 `next_time` 规则而拆成“周日版 / 工作日版”等多个 Scheduler 实例。
- 日常 Job 需求默认先写清 `job_status -> next_time`，不为每个 Job 重复设计
  `run_status`。
- 不使用裸 `success / error`；代码审查和汇报必须指明是 `run_status` 还是 Cell 内业务判断。
- 不新增持久化 `job_status`。
- 不让 Scheduler 根据业务结果、消息或日志修改 `next_time`。
- Job 专属事实巡检命中时只原子设置目标 Job 的 `next_time=now`；禁止新增
  `trigger_request`、`repeat_trigger`、`refresh_only` 或上下文去重协议。
- 模拟器重启后的登录排队可以通过统一原子入口显式设置
  `login-game.next_time=min(now, 其它作业最早的有效 next_time)-1分钟`，使登录排在现有
  时间队列首位；若登录已有更早时间则保持不变。外部 Scheduler 只按时间、优先级、
  attempt 和 Kernel 资源派发，不读取启动状态、公告、OCR、overlay 或维护业务事实，
  也不据此改期、阻断或内联执行登录。被登录链挡住的原业务 Job 保持原
  `next_time`，登录完成后自然重试。
- “登录”Job 自己检查设备健康并处理正式场景。设备非健康时走标准设备修复；只有正式
  `#14/#15/#16/#17/#18/#415` 可以触发相应登录或维护动作，公告判断只认正式 `#14`
  场景，不使用 OCR/overlay 推断。健康设备上的其它 scene 或 unknown 都原地幂等完成，
  不强制导航到 `#34`；正常完成后登录 Job 把自己的 `next_time` 清回 `None`。
- Job 启动后直接读取所属业务域的最新事实；禁止用 `scheduler_meta` 或隐藏 Cell
  payload 运输巡检业务上下文。
- 全仓检查 Scheduler 对 `next_time` 的写入点：除 `run_status=error` 收尾外，
  其余写入必须来自 Job、Job 专属事实触发命令或明确的人工配置命令。
- `attempt_id` 只标识某次 Job Cell，不能替代 `run_status`，也不形成新的业务状态。

## 验收清单

1. 用户只说 `run_status` 或 `job_status` 时，AI 是否直接按本文理解？
2. 日常设计是否先明确每个 `job_status` 对应的 `next_time`？
3. Job 的所有正常结束分支是否显式写入 `next_time`？
4. `success` 收尾是否完全不改 `next_time`？
5. `error` 是否只按 Job 的预设错误重试延迟修改 `next_time`？
6. 0 级默认 10 分钟、1 级及以上默认 0 分钟是否成立？
7. `interrupted` 是否还原 `original_next_time`，且旧 Cell 已真正停止？
8. Scheduler 是否还存在读取业务消息或 `job_status` 决定时间的路径？

任一项不成立，都说明业务逻辑、运行收尾和调度权限仍然混层。
