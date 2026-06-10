# 凡修 data-annotation 运行设备约定

> Last Updated: 2026-06-02

## 当前事实

- 凡修脚本、data-annotation 标注、Runtime、Scheduler、守护和 MuMu 画面流都以 `codepc_mf` 为唯一运行目标。
- `codepc_mi15` 上的旧版凡修脚本和旧云手机流程已经退役，不再作为凡修运行、验证、截图、日志或资产树事实来源。
- `data-annotation` 资产树按设备 `entry_id` 存放；当前有效资产树是 `codepc_mf` 对应的 `30b82d72-8a76-4a74-be4b-4fc1591c6ce2.json`。
- `frontend/src/standard/fanxiu/game-window2` 是历史博物馆代码，不再注册为当前页面；当前“数据标注”页面是 `/fanxiu/data-annotation`，由 `data-annotation` 承载。
- 后端和前端里仍有 `game-window2` 命名的截图、点击、匹配、流式画面 API，这是给 `data-annotation` 复用的底层兼容能力，不代表 `game-window2` 页面仍在运行。

## 开发要求

- 凡修 data-annotation 页面默认选择 `codepc_mf`，不要再优先选择 `mi15`。
- 后端 Runtime、Scheduler、守护和调试验证都应使用 `codepc_mf` 入口。
- Runtime/守护截图与点击默认只使用本机 MuMu ADB 通道：优先尝试常规本机端口，例如 `127.0.0.1:7555/16416/5555`，也允许 `MuMuManager.exe info --vmindex all` 返回的当前本机实例 `adb_host_ip:adb_port`。抓包或 Android proxy 服务从普通 `adb devices` 发现的远端设备（如非 MuMuManager 返回的 `192.168.*:5555`）不是凡修 Runtime 的默认截图/点击目标。抓包服务可以使用 ADB 做代理配置或设备发现，但它发现到的设备不能自动进入 Runtime 截图/点击候选。确需调试远端设备时必须显式设置环境变量，不得由抓包服务自动混入。
- ADB 是 Runtime 的主通道；桌面窗口捕获只允许作为显式调试兜底，目标也必须是本机 `MuMu` 窗口，不再使用向日葵窗口标题或向日葵投屏通道。
- 当前 Runtime 底层模块边界：
  - `backend/core/fanxiu_mumu_control.py`：MuMu ADB、MuMu 窗口捕获兜底、截图匹配、点击/拖拽/输入。
  - `backend/core/window_capture_preview.py`：通用 Windows 窗口捕获工具。
  - 抓包服务只能负责网络代理、证书、pcap/数据流解析，不应提供截图、点击、OCR 或行为树设备候选。
- 不要重新注册 `/fanxiu/game-window2` 页面；需要做当前标注功能时改 `/fanxiu/data-annotation`。
- 若发现 `codepc_mi15` 下残留凡修 data-annotation 资产树、旧画面流或旧脚本文档，应视为历史残留清理对象，不要用它推断当前运行状态。
- 遇到“当前运行事实”和旧文档冲突时，以 `codepc_mf` 本机运行状态和这份约定为准。

## 常见误区

- 不要把考勤、小鹅通等其它业务里仍可能使用 `mi15` 的说明套到凡修 data-annotation。
- 不要把每设备一份资产树理解为凡修当前有多套有效标注。凡修当前只保留 mf 这套。
