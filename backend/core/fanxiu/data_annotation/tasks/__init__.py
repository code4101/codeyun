"""具体玩法的 Task 实现与组合能力。

复用入口从 data_annotation.jobs 的注册定义查找；default_jobs 把任务类型
映射到本目录中的实现，文件名和 Mixin 方法名不是另一套任务标识。
Mixin 由 BehaviorTreeExecutor 组合，不单独构造为游戏执行器；其内部
通过 BehaviorTreeContext 使用识别、导航和 Shape 操作。模块导入不应
触发游戏读取或动作。纯解析/策略放在可独立调用的领域函数中。
"""
