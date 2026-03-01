import subprocess
import time
import requests
import sys
import os
import signal

def is_server_ready(url, timeout=30):
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            resp = requests.get(url, timeout=1)
            if resp.status_code == 200:
                return True
        except requests.exceptions.RequestException:
            pass
        time.sleep(1)
    return False

def run_e2e():
    print("Starting backend server for E2E tests...")
    
    # Start the backend server
    # Assuming we are running from project root
    server_process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.app:app", "--host", "127.0.0.1", "--port", "8000"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "PYTHONPATH": os.getcwd()}
    )
    
    try:
        # Wait for server to be ready
        if not is_server_ready("http://127.0.0.1:8000/"):
            print("Server failed to start within timeout.")
            # Print stderr
            _, stderr = server_process.communicate()
            print(stderr.decode())
            sys.exit(1)
            
        print("Server is ready. Running tests...")
        
        # Run pytest
        # Specifically run the integration test
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/backend/test_integration.py", "-v"],
            env={**os.environ, "PYTHONPATH": os.getcwd()}
        )
        
        if result.returncode != 0:
            print("Tests failed.")
            sys.exit(result.returncode)
        
        print("Tests passed successfully.")
        
    finally:
        print("Stopping server...")
        server_process.terminate()
        try:
            server_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server_process.kill()

if __name__ == "__main__":
    run_e2e()
