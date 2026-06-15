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

CodeYun 本机守护
  -> 独立进程，使用系统临时目录中的 pid/log
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
uv run python -m compileall -q backend scripts dev.py
```

可通过 `CODEYUN_WATCHDOG_RELOAD_CHECK_COMMAND` 覆盖；设为空字符串表示跳过预检查。

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

## 验收标准

- 手动启动 CodeYun 后，守护存在。
- 杀掉 CodeYun 服务组后，守护能重新拉起。
- 修改后端源码后，守护等待静默期再尝试重启。
- 预检查失败时旧服务继续可用。
- 守护和 CodeYun 后台启动均不弹控制台窗口。
