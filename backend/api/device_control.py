import os
import socket
import subprocess
import sys
from typing import Dict, List, Optional

import psutil
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.core.auth import verify_api_token
from backend.core.device import device_manager, get_device_id, match_cmdline

router = APIRouter(dependencies=[Depends(verify_api_token)])


class MatchProcessItem(BaseModel):
    id: str
    command: str


class MatchProcessesRequest(BaseModel):
    tasks: List[MatchProcessItem]


@router.post("/match_processes")
def match_processes(req: MatchProcessesRequest):
    results = {}

    procs = []
    try:
        procs = list(
            psutil.process_iter(
                ["pid", "name", "cmdline", "create_time", "cpu_percent", "memory_info"]
            )
        )
    except Exception:
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

                    try:
                        mem = proc.info["memory_info"].rss if proc.info["memory_info"] else 0
                    except Exception:
                        mem = 0

                    results[task.id] = {
                        "id": task.id,
                        "running": True,
                        "pid": proc.pid,
                        "started_at": proc.info["create_time"],
                        "cpu_percent": proc.info["cpu_percent"],
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
    try:
        run_env = os.environ.copy()
        if req.env:
            run_env.update(req.env)

        creationflags = 0
        if sys.platform == "win32":
            creationflags = 0x08000000

        import shlex

        try:
            cmd_args = shlex.split(req.command, posix=(sys.platform != "win32"))
        except Exception:
            cmd_args = req.command.split()

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

        return {
            "status": "started",
            "pid": proc.pid,
            "message": "Process started. Note: Log streaming for raw exec_cmd is not yet implemented.",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status")
def get_device_control_status():
    hostname = socket.gethostname()
    device_id = get_device_id()

    local_dev = device_manager.get_device(device_id)
    python_exec = None
    if local_dev:
        python_exec = local_dev.python_exec

    return {
        "status": "ok",
        "hostname": hostname,
        "platform": sys.platform,
        "id": device_id,
        "python_exec": python_exec,
    }


class RenameRequest(BaseModel):
    name: str


@router.post("/rename")
def rename_device(req: RenameRequest):
    _ = req
    raise HTTPException(status_code=400, detail="本机设备名称请通过用户入口别名管理，不再支持设备面重命名")


class ConfigRequest(BaseModel):
    python_exec: Optional[str] = None


@router.post("/config")
def update_device_control_config(req: ConfigRequest):
    _ = req
    raise HTTPException(status_code=400, detail="本机运行配置不再支持通过该接口持久化")
