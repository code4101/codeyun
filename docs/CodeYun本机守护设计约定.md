# CodeYun 本机守护设计约定

## 背景与目标

CodeYun 需要在本机长期运行，并且不能因为后端导入失败、前端异常、坏代码热加载、窗口弹出或多实例冲突影响日常使用。为此，本机守护必须是 CodeYun 主服务之外的独立服务。

目标：

- CodeYun 每次启动时都确认本机守护存在；没有则启动。
- 守护独立于 CodeYun 主进程；CodeYun 崩溃不能带死守护。
- 日常开发以用户打开的 `uv run dev.py` 命令行窗口作为主控。
- 用户日常优先使用仓库根目录 `codeyun-dev.cmd` 打开主控；它只是固定 cwd、环境变量和 `uv run dev.py`，不另建一套运行逻辑。
- 守护负责恢复 CodeYun 可用性；当命令行主控仍存活时，守护只观测，不接管热加载或重启。
- `dev.py` 负责单实例收敛和本窗口内的后端热加载；守护只在没有主控时用隐藏 `pythonw dev.py` 兜底恢复。
- CodeYun 是局域网服务：开发前端和后端默认监听 `0.0.0.0`，既支持本机访问，也允许同一局域网内的 iPad 等设备通过本机 IPv4 地址访问。
- 后台运行不得弹出控制台窗口。
- 另有 2 小时稳定性巡检，定期检查健康、守护、主控心跳和可见控制台弹窗证据。

## 总体设计

```text
用户手动启动
  -> codeyun-dev.cmd
  -> uv run dev.py
  -> 当前控制台成为 console host
  -> dev.py 在原控制台内记录心跳、管理热加载和子服务

CodeYun 后端启动
  -> ensure_local_builtin_services_on_startup()
  -> 检查 CodeYun 本机守护是否存在
  -> 不存在则用 pythonw 启动 scripts/codeyun_watchdog.py --loop

Windows 登录自启
  -> 由固定计划任务 CodeYun Watchdog 管理
  -> 计划任务直接启动 scripts/codeyun_watchdog.py --loop
  -> 守护再按恢复策略拉起 dev.py

CodeYun 本机守护
  -> 独立进程，使用系统临时目录中的 pid/log
  -> 如果发现命令行 console host 心跳新鲜，只记录状态，不重启 dev.py
  -> 定期检查后端和前端健康状态
  -> 没有命令行主控且服务不可用时调用 dev.py 重新拉起
  -> 不承担热加载重启；源码变化由 dev.py 命令行主控处理
  -> 不承担本机常驻命令服务自检；sync/frpc/nginx 等由后端启动和后台任务负责

dev.py
  -> 命令行开发主控
  -> 启动前清理当前仓库旧 CodeYun 服务组
  -> 启动一个后端和一个前端
  -> 默认使用 outer backend watcher，在当前进程内完成后端延迟热加载
  -> 写入系统临时目录中的 console host 心跳，告知守护不要接管

2 小时稳定性巡检
  -> Windows 计划任务 CodeYun Stability Check
  -> pythonw 启动 scripts/codeyun_stability_check.py --json
  -> 检查后端/前端健康、watchdog 进程、console host 心跳
  -> 确保可见控制台监控器存在，并审计最近 2 小时事件
  -> 报告写入系统临时目录，不弹窗口，不接管热加载
```

## 职责边界

### CodeYun 后端

CodeYun 后端只负责自举守护：

- 启动时检查守护是否存在。
- 守护不存在时拉起守护。
- 守护已存在时不重复启动。
- 后端不承担长期守护职责。

后端不能依赖守护才能完成自身启动，否则会形成启动环。

### CodeYun 本机守护

守护是外部监管者：

- 使用本地进程、锁文件、日志和 HTTP 探针判断状态。
- 不依赖 CodeYun 后端 API 管理自己。
- 没有命令行主控时，后端或前端异常由守护恢复 CodeYun。
- 存在命令行主控时，后端/前端异常和热加载由 `dev.py` 在原控制台内处理；守护不得杀掉主控后新建隐藏实例。
- 代码变化不再由守护判断是否适合重启。
- sync、frpc、nginx 等本机常驻命令服务不由守护循环托管；它们通过 CodeYun 后端启动钩子和“常驻服务自检”后台任务处理。
- 自身必须足够保守，避免重启风暴。

### dev.py

`dev.py` 是命令行开发主控：

- 启动前清理当前仓库的旧 CodeYun dev runner、uvicorn、Vite 和端口占用。
- 只清理命令行或工作目录明确指向当前仓库的进程。
- 默认负责后端源码变化后的延迟热加载，保持在用户打开的同一个控制台窗口里运行。
- 后端热加载只有 `outer` 和 `off` 两种模式；不再保留 `uvicorn --reload` 这条第二套热加载路径。
- 启动时写入 `%TEMP%\codeyun\codeyun-console-host.json` 心跳；守护据此退让。
- 不默认关闭守护自启动。
- 前端和后端默认监听 `0.0.0.0`，不得在普通启动、守护恢复或 Agent 临时恢复时缩窄为 `127.0.0.1`。恢复验收必须同时探测 `127.0.0.1` 与当前首选局域网 IPv4；只有本机入口成功不算恢复完成。

### codeyun-dev.cmd

`codeyun-dev.cmd` 是面向用户的唯一推荐主控启动入口：

- 固定切换到仓库根目录，避免从其他 cwd 启动导致状态和路径分裂。
- 设置 `CODEYUN_DEV_CONSOLE_HOST=1`，显式声明当前进程是命令行主控。
- 默认设置 `CODEYUN_DEV_BACKEND_RELOAD_MODE=outer`，让后端热加载由 `dev.py` 外层 watcher 控制。
- 优先运行 `uv run dev.py`；仅在 `uv` 不可用时回落到 `.venv\Scripts\python.exe dev.py`。
- 不使用 `pythonw.exe`，因为这条路径本来就应该有一个用户可见的主控制台。

这层壳不做健康判断、不做作业管理、不隐藏子进程、不另起 watchdog。它只把“用户启动 CodeYun 主控”这件事固定成一个可重复的入口。

## 重启策略

守护只保留恢复型重启作为默认能力。热加载由命令行 `dev.py` 主控承担。

### 恢复型重启

触发条件：

- 后端健康检查失败。
- 前端健康检查失败。
- CodeYun 服务组消失或端口不可用。

行为：

- 如果服务仍在启动宽限期内，等待。
- 超过宽限期仍不可用，则调用 `dev.py` 拉起。
- 不要求源码静默期，也不执行额外源码预检查。

## 后台子进程启动规范

核心不变量只有一条：CodeYun 体系内的后台启动默认无窗口；只有显式 debug/monitor 工具才允许可见控制台。
不要把它理解成一套按业务分类的复杂框架，也不要为 adb、tshark、uv、npm、Python 作业分别发明启动方案。
新增路径应先判断能否进入统一 launcher；不能进入时，应优先修 launcher 的覆盖能力，而不是在业务代码里补一份窗口隐藏逻辑。

CodeYun 常驻服务、守护、ADB/MuMu/tshark 调用和公网前端构建，必须统一使用
`backend.core.runtime.process_launcher`：

- `run_quiet(...)`：一次性短命令，默认禁止 Windows 控制台弹出。
- `check_call_quiet(...)`：需要失败即抛出的短命令。
- `check_output_quiet(...)`：需要读取 stdout 的短命令。
- `popen_service(...)`：非 Python 的后台长驻进程，默认独立进程组、脱离控制台和当前 job。
- `python_module_service_command(...)` / `popen_python_module_service(...)`：后台 Python 模块服务。
- `python_script_service_command(...)` / `popen_python_script_service(...)`：后台 Python 脚本服务。
- `node_npm_command(...)` / `node_script_command(...)`：后台 Node/NPM 命令，Windows 下绕开 `npm.cmd`。
- `apply_background_node_env(...)`：为后台 Node 工具注入 child_process 隐藏窗口补丁。
- `apply_background_python_env(...)`：为后台 Python 服务注入子进程隐藏窗口补丁。

`backend.core.runtime.subprocess_utils` 是底层实现层，只保存 Windows flags、`pythonw` 解析、`node npm-cli.js`
解析等细节。业务模块、API 模块、守护脚本和 `dev.py` 不应直接导入它；新增调用必须走
`process_launcher`。

### 进程级兜底

所有 CodeYun 长驻 Python 服务入口还必须在导入业务模块前调用
`install_child_process_no_window_default()`：

- `backend.app`
- `backend.services.ocr_daemon`
- `backend.services.game_window_daemon`
- `backend.services.proxy_traffic_audit_daemon`
- `backend.core.runtime.uvicorn_hidden`

这不是替代 `process_launcher`，而是兜底保护：显式启动后台服务仍必须走 `popen_service(...)` /
`run_quiet(...)` 等统一入口；如果第三方库或未来代码误用裸 `subprocess.Popen`，当前进程也会默认追加
`CREATE_NO_WINDOW` 和隐藏 `STARTUPINFO`，避免重新出现可见控制台闪烁。

底层默认策略：

- 后台 Python 优先用 `resolve_pythonw(ROOT_DIR, sys.executable)`，Windows 下优先 `.venv/Scripts/pythonw.exe`。
- `run_quiet(...)` / `popen_service(...)` 发现命令入口是 `python.exe`、`pythonw.exe`、`python`、`pythonw`、`py.exe`
  或 `py` 时，也会自动注入后台 Python 服务环境。这保证了“先构造 Python 命令，再交给通用 launcher”的两步写法不会绕过
  子进程隐藏继承策略。
- 后台 Python 服务由 `popen_python_module_service(...)` / `popen_python_script_service(...)` 启动时，会自动在
  `PYTHONPATH` 前置 CodeYun 的 `sitecustomize.py`，并设置 `CODEYUN_NO_WINDOW_SUBPROCESS_DEFAULT=1`。这会让
  被托管的外部 Python 进程在解释器启动时安装同样的 `subprocess.Popen` 无窗口兜底，覆盖 adb、git、
  tshark 等由外部项目二次启动的短命令。
- 用户手动运行的 `uv run dev.py` 是唯一允许长期可见的开发控制台，默认不再重定向到 `pythonw.exe`。
- 守护或作业系统后台拉起 `dev.py` 时仍属于后台 Python 服务，必须用 `pythonw.exe dev.py` 启动，避免额外弹出控制台。
- `dev.py` 内部拉起后端时使用 `pythonw.exe -m backend.core.runtime.uvicorn_hidden`，由包装模块负责运行 uvicorn
  并把日志写入系统临时目录，避免为后端额外弹出控制台窗口。
- 后台 npm 不直接执行 `npm.cmd`；使用 `node_npm_command(...)`，Windows 下优先转成 `node.exe npm-cli.js ...`。

新增后台调用时，不要在业务文件里重新手写 `subprocess.run/Popen`、`STARTUPINFO`、`CREATE_NO_WINDOW`、
`DETACHED_PROCESS`、`CREATE_BREAKAWAY_FROM_JOB` 或 `npm.cmd` 规避逻辑。确实需要可见控制台的诊断工具，
必须显式命名为 monitor/debug，并写入系统临时目录日志，不能混入常驻服务路径。

### 分层调用口径

- `dev.py` 和 `scripts/codeyun_watchdog.py` 是本机启动/守护层，也统一使用 `process_launcher`。
- `backend/core/runtime/**`、`backend/core/fanxiu/**`、`backend/api/**` 这类业务运行时代码，默认不要直接调用
  `subprocess.Popen` 启动后台服务；应使用 `process_launcher`。
- `subprocess.run/check_output/check_call` 只有在调用方本身已经是前台 CLI、测试或一次性维护脚本时可以裸用。
  后端服务路径里的短命令必须使用 `run_quiet(...)`、`check_call_quiet(...)` 或 `check_output_quiet(...)`。
- `backend/tests/test_subprocess_usage_policy.py` 会审计后端、`dev.py` 和本机守护脚本，阻止运行时路径重新绕回
  裸 `subprocess` 或底层 `subprocess_utils`。

## 可见控制台审计

弹窗问题不再是本机守护的常驻职责。日常开发接受一个用户手动打开的 `uv run dev.py`
控制台窗口；额外弹窗排查只作为按需审计工具运行。

- 正式监控脚本为 `scripts/codeyun_visible_console_monitor.py`。
- 审计入口为 `scripts/codeyun_popup_audit.py --ensure-monitor`。
- 审计状态中的 `coverage_valid=true` 只表示本次审计覆盖有效。
- 如果监控器死亡，审计入口会用无窗口方式重新拉起。
- 监控器死亡后重新拉起时，24 小时无弹窗的有效观察窗口必须重新计算；不能把监控断档前后的时间拼起来当作连续证据。
- 当前 24 小时基线写在系统临时目录 `codeyun/visible-console-monitor/codeyun_popup_24h_baseline.json`。
- 稳定性巡检不依赖 24 小时基线是否干净；它使用 `scripts/codeyun_popup_audit.py --since-hours 2`
  审计最近 2 小时，避免历史旧弹窗长期污染当前巡检结论。

## 2 小时稳定性巡检

稳定性巡检是观察和报告程序，不替代 watchdog，也不参与热加载。它的入口是
`scripts/codeyun_stability_check.py`。

默认检查项：

- 后端 `/api/health` 和前端首页是否可访问。
- `scripts/codeyun_watchdog.py --loop` 是否存在。
- 如果 console host 心跳新鲜，则确认热加载主控存在；健康短暂失败时按启动宽限期处理。
- 可见控制台监控器是否仍在覆盖当前窗口。
- 最近 2 小时是否出现 CodeYun 服务链路导致的可见控制台窗口。

输出位置：

- 最新报告：`%TEMP%\codeyun\stability-check\latest.json`
- 历史报告：`%TEMP%\codeyun\stability-check\history.jsonl`

手动运行：

```bash
uv run python scripts/codeyun_stability_check.py --json
```

安装 2 小时计划任务：

```bash
uv run python scripts/codeyun_stability_check.py --install-task
```

查看计划任务：

```bash
uv run python scripts/codeyun_stability_check.py --task-status
```

这个计划任务使用 `pythonw.exe` 运行巡检脚本，因此不会弹出控制台窗口。它只报告问题；真正的恢复仍由每分钟运行的 watchdog 负责。

## 单实例原则

单实例由两层保证：

- 守护自身通过系统临时目录中的 pid 文件保证同一时间只有一个 active 守护。
- `dev.py` 每次启动时清理当前仓库旧 CodeYun 服务组，保证最终只有一个 CodeYun 实例。

如果 Windows 下出现 `pythonw` launcher 父进程和 active 守护子进程同时存在，状态展示应区分：

- `active_pid`：真正持有锁并执行循环的守护。
- `launcher_pids`：启动链路中的父级进程。

不能把 launcher 误判为第二个 active 守护。

## 禁止事项

- 不要把守护做成 CodeYun 后端线程。
- 不要让守护依赖后端接口管理自身。
- 不要在预检查失败时重启覆盖旧服务。
- 不要让多个组件分别拼 uvicorn、Vite 或 taskkill 命令。
- 不要使用会弹控制台窗口的启动方式。
- 不要清理当前仓库之外的 Python、Node、Vite 或 dev.py 进程。

## 运行配置

- `CODEYUN_WATCHDOG_AUTOSTART`：是否在 CodeYun 启动时自举守护，默认启用。
- `CODEYUN_WATCHDOG_INTERVAL_SECONDS`：守护检查间隔，默认 60 秒。
- `CODEYUN_WATCHDOG_LOG`：守护日志路径，默认位于系统临时目录。
- `CODEYUN_WATCHDOG_LOCK`：守护 pid 文件路径，默认位于系统临时目录。
- `CODEYUN_STABILITY_POPUP_WINDOW_HOURS`：稳定性巡检的弹窗审计窗口，默认 2 小时。
- `CODEYUN_STABILITY_REQUEST_TIMEOUT`：稳定性巡检 HTTP 探针超时，默认 10 秒。
- `CODEYUN_STABILITY_STARTUP_GRACE_SECONDS`：console host 刚启动时允许健康探针暂时失败的宽限期，默认 180 秒。

`CODEYUN_WATCHDOG_AUTOSTART` 不是系统开机自启开关。系统登录自启由 Windows 计划任务
`CodeYun Watchdog` 管理；运行页中 CodeYun 本机守护的“配置”入口只创建或禁用这一个固定任务，不扫描其他启动项来源。

## 验收标准

- 手动启动 CodeYun 后，守护存在。
- 杀掉 CodeYun 服务组后，守护能重新拉起。
- 修改后端源码后，由用户打开的 `uv run dev.py` 控制台在原窗口内完成热加载；守护只观测 console host 心跳。
- 命令行主控存活时，即使健康探针短暂失败，守护也不杀掉主控或新建隐藏实例。
- 守护和 CodeYun 后台启动均不弹控制台窗口。
- `codeyun-dev.cmd` 从用户控制台启动 `dev.py`，不使用隐藏 Python。
- `scripts/codeyun_stability_check.py --install-task` 创建 2 小时巡检计划任务，任务命令使用 `pythonw.exe`。
- 稳定性巡检报告中 CodeYun 服务弹窗数为 0；若出现服务弹窗，报告状态必须进入 `attention_required`。
