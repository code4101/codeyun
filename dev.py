import argparse
import ctypes
import hashlib
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from backend.core.services.launcher import (
    apply_background_node_env,
    background_popen_kwargs,
    check_call_quiet,
    node_npm_command,
    popen_service,
    resolve_npm_executable,
    run_quiet,
)

try:
    from watchfiles import watch
except ImportError:
    watch = None

try:
    from dotenv import dotenv_values
except ImportError:
    dotenv_values = None


BACKEND_RELOAD_MODES = ("outer", "off")
BACKEND_RELOAD_MODE_ENV = "CODEYUN_DEV_BACKEND_RELOAD_MODE"
CHECK_INTERVAL_ENV = "CODEYUN_DEV_CHECK_INTERVAL_SECONDS"
BACKEND_RELOAD_COOLDOWN_ENV = "CODEYUN_DEV_BACKEND_RELOAD_COOLDOWN_SECONDS"
DEV_CONSOLE_HOST_ENV = "CODEYUN_DEV_CONSOLE_HOST"

DEFAULT_BACKEND_RELOAD_MODE = "outer"
DEFAULT_CHECK_INTERVAL_SECONDS = 5.0
DEFAULT_BACKEND_RELOAD_COOLDOWN_SECONDS = 60.0
DEFAULT_BACKEND_HOST = "0.0.0.0"
DEFAULT_BACKEND_PORT = 8000
DEFAULT_FRONTEND_PORT = 5173
FRONTEND_HEALTH_PATHS = ("/@vite/client", "/src/main.ts", "/src/views/Login.vue")
FRONTEND_HEALTH_FAILURE_LIMIT = 12
CONSOLE_HOST_STATUS_FILENAME = "codeyun-console-host.json"

BACKEND_WATCH_TARGETS = ("backend", "pyproject.toml", "uv.lock", ".env")
BACKEND_WATCH_EXTENSIONS = {".env", ".ini", ".json", ".py", ".toml", ".yaml", ".yml"}
IGNORED_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "node_modules",
}


class DevInstanceLock:
    def __init__(self, handle=None, file=None):
        self.handle = handle
        self.file = file

    def close(self):
        if self.handle is not None:
            ctypes.windll.kernel32.CloseHandle(self.handle)
            self.handle = None
        if self.file is not None:
            try:
                import fcntl

                fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
            finally:
                self.file.close()
                self.file = None


def acquire_dev_instance_lock(root_dir):
    normalized_root = os.path.normcase(os.path.abspath(root_dir))
    digest = hashlib.sha1(normalized_root.encode("utf-8")).hexdigest()[:16]
    if os.name == "nt":
        name = f"Local\\CodeYunDevSupervisor-{digest}"
        handle = ctypes.windll.kernel32.CreateMutexW(None, False, name)
        if not handle:
            raise OSError("Unable to create CodeYun development supervisor mutex")
        if ctypes.windll.kernel32.GetLastError() == 183:
            ctypes.windll.kernel32.CloseHandle(handle)
            raise RuntimeError("Another CodeYun development supervisor is already running for this repository")
        return DevInstanceLock(handle=handle)

    import fcntl

    path = os.path.join(tempfile.gettempdir(), f"codeyun-dev-{digest}.lock")
    file = open(path, "a+", encoding="utf-8")
    try:
        fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        file.close()
        raise RuntimeError("Another CodeYun development supervisor is already running for this repository")
    return DevInstanceLock(file=file)


def log(message):
    text = str(message)
    try:
        print(text, flush=True)
    except Exception:
        pass
    log_path = os.environ.get("CODEYUN_DEV_SUPERVISOR_LOG")
    if log_path:
        try:
            with open(log_path, "a", encoding="utf-8") as file:
                file.write(text + "\n")
        except OSError:
            pass


def console_host_status_path():
    return os.path.join(tempfile.gettempdir(), "codeyun", CONSOLE_HOST_STATUS_FILENAME)


def is_console_host_enabled():
    if not _env_flag_value(os.environ.get(DEV_CONSOLE_HOST_ENV), default=True):
        return False
    if os.name == "nt" and os.path.basename(sys.executable).lower() == "pythonw.exe":
        return False
    return True


def write_console_host_status(root_dir, backend_host, backend_port, frontend_port, backend_reload_mode):
    path = console_host_status_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    now = time.time()
    payload = {
        "pid": os.getpid(),
        "root_dir": os.path.abspath(root_dir),
        "started_at": getattr(write_console_host_status, "_started_at", now),
        "heartbeat_at": now,
        "backend_host": backend_host,
        "backend_port": backend_port,
        "frontend_port": frontend_port,
        "backend_reload_mode": backend_reload_mode,
    }
    write_console_host_status._started_at = payload["started_at"]
    with open(path, "w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)


def clear_console_host_status():
    path = console_host_status_path()
    try:
        with open(path, encoding="utf-8") as file:
            payload = json.load(file)
    except (OSError, json.JSONDecodeError):
        return
    if int(payload.get("pid") or 0) != os.getpid():
        return
    try:
        os.remove(path)
    except OSError:
        pass


class PortInUseError(RuntimeError):
    pass


@dataclass(frozen=True)
class SupervisorConfig:
    backend_reload_mode: str
    check_interval_seconds: float
    backend_reload_cooldown_seconds: float


def get_npm_path():
    return resolve_npm_executable()


def _env_flag_value(value, default=True):
    if value is None:
        return default
    return str(value).strip().lower() not in {"0", "false", "no", "off"}


def load_dotenv_into_env(root_dir, env):
    if not _env_flag_value(env.get("CODEYUN_LOAD_DOTENV"), default=True):
        return

    env_file = os.path.join(root_dir, ".env")
    if not os.path.isfile(env_file):
        return

    if dotenv_values is not None:
        for key, value in dotenv_values(env_file).items():
            if key and value is not None and key not in env:
                env[key] = value
        return

    try:
        with open(env_file, encoding="utf-8") as file:
            lines = file.readlines()
    except OSError as exc:
        log(f"Skipping .env load: {exc}")
        return

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        if not key or key in env:
            continue
        value = value.strip().strip("'\"")
        env[key] = value


def setup_env(root_dir):
    env = os.environ.copy()
    load_dotenv_into_env(root_dir, env)
    python_executable = sys.executable
    env["CODEYUN_ENV"] = "development"
    env["PYTHONUNBUFFERED"] = "1"
    apply_background_node_env(env, root_dir=root_dir)

    venv_scripts = os.path.join(root_dir, ".venv", "Scripts" if os.name == "nt" else "bin")
    if os.path.isdir(venv_scripts):
        python_name = "python.exe" if os.name == "nt" else "python"
        candidate = os.path.join(venv_scripts, python_name)
        if os.path.exists(candidate):
            python_executable = candidate
            env["CODEYUN_PYTHON_EXEC"] = python_executable
        env["PATH"] = venv_scripts + os.pathsep + env.get("PATH", "")

    npm_exec = get_npm_path()
    if os.path.isabs(npm_exec):
        npm_dir = os.path.dirname(npm_exec)
        if npm_dir not in env.get("PATH", ""):
            env["PATH"] = npm_dir + os.pathsep + env.get("PATH", "")

    env["PYTHONPATH"] = root_dir + os.pathsep + env.get("PYTHONPATH", "")
    return env, python_executable, npm_exec


def read_env_choice(names, default, choices):
    for name in names:
        raw = os.environ.get(name)
        if raw is None or not raw.strip():
            continue

        value = raw.strip().lower()
        if value in choices:
            return value

        log(f"Ignoring invalid {name}={raw!r}; expected one of: {', '.join(choices)}.")
    return default


def read_env_float(name, default, minimum):
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default

    try:
        value = float(raw)
    except ValueError:
        log(f"Ignoring invalid {name}={raw!r}; expected a number.")
        return default

    if value < minimum:
        log(f"Ignoring invalid {name}={raw!r}; expected >= {minimum}.")
        return default

    return value


def read_backend_port(env):
    raw = str(env.get("CODEYUN_BACKEND_PORT") or "").strip()
    if not raw:
        return DEFAULT_BACKEND_PORT

    try:
        value = int(raw)
    except ValueError:
        log(f"Ignoring invalid CODEYUN_BACKEND_PORT={raw!r}; expected an integer.")
        return DEFAULT_BACKEND_PORT

    if not (0 < value < 65536):
        log(f"Ignoring invalid CODEYUN_BACKEND_PORT={raw!r}; expected 1-65535.")
        return DEFAULT_BACKEND_PORT

    return value


def read_backend_host(env):
    return str(env.get("CODEYUN_BACKEND_HOST") or DEFAULT_BACKEND_HOST).strip() or DEFAULT_BACKEND_HOST


def tcp_port_can_bind(host, port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def _local_address_uses_port(local_address, port):
    suffix = f":{port}"
    if local_address.endswith(suffix):
        return True
    if local_address.startswith("[") and local_address.endswith(suffix):
        return True
    return False


def find_tcp_listener_pids(port):
    if os.name == "nt":
        pids = set()
        output = _run_text_command(["netstat", "-ano", "-p", "tcp"])
        for line in output.splitlines():
            parts = line.split()
            if len(parts) < 5 or parts[0].upper() != "TCP":
                continue
            local_address = parts[1]
            state = parts[-2].upper()
            if state != "LISTENING" or not _local_address_uses_port(local_address, port):
                continue
            try:
                pids.add(int(parts[-1]))
            except ValueError:
                continue
        return sorted(pids)

    try:
        import psutil
    except ImportError:
        return []

    pids = set()
    try:
        connections = psutil.net_connections(kind="tcp")
    except Exception:
        return []
    for conn in connections:
        if conn.status != psutil.CONN_LISTEN or not conn.laddr or not conn.pid:
            continue
        if int(getattr(conn.laddr, "port", 0) or 0) == int(port):
            pids.add(int(conn.pid))
    return sorted(pids)


def _process_parent_map():
    if os.name == "nt":
        try:
            import psutil
        except ImportError:
            return {}
        parents = {}
        try:
            iterator = psutil.process_iter(["pid", "ppid"])
            for proc in iterator:
                try:
                    pid = proc.info.get("pid")
                    ppid = proc.info.get("ppid")
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
                    continue
                if pid is not None and ppid is not None:
                    parents[int(pid)] = int(ppid)
        except OSError as exc:
            log(f"Skipping parent process map scan: {exc}")
        return parents

    output = _run_text_command(["ps", "-eo", "pid=,ppid="])
    parents = {}
    for line in output.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        try:
            pid, parent_pid = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        parents[pid] = parent_pid
    return parents


def _run_text_command(cmd):
    try:
        result = run_quiet(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError:
        return ""
    return result.stdout


def _current_process_tree_pids():
    current_pid = os.getpid()
    pids = {current_pid}

    parents = _process_parent_map()
    pid = current_pid
    while pid in parents and parents[pid] not in pids:
        pid = parents[pid]
        pids.add(pid)
    return pids


def _path_mentions_root(text, root_dir):
    if not text:
        return False
    normalized_text = str(text).lower().replace("/", "\\")
    normalized_root = os.path.abspath(root_dir).lower().replace("/", "\\")
    return normalized_root in normalized_text


def _windows_processes():
    try:
        import psutil
    except ImportError:
        return []

    rows = []
    try:
        iterator = psutil.process_iter(["pid", "name", "cmdline"])
        for proc in iterator:
            try:
                command_line = " ".join(proc.info.get("cmdline") or [])
                cwd = proc.cwd()
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
                continue
            rows.append(
                {
                    "ProcessId": proc.info.get("pid"),
                    "Name": proc.info.get("name"),
                    "CommandLine": command_line,
                    "Cwd": cwd,
                }
            )
    except OSError as exc:
        log(f"Skipping Windows process scan: {exc}")
    return rows


def _process_matches_dev_runner(name, command_line, root_dir, cwd=""):
    name = (name or "").lower()
    command_line = (command_line or "").lower()
    if not command_line:
        return False
    if not (_path_mentions_root(command_line, root_dir) or _path_mentions_root(cwd, root_dir)):
        return False

    if "dev.py" in command_line:
        return True
    if "uvicorn" in command_line and "backend.app:app" in command_line:
        return True
    if name in {"node.exe", "node"} and "vite" in command_line:
        return True
    if name in {"cmd.exe", "cmd"} and "npm" in command_line and "dev" in command_line:
        return True
    return False


def find_stale_dev_process_pids():
    protected_pids = _current_process_tree_pids()
    root_dir = os.path.dirname(os.path.abspath(__file__))

    if os.name == "nt":
        pids = set()
        for row in _windows_processes():
            try:
                pid = int(row.get("ProcessId"))
            except (TypeError, ValueError):
                continue
            if pid in protected_pids:
                continue
            if _process_matches_dev_runner(row.get("Name"), row.get("CommandLine"), root_dir, row.get("Cwd")):
                pids.add(pid)
        return sorted(pids)

    output = _run_text_command(["ps", "-eo", "pid=,comm=,args="])
    pids = set()
    for line in output.splitlines():
        parts = line.strip().split(maxsplit=2)
        if len(parts) < 3:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        if pid in protected_pids:
            continue
        if _process_matches_dev_runner(parts[1], parts[2], root_dir):
            pids.add(pid)
    return sorted(pids)


def terminate_pids(pids, reason):
    pids = sorted(set(pid for pid in pids if pid and pid != os.getpid()))
    if not pids:
        return

    log(f"Cleaning stale {reason}: PID(s) {', '.join(str(pid) for pid in pids)}")
    if os.name == "nt":
        try:
            import psutil
        except ImportError:
            return
        targets = []
        for pid in pids:
            try:
                proc = psutil.Process(pid)
                targets.extend(proc.children(recursive=True))
                targets.append(proc)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        seen = set()
        unique_targets = []
        for proc in targets:
            if proc.pid in seen or proc.pid == os.getpid():
                continue
            seen.add(proc.pid)
            unique_targets.append(proc)
        for proc in unique_targets:
            try:
                proc.terminate()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        _gone, alive = psutil.wait_procs(unique_targets, timeout=5.0)
        for proc in alive:
            try:
                proc.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        return

    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except OSError:
            continue

    deadline = time.monotonic() + 5
    remaining = set(pids)
    while remaining and time.monotonic() < deadline:
        for pid in list(remaining):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                remaining.discard(pid)
            except OSError:
                remaining.discard(pid)
        if remaining:
            time.sleep(0.1)

    for pid in remaining:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass


def cleanup_stale_dev_environment(ports):
    full_scan = _env_flag_value(os.environ.get("CODEYUN_DEV_CLEANUP_STALE_PROCESS_SCAN"), default=False)
    if full_scan:
        terminate_pids(find_stale_dev_process_pids(), "dev runner processes")
    else:
        log(
            "Skipping stale dev process scan; cleaning only known port listeners. "
            "Set CODEYUN_DEV_CLEANUP_STALE_PROCESS_SCAN=1 to enable the full scan."
        )

    protected_pids = _current_process_tree_pids() if full_scan else {os.getpid()}
    port_pids = set()
    for port in ports:
        for pid in find_tcp_listener_pids(port):
            if pid not in protected_pids:
                port_pids.add(pid)
    terminate_pids(port_pids, f"listeners on ports {', '.join(str(port) for port in ports)}")

    if port_pids:
        time.sleep(0.5)


def cleanup_port_listeners(port):
    protected_pids = {os.getpid()}
    pids = [pid for pid in find_tcp_listener_pids(port) if pid not in protected_pids]
    terminate_pids(pids, f"listeners on port {port}")


def cleanup_unmanaged_port_listeners(port, protected_pids):
    pids = [pid for pid in find_tcp_listener_pids(port) if pid not in protected_pids]
    terminate_pids(pids, f"unmanaged listeners on port {port}")


def wait_for_backend_port_release(host, port, timeout_seconds=10.0):
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if tcp_port_can_bind(host, port):
            return True
        time.sleep(0.2)
    return tcp_port_can_bind(host, port)


def ensure_backend_port_available(host, port):
    if tcp_port_can_bind(host, port):
        return

    pids = find_tcp_listener_pids(port)
    pid_text = f" Listening PID(s): {', '.join(str(pid) for pid in pids)}." if pids else ""
    raise PortInUseError(
        f"Backend port {host}:{port} is already in use.{pid_text} "
        "Stop the existing CodeYun backend/dev runner before starting a new one."
    )


def parse_args(argv):
    default_reload_mode = read_env_choice(
        (BACKEND_RELOAD_MODE_ENV,),
        default=DEFAULT_BACKEND_RELOAD_MODE,
        choices=BACKEND_RELOAD_MODES,
    )

    parser = argparse.ArgumentParser(description="CodeYun development supervisor")
    parser.add_argument(
        "--backend-reload-mode",
        choices=BACKEND_RELOAD_MODES,
        default=default_reload_mode,
        help=(
            "Backend reload strategy. "
            f"Defaults to {default_reload_mode!r} and can be overridden via "
            f"{BACKEND_RELOAD_MODE_ENV}."
        ),
    )
    return parser.parse_args(argv)


def load_config(args):
    return SupervisorConfig(
        backend_reload_mode=args.backend_reload_mode,
        check_interval_seconds=read_env_float(
            CHECK_INTERVAL_ENV,
            default=DEFAULT_CHECK_INTERVAL_SECONDS,
            minimum=0.5,
        ),
        backend_reload_cooldown_seconds=read_env_float(
            BACKEND_RELOAD_COOLDOWN_ENV,
            default=DEFAULT_BACKEND_RELOAD_COOLDOWN_SECONDS,
            minimum=1.0,
        ),
    )


def popen_kwargs():
    kwargs = {
        "shell": False,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    kwargs.update(background_popen_kwargs(independent=True))
    return kwargs


class ProcessLifecycleGuard:
    def register(self, proc):
        return None

    def stop(self, proc):
        return False

    def close(self):
        return None


if os.name == "nt":
    from ctypes import wintypes

    class LARGE_INTEGER(ctypes.Structure):
        _fields_ = [("QuadPart", ctypes.c_longlong)]


    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", LARGE_INTEGER),
            ("PerJobUserTimeLimit", LARGE_INTEGER),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]


    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]


    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]


    class WindowsJobObjectGuard(ProcessLifecycleGuard):
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
        JOB_OBJECT_LIMIT_BREAKAWAY_OK = 0x00000800
        JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9

        def __init__(self):
            self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            self.enabled = False
            create_job = self.kernel32.CreateJobObjectW
            create_job.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
            create_job.restype = wintypes.HANDLE

            set_info = self.kernel32.SetInformationJobObject
            set_info.argtypes = [
                wintypes.HANDLE,
                ctypes.c_int,
                ctypes.c_void_p,
                wintypes.DWORD,
            ]
            set_info.restype = wintypes.BOOL

            self._create_job = create_job
            self._set_info = set_info
            self.handles_by_pid = {}
            probe = self._new_job_handle()
            if probe is None:
                return
            self.kernel32.CloseHandle(probe)
            self.enabled = True

        def _new_job_handle(self):
            job_handle = self._create_job(None, None)
            if not job_handle:
                error = ctypes.get_last_error()
                log(f"Windows job object unavailable (CreateJobObjectW failed: {error}).")
                return None

            info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
            info.BasicLimitInformation.LimitFlags = (
                self.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | self.JOB_OBJECT_LIMIT_BREAKAWAY_OK
            )

            ok = self._set_info(
                job_handle,
                self.JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
                ctypes.byref(info),
                ctypes.sizeof(info),
            )
            if not ok:
                error = ctypes.get_last_error()
                self.kernel32.CloseHandle(job_handle)
                log(f"Windows job object unavailable (SetInformationJobObject failed: {error}).")
                return None
            return job_handle

        def register(self, proc):
            if not self.enabled or proc is None or proc.poll() is not None:
                return

            old_handle = self.handles_by_pid.pop(proc.pid, None)
            if old_handle:
                self.kernel32.CloseHandle(old_handle)

            job_handle = self._new_job_handle()
            if job_handle is None:
                return

            assign = self.kernel32.AssignProcessToJobObject
            assign.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
            assign.restype = wintypes.BOOL

            proc_handle = wintypes.HANDLE(int(proc._handle))
            ok = assign(job_handle, proc_handle)
            if not ok:
                error = ctypes.get_last_error()
                self.kernel32.CloseHandle(job_handle)
                log(
                    "Failed to bind child process to Windows job object "
                    f"(pid={proc.pid}, error={error})."
                )
                return

            self.handles_by_pid[proc.pid] = job_handle

        def stop(self, proc):
            if proc is None:
                return False

            job_handle = self.handles_by_pid.pop(proc.pid, None)
            if job_handle is None:
                return False

            self.kernel32.CloseHandle(job_handle)
            return True

        def close(self):
            for job_handle in list(self.handles_by_pid.values()):
                self.kernel32.CloseHandle(job_handle)
            self.handles_by_pid.clear()
            self.enabled = False

else:
    WindowsJobObjectGuard = ProcessLifecycleGuard


def create_process_guard():
    if os.name == "nt":
        return WindowsJobObjectGuard()
    return ProcessLifecycleGuard()


def start_backend(root_dir, env, python_executable, reload_mode, backend_host, backend_port):
    ensure_backend_port_available(backend_host, backend_port)

    if reload_mode == "outer":
        log("Launching backend with uvicorn (outer-supervised delayed reload) ...")
    else:
        log("Launching backend with uvicorn (reload disabled) ...")

    cmd = [
        python_executable,
        "-m",
        "uvicorn",
        "backend.app:app",
        "--host",
        backend_host,
        "--port",
        str(backend_port),
    ]
    # The backend is part of the visible CodeYun console host.  It inherits the
    # existing console and therefore cannot create a second flashing window.
    return subprocess.Popen(cmd, cwd=root_dir, env=env)


def ensure_frontend_deps(frontend_dir, env, npm_exec):
    node_modules = os.path.join(frontend_dir, "node_modules")
    if os.path.isdir(node_modules):
        return

    log("Installing frontend dependencies ...")
    check_call_quiet(
        node_npm_command("install", npm_executable=npm_exec),
        cwd=frontend_dir,
        env=env,
    )


def resolve_vite_command(frontend_dir, npm_exec):
    vite_entry = os.path.join(frontend_dir, "node_modules", "vite", "bin", "vite.js")
    node_exec = shutil.which("node.exe" if os.name == "nt" else "node") or shutil.which("node")
    if node_exec and os.path.isfile(vite_entry):
        return [node_exec, vite_entry]
    return node_npm_command("run", "dev", npm_executable=npm_exec)


def start_frontend(frontend_dir, env, npm_exec):
    log("Launching frontend with Vite ...")
    return popen_service(
        resolve_vite_command(frontend_dir, npm_exec),
        cwd=frontend_dir,
        env=env,
    )


def wait_for_tcp_port(host, port, timeout_seconds=20.0):
    deadline = time.monotonic() + timeout_seconds
    target_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((target_host, port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.2)
    return False


def probe_frontend_http(port, path="/src/main.ts", timeout_seconds=2.0):
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        headers={"Cache-Control": "no-cache"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            content_type = str(response.headers.get("Content-Type") or "").lower()
            return response.status == 200 and "javascript" in content_type
    except (OSError, urllib.error.URLError, TimeoutError):
        return False


def stop_process(proc, process_guard=None):
    if not proc or proc.poll() is not None:
        return

    if os.name == "nt":
        if process_guard is not None and process_guard.stop(proc):
            try:
                proc.wait(timeout=5)
            except Exception:
                pass
            return

        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        return

    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        proc.wait(timeout=5)
    except Exception:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:
            proc.kill()


def process_tree_pids(proc):
    if proc is None or proc.poll() is not None:
        return set()

    pids = {int(proc.pid)}
    try:
        import psutil
    except ImportError:
        return pids

    try:
        parent = psutil.Process(proc.pid)
        pids.update(int(child.pid) for child in parent.children(recursive=True))
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        pass
    return pids


def should_watch_backend_file(path):
    name = os.path.basename(path)
    if name.startswith(".env"):
        return True
    return os.path.splitext(name)[1].lower() in BACKEND_WATCH_EXTENSIONS


def normalize_path(path):
    return os.path.normcase(os.path.abspath(path))


def is_top_level_backend_config(root_dir, abs_path):
    root_dir = normalize_path(root_dir)
    abs_path = normalize_path(abs_path)
    if os.path.dirname(abs_path) != root_dir:
        return False

    name = os.path.basename(abs_path)
    return name in {"pyproject.toml", "uv.lock"} or name.startswith(".env")


def is_backend_watch_path(root_dir, path):
    abs_path = normalize_path(path)
    root_dir = normalize_path(root_dir)

    try:
        rel_path = os.path.relpath(abs_path, root_dir)
    except ValueError:
        return False

    parts = rel_path.split(os.sep)
    if any(part in IGNORED_DIRS for part in parts):
        return False

    if len(parts) >= 2 and parts[0] == "backend" and parts[1] == "tests":
        return False

    if parts[0] == "backend":
        return should_watch_backend_file(abs_path)

    return is_top_level_backend_config(root_dir, abs_path)


def iter_backend_watch_files(root_dir):
    for rel_target in BACKEND_WATCH_TARGETS:
        abs_target = os.path.join(root_dir, rel_target)
        if os.path.isdir(abs_target):
            for current_root, dirnames, filenames in os.walk(abs_target):
                dirnames[:] = [name for name in dirnames if name not in IGNORED_DIRS]
                for filename in filenames:
                    abs_path = os.path.join(current_root, filename)
                    if should_watch_backend_file(abs_path):
                        yield normalize_path(abs_path)
            continue

        if os.path.isfile(abs_target) and should_watch_backend_file(abs_target):
            yield normalize_path(abs_target)


def build_backend_snapshot(root_dir):
    snapshot = {}
    for abs_path in iter_backend_watch_files(root_dir):
        try:
            snapshot[abs_path] = os.stat(abs_path).st_mtime_ns
        except FileNotFoundError:
            continue
    return snapshot


def describe_backend_snapshot_change(root_dir, before, after):
    before_keys = set(before)
    after_keys = set(after)

    changed = sorted(path for path in before_keys & after_keys if before[path] != after[path])
    added = sorted(after_keys - before_keys)
    removed = sorted(before_keys - after_keys)

    if changed:
        return f"changed: {os.path.relpath(changed[0], root_dir)}"
    if added:
        return f"added: {os.path.relpath(added[0], root_dir)}"
    if removed:
        return f"removed: {os.path.relpath(removed[0], root_dir)}"
    return "backend files changed"


def describe_backend_watchfiles_change(root_dir, changes):
    if not changes:
        return None

    relevant_changes = []
    for change, path in changes:
        if not is_backend_watch_path(root_dir, path):
            continue

        event_name = getattr(change, "name", str(change)).lower()
        relevant_changes.append((normalize_path(path), event_name))

    if not relevant_changes:
        return None

    relevant_changes.sort()
    abs_path, event_name = relevant_changes[0]
    return f"{event_name}: {os.path.relpath(abs_path, root_dir)}"


class BackendChangeWatcher:
    def __init__(self, root_dir, check_interval_seconds):
        self.root_dir = root_dir
        self.check_interval_seconds = check_interval_seconds
        self.strategy = "watchfiles" if watch is not None else "polling"
        self.stop_event = None
        self.iterator = None
        self.snapshot = None

        if self.strategy == "watchfiles":
            self.stop_event = threading.Event()
            self.iterator = watch(
                root_dir,
                watch_filter=lambda change, path: is_backend_watch_path(root_dir, path),
                stop_event=self.stop_event,
                rust_timeout=max(100, int(check_interval_seconds * 1000)),
                yield_on_timeout=True,
                raise_interrupt=False,
                ignore_permission_denied=True,
            )
        else:
            self.snapshot = build_backend_snapshot(root_dir)

    def poll(self):
        if self.strategy == "watchfiles":
            try:
                changes = next(self.iterator)
            except StopIteration:
                return None
            return describe_backend_watchfiles_change(self.root_dir, changes)

        latest_snapshot = build_backend_snapshot(self.root_dir)
        if latest_snapshot == self.snapshot:
            return None

        reason = describe_backend_snapshot_change(self.root_dir, self.snapshot, latest_snapshot)
        self.snapshot = latest_snapshot
        return reason

    def refresh(self):
        if self.strategy == "polling":
            self.snapshot = build_backend_snapshot(self.root_dir)

    def close(self):
        if self.stop_event is not None:
            self.stop_event.set()


def restart_backend(root_dir, env, python_executable, process_guard, reload_mode, current_proc):
    stop_process(current_proc, process_guard=process_guard)
    backend_host = read_backend_host(env)
    backend_port = read_backend_port(env)
    if not wait_for_backend_port_release(backend_host, backend_port):
        cleanup_port_listeners(backend_port)
        wait_for_backend_port_release(backend_host, backend_port)
    proc = start_backend(
        root_dir,
        env,
        python_executable,
        reload_mode=reload_mode,
        backend_host=backend_host,
        backend_port=backend_port,
    )
    process_guard.register(proc)
    return proc


def main():
    args = parse_args(sys.argv[1:])
    config = load_config(args)
    root_dir = os.path.dirname(os.path.abspath(__file__))
    frontend_dir = os.path.join(root_dir, "frontend")

    try:
        instance_lock = acquire_dev_instance_lock(root_dir)
    except RuntimeError as exc:
        log(str(exc))
        return

    log("Starting CodeYun services (supervised dev runner)...")
    env, python_executable, npm_exec = setup_env(root_dir)
    backend_host = read_backend_host(env)
    backend_port = read_backend_port(env)
    log(f"Resolved npm: {npm_exec}")
    log(f"Backend bind: {backend_host}:{backend_port}")
    log(f"Backend reload mode: {config.backend_reload_mode}")
    log(f"Supervisor check interval: {config.check_interval_seconds:.1f}s")
    if config.backend_reload_mode == "outer":
        log(f"Backend reload cooldown: {config.backend_reload_cooldown_seconds:.1f}s")
    console_host_enabled = is_console_host_enabled()
    if console_host_enabled:
        write_console_host_status(root_dir, backend_host, backend_port, DEFAULT_FRONTEND_PORT, config.backend_reload_mode)
        log(f"Console host heartbeat: {console_host_status_path()}")

    cleanup_stale_dev_environment((backend_port, DEFAULT_FRONTEND_PORT))

    process_guard = create_process_guard()
    backend_proc = None
    frontend_proc = None
    backend_watcher = None
    backend_pending_change = False
    backend_last_change_at = None
    backend_last_change_reason = None
    frontend_health_failure_count = 0
    frontend_health_probe_index = 0

    try:
        backend_proc = start_backend(
            root_dir,
            env,
            python_executable,
            reload_mode=config.backend_reload_mode,
            backend_host=backend_host,
            backend_port=backend_port,
        )
        process_guard.register(backend_proc)

        if config.backend_reload_mode == "outer":
            backend_watcher = BackendChangeWatcher(root_dir, config.check_interval_seconds)
            log(f"Outer backend watcher: {backend_watcher.strategy}")

        ensure_frontend_deps(frontend_dir, env, npm_exec)
        log(f"Waiting for backend port {backend_port} before launching frontend ...")
        if not wait_for_tcp_port(backend_host, backend_port):
            log("Backend port did not become reachable in time; launching frontend anyway.")
        frontend_proc = start_frontend(frontend_dir, env, npm_exec)
        process_guard.register(frontend_proc)

        log(f"Backend:  http://127.0.0.1:{backend_port}/docs")
        log("Frontend: http://localhost:5173")
        log("Press Ctrl+C once to stop.")

        while True:
            loop_started_at = time.monotonic()
            change_reason = None
            if console_host_enabled:
                write_console_host_status(
                    root_dir,
                    backend_host,
                    backend_port,
                    DEFAULT_FRONTEND_PORT,
                    config.backend_reload_mode,
                )

            if backend_watcher is not None:
                change_reason = backend_watcher.poll()

            frontend_service_pids = process_tree_pids(frontend_proc)
            cleanup_unmanaged_port_listeners(DEFAULT_FRONTEND_PORT, frontend_service_pids)

            now = time.monotonic()
            if change_reason:
                backend_pending_change = True
                backend_last_change_at = now
                backend_last_change_reason = change_reason
                log(
                    "Backend change detected "
                    f"({change_reason}); waiting {config.backend_reload_cooldown_seconds:.1f}s "
                    "of quiet time before restart."
                )

            backend_code = backend_proc.poll()
            if backend_code is not None:
                log(f"Backend exited with code {backend_code}. Restarting backend ...")
                backend_proc = restart_backend(
                    root_dir,
                    env,
                    python_executable,
                    process_guard,
                    reload_mode=config.backend_reload_mode,
                    current_proc=backend_proc,
                )
                if backend_watcher is not None:
                    backend_watcher.refresh()
                if backend_pending_change:
                    log("Backend restart picked up the latest files; clearing pending delayed reload.")
                    backend_pending_change = False
                    backend_last_change_at = None
                    backend_last_change_reason = None

            elif (
                config.backend_reload_mode == "outer"
                and backend_pending_change
                and backend_last_change_at is not None
                and now - backend_last_change_at >= config.backend_reload_cooldown_seconds
            ):
                reason = backend_last_change_reason or "quiet period reached"
                log(f"Backend quiet period reached after {reason}; restarting backend ...")
                backend_proc = restart_backend(
                    root_dir,
                    env,
                    python_executable,
                    process_guard,
                    reload_mode=config.backend_reload_mode,
                    current_proc=backend_proc,
                )
                if backend_watcher is not None:
                    backend_watcher.refresh()
                backend_pending_change = False
                backend_last_change_at = None
                backend_last_change_reason = None

            frontend_code = frontend_proc.poll()
            if frontend_code is not None:
                log(f"Frontend exited with code {frontend_code}. Restarting frontend ...")
                process_guard.stop(frontend_proc)
                frontend_proc = start_frontend(frontend_dir, env, npm_exec)
                process_guard.register(frontend_proc)
                frontend_health_failure_count = 0
            else:
                health_path = FRONTEND_HEALTH_PATHS[frontend_health_probe_index % len(FRONTEND_HEALTH_PATHS)]
                frontend_health_probe_index += 1
                if probe_frontend_http(DEFAULT_FRONTEND_PORT, health_path):
                    frontend_health_failure_count = 0
                else:
                    frontend_health_failure_count += 1
                    log(
                        f"Frontend health probe failed ({frontend_health_failure_count}/"
                        f"{FRONTEND_HEALTH_FAILURE_LIMIT}): {health_path}"
                    )
                    if frontend_health_failure_count >= FRONTEND_HEALTH_FAILURE_LIMIT:
                        log("Frontend process is alive but module service is unhealthy. Restarting frontend ...")
                        process_guard.stop(frontend_proc)
                        frontend_proc = start_frontend(frontend_dir, env, npm_exec)
                        process_guard.register(frontend_proc)
                        frontend_health_failure_count = 0

            loop_elapsed = time.monotonic() - loop_started_at
            sleep_for = max(0.0, config.check_interval_seconds - loop_elapsed)
            if sleep_for > 0:
                time.sleep(sleep_for)

    except KeyboardInterrupt:
        log("Stopping services ...")
    except PortInUseError as exc:
        log(f"Cannot start backend: {exc}")
    finally:
        if backend_watcher is not None:
            backend_watcher.close()
        stop_process(frontend_proc, process_guard=process_guard)
        stop_process(backend_proc, process_guard=process_guard)
        process_guard.close()
        if console_host_enabled:
            clear_console_host_status()
        instance_lock.close()
        log("Goodbye.")


if __name__ == "__main__":
    main()
