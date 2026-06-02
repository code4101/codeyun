# 凡修 game-window3 运行设备约定

> Last Updated: 2026-06-02

## 当前事实

- 凡修脚本、game-window3 标注、Runtime、Scheduler、守护和 MuMu 画面流都以 `codepc_mf` 为唯一运行目标。
- `codepc_mi15` 上的旧版凡修脚本和旧云手机流程已经退役，不再作为凡修运行、验证、截图、日志或资产树事实来源。
- `game-window3` 资产树按设备 `entry_id` 存放；当前有效资产树是 `codepc_mf` 对应的 `30b82d72-8a76-4a74-be4b-4fc1591c6ce2.json`。
- `frontend/src/standard/fanxiu/game-window2` 是历史博物馆代码，不再注册为当前页面；当前“数据标注”页面是 `/fanxiu/data-annotation`，由 `game-window3` 承载。
- 后端和前端里仍有 `game-window2` 命名的截图、点击、匹配、流式画面 API，这是给 `game-window3` 复用的底层兼容能力，不代表 `game-window2` 页面仍在运行。

## 开发要求

- 凡修 game-window3 页面默认选择 `codepc_mf`，不要再优先选择 `mi15`。
- 后端 Runtime、Scheduler、守护和调试验证都应使用 `codepc_mf` 入口。
- 不要重新注册 `/fanxiu/game-window2` 页面；需要做当前标注功能时改 `/fanxiu/data-annotation`。
- 若发现 `codepc_mi15` 下残留凡修 game-window3 资产树、旧画面流或旧脚本文档，应视为历史残留清理对象，不要用它推断当前运行状态。
- 遇到“当前运行事实”和旧文档冲突时，以 `codepc_mf` 本机运行状态和这份约定为准。

## 常见误区

- 不要把考勤、小鹅通等其它业务里仍可能使用 `mi15` 的说明套到凡修 game-window3。
- 不要把每设备一份资产树理解为凡修当前有多套有效标注。凡修当前只保留 mf 这套。
