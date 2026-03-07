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
import json
import ctypes
import hashlib
from ctypes import wintypes
from typing import Dict, List, Optional, Any, Callable
from abc import ABC, abstractmethod
from pydantic import BaseModel
import asyncio
from sqlalchemy import text

import uuid

from backend.core.settings import get_settings

# --- Shared Constants (Consider moving to a config file) ---
settings = get_settings()
DATA_DIR = os.fspath(settings.data_dir)
LOGS_DIR = os.path.join(DATA_DIR, "logs")
SYSTEM_ID_FILE = os.path.join(DATA_DIR, "system_id.json") # Deprecated
LEGACY_CONFIG_FILE = os.path.join(DATA_DIR, "config.json")
LEGACY_DEVICE_STATE_FILE = os.path.join(DATA_DIR, "device_state.json")
LEGACY_NODE_STATE_FILE = os.path.join(DATA_DIR, "node_state.json")
PIDS_FILE = os.path.join(DATA_DIR, "pids.json")
DEVICE_IDENTITY_VERSION = 2


def _get_machine_state_dir() -> str:
    explicit = (os.getenv("CODEYUN_MACHINE_STATE_DIR") or "").strip()
    if explicit:
        return os.path.abspath(os.path.expanduser(explicit))

    if sys.platform == 'win32':
        base = os.getenv("LOCALAPPDATA") or os.getenv("APPDATA")
        if base:
            return os.path.join(base, "CodeYun")

    xdg_state_home = os.getenv("XDG_STATE_HOME") or os.getenv("XDG_CONFIG_HOME")
    if xdg_state_home:
        return os.path.join(xdg_state_home, "codeyun")

    return os.path.join(os.path.expanduser("~"), ".codeyun")


MACHINE_STATE_DIR = _get_machine_state_dir()
MACHINE_IDENTITY_FILE = os.path.join(MACHINE_STATE_DIR, "device_identity.json")

if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)

if not os.path.exists(LOGS_DIR):
    os.makedirs(LOGS_DIR)

def _load_json_file(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_json_file(path: str, payload: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(payload, f, indent=2)


def _normalize_local_config(raw: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        return {}

    config = dict(raw)
    device_id = config.get("device_id") or config.get("system_id")
    if device_id:
        config["device_id"] = str(device_id)
    config.pop("system_id", None)
    config.pop("api_token", None)
    return config


def _load_legacy_local_config() -> Dict[str, Any]:
    if os.path.exists(LEGACY_DEVICE_STATE_FILE):
        legacy = _load_json_file(LEGACY_DEVICE_STATE_FILE)
        if legacy:
            return _normalize_local_config(legacy)

    if os.path.exists(LEGACY_NODE_STATE_FILE):
        legacy = _load_json_file(LEGACY_NODE_STATE_FILE)
        if legacy:
            return _normalize_local_config(legacy)

    if os.path.exists(LEGACY_CONFIG_FILE):
        legacy = _load_json_file(LEGACY_CONFIG_FILE)
        if legacy:
            return _normalize_local_config({
                "device_id": legacy.get("device_id") or legacy.get("system_id"),
                "name": legacy.get("name"),
                "python_exec": legacy.get("python_exec"),
                "created_at": legacy.get("created_at"),
            })

    return {}


def get_local_config():
    """Load legacy persisted local device state for migration compatibility."""
    return _load_legacy_local_config()


def save_local_config(config: Dict):
    _ = config


def _load_machine_identity() -> Dict[str, Any]:
    return _load_json_file(MACHINE_IDENTITY_FILE)


def _save_machine_identity(device_id: str, source: str, seed_value: Optional[str], created_at: Optional[float]) -> None:
    existing = _load_machine_identity()
    payload = {
        "device_id": device_id,
        "device_identity_version": DEVICE_IDENTITY_VERSION,
        "device_identity_source": source,
        "created_at": existing.get("created_at") or created_at or time.time(),
        "updated_at": time.time(),
    }
    if seed_value:
        payload["device_identity_seed_sha256"] = hashlib.sha256(
            seed_value.encode("utf-8")
        ).hexdigest()

    try:
        _save_json_file(MACHINE_IDENTITY_FILE, payload)
    except Exception as exc:
        print(f"Failed to save machine identity: {exc}")


def _read_windows_machine_guid() -> Optional[str]:
    if sys.platform != 'win32':
        return None

    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography") as key:
            value, _ = winreg.QueryValueEx(key, "MachineGuid")
        return str(value).strip().lower() or None
    except Exception:
        return None


def _read_linux_machine_id() -> Optional[str]:
    for path in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                value = f.read().strip().lower()
            if value:
                return value
        except Exception:
            continue
    return None


def _machine_identity_seed() -> tuple[Optional[str], Optional[str]]:
    windows_guid = _read_windows_machine_guid()
    if windows_guid:
        return "windows_machine_guid", windows_guid

    linux_machine_id = _read_linux_machine_id()
    if linux_machine_id:
        return "linux_machine_id", linux_machine_id

    mac = uuid.getnode()
    if mac:
        return "mac_address", f"{mac:012x}"

    hostname = socket.gethostname().strip().lower()
    if hostname:
        return "hostname", hostname

    return None, None


def _stable_device_id_from_seed(source: str, seed_value: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"codeyun-device:{source}:{seed_value}"))


def _current_device_id_from_state(config: Dict[str, Any]) -> Optional[str]:
    current_id = config.get('device_id') or config.get('system_id')
    if current_id:
        return str(current_id)

    if os.path.exists(SYSTEM_ID_FILE):
        try:
            with open(SYSTEM_ID_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if data.get('id'):
                return str(data['id'])
        except Exception:
            pass

    return None


def _table_exists(session, table_name: str) -> bool:
    row = session.exec(
        text(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=:table_name"
        ),
        params={"table_name": table_name},
    ).first()
    return bool(row)


def _backup_device_id_migration(old_id: str, new_id: str) -> None:
    from backend.db import engine

    backup_root = os.path.join(
        DATA_DIR,
        "backups",
        f"device-id-migration-{int(time.time())}-{old_id[:8]}-to-{new_id[:8]}",
    )
    os.makedirs(backup_root, exist_ok=True)

    for path in (
        LEGACY_DEVICE_STATE_FILE,
        LEGACY_NODE_STATE_FILE,
        LEGACY_CONFIG_FILE,
        SYSTEM_ID_FILE,
        MACHINE_IDENTITY_FILE,
    ):
        if os.path.exists(path):
            shutil.copy2(path, os.path.join(backup_root, os.path.basename(path)))

    db_path = getattr(engine.url, "database", None)
    if db_path and os.path.exists(db_path):
        shutil.copy2(db_path, os.path.join(backup_root, os.path.basename(db_path)))


def _merge_directory(src: str, dst: str) -> None:
    if not os.path.exists(src):
        return
    os.makedirs(dst, exist_ok=True)

    for name in os.listdir(src):
        src_path = os.path.join(src, name)
        dst_path = os.path.join(dst, name)
        if os.path.isdir(src_path):
            _merge_directory(src_path, dst_path)
            if os.path.exists(src_path) and not os.listdir(src_path):
                os.rmdir(src_path)
        else:
            if os.path.exists(dst_path):
                base, ext = os.path.splitext(dst_path)
                dst_path = f"{base}.migrated{ext}"
            shutil.move(src_path, dst_path)

    if os.path.exists(src) and not os.listdir(src):
        os.rmdir(src)


def _migrate_device_directories(old_id: str, new_id: str) -> None:
    old_root = os.path.join(DATA_DIR, old_id)
    _ = new_id
    if not os.path.exists(old_root):
        return

    if os.path.exists(old_root):
        shutil.rmtree(old_root, ignore_errors=True)


def _migrate_device_references(old_id: str, new_id: str) -> None:
    from backend.db import engine
    from sqlmodel import Session

    with Session(engine) as session:
        if _table_exists(session, "task"):
            session.exec(
                text("UPDATE task SET device_id = :new_id WHERE device_id = :old_id"),
                params={"old_id": old_id, "new_id": new_id},
            )

        if _table_exists(session, "userdeviceentry"):
            session.exec(
                text(
                    "UPDATE userdeviceentry SET device_id = :new_id WHERE device_id = :old_id"
                ),
                params={"old_id": old_id, "new_id": new_id},
            )

        if _table_exists(session, "device"):
            try:
                session.exec(
                    text("UPDATE device SET id = :new_id WHERE id = :old_id"),
                    params={"old_id": old_id, "new_id": new_id},
                )
            except Exception:
                pass

        session.commit()


def _persist_device_identity(
    *,
    device_id: str,
    source: str,
    seed_value: Optional[str],
    created_at_hint: Optional[float],
) -> None:
    _save_machine_identity(
        device_id=device_id,
        source=source,
        seed_value=seed_value,
        created_at=created_at_hint,
    )


def get_device_token() -> Optional[str]:
    token = get_settings().device_token.strip()
    return token or None


def get_device_id():
    """Get the stable unique ID for this node and migrate legacy state if needed."""
    config = get_local_config()
    current_id = _current_device_id_from_state(config)
    current_version = int(config.get("device_identity_version") or 0)

    machine_identity = _load_machine_identity()
    machine_identity_id = machine_identity.get("device_id")
    if machine_identity_id:
        target_id = str(machine_identity_id)
        source = machine_identity.get("device_identity_source") or "machine_identity_file"
        seed_value = None
    elif current_id and current_version >= DEVICE_IDENTITY_VERSION:
        target_id = current_id
        source = config.get("device_identity_source") or "device_config"
        seed_value = None
    else:
        seed_source, seed_value = _machine_identity_seed()
        if seed_source and seed_value:
            target_id = _stable_device_id_from_seed(seed_source, seed_value)
            source = seed_source
        elif current_id:
            target_id = current_id
            source = "legacy_device_config"
            seed_value = None
        else:
            target_id = str(uuid.uuid4())
            source = "generated_uuid"
            seed_value = None

    if (
        current_id
        and current_id != target_id
        and current_version < DEVICE_IDENTITY_VERSION
        and not machine_identity_id
    ):
        print(f"Migrating local device identity from {current_id} to {target_id}")
        _backup_device_id_migration(current_id, target_id)
        _migrate_device_references(current_id, target_id)
        _migrate_device_directories(current_id, target_id)

    _persist_device_identity(
        device_id=target_id,
        source=source,
        seed_value=seed_value,
        created_at_hint=config.get("created_at"),
    )

    if os.path.exists(SYSTEM_ID_FILE):
        try:
            os.remove(SYSTEM_ID_FILE)
        except Exception:
            pass

    return target_id

def get_system_id():
    return get_device_id()

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
    def rename_device(self, new_name: str) -> bool:
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
        _ = device_id
        log_dir = LOGS_DIR
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
            device_id = get_device_id()
        if name is None:
            name = socket.gethostname()

        super().__init__(device_id, name, python_exec, api_token, order_index)
        self.processes: Dict[str, psutil.Process] = {}
        self.saved_pids: Dict[str, int] = {}
        self.last_run_info: Dict[str, Dict[str, Any]] = {} # Store finished_at, started_at for ended tasks
        self.lock = threading.RLock()
        self.load_pids()
        
    def load_pids(self):
        from backend.models import TaskRuntime

        self.saved_pids = {}
        self.last_run_info = {}

        try:
            with Session(engine) as session:
                rows = session.exec(
                    select(TaskRuntime).where(TaskRuntime.device_id == self.device_id)
                ).all()
        except Exception:
            rows = []

        if rows:
            for row in rows:
                if row.pid is not None:
                    self.saved_pids[row.task_id] = int(row.pid)
                if row.started_at or row.finished_at:
                    self.last_run_info[row.task_id] = {
                        "started_at": row.started_at,
                        "finished_at": row.finished_at,
                    }
            return

        if not os.path.exists(PIDS_FILE):
            return

        try:
            with open(PIDS_FILE, 'r', encoding='utf-8') as f:
                raw_pids = json.load(f)
            self.saved_pids = {
                str(task_id): int(pid)
                for task_id, pid in raw_pids.items()
                if pid is not None
            }
            if self.saved_pids:
                self.save_pids()
        except Exception:
            self.saved_pids = {}

    def save_pids(self):
        from backend.models import TaskRuntime

        try:
            now = time.time()
            task_ids = set(self.saved_pids) | set(self.last_run_info)

            with Session(engine) as session:
                existing_rows = session.exec(
                    select(TaskRuntime).where(TaskRuntime.device_id == self.device_id)
                ).all()
                existing_by_task = {row.task_id: row for row in existing_rows}

                for task_id in task_ids:
                    row = existing_by_task.get(task_id)
                    if row is None:
                        row = TaskRuntime(task_id=task_id, device_id=self.device_id)

                    row.device_id = self.device_id
                    row.updated_at = now

                    if task_id in self.saved_pids:
                        row.pid = int(self.saved_pids[task_id])
                        process = self.processes.get(task_id)
                        started_at = self.last_run_info.get(task_id, {}).get("started_at")
                        if process is not None:
                            try:
                                started_at = process.create_time()
                            except (psutil.NoSuchProcess, psutil.AccessDenied):
                                pass
                        row.started_at = started_at or row.started_at or now
                        row.finished_at = None
                    else:
                        info = self.last_run_info.get(task_id, {})
                        row.pid = None
                        row.started_at = info.get("started_at") or row.started_at
                        row.finished_at = info.get("finished_at") or row.finished_at or now

                    session.add(row)

                session.commit()
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
        log_file_path = os.path.join(LOGS_DIR, f"{task_id}.log")
        if not os.path.exists(log_file_path):
            return []
        
        try:
            from collections import deque
            with open(log_file_path, 'r', encoding='utf-8', errors='replace') as f:
                return list(deque(f, maxlen=lines))
        except Exception as e:
            return [f"Error reading logs: {e}"]

    def rename_device(self, new_name: str) -> bool:
        with self.lock:
            self.name = new_name
        return True

from backend.db import engine, init_db

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
        device_id = get_device_id()
        hostname = socket.gethostname()
        token = get_device_token()

        self.devices = {
            device_id: LocalDevice(device_id, hostname, None, api_token=token)
        }

    def _save_device_to_db(self, device: BaseDevice):
        _ = device

    def save(self):
        pass

    def get_local_device_id(self) -> str:
        return get_device_id()

    def get_device(self, device_id: str) -> Optional[BaseDevice]:
        local_device_id = get_device_id()
        if device_id == local_device_id or device_id == "local":
            return self.devices.get(local_device_id)
        return self.devices.get(device_id)

    def update_device(self, device_id: str, python_exec: Optional[str] = None):
        _ = python_exec
        dev = self.devices.get(device_id)
        if isinstance(dev, LocalDevice):
            return False
        return False

    def rename_device(self, device_id: str, new_name: str) -> bool:
        _ = new_name
        dev = self.devices.get(device_id)
        if not isinstance(dev, LocalDevice):
            return False
        return False
        
    def get_all_devices(self) -> List[Dict[str, Any]]:
        return [d.to_dict() for d in self.devices.values()]

device_manager = DeviceManager()
