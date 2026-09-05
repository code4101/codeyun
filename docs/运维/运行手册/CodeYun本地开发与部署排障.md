# CodeYun 本地开发与部署排障

## 本地开发主入口

- 统一使用 `uv run dev.py` 启动前后端；它是长驻进程，终端或工具等待超时不等于启动失败。
- 仅在 `uv` 不可用时使用 `.\.venv\Scripts\python.exe dev.py`，不要依赖全局 Python。
- 后台诊断日志写入 `%TEMP%\codeyun\...`，并分离 stdout/stderr；不得把服务日志写进仓库。

## 启动核查

先检查端口和受控进程，不使用 WMI 扫描进程命令行：

```powershell
netstat -ano | Select-String ':8000|:5173'
Get-Process python,node,uv -ErrorAction SilentlyContinue | Select-Object Id,ProcessName,Path
```

成功判据：

- 前端日志出现 `VITE ... ready`。
- 后端日志出现 `Application startup complete`。

重复调试前只清理命令行明确包含当前仓库 `dev.py`、`uvicorn` 或 `vite` 的残留进程，不能扩大到其他仓库或普通 Python/Node 进程。排查顺序为：

1. `uv sync`，确保依赖与锁文件一致。
2. 查看后端错误日志，优先处理导入和依赖错误。
3. 检查端口占用与重复进程。

## 部署现状

- 仓库内 GitHub Actions 自动部署链路已于 `2026-04-16` 移除；恢复旧方案只参考 [自动部署恢复档案](../../归档/自动部署恢复档案.md)。
- 服务器历史运行口径是系统级 `systemd` 服务 `codeyun-backend`，不是 `systemctl --user`；相关模板当前不在仓库中。
- 服务器 `.env` 只存应用配置，不存 SSH 登录信息。
- `CODEYUN_DATA_DIR` 可选；未配置时使用仓库外的 `C:\home\chenkunze\data\m2603codeyun\codepc_<本机名>`，不得回落到 `backend/data/`。

更完整的本机主控、守护和进程所有权设计见 [CodeYun 本机守护设计约定](../../平台/架构/CodeYun本机守护设计约定.md)。
