import os
import sys
import subprocess
import threading
import psutil
import socket
from typing import Optional, List, Dict
import platform

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from backend.core.auth import verify_api_token
from backend.core.device import match_cmdline, TaskStatus, get_system_id, device_manager, LocalDevice

router = APIRouter(dependencies=[Depends(verify_api_token)])


class MatchProcessItem(BaseModel):
    id: str
    command: str


class MatchProcessesRequest(BaseModel):
    tasks: List[MatchProcessItem]


@router.post("/match_processes")
def match_processes(req: MatchProcessesRequest):
    """
    Check if processes are running for the given list of tasks.
    Returns a map of task_id -> TaskStatus
    """
    results = {}

    # Snapshot processes
    procs = []
    try:
        procs = list(psutil.process_iter(["pid", "name", "cmdline", "create_time", "cpu_percent", "memory_info"]))
    except:
        pass

    used_pids = set()

    for task in req.tasks:
        found = False
        for proc in procs:
            if proc.pid in used_pids:
                continue

            try:
                cmdline = proc.info["cmdline"]
                if not cmdline:
                    continue

                if match_cmdline(task.command, cmdline):
                    found = True
                    used_pids.add(proc.pid)

                    # Construct status
                    try:
                        mem = proc.info["memory_info"].rss if proc.info["memory_info"] else 0
                    except:
                        mem = 0

                    results[task.id] = {
                        "id": task.id,
                        "running": True,
                        "pid": proc.pid,
                        "started_at": proc.info["create_time"],
                        "cpu_percent": proc.info["cpu_percent"],  # Note: this might be 0.0 on first call
                        "memory_rss": mem,
                    }
                    break
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        if not found:
            results[task.id] = {"id": task.id, "running": False}

    return results


class ExecCmdRequest(BaseModel):
    command: str
    cwd: Optional[str] = None
    env: Optional[Dict[str, str]] = None


@router.post("/exec_cmd")
def execute_command(req: ExecCmdRequest):
    """
    Execute a shell command.
    This is the atomic capability for RemoteDevice.
    """
    # Security check: In production, this needs authentication!
    try:
        # Prepare environment
        run_env = os.environ.copy()
        if req.env:
            run_env.update(req.env)

        # Use subprocess to run
        # Note: This is a synchronous call for simple commands.
        # For long-running tasks, we might need async or background tasks,
        # but for now, we assume this is used for short commands or starting detached processes.

        # However, TaskManager expects to start a long-running process and get a PID.
        # So we need to spawn a process and return immediately.

        creationflags = 0
        if sys.platform == "win32":
            creationflags = 0x08000000  # CREATE_NO_WINDOW

        import shlex

        try:
            cmd_args = shlex.split(req.command, posix=(sys.platform != "win32"))
        except:
            cmd_args = req.command.split()

        # Fix python path if needed (similar to LocalDevice logic)
        # But here we are the remote agent, so we use our own python or configured one

        proc = subprocess.Popen(
            cmd_args,
            cwd=req.cwd,
            env=run_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            creationflags=creationflags,
            close_fds=True,
        )

        # We need to manage this process if we want to stream logs or stop it later.
        # But wait, RemoteDevice logic in Device.py expects to just send a command.
        # If we want full feature parity (logs, status), the Agent needs its own TaskManager!

        # This confirms the architecture:
        # Master TaskManager -> RemoteDevice -> Slave Agent -> Slave TaskManager

        # So this Agent API should actually delegate to the Slave's TaskManager?
        # Or should we keep it simple for now?

        # If we want "start task" on remote, we should call the remote's /api/tasks/create and /start?
        # Yes, that makes more sense. RemoteDevice should act as a client to the Remote's TaskManager API.

        # But wait, the user wants "Remote Agent" capability.
        # If every machine runs full codeyun, they all have TaskManager.
        # So RemoteDevice just calls the standard TaskManager API on the remote machine.

        # But we also need raw "exec_cmd" for ad-hoc commands (like listing files).

        return {
            "status": "started",
            "pid": proc.pid,
            "message": "Process started. Note: Log streaming for raw exec_cmd is not yet implemented.",
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status")
def get_status():
    # Try to find local device name
    hostname = socket.gethostname()
    system_id = get_system_id()
    
    # Check if device manager has a name for this system
    local_dev = device_manager.get_device(system_id)
    python_exec = None
    if local_dev:
        hostname = local_dev.name
        python_exec = local_dev.python_exec
        
    return {
        "status": "ok", 
        "hostname": hostname, 
        "platform": sys.platform,
        "id": system_id,
        "python_exec": python_exec
    }

class RenameRequest(BaseModel):
    name: str

@router.post("/rename")
def rename_agent(req: RenameRequest):
    # Use global device_manager and get_system_id imported at top level
    # to avoid potential ImportError with relative imports inside function
    
    system_id = get_system_id()
    
    # Use the manager's method which handles DB persistence
    success = device_manager.rename_device(system_id, req.name)
    
    if not success:
        return {"status": "error", "message": "Failed to rename local device"}
    
    return {"status": "renamed", "name": req.name}

class ConfigRequest(BaseModel):
    python_exec: Optional[str] = None

@router.post("/config")
def update_config(req: ConfigRequest):
    system_id = get_system_id()
    
    # Update local configuration
    success = device_manager.update_device(system_id, python_exec=req.python_exec)
    
    if not success:
        return {"status": "error", "message": "Failed to update config"}
        
    return {"status": "updated", "python_exec": req.python_exec}
