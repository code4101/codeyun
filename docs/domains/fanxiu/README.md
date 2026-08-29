# 凡修实现索引

凡修的总体架构、判断规则和操作边界统一由 [fanxiu skill](../../../../skills/fanxiu/SKILL.md) 管理；唯一主干是 [《凡修手游自动化系统》](../../../../skills/fanxiu/references/凡修手游自动化系统.md)。

本目录只保存无法由代码自解释的最小实现契约、外部运行手册和仍被源码引用的证据入口。私有函数、字段、地址、算法步骤、运行数字、完成计划和复盘不在这里维护第二份正文。

## 接口层

- [动态插桩契约](architecture/凡修动态插桩底座.md)
- [逆向增量更新](architecture/凡修逆向增量更新约定.md)
- [逆向资源安全边界](architecture/凡修逆向资源安全边界.md)
- [抓包服务契约](architecture/凡修抓包服务架构约定.md)
- [抓包业务写入契约](architecture/凡修抓包业务数据落库设计.md)
- [GUI 场景图契约](architecture/凡修GUI场景地图与图模型约定.md)
- [data-annotation 术语](architecture/凡修data-annotation命名约定.md)
- [逆向工作区入口](context/FANXIU_REVERSE_CONTEXT.md)

## 调度层

- [Kernel 与运行权](architecture/凡修行为树运行框架约定.md)
- [Runtime 与 Scheduler API](architecture/凡修data-annotation自动化Runtime与Scheduler.md)
- [Job 状态与时间](architecture/fanxiu-job-scheduling-semantics.md)
- [行为树公共能力](architecture/凡修行为树业务能力约定.md)

## 被源码引用的业务契约

- [红包作业](jobs/凡修红包作业.md)
- [洞天行动力](jobs/凡修洞天行动力作业.md)
- [活动答题](jobs/凡修活动答题作业.md)
- [兽魂快捷合成](jobs/凡修兽魂研究与自动配置.md)

## 外部运行与未闭环入口

- [data-annotation 运行设备](runbooks/凡修data-annotation运行设备约定.md)
- [MuMu 异常恢复](runbooks/凡修MuMu模拟器异常恢复手册.md)
- [拜谒与行为树遗留验证](plans/凡修拜谒与行为树基础设施任务清单.md)

## 权威性

1. 代码、类型、注册表和测试是当前实现事实。
2. 本目录只解释公开契约和代码无法表达的外部操作。
3. skill 负责稳定判断，不复制当前实现。
4. Git 历史承担旧方案追溯；已完成计划和运行快照不继续留在导航中。
