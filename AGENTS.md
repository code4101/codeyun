# AGENTS.md

## 运行约定（重要）

- 所有命令默认在仓库根目录执行：`D:\home\chenkunze\slns\codeyun`
- Python 命令优先使用 `uv run`
- 启动开发环境统一使用：`uv run dev.py`
- 运行测试统一使用：`uv run pytest`
- 临时 Python 命令统一使用：`uv run python ...`

## 兜底方案

- 仅在 `uv` 不可用时，Windows 使用：
  - `.\.venv\Scripts\python.exe dev.py`
- 不要依赖全局 `python` 或其他项目的虚拟环境

## 前端命令

- 安装依赖：`npm install --prefix frontend`
- 单独启动前端：`npm run dev --prefix frontend`

## 前端页面/菜单挂载约定（重要）

- `frontend/src/standard/**/index.ts` 只负责注册页面路由，不会自动把页面加到侧边栏菜单。
- 如果新增页面需要在左侧导航里可见，至少要同步检查这几层：
  - `frontend/src/standard/**/index.ts`：页面路由定义
  - `frontend/src/features/access/permissionRegistry.json`：补 `route_paths`，需要作为菜单点击项时还要补 `menu_paths`
  - `frontend/src/layout/MainLayout.vue`：侧边栏菜单是手写结构，必须显式加 `el-menu-item` 或 `el-sub-menu`
- 如果是“某页面下的新子页面”，不要只复用父页面 `menuPath` 就结束；要先判断用户是否需要在侧边栏直接看到这个子项。
- 对带子菜单的场景，还要同步检查 `MainLayout.vue` 里的：
  - 路径常量和标题常量
  - `*MenuVisible` 之类的显示条件
  - `defaultOpeneds` 里的默认展开逻辑
  - 必要时的 submenu 标题点击跳转入口

## 作业管理约定（重要）

- 用户要求“加作业”时，优先理解为给 CodeYun 增加一个可选的作业类型能力，而不是直接给当前机器创建已启用的定时实例。
- 作业类型、执行逻辑、默认说明可以随代码提交；具体是否加入清单、是否启用、定时策略和下次触发时间属于本地数据库配置。
- 新增的专用作业类型默认不要出现在运行清单里，应通过“作业 +”的类型目录让用户按需添加。
- 这样新机器部署 CodeYun 时保持干净清爽，不继承当前机器的一堆个人作业和触发时间。

## 部署运维约定（重要）

- 仓库内的 GitHub Actions 自动部署链路已于 `2026-04-16` 移除，不要再假设 `.github/workflows/deploy-ubuntu24.yml -> deploy/update.sh` 仍然存在。
- 如需恢复旧方案，唯一参考文档是：`docs/自动部署恢复档案.md`。
- 当前服务器历史口径仍是系统级 `systemd` 服务 `codeyun-backend`，不是 `systemctl --user`；但相关模板文件已从仓库移除。
- 服务器运行时 `.env` 只负责应用配置，不负责存 SSH 登录信息。
- `CODEYUN_DATA_DIR` 是可选项；如果不配置，后端默认使用仓库外的数据工作区
  `D:\home\chenkunze\data\m2603codeyun\codepc_<本机名>`，不要再回落到 `backend/data/`。

## DSP 静态同步约定

- 戴森球静态资源统一使用：`uv run python scripts/build_dsp_static.py`
- 该脚本现在是幂等的：
  - 若 `dsp-calc` 源码内容未变化，则快速跳过，不重复 `npm install / build / copy`
  - 若源码或依赖清单变化，则自动重新构建并替换 `frontend/public/dsp-calc`
- 脚本本地状态存放在 `frontend/.codeyun-state/`，该目录已加入 `.gitignore`
- 需要忽略缓存强制重建时，使用：`uv run python scripts/build_dsp_static.py --force`

## dev.py 调试策略（重要）

- `dev.py` 是长驻进程，终端/工具超时不等于启动失败。
- 当命令超时时，先检查是否已成功启动，而不是立即判定失败：
  - `netstat -ano | Select-String ':8000|:5173'`
  - `Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and $_.CommandLine -match 'codeyun' }`
- 重复调试前先清理残留进程，避免多开导致端口冲突或日志混淆：
  - `python.exe / node.exe / cmd.exe` 中命令行包含 `dev.py`、`uvicorn`、`vite` 的进程都应清理。
- 为了稳定抓错误，优先使用“后台启动 + 分离 stdout/stderr 日志”方式，不依赖前台交互输出。
- 成功判据：
  - 前端日志出现 `VITE ... ready`
  - 后端日志出现 `Application startup complete`
- 失败排查顺序：
  1. `uv sync`（确保依赖与锁文件一致）
  2. 看后端错误日志（通常是导入/依赖问题）
  3. 看端口占用与重复进程

## 星图笔记筛选约定（重要）

- 星图笔记里的“筛选”不是传统 `where` 条件拼接，而是受 `pyxllib/file/walker.py` 启发的一套“有序规则链”。
- 规则按顺序执行，后面的 `include / exclude` 可以覆盖前面的结果；理解时不要把它当成静态布尔表达式。
- 当前产品把同一套规则模型分成两层执行：
  - `后端筛选`：跑在后端完整候选集上，决定当前 tab 从后端加载哪些节点/边。
  - `前端筛选`：跑在当前已加载结果上，规则结构与后端一致，只影响当前视图的实时渲染。
- 这两层不是两套不同语义，而是同一套规则程序在不同执行层运行；开发时优先保持 schema 和行为对称。

### 核心心智

- 后端筛选负责“加载哪些数据”。
- 前端筛选负责“已加载数据现在怎么显示”。
- 两层执行顺序固定：
  1. 先运行后端筛选，得到当前 tab 的结果集。
  2. 再运行前端筛选，得到当前视图的可见结果。

### 当前实现约定

- 后端统一接口优先使用 `/api/notes/query-program`。
- `noteStore` 负责共享实体缓存；每个 tab 自己持有 `dataProgram / viewProgram / currentMonth` 这类视图状态。
- 列表、全局星系：
  - 都有 `后端筛选` 和 `前端筛选`
  - 后端筛选需要显式点击“执行”后才生效并保存
  - 前端筛选修改后立即生效并立即保存
- 日历：
  - `后端筛选` 不给用户直接编辑规则链
  - 它由当前月视图的整块可见网格日期范围自动生成
  - 注意这里不是自然月，而是包含月头/月尾补出来的上月末、下月初日期
  - `前端筛选` 仍是通用规则链，默认 `包含全部节点`
- 行星图 / 卫星图 / 临时图 tab：
  - 默认不要强行套用全局星系那套筛选栏
  - 是否接入通用筛选，要看具体产品语义

### 开发判断原则

- 会改变结果集边界、加载量、分页、后端遍历范围的，放后端筛选。
- 只影响当前已加载结果可见性、排序、局部隐藏、即时重绘反馈的，放前端筛选。
- 如果一个条件理论上两层都能做：
  - 影响“加载哪些数据”的版本放后端
  - 影响“当前怎么显示”的版本放前端
- 不要再回到“每个视图自己发明一套筛选语义”的旧模式；允许不同视图有不同 UI，但底层规则模型要尽量统一。
