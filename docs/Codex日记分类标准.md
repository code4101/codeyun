# Codex 日记分类标准

本文档记录用户确认过的每日总结分类边界。每条经验需要同步沉淀到
`tests/fixtures/codex_diary_category_cases.jsonl`，作为分类回归测试样本。

## 凡修

凡修分类覆盖《凡修》手游相关的数据、资源、运行时、行为树、任务闭环、抓包、
GUI 自动化和业务条目维护。只要工作对象是凡修手游或凡修自动化链路，即使代码
修改发生在 CodeYun 仓库内，也应归为凡修，而不是 CodeYun/综合。

强信号包括：

- 明确出现“凡修”。
- 游戏业务词：洞天、福地、洞天福地、祈愿、炼丹、淬体、灵兽、妖王、仙花、仙府、宗城、法宝、道具、仙舟、衣橱等。
- 自动化与运行语境：`daily_foundation.py`、`mail.py`、日常基础、日常任务、领取、返回闭环、稳定回归锚点、场景编号、目标场景、世界步骤、抓包巡检、`packet_worker`、`pcap`。
- 用户反馈确认：凡修手游里的洞天任务，以及围绕该任务的功能开发，都属于凡修。

不要因为以下因素改判为 CodeYun/综合：

- `project_label` 是 `codeyun`。
- 修改文件位于 CodeYun 仓库。
- 内容里出现“修复、实现、验证、闭环”等通用工程词。

已确认样本：

- `fanxiu-dongtian-return-loop-20260628`：洞天福地返回闭环，误分为 CodeYun/综合，标准分类为凡修。

## CodeYun/综合

CodeYun/综合覆盖 CodeYun 自身的系统级治理、跨模块根因修复、自动化规范、运行时策略、
缓存/资源链路保护，以及不专属于“笔记、资源、集群”等细分 CodeYun 子类的工程治理工作。

强信号包括：

- `daily-thread`、`automation-daily-thread`、`codex-automation-management`。
- 系统级治理、全局行为治理、根因修复。
- runtime 点击策略、标注点击 helper、中心点 fallback。
- `asset-tree`、前端缓存、保存 shape、失败提示、保护逻辑。

不要因为以下词汇把它误判为“缺陷”：

- 失败、问题、风险、缺失、修复、排查。

这些词只是工程治理过程中的现象描述。只有当记录主体是一个具体 bug/缺陷条目本身，
而不是 CodeYun 系统治理成果时，才归“缺陷”。

已确认样本：

- `codeyun-general-daily-thread-root-cause-20260628`：daily-thread 根因修复，误分为缺陷，标准分类为 CodeYun/综合。
