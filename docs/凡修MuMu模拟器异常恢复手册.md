# 凡修 MuMu 模拟器异常恢复手册

> Last Updated: 2026-06-16

本文记录 TapTap 内置 MuMu 模拟器出现「运行异常，请重启安卓设备尝试解决」时的诊断和恢复流程。

## 适用场景

典型现象：

- MuMu 窗口弹出「运行异常，请重启安卓设备尝试解决」。
- 凡修 Runtime/守护后续报 `ADB截图失败`、设备断开或取帧失败。
- `MuMuManager.exe info --vmindex 1` 显示外壳进程存在，但 `is_android_started=false`。

注意：这种情况下 `ADB截图失败` 通常是下游症状，不应先按 Runtime 截图链路排查。应先确认模拟器/安卓容器是否已经崩溃。

## 快速诊断

当前 TapTap 内置 MuMu 管理器路径：

```powershell
D:\TapTap\Support\android_emulator\engine\nx_main\MuMuManager.exe
```

查看实例状态：

```powershell
& 'D:\TapTap\Support\android_emulator\engine\nx_main\MuMuManager.exe' info --vmindex all
```

重点看实例 `1`：

- `is_process_started`
- `is_android_started`
- `player_state`
- `launch_err_code`
- `headless_pid`
- `adb_host_ip`
- `adb_port`

查看最近 MuMu 日志：

```powershell
Get-Content -Tail 200 D:\TapTap\Support\android_emulator\engine\vms\EGTapTap-12.0-1\logs\shell.log
```

搜索渲染崩溃：

```powershell
rg -n -i "VERR|GRAPHIC_CRASH|RuntimeError|showRuntimeError|Android error|Unknown crash|运行异常" `
  D:\TapTap\Support\android_emulator\engine\vms\EGTapTap-12.0-1\logs `
  D:\TapTap\Support\android_emulator\engine\vms\EGTapTap-12.0-1\data\exportLogs
```

2026-06-16 实例中确认过的直接崩溃信号：

```text
Android error: 900, VERR_UNKONW_GRAPHIC_CRASH
onPlayerHeadlessCrash: code=900 msg=VERR_UNKONW_GRAPHIC_CRASH
ShellWindow::showRuntimeErrorWin
Renderer::runDetector ... libEGL
```

这表示 MuMu 图形/渲染层崩溃，不是凡修业务任务逻辑直接报错。

## 资源压力检查

渲染崩溃常和系统资源压力同时出现。先看 Windows commit 和可用内存：

```powershell
Get-Counter '\Memory\Available MBytes','\Memory\Committed Bytes','\Memory\Commit Limit' |
  Select-Object -ExpandProperty CounterSamples |
  Select-Object Path,CookedValue
```

查看大内存进程：

```powershell
Get-Process |
  Sort-Object PrivateMemorySize64 -Descending |
  Select-Object -First 25 ProcessName,Id,
    @{n='WorkingGB';e={[math]::Round($_.WorkingSet64/1GB,2)}},
    @{n='PrivateGB';e={[math]::Round($_.PrivateMemorySize64/1GB,2)}},
    @{n='VirtualGB';e={[math]::Round($_.VirtualMemorySize64/1GB,2)}},
    StartTime,Path |
  Format-Table -AutoSize
```

Windows 事件日志里如果出现 `Microsoft-Windows-Resource-Exhaustion-Detector`，说明系统已经诊断到虚拟内存不足。此时直接点弹窗里的「立即重启」可能会再次进入同类崩溃，优先释放资源或重启模拟器实例。

### 提交内存与物理内存

任务管理器里的「已提交 x/y GB」不是已经插在机器上的物理内存，而是 Windows 对所有进程承诺的可提交内存额度。这个额度主要来自物理内存和页面文件。它接近上限时，即使「可用物理内存」还有几 GB，进程继续申请内存仍可能失败，虚拟机、浏览器、Python 和 WMI 这类长驻进程都容易受影响。

判断优先级：

- 物理内存高：说明 RAM 紧张，但不一定马上崩。
- 已提交接近上限：说明 commit limit 紧张，是真正会触发 `Resource-Exhaustion-Detector` 的风险点。
- `Pages/sec` 很高：说明系统正在频繁换页，虚拟机/图形层稳定性会下降。

不要把「清理内存」理解成可以安全释放 commit。修剪 working set 只会把部分物理页赶去待机/页面文件，通常不能释放进程已经提交的私有内存。真正能降低 commit 的方式是：

- 让泄漏/占用进程自己释放。
- 重启异常进程或服务。
- 关闭不必要的大进程。
- 增大页面文件，提高 commit limit。
- 重启系统，清掉异常服务和驱动状态。

自动化层不应默认重启 `Winmgmt`/WMI 这类系统服务。它可能影响任务管理器、IDE、监控、驱动管理和系统查询。当前策略是自动记录、告警和恢复 MuMu；是否重启系统服务由人工确认。

### 抓包、ADB 与外部工具影响

抓包和 ADB 会影响资源，但表现不同：

- ADB 高频截图、OCR、画面流会增加 `python.exe`、`adb.exe`、MuMu 进程的提交内存、句柄和 CPU 压力。
- Wireshark/dumpcap/Npcap 抓包会增加驱动缓冲、用户态抓包进程内存、磁盘写入和 CPU 压力。
- 反复通过 WMI 查询进程、服务、性能计数器的监控工具可能推高 `svchost.exe / Winmgmt` 的提交内存。若事件日志里最大占用者是 `svchost.exe` 且服务为 `Winmgmt`，优先按 WMI 异常或 WMI 查询压力排查；常规监控不要用 `Get-CimInstance`、`Get-WmiObject` 继续打 WMI，服务归属优先用 `sc.exe queryex type= service state= all` 或 CodeYun 的 `_windows_services_for_pid()`。
- CodeYun 凡修 Runtime 抓包默认让模拟器内 `tcpdump` 写远端 pcap 文件，再按段拉回并解析，不应在本机长期堆大内存包缓冲。packet sync 已限制为串行执行；如果上一段还在解析，新的 snapshot 同步会跳过，避免多个 tshark/JSON 解码线程叠加提交内存。

凡修 MuMu 健康日志会在异常/恢复事件中记录 `host_resources.commit`、`pressure_hints`、Top 私有内存进程和 svchost 承载服务。若出现 `windows_commit_nearly_exhausted`、`winmgmt_wmi_commit_growth`，应先处理宿主机提交内存压力，再调整 MuMu 配置。

## 标准恢复流程

优先使用 MuMuManager 官方控制命令，不先强杀进程。

关闭实例：

```powershell
& 'D:\TapTap\Support\android_emulator\engine\nx_main\MuMuManager.exe' control --vmindex 1 shutdown
```

确认已关闭：

```powershell
& 'D:\TapTap\Support\android_emulator\engine\nx_main\MuMuManager.exe' info --vmindex 1
```

期望：

```json
{
  "is_process_started": false,
  "is_android_started": false
}
```

重新启动：

```powershell
& 'D:\TapTap\Support\android_emulator\engine\nx_main\MuMuManager.exe' control --vmindex 1 launch
```

等待 20 到 60 秒后确认：

```powershell
& 'D:\TapTap\Support\android_emulator\engine\nx_main\MuMuManager.exe' info --vmindex 1
```

期望：

```json
{
  "is_process_started": true,
  "is_android_started": true,
  "player_state": "start_finished"
}
```

如果 `restart` 返回成功但 PID 不变、`is_android_started=false`，说明它没有真正从 runtime error 状态恢复，应改用 `shutdown -> launch`。

## 自动恢复守护

凡修行为树体系里可以把该能力归为 `device_health` 环境守护。它不是普通业务作业，也不是游戏内弹窗守护；它只负责确认 MuMu/安卓容器是否可用。

自动守护原则：

- 默认允许在确认 MuMu/安卓容器异常后执行 `shutdown -> launch` 自动恢复；可显式设置 `FANXIU_MUMU_DEVICE_AUTO_RECOVERY=0` 禁用。
- Runtime 页面「守护 / 设备健康」控制的是低频 `resident_heartbeat` 健康检查；ADB 取帧失败时仍有截图基础设施兜底恢复，用于在真实截图链路断开时尽快恢复可取帧状态。
- 低频设备健康守护和 ADB 取帧失败兜底共用同一套 MuMu 恢复锁、跨进程冷却和诊断日志，不应并发执行多个 `shutdown -> launch`。
- 常驻服务 tick 只读缓存状态，不每轮启动外部命令。
- 默认每 60 秒最多执行一次真实 `MuMuManager info` 健康检查。
- 单次 ADB 截图失败只累计失败，不立即重启模拟器。
- 连续 3 次 ADB 取帧失败后，才升级执行一次真实健康检查。
- 连续 ADB 失败本身也可以触发恢复：真实案例里出现过 `MuMuManager info` 仍显示 `is_android_started=true / player_state=start_finished`，但 `screencap` 连接被拒绝或被远端重置，此时应强制重建安卓容器。
- 健康检查确认 `is_process_started=true` 且 `is_android_started=false`，或实例停止等环境异常时，也执行恢复。
- 恢复动作全局互斥，并带冷却，避免多个作业同时 `shutdown -> launch` 或反复重启。
- 恢复冷却需要跨 CodeYun 后端热重载生效；当前实现把上次恢复时间写到系统临时目录
  `%TEMP%\codeyun\fanxiu_mumu_device_health\recovery_state.json`，避免 Python 进程重启后马上再次重建 MuMu。
- 设备健康守护会额外写 JSONL 诊断日志到 CodeYun 数据目录：
  `fanxiu/mumu-device-health/device-health-YYYYMMDD.jsonl`。日志记录 `adb_failure`、`health_check`、`recovery_start`、`recovery_success`、`recovery_failed` 和 `recovery_skipped`，并在异常/恢复事件中附带宿主机内存、swap/pagefile、Top 私有内存进程和 MuMu/TapTap 相关进程快照，供后续排查崩溃原因。
- 异常/恢复事件还会采集 MuMu 原生日志尾部，写入 `mumu_native` 字段。优先看 `mumu_native.suspected_causes` 和 `mumu_native.marker_lines`，用于区分渲染崩溃、VirtualBox/Hyper-V 错误、宿主资源压力和 ADB/RPC 超时。

恢复后只保证：

- MuMu 安卓容器重新启动。
- 凡修游戏包 `com.frxxcrjpwssc3.ggws` 被拉到前台。

恢复层不负责关闭公告、点击「进入游戏」、处理活动弹窗或推进游戏流程。这些仍交给 Runtime 场景识别、已标注弹窗守护和具体业务任务处理。

## 启动凡修游戏

凡修游戏包名：

```text
com.frxxcrjpwssc3.ggws
```

不必一定通过 TapTap GUI 的「开始游戏」按钮，可以直接启动已安装游戏包：

```powershell
& 'D:\TapTap\Support\android_emulator\engine\nx_main\MuMuManager.exe' control --vmindex 1 app launch --package com.frxxcrjpwssc3.ggws
```

确认前台 Activity：

```powershell
& 'D:\TapTap\Support\android_emulator\engine\nx_main\MuMuManager.exe' adb --vmindex 1 shell dumpsys window |
  Select-String -Pattern 'mCurrentFocus|mFocusedApp'
```

期望看到：

```text
com.frxxcrjpwssc3.ggws/com.flamePhoenix.plugin.activity.FlameUnityActivity
```

## 恢复后验证

保存当前画面到系统临时目录：

```powershell
$dir = Join-Path $env:TEMP 'codeyun\fanxiu_recovery'
New-Item -ItemType Directory -Force -Path $dir | Out-Null
$out = Join-Path $dir 'final_game_state.png'
& 'D:\TapTap\Support\android_emulator\engine\nx_main\MuMuManager.exe' adb --vmindex 1 shell screencap -p /sdcard/codex_final_game_state.png
& 'D:\TapTap\Support\android_emulator\engine\nx_main\MuMuManager.exe' adb --vmindex 1 pull /sdcard/codex_final_game_state.png $out
Write-Output $out
```

如果画面停在公告、活动弹窗或登录页，这是游戏正常启动后的状态，不是模拟器异常。可以按恢复需要手动关闭弹窗或点击「进入游戏」，但不要把这些临时坐标沉淀进 Runtime 业务逻辑。

## 处理边界

- 这份手册只处理 MuMu/TapTap 运行环境恢复，不改凡修 Runtime/Scheduler 行为。
- 不要把 `ADB截图失败` 单独当成根因；先看 MuMu `is_android_started` 和渲染崩溃日志。
- 不要自动补标「运行异常」弹窗或把恢复坐标写入业务任务。它属于模拟器运维故障，不是游戏业务流程。
- 如果 Windows commit 长期接近上限，后续仍可能复发。应继续排查大内存进程，例如异常 `svchost.exe`、长驻 Python、IDE 或浏览器进程。
