# 凡修抓包历史博物馆

本目录保存已经退出生产架构的抓包实现，仅供开发者在独立人工取证时显式调用。

硬边界：

- CodeYun 启动、Runtime 管理、凡修 Kernel、Scheduler、巡检、页面和业务 Job 禁止导入本目录。
- 当前业务状态只允许按需读取游戏 Runtime；Runtime 不完整时修复 Runtime 或安全重试。
- 本目录不得作为 Runtime 的兼容、降级或兜底数据源。
- 唯一保留入口要求人工确认：

  `uv run python -m backend.core.fanxiu.history_museum.packet_capture --i-understand-this-is-retired`

该命令不会被任何生产代码、配置项或守护进程调用。

除守护入口外，本目录还保留数字门、斗破、热更新、邮件、储物袋和玩家面板的旧研究实现，开发者只能通过本目录的完整模块路径人工导入；生产 API 不注册这些探针。
