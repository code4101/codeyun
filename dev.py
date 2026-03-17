import argparse
import ctypes
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass

try:
    from watchfiles import watch
except ImportError:
    watch = None


BACKEND_RELOAD_MODES = ("outer", "uvicorn")
BACKEND_RELOAD_MODE_ENV = "CODEYUN_DEV_BACKEND_RELOAD_MODE"
LEGACY_BACKEND_RELOAD_MODE_ENV = "CODEYUN_BACKEND_RELOAD_MODE"
CHECK_INTERVAL_ENV = "CODEYUN_DEV_CHECK_INTERVAL_SECONDS"
BACKEND_RELOAD_COOLDOWN_ENV = "CODEYUN_DEV_BACKEND_RELOAD_COOLDOWN_SECONDS"

DEFAULT_BACKEND_RELOAD_MODE = "outer"
DEFAULT_CHECK_INTERVAL_SECONDS = 5.0
DEFAULT_BACKEND_RELOAD_COOLDOWN_SECONDS = 60.0

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
WINDOWS_CREATE_BREAKAWAY_FROM_JOB = 0x01000000


def log(message):
    print(message, flush=True)


@dataclass(frozen=True)
class SupervisorConfig:
    backend_reload_mode: str
    check_interval_seconds: float
    backend_reload_cooldown_seconds: float


def get_npm_path():
    if os.name != "nt":
        return shutil.which("npm") or "npm"

    npm_path = shutil.which("npm.cmd") or shutil.which("npm")
    if npm_path:
        return npm_path

    node_path = shutil.which("node") or shutil.which("node.exe")
    if node_path:
        npm_candidate = os.path.join(os.path.dirname(node_path), "npm.cmd")
        if os.path.exists(npm_candidate):
            return npm_candidate

    trae_npm = os.path.expanduser(r"~/.trae/sdks/versions/node/current/npm.cmd")
    if os.path.exists(trae_npm):
        return trae_npm

    return "npm.cmd"


def setup_env(root_dir):
    env = os.environ.copy()
    python_executable = sys.executable
    env["CODEYUN_ENV"] = "development"
    env["PYTHONUNBUFFERED"] = "1"

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


def parse_args(argv):
    default_reload_mode = read_env_choice(
        (BACKEND_RELOAD_MODE_ENV, LEGACY_BACKEND_RELOAD_MODE_ENV),
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
    }
    if os.name == "nt":
        kwargs["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP | WINDOWS_CREATE_BREAKAWAY_FROM_JOB
        )
    else:
        kwargs["start_new_session"] = True
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


def start_backend(root_dir, env, python_executable, reload_mode):
    if reload_mode == "uvicorn":
        log("Launching backend with uvicorn --reload ...")
    else:
        log("Launching backend with uvicorn (outer-supervised delayed reload) ...")

    cmd = [
        python_executable,
        "-m",
        "uvicorn",
        "backend.app:app",
        "--host",
        "0.0.0.0",
        "--port",
        "8000",
    ]
    if reload_mode == "uvicorn":
        cmd.extend(
            [
                "--reload",
                "--reload-dir",
                "backend",
            ]
        )
    return subprocess.Popen(cmd, cwd=root_dir, env=env, **popen_kwargs())


def ensure_frontend_deps(frontend_dir, env, npm_exec):
    node_modules = os.path.join(frontend_dir, "node_modules")
    if os.path.isdir(node_modules):
        return

    log("Installing frontend dependencies ...")
    subprocess.check_call([npm_exec, "install"], cwd=frontend_dir, env=env, shell=False)


def start_frontend(frontend_dir, env, npm_exec):
    log("Launching frontend with Vite ...")
    return subprocess.Popen(
        [npm_exec, "run", "dev"],
        cwd=frontend_dir,
        env=env,
        **popen_kwargs(),
    )


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
    proc = start_backend(root_dir, env, python_executable, reload_mode=reload_mode)
    process_guard.register(proc)
    return proc


def main():
    args = parse_args(sys.argv[1:])
    config = load_config(args)
    root_dir = os.path.dirname(os.path.abspath(__file__))
    frontend_dir = os.path.join(root_dir, "frontend")

    log("Starting CodeYun services (supervised dev runner)...")
    env, python_executable, npm_exec = setup_env(root_dir)
    log(f"Resolved npm: {npm_exec}")
    log(f"Backend reload mode: {config.backend_reload_mode}")
    log(f"Supervisor check interval: {config.check_interval_seconds:.1f}s")
    if config.backend_reload_mode == "outer":
        log(f"Backend reload cooldown: {config.backend_reload_cooldown_seconds:.1f}s")

    process_guard = create_process_guard()
    backend_proc = None
    frontend_proc = None
    backend_watcher = None
    backend_pending_change = False
    backend_last_change_at = None
    backend_last_change_reason = None

    try:
        backend_proc = start_backend(
            root_dir,
            env,
            python_executable,
            reload_mode=config.backend_reload_mode,
        )
        process_guard.register(backend_proc)

        if config.backend_reload_mode == "outer":
            backend_watcher = BackendChangeWatcher(root_dir, config.check_interval_seconds)
            log(f"Outer backend watcher: {backend_watcher.strategy}")

        ensure_frontend_deps(frontend_dir, env, npm_exec)
        frontend_proc = start_frontend(frontend_dir, env, npm_exec)
        process_guard.register(frontend_proc)

        log("Backend:  http://localhost:8000/docs")
        log("Frontend: http://localhost:5173")
        log("Press Ctrl+C once to stop.")

        while True:
            loop_started_at = time.monotonic()
            change_reason = None

            if backend_watcher is not None:
                change_reason = backend_watcher.poll()

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
                process_guard.stop(backend_proc)
                backend_proc = start_backend(
                    root_dir,
                    env,
                    python_executable,
                    reload_mode=config.backend_reload_mode,
                )
                process_guard.register(backend_proc)
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

            loop_elapsed = time.monotonic() - loop_started_at
            sleep_for = max(0.0, config.check_interval_seconds - loop_elapsed)
            if sleep_for > 0:
                time.sleep(sleep_for)

    except KeyboardInterrupt:
        log("Stopping services ...")
    finally:
        if backend_watcher is not None:
            backend_watcher.close()
        stop_process(frontend_proc, process_guard=process_guard)
        stop_process(backend_proc, process_guard=process_guard)
        process_guard.close()
        log("Goodbye.")


if __name__ == "__main__":
    main()
