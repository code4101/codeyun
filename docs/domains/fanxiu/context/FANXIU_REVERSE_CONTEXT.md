# 凡修逆向工作区入口

本页只帮助开发者找到稳定工作区和工程入口，不保存历次调查、运行数量、工具安装日志或当前页面状态。

## 稳定边界

| 用途 | 路径 |
|---|---|
| 逆向根目录 | `D:\home\chenkunze\data\m2606凡修逆向` |
| 游戏文件 | `D:\home\chenkunze\data\m2606凡修逆向\frxx_game_files` |
| 分析输出 | `D:\home\chenkunze\data\m2606凡修逆向\frxx_analysis_exports` |

TapTap/MuMu 安装和虚拟机目录是易变输入来源，不是长期输出目录。临时探针和诊断证据进入 `%TEMP%\codeyun\...`。

## 工程入口

- 静态资源、图鉴和版本适配：`backend/core/fanxiu/catalog/` 及相关脚本、测试。
- 只读运行态：`backend/core/fanxiu/instrumentation/`。
- 抓包、协议事实和历史研究：以 `backend/core/fanxiu/` 当前包结构及测试检索，不依赖本文中的旧模块清单。
- 前端页面和 API：从路由注册表、OpenAPI 与 `frontend/src/standard/fanxiu/` 查询。

## 接手顺序

1. 读取 [凡修 skill](../../../../../skills/fanxiu/SKILL.md) 和当前问题所属能力层。
2. 用 manifest/verifier 确认本机数据根与输入版本。
3. 从代码、测试和数据库读取当前事实，不从旧报告数字推断。
4. 未知版本或 schema 先失败关闭并保留最小证据；解决后回收进 parser、profile、fixture 和测试。

旧调查过程可从 Git 历史和备份追溯，不再追加到本文件。
