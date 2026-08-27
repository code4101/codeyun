# CodeYun 文档中心

本目录按“当前事实、操作方法、工作过程、历史追溯”分层管理。阅读文档时，先判断文档所处层级，不要把计划、研究记录或自动化增量日志当成当前实现。

## 阅读入口

- AI 接手整个仓库：先读 [AI_CONTEXT.md](./AI_CONTEXT.md)，再按其中链接进入具体业务域。
- CodeYun 平台架构：进入 [platform/architecture](./platform/architecture/)；开发约定见 [platform/conventions](./platform/conventions/)。
- 凡修：先读 [凡修文档地图](./domains/fanxiu/README.md)。
- 考勤：进入 [domains/attendance](./domains/attendance/)。
- 星图笔记：进入 [domains/notes](./domains/notes/)。
- 星云表格：进入 [domains/spreadsheets](./domains/spreadsheets/)。
- 股票与账单：进入 [domains/finance](./domains/finance/)。
- 外部数据与内容集成：进入 [domains/integrations](./domains/integrations/)；Pixiv 作者关注规则见 [Pixiv 作者私密关注机制](./domains/integrations/architecture/Pixiv作者私密关注机制.md)。
- 启动、排障和恢复：进入 [operations/runbooks](./operations/runbooks/)。
- 自动化设计与长期上下文：进入 [automation](./automation/)。
- 产品能力概览：参见 [中文模块说明](./reference/modules.zh-CN.md) 或 [English modules](./reference/modules.md)。

## 目录层级

| 目录 | 层级 | 内容 | 是否可作为当前事实 |
| --- | --- | --- | --- |
| `platform/` | L1 | 跨业务的平台架构与开发约定 | 是 |
| `domains/` | L1-L2 | 各业务域的架构、指南、标准作业与研究 | 以域内索引为准 |
| `operations/` | L2 | 启动、部署、恢复和排障手册 | 是 |
| `automation/` | L2-L3 | 自动化设计、游标、候选队列和增量上下文 | 仅设计正文可作为事实 |
| `research/` | L3 | 尚未固化为系统约定的研究材料 | 否 |
| `archive/` | L4 | 已停用方案、完成计划和恢复档案 | 否 |
| `reference/` | 参考 | 面向读者的能力概览与参考资料 | 不定义实现 |
| `assets/` | 资源 | 文档使用的图片等静态资源 | 不适用 |

## 文档类型和优先级

发生冲突时，按以下顺序判断：

1. `AGENTS.md` 中的强约束。
2. `platform/architecture`、`platform/conventions` 或业务域 `architecture` 中明确声明的唯一权威正文。
3. `guides`、`jobs`、`runbooks` 中的当前操作方法。
4. `plans`、`research` 和 `automation/context` 中的工作记录。
5. `archive` 中仅供追溯的历史材料。

文件名包含“设计”并不自动代表它是当前架构；文件所在层级和正文中的状态声明共同决定其权威性。

## 新文档放置规则

- 描述系统现在如何工作：放入平台或业务域的 `architecture`。
- 描述必须遵守的开发规则：放入 `platform/conventions`，业务专用规则放入相应业务域。
- 描述如何执行、恢复或排障：放入 `guides` 或 `operations/runbooks`。
- 描述一个可选业务作业：放入相应业务域的 `jobs`。
- 尚未实施的方案、任务清单：放入 `plans`。
- 探索、测量、抓包观察和方案比较：放入 `research`。
- 自动化游标、候选队列和增量记忆：放入 `automation/context` 或业务域 `context`。
- 已失效但仍有追溯价值：移入 `archive`，并在正文顶部标明失效原因和替代入口。

`docs/` 根目录只保留本文和约定俗成的 `AI_CONTEXT.md`。新增文档不得继续堆在根目录。
