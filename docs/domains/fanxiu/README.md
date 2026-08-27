# 凡修文档地图

凡修是 CodeYun 文档量最大的业务域。文档按用途拆分，避免把长期架构、标准作业、探索记录和阶段计划混为一体。

## 首要权威正文

开发或排查凡修功能时，按任务选择下列入口：

- Kernel、Cell、Runtime 与 Scheduler 边界：[凡修行为树运行框架约定](./architecture/凡修行为树运行框架约定.md)
- Job 状态与调度语义：[凡修 Job 运行与调度语义](./architecture/fanxiu-job-scheduling-semantics.md)
- GUI scene、识别图和移动模型：[凡修 GUI 场景地图与图模型约定](./architecture/凡修GUI场景地图与图模型约定.md)
- `scene/frame/view` 命名：[凡修 data-annotation 命名约定](./architecture/凡修data-annotation命名约定.md)
- 行为树业务能力复用：[凡修行为树业务能力约定](./architecture/凡修行为树业务能力约定.md)
- 抓包服务边界：[凡修抓包服务架构约定](./architecture/凡修抓包服务架构约定.md)
- 抓包业务数据入库：[凡修抓包业务数据落库设计](./architecture/凡修抓包业务数据落库设计.md)
- 逆向增量更新：[凡修逆向增量更新约定](./architecture/凡修逆向增量更新约定.md)
- 逆向资源安全边界：[凡修逆向资源安全边界](./architecture/凡修逆向资源安全边界.md)

## 其他分区

- [architecture](./architecture/)：当前架构、边界和强约定。
- [guides](./guides/)：研究新玩法、补充标注和上线作业的方法。
- [jobs](./jobs/)：具体可选作业的业务说明和验收口径。
- [runbooks](./runbooks/)：设备、MuMu 和运行环境排障。
- [research](./research/)：探索、测量、协议观察和策略研究，不直接定义当前实现。
- [plans](./plans/)：阶段任务、重构清单和未完成计划。
- [context](./context/)：供后续 Agent 接手的长周期逆向上下文。

## 冲突处理

`architecture` 中明确声明为“唯一权威正文”的文件优先级最高。`jobs`、`guides`、`plans`、`research` 和 `context` 如果保留了旧术语或旧方案，只能作为案例与历史证据，不能覆盖当前架构约定。
