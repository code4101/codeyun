import requests
import json
import os
import sys

# Add project root to sys.path if needed for relative imports (though this script mainly uses requests)
# Current: backend/tests/manual/test_flow.py
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

BASE_URL = "http://localhost:8000"

def login():
    # 1. Login to get User Token
    print(f"\n[1] Logging in as 'code4101'...")
    url = f"{BASE_URL}/api/auth/login"
    data = {"username": "code4101", "password": "123456"}
    
    try:
        resp = requests.post(url, data=data)
        if resp.status_code != 200:
            print(f"Login failed: {resp.status_code} {resp.text}")
            return None
        
        token = resp.json()["access_token"]
        print(f"Login success! User Token: {token[:10]}...")
        return token
    except Exception as e:
        print(f"Login error: {e}")
        return None

def get_devices(user_token):
    # 2. Get Device List using User Token
    print(f"\n[2] Fetching devices for user...")
    url = f"{BASE_URL}/api/devices/"
    headers = {"Authorization": f"Bearer {user_token}"}
    
    try:
        resp = requests.get(url, headers=headers)
        if resp.status_code != 200:
            print(f"Get devices failed: {resp.status_code} {resp.text}")
            return []
            
        devices = resp.json()
        print(f"Found {len(devices)} devices:")
        for i, d in enumerate(devices):
            # Extract info
            dev_id = d.get('device_id')
            alias = d.get('alias')
            token = d.get('token')
            remote_url = d.get('device', {}).get('url')
            
            print(f"  [{i+1}] ID: {dev_id}")
            print(f"      Alias: {alias}")
            print(f"      Remote URL: {remote_url}")
            print(f"      Device Token: {token[:10]}...")
            
            # 3. Test Remote Connection using Device Token
            if remote_url and token:
                test_remote_agent(remote_url, token)
            else:
                print(f"      [!] Cannot test remote: Missing URL or Token")
                
        return devices
    except Exception as e:
        print(f"Get devices error: {e}")
        return []

def test_remote_agent(url, token):
    # 3. Test Agent Status using Device Token
    print(f"      -> Testing connection to {url}...")
    target_url = f"{url.rstrip('/')}/api/agent/status"
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Device-Token": token
    }
    
    try:
        resp = requests.get(target_url, headers=headers, timeout=5)
        if resp.status_code == 200:
            print(f"         [OK] Connected! Remote ID: {resp.json().get('id')}")
        else:
            print(f"         [FAIL] Status: {resp.status_code} {resp.text}")
    except Exception as e:
        print(f"         [ERROR] Connection failed: {e}")

if __name__ == "__main__":
    token = login()
    if token:
        get_devices(token)
