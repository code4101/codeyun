# Codex 日记分类标准

本文档记录用户确认过的每日总结分类边界。每条经验需要同步沉淀到
`tests/fixtures/codex_diary_category_cases.jsonl`，作为分类回归测试样本。

## 分类修正后的聚合不变量

Codex 日记的活跃节点以“日期 × 主分类”唯一：同一天同一主分类只能有一条。
修改日记分类不是只改 `primary_category` 等分类字段；修改后必须立即按新的分组键
重新聚合，合并正文、工时、轮次、来源线程和设备，并将被吸收节点软删除留痕。
因此“分类正确但同日凡修仍拆成多条”仍然属于未修复完成。

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
- 凡修正式 Job 注册表中的身份：`task_type`、用户可见 `label`、`standard_job_id`。这些是业务归属的权威来源；例如 `activity_quiz / 活动_答题 / activity-quiz`、`lilian_event / 历练_事件`、`xianqiao_trial / 仙窍_试炼`。分类器应动态读取注册表，新增作业后自动获得凡修归属，不能继续靠逐个补关键词。
- 凡修行为树的通用 Job 模型：作业状态设计、作业执行模型、作业返回值，以及与之配套的 `run_status`、`job_status`、`next_time`、`success/error`、Scheduler、错误重试和触发机制。即使没有出现某个具体作业名称，仍属于凡修；“文档归一”只是输出形态，不属于 CodeYun/笔记。
- 游戏业务词：洞天、福地、洞天福地、论道、祈愿、炼丹、淬体、灵兽、妖王、仙花、仙府、宗城、法宝、道具、仙舟、衣橱等。
- 自动化与运行语境：`daily_foundation.py`、`mail.py`、日常基础、日常任务、领取、返回闭环、稳定回归锚点、场景编号、目标场景、世界步骤、抓包巡检、`packet_worker`、`pcap`。
- 动态插桩链路：动态插桩、游戏实例缓存、运行态主槽、灵器实例、`cleanseId`、Frida/Lua 运行态读取及其数据库快照、API 和前端投影。
- 论道链路：论道座位、抢座、落座、三清、大罗及论道场景识别；围绕这些对象处理 OCR、GPU/CUDA、端口争抢或性能优化时仍归凡修。
- VIP/日常链路：`daily_vip`、`日常_vip`、每日限购、免费、`#34`、`#291`、`#292`、固定标注路径、真实 Runtime 闭环。
- 用户反馈确认：凡修手游里的洞天任务，以及围绕该任务的功能开发，都属于凡修。
- 云梦活动链路：云梦、云梦试剑、云梦论剑、`Yunmeng/YunmengPK`、兑币/累计兑币、挑战记录、个人榜/位面榜、丹均积分和目标预测。接口、算法、数据库、知识库或页面只是实现载体，不能改判为 CodeYun/笔记或综合。

不要因为以下因素改判为 CodeYun/综合：

- `project_label` 是 `codeyun`。
- 修改文件位于 CodeYun 仓库。
- 内容里出现“修复、实现、验证、闭环”等通用工程词。

已确认样本：

- `fanxiu-dongtian-return-loop-20260628`：洞天福地返回闭环，误分为 CodeYun/综合，标准分类为凡修。
- `fanxiu-daily-vip-runtime-loop-20260628`：日常_vip行为链路闭环，误分为 pyxllib，标准分类为凡修。
- `fanxiu-dynamic-instrumentation-spiritware-20260801`：动态插桩读取灵器实例并写回前端表格，误分为 CodeYun/笔记，标准分类为凡修。
- `fanxiu-lundao-ocr-gpu-20260801`：论道 OCR 切回 GPU 并优化识别性能，误分为 CodeYun/集群，标准分类为凡修。
- `fanxiu-lundao-dynamic-seating-20260720`：论道动态抢座策略，误分为 CodeYun/笔记，标准分类为凡修。
- `fanxiu-lundao-status-vocabulary-20260718`：论道状态词汇与场景映射，误分为考勤，标准分类为凡修。
- `fanxiu-activity-quiz-standard-job-20260731`：活动_答题标准作业、共享题库、奖励钩子与极速点击链路，误分为 CodeYun/笔记，标准分类为凡修。
- `fanxiu-job-status-model-20260730`：凡修行为树的作业状态、`success/error`、`next_time` 与 Scheduler 职责边界规划，误分为 CodeYun/笔记，标准分类为凡修。
- `fanxiu-yunmeng-trial-20260802`：云梦试剑累计兑币、挑战记录、榜单与目标预测，误分为 CodeYun/笔记，标准分类为凡修。

## CodeYun/笔记

CodeYun/笔记既覆盖星图笔记、图书馆、PDF、阅读器、文档视图等笔记产品能力，也覆盖
用户与 Codex 进行的知识解释、原理讨论和可沉淀阅读内容。后者不要求实际修改笔记系统代码。

典型内容包括计算理论、数学物理、医学科普、哲学启发、文章摘录和阅读整理。例如图灵机
停机问题、量子计算原理、重症肌无力科普均属于 CodeYun/笔记。即使同一日或同一批导入
记录里还出现修道班、返款或课程配置，也必须先按最小问答事务拆分，不能把这些独立知识
话题染成考勤。

已确认样本：

- `codeyun-note-halting-problem-20260725`：停机问题与可计算性解释，误分为考勤，标准分类为 CodeYun/笔记。
- `codeyun-note-quantum-computing-20260725`：量子计算与概率振幅原理，误分为考勤，标准分类为 CodeYun/笔记。
- `codeyun-note-medical-explanation-20260725`：重症肌无力医学科普，误分为考勤，标准分类为 CodeYun/笔记。

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
它通常是实现层或承载层，不是业务归属。若同一项工作同时涉及考勤、凡修、造化仙缘等
明确业务，即使确实修改、迁移或归档了 pyxllib 代码，也应优先归入具体业务；只有工作主体
就是 pyxllib 通用库本身、且没有更具体业务对象时，才单独归为 pyxllib。

不要因为以下因素把 CodeYun 工作误判为 pyxllib：

- 性能优化技能、测试工具或报告格式里提到 `pyxllib.algo.stat.ValuesStat2`。
- CodeYun 仓库里的 Python 后端测试间接 import pyxllib。
- 前端任务里出现通用工程词、统计词或工具库名。

Vue3、`.vue`、`frontend/src/**`、CodeYun 页面首屏/热路径/组件渲染/前端设计巡检，默认不属于 pyxllib。

已确认样本：

- `attendance-over-pyxllib-architecture-convergence-20260731`：考勤系统退役 WPS/JSA、收敛为 CodeYun 单一架构，并把历史能力归档到 pyxllib 博物馆；pyxllib 是归档与实现位置，标准分类为考勤。

## CodeYun/集群

CodeYun/集群只覆盖多机器/多端服务、设备与服务 token、局域网服务发现、OCR 集中化、
后台作业调度等“集群运行和运维”主题。

不要仅因为路径或页面名称包含 `cluster` 就归为 CodeYun/集群。若主体是文件浏览、
treesize、filesystem 热路径、前端首屏性能、课程随机片段等普通 CodeYun 功能优化，
应归 CodeYun/综合。
