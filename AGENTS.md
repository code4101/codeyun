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
