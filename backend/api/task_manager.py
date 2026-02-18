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
from fastapi import APIRouter, HTTPException, BackgroundTasks, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

# Import Device Manager
try:
    from ..core.device import device_manager, TaskStatus
    from .websocket_manager import manager as ws_manager
except ImportError:
    sys.path.append(os.path.join(os.path.dirname(os.path.dirname(__file__))))
    from core.device import device_manager, TaskStatus
    from api.websocket_manager import manager as ws_manager

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
    local_id = task_manager.store._get_local_device_id()
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

# --- Models ---

class TaskConfig(BaseModel):
    id: str
    name: str
    command: str
    cwd: Optional[str] = None
    env: Optional[Dict[str, str]] = None
    created_at: float
    description: Optional[str] = None
    device_id: str = Field(default_factory=socket.gethostname)
    schedule: Optional[str] = None # Cron expression
    timeout: Optional[int] = None # Timeout in seconds

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

class UpdateDeviceRequest(BaseModel):
    id: Optional[str] = None # New ID if renaming
    python_exec: Optional[str] = None

# --- Persistence ---
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
TASKS_FILE = os.path.join(DATA_DIR, "tasks.json")

class TaskStore:
    def __init__(self, data_dir: str = DATA_DIR):
        self.data_dir = data_dir
        self.tasks: Dict[str, TaskConfig] = {}
        self.load()

    def _get_local_device_id(self) -> str:
        try:
            for d in device_manager.devices.values():
                if d.to_dict().get('type') == 'LocalDevice':
                    return d.device_id
        except Exception:
            pass
        return socket.gethostname()

    def load(self):
        # Load local tasks only
        local_id = self._get_local_device_id()
        device_dir = os.path.join(self.data_dir, local_id)
        device_tasks_path = os.path.join(device_dir, "tasks.json")
        
        # Also check legacy path for migration
        legacy_file_path = os.path.join(self.data_dir, "tasks.json")

        if os.path.exists(legacy_file_path):
             try:
                with open(legacy_file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                    if content.strip():
                        data = json.loads(content)
                        self._load_items(data)
             except Exception as e:
                print(f"Error loading legacy tasks file: {e}")

        if os.path.exists(device_tasks_path):
            try:
                with open(device_tasks_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                    if content.strip():
                        data = json.loads(content)
                        self._load_items(data)
            except Exception as e:
                print(f"Error loading tasks from {device_tasks_path}: {e}")

    def _load_items(self, data: List[Dict]):
        local_id = self._get_local_device_id()
        hostname = socket.gethostname()
        
        for item in data:
            try:
                if 'device_id' not in item:
                    item['device_id'] = local_id
                if item.get('device_id') == 'local':
                    item['device_id'] = local_id
                
                # Filter: Only keep tasks that belong to THIS device
                if item['device_id'] not in [local_id, hostname]:
                    continue

                task = TaskConfig(**item)
                self.tasks[task.id] = task
            except Exception as e:
                print(f"Error loading individual task: {e}")

    def save(self):
        try:
            local_id = self._get_local_device_id()
            local_tasks = list(self.tasks.values())
            
            device_dir = os.path.join(self.data_dir, local_id)
            if not os.path.exists(device_dir):
                os.makedirs(device_dir)
            
            tasks_file = os.path.join(device_dir, "tasks.json")
            
            with open(tasks_file, 'w', encoding='utf-8') as f:
                json.dump([t.dict() for t in local_tasks], f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"Error saving tasks: {e}")

    def add_task(self, task: TaskConfig):
        self.tasks[task.id] = task
        self.save()

    def remove_task(self, task_id: str):
        if task_id in self.tasks:
            del self.tasks[task_id]
            self.save()

    def get_task(self, task_id: str) -> Optional[TaskConfig]:
        return self.tasks.get(task_id)

    def reorder_tasks(self, ordered_ids: List[str]):
        new_tasks = {}
        for task_id in ordered_ids:
            if task_id in self.tasks:
                new_tasks[task_id] = self.tasks[task_id]
        
        for task_id, task in self.tasks.items():
            if task_id not in new_tasks:
                new_tasks[task_id] = task
        
        self.tasks = new_tasks
        self.save()

    def get_all_tasks(self) -> List[TaskConfig]:
        return list(self.tasks.values())

# --- Manager ---

class TaskManager:
    def __init__(self, store: Optional[TaskStore] = None):
        self.store = store or TaskStore()
        self.scheduler = BackgroundScheduler()
        self.scheduler.start()
        # Initial scan to sync state and restore timeouts
        self.scan_running_tasks(restore_timeouts=True)
        self.load_schedules()

    def load_schedules(self):
        for task in self.store.get_all_tasks():
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
        local_tasks = self.store.get_all_tasks()
        local_id = self.store._get_local_device_id()
        device = device_manager.get_device(local_id)
        if device:
            device.scan_running_tasks(local_tasks)
            
            # Only restore timeouts on startup (explicit request)
            if restore_timeouts:
                # We need to access LocalDevice specific methods
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
                            # Start watcher for remaining time
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
        tasks = self.store.get_all_tasks()
        results = []
        for t in tasks:
            status = self.get_task_status(t.id)
            results.append({**t.dict(), "status": status.dict()})
        await ws_manager.broadcast("task_list", results)

    def start_task(self, task_id: str):
        task = self.store.get_task(task_id)
        if not task:
             raise HTTPException(status_code=404, detail="Task not found")
        
        local_id = self.store._get_local_device_id()
        device = device_manager.get_device(local_id)
        if not device:
            raise HTTPException(status_code=500, detail="Local device manager unavailable")
            
        try:
            result = device.start_task(task.id, task.command, task.cwd, task.env, timeout=task.timeout)
            if result.get("status") == "already_running":
                print(f"Task {task_id} skipped: already running (PID: {result.get('pid')})")
            return result
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    def stop_task(self, task_id: str):
        local_id = self.store._get_local_device_id()
        device = device_manager.get_device(local_id)
        if not device:
             return {"status": "device_not_found"}
        return device.stop_task(task_id)

    def get_task_status(self, task_id: str):
        local_id = self.store._get_local_device_id()
        device = device_manager.get_device(local_id)
        if not device:
            return TaskStatus(id=task_id, running=False, message="Local device unavailable")
        return device.get_task_status(task_id)

    def get_logs(self, task_id: str, lines: int = 50):
        local_id = self.store._get_local_device_id()
        device = device_manager.get_device(local_id)
        if not device:
            return ["Local device unavailable"]
        return device.get_logs(task_id, lines)
    
    def reorder_tasks(self, task_ids: List[str]):
        self.store.reorder_tasks(task_ids)

    def find_related_processes(self, task_id: str) -> List[Dict[str, Any]]:
        task = self.store.get_task(task_id)
        if not task:
            return []
            
        local_id = self.store._get_local_device_id()
        device = device_manager.get_device(local_id)
        if not device:
            return []
            
        return device.find_related_processes(task.command)

    def kill_process(self, pid: int) -> bool:
        local_id = self.store._get_local_device_id()
        device = device_manager.get_device(local_id)
        if not device:
            return False
        return device.kill_process_by_pid(pid)

    def associate_process(self, task_id: str, pid: int):
        local_id = self.store._get_local_device_id()
        device = device_manager.get_device(local_id)
        if not device:
             raise HTTPException(status_code=500, detail="Local device unavailable")
             
        result = device.associate_process(task_id, pid)
        if result.get("status") == "error":
            raise HTTPException(status_code=400, detail=result.get("message"))
            
        # Update Task Config
        task = self.store.get_task(task_id)
        if task:
            # Update command
            cmd_list = result.get("cmdline")
            if cmd_list:
                # Simple join or accurate reconstruction?
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
            
            self.store.save()
            
        return result

task_manager = TaskManager()

# --- Routes ---

# --- Device Directory Routes (MOVED UP to avoid conflict with /{task_id}) ---

@router.get("/devices")
def list_devices():
    try:
        # Trigger sync for remote devices (blocking with timeout)
        # This ensures we have the latest config (like python path)
        device_manager.sync_remote_devices()
        
        devices = device_manager.get_all_devices()
        return devices
    except Exception as e:
        print(f"Error listing devices: {e}")
        import traceback
        traceback.print_exc()
        return []

@router.post("/devices/add")
def add_device(req: AddDeviceRequest):
    try:
        device_id = None
        hostname = None
        python_exec = None
        
        # Always fetch from remote to verify and get ID
        try:
            # normalize url
            url = req.url.rstrip('/')
            if not url.startswith('http'):
                url = 'http://' + url
                
            resp = requests.get(f"{url}/api/agent/status", timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                device_id = data.get("id")
                hostname = data.get("hostname")
                python_exec = data.get("python_exec")
                
                if not device_id:
                    print(f"Warning: Remote device at {url} returned status 200 but no ID. Response: {data}")
            else:
                error_msg = f"Remote device returned status {resp.status_code}"
                try:
                    error_msg += f": {resp.text[:100]}"
                except:
                    pass
                raise HTTPException(status_code=400, detail=error_msg)
                    
        except requests.exceptions.Timeout:
            raise HTTPException(status_code=400, detail=f"Connection timed out connecting to {url}.")
        except requests.exceptions.ConnectionError:
            raise HTTPException(status_code=400, detail=f"Failed to connect to {url}.")
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Failed to connect to remote device: {str(e)}")
        
        if not device_id:
            raise HTTPException(status_code=400, detail="Could not determine remote device ID.")
        
        existing = device_manager.get_device(device_id)
        if existing and existing.to_dict()['type'] == 'LocalDevice':
             raise HTTPException(status_code=409, detail="This device ID matches the Local Device.")

        final_url = req.url
        if not final_url.startswith('http'):
            final_url = 'http://' + final_url
            
        name = hostname or "Unknown"
        device_manager.add_remote_device(device_id, name, final_url, python_exec)
        return {"status": "added", "id": device_id, "name": name}
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error adding device: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/devices/{device_id}")
def delete_device(device_id: str):
    success = device_manager.remove_device(device_id)
    if not success:
         raise HTTPException(status_code=404, detail="Device not found")
    return {"status": "deleted"}

@router.post("/devices/{device_id}/update")
def update_device(device_id: str, req: UpdateDeviceRequest):
    if req.id:
        success = device_manager.rename_device(device_id, req.id)
        if not success:
            raise HTTPException(status_code=500, detail="Failed to rename device. If this is a remote device, check connection.")
            
    if req.python_exec is not None:
        success = device_manager.update_device(device_id, req.python_exec)
        if not success:
            raise HTTPException(status_code=404, detail="Device not found")
        
    return {"status": "updated", "id": device_id, "name": req.id}


# --- Task Routes ---

@router.get("/list")
def list_tasks():
    # Only return local tasks
    task_manager.scan_running_tasks()
    
    tasks = task_manager.store.get_all_tasks()
    results = []
    
    for t in tasks:
        status = task_manager.get_task_status(t.id)
        results.append({**t.dict(), "status": status.dict()})
        
    return results

@router.post("/create")
def create_task(req: CreateTaskRequest):
    # Always create locally
    # If device_id is provided, it should match local ID, but we enforce local creation regardless
    # or we can respect it if it matches.
    
    local_id = task_manager.store._get_local_device_id()
    
    new_task = TaskConfig(
        id=str(uuid.uuid4()),
        name=req.name,
        command=req.command,
        cwd=req.cwd,
        env={},
        created_at=time.time(),
        description=req.description,
        device_id=local_id, # Force local ID
        schedule=req.schedule,
        timeout=req.timeout
    )
    task_manager.store.add_task(new_task)
    if req.schedule:
        task_manager.update_schedule(new_task.id, req.schedule)
    task_manager.scan_running_tasks()
    return new_task

@router.delete("/{task_id}")
def delete_task(task_id: str):
    task = task_manager.store.get_task(task_id)
    if task:
         task_manager.update_schedule(task_id, None)
         task_manager.stop_task(task_id)
         task_manager.store.remove_task(task_id)
         return {"status": "deleted"}
    raise HTTPException(status_code=404, detail="Task not found")

@router.post("/{task_id}/start")
def start_task_route(task_id: str):
    return task_manager.start_task(task_id)

@router.post("/{task_id}/stop")
def stop_task_route(task_id: str):
    return task_manager.stop_task(task_id)

@router.post("/{task_id}/update")
def update_task_route(task_id: str, req: UpdateTaskRequest):
    task = task_manager.store.get_task(task_id)
    if task:
        if req.name is not None: task.name = req.name
        if req.command is not None: task.command = req.command
        if req.cwd is not None: task.cwd = req.cwd
        if req.description is not None: task.description = req.description
        if req.schedule is not None:
            task.schedule = req.schedule
            task_manager.update_schedule(task_id, req.schedule)
        if req.timeout is not None:
            task.timeout = req.timeout
        # Ignore device_id update or force local
        task_manager.store.save()
        return task
    raise HTTPException(status_code=404, detail="Task not found")

@router.post("/reorder")
def reorder_tasks_route(task_ids: List[str]):
    task_manager.reorder_tasks(task_ids)
    return {"status": "reordered"}

@router.get("/{task_id}")
def get_task_details(task_id: str):
    task = task_manager.store.get_task(task_id)
    if task:
        status = task_manager.get_task_status(task_id)
        return {
            **task.dict(),
            "status": status.dict()
        }
    raise HTTPException(status_code=404, detail="Task not found")

@router.get("/{task_id}/logs")
def get_task_logs(task_id: str, n: int = 500):
    """
    Get the last n lines of logs for a task.
    Defaults to 500 lines.
    """
    logs = task_manager.get_logs(task_id, n)
    return {"logs": logs}

@router.get("/{task_id}/related_processes")
def get_related_processes(task_id: str):
    return task_manager.find_related_processes(task_id)

@router.post("/process/kill")
def kill_process_route(req: Dict[str, int]):
    pid = req.get("pid")
    if not pid:
        raise HTTPException(status_code=400, detail="PID required")
    success = task_manager.kill_process(pid)
    if success:
        return {"status": "killed"}
    raise HTTPException(status_code=500, detail="Failed to kill process")

@router.post("/{task_id}/associate")
def associate_process_route(task_id: str, req: Dict[str, int]):
    pid = req.get("pid")
    if not pid:
        raise HTTPException(status_code=400, detail="PID required")
    return task_manager.associate_process(task_id, pid)

# --- WebSocket Endpoint ---

@router.websocket("/ws/logs/{task_id}")
async def websocket_logs(websocket: WebSocket, task_id: str):
    room = f"task_logs:{task_id}"
    await ws_manager.connect(websocket, room)
    try:
        # Initially send last N logs?
        # Maybe frontend can request via HTTP first, or WS message.
        # Let's keep it simple: stream new logs only.
        while True:
            # Keep connection alive
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket, room)
    except Exception as e:
        print(f"WS error: {e}")
        ws_manager.disconnect(websocket, room)

@router.websocket("/ws/tasks")
async def websocket_tasks(websocket: WebSocket):
    room = "task_list"
    await ws_manager.connect(websocket, room)
    try:
        while True:
            # We can also receive commands from frontend here if we want full duplex control
            # For now, just keep alive and push updates
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket, room)

