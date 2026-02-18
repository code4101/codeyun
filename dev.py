import subprocess
import sys
import time
import os
import signal

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

        # Check for local Node.js in tools/node
        local_node_dir = os.path.join(root_dir, "tools", "node")
        npm_exec = "npm"
        
        # Verify local node installation integrity
        # npm.cmd on windows usually points to node_modules/npm/bin/npm-cli.js
        npm_cli_js = os.path.join(local_node_dir, "node_modules", "npm", "bin", "npm-cli.js")
        
        if os.path.exists(local_node_dir) and os.path.exists(npm_cli_js):
            print(f"   Using local Node.js: {local_node_dir}")
            env["PATH"] = local_node_dir + os.pathsep + env.get("PATH", "")
            if os.name == 'nt':
                npm_exec = os.path.join(local_node_dir, "npm.cmd")
        elif os.path.exists(local_node_dir):
            print(f"   ⚠️  Local Node.js found at {local_node_dir} but seems incomplete (missing npm-cli.js). Falling back to system Node.js.")

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
        backend_cmd = [python_executable, "-m", "uvicorn", "app:app", "--reload", "--host", "0.0.0.0", "--port", "8000"]
        backend_proc = subprocess.Popen(
            backend_cmd, 
            cwd=backend_dir,
            shell=False,
            env=env
        )
        processes.append(backend_proc)

        # 2. 启动前端 (Frontend)
        print("🚀 Launching Frontend (Vite)...")
        
        # Check if node_modules exists and is valid (has .bin/vite)
        node_modules_path = os.path.join(frontend_dir, "node_modules")
        vite_bin = os.path.join(node_modules_path, ".bin", "vite.cmd" if os.name == 'nt' else "vite")
        
        if not os.path.exists(node_modules_path) or not os.path.exists(vite_bin):
             print("   Installing dependencies (missing or incomplete)... this may take a while")
             try:
                 # Use determined npm executable
                 npm_install_cmd = [npm_exec, "install"]
                 # If using system npm on windows, ensure it's npm.cmd
                 if npm_exec == "npm" and os.name == 'nt':
                     npm_install_cmd[0] = "npm.cmd"
                 
                 subprocess.check_call(npm_install_cmd, cwd=frontend_dir, shell=True, env=env)
             except subprocess.CalledProcessError as e:
                 print(f"⚠️  npm install failed: {e}")

        # npm 需要 shell=True 在 windows 上才能运行，或者用 npm.cmd
        npm_run_cmd = [npm_exec, "run", "dev"]
        # 在 Windows 上通常需要 shell=True 或者指定 npm.cmd
        if npm_exec == "npm" and os.name == 'nt':
            npm_run_cmd[0] = "npm.cmd"
            
        try:
            frontend_proc = subprocess.Popen(
                npm_run_cmd, 
                cwd=frontend_dir,
                shell=False,
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

        # 等待进程结束（通常它们会一直运行直到被中断）
        # 我们轮询检查进程状态
        while True:
            time.sleep(1)
            if backend_proc.poll() is not None:
                print("Backend process exited unexpected.")
                break
            if frontend_proc and frontend_proc.poll() is not None:
                print("Frontend process exited unexpected.")
                break

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
