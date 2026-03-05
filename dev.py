import subprocess
import sys
import time
import os
import signal
import shutil

def get_npm_path():
    """Resolve npm executable path explicitly."""
    if os.name != 'nt':
        return "npm"
        
    # 1. Try npm.cmd directly
    npm_path = shutil.which("npm.cmd")
    if not npm_path:
        npm_path = shutil.which("npm")
    
    # 2. Fallback: try to find node and look for npm relative to it
    if not npm_path:
        node_path = shutil.which("node") or shutil.which("node.exe")
        if node_path:
            npm_candidate = os.path.join(os.path.dirname(node_path), "npm.cmd")
            if os.path.exists(npm_candidate):
                npm_path = npm_candidate

    # 3. Fallback: Check Trae managed Node.js path
    if not npm_path:
        trae_npm = os.path.expanduser(r"~/.trae/sdks/versions/node/current/npm.cmd")
        if os.path.exists(trae_npm):
            npm_path = trae_npm
            
    return npm_path if npm_path else "npm.cmd"

def setup_env(root_dir):
    """Prepare environment variables."""
    env = os.environ.copy()
    
    # Ensure npm dir is in PATH
    npm_path = get_npm_path()
    if npm_path and os.path.isabs(npm_path):
        npm_dir = os.path.dirname(npm_path)
        if npm_dir not in env["PATH"]:
            env["PATH"] = npm_dir + os.pathsep + env.get("PATH", "")
            
    # Inject venv to PATH
    venv_scripts = os.path.join(root_dir, ".venv", "Scripts")
    python_executable = sys.executable
    
    if os.path.exists(venv_scripts):
        # print(f"   Injecting venv to PATH: {venv_scripts}")
        env["PATH"] = venv_scripts + os.pathsep + env.get("PATH", "")
        python_executable = os.path.join(venv_scripts, "python.exe")
        env["CODEYUN_PYTHON_EXEC"] = python_executable
    
    # Ensure root_dir is in PYTHONPATH
    env["PYTHONPATH"] = root_dir + os.pathsep + env.get("PYTHONPATH", "")
    
    return env, python_executable, npm_path

def start_backend(root_dir, env, python_executable):
    """Start backend process."""
    print("🚀 Launching Backend (FastAPI)...")
    cmd = [python_executable, "-m", "uvicorn", "backend.app:app", "--reload", "--host", "0.0.0.0", "--port", "8000"]
    
    # Use shell=True on Windows to act as a signal buffer
    # This helps in some cases to prevent direct signal propagation from uvicorn to dev.py
    use_shell = (os.name == 'nt')
    return subprocess.Popen(cmd, cwd=root_dir, shell=use_shell, env=env)

def start_frontend(frontend_dir, env, npm_exec):
    """Start frontend process."""
    print("🚀 Launching Frontend (Vite)...")
    node_modules_path = os.path.join(frontend_dir, "node_modules")
    
    if not os.path.exists(node_modules_path):
        print("   Installing dependencies (missing or incomplete)... this may take a while")
        try:
            subprocess.check_call([npm_exec, "install"], cwd=frontend_dir, shell=True, env=env)
        except subprocess.CalledProcessError as e:
            print(f"⚠️  npm install failed: {e}")

    try:
        proc = subprocess.Popen(
            [npm_exec, "run", "dev"], 
            cwd=frontend_dir,
            shell=(os.name == 'nt'),
            env=env
        )
        print(f"   Frontend: http://localhost:5173")
        return proc
    except Exception as e:
        print(f"⚠️  Frontend failed to start: {e}")
        return None

def main():
    print("Starting CodeYun services with Process Guardian...")
    
    root_dir = os.path.dirname(os.path.abspath(__file__))
    frontend_dir = os.path.join(root_dir, "frontend")
    
    # Setup environment
    env, python_executable, npm_exec = setup_env(root_dir)
    print(f"   Resolved npm path: {npm_exec}")

    backend_proc = None
    frontend_proc = None

    try:
        # Initial start
        backend_proc = start_backend(root_dir, env, python_executable)
        frontend_proc = start_frontend(frontend_dir, env, npm_exec)
        
        print("\n✨ Services initialized! Press Ctrl+C to stop.")
        print(f"   Backend: http://localhost:8000/docs")
        print("-" * 50)

        # Monitor loop
        running = True
        consecutive_interrupts = 0
        while running:
            try:
                time.sleep(2) # Check every 2 seconds
                consecutive_interrupts = 0 # Reset counter on successful sleep
                
                # Monitor Backend
                if backend_proc is None or backend_proc.poll() is not None:
                    print("\n⚠️  Backend process died! Restarting in 1s...")
                    time.sleep(1)
                    try:
                        backend_proc = start_backend(root_dir, env, python_executable)
                    except Exception as e:
                        print(f"❌ Failed to restart backend: {e}")
                
                # Monitor Frontend
                if frontend_proc is None or frontend_proc.poll() is not None:
                    print("\n⚠️  Frontend process died! Restarting in 1s...")
                    time.sleep(1)
                    try:
                        frontend_proc = start_frontend(frontend_dir, env, npm_exec)
                    except Exception as e:
                        print(f"❌ Failed to restart frontend: {e}")

            except KeyboardInterrupt:
                # On Windows, uvicorn reload might trigger this. 
                # We check if it's a real exit request later or just exit here.
                # To be safe and responsive to real Ctrl+C:
                consecutive_interrupts += 1
                if consecutive_interrupts >= 2:
                     print("\n\n🛑 Stopping services (Double Ctrl+C detected)...")
                     running = False
                     break
                else:
                     print("\n⚠️  Ignored single signal/Ctrl+C (Press again to stop)...")
                     continue

    except KeyboardInterrupt:
        print("\n\n🛑 Stopping services...")
    finally:
        # Graceful shutdown
        for p in [backend_proc, frontend_proc]:
            if p and p.poll() is None:
                try:
                    if os.name == 'nt':
                        p.terminate() 
                    else:
                        p.terminate()
                except Exception:
                    pass
        
        time.sleep(1)
        print("Goodbye!")

if __name__ == "__main__":
    main()
