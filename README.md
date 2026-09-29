# CodeYun

个人超级工具集成平台，将知识管理、文件阅读、任务调度和日常自动化集中到一个 Web 界面中，支持本地运行与自托管。

网站：[code4101.com](https://code4101.com/)

## 主要功能

- **知识与阅读**：星图笔记、Project Graph、PDF 图书馆与多标签工作台。
- **任务与设备**：集群管理、任务调度、运行状态与日志查看。
- **数据与自动化**：表格数据处理、课程考勤及业务自动化工具。
- **实用工具**：文件处理、媒体下载、计算与可视化工具。

## 技术栈

- 后端：Python、FastAPI、SQLModel、SQLite。
- 前端：Vue 3、TypeScript、Vite、Element Plus。
- 开发工具：uv、npm、pytest。

## 本地开发

需要 Python 3.10+、uv 和 Node.js/npm。后端依赖见 [backend/pyproject.toml](backend/pyproject.toml)；其中包含本地路径依赖，首次部署需按实际环境配置。部分业务功能还依赖本地扩展、外部服务或设备。

在已配置 Python 依赖的环境中，从仓库根目录执行：

```bash
npm install --prefix frontend
uv run dev.py
```

默认访问 [前端页面](http://localhost:5173) 和 [后端 API 文档](http://localhost:8000/docs)。`dev.py` 统一启动并管理前后端开发服务。

环境配置支持 `.env` 和系统环境变量，可通过 `CODEYUN_DATA_DIR` 指定数据目录。个人数据、密钥和运行产物应保存在源码之外。

常用检查：

```bash
uv run pytest
npm run check --prefix frontend
```

## 代码结构

```text
backend/       后端 API、业务逻辑与测试
frontend/      前端页面、共享组件与工具模块
integrations/  外部项目集成
scripts/       维护与检查脚本
tests/         项目测试
docs/          项目文档
dev.py         开发环境启动入口
```

项目文档统一从 [文档中心](docs/文档中心.md) 进入，开发约定见 [AGENTS.md](AGENTS.md)。设计与实现细节以相关代码、类型和测试为准。

## 开源协议

本项目采用 [Apache License 2.0](LICENSE)。第三方组件和资源遵循各自的许可声明。
