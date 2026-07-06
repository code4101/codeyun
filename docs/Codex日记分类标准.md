# Codex 日记分类标准

本文档记录用户确认过的每日总结分类边界。每条经验需要同步沉淀到
`tests/fixtures/codex_diary_category_cases.jsonl`，作为分类回归测试样本。

## 样本口径

分类测试样本必须使用每日总结背后的原始对话数据：原始线程标题、用户请求、
助手结果、项目标签和工作目录。AI 生成的日记标题、日记摘要、已有分类都是输出结果，
其中可能包含错误，不能作为分类因果输入。它们只能作为定位字段或错误结果记录。

## 凡修

凡修分类覆盖《凡修》手游相关的数据、资源、运行时、行为树、任务闭环、抓包、
GUI 自动化和业务条目维护。只要工作对象是凡修手游或凡修自动化链路，即使代码
修改发生在 CodeYun 仓库内，也应归为凡修，而不是 CodeYun/综合。

强信号包括：

- 明确出现“凡修”。
- 游戏业务词：洞天、福地、洞天福地、祈愿、炼丹、淬体、灵兽、妖王、仙花、仙府、宗城、法宝、道具、仙舟、衣橱等。
- 自动化与运行语境：`daily_foundation.py`、`mail.py`、日常基础、日常任务、领取、返回闭环、稳定回归锚点、场景编号、目标场景、世界步骤、抓包巡检、`packet_worker`、`pcap`。
- VIP/日常链路：`daily_vip`、`日常_vip`、每日限购、免费、`#34`、`#291`、`#292`、固定标注路径、真实 Runtime 闭环。
- 用户反馈确认：凡修手游里的洞天任务，以及围绕该任务的功能开发，都属于凡修。

不要因为以下因素改判为 CodeYun/综合：

- `project_label` 是 `codeyun`。
- 修改文件位于 CodeYun 仓库。
- 内容里出现“修复、实现、验证、闭环”等通用工程词。

已确认样本：

- `fanxiu-dongtian-return-loop-20260628`：洞天福地返回闭环，误分为 CodeYun/综合，标准分类为凡修。
- `fanxiu-daily-vip-runtime-loop-20260628`：日常_vip行为链路闭环，误分为 pyxllib，标准分类为凡修。

## CodeYun/综合

CodeYun/综合覆盖 CodeYun 自身的系统级治理、跨模块根因修复、自动化规范、运行时策略、
缓存/资源链路保护，以及不专属于“笔记、资源、集群”等细分 CodeYun 子类的工程治理工作。

强信号包括：

- `daily-thread`、`automation-daily-thread`、`codex-automation-management`。
- 系统级治理、全局行为治理、根因修复。
- runtime 点击策略、标注点击 helper、中心点 fallback。
- `asset-tree`、前端缓存、保存 shape、失败提示、保护逻辑。
- `cluster/files`、`cluster/treesize`、`cluster/view-chan-course`、`filesystem.py`、目录请求、递归树缓存、首屏性能、随机学习体验。
- CodeYun 页面性能优化、CodeYun 前端设计巡检。
- `frontend/src/standard/**`、`frontend/src/components/**`、`frontend/src/utils/**`、`.vue`、Vue3、首屏、首帧、热路径、前端加载、组件渲染。
- `cell-logs`、`Promise.allSettled`、`StarNotes.vue`、`refreshNodeInternals`、`notes/galaxy`。
- UI 自主学习、学习 checkpoint、`candidates.json`、自动化提示词中文化、巡检状态词本地化、随机提示入口、随机阅读、Tip of the Day、每日一句、开源项目核验、AlphaGPT/半夏之神调研结论。
- 浏览器、代理、网络、下载、候选回灌、页面状态同步等 CodeYun 内部功能修复。比如“修正系统代理地址”“修复 pinterest 候选回灌”这类 Codex 工作，不归“后勤”“模块”或“缺陷”，优先归 CodeYun/综合。

不要因为以下词汇把它误判为“缺陷”：

- 失败、问题、风险、缺失、修复、排查。

这些词只是工程治理过程中的现象描述。Codex 日记自动分类里禁用“缺陷”作为一级分类；
遇到具体修复类工作，按业务归属归到 CodeYun/综合、考勤、凡修等更具体类别。

## 后勤

后勤只覆盖生活层面的事务，例如日常安排、购物、证件、账号、居住、家庭设备和个人生活琐事。
它不覆盖代码工作、系统配置、浏览器代理、下载链路、候选回灌、接口修复、页面状态同步或工程排障。

如果标题或摘要里出现“代理、系统、Chrome、Google、Pixiv、pinterest、候选、数据库、页面、接口、脚本、配置”等工程语境，
应先判断是否属于 CodeYun/综合、考勤或凡修，而不是因为“处理杂事/修复环境”就归后勤。

已确认样本：

- `codeyun-general-daily-thread-root-cause-20260628`：daily-thread 根因修复，误分为缺陷，标准分类为 CodeYun/综合。
- `codeyun-general-cluster-files-performance-20260628`：集群性能优化，误分为 CodeYun/集群，标准分类为 CodeYun/综合。
- `codeyun-general-frontend-first-screen-20260628`：前端首屏性能裁剪，误分为 pyxllib，标准分类为 CodeYun/综合。
- `codeyun-general-ui-learning-automation-20260628`：UI学习，误分为 pyxllib，标准分类为 CodeYun/综合。
- `codeyun-general-automation-localization-20260628`：抓包巡检自动化中文化，误分为 pyxllib，标准分类为 CodeYun/综合。
- `codeyun-general-random-reading-psychology-20260628`：随机阅读心理机制，误分为 pyxllib，标准分类为 CodeYun/综合。

## pyxllib

pyxllib 只覆盖 pyxllib 这个 Python 通用库自身的工具层、编程层、跨项目基础设施和库内能力演进。

不要因为以下因素把 CodeYun 工作误判为 pyxllib：

- 性能优化技能、测试工具或报告格式里提到 `pyxllib.algo.stat.ValuesStat2`。
- CodeYun 仓库里的 Python 后端测试间接 import pyxllib。
- 前端任务里出现通用工程词、统计词或工具库名。

Vue3、`.vue`、`frontend/src/**`、CodeYun 页面首屏/热路径/组件渲染/前端设计巡检，默认不属于 pyxllib。

## CodeYun/集群

CodeYun/集群只覆盖多机器/多端服务、设备与服务 token、局域网服务发现、OCR 集中化、
后台作业调度等“集群运行和运维”主题。

不要仅因为路径或页面名称包含 `cluster` 就归为 CodeYun/集群。若主体是文件浏览、
treesize、filesystem 热路径、前端首屏性能、课程随机片段等普通 CodeYun 功能优化，
应归 CodeYun/综合。
