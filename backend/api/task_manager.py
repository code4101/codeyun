import subprocess
import threading
import sys
import os
import time
import datetime
import json
import uuid
import shlex
import socket
import requests
from collections import deque
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, HTTPException, BackgroundTasks, WebSocket, WebSocketDisconnect, Depends
from pydantic import BaseModel, Field
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlmodel import Session, select

from backend.api.websocket_manager import manager as ws_manager
from backend.core.auth import verify_api_token
from backend.core.device import device_manager, TaskStatus
from backend.db import engine
from backend.models import Task as TaskModel
from backend.models import UserDevice

import asyncio

router = APIRouter()

@router.on_event("startup")
async def startup_event():
    # We need to capture the loop here
    loop = asyncio.get_running_loop()

    def thread_safe_log_callback(task_id, line):
        try:
             asyncio.run_coroutine_threadsafe(ws_manager.broadcast_log(task_id, line), loop)
        except Exception as e:
            pass

    # Register callback on local device
    local_id = task_manager._get_local_device_id()
    device = device_manager.get_device(local_id)
    if device:
        device.set_log_callback(thread_safe_log_callback)

    # Start a background task to broadcast status periodically
    asyncio.create_task(status_broadcaster())

async def status_broadcaster():
    while True:
        try:
            # Broadcast task list if anyone is watching
            if "task_list" in ws_manager.rooms and ws_manager.rooms["task_list"]:
                await task_manager.broadcast_status()
            
            await asyncio.sleep(2) # 2s interval
        except Exception as e:
            print(f"Broadcaster error: {e}")
            await asyncio.sleep(5)

# --- API Models ---

class CreateTaskRequest(BaseModel):
    name: str
    command: str
    cwd: Optional[str] = None
    description: Optional[str] = None
    device_id: Optional[str] = Field(default_factory=socket.gethostname)
    schedule: Optional[str] = None
    timeout: Optional[int] = None

class UpdateTaskRequest(BaseModel):
    name: Optional[str] = None
    command: Optional[str] = None
    cwd: Optional[str] = None
    description: Optional[str] = None
    device_id: Optional[str] = None
    schedule: Optional[str] = None
    timeout: Optional[int] = None

class AddDeviceRequest(BaseModel):
    url: str
    token: str # Token is required to access the remote agent

class UpdateDeviceRequest(BaseModel):
    id: Optional[str] = None # New ID if renaming
    python_exec: Optional[str] = None
    token: Optional[str] = None # Allow updating token

# --- Manager ---

class TaskManager:
    def __init__(self):
        self.scheduler = BackgroundScheduler()
        self.scheduler.start()
        # Initial scan to sync state and restore timeouts
        self.scan_running_tasks(restore_timeouts=True)
        self.load_schedules()

    def _get_local_device_id(self) -> str:
        # Optimization: cache this?
        # But device_manager loads from DB now, so it should be fast
        for d in device_manager.devices.values():
            if d.to_dict().get('type') == 'LocalDevice':
                return d.device_id
        return socket.gethostname()

    def load_schedules(self):
        with Session(engine) as session:
            tasks = session.exec(select(TaskModel)).all()
            for task in tasks:
                if task.schedule:
                    self.update_schedule(task.id, task.schedule)

    def update_schedule(self, task_id: str, cron_expression: Optional[str]):
        # Remove existing job if any
        if self.scheduler.get_job(task_id):
            self.scheduler.remove_job(task_id)
        
        if cron_expression:
            try:
                self.scheduler.add_job(
                    self.start_task, 
                    CronTrigger.from_crontab(cron_expression), 
                    id=task_id, 
                    args=[task_id],
                    replace_existing=True
                )
                print(f"Scheduled task {task_id} with cron: {cron_expression}")
            except Exception as e:
                print(f"Failed to schedule task {task_id}: {e}")

    def scan_running_tasks(self, restore_timeouts: bool = False):
        # Scan local tasks
        local_id = self._get_local_device_id()
        
        with Session(engine) as session:
            # Get tasks for local device
            # Note: We need to filter by device_id.
            # Assuming TaskModel has device_id.
            stmt = select(TaskModel).where(TaskModel.device_id == local_id)
            local_tasks = session.exec(stmt).all()

        device = device_manager.get_device(local_id)
        if device:
            device.scan_running_tasks(local_tasks)
            
            # Only restore timeouts on startup (explicit request)
            if restore_timeouts:
                if hasattr(device, 'processes') and hasattr(device, '_watch_timeout'):
                     self._restore_timeouts(device, local_tasks)

    def _restore_timeouts(self, device, tasks):
        import psutil
        import threading
        import time

        print("Restoring timeout watchers for running tasks...")
        with device.lock:
            for task in tasks:
                if not task.timeout or task.timeout <= 0:
                    continue
                
                # Check if task is running
                if task.id in device.processes:
                    proc = device.processes[task.id]
                    try:
                        if not proc.is_running():
                            continue
                            
                        # Calculate remaining time
                        create_time = proc.create_time()
                        elapsed = time.time() - create_time
                        remaining = task.timeout - elapsed
                        
                        if remaining <= 0:
                            print(f"Task {task.id} expired during downtime (overdue by {-remaining:.1f}s). Stopping now.")
                            device.stop_task(task.id)
                        else:
                            print(f"Restoring watcher for Task {task.id} (PID {proc.pid}). Remaining: {remaining:.1f}s")
                            watcher = threading.Thread(
                                target=device._watch_timeout, 
                                args=(proc, remaining, task.id)
                            )
                            watcher.daemon = True
                            watcher.start()
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass

    async def broadcast_status(self):
        """Broadcast current status to all WS clients"""
        self.scan_running_tasks()
        
        with Session(engine) as session:
             # Sort by order, then created_at
             stmt = select(TaskModel).order_by(TaskModel.order, TaskModel.created_at)
             tasks = session.exec(stmt).all()
        
        results = []
        for t in tasks:
            status = self.get_task_status(t.id)
            # Convert SQLModel to dict
            t_dict = t.dict()
            t_dict["status"] = status.dict()
            results.append(t_dict)
            
        await ws_manager.broadcast("task_list", results)

    def start_task(self, task_id: str):
        with Session(engine) as session:
            task = session.get(TaskModel, task_id)
            if not task:
                 raise HTTPException(status_code=404, detail="Task not found")
            
            # Assuming task is local for now, or check task.device_id
            target_device_id = task.device_id
            
        device = device_manager.get_device(target_device_id)
        if not device:
            raise HTTPException(status_code=500, detail=f"Device {target_device_id} unavailable")
            
        try:
            # Pass command and env from DB
            result = device.start_task(task.id, task.command, task.cwd, env={}, timeout=task.timeout)
            if result.get("status") == "already_running":
                print(f"Task {task_id} skipped: already running (PID: {result.get('pid')})")
            return result
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    def stop_task(self, task_id: str):
        with Session(engine) as session:
            task = session.get(TaskModel, task_id)
            if not task:
                 return {"status": "not_found"}
            target_device_id = task.device_id

        device = device_manager.get_device(target_device_id)
        if not device:
             return {"status": "device_not_found"}
        return device.stop_task(task_id)

    def get_task_status(self, task_id: str):
        # We need to know which device this task belongs to
        # But get_task_status is often called in loop.
        # Optimization: The caller might already know the device?
        # For now, let's look it up.
        with Session(engine) as session:
            task = session.get(TaskModel, task_id)
            if not task:
                 return TaskStatus(id=task_id, running=False, message="Task not found in DB")
            target_device_id = task.device_id

        device = device_manager.get_device(target_device_id)
        if not device:
            return TaskStatus(id=task_id, running=False, message="Device unavailable")
        return device.get_task_status(task_id)

    def get_logs(self, task_id: str, lines: int = 50):
        with Session(engine) as session:
            task = session.get(TaskModel, task_id)
            if not task:
                 return ["Task not found"]
            target_device_id = task.device_id

        device = device_manager.get_device(target_device_id)
        if not device:
            return ["Device unavailable"]
        return device.get_logs(task_id, lines)
    
    def reorder_tasks(self, task_ids: List[str]):
        with Session(engine) as session:
            for idx, t_id in enumerate(task_ids):
                task = session.get(TaskModel, t_id)
                if task:
                    task.order = idx
                    session.add(task)
            session.commit()

    def find_related_processes(self, task_id: str) -> List[Dict[str, Any]]:
        with Session(engine) as session:
            task = session.get(TaskModel, task_id)
            if not task:
                return []
            target_device_id = task.device_id
            
        device = device_manager.get_device(target_device_id)
        if not device:
            return []
            
        return device.find_related_processes(task.command)

    def kill_process(self, pid: int) -> bool:
        # Default to local device for generic kill?
        # Or we need to know which device.
        # The API /process/kill doesn't specify device.
        # Assuming local.
        local_id = self._get_local_device_id()
        device = device_manager.get_device(local_id)
        if not device:
            return False
        return device.kill_process_by_pid(pid)

    def associate_process(self, task_id: str, pid: int):
        with Session(engine) as session:
            task = session.get(TaskModel, task_id)
            if not task:
                 raise HTTPException(status_code=404, detail="Task not found")
            target_device_id = task.device_id
            
        device = device_manager.get_device(target_device_id)
        if not device:
             raise HTTPException(status_code=500, detail="Device unavailable")
             
        result = device.associate_process(task_id, pid)
        if result.get("status") == "error":
            raise HTTPException(status_code=400, detail=result.get("message"))
            
        # Update Task Config in DB
        with Session(engine) as session:
            task = session.get(TaskModel, task_id) # Re-fetch to be safe
            if task:
                # Update command
                cmd_list = result.get("cmdline")
                if cmd_list:
                    if sys.platform == 'win32':
                        task.command = subprocess.list2cmdline(cmd_list)
                    else:
                        task.command = shlex.join(cmd_list)
                
                # Update CWD
                cwd = result.get("cwd")
                if cwd:
                    task.cwd = cwd
                elif "cwd" in result and result["cwd"] is None:
                     task.cwd = "Unknown"
                
                session.add(task)
                session.commit()
            
        return result

task_manager = TaskManager()

# --- Routes ---

# --- Device Directory Routes ---

@router.get("/devices")
def list_devices():
    try:
        device_manager.sync_remote_devices()
        devices = device_manager.get_all_devices()
        return devices
    except Exception as e:
        print(f"Error listing devices: {e}")
        return []

@router.post("/devices/add")
def add_device(req: AddDeviceRequest):
    try:
        device_id = None
        hostname = None
        python_exec = None
        
        try:
            url = req.url.rstrip('/')
            if not url.startswith('http'):
                url = 'http://' + url
                
            resp = requests.get(f"{url}/api/agent/status", headers={"X-Device-Token": req.token}, timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                device_id = data.get("id")
                hostname = data.get("hostname")
                python_exec = data.get("python_exec")
            else:
                raise HTTPException(status_code=400, detail=f"Remote status {resp.status_code}")
                    
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Connection failed: {str(e)}")
        
        if not device_id:
            raise HTTPException(status_code=400, detail="Could not determine remote device ID.")
        
        existing = device_manager.get_device(device_id)
        if existing and existing.to_dict()['type'] == 'LocalDevice':
             raise HTTPException(status_code=409, detail="Matches Local Device.")

        final_url = req.url
        if not final_url.startswith('http'):
            final_url = 'http://' + final_url
            
        name = hostname or "Unknown"
        device_manager.add_remote_device(device_id, name, final_url, python_exec, req.token)
        return {"status": "added", "id": device_id, "name": name}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/devices/{device_id}")
def delete_device(device_id: str, token_device: UserDevice = Depends(verify_api_token)):
    success = device_manager.remove_device(device_id)
    if not success:
         raise HTTPException(status_code=404, detail="Device not found")
    return {"status": "deleted"}

@router.post("/devices/{device_id}/update")
def update_device(device_id: str, req: UpdateDeviceRequest, token_device: UserDevice = Depends(verify_api_token)):
    if req.id:
        success = device_manager.rename_device(device_id, req.id)
        if not success:
            raise HTTPException(status_code=500, detail="Failed to rename.")
            
    if req.python_exec is not None:
        success = device_manager.update_device(device_id, req.python_exec)
        if not success:
            raise HTTPException(status_code=404, detail="Device not found")
    
    if req.token:
        # Update device token manually
        dev = device_manager.get_device(device_id)
        if dev and hasattr(dev, 'api_token'):
            dev.api_token = req.token
            device_manager._save_device_to_db(dev)

    return {"status": "updated", "id": device_id, "name": req.id}


# --- Task Routes ---

@router.get("/")
def list_tasks(
    token_device: UserDevice = Depends(verify_api_token)
):
    """
    List tasks.
    Filtered by the authenticated device (token).
    A device should only see its own tasks.
    """
    # Verify that the token belongs to a valid device
    # The dependency verify_api_token already returns the Device object
    
    requesting_device_id = token_device.id
    
    # 1. Scan running tasks on this device (Runtime check)
    # But wait, if this is a remote request, we can't scan 'local' tasks here.
    # If the request comes from a Remote Agent (asking for its tasks to run?), 
    # OR if the request comes from Frontend (via Proxy) asking for tasks of a specific device.
    
    # Current Architecture:
    # Frontend -> Backend (as Proxy) -> Remote Agent
    # OR
    # Frontend -> Backend (as Target) -> Local Tasks
    
    # When Frontend calls /api/task/ (via getDeviceApi which targets remote URL),
    # it hits THIS endpoint on the Remote Machine.
    # So 'token_device' is the device associated with the token used.
    # Wait, the token used by Frontend to talk to Remote is the "Master Token" of that Remote Device.
    # So `token_device` should be the Remote Device itself (self-reference).
    
    # So we should return all tasks where device_id == requesting_device_id.
    
    # However, if we are on the Master Backend, and we use a Token for a Remote Device,
    # that Token is stored in Master DB.
    # If we are on the Remote Backend (Agent), it has its own DB?
    # Or is the Agent stateless?
    
    # If the Agent is full CodeYun instance, it has its own DB.
    # The Token we use to connect to it is the "Master Token" of that Agent.
    # So `token_device` will be the LocalDevice of that Agent (if we set it up right).
    
    # Let's assume standard behavior: Return tasks for the device identified by the token.
    # If the token is the Master Token of the current machine, return all tasks for this machine.
    
    # 1. Sync runtime status
    # If we are the target device, we can scan our own processes.
    local_id = task_manager._get_local_device_id()
    if requesting_device_id == local_id:
        task_manager.scan_running_tasks()
    
    # 2. Fetch from DB
    with Session(engine) as session:
        # Filter by device_id
        stmt = select(TaskModel).where(TaskModel.device_id == requesting_device_id).order_by(TaskModel.order, TaskModel.created_at)
        tasks = session.exec(stmt).all()
    
    results = []
    for t in tasks:
        status = task_manager.get_task_status(t.id)
        t_dict = t.dict()
        t_dict["status"] = status.dict()
        results.append(t_dict)
        
    return results

@router.get("/list")
def list_tasks_deprecated():
    # Keep for backward compatibility if needed, but alias to root
    # Ideally should use dependency too.
    return [] 

@router.post("/create")
def create_task(
    req: CreateTaskRequest,
    token_device: UserDevice = Depends(verify_api_token)
):
    # Determine device ID
    # Default to the device associated with the token (self)
    # If device_id is provided in request, it must match the token's device_id
    # unless we are superuser? For now, enforce self-creation.
    
    target_device_id = token_device.device_id # Use device_id from UserDevice
    
    # Optional: Allow user to specify device_id if it matches token?
    if req.device_id and req.device_id != target_device_id:
        # In a proxy scenario, the proxy might forward the request.
        # But here we are the target agent.
        # So we should only create tasks for ourselves.
        pass # Ignore or warn? Let's use target_device_id.

    new_task = TaskModel(
        id=str(uuid.uuid4()),
        name=req.name,
        command=req.command,
        cwd=req.cwd,
        description=req.description,
        device_id=target_device_id,
        schedule=req.schedule,
        timeout=req.timeout,
        created_at=time.time(),
        order=0 # Default order
    )
    
    with Session(engine) as session:
        session.add(new_task)
        session.commit()
        session.refresh(new_task)
        
    if req.schedule:
        task_manager.update_schedule(new_task.id, req.schedule)
    
    task_manager.scan_running_tasks()
    return new_task

@router.delete("/{task_id}")
def delete_task(task_id: str, token_device: UserDevice = Depends(verify_api_token)):
    with Session(engine) as session:
        task = session.get(TaskModel, task_id)
        if task:
             # if task.device_id != token_device.device_id:
             #    raise HTTPException(status_code=403, detail="Cannot delete task of another device")
             
             task_manager.update_schedule(task_id, None)
             task_manager.stop_task(task_id)
             session.delete(task)
             session.commit()
             return {"status": "deleted"}
    raise HTTPException(status_code=404, detail="Task not found")

@router.post("/{task_id}/start")
def start_task_route(task_id: str, token_device: UserDevice = Depends(verify_api_token)):
    # Verify ownership
    with Session(engine) as session:
        task = session.get(TaskModel, task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        # if task.device_id != token_device.device_id:
        #    raise HTTPException(status_code=403, detail="Cannot access task of another device")
            
    return task_manager.start_task(task_id)

@router.post("/{task_id}/stop")
def stop_task_route(task_id: str, token_device: UserDevice = Depends(verify_api_token)):
    # Verify ownership
    with Session(engine) as session:
        task = session.get(TaskModel, task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        # if task.device_id != token_device.device_id:
        #    raise HTTPException(status_code=403, detail="Cannot access task of another device")

    return task_manager.stop_task(task_id)

@router.post("/{task_id}/update")
def update_task_route(task_id: str, req: UpdateTaskRequest, token_device: UserDevice = Depends(verify_api_token)):
    with Session(engine) as session:
        task = session.get(TaskModel, task_id)
        if task:
            # if task.device_id != token_device.device_id:
            #     raise HTTPException(status_code=403, detail="Cannot access task of another device")

            if req.name is not None: task.name = req.name
            if req.command is not None: task.command = req.command
            if req.cwd is not None: task.cwd = req.cwd
            if req.description is not None: task.description = req.description
            if req.schedule is not None:
                task.schedule = req.schedule
                task_manager.update_schedule(task_id, req.schedule)
            if req.timeout is not None:
                task.timeout = req.timeout
            
            session.add(task)
            session.commit()
            session.refresh(task)
            return task
    raise HTTPException(status_code=404, detail="Task not found")

@router.post("/reorder")
def reorder_tasks_route(task_ids: List[str], token_device: UserDevice = Depends(verify_api_token)):
    # Verify ownership of all tasks?
    # For performance, maybe just assume if one is ok?
    # Or verify all.
    # with Session(engine) as session:
    #    for t_id in task_ids:
    #        t = session.get(TaskModel, t_id)
    #        if t and t.device_id != token_device.device_id:
    #            raise HTTPException(status_code=403, detail="Cannot reorder tasks of another device")
    
    task_manager.reorder_tasks(task_ids)
    return {"status": "reordered"}

@router.get("/{task_id}")
def get_task_details(task_id: str, token_device: UserDevice = Depends(verify_api_token)):
    with Session(engine) as session:
        task = session.get(TaskModel, task_id)
        if task:
            # if task.device_id != token_device.device_id:
            #     raise HTTPException(status_code=403, detail="Cannot access task of another device")
            
            status = task_manager.get_task_status(task_id)
            return {
                **task.dict(),
                "status": status.dict()
            }
    raise HTTPException(status_code=404, detail="Task not found")

@router.get("/{task_id}/logs")
def get_task_logs(task_id: str, n: int = 500, token_device: UserDevice = Depends(verify_api_token)):
    with Session(engine) as session:
        task = session.get(TaskModel, task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        # if task.device_id != token_device.device_id:
        #    raise HTTPException(status_code=403, detail="Cannot access task of another device")
            
    logs = task_manager.get_logs(task_id, n)
    return {"logs": logs}

@router.get("/{task_id}/related_processes")
def get_related_processes(task_id: str, token_device: UserDevice = Depends(verify_api_token)):
    with Session(engine) as session:
        task = session.get(TaskModel, task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        # if task.device_id != token_device.device_id:
        #    raise HTTPException(status_code=403, detail="Cannot access task of another device")

    return task_manager.find_related_processes(task_id)

@router.post("/process/kill")
def kill_process_route(req: Dict[str, int], token_device: UserDevice = Depends(verify_api_token)):
    # Kill process on the requesting device
    # TaskManager.kill_process uses local device by default, 
    # BUT if this is a remote agent, local IS the target.
    # If this is master backend acting as proxy... wait.
    # Frontend calls /api/task/process/kill via proxy.
    # It hits this endpoint on Remote Agent.
    # Remote Agent (this code) calls task_manager.kill_process.
    # task_manager.kill_process gets local device. Correct.
    
    pid = req.get("pid")
    if not pid:
        raise HTTPException(status_code=400, detail="PID required")
    success = task_manager.kill_process(pid)
    if success:
        return {"status": "killed"}
    raise HTTPException(status_code=500, detail="Failed to kill process")

@router.post("/{task_id}/associate")
def associate_process_route(task_id: str, req: Dict[str, int], token_device: UserDevice = Depends(verify_api_token)):
    with Session(engine) as session:
        task = session.get(TaskModel, task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        if task.device_id != token_device.device_id:
            raise HTTPException(status_code=403, detail="Cannot access task of another device")

    pid = req.get("pid")
    if not pid:
        raise HTTPException(status_code=400, detail="PID required")
    return task_manager.associate_process(task_id, pid)

# --- WebSocket Endpoint ---

@router.websocket("/ws/logs/{task_id}")
async def websocket_logs(websocket: WebSocket, task_id: str, token_device: UserDevice = Depends(verify_api_token)):
    room = f"task_logs:{task_id}"
    # Token is already verified by dependency
    await ws_manager.connect(websocket, room)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket, room)
    except Exception as e:
        print(f"WS error: {e}")
        ws_manager.disconnect(websocket, room)

@router.websocket("/ws/tasks")
async def websocket_tasks(websocket: WebSocket, token_device: UserDevice = Depends(verify_api_token)):
    room = "task_list"
    # Token is already verified by dependency
    await ws_manager.connect(websocket, room)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket, room)
