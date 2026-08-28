# 凡修文档地图

凡修文档以 `skills/fanxiu/references/凡修手游自动化系统.md` 为主干核心。它定义：

- 空间维度：接口层、调度层、业务层。
- 时间维度：研发、应用、反馈、再研发。

本目录的首层文件夹继续表达文档性质：`architecture` 是当前权威事实，`guides` 是操作方法，`jobs` 是具体作业，`research` 是探索证据，`plans` 是未完成工作，`runbooks` 是运维流程，`context` 是长周期接手上下文。下面的阅读地图按主干核心重新分组；文档性质与空间层次是两个维度，不互相替代。

## 主干核心

- 总体架构：`D:\home\chenkunze\slns\skills\fanxiu\references\凡修手游自动化系统.md`
- 凡修 L1 路由：`D:\home\chenkunze\slns\skills\fanxiu\SKILL.md`
- 凡修 L2 能力树：`D:\home\chenkunze\slns\skills\fanxiu\references\README.md`

跨层设计、新能力归属或规则看似矛盾时，先回到总体架构确定所有者，再进入下列分支。

## 1. 接口层

接口层负责通过逆向与 GUI 构造稳定业务语义接口，回答“系统如何读取事实、如何操作游戏、如何验证结果”。

### 1.1 逆向接入与 Runtime

当前架构：

- [动态插桩底座](./architecture/凡修动态插桩底座.md)
- [逆向增量更新约定](./architecture/凡修逆向增量更新约定.md)
- [逆向资源安全边界](./architecture/凡修逆向资源安全边界.md)
- [抓包服务架构约定](./architecture/凡修抓包服务架构约定.md)
- [抓包独立服务重构设计](./architecture/凡修抓包独立服务重构设计.md)
- [抓包业务数据落库设计](./architecture/凡修抓包业务数据落库设计.md)

研发、反馈与上下文：

- [凡修逆向长周期上下文](./context/FANXIU_REVERSE_CONTEXT.md)
- [邮件抓包协议研究](./research/凡修邮件抓包协议研究.md)
- [运行态抓包观察事实层](./research/凡修运行态抓包观察事实层.md)
- [凡修信息窗](./research/凡修信息窗.md)
- [邮件抓包完整性任务清单](./plans/凡修邮件抓包完整性任务清单.md)
- [抓包退役与 Runtime 补齐清单](./plans/凡修抓包退役与Runtime补齐清单.md)

### 1.2 GUI 接入

当前架构与术语：

- [GUI 场景地图与图模型约定](./architecture/凡修GUI场景地图与图模型约定.md)
- [data-annotation 命名约定](./architecture/凡修data-annotation命名约定.md)

研发与操作方法：

- [data-annotation 自动探索标注数据](./guides/凡修data-annotation自动探索标注数据.md)
- [data-annotation 闭环与浮动模板案例](./guides/凡修data-annotation闭环与浮动模板案例.md)
- [重复槽位对齐标注方法](./guides/凡修重复槽位对齐标注方法.md)
- [data-annotation 运行设备约定](./runbooks/凡修data-annotation运行设备约定.md)

### 1.3 Runtime-GUI 协作

- [Runtime-GUI 对齐与兽渊研发计划](./plans/凡修Runtime-GUI对齐与兽渊研发计划.md)

具体对齐原则由 `fanxiu` skill 的“接口层 / Runtime-GUI 对齐”节点定义；项目文档只保存当前实现、计划和真实证据。

## 2. 调度层

调度层负责运行权、Kernel、Cell、顶层 Task 与 Task 内行为树，回答“何时运行、谁运行、一次运行如何组织”。

### 2.1 Kernel、Cell 与行为树 Runtime

- [行为树运行框架约定](./architecture/凡修行为树运行框架约定.md)
- [行为树业务能力约定](./architecture/凡修行为树业务能力约定.md)
- [data-annotation 自动化 Runtime 与 Scheduler](./architecture/凡修data-annotation自动化Runtime与Scheduler.md)
- [行为树 wait_click 极简接口计划](./plans/凡修行为树重构任务-wait_click极简接口.md)
- [拜谒与行为树基础设施任务清单](./plans/凡修拜谒与行为树基础设施任务清单.md)

### 2.2 Job、Scheduler 与运行状态

- [Job 运行与调度语义](./architecture/fanxiu-job-scheduling-semantics.md)
- [作业清单完整性约定](./architecture/凡修作业清单完整性约定.md)
- [新作业研究与上线指南](./guides/凡修新作业研究与上线指南.md)
- [日常经验 Job 契约](./research/fanxiu-daily-experience.md)

### 2.3 设备与运行恢复

- [MuMu 模拟器异常恢复手册](./runbooks/凡修MuMu模拟器异常恢复手册.md)

### 2.4 工程调度与 AI 调度交接

- [夜间自主研发总控](./plans/凡修夜间自主研发总控-20260813.md)
- [2026-08-27 凡修研发与运行复盘](./research/2026-08-27凡修研发与运行复盘.md)

这两类文档是调度层在研发、应用和反馈阶段的记录，不定义第四套调度模型。

## 3. 业务层

业务层负责玩法本体、业务准入、完成判据、资源与代币策略以及下一次运行意图。

### 3.0 共享业务模型

- [活动标准事实与兑换评估](./architecture/凡修活动标准事实与兑换评估.md)
- [玩家战力观察入库](./jobs/凡修玩家战力观察入库.md)

### 3.1 日常

- [洞天行动力作业](./jobs/凡修洞天行动力作业.md)
- [灵塔挑战作业](./jobs/凡修灵塔挑战作业.md)
- [天机阁有奖竞答作业](./jobs/凡修天机阁有奖竞答作业.md)

### 3.2 周常

当前尚无独立周常权威正文；具体周常规则保留在对应作业和计划中，形成稳定共同模型后再在本节点增加入口。

### 3.3 资源

- [兽魂研究与自动配置](./jobs/凡修兽魂研究与自动配置.md)
- [仙窍机制与可视化](./research/凡修仙窍机制与可视化.md)

### 3.4 日程：玩法榜、资源榜与小活动

共同架构：

- [统一榜单生命周期系统设计](./architecture/凡修统一榜单生命周期系统设计.md)
- [天地弈局功能逻辑](./architecture/凡修天地弈局功能逻辑.md)

具体玩法与事实：

- [魔道入侵自动化](./jobs/凡修魔道入侵自动化.md)
- [仙缘斗法标准作业](./jobs/凡修仙缘斗法标准作业.md)
- [云梦试剑页面实施清单](./jobs/云梦试剑页面实施清单.md)
- [云梦试剑运行态测量](./research/凡修云梦试剑运行态测量.md)
- [活动商店动态采集](./research/凡修活动商店动态采集.md)

### 3.5 大活动

- [活动答题作业](./jobs/凡修活动答题作业.md)
- [活动兑换规划](./jobs/凡修活动兑换规划.md)
- [天河仙会 UI 探索](./research/凡修天河仙会UI探索.md)
- [缘定三生冲榜策略](./research/凡修缘定三生冲榜策略.md)
- [周四秘藏双阶段研发计划](./plans/凡修周四秘藏双阶段研发计划.md)

## 横向生命周期的使用方式

每个空间节点都可能同时存在四类材料：

1. 【研发】`research`、`plans` 与探索型 `guides` 负责发现事实、验证假设和形成能力。
2. 【应用】`architecture`、已上线 `jobs` 与 `runbooks` 负责稳定运行。
3. 【反馈】真实日志、复盘和故障研究定位失效层与根因。
4. 【再研发】将反馈重新路由到接口层、调度层或业务层的具体所有者。

不要因为文档处于不同生命周期，就把它们解释成互相竞争的总体规则。判断两条规则是否冲突时，至少比较所属空间层、对象所有者、生命周期阶段和适用前置条件。

## 权威性与冲突处理

- 主干核心负责总体分层和跨层关系，保持单文件完整、默认只读。
- `architecture` 中明确声明为“唯一权威正文”的项目文件负责当前实现事实。
- skill 专项规范负责人机共同维护的判断边界；项目 docs 保存实现、证据、计划与历史。
- `jobs`、`guides`、`plans`、`research` 和 `context` 中的旧术语或旧方案不能覆盖当前架构正文。
- 看似冲突时先判断是否只是空间层、生命周期或对象不同；只有同一所有者、同一前置条件下要求相反动作，才属于真冲突。
