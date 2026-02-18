import os
import sys
import socket
import subprocess
import threading
import psutil
import shlex
import time
import datetime
import requests
import json
import ctypes
from ctypes import wintypes
from typing import Dict, List, Optional, Any, Callable
from abc import ABC, abstractmethod
from pydantic import BaseModel
import asyncio

import uuid

# --- Shared Constants (Consider moving to a config file) ---
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
LOGS_DIR = os.path.join(DATA_DIR, "logs")
DEVICES_FILE = os.path.join(DATA_DIR, "devices.json")
SYSTEM_ID_FILE = os.path.join(DATA_DIR, "system_id.json")
PIDS_FILE = os.path.join(DATA_DIR, "pids.json")

if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)

if not os.path.exists(LOGS_DIR):
    os.makedirs(LOGS_DIR)

def get_system_id():
    """Get or generate a persistent unique ID for this machine"""
    if os.path.exists(SYSTEM_ID_FILE):
        try:
            with open(SYSTEM_ID_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if data.get('id'):
                    return data['id']
        except Exception:
            pass
    
    # Generate new ID
    new_id = str(uuid.uuid4())
    try:
        with open(SYSTEM_ID_FILE, 'w', encoding='utf-8') as f:
            json.dump({'id': new_id, 'created_at': time.time()}, f)
    except Exception as e:
        print(f"Failed to save system ID: {e}")
        
    return new_id

class TaskStatus(BaseModel):
    id: str
    running: bool
    pid: Optional[int] = None
    started_at: Optional[float] = None
    finished_at: Optional[float] = None  # New field
    cpu_percent: Optional[float] = None
    memory_rss: Optional[int] = None  # bytes
    message: Optional[str] = None

def match_cmdline(target_cmd: str, proc_cmdline: List[str]) -> bool:
    """Logic to match a target command string against a process cmdline list"""
    try:
        try:
            target_args = shlex.split(target_cmd, posix=(sys.platform != 'win32'))
        except:
            target_args = target_cmd.split()
        
        # Windows/non-posix shlex keeps quotes, so we strip them
        if sys.platform == 'win32':
             target_args = [arg.strip('"') for arg in target_args]
        
        if not target_args:
            return False

        n = len(target_args)
        if len(proc_cmdline) < n:
            return False
            
        for i in range(len(proc_cmdline) - n + 1):
            sub = proc_cmdline[i:i+n]
            if sub == target_args:
                return True
        
        if target_args[0].startswith('python') or target_args[0].endswith('python.exe') or target_args[0].endswith('python'):
             rest_target = target_args[1:]
             if not rest_target:
                 return False 
             
             n_rest = len(rest_target)
             for i in range(len(proc_cmdline) - n_rest + 1):
                 if proc_cmdline[i:i+n_rest] == rest_target:
                     return True

        return False
    except Exception:
        return False

def parse_cmdline(cmdline: str) -> List[str]:
    if sys.platform != 'win32':
        return shlex.split(cmdline, posix=True)
    
    # Windows: Use CommandLineToArgvW for correct parsing of quotes and backslashes
    try:
        ctypes.windll.shell32.CommandLineToArgvW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
        ctypes.windll.shell32.CommandLineToArgvW.restype = ctypes.POINTER(wintypes.LPWSTR)
        
        nargs = ctypes.c_int()
        res = ctypes.windll.shell32.CommandLineToArgvW(cmdline, ctypes.byref(nargs))
        
        if not res:
             return shlex.split(cmdline, posix=False)
             
        try:
            return [res[i] for i in range(nargs.value)]
        finally:
            ctypes.windll.kernel32.LocalFree(res)
    except Exception:
        return shlex.split(cmdline, posix=False)

class BaseDevice(ABC):
    def __init__(self, device_id: str, name: str, python_exec: Optional[str] = None):
        self.device_id = device_id
        self.name = name
        self.python_exec = python_exec
        self.log_callback: Optional[Callable[[str, str], None]] = None # task_id, line

    def set_log_callback(self, callback: Callable[[str, str], None]):
        self.log_callback = callback

    @abstractmethod
    def start_task(self, task_id: str, command: str, cwd: Optional[str] = None, env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        pass

    @abstractmethod
    def stop_task(self, task_id: str) -> Dict[str, Any]:
        pass

    @abstractmethod
    def get_task_status(self, task_id: str) -> TaskStatus:
        pass
    
    @abstractmethod
    def get_logs(self, task_id: str, lines: int = 50) -> List[str]:
        pass

    @abstractmethod
    def find_related_processes(self, command: str) -> List[Dict[str, Any]]:
        pass
        
    @abstractmethod
    def kill_process_by_pid(self, pid: int) -> bool:
        pass

    @abstractmethod
    def associate_process(self, task_id: str, pid: int) -> Dict[str, Any]:
        pass

    @abstractmethod
    def rename_remote_device(self, new_name: str) -> bool:
        pass

    @abstractmethod
    def scan_running_tasks(self, tasks_to_check: List[Any]):
        pass

    def to_dict(self):
        return {
            "id": self.device_id,
            "name": self.name,
            "type": type(self).__name__,
            "python_exec": self.python_exec
        }

class LocalDevice(BaseDevice):
    def __init__(self, device_id: str = None, name: str = None, python_exec: Optional[str] = None):
        if device_id is None:
            device_id = get_system_id()
        if name is None:
            name = socket.gethostname()
        super().__init__(device_id, name, python_exec)
        self.processes: Dict[str, psutil.Process] = {}
        self.saved_pids: Dict[str, int] = {}
        self.last_run_info: Dict[str, Dict[str, Any]] = {} # Store finished_at, started_at for ended tasks
        self.lock = threading.RLock()
        self.load_pids()
        
    def load_pids(self):
        if os.path.exists(PIDS_FILE):
            try:
                with open(PIDS_FILE, 'r', encoding='utf-8') as f:
                    self.saved_pids = json.load(f)
            except Exception:
                self.saved_pids = {}

    def save_pids(self):
        try:
            with open(PIDS_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.saved_pids, f)
        except Exception as e:
            print(f"Failed to save PIDs: {e}")
    
    def scan_running_tasks(self, tasks_to_check: List[Any]):
        with self.lock:
            pids_changed = False
            
            # 1. Clean up existing tracked processes
            for tid, proc in list(self.processes.items()):
                try:
                    if not proc.is_running() or proc.status() == psutil.STATUS_ZOMBIE:
                        # Process finished
                        try:
                            create_time = proc.create_time()
                        except:
                            create_time = None
                        
                        self.last_run_info[tid] = {
                            "started_at": create_time,
                            "finished_at": time.time()
                        }
                        del self.processes[tid]
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    # Process gone
                    if tid in self.processes:
                        # We might not have create_time if it's already gone
                        self.last_run_info[tid] = {
                            "started_at": None, # Cannot retrieve
                            "finished_at": time.time()
                        }
                        del self.processes[tid]
            
            # 2. Try to restore from saved_pids
            for task in tasks_to_check:
                t_id = str(task.id)
                # Skip if already running
                if t_id in self.processes:
                    continue

                if t_id in self.saved_pids:
                    pid = self.saved_pids[t_id]
                    try:
                        proc = psutil.Process(pid)
                        if proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE:
                             # Verify command if available
                             cmd = getattr(task, 'command', None)
                             if cmd:
                                 try:
                                     p_cmd = proc.cmdline()
                                     if match_cmdline(cmd, p_cmd):
                                         self.processes[t_id] = proc
                                         continue
                                 except (psutil.NoSuchProcess, psutil.AccessDenied):
                                     pass
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
                    
                    # If we reached here, it means we failed to restore (dead or mismatch)
                    # Clean up invalid PID
                    if t_id in self.saved_pids:
                        del self.saved_pids[t_id]
                        pids_changed = True

            # 3. Deep scan for missing tasks (Re-association)
            missing_tasks = [t for t in tasks_to_check if str(t.id) not in self.processes]
            
            if missing_tasks:
                candidates = []
                try:
                    # Snapshot all system processes once
                    for proc in psutil.process_iter(['pid', 'cmdline', 'create_time']):
                        try:
                            if proc.info['cmdline']:
                                candidates.append(proc)
                        except (psutil.NoSuchProcess, psutil.AccessDenied):
                            continue
                except Exception:
                    pass
                
                used_pids = set(p.pid for p in self.processes.values())
                
                for task in missing_tasks:
                    t_id = str(task.id)
                    cmd = getattr(task, 'command', None)
                    if not cmd:
                        continue
                        
                    for proc in candidates:
                        if proc.pid in used_pids:
                            continue
                            
                        try:
                            if match_cmdline(cmd, proc.info['cmdline']):
                                # Found a match!
                                self.processes[t_id] = proc
                                self.saved_pids[t_id] = proc.pid
                                used_pids.add(proc.pid)
                                pids_changed = True
                                break
                        except (psutil.NoSuchProcess, psutil.AccessDenied):
                            continue
            
            if pids_changed:
                self.save_pids()

    def find_related_processes(self, command: str) -> List[Dict[str, Any]]:
        """Scan system for processes that might be related to this command"""
        results = []
        try:
            target_args = shlex.split(command, posix=(sys.platform != 'win32'))
        except:
            target_args = command.split()
            
        if not target_args:
            return []

        target_exe = target_args[0] # Keep original case for path checking
        
        # 1. 自动补全 python 路径：如果目标是 'python' 且配置了 python_exec，则使用具体路径
        if target_exe.lower() in ['python', 'python.exe'] and self.python_exec:
             target_exe = self.python_exec

        # Check if target executable has path component
        target_has_path = os.path.dirname(target_exe) != ''
        target_exe_lower = target_exe.lower()

        if sys.platform == 'win32' and not target_exe_lower.endswith('.exe'):
            target_exe_lower += '.exe'
        
        # Simple heuristic: match executable name
        target_name = os.path.basename(target_exe_lower)

        for proc in psutil.process_iter(['pid', 'name', 'cmdline', 'create_time', 'memory_info', 'exe']):
            try:
                p_name = proc.info['name'].lower() if proc.info['name'] else ''
                p_cmd = proc.info['cmdline'] or []
                p_exe = proc.info['exe'] or ''
                
                # Match score:
                # 3: Exact cmdline match
                # 2: Cmdline contains target args
                # 1: Executable name match
                score = 0
                
                if match_cmdline(command, p_cmd):
                     # Check if it is exact match (all args match)
                     if len(p_cmd) == len(target_args) and all(str(a).strip() == str(b).strip() for a, b in zip(p_cmd, target_args)):
                         score = 3
                     else:
                         score = 2
                elif target_name in p_name:
                    # Heuristic match (Score 1)
                    # If target specifies a path, we MUST verify that the process path matches.
                    if target_has_path:
                        is_path_match = False
                        
                        # Normalize target path
                        target_norm = os.path.normpath(target_exe_lower)
                        
                        # Check 1: cmdline[0] (User often runs specific path)
                        # ONLY if cmdline[0] has path info. If it's just "python.exe", it proves nothing.
                        if p_cmd:
                            cmd0 = p_cmd[0].lower()
                            cmd0_norm = os.path.normpath(cmd0)
                            
                            # Only compare if cmd0 has directory component
                            if os.path.dirname(cmd0_norm) != '':
                                if cmd0_norm == target_norm:
                                    is_path_match = True
                                elif cmd0_norm.endswith(target_norm) or target_norm.endswith(cmd0_norm):
                                    is_path_match = True

                        # Check 2: Actual exe path (symlinks resolved)
                        if not is_path_match and p_exe:
                            p_exe_norm = os.path.normpath(p_exe).lower()
                            if p_exe_norm == target_norm:
                                is_path_match = True
                            # p_exe is absolute path, so endswith is safe (checking if target matches suffix)
                            # But target_norm (absolute) endswith p_exe_norm (absolute) is just equality check.
                            elif p_exe_norm.endswith(target_norm): 
                                is_path_match = True
                        
                        if not is_path_match:
                            continue
 
                    score = 1
                
                if score > 0:
                    results.append({
                        "pid": proc.info['pid'],
                        "name": proc.info['name'],
                        "exe": p_exe, # Return full path
                        "cmdline": ' '.join(p_cmd),
                        "cmd_args": ' '.join(p_cmd[1:]) if len(p_cmd) > 1 else '',
                        "started_at": proc.info['create_time'],
                        "memory_rss": proc.info['memory_info'].rss,
                        "score": score
                    })

            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        
        # Sort by score desc, then start time desc
        results.sort(key=lambda x: (-x['score'], -x['started_at']))
        return results

    def kill_process_by_pid(self, pid: int) -> bool:
        try:
            p = psutil.Process(pid)
            p.terminate()
            try:
                p.wait(timeout=2)
            except psutil.TimeoutExpired:
                p.kill()
            return True
        except psutil.NoSuchProcess:
            return True # Already gone
        except Exception as e:
            print(f"Failed to kill {pid}: {e}")
            return False

    def associate_process(self, task_id: str, pid: int) -> Dict[str, Any]:
        with self.lock:
            try:
                proc = psutil.Process(pid)
                if not proc.is_running():
                     return {"status": "error", "message": "Process not running"}
                
                # Update process map
                self.processes[task_id] = proc
                self.saved_pids[task_id] = pid
                self.save_pids()
                
                # Gather info to return
                info = {
                    "status": "associated",
                    "pid": pid,
                    "started_at": proc.create_time(),
                    "cmdline": list(proc.cmdline()), # Return as list
                }
                
                try:
                    info["cwd"] = proc.cwd()
                except (psutil.AccessDenied, psutil.NoSuchProcess):
                    info["cwd"] = None
                    
                return info
            except psutil.NoSuchProcess:
                return {"status": "error", "message": "Process not found"}
            except Exception as e:
                return {"status": "error", "message": str(e)}

    def _watch_timeout(self, process, timeout_sec, task_id):
        """Watcher thread to enforce timeout"""
        try:
            process.wait(timeout=timeout_sec)
        except subprocess.TimeoutExpired:
            print(f"Task {task_id} (PID {process.pid}) timed out after {timeout_sec}s. Killing...")
            try:
                # Use stop_task logic for clean termination
                self.stop_task(task_id)
            except Exception as e:
                print(f"Error killing timed out task {task_id}: {e}")

    def start_task(self, task_id: str, command: str, cwd: Optional[str] = None, env: Optional[Dict[str, str]] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        with self.lock:
            # Check internal cache first
            if task_id in self.processes:
                proc = self.processes[task_id]
                if proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE:
                     return {"status": "already_running", "pid": proc.pid}
                else:
                    del self.processes[task_id]

            # Double check: Scan actual processes just in case cache is stale (Lazy Loading scenario)
            # This is a quick scan for a single command
            try:
                for proc in psutil.process_iter(['pid', 'cmdline']):
                    try:
                        cmdline = proc.info['cmdline']
                        if not cmdline:
                            continue
                        if match_cmdline(command, cmdline):
                             self.processes[task_id] = proc
                             return {"status": "already_running", "pid": proc.pid}
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        continue
            except Exception:
                pass

            log_dir = os.path.join(DATA_DIR, self.device_id, "logs")
            if not os.path.exists(log_dir):
                os.makedirs(log_dir)
            log_file_path = os.path.join(log_dir, f"{task_id}.log")
            
            run_env = os.environ.copy()
            if env:
                run_env.update(env)
            run_env["PYTHONIOENCODING"] = "utf-8"
            run_env["PYTHONUTF8"] = "1"
            run_env["PYTHONUNBUFFERED"] = "1"
            
            try:
                cmd_args = parse_cmdline(command)
            except:
                cmd_args = command.split()

            ps_executable = "powershell"
            import shutil
            if shutil.which("pwsh"):
                ps_executable = "pwsh"

            if cmd_args and cmd_args[0] == '.' and sys.platform == 'win32':
                cmd_args = [ps_executable, "-Command", command]
            elif cmd_args and len(cmd_args) > 0 and cmd_args[0].lower().endswith('.ps1') and sys.platform == 'win32':
                 cmd_args.insert(0, ps_executable)

            # Add support for .vbs scripts
            elif cmd_args and len(cmd_args) > 0 and cmd_args[0].lower().endswith('.vbs') and sys.platform == 'win32':
                 cmd_args.insert(0, "cscript")
                 cmd_args.insert(1, "//Nologo")
            
            # Determine python executable
            # Priority: Device Config > Env Var > Default Fallback
            default_python = self.python_exec
            
            if not default_python:
                default_python = os.environ.get("XLWEB_PYTHON_EXEC")
            
            if not default_python:
                 # Default to local .venv if available
                 project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
                 possible_venv = os.path.join(project_root, ".venv", "Scripts", "python.exe")
                 if os.path.exists(possible_venv):
                     default_python = possible_venv

            if default_python and cmd_args and (cmd_args[0] == 'python' or cmd_args[0] == 'python.exe'):
                cmd_args[0] = default_python

            actual_cwd = cwd if cwd and os.path.exists(cwd) else None

            try:
                if os.path.exists(log_file_path) and os.path.getsize(log_file_path) > 10 * 1024 * 1024:
                    backup_path = log_file_path + ".old"
                    if os.path.exists(backup_path):
                        os.remove(backup_path)
                    os.rename(log_file_path, backup_path)
            except Exception as e:
                print(f"Log rotation failed: {e}")

            try:
                with open(log_file_path, 'a', encoding='utf-8') as log_f:
                    log_f.write(f"\n--- Starting task at {datetime.datetime.now()} ---\n")
                    log_f.write(f"Command: {cmd_args}\n")
                    log_f.write(f"CWD: {actual_cwd}\n")
                    log_f.write(f"Env PYTHONIOENCODING: {run_env.get('PYTHONIOENCODING')}\n")
                
                creationflags = 0
                if sys.platform == 'win32':
                    creationflags = 0x08000000
                
                proc = subprocess.Popen(
                    cmd_args,
                    cwd=actual_cwd,
                    env=run_env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    creationflags=creationflags,
                    close_fds=True
                )
                
                self.processes[task_id] = psutil.Process(proc.pid)
                
                # Save PID persistence
                self.saved_pids[task_id] = proc.pid
                self.save_pids()
                
                t = threading.Thread(target=self._stream_logs, args=(task_id, proc, log_file_path))
                t.daemon = True
                t.start()
                
                pid = proc.pid
                
                # Start timeout watcher if configured
                if timeout and timeout > 0:
                    watcher = threading.Thread(
                        target=self._watch_timeout, 
                        args=(proc, timeout, task_id)
                    )
                    watcher.daemon = True
                    watcher.start()

                return {"status": "started", "pid": pid}

            except Exception as e:
                raise Exception(f"Failed to start task: {e}")

    def _stream_logs(self, task_id, process, log_file_path):
        try:
            with open(log_file_path, 'a', encoding='utf-8') as f:
                for line in iter(process.stdout.readline, b''):
                    decoded_line = ''
                    try:
                        decoded_line = line.decode('utf-8')
                    except UnicodeDecodeError:
                        try:
                            decoded_line = line.decode('gbk')
                        except:
                            decoded_line = line.decode('utf-8', errors='replace')
                    
                    # Write to file
                    f.write(decoded_line)
                    f.flush()
                    
                    # Callback for WebSocket
                    if self.log_callback:
                        try:
                            # Callback might be async or sync? 
                            # If it's async (which it likely is for WS), we need to run it in loop?
                            # But we are in a thread here.
                            # So we should call a thread-safe wrapper.
                            self.log_callback(task_id, decoded_line)
                        except Exception as e:
                            print(f"Log callback error: {e}")

        except Exception as e:
            print(f"Log streamer error for pid {process.pid}: {e}")
        finally:
            process.stdout.close()

    def stop_task(self, task_id: str) -> Dict[str, Any]:
        with self.lock:
            if task_id not in self.processes:
                # Clean up persistence if exists
                if task_id in self.saved_pids:
                    del self.saved_pids[task_id]
                    self.save_pids()
                return {"status": "not_running"}
            
            proc = self.processes[task_id]
            try:
                # Capture start time before killing
                try:
                    create_time = proc.create_time()
                except:
                    create_time = None

                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except psutil.TimeoutExpired:
                    proc.kill()
                
                # Record completion info
                self.last_run_info[task_id] = {
                    "started_at": create_time,
                    "finished_at": time.time()
                }
                
                del self.processes[task_id]
                if task_id in self.saved_pids:
                    del self.saved_pids[task_id]
                    self.save_pids()
                return {"status": "stopped"}
            except psutil.NoSuchProcess:
                del self.processes[task_id]
                if task_id in self.saved_pids:
                    del self.saved_pids[task_id]
                    self.save_pids()
                return {"status": "stopped", "message": "Process already gone"}
            except Exception as e:
                 raise Exception(str(e))

    def get_task_status(self, task_id: str) -> TaskStatus:
        with self.lock:
            running = False
            pid = None
            started_at = None
            cpu_percent = None
            memory_rss = None
            finished_at = None
            
            if task_id in self.processes:
                proc = self.processes[task_id]
                if proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE:
                    running = True
                    pid = proc.pid
                    try:
                        started_at = proc.create_time()
                        cpu_percent = proc.cpu_percent(interval=None)
                        memory_rss = proc.memory_info().rss
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
                else:
                    # Clean up if dead
                    try:
                        create_time = proc.create_time()
                    except:
                        create_time = None
                    self.last_run_info[task_id] = {
                        "started_at": create_time,
                        "finished_at": time.time()
                    }
                    del self.processes[task_id]
            
            # If not running, check history
            if not running and task_id in self.last_run_info:
                info = self.last_run_info[task_id]
                started_at = info.get("started_at")
                finished_at = info.get("finished_at")

            return TaskStatus(
                id=task_id,
                running=running, 
                pid=pid, 
                started_at=started_at,
                finished_at=finished_at,
                cpu_percent=cpu_percent,
                memory_rss=memory_rss
            )

    def get_logs(self, task_id: str, lines: int = 50) -> List[str]:
        log_dir = os.path.join(DATA_DIR, self.device_id, "logs")
        log_file_path = os.path.join(log_dir, f"{task_id}.log")
        if not os.path.exists(log_file_path):
            # Fallback to old path for backward compatibility?
            old_log_path = os.path.join(LOGS_DIR, f"{task_id}.log")
            if os.path.exists(old_log_path):
                log_file_path = old_log_path
            else:
                return []
        
        try:
            from collections import deque
            with open(log_file_path, 'r', encoding='utf-8', errors='replace') as f:
                return list(deque(f, maxlen=lines))
        except Exception as e:
            return [f"Error reading logs: {e}"]

    def rename_remote_device(self, new_name: str) -> bool:
        """For local device, we just update our name locally"""
        with self.lock:
            self.name = new_name
            # Persistence is handled by DeviceManager calling save()
            # But DeviceManager.rename_device already updates self.name?
            # Yes, DeviceManager.rename_device does: device.name = new_name.
            # So this method might be redundant for LocalDevice if called from DeviceManager.
            pass
        return True

class RemoteDevice(BaseDevice):
    def __init__(self, device_id: str, name: str, url: str, python_exec: Optional[str] = None):
        super().__init__(device_id, name, python_exec)
        self.url = url.rstrip('/')
        self.task_statuses: Dict[str, TaskStatus] = {}
        self.lock = threading.RLock()

    def sync_config(self) -> bool:
        """Fetch latest configuration from remote device and update local cache"""
        try:
            resp = requests.get(f"{self.url}/api/agent/status", timeout=2)
            if resp.status_code == 200:
                data = resp.json()
                changed = False
                
                # Update python_exec
                remote_exec = data.get("python_exec")
                if remote_exec != self.python_exec:
                    self.python_exec = remote_exec
                    changed = True
                
                return changed
        except Exception:
            pass
        return False
    
    def to_dict(self):
        d = super().to_dict()
        d['url'] = self.url
        return d
        
    def rename_remote_device(self, new_name: str) -> bool:
        """Call remote API to rename itself"""
        try:
            # We need an API on the remote agent to update its own name.
            # POST /api/agent/rename { "name": "new_name" }
            resp = requests.post(f"{self.url}/api/agent/rename", json={"name": new_name}, timeout=5)
            if resp.status_code == 200:
                self.name = new_name # Update local cache of the name
                return True
            return False
        except Exception as e:
            print(f"Failed to rename remote device {self.device_id} at {self.url}: {e}")
            if 'resp' in locals():
                try:
                    print(f"Response code: {resp.status_code}, Body: {resp.text}")
                except:
                    pass
            return False

    def start_task(self, task_id: str, command: str, cwd: Optional[str] = None, env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        try:
            # We assume remote task ID is same as local?
            # Actually, remote API expects us to tell it the ID?
            # If we are starting an existing task, we use /task/<id>/start
            # But wait, RemoteDevice.start_task signature is generic.
            # Here, it seems we are just running a command?
            # The previous implementation was using /api/agent/exec_cmd which is raw command execution.
            # But if we want full task management, we should use /api/task/...
            
            # If the task already exists on remote, we call start.
            # If not, we call create?
            # The caller (TaskManager) already knows if task exists or not.
            # If task exists in our local cache (fetched from remote), we call start.
            
            # However, start_task here takes command/cwd/env.
            # This implies we might be starting a new process or re-starting.
            
            # Let's try calling the high-level API first.
            resp = requests.post(f"{self.url}/api/task/{task_id}/start", params={"device_id": self.device_id}, timeout=5)
            if resp.status_code == 404:
                 # Task might not exist on remote (maybe we only have it in our cache but remote restarted?)
                 # Or maybe we need to create it first?
                 # But start_task implies "run this".
                 # Let's fallback to creating it if needed, or just fail.
                 # For now, let's assume if it's 404, we can't start it.
                 pass
            
            if resp.status_code == 200:
                 return resp.json()
                 
            # Fallback to raw execution if needed?
            # No, keep it consistent.
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def stop_task(self, task_id: str) -> Dict[str, Any]:
        try:
            resp = requests.post(f"{self.url}/api/task/{task_id}/stop", params={"device_id": self.device_id}, timeout=5)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def get_task_status(self, task_id: str) -> TaskStatus:
        # Optimization: Use batched status from scan_running_tasks
        with self.lock:
             return self.task_statuses.get(task_id, TaskStatus(id=task_id, running=False))

    def get_logs(self, task_id: str, lines: int = 50) -> List[str]:
        try:
            resp = requests.get(f"{self.url}/api/task/{task_id}/logs", params={"n": lines, "device_id": self.device_id}, timeout=5)
            if resp.status_code == 200:
                return resp.json().get("logs", [])
            return [f"Error fetching logs: {resp.status_code}"]
        except Exception as e:
            return [f"Connection error: {e}"]

    def scan_running_tasks(self, tasks_to_check: List[Any]):
        # Fetch ALL tasks from remote to sync status
        try:
            # We use /api/task/list to get full status
            resp = requests.get(f"{self.url}/api/task/list", params={"refresh_device_id": self.device_id}, timeout=5)
            if resp.status_code == 200:
                remote_tasks = resp.json()
                with self.lock:
                    self.task_statuses.clear()
                    for t_data in remote_tasks:
                        # Map remote status to local
                        if "status" in t_data:
                            self.task_statuses[t_data["id"]] = TaskStatus(**t_data["status"])
        except Exception as e:
            # print(f"Error scanning remote {self.device_id}: {e}")
            pass
    
    def find_related_processes(self, command: str) -> List[Dict[str, Any]]:
        # Remote API currently does not support finding processes by raw command string.
        # It only supports finding by task_id via /api/task/{task_id}/related_processes
        return []

    def kill_process_by_pid(self, pid: int) -> bool:
        try:
            resp = requests.post(f"{self.url}/api/task/process/kill", json={"pid": pid}, timeout=5)
            if resp.status_code == 200:
                return True
            return False
        except Exception as e:
            print(f"Failed to kill remote process {pid} on {self.device_id}: {e}")
            return False

    def associate_process(self, task_id: str, pid: int) -> Dict[str, Any]:
        try:
            resp = requests.post(f"{self.url}/api/task/{task_id}/associate", json={"pid": pid}, params={"device_id": self.device_id}, timeout=5)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            return {"status": "error", "message": str(e)}

    # New methods for full CRUD proxy
    def list_tasks(self) -> List[Dict]:
        try:
            resp = requests.get(f"{self.url}/api/task/list", params={"refresh_device_id": self.device_id}, timeout=5)
            if resp.status_code == 200:
                tasks = resp.json()
                # Ensure device_id is correct
                for t in tasks:
                    t['device_id'] = self.device_id
                return tasks
            return []
        except Exception:
            return []

    def create_task(self, task_data: Dict) -> Dict:
        # Proxy create
        # We need to make sure we send the right data
        resp = requests.post(f"{self.url}/api/task/create", json=task_data, timeout=5)
        resp.raise_for_status()
        t = resp.json()
        t['device_id'] = self.device_id
        return t

    def update_task(self, task_id: str, task_data: Dict) -> Dict:
        resp = requests.post(f"{self.url}/api/task/{task_id}/update", json=task_data, params={"device_id": self.device_id}, timeout=5)
        resp.raise_for_status()
        t = resp.json()
        t['device_id'] = self.device_id
        return t

    def delete_task(self, task_id: str) -> Dict:
        resp = requests.delete(f"{self.url}/api/task/{task_id}", params={"device_id": self.device_id}, timeout=5)
        resp.raise_for_status()
        return resp.json()
    
    def to_dict(self):
        d = super().to_dict()
        d['url'] = self.url
        return d

class DeviceManager:
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(DeviceManager, cls).__new__(cls)
            # Initialize devices dictionary FIRST
            cls._instance.devices: Dict[str, BaseDevice] = {}
            # Then load
            cls._instance.load()
        return cls._instance

    def load(self):
        # Local device logic first
        system_id = get_system_id()
        hostname = socket.gethostname()
        
        # Load existing config
        loaded_devices = {}
        if os.path.exists(DEVICES_FILE):
            try:
                with open(DEVICES_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    for item in data:
                        # Use dict temporary to avoid abstract class instantiation issues during partial load
                        loaded_devices[item['id']] = item
            except Exception as e:
                print(f"Error loading devices file: {e}")

        # Instantiate devices
        for dev_id, item in loaded_devices.items():
            try:
                if item['type'] == 'LocalDevice':
                    name = item.get('name', item['id'])
                    self.devices[dev_id] = LocalDevice(dev_id, name, item.get('python_exec'))
                elif item['type'] == 'RemoteDevice':
                    name = item.get('name', item['id'])
                    # RemoteDevice MUST implement all abstract methods now
                    self.devices[dev_id] = RemoteDevice(dev_id, name, item['url'], item.get('python_exec'))
            except TypeError as e:
                print(f"Error instantiating device {dev_id} ({item.get('type')}): {e}")
                # Skip invalid devices to prevent crash
                continue

        # Ensure local device exists (if not loaded)
        if system_id not in self.devices:
             # Check for legacy hostname migration
             pass 
             # For simplicity in this fix, just create if missing
             if not any(isinstance(d, LocalDevice) for d in self.devices.values()):
                 self.devices[system_id] = LocalDevice(system_id, hostname)
                 self.save()

    def save(self):
        try:
            with open(DEVICES_FILE, 'w', encoding='utf-8') as f:
                json.dump([d.to_dict() for d in self.devices.values()], f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"Error saving devices: {e}")

    def get_device(self, device_id: str) -> Optional[BaseDevice]:
        # 1. Try direct ID lookup
        device = self.devices.get(device_id)
        if device:
            return device
            
        # 2. Try lookup by name (fallback for legacy tasks using hostname)
        for d in self.devices.values():
            if d.name == device_id:
                return d
                
        # 3. Special case: if device_id matches current hostname, return the LocalDevice
        # (This handles cases where tasks were saved with hostname but LocalDevice has a UUID)
        if device_id == socket.gethostname():
            for d in self.devices.values():
                if isinstance(d, LocalDevice):
                    return d
                    
        return None

    def add_remote_device(self, device_id: str, name: str, url: str, python_exec: Optional[str] = None):
        self.devices[device_id] = RemoteDevice(device_id, name, url, python_exec)
        self.save()

    def update_device(self, device_id: str, python_exec: Optional[str] = None):
        if device_id in self.devices:
            self.devices[device_id].python_exec = python_exec
            self.save()
            return True
        return False

    def rename_device(self, device_id: str, new_name: str) -> bool:
        if device_id not in self.devices:
            return False
            
        # 1. Update remote machine (if remote) or local machine name
        device = self.devices[device_id]
        success = device.rename_remote_device(new_name)
        
        # 2. Update local registry if remote update succeeded (or if local)
        # Note: rename_remote_device already updates self.name on the device object.
        # But we need to save the change in devices.json.
        if success:
            device.name = new_name # Redundant but safe
            self.save()
            return True
        
        return False

    def remove_device(self, device_id: str) -> bool:
        if device_id not in self.devices:
            return False
        # Prevent removing local device?
        # if isinstance(self.devices[device_id], LocalDevice):
        #    return False
        
        del self.devices[device_id]
        self.save()
        return True

    def sync_remote_devices(self):
        """Sync configuration for all remote devices"""
        any_changed = False
        
        # Use a list to avoid runtime error if dict changes (though we only modify values)
        # Use threading to speed up? For now, sequential with short timeout (2s)
        # If we have many devices, we should use threading.
        
        threads = []
        
        def sync_worker(dev):
            if dev.sync_config():
                nonlocal any_changed
                any_changed = True

        for device in self.devices.values():
            if isinstance(device, RemoteDevice):
                t = threading.Thread(target=sync_worker, args=(device,))
                t.daemon = True
                t.start()
                threads.append(t)
        
        for t in threads:
            t.join(timeout=3)
            
        if any_changed:
            self.save()

    def get_all_devices(self) -> List[Dict[str, Any]]:
        return [d.to_dict() for d in self.devices.values()]

device_manager = DeviceManager()
