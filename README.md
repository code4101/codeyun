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

## Run Convention

- 统一使用 `uv run` 执行 Python 相关命令
- 避免依赖全局 `python` 或其他项目虚拟环境
- 详细约定见 [AGENTS.md](AGENTS.md)

## Common Commands

```bash
# 后端测试
uv run pytest

# 仅启动前端
npm run dev --prefix frontend

# 仅启动后端（开发）
uv run python -m uvicorn backend.app:app --reload --host 0.0.0.0 --port 8000
```

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
