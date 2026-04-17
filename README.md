# CodeYun

CodeYun 是一个个人工具平台，核心包含：
- 多设备任务管理（启动、停止、调度、日志）
- 笔记与关系图谱
- 一些实用工具页面

## Quick Start

在项目根目录执行（`D:\home\chenkunze\slns\codeyun`）：

```bash
uv sync
npm install --prefix frontend
uv run dev.py
```

启动后访问：
- Frontend: http://localhost:5173
- Backend API: http://localhost:8000/docs

`dev.py` 仅用于开发调试：后端默认使用 `outer` 模式，由 `dev.py` 外层监督；默认每 `5` 秒检查一次前后端进程是否仍在，后端源码变化后会等待 `60` 秒静默期再重启，前端仍使用 `vite dev`。`dev.py` 只托管它自己拉起的前后端，集群管理里的 task 进程保持独立，不随 `dev.py` 一起被清理。
这些默认值可通过环境变量覆盖：
- `CODEYUN_DEV_CHECK_INTERVAL_SECONDS`
- `CODEYUN_DEV_BACKEND_RELOAD_COOLDOWN_SECONDS`
- `CODEYUN_DEV_BACKEND_RELOAD_MODE`

如需切回 `uvicorn --reload`，可执行 `uv run dev.py --backend-reload-mode uvicorn`。
仓库内原有的 GitHub Actions 自动部署链路已移除；如需恢复旧方案，参考 [docs/自动部署恢复档案.md](docs/自动部署恢复档案.md)。当前生产部署不依赖仓库内的 `deploy/` 目录或自动 workflow。

## Run Convention

- 统一使用 `uv run` 执行 Python 相关命令
- 避免依赖全局 `python` 或其他项目虚拟环境
- 详细约定见 [AGENTS.md](AGENTS.md)

## Common Commands

```bash
# 后端测试
uv run pytest

# 前端开发
npm run dev --prefix frontend

# 仅启动后端（开发）
uv run python -m uvicorn backend.app:app --reload --host 0.0.0.0 --port 8000

# 前端生产检查（类型检查 + vite build）
npm run check --prefix frontend

# 本地生产式检查（含 Linux 大小写兼容性预检查）
uv run python scripts/check_prod.py
```

## Environment Modes

- `development`: `dev.py` 强制使用，保留热更新和开发默认 CORS
- `test`: 测试环境使用，避免读取本地 `.env`
- `production`: 生产部署使用，默认关闭 `/docs` 和 `/openapi.json`

后端环境配置集中在 [`backend/core/settings.py`](backend/core/settings.py)，可通过 `.env` 或系统环境变量覆盖。

## Recommended Workflow

平时开发可以一直跑：

```bash
uv run dev.py
```

开发中途想尽早发现前端构建问题，可以单独执行：

```bash
npm run check --prefix frontend
```

提交或部署前，再执行一次完整的本地生产式检查：

```bash
uv run python scripts/check_prod.py
```

这样能提前发现只会在构建产物、生产式启动或 Linux 环境下暴露的问题，比如资源路径、打包异常、生产 CORS、docs 暴露、以及大小写路径在 Linux 下失效等。

## Project Layout

```text
codeyun/
├── backend/
├── frontend/
├── tests/
├── dev.py
├── pyproject.toml
└── AGENTS.md
```

## License

[Apache License 2.0](LICENSE)
