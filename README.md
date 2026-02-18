# CodeYun (Code Cloud)

CodeYun is a personal super tool integration platform designed to provide a comprehensive solution for cross-device background task management, remote control, and system monitoring. It acts as a centralized hub for managing your digital environment.

## 🚀 Features

### 1. Cluster Task Manager
- **Unified Interface**: Manage tasks across multiple devices (local and remote) from a single dashboard.
- **Process Management**: Start, stop, and monitor processes based on PID with authoritative status tracking.
- **Task Scheduling**: Support for Cron-based scheduling to automate routine tasks.
- **Real-time Logs**: View live logs for running tasks to debug and monitor performance.
- **Drag-and-Drop Sorting**: Easily organize your task list with drag-and-drop functionality.

### 2. Multi-Device Agent System
- **Agent Mode**: Every CodeYun backend instance can function as an Agent, allowing you to build a cluster of managed devices.
- **Remote Control**: Add remote devices via URL and control their tasks and file systems seamlessly.
- **Device Discovery**: (Planned) Auto-discovery of devices on the local network.

### 3. File System Explorer
- **Remote Access**: Browse and manipulate files and directories on connected remote devices.
- **File Operations**: Support for basic file operations (view, edit, delete).

## 🛠️ Tech Stack

### Backend
- **Language**: Python 3.10+
- **Framework**: FastAPI (High-performance web framework)
- **Server**: Uvicorn (ASGI server)
- **Key Libraries**:
  - `psutil`: For system and process monitoring.
  - `apscheduler`: For advanced task scheduling.
  - `pydantic`: For data validation and settings management.

### Frontend
- **Framework**: Vue 3 (Composition API)
- **Build Tool**: Vite (Next-generation frontend tooling)
- **Language**: TypeScript
- **UI Library**: Element Plus
- **State Management**: Pinia (implied by store usage)
- **HTTP Client**: Axios

## 📦 Installation & Setup

### Prerequisites
- Python 3.10 or higher
- Node.js & npm (A local version is included in `tools/node` for convenience)

### Quick Start

The project includes a helper script `dev.py` to start both the backend and frontend services automatically.

1. **Clone the repository**:
   ```bash
   git clone <repository-url>
   cd codeyun
   ```

2. **Install Backend Dependencies**:
   It is recommended to use a virtual environment.
   ```bash
   # Create virtual environment
   python -m venv .venv
   
   # Activate virtual environment
   # Windows:
   .venv\Scripts\activate
   # Linux/Mac:
   source .venv/bin/activate

   # Install dependencies
   pip install -r backend/requirements.txt
   # OR if using pyproject.toml
   pip install -e backend
   ```

3. **Start Development Server**:
   Run the `dev.py` script from the root directory. This script will:
   - Check for a local Node.js environment.
   - Start the FastAPI backend on `http://localhost:8000`.
   - Install frontend dependencies (if missing) and start the Vite dev server on `http://localhost:5173`.

   ```bash
   python dev.py
   ```

4. **Access the Application**:
   Open your browser and navigate to:
   - **Frontend**: [http://localhost:5173](http://localhost:5173)
   - **Backend API Docs**: [http://localhost:8000/docs](http://localhost:8000/docs)

## 📂 Project Structure

```
codeyun/
├── backend/                # Python FastAPI Backend
│   ├── api/                # API Endpoints
│   ├── core/               # Core logic and utilities
│   ├── data/               # Data storage (JSON files, logs)
│   ├── tests/              # Backend tests
│   └── app.py              # Application entry point
├── frontend/               # Vue 3 + TypeScript Frontend
│   ├── src/
│   │   ├── api/            # API client wrappers
│   │   ├── components/     # Reusable Vue components
│   │   ├── views/          # Page views (TaskManager, etc.)
│   │   └── store/          # State management
│   └── vite.config.ts      # Vite configuration
├── tools/                  # Helper tools (e.g., local Node.js)
├── AGENTS.md               # Agent documentation and project status
├── TODO.md                 # Project roadmap and todo list
├── dev.py                  # Development startup script
└── start.ps1               # PowerShell startup script
```

## 📝 Configuration

- **Backend Config**: Configuration is managed via environment variables and local JSON files in `backend/data/`.
- **Frontend Config**: Vite configuration in `frontend/vite.config.ts`.

## 🤝 Contributing

1. Fork the repository.
2. Create your feature branch (`git checkout -b feature/AmazingFeature`).
3. Commit your changes (`git commit -m 'Add some AmazingFeature'`).
4. Push to the branch (`git push origin feature/AmazingFeature`).
5. Open a Pull Request.

## 📄 License

[MIT License](LICENSE) (Assuming MIT, please update if different)
