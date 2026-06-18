# CodeYun 本机守护设计约定

## 背景与目标

CodeYun 需要在本机长期运行，并且不能因为后端导入失败、前端异常、坏代码热加载、窗口弹出或多实例冲突影响日常使用。为此，本机守护必须是 CodeYun 主服务之外的独立服务。

目标：

- CodeYun 每次启动时都确认本机守护存在；没有则启动。
- 守护独立于 CodeYun 主进程；CodeYun 崩溃不能带死守护。
- 守护负责恢复 CodeYun 可用性，并在代码稳定、检查通过后触发重启。
- 启动器负责单实例收敛；守护只决定何时调用启动器。
- 后台运行不得弹出控制台窗口。

## 总体设计

```text
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
  -> 确认可见控制台弹窗监控器存活
  -> 定期检查后端和前端健康状态
  -> 服务不可用时调用 dev.py 重新拉起
  -> 服务健康时监控源码变化
  -> 变化稳定且预检查通过后调用 dev.py 重启

dev.py
  -> 单次启动器
  -> 启动前清理当前仓库旧 CodeYun 服务组
  -> 启动一个后端和一个前端
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
- 后端或前端异常时负责恢复 CodeYun。
- 代码变化时负责判断是否适合重启。
- 24 小时弹窗观察期内负责保持可见控制台监控器存活；监控器消失时重新拉起。
- 自身必须足够保守，避免重启风暴。

### dev.py

`dev.py` 是单次启动器：

- 启动前清理当前仓库的旧 CodeYun dev runner、uvicorn、Vite 和端口占用。
- 只清理命令行或工作目录明确指向当前仓库的进程。
- 不负责长期判断“何时该重启”。
- 不默认关闭守护自启动。

## 重启策略

守护区分两种重启。

### 恢复型重启

触发条件：

- 后端健康检查失败。
- 前端健康检查失败。
- CodeYun 服务组消失或端口不可用。

行为：

- 如果服务仍在启动宽限期内，等待。
- 超过宽限期仍不可用，则调用 `dev.py` 拉起。
- 不要求源码静默期，也不执行热加载预检查。

### 热加载型重启

触发条件：

- CodeYun 当前健康。
- 被监控源码文件发生变化。

行为：

1. 等待静默期，默认 120 秒。
2. 静默期内又有变化则重新计时。
3. 静默期结束后执行预检查。
4. 预检查通过才调用 `dev.py` 重启。
5. 预检查失败则保留旧服务，记录日志，并重新等待下一个静默期。

默认预检查：

```bash
pythonw.exe -m compileall -q backend scripts dev.py
```

可通过 `CODEYUN_WATCHDOG_RELOAD_CHECK_COMMAND` 覆盖；设为空字符串表示跳过预检查。

## 后台子进程启动规范

CodeYun 常驻服务、守护、热加载预检查、ADB/MuMu/tshark 调用和公网前端构建，必须统一使用
`backend.core.runtime.process_launcher`：

- `run_quiet(...)`：一次性短命令，默认禁止 Windows 控制台弹出。
- `check_call_quiet(...)`：需要失败即抛出的短命令。
- `check_output_quiet(...)`：需要读取 stdout 的短命令。
- `popen_service(...)`：非 Python 的后台长驻进程，默认独立进程组、脱离控制台和当前 job。
- `python_module_service_command(...)` / `popen_python_module_service(...)`：后台 Python 模块服务。
- `python_script_service_command(...)` / `popen_python_script_service(...)`：后台 Python 脚本服务。
- `node_npm_command(...)` / `node_script_command(...)`：后台 Node/NPM 命令，Windows 下绕开 `npm.cmd`。
- `apply_background_node_env(...)`：为后台 Node 工具注入 child_process 隐藏窗口补丁。

`backend.core.runtime.subprocess_utils` 是底层实现层，只保存 Windows flags、`pythonw` 解析、`node npm-cli.js`
解析等细节。业务模块、API 模块、守护脚本和 `dev.py` 不应直接导入它；新增调用必须走
`process_launcher`。

底层默认策略：

- 后台 Python 优先用 `resolve_pythonw(ROOT_DIR, sys.executable)`，Windows 下优先 `.venv/Scripts/pythonw.exe`。
- `dev.py` 监督器本身属于后台 Python 服务，必须用 `pythonw.exe dev.py` 启动，避免 Windows Terminal 为监督器分配伪控制台。
- `dev.py` 内部拉起 `uvicorn` 后端时例外：必须用 `resolve_python(ROOT_DIR, sys.executable)` 选择 `python.exe`，
  再通过隐藏启动 flags 和断开的 stdio 禁止弹窗。不要用 `pythonw.exe` 运行 uvicorn；它可能丢失正常 stdio 语义并卡在启动期。
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

## 可见控制台监控

弹窗问题的 24 小时观察不能只看“日志里没有事件”，还必须证明监控器本身持续有效。

- 正式监控脚本为 `scripts/codeyun_visible_console_monitor.py`。
- 审计入口为 `scripts/codeyun_popup_audit.py --ensure-monitor`。
- 审计状态中的 `coverage_valid=true` 才表示监控覆盖有效。
- 如果监控器死亡，审计入口和 CodeYun 本机守护都会用无窗口方式重新拉起。
- 监控器死亡后重新拉起时，24 小时无弹窗的有效观察窗口必须重新计算；不能把监控断档前后的时间拼起来当作连续证据。
- 当前 24 小时基线写在系统临时目录 `codeyun/visible-console-monitor/codeyun_popup_24h_baseline.json`。

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
- `CODEYUN_WATCHDOG_RELOAD`：是否启用守护式热加载，默认启用。
- `CODEYUN_WATCHDOG_RELOAD_QUIET_SECONDS`：热加载静默期，默认 120 秒。
- `CODEYUN_WATCHDOG_RELOAD_CHECK_COMMAND`：热加载预检查命令。
- `CODEYUN_WATCHDOG_LOG`：守护日志路径，默认位于系统临时目录。
- `CODEYUN_WATCHDOG_LOCK`：守护 pid 文件路径，默认位于系统临时目录。

`CODEYUN_WATCHDOG_AUTOSTART` 不是系统开机自启开关。系统登录自启由 Windows 计划任务
`CodeYun Watchdog` 管理；运行页中 CodeYun 本机守护的“配置”入口只创建或禁用这一个固定任务，不扫描其他启动项来源。

## 验收标准

- 手动启动 CodeYun 后，守护存在。
- 杀掉 CodeYun 服务组后，守护能重新拉起。
- 修改后端源码后，守护等待静默期再尝试重启。
- 预检查失败时旧服务继续可用。
- 守护和 CodeYun 后台启动均不弹控制台窗口。
