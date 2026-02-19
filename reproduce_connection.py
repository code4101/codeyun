import requests
import sys

url = "http://192.168.31.15:8000/api/agent/status"
token = "iyejq1EeRqeHX64dG1gXmhnoZkHpq6joPn_xOunnoC0"
print(f"Testing connection to {url} with token {token}...")

try:
    print("\n--- Test 1: No Token ---")
    resp = requests.get(url, timeout=5)
    print(f"Status Code: {resp.status_code}")
except Exception as e:
    print(f"Error 1: {e}")

try:
    print("\n--- Test 2: With Token ---")
    headers = {
        "Authorization": f"Bearer {token}"
    }
    resp = requests.get(url, headers=headers, timeout=5)
    print(f"Status Code: {resp.status_code}")
    print(f"Response: {resp.text}")
except Exception as e:
    print(f"Error 2: {e}")
