# 凡修 data-annotation Runtime 与 Scheduler 契约

## Runtime

Runtime 提供 scene 识别、Shape 解析、公共动作、行为树上下文、Kernel 状态和可观测结果。业务通过公开上下文调用能力，不私有直调 Runner。

公开语义包括：

- `wait_scene()` / 兼容 `wait_view()`
- `go_scene()`
- `wait_click()`
- 当前 scene、动作日志、Job 终态和必要诊断

精确 Python 签名和返回类型以源码与类型测试为准。

## Scheduler

Scheduler 位于 Kernel 外，只负责：

- 根据 `next_time` 和优先级选择到期 Job；
- 领取 attempt 并以 CAS 写回终态；
- 仲裁唯一 Kernel 和模拟器运行权；
- 按静态配置处理技术错误重试。

Scheduler 不读取 OCR、scene、业务消息或 `job_status`，也不保存业务步骤。

## 作业定义

- 作业类型由代码注册表定义；实例配置保存在本地数据层。
- 新作业类型默认不自动加入或启用当前机器清单。
- 顶层 Job 对外可调度；子 Task 和公共动作不成为平级作业。
- 注册表是作业身份和前端目录的事实源，不维护手写 Markdown 清单。

## 验收

运行态页面是观测和控制入口，不是第二执行器。测试需覆盖注册、attempt、终态、interrupt、时间写回和权限；真实 GUI 动作需另做设备验收。
