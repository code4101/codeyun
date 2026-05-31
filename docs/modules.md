# CodeYun Module Guide

[简体中文](modules.zh-CN.md)

This guide summarizes the public, general-purpose modules in CodeYun. The main README gives a short overview; this document explains what each module is for and how to start using the project locally.

## Installation

CodeYun is developed as a Python + Vue monorepo. Use the repository root as the working directory.

```bash
uv sync
npm install --prefix frontend
uv run dev.py
```

Open the local development services:

- Frontend: http://localhost:5173
- Backend API docs: http://localhost:8000/docs
- Public website: https://code4101.com/

Common checks:

```bash
uv run pytest
npm run check --prefix frontend
uv run python scripts/check_prod.py
```

## Knowledge Graph Notes

![Knowledge graph notes calendar view](assets/星图笔记.png)

The notes module is designed for structured knowledge work rather than plain text storage.

Core capabilities:

- Manage notes as entities with relationships and categories.
- Use shared rule programs to filter data across multiple views.
- Separate backend filtering from frontend visibility filtering.
- Switch between calendar, graph, and list-oriented workflows.
- Keep view state per tab while sharing cached note entities.

Typical use:

- Personal knowledge management.
- Project diaries and work logs.
- Topic maps for code, documents, and recurring workflows.
- Calendar-based review of important notes and activity clusters.

## Spreadsheet Workflows

CodeYun includes spreadsheet-oriented workflows for data import, rebuilding, validation, and API-backed access.

Core capabilities:

- Import structured spreadsheet data into backend-managed storage.
- Rebuild derived workbooks or datasets from repeatable scripts.
- Validate access rules and table-level behavior through tests.
- Expose spreadsheet-backed data through APIs and focused frontend pages.
- Keep reusable data operations in scripts instead of one-off manual edits.

Typical use:

- Small internal data tools.
- Repeatable workbook generation.
- Data migration and validation.
- Turning spreadsheet operations into maintainable backend workflows.

Related examples live under `scripts/`, `backend/api/`, `backend/core/`, and the spreadsheet-oriented docs in `docs/`.

## Cluster and Task Management

![Cluster and task management dashboard](assets/集群管理.png)

The cluster module provides a single operational surface for local and remote processes.

Core capabilities:

- Register local and remote devices.
- Start, stop, and inspect tasks from one UI.
- Track CPU and memory usage over time.
- Collect task status and logs.
- Keep long-lived services separate from the development server lifecycle.
- Define scheduled jobs without forcing local machine-specific schedules into source code.

Typical use:

- Manage self-hosted services on a workstation or small homelab.
- Supervise background Python scripts, sync tools, web services, and utility processes.
- Keep operational visibility without deploying a large monitoring stack.

## Job System

The job system is intended for reusable job types rather than hard-coded personal schedules.

Core capabilities:

- Add reusable job types to the catalog.
- Keep job execution logic in source code.
- Keep enablement, schedules, and next trigger times in local data configuration.
- Avoid shipping personal machine schedules into a clean deployment.

Typical use:

- Periodic cleanup.
- Data synchronization.
- Report generation.
- Maintenance checks.

## Visual and Utility Tools

![Visual production planning tool](assets/戴森球.png)

CodeYun can host focused tools inside the same authenticated frontend shell.

Core capabilities:

- Add domain-specific calculators and planners.
- Reuse shared layout, routing, permissions, and deployment conventions.
- Keep standalone tools discoverable from the platform navigation.
- Combine visual interfaces with backend APIs and local data files when needed.

Typical use:

- Visual planning tools.
- PDF/file helpers.
- AI-assisted utility pages.
- Diagnostics and developer productivity tools.

## Project Structure

```text
codeyun/
├── backend/          # FastAPI backend, APIs, services, standard modules
├── frontend/         # Vue/Vite frontend
├── docs/             # Design notes and module documentation
├── docs/assets/      # README and documentation images
├── scripts/          # Maintenance and data workflow scripts
├── tests/            # Backend tests
├── dev.py            # Integrated development supervisor
├── pyproject.toml
└── AGENTS.md
```

## Development Notes

- Use `uv run dev.py` for day-to-day development.
- Use `uv run pytest` before committing backend changes.
- Use `npm run check --prefix frontend` before frontend releases.
- Use `uv run python scripts/check_prod.py` for production-style local validation.
- Keep public docs focused on general-purpose modules and avoid environment-specific secrets, private paths, or personal account data.
