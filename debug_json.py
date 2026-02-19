import requests
import json

BASE_URL = "http://localhost:8000"

def debug_response():
    # Login
    url = f"{BASE_URL}/api/auth/login"
    data = {"username": "code4101", "password": "123456"}
    resp = requests.post(url, data=data)
    token = resp.json()["access_token"]
    
    # Get Devices
    url = f"{BASE_URL}/api/devices/"
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.get(url, headers=headers)
    
    print(json.dumps(resp.json(), indent=2))

if __name__ == "__main__":
    debug_response()