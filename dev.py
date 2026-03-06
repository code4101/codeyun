import os
import shutil
import signal
import subprocess
import sys
import time


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


def popen_kwargs():
    kwargs = {"shell": False}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    return kwargs


def start_backend(root_dir, env, python_executable):
    print("Launching backend with uvicorn --reload ...")
    cmd = [
        python_executable,
        "-m",
        "uvicorn",
        "backend.app:app",
        "--reload",
        "--reload-dir",
        "backend",
        "--host",
        "0.0.0.0",
        "--port",
        "8000",
    ]
    return subprocess.Popen(cmd, cwd=root_dir, env=env, **popen_kwargs())


def ensure_frontend_deps(frontend_dir, env, npm_exec):
    node_modules = os.path.join(frontend_dir, "node_modules")
    if os.path.isdir(node_modules):
        return

    print("Installing frontend dependencies ...")
    subprocess.check_call([npm_exec, "install"], cwd=frontend_dir, env=env, shell=False)


def start_frontend(frontend_dir, env, npm_exec):
    print("Launching frontend with Vite ...")
    return subprocess.Popen(
        [npm_exec, "run", "dev"],
        cwd=frontend_dir,
        env=env,
        **popen_kwargs(),
    )


def stop_process(proc):
    if not proc or proc.poll() is not None:
        return

    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return

    try:
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=5)
    except Exception:
        proc.kill()


def main():
    root_dir = os.path.dirname(os.path.abspath(__file__))
    frontend_dir = os.path.join(root_dir, "frontend")

    print("Starting CodeYun services (single-layer dev runner)...")
    env, python_executable, npm_exec = setup_env(root_dir)
    print(f"Resolved npm: {npm_exec}")

    backend_proc = None
    frontend_proc = None

    try:
        backend_proc = start_backend(root_dir, env, python_executable)
        ensure_frontend_deps(frontend_dir, env, npm_exec)
        frontend_proc = start_frontend(frontend_dir, env, npm_exec)

        print("Backend:  http://localhost:8000/docs")
        print("Frontend: http://localhost:5173")
        print("Press Ctrl+C once to stop.")

        while True:
            backend_code = backend_proc.poll()
            frontend_code = frontend_proc.poll()
            if backend_code is not None:
                print(f"Backend exited with code {backend_code}.")
                break
            if frontend_code is not None:
                print(f"Frontend exited with code {frontend_code}.")
                break
            time.sleep(1)
    except KeyboardInterrupt:
        print("Stopping services ...")
    finally:
        stop_process(frontend_proc)
        stop_process(backend_proc)
        print("Goodbye.")


if __name__ == "__main__":
    main()
