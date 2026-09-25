"""凡修自动化：业务表达意图，公共能力负责实现，调度持有运行权。

阅读代码先沿正式 Task 的调用链，不从 GUI 点击或 Runtime 字段拼业务：

* data_annotation.tasks：业务目标、准入、流程组合、完成判据。
* activity：活动实例、时程、资源策略与持久业务事实。
* runtime_gui / instrumentation / catalog：界面对齐、实时事实和静态规则。
* data_annotation 中的识别、导航及资产模块：游戏交互接口。
* behavior_tree 与 kernel_scheduler_*：Cell、Kernel、派发与运行权。

data_annotation 是历史目录名，不能据此把任务和调度视为标注子功能。
新增共享流程应提供业务语义入口；活动差异由适配器承接，纯解析/策略
不得依赖执行器。接口附近说明输入、终态、副作用和差异扩展点；诊断
保留第一现场，但不要求上层理解内部操作才能正确调用。
"""
