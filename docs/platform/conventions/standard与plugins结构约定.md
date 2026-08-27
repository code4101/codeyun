# standard 与 plugins 结构约定

本文档用于说明当前项目中 `standard` 与 `plugins` 两层结构的设计意图、使用边界，以及后续新功能开发时的推荐落点。

## 1. 背景

项目原先的常规功能主要分散在：

- 前端：`frontend/src/views/...`
- 后端：`backend/api/*.py`

这种组织方式对“按页面/路由找源码”并不友好。  
随着后续插件化、私有源码外置、按功能树组织代码的需求越来越明确，项目开始引入两层新的功能来源：

- `standard`
- `plugins`

目标不是一次性重写所有历史实现，而是先把**功能装配层**整理清楚，再逐步迁移具体实现。

## 2. 核心概念

### 2.1 standard

`standard` 表示项目的**标准功能来源**。

它的含义是：

- 常规随项目一起交付的功能
- 默认由主仓库维护
- 是页面、业务接口、标准功能树的主要来源

它**不表示**：

- 所有标准功能都会自动展示
- 它等于权限“公开”
- 它包含宿主全部基础设施

一句话概括：

> `standard` 是“标准功能代码集合”，不是“最终展示结果”，也不是“全项目所有代码”。

### 2.2 plugins

`plugins` 表示项目的**插件功能来源**。

它的含义是：

- 通过额外挂载方式引入的功能
- 作为 `standard` 的补充来源存在
- 可以来自外部目录、私有仓库、后续的插件制品
- 语义上与 `standard` 对称

它**不表示**：

- 一定是私有源码
- 一定不能公开给普通用户使用

一句话概括：

> `plugins` 是“插件功能代码集合”，不是“私有功能”的同义词。

进一步说：

- `standard` 是标准功能的主来源
- `plugins` 是标准功能之外的补充来源
- 非开源功能只是 `plugins` 的一种具体用法，不是 `plugins` 的定义本身

因此完全可以存在这样的组合：

- 开源插件功能
- 非开源插件功能
- 插件源码私有，但页面功能对外开放

### 2.3 源码公开与访问权限是正交概念

需要强调：

- `standard / plugins` 讨论的是**功能来源与装配方式**
- 是否进入 GitHub，讨论的是**源码归属与分发策略**
- 权限系统讨论的是**谁可以访问**

这三者是正交概念，不要混用。

当前项目的明确方向是：

- `standard` 承载标准功能
- `plugins` 承载扩展功能
- 需要不公开源码的功能，优先作为 `plugins` 的本地私有实现存在

也就是说，在本项目里更推荐这样理解：

> `plugins` 是扩展承载层；“非开源”是其中一个常见子集。

## 3. 当前装配顺序

### 3.1 前端

当前前端路由总表在：

- [frontend/src/router/pageRegistry.ts](../../../frontend/src/router/pageRegistry.ts)

页面来源按如下顺序叠加：

1. `standardPageRegistry`
2. `pluginPageRegistry`

对应代码入口：

- [frontend/src/standard/index.ts](../../../frontend/src/standard/index.ts)
- [frontend/src/plugins/index.ts](../../../frontend/src/plugins/index.ts)

这意味着：

- 标准功能先装配
- 插件功能后装配

### 3.2 后端

当前后端应用入口在：

- [backend/app.py](../../../backend/app.py)

业务路由挂载顺序按如下层次组织：

1. 宿主级全局路由
2. `register_standard_modules(app)`
3. `register_plugin_modules(app)`

对应代码入口：

- [backend/standard/registry.py](../../../backend/standard/registry.py)
- [backend/plugins/registry.py](../../../backend/plugins/registry.py)

## 3.3 当前 plugins 的目录契约

当前 `plugins` 已经具备最小可用的宿主能力，具体插件按如下目录接入：

前端：

```text
frontend/src/plugins/modules/<插件名>/index.ts
frontend/src/plugins/modules/<插件名>/permissionRegistry.json
```

后端：

```text
backend/plugins/modules/<插件名>/__init__.py
```

其中：

- 前端 `index.ts` 负责导出页面定义和可选的插件菜单定义
- 前端 `permissionRegistry.json` 负责补充该插件对应的权限节点
- 后端 `__init__.py` 负责导出 `register(app)`，自行注册路由、任务或其他挂载逻辑

这几个目录的具体插件实现可以本地存在但不随主仓库提交，当前 `.gitignore` 已经为 `plugins/modules/*` 预留了忽略规则。

## 4. 目录职责

### 4.1 应放进 standard 的内容

适合放进 `standard` 的是：

- 某个标准页面/标准功能的前端入口
- 某个标准业务域的后端注册入口
- 某个标准功能自己的局部实现
- 某个标准功能树下的局部共享代码

典型例子：

- `frontend/src/standard/fanxiu/...`
- `backend/standard/fanxiu/...`

### 4.2 暂时不放进 standard 的内容

以下内容目前不建议强行并入 `standard`：

- 全局鉴权
- 数据库连接
- 上传与静态资源基础设施
- 全局 feature access
- 宿主启动逻辑
- 设备/调度等跨功能平台底座

这些更接近宿主基础设施，仍应保留在现有外层结构中，例如：

- `backend/core/...`
- `backend/db.py`
- `backend/api/access.py`
- `backend/api/auth.py`
- `backend/api/upload.py`

一句话概括：

> `standard` 承载标准功能树，不承载宿主的一切底层设施。

## 5. 当前迁移阶段

当前迁移阶段已经分成了前后端两种状态：

- 前端：标准页面的实现层已经大幅迁入 `frontend/src/standard/...`
- 后端：仍以“标准入口 + 历史实现桥接”为主，但正在逐步细化入口粒度

也就是说，目前 `standard` 在两端承担的角色并不完全一样：

- 前端更接近“目录即实现”
- 后端更接近“目录即标准入口，业务实现逐步迁入”

当前后端仍有不少历史实现保留在：

- `backend/api/*.py`

但这些历史实现已经开始通过 `backend/standard/...` 的更细入口来表达归属。例如：

- `backend/standard/tools/ai_chat/...`
- `backend/standard/tools/ai_config/...`
- `backend/standard/tools/ai_git_commit/...`
- `backend/standard/cluster/devices/...`
- `backend/standard/cluster/tasks/...`
- `backend/standard/fanxiu/status/...`
- `backend/standard/fanxiu/chars/...`
- `backend/standard/admin/accounts/...`
- `backend/standard/admin/images/...`

这样做的好处是：

- 前端已经能按功能树直接定位页面实现
- 后端可以先稳定标准入口，再分批搬迁大体量业务模块
- 不会为了“目录好看”一次性打散高风险历史逻辑

需要注意的是，后端的标准目录有时会按“API 资源”而不是“页面名”切分。  
原因是一个后端资源往往会同时服务多个页面，例如文件浏览、标注浏览、任务页可能共享一组设备或任务接口。

## 6. 新功能开发建议

### 6.1 新的标准功能

如果是新增常规功能，优先放进 `standard`。

如果不是“全新页面”，而是“某个已有标准页面的业务特化版本”，并且希望父页后续新增通用能力时子页能自动同步继承，则不要复制 `page.vue`；应优先采用“页面继承”机制，见：

- [docs/platform/conventions/页面继承机制约定.md](./页面继承机制约定.md)

前端建议：

```text
frontend/src/standard/<域>/...
```

后端建议：

```text
backend/standard/<域>/...
```

如果功能很简单，可以采用极简形态：

- 一个 `page.vue`
- 一个 `module.py`

如果功能较复杂，再在该目录下继续细分。

### 6.2 新的插件功能

如果功能明确是插件来源，而不是主仓库标准功能，则放进 `plugins`。

这里的“插件功能”包括但不限于两类：

- 需要以插件方式补充 `standard` 的功能
- 需要源码不进入主仓库的功能

当前 `plugins` 已经具备最小可用的注册与加载能力：

- [frontend/src/plugins/index.ts](../../../frontend/src/plugins/index.ts)
- [backend/plugins/registry.py](../../../backend/plugins/registry.py)

后续接入真实插件时，应优先沿用这一层，而不是继续把扩展散落到宿主代码里。

### 6.3 私有源码功能

如果功能需要源码不进入公开仓库，长期建议也是放进 `plugins`，只是插件源码本体不随主仓库提交。

推荐做法是：

- 保留仓库内的 `plugins` 宿主与注册层
- 具体插件实现放在本地私有目录、外部私有仓库，或直接 `gitignore`
- 由插件注册机制把它们挂入系统

这里要区分两件事：

- `plugins` 解决的是“扩展如何接入宿主”
- 是否开源，解决的是“插件源码是否进入公开仓库”

因此更准确的关系是：

> 非开源功能 ⊂ 插件功能

旧的 `private` 兼容层已经移除；后续新的非开源功能统一按 `plugins` 的形态来组织。

## 7. 推荐判断规则

写新代码时，可以按下面的顺序判断：

1. 这是宿主基础设施吗？
2. 如果不是，它是标准功能还是插件功能？
3. 如果是标准功能，它属于哪棵功能树？
4. 它是某个功能专属，还是跨多个功能共享？

对应落点建议：

- 宿主基础设施：保留在现有 `core/db/api` 等基础层
- 标准功能：进入 `standard`
- 插件功能：进入 `plugins`
- 本地私有实现：优先作为 `plugins` 的私有实现存在

## 8. 设计收益

这套结构的核心收益有三点：

1. 源码更容易按功能树定位
2. `standard / plugins` 的角色边界更清晰
3. 可以渐进迁移，不要求一次性重写历史代码

最终目标不是“所有代码都搬进 `standard`”，而是：

> 让标准功能、插件功能、私有扩展、宿主基础设施各自站在正确层级上。
