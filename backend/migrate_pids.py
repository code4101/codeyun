import json
import os
import sys
import time
import psutil
from sqlmodel import Session, select, create_engine
from backend.models import Task, TaskRuntime
from backend.core.settings import get_settings

def migrate():
    settings = get_settings()
    # settings.database_url is likely correct as it uses env vars or defaults
    # But we need to ensure we are connecting to the same DB as the app.
    # The app uses settings.database_url which defaults to sqlite:///{data_dir}/codeyun.db
    
    print(f"Database URL: {settings.database_url}")
    engine = create_engine(settings.database_url)
    
    # Manually construct paths if needed, but settings.data_dir should work
    # data_dir = settings.data_dir
    # pids_path = os.path.join(data_dir, "pids.json")
    # config_path = os.path.join(data_dir, "config.json")
    
    # Use user provided path for source by default, or via arguments
    import argparse
    parser = argparse.ArgumentParser(description="Migrate legacy pids.json to sqlite task table")
    parser.add_argument("--pids", default=r"c:\home\chenkunze\slns\codeyun\backend\data\pids.json", help="Path to pids.json")
    parser.add_argument("--config", default=r"c:\home\chenkunze\slns\codeyun\backend\data\config.json", help="Path to config.json")
    args = parser.parse_args()
    
    pids_path = args.pids
    config_path = args.config
    
    if not os.path.exists(pids_path):
        print(f"No pids.json found at {pids_path}")
        return

    with open(pids_path, 'r', encoding='utf-8') as f:
        pids_data = json.load(f)
        
    device_id = "local"
    if os.path.exists(config_path):
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
            device_id = config.get("system_id") or config.get("device_id") or "local"
            
    print(f"Device ID: {device_id}")
    print(f"Found {len(pids_data)} tasks in pids.json")
    
    with Session(engine) as session:
        for task_id, pid in pids_data.items():
            if not pid:
                continue
                
            # Check if task already exists
            existing_task = session.get(Task, task_id)
            if existing_task:
                print(f"Task {task_id} already exists. Skipping.")
                continue
            
            print(f"Processing Task {task_id} (PID {pid})...")
            
            command_str = None
            cwd = None
            name = f"recovered_task_{task_id[:8]}"
            create_time = time.time()
            
            # 1. Try to get from running process
            try:
                proc = psutil.Process(pid)
                try:
                    cmdline = proc.cmdline()
                    cwd = proc.cwd()
                    create_time = proc.create_time()
                    name = proc.name()
                    import shlex
                    command_str = " ".join(shlex.quote(arg) for arg in cmdline)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            except psutil.NoSuchProcess:
                pass

            # 2. If not running, try to recover from logs
            if not command_str:
                # Find log file in ANY device directory
                found_log_path = None
                data_root = os.path.dirname(pids_path)
                
                # Check data/{device_id}/logs/{task_id}.log
                for item in os.listdir(data_root):
                    d_path = os.path.join(data_root, item)
                    if os.path.isdir(d_path):
                         l_path = os.path.join(d_path, "logs", f"{task_id}.log")
                         if os.path.exists(l_path):
                             found_log_path = l_path
                             break
                
                # Also check data/logs/{task_id}.log
                if not found_log_path:
                     l_path = os.path.join(data_root, "logs", f"{task_id}.log")
                     if os.path.exists(l_path):
                         found_log_path = l_path

                if found_log_path:
                    print(f"  Found log file: {found_log_path}")
                    try:
                        with open(found_log_path, 'r', encoding='utf-8', errors='ignore') as log_f:
                            # Read first 20 lines to find metadata
                            head = []
                            try:
                                for _ in range(20):
                                    head.append(next(log_f))
                            except StopIteration:
                                pass
                            
                            for line in head:
                                if line.startswith("Command:"):
                                    try:
                                        cmd_list_str = line[len("Command:"):].strip()
                                        import ast
                                        cmd_list = ast.literal_eval(cmd_list_str)
                                        if isinstance(cmd_list, list):
                                            import shlex
                                            command_str = " ".join(shlex.quote(str(x)) for x in cmd_list)
                                            if cmd_list:
                                                name = os.path.basename(str(cmd_list[0]))
                                        else:
                                            command_str = cmd_list_str
                                    except:
                                        command_str = line[len("Command:"):].strip()
                                        
                                elif line.startswith("CWD:"):
                                    val = line[len("CWD:"):].strip()
                                    if val and val != "None":
                                        cwd = val
                                    
                                elif line.startswith("--- Starting task at"):
                                    try:
                                        time_str = line[len("--- Starting task at"):].strip().rstrip("-").strip()
                                        from datetime import datetime
                                        dt = datetime.fromisoformat(time_str)
                                        create_time = dt.timestamp()
                                    except:
                                        pass
                    except Exception as e:
                        print(f"  Error reading log: {e}")
            
            if not command_str:
                print(f"  Could not recover command for PID {pid}. Skipping.")
                continue

            try:
                # Create Task
                task = Task(
                    id=task_id,
                    name=name,
                    command=command_str,
                    cwd=cwd,
                    device_id=device_id,
                    created_at=create_time
                )
                session.add(task)
                
                # Create/Update TaskRuntime
                # Check if runtime exists
                runtime = session.get(TaskRuntime, task_id)
                if not runtime:
                    runtime = TaskRuntime(
                        task_id=task_id, 
                        device_id=device_id,
                        pid=pid,
                        started_at=create_time,
                        updated_at=time.time()
                    )
                    session.add(runtime)
                else:
                    runtime.pid = pid
                    runtime.started_at = create_time
                    runtime.updated_at = time.time()
                    session.add(runtime)
                
                print(f"  Migrated Task {task_id}")
                
            except psutil.NoSuchProcess:
                print(f"  PID {pid} does not exist.")
            except Exception as e:
                print(f"  Error processing {task_id}: {e}")
        
        session.commit()
        print("Migration completed.")

if __name__ == "__main__":
    migrate()
