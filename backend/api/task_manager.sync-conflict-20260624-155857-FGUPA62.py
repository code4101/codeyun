import subprocess
import sys
import time
import uuid
import shlex
import socket
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect, Depends
from pydantic import BaseModel, Field
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlmodel import Session, select

from backend.api.websocket_manager import manager as ws_manager
from backend.core.auth import verify_api_token
from backend.core.device import BaseDevice, device_manager, TaskStatus
from backend.db import engine
from backend.models import Task as TaskModel

import asyncio

router = APIRouter()

_status_broadcaster_task: Optional[asyncio.Task] = None

async def start_task_manager_services():
    global _status_broadcaster_task

    if _status_broadcaster_task and not _status_broadcaster_task.done():
        return

    loop = asyncio.get_running_loop()

    def thread_safe_log_callback(task_id, line):
        try:
            asyncio.run_coroutine_threadsafe(ws_manager.broadcast_log(task_id, line), loop)
        except Exception:
            pass

    try:
        local_id = task_manager._get_local_device_id()
        device = device_manager.get_device(local_id)
        if device:
            device.set_log_callback(thread_safe_log_callback)
    except Exception:
        pass

    _status_broadcaster_task = asyncio.create_task(status_broadcaster())

async def stop_task_manager_services():
    global _status_broadcaster_task

    if _status_broadcaster_task:
        _status_broadcaster_task.cancel()
        _status_broadcaster_task = None

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

# --- Manager ---

class TaskManager:
    def __init__(self):
        self.scheduler = BackgroundScheduler()
        self.scheduler.start()
        # Initial scan to sync state and restore timeouts
        self.scan_running_tasks(restore_timeouts=True)
        self.load_schedules()

    def _get_local_device_id(self) -> str:
        return device_manager.get_local_device_id()

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
            t_dict = t.model_dump()
            t_dict["status"] = status.model_dump()
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

# --- Task Routes ---

@router.get("/")
def list_tasks(
    token_device: BaseDevice = Depends(verify_api_token)
):
    """
    List tasks for the authenticated node.
    """
    requesting_device_id = token_device.id
    local_id = task_manager._get_local_device_id()
    if requesting_device_id == local_id:
        task_manager.scan_running_tasks()
    
    with Session(engine) as session:
        stmt = select(TaskModel).where(TaskModel.device_id == requesting_device_id).order_by(TaskModel.order, TaskModel.created_at)
        tasks = session.exec(stmt).all()
    
    results = []
    for t in tasks:
        status = task_manager.get_task_status(t.id)
        t_dict = t.model_dump()
        t_dict["status"] = status.model_dump()
        results.append(t_dict)
        
    return results

@router.get("/list")
def list_tasks_deprecated(
    token_device: BaseDevice = Depends(verify_api_token)
):
    return list_tasks(token_device)

@router.post("/create")
def create_task(
    req: CreateTaskRequest,
    token_device: BaseDevice = Depends(verify_api_token)
):
    target_device_id = token_device.device_id

    with Session(engine) as session:
        last_task = session.exec(
            select(TaskModel)
            .where(TaskModel.device_id == target_device_id)
            .order_by(TaskModel.order.desc(), TaskModel.created_at.desc())
        ).first()
        next_order = 0 if not last_task or last_task.order is None else last_task.order + 1

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
            order=next_order,
        )
        session.add(new_task)
        session.commit()
        session.refresh(new_task)
        
    if req.schedule:
        task_manager.update_schedule(new_task.id, req.schedule)
    
    task_manager.scan_running_tasks()
    return new_task

@router.delete("/{task_id}")
def delete_task(task_id: str, token_device: BaseDevice = Depends(verify_api_token)):
    with Session(engine) as session:
        task = session.get(TaskModel, task_id)
        if task:
            task_manager.update_schedule(task_id, None)
            task_manager.stop_task(task_id)
            session.delete(task)
            session.commit()
            return {"status": "deleted"}
    raise HTTPException(status_code=404, detail="Task not found")

@router.post("/{task_id}/start")
def start_task_route(task_id: str, token_device: BaseDevice = Depends(verify_api_token)):
    with Session(engine) as session:
        task = session.get(TaskModel, task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")

    return task_manager.start_task(task_id)

@router.post("/{task_id}/stop")
def stop_task_route(task_id: str, token_device: BaseDevice = Depends(verify_api_token)):
    with Session(engine) as session:
        task = session.get(TaskModel, task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")

    return task_manager.stop_task(task_id)

@router.post("/{task_id}/update")
def update_task_route(task_id: str, req: UpdateTaskRequest, token_device: BaseDevice = Depends(verify_api_token)):
    with Session(engine) as session:
        task = session.get(TaskModel, task_id)
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
            
            session.add(task)
            session.commit()
            session.refresh(task)
            return task
    raise HTTPException(status_code=404, detail="Task not found")

@router.post("/reorder")
def reorder_tasks_route(task_ids: List[str], token_device: BaseDevice = Depends(verify_api_token)):
    task_manager.reorder_tasks(task_ids)
    return {"status": "reordered"}

@router.get("/{task_id}")
def get_task_details(task_id: str, token_device: BaseDevice = Depends(verify_api_token)):
    with Session(engine) as session:
        task = session.get(TaskModel, task_id)
        if task:
            status = task_manager.get_task_status(task_id)
            return {
                **task.model_dump(),
                "status": status.model_dump()
            }
    raise HTTPException(status_code=404, detail="Task not found")

@router.get("/{task_id}/logs")
def get_task_logs(task_id: str, n: int = 500, token_device: BaseDevice = Depends(verify_api_token)):
    with Session(engine) as session:
        task = session.get(TaskModel, task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")

    logs = task_manager.get_logs(task_id, n)
    return {"logs": logs}

@router.get("/{task_id}/related_processes")
def get_related_processes(task_id: str, token_device: BaseDevice = Depends(verify_api_token)):
    with Session(engine) as session:
        task = session.get(TaskModel, task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")

    return task_manager.find_related_processes(task_id)

@router.post("/process/kill")
def kill_process_route(req: Dict[str, int], token_device: BaseDevice = Depends(verify_api_token)):
    pid = req.get("pid")
    if not pid:
        raise HTTPException(status_code=400, detail="PID required")
    success = task_manager.kill_process(pid)
    if success:
        return {"status": "killed"}
    raise HTTPException(status_code=500, detail="Failed to kill process")

@router.post("/{task_id}/associate")
def associate_process_route(task_id: str, req: Dict[str, int], token_device: BaseDevice = Depends(verify_api_token)):
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
async def websocket_logs(websocket: WebSocket, task_id: str, token_device: BaseDevice = Depends(verify_api_token)):
    room = f"task_logs:{task_id}"
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
async def websocket_tasks(websocket: WebSocket, token_device: BaseDevice = Depends(verify_api_token)):
    room = "task_list"
    await ws_manager.connect(websocket, room)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket, room)
