# 数据标注自动化 Runtime 与 Scheduler

本文档记录凡修 `数据标注` Runtime、Scheduler 与行为树的职责边界。

命名先读：[凡修 data-annotation 命名约定](./凡修data-annotation命名约定.md)。简单说：`scene` 是业务场景主术语，`frame` 只表示截图/匹配输入或资产树底层帧结构，`view` 只作为历史兼容名保留；资产树是标注事实容器，识别树是 Runtime 动态候选计划。

## 核心原则

- 资产树是感知与定位的第一数据源。
- 已有场景、父帧/子帧、shape 标注可用时，Runtime 禁止猜坐标。
- 处理行为树任务前必须先读取当前 `entry_id` 的资产树标注，而不是从截图重新目测入口。当前机器的标注数据位于 `CODEYUN_DATA_DIR/fanxiu/data-annotation/entries/<entry_id>/asset-tree.json`，图片位于同一 entry 目录下的 `images/`，后端统一通过 `_data_annotation_asset_tree_path(entry_id)` 和 `_index_images()` 读取。
- 前端只负责控制、调试、展示和标注，不承载正式任务状态机。
- Runtime 负责实际执行、当前场景识别、守护 tick、点击、输入、等待。
- Scheduler 负责任务清单、到期判断、按分组和触发时间排队，以及手动触发。
- 新增 Runtime 业务代码优先使用 `scene` 术语：`current_scene()`、`wait_scene()`、`go_scene()` 分别表示当前识别、等待目标场景和正式场景移动。`wait_view()` / `goto_view()` 保留为历史兼容入口，服务旧调用和底层 `View` 类模型，不作为新业务 API 的命名方向。

## 资产树与识别树

资产树 `asset tree` 是标注资产的分组和数据容器，保存 scene、shape、OCR/图像匹配配置、`sceneJumpTarget` 和人工资产分组。资产分组嵌套不能直接等同于运行时 sub scene 关系；分组可以帮助人管理资产，但不应天然决定 Runtime 候选展开、等待顺序或跳转策略。

识别树 `recognition tree` 是 Runtime 每次识别或跳转时动态生成的候选计划。它综合资产事实、图片 `layer` 字段、兼容期 `image.children` 结构、当前目标、route/path 上下文、弹窗/浮层规则和本次动作产生的 `layer0`，再决定先识别哪些候选、命中后如何下钻、失败后如何回退到全局队列。

当前第一批落地边界：

- `backend/core/fanxiu/data_annotation/recognition_tree.py` 负责把资产树投影成 Runtime 识别候选节点。`type="folder"` 分组只进入 `asset_path`，不产生父子识别关系；只有 `image.children` 会形成当前兼容保留的 scene/sub-scene 结构。
- `runtime_runner` 的 root 场景候选收集通过 recognition tree helper 生成，保持原有 layer 1 -> 2 -> 3 与弹窗过滤行为。
- `layer0` 仍由调用方目标或 `sceneJumpTarget` 动态传入；helper 已提供 layer0 候选扩展边界，用于后续把 preferred target、父级上下文和目标子树统一收敛到识别树计划。

后续分批改造：

- 第一批已做：抽出 recognition tree 构建边界，补 focused tests，保持现有识别器行为不变。
- 第二批可做：让 `SceneRecognizer.identify_scene_tree_number` 接收显式 recognition tree plan，减少它直接读取资产树结构的职责。
- 必须兼容保留：`image.children` 当前仍是已人工确认的 scene/sub-scene 资产事实，不能一次性移除；持久化字段 `type="folder"` 和 `children` 暂不改 schema；`wait_view()`、`goto_view()`、`View` 类仍保留历史兼容。
- 暂不动：目录隐式返回、具体 sub scene 策略、#59/#277、仙府入口误识别和真实 Runtime 验收。

## 资产树与识别结构语义

资产树当前同时保存两条正交识别事实：`layer` 和兼容期 `structure`。`layer` 表示 root scene 所在的全局识别队列；`structure` 表示已人工确认的 scene/sub-scene 细化关系。资产分组只是方便人找资产和组织 root 识别队列；运行时父子候选关系必须先投影为 recognition tree，再由识别树结合当前上下文展开。

如果一个流程可达某个父 scene，那么该父 scene 下的 sub-scene 在逻辑上也可作为识别树候选；任务实现不应要求每条跳转路径都重复写到每个 sub-scene。

定期检查 scene/sub-scene 归纳时，使用标准入口：

```bash
uv run python scripts/fanxiu_organize_frame_structure.py --scope layer --keep-unshared-parent-identities
```

该命令默认 dry-run：按识别层视图收集 root scene，跨资产分组检查“父 scene 的场景身份锚点是否在另一个 root scene 中成立”，并输出候选父子关系、命中锚点、分数和双方路径。确认候选合理后再追加 `--write` 写回资产树；写回时会创建备份。资产分组不作为归纳依据；写回结果仍使用现有 `image.children` 表达 sub-scene。

Runtime 识别场景时应优先使用资产树内已有 scene 和 shape。只有缺少标注时，任务才应报错或进入人工补标流程，不应在正式任务里临时猜测按钮位置。

frame 承载“场景身份”，shape 承载“判定证据”。同一场景可以有多个 `isSceneIdentity / sceneIdentityRole` 锚点，Runtime 场景分数按“全部场景标识都命中”理解：同一 view 内多个场景标识取最低分，任意一个 required 锚点低于阈值都不能判定为该场景。检测/调试页可以把每个条件都展示出来，用来评估是哪一个锚点没有通过。不要把同一场景的多个备选锚点都勾成场景标识；如果只是备选，请拆成不同子帧，或只保留当前稳定的一组必要锚点。

scene 资产的 `layer` 表示 root scene 进入哪条全局识别队列：`layer=1` 先识别，`layer=2` 后识别，`layer=3` 最后识别，通常用于模板、素材、过渡和低优先级 scene。同一个 `layer` 内用 `layerOrder` 表达识别优先顺序；识别层视图里的拖拽排序只更新这个顺序字段，不改变资产分组归属。`layer` 和 `layerOrder` 都不表达父子关系；scene 与 sub-scene 的候选关系由 recognition tree 根据 `image.children`、route/path 上下文和 `layer0` 动态计算。

旧 frame 等级只作为一次性迁移输入：旧等级 2 迁移为 `layer=1`，旧等级 1 迁移为 `layer=2`，旧非场景帧迁移为 `layer=3`。迁移后代码、UI 和文档只使用 `layer` 与 `structure`。shape 只表达“这个框是否作为当前 frame 场景标识，以及它的图像/OCR required/optional/off 策略”。

Runtime 无上下文识别按 root `Layer 1 -> Layer 2 -> Layer 3` 的全局队列阻断式尝试。前一层命中 root scene 后，不再检测后续 root layer；而是沿识别树候选继续细化。sub-scene 命中则继续细化，sub-scene 不命中则返回最近命中的父 scene。子 scene 的 `layer` 不参与 subtree 细化顺序，也不会让识别重新回到全局 layer 队列。如果传入候选清单或 `sceneJumpTarget` 预期目标，则候选视为 `layer0`，优先在这些候选所在 root 及其识别树子树内识别，候选未命中才回到默认 root layer 队列。业务调试不得临时只测一个 shape 就断言当前场景；必须调用 `ctx.scene(...)`、`runtime.current_scene(...)`、`runtime.go_scene(...)` 或正式 `go_scene` 等公共入口，让基础设施按 layer、识别树候选和全部场景标识条件判定。

sub-scene 的场景身份按链式语义理解：父 scene 命中后才会进入子 scene 识别，因此子 scene 只需要匹配自己的增量场景标识，不应把父 scene 的场景标识重复并入子 scene 的匹配谓词。语义上 `#75 = #69 + #75 own identity`，运行时则是先算 #69，命中后只算 #75 自己。

shape 的使用规则与场景身份不同：sub-scene 可以继承祖先 scene 的普通 shape、动作 shape、区域 shape 和滚动 shape。查找 shape 时先查当前 scene 自己定义的 shape，再沿父 scene、祖先 scene 向上查找；子 scene 的同名 shape 会覆盖父 scene 的同名 shape。点击、匹配、OCR 裁剪和滚动区域使用 inherited shape 时，参考标注来源仍是 shape 所在的原 scene，但当前场景上下文仍是已识别到的子 scene。

行为树实现必须复用资产树的三类信息：

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

- 滚动窗口的默认动作由 Runtime 统一提供：手势比例 `50%`，拖拽 `duration=1.5s`，滚动后等待 `1s` 再重新截图/OCR/签名。业务任务默认只声明滚动容器、方向和目标，不应在单个任务里临时覆盖 `ratio/duration/settle_seconds`。
- 如果某个窗口确实需要专项滚动参数，必须有标注备注、用户明确要求或真实运行证据支撑；否则应回到 Runtime 默认滚动入口。邮件、日常列表、拜谒、活动列表等都按同一默认口径理解。
- Runtime 已有通用默认等待、超时、重试和 settle 机制时，业务任务默认复用这些基础机制，不为单个流程重复声明局部 timeout。只有出现真实卡死、业务语义需要不同上限、或用户明确要求时，才新增专项参数；不要因为理论上“可能略超一轮”而制造更多配置和维护点。
- 对存在固定遮挡、半透明提示、顶部/底部浮层的滚动窗口，调用 `scroll_shape_content(..., recognition_shape="识别区")`。手势仍作用在滚动容器上，但滚动前后的签名和变更判断只看指定识别区，避免遮挡区域污染“是否滚动成功”的判断。
- 命中目标但靠近边缘、遮挡区或点击不安全时，应先把它当候选，再通过小幅复位、重新截图和候选复核处理；不要用改变通用滚动比例或缩短拖拽时间来绕过这个问题。
- 候选框已经命中但靠近识别区边缘时，调用 `nudge_shape_content_for_box(...)` 做小比例复位。该动作是候选后处理，不替代默认滚动查找；复位后必须重新截图/OCR 复核目标仍是同一个业务对象。
- 列表滚动后不要立即对当前帧计算签名；先固定等待 1 秒，让拖拽惯性和复位动画结束。这里不再做“等待到稳定”的递归判断，避免稳定判断本身依赖哈希而形成循环。
- 滚动到底的主要判据是：一次滚动并等待后，可见列表签名与上一轮滚动后的签名相同。连续空滚动次数只能作为异常兜底，不应作为正常完成逻辑。
- 资产树里的「遮挡」分组表示固定排除区域，不表示固定遮挡内容。这些区域经常出现不同通知文字、公告条或临时浮层，因此其中 OCR/图像内容不能进入列表哈希或滚动签名。
- 计算列表签名时，应把「遮挡」分组下所有 shape 转换为当前帧坐标屏蔽框；OCR 行中心落在屏蔽框内时直接跳过。

### 闭环幂等与浮动模板

滚动清单里的任务项应优先建模为“父条目 + 子控件”的模板，而不是为每个滚动位置保存一张专用帧。具体经验见 [凡修 data-annotation 闭环与浮动模板案例](./凡修data-annotation闭环与浮动模板案例.md)。

- 场景身份只使用稳定页头、外框或固定控件；滚动窗口内容不能作为场景锚点。
- 运行时先确认当前场景，再在滚动容器中用 OCR 找父标题，最后用模板里的子控件相对偏移计算点击点。
- 点击后必须等待明确结果帧、无事项帧或原场景超时保底；只发出点击不能算闭环。

### OCR 结果层级

- Runtime 默认 OCR helper 返回 line 级结果，适合场景文本判断、列表标题查找、进度读取和普通按钮定位。
- 需要按单个词或字计算点击点时，使用 `ocr_words_in_shapes(view, shape_titles, options=...)`。该 helper 会在指定 shape 裁剪区内启用 `return_word_box`，并把裁剪坐标恢复到整屏坐标。
- `return_word_box`、`text_det_thresh`、`text_det_box_thresh`、`text_det_unclip_ratio` 等 PaddleOCR 参数只能通过 OCR 服务白名单逐次透传；不要全局修改 OCR 默认配置。
- 任务代码应先限制识别区，再启用精细 OCR。没有 word box 或目标未拆开时，可以退回 line 级子串估算，但必须在日志里保留原始 OCR 文本和计算出的点击点，方便复盘合并误差。

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
- 行为树总开关、守护开关状态

行为树是 Runtime 的常驻内核服务，不应由 CodeYun 页面或 Scheduler 生命周期拥有。页面进入当前 `entry_id`、本地脚本提交任务、后端调度到期时都可以确保服务 loop 存在；关闭工程作业组或暂停自动调度只阻止 CodeYun 常驻 Scheduler 自主提交到期作业，不停止内核服务，也不阻止用户/AI 通过 `task cell` 继续串行提交任务。

守护不是独立线程。守护开关只是常驻行为树里的前置检查节点配置：开启后每轮空闲 tick 或业务任务 tick 都会先检查守护，关闭后该节点直接跳过。没有任务时，常驻服务可以做低频空闲复原；这仍属于同一个行为树 loop，不是另起一个守护服务。

空闲复原是行为树内建自维护：当没有运行任务、没有待消费 task cell、没有到期自动作业时，resident loop 最多每 5 分钟运行一次完整但有上限的 recovery。该 recovery 复用 `device_health` 和 `close_popups` 两个守护项，先处理设备健康，再多轮关闭已标注弹窗，直到守护跳过或达到上限；它不启动业务作业，也不新增独立设备心跳。

守护 tick 的职责边界：

- 优先处理独立弹窗、遮挡帧和已标注的全局干扰项。
- 只使用资产树里的场景标识和动作 shape。
- `弹窗` 分组第一层图片里的 `空白` 表示背景关闭区，可直接作为关闭动作使用，不要求填写 `sceneJumpTarget`。同一弹窗有多个关闭候选时优先点 `空白`：显式关闭按钮可能太小，`确定` 可能触发跳转或领取等业务行为；背景取消通常更大、更稳、副作用更少。
- 对 `sceneJumpTarget=-1` 的 shape，可视为独立弹窗关闭动作。
- 对无自动动作的遮挡帧，只记录发现，不猜测点击。
- 对“万灵切磋邀请”这类特殊守护任务，必须先在资产树里补足识别 shape 和动作 shape，Runtime 才能稳定处理。

## Scheduler

Scheduler 不直接点击游戏，只负责维护作业配置、到期判断和只读计划。真正执行由常驻行为树在空闲 tick 中读取 Scheduler 结果后串行提交给 Runtime 业务节点。

### 工程 Scheduler 与 AI 保底调度

凡修每日任务当前有两个触发主体，语义必须分开：

- 工程 Scheduler：CodeYun 后端常驻 loop 根据 Scheduler 计划自主提交到期作业。`job_group_enabled=false` 只关闭这一条工程自主入口，用来避免后端在用户或 AI 正在操作时自行启动作业，造成行为树并发冲突。
- AI 保底调度：Codex/AI 定时巡检 `doctor` 结果，发现已有启用且到期的作业时，作为人工代理按顺序提交一个作业。AI 保底不应把 `job_group_enabled=false` 理解为“不运行”；它应理解为“工程入口已让出调度权，由 AI 串行接管”。

因此，作业组关闭时：

- `scheduler.plan.next_action=job_group_disabled` 仍可以保留，用来说明工程 Scheduler 不会自主拉起任务。
- AI 保底若看到 `due_tasks` 非空、`maintenance.automation_safe=true`、没有 `needs_human_annotation`、没有 `blocked_by`、没有 Runtime/owner/manual_jobs 正在运行，应继续按 `due_tasks` 顺序执行第一个作业。
- AI 保底执行时不能修改 `job_group_enabled`、不能改写启用状态、不能手工推进 `next_time`。它只能通过 Runtime/行为树公开入口提交具体 `task_type`，让任务成功/失败后按正常 Scheduler 状态回写 `last_run_at`、`last_result`、`next_time` 或 `retry_after`。
- AI 保底和工程 Scheduler 一样必须遵守行为树串行约束：一次只运行一个任务；若已有 owner、Runtime 任务、manual_jobs 或隔离锁正在占用，应等待/跳过本轮并报告串行互斥，不得并行启动。
- 标注缺失、阻断浮层、Runtime 错误、ADB/MuMu 基础设施异常仍是真阻断。AI 可以修基础设施，但不能靠猜坐标、降阈值、自动补资产树来强行执行。

AI 心跳的首选入口是：

```bash
uv run python scripts/fanxiu_bt.py watch-doctor --max-iterations 1 --auto-run-due
```

该入口内部会先 doctor，确认安全后以 `ignore_job_group_disabled=true` 接管到期作业；每提交一个作业后重新 doctor，继续处理下一个安全到期作业，直到 `due_tasks` 清空或出现真实阻塞。心跳提示词不应再把 `job_group_disabled` 写成跳过条件，也不应绕过该入口手工篡改 Scheduler 数据。

### AI 保底成功判据

AI 保底调度不能只用 Scheduler 的 `last_result=success` 或 Runtime 任务结束作为用户汇报的唯一成功证据。凡修日常任务必须区分三层结果：

- 调度成功：AI/工程入口按时提交了某个 Scheduler task。
- 行为闭环成功：Runtime 找到对应页面、点击了标注动作，并处理了已知弹窗或结果页。
- 业务完成成功：真实游戏画面中的任务计数、剩余次数、奖励状态或目标场景已经符合该作业的业务完成条件。

其中只有第三层才能对用户说“今天这个作业完成了”。新版 `日常_助手` 的小助手项闭环细则是二级专项规则，见 [凡修data-annotation闭环与浮动模板案例](./凡修data-annotation闭环与浮动模板案例.md)；它已经覆盖 `日常_游历`、`日常_妖王来袭`、`日常_妖族袭城`、`日常_灵祖`、`日常_灵塔`、`日常_双修`，这些不再设计独立 Scheduler 作业。未明确纳入新版小助手的能力，例如 `日常_每日副本`，仍按独立日常作业理解。

日常作业按幂等任务理解：目标不是“本轮必须实际点击一次领取、挑战或购买”，而是“确认今天已经没有可领取、可挑战、可使用次数或可执行事项”。因此，重复运行时如果发现目标项已不存在、免费项已不可见、剩余次数为 0、显示已领取/已完成，或进入非目标详情后能安全返回原列表，都应作为业务完成证据写成功并推进到下一天；不要把“本轮没有实际领到/打到”直接当成错误或重试。真正的错误是无法确认状态、无法安全返回、缺少关键标注或进入未知高风险状态。

当用户截图显示日常页仍有 `0/x` 计数时，应以真实画面为准，反查对应 Scheduler task 是否启用、是否到期、是否被误报成功；不得用“日志里 success”否定游戏画面。

AI 保底和人工复盘可使用只读作业 `daily_audit` 做日常总账复核：Runtime 先进入 `#69 日常`，只遍历已标注的 `滚动窗口`，读取每个任务块的标题和最后一个 `当前/总次数`，再把可确定的条目映射回 Scheduler task。该复核只作为“未完成反证”：如果复核时间晚于某个 Scheduler task 的 `last_run_at`，且日常页显示该任务次数未满，Scheduler plan 应重新把该任务视为到期候选；如果复核时间早于任务最后运行时间，则不能用旧画面否定新结果。未能映射的未完成条目应作为 `unmapped_incomplete` 报告，等待补 Scheduler 能力或映射规则，不能强行归到相似任务上。

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
- `schedule_kind=weekly` 表示每周定时任务，按 `weekdays`（周一为 0）和 `schedule_times` 生成下一次 `next_time`。
- 尚未迁移的旧任务使用 `legacy_daily_task` / `legacy_dynamic_task` 占位，但不允许作为 Runtime 任务启动。
- 未验收任务不应在 Runtime 中保留可调用执行函数；需要迁移时先补齐资产树/shape 验证，再新增对应执行函数和测试。
- 当前默认清单只把旧版任务导入为占位，不默认启用具体日常任务。具体迁移必须等对应资产树和 shape 数据确认充分后再逐项打开。
- `run-due` 只启动已支持的到期任务；旧任务占位即使被手动启用，也不会作为可执行任务启动。
- `run-now` 对未纳入框架验收的任务直接返回 400，避免误触发未确认链路。

这个设计的目的不是继续固定死行为树，而是保留旧版成熟任务目录，同时允许人工随时通过 Scheduler 或 task cell 发送单个任务，例如临时收到礼包码后执行 `gift_code_redeem`。

## 运维入口优先级

如果问题本质是“凡修行为树服务是否在跑、要不要重启、当前 owner/队列/隔离/doctor 怎么样”，默认不要先从任务提交或 Scheduler 业务入口下手，而是按下面顺序：

1. 先读 [`行为树运行单元运维约定`](./行为树运行单元运维约定.md)
2. 再用统一 runtime item action：
   - `trigger`：`ensure resident service`
   - `inspect`：读取 runtime/owner/manual_job/isolation/doctor 摘要
   - `restart`：`shutdown_service -> ensure service`
   - `wake`：只唤醒 resident loop 重新轮询
3. 只有在明确处理中断当前业务任务、提交 task cell、执行 cell tick 或调度具体 Scheduler task 时，才进入 Runtime/Scheduler 业务接口。

边界：

- `/data-annotation/runtime/task/stop` 不是停机接口，只是“停止当前业务任务”。
- 旧 task tick 路由已移除；单步推进使用 `/data-annotation/runtime/cell/tick`。
- `scheduler/task/run-now` 不是行为树运维入口，它的职责是提交某个具体任务实例。

调度规则：

- 常驻行为树每轮按 `守护 -> task cell -> 自动作业` 的顺序检查。
- 临时执行来自用户、工程 Scheduler 或 AI Scheduler 提交的 kernel cell。正式任务使用 `/data-annotation/runtime/cells/task`，AI/开发探针使用 `/data-annotation/runtime/cells/code`；它们进入 kernel 受控队列，不代表启动行为树服务。
- 自动作业只拉取非 `schedule_kind=manual` 的到期任务，避免用户手动任务被空转 loop 反复执行。
- 工程自动作业受 `job_group_enabled` 约束；AI 保底调度在确认串行安全后可以接管这些已到期任务，但仍要把执行记录落回对应 Scheduler task。
- Runtime 空闲时，常驻服务可以启动下一个任务。
- Runtime 正在运行时，Scheduler 不抢占当前任务，也不把同组新任务标记为 `queued`；这些任务只作为候选保留，等当前任务完成并释放控制权后，由下一轮 tick 重新读取最新计划并选择一个任务启动。
- 每轮自动作业调度只启动一个到期任务。即使同时有多个任务到期，也必须等当前任务 `SUCCESS / FAILURE / STOP` 后，下一轮再判断下一个任务，避免在业务层形成“一心二用”的隐式队列。
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

这里的 #49、#78、#81、#82 和按钮位置都来自资产树标注。

### 开发调试代码 cell

`runtime/cells/code` 是通用临时代码执行入口；底层复用 `debug_eval` 执行能力，但调用方不应再把 `task_type=debug_eval` 当成首选 API。它不是邮件、日常或某个具体业务的专用接口。

它用于让 agent/开发者在真实 Runtime 上下文中临时运行一段 Python：

- 默认 `mode=readonly`，允许截图、场景识别、OCR、读取标注和记录日志。
- 需要点击、拖拽等真实动作时必须显式传 `mode=act`。
- 代码里自动注入 `ctx`，常用能力包括 `ctx.frame()`、`ctx.scene()`、`ctx.ocr()`、`ctx.ocr_words_in_shapes()`、`ctx.image()`、`ctx.shape()`、`ctx.shape_score()`、`ctx.shape_probe()`、`ctx.wait_click()`、`ctx.wait_scene()`、`ctx.go_scene()`、`ctx.wait_view()`、`ctx.wait_click_then_view()`、`ctx.tap_shape()`、`ctx.tap()`、`ctx.drag()`、`ctx.log()`。
- `ctx.ocr_words_in_shapes(scene, shape_titles, options=...)` 是只读 word box OCR 探针，用于在指定标注区域内启用 `return_word_box` 等 per-call 参数，验证精细点击所需的词框坐标。
- `ctx.shape_score(scene, shape)` 是只读相似度探针；`ctx.shape_probe(scene, shape)` 会按点击前匹配口径返回每个 condition 的 `similarity/matched`、`scene_threshold/overlay_threshold` 和关键 shape 配置。它们适合在真实当前帧上复查某个 shape 是否满足点击前匹配条件；分数不足时应修标或调参，不应在代码 cell 中改成固定坐标硬点。
- `ctx.wait_scene(*scenes)` 是动作模式能力，用于等待目标场景出现；`ctx.wait_view(*views)` 仅作为历史兼容名保留。
- `ctx.go_scene(scene)` 是动作模式能力，用于通过通用场景移动进入目标场景；`ctx.goto_view(...)` 不作为新调试代码示例继续扩散。
- `ctx.wait_click_then_view(source, shape, *targets)` 是动作模式能力，用于通过公开 code cell / task cell 验证局部转场 helper；它仍会尊重 `wait_click` 的点击前匹配条件。
- 支持两种写法：直接执行短代码，或定义 `def task(ctx): ...`，系统会自动调用；`task` 返回生成器时继续沿用现有 yield/tick 机制。
- 调用入口使用 `POST /data-annotation/runtime/cells/code` 或 core `runtime_framework.submit_code_cell(...)`；不要新建第二套调度或后台线程，也不要私有直调 runner。

只读探针示例：

```json
{
  "entry_id": "30b82d72-8a76-4a74-be4b-4fc1591c6ce2",
  "mode": "readonly",
  "timeout_seconds": 60,
  "code": "info = ctx.scene(); ctx.log({'scene': info}); result = info"
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
  "entry_id": "30b82d72-8a76-4a74-be4b-4fc1591c6ce2",
  "mode": "act",
  "code": "ctx.tap_shape(121, '返回')"
}
```

动作模式只能在明确需要改变游戏状态时使用。邮件、领取、删除等高风险流程仍应优先用 `readonly` 先列出决策表，再提交 `act` 代码或沉淀为正式注册作业。

## 前端

前端页面中的正式入口应称为“任务调试台”。

允许保留的前端能力：

- 资产树编辑
- shape 标注
- 图片对比
- 抠图/检测
- 单步 Runtime tick
- 提交 task/code cell 与停止当前业务任务
- 启停守护
- 查看 Runtime/Scheduler 写入的 `WorldFacts`
- 查看 Scheduler 后端生成的只读 `plan`
- Scheduler 任务清单与手动触发

不应继续作为正式执行框架的能力：

- 前端长流程任务状态机
- 前端循环等待并决策下一步
- 前端持久化任务执行状态作为正式任务源

旧前端任务执行日志接口和 `/api/fanxiu/data-annotation/gift-code-task/*` 已移除。新代码统一使用 `/runtime/*` 和 `/scheduler/*`。

## 当前落地状态

已完成：

- 后端 Runtime 单实例执行器。
- 后端 Scheduler 任务清单、到期过滤、手动任务、执行到期任务。
- Runtime 守护开关，前端可显式启停。
- `WorldFacts` 稳定分层为 `runtime`、`guard`、`discoveries`、`events`，Runtime 和 Scheduler 都只写自己的事实，不共同覆盖核心状态。
- `GET /api/fanxiu/data-annotation/runtime/world-facts` 只读暴露事实文件，任务调试台可直接查看。
- `GET /api/fanxiu/data-annotation/scheduler/plan` 只读暴露 Scheduler 规划结果。
- Scheduler 可从 `WorldFacts.discoveries.task` 回写动态任务 `next_time` 和任务 `retry_after`。
- 前端“任务调试台”只负责提交、查看和调试行为树任务，不承载正式任务状态机。
- 当前框架验收入口包含 `gift_code_redeem`、通用 `go_scene` 场景移动、`hide_floating_window`。
- 旧版动态任务和每日任务目录接入 Scheduler。
- 旧版任务当前使用 `legacy_daily_task` / `legacy_dynamic_task` 占位，不进入 Runtime 执行入口。
- 未验收任务即使被外部状态写成 `enabled=true`，读取任务清单时也会强制改回 `enabled=false` 并标记 `last_result=unsupported`。
- 未验收的日常/报名/游历执行函数已从 Runtime 中移除，避免误认为已迁移。
- 前端正式长流程任务状态机和旧任务执行日志接口已移除。

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
- 场景身份识别不能用全屏 scan 兜底。候选组可以并行打分，但必须按候选顺序返回第一个过阈值的显式场景，不能让低优先级弱锚点或全图相似素材用最高分抢结果。像“日程”这种也会出现在世界页入口上的元素不能单独作为日程页身份锚点。
- 不要手动点游戏、直接改状态文件或只调用底层函数来冒充行为树验收；只有用户明确要求底层探针时，才把结论限定为底层探针结果。

已加测试：

- 默认 Scheduler 任务目录包含旧版行为树每日/动态任务。
- 旧状态文件会被默认结构修正，不会保留错误的 task_type/source/schedule_kind。
- 每日任务执行后会推进到下一次 `schedule_times`。
- `enabled`、`next_time`、`retry_after` 的到期判断。
- Runtime 忙碌时，Scheduler 不会改写同组任务状态；到期任务保留为候选，等待后续空闲 tick 重新选择。
- Runtime task dispatch 使用后端正式任务入口。
- Runtime 状态新增 `service_running`，表示常驻行为树 loop 是否存在；`running` 只表示当前是否有业务任务在执行。
- runtime 页面不再自动开启“关闭弹窗”守护来制造运行状态。进入页面只确保常驻服务存在，守护/作业开关完全由页面配置决定。
- CodeYun 后端启动或热重载后，如果当前机器启用了 `fanxiu-behavior-tree` 内置服务，会自动 ensure data-annotation resident loop，恢复持久化的 entry、守护配置、日志和空转调度能力。API 入口仍只负责投递任务、修改配置或唤醒 resident loop。
- Scheduler 任务成功/错误后的 `next_time` / `retry_after` 更新。
- Runtime/Guard 事实写入不会覆盖历史发现和事件。
- Scheduler 任务状态变化会写入 `discoveries.task` 和事件流。
- Scheduler 会从 `WorldFacts.discoveries.task` 同步 `next_time`、`retry_after`、`last_run_at`、`last_result`。
- Scheduler 任务清单 PUT 会保留后端运行结果字段，避免前端旧整表状态覆盖刚执行完的 `日常_报名` 次日触发时间。
- Runtime 会递归读取父帧/子帧，并把子帧中的独立弹窗/遮挡纳入守护候选。
- 未验收 legacy 任务不能被状态文件或 PUT 请求误启用。
- `go_scene` 迁移期仍支持嵌套子场景的 `离开/返回/关闭` 空目标隐式返回父场景标题对应的同名图片；例如“世界”分组下的 `#85 某区域内部` 可规划回 `#34 世界`。这属于旧标注兼容，不代表资产分组天然决定 sub-scene 识别关系。
- `go_scene` 等待阶段会把“离开场景”确认弹窗视为中间态，识别到 `确认/确定` 后自动点击并继续等待原目标；该确认弹窗不能被学习成原按钮的最终 `sceneJumpTarget`。
- 场景身份默认阈值按 80 执行；`#85[离开]` 这类小图标在世界画面中 69% 的弱相似度不能算命中。真实世界场景当前补了 `#34[大地图当前入口]` OCR 身份锚点，用于兼容实际画面与旧 #34 截图不同的状态。

### 历史验收记录

以下记录保留当时真实验收事实，不代表现行推荐入口。现行任务入口是 `/data-annotation/runtime/cells/task`，临时代码入口是 `/data-annotation/runtime/cells/code`。

- 2026-06-04 真实验收：从当前 MuMu/ADB 画面通过当时的旧任务提交入口执行 `go_scene #34`，最终 `success/done/current_scene=34`，耗时约 6 秒；证据截图保存在 `.codex_tmp/fanxiu_go_scene_34_final_20260604_012723/`。后续修复守护 tick 语义后再次真实验收，仍为 `success/done/current_scene=34`，耗时约 6 秒；证据截图保存在 `.codex_tmp/fanxiu_go_scene_34_after_guard_fix_20260604_015404/`。
- 2026-06-04 常驻行为树改造后真实验收：状态接口可拉起常驻服务并恢复守护配置，状态为 `service_running=true`、`running=false`、`guard_enabled=true`、`guard_running=true`；随后通过当时的旧任务提交入口提交 `go_scene #34`，入口返回“手动作业已提交”，最终 `success/done/current_scene=34`，截图保存在 `.codex_tmp/fanxiu_resident_runtime_20260604/`。再次重载后状态仍能恢复为 `service_running=true` 且空转识别 `#34 world 100%`。
- 2026-06-04 任务提交入口收敛后真实验收：当时的旧任务提交入口不再直接启动业务线程，而是写入内部队列并唤醒常驻服务；常驻 loop 串行消费 `manual-1780522415517-4061817e` 后执行生成器式 `go_scene #34`，最终 `success/done/current_scene=34`，耗时约 6.7 秒，真实截图保存在 `.codex_tmp/fanxiu_manual_direct_generator_20260604/`。
- 2026-06-04 场景身份 role 语义修复后真实验收：在真实日程页通过当时的旧任务提交入口执行 `go_scene #34`，Runtime 识别 `#66 -> #34`，点击 `返回`，跳转耗时 `2.3s`，最终 `success/done/current_scene=34`；证据截图保存在 `.codex_tmp/fanxiu_scene_role_fix_66_to_34_final_20260604/`。这次不是“已在目标场景”的短路命中。
- 2026-06-04 去掉手动作业 drain 线程和 `run-now` 直跑旧 pending job 后真实验收：通过当时的旧 tick 入口提交 `manual_tick`，resident loop 消费 `manual-1780525981456-8e88286b`，最终 `success/done/current_scene=34`，`manual_jobs.json=[]`，证据保存在 `.codex_tmp/fanxiu_resident_no_drain_manual_tick_20260604/`。
- 2026-06-04 手动作业并入 resident service 线程后真实验收：通过当时的旧 tick 入口提交 `manual_tick`，resident loop 直接执行 `manual-1780526898389-9e4c1ab5`，不再创建 `fanxiu-data-annotation-manual-job` 业务线程，最终 `success/done/current_scene=34`，`manual_jobs.json=[]`，证据保存在 `.codex_tmp/fanxiu_manual_inline_resident_tick_20260604/`。
- 2026-06-04 Scheduler 到期任务并入 resident service 线程：`start_scheduler_tasks` 不再创建 `fanxiu-data-annotation-scheduler` 业务线程，resident loop 空闲时直接串行执行到期任务；单元回归已覆盖 Runtime 不再拥有 `_thread` 字段。真实验收临时追加 `codex-temp-go-world` 到期任务，执行 Scheduler run-due 后最终 `scheduler_run_due/success/current_scene=34`，证据保存在 `.codex_tmp/fanxiu_scheduler_inline_run_due_20260604/`，验收后已恢复原 Scheduler 配置。
- 2026-06-04 direct generic/gift-code 过渡入口已移除：直接 Runtime 任务统一并入 resident service，不再保留 `start_generic_runtime_task` 和旧 `start()`；Runtime 后台线程只保留 resident service。
- 2026-06-04 移除 `_thread` 字段后真实验收：通过当时的旧 tick 入口提交 `manual_tick`，resident service 消费 `manual-1780528737905-a0f24121`，最终 `success/done/current_scene=34`，`manual_jobs.json=[]`，证据保存在 `.codex_tmp/fanxiu_no_runtime_thread_manual_tick_20260604/`。
- 2026-06-04 移除旧直启入口和无调用 `_run` 后真实验收：通过当时的旧 tick 入口提交 `manual_tick`，resident service 消费 `manual-1780529858028-ce12bf62`，最终 `success/done/current_scene=34`，耗时约 31.1 秒，`manual_jobs.json=[]`，真实截图 `07_final_screencap.png` 保存在 `.codex_tmp/fanxiu_resident_after_entry_cleanup_20260604/`。
- 2026-06-04 stop 语义收窄后真实验收：先调用停止当前业务任务接口，返回 `idle/当前没有正在运行的任务/service_running=true`，证明 stop 不关闭 resident service；随后通过当时的旧 tick 入口提交 `manual_tick`，最终 `success/done/current_scene=34`，耗时约 8.4 秒，`manual_jobs.json=[]`，真实截图 `06_final_screencap.png` 保存在 `.codex_tmp/fanxiu_stop_current_task_semantics_20260604/`。
- 2026-06-04 相关回归：`uv run pytest backend\tests\test_fanxiu_data_annotation_runtime_guard.py tests\test_fanxiu_data_annotation_scheduler.py -q` 为 `71 passed`；`npm run typecheck --prefix frontend` 通过。

