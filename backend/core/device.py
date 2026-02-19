import shutil
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
import secrets

import uuid

# --- Shared Constants (Consider moving to a config file) ---
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
LOGS_DIR = os.path.join(DATA_DIR, "logs")
SYSTEM_ID_FILE = os.path.join(DATA_DIR, "system_id.json") # Deprecated
CONFIG_FILE = os.path.join(DATA_DIR, "config.json") # Unified config file
PIDS_FILE = os.path.join(DATA_DIR, "pids.json")

if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)

if not os.path.exists(LOGS_DIR):
    os.makedirs(LOGS_DIR)

def get_local_config():
    """Load local configuration (python_exec, etc.) from config.json"""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_local_config(config: Dict):
    try:
        # Read existing config to preserve other fields if we are doing partial update
        # But wait, usually we pass the full config. 
        # To be safe, let's read and merge if the passed config is partial?
        # The current usage seems to be passing the full config object obtained from get_local_config.
        # So overwrite is fine.
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2)
    except Exception as e:
        print(f"Failed to save local config: {e}")

def get_system_id():
    """Get or generate a persistent unique ID for this machine"""
    config = get_local_config()
    
    # 1. Check if ID is already in config
    if config.get('system_id'):
        return config['system_id']

    # 2. Migration: Check old system_id.json
    if os.path.exists(SYSTEM_ID_FILE):
        try:
            with open(SYSTEM_ID_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if data.get('id'):
                    sys_id = data['id']
                    created_at = data.get('created_at')
                    
                    # Merge into config
                    config['system_id'] = sys_id
                    if created_at:
                        config['created_at'] = created_at
                    
                    save_local_config(config)
                    
                    # Remove old file
                    try:
                        os.remove(SYSTEM_ID_FILE)
                    except:
                        pass
                        
                    return sys_id
        except Exception:
            pass
    
    # 3. Generate new ID
    new_id = str(uuid.uuid4())
    config['system_id'] = new_id
    config['created_at'] = time.time()
    save_local_config(config)
        
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
    def __init__(self, device_id: str, name: str, python_exec: Optional[str] = None, api_token: Optional[str] = None, order_index: int = 0):
        self.device_id = device_id
        self.name = name
        self.python_exec = python_exec
        self.api_token = api_token
        self.order_index = order_index
        self.log_callback: Optional[Callable[[str, str], None]] = None # task_id, line

    @property
    def id(self):
        return self.device_id

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
            "python_exec": self.python_exec,
            "order_index": self.order_index
        }

class CommandResolver(ABC):
    @abstractmethod
    def resolve(self, command: str) -> List[str]:
        pass

    @staticmethod
    def for_platform(platform: str) -> 'CommandResolver':
        if platform == 'win32':
            return WindowsCommandResolver()
        return PosixCommandResolver()

class WindowsCommandResolver(CommandResolver):
    def resolve(self, command: str) -> List[str]:
        try:
            return parse_cmdline(command)
        except Exception:
            return command.split()

class PosixCommandResolver(CommandResolver):
    def resolve(self, command: str) -> List[str]:
        try:
            return parse_cmdline(command)
        except:
            return command.split()

class LogManager:
    @staticmethod
    def prepare_log_path(device_id: str, task_id: str) -> str:
        log_dir = os.path.join(DATA_DIR, device_id, "logs")
        if not os.path.exists(log_dir):
            os.makedirs(log_dir)
        log_file_path = os.path.join(log_dir, f"{task_id}.log")
        
        # Rotation logic
        try:
            if os.path.exists(log_file_path) and os.path.getsize(log_file_path) > 10 * 1024 * 1024:
                backup_path = log_file_path + ".old"
                if os.path.exists(backup_path):
                    os.remove(backup_path)
                os.rename(log_file_path, backup_path)
        except Exception as e:
            print(f"Log rotation failed: {e}")
            
        return log_file_path

    @staticmethod
    def start_stream(task_id: str, process: subprocess.Popen, log_file_path: str, callback: Optional[Callable[[str, str], None]]):
        def _stream():
            try:
                # Open with 'a' (append) or 'w' (write)? 
                # Original code used 'a'.
                # But wait, we are creating a new file or appending?
                # Usually we want to append if we rotated.
                # But if it's a new run, we might want to truncate?
                # The rotation logic handles keeping old logs.
                # So 'a' is correct.
                with open(log_file_path, 'a', encoding='utf-8') as f:
                    # Write header
                    # Actually header writing is done separately in original code.
                    # I'll add a separate method for header writing if needed, or just let caller do it.
                    # But wait, the original code writes header before starting process.
                    # And then stream logs writes stdout.
                    
                    # We need to make sure process.stdout is not None
                    if not process.stdout:
                        return

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
                        
                        # Callback
                        if callback:
                            try:
                                callback(task_id, decoded_line)
                            except Exception as e:
                                print(f"Log callback error: {e}")
            except Exception as e:
                print(f"Log streamer error for pid {process.pid}: {e}")
            finally:
                if process.stdout:
                    process.stdout.close()

        t = threading.Thread(target=_stream, daemon=True)
        t.start()

class TimeoutWatchdog:
    @staticmethod
    def start(process: subprocess.Popen, timeout: Optional[int], task_id: str, stop_callback: Callable[[str], None]):
        if not timeout or timeout <= 0:
            return

        def _watch():
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                print(f"Task {task_id} (PID {process.pid}) timed out after {timeout}s. Killing...")
                try:
                    stop_callback(task_id)
                except Exception as e:
                    print(f"Error killing timed out task {task_id}: {e}")
        
        watcher = threading.Thread(target=_watch, daemon=True)
        watcher.start()

class ErrorMapper:
    @staticmethod
    def map_start_error(e: Exception, cmd_args: List[str], cwd: Optional[str]) -> str:
        error_msg = str(e)
        
        # Common system errors for "Command not found" or "Exec format error"
        is_not_found = "FileNotFoundError" in error_msg or "The system cannot find the file specified" in error_msg or "[WinError 2]" in error_msg or "系统找不到指定的文件" in error_msg
        is_exec_format_error = "[WinError 193]" in error_msg or "is not a valid Win32 application" in error_msg or "不是有效的 Win32 应用程序" in error_msg
        
        if is_not_found or is_exec_format_error:
            cmd_name = cmd_args[0] if cmd_args else "command"
            cmd_lower = cmd_name.lower()
            
            error_msg += f"\\n[Hint] Command '{cmd_name}' failed to start."
            
            # Specific hints for common script types
            if cmd_lower.endswith(".py"):
                 error_msg += f"\\n[Hint] Python scripts should be run with the python interpreter: 'python {cmd_name}'"
            elif cmd_lower.endswith(".ps1"):
                 error_msg += f"\\n[Hint] PowerShell scripts should be run with powershell: 'powershell -File {cmd_name}' or 'pwsh -File {cmd_name}'"
            elif cmd_lower.endswith(".vbs"):
                 error_msg += f"\\n[Hint] VBScript files should be run with cscript: 'cscript {cmd_name}'"
            elif cmd_lower.endswith(".sh"):
                 error_msg += f"\\n[Hint] Shell scripts cannot be run directly on Windows (unless via WSL/Git Bash)."
            elif cmd_lower.startswith("./") or cmd_lower.startswith(".\\"):
                 error_msg += f"\\n[Hint] If this is a script, ensure you are invoking the correct interpreter (e.g., python, powershell, node)."
            elif is_not_found:
                 error_msg += f"\\n[Hint] Please use absolute path or ensure '{cmd_name}' is in system PATH."
                 
        return f"Failed to start task: {error_msg}"

class LocalDevice(BaseDevice):
    def __init__(self, device_id: str = None, name: str = None, python_exec: Optional[str] = None, api_token: Optional[str] = None, order_index: int = 0):
        if device_id is None:
            device_id = get_system_id()
        if name is None:
            name = socket.gethostname()
            
        # Load local config to override python_exec
        local_config = get_local_config()
        if not python_exec:
             python_exec = local_config.get("python_exec")
             
        super().__init__(device_id, name, python_exec, api_token, order_index)
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
        
        # Removed python auto-complete logic
        
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

    def _ensure_not_running(self, task_id: str, command: str) -> Optional[Dict[str, Any]]:
        # Check internal cache first
        if task_id in self.processes:
            proc = self.processes[task_id]
            if proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE:
                 return {"status": "already_running", "pid": proc.pid}
            else:
                del self.processes[task_id]

        # Double check: Scan actual processes just in case cache is stale (Lazy Loading scenario)
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
        return None

    def start_task(self, task_id: str, command: str, cwd: Optional[str] = None, env: Optional[Dict[str, str]] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        with self.lock:
            # Check if running
            existing = self._ensure_not_running(task_id, command)
            if existing:
                return existing

        # Log setup
        try:
            log_file_path = LogManager.prepare_log_path(self.device_id, task_id)
        except Exception as e:
            # If log preparation fails (e.g. invalid chars in task_id), we should still try to return a meaningful error
            # But wait, if we can't create log file, we can't stream logs.
            # We should probably fail the task start.
             raise Exception(f"Failed to prepare log file: {e}")
            
        # Env setup
        run_env = os.environ.copy()
        if env:
            run_env.update(env)
        run_env["PYTHONIOENCODING"] = "utf-8"
        run_env["PYTHONUTF8"] = "1"
        run_env["PYTHONUNBUFFERED"] = "1"
        
        # Command resolution
        resolver = CommandResolver.for_platform(sys.platform)
        cmd_args = resolver.resolve(command)
        
        actual_cwd = cwd if cwd and os.path.exists(cwd) else None

        try:
            # Write header
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
            
            # Start background threads
            LogManager.start_stream(task_id, proc, log_file_path, self.log_callback)
            TimeoutWatchdog.start(proc, timeout, task_id, self.stop_task)

            return {"status": "started", "pid": proc.pid}

        except Exception as e:
            error_msg = ErrorMapper.map_start_error(e, cmd_args, actual_cwd)
            raise Exception(error_msg)

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
            pass
        return True

class RemoteDevice(BaseDevice):
    def __init__(self, device_id: str, name: str, url: str, python_exec: Optional[str] = None, api_token: Optional[str] = None, order_index: int = 0):
        super().__init__(device_id, name, python_exec, api_token, order_index)
        self.url = url.rstrip('/')
        self.task_statuses: Dict[str, TaskStatus] = {}
        self.lock = threading.RLock()

    def _get_headers(self):
        headers = {}
        if self.api_token:
            headers["X-Device-Token"] = self.api_token
        return headers

    def sync_config(self) -> bool:
        """Fetch latest configuration from remote device and update local cache"""
        try:
            resp = requests.get(f"{self.url}/api/agent/status", headers=self._get_headers(), timeout=2)
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
            resp = requests.post(f"{self.url}/api/agent/rename", json={"name": new_name}, headers=self._get_headers(), timeout=5)
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

    def push_config(self) -> bool:
        """Push local configuration (like python_exec) to remote device"""
        try:
            payload = {}
            if self.python_exec:
                payload["python_exec"] = self.python_exec
            
            if not payload:
                return True
                
            resp = requests.post(f"{self.url}/api/agent/config", json=payload, headers=self._get_headers(), timeout=5)
            if resp.status_code == 200:
                return True
            return False
        except Exception as e:
            print(f"Failed to push config to remote device {self.device_id}: {e}")
            return False

    def start_task(self, task_id: str, command: str, cwd: Optional[str] = None, env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        try:
            # Let's try calling the high-level API first.
            resp = requests.post(f"{self.url}/api/task/{task_id}/start", params={"device_id": self.device_id}, headers=self._get_headers(), timeout=5)
            if resp.status_code == 404:
                 pass
            
            if resp.status_code == 200:
                 return resp.json()
                 
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def stop_task(self, task_id: str) -> Dict[str, Any]:
        try:
            resp = requests.post(f"{self.url}/api/task/{task_id}/stop", params={"device_id": self.device_id}, headers=self._get_headers(), timeout=5)
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
            resp = requests.get(f"{self.url}/api/task/{task_id}/logs", params={"n": lines, "device_id": self.device_id}, headers=self._get_headers(), timeout=5)
            if resp.status_code == 200:
                return resp.json().get("logs", [])
            return [f"Error fetching logs: {resp.status_code}"]
        except Exception as e:
            return [f"Connection error: {e}"]

    def scan_running_tasks(self, tasks_to_check: List[Any]):
        # Fetch ALL tasks from remote to sync status
        try:
            # We use /api/task/list to get full status
            resp = requests.get(f"{self.url}/api/task/list", params={"refresh_device_id": self.device_id}, headers=self._get_headers(), timeout=5)
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
            resp = requests.post(f"{self.url}/api/task/process/kill", json={"pid": pid}, headers=self._get_headers(), timeout=5)
            if resp.status_code == 200:
                return True
            return False
        except Exception as e:
            print(f"Failed to kill remote process {pid} on {self.device_id}: {e}")
            return False

    def associate_process(self, task_id: str, pid: int) -> Dict[str, Any]:
        try:
            resp = requests.post(f"{self.url}/api/task/{task_id}/associate", json={"pid": pid}, params={"device_id": self.device_id}, headers=self._get_headers(), timeout=5)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            return {"status": "error", "message": str(e)}

    # New methods for full CRUD proxy
    def list_tasks(self) -> List[Dict]:
        try:
            resp = requests.get(f"{self.url}/api/task/list", params={"refresh_device_id": self.device_id}, headers=self._get_headers(), timeout=5)
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
        resp = requests.post(f"{self.url}/api/task/create", json=task_data, headers=self._get_headers(), timeout=5)
        resp.raise_for_status()
        t = resp.json()
        t['device_id'] = self.device_id
        return t

    def update_task(self, task_id: str, task_data: Dict) -> Dict:
        resp = requests.post(f"{self.url}/api/task/{task_id}/update", json=task_data, params={"device_id": self.device_id}, headers=self._get_headers(), timeout=5)
        resp.raise_for_status()
        t = resp.json()
        t['device_id'] = self.device_id
        return t

    def delete_task(self, task_id: str) -> Dict:
        resp = requests.delete(f"{self.url}/api/task/{task_id}", params={"device_id": self.device_id}, headers=self._get_headers(), timeout=5)
        resp.raise_for_status()
        return resp.json()
    
    def to_dict(self):
        d = super().to_dict()
        d['url'] = self.url
        return d

from backend.db import engine, init_db
# from backend.models import Device as DeviceModel

from sqlmodel import Session, select

class DeviceManager:
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(DeviceManager, cls).__new__(cls)
            # Initialize DB
            try:
                init_db()
            except Exception as e:
                print(f"Failed to init DB: {e}")
                
            # Initialize devices dictionary FIRST
            cls._instance.devices: Dict[str, BaseDevice] = {}
            # Then load
            cls._instance.load()
        return cls._instance

    def load(self):
        # Ensure schema
        try:
            from sqlalchemy import text
            with Session(engine) as session:
                try:
                    session.exec(text("ALTER TABLE device ADD COLUMN order_index INTEGER DEFAULT 0"))
                    session.commit()
                except Exception:
                    pass
                try:
                    session.exec(text("ALTER TABLE userdevice ADD COLUMN order_index INTEGER DEFAULT 0"))
                    session.commit()
                except Exception:
                    pass
        except Exception as e:
            print(f"Schema update warning: {e}")

        # Initialize Local Device from file/system info
        system_id = get_system_id()
        hostname = socket.gethostname()
        local_config = get_local_config()
        
        # We don't load 'Device' table anymore for the manager state
        # The manager state is now:
        # 1. Local Device (always present)
        # 2. Remote Devices (dynamic, loaded on demand or cached? 
        #    Actually, for "Cluster Management", we might want to keep track of known remotes in memory?
        #    But per new design, remotes are User-Specific.
        #    So the DeviceManager (singleton) should primarily manage the LOCAL device.
        #    Remote devices are instantiated per-request based on UserDevice info?
        
        # However, for background sync tasks, we might need a list of active remotes.
        # If we remove Device table, where do we get the list of ALL remotes to sync?
        # Maybe we don't need to sync globally anymore?
        # Syncing should be done when User requests status, or via a background task iterating UserDevices?
        
        # Let's keep it simple: DeviceManager manages the LOCAL device resource.
        # RemoteDevice objects are just temporary proxies created when needed by API.
        
        # BUT: For backward compatibility and keeping existing logic working,
        # we can still load from Device table if we haven't deleted it yet.
        # OR, we switch to fully stateless RemoteDevice management.
        
        # Let's go with: DeviceManager holds LocalDevice.
        # RemoteDevices are not held in global memory anymore, they are instantiated ad-hoc.
        
        token = local_config.get("api_token")
        if not token:
             token = secrets.token_urlsafe(32)
             local_config["api_token"] = token
             save_local_config(local_config)
             
        python_exec = local_config.get("python_exec")
        
        self.devices[system_id] = LocalDevice(system_id, hostname, python_exec, api_token=token)

    def _save_device_to_db(self, device: BaseDevice):
        # Deprecated: We don't save to Device table anymore.
        # For LocalDevice, we save to config.json
        if isinstance(device, LocalDevice):
            conf = get_local_config()
            conf["python_exec"] = device.python_exec
            conf["api_token"] = device.api_token
            # Name is hostname, usually not changeable via config unless we add a field
            save_local_config(conf)

    def save(self):
        # Deprecated: Individual updates should write to DB immediately.
        # But for compatibility, we can iterate and save all?
        # Let's keep it empty or log warning.
        pass

    def get_device(self, device_id: str) -> Optional[BaseDevice]:
        # 1. Check local device
        system_id = get_system_id()
        if device_id == system_id or device_id == "local":
            return self.devices.get(system_id)
            
        # 2. Remote devices are now ephemeral/user-specific.
        # This method should generally NOT be used for remote devices unless we pass context.
        # But for compatibility, if we have it in memory (we don't load them anymore), return it.
        # OR, we can try to look up from UserDevice table? 
        # But we don't have user_id here.
        
        # So this method is strictly for LocalDevice now?
        # Or we can return None for remote, and let the caller handle instantiation from UserDevice.
        
        return self.devices.get(device_id)

    def add_remote_device(self, device_id: str, name: str, url: str, python_exec: Optional[str] = None, api_token: Optional[str] = None):
        # No-op in new architecture for global manager
        pass

    def update_device(self, device_id: str, python_exec: Optional[str] = None):
        if device_id in self.devices:
            dev = self.devices[device_id]
            dev.python_exec = python_exec
            self._save_device_to_db(dev)
            
            # Local device config is saved to file.
            # No push needed for local device itself.
            return True
        return False

    def rename_device(self, device_id: str, new_name: str) -> bool:
        # Only support renaming local device hostname via this method
        # Remote renaming is now handled by updating UserDevice name, or calling remote API directly
        # But for LocalDevice, we don't really rename hostname.
        # We just update config?
        # Actually, hostname is system property.
        
        # If we want to support "alias" for local device, we can store it in config.json
        if device_id in self.devices:
             # Just update in memory?
             # Or do nothing.
             # UserDevice table holds the name used by clients.
             pass
        return True

    def remove_device(self, device_id: str) -> bool:
        # No-op
        return True

    def sync_remote_devices(self):
        # No global sync
        pass
        
    def get_all_devices(self) -> List[Dict[str, Any]]:
        # Only returns local device
        return [d.to_dict() for d in self.devices.values()]
    
    def reorder_devices(self, device_ids: List[str]):
        # No-op
        return True

device_manager = DeviceManager()
