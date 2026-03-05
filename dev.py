import subprocess
import sys
import time
import os
import signal

import shutil

def main():
    print("Starting CodeYun services...")
    
    # 获取当前工作目录
    root_dir = os.path.dirname(os.path.abspath(__file__))
    backend_dir = os.path.join(root_dir, "backend")
    frontend_dir = os.path.join(root_dir, "frontend")

    processes = []

    try:
        # 1. 启动后端 (Backend)
        print("🚀 Launching Backend (FastAPI)...")

        # Prepare environment with local projects
        env = os.environ.copy()

        # Resolve npm executable path explicitly
        # This is more robust than relying on PATH in subprocess, especially on Windows
        npm_exec = "npm"
        if os.name == 'nt':
            npm_path = shutil.which("npm.cmd")
            if not npm_path:
                npm_path = shutil.which("npm")
            
            # Fallback: try to find node and look for npm relative to it
            if not npm_path:
                node_path = shutil.which("node") or shutil.which("node.exe")
                if node_path:
                    # Look for npm.cmd in the same directory as node.exe
                    npm_candidate = os.path.join(os.path.dirname(node_path), "npm.cmd")
                    if os.path.exists(npm_candidate):
                        npm_path = npm_candidate

            # Fallback 2: Check Trae managed Node.js path
            if not npm_path:
                trae_npm = os.path.expanduser(r"~/.trae/sdks/versions/node/current/npm.cmd")
                if os.path.exists(trae_npm):
                    npm_path = trae_npm
            
            if npm_path:
                npm_exec = npm_path
                print(f"   Resolved npm path: {npm_exec}")
                # Ensure the directory of npm is in PATH so it can find node during execution
                npm_dir = os.path.dirname(npm_path)
                if npm_dir not in env["PATH"]:
                     env["PATH"] = npm_dir + os.pathsep + env.get("PATH", "")
            else:
                npm_exec = "npm.cmd"
                print("   ⚠️  Could not resolve npm path, falling back to 'npm.cmd'")
                # Try to print helpful debug info
                print(f"   DEBUG: PATH={os.environ.get('PATH')}")

        pythonpath = env.get("PYTHONPATH", "")
        
        # Add local project paths (Optional, only if needed)
        # local_paths = [
        #     r"d:\home\chenkunze\slns\xlproject\src",
        #     r"d:\home\chenkunze\slns\pyxllib\src"
        # ]
        # if local_paths:
        #     new_pythonpath = os.pathsep.join(local_paths + [pythonpath]) if pythonpath else os.pathsep.join(local_paths)
        #     env["PYTHONPATH"] = new_pythonpath
        #     print(f"   Added to PYTHONPATH: {local_paths}")

        # 注入 codeyun 的虚拟环境到 PATH
        # 这样启动的子任务如果使用 'python' 命令，会优先使用该环境
        venv_scripts = os.path.join(root_dir, ".venv", "Scripts")
        python_executable = sys.executable

        if os.path.exists(venv_scripts):
            print(f"   Injecting venv to PATH: {venv_scripts}")
            env["PATH"] = venv_scripts + os.pathsep + env.get("PATH", "")
            # 显式传递 Python 解释器路径，供 TaskManager 使用
            python_executable = os.path.join(venv_scripts, "python.exe")
            env["CODEYUN_PYTHON_EXEC"] = python_executable
        else:
             print(f"   Warning: .venv not found at {venv_scripts}, using default python")
        
        # 使用 shell=True 确保能找到 python 命令，但最好直接调用
        # Run as a package from root dir to support relative imports correctly
        backend_cmd = [python_executable, "-m", "uvicorn", "backend.app:app", "--reload", "--host", "0.0.0.0", "--port", "8000"]
        
        # Ensure root_dir is in PYTHONPATH
        env["PYTHONPATH"] = root_dir + os.pathsep + env.get("PYTHONPATH", "")
        
        backend_proc = subprocess.Popen(
            backend_cmd, 
            cwd=root_dir, # Run from root
            shell=False,
            env=env
        )
        processes.append(backend_proc)

        # 2. 启动前端 (Frontend)
        print("🚀 Launching Frontend (Vite)...")
        
        # Check if node_modules exists and is valid (has .bin/vite)
        node_modules_path = os.path.join(frontend_dir, "node_modules")
        
        # Use simple command structure
        npm_install_cmd = [npm_exec, "install"]
        npm_run_cmd = [npm_exec, "run", "dev"]
        
        # Debugging info
        print(f"   Command: {' '.join(npm_run_cmd)}")
        print(f"   CWD: {frontend_dir}")
        
        if not os.path.exists(node_modules_path):
             print("   Installing dependencies (missing or incomplete)... this may take a while")
             try:
                 subprocess.check_call(npm_install_cmd, cwd=frontend_dir, shell=True, env=env)
             except subprocess.CalledProcessError as e:
                 print(f"⚠️  npm install failed: {e}")

        try:
            frontend_proc = subprocess.Popen(
                npm_run_cmd, 
                cwd=frontend_dir,
                shell=(os.name == 'nt'),
                env=env
            )
            processes.append(frontend_proc)
            print(f"   Frontend: http://localhost:5173")
        except FileNotFoundError:
            print("⚠️  Frontend failed to start: npm not found. Please ensure Node.js is installed.")
            frontend_proc = None
        except Exception as e:
            print(f"⚠️  Frontend failed to start: {e}")
            frontend_proc = None

        print("\n✨ Services initialized! Press Ctrl+C to stop.")
        print(f"   Backend: http://localhost:8000/docs")
        print("-" * 50)

        # Keep main process running until Ctrl+C
        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        print("\n\n🛑 Stopping services...")
    finally:
        # 优雅关闭所有进程
        for p in processes:
            if p.poll() is None: # 如果还在运行
                try:
                    if os.name == 'nt':
                        # Windows 下 terminate 可能不够，有时需要 taskkill 整个进程树
                        # 但对于 uvicorn 和 vite，简单的 terminate 通常有效
                        # 如果需要强制关闭子进程（如 uvicorn 的重载进程），可能需要更强力的手段
                        # 这里先尝试简单的 terminate
                        p.terminate() 
                    else:
                        p.terminate()
                except Exception:
                    pass
        
        # 给一点时间让它们清理
        time.sleep(1)
        print("Goodbye!")

if __name__ == "__main__":
    main()
